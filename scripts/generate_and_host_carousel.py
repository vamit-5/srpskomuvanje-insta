#!/usr/bin/env python3
"""
generate_and_host_carousel.py
--------------------------------
1. Bira nasumičnu "priču" (niz od 6-7 slajdova koji grade narativ) iz
   content/stories.json.
2. Za SVAKI slajd generiše pozadinsku sliku preko Pollinations.ai i
   ispisuje tekst tog slajda preko slike (Pillow) - tekst po sredini,
   narativni stil, BEZ emoji (font ih ne podržava).
3. Otpremi svaku sliku na Cloudinary.
4. Upisuje listu image_url-ova i glavni caption u output/carousel_content.json
   za publish_carousel.py.
"""

import io
import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from PIL import Image, ImageDraw, ImageFont

STORIES_FILE = "content/stories.json"
OUTPUT_FILE = "output/carousel_content.json"
MAX_RETRIES = 5
RETRY_DELAYS = [5, 10, 20, 40]
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

SCENE_PROMPTS = [
    "candid phone photo of a Serbian couple about to kiss, Slavic Balkan features, warm golden light, natural skin texture, amateur photography, authentic, not posed",
    "candid photo of a Serbian couple dancing close together at a night club, Slavic features, colorful lights, amateur phone photo style, natural, realistic",
    "candid phone photo close-up of a beautiful Serbian woman with a flirty smile, Slavic features, warm candlelight, natural makeup, authentic, realistic skin",
    "candid photo of a Serbian couple laughing intimately at a rooftop bar at night, Balkan features, city lights, amateur photography, natural, imperfect",
    "candid phone photo of Serbian couple holding hands on a table, candlelight, Slavic features, natural skin texture, authentic, not posed",
    "candid photo of an attractive Serbian couple walking closely on a vibrant night street, Slavic Balkan features, neon lights, amateur phone photo style",
    "candid phone photo of two Serbian friends sharing a drink, eye contact, Slavic features, warm bar lighting, amateur photography, natural, authentic",
    "candid photo of a Serbian couple embracing at sunset on a rooftop, Slavic Balkan features, warm golden hour light, amateur phone photo style, realistic",
]


def log(msg):
    print(f"[generate_and_host_carousel] {msg}", flush=True)


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
        f"?width=1080&height=1350&seed={seed}&nologo=true&model=flux"
    )
    log(f"Generišem sliku: {prompt} (seed={seed})")
    return http_get_bytes_with_retry(url)


def add_slide_text(image_bytes, text):
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    width, height = img.size

    try:
        font_size = int(width * 0.075)
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

    # Blaže zatamnjenje cele slike (65/255 ~ 25%) - fotografija ostaje jasno
    # vidljiva, dovoljno samo da beli tekst bude čitljiv
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
        f'Content-Disposition: form-data; name="file"; filename="slide.jpg"\r\n'
        f"Content-Type: image/jpeg\r\n\r\n"
    ).encode("utf-8")
    body += image_bytes
    body += f"\r\n--{boundary}--\r\n".encode("utf-8")

    url = f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload"
    content_type = f"multipart/form-data; boundary={boundary}"

    data = http_post_with_retry(url, body, content_type)
    if "secure_url" not in data:
        raise RuntimeError(f"Neočekivan odgovor od Cloudinary-ja: {data}")
    return data["secure_url"]


def main():
    story = pick_story()
    log(f"Izabrana priča: {story['title']} ({len(story['slides'])} slajdova)")

    image_urls = []
    for i, slide_text in enumerate(story["slides"]):
        log(f"Slajd {i + 1}/{len(story['slides'])}: {slide_text}")
        base_image = generate_base_image(seed_offset=i * 1000)
        final_image = add_slide_text(base_image, slide_text)
        url = upload_to_cloudinary(final_image)
        image_urls.append(url)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"title": story["title"], "image_urls": image_urls, "caption": story["caption"]},
            f,
            ensure_ascii=False,
            indent=2,
        )
    log(f"Gotovo. {len(image_urls)} slika spremno za carousel.")


if __name__ == "__main__":
    main()
