#!/usr/bin/env python3
"""
generate_and_host_story.py
-----------------------------
1. Uzima SLEDEĆU neiskorišćenu sliku sa Google Drive-a, MEŠANO iz bilo kog
   od sva 4 foldera: "Srpskomuvanje/feed/kartice", "feed/obicne slike",
   "carousels/kartice", "carousels/obicne slike". "Kartice" imaju
   PRIORITET (gdrive_helper.KARTICE_WEIGHT - 70% šanse) jer su već gotov
   dizajn i ne treba im nikakva obrada.
2. Slika se UVEK uklapa u tačan Instagram Story format 1080x1920 (9:16),
   BEZ sečenja - prazan prostor se popunjava zamućenom uvećanom kopijom
   iste slike.
2a. Ako je slika "kartica" (već gotov dizajn) - NIŠTA se ne dodaje preko
    slike (bez teksta, bez CTA, bez logotipa). Samo se uklopi u format -
    ovde se ništa ne uređuje, samo se postavlja.
2b. Ako je "obična slika" - dodaje se kratka šokantna/"uhvatljiva" izjava
    (u donjem delu slike, ne po sredini, ne skroz na dnu) + CTA linija +
    mini brend bedž pri dnu na sredini, pošto Instagram Stories nemaju
    caption pa CTA MORA biti na samoj slici.
3. Otpremi finalnu sliku na Cloudinary da dobije javni URL. URL se
   dodatno "osigurava" eksplicitnom Cloudinary transformacijom (tačne
   dimenzije u URL-u) da bilo kakva podešavanja upload preset-a ne mogu
   slučajno da promene format.
4. Upisuje rezultat (image_url i podatke o slici sa Drive-a) u
   output/story_content.json za publish_story.py. Taj skript, POSLE
   uspešnog objavljivanja, premešta iskorišćenu sliku u "Objavljeno"
   folder na Drive-u da se nikad ne ponovi.

NAPOMENA: Instagram Content Publishing API ne podržava caption za Stories,
zato se sav tekst (uključujući CTA) ispisuje direktno na sliku - ali SAMO
za "obične slike". "Kartice" ostaju potpuno čiste u svim formatima.
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

CONFESSIONS_FILE = "content/confessions.json"
OUTPUT_FILE = "output/story_content.json"
LOGO_PATH = "logo.png"
MAX_RETRIES = 5
RETRY_DELAYS = [5, 10, 20, 40]
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1920
CTA_TEXT = "SRPSKOMUVANJE.RS - LINK U BIO-U"

ACCENT_COLOR = (224, 102, 255, 255)  # lila-roza, za istaknute reči

HIGHLIGHT_WORDS = {
    "priznajem",
    "volim", "verujem", "tražim", "čekam",
    "besplatno", "besplatan", "besplatna",
    "diskretno", "diskretan", "diskretna",
    "opasna", "opasne", "opasnim",
    "slobodna",
}

# Reči koje se NIKAD ne biraju kao "glavna reč" kad HIGHLIGHT_WORDS ne
# pogodi ništa (veznici, predlozi i sl. - ne nose poentu rečenice).
STOPWORDS = {
    "u", "i", "je", "su", "sam", "smo", "ste", "se", "da", "na", "za", "sa",
    "od", "do", "ni", "ne", "a", "o", "pa", "ali", "ili", "kao", "sto", "što",
    "koji", "koja", "koje", "kod", "iz", "po", "ka", "još", "vec", "već",
    "ovog", "meseca", "godine", "godina", "srbiji", "srbija", "ti", "je,",
}

# Tekst na Story slici se NIKAD ne sme protegnuti preko previše prostora -
# font se automatski smanjuje (do MIN_FONT_SCALE) dok cela izjava (sa
# pozadinom) ne stane unutar MAX_BAND_HEIGHT_FRACTION visine slike.
BASE_FONT_SCALE = 0.062
MIN_FONT_SCALE = 0.032
MAX_BAND_HEIGHT_FRACTION = 0.30


def log(msg):
    print(f"[generate_and_host_story] {msg}", flush=True)


def pick_confession():
    with open(CONFESSIONS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return random.choice(data["confessions"])


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


def fit_text_and_font(draw, text_upper, width, height, max_width, reserved_extra=0):
    """Nalazi NAJVEĆI font (počev od BASE_FONT_SCALE naniže) za koji cela
    izjava (sa pozadinom, i sa mestom rezervisanim za CTA liniju ispod nje
    - reserved_extra) staje unutar MAX_BAND_HEIGHT_FRACTION visine slike -
    da tekst NIKAD ne prekrije prevelik deo slike, bez obzira koliko je
    izjava duga."""
    max_band_height = int(height * MAX_BAND_HEIGHT_FRACTION) - reserved_extra
    max_band_height = max(max_band_height, int(height * 0.08))
    scale = BASE_FONT_SCALE
    while scale >= MIN_FONT_SCALE:
        try:
            font = ImageFont.truetype(FONT_PATH, int(width * scale))
        except OSError:
            font = ImageFont.load_default()
            return font, wrap_text(draw, text_upper, font, max_width)
        lines = wrap_text(draw, text_upper, font, max_width)
        line_height = int(font.size * 1.18)
        if line_height * len(lines) <= max_band_height:
            return font, lines
        scale -= 0.004
    font = ImageFont.truetype(FONT_PATH, int(width * MIN_FONT_SCALE))
    return font, wrap_text(draw, text_upper, font, max_width)


def pick_highlight_words(text_upper):
    """Vraća SET reči koje treba istaći lila-roza bojom. Prvo probamo
    fiksnu listu HIGHLIGHT_WORDS. Ako nijedna reč iz teksta ne pogodi tu
    listu, automatski biramo GLAVNU reč: prvo broj, inače najdužu 'pravu'
    reč - da SVAKI tekst na slici uvek ima bar jednu istaknutu reč."""
    words = text_upper.split()
    normalized = [normalize_word(w) for w in words]

    if any(w in HIGHLIGHT_WORDS for w in normalized):
        return HIGHLIGHT_WORDS

    for w in normalized:
        if any(ch.isdigit() for ch in w):
            return {w}

    candidates = [w for w in normalized if w and w not in STOPWORDS and len(w) > 3]
    if candidates:
        return {max(candidates, key=len)}

    return {max(normalized, key=len)} if normalized else set()


def draw_highlighted_line(draw, line, font, y, canvas_width, highlight_words):
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
        is_highlight = normalize_word(word) in highlight_words
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


_logo_cache = {}


def load_logo():
    if "img" not in _logo_cache:
        try:
            _logo_cache["img"] = Image.open(LOGO_PATH).convert("RGBA")
        except (FileNotFoundError, OSError):
            log(f"UPOZORENJE: {LOGO_PATH} nije nađen, crtam samo tekst bez loga.")
            _logo_cache["img"] = None
    return _logo_cache["img"]


def draw_mini_badge(img, draw, width, height, position="bottom-center"):
    """Mini brend bedž - logo + 'srpskomuvanje', minijaturan ali čitljiv.
    Podrazumevano je pri DNU, na sredini slike. Koristi se SAMO na
    'običnim slikama' - kartice ostaju bez ikakvog dodatka."""
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

    if position == "bottom-center":
        left, top = (width - box_w) // 2, height - margin - box_h
    elif position == "top-left":
        left, top = margin, margin
    elif position == "top-right":
        left, top = width - margin - box_w, margin
    elif position == "bottom-left":
        left, top = margin, height - margin - box_h
    else:  # bottom-right
        left, top = width - margin - box_w, height - margin - box_h

    right, bottom = left + box_w, top + box_h
    draw.rounded_rectangle([(left, top), (right, bottom)], radius=int(pad_y * 1.4), fill=(0, 0, 0, 90))

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


def render_kartica_story(local_path):
    """'Kartice' se SAMO uklapaju u Story format (bez sečenja) - potpuno
    BEZ teksta, CTA linije ili logotipa. Ovde se ništa ne uređuje, samo se
    slika postavlja."""
    img = Image.open(local_path)
    canvas = fit_within_canvas(img, TARGET_WIDTH, TARGET_HEIGHT)
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def render_obicna_slika_story(local_path):
    img = Image.open(local_path)
    canvas = fit_within_canvas(img, TARGET_WIDTH, TARGET_HEIGHT)
    width, height = canvas.size
    canvas = canvas.convert("RGBA")

    # VAŽNO: sve što ima providnu (alpha) pozadinu crta se na POSEBNOM
    # potpuno providnom sloju, koji se tek na kraju "stopi" (alpha_composite)
    # sa slikom. Direktno crtanje providne boje na samu sliku (kako je bilo
    # ranije) u Pillow-u NE meša boje - samo PREPIŠE piksele, pa providna
    # crna pozadina ispadne potpuno NEPROVIDNA (čisto crna) na finalnoj
    # slici. Ovo je bio pravi uzrok "pozadina nije providna" problema.
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay, "RGBA")

    confession = pick_confession()

    try:
        cta_font = ImageFont.truetype(FONT_PATH, int(width * 0.038))
    except OSError:
        log("UPOZORENJE: DejaVu font nije nađen, koristim default font.")
        cta_font = ImageFont.load_default()

    text_upper = confession.upper()
    max_width = int(width * 0.78)
    cta_line_height = int(cta_font.size * 1.3) if hasattr(cta_font, "size") else 20
    gap = int(height * 0.018)
    text_font, lines = fit_text_and_font(
        draw, text_upper, width, height, max_width, reserved_extra=gap + cta_line_height
    )
    highlight_words = pick_highlight_words(text_upper)

    line_height = int(text_font.size * 1.18) if hasattr(text_font, "size") else 26

    total_text_height = line_height * len(lines) + gap + cta_line_height

    pad_v = int(height * 0.022)
    bottom_margin = int(height * 0.13)  # ostavlja mesta za mini bedž ispod

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
        draw_highlighted_line(draw, line, text_font, y, width, highlight_words)
        y += line_height

    y += gap
    draw_accent_line(draw, CTA_TEXT, cta_font, y, width)

    draw_mini_badge(overlay, draw, width, height, "bottom-center")

    canvas = Image.alpha_composite(canvas, overlay)
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


def force_cloudinary_dimensions(url, width, height):
    """Ubacuje eksplicitnu Cloudinary transformaciju u URL da GARANTUJE
    tačne finalne dimenzije slike - bez obzira na podešavanja upload
    preset-a."""
    marker = "/upload/"
    idx = url.find(marker)
    if idx == -1:
        return url
    insert_at = idx + len(marker)
    transform = f"w_{width},h_{height},c_fit,q_auto/"
    return url[:insert_at] + transform + url[insert_at:]


def main():
    picked = gdrive_helper.pick_random_story_source()
    log(f"Slika: {picked['content_type']}/{picked['subtype']}/{picked['file_name']}")

    if picked["subtype"] == "kartice":
        final_image = render_kartica_story(picked["local_path"])
    else:
        final_image = render_obicna_slika_story(picked["local_path"])

    image_url = upload_to_cloudinary(final_image)
    image_url = force_cloudinary_dimensions(image_url, TARGET_WIDTH, TARGET_HEIGHT)

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "category": f"{picked['content_type']}/{picked['subtype']}",
                "image_url": image_url,
                "gdrive_file_id": picked["file_id"],
                "gdrive_file_name": picked["file_name"],
                "gdrive_source_folder_id": picked["source_folder_id"],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    log(f"Gotovo. Story slika: {image_url}")


if __name__ == "__main__":
    main()
