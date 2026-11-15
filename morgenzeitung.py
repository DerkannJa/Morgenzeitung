#!/usr/bin/env python3
"""Morgenzeitung: RSS + Home Assistant -> EPUB -> Send-to-Kindle (Gmail).

Aufruf:
  morgenzeitung.py                 bauen und an den Kindle schicken
  morgenzeitung.py --nosend        nur bauen (EPUB landet im Ausgabe-Ordner)
  morgenzeitung.py --config PFAD   andere Konfigurationsdatei
"""
import argparse
import datetime as dt
import html
import json
import logging
import re
import smtplib
import sys
import tomllib
import uuid
import zipfile
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import requests

try:
    import trafilatura
except ImportError:  # Volltext ist optional
    trafilatura = None
try:
    from googlenewsdecoder import gnewsdecoder
except ImportError:  # Google-News-Links auflösen ist optional
    gnewsdecoder = None

TZ = ZoneInfo("Europe/Berlin")
UA = "Mozilla/5.0 (X11; Linux x86_64) Morgenzeitung/1.0"
TAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
          "September", "Oktober", "November", "Dezember"]
WETTER = {
    "clear-night": "klar", "cloudy": "bewölkt", "exceptional": "Unwetter", "fog": "Nebel",
    "hail": "Hagel", "lightning": "Gewitter", "lightning-rainy": "Gewitter mit Regen",
    "partlycloudy": "teils bewölkt", "pouring": "Starkregen", "rainy": "Regen",
    "snowy": "Schnee", "snowy-rainy": "Schneeregen", "sunny": "sonnig",
    "windy": "windig", "windy-variant": "windig, bewölkt",
}
log = logging.getLogger("morgenzeitung")


def esc(s):
    return html.escape(s or "", quote=True)


def datum_lang(d):
    return f"{TAGE[d.weekday()]}, {d.day}. {MONATE[d.month - 1]} {d.year}"


def text_kurz(s, max_woerter):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(re.sub(r"\s+", " ", s)).strip()
    w = s.split(" ")
    return s if len(w) <= max_woerter else " ".join(w[:max_woerter]) + " …"


# ---------------------------------------------------------------- Home Assistant
class HA:
    def __init__(self, cfg):
        self.url = cfg["url"].rstrip("/")
        self.h = {"Authorization": f"Bearer {cfg['token']}", "Content-Type": "application/json"}

    def get(self, pfad, **params):
        r = requests.get(self.url + pfad, headers=self.h, params=params, timeout=20)
        r.raise_for_status()
        return r.json()

    def post(self, pfad, body):
        r = requests.post(self.url + pfad, headers=self.h, json=body, timeout=20)
        r.raise_for_status()
        return r.json()


def zahl(x):
    try:
        return f"{float(x):.0f}"
    except (TypeError, ValueError):
        return "?"


def wetter_html(ha, entity):
    st = ha.get(f"/api/states/{entity}")
    a = st.get("attributes", {})
    einheit = a.get("temperature_unit", "°C")
    jetzt = [f"{esc(WETTER.get(st['state'], st['state']))}, {zahl(a.get('temperature'))} {esc(einheit)}"]
    if a.get("humidity") is not None:
        jetzt.append(f"Luftfeuchte {zahl(a['humidity'])} %")
    if a.get("wind_speed") is not None:
        jetzt.append(f"Wind {zahl(a['wind_speed'])} {esc(a.get('wind_speed_unit', 'km/h'))}")
    teile = [f"<p class='zeile'><b>Jetzt</b> {', '.join(jetzt)}</p>"]
    try:
        res = ha.post("/api/services/weather/get_forecasts?return_response",
                      {"entity_id": entity, "type": "daily"})
        heute = dt.datetime.now(TZ).date()
        for f in res["service_response"][entity]["forecast"][:4]:
            tag = dt.datetime.fromisoformat(f["datetime"]).astimezone(TZ).date()
            name = "Heute" if tag == heute else TAGE[tag.weekday()][:2]
            regen = f.get("precipitation_probability")
            regen = f", Regen {zahl(regen)} %" if regen is not None else ""
            teile.append(f"<p class='zeile'><b>{name}</b> "
                         f"{esc(WETTER.get(f.get('condition'), f.get('condition', '')))}, "
                         f"{zahl(f.get('templow'))} bis {zahl(f.get('temperature'))} {esc(einheit)}{regen}</p>")
    except Exception as e:  # Vorhersage optional
        log.warning("Wettervorhersage nicht verfügbar: %s", e)
    return "".join(teile)


def termine_html(ha, kalender, tage):
    jetzt = dt.datetime.now(TZ)
    start = jetzt.replace(hour=0, minute=0, second=0, microsecond=0)
    ende = start + dt.timedelta(days=tage)
    events = []
    for cal in kalender:
        try:
            for e in ha.get(f"/api/calendars/{cal}", start=start.isoformat(), end=ende.isoformat()):
                s = e["start"]
                if "dateTime" in s:
                    beginn = dt.datetime.fromisoformat(s["dateTime"]).astimezone(TZ)
                    ganztag = False
                else:
                    beginn = dt.datetime.fromisoformat(s["date"]).replace(tzinfo=TZ)
                    ganztag = True
                events.append((beginn, ganztag, e.get("summary", "(ohne Titel)"), e.get("location")))
        except Exception as ex:
            log.warning("Kalender %s nicht lesbar: %s", cal, ex)
    if not events:
        return "<p class='zeile leer'>Keine Termine.</p>"
    events.sort(key=lambda x: (x[0].date(), not x[1], x[0]))
    out = []
    for beginn, ganztag, titel, ort in events:
        diff = (beginn.date() - start.date()).days
        tag = "Heute" if diff == 0 else "Morgen" if diff == 1 else TAGE[beginn.weekday()]
        zeit = "" if ganztag else " " + beginn.strftime("%H:%M")
        ort = f" <i>({esc(ort)})</i>" if ort else ""
        out.append(f"<p class='zeile'><b>{tag}{zeit}</b> {esc(titel)}{ort}</p>")
    return "".join(out)


def feiertage_html(ha, kalender, tage):
    """Feiertage der nächsten `tage` Tage (ab heute), oder None wenn keine."""
    heute = dt.datetime.now(TZ).date()
    start = dt.datetime.combine(heute, dt.time(), TZ)
    ende = start + dt.timedelta(days=tage + 1)
    eintraege = []
    for e in ha.get(f"/api/calendars/{kalender}", start=start.isoformat(), end=ende.isoformat()):
        s = e["start"]
        tag = dt.date.fromisoformat(s["date"]) if "date" in s else \
            dt.datetime.fromisoformat(s["dateTime"]).astimezone(TZ).date()
        diff = (tag - heute).days
        wann = "heute" if diff == 0 else "morgen" if diff == 1 else f"in {diff} Tagen"
        eintraege.append((tag, f"<p class='zeile'><b>{TAGE[tag.weekday()][:2]}, {tag:%d.%m.}</b> "
                               f"{esc(e.get('summary', ''))} <i>({wann})</i></p>"))
    if not eintraege:
        return None
    eintraege.sort()
    return "".join(x for _, x in eintraege)


# ---------------------------------------------------------------- Nachrichten
def eintrag_zeit(e):
    for k in ("published_parsed", "updated_parsed"):
        t = e.get(k)
        if t:
            return dt.datetime(*t[:6], tzinfo=dt.timezone.utc)
    return None


def titel_norm(t):
    t = re.sub(r"\s+-\s+[^-]+$", "", t or "")  # " - Quelle" bei Google News entfernen
    return re.sub(r"\W+", " ", t.lower()).strip()


# Google leitet Anfragen aus der EU auf die Cookie-Zustimmung um. Mit diesen
# Cookies gilt die Zustimmung als erteilt bzw. abgelehnt und die Seite lädt direkt.
GOOGLE_COOKIES = {"CONSENT": "YES+cb", "SOCS": "CAESEwgDEgk0ODE3Nzk3MjQaAmVuIAEaBgiA_LyaBg"}


def google_link_aufloesen(url):
    m = re.search(r"/articles/([^?/]+)", url)
    if not m:
        return None
    art_id = m.group(1)
    s = requests.Session()
    s.headers["User-Agent"] = UA
    s.cookies.update(GOOGLE_COOKIES)
    sig = ts = None
    for basis in ("https://news.google.com/articles/", "https://news.google.com/rss/articles/"):
        r = s.get(basis + art_id, timeout=20)
        sig = re.search(r'data-n-a-sg="([^"]+)"', r.text)
        ts = re.search(r'data-n-a-ts="([^"]+)"', r.text)
        if sig and ts:
            break
    if not (sig and ts):
        log.info("Google-Seite ohne Signatur (Status %s, Ziel %s)", r.status_code, r.url[:60])
        return None
    req = ('["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
           f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{art_id}",{ts.group(1)},"{sig.group(1)}"]')
    payload = {"f.req": json.dumps([[["Fbv4je", req]]])}
    r = s.post("https://news.google.com/_/DotsSplashUi/data/batchexecute", data=payload,
               headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"}, timeout=20)
    daten = json.loads(r.text.split("\n\n")[1])[:-2]
    return json.loads(daten[0][2])[1]


def echter_link(url):
    """Google-News-Weiterleitung in die echte Artikel-URL auflösen."""
    if "news.google.com" not in url:
        return url
    try:
        echt = google_link_aufloesen(url)
        if echt:
            return echt
    except Exception as e:
        log.info("Google-Link (eigene Methode) nicht auflösbar: %s", e)
    if not gnewsdecoder:
        return None
    try:
        res = gnewsdecoder(url, interval=1)
        if res.get("status"):
            return res["decoded_url"]
        log.info("Google-Link nicht auflösbar: %s", res.get("message"))
    except Exception as e:
        log.info("Google-Link nicht auflösbar: %s", e)
    return None


def volltext(url, max_woerter):
    if not trafilatura or not url:
        return None
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        r.raise_for_status()
        txt = trafilatura.extract(r.content, include_comments=False, include_tables=False,
                                  favor_precision=True)
    except Exception as e:
        log.info("Volltext fehlgeschlagen (%s): %s", url, e)
        return None
    if not txt:
        return None
    # Bezahlschranken liefern oft Platzhaltertext ("Lorem ipsum ...") statt des Artikels.
    # Alles ab dem ersten Platzhalter-Absatz verwerfen; bleibt zu wenig übrig -> kein Volltext.
    zeilen, bezahlschranke = [], False
    for p in txt.split("\n"):
        if re.search(r"lorem ipsum|consetetur sadipscing|dolor sit amet", p, re.I):
            bezahlschranke = True
            break
        zeilen.append(p)
    txt = "\n".join(zeilen)
    if len(txt.split()) < 60:
        if bezahlschranke:
            log.info("Bezahlschranke erkannt, Artikel übersprungen: %s", url)
        return None
    absaetze, woerter = [], 0
    for p in txt.split("\n"):
        p = p.strip()
        if not p:
            continue
        n = len(p.split())
        if woerter + n > max_woerter:
            rest = max(0, max_woerter - woerter)
            if rest > 15:
                absaetze.append(f"<p>{esc(' '.join(p.split()[:rest]))} …</p>")
            absaetze.append("<p class='gekuerzt'>(gekürzt)</p>")
            break
        absaetze.append(f"<p>{esc(p)}</p>")
        woerter += n
    else:
        if bezahlschranke:
            absaetze.append("<p class='gekuerzt'>(Rest hinter Bezahlschranke)</p>")
    return "".join(absaetze)


def feed_laden(url, grenze):
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        resp.raise_for_status()
        feed = feedparser.parse(resp.content)
    except Exception as e:
        log.warning("Feed nicht erreichbar (%s): %s", url, e)
        return []
    eintraege = []
    for e in feed.entries:
        zeit = eintrag_zeit(e)
        if zeit and zeit < grenze:
            continue
        eintraege.append((zeit or grenze, e, feed.feed.get("title", "")))
    eintraege.sort(key=lambda x: x[0], reverse=True)
    return eintraege


def rubrik_sammeln(r, gesehen, max_woerter):
    grenze = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=r.get("max_alter_stunden", 48))
    max_woerter = r.get("max_woerter", max_woerter)
    nur_mit_text = r.get("nur_mit_text", False)
    woerter = r.get("stichwoerter") or []
    stichwort_re = re.compile(r"\b(" + "|".join(re.escape(w) for w in woerter) + r")\b", re.I) if woerter else None
    # Feeds abwechselnd durchgehen, damit jede Quelle zum Zug kommt
    listen = [feed_laden(u, grenze) for u in r["feeds"]]
    kandidaten = []
    for i in range(max((len(l) for l in listen), default=0)):
        kandidaten += [l[i] for l in listen if i < len(l)]

    artikel = []
    versuche = 0
    for zeit, e, feedtitel in kandidaten:
        if len(artikel) >= r.get("anzahl", 5) or versuche >= r.get("anzahl", 5) * 3:
            break
        key = titel_norm(e.get("title"))
        if not key or key in gesehen:
            continue
        titel = html.unescape(e.get("title", "")).strip()
        if stichwort_re and not stichwort_re.search(titel + " " + (e.get("summary") or "")):
            continue  # passt zu keinem Stichwort der Rubrik
        quelle = (e.get("source") or {}).get("title") or feedtitel
        if quelle and titel.endswith(" - " + quelle):
            titel = titel[: -len(quelle) - 3]
        body = None
        if r.get("volltext"):
            versuche += 1
            body = volltext(echter_link(e.get("link", "")), max_woerter)
        if not body:
            kurz = text_kurz(e.get("summary") or e.get("description"), 120)
            if kurz and titel_norm(kurz) != key and not kurz.startswith(titel[:40]):
                body = f"<p>{esc(kurz)}</p>"
            else:
                body = ""
        if nur_mit_text and len(re.sub(r"<[^>]+>", " ", body).split()) < 25:
            continue  # nur Überschrift -> überspringen, nächsten Artikel nehmen
        gesehen.add(key)
        artikel.append({
            "titel": titel,
            "quelle": quelle,
            "zeit": zeit.astimezone(TZ),
            "body": body,
        })
    log.info("Rubrik %s: %d Artikel", r["name"], len(artikel))
    return artikel


# ---------------------------------------------------------------- Flohmarkt-Termine (hessenschau)
DATUM_RE = re.compile(r"^\d{1,2}\.(/\d{1,2}\.)?\s*(Januar|Februar|März|April|Mai|Juni|Juli|August|"
                      r"September|Oktober|November|Dezember)")
STOP_ZEILEN = ("weitere informationen", "ihr flohmarkt auf hessenschau", "termine für ihre veranstaltungen",
               "schicken sie uns", "ende der weiteren informationen")


def ort_treffer(ueberschrift, orte_l):
    """'Rüsselsheim am Main' passt zu 'Rüsselsheim', 'Langenselbold' aber nicht zu 'Langen'."""
    u = ueberschrift.lower()
    for kl, ort in orte_l.items():
        if u == kl or u.startswith(kl + " ") or u.startswith(kl + "-") or u.startswith(kl + "("):
            return ort
    return None


def termine_parsen(text, orte, mit_maerkten=False):
    """Zerlegt die hessenschau-Terminübersicht in {Ort: [Einträge]} für die gewünschten Orte."""
    orte_l = {o.lower(): o for o in orte}
    ergebnis, ort, abschnitt, datum = {}, None, "floh", None
    for zeile in (z.strip() for z in text.splitlines()):
        if not zeile:
            continue
        kl = zeile.lower()
        if any(kl.startswith(s) for s in STOP_ZEILEN):
            break
        if kl.startswith("märkte und feste") or kl == "märkte" or kl == "feste":
            abschnitt, ort = "markt", None
            continue
        if kl in ("flohmärkte", "direkt zu den märkten und festen"):
            continue
        ist_eintrag = zeile.startswith(("-", "–", "•")) or DATUM_RE.match(zeile)
        if not ist_eintrag and len(zeile) < 40 and "uhr" not in kl:
            ort, datum = zeile, None  # neue Ortsüberschrift
            continue
        if abschnitt == "markt" and not mit_maerkten:
            continue
        treffer = ort_treffer(ort, orte_l) if ort else None
        if not treffer:
            continue
        m = DATUM_RE.match(zeile)
        if m and len(zeile) <= len(m.group(0)) + 20 and not re.search(r"\s[-–]\s", zeile):
            datum = zeile  # reine Datumszeile, Text folgt in der nächsten Zeile
            continue
        eintrag = zeile.lstrip("-–• ").strip()
        if datum and not DATUM_RE.match(eintrag):
            eintrag = f"{datum} – {eintrag}"
        else:
            eintrag = re.sub(r"\s+-\s+", " – ", eintrag, count=1)
        ergebnis.setdefault(treffer, []).append(eintrag)
    return ergebnis


def termine_sammeln(r):
    """Rubrik-Typ 'hessenschau_termine': aktuelle Flohmarkt-Übersicht laden und nach Orten filtern."""
    grenze = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=8)
    seite = None
    for zeit, e, _ in feed_laden(r["feeds"][0], grenze):
        if "flohmaerkte" in e.get("link", "") or "Flohmärkte" in e.get("title", ""):
            seite = (zeit, e)
            break
    if not seite:
        log.info("Rubrik %s: keine aktuelle Terminübersicht gefunden", r["name"])
        return []
    zeit, e = seite
    try:
        resp = requests.get(e["link"], headers={"User-Agent": UA}, timeout=20)
        resp.raise_for_status()
        text = trafilatura.extract(resp.content, include_tables=False, favor_recall=True) or ""
    except Exception as ex:
        log.warning("Terminübersicht nicht ladbar: %s", ex)
        return []
    orte = r.get("orte", [])
    gefunden = termine_parsen(text, orte, r.get("mit_maerkten", False))
    if not gefunden:
        log.info("Rubrik %s: keine Termine in deinen Orten", r["name"])
        return []
    body = []
    for ort in orte:  # Reihenfolge wie in der Konfiguration (nächste Orte zuerst)
        for eintrag in gefunden.get(ort, []):
            body.append(f"<p class='zeile'><b>{esc(ort)}</b> {esc(eintrag)}</p>")
    titel = html.unescape(e.get("title", "Flohmärkte")).strip()
    log.info("Rubrik %s: %d Termine", r["name"], len(body))
    return [{"titel": titel, "quelle": "hessenschau.de", "zeit": zeit.astimezone(TZ), "body": "".join(body)}]


# ---------------------------------------------------------------- EPUB
CSS = """
body { margin: 0; padding: 0; line-height: 1.4; }
p { margin: 0 0 0.6em 0; text-indent: 0; text-align: left; hyphens: auto; -webkit-hyphens: auto; }

/* Titelkopf */
.kopf { text-align: center; border-top: 3px solid #000; border-bottom: 1px solid #000;
        padding: 0.4em 0 0.3em; margin: 0 0 0.2em; }
.kopf h1 { font-size: 1.45em; font-weight: bold; margin: 0; letter-spacing: 0.03em; }
.datum { text-align: center; font-size: 0.85em; text-transform: uppercase; letter-spacing: 0.08em;
         border-bottom: 3px solid #000; padding: 0.2em 0 0.3em; margin: 0 0 1.2em; }

/* Kästen auf der Titelseite */
.kasten { margin: 0 0 1.1em; }
.kasten h2 { font-size: 0.85em; text-transform: uppercase; letter-spacing: 0.1em; font-weight: bold;
             border-bottom: 1px solid #000; padding-bottom: 0.15em; margin: 0 0 0.4em; }
.zeile { margin: 0 0 0.25em; text-align: left; }
.zeile b { display: inline-block; min-width: 5em; }
.leer { font-style: italic; color: #555; }
.inhalt a { text-decoration: none; color: #000; }
.inhalt .zeile b { min-width: 0; }

/* Rubriken und Artikel */
.rubrik { font-size: 1.1em; font-weight: bold; text-transform: uppercase; letter-spacing: 0.08em;
          text-align: left; border-top: 3px solid #000; border-bottom: 1px solid #000;
          padding: 0.25em 0; margin: 0 0 0.8em; }
.artikel { margin: 0 0 1em; padding-bottom: 0.8em; border-bottom: 1px solid #999; }
.artikel.letzter { border-bottom: none; }
.artikel h3 { font-size: 1.1em; font-weight: bold; line-height: 1.25; text-align: left; margin: 0 0 0.2em; }
.meta { font-size: 0.75em; color: #555; text-transform: uppercase; letter-spacing: 0.05em; margin: 0 0 0.5em; }
.gekuerzt { font-size: 0.8em; font-style: italic; color: #555; }
.ende { text-align: center; margin-top: 2em; font-size: 0.85em; letter-spacing: 0.15em; }
"""


def xhtml(titel, inhalt):
    return ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
            '<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
            f'xml:lang="de" lang="de"><head><meta charset="utf-8"/><title>{esc(titel)}</title>'
            '<link rel="stylesheet" type="text/css" href="style.css"/></head>'
            f'<body>{inhalt}</body></html>')


def epub_bauen(pfad, titel, heute, kapitel):
    """kapitel: Liste von (dateiname, titel, html_inhalt)."""
    ident = f"urn:uuid:{uuid.uuid4()}"
    geaendert = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest = "".join(f'<item id="c{i}" href="{d}" media-type="application/xhtml+xml"/>'
                       for i, (d, _, _) in enumerate(kapitel))
    spine = "".join(f'<itemref idref="c{i}"/>' for i in range(len(kapitel)))
    opf = f"""<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid" xml:lang="de">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="bookid">{ident}</dc:identifier>
<dc:title>{esc(titel)} – {heute:%d.%m.%Y}</dc:title>
<dc:language>de</dc:language>
<dc:creator>Morgenzeitung</dc:creator>
<dc:date>{heute:%Y-%m-%d}</dc:date>
<meta property="dcterms:modified">{geaendert}</meta>
</metadata>
<manifest>
<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
<item id="css" href="style.css" media-type="text/css"/>
{manifest}
</manifest>
<spine toc="ncx">{spine}</spine>
</package>"""
    nav_li = "".join(f'<li><a href="{d}">{esc(t)}</a></li>' for d, t, _ in kapitel)
    nav = xhtml("Inhalt", f'<nav epub:type="toc" id="toc"><h2>Inhalt</h2><ol>{nav_li}</ol></nav>')
    ncx_pts = "".join(
        f'<navPoint id="n{i}" playOrder="{i + 1}"><navLabel><text>{esc(t)}</text></navLabel>'
        f'<content src="{d}"/></navPoint>' for i, (d, t, _) in enumerate(kapitel))
    ncx = (f'<?xml version="1.0" encoding="utf-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
           f'version="2005-1"><head><meta name="dtb:uid" content="{ident}"/></head>'
           f'<docTitle><text>{esc(titel)}</text></docTitle><navMap>{ncx_pts}</navMap></ncx>')
    container = ('<?xml version="1.0" encoding="utf-8"?><container version="1.0" '
                 'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                 '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
                 '</rootfiles></container>')
    with zipfile.ZipFile(pfad, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/nav.xhtml", nav, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/toc.ncx", ncx, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/style.css", CSS, compress_type=zipfile.ZIP_DEFLATED)
        for d, t, inhalt in kapitel:
            z.writestr(f"OEBPS/{d}", xhtml(t, inhalt), compress_type=zipfile.ZIP_DEFLATED)


def zeitung_bauen(cfg):
    z = cfg["zeitung"]
    heute = dt.datetime.now(TZ)
    max_w = z.get("max_woerter_pro_artikel", 900)

    # Titelseite mit Wetter + Terminen
    titelseite = [f"<div class='kopf'><h1>{esc(z['titel'])}</h1></div>"
                  f"<p class='datum'>{datum_lang(heute)}</p>"]

    def kasten(titel, inhalt):
        titelseite.append(f"<div class='kasten'><h2>{titel}</h2>{inhalt}</div>")

    hacfg = cfg.get("homeassistant")
    if hacfg and "LANGLEBIG" not in hacfg.get("token", "LANGLEBIG"):
        ha = HA(hacfg)
        try:
            kasten("Wetter", wetter_html(ha, hacfg["wetter"]))
        except Exception as e:
            log.warning("Wetter nicht verfügbar: %s", e)
        kasten("Termine", termine_html(ha, hacfg.get("kalender", []), hacfg.get("kalender_tage", 2)))
        if hacfg.get("feiertage"):
            try:
                ft = feiertage_html(ha, hacfg["feiertage"], hacfg.get("feiertage_tage", 7))
                if ft:
                    kasten("Feiertage", ft)
            except Exception as e:
                log.warning("Feiertage nicht lesbar: %s", e)
    else:
        log.warning("Home Assistant nicht konfiguriert – Wetter/Termine fehlen")

    kapitel, uebersicht, gesehen = [], [], set()
    for i, r in enumerate(cfg.get("rubrik", [])):
        if r.get("typ") == "hessenschau_termine":
            artikel = termine_sammeln(r)
        else:
            artikel = rubrik_sammeln(r, gesehen, max_w)
        if not artikel:
            continue
        datei = f"r{i:02d}.xhtml"
        teile = [f"<h2 class='rubrik'>{esc(r['name'])}</h2>"]
        for n, a in enumerate(artikel):
            klasse = "artikel letzter" if n == len(artikel) - 1 else "artikel"
            teile.append(f"<div class='{klasse}'><h3>{esc(a['titel'])}</h3>"
                         f"<p class='meta'>{esc(a['quelle'])} · {a['zeit']:%d.%m., %H:%M} Uhr</p>"
                         f"{a['body']}</div>")
        kapitel.append((datei, r["name"], "".join(teile)))
        uebersicht.append(f"<p class='zeile'><a href='{datei}'><b>{esc(r['name'])}</b></a> "
                          f"· {len(artikel)} Artikel</p>")

    kasten("In dieser Ausgabe", "<div class='inhalt'>" + "".join(uebersicht) + "</div>")
    if kapitel:
        d, t, inhalt = kapitel[-1]
        kapitel[-1] = (d, t, inhalt + "<p class='ende'>— ENDE DER AUSGABE —</p>")
    kapitel.insert(0, ("titel.xhtml", "Titelseite", "".join(titelseite)))

    ordner = Path(z["ausgabe_ordner"])
    ordner.mkdir(parents=True, exist_ok=True)
    pfad = ordner / f"Morgenzeitung_{heute:%Y-%m-%d}.epub"
    epub_bauen(pfad, z["titel"], heute, kapitel)
    log.info("EPUB erstellt: %s (%d KB)", pfad, pfad.stat().st_size // 1024)

    tage = z.get("behalten_tage", 14)
    for alt in ordner.glob("Morgenzeitung_*.epub"):
        if dt.datetime.fromtimestamp(alt.stat().st_mtime, TZ) < heute - dt.timedelta(days=tage):
            alt.unlink()
    return pfad


def senden(cfg, pfad):
    m = cfg["mail"]
    msg = EmailMessage()
    msg["From"] = m["absender"]
    msg["To"] = m["kindle_adresse"]
    msg["Subject"] = pfad.stem
    msg.set_content("Morgenzeitung")
    msg.add_attachment(pfad.read_bytes(), maintype="application", subtype="epub+zip",
                       filename=pfad.name)
    with smtplib.SMTP(m["smtp_server"], m["smtp_port"], timeout=60) as s:
        s.starttls()
        s.login(m["absender"], m["app_passwort"].replace(" ", ""))
        s.send_message(msg)
    log.info("An %s gesendet", m["kindle_adresse"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(Path(__file__).with_name("config.toml")))
    ap.add_argument("--nosend", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    with open(args.config, "rb") as f:
        cfg = tomllib.load(f)
    pfad = zeitung_bauen(cfg)
    if not args.nosend:
        senden(cfg, pfad)


if __name__ == "__main__":
    sys.exit(main())
