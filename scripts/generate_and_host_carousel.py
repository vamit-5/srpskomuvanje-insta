#!/usr/bin/env python3
"""
generate_and_host_carousel.py
--------------------------------
1. Bira nasumičnu "priču" (niz od 6-7 slajdova koji grade narativ) iz
   content/stories.json.
2. Za SVAKI slajd traži PRAVU fotografiju preko Pexels API-ja (besplatno) -
   biramo scene koje prikazuju diskretnost, anonimnost i ljubav (siluete,
   senke, dodir ruku) - lice se NE vidi jasno, da izgleda autentično i da
   izbegnemo pravni rizik.
3. Iseca sliku na tačan format (1080x1350), tamni je (jak crni sloj preko
   cele slike) i ispisuje tekst tog slajda VELIKIM SLOVIMA po sredini
   (Pillow) - ključne reči su istaknute u ljubičastoj boji, ostatak beo.
   Dodaje brojčanu oznaku slajda (npr. "3/7") i mali brend tag. BEZ emoji.
4. Otpremi svaku sliku na Cloudinary.
5. Upisuje listu image_url-ova i glavni caption u output/carousel_content.json
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
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1350
PEXELS_SEARCH_URL = "https://api.pexels.com/v1/search"

# Ljubičasta/lila akcentna boja za istaknute reči - menjaj samo ovu liniju
# ako želiš drugu nijansu.
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
}

# Upiti biraju scene koje prikazuju DISKRETNOST, ANONIMNOST i LJUBAV -
# siluete, senke, dodir ruku - lice se NE vidi jasno. Izgleda autentično i
# izbegava pravni rizik.
SCENE_QUERIES = [
    "couple silhouette embrace night city",
    "hands touching candlelight love",
    "couple silhouette distance night street",
    "candlelight bar close up hands",
    "nightclub lights silhouette dancing",
    "couple walking night city from behind",
    "mysterious silhouette rain window night",
    "anonymous crowd blurred motion night city",
    "couple kissing silhouette sunset",
    "phone screen dark room scrolling hand",
]


def log(msg):
    print(f"[generate_and_host_carousel] {msg}", flush=True)


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


def pick_story():
    with open(STORIES_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return random.choice(data["stories"])


def pick_photo_url(api_key):
    query = random.choice(SCENE_QUERIES)
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


def draw_brand_tag(draw, font, width, height):
    text = "SRPSKOMUVANJE.RS"
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    pad_x = int(width * 0.02)
    pad_y = int(height * 0.01)

    left = width - int(width * 0.04) - text_w - pad_x * 2
    top = height - int(height * 0.04) - text_h - pad_y * 2
    right = left + text_w + pad_x * 2
    bottom = top + text_h + pad_y * 2

    draw.rectangle([(left, top), (right, bottom)], outline=ACCENT_COLOR, width=2, fill=(0, 0, 0, 150))
    draw.text((left + pad_x, top + pad_y - bbox[1]), text, font=font, fill=ACCENT_COLOR)


def draw_page_badge(draw, index, total, font, width, height):
    text = f"{index}/{total}"
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    pad_x = int(width * 0.025)
    pad_y = int(height * 0.012)

    left = int(width * 0.04)
    top = int(height * 0.04)
    right = left + text_w + pad_x * 2
    bottom = top + text_h + pad_y * 2

    draw.rectangle([(left, top), (right, bottom)], outline=ACCENT_COLOR, width=3, fill=(0, 0, 0, 160))
    draw.text((left + pad_x, top + pad_y - bbox[1]), text, font=font, fill=(255, 255, 255, 255))


def add_slide_text(image_bytes, text, slide_number, total_slides):
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = crop_to_fill(img, TARGET_WIDTH, TARGET_HEIGHT)
    width, height = img.size

    # Mnogo tamnija pozadina nego pre - tekst sad dominira, slika je
    # atmosfera u pozadini
    img = img.convert("RGBA")
    dark_overlay = Image.new("RGBA", img.size, (0, 0, 0, 165))
    img = Image.alpha_composite(img, dark_overlay)
    draw = ImageDraw.Draw(img, "RGBA")

    try:
        font_size = int(width * 0.085)
        font = ImageFont.truetype(FONT_PATH, font_size)
        badge_font = ImageFont.truetype(FONT_PATH, int(width * 0.045))
        tag_font = ImageFont.truetype(FONT_PATH, int(width * 0.03))
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

    draw_page_badge(draw, slide_number, total_slides, badge_font, width, height)
    draw_brand_tag(draw, tag_font, width, height)

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
    api_key = os.environ.get("PEXELS_API_KEY", "").strip()
    if not api_key:
        log("GREŠKA: nedostaje PEXELS_API_KEY.")
        raise SystemExit(1)

    story = pick_story()
    log(f"Izabrana priča: {story['title']} ({len(story['slides'])} slajdova)")

    total_slides = len(story["slides"])
    image_urls = []
    for i, slide_text in enumerate(story["slides"]):
        log(f"Slajd {i + 1}/{total_slides}: {slide_text}")
        photo_url = pick_photo_url(api_key)
        base_image = http_get_bytes_with_retry(photo_url)
        final_image = add_slide_text(base_image, slide_text, i + 1, total_slides)
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
