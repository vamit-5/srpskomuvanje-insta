#!/usr/bin/env python3
"""
publish_carousel.py
----------------------
Objavljuje Carousel (više slika za swipe) na Instagram, koristeći podatke
koje je generate_and_host_carousel.py pripremio u
output/carousel_content.json.

Koraci (Instagram Content Publishing API):
1. Za SVAKU sliku: POST /{ig-user-id}/media sa image_url + is_carousel_item=true
   (BEZ caption-a na pojedinačnim slikama) -> dobijamo container ID za svaku.
2. POST /{ig-user-id}/media sa media_type=CAROUSEL + children=[svi ID-jevi] +
   caption -> dobijamo GLAVNI container ID.
3. Sačekamo da glavni container bude spreman.
4. POST /{ig-user-id}/media_publish sa creation_id=glavni container ID.

Posle uspešnog objavljivanja, premeštamo SVAKU iskorišćenu sliku na Google
Drive-u u njen "Objavljeno" podfolder (da se nikad ne ponovi ista slika).
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import gdrive_helper

MAX_RETRIES = 5
RETRY_DELAYS = [5, 10, 20, 40]
STATUS_POLL_ATTEMPTS = 10
STATUS_POLL_DELAY = 5


def log(msg):
    print(f"[publish_carousel] {msg}", flush=True)


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


def load_carousel_content():
    with open("output/carousel_content.json", "r", encoding="utf-8") as f:
        return json.load(f)


def wait_until_ready(container_id, token):
    url = f"https://graph.instagram.com/v21.0/{container_id}?fields=status_code&access_token={token}"
    for attempt in range(1, STATUS_POLL_ATTEMPTS + 1):
        data = http_get_with_retry(url)
        status = data.get("status_code")
        log(f"Status container-a {container_id}: {status} (provera {attempt}/{STATUS_POLL_ATTEMPTS})")
        if status == "FINISHED":
            return True
        if status == "ERROR":
            raise RuntimeError(f"Container je pukao sa statusom ERROR: {data}")
        time.sleep(STATUS_POLL_DELAY)
    raise RuntimeError(f"Container {container_id} nije postao spreman u razumnom vremenu.")


def archive_used_carousel(content):
    """Premesti SVAKU iskorišćenu sliku iz carousela u Objavljeno na
    Drive-u (svaku sliku samo jednom, čak i ako je ista slika bila na
    više slajdova). Ako neka ne uspe, ne rušimo ceo posao (carousel je
    već objavljen) - samo upozorimo i nastavimo sa ostalima."""
    items = content.get("gdrive_items")
    if not items:
        log("UPOZORENJE: nema Google Drive podataka u carousel_content.json, preskačem arhiviranje.")
        return

    seen_file_ids = set()
    for item in items:
        file_id = item.get("file_id")
        if not file_id or file_id in seen_file_ids:
            continue
        seen_file_ids.add(file_id)
        if "source_folder_id" not in item:
            continue
        try:
            gdrive_helper.archive_image(
                {
                    "file_id": file_id,
                    "file_name": item.get("file_name"),
                    "source_folder_id": item["source_folder_id"],
                }
            )
        except Exception as e:
            log(f"UPOZORENJE: nisam uspeo da arhiviram sliku '{item.get('file_name', file_id)}' ({e}).")


def main():
    ig_user_id = os.environ.get("IG_USER_ID", "").strip()
    if not ig_user_id:
        log("GREŠKA: nedostaje IG_USER_ID.")
        sys.exit(1)

    token = load_token()
    content = load_carousel_content()
    image_urls = content["image_urls"]

    if len(image_urls) < 2:
        raise RuntimeError("Carousel mora imati bar 2 slike.")

    log(f"Pravim {len(image_urls)} pojedinačnih container-a za priču '{content['title']}'...")
    item_container_ids = []
    create_url = f"https://graph.instagram.com/v21.0/{ig_user_id}/media"
    for i, image_url in enumerate(image_urls):
        log(f"Slajd {i + 1}/{len(image_urls)}...")
        result = http_post_form_with_retry(
            create_url,
            {
                "image_url": image_url,
                "is_carousel_item": "true",
                "access_token": token,
            },
        )
        if "id" not in result:
            raise RuntimeError(f"Neočekivan odgovor pri kreiranju slajda {i + 1}: {result}")
        item_container_ids.append(result["id"])

    log("Pravim glavni CAROUSEL container...")
    main_result = http_post_form_with_retry(
        create_url,
        {
            "media_type": "CAROUSEL",
            "children": ",".join(item_container_ids),
            "caption": content["caption"],
            "access_token": token,
        },
    )
    if "id" not in main_result:
        raise RuntimeError(f"Neočekivan odgovor pri kreiranju glavnog container-a: {main_result}")
    main_container_id = main_result["id"]
    log(f"Glavni container kreiran: {main_container_id}")

    wait_until_ready(main_container_id, token)

    log("Objavljujem carousel...")
    publish_url = f"https://graph.instagram.com/v21.0/{ig_user_id}/media_publish"
    publish_result = http_post_form_with_retry(
        publish_url,
        {"creation_id": main_container_id, "access_token": token},
    )
    if "id" not in publish_result:
        raise RuntimeError(f"Neočekivan odgovor pri objavljivanju: {publish_result}")

    log(f"USPEŠNO OBJAVLJEN CAROUSEL! Media ID: {publish_result['id']}")

    archive_used_carousel(content)


if __name__ == "__main__":
    main()
