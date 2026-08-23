#!/usr/bin/env python3
"""
generate_and_host_story.py
-----------------------------
1. Bira tekst za Story iz DVA izvora naizmenično (za veću raznovrsnost):
   - polovina puta: hook iz content/captions.json (zadata kategorija)
   - polovina puta: jedna rečenica iz nasumične priče u content/stories.json
2. Traži PRAVU fotografiju preko Pexels API-ja (besplatno) - biramo scene
   koje prikazuju diskretnost, anonimnost i ljubav (siluete, senke, dodir
   ruku), sa evropskim/balkanskim izgledom osoba - lice se NE vidi jasno.
3. Iseca sliku na format cele Instagram Story (1080x1920), tamni je (crni
   sloj preko cele slike + jača zona iza teksta) i ispisuje tekst VELIKIM
   SLOVIMA (Pillow) - ključne reči su istaknute u ljubičastoj boji, ostatak
   beo. Ispod dodaje fiksnu CTA liniju (srpskomuvanje.rs - link u bio-u) u
   ljubičastoj boji. BEZ emoji.
4. Otpremi finalnu sliku na Cloudinary da dobije javni URL.
5. Upisuje rezultat (category, hook, image_url) u output/story_content.json
   za publish_story.py.

Poziva se ovako: python scripts/generate_and_host_story.py <kategorija>
gde je <kategorija> jedno od: cta, humor_citati, relatable (koristi se samo
za biranje raspoloženja fotografije, ne i teksta).

NAPOMENA: Instagram Content Publishing API ne podržava caption za Stories,
zato Stories NE nose caption - koristimo samo "hook" tekst i dodajemo fiksnu
CTA liniju direktno na sliku.
"""

import io
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from PIL import Image, ImageDraw, ImageFont

CAPTIONS_FILE = "content/captions.json"
STORIES_FILE = "content/stories.json"
OUTPUT_FILE = "output/story_content.json"
MAX_RETRIES = 5
RETRY_DELAYS = [5, 10, 20, 40]
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
CTA_TEXT = "SRPSKOMUVANJE.RS - LINK U BIO-U"
PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"

# Ljubičasta/lila akcentna boja za istaknute reči - menjaj samo ovu liniju
# ako želiš drugu nijansu.
ACCENT_COLOR = (191, 64, 255, 255)

# Reči koje će biti istaknute akcentnom bojom kad se pojave u tekstu
# (ostatak teksta ostaje beo). Poredi se bez velikih/malih slova i
# interpunkcije.
HIGHLIGHT_WORDS = {
    "srbi", "srpkinje", "srba", "srpkinja",
    "blizini", "blizine", "blizu",
    "večeras", "noćas",
    "smuvaš", "smuvaju", "smuvaj", "smuvao", "smuvala", "smuvate", "smuvaćeš",
    "srpskomuvanje.rs",
    "app", "app-a", "app-u",
    "srpski", "srbiji",
    "najhotiji", "hotiji", "hot",
    "besplatno", "besplatan", "besplatna",
    "diskretno", "diskretan", "diskretna", "diskretnost",
    "prvi", "prvog",
    "potpuno",
    "tajna", "tajno", "anonimno", "anoniman",
    "ljubav", "strast", "strasti",
    "sada", "odmah", "danas",
}

# Upiti biraju scene koje prikazuju DISKRETNOST, ANONIMNOST i LJUBAV -
# siluete, senke, dodir ruku, sa evropskim/balkanskim izgledom osoba - lice
# se NE vidi jasno. Izgleda autentično i izbegava pravni rizik.
PEXELS_QUERY_POOLS = {
    "cta": [
        "european couple silhouette sunset holding hands",
        "european couple close up night city lights",
        "european couple silhouette secret embrace night",
        "european couple dancing silhouette nightclub",
    ],
    "humor_citati": [
        "european friends laughing silhouette bar night",
        "european couple laughing close up candlelight",
        "european person secret smile close up night portrait",
        "two european people clinking glasses night out",
    ],
    "relatable": [
        "european person texting phone bed dark room",
        "european woman smiling at phone screen dark room",
        "european woman getting ready mirror silhouette bedroom",
        "european friends laughing cafe table from behind",
    ],
}


def log(msg):
    print(f"[generate_and_host_story] {msg}", flush=True)


def http_get_bytes_with_retry(url, headers=None):
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req_headers = {"User-Agent": "srpskomuvanje-bot/1.0"}
            if headers:
                req_headers.update(headers)
            req = urllib.request.Request(url, headers=req_headers)
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


def http_get_json_with_retry(url, headers=None):
    raw = http_get_bytes_with_retry(url, headers=headers)
    return json.loads(raw.decode("utf-8"))


def http_post_with_retry(url, data_bytes, content_type):
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, data=data_bytes, method="POST")
            req.add_header("Content-Type", content_type)
            with urllib.request.urlopen(req, timeout=60) as response:
                body = response.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if 400 <= e.code < 500:
                log(f"TRAJNA GREŠKA ({e.code}), odustajem. Odgovor: {body}")
                raise RuntimeError(f"Trajna greška {e.code}: {body}") from e
            last_error = RuntimeError(f"HTTP {e.code}: {body}")
            log(f"Privremena greška (pokušaj {attempt}/{MAX_RETRIES}): {last_error}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_error = e
            log(f"Mrežna greška (pokušaj {attempt}/{MAX_RETRIES}): {e}")

        if attempt < MAX_RETRIES:
            delay = RETRY_DELAYS[attempt - 1]
            log(f"Čekam {delay}s pre sledećeg pokušaja...")
            time.sleep(delay)

    raise RuntimeError(f"Svi pokušaji neuspešni. Poslednja greška: {last_error}")


def pick_story_text():
    use_caption_hook = random.random() < 0.5

    if use_caption_hook:
        with open(CAPTIONS_FILE, "r", encoding="utf-8") as f:
            bank = json.load(f)
        category = random.choice(list(bank.keys()))
        entry = random.choice(bank[category])
        return entry["hook"]

    with open(STORIES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    story = random.choice(data["stories"])
    return random.choice(story["slides"])


def pick_photo_url(category, api_key):
    query_pool = PEXELS_QUERY_POOLS.get(category, PEXELS_QUERY_POOLS["relatable"])
    query = random.choice(query_pool)
    page = random.randint(1, 3)
    encoded_query = urllib.parse.quote(query)
    url = (
        f"{PEXELS_SEARCH_URL}?query={encoded_query}"
        f"&per_page=15&page={page}&orientation=portrait"
    )
    log(f"Tražim fotografiju na Pexels-u: '{query}' (strana {page})")
    data = http_get_json_with_retry(url, headers={"Authorization": api_key})

    photos = [p for p in data.get("photos", []) if p.get("src", {}).get("large2x") or p.get("src", {}).get("original")]
    if not photos:
        log("Nema rezultata za taj upit, probam rezervni upit...")
        fallback_url = (
            f"{PEXELS_SEARCH_URL}?query=european+couple+silhouette+romantic"
            f"&per_page=15&page=1&orientation=portrait"
        )
        data = http_get_json_with_retry(fallback_url, headers={"Authorization": api_key})
        photos = [p for p in data.get("photos", []) if p.get("src", {}).get("large2x") or p.get("src", {}).get("original")]
        if not photos:
            raise RuntimeError("Pexels nije vratio nijednu upotrebljivu fotografiju.")

    photo = random.choice(photos)
    return photo["src"].get("large2x") or photo["src"]["original"]


def crop_to_fill(img, target_w, target_h):
    src_w, src_h = img.size
    src_ratio = src_w / src_h
    target_ratio = target_w / target_h

    if src_ratio > target_ratio:
        new_h = target_h
        new_w = max(target_w, int(src_w * (target_h / src_h)))
    else:
        new_w = target_w
        new_h = max(target_h, int(src_h * (target_w / src_w)))

    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - target_w) // 2
    top = (new_h - target_h) // 2
    return img.crop((left, top, left + target_w, top + target_h))


def normalize_word(word):
    return word.strip(".,!?:;()\"'„“—-").lower()


def wrap_text(draw, text, font, max_width):
    words = text.split()
    lines = []
    current_line = ""
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
    return lines


def draw_highlighted_line(draw, line, font, y, canvas_width):
    words = line.split(" ")
    space_bbox = draw.textbbox((0, 0), " ", font=font)
    space_width = space_bbox[2] - space_bbox[0]

    widths = []
    for word in words:
        bbox = draw.textbbox((0, 0), word, font=font)
        widths.append(bbox[2] - bbox[0])

    total_width = sum(widths) + space_width * max(len(words) - 1, 0)
    x = (canvas_width - total_width) / 2

    for word, w in zip(words, widths):
        is_highlight = normalize_word(word) in HIGHLIGHT_WORDS
        color = ACCENT_COLOR if is_highlight else (255, 255, 255, 255)
        for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, 2), (-2, 2), (2, -2)]:
            draw.text((x + dx, y + dy), word, font=font, fill=(0, 0, 0, 255))
        draw.text((x, y), word, font=font, fill=color)
        x += w + space_width


def draw_accent_line(draw, line, font, y, canvas_width):
    bbox = draw.textbbox((0, 0), line, font=font)
    line_width = bbox[2] - bbox[0]
    x = (canvas_width - line_width) / 2
    for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, 2), (-2, 2), (2, -2)]:
        draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 255))
    draw.text((x, y), line, font=font, fill=ACCENT_COLOR)


def add_story_text(image_bytes, text):
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = crop_to_fill(img, TARGET_WIDTH, TARGET_HEIGHT)
    width, height = img.size

    # Tamnija pozadina - lagani sloj preko cele slike + jača zona iza teksta
    img = img.convert("RGBA")
    dark_overlay = Image.new("RGBA", img.size, (0, 0, 0, 100))
    img = Image.alpha_composite(img, dark_overlay)
    draw = ImageDraw.Draw(img, "RGBA")

    try:
        hook_font = ImageFont.truetype(FONT_PATH, int(width * 0.075))
        cta_font = ImageFont.truetype(FONT_PATH, int(width * 0.042))
    except OSError:
        log("UPOZORENJE: DejaVu font nije nađen, koristim default font.")
        hook_font = ImageFont.load_default()
        cta_font = ImageFont.load_default()

    text_upper = text.upper()
    max_width = int(width * 0.85)
    lines = wrap_text(draw, text_upper, hook_font, max_width)

    hook_line_height = int(hook_font.size * 1.15) if hasattr(hook_font, "size") else 24
    cta_line_height = int(cta_font.size * 1.3) if hasattr(cta_font, "size") else 20
    gap = int(height * 0.02)

    total_height = hook_line_height * len(lines) + gap + cta_line_height

    # Ostavljamo prazan prostor pri dnu (Instagram Story kontrole/reply polje)
    bottom_margin = int(height * 0.16)
    band_bottom = height - bottom_margin
    band_top = band_bottom - total_height - int(height * 0.05)
    draw.rectangle([(0, band_top), (width, band_bottom)], fill=(0, 0, 0, 175))

    y = band_bottom - total_height - int(height * 0.02)
    for line in lines:
        draw_highlighted_line(draw, line, hook_font, y, width)
        y += hook_line_height

    y += gap
    draw_accent_line(draw, CTA_TEXT, cta_font, y, width)

    img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def upload_to_cloudinary(image_bytes):
    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "").strip()
    upload_preset = os.environ.get("CLOUDINARY_UPLOAD_PRESET", "").strip()
    if not cloud_name or not upload_preset:
        raise RuntimeError("Nedostaje CLOUDINARY_CLOUD_NAME ili CLOUDINARY_UPLOAD_PRESET.")

    boundary = uuid.uuid4().hex
    body = b""
    body += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="upload_preset"\r\n\r\n{upload_preset}\r\n'
    ).encode("utf-8")
    body += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="story.jpg"\r\n'
        f"Content-Type: image/jpeg\r\n\r\n"
    ).encode("utf-8")
    body += image_bytes
    body += f"\r\n--{boundary}--\r\n".encode("utf-8")

    url = f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload"
    content_type = f"multipart/form-data; boundary={boundary}"

    log("Otpremam sliku na Cloudinary...")
    data = http_post_with_retry(url, body, content_type)
    if "secure_url" not in data:
        raise RuntimeError(f"Neočekivan odgovor od Cloudinary-ja: {data}")
    return data["secure_url"]


def main():
    if len(sys.argv) < 2:
        log("GREŠKA: navedi kategoriju kao argument (cta, humor_citati, relatable).")
        sys.exit(1)

    category = sys.argv[1]

    api_key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not api_key:
        log("GREŠKA: nedostaje PEXELS_API_KEY.")
        sys.exit(1)

    hook = pick_story_text()

    photo_url = pick_photo_url(category, api_key)
    log(f"Preuzimam fotografiju: {photo_url}")
    base_image = http_get_bytes_with_retry(photo_url)
    final_image = add_story_text(base_image, hook)
    image_url = upload_to_cloudinary(final_image)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"category": category, "hook": hook, "image_url": image_url},
            f,
            ensure_ascii=False,
            indent=2,
        )
    log(f"Gotovo. Story slika: {image_url}")


if __name__ == "__main__":
    main()
