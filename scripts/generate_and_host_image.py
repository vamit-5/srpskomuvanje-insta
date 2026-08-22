#!/usr/bin/env python3
"""
generate_and_host_image.py
----------------------------
1. Bira nasumičan hook+caption iz banke tekstova (content/captions.json) za
   zadatu kategoriju.
2. Generiše pozadinsku sliku preko Pollinations.ai (besplatno, bez ključa).
3. Dodaje hook tekst preko slike (Pillow) - BEZ emoji (font ih ne podržava,
   emoji idu samo u caption ispod objave).
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

IMAGE_PROMPT_POOLS = {
    "cta": [
        "close up of two people about to kiss, warm golden light, intense eye contact, cinematic romance",
        "flirty smile close-up portrait, warm candlelight, seductive mood, cinematic photography",
        "couple laughing intimately at a rooftop bar at night, city lights background, romantic tension",
        "attractive couple walking closely together on a vibrant night street, neon lights, cinematic",
    ],
    "humor_citati": [
        "couple dancing close together at a night club, colorful neon lights, energetic mood",
        "two people sharing a drink, eye contact, warm bar lighting, flirtatious mood",
        "close up of hands intertwined on a table, candlelight, romantic and sensual mood",
        "silhouette of couple embracing at sunset on a rooftop, warm golden hour light, romantic",
    ],
    "relatable": [
        "person smiling at phone screen with hopeful expression, warm room light, lifestyle photography",
        "close up of phone glowing in the dark with a smiling reflection, moody warm light",
        "young person getting ready in front of mirror, warm light, excited expression, lifestyle photo",
        "couple laughing together at a cafe table, candid photography, warm afternoon light",
    ],
}


def log(msg):
    print(f"[generate_and_host_image] {msg}", flush=True)


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


def generate_base_image(category):
    prompt_pool = IMAGE_PROMPT_POOLS.get(category, IMAGE_PROMPT_POOLS["relatable"])
    prompt = random.choice(prompt_pool)
    seed = random.randint(1, 999999)
    encoded_prompt = urllib.parse.quote(prompt)
    url = (
        f"https://image.pollinations.ai/prompt/{encoded_prompt}"
        f"?width=1080&height=1350&seed={seed}&nologo=true&model=flux"
    )
    log(f"Generišem sliku: {prompt} (seed={seed})")
    return http_get_bytes_with_retry(url)


def add_hook_text(image_bytes, hook_text):
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
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

    entry = pick_caption_entry(category)
    hook = entry["hook"]
    caption = entry["caption"]

    base_image = generate_base_image(category)
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
