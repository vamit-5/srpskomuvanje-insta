#!/usr/bin/env python3
"""
publish_reels.py
-------------------
Objavljuje Reels na Instagram, koristeći podatke koje je
build_reels_video.py pripremio u output/reels_content.json.

Koraci:
1. POST /{ig-user-id}/media sa video_url + media_type=REELS + caption
   -> dobijamo container ID.
2. Sačekamo da container bude spreman (obrada videa traje duže nego kod slika).
3. POST /{ig-user-id}/media_publish sa creation_id=container_id.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_RETRIES = 5
RETRY_DELAYS = [5, 10, 20, 40]
STATUS_POLL_ATTEMPTS = 30
STATUS_POLL_DELAY = 10


def log(msg):
    print(f"[publish_reels] {msg}", flush=True)


def http_post_form_with_retry(url, params):
    data = urllib.parse.urlencode(params).encode("utf-8")
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=30) as response:
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


def http_get_with_retry(url):
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                body = response.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if 400 <= e.code < 500:
                raise RuntimeError(f"Trajna greška {e.code}: {body}") from e
            last_error = RuntimeError(f"HTTP {e.code}: {body}")
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last_error = e

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAYS[attempt - 1])

    raise RuntimeError(f"Svi pokušaji neuspešni. Poslednja greška: {last_error}")


def load_token():
    with open("state/ig_token.json", "r", encoding="utf-8") as f:
        return json.load(f)["access_token"]


def load_reels_content():
    with open("output/reels_content.json", "r", encoding="utf-8") as f:
        return json.load(f)


def wait_until_ready(container_id, token):
    url = f"https://graph.instagram.com/v21.0/{container_id}?fields=status_code&access_token={token}"
    for attempt in range(1, STATUS_POLL_ATTEMPTS + 1):
        data = http_get_with_retry(url)
        status = data.get("status_code")
        log(f"Status container-a: {status} (provera {attempt}/{STATUS_POLL_ATTEMPTS})")
        if status == "FINISHED":
            return True
        if status == "ERROR":
            raise RuntimeError(f"Container je pukao sa statusom ERROR: {data}")
        time.sleep(STATUS_POLL_DELAY)
    raise RuntimeError("Container nije postao spreman u razumnom vremenu (video obrada predugo traje).")


def main():
    ig_user_id = os.environ.get("IG_USER_ID", "").strip()
    if not ig_user_id:
        log("GREŠKA: nedostaje IG_USER_ID.")
        sys.exit(1)

    token = load_token()
    content = load_reels_content()

    log("Pravim media container za Reels...")
    create_url = f"https://graph.instagram.com/v21.0/{ig_user_id}/media"
    create_result = http_post_form_with_retry(
        create_url,
        {
            "video_url": content["video_url"],
            "media_type": "REELS",
            "caption": content["caption"],
            "access_token": token,
        },
    )
    if "id" not in create_result:
        raise RuntimeError(f"Neočekivan odgovor pri kreiranju container-a: {create_result}")
    container_id = create_result["id"]
    log(f"Container kreiran: {container_id}")

    wait_until_ready(container_id, token)

    log("Objavljujem...")
    publish_url = f"https://graph.instagram.com/v21.0/{ig_user_id}/media_publish"
    publish_result = http_post_form_with_retry(
        publish_url,
        {"creation_id": container_id, "access_token": token},
    )
    if "id" not in publish_result:
        raise RuntimeError(f"Neočekivan odgovor pri objavljivanju: {publish_result}")

    log(f"USPEŠNO OBJAVLJEN REELS! Media ID: {publish_result['id']}")


if __name__ == "__main__":
    main()
