#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
spiele_laden.py - fussball.de-Crawler fuer die SV/DJK-Aufstellungs-App

Holt fuer die Herren (Erste) und die Herren-Reserve:
  * kommende Spiele (Liga + Pokal) mit eindeutiger Zuordnung I./II.
  * die aktuellen Tabellen
  * den Kader: offiziell, falls auf fussball.de freigegeben, sonst aus den
    Aufstellungen (Spielberichten) der laufenden Saison inkl. Einsatzzahl
und schreibt alles nach "spiele.js" im Ordner dieses Skripts.
aufstellung/index.html im selben Ordner liest die Datei automatisch ein.

Nutzung:   python spiele_laden.py
Benoetigt: nur Python 3. fontTools wird bei Bedarf nachinstalliert (schnellere
           Namens-Entschleierung); klappt das nicht, laeuft es ueber die
           Spielerprofile.
"""

import importlib
import io
import json
import re
import subprocess
import sys
import time
import html as htmllib
import urllib.request
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

BASE = "https://www.fussball.de"
CLUB_ID = "00ES8GNAVO0000B9VV0AG08LVUPGND5I"
CLUB_URL = (BASE + "/verein/sv-djk-nordhausen-zipplingen-wuerttemberg/-/id/"
            + CLUB_ID)
# Rueckfall, falls die Mannschaften auf der Vereinsseite nicht erkannt werden
KNOWN_TEAMS = {"I": "011MIEE3E0000000VTVG0001VTR8C1K7",
               "II": "011MID9CF8000000VTVG0001VTR8C1K7"}

URL_CLUB_TEAMS = BASE + "/ajax.club.teams/-/action/search/id/{cid}"
URL_MATCHPLAN = (BASE + "/ajax.team.matchplan/-/mime-type/HTML/"
                 "show-venues/false/team-id/{tid}")
URL_PREV = BASE + "/ajax.team.prev.games/-/mode/PAGE/team-id/{tid}"
URL_TABLE = BASE + "/ajax.team.table/-/team-id/{tid}"
URL_SQUAD = (BASE + "/ajax.team.squad/-/mode/PAGE/order-by/1/saison/{saison}/"
             "show-filter/true/team-id/{tid}")
URL_LINEUP = BASE + "/ajax.match.lineup/-/mode/PAGE/spiel/{gid}"
URL_FONT = BASE + "/export.fontface/-/format/woff/id/{font}/type/font"
URL_TEAM_PAGE = BASE + "/mannschaft/-/saison/{saison}/team-id/{tid}"
URL_GAME = BASE + "/spiel/-/spiel/{gid}"

OWN_RE = re.compile(r"nordhausen|zipplingen|zippl\.", re.I)
DATE_RE = re.compile(r"(\d{1,2}\.\d{1,2}\.\d{2,4})")
TIME_RE = re.compile(r"\b(\d{1,2}:\d{2})\b")
GAME_ID_RE = re.compile(r"/-/spiel/([0-9A-Z]{20,40})")
PUA = re.compile("[-]")

HERE = Path(__file__).resolve().parent
OUT_FILE = HERE / "spiele.js"
PREFIX = "window.SVDJK_SPIELE="

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "de-DE,de;q=0.9",
}
PAUSE = 0.4            # Sekunden zwischen Abrufen (fussball.de schonen)
LINEUP_RETRY_DAYS = 21  # so lange wird ein fehlender Spielbericht erneut gesucht
LINEUP_VERSION = 2      # 2 = mit Torwart-Kennung
VENUE_FRESH_DAYS = 10   # Spielorte naher Spiele taeglich neu pruefen

_last_request = [0.0]


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def fetch(url: str, binary: bool = False, quiet: bool = False):
    wait = PAUSE - (time.time() - _last_request[0])
    if wait > 0:
        time.sleep(wait)
    if not quiet:
        print(f"  GET {url}")
    last_err = None
    for attempt in range(2):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
            _last_request[0] = time.time()
            return raw if binary else raw.decode("utf-8", errors="replace")
        except Exception as e:          # noqa: BLE001 - alles loggen, einmal wiederholen
            last_err = e
            time.sleep(1.5)
    _last_request[0] = time.time()
    raise RuntimeError(f"{url}: {last_err}")


def clean(s: str) -> str:
    """Whitespace glaetten, unsichtbare Trennzeichen entfernen."""
    s = re.sub(r"[​-‏⁠﻿­]", "", s or "")
    return re.sub(r"\s+", " ", s).strip()


def html_text(s: str) -> str:
    return clean(htmllib.unescape(re.sub(r"<[^>]+>", " ", s or "")))


# ---------------------------------------------------------------------------
# Mini-DOM (nur Standardbibliothek)
# ---------------------------------------------------------------------------
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs=None, parent=None):
        self.tag = tag
        self.attrs = dict(attrs or [])
        self.children = []
        self.parent = parent

    @property
    def classes(self):
        return set((self.attrs.get("class") or "").split())

    def iter(self):
        for c in self.children:
            if isinstance(c, Node):
                yield c
                yield from c.iter()

    def find_all(self, tag=None, cls=None, attr=None):
        out = []
        for n in self.iter():
            if tag and n.tag != tag:
                continue
            if cls and cls not in n.classes:
                continue
            if attr and attr not in n.attrs:
                continue
            out.append(n)
        return out

    def find(self, tag=None, cls=None, attr=None):
        for n in self.iter():
            if tag and n.tag != tag:
                continue
            if cls and cls not in n.classes:
                continue
            if attr and attr not in n.attrs:
                continue
            return n
        return None

    def raw_text(self):
        parts = []
        for c in self.children:
            parts.append(c if isinstance(c, str) else c.raw_text())
        return "".join(parts)

    def text(self):
        return clean(self.raw_text())


class _Builder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#root")
        self.cur = self.root

    def handle_starttag(self, tag, attrs):
        node = Node(tag, attrs, self.cur)
        self.cur.children.append(node)
        if tag not in VOID:
            self.cur = node

    def handle_startendtag(self, tag, attrs):
        self.cur.children.append(Node(tag, attrs, self.cur))

    def handle_endtag(self, tag):
        n = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data):
        self.cur.children.append(data)


def dom(html_src: str) -> Node:
    b = _Builder()
    b.feed(html_src or "")
    b.close()
    return b.root


# ---------------------------------------------------------------------------
# Namens-Entschleierung (fussball.de rendert Namen ueber Spezial-Schriften)
# ---------------------------------------------------------------------------
_fonttools = {"state": None}     # None = ungetestet, True/False = verfuegbar
_font_maps = {}


def have_fonttools() -> bool:
    if _fonttools["state"] is not None:
        return _fonttools["state"]
    try:
        importlib.import_module("fontTools.ttLib")
        _fonttools["state"] = True
    except ImportError:
        print("  fontTools fehlt - versuche Installation ...")
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                            "--disable-pip-version-check", "fonttools"],
                           timeout=180, check=False, capture_output=True)
            importlib.invalidate_caches()
            importlib.import_module("fontTools.ttLib")
            _fonttools["state"] = True
        except Exception:               # noqa: BLE001
            print("  fontTools nicht verfuegbar - Namen kommen aus den Profilen.")
            _fonttools["state"] = False
    return _fonttools["state"]


def font_map(font_name: str) -> dict:
    if font_name in _font_maps:
        return _font_maps[font_name]
    mapping = {}
    if have_fonttools():
        try:
            from fontTools.ttLib import TTFont
            from fontTools import agl
            data = fetch(URL_FONT.format(font=font_name), binary=True, quiet=True)
            cmap = TTFont(io.BytesIO(data)).getBestCmap() or {}
            for code, glyph in cmap.items():
                ch = agl.toUnicode(glyph)
                if ch:
                    mapping[code] = ch
        except Exception as e:          # noqa: BLE001
            print(f"  Schrift {font_name} nicht lesbar: {e}")
    _font_maps[font_name] = mapping
    return mapping


def decode(node) -> str:
    """Text eines Knotens, verschleierte <span data-obfuscation> entschluesselt.
    Nicht entschluesselbare Zeichen bleiben als Privatzeichen stehen."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    font = node.attrs.get("data-obfuscation")
    if font:
        m = font_map(font)
        return "".join(m.get(ord(c), c) for c in node.raw_text())
    return "".join(decode(c) for c in node.children)


def player_name(wrapper: Node):
    """(Vorname, Nachname, Roh-Laenge Vorname) aus einem Spieler-Knoten.
    Liefert None, wenn der Name nicht veroeffentlicht ist ("k.A.")."""
    box = wrapper.find(cls="player-name") or wrapper
    fn, ln = box.find(cls="firstname"), box.find(cls="lastname")
    if fn is None and ln is None:
        full = clean(decode(box))
        if not full or full.lower().startswith("k.a"):
            return None
        parts = full.split(" ")
        return (" ".join(parts[:-1]), parts[-1], None) if len(parts) > 1 else ("", full, None)
    first, last = clean(decode(fn)), clean(decode(ln))
    if not (first or last) or (last.lower().startswith("k.a") and not first):
        return None
    raw_first = len(clean(fn.raw_text())) if fn is not None else 0
    return (first, last, raw_first)


_profile_names = {}


def resolve_name(wrapper: Node, pid: str, known: dict):
    """'Nachname Vorname' oder None. Nutzt Schrift, sonst Profilseite."""
    got = player_name(wrapper)
    if got is None:
        return None
    first, last, raw_first = got
    if not PUA.search(first + last) and (first or last):
        return clean(f"{last} {first}")
    if pid and pid in known:
        return known[pid]
    if not pid:
        return None
    # Rueckfall: Klarname von der Profilseite holen
    try:
        prof = dom(fetch(f"{BASE}/spielerprofil/-/player-id/{pid}", quiet=True))
        tag = prof.find(cls="profile-name")
        full = tag.text() if tag else ""
    except Exception:                   # noqa: BLE001
        full = ""
    if not full:
        return None
    if raw_first and len(full) > raw_first and full[raw_first] == " ":
        f, l = full[:raw_first], full[raw_first + 1:]
    else:
        parts = full.split(" ")
        f, l = " ".join(parts[:-1]), parts[-1]
    return clean(f"{l} {f}")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------
def season_info(today: date):
    start_year = today.year if today.month >= 7 else today.year - 1
    saison = f"{start_year % 100:02d}{(start_year + 1) % 100:02d}"
    return saison, date(start_year, 7, 1)


def parse_date(d: str):
    for f in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(d, f).date()
        except ValueError:
            pass
    return None


def team_items(html_src: str):
    """[(team_id, Text, Liga, URL)] aus Vereinsseite oder ajax.club.teams.
    Je Mannschaft zaehlt der Linktext der Form 'Kategorie - Name'."""
    root = dom(html_src)
    best = {}
    order = []

    def scan(container, liga_scope):
        for a in container.find_all("a"):
            href = a.attrs.get("href", "")
            m = re.search(r"team-id/([0-9A-Z]{20,40})", href)
            if not m:
                continue
            tid, text = m.group(1), a.text()
            if not text:
                continue
            liga = ""
            for b in liga_scope.find_all("a"):
                h = b.attrs.get("href", "")
                if "/spieltagsuebersicht/" in h and "pokal" not in h.lower():
                    liga = b.text()
                    break
            if tid not in best:
                order.append(tid)
                best[tid] = (tid, text, liga, href)
            elif " - " in text and " - " not in best[tid][1]:
                best[tid] = (tid, text, liga or best[tid][2], href)

    for item in root.find_all(cls="item"):
        scan(item, item)
    if not best:
        scan(root, Node("#leer"))
    return [best[t] for t in order]


def classify(items):
    """{'I': (id, name, liga, url), 'II': ...} fuer Herren und Herren-Reserve."""
    found = {}
    for tid, text, liga, href in items:
        cat, _, name = text.partition(" - ")
        cat = cat.strip().lower()
        if not name:
            continue
        if "reserve" in cat:
            found.setdefault("II", (tid, name.strip(), liga, href))
        elif cat == "herren":
            key = "II" if re.search(r"\bII\b", name) else "I"
            found.setdefault(key, (tid, name.strip(), liga, href))
    return found


def parse_games(html_src: str):
    """Spielliste (Spielplan / naechste / letzte Spiele) zeilenweise lesen."""
    games = []
    cur = {"date": "", "time": "", "comp": "", "cat": ""}

    def take_comp(txt):
        parts = [p.strip() for p in txt.split("|") if p.strip()]
        parts = [p for p in parts if not DATE_RE.search(p)]
        if parts:
            cur["comp"] = parts[-1]
            cur["cat"] = parts[0] if len(parts) > 1 else cur["cat"]

    for row in re.split(r"(?i)(?=<tr[\s>])", html_src or ""):
        head = row[:160]
        if "row-headline" in head:
            txt = html_text(row)
            dm, tm = DATE_RE.search(txt), TIME_RE.search(txt)
            if dm:
                cur["date"] = dm.group(1)
            if tm:
                cur["time"] = tm.group(1)
            take_comp(txt)
        if "row-competition" in head:
            cd = re.search(r'class="column-date"[^>]*>(.*?)</td>', row, re.S)
            if cd:
                t = html_text(cd.group(1))
                dm, tm = DATE_RE.search(t), TIME_RE.search(t)
                if dm:
                    cur["date"] = dm.group(1)
                if tm:
                    cur["time"] = tm.group(1)
            ct = re.search(r'class="column-team"[^>]*>(.*?)</td>', row, re.S)
            if ct:
                take_comp(html_text(ct.group(1)))
        names = re.findall(r"club-name[^>]*>\s*([^<]+?)\s*<", row)
        if len(names) >= 2 and cur["date"]:
            gid = GAME_ID_RE.search(row)
            games.append({
                "date": cur["date"], "time": cur["time"],
                "homeTeam": clean(htmllib.unescape(names[0])),
                "awayTeam": clean(htmllib.unescape(names[1])),
                "competition": cur["comp"], "category": cur["cat"],
                "id": gid.group(1) if gid else "",
            })
    if games:
        return games
    return parse_games_legacy(html_src)


def parse_games_legacy(page: str):
    """Alter Parser (Blockweise ab 'row-competition') als Rueckfall."""
    games = []
    for c in (page or "").split("row-competition")[1:]:
        dm = DATE_RE.search(c)
        names = re.findall(r"club-name[^>]*>\s*([^<]+?)\s*<", c)
        if not dm or len(names) < 2:
            continue
        tm = TIME_RE.search(c)
        cm = re.search(r">([^<>]*(?:Liga|Pokal|Staffel|Klasse|Runde|Turnier|"
                       r"Freundschaft|Relegation|Junioren|Jugend|Frauen)[^<>]*)<", c, re.I)
        gid = GAME_ID_RE.search(c)
        games.append({"date": dm.group(1), "time": tm.group(1) if tm else "",
                      "homeTeam": clean(htmllib.unescape(names[0])),
                      "awayTeam": clean(htmllib.unescape(names[1])),
                      "competition": html_text(cm.group(1)) if cm else "",
                      "category": "", "id": gid.group(1) if gid else ""})
    return games


def _num(s: str):
    s = clean(s).replace(",", ".")
    try:
        v = float(s)
        return int(v) if v == int(v) else v
    except ValueError:
        return s


def parse_table(html_src: str):
    rows, last_rank = [], 0
    for tr in dom(html_src).find_all("tr"):
        if "thead" in tr.classes:
            continue
        tds = tr.find_all("td")
        ci = next((i for i, td in enumerate(tds) if "column-club" in td.classes), None)
        if ci is None or len(tds) < ci + 8:
            continue
        rank_td = next((td for td in tds if "column-rank" in td.classes), None)
        rank_txt = clean(rank_td.text() if rank_td else "").rstrip(".")
        rank = int(rank_txt) if rank_txt.isdigit() else last_rank
        last_rank = rank
        club = tds[ci]
        name_node = club.find(cls="club-name")
        cls = tr.classes
        rows.append({
            "pl": rank,
            "team": clean((name_node or club).text()),
            "sp": _num(tds[ci + 1].text()), "g": _num(tds[ci + 2].text()),
            "u": _num(tds[ci + 3].text()), "v": _num(tds[ci + 4].text()),
            "tore": clean(tds[ci + 5].text()).replace(" ", ""),
            "diff": _num(tds[ci + 6].text()), "pkt": _num(tds[ci + 7].text()),
            "auf": any("promotion" in c for c in cls),
            "ab": any("relegation" in c for c in cls),
        })
    return rows


_SURFACE = re.compile(r"^(rasen|kunstrasen|hart|natur|tennen|asche|kunststoff|halle|"
                      r"kleinfeld|soccer)\w*$", re.I)
_VENUE_PREFIX = re.compile(r"^(sportplatz|sportgel(?:ä|ae)nde|sportanlage|sportpark|"
                           r"sportzentrum|stadion|hauptplatz|nebenplatz|rasenplatz|"
                           r"kunstrasenplatz)\s+(.+)$", re.I)
_CLUBISH = re.compile(r"\b(FC|SV|TSV|SG|SGM|DJK|TV|SC|VfB|VfL|VfR|Spfr|FV|TSG|SpVgg)\b")


def short_venue(raw: str) -> str:
    """'Rasenplatz, Sportplatz Pflaumloch, Kirchstr., 73469 Riesbuerg' -> 'Pflaumloch'.
    Aufbau bei fussball.de: Platzart, Sportstaette, Strasse, PLZ Ort."""
    parts = [clean(p) for p in (raw or "").split(",") if clean(p)]
    if not parts:
        return ""
    if len(parts) > 1 and _SURFACE.match(parts[0]):
        parts = parts[1:]
    town = ""
    for p in reversed(parts):
        m = re.match(r"^\d{5}\s+(.+)$", p)
        if m:
            town = m.group(1)
            break
    name = clean(re.sub(r"\([^)]*\)", " ", parts[0]))
    m = _VENUE_PREFIX.match(name)
    if m:
        toks = [t for t in m.group(2).split() if not re.fullmatch(r"\d+|[IVX]+|[A-Z]", t)]
        rest = " ".join(toks)
        if (rest and rest[0].isupper() and not re.search(r"\d|str\.|stra(ß|ss)e|weg\b", rest, re.I)
                and not _CLUBISH.search(rest)):
            return rest
    return town or name


def parse_venue(html_src: str):
    """(Rohtext, Karten-Link) aus der Spielseite."""
    root = dom(html_src)
    stage = next((n for n in root.find_all("section") if n.attrs.get("id") == "stage"), root)
    a = stage.find("a", cls="location") or root.find("a", cls="location")
    if a is None:
        return "", ""
    return a.text(), a.attrs.get("href", "")


def parse_lineup(html_src: str, known: dict):
    """[(Name, player_id, Startelf?, Torwart?)] der eigenen Mannschaft oder None."""
    root = dom(html_src)
    ml = root.find(cls="match-lineup")
    if ml is None:
        return None
    head = ml.find(cls="head") or ml
    clubs = [n.text() for n in head.find_all(cls="club-name")][:2]
    if len(clubs) != 2:
        return None
    home_own, away_own = bool(OWN_RE.search(clubs[0])), bool(OWN_RE.search(clubs[1]))
    if home_own == away_own:
        return None
    side = "home" if home_own else "away"
    players = []
    for sect, start in (("starting", True), ("substitutes", False)):
        for block in ml.find_all(cls=sect):
            for w in block.find_all(cls="player-wrapper"):
                if side not in w.classes:
                    continue
                href = w.attrs.get("href", "")
                m = re.search(r"player-id/([0-9A-Z]+)", href)
                pid = m.group(1) if m else ""
                name = resolve_name(w, pid, known)
                if pid and name:
                    known[pid] = name
                marks = {n.text() for n in w.find_all(cls="c") + w.find_all(cls="k")}
                players.append([name or "", pid, 1 if start else 0, 1 if "T" in marks else 0])
    return players


def parse_squad(html_src: str, known: dict):
    root = dom(html_src)
    txt = root.text()
    if "nicht zur Ver" in txt and "freigegeben" in txt:
        return {"status": "nicht freigegeben", "spieler": []}
    names, seen = [], set()
    for a in root.find_all("a"):
        href = a.attrs.get("href", "")
        m = re.search(r"spielerprofil/-/player-id/([0-9A-Z]+)", href)
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        name = resolve_name(a, m.group(1), known)
        if name:
            known[m.group(1)] = name
            names.append(name)
    return {"status": "ok" if names else "leer", "spieler": names}


# ---------------------------------------------------------------------------
# Hauptablauf
# ---------------------------------------------------------------------------
def load_previous():
    try:
        s = OUT_FILE.read_text(encoding="utf-8").strip()
        return json.loads(s[len(PREFIX):].rstrip(";")) if s.startswith(PREFIX) else {}
    except Exception:                   # noqa: BLE001
        return {}


def find_teams():
    items = []
    try:
        items = team_items(fetch(URL_CLUB_TEAMS.format(cid=CLUB_ID)))
    except Exception as e:              # noqa: BLE001
        print(f"  Mannschaftsliste nicht abrufbar: {e}")
    found = classify(items)
    if len(found) < 2:
        try:
            found = {**classify(team_items(fetch(CLUB_URL))), **found}
        except Exception as e:          # noqa: BLE001
            print(f"  Vereinsseite nicht abrufbar: {e}")
    for key, tid in KNOWN_TEAMS.items():
        if key not in found:
            print(f"  Herren {key} nicht erkannt - nutze bekannte ID.")
            found[key] = (tid, "SV DJK Nordhausen-Zipplingen", "", "")
    return found


def main():
    now = datetime.now()
    today = now.date()
    saison, season_start = season_info(today)
    stand = now.strftime("%d.%m.%Y %H:%M")
    prev = load_previous()
    known = dict(prev.get("names") or {})
    lineups = {k: v for k, v in (prev.get("lineups") or {}).items()
               if (parse_date(v.get("d", "")) or date(1900, 1, 1)) >= season_start}

    print(f"\n=== fussball.de laden (Saison {saison[:2]}/{saison[2:]}) ===")
    print("Mannschaften erkennen ...")
    teams = find_teams()
    for k in ("I", "II"):
        tid, name, liga, _ = teams[k]
        print(f"  {k:>2}: {name}  [{liga or '-'}]  {tid}")

    # --- Spiele -------------------------------------------------------------
    print("\nSpielplaene ...")
    games, seen_ids, past = [], set(), {"I": {}, "II": {}}
    for k in ("I", "II"):
        tid = teams[k][0]
        for url in (URL_MATCHPLAN.format(tid=tid), URL_PREV.format(tid=tid)):
            try:
                found = parse_games(fetch(url))
            except Exception as e:      # noqa: BLE001
                print(f"    -> fehlgeschlagen: {e}")
                continue
            print(f"    -> {len(found)} Spiele")
            for g in found:
                d = parse_date(g["date"])
                if not d:
                    continue
                g["team"] = k
                if d < today:
                    if d >= season_start and g["id"]:
                        past[k][g["id"]] = d
                    continue
                key = g["id"] or (g["date"], g["homeTeam"], g["awayTeam"])
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                games.append(g)
    games.sort(key=lambda g: (parse_date(g["date"]), g["time"]))

    # --- Spielorte ----------------------------------------------------------
    print("\nSpielorte ...")
    venues = {k: v for k, v in (prev.get("venues") or {}).items()
              if any(g["id"] == k for g in games)}
    today_s = today.strftime("%d.%m.%Y")
    for g in games:
        if not g["id"]:
            continue
        c = venues.get(g["id"])
        near = (parse_date(g["date"]) - today).days <= VENUE_FRESH_DAYS
        if c and c.get("raw") and (not near or c.get("t") == today_s):
            continue
        try:
            raw, url = parse_venue(fetch(URL_GAME.format(gid=g["id"]), quiet=True))
        except Exception as e:          # noqa: BLE001
            print(f"    {g['date']}: fehlgeschlagen ({e})")
            continue
        if raw:
            venues[g["id"]] = {"raw": raw, "url": url, "ort": short_venue(raw), "t": today_s}
    for g in games:
        v = venues.get(g["id"]) or {}
        g["ort"], g["venue"], g["venueUrl"] = v.get("ort", ""), v.get("raw", ""), v.get("url", "")
    print(f"    {sum(1 for g in games if g['ort'])} von {len(games)} Spielen mit Spielort")

    # --- Tabellen -----------------------------------------------------------
    print("\nTabellen ...")
    tabellen = dict(prev.get("tabellen") or {})
    for k in ("I", "II"):
        tid, name, liga, href = teams[k]
        try:
            rows = parse_table(fetch(URL_TABLE.format(tid=tid)))
            if rows:
                tabellen[k] = {"liga": liga, "stand": stand, "rows": rows,
                               "url": href or URL_TEAM_PAGE.format(saison=saison, tid=tid)}
                print(f"    -> {len(rows)} Mannschaften")
            else:
                print("    -> keine Tabelle gefunden (alte bleibt stehen)")
        except Exception as e:          # noqa: BLE001
            print(f"    -> fehlgeschlagen: {e}")

    # --- Kader: offiziell ---------------------------------------------------
    print("\nKader ...")
    offiziell = {}
    for k in ("I", "II"):
        try:
            offiziell[k] = parse_squad(
                fetch(URL_SQUAD.format(saison=saison, tid=teams[k][0])), known)
        except Exception as e:          # noqa: BLE001
            offiziell[k] = {"status": f"Fehler: {e}", "spieler": []}
        print(f"    {k:>2}: offizieller Kader {offiziell[k]['status']}"
              f" ({len(offiziell[k]['spieler'])} Namen)")

    # --- Kader: aus Aufstellungen ------------------------------------------
    todo = []
    for k in ("I", "II"):
        for gid, d in past[k].items():
            c = lineups.get(gid)
            if c and c.get("p") and c.get("v", 1) >= LINEUP_VERSION:
                continue                  # schon im Cache
            if c and c.get("leer") and (today - d).days > LINEUP_RETRY_DAYS:
                continue                  # kein Spielbericht mehr zu erwarten
            todo.append((k, gid, d))
    print(f"  {len(todo)} Spielbericht(e) neu zu lesen, "
          f"{sum(1 for v in lineups.values() if v.get('p'))} im Cache")
    for k, gid, d in todo:
        try:
            players = parse_lineup(fetch(URL_LINEUP.format(gid=gid)), known)
        except Exception as e:          # noqa: BLE001
            print(f"    -> fehlgeschlagen: {e}")
            continue
        entry = {"t": k, "d": d.strftime("%d.%m.%Y"), "v": LINEUP_VERSION}
        if players:
            entry["p"] = players
            print(f"    -> {sum(1 for p in players if p[0])} Namen"
                  f" ({sum(1 for p in players if not p[0])} ohne Freigabe)")
        else:
            entry["leer"] = True
            print("    -> noch keine Aufstellung")
        lineups[gid] = entry

    kader = {"stand": stand, "offiziell": offiziell}
    for k in ("I", "II"):
        agg = {}
        spiele = 0
        for v in lineups.values():
            if v.get("t") != k or not v.get("p"):
                continue
            spiele += 1
            for row in v["p"]:
                name, pid, start = row[0], row[1], row[2]
                tw = row[3] if len(row) > 3 else 0
                name = known.get(pid, name) if pid else name
                if not name:
                    continue
                a = agg.setdefault(pid or name, {"name": name, "spiele": 0, "startelf": 0, "tw": 0})
                a["name"] = name
                a["spiele"] += 1
                a["startelf"] += start
                a["tw"] += tw
        for name in offiziell.get(k, {}).get("spieler", []):
            if not any(a["name"] == name for a in agg.values()):
                agg[name] = {"name": name, "spiele": 0, "startelf": 0, "tw": 0}
        kader[k] = sorted(agg.values(), key=lambda a: (-a["spiele"], -a["startelf"], a["name"]))
        kader["spieleAusgewertet" + k] = spiele
        print(f"    {k:>2}: {len(kader[k])} Spieler aus {spiele} Spielbericht(en)")

    if not games and not any(tabellen.values()) and not any(kader[k] for k in ("I", "II")):
        print("\nFEHLER: Weder Spiele noch Tabellen noch Kader gefunden.")
        sys.exit(1)

    payload = {
        "stand": stand,
        "saison": f"{saison[:2]}/{saison[2:]}",
        "teams": {k: {"id": teams[k][0], "name": teams[k][1], "liga": teams[k][2],
                      "url": teams[k][3]} for k in ("I", "II")},
        "data": games,
        "tabellen": tabellen,
        "kader": kader,
        "lineups": lineups,
        "venues": venues,
        "names": known,
    }
    OUT_FILE.write_text(PREFIX + json.dumps(payload, ensure_ascii=False) + ";",
                        encoding="utf-8")

    print(f"\nOK: {len(games)} kommende Spiele, "
          f"{len(tabellen)} Tabelle(n), Kader I={len(kader['I'])} / II={len(kader['II'])}"
          f" -> {OUT_FILE.name}")
    for g in games[:8]:
        print(f"  [{g['team']:>2}] {g['date']} {g['time']}  {g['homeTeam']} - "
              f"{g['awayTeam']}  ({g['competition']})  @ {g['ort'] or '?'}")


if __name__ == "__main__":
    main()
