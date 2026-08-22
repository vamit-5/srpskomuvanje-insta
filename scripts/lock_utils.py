#!/usr/bin/env python3
"""
lock_utils.py
--------------
Generički katanac (lock) mehanizam - sprečava da se ista vrsta objave
(Feed, Carousel, Reels, Stories) pokrene dvaput u isto vreme (npr. ako
dupliramo raspored ili spoljašnji "budilnik" pozove workflow istovremeno
kad i GitHub cron).

Upotreba:
    python scripts/lock_utils.py acquire <ime>   -> pokušava da zauzme katanac
    python scripts/lock_utils.py release <ime>   -> otključava katanac

Fajl katanca: locks/<ime>_lock.txt (sadrži Unix timestamp poslednjeg zauzimanja)
Svež katanac = mlađi od 1500 sekundi (25 minuta) - ako je svež, novo
pokretanje se TIHO povlači (exit 0, samo ispisuje da je preskočeno).

Ovaj skript postavlja GitHub Actions output "acquired" (true/false) preko
GITHUB_OUTPUT fajla, da workflow YAML zna da li da nastavi sa poslom i da
li da na kraju otključa katanac.
"""

import os
import subprocess
import sys
import time

FRESH_THRESHOLD_SECONDS = 1500  # 25 minuta
MAX_RETRIES = 5
RETRY_DELAYS = [5, 10, 20, 40]


def log(msg):
    print(f"[lock_utils] {msg}", flush=True)


def lock_path(name):
    return f"locks/{name}_lock.txt"


def set_github_output(key, value):
    output_file = os.environ.get("GITHUB_OUTPUT")
    if not output_file:
        return
    with open(output_file, "a", encoding="utf-8") as f:
        f.write(f"{key}={value}\n")


def run(cmd):
    log(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout.strip():
        log(result.stdout.strip())
    if result.returncode != 0 and result.stderr.strip():
        log(result.stderr.strip())
    return result


def git_setup():
    run(["git", "config", "user.name", "github-actions[bot]"])
    run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"])


def git_commit_and_push(path, message):
    run(["git", "add", path])
    commit_result = run(["git", "commit", "-m", message])
    if commit_result.returncode != 0:
        return False  # nema promena, ili je neko drugi u međuvremenu commit-ovao

    for attempt in range(1, MAX_RETRIES + 1):
        run(["git", "fetch", "origin"])
        rebase = run(["git", "rebase", "origin/main"])
        if rebase.returncode != 0:
            log("Rebase konflikt - odustajem od ovog pokušaja zauzimanja katanca.")
            run(["git", "rebase", "--abort"])
            return False

        push = run(["git", "push", "origin", "HEAD:main"])
        if push.returncode == 0:
            return True

        log(f"Push nije uspeo (pokušaj {attempt}/{MAX_RETRIES}) - neko je upravo zauzeo katanac pre nas.")
        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)])

    return False


def acquire(name):
    path = lock_path(name)
    git_setup()

    run(["git", "fetch", "origin"])
    run(["git", "reset", "--hard", "origin/main"])

    now = int(time.time())
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
        try:
            last_time = int(content)
        except ValueError:
            last_time = 0

        age = now - last_time
        if last_time > 0 and age < FRESH_THRESHOLD_SECONDS:
            log(f"Katanac '{name}' je svež (star {age}s) - tiho preskačem ovo pokretanje.")
            set_github_output("acquired", "false")
            return

    os.makedirs("locks", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(str(now))

    success = git_commit_and_push(path, f"lock: zauzimam {name}")
    if success:
        log(f"Katanac '{name}' zauzet.")
        set_github_output("acquired", "true")
    else:
        log(f"Neko drugi je upravo zauzeo katanac '{name}' pre nas - tiho preskačem.")
        set_github_output("acquired", "false")


def release(name):
    path = lock_path(name)
    git_setup()
    run(["git", "fetch", "origin"])
    run(["git", "reset", "--hard", "origin/main"])

    os.makedirs("locks", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("0")

    success = git_commit_and_push(path, f"unlock: oslobađam {name}")
    if success:
        log(f"Katanac '{name}' otključan.")
    else:
        log(f"UPOZORENJE: otključavanje '{name}' nije uspelo posle svih pokušaja - proveri ručno.")


def main():
    if len(sys.argv) < 3:
        log("Upotreba: python scripts/lock_utils.py [acquire|release] <ime>")
        sys.exit(1)

    action = sys.argv[1]
    name = sys.argv[2]

    if action == "acquire":
        acquire(name)
    elif action == "release":
        release(name)
    else:
        log(f"Nepoznata akcija: {action}")
        sys.exit(1)


if __name__ == "__main__":
    main()
