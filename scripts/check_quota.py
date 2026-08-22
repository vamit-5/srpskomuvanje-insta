#!/usr/bin/env python3
"""
check_quota.py
----------------
Proverava Instagram publishing kvotu PRE nego što trošimo resurse na
generisanje sadržaja koji bi svejedno pao na objavljivanju.

Poziva: GET /{ig-user-id}/content_publishing_limit?fields=config,quota_usage

Postavlja GitHub Actions output "ok" (true/false) - ako je kvota skoro
potpuno potrošena, ok=false i workflow treba tiho da preskoči ostatak posla.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

MAX_RETRIES = 5
RETRY_DELAYS = [5, 10, 20, 40]
SAFETY_MARGIN = 2  # ostavljamo bar 2 slobodna mesta u kvoti kao rezervu


def log(msg):
    print(f"[check_quota] {msg}", flush=True)


def set_github_output(key, value):
    output_file = os.environ.get("GITHUB_OUTPUT")
    if not output_file:
        return
    with open(output_file, "a", encoding="utf-8") as f:
        f.write(f"{key}={value}\n")


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


def load_token():
    with open("state/ig_token.json", "r", encoding="utf-8") as f:
        return json.load(f)["access_token"]


def main():
    ig_user_id = os.environ.get("IG_USER_ID", "").strip()
    if not ig_user_id:
        log("GREŠKA: nedostaje IG_USER_ID.")
        set_github_output("ok", "false")
        sys.exit(1)

    token = load_token()
    url = (
        f"https://graph.instagram.com/v21.0/{ig_user_id}/content_publishing_limit"
        f"?fields=config,quota_usage&access_token={token}"
    )

    try:
        data = http_get_with_retry(url)
    except RuntimeError as e:
        log(f"Ne mogu da proverim kvotu ({e}) - iz predostrožnosti preskačem ovo pokretanje.")
        set_github_output("ok", "false")
        return

    try:
        item = data["data"][0]
        quota_usage = item["quota_usage"]
        quota_total = item["config"]["quota_total"]
    except (KeyError, IndexError, TypeError) as e:
        log(f"Neočekivan odgovor ({data}) - iz predostrožnosti preskačem. Greška: {e}")
        set_github_output("ok", "false")
        return

    remaining = quota_total - quota_usage
    log(f"Kvota: {quota_usage}/{quota_total} iskorišćeno, {remaining} preostalo.")

    if remaining <= SAFETY_MARGIN:
        log("Kvota skoro potpuno potrošena - tiho preskačem ovo pokretanje.")
        set_github_output("ok", "false")
    else:
        set_github_output("ok", "true")


if __name__ == "__main__":
    main()
