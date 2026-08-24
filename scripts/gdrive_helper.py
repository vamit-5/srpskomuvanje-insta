#!/usr/bin/env python3
"""
gdrive_helper.py
-------------------
Deljena pomoćna biblioteka za sve "generate_and_host_*" i "publish_*"
skripte. Korisnik SAM generiše slike (na svom Higgsfield nalogu) i ručno ih
ubacuje u odgovarajući folder na svom Google Drive-u. Ova biblioteka se
povezuje na taj Drive (preko servisnog naloga - GDRIVE_SERVICE_ACCOUNT_JSON
secret) i uzima odatle sledeću neiskorišćenu sliku.

OČEKIVANA STRUKTURA FOLDERA na Google Drive-u (mora biti podeljena sa
servisnim nalogom, sa pravom "Editor"):

    Srpskomuvanje/
      feed/
        kartice/          <- već gotove, dizajnirane slike (kao kartice u
                              dating aplikacijama) - postavljaju se TAČNO
                              onako kako jesu, bez ikakve izmene.
        obicne slike/     <- obične, needitovane slike - skript na njih
                              dodaje kratku "priznajem..." izjavu i logo.
      carousels/
        kartice/
        obicne slike/
      reels/               (za sad se ne koristi ovde)

Store slike se biraju MEŠANO iz sva 4 podfoldera (feed/kartice,
feed/obicne slike, carousels/kartice, carousels/obicne slike).

Traženje foldera po imenu NIJE osetljivo na velika/mala slova (da ne bi
pravilo problem ako se neko malo razlikuje npr. "Feed" vs "feed").

Posle uspešnog objavljivanja na Instagram, iskorišćena slika se automatski
premešta u podfolder "Objavljeno" (pravi se sam unutar istog foldera iz
kog je slika uzeta, ako još ne postoji), tako da se ista slika nikad ne
ponovi.
"""

import io
import json
import os
import random

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

SCOPES = ["https://www.googleapis.com/auth/drive"]
ROOT_FOLDER_NAME = "Srpskomuvanje"
CONTENT_TYPES = ["feed", "carousels"]
SUBTYPES = ["kartice", "obicne slike"]
ARCHIVE_FOLDER_NAME = "Objavljeno"
FOLDER_MIME = "application/vnd.google-apps.folder"


def log(msg):
    print(f"[gdrive_helper] {msg}", flush=True)


_service_cache = {}


def get_drive_service():
    """Napravi (ili vrati keširanu) konekciju ka Google Drive API-ju preko
    servisnog naloga čiji JSON ključ čuvamo u GDRIVE_SERVICE_ACCOUNT_JSON
    GitHub Secret-u."""
    if "service" not in _service_cache:
        raw = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON", "").strip()
        if not raw:
            raise RuntimeError(
                "Nedostaje GDRIVE_SERVICE_ACCOUNT_JSON (GitHub Secret) - "
                "proveri da li je dodat u Settings > Secrets and variables > Actions."
            )
        try:
            info = json.loads(raw)
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"GDRIVE_SERVICE_ACCOUNT_JSON nije ispravan JSON ({e}). "
                "Proveri da li si nalepio/la CEO sadržaj preuzetog .json fajla."
            ) from e
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
        _service_cache["service"] = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _service_cache["service"]


def find_folder(service, name, parent_id=None):
    """Traži podfolder po imenu, NIJE osetljivo na velika/mala slova."""
    query = f"mimeType = '{FOLDER_MIME}' and trashed = false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    result = service.files().list(q=query, fields="files(id, name)", spaces="drive", pageSize=200).execute()
    target = name.strip().lower()
    for f in result.get("files", []):
        if f["name"].strip().lower() == target:
            return f["id"]
    return None


def ensure_folder(service, name, parent_id):
    """Nađe podfolder po imenu, ili ga napravi ako još ne postoji (koristi
    se za automatsko pravljenje 'Objavljeno' foldera)."""
    folder_id = find_folder(service, name, parent_id)
    if folder_id:
        return folder_id
    metadata = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
    folder = service.files().create(body=metadata, fields="id").execute()
    log(f"Napravio sam novi folder '{name}' na Google Drive-u.")
    return folder["id"]


def list_files(service, parent_id):
    """Sve što NIJE folder unutar datog foldera (slike, uglavnom). Slike
    koje su već u 'Objavljeno' podfolderu se automatski ne broje ovde jer
    su u drugom (ugnježdenom) folderu."""
    query = f"'{parent_id}' in parents and trashed = false and mimeType != '{FOLDER_MIME}'"
    result = service.files().list(q=query, fields="files(id, name, mimeType)", spaces="drive", pageSize=1000).execute()
    return result.get("files", [])


def download_file(service, file_id, local_path):
    os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
    request = service.files().get_media(fileId=file_id)
    with io.FileIO(local_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    return local_path


def move_file(service, file_id, new_parent_id, old_parent_id):
    service.files().update(
        fileId=file_id,
        addParents=new_parent_id,
        removeParents=old_parent_id,
        fields="id, parents",
    ).execute()


def _get_root(service):
    root_id = find_folder(service, ROOT_FOLDER_NAME)
    if not root_id:
        raise RuntimeError(
            f"Ne mogu da nađem folder '{ROOT_FOLDER_NAME}' na Google Drive-u. "
            "Proveri da li postoji i da li je podeljen sa servisnim nalogom (Editor pravo)."
        )
    return root_id


def _get_subtype_folder(service, root_id, content_type, subtype):
    content_id = find_folder(service, content_type, root_id)
    if not content_id:
        log(f"UPOZORENJE: folder '{content_type}' ne postoji unutar '{ROOT_FOLDER_NAME}'.")
        return None
    subtype_id = find_folder(service, subtype, content_id)
    if not subtype_id:
        log(f"UPOZORENJE: folder '{content_type}/{subtype}' ne postoji.")
        return None
    return subtype_id


def count_images(content_type, subtype):
    """Koliko slika trenutno ima u '{content_type}/{subtype}' (0 ako folder
    ne postoji ili je prazan) - korisno da se pre biranja proveri da li
    ima dovoljno slika za carousel."""
    service = get_drive_service()
    root_id = _get_root(service)
    folder_id = _get_subtype_folder(service, root_id, content_type, subtype)
    if not folder_id:
        return 0
    return len(list_files(service, folder_id))


def pick_random_image(content_type, subtype=None, work_dir="/tmp/gdrive_images"):
    """Bira JEDNU nasumičnu, još neobjavljenu sliku iz
    '{content_type}/kartice' ili '{content_type}/obicne slike' (content_type
    je 'feed' ili 'carousels'). Ako 'subtype' nije zadat, nasumično bira
    između kartice/obicne slike (probajući oba ako je jedan prazan).

    Vraća dict: local_path, subtype, file_id, file_name, source_folder_id.
    """
    service = get_drive_service()
    root_id = _get_root(service)

    subtypes_to_try = [subtype] if subtype else random.sample(SUBTYPES, len(SUBTYPES))
    for st in subtypes_to_try:
        folder_id = _get_subtype_folder(service, root_id, content_type, st)
        if not folder_id:
            continue
        images = list_files(service, folder_id)
        if images:
            chosen = random.choice(images)
            local_path = os.path.join(work_dir, f"{chosen['id']}_{chosen['name']}")
            download_file(service, chosen["id"], local_path)
            log(f"Uzeta slika sa Drive-a: {content_type}/{st}/{chosen['name']}")
            return {
                "local_path": local_path,
                "subtype": st,
                "file_id": chosen["id"],
                "file_name": chosen["name"],
                "source_folder_id": folder_id,
            }
        log(f"Folder '{content_type}/{st}' je prazan.")

    raise RuntimeError(
        f"Nema nijedne nove slike u '{content_type}' (ni kartice ni obicne slike) na Google Drive-u. "
        "Ubaci bar jednu novu sliku na Drive pa pokreni ponovo."
    )


def pick_random_images_multi(content_type, subtype, count, allow_repeat=False, work_dir="/tmp/gdrive_images"):
    """Za Carousels - bira više slika odjednom iz '{content_type}/{subtype}'.

    Ako allow_repeat=True, dozvoljeno je da vrati ISTU sliku više puta
    (korisno kad želiš istu osobu sa različitim tekstom na svakom slajdu) -
    tada bira JEDNU sliku i ponavlja je 'count' puta. Ako allow_repeat=False,
    bira do 'count' RAZLIČITIH slika (ili manje, ako nema dovoljno).

    Vraća listu dict-ova (isti oblik kao pick_random_image), po JEDAN za
    svaki slajd (čak i ako je local_path/file_id isti kod ponavljanja).
    """
    service = get_drive_service()
    root_id = _get_root(service)
    folder_id = _get_subtype_folder(service, root_id, content_type, subtype)
    if not folder_id:
        raise RuntimeError(f"Folder '{content_type}/{subtype}' ne postoji ili je prazan.")

    images = list_files(service, folder_id)
    if not images:
        raise RuntimeError(f"Folder '{content_type}/{subtype}' je prazan - ubaci slike pa pokreni ponovo.")

    def _download(chosen):
        local_path = os.path.join(work_dir, f"{chosen['id']}_{chosen['name']}")
        download_file(service, chosen["id"], local_path)
        return {
            "local_path": local_path,
            "subtype": subtype,
            "file_id": chosen["id"],
            "file_name": chosen["name"],
            "source_folder_id": folder_id,
        }

    if allow_repeat:
        chosen = random.choice(images)
        picked_one = _download(chosen)
        log(f"Uzeta ISTA slika za {count} slajdova: {content_type}/{subtype}/{chosen['name']}")
        return [picked_one for _ in range(count)]

    random.shuffle(images)
    selected = images[:count]
    result = [_download(img) for img in selected]
    log(f"Uzeto {len(result)} različitih slika: {content_type}/{subtype}")
    return result


def pick_random_story_source(work_dir="/tmp/gdrive_images"):
    """Za Storys - meša SVA 4 podfoldera (feed/kartice, feed/obicne slike,
    carousels/kartice, carousels/obicne slike) i bira JEDNU nasumičnu, još
    neobjavljenu sliku iz bilo kog od njih koji nije prazan.

    Vraća dict: local_path, content_type, subtype, file_id, file_name,
    source_folder_id.
    """
    service = get_drive_service()
    root_id = _get_root(service)

    sources = [(ct, st) for ct in CONTENT_TYPES for st in SUBTYPES]
    random.shuffle(sources)

    for content_type, subtype in sources:
        folder_id = _get_subtype_folder(service, root_id, content_type, subtype)
        if not folder_id:
            continue
        images = list_files(service, folder_id)
        if images:
            chosen = random.choice(images)
            local_path = os.path.join(work_dir, f"{chosen['id']}_{chosen['name']}")
            download_file(service, chosen["id"], local_path)
            log(f"Uzeta slika za Story: {content_type}/{subtype}/{chosen['name']}")
            return {
                "local_path": local_path,
                "content_type": content_type,
                "subtype": subtype,
                "file_id": chosen["id"],
                "file_name": chosen["name"],
                "source_folder_id": folder_id,
            }

    raise RuntimeError(
        "Nema nijedne nove slike ni u jednom od foldera (feed/carousels x kartice/obicne slike) "
        "na Google Drive-u. Ubaci bar jednu novu sliku pa pokreni ponovo."
    )


def archive_image(picked):
    """Premesti iskorišćenu sliku u 'Objavljeno' podfolder (napravljen
    unutar ISTOG foldera iz kog je slika uzeta) da se ne ponovi. 'picked'
    mora imati bar: file_id, source_folder_id (opciono file_name za log)."""
    service = get_drive_service()
    archive_id = ensure_folder(service, ARCHIVE_FOLDER_NAME, picked["source_folder_id"])
    move_file(service, picked["file_id"], archive_id, picked["source_folder_id"])
    log(f"Slika '{picked.get('file_name', picked['file_id'])}' premeštena u Objavljeno/.")
