#!/usr/bin/env python3
"""
generate_and_host_carousel.py
--------------------------------
1. Bira nasumičan "profil" (ime, pol, godine) iz content/profiles.json.
2. Generiše JEDAN HYPERREALISTIČAN portret te osobe preko Higgsfield API-ja
   (plaćeno, ~$0.09-0.15 po slici) - Srbi/Srpkinje, autentično, ne
   generički izgled. Taj ISTI portret se koristi za SVIH 7 slajdova (da ne
   plaćamo 7 slika po objavi) - menja se samo tekst preko slike.
3. Bira nasumičnu "priču" (niz od 6-7 slajdova koji grade narativ) iz
   content/stories.json.
4. Za SVAKI slajd: iseca portret na tačan format (1080x1350), tamni ga i
   ispisuje tekst tog slajda VELIKIM SLOVIMA po sredini (Pillow) - ključne
   reči su istaknute u ljubičastoj boji, ostatak beo. Dodaje broj slajda
   (npr. "3/7") gore levo, IME i GODINE gore desno, i brend tag dole desno.
   BEZ emoji.
5. Otpremi svaku sliku na Cloudinary (Higgsfield čuva slike samo 7 dana,
   zato odmah otpremamo portret na Cloudinary i njega dalje koristimo).
6. Upisuje listu image_url-ova i caption (profilova kratka priča + CTA) u
   output/carousel_content.json za publish_carousel.py.
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
STORIES_FILE = "content/stories.json"
OUTPUT_FILE = "output/carousel_content.json"
MAX_RETRIES = 5
RETRY_DELAYS = [5, 10, 20, 40]
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1350
HIGGSFIELD_ENDPOINT = "https://platform.higgsfield.ai/higgsfield-ai/soul/standard"
HIGGSFIELD_ASPECT_RATIO = "3:4"
HIGGSFIELD_RESOLUTION = "720p"
MIN_AGE = 22
MAX_AGE = 34

# Ljubičasta/lila akcentna boja - menjaj samo ovu liniju ako želiš drugu
# nijansu.
ACCENT_COLOR = (191, 64, 255, 255)

# Reči koje će biti istaknute akcentnom bojom kad se pojave u tekstu slajda
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
    "garantovano", "garantujemo",
    "vrele", "vrela",
    "igre",
    "jedini", "jedina", "jedinstveno",
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
    print(f"[generate_and_host_carousel] {msg}", flush=True)


def higgsfield_headers():
    key_id = os.environ.get("HF_API_KEY_ID", "").strip()
    key_secret = os.environ.get("HF_API_KEY_SECRET", "").strip()
    if not key_id or not key_secret:
        raise RuntimeError("Nedostaje HF_API_KEY_ID ili HF_API_KEY_SECRET.")
    # User-Agent je OBAVEZAN - bez njega Higgsfield-ov Cloudflare vraća
    # grešku 403 (error code 1010) jer podrazumevani Python User-Agent
    # izgleda kao bot.
    return {
        "Authorization": f"Key {key_id}:{key_secret}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }


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
    bio_template = random.choice(data["bio_templates"][gender])
    bio = bio_template.format(name=name, age=age)
    return {
        "gender": gender,
        "name": name,
        "age": age,
        "bio": bio,
        "prompt_pool": HIGGSFIELD_PROMPTS[gender],
    }


def pick_story():
    with open(STORIES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return random.choice(data["stories"])


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


def draw_corner_tag(draw, text, font, width, height, corner, text_color):
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    pad_x = int(width * 0.02)
    pad_y = int(height * 0.01)

    if corner == "top-left":
        left = int(width * 0.04)
        top = int(height * 0.04)
    elif corner == "top-right":
        left = width - int(width * 0.04) - text_w - pad_x * 2
        top = int(height * 0.04)
    else:  # bottom-right
        left = width - int(width * 0.04) - text_w - pad_x * 2
        top = height - int(height * 0.04) - text_h - pad_y * 2

    right = left + text_w + pad_x * 2
    bottom = top + text_h + pad_y * 2
    draw.rectangle([(left, top), (right, bottom)], outline=ACCENT_COLOR, width=2, fill=(0, 0, 0, 150))
    draw.text((left + pad_x, top + pad_y - bbox[1]), text, font=font, fill=text_color)


def add_slide_text(base_rgb_image, text, slide_number, total_slides, profile):
    # KRUCIJALNO: pravimo .copy() od baznog isečenog portreta za SVAKI
    # slajd, da se tamni sloj i tekst ne gomilaju jedni na druge.
    img = base_rgb_image.copy().convert("RGBA")
    width, height = img.size

    dark_overlay = Image.new("RGBA", img.size, (0, 0, 0, 165))
    img = Image.alpha_composite(img, dark_overlay)
    draw = ImageDraw.Draw(img, "RGBA")

    try:
        font_size = int(width * 0.085)
        font = ImageFont.truetype(FONT_PATH, font_size)
        badge_font = ImageFont.truetype(FONT_PATH, int(width * 0.045))
        tag_font = ImageFont.truetype(FONT_PATH, int(width * 0.032))
    except OSError:
        log("UPOZORENJE: DejaVu font nije nađen, koristim default font.")
        font = ImageFont.load_default()
        badge_font = ImageFont.load_default()
        tag_font = ImageFont.load_default()
        font_size = 20

    text_upper = text.upper()
    max_width = int(width * 0.85)
    lines = wrap_text(draw, text_upper, font, max_width)

    line_height = int(font_size * 1.15)
    total_text_height = line_height * len(lines)

    y = (height - total_text_height) / 2
    for line in lines:
        draw_highlighted_line(draw, line, font, y, width)
        y += line_height

    draw_corner_tag(draw, f"{slide_number}/{total_slides}", badge_font, width, height, "top-left", (255, 255, 255, 255))
    draw_corner_tag(draw, f"{profile['name'].upper()}, {profile['age']}", tag_font, width, height, "top-right", ACCENT_COLOR)
    draw_corner_tag(draw, "SRPSKOMUVANJE.RS", tag_font, width, height, "bottom-right", ACCENT_COLOR)

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

    story = pick_story()
    total_slides = len(story["slides"])
    log(f"Izabrana priča: {story['title']} ({total_slides} slajdova)")

    portrait_url = generate_portrait_with_fallback(profile["prompt_pool"])
    log(f"Preuzimam portret: {portrait_url}")
    portrait_bytes = http_get_bytes_with_retry(portrait_url)
    base_image = Image.open(io.BytesIO(portrait_bytes)).convert("RGB")
    base_image = crop_to_fill(base_image, TARGET_WIDTH, TARGET_HEIGHT)

    image_urls = []
    for i, slide_text in enumerate(story["slides"]):
        log(f"Slajd {i + 1}/{total_slides}: {slide_text}")
        final_image = add_slide_text(base_image, slide_text, i + 1, total_slides, profile)
        url = upload_to_cloudinary(final_image)
        image_urls.append(url)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "title": f"{story['title']} - {profile['name']}, {profile['age']}",
                "image_urls": image_urls,
                "caption": profile["bio"],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    log(f"Gotovo. {len(image_urls)} slika spremno za carousel.")
    log(f"Caption: {profile['bio']}")


if __name__ == "__main__":
    main()
