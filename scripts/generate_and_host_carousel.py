#!/usr/bin/env python3
"""
generate_and_host_carousel.py
--------------------------------
1. Bira nasumično "kartice" ili "obicne slike" iz
   "Srpskomuvanje/carousels/" na Google Drive-u (koji god ima dovoljno
   slika - treba bar 2 za carousel).
2a. Ako je "kartice" - bira NEKOLIKO RAZLIČITIH gotovih slika (4-7,
    zavisno koliko ih ima) i koristi ih TAČNO onakve kakve jesu, bez
    ikakve izmene, kao slajdove.
2b. Ako je "obicne slike" - ponekad bira NEKOLIKO RAZLIČITIH slika (svaka
    sa svojim "Priznajem..." tekstom), a ponekad bira JEDNU te ISTU sliku i
    ponavlja je na svim slajdovima sa RAZLIČITIM tekstom na svakom (nasumično
    se bira koji od ta dva načina). Svaka slika se uklapa CELA (bez sečenja)
    u format 1080x1350 (3:4), sa zamućenom pozadinom da popuni prazan
    prostor, plus broj slajda gore levo i logo+brend dole desno.
3. Otpremi svaku sliku na Cloudinary (besplatan hosting) da dobije javni
   URL (Instagram mora da povuče slike sa javnog linka).
4. Bira nasumičan CTA caption i upisuje sve (image_urls, caption, podatke
   o slikama sa Drive-a) u output/carousel_content.json za
   publish_carousel.py. Taj skript, POSLE uspešnog objavljivanja, premešta
   svaku iskorišćenu sliku u "Objavljeno" na Drive-u da se nikad ne ponovi.
"""

import io
import json
import os
import random
import time
import urllib.error
import urllib.request
import uuid

from PIL import Image, ImageDraw, ImageFilter, ImageFont

import gdrive_helper

CONTENT_TYPE = "carousels"
CONFESSIONS_FILE = "content/confessions.json"
OUTPUT_FILE = "output/carousel_content.json"
LOGO_PATH = "logo.png"
MAX_RETRIES = 5
RETRY_DELAYS = [5, 10, 20, 40]
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1350
MIN_SLIDES = 4
MAX_SLIDES = 7
# Šansa (0-1) da se za "obicne slike" ponovi JEDNA ista slika na svim
# slajdovima (sa različitim tekstom), umesto da se uzme više različitih slika.
REPEAT_SAME_IMAGE_CHANCE = 0.5

# Ljubičasta/lila akcentna boja - menjaj samo ovu liniju ako želiš drugu
# nijansu.
ACCENT_COLOR = (191, 64, 255, 255)

HIGHLIGHT_WORDS = {
    "priznajem",
    "volim", "verujem", "tražim", "čekam",
    "besplatno", "besplatan", "besplatna",
    "diskretno", "diskretan", "diskretna",
}


def log(msg):
    print(f"[generate_and_host_carousel] {msg}", flush=True)


def load_confessions_data():
    with open(CONFESSIONS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def pick_confessions(k):
    data = load_confessions_data()
    pool = data["confessions"][:]
    random.shuffle(pool)
    if k <= len(pool):
        return pool[:k]
    # Ako treba više izjava nego što ih imamo, dopuni sa ponavljanjem.
    result = pool[:]
    while len(result) < k:
        result.append(random.choice(data["confessions"]))
    return result[:k]


def pick_cta_caption():
    data = load_confessions_data()
    return random.choice(data["cta_captions"])


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


_logo_cache = {}


def load_logo():
    if "img" not in _logo_cache:
        try:
            _logo_cache["img"] = Image.open(LOGO_PATH).convert("RGBA")
        except (FileNotFoundError, OSError):
            log(f"UPOZORENJE: {LOGO_PATH} nije nađen, crtam samo tekst bez loga.")
            _logo_cache["img"] = None
    return _logo_cache["img"]


def draw_brand_badge(img, draw, width, height, corner="bottom-right"):
    logo = load_logo()
    text = "srpskomuvanje"
    try:
        badge_font = ImageFont.truetype(FONT_PATH, int(width * 0.038))
    except OSError:
        badge_font = ImageFont.load_default()

    icon_size = int(width * 0.09)
    gap = int(width * 0.018)
    pad_x = int(width * 0.022)
    pad_y = int(width * 0.015)
    margin = int(width * 0.04)

    bbox = draw.textbbox((0, 0), text, font=badge_font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    content_w = (icon_size + gap if logo else 0) + text_w
    content_h = max(icon_size if logo else 0, text_h)
    box_w = content_w + pad_x * 2
    box_h = content_h + pad_y * 2

    if corner == "top-left":
        left, top = margin, margin
    elif corner == "top-right":
        left, top = width - margin - box_w, margin
    elif corner == "bottom-left":
        left, top = margin, height - margin - box_h
    else:  # bottom-right
        left, top = width - margin - box_w, height - margin - box_h

    right, bottom = left + box_w, top + box_h
    draw.rounded_rectangle([(left, top), (right, bottom)], radius=int(pad_y * 1.3), fill=(0, 0, 0, 145))

    cursor_x = left + pad_x
    center_y = (top + bottom) // 2

    if logo:
        logo_resized = logo.resize((icon_size, icon_size), Image.LANCZOS)
        img.paste(logo_resized, (cursor_x, center_y - icon_size // 2), logo_resized)
        cursor_x += icon_size + gap

    draw.text((cursor_x, center_y - text_h // 2 - bbox[1]), text, font=badge_font, fill=(255, 255, 255, 255))


def fit_within_canvas(img, target_w, target_h):
    """Uklapa CELU sliku (bez sečenja) unutar canvas-a, sa zamućenom
    uvećanom kopijom iste slike kao pozadinom da popuni prazan prostor."""
    img = img.convert("RGB")
    src_w, src_h = img.size

    fit_scale = min(target_w / src_w, target_h / src_h)
    fit_w, fit_h = max(1, int(src_w * fit_scale)), max(1, int(src_h * fit_scale))
    fitted = img.resize((fit_w, fit_h), Image.LANCZOS)

    bg_scale = max(target_w / src_w, target_h / src_h)
    bg_w, bg_h = max(1, int(src_w * bg_scale)), max(1, int(src_h * bg_scale))
    bg = img.resize((bg_w, bg_h), Image.LANCZOS)
    bg_left = (bg_w - target_w) // 2
    bg_top = (bg_h - target_h) // 2
    bg = bg.crop((bg_left, bg_top, bg_left + target_w, bg_top + target_h))
    bg = bg.filter(ImageFilter.GaussianBlur(45))
    dark = Image.new("RGB", bg.size, (0, 0, 0))
    bg = Image.blend(bg, dark, 0.35)

    canvas = bg.copy()
    paste_x = (target_w - fit_w) // 2
    paste_y = (target_h - fit_h) // 2
    canvas.paste(fitted, (paste_x, paste_y))
    return canvas


def render_kartica_slide(local_path):
    """'Kartice' se NE DIRAJU - samo se propuštaju u JPEG format tačno
    onakve kakve jesu (bez sečenja, teksta ili brojeva slajda)."""
    img = Image.open(local_path).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def render_obicna_slika_slide(local_path, confession, slide_number, total_slides):
    img = Image.open(local_path)
    canvas = fit_within_canvas(img, TARGET_WIDTH, TARGET_HEIGHT)
    width, height = canvas.size
    canvas = canvas.convert("RGBA")
    draw = ImageDraw.Draw(canvas, "RGBA")

    try:
        text_font = ImageFont.truetype(FONT_PATH, int(width * 0.075))
        badge_font = ImageFont.truetype(FONT_PATH, int(width * 0.045))
    except OSError:
        log("UPOZORENJE: DejaVu font nije nađen, koristim default font.")
        text_font = ImageFont.load_default()
        badge_font = ImageFont.load_default()

    text_upper = confession.upper()
    max_width = int(width * 0.85)
    lines = wrap_text(draw, text_upper, text_font, max_width)

    line_height = int(text_font.size * 1.15) if hasattr(text_font, "size") else 28
    total_text_height = line_height * len(lines)

    y = (height - total_text_height) / 2
    for line in lines:
        draw_highlighted_line(draw, line, text_font, y, width)
        y += line_height

    draw_corner_tag(draw, f"{slide_number}/{total_slides}", badge_font, width, height, "top-left", (255, 255, 255, 255))
    draw_brand_badge(canvas, draw, width, height, "bottom-right")

    canvas = canvas.convert("RGB")
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=90)
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


def choose_subtype_and_slides():
    """Nasumično bira 'kartice' ili 'obicne slike' (koji god ima dovoljno
    slika - bar 2), i koliko slajdova ćemo praviti. Vraća (subtype, k,
    allow_repeat)."""
    subtypes = random.sample(gdrive_helper.SUBTYPES, len(gdrive_helper.SUBTYPES))
    for st in subtypes:
        available = gdrive_helper.count_images(CONTENT_TYPE, st)
        if st == "obicne slike" and available >= 1 and random.random() < REPEAT_SAME_IMAGE_CHANCE:
            k = random.randint(MIN_SLIDES, MAX_SLIDES)
            return st, k, True
        if available >= 2:
            k = min(random.randint(MIN_SLIDES, MAX_SLIDES), available)
            return st, k, False
    raise RuntimeError(
        "Nema dovoljno slika ni u 'kartice' ni u 'obicne slike' unutar 'carousels' "
        "(treba bar 2) - ubaci još slika na Drive pa pokreni ponovo."
    )


def main():
    subtype, k, allow_repeat = choose_subtype_and_slides()
    picked_list = gdrive_helper.pick_random_images_multi(CONTENT_TYPE, subtype, k, allow_repeat=allow_repeat)
    total_slides = len(picked_list)
    log(f"Carousel: carousels/{subtype}, {total_slides} slajdova, ista slika ponovljena: {allow_repeat}")

    if subtype == "kartice":
        confessions = [None] * total_slides
    else:
        confessions = pick_confessions(total_slides)

    image_urls = []
    for i, picked in enumerate(picked_list):
        log(f"Slajd {i + 1}/{total_slides}: {picked['file_name']}")
        if subtype == "kartice":
            final_image = render_kartica_slide(picked["local_path"])
        else:
            final_image = render_obicna_slika_slide(picked["local_path"], confessions[i], i + 1, total_slides)
        url = upload_to_cloudinary(final_image)
        image_urls.append(url)

    caption = pick_cta_caption()

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "title": f"Carousel - {subtype}",
                "image_urls": image_urls,
                "caption": caption,
                "gdrive_items": [
                    {
                        "file_id": p["file_id"],
                        "file_name": p["file_name"],
                        "source_folder_id": p["source_folder_id"],
                    }
                    for p in picked_list
                ],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    log(f"Gotovo. {len(image_urls)} slika spremno za carousel.")
    log(f"Caption: {caption}")


if __name__ == "__main__":
    main()
