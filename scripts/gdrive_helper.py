#!/usr/bin/env python3
"""
gdrive_helper.py
-------------------
Deljena logika za sve "generate_and_host_*.py" skripte - povezivanje na
Google Drive preko servisnog naloga i biranje slika iz sledeće strukture:

    Srpskomuvanje/
      feed/
        kartice/        <- gotov dizajn, NIKAD se ne dira, samo se postavlja
        obicne slike/   <- obične slike, dodaje im se tekst
      carousels/
        kartice/
        obicne slike/
      reels/             <- ne koristi se (Reels se ne diraju)

Traženje foldera po imenu je NEOSETLJIVO na velika/mala slova (korisnik je
u razgovoru pisao "feed"/"kartice" malim slovima, a stvarni folderi na
Drive-u mogu imati bilo koju kombinaciju velikih/malih slova).

PRIORITET KARTICA: kartice su već gotov, uređen dizajn i korisnik želi da
se ONE prioritetno postavljaju (ne diramo ih, samo ih postavljamo - to je
najmanje posla za najbolji rezultat). KARTICE_WEIGHT ispod određuje koliki
je procenat šanse da se izabere baš "kartice" folder kad oba foldera imaju
slika (0.7 = 70% šanse za kartice, 30% za obične slike).

Posle uspešnog objavljivanja (u publish_*.py skriptama), iskorišćena slika
se premešta u automatski kreiran "Objavljeno" podfolder - unutar ISTOG
foldera iz kog je uzeta - da se nikad ne ponovi.

POPRAVKA (avgust 2026): count_images() je ranije ćutke gutao SVAKU grešku
(RuntimeError) i vraćao 0, bez ijednog traga u logu - zbog toga se činilo
da "kartice" nikad nema slika, iako je pravi uzrok bio neka konkretna
greška (npr. da se ime foldera na Drive-u ne poklapa tačno). Sada se
svaka takva greška ODŠTAMPA u GitHub Actions log, a greška "folder nije
nađen" dodatno ispisuje TAČNU listu podfoldera koji stvarno postoje na
tom mestu - da bi se odmah videlo da li je u pitanju drugačije ime foldera.
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

# Šansa da se izabere "kartice" umesto "obicne slike" kad oba foldera
# imaju slika (kartice su prioritet - ne treba im nikakva obrada).
KARTICE_WEIGHT = 0.7


def log(msg):
    print(f"[gdrive_helper] {msg}", flush=True)


_service_cache = {}


def get_drive_service():
    if "service" in _service_cache:
        return _service_cache["service"]

    raw = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise RuntimeError("Nedostaje GDRIVE_SERVICE_ACCOUNT_JSON u GitHub Secrets.")

    info = json.loads(raw)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    _service_cache["service"] = service
    return service


def find_folder(service, name, parent_id=None):
    """Traži folder po imenu (neosetljivo na velika/mala slova) unutar
    parent_id (ili globalno ako parent_id nije zadat). Vraća ID prvog
    poklapanja ili None."""
    query_parts = ["mimeType = 'application/vnd.google-apps.folder'", "trashed = false"]
    if parent_id:
        query_parts.append(f"'{parent_id}' in parents")
    query = " and ".join(query_parts)

    page_token = None
    target = name.strip().lower()
    while True:
        response = service.files().list(
            q=query,
            spaces="drive",
            fields="nextPageToken, files(id, name)",
            pageToken=page_token,
        ).execute()
        for f in response.get("files", []):
            if f["name"].strip().lower() == target:
                return f["id"]
        page_token = response.get("nextPageToken")
        if not page_token:
            return None


def _list_subfolder_names(service, parent_id):
    """Pomoćna funkcija SAMO za jasnije poruke o grešci: vraća listu
    imena svih podfoldera unutar parent_id (da se u poruci greške vidi
    tačno koji folderi stvarno postoje, umesto samo 'nije nađen')."""
    try:
        response = service.files().list(
            q=f"'{parent_id}' in parents and trashed = false and mimeType = '{FOLDER_MIME}'",
            spaces="drive",
            fields="files(name)",
        ).execute()
        return [f["name"] for f in response.get("files", [])]
    except Exception:
        return []


def ensure_folder(service, name, parent_id):
    """Nađe folder po imenu unutar parent_id, ili ga napravi ako ne
    postoji. Vraća ID foldera."""
    existing = find_folder(service, name, parent_id)
    if existing:
        return existing
    metadata = {
        "name": name,
        "mimeType": FOLDER_MIME,
        "parents": [parent_id],
    }
    created = service.files().create(body=metadata, fields="id").execute()
    log(f"Napravljen novi folder '{name}' (ID: {created['id']}).")
    return created["id"]


def list_files(service, parent_id):
    """Vraća listu svih fajlova (NE foldera) direktno unutar parent_id."""
    query = f"'{parent_id}' in parents and trashed = false and mimeType != '{FOLDER_MIME}'"
    files = []
    page_token = None
    while True:
        response = service.files().list(
            q=query,
            spaces="drive",
            fields="nextPageToken, files(id, name)",
            pageToken=page_token,
        ).execute()
        files.extend(response.get("files", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return files


def download_file(service, file_id, local_path):
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
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
            f"Nije nađen folder '{ROOT_FOLDER_NAME}' na Google Drive-u. "
            f"Proveri da li je podeljen sa servisnim nalogom (Editor pristup)."
        )
    return root_id


def _get_subtype_folder(service, root_id, content_type, subtype):
    content_folder_id = find_folder(service, content_type, root_id)
    if not content_folder_id:
        siblings = _list_subfolder_names(service, root_id)
        raise RuntimeError(
            f"Nije nađen folder '{content_type}' unutar '{ROOT_FOLDER_NAME}'. "
            f"Podfolderi koji TU stvarno postoje: {siblings or '(nijedan)'}"
        )
    subtype_folder_id = find_folder(service, subtype, content_folder_id)
    if not subtype_folder_id:
        siblings = _list_subfolder_names(service, content_folder_id)
        raise RuntimeError(
            f"Nije nađen folder '{subtype}' unutar '{content_type}'. "
            f"Podfolderi koji TU stvarno postoje: {siblings or '(nijedan)'}"
        )
    return subtype_folder_id


def count_images(content_type, subtype):
    """Vraća broj slika u {content_type}/{subtype}, ili 0 ako folder ne
    postoji ili je prazan. POPRAVKA: svaka greška se sada ODŠTAMPA u log
    pre nego što se vrati 0 (ranije se ćutke gutala, pa se nije videlo
    ZAŠTO je 'kartice' uvek ispadalo prazno)."""
    try:
        service = get_drive_service()
        root_id = _get_root(service)
        folder_id = _get_subtype_folder(service, root_id, content_type, subtype)
        n = len(list_files(service, folder_id))
        log(f"{content_type}/{subtype}: {n} slika (folder id={folder_id}).")
        return n
    except RuntimeError as e:
        log(f"GRESKA pri brojanju '{content_type}/{subtype}': {e}")
        return 0
    except Exception as e:
        log(f"NEOCEKIVANA GRESKA pri brojanju '{content_type}/{subtype}': {type(e).__name__}: {e}")
        return 0


def _choose_weighted_subtype(content_type):
    """Bira 'kartice' ili 'obicne slike' - kartice imaju prioritet
    (KARTICE_WEIGHT šanse) kad oba foldera imaju slika."""
    kartice_count = count_images(content_type, "kartice")
    obicne_count = count_images(content_type, "obicne slike")

    log(f"{content_type}: kartice={kartice_count}, obicne slike={obicne_count}, "
        f"KARTICE_WEIGHT={KARTICE_WEIGHT}")

    if kartice_count == 0 and obicne_count == 0:
        raise RuntimeError(
            f"Nema slika ni u '{content_type}/kartice' ni u '{content_type}/obicne slike'."
        )
    if kartice_count == 0:
        return "obicne slike"
    if obicne_count == 0:
        return "kartice"
    return "kartice" if random.random() < KARTICE_WEIGHT else "obicne slike"


def pick_random_image(content_type, subtype=None, work_dir="/tmp/gdrive_images"):
    """Bira JEDNU nasumičnu sliku iz {content_type}/{subtype}. Ako subtype
    nije zadat, prvo bira folder (kartice imaju prioritet), pa se vraća na
    drugi ako je prvi prazan. Vraća dict sa local_path, subtype, file_id,
    file_name, source_folder_id."""
    service = get_drive_service()
    root_id = _get_root(service)

    if subtype is None:
        subtype = _choose_weighted_subtype(content_type)

    folder_id = _get_subtype_folder(service, root_id, content_type, subtype)
    files = list_files(service, folder_id)

    if not files:
        other = "obicne slike" if subtype == "kartice" else "kartice"
        log(f"UPOZORENJE: '{content_type}/{subtype}' je prazan, probam '{content_type}/{other}'.")
        folder_id = _get_subtype_folder(service, root_id, content_type, other)
        files = list_files(service, folder_id)
        if not files:
            raise RuntimeError(f"Nema slika ni u '{content_type}/kartice' ni u '{content_type}/obicne slike'.")
        subtype = other

    chosen = random.choice(files)
    local_path = os.path.join(work_dir, content_type, subtype, chosen["name"])
    download_file(service, chosen["id"], local_path)

    return {
        "local_path": local_path,
        "subtype": subtype,
        "file_id": chosen["id"],
        "file_name": chosen["name"],
        "source_folder_id": folder_id,
    }


def pick_random_images_multi(content_type, subtype, count, allow_repeat=False, work_dir="/tmp/gdrive_images"):
    """Za carousel: ako je allow_repeat=True, bira JEDNU sliku i vraća je
    duplikovanu `count` puta (svaki slajd dobija drugačiji tekst, ali istu
    sliku). Ako je allow_repeat=False, bira do `count` RAZLIČITIH slika."""
    service = get_drive_service()
    root_id = _get_root(service)
    folder_id = _get_subtype_folder(service, root_id, content_type, subtype)
    files = list_files(service, folder_id)

    if not files:
        raise RuntimeError(f"Nema slika u '{content_type}/{subtype}'.")

    if allow_repeat:
        chosen = random.choice(files)
        local_path = os.path.join(work_dir, content_type, subtype, chosen["name"])
        download_file(service, chosen["id"], local_path)
        item = {
            "local_path": local_path,
            "subtype": subtype,
            "file_id": chosen["id"],
            "file_name": chosen["name"],
            "source_folder_id": folder_id,
        }
        return [item] * count

    k = min(count, len(files))
    chosen_files = random.sample(files, k)
    results = []
    for chosen in chosen_files:
        local_path = os.path.join(work_dir, content_type, subtype, chosen["name"])
        download_file(service, chosen["id"], local_path)
        results.append(
            {
                "local_path": local_path,
                "subtype": subtype,
                "file_id": chosen["id"],
                "file_name": chosen["name"],
                "source_folder_id": folder_id,
            }
        )
    return results


def pick_random_story_source(work_dir="/tmp/gdrive_images"):
    """Meša sve 4 kombinacije (feed/carousels x kartice/obicne slike) i
    bira jednu nasumičnu sliku iz bilo kog foldera koji ima slika. Kartice
    imaju prioritet (KARTICE_WEIGHT), ali se meša i content_type (feed vs
    carousels) ravnomerno."""
    service = get_drive_service()
    root_id = _get_root(service)

    content_type = random.choice(CONTENT_TYPES)
    try:
        subtype = _choose_weighted_subtype(content_type)
    except RuntimeError:
        other_content_type = "carousels" if content_type == "feed" else "feed"
        content_type = other_content_type
        subtype = _choose_weighted_subtype(content_type)

    folder_id = _get_subtype_folder(service, root_id, content_type, subtype)
    files = list_files(service, folder_id)

    if not files:
        other = "obicne slike" if subtype == "kartice" else "kartice"
        folder_id = _get_subtype_folder(service, root_id, content_type, other)
        files = list_files(service, folder_id)
        if not files:
            raise RuntimeError(f"Nema slika u '{content_type}' folderima.")
        subtype = other

    chosen = random.choice(files)
    local_path = os.path.join(work_dir, content_type, subtype, chosen["name"])
    download_file(service, chosen["id"], local_path)

    return {
        "local_path": local_path,
        "content_type": content_type,
        "subtype": subtype,
        "file_id": chosen["id"],
        "file_name": chosen["name"],
        "source_folder_id": folder_id,
    }


def archive_image(picked):
    """Premesti picked['file_id'] iz picked['source_folder_id'] u
    'Objavljeno' podfolder (napravljen direktno unutar source_folder_id,
    napravi se automatski ako ne postoji)."""
    service = get_drive_service()
    archive_folder_id = ensure_folder(service, ARCHIVE_FOLDER_NAME, picked["source_folder_id"])
    move_file(service, picked["file_id"], archive_folder_id, picked["source_folder_id"])
    log(f"Arhivirano: '{picked.get('file_name', picked['file_id'])}' -> Objavljeno")
