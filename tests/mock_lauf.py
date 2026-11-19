"""Kompletter Probelauf ohne Netz: nachgebaute Feeds, Home Assistant und Artikelseiten.

Simuliert zwei Tage hintereinander, um den Verlauf zu prüfen:
  Tag 1: jede Quelle liefert 6 Meldungen
  Tag 2: dieselben 6 Meldungen + 2 neue  -> nur die neuen dürfen erscheinen
Aufruf: python3 tests/mock_lauf.py   (EPUBs landen in tests/out/)
"""
import datetime as dt
import logging
import pathlib
import random
import shutil
import sys
import tomllib

HIER = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HIER.parent))
import morgenzeitung as m  # noqa: E402

random.seed(1)
# großer Wortschatz, damit sich unabhängige Meldungen nicht zufällig "ähneln"
WOERTER = ["".join(random.choice("bcdfghklmnprstvwz") + random.choice("aeiou") for _ in range(4))
           for _ in range(3000)]
_ALT = ("Brücke Kläranlage Ausschreibung Stromnetz Grafikkarte Prozessor Schulhof Turnhalle Sportplatz "
           "Firmware Datenleck Rechenzentrum Kühlung Glasfaser Bahnhof Klinik Feuerwehr Sporthalle "
           "Konsole Patch Turnier Saison Arena Haushalt Bebauungsplan Kita Parkhaus Ladesäule Solaranlage "
           "Windpark Wasserstoff Speicher Router Sicherheitslücke Update Weihnachtsmarkt Bücherei Museum").split()
jetzt = dt.datetime.now(dt.timezone.utc)
TITEL = {}


def titel_fuer(quelle, i):
    if (quelle, i) not in TITEL:
        TITEL[(quelle, i)] = " ".join(random.sample(WOERTER, 4)) + f" {quelle[-3:]}{i}"
    return TITEL[(quelle, i)]


def rss(quelle, nummern):
    items = "".join(
        f"<item><title>{titel_fuer(quelle, i)} - Quelle{i}</title><link>https://example.org/{abs(hash(quelle))}/{i}</link>"
        f"<pubDate>{(jetzt - dt.timedelta(hours=i % 20)).strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate>"
        f"<description>Kurzer Anriss zur Meldung {i}</description><source url='x'>Quelle{i}</source></item>"
        for i in nummern)
    return f"<?xml version='1.0'?><rss version='2.0'><channel><title>Feed</title>{items}</channel></rss>".encode()


def artikel(url):
    w = random.sample(WOERTER, 12)
    absatz = lambda k: " ".join(random.sample(WOERTER, 10)) + f" wurde heute vorgestellt und ausführlich erklärt ({k})."
    return ("<html><head><meta charset='utf-8'></head><body><article><h1>Titel</h1>"
            f"<p>{' '.join(w)} sind die Themen dieses Berichts, der {url[-6:]} betrifft und viele Details enthält.</p>"
            f"<figure><img src='x.jpg'><figcaption>{' '.join(w[:6])} auf dem Bild. Foto: Agentur</figcaption></figure>"
            + "".join(f"<p>{absatz(k)} {absatz(k + 100)}</p>" for k in range(6))
            + "<h2>Hintergrund</h2>" + "".join(f"<p>{absatz(k + 200)} {absatz(k + 300)}</p>" for k in range(4))
            + "</article></body></html>")


class R:
    def __init__(self, content=b"", js=None):
        self.content, self._js = content, js
        self.text = content.decode("utf-8", "ignore") if content else ""

    def raise_for_status(self):
        pass

    def json(self):
        return self._js


TAG = {"nr": 1}


def get(url, headers=None, params=None, timeout=None):
    if "/api/states/" in url:
        return R(js={"state": "partlycloudy", "attributes": {"temperature": 7.5, "temperature_unit": "°C",
                                                             "humidity": 80, "wind_speed": 12, "wind_speed_unit": "km/h"}})
    if "/api/calendars/calendar.claude" in url:
        return R(js=[{"summary": "Zahnarzt", "location": "Mörfelden",
                      "start": {"dateTime": dt.datetime.now(m.TZ).replace(hour=15, minute=0).isoformat()}}])
    if "/api/calendars/calendar.abfall" in url:
        return R(js=[{"summary": "Gelbe Tonne", "start": {"date": (dt.date.today() + dt.timedelta(days=1)).isoformat()}}])
    if "app_deutschland_he" in url:
        return R(js=[{"summary": "Erster Weihnachtstag", "start": {"date": (dt.date.today() + dt.timedelta(days=5)).isoformat()}}])
    if "/api/calendars/" in url:
        return R(js=[])
    if "example.org" in url:
        return R(content=artikel(url).encode())
    if "hessenschau.de/freizeit" in url:
        return R(content=b"<?xml version='1.0'?><rss version='2.0'><channel><title>hs</title></channel></rss>")
    nummern = range(6) if TAG["nr"] == 1 else range(8)
    return R(content=rss(url, nummern))


def post(url, headers=None, json=None, timeout=None):
    if "anilist" in url:
        if "MediaListCollection" in json["query"]:
            return R(js={"data": {"MediaListCollection": {"lists": [{"entries": [
                {"media": {"id": 1, "title": {"romaji": "Sousou no Frieren", "english": "Frieren"}}},
                {"media": {"id": 2, "title": {"romaji": "Kusuriya no Hitorigoto", "english": None}}}]}]}}})
        t0 = json["variables"]["bis"] - 86400
        return R(js={"data": {"Page": {"airingSchedules": [{"airingAt": t0 + 3 * 3600, "episode": 7, "mediaId": 2},
                                                          {"airingAt": t0 + 17 * 3600 + 1800, "episode": 12, "mediaId": 1}]}}})
    fc = [{"datetime": (jetzt + dt.timedelta(days=d)).isoformat(), "condition": "rainy", "temperature": 9,
           "templow": 3, "precipitation_probability": 60} for d in range(5)]
    return R(js={"service_response": {"weather.forecast_home": {"forecast": fc}}})


m.requests.get, m.requests.post = get, post
m.google_link_aufloesen = lambda u: u.replace("news.google.com", "example.org")

out = HIER / "out"
shutil.rmtree(out, ignore_errors=True)
cfg = tomllib.load(open(HIER.parent / "config.toml", "rb"))
cfg["zeitung"]["ausgabe_ordner"] = str(out)
cfg["zeitung"]["verlauf_datei"] = str(out / "verlauf.json")
cfg["homeassistant"]["token"] = "test"
if "anime" in cfg:
    cfg["anime"]["anilist_nutzer"] = "test"
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logging.getLogger("morgenzeitung").setLevel(logging.INFO)

for tag in (1, 2):
    TAG["nr"] = tag
    print(f"\n===== Tag {tag} =====")
    pfad, verlauf = m.zeitung_bauen(cfg)
    verlauf.speichern()
    ziel = out / f"Tag{tag}_{pfad.name}"
    pfad.rename(ziel)
    print("EPUB:", ziel)
