#!/usr/bin/env python3
"""
generate_and_host_story.py
-----------------------------
1. Bira nasumičan "profil" (ime, pol, godine) iz content/profiles.json,
   isto kao Feed objave.
2. Generiše HYPERREALISTIČAN portret te osobe preko Higgsfield API-ja
   (plaćeno, ~$0.09-0.15 po slici) - Srbi/Srpkinje, autentično, ne
   generički izgled.
3. Iseca sliku na format cele Instagram Story (1080x1920), tamni je i
   ispisuje IME, GODINE i kratku privlačnu rečenicu VELIKIM SLOVIMA
   (Pillow) - ključne reči su istaknute u ljubičastoj boji, ostatak beo.
   Ispod dodaje fiksnu CTA liniju (srpskomuvanje.rs - link u bio-u) u
   ljubičastoj boji. BEZ emoji.
4. Otpremi finalnu sliku na Cloudinary (besplatan hosting) da dobije javni
   URL (Higgsfield čuva slike samo 7 dana, zato ih odmah prebacujemo).
5. Upisuje rezultat (category, hook, image_url) u output/story_content.json
   za publish_story.py.

Poziva se ovako: python scripts/generate_and_host_story.py
(kategorija se više ne koristi za ovaj format)

NAPOMENA: Instagram Content Publishing API ne podržava caption za Stories,
zato Stories NE nose caption - koristimo samo ime/godine/hook i dodajemo
fiksnu CTA liniju direktno na sliku.
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

PROFILES_FILE = "content/profiles.json"
OUTPUT_FILE = "output/story_content.json"
MAX_RETRIES = 5
RETRY_DELAYS = [5, 10, 20, 40]
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
CTA_TEXT = "SRPSKOMUVANJE.RS - LINK U BIO-U"
HIGGSFIELD_ENDPOINT = "https://platform.higgsfield.ai/higgsfield-ai/soul/standard"
HIGGSFIELD_ASPECT_RATIO = "9:16"
HIGGSFIELD_RESOLUTION = "720p"
MIN_AGE = 22
MAX_AGE = 34

# Ljubičasta/lila akcentna boja - menjaj samo ovu liniju ako želiš drugu
# nijansu.
ACCENT_COLOR = (191, 64, 255, 255)

# Reči koje će biti istaknute akcentnom bojom kad se pojave u tekstu na
# slici (ostatak teksta ostaje beo). Poredi se bez velikih/malih slova i
# interpunkcije.
HIGHLIGHT_WORDS = {
    "sam", "sama",
    "tražim", "čekam", "spreman", "spremna",
    "pravog", "pravu", "pravo",
    "umoran", "umorna",
    "srpskomuvanje.rs",
    "besplatno", "besplatan", "besplatna",
    "diskretno", "diskretan", "diskretna",
}

# Higgsfield promptovi za hyperrealistične portrete - Slavic/Balkan izgled,
# editorijalni stil, autentično, ne generički AI izgled.
HIGGSFIELD_PROMPTS = {
    "male": [
        "Editorial portrait of a young Serbian man in his late 20s, natural daylight, candid confident expression, casual streetwear, authentic skin texture, Balkan features, shot on 50mm lens, photorealistic",
        "Editorial portrait of an attractive Serbian man, warm evening light, slight smile, short beard, casual jacket, natural skin texture, candid not posed, photorealistic",
        "Editorial portrait of a handsome Serbian man in a cozy cafe, soft window light, genuine smile, Balkan features, natural skin texture, photorealistic, amateur candid feel",
        "Editorial portrait of a young Serbian man outdoors at golden hour, relaxed pose, natural skin texture, Balkan features, photorealistic, candid expression",
    ],
    "female": [
        "Editorial portrait of a young Serbian woman in her late 20s, natural daylight, soft candid smile, casual stylish outfit, authentic skin texture, Balkan features, shot on 50mm lens, photorealistic",
        "Editorial portrait of an attractive Serbian woman, warm evening light, gentle expression, natural makeup, Balkan features, natural skin texture, candid not posed, photorealistic",
        "Editorial portrait of a beautiful Serbian woman in a cozy cafe, soft window light, genuine smile, Balkan features, natural skin texture, photorealistic, amateur candid feel",
        "Editorial portrait of a young Serbian woman outdoors at golden hour, relaxed pose, natural skin texture, Balkan features, photorealistic, candid expression",
    ],
}


def log(msg):
    print(f"[generate_and_host_story] {msg}", flush=True)


def higgsfield_headers():
    key_id = os.environ.get("HF_API_KEY_ID", "").strip()
    key_secret = os.environ.get("HF_API_KEY_SECRET", "").strip()
    if not key_id or not key_secret:
        raise RuntimeError("Nedostaje HF_API_KEY_ID ili HF_API_KEY_SECRET.")
    return {"Authorization": f"Key {key_id}:{key_secret}"}


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


def http_get_json(url, headers):
    req = urllib.request.Request(url, method="GET")
    for k, v in headers.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def http_post_json_with_retry(url, payload, headers):
    data = json.dumps(payload).encode("utf-8")
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/json")
            for k, v in headers.items():
                req.add_header(k, v)
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


def poll_until_done(status_url, headers):
    delay = 2
    max_delay = 10
    max_wait_seconds = 360
    waited = 0
    while waited < max_wait_seconds:
        try:
            data = http_get_json(status_url, headers)
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
            log(f"Greška pri proveri statusa, pokušavam ponovo: {e}")
            time.sleep(delay)
            waited += delay
            delay = min(delay + 1, max_delay)
            continue

        status = data.get("status")
        log(f"Status generisanja: {status} (čekano {waited}s)")
        if status == "completed":
            return data
        if status in ("failed", "nsfw", "canceled"):
            raise RuntimeError(f"Higgsfield generisanje nije uspelo (status={status}).")

        time.sleep(delay)
        waited += delay
        delay = min(delay + 1, max_delay)

    raise RuntimeError("Higgsfield generisanje nije završeno u razumnom vremenu.")


def generate_higgsfield_portrait(prompt):
    headers = higgsfield_headers()
    payload = {
        "prompt": prompt,
        "aspect_ratio": HIGGSFIELD_ASPECT_RATIO,
        "resolution": HIGGSFIELD_RESOLUTION,
    }
    log(f"Šaljem zahtev Higgsfield-u: {prompt}")
    submit_result = http_post_json_with_retry(HIGGSFIELD_ENDPOINT, payload, headers)

    status_url = submit_result.get("status_url")
    if not status_url:
        raise RuntimeError(f"Neočekivan odgovor od Higgsfield-a: {submit_result}")

    result = poll_until_done(status_url, headers)
    images = result.get("images") or []
    if not images or "url" not in images[0]:
        raise RuntimeError(f"Higgsfield nije vratio sliku: {result}")
    return images[0]["url"]


def generate_portrait_with_fallback(prompt_pool):
    last_error = None
    for attempt in range(2):
        prompt = random.choice(prompt_pool)
        try:
            return generate_higgsfield_portrait(prompt)
        except RuntimeError as e:
            last_error = e
            log(f"Pokušaj generisanja nije uspeo ({e}), probam ponovo sa drugim promptom...")
    raise RuntimeError(f"Higgsfield generisanje nije uspelo posle 2 pokušaja: {last_error}")


def pick_profile():
    with open(PROFILES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    gender = random.choice(["male", "female"])
    name = random.choice(data["names"][gender])
    age = random.randint(MIN_AGE, MAX_AGE)
    hook = random.choice(data["hooks"][gender])
    return {
        "gender": gender,
        "name": name,
        "age": age,
        "hook": hook,
        "prompt_pool": HIGGSFIELD_PROMPTS[gender],
    }


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


def add_profile_text(image_bytes, profile):
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = crop_to_fill(img, TARGET_WIDTH, TARGET_HEIGHT)
    width, height = img.size

    img = img.convert("RGBA")
    dark_overlay = Image.new("RGBA", img.size, (0, 0, 0, 100))
    img = Image.alpha_composite(img, dark_overlay)
    draw = ImageDraw.Draw(img, "RGBA")

    try:
        name_font = ImageFont.truetype(FONT_PATH, int(width * 0.09))
        hook_font = ImageFont.truetype(FONT_PATH, int(width * 0.065))
        cta_font = ImageFont.truetype(FONT_PATH, int(width * 0.042))
    except OSError:
        log("UPOZORENJE: DejaVu font nije nađen, koristim default font.")
        name_font = ImageFont.load_default()
        hook_font = ImageFont.load_default()
        cta_font = ImageFont.load_default()

    name_line = f"{profile['name'].upper()}, {profile['age']}"
    hook_upper = profile["hook"].upper()
    max_width = int(width * 0.85)
    hook_lines = wrap_text(draw, hook_upper, hook_font, max_width)

    name_line_height = int(name_font.size * 1.15) if hasattr(name_font, "size") else 28
    hook_line_height = int(hook_font.size * 1.15) if hasattr(hook_font, "size") else 24
    cta_line_height = int(cta_font.size * 1.3) if hasattr(cta_font, "size") else 20
    gap = int(height * 0.02)

    total_text_height = (
        name_line_height + gap + hook_line_height * len(hook_lines) + gap + cta_line_height
    )

    # Ostavljamo prazan prostor pri dnu (Instagram Story kontrole/reply polje)
    bottom_margin = int(height * 0.16)
    band_bottom = height - bottom_margin
    band_top = band_bottom - total_text_height - int(height * 0.05)
    draw.rectangle([(0, band_top), (width, band_bottom)], fill=(0, 0, 0, 175))

    y = band_bottom - total_text_height - int(height * 0.02)
    draw_accent_line(draw, name_line, name_font, y, width)
    y += name_line_height + gap
    for line in hook_lines:
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
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, data=body, method="POST")
            req.add_header("Content-Type", content_type)
            with urllib.request.urlopen(req, timeout=60) as response:
                result = json.loads(response.read().decode("utf-8"))
                if "secure_url" not in result:
                    raise RuntimeError(f"Neočekivan odgovor od Cloudinary-ja: {result}")
                return result["secure_url"]
        except urllib.error.HTTPError as e:
            body_text = e.read().decode("utf-8", errors="replace")
            if 400 <= e.code < 500:
                log(f"TRAJNA GREŠKA ({e.code}), odustajem. Odgovor: {body_text}")
                raise RuntimeError(f"Trajna greška {e.code}: {body_text}") from e
            last_error = RuntimeError(f"HTTP {e.code}: {body_text}")
            log(f"Privremena greška (pokušaj {attempt}/{MAX_RETRIES}): {last_error}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_error = e
            log(f"Mrežna greška (pokušaj {attempt}/{MAX_RETRIES}): {e}")

        if attempt < MAX_RETRIES:
            delay = RETRY_DELAYS[attempt - 1]
            log(f"Čekam {delay}s pre sledećeg pokušaja...")
            time.sleep(delay)

    raise RuntimeError(f"Svi pokušaji neuspešni. Poslednja greška: {last_error}")


def main():
    profile = pick_profile()
    log(f"Profil: {profile['name']}, {profile['age']} godina ({profile['gender']})")

    portrait_url = generate_portrait_with_fallback(profile["prompt_pool"])
    log(f"Preuzimam portret: {portrait_url}")
    base_image = http_get_bytes_with_retry(portrait_url)
    final_image = add_profile_text(base_image, profile)
    image_url = upload_to_cloudinary(final_image)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {"category": profile["gender"], "hook": profile["hook"], "image_url": image_url},
            f,
            ensure_ascii=False,
            indent=2,
        )
    log(f"Gotovo.
