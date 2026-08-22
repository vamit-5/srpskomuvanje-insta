#!/usr/bin/env python3
"""
refresh_token.py
------------------
Pokreće se AUTOMATSKI, na raspored (jednom dnevno), i produžava trenutni
dugotrajni Instagram access token za još 60 dana, TRAJNO, bez ručne intervencije.

Meta pravilo: dugotrajni token se može osvežiti tek kad ima BAREM 24h od
poslednjeg izdavanja/osvežavanja, i mora biti još uvek validan (nije istekao).
Zato ovaj workflow mora da se pokreće redovno (dnevno je više nego dovoljno
sigurno, pošto token traje 60 dana - ima ogromnu marginu).

Ako se ovaj workflow ne pokrene 60 dana zaredom (npr. GitHub Actions je bio
ugašen), token će isteći i moraćeš ručno da pokreneš bootstrap_token.py
ponovo sa svežim tokenom sa Meta dashboard-a.
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
RETRY_DELAYS = [5, 10, 20, 40]


def log(msg):
    print(f"[refresh_token] {msg}", flush=True)


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


def load_state():
    if not os.path.exists(STATE_FILE):
        log(f"GREŠKA: {STATE_FILE} ne postoji. Prvo moraš pokrenuti bootstrap_token.py ručno.")
        sys.exit(1)
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def refresh_token(current_token):
    url = (
        "https://graph.instagram.com/refresh_access_token"
        f"?grant_type=ig_refresh_token"
        f"&access_token={current_token}"
    )
    log("Osvežavam token...")
    data = http_get_with_retry(url)
    if "access_token" not in data:
        raise RuntimeError(f"Neočekivan odgovor od Instagram API-ja: {data}")
    return data["access_token"], data.get("expires_in", 5184000)


def save_state(access_token, expires_in):
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
    run(["git", "config", "user.name", "github-actions[bot]"])
    run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"])
    run(["git", "add", STATE_FILE])

    commit_result = run(["git", "commit", "-m", "refresh: osvežen Instagram token"])
    if commit_result.returncode != 0:
        log("Nema promena za commit - preskačem push.")
        return

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

        log(f"Push nije uspeo (pokušaj {attempt}/{MAX_RETRIES}) - pokušavam ponovo.")
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)])

    raise RuntimeError("Push nije uspeo posle svih pokušaja.")


def main():
    state = load_state()
    current_token = state["access_token"]

    age_days = (int(time.time()) - state["updated_at"]) / 86400
    log(f"Trenutni token star je ~{age_days:.1f} dana.")

    if age_days < 1:
        log("Token je mlađi od 24h - Meta ne dozvoljava osvežavanje pre toga. Tiho preskačem.")
        return

    new_token, expires_in = refresh_token(current_token)
    days = expires_in / 86400
    log(f"Uspešno osvežen token, važi još ~{days:.0f} dana.")

    save_state(new_token, expires_in)
    git_commit_and_push()


if __name__ == "__main__":
    main()
