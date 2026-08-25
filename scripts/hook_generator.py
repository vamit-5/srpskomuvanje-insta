#!/usr/bin/env python3
"""
hook_generator.py
--------------------
Generiše NASUMIČNE "hook" tekstove (statistika o Srbiji/SrpskoMuvanje) na
osnovu unapred pripremljenih šablona sa promenljivim brojevima. Ovo je
DOPUNA statičkoj listi "hooks" iz content/confessions.json - cilj je da
postoji praktično NEOGRANIČENA raznovrsnost (hiljade različitih verzija),
a ne da se uvek ponavlja isti mali skup fiksnih tekstova.

Svaki šablon ima:
  - "overlay"  - tekst koji ide PREKO slike (sa {n} ili {pct} placeholder-om)
  - "caption"  - tekst koji ide u opis (caption), VEZAN za ISTI broj
  - "kind"     - "int" (ceo broj, formatiran sa tačkom kao razdelnikom
                 hiljada, npr. 29.402) ili "pct" (procenat sa jednom
                 decimalom, npr. 87,3%)
  - "min"/"max" - opseg iz kog se nasumično bira broj

generate_hook() vraća {"overlay": ..., "caption": ...} - isti oblik kao
stavke u "hooks" listi u confessions.json, tako da se mogu koristiti na
identičan način.
"""

import random

HOOK_TEMPLATES = [
    {
        "overlay": "Na SrpskoMuvanje se svaki dan razmeni preko {n} poruka.",
        "caption": "Dok ti razmišljaš da li da probaš, {n} poruka je već razmenjeno danas. Uključi se. 😏",
        "kind": "int", "min": 800, "max": 6000,
    },
    {
        "overlay": "Ove nedelje je {n} novih profila otvoreno na SrpskoMuvanje.",
        "caption": "{n} novih ljudi ove nedelje. Neko od njih možda traži baš tebe. Link u bio-u. 🔥",
        "kind": "int", "min": 150, "max": 2500,
    },
    {
        "overlay": "{n} parova se ove godine upoznalo preko SrpskoMuvanje.",
        "caption": "{n} priča je počelo ovde. Tvoja još nije - a mogla bi. 👀",
        "kind": "int", "min": 40, "max": 900,
    },
    {
        "overlay": "Preko {n} korisnika iz Beograda je već na SrpskoMuvanje.",
        "caption": "Beograd je pun ljudi koji traže isto što i ti. {n} ih je već unutra. Pridruži se. 🇷🇸",
        "kind": "int", "min": 300, "max": 8000,
    },
    {
        "overlay": "Preko {n} korisnika iz Novog Sada je već na SrpskoMuvanje.",
        "caption": "Novi Sad se muva na SrpskoMuvanje - {n} profila i broji se dalje. Ti gde si? 😏",
        "kind": "int", "min": 150, "max": 4000,
    },
    {
        "overlay": "{pct} korisnika SrpskoMuvanje kaže da je pronašlo bolji razgovor nego na drugim aplikacijama.",
        "caption": "Dosta botova i praznih razgovora. Ovde je {pct} ljudi zadovoljno. Probaj i ti. 🔥",
        "kind": "pct", "min": 60.0, "max": 95.0,
    },
    {
        "overlay": "Prosečno se na SrpskoMuvanje mečuje neko svakih {n} sekundi.",
        "caption": "Dok si pročitao ovo, negde se upravo desio novi meč. Ti čekaš zašto? 😏",
        "kind": "int", "min": 8, "max": 90,
    },
    {
        "overlay": "{n} ljudi je ove nedelje rešilo da prestane da čeka i da se prijavi na SrpskoMuvanje.",
        "caption": "{n} ljudi je odlučilo da promeni nešto ove nedelje. Ti čekaš savršen trenutak? Ne postoji. Uđi sad. 👀",
        "kind": "int", "min": 200, "max": 3000,
    },
    {
        "overlay": "Skoro {pct} mladih u Srbiji priznaje da im nedostaje neko ozbiljan razgovor uveče.",
        "caption": "Ako si u tih {pct} - znaš gde da odeš. SrpskoMuvanje, besplatno, diskretno. 🔥",
        "kind": "pct", "min": 55.0, "max": 85.0,
    },
    {
        "overlay": "Na SrpskoMuvanje je trenutno online preko {n} ljudi iz cele Srbije.",
        "caption": "{n} ljudi je online baš sad. Neko od njih čeka tvoju poruku. Ne oklevaj. 😏",
        "kind": "int", "min": 500, "max": 9000,
    },
    {
        "overlay": "{n} korisnika je ovog meseca promenilo status iz 'sam' u 'pričamo se'.",
        "caption": "{n} ljudi je ovog meseca prestalo da bude samo. Ko je sledeći? SrpskoMuvanje. 🔥",
        "kind": "int", "min": 80, "max": 1200,
    },
    {
        "overlay": "Prosečan korisnik SrpskoMuvanje dobije prvu poruku za manje od {n} minuta.",
        "caption": "Manje od {n} minuta i već pričaš sa nekim novim. Zašto bi čekao duže? 👀",
        "kind": "int", "min": 5, "max": 40,
    },
    {
        "overlay": "Preko {n} fotografija je postavljeno na SrpskoMuvanje profilima ovog meseca.",
        "caption": "{n} novih lica ovog meseca. Jesi li video sve? Vreme je da uđeš i ti. 🔥",
        "kind": "int", "min": 1000, "max": 15000,
    },
    {
        "overlay": "{n} Srba je ovog meseca izbrisalo staru dating aplikaciju i prešlo na SrpskoMuvanje.",
        "caption": "{n} ljudi je reklo dosta stranim aplikacijama punim botova. SrpskoMuvanje - samo pravi Srbi. 🇷🇸",
        "kind": "int", "min": 100, "max": 2000,
    },
    {
        "overlay": "SrpskoMuvanje raste za preko {n} novih korisnika svake nedelje.",
        "caption": "{n} novih ljudi svake nedelje. Zajednica raste - pridruži se dok je najbolji trenutak. 🔥",
        "kind": "int", "min": 200, "max": 3500,
    },
    {
        "overlay": "{pct} korisnika SrpskoMuvanje kaže da je aplikacija diskretnija od očekivanog.",
        "caption": "Diskrecija je prioritet. {pct} se slaže. Tvoja privatnost, tvoja pravila. SrpskoMuvanje. 😏",
        "kind": "pct", "min": 80.0, "max": 98.0,
    },
    {
        "overlay": "Poslednjih 30 dana je poslato preko {n} „cao“ poruka na SrpskoMuvanje.",
        "caption": "Sve počinje od jednog „cao“. {n} ljudi je već krenulo. Tvoj red. 👀",
        "kind": "int", "min": 5000, "max": 40000,
    },
    {
        "overlay": "{n} korisnika SrpskoMuvanje je stariji od 30 godina i i dalje aktivno traži nekog ozbiljnog.",
        "caption": "Ljubav nema rok trajanja. {n} ljudi to zna. A ti? SrpskoMuvanje. 🔥",
        "kind": "int", "min": 300, "max": 4000,
    },
    {
        "overlay": "{n} korisnika SrpskoMuvanje ima manje od 25 godina.",
        "caption": "Mladi Srbi su već ovde - {n} i broji se dalje. Ne ostaj poslednji. 😏",
        "kind": "int", "min": 500, "max": 6000,
    },
    {
        "overlay": "Za poslednjih {n} dana, SrpskoMuvanje beleži rekordan broj novih prijava.",
        "caption": "Poslednjih {n} dana je bilo ludo - toliko novih ljudi. Uskoči dok traje. 🔥",
        "kind": "int", "min": 3, "max": 30,
    },
    {
        "overlay": "Preko {n} korisnika je ostavilo pozitivnu ocenu za SrpskoMuvanje iskustvo.",
        "caption": "{n} zadovoljnih ljudi ne može da greši. Probaj i vidi zašto. 👀",
        "kind": "int", "min": 200, "max": 5000,
    },
    {
        "overlay": "Skoro {pct} korisnika SrpskoMuvanje kaže da su dobili bar jedan ozbiljan razgovor u prvih 7 dana.",
        "caption": "Prvih 7 dana je najvažnije - i {pct} ljudi to potvrđuje. Tvojih 7 dana počinje sad. SrpskoMuvanje. 🔥",
        "kind": "pct", "min": 50.0, "max": 90.0,
    },
]


def _format_int(n):
    """29402 -> '29.402' (srpski razdelnik hiljada je tačka)."""
    return f"{n:,}".replace(",", ".")


def _format_pct(x):
    """87.3 -> '87,3%' (srpska decimalna zapeta)."""
    return f"{x:.1f}".replace(".", ",") + "%"


def generate_hook():
    """Bira NASUMIČAN šablon i NASUMIČAN broj unutar njegovog opsega, i
    vraća {"overlay": ..., "caption": ...} - identičnog oblika kao
    ručno pisane stavke u 'hooks' listi. Isti broj se koristi i u
    overlay-u i u caption-u (vezani su)."""
    template = random.choice(HOOK_TEMPLATES)

    if template["kind"] == "int":
        value = random.randint(template["min"], template["max"])
        display = _format_int(value)
    else:
        value = random.uniform(template["min"], template["max"])
        display = _format_pct(value)

    return {
        "overlay": template["overlay"].format(n=display, pct=display),
        "caption": template["caption"].format(n=display, pct=display),
    }
