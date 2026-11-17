"""Offline-Tests für Volltext-Aufbereitung, Bezahlschranken und Themen-Duplikate.
Aufruf: python3 tests/test_funktionen.py   (kein Netz nötig)"""
import pathlib
import re
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

# 5) Game Pass: Spieleliste aus TA-Artikel und Stunden von der TA-Seite
md = """# Xbox Game Pass loses eight more games at the end of November
Xbox has now revealed the second wave of games leaving Xbox Game Pass.
## Games leaving Xbox Game Pass and PC Game Pass on November 30
- [Aliens: Dark Descent](https://www.trueachievements.com/game/Aliens-Dark-Descent) (Cloud, Console, PC)
- Moving Out 2 (Cloud, Console, PC)
- DreamWorks Gabby's Dollhouse: Ready to Party
- [Totally Reliable Delivery Service](/game/Totally-Reliable-Delivery-Service)
## New games joining Game Pass
- [High on Life 2](/game/High-on-Life-2)
"""
sp = m.abgang_spiele(md)
pruefe([x[1] for x in sp] == ["Aliens-Dark-Descent", "Moving-Out-2", "DreamWorks-Gabbys-Dollhouse-Ready-to-Party",
                              "Totally-Reliable-Delivery-Service"], f"Game-Pass-Liste: {[x[1] for x in sp]}")
pruefe(all(x[2] == "November 30" for x in sp), "Game-Pass-Abgangsdatum")
pruefe(bool(m.ABGANG_TITEL.search("Xbox Game Pass loses eight more games at the end of November")), "Titel erkannt")
pruefe(not m.ABGANG_TITEL.search("Xbox Game Pass confirms second wave of new games for November"), "Zugangs-Titel ignoriert")


class Antw:
    def __init__(self, text, code=200):
        self.text, self.status_code = text, code


seiten = {
    "/game/Moving-Out-2/completiontime": "<p>The estimated time to complete all 52 <b>Moving Out 2</b> achievements is 15-20 hours.</p>",
    "/game/Gabby/completiontime": "",
    "/game/Gabby/achievements": "<meta content='It takes around 1-2 hours to unlock all of the achievements on Xbox Series X|S.'>",
}
alt_get = m.requests.get
m.requests.get = lambda url, **k: Antw(seiten.get(url.replace(m.TA, ""), ""), 200 if url.replace(m.TA, "") in seiten else 404)
pruefe(m.ta_stunden("Moving-Out-2") == (15, 20, "Moving Out 2"), "TA-Stunden aus completiontime")
pruefe(m.ta_stunden("Gabby") == (1, 2, None), "TA-Stunden aus achievements")
pruefe(m.ta_stunden("Gibtsnicht") is None, "TA-Stunden fehlen")
m.requests.get = alt_get

seite = ('<nav><a href="/game/Halo-Infinite">Halo Infinite</a></nav><script>{"articleBody":"Games leaving Xbox Game Pass '
         'on November 30 Aliens: Dark Descent Star Trucker"}</script><a href="/game/Aliens-Dark-Descent"><b>Aliens: Dark Descent</b></a>'
         '<a href="/game/Star-Trucker/achievements">Star Trucker</a>')
txt = " ".join(m.json_text(t) for t in re.findall(r'"articleBody"\s*:\s*"((?:[^"\\]|\\.)*)"', seite))
pruefe([x[1] for x in m.spiele_aus_links(seite, txt)] == ["Aliens-Dark-Descent", "Star-Trucker"], "Game-Pass-Fallback über Links")

print()
print("Alles in Ordnung." if not FEHLER else f"{len(FEHLER)} Fehler.")
sys.exit(1 if FEHLER else 0)
