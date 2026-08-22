#!/usr/bin/env python3
"""
generate_and_host_image.py
----------------------------
1. Bira nasumičan hook+caption iz banke tekstova (content/captions.json) za
   zadatu kategoriju.
2. Traži PRAVU fotografiju preko Pexels API-ja (besplatno, sa API ključem) -
   biramo isključivo scene gde se lice NE vidi jasno (siluete, pogled sa
   leđa, atmosfera) da izgleda autentično i da izbegnemo pravni rizik
   korišćenja tuđeg lika.
3. Iseca sliku na tačan format (1080x1350) i dodaje hook tekst preko slike
   (Pillow) - BEZ emoji (font ih ne podržava, emoji idu samo u caption).
4. Otpremi finalnu sliku na Cloudinary (besplatan hosting) da dobije javni URL.
5. Upisuje rezultat (image_url, caption, category) u output/post_content.json
   za sledeći korak (publish skriptu) da pročita.

Poziva se ovako: python scripts/generate_and_host_image.py <kategorija>
gde je <kategorija> jedno od: cta, humor_citati, relatable
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
OUTPUT_FILE = "output/post_content.json"
MAX_RETRIES = 5
RETRY_DELAYS = [5, 10, 20, 40]
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1350
PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"

# Upiti biraju scene gde se lice NE vidi jasno (siluete, pogled sa leđa,
# atmosfera sobe/izlaska) - izgleda autentično i izbegava pravni rizik.
PEXELS_QUERY_POOLS = {
    "cta": [
        "couple silhouette sunset holding hands",
        "romantic couple close up night city lights",
        "hotel room window night city view",
        "couple dancing silhouette nightclub",
    ],
    "humor_citati": [
        "friends laughing silhouette bar night",
        "couple laughing close up candlelight",
        "rooftop bar couple silhouette sunset",
        "two people clinking glasses night out",
    ],
    "relatable": [
        "person texting phone bed dark room",
        "woman smiling at phone screen dark room",
        "getting ready mirror silhouette bedroom",
        "friends laughing cafe table from behind",
    ],
}


def log(msg):
    print(f"[generate_and_host_image] {msg}", flush=True)


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


def pick_caption_entry(category):
    with open(CAPTIONS_FILE, "r", encoding="utf-8") as f:
        bank = json.load(f)
    entries = bank.get(category)
    if not entries:
        raise RuntimeError(f"Nema tekstova za kategoriju '{category}' u {CAPTIONS_FILE}")
    return random.choice(entries)


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
            f"{PEXELS_SEARCH_URL}?query=couple+silhouette+romantic"
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


def add_hook_text(image_bytes, hook_text):
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = crop_to_fill(img, TARGET_WIDTH, TARGET_HEIGHT)
    draw = ImageDraw.Draw(img, "RGBA")
    width, height = img.size

    try:
        font_size = int(width * 0.065)
        font = ImageFont.truetype(FONT_PATH, font_size)
    except OSError:
        log("UPOZORENJE: DejaVu font nije nađen, koristim default font.")
        font = ImageFont.load_default()
        font_size = 20

    words = hook_text.split()
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

    line_height = int(font_size * 1.25)
    total_text_height = line_height * len(lines)

    # Poluprovidna traka SAMO pri dnu slike (ne cela slika), da ostatak
    # fotografije ostane jasno vidljiv
    band_top = height - total_text_height - int(height * 0.08)
    draw.rectangle([(0, band_top), (width, height)], fill=(0, 0, 0, 110))

    y = height - total_text_height - int(height * 0.05)
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_width = bbox[2] - bbox[0]
        x = (width - line_width) / 2
        for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2), (-2, -2), (2, 2), (-2, 2), (2, -2)]:
            draw.text((x + dx, y + dy), line, font=font, fill=(0, 0, 0, 255))
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_height

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
        f'Content-Disposition: form-data; name="file"; filename="post.jpg"\r\n'
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

    entry = pick_caption_entry(category)
    hook = entry["hook"]
    caption = entry["caption"]

    photo_url = pick_photo_url(category, api_key)
    log(f"Preuzimam fotografiju: {photo_url}")
    base_image = http_get_bytes_with_retry(photo_url)
    final_image = add_hook_text(base_image, hook)
    image_url = upload_to_cloudinary(final_image)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"category": category, "hook": hook, "caption": caption, "image_url": image_url},
            f,
            ensure_ascii=False,
            indent=2,
        )
    log(f"Gotovo. Slika: {image_url}")
    log(f"Caption: {caption}")


if __name__ == "__main__":
    main()
