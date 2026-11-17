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


# ---------------------------------------------------------------- Anime (AniList)
ANILIST = "https://graphql.anilist.co"
ANILIST_LISTE = """query ($name: String) {
  MediaListCollection(userName: $name, type: ANIME, status_in: [CURRENT, REPEATING]) {
    lists { entries { media { id title { romaji english } } } }
  }
}"""
ANILIST_TERMINE = """query ($ids: [Int], $von: Int, $bis: Int) {
  Page(perPage: 50) {
    airingSchedules(mediaId_in: $ids, airingAt_greater: $von, airingAt_lesser: $bis, sort: TIME) {
      airingAt episode mediaId
    }
  }
}"""


def anilist(query, variablen):
    r = requests.post(ANILIST, json={"query": query, "variables": variablen},
                      headers={"User-Agent": UA}, timeout=20)
    daten = r.json()
    if daten.get("errors"):
        raise RuntimeError("; ".join(e.get("message", "?") for e in daten["errors"]))
    r.raise_for_status()
    return daten["data"]


def anime_html(cfg):
    """Neue Folgen von heute für alle Anime, die auf AniList als 'Watching' stehen. None wenn keine."""
    nutzer = cfg.get("anilist_nutzer", "")
    if not nutzer or nutzer.startswith("DEIN"):
        return None
    liste = anilist(ANILIST_LISTE, {"name": nutzer})["MediaListCollection"]
    titel = {}
    for l in (liste or {}).get("lists") or []:
        for e in l.get("entries") or []:
            t = e["media"]["title"]
            titel[e["media"]["id"]] = (t.get("english") if cfg.get("titel") == "english" else None) \
                or t.get("romaji") or t.get("english") or "?"
    if not titel:
        log.info("Anime: keine Serien auf 'Watching'")
        return None
    jetzt = dt.datetime.now(TZ)
    start = jetzt.replace(hour=0, minute=0, second=0, microsecond=0)
    ende = start + dt.timedelta(days=1)
    folgen = anilist(ANILIST_TERMINE, {"ids": list(titel), "von": int(start.timestamp()) - 1,
                                       "bis": int(ende.timestamp())})["Page"]["airingSchedules"]
    log.info("Anime: %d Serien, %d neue Folgen heute", len(titel), len(folgen))
    if not folgen:
        return None
    out = []
    for f in folgen:
        zeit = dt.datetime.fromtimestamp(f["airingAt"], TZ)
        wann = "schon da" if zeit <= jetzt else zeit.strftime("%H:%M")
        out.append(f"<p class='zeile'><b>{wann}</b> {esc(titel.get(f['mediaId'], '?'))} "
                   f"<i>(Folge {f['episode']})</i></p>")
    return "".join(out)


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


# ---------------------------------------------------------------- Themen & Verlauf
STOPPWOERTER = set("""
aber alle allem allen aller alles also auch auf aus bei beim bereits bis bisher dabei damit dann
darauf darum dass dazu dem den denen denn der deren des dessen die dies diese diesem diesen dieser
dieses doch dort durch eine einem einen einer eines einmal etwa euch fast für gegen hatte hatten
hier hinter immer ihre ihrem ihren ihrer jetzt kann kein keine können könnte machen mehr mehrere
mich mit nach neue neuen neuer neues nicht noch nun nur oder ohne schon sehr seid sein seine seinem
seinen seiner seit sich sie sind soll sollen sowie über um und uns unter viel viele vom von vor
wann war waren warum was weil weiter weitere wenn werden wieder wird wurde wurden zum zur zwei
heute gestern morgen woche jahr jahre jahren uhr prozent laut sagt sagte erste ersten neu
about after again also been before being between both could each from have here into just like
make more most much must only other over said same should some such than that their them then
there these they this those through under very week what when where which while will with would
your year years today first news update updates report reports says
januar februar märz april juni juli august september oktober november dezember montag dienstag
mittwoch donnerstag freitag samstag sonntag spiel spiele game games kommen kommt gibt startet
stellt bringt zeigt plant diese dieses stadt kreis landkreis gemeinde region
""".split())


def themen_woerter(text):
    """Bedeutungstragende Wörter eines Textes, grob auf den Wortstamm gekürzt."""
    woerter = set()
    text = text or ""
    # Abkürzungen (GDL, RTX, DEL, KI) zählen trotz Kürze mit
    abk = {w.lower() for w in re.findall(r"\b[A-ZÄÖÜ]{2,5}\b", text)}
    for w in re.findall(r"[a-zäöüß0-9]+", text.lower()):
        if w in abk and w not in STOPPWOERTER:
            woerter.add(w)
            continue
        if w.isdigit():
            if len(w) >= 2:
                woerter.add(w)  # Versionsnummern, Jahreszahlen
            continue
        if len(w) < 4 or w in STOPPWOERTER:
            continue
        for endung in ("ungen", "ung", "ern", "en", "er", "es", "e", "s", "n"):
            if len(w) > 6 and w.endswith(endung):
                w = w[: -len(endung)]
                break
        woerter.add(w)
    return woerter


def _ueberlappung(a, b):
    if min(len(a), len(b)) < 3:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _jaccard(a, b):
    if len(a) < 10 or len(b) < 10:
        return 0.0
    return len(a & b) / len(a | b)


def gleiches_thema(a, b):
    """a, b: dict mit 'titel' (Wortmenge) und 'anfang' (Wortmenge der ersten ~80 Wörter)."""
    ta, tb = a["titel"], b["titel"]
    if min(len(ta), len(tb)) == 2 and len(ta & tb) == 2:
        return True  # kurzer Titel steckt komplett im anderen
    ov = _ueberlappung(ta, tb)
    if ov >= 0.6 or (ov >= 0.5 and min(len(a["titel"]), len(b["titel"])) >= 4):
        return True
    lj = _jaccard(a.get("anfang", set()), b.get("anfang", set()))
    return lj >= 0.3 or (ov >= 0.4 and lj >= 0.18)


class Verlauf:
    """Merkt sich, welche Berichte (und Themen) schon in einer verschickten Ausgabe standen."""

    def __init__(self, pfad, tage=21):
        self.pfad = Path(pfad) if pfad else None
        self.eintraege = []
        self.neu = []
        if self.pfad and self.pfad.exists():
            try:
                daten = json.loads(self.pfad.read_text(encoding="utf-8"))
                grenze = (dt.date.today() - dt.timedelta(days=tage)).isoformat()
                self.eintraege = [e for e in daten if e.get("datum", "") >= grenze]
            except Exception as ex:
                log.warning("Verlauf nicht lesbar, starte leer: %s", ex)
        self.urls = {e.get("url") for e in self.eintraege if e.get("url")}
        self.keys = {e.get("key") for e in self.eintraege}
        self.themen = [{"titel": set(e.get("titel", [])), "anfang": set(e.get("anfang", []))}
                       for e in self.eintraege]

    def schon_gezeigt(self, key, url):
        return key in self.keys or (url and url in self.urls)

    def thema_bekannt(self, thema):
        return any(gleiches_thema(thema, t) for t in self.themen)

    def merken(self, key, url, thema):
        self.neu.append({"datum": dt.date.today().isoformat(), "key": key, "url": url,
                         "titel": sorted(thema["titel"]), "anfang": sorted(thema.get("anfang", set()))})

    def speichern(self):
        if not self.pfad or not self.neu:
            return
        self.pfad.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.pfad.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.eintraege + self.neu, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.pfad)
        log.info("Verlauf: %d neue Berichte gemerkt (%d insgesamt)", len(self.neu),
                 len(self.eintraege) + len(self.neu))


def wort_re(woerter):
    return re.compile(r"\b(" + "|".join(re.escape(w) for w in woerter) + r")\b", re.I) if woerter else None


# ---------------------------------------------------------------- Volltext
# Hinweise, dass der Artikel hinter einer Bezahlschranke steckt
PAYWALL_TEXT = re.compile(
    r"lorem ipsum|consetetur sadipscing|um alles zu lesen|um den (ganzen|vollständigen) artikel zu lesen|"
    r"um weiterzulesen|jetzt weiterlesen mit|weiterlesen mit (plus|abo|\w+\+)|artikel freischalten|"
    r"exklusiv für abonnent|nur für abonnent|für abonnenten verfügbar|mit einem abo|"
    r"jetzt abo abschließen|zugang zu allen (artikeln|inhalten)|sie sind bereits abonnent|"
    r"bereits abonnent\?|plus-artikel|dieser artikel ist exklusiv|"
    r"subscribe to (continue|read|keep reading)|to continue reading|this article is for subscribers|"
    r"pay to read|subscribers only|already a subscriber", re.I)
PAYWALL_HTML = re.compile(r'"isAccessibleForFree"\s*:\s*"?false', re.I)
# Bildunterschriften, Bildnachweise und ähnliches vor dem Extrahieren entfernen
BILD_KLASSEN = re.compile(r"caption|credit|copyright|bildunterschrift|bildnachweis|image-?source|"
                          r"media-?description|photo-?info", re.I)
BILDNACHWEIS = re.compile(r"^(foto|fotos|bild|bilder|quelle|grafik|video|image|photo|credit)s?\s*[:|/©]|^©|"
                          r"\((foto|bild)\s*:", re.I)


class Paywall(Exception):
    pass


def html_saeubern(roh):
    """Entfernt Abbildungen samt Bildunterschriften, bevor trafilatura den Text herauszieht."""
    try:
        from lxml import html as lhtml
        baum = lhtml.fromstring(roh)
        for el in baum.xpath("//figure|//figcaption|//picture|//img|//video|//aside"):
            el.drop_tree()
        for el in baum.xpath("//*[@class or @id]"):
            if BILD_KLASSEN.search((el.get("class") or "") + " " + (el.get("id") or "")):
                el.drop_tree()
        return lhtml.tostring(baum, encoding="unicode")
    except Exception as ex:
        log.info("HTML-Säuberung übersprungen: %s", ex)
        return roh


def volltext(url, max_woerter):
    """Gibt {'html', 'woerter', 'anfang'} zurück, None wenn kein brauchbarer Text,
    oder wirft Paywall, wenn der Artikel hinter einer Bezahlschranke steckt."""
    if not trafilatura or not url:
        return None
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
        r.raise_for_status()
        roh = r.content
    except Exception as e:
        log.info("Volltext fehlgeschlagen (%s): %s", url, e)
        return None
    kopf = roh[:400000].decode("utf-8", "ignore")
    if PAYWALL_HTML.search(kopf):
        raise Paywall(url)
    try:
        # Bytes an trafilatura geben (Kodierung aus der Seite); Bildunterschriften vorher entfernen
        sauber = html_saeubern(roh)
        txt = trafilatura.extract(sauber, output_format="markdown", include_formatting=True,
                                  include_comments=False, include_tables=False, include_images=False,
                                  include_links=False, favor_precision=True)
    except Exception as e:
        log.info("Volltext fehlgeschlagen (%s): %s", url, e)
        return None
    if not txt:
        return None
    if PAYWALL_TEXT.search(txt):
        raise Paywall(url)

    # Absätze aufbereiten: Bildnachweise raus, inhaltliche Doppelungen raus
    bloecke, gesehen = [], []
    for zeile in txt.split("\n"):
        z = zeile.strip()
        if not z:
            continue
        z = re.sub(r"\*\*|__", "", z)
        if z.startswith("#"):
            bloecke.append(("h", z.lstrip("#").strip()))
            continue
        liste = z.startswith(("- ", "* ", "• "))
        if liste:
            z = z[2:].strip()
        z = re.sub(r"(?<!\w)[*_](?!\s)(.+?)(?<!\s)[*_](?!\w)", r"\1", z)
        if BILDNACHWEIS.search(z) or (len(z.split()) < 5 and not liste and not z.endswith((".", "!", "?", ":"))):
            continue
        w = themen_woerter(z)
        if len(w) >= 4 and any(len(w & g) / len(w | g) >= 0.6 for g in gesehen):
            continue  # inhaltlich fast gleich wie ein früherer Absatz (typisch: Bildunterschrift)
        gesehen.append(w)
        bloecke.append(("li" if liste else "p", z))

    gesamt = sum(len(t.split()) for art, t in bloecke if art != "h")
    if gesamt < 60:
        return None

    teile, woerter, offen_liste, erster = [], 0, False, True
    for art, t in bloecke:
        if art != "li" and offen_liste:
            teile.append("</ul>")
            offen_liste = False
        n = len(t.split())
        if art != "h" and woerter + n > max_woerter:
            rest = max(0, max_woerter - woerter)
            if rest > 15:
                teile.append(f"<p>{esc(' '.join(t.split()[:rest]))} …</p>")
            teile.append("<p class='gekuerzt'>(gekürzt)</p>")
            break
        if art == "h":
            if woerter:  # Zwischenüberschrift nur innerhalb des Textes, nicht als Doppel-Titel
                teile.append(f"<h4>{esc(t)}</h4>")
            continue
        if art == "li":
            if not offen_liste:
                teile.append("<ul>")
                offen_liste = True
            teile.append(f"<li>{esc(t)}</li>")
        else:
            teile.append(f"<p class='lead'>{esc(t)}</p>" if erster else f"<p>{esc(t)}</p>")
            erster = False
        woerter += n
    if offen_liste:
        teile.append("</ul>")
    klartext = " ".join(t for art, t in bloecke if art != "h")
    return {"html": "".join(teile), "woerter": gesamt,
            "anfang": themen_woerter(" ".join(klartext.split()[:80]))}


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


def rubrik_sammeln(r, gesehen, max_woerter, verlauf, ausgabe):
    """gesehen: Titel-Schlüssel dieser Ausgabe; verlauf: frühere Ausgaben;
    ausgabe: Themen dieser Ausgabe (für Themen-Duplikate über Rubriken hinweg)."""
    grenze = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=r.get("max_alter_stunden", 48))
    max_woerter = r.get("max_woerter", max_woerter)
    nur_mit_text = r.get("nur_mit_text", False)
    stichwort_re = wort_re(r.get("stichwoerter"))
    ausschluss_re = wort_re(r.get("ausschluss"))
    # Feeds abwechselnd durchgehen, damit jede Quelle zum Zug kommt
    listen = [feed_laden(u, grenze) for u in r["feeds"]]
    kandidaten = []
    for i in range(max((len(l) for l in listen), default=0)):
        kandidaten += [l[i] for l in listen if i < len(l)]

    artikel, versuche, gruende = [], 0, {}
    # Wörter, die in fast jedem Titel der Rubrik stehen ("Rocket League"), zählen beim Themenvergleich nicht
    rubrik_woerter = themen_woerter(r["name"] + " " + " ".join(r.get("themen_ignorieren", [])))

    def weg(grund):
        gruende[grund] = gruende.get(grund, 0) + 1

    for zeit, e, feedtitel in kandidaten:
        if len(artikel) >= r.get("anzahl", 5) or versuche >= r.get("anzahl", 5) * 4:
            break
        key = titel_norm(e.get("title"))
        link = e.get("link", "")
        if not key or key in gesehen:
            continue
        if verlauf.schon_gezeigt(key, link):
            weg("schon gelesen")
            continue
        titel = html.unescape(e.get("title", "")).strip()
        quelle = (e.get("source") or {}).get("title") or feedtitel
        if quelle and titel.endswith(" - " + quelle):
            titel = titel[: -len(quelle) - 3]
        kurztext = text_kurz(e.get("summary") or e.get("description"), 120)
        if ausschluss_re and ausschluss_re.search(titel + " " + kurztext):
            weg("Ausschlusswort")
            continue
        thema = {"titel": themen_woerter(titel) - rubrik_woerter, "anfang": set()}
        if verlauf.thema_bekannt(thema) or any(gleiches_thema(thema, t) for t in ausgabe):
            weg("Thema schon da")
            continue

        body, woerter, echt = None, 0, link
        if r.get("volltext"):
            versuche += 1
            echt = echter_link(link) or link
            if echt != link and verlauf.schon_gezeigt(key, echt):
                weg("schon gelesen")
                continue
            try:
                vt = volltext(echt, max_woerter)
            except Paywall:
                log.info("Bezahlschranke, übersprungen: %s", echt)
                weg("Bezahlschranke")
                continue
            if vt:
                body, woerter, thema["anfang"] = vt["html"], vt["woerter"], vt["anfang"]
        if not body:
            if kurztext and titel_norm(kurztext) != key and not kurztext.startswith(titel[:40]):
                body = f"<p class='lead'>{esc(kurztext)}</p>"
                woerter = len(kurztext.split())
                thema["anfang"] = themen_woerter(kurztext)
            else:
                body = ""
        if nur_mit_text and woerter < 25:
            weg("kein lesbarer Text")
            continue  # nur Überschrift -> überspringen, nächsten Artikel nehmen
        volltext_klar = re.sub(r"<[^>]+>", " ", body)
        if stichwort_re and not stichwort_re.search(titel + " " + kurztext + " " + volltext_klar):
            weg("kein Stichwort")
            continue
        if ausschluss_re and ausschluss_re.search(volltext_klar):
            weg("Ausschlusswort")
            continue
        if thema["anfang"] and (verlauf.thema_bekannt(thema) or any(gleiches_thema(thema, t) for t in ausgabe)):
            weg("Thema schon da")
            continue
        gesehen.add(key)
        ausgabe.append(thema)
        verlauf.merken(key, echt, thema)
        if echt != link:
            verlauf.merken(key, link, thema)
        artikel.append({
            "titel": titel,
            "quelle": quelle,
            "zeit": zeit.astimezone(TZ),
            "body": body,
            "lesezeit": max(1, round(woerter / 200)) if woerter >= 150 else 0,
        })
    weg_text = ", ".join(f"{n}× {g}" for g, n in gruende.items())
    log.info("Rubrik %s: %d Artikel%s", r["name"], len(artikel), f" (aussortiert: {weg_text})" if weg_text else "")
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


def termine_sammeln(r, verlauf):
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
    if verlauf.schon_gezeigt(None, e["link"]):
        log.info("Rubrik %s: Übersicht dieser Woche schon gezeigt", r["name"])
        return []
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
    verlauf.merken("termine:" + e["link"], e["link"], {"titel": set(), "anfang": set()})
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
.lead { font-weight: bold; }
.artikel h4 { font-size: 1em; font-weight: bold; margin: 0.8em 0 0.3em; }
.artikel ul { margin: 0 0 0.6em 1.2em; padding: 0; }
.artikel li { margin: 0 0 0.25em; text-align: left; }
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

    if cfg.get("anime"):
        try:
            an = anime_html(cfg["anime"])
            if an:
                kasten("Anime – neue Folgen heute", an)
        except Exception as e:
            log.warning("Anime (AniList) nicht verfügbar: %s", e)

    verlauf = Verlauf(z.get("verlauf_datei"), z.get("verlauf_tage", 21))
    kapitel, uebersicht, gesehen, ausgabe = [], [], set(), []
    for i, r in enumerate(cfg.get("rubrik", [])):
        if r.get("typ") == "hessenschau_termine":
            artikel = termine_sammeln(r, verlauf)
        else:
            artikel = rubrik_sammeln(r, gesehen, max_w, verlauf, ausgabe)
        if not artikel:
            continue
        datei = f"r{i:02d}.xhtml"
        teile = [f"<h2 class='rubrik'>{esc(r['name'])}</h2>"]
        for n, a in enumerate(artikel):
            klasse = "artikel letzter" if n == len(artikel) - 1 else "artikel"
            teile.append(f"<div class='{klasse}'><h3>{esc(a['titel'])}</h3>"
                         f"<p class='meta'>{esc(a['quelle'])} · {a['zeit']:%d.%m., %H:%M} Uhr"
                         f"{' · ca. ' + str(a['lesezeit']) + ' Min.' if a.get('lesezeit') else ''}</p>"
                         f"{a['body']}</div>")
        kapitel.append((datei, r["name"], "".join(teile)))
        uebersicht.append(f"<p class='zeile'><a href='{datei}'><b>{esc(r['name'])}</b></a></p>")

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
    return pfad, verlauf


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


def zusammenfuehren(basis, oben):
    """Werte aus 'oben' (geheim.toml) über 'basis' (config.toml) legen, Tabellen rekursiv."""
    for k, v in oben.items():
        if isinstance(v, dict) and isinstance(basis.get(k), dict):
            zusammenfuehren(basis[k], v)
        else:
            basis[k] = v
    return basis


def config_laden(config_pfad, geheim_pfad=None):
    with open(config_pfad, "rb") as f:
        cfg = tomllib.load(f)
    if geheim_pfad:
        kandidaten = [Path(geheim_pfad)]
    else:
        hier = Path(__file__).resolve().parent
        kandidaten = [hier / "geheim.toml", hier.parent / "geheim.toml"]
    for k in kandidaten:
        if k.is_file():
            with open(k, "rb") as f:
                zusammenfuehren(cfg, tomllib.load(f))
            log.info("Zugangsdaten aus %s geladen", k)
            break
    return cfg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(Path(__file__).with_name("config.toml")))
    ap.add_argument("--nosend", action="store_true")
    ap.add_argument("--geheim", help="Datei mit Passwörtern/Token (Standard: geheim.toml neben oder über dem Skript)")
    ap.add_argument("--merken", action="store_true", help="mit --nosend: Berichte trotzdem als gelesen merken")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = config_laden(Path(args.config), args.geheim)
    pfad, verlauf = zeitung_bauen(cfg)
    if not args.nosend:
        senden(cfg, pfad)
        verlauf.speichern()  # erst nach erfolgreichem Versand merken
    elif args.merken:
        verlauf.speichern()


if __name__ == "__main__":
    sys.exit(main())
