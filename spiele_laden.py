#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
spiele_laden.py - Mini-Crawler fuer fussball.de (Android/Termux + PC)

Holt die kommenden Spiele aller Vereinsmannschaften und schreibt sie
als "spiele.js" in den Ordner dieses Skripts. aufstellung.html im
selben Ordner liest die Datei beim Oeffnen von "Vorbefuellen" automatisch.

Nutzung:   python spiele_laden.py
Benoetigt: nur Python 3 (keine Zusatzpakete)
"""

import json
import re
import sys
import time
import html as htmllib
import urllib.request
from datetime import date, datetime
from pathlib import Path

CLUB_URL = ("https://www.fussball.de/verein/"
            "sv-djk-nordhausen-zipplingen-wuerttemberg/-/id/"
            "00ES8GNAVO0000B9VV0AG08LVUPGND5I")
MATCHPLAN = ("https://www.fussball.de/ajax.team.matchplan/-/"
             "mime-type/HTML/show-venues/false/team-id/{tid}")
MAX_TEAMS = 12

HERE = Path(__file__).resolve().parent
DEBUG_FILE = HERE / "debug_antwort.html"
OUT_FILE = HERE / "spiele.js"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/128.0.0.0 Mobile Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "de-DE,de;q=0.9",
}

COMP_WORDS = (r"Liga|Pokal|Staffel|Klasse|Runde|Turnier|"
              r"Freundschaft|Relegation|Junioren|Jugend|Frauen")


def get_html(url: str) -> str:
    print(f"  GET {url}")
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    return raw.decode("utf-8", errors="replace")


def html_text(s: str) -> str:
    t = htmllib.unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"\s+", " ", t).strip()


def parse_matchplan(page: str) -> list:
    """fussball.de-Spielplan: je Spiel eine 'row-competition'-Zeile
    (Datum/Uhrzeit + Wettbewerb), danach die Zeile mit beiden Vereinen."""
    games = []
    chunks = page.split("row-competition")
    for c in chunks[1:]:
        dm = re.search(r"(\d{1,2}\.\d{1,2}\.\d{2,4})", c)
        if not dm:
            continue
        tm = re.search(r"(\d{1,2}:\d{2})", c)
        names = re.findall(r"club-name[^>]*>\s*([^<]+?)\s*<", c)
        if len(names) < 2:
            continue
        cm = re.search(r">([^<>]*(?:%s)[^<>]*)<" % COMP_WORDS, c, re.IGNORECASE)
        games.append({
            "date": dm.group(1),
            "time": tm.group(1) if tm else "",
            "homeTeam": html_text(names[0]),
            "awayTeam": html_text(names[1]),
            "competition": html_text(cm.group(1)) if cm else "",
        })
    return games


def parse_game_date(d: str):
    for f in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(d, f).date()
        except ValueError:
            pass
    return None


def fail(msg: str, dump: str = ""):
    if dump:
        DEBUG_FILE.write_text(dump, encoding="utf-8")
        print(f"\nFEHLER: {msg}")
        print(f"Antwort zur Analyse gespeichert: {DEBUG_FILE}")
    else:
        print(f"\nFEHLER: {msg}")
    sys.exit(1)


def main():
    print("\n=== fussball.de Spiele laden ===")
    print("Vereinsseite laden ...")
    try:
        club_html = get_html(CLUB_URL)
    except Exception as e:
        fail(f"Vereinsseite nicht erreichbar: {e}")

    team_ids = []
    for m in re.finditer(r"team-id/([0-9A-Z]{20,40})", club_html):
        tid = m.group(1)
        if tid not in team_ids:
            team_ids.append(tid)
    team_ids = team_ids[:MAX_TEAMS]
    if not team_ids:
        fail("Keine Team-IDs auf der Vereinsseite gefunden.", club_html)
    print(f"  {len(team_ids)} Mannschaft(en) gefunden.")

    all_games, last_html = [], ""
    for tid in team_ids:
        try:
            h = get_html(MATCHPLAN.format(tid=tid))
            last_html = h
            g = parse_matchplan(h)
            print(f"    -> {len(g)} Spiele im Spielplan")
            all_games.extend(g)
        except Exception as e:
            print(f"    -> Abruf fehlgeschlagen: {e}")
        time.sleep(0.4)

    today, seen, future = date.today(), set(), []
    for g in all_games:
        dt = parse_game_date(g["date"])
        if not dt or dt < today:
            continue
        key = (g["date"], g["homeTeam"], g["awayTeam"])
        if key in seen:
            continue
        seen.add(key)
        future.append(g)
    future.sort(key=lambda g: (parse_game_date(g["date"]), g["time"]))

    if not future:
        fail("Spielplaene geladen, aber keine kommenden Spiele erkannt.",
             last_html)

    payload = {"stand": datetime.now().strftime("%d.%m.%Y %H:%M"),
               "data": future}
    OUT_FILE.write_text("window.SVDJK_SPIELE=" +
                        json.dumps(payload, ensure_ascii=False) + ";",
                        encoding="utf-8")

    print(f"\nOK: {len(future)} kommende Spiele gespeichert in {OUT_FILE}")
    for g in future[:10]:
        print(f"  {g['date']} {g['time']}  {g['homeTeam']} - "
              f"{g['awayTeam']}  [{g['competition']}]")
    print("\nJetzt aufstellung.html (im selben Ordner) neu laden "
          "-> Vorbefuellen oeffnen.")


if __name__ == "__main__":
    main()
