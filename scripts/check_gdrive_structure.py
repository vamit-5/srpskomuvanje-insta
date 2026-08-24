#!/usr/bin/env python3
"""
check_gdrive_structure.py
--------------------------
DIJAGNOSTIKA (ne menja i ne briše ništa na Google Drive-u).

Ispisuje kompletnu strukturu foldera koju pipeline koristi: ID-jeve,
tačan broj slika u svakom podfolderu, i - ako neki folder nije nađen -
tačnu listu podfoldera koji STVARNO postoje na tom mestu (da se odmah
vidi ako je razlog npr. drugačije ime foldera).

Pokreće se RUČNO kroz GitHub Actions (workflow "Proveri Google Drive
strukturu" -> Run workflow). Rezultat se vidi u logu tog run-a.
"""

import gdrive_helper as gh


def dump_folder(service, label, folder_id):
    print(f"\n=== {label}  (folder id: {folder_id}) ===")
    files = gh.list_files(service, folder_id)
    if not files:
        print("  (nema fajlova direktno u ovom folderu)")
        return
    for f in files:
        print(f"  - {f['name']}  (id={f['id']})")


def main():
    print("Proveravam Google Drive strukturu...\n")

    try:
        service = gh.get_drive_service()
    except Exception as e:
        print(f"KRITICNA GRESKA: ne mogu da se povežem na Google Drive: {e}")
        raise SystemExit(1)

    try:
        root_id = gh._get_root(service)
    except Exception as e:
        print(f"KRITICNA GRESKA: {e}")
        print("\n=> Ovo obično znači da folder 'Srpskomuvanje' ili ne postoji, ili "
              "nije deljen sa servisnim nalogom. Proveri 'client_email' iz "
              "GDRIVE_SERVICE_ACCOUNT_JSON secreta i podeli mu taj folder na Drive-u "
              "(Editor pristup).")
        raise SystemExit(1)

    print(f"OK: glavni folder 'Srpskomuvanje' pronađen (id={root_id}).")
    print(f"Podfolderi koji tu postoje: {gh._list_subfolder_names(service, root_id)}")

    for content_type in gh.CONTENT_TYPES:
        content_folder_id = gh.find_folder(service, content_type, root_id)
        if not content_folder_id:
            print(f"\nGRESKA: folder '{content_type}' nije nađen unutar 'Srpskomuvanje'.")
            continue

        print(f"\n--- {content_type} (id={content_folder_id}) ---")
        print(f"Podfolderi koji tu postoje: {gh._list_subfolder_names(service, content_folder_id)}")

        for subtype in gh.SUBTYPES:
            sub_id = gh.find_folder(service, subtype, content_folder_id)
            if not sub_id:
                print(f"\nGRESKA: folder '{subtype}' nije nađen unutar '{content_type}'.")
                continue
            dump_folder(service, f"{content_type} / {subtype}", sub_id)
            n = gh.count_images(content_type, subtype)
            print(f"  --> count_images() vraća: {n}")

    print("\nGotovo. Ako 'kartice' i dalje pokazuje 0 slika a ti znaš da ih ima, "
          "pošalji ceo ovaj log da se vidi tačan razlog.")


if __name__ == "__main__":
    main()
