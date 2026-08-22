#!/usr/bin/env python3
"""
build_reels_video.py
-----------------------
Uzima frejmove (slike) iz output/reels_manifest.json i pravi kratak
vertikalni Reels video (1080x1920):
- Svaki slajd postaje klip sa blagim zoom-in efektom (~2s), poslednji ~3s.
- Klipovi se nadovezuju (tvrdi rez, bez zvuka za sada).
- Finalni video se otprema na Cloudinary da dobije video_url.
- Upisuje output/reels_content.json {video_url, caption} za publish_reels.py.
"""

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid

MANIFEST_FILE = "output/reels_manifest.json"
CLIPS_DIR = "output/reels_clips"
FINAL_VIDEO = "output/reels_video.mp4"
OUTPUT_FILE = "output/reels_content.json"
FPS = 25
SLIDE_DURATION = 2.0
LAST_SLIDE_DURATION = 3.0
WIDTH = 1080
HEIGHT = 1920
MAX_RETRIES = 5
RETRY_DELAYS = [5, 10, 20, 40]


def log(msg):
    print(f"[build_reels_video] {msg}", flush=True)


def run(cmd):
    log(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log(result.stderr[-2000:])
        raise RuntimeError(f"Komanda nije uspela: {' '.join(cmd)}")
    return result


def build_clip(frame_path, duration, clip_path):
    frames = int(duration * FPS)
    zoom_filter = (
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},"
        f"zoompan=z='min(zoom+0.0015,1.08)':d={frames}:s={WIDTH}x{HEIGHT}:fps={FPS}"
    )
    run([
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", frame_path,
        "-vf", zoom_filter,
        "-t", str(duration),
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264",
        "-preset", "veryfast",
        clip_path,
    ])


def concat_clips(clip_paths, output_path):
    list_file = os.path.join(CLIPS_DIR, "concat_list.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for path in clip_paths:
            f.write(f"file '{os.path.abspath(path)}'\n")

    run([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", list_file,
        "-c", "copy",
        output_path,
    ])


def http_post_with_retry(url, data_bytes, content_type):
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, data=data_bytes, method="POST")
            req.add_header("Content-Type", content_type)
            with urllib.request.urlopen(req, timeout=180) as response:
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


def upload_video_to_cloudinary(video_path):
    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME", "").strip()
    upload_preset = os.environ.get("CLOUDINARY_UPLOAD_PRESET", "").strip()
    if not cloud_name or not upload_preset:
        raise RuntimeError("Nedostaje CLOUDINARY_CLOUD_NAME ili CLOUDINARY_UPLOAD_PRESET.")

    boundary = uuid.uuid4().hex
    with open(video_path, "rb") as f:
        video_bytes = f.read()

    body = b""
    body += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="upload_preset"\r\n\r\n{upload_preset}\r\n'
    ).encode("utf-8")
    body += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="reels.mp4"\r\n'
        f"Content-Type: video/mp4\r\n\r\n"
    ).encode("utf-8")
    body += video_bytes
    body += f"\r\n--{boundary}--\r\n".encode("utf-8")

    url = f"https://api.cloudinary.com/v1_1/{cloud_name}/video/upload"
    content_type = f"multipart/form-data; boundary={boundary}"

    log("Otpremam video na Cloudinary (može potrajati)...")
    data = http_post_with_retry(url, body, content_type)
    if "secure_url" not in data:
        raise RuntimeError(f"Neočekivan odgovor od Cloudinary-ja: {data}")
    return data["secure_url"]


def main():
    with open(MANIFEST_FILE, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    frame_paths = manifest["frame_paths"]
    os.makedirs(CLIPS_DIR, exist_ok=True)

    clip_paths = []
    for i, frame_path in enumerate(frame_paths):
        duration = LAST_SLIDE_DURATION if i == len(frame_paths) - 1 else SLIDE_DURATION
        clip_path = os.path.join(CLIPS_DIR, f"clip_{i:02d}.mp4")
        log(f"Pravim klip {i + 1}/{len(frame_paths)} (trajanje {duration}s)...")
        build_clip(frame_path, duration, clip_path)
        clip_paths.append(clip_path)

    log("Spajam klipove...")
    concat_clips(clip_paths, FINAL_VIDEO)

    video_url = upload_video_to_cloudinary(FINAL_VIDEO)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump({"video_url": video_url, "caption": manifest["caption"]}, f, ensure_ascii=False, indent=2)

    log(f"Gotovo. Video: {video_url}")


if __name__ == "__main__":
    main()
