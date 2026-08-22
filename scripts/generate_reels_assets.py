#!/usr/bin/env python3
"""
generate_reels_assets.py
---------------------------
1. Bira nasumičnu "priču" iz content/stories.json (isti narativ kao Carousel).
2. Za SVAKI slajd generiše sliku (1080x1920, vertikalni format za Reels) preko
   Pollinations.ai i ispisuje tekst tog slajda preko slike (Pillow), BEZ emoji.
3. Čuva slike LOKALNO u output/reels_frames/ (potrebne su lokalno za ffmpeg).
4. Upisuje manifest (redosled slajdova, caption) u output/reels_manifest.json
   za build_reels_video.py.
"""

import io
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw, ImageFont

STORIES_FILE = "content/stories.json"
FRAMES_DIR = "output/reels_frames"
MANIFEST_FILE = "output/reels_manifest.json"
MAX_RETRIES = 5
RETRY_DELAYS = [5, 10, 20, 40]
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

SCENE_PROMPTS = [
    "close up of two people about to kiss, warm golden light, intense eye contact, cinematic romance",
    "couple dancing close together at a night club, colorful neon lights, energetic mood",
    "flirty smile close-up portrait, warm candlelight, seductive mood, cinematic photography",
    "couple laughing intimately at a rooftop bar at night, city lights background, romantic tension",
    "close up of hands intertwined on a table, candlelight, romantic and sensual mood",
    "attractive couple walking closely together on a vibrant night street, neon lights, cinematic",
    "two people sharing a drink, eye contact, warm bar lighting, flirtatious mood",
    "silhouette of couple embracing at sunset on a rooftop, warm golden hour light, romantic",
]


def log(msg):
    print(f"[generate_reels_assets] {msg}", flush=True)


def http_get_bytes_with_retry(url):
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "srpskomuvanje-bot/1.0"})
            with urllib.request.urlopen(req, timeout=60) as response:
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


def pick_story():
    with open(STORIES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return random.choice(data["stories"])


def generate_base_image(seed_offset):
    prompt = random.choice(SCENE_PROMPTS)
    seed = random.randint(1, 999999) + seed_offset
    encoded_prompt = urllib.parse.quote(prompt)
    url = (
        f"https://image.pollinations.ai/prompt/{encoded_prompt}"
        f"?width=1080&height=1920&seed={seed}&nologo=true&model=flux"
    )
    log(f"Generišem sliku: {prompt} (seed={seed})")
    return http_get_bytes_with_retry(url)


def add_slide_text(image_bytes, text):
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    width, height = img.size

    try:
        font_size = int(width * 0.08)
        font = ImageFont.truetype(FONT_PATH, font_size)
    except OSError:
        log("UPOZORENJE: DejaVu font nije nađen, koristim default font.")
        font = ImageFont.load_default()
        font_size = 20

    draw = ImageDraw.Draw(img)
    words = text.split()
    lines = []
    current_line = ""
    max_width = int(width * 0.85)
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

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 65))
    img = Image.alpha_composite(img.convert("RGBA"), overlay)
    draw = ImageDraw.Draw(img, "RGBA")

    y = (height - total_text_height) / 2
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_width = bbox[2] - bbox[0]
        x = (width - line_width) / 2
        for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, 2), (-2, 2), (2, -2)]:
            draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 255))
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_height

    img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def main():
    story = pick_story()
    log(f"Izabrana priča: {story['title']} ({len(story['slides'])} slajdova)")

    os.makedirs(FRAMES_DIR, exist_ok=True)
    frame_paths = []
    for i, slide_text in enumerate(story["slides"]):
        log(f"Slajd {i + 1}/{len(story['slides'])}: {slide_text}")
        base_image = generate_base_image(seed_offset=i * 1000)
        final_image = add_slide_text(base_image, slide_text)
        path = os.path.join(FRAMES_DIR, f"slide_{i:02d}.jpg")
        with open(path, "wb") as f:
            f.write(final_image)
        frame_paths.append(path)

    manifest = {
        "title": story["title"],
        "frame_paths": frame_paths,
        "caption": story["caption"],
    }
    with open(MANIFEST_FILE, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    log(f"Gotovo. {len(frame_paths)} frejmova spremno za video.")


if __name__ == "__main__":
    main()
