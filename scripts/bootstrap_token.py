#!/usr/bin/env python3
"""
bootstrap_token.py
-------------------
POKREĆE SE SAMO JEDNOM, RUČNO, kada prvi put podešavamo nalog (ili ako iz nekog
razloga automatski refresh token workflow ne uspe 60 dana zaredom pa nam istekne
sve, pa moramo da krenemo ispočetka).

Šta radi:
1. Uzima SVEŽ (kratkotrajan ili tek generisan) Instagram access token iz
   GitHub Secret-a IG_SHORT_LIVED_TOKEN i App Secret iz IG_APP_SECRET.
2. Zamenjuje ga za DUGOTRAJNI (60 dana) access token pozivom na Meta API.
3. Upisuje taj token (i vreme kad je izdat) u state/ig_token.json unutar repo-a.
4. Commit-uje i push-uje tu promenu u git (sa retry logikom - isti obrazac
   kao za lock fajlove).

Posle ovoga, refresh_token.py (koji se pokreće automatski na raspored) će sam
produžavati ovaj token zauvek, bez tvog učešća.
"""

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

STATE_FILE = "state/ig_token.json"
MAX_RETRIES = 5
RETRY_DELAYS = [5, 10, 20, 40]  # sekunde, rastuća pauza između pokušaja


def log(msg):
    print(f"[bootstrap_token] {msg}", flush=True)


def http_get_with_retry(url):
    """GET zahtev sa retry logikom: do 5 pokušaja, samo za privremene (mrežne/5xx)
    greške. Za trajne (4xx) greške odmah odustaje."""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                body = response.read().decode("utf-8")
                return json.loads(body)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            if 400 <= e.code < 500:
                log(f"TRAJNA GREŠKA ({e.code}), odustajem odmah. Odgovor: {body}")
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


def exchange_for_long_lived_token(short_lived_token, app_secret):
    url = (
        "https://graph.instagram.com/access_token"
        f"?grant_type=ig_exchange_token"
        f"&client_secret={app_secret}"
        f"&access_token={short_lived_token}"
    )
    log("Zamenjujem token za dugotrajni (60 dana)...")
    data = http_get_with_retry(url)
    if "access_token" not in data:
        raise RuntimeError(f"Neočekivan odgovor od Instagram API-ja: {data}")
    return data["access_token"], data.get("expires_in", 5184000)


def save_state(access_token, expires_in):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    state = {
        "access_token": access_token,
        "updated_at": int(time.time()),
        "expires_in": expires_in,
    }
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
    log(f"Upisano u {STATE_FILE}")


def run(cmd):
    log(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout.strip():
        log(result.stdout.strip())
    if result.returncode != 0:
        log(result.stderr.strip())
    return result


def git_commit_and_push():
    # KORAK 1: prvo lokalne izmene (add + commit) - dokazani redosled
    run(["git", "config", "user.name", "github-actions[bot]"])
    run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"])
    run(["git", "add", STATE_FILE])

    commit_result = run(["git", "commit", "-m", "bootstrap: novi dugotrajni Instagram token"])
    if commit_result.returncode != 0:
        log("Nema promena za commit (ili je commit već napravljen) - preskačem push.")
        return

    # KORAK 2: TEK ONDA fetch + rebase + push, sa retry petljom
    for attempt in range(1, MAX_RETRIES + 1):
        run(["git", "fetch", "origin"])
        rebase = run(["git", "rebase", "origin/main"])
        if rebase.returncode != 0:
            log("Rebase nije uspeo, prekidam (proveri ručno stanje repo-a).")
            sys.exit(1)

        push = run(["git", "push", "origin", "HEAD:main"])
        if push.returncode == 0:
            log("Push uspešan.")
            return

        log(f"Push nije uspeo (pokušaj {attempt}/{MAX_RETRIES}) - neko je možda upravo push-ovao. Pokušavam ponovo.")
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)])

    raise RuntimeError("Push nije uspeo posle svih pokušaja.")


def main():
    short_lived_token = os.environ.get("IG_SHORT_LIVED_TOKEN", "").strip()
    app_secret = os.environ.get("IG_APP_SECRET", "").strip()

    if not short_lived_token or not app_secret:
        log("GREŠKA: nedostaje IG_SHORT_LIVED_TOKEN ili IG_APP_SECRET (GitHub Secrets).")
        sys.exit(1)

    access_token, expires_in = exchange_for_long_lived_token(short_lived_token, app_secret)
    days = expires_in / 86400
    log(f"Uspešno dobijen dugotrajni token, važi još ~{days:.0f} dana.")

    save_state(access_token, expires_in)
    git_commit_and_push()
    log("Gotovo. Od sada refresh_token.yml automatski održava ovaj token svežim.")


if __name__ == "__main__":
    main()
