"""Offline-Tests für Volltext-Aufbereitung, Bezahlschranken und Themen-Duplikate.
Aufruf: python3 tests/test_funktionen.py   (kein Netz nötig)"""
import pathlib
import re
import json
from pathlib import Path
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import morgenzeitung as m  # noqa: E402

FEHLER = []


def pruefe(bedingung, text):
    print(("OK   " if bedingung else "FEHL ") + text)
    if not bedingung:
        FEHLER.append(text)


class Antwort:
    def __init__(self, html):
        self.content = html.encode("utf-8")
        self.text = html

    def raise_for_status(self):
        pass


def seite(inhalt, kopf=""):
    return (f"<html><head><meta charset='utf-8'>{kopf}<title>T</title></head><body>"
            f"<article><h1>Überschrift des Artikels</h1>{inhalt}</article></body></html>")


ABSATZ = ("Die Stadtverordneten haben am Dienstagabend mit großer Mehrheit beschlossen, dass der neue "
          "Radweg zwischen Mörfelden und Walldorf im kommenden Frühjahr gebaut wird. Die Kosten liegen "
          "bei rund zwei Millionen Euro, der Großteil kommt aus Fördermitteln des Landes.")


def mit_seite(html):
    m.requests.get = lambda *a, **k: Antwort(html)


# 1) Bildunterschriften verschwinden, Zwischenüberschrift und Liste bleiben
mit_seite(seite(
    f"<p>{ABSATZ}</p>"
    "<figure><img src='a.jpg'><figcaption>Der neue Radweg soll Mörfelden und Walldorf verbinden. "
    "Foto: Stadt</figcaption></figure>"
    f"<p>Bürgermeister Thomas Winkler sprach von einem wichtigen Schritt für die Verkehrswende in der "
    f"Doppelstadt. Anwohner hatten sich seit Jahren für eine sichere Verbindung eingesetzt.</p>"
    "<div class='image-caption'>Bildunterschrift: Anwohner bei der Sitzung</div>"
    "<h2>Wie geht es weiter?</h2>"
    "<p>Die Ausschreibung soll noch im Dezember starten. Geplant sind folgende Schritte:</p>"
    "<ul><li>Ausschreibung im Dezember</li><li>Baubeginn im März</li><li>Freigabe im Herbst</li></ul>"
    "<p>Der Radweg wird mit einer Beleuchtung ausgestattet, die nachts gedimmt wird, um Insekten zu schützen. "
    "Außerdem entstehen an beiden Enden neue Fahrradabstellanlagen mit Lademöglichkeiten.</p>"))
r = m.volltext("https://example.org/a", 400)
pruefe(r is not None, "Normaler Artikel liefert Text")
pruefe("Foto: Stadt" not in r["html"] and "verbinden" not in r["html"].split("Bürgermeister")[0][-200:],
       "Bildunterschrift entfernt")
pruefe("Bildunterschrift" not in r["html"], "Bildunterschrift per CSS-Klasse entfernt")
pruefe("<h4>Wie geht es weiter?</h4>" in r["html"], "Zwischenüberschrift als <h4>")
pruefe("<ul><li>Ausschreibung im Dezember</li>" in r["html"], "Aufzählung als Liste")
pruefe(r["html"].startswith("<p class='lead'>"), "Erster Absatz als Vorspann hervorgehoben")

# 2) Doppelte Absätze (Bildunterschrift als normaler Absatz wiederholt) fliegen raus
mit_seite(seite(f"<p>{ABSATZ}</p><p>{ABSATZ.replace('am Dienstagabend ', '')}</p>"
                "<p>Weitere Informationen gibt es auf der Internetseite der Stadt, wo auch die Pläne für den "
                "neuen Radweg und die Beleuchtung im Detail einsehbar sind und kommentiert werden können.</p>"))
r = m.volltext("https://example.org/b", 400)
pruefe(r is not None and r["html"].count("Stadtverordneten") == 1, "Fast gleicher Absatz nur einmal")

# 3) Bezahlschranken
for name, html in [
    ("Text 'um alles zu lesen'", seite(f"<p>{ABSATZ}</p><p>Bezahlen Sie jetzt, um alles zu lesen.</p>")),
    ("Platzhaltertext Lorem ipsum", seite(f"<p>{ABSATZ}</p><p>Lorem ipsum dolor sit amet, consetetur sadipscing elitr.</p>")),
    ("JSON-LD isAccessibleForFree", seite(f"<p>{ABSATZ}</p>" * 3,
                                          kopf='<script type="application/ld+json">{"isAccessibleForFree": "False"}</script>')),
    ("Englisch 'subscribe to continue'", seite(f"<p>{ABSATZ}</p><p>Subscribe to continue reading.</p>")),
]:
    mit_seite(html)
    try:
        m.volltext("https://example.org/p", 400)
        pruefe(False, f"Bezahlschranke erkannt: {name}")
    except m.Paywall:
        pruefe(True, f"Bezahlschranke erkannt: {name}")

# 4) Themenvergleich
def th(t, rub=""):
    return {"titel": m.themen_woerter(t) - m.themen_woerter(rub), "anfang": set()}


for a, b, rub, erwartet in [
    ("Nvidia stellt neue RTX 5080 Super vor", "RTX 5080 Super: Nvidia präsentiert neue Grafikkarte", "", True),
    ("Bahnstreik: GDL kündigt Warnstreik für Donnerstag an",
     "GDL ruft zum Warnstreik am Donnerstag auf – Bahnverkehr betroffen", "", True),
    ("Rocket League Season 21 startet mit neuer Arena", "Rocket League: Patch-Notes für Version 2.60", "Rocket League", False),
    ("Rocket League Season 21 startet mit neuer Arena",
     "Rocket League Season 21: Neue Arena und Battle Pass ab heute", "Rocket League", True),
    ("Home Assistant 2026.12 ist da", "Home Assistant 2026.12 Release: Das ist neu", "Smart Home", True),
    ("Xbox Game Pass: Diese Spiele kommen im Dezember", "PlayStation Plus: Die Spiele im Dezember", "Gaming", False),
    ("Stadt Mörfelden-Walldorf plant neuen Radweg", "Mörfelden-Walldorf: Weihnachtsmarkt startet am Freitag",
     "Lokales Mörfelden Walldorf Mörfelden-Walldorf", False),
    ("Weihnachtsmarkt in Mörfelden öffnet am Freitag", "Mörfelden: Weihnachtsmarkt auf dem Dalles öffnet",
     "Lokales Mörfelden Walldorf Mörfelden-Walldorf", True),
]:
    pruefe(m.gleiches_thema(th(a, rub), th(b, rub)) == erwartet,
           f"{'gleiches' if erwartet else 'anderes'} Thema: {a!r} / {b!r}")

# 5) Game Pass: Spieleliste aus News-Artikeln (verschiedene Schreibweisen)
md = """# Xbox Game Pass loses eight more games at the end of November
Xbox has now revealed the second wave of games leaving Xbox Game Pass.
## Games leaving Xbox Game Pass and PC Game Pass on November 30
- [Aliens: Dark Descent](https://example.org/aliens) (Cloud, Console, PC)
- **Moving Out 2** (Cloud, Console, PC)
- DreamWorks Gabby's Dollhouse: Ready to Party™
## New games joining Game Pass
- High on Life 2
"""
sp = m.abgang_spiele(md)
pruefe(sp == [("Aliens: Dark Descent", "November 30"), ("Moving Out 2", "November 30"),
              ("DreamWorks Gabby's Dollhouse: Ready to Party", "November 30")], f"Game-Pass-Liste: {sp}")
md2 = """Here's what's leaving.
**Leaving November 30**
- November 30: Star Trucker (Cloud, Console, and PC)
- Generation Zero – Cloud, Console and PC
"""
sp2 = m.abgang_spiele(md2)
pruefe([x[0] for x in sp2] == ["Star Trucker", "Generation Zero"] and sp2[0][1] == "November 30", f"Game-Pass-Liste 2: {sp2}")
pruefe(bool(m.ABGANG_TITEL.search("Xbox Game Pass loses eight more games at the end of November")), "Titel erkannt")
pruefe(not m.ABGANG_TITEL.search("Xbox Game Pass confirms second wave of new games for November"), "Zugangs-Titel ignoriert")

pruefe(m.spiel_key("DreamWorks Gabby’s Dollhouse: Ready to Party") == m.spiel_key("Dreamworks Gabby's Dollhouse - Ready To Party"),
       "Spielname-Vergleich (Apostroph/Groß/Satzzeichen)")
import datetime as _dt
pruefe(m.abgangsdatum("November 30", _dt.date(2026, 11, 19)) == _dt.date(2026, 11, 30), "Abgangsdatum dieses Jahr")
pruefe(m.abgangsdatum("January 15", _dt.date(2026, 12, 20)) == _dt.date(2027, 1, 15), "Abgangsdatum nächstes Jahr")

# Game-Pass-Kasten über zwei Tage: zwei Artikel mit demselben Spiel (andere Schreibweise), bleibt bis zum Abgang stehen
import tempfile, types
tmp = Path(tempfile.mkdtemp()) / "gp.json"
artikel_md = {
    "https://a.example/x": "## Games leaving Game Pass on November 30\n- DreamWorks Gabby’s Dollhouse: Ready to Party\n- Star Trucker\n",
    "https://b.example/y": "## Leaving November 30\n- DreamWorks Gabby's Dollhouse: Ready to Party\n- Moving Out 2\n",
}
alt = (m.feed_laden, m.echter_link, m.requests.get, m.trafilatura.extract, m.hltb_stunden)
fe = lambda t, l: (None, {"title": t, "link": l}, "")
m.feed_laden = lambda url, grenze: [fe("Xbox Game Pass loses eight games", "https://a.example/x"),
                                    fe("Eight games leaving Xbox Game Pass", "https://b.example/y")]
m.echter_link = lambda u: u
m.requests.get = lambda url, **k: types.SimpleNamespace(content=url, raise_for_status=lambda: None)
m.trafilatura.extract = lambda inhalt, **k: artikel_md[inhalt]
zeiten = {"Star Trucker": 34.0, "Moving Out 2": 23.4}
aufrufe = []
m.hltb_stunden = lambda n: aufrufe.append(n) or (zeiten.get(n, 2.71), n)
html1 = m.gamepass_html({"feeds": ["f"], "max_stunden": 12, "datei": str(tmp)}, {})
pruefe(html1.count("Gabby") == 1 and "bis 30.11." in html1 and "Star Trucker" not in html1, f"Kasten Tag 1: {html1}")
pruefe(sum("Gabby" in a for a in aufrufe) == 1, "Gabby nur einmal bei HLTB nachgeschlagen")
m.feed_laden = lambda url, grenze: []
html2 = m.gamepass_html({"feeds": ["f"], "max_stunden": 12, "datei": str(tmp)}, {})
pruefe(html2 == html1, "Kasten bleibt am Folgetag stehen")
st = json.loads(tmp.read_text())
st["spiele"][next(iter(st["spiele"]))]["bis"] = "2000-01-01"
tmp.write_text(json.dumps(st))
pruefe(m.gamepass_html({"feeds": ["f"], "max_stunden": 12, "datei": str(tmp)}, {}) is None, "Kasten verschwindet nach Abgang")
m.feed_laden, m.echter_link, m.requests.get, m.trafilatura.extract, m.hltb_stunden = alt

print()
print("Alles in Ordnung." if not FEHLER else f"{len(FEHLER)} Fehler.")
sys.exit(1 if FEHLER else 0)
