#!/usr/bin/env python3
"""
generate_reels_assets.py
---------------------------
1. Bira nasumičnu "priču" iz content/stories.json (isti narativ kao Carousel).
2. Za SVAKI slajd traži PRAVI kratak video klip preko Pexels API-ja
   (besplatno) - biramo scene gde se lice NE vidi jasno (siluete, atmosfera
   grada/bara/sobe) da izgleda autentično i da izbegnemo pravni rizik.
3. Preuzima svaki klip LOKALNO u output/reels_clips_raw/ (potrebni su
   lokalno za ffmpeg u sledećem koraku).
4. Za svaki slajd generiše PROVIDAN PNG (1080x1920) sa tekstom tog slajda
   (Pillow), BEZ emoji, koji će build_reels_video.py preklopiti preko videa.
5. Upisuje manifest (klip + overlay + trajanje po slajdu, caption) u
   output/reels_manifest.json za build_reels_video.py.
"""

import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw, ImageFont

STORIES_FILE = "content/stories.json"
CLIPS_DIR = "output/reels_clips_raw"
OVERLAYS_DIR = "output/reels_overlays"
MANIFEST_FILE = "output/reels_manifest.json"
MAX_RETRIES = 5
RETRY_DELAYS = [5, 10, 20, 40]
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
WIDTH = 1080
HEIGHT = 1920
SLIDE_DURATION = 3.5
LAST_SLIDE_DURATION = 4.5
MIN_CLIP_DURATION = 4
PEXELS_VIDEO_SEARCH_URL = "https://api.pexels.com/videos/search"

# Upiti biraju scene gde se lice NE vidi jasno (siluete, atmosfera grada/
# bara/sobe) - izgleda autentično i izbegava pravni rizik.
SCENE_VIDEO_QUERIES = [
    "city night lights traffic",
    "candle flame close up night",
    "couple silhouette walking night",
    "bar nightlife lights ambience",
    "hands touching table candlelight",
    "nightclub dancing lights silhouette",
    "city window night view room",
    "couple silhouette sunset romantic",
]


def log(msg):
    print(f"[generate_reels_assets] {msg}", flush=True)


def http_get_bytes_with_retry(url, headers=None, timeout=120):
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req_headers = {"User-Agent": "srpskomuvanje-bot/1.0"}
            if headers:
                req_headers.update(headers)
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as e:
            if 400 <= e.code < 500:
                log(f"TRAJNA GREŠKA ({e.code}), odustajem.")
                raise RuntimeError(f"Trajna greška {e.code}") from e
            last_error = RuntimeError(f"HTTP {e.code}")
            log(f"Privremena greška (pokušaj {attempt}/{MAX_RETRIES}): {last_error}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_error = e
            log(f"Mrežna greška (pokušaj {attempt}/{MAX_RETRIES}): {e}")

        if attempt < MAX_RETRIES:
            delay = RETRY_DELAYS[attempt - 1]
            log(f"Čekam {delay}s pre sledećeg pokušaja...")
            time.sleep(delay)

    raise RuntimeError(f"Svi pokušaji neuspešni. Poslednja greška: {last_error}")


def http_get_json_with_retry(url, headers=None):
    raw = http_get_bytes_with_retry(url, headers=headers, timeout=30)
    return json.loads(raw.decode("utf-8"))


def pick_story():
    with open(STORIES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return random.choice(data["stories"])


def pick_video_file(video_obj):
    candidates = [
        vf for vf in video_obj.get("video_files", [])
        if vf.get("file_type") == "video/mp4" and vf.get("width")
    ]
    if not candidates:
        return None
    # Biramo umerenu rezoluciju (brže preuzimanje, dovoljno kvalitetno)
    candidates.sort(key=lambda vf: abs(vf["width"] - 720))
    return candidates[0]


def pick_video_url(api_key):
    query = random.choice(SCENE_VIDEO_QUERIES)
    page = random.randint(1, 3)
    encoded_query = urllib.parse.quote(query)
    url = (
        f"{PEXELS_VIDEO_SEARCH_URL}?query={encoded_query}"
        f"&per_page=15&page={page}&orientation=portrait"
    )
    log(f"Tražim video klip na Pexels-u: '{query}' (strana {page})")
    data = http_get_json_with_retry(url, headers={"Authorization": api_key})

    videos = data.get("videos", [])
    good_videos = [v for v in videos if v.get("duration", 0) >= MIN_CLIP_DURATION]
    pool = good_videos if good_videos else videos

    random.shuffle(pool)
    for video in pool:
        video_file = pick_video_file(video)
        if video_file:
            return video_file["link"]

    log("Nema rezultata za taj upit, probam rezervni upit...")
    fallback_url = (
        f"{PEXELS_VIDEO_SEARCH_URL}?query=city+night+lights"
        f"&per_page=15&page=1&orientation=portrait"
    )
    data = http_get_json_with_retry(fallback_url, headers={"Authorization": api_key})
    for video in data.get("videos", []):
        video_file = pick_video_file(video)
        if video_file:
            return video_file["link"]

    raise RuntimeError("Pexels nije vratio nijedan upotrebljiv video klip.")


def render_overlay(text):
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img, "RGBA")

    try:
        font_size = int(WIDTH * 0.055)
        font = ImageFont.truetype(FONT_PATH, font_size)
    except OSError:
        log("UPOZORENJE: DejaVu font nije nađen, koristim default font.")
        font = ImageFont.load_default()
        font_size = 20

    words = text.split()
    lines = []
    current_line = ""
    max_width = int(WIDTH * 0.85)
    for word in words:
        test_line = (current_line + " " + word).strip()
        bbox = draw.textbbox((0, 0), test_line, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)

    line_height = int(font_size * 1.3)
    total_text_height = line_height * len(lines)

    # Ostavljamo prazan prostor pri samom dnu ekrana (tu Instagram prikazuje
    # svoje dugmiće/caption preko videa) - tekst je podignut iznad toga.
    bottom_margin = int(HEIGHT * 0.22)
    padding = int(HEIGHT * 0.03)

    band_bottom = HEIGHT - bottom_margin
    band_top = band_bottom - total_text_height - padding * 2
    draw.rectangle([(0, band_top), (WIDTH, band_bottom)], fill=(0, 0, 0, 130))

    y = band_bottom - total_text_height - padding
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_width = bbox[2] - bbox[0]
        x = (WIDTH - line_width) / 2
        for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, 2), (-2, 2), (2, -2)]:
            draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 255))
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_height

    return img


def main():
    api_key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not api_key:
        log("GREŠKA: nedostaje PEXELS_API_KEY.")
        raise SystemExit(1)

    story = pick_story()
    log(f"Izabrana priča: {story['title']} ({len(story['slides'])} slajdova)")

    os.makedirs(CLIPS_DIR, exist_ok=True)
    os.makedirs(OVERLAYS_DIR, exist_ok=True)

    slides = []
    for i, slide_text in enumerate(story["slides"]):
        is_last = i == len(story["slides"]) - 1
        duration = LAST_SLIDE_DURATION if is_last else SLIDE_DURATION
        log(f"Slajd {i + 1}/{len(story['slides'])}: {slide_text}")

        video_url = pick_video_url(api_key)
        log(f"Preuzimam video klip: {video_url}")
        clip_bytes = http_get_bytes_with_retry(video_url)
        clip_path = os.path.join(CLIPS_DIR, f"clip_{i:02d}.mp4")
        with open(clip_path, "wb") as f:
            f.write(clip_bytes)

        overlay_img = render_overlay(slide_text)
        overlay_path = os.path.join(OVERLAYS_DIR, f"overlay_{i:02d}.png")
        overlay_img.save(overlay_path, format="PNG")

        slides.append({
            "clip_path": clip_path,
            "overlay_path": overlay_path,
            "duration": duration,
        })

    manifest = {
        "title": story["title"],
        "caption": story["caption"],
        "slides": slides,
    }
    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    log(f"Gotovo. {len(slides)} video klipova spremno za montažu.")


if __name__ == "__main__":
    main()
