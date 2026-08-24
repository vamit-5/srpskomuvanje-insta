#!/usr/bin/env python3
"""
generate_and_host_carousel.py
---------------------------------
1. Bira SLUČAJNO iz "Srpskomuvanje/carousels/kartice" ili
   "Srpskomuvanje/carousels/obicne slike".
2. Nekad pravi carousel od VIŠE RAZLIČITIH slika, a nekad (samo za
   "obične slike") od JEDNE ISTE slike ponovljene više puta, sa RAZLIČITIM
   tekstom na svakom slajdu.
3. Svaki slajd se UVEK uklapa u tačan Instagram format 1080x1350 (4:5) -
   to je MAKSIMALNI portret format koji Instagram prikazuje BEZ da sam
   dodaje prazan prostor sa strane. Slika se NIKAD ne seče - prazan
   prostor se popunjava zamućenom uvećanom kopijom iste slike.
3a. "Kartice" - samo se uklope u format, BEZ IKAKVOG teksta ili logotipa.
3b. "Obične slike" - dodaje se kratka šokantna "Priznajem: ..." izjava, ne
    veliko i ne po sredini - u donjem delu slajda (malo iznad dna), na
    providnoj crnoj pozadini, beli tekst, istaknute reči lila-roza. Plus
    mini brojač slajda (npr. "3/6") u gornjem levom ćošku i mini brend
    bedž u gornjem desnom ćošku.
4. Otpremi sve slike na Cloudinary.
5. Bira generički CTA tekst (NIKAD u prvom licu) za caption celog carousela.
6. Upisuje rezultat u output/carousel_content.json za publish_carousel.py.
   Taj skript, POSLE uspešnog objavljivanja, premešta SVAKU iskorišćenu
   sliku u njen "Objavljeno" podfolder na Drive-u (svaku samo jednom, čak
   i ako je ista slika bila na više slajdova).
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

# 4:5 - Instagramov maksimalni portret format. Bilo šta "uže" od ovoga
# (npr. 3:4) Instagram sam uokviri praznim prostorom sa strane.
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1350

MIN_SLIDES = 4
MAX_SLIDES = 7
REPEAT_SAME_IMAGE_CHANCE = 0.5  # samo za "obične slike"

ACCENT_COLOR = (224, 102, 255, 255)  # lila-roza, za istaknute reči

HIGHLIGHT_WORDS = {
    "priznajem",
    "volim", "verujem", "tražim", "čekam",
    "besplatno", "besplatan", "besplatna",
    "diskretno", "diskretan", "diskretna",
}


def log(msg):
    print(f"[generate_and_host_carousel] {msg}", flush=True)


def choose_subtype_and_slides():
    """Bira folder (kartice/obicne slike) i broj slajdova. 'Kartice' idu
    UVEK sa različitim slikama (nikad ponavljanje - nema smisla ponavljati
    kartice pošto ostaju bez teksta). 'Obične slike' idu ili sa različitim
    slikama, ili (50% šanse, ili kad nema dovoljno različitih) sa jednom
    istom slikom ponovljenom više puta uz različit tekst na svakom slajdu."""
    candidates = ["kartice", "obicne slike"]
    random.shuffle(candidates)
    for subtype in candidates:
        count = gdrive_helper.count_images(CONTENT_TYPE, subtype)
        if count == 0:
            continue
        if subtype == "obicne slike":
            if count >= 2 and random.random() >= REPEAT_SAME_IMAGE_CHANCE:
                k = min(random.randint(MIN_SLIDES, MAX_SLIDES), count)
                return subtype, k, False
            k = random.randint(MIN_SLIDES, MAX_SLIDES)
            return subtype, k, True
        else:  # kartice
            if count >= 2:
                k = min(random.randint(MIN_SLIDES, MAX_SLIDES), count)
                return subtype, k, False
            continue
    raise RuntimeError("Nema dovoljno slika ni u jednom folderu za carousel (treba bar 2 slike).")


def pick_confessions(k):
    with open(CONFESSIONS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    pool = list(data["confessions"])
    if k <= len(pool):
        return random.sample(pool, k)
    result = list(pool)
    random.shuffle(result)
    while len(result) < k:
        result.append(random.choice(pool))
    return result[:k]


def pick_cta_caption():
    with open(CONFESSIONS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
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


_logo_cache = {}


def load_logo():
    if "img" not in _logo_cache:
        try:
            _logo_cache["img"] = Image.open(LOGO_PATH).convert("RGBA")
        except (FileNotFoundError, OSError):
            log(f"UPOZORENJE: {LOGO_PATH} nije nađen, crtam bez loga.")
            _logo_cache["img"] = None
    return _logo_cache["img"]


def draw_mini_badge(img, draw, width, height, corner="top-right"):
    """Mini brend bedž - logo + 'srpskomuvanje', UVEK u ćošku, potpuno
    minijaturan, ali čitljiv. Koristi se SAMO na 'običnim slikama' -
    kartice ostaju bez ikakvog dodatka."""
    logo = load_logo()
    text = "srpskomuvanje"
    try:
        badge_font = ImageFont.truetype(FONT_PATH, max(14, int(width * 0.024)))
    except OSError:
        badge_font = ImageFont.load_default()

    icon_size = max(16, int(width * 0.05))
    gap = int(width * 0.01)
    pad_x = int(width * 0.014)
    pad_y = int(width * 0.009)
    margin = int(width * 0.03)

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
    draw.rounded_rectangle([(left, top), (right, bottom)], radius=int(pad_y * 1.4), fill=(0, 0, 0, 140))

    cursor_x = left + pad_x
    center_y = (top + bottom) // 2

    if logo:
        logo_resized = logo.resize((icon_size, icon_size), Image.LANCZOS)
        img.paste(logo_resized, (cursor_x, center_y - icon_size // 2), logo_resized)
        cursor_x += icon_size + gap

    draw.text((cursor_x, center_y - text_h // 2 - bbox[1]), text, font=badge_font, fill=(255, 255, 255, 255))


def draw_slide_tag(draw, width, height, slide_number, total_slides):
    """Mini brojač slajda (npr. '3/6'), gornji levi ćošak, minijaturan."""
    text = f"{slide_number}/{total_slides}"
    try:
        font = ImageFont.truetype(FONT_PATH, max(14, int(width * 0.028)))
    except OSError:
        font = ImageFont.load_default()

    pad_x = int(width * 0.016)
    pad_y = int(width * 0.010)
    margin = int(width * 0.03)

    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    box_w = text_w + pad_x * 2
    box_h = text_h + pad_y * 2

    left, top = margin, margin
    right, bottom = left + box_w, top + box_h
    draw.rounded_rectangle([(left, top), (right, bottom)], radius=int(pad_y * 1.3), fill=(0, 0, 0, 140))
    draw.text((left + pad_x, top + pad_y - bbox[1]), text, font=font, fill=(255, 255, 255, 255))


def fit_within_canvas(img, target_w, target_h):
    """Uklapa CELU sliku (bez sečenja) unutar canvas-a TAČNIH dimenzija
    target_w x target_h. Prazan prostor se popunjava zamućenom uvećanom
    kopijom iste slike, tako da Instagram nikad sam ne dodaje prazan
    prostor sa strane."""
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
    """'Kartice' se SAMO uklapaju u tačan format - BEZ IKAKVOG teksta,
    brojača slajda ili logotipa preko slike."""
    img = Image.open(local_path)
    canvas = fit_within_canvas(img, TARGET_WIDTH, TARGET_HEIGHT)
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def render_obicna_slika_slide(local_path, confession, slide_number, total_slides):
    img = Image.open(local_path)
    canvas = fit_within_canvas(img, TARGET_WIDTH, TARGET_HEIGHT)
    width, height = canvas.size
    canvas = canvas.convert("RGBA")
    draw = ImageDraw.Draw(canvas, "RGBA")

    try:
        text_font = ImageFont.truetype(FONT_PATH, int(width * 0.062))
    except OSError:
        log("UPOZORENJE: DejaVu font nije nađen, koristim default font.")
        text_font = ImageFont.load_default()

    text_upper = confession.upper()
    max_width = int(width * 0.78)
    lines = wrap_text(draw, text_upper, text_font, max_width)

    line_height = int(text_font.size * 1.2) if hasattr(text_font, "size") else 26
    total_text_height = line_height * len(lines)

    pad_v = int(height * 0.025)
    bottom_margin = int(height * 0.09)  # "malo iznad dna" - ne skroz na dnu

    band_bottom = height - bottom_margin
    band_top = band_bottom - total_text_height - pad_v * 2
    band_left = int(width * 0.06)
    band_right = width - band_left

    draw.rounded_rectangle(
        [(band_left, band_top), (band_right, band_bottom)],
        radius=int(pad_v * 1.2),
        fill=(0, 0, 0, 165),
    )

    y = band_top + pad_v
    for line in lines:
        draw_highlighted_line(draw, line, text_font, y, width)
        y += line_height

    draw_slide_tag(draw, width, height, slide_number, total_slides)
    draw_mini_badge(canvas, draw, width, height, "top-right")

    canvas = canvas.convert("RGB")
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def upload_to_cloudinary(image_bytes, slide_index):
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
        f'Content-Disposition: form-data; name="file"; filename="slide_{slide_index}.jpg"\r\n'
        f"Content-Type: image/jpeg\r\n\r\n"
    ).encode("utf-8")
    body += image_bytes
    body += f"\r\n--{boundary}--\r\n".encode("utf-8")

    url = f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload"
    content_type = f"multipart/form-data; boundary={boundary}"

    log(f"Otpremam slajd {slide_index} na Cloudinary...")
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
    subtype, k, allow_repeat = choose_subtype_and_slides()
    log(f"Carousel: subtype={subtype}, slajdova={k}, ponavljanje_iste_slike={allow_repeat}")

    picks = gdrive_helper.pick_random_images_multi(CONTENT_TYPE, subtype, k, allow_repeat=allow_repeat)

    if subtype == "kartice":
        confessions_for_slides = [None] * len(picks)
    else:
        confessions_for_slides = pick_confessions(len(picks))

    image_urls = []
    gdrive_items = []
    for i, picked in enumerate(picks):
        if subtype == "kartice":
            final_image = render_kartica_slide(picked["local_path"])
        else:
            final_image = render_obicna_slika_slide(
                picked["local_path"], confessions_for_slides[i], i + 1, len(picks)
            )
        image_url = upload_to_cloudinary(final_image, i + 1)
        image_urls.append(image_url)
        gdrive_items.append(
            {
                "file_id": picked["file_id"],
                "file_name": picked["file_name"],
                "source_folder_id": picked["source_folder_id"],
            }
        )

    caption = pick_cta_caption()
    title = f"{CONTENT_TYPE}/{subtype}"

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "title": title,
                "image_urls": image_urls,
                "caption": caption,
                "gdrive_items": gdrive_items,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    log(f"Gotovo. Carousel sa {len(image_urls)} slika spreman.")


if __name__ == "__main__":
    main()
