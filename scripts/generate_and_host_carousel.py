#!/usr/bin/env python3
"""
generate_and_host_carousel.py
---------------------------------
1. Bira "Srpskomuvanje/carousels/kartice" ili "carousels/obicne slike".
   "Kartice" imaju PRIORITET (gdrive_helper.KARTICE_WEIGHT - 70% šanse kad
   ima bar 2 kartice) jer su već gotov dizajn i ne treba im nikakva obrada.
2. Nekad pravi carousel od VIŠE RAZLIČITIH slika, a nekad (samo za
   "obične slike") od JEDNE ISTE slike ponovljene više puta, sa RAZLIČITIM
   tekstom na svakom slajdu.
3. Svaki slajd se UVEK uklapa u tačan Instagram format 1080x1350 (4:5).
   Slika se NIKAD ne seče - prazan prostor se popunjava zamućenom
   uvećanom kopijom iste slike. Svaki otpremljeni URL se dodatno
   "osigurava" eksplicitnom Cloudinary transformacijom (tačne dimenzije u
   URL-u) da ne bi slučajno došlo do praznog prostora sa strane.
3a. "Kartice" - samo se uklope u format, BEZ IKAKVOG teksta ili logotipa -
    ovde se ništa ne uređuje, ni na jednom slajdu, samo se postavljaju.
3b. "Obične slike" - dodaje se kratka šokantna/"uhvatljiva" izjava (ne
    veliko, ne po sredini) - u donjem delu slajda, na providnoj crnoj
    pozadini, beli tekst, istaknute reči lila-roza. Mini brend bedž je pri
    DNU na sredini slike (van dometa Instagramovog brojača slajdova gore).
    NEMA više brojanja slajdova (1/6, 2/6...) nacrtanog na slici - to
    Instagram već sam prikazuje svojim ugrađenim indikatorom.
3c. POSLEDNJI slajd "običnih slika" carousela je UVEK poseban - poziva
    gledaoca da se besplatno pridruži prvom srpskom dating app-u, umesto
    obične "Priznajem..." izjave.
4. Otpremi sve slike na Cloudinary.
5. Bira preuveličan/hype CTA tekst (NIKAD u prvom licu) za caption celog
   carousela.
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

# 4:5 - zvanično podržan Instagram Graph API opseg je 4:5 do 1.91:1.
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1350

MIN_SLIDES = 4
MAX_SLIDES = 7
REPEAT_SAME_IMAGE_CHANCE = 0.5  # samo za "obične slike"

# Šansa da se za "obične slike" carousel iskoristi novi "hook" par
# (statistika o Srbiji) na jednom od slajdova - kod "hook" para su
# tekst na tom slajdu i CELI caption carousela MEĐUSOBNO POVEZANI (prva
# rečenica je na slici, ostatak ide u opis). Inače se slajdovi i caption
# biraju nezavisno kao i do sada.
HOOK_CHANCE = 0.5

ACCENT_COLOR = (224, 102, 255, 255)  # lila-roza, za istaknute reči

HIGHLIGHT_WORDS = {
    "priznajem",
    "volim", "verujem", "tražim", "čekam",
    "besplatno", "besplatan", "besplatna",
    "diskretno", "diskretan", "diskretna",
    "opasna", "opasne", "opasnim",
    "slobodna",
    "pridruži", "skini", "uđi",
}


def log(msg):
    print(f"[generate_and_host_carousel] {msg}", flush=True)


def choose_subtype_and_slides():
    """Bira folder i broj slajdova. 'Kartice' imaju PRIORITET
    (gdrive_helper.KARTICE_WEIGHT šanse) kad ima bar 2 kartice - idu UVEK
    sa različitim slikama (nikad ponavljanje, nema smisla ponavljati
    kartice pošto ostaju bez teksta). 'Obične slike' idu ili sa različitim
    slikama, ili (50% šanse, ili kad nema dovoljno različitih) sa jednom
    istom slikom ponovljenom više puta uz različit tekst na svakom
    slajdu."""
    kartice_count = gdrive_helper.count_images(CONTENT_TYPE, "kartice")
    obicne_count = gdrive_helper.count_images(CONTENT_TYPE, "obicne slike")

    if kartice_count == 0 and obicne_count == 0:
        raise RuntimeError("Nema dovoljno slika ni u jednom folderu za carousel (treba bar 2 slike).")

    prefer_kartice = kartice_count >= 2 and (
        obicne_count < 2 or random.random() < gdrive_helper.KARTICE_WEIGHT
    )

    if prefer_kartice:
        k = min(random.randint(MIN_SLIDES, MAX_SLIDES), kartice_count)
        return "kartice", k, False

    if obicne_count >= 2 and random.random() >= REPEAT_SAME_IMAGE_CHANCE:
        k = min(random.randint(MIN_SLIDES, MAX_SLIDES), obicne_count)
        return "obicne slike", k, False

    if obicne_count >= 1:
        k = random.randint(MIN_SLIDES, MAX_SLIDES)
        return "obicne slike", k, True

    # Nema dovoljno "običnih slika", a kartica ima samo 1 - ne može carousel.
    raise RuntimeError("Nema dovoljno slika ni u jednom folderu za carousel (treba bar 2 slike).")


def pick_confessions(k):
    """Bira k tekstova za slajdove: poslednji je UVEK poseban 'pridruži se
    besplatno' poziv, ostali su nasumične 'Priznajem...' izjave."""
    with open(CONFESSIONS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    pool = list(data["confessions"])
    closing_pool = list(data["closing_slide_texts"])

    regular_needed = k - 1
    if regular_needed <= len(pool):
        regular = random.sample(pool, regular_needed)
    else:
        regular = list(pool)
        random.shuffle(regular)
        while len(regular) < regular_needed:
            regular.append(random.choice(pool))
        regular = regular[:regular_needed]

    closing = random.choice(closing_pool)
    return regular + [closing]


def pick_cta_caption():
    with open(CONFESSIONS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return random.choice(data["cta_captions"])


def pick_confessions_and_caption(k):
    """Bira k tekstova za slajdove (poslednji je UVEK poseban 'pridruži se
    besplatno' poziv) PLUS caption za ceo carousel. Sa HOOK_CHANCE
    verovatnoćom, JEDAN od običnih slajdova koristi tekst iz novog 'hooks'
    para (statistika o Srbiji), a caption celog carousela je VEZAN za TAJ
    ISTI hook (prva rečenica na slici, ostatak u opisu). Inače se slajdovi
    i caption biraju nezavisno kao i do sada."""
    with open(CONFESSIONS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    pool = list(data["confessions"])
    closing_pool = list(data["closing_slide_texts"])
    hooks = data.get("hooks", [])

    regular_needed = k - 1
    if regular_needed <= len(pool):
        regular = random.sample(pool, regular_needed)
    else:
        regular = list(pool)
        random.shuffle(regular)
        while len(regular) < regular_needed:
            regular.append(random.choice(pool))
        regular = regular[:regular_needed]

    if hooks and regular_needed >= 1 and random.random() < HOOK_CHANCE:
        hook = random.choice(hooks)
        slot = random.randrange(regular_needed)
        regular[slot] = hook["overlay"]
        caption = hook["caption"]
    else:
        caption = random.choice(data["cta_captions"])

    closing = random.choice(closing_pool)
    return regular + [closing], caption


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


def draw_mini_badge(img, draw, width, height, position="bottom-center"):
    """Mini brend bedž - logo + 'srpskomuvanje', minijaturan ali čitljiv.
    Podrazumevano je pri DNU, na sredini slike - namerno NE u ćošku, jer
    Instagram tamo prikazuje svoj brojač slajdova (npr. '1/4') koji bi ga
    prekrio. Koristi se SAMO na 'običnim slikama' - kartice ostaju bez
    ikakvog dodatka."""
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
    draw.rounded_rectangle([(left, top), (right, bottom)], radius=int(pad_y * 1.4), fill=(0, 0, 0, 150))

    cursor_x = left + pad_x
    center_y = (top + bottom) // 2

    if logo:
        logo_resized = logo.resize((icon_size, icon_size), Image.LANCZOS)
        img.paste(logo_resized, (cursor_x, center_y - icon_size // 2), logo_resized)
        cursor_x += icon_size + gap

    draw.text((cursor_x, center_y - text_h // 2 - bbox[1]), text, font=badge_font, fill=(255, 255, 255, 255))


def fit_within_canvas(img, target_w, target_h):
    """Uklapa CELU sliku (bez sečenja) unutar canvas-a TAČNIH dimenzija
    target_w x target_h. Prazan prostor se popunjava zamućenom uvećanom
    kopijom iste slike."""
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
    """'Kartice' se SAMO uklapaju u tačan format - BEZ IKAKVOG teksta ili
    logotipa preko slike, ni na jednom slajdu (uključujući poslednji)."""
    img = Image.open(local_path)
    canvas = fit_within_canvas(img, TARGET_WIDTH, TARGET_HEIGHT)
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def render_obicna_slika_slide(local_path, text):
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

    text_upper = text.upper()
    max_width = int(width * 0.78)
    lines = wrap_text(draw, text_upper, text_font, max_width)

    line_height = int(text_font.size * 1.2) if hasattr(text_font, "size") else 26
    total_text_height = line_height * len(lines)

    pad_v = int(height * 0.025)
    bottom_margin = int(height * 0.15)  # ostavlja mesta za mini bedž ispod

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

    draw_mini_badge(canvas, draw, width, height, "bottom-center")

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


def force_cloudinary_dimensions(url, width, height):
    """Ubacuje eksplicitnu Cloudinary transformaciju u URL da GARANTUJE
    tačne finalne dimenzije slajda - bez obzira na podešavanja upload
    preset-a."""
    marker = "/upload/"
    idx = url.find(marker)
    if idx == -1:
        return url
    insert_at = idx + len(marker)
    transform = f"w_{width},h_{height},c_fit,q_auto/"
    return url[:insert_at] + transform + url[insert_at:]


def main():
    subtype, k, allow_repeat = choose_subtype_and_slides()
    log(f"Carousel: subtype={subtype}, slajdova={k}, ponavljanje_iste_slike={allow_repeat}")

    picks = gdrive_helper.pick_random_images_multi(CONTENT_TYPE, subtype, k, allow_repeat=allow_repeat)

    if subtype == "kartice":
        texts_for_slides = [None] * len(picks)
        caption = pick_cta_caption()
    else:
        texts_for_slides, caption = pick_confessions_and_caption(len(picks))

    image_urls = []
    gdrive_items = []
    for i, picked in enumerate(picks):
        if subtype == "kartice":
            final_image = render_kartica_slide(picked["local_path"])
        else:
            final_image = render_obicna_slika_slide(picked["local_path"], texts_for_slides[i])
        image_url = upload_to_cloudinary(final_image, i + 1)
        image_url = force_cloudinary_dimensions(image_url, TARGET_WIDTH, TARGET_HEIGHT)
        image_urls.append(image_url)
        gdrive_items.append(
            {
                "file_id": picked["file_id"],
                "file_name": picked["file_name"],
                "source_folder_id": picked["source_folder_id"],
            }
        )

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
