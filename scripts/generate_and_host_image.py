#!/usr/bin/env python3
"""
generate_and_host_image.py
----------------------------
1. Uzima SLEDEĆU neiskorišćenu sliku sa Google Drive-a, nasumično iz
   "Srpskomuvanje/feed/kartice" ili "Srpskomuvanje/feed/obicne slike" - te
   slike korisnik SAM generiše i ručno ih ubacuje na Drive.
2a. Ako je slika iz "kartice" (već gotov, dizajniran izgled) - NE DIRA SE
    NIKAKO, samo se propušta u JPEG format i šalje dalje tačno onakva
    kakva jeste.
2b. Ako je slika iz "obicne slike" (obična, neuređena slika) - uklapa se
    CELA (bez sečenja) u format 1080x1350 (3:4), sa blago zamućenom
    pozadinom iste slike da popuni prazan prostor, i ispisuje se kratka
    "Priznajem: ..." izjava VELIKIM SLOVIMA (Pillow), plus logo + brend
    tekst u ćošku.
3. Otpremi finalnu sliku na Cloudinary (besplatan hosting) da dobije javni
   URL (Instagram mora da povuče sliku sa javnog linka).
4. Bira nasumičan CTA caption (poziv na akciju za srpskomuvanje.rs, NE
   piše u prvom licu kao da je to profil osobe sa slike) i upisuje sve
   (image_url, caption, podatke o slici sa Drive-a) u
   output/post_content.json za publish_feed.py. Taj skript, POSLE
   uspešnog objavljivanja, premešta iskorišćenu sliku u "Objavljeno"
   folder na Drive-u da se nikad ne ponovi.
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

CONTENT_TYPE = "feed"
CONFESSIONS_FILE = "content/confessions.json"
OUTPUT_FILE = "output/post_content.json"
LOGO_PATH = "logo.png"
MAX_RETRIES = 5
RETRY_DELAYS = [5, 10, 20, 40]
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
TARGET_WIDTH = 1080
TARGET_HEIGHT = 1350

# Ljubičasta/lila akcentna boja - menjaj samo ovu liniju ako želiš drugu
# nijansu.
ACCENT_COLOR = (191, 64, 255, 255)

# Reči koje će biti istaknute akcentnom bojom kad se pojave u "Priznajem..."
# tekstu na slici (ostatak teksta ostaje beo).
HIGHLIGHT_WORDS = {
    "priznajem",
    "volim", "verujem", "tražim", "čekam",
    "besplatno", "besplatan", "besplatna",
    "diskretno", "diskretan", "diskretna",
}


def log(msg):
    print(f"[generate_and_host_image] {msg}", flush=True)


def load_confessions_data():
    with open(CONFESSIONS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def pick_confession():
    data = load_confessions_data()
    return random.choice(data["confessions"])


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


_logo_cache = {}


def load_logo():
    if "img" not in _logo_cache:
        try:
            _logo_cache["img"] = Image.open(LOGO_PATH).convert("RGBA")
        except (FileNotFoundError, OSError):
            log(f"UPOZORENJE: {LOGO_PATH} nije nađen, crtam samo tekst bez loga.")
            _logo_cache["img"] = None
    return _logo_cache["img"]


def draw_brand_badge(img, draw, width, height, corner="top-left"):
    """Crta logo (logo.png, providna pozadina) + tekst 'srpskomuvanje'
    u ćošku slike, na providnoj tamnoj pločici radi čitljivosti."""
    logo = load_logo()
    text = "srpskomuvanje"
    try:
        badge_font = ImageFont.truetype(FONT_PATH, int(width * 0.042))
    except OSError:
        badge_font = ImageFont.load_default()

    icon_size = int(width * 0.11)
    gap = int(width * 0.02)
    pad_x = int(width * 0.025)
    pad_y = int(width * 0.018)
    margin = int(width * 0.045)

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


def render_kartica(local_path):
    """'Kartice' se NE DIRAJU - samo se propuštaju u JPEG format tačno
    onakve kakve jesu, bez sečenja, uklapanja ili teksta."""
    img = Image.open(local_path).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def render_obicna_slika(local_path):
    img = Image.open(local_path)
    canvas = fit_within_canvas(img, TARGET_WIDTH, TARGET_HEIGHT)
    width, height = canvas.size
    canvas = canvas.convert("RGBA")
    draw = ImageDraw.Draw(canvas, "RGBA")

    confession = pick_confession()

    try:
        text_font = ImageFont.truetype(FONT_PATH, int(width * 0.075))
    except OSError:
        log("UPOZORENJE: DejaVu font nije nađen, koristim default font.")
        text_font = ImageFont.load_default()

    text_upper = confession.upper()
    max_width = int(width * 0.85)
    lines = wrap_text(draw, text_upper, text_font, max_width)

    line_height = int(text_font.size * 1.15) if hasattr(text_font, "size") else 28
    total_text_height = line_height * len(lines)

    band_top = height - total_text_height - int(height * 0.10)
    draw.rectangle([(0, band_top), (width, height)], fill=(0, 0, 0, 175))

    y = height - total_text_height - int(height * 0.06)
    for line in lines:
        draw_highlighted_line(draw, line, text_font, y, width)
        y += line_height

    draw_brand_badge(canvas, draw, width, height, "top-left")

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
        f'Content-Disposition: form-data; name="file"; filename="post.jpg"\r\n'
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
    picked = gdrive_helper.pick_random_image(CONTENT_TYPE)
    log(f"Slika: feed/{picked['subtype']}/{picked['file_name']}")

    if picked["subtype"] == "kartice":
        final_image = render_kartica(picked["local_path"])
    else:
        final_image = render_obicna_slika(picked["local_path"])

    image_url = upload_to_cloudinary(final_image)
    caption = pick_cta_caption()

    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "category": picked["subtype"],
                "caption": caption,
                "image_url": image_url,
                "gdrive_file_id": picked["file_id"],
                "gdrive_file_name": picked["file_name"],
                "gdrive_source_folder_id": picked["source_folder_id"],
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    log(f"Gotovo. Slika: {image_url}")
    log(f"Caption: {caption}")


if __name__ == "__main__":
    main()
