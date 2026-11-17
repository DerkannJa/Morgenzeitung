# Übergabe: Morgenzeitung – Stand 17.11.2026

Diese Datei ist der Einstiegspunkt für jede weitere Arbeit an der Morgenzeitung. Sie beschreibt, was gebaut ist, wie es bei Luka läuft, was bekannt wackelig ist und was als Nächstes ansteht. Wer weiterarbeitet (Luka oder Claude in einem neuen Chat), sollte diese Datei zuerst komplett lesen.

## 1. Worum es geht

Luka bekommt jeden Morgen um 05:30 Uhr eine personalisierte Zeitung als EPUB auf seinen Kindle (11. Generation, 6 Zoll, Schwarz-Weiß). Inspiriert vom TikTok-Video von „JonathansBuild" (Heimserver, der morgens eine Zeitung auf den E-Reader schickt). Keine Werbung, kein Endlos-Scrollen – wenn die Zeitung zu Ende ist, ist sie zu Ende.

Inhalt einer Ausgabe:
- **Titelseite:** Wetter (aktuell + 4 Tage), Termine heute/morgen aus mehreren HA-Kalendern, Feiertage Hessen 7 Tage im Voraus, Anime mit neuer Folge heute (AniList, Kasten nur an solchen Tagen), anklickbares Inhaltsverzeichnis (nur Rubriknamen, ohne Artikelanzahl – so gewünscht).
- **Rubriken** (Reihenfolge = `config.toml`): Nachrichten, Lokales, Flohmärkte, Flohmarkt-News, IT & Technik, Rechenzentrum, Smart Home, Hacks & Entdeckungen, Gaming, Neue Releases, TrueAchievements & Gamerscore, Rocket League.

## 2. Wo es läuft

| Was | Wert |
|---|---|
| Host | Proxmox VE 9, `192.168.178.103` (Mini-PC N150, 32 GB) |
| Container | LXC **105** `morgenzeitung`, Debian 13, unprivilegiert, DHCP |
| Programmordner | `/opt/morgenzeitung` (Skript, `config.toml`, `venv/`, `ausgaben/`) |
| Zeitplan | systemd-Timer `morgenzeitung.timer`, täglich 05:30 Europe/Berlin, `Persistent=true` |
| Versand | Gmail SMTP (`smtp.gmail.com:587`, App-Passwort) → Send-to-Kindle `luka.geiss_9LgRqi@kindle.com` (Gmail-Adresse ist bei Amazon als Absender freigegeben) |
| Home Assistant | `http://192.168.178.173:8123`, langlebiger Token „Morgenzeitung“ |
| Sicherheitsupdates | `unattended-upgrades` im LXC aktiv |

Secrets (Gmail-Adresse, App-Passwort, HA-Token, AniList-Name) stehen **nur** in `/opt/morgenzeitung/geheim.toml` im LXC (chmod 600). Das Skript legt diese Datei beim Laden über `config.toml` (`config_laden()`/`zusammenfuehren()`). Im Repo stehen Platzhalter. Nie echte Werte committen.

Layout im LXC: `/opt/morgenzeitung/app` = Git-Checkout von `main` (öffentliches Repo, kein Key nötig), daneben `geheim.toml`, `venv/`, `ausgaben/`, `verlauf.json`, `alt/` (Dateien aus der ZIP-Zeit).

### Home-Assistant-Entitäten, die genutzt werden
- `weather.forecast_home` – Wetter + `weather.get_forecasts` (daily)
- `calendar.claude` – lokaler HA-Kalender, in den Claude Termine einträgt
- `calendar.google_luka` – Google-Kalender per Integration „Remote Calendar“ (iCal-Adresse, nur lesend; Aktualisierung mit einigen Stunden Verzögerung)
- `calendar.abfall` – Müllabfuhr
- `calendar.app_deutschland_he` – Feiertage Deutschland/Hessen (Integration „Holiday“)

## 3. Aufbau des Codes

Alles steckt in `morgenzeitung.py` (eine Datei, ~550 Zeilen, Python 3.13, Abhängigkeiten: `feedparser`, `requests`, `trafilatura`, `googlenewsdecoder`).

Ablauf von `zeitung_bauen(cfg)`:
1. Titelseite: `wetter_html`, `termine_html`, `feiertage_html` über die HA-REST-API (Klasse `HA`), danach `anime_html` (AniList, siehe 4.7).
2. Für jede `[[rubrik]]`:
   - `typ = "hessenschau_termine"` → `termine_sammeln()` (siehe 4.4)
   - sonst → `rubrik_sammeln()`: Feeds laden (`feed_laden`), **abwechselnd** aus allen Feeds der Rubrik nehmen (Round-Robin), Alter filtern, Stichwortfilter, Duplikate über alle Rubriken hinweg aussortieren (`titel_norm`), bei `volltext = true` Artikeltext holen.
3. `epub_bauen()` schreibt ein EPUB 3 von Hand (zipfile, `mimetype` unkomprimiert zuerst, `nav.xhtml` + `toc.ncx`, ein Kapitel pro Rubrik). Kein Calibre nötig.
4. `senden()` verschickt per SMTP/STARTTLS.
5. Alte EPUBs werden nach `behalten_tage` gelöscht.

Aufruf: `morgenzeitung.py` (bauen + senden), `--nosend` (nur bauen), `--config PFAD`.

### Konfigurationsschlüssel pro Rubrik
| Schlüssel | Bedeutung |
|---|---|
| `feeds` | Liste von RSS/Atom-URLs |
| `anzahl` | max. Artikel |
| `volltext` | Artikeltext laden (auch bei Google-News-Links, die werden aufgelöst) |
| `nur_mit_text` | Meldungen ohne lesbaren Text (< 25 Wörter) überspringen |
| `max_woerter` | Kürzungsgrenze für diese Rubrik (sonst global `max_woerter_pro_artikel`) |
| `max_alter_stunden` | ältere Meldungen ignorieren |
| `stichwoerter` | nur Einträge, deren Titel, Kurztext oder Artikeltext ein Stichwort enthält (Wortgrenzen, case-insensitive) |
| `ausschluss` | Einträge mit einem dieser Wörter in Titel, Kurztext oder Artikeltext fliegen raus (z. B. College bei Rocket League, Eishockey bei Gaming) |
| `themen_ignorieren` | Wörter, die beim Themenvergleich nicht zählen (z. B. Ortsnamen bei Lokales); der Rubrikname zählt automatisch nicht |
| `typ = "hessenschau_termine"` + `orte` + `mit_maerkten` | Flohmarkt-Terminübersicht der hessenschau, gefiltert nach Orten |

## 4. Besonderheiten und warum sie so gebaut sind

### 4.1 Google-News-Links auflösen (`echter_link`, `google_link_aufloesen`)
Google-News-RSS liefert nur verschlüsselte Weiterleitungslinks. Aus Deutschland antwortet Google mit 302 auf die Cookie-Zustimmung. Lösung: eigene Implementierung mit Cookies `CONSENT`/`SOCS`, liest `data-n-a-sg`/`data-n-a-ts` und fragt `.../DotsSplashUi/data/batchexecute` (`Fbv4je`/`garturlreq`). Fallback: Paket `googlenewsdecoder`. **Inoffizielle Schnittstelle** – kann jederzeit brechen. Symptom im Log: „Google-Link nicht auflösbar“ / „Google-Seite ohne Signatur“ → Rubriken mit `nur_mit_text = true` werden dann leer bzw. zeigen nur Überschriften.

### 4.2 Bezahlschranken (`volltext` → `Paywall`)
Luka will Paywall-Artikel **gar nicht** sehen, auch nicht den frei lesbaren Anfang. `volltext()` wirft `Paywall`, wenn
- das Seiten-HTML `"isAccessibleForFree": false` (schema.org) enthält, oder
- der extrahierte Text eine typische Formulierung enthält (`PAYWALL_TEXT`: „um alles zu lesen“, „jetzt weiterlesen mit“, „exklusiv für Abonnenten“, „Lorem ipsum“-Platzhalter, „subscribe to continue“ …).
Der Artikel wird dann übersprungen und der nächste rückt nach. Log: „Bezahlschranke, übersprungen“. Neue Formulierungen bei Bedarf in `PAYWALL_TEXT` ergänzen.

### 4.3 Zeichenkodierung
`trafilatura.extract()` bekommt **Bytes** (`r.content`), nicht `r.text`. Sonst kommen bei Seiten ohne Charset-Header (z. B. heise) kaputte Umlaute („Ã¼“).

### 4.4 Flohmärkte (`termine_sammeln`, `termine_parsen`)
Quelle: wöchentliche Übersicht „Flohmärkte, Märkte und Feste in Hessen“ der hessenschau, gefunden über `https://www.hessenschau.de/freizeit/index.rss` (Link enthält `flohmaerkte`). Parser zerlegt Text in Orts-Überschriften und Einträge, zwei Formate werden erkannt: „Datum“-Zeile + „- Text“-Zeile, oder „Datum - Text“ in einer Zeile. Ortsabgleich über `ort_treffer` („Rüsselsheim am Main“ passt zu „Rüsselsheim“, „Langenselbold“ nicht zu „Langen“). Abschnitt „Märkte und Feste“ nur mit `mit_maerkten = true`. Übersicht deckt immer nur bis zum kommenden Sonntag ab. Entwickelt mit einer nachgebauten Seite; das Problem beim ersten echten Lauf war laut Luka ein Eingabefehler seinerseits, kein Fehler im Parser. Ändert die hessenschau ihr Seitenformat, zuerst den echten Seitentext ansehen.

Die Google-Rubrik „Flohmarkt-News“ liefert oft 0 Treffer (Google hält `when:14d` bei komplexen Suchen nicht ein, Treffer sind meist alt). Das ist erwartetes Verhalten; leere Rubriken entfallen automatisch.

### 4.4b Verlauf: jeder Bericht nur einmal (`Verlauf`)
Datei `verlauf_datei` (`/opt/morgenzeitung/verlauf.json`, nicht im Repo) speichert pro gezeigtem Bericht Datum, Titel-Schlüssel, URL(s) und die Themen-Wörter. Einträge älter als `verlauf_tage` (21) fallen raus. Gemerkt wird **erst nach erfolgreichem Versand** (`--nosend` merkt nichts, außer mit `--merken`). Ein Bericht, der schon einmal verschickt wurde, erscheint nie wieder; findet eine Rubrik nichts Neues, entfällt sie. Die Flohmarkt-Wochenübersicht erscheint dadurch einmal pro Woche. Verlauf zurücksetzen: Datei löschen.

### 4.4c Themen-Duplikate (`themen_woerter`, `gleiches_thema`)
Ohne KI, über Wortvergleich: bedeutungstragende Wörter (Stoppwörter raus, grob gestemmt, Abkürzungen wie GDL/RTX und Zahlen zählen mit). Gleiches Thema, wenn
- Titel-Überlappung ≥ 0,6, oder ≥ 0,5 bei mindestens 4 Wörtern, oder ein 2-Wort-Titel komplett im anderen steckt, oder
- die ersten ~80 Wörter des Artikels Jaccard ≥ 0,3 haben (bzw. ≥ 0,18 bei Titel-Überlappung ≥ 0,4).
Verglichen wird mit allen Berichten dieser Ausgabe (rubrikübergreifend) und mit dem Verlauf. Wörter aus dem Rubriknamen und `themen_ignorieren` zählen nicht. Beispiele und Grenzfälle stehen in `tests/test_funktionen.py` – bei Fehlalarmen oder durchrutschenden Doppelungen dort einen Fall ergänzen und Schwellen anpassen. Bekannte Grenze: sehr kurze Titel ohne Artikeltext („Xbox Game Pass im Dezember“) werden nicht immer als gleich erkannt.

### 4.4d Lesbarkeit der Artikel
`html_saeubern()` entfernt vor dem Extrahieren `figure`, `figcaption`, `picture`, `img`, `video`, `aside` und Elemente mit Klassen wie caption/credit/copyright/bildunterschrift. Danach: Bildnachweis-Zeilen („Foto: …“, „©“) raus, Absätze, die einem früheren zu ≥ 60 % gleichen, raus. trafilatura liefert Markdown → Zwischenüberschriften werden `<h4>`, Aufzählungen `<ul>`. Erster Absatz als fetter Vorspann (`.lead`), in der Kopfzeile „ca. X Min.“ Lesezeit (ab 150 Wörtern).

### 4.5 Hacks & Entdeckungen
Hacker News (`hnrss.org/newest?q=…&points=30`), HN-Startseite, Hackaday – lokal gefiltert über `stichwoerter`. Mehrdeutige Begriffe („Echo“, „Ring“, „Nest“, „Matter“, „Hue“) bewusst nur in eindeutiger Form. Reddit-RSS wurde von Reddit am 13.11.2026 eingestellt – nicht verwenden.

### 4.7 Anime (`anime_html`, Abschnitt `[anime]`)
AniList-GraphQL-API (`graphql.anilist.co`, ohne Schlüssel). Liest Lukas Liste „Watching“/„Rewatching“ (`anilist_nutzer`, Liste muss öffentlich sein, sonst Fehler „Private User“ im Log) und fragt `airingSchedules` für heute 00:00–24:00 ab. Uhrzeit = TV-Ausstrahlung Japan in deutscher Zeit; bereits gelaufene Folgen stehen als „schon da“. Keine Folge heute → kein Kasten. `titel = "romaji"` oder `"english"`. `update.sh` übernimmt `anilist_nutzer` aus der alten Konfiguration.

### 4.6 Layout (Kindle 11. Gen, 6")
CSS in `CSS` im Skript: linksbündig statt Blocksatz (Luka fand Blocksatz unschön), Zeitungskopf mit Linien, Titelseiten-„Kästen“ mit Spalten (`.zeile b { min-width: 5em }`), Rubriken mit Balken, Artikel mit Trennlinien. Titel 1.45em, damit „Lukas Morgenzeitung“ in eine Zeile passt. Keine Bilder (Graustufen-Gerät).

## 5. Updates (automatisch seit 17.11.2026)

**Ein Push auf `main` reicht.** Der Service hat `ExecStartPre=-/usr/local/bin/zeitung-holen`: vor jedem Lauf (05:30) `git fetch` + `reset --hard origin/main`, `pip install -r requirements.txt`, dann Prüfung (`py_compile` des Skripts + `tomllib` der `config.toml`). Ist der neue Stand kaputt, wird auf den vorherigen Commit zurückgesetzt. Ist GitHub nicht erreichbar, läuft der bisherige Stand.

- Sofort aktualisieren + Probelauf ohne Versand (im LXC): `zeitung-update`
- Vom Proxmox-Host aus: `pct exec 105 -- zeitung-update`
- Senden: `pct exec 105 -- systemctl start morgenzeitung.service`, Log: `journalctl -u morgenzeitung -n 30 --no-pager`
- Neuinstallation/Reparatur (idempotent, übernimmt Zugangsdaten aus alter `config.toml`):
  `pct exec 105 -- bash -c "apt-get update -qq && apt-get install -y -qq curl && curl -fsSL https://raw.githubusercontent.com/DerkannJa/Morgenzeitung/main/install.sh | bash"`

Folge: `config.toml` im Repo ist die einzige Konfiguration. Änderungen direkt im LXC an `app/config.toml` werden beim nächsten Lauf überschrieben → immer im Repo ändern. Nur `geheim.toml` ist lokal.

Luka-Präferenz für Shell-Anleitungen: alle Befehle auf einmal, jeder in eigenem Codeblock.

## 6. Testen ohne Lukas Server

Zwei Offline-Tests (kein Netz nötig):
```bash
pip install feedparser requests trafilatura googlenewsdecoder lxml
python3 tests/test_funktionen.py   # Bildunterschriften, Absatz-Doppelungen, Bezahlschranken, Themenvergleich
python3 tests/mock_lauf.py         # zwei Tage Probelauf, prüft den Verlauf; EPUBs in tests/out/
```
Prüft nur Struktur und Layout; echte Feeds/Google/hessenschau verhalten sich anders. Vorschau einer Seite: EPUB entpacken und `OEBPS/titel.xhtml` im Browser öffnen (headless Chrome-Screenshot bei 600×1000 ähnelt dem Kindle).

## 7. Offene Punkte und Ideen

- [ ] Über einige Wochen beobachten, ob die Flohmarkt-Rubrik mit der echten hessenschau-Seite zuverlässig Treffer liefert.
- [ ] Themen-Duplikate und Paywall-Erkennung im echten Betrieb beobachten (Log: „aussortiert: …“ pro Rubrik); Schwellen/Formulierungen bei Bedarf nachschärfen.
- [x] Deployment per Git statt ZIP (17.11.2026, siehe 5).
- [ ] Lauf-Überwachung: bei Fehler im Timer-Lauf Push über Home Assistant (z. B. `OnFailure=` im Service → Webhook an HA).
- [ ] Optional KI-Auswahl/Zusammenfassung (wie im Video): lokal über Ollama auf Lukas Desktop (RTX 3070 Ti, muss laufen) oder per API. Bisher bewusst ohne KI.
- [ ] Optional: Mails (Gmail) als Rubrik „Wichtiges aus dem Postfach“.
- [ ] E-Ink-Displays (Garderobe: Kalender/Wetter) sind ein **separates** Projekt; Hardware: 2× Seeed TRMNL 7.5" (OG) DIY Kit über ESPHome. Black-Friday-Preiswächter läuft als geplante Aufgabe bis 02.12.2026.

## 8. Wie Luka zusammenarbeiten möchte

- Deutsch, klar und knapp, keine Emojis, keine geschönten Antworten, nichts erfinden – lieber nachfragen.
- Vor Annahmen über seine Ausstattung erst fragen.
- Shell-Anleitungen: alle Befehle auf einmal, jeder in einem eigenen Codeblock.
- Bei Rückmeldungen zu Updates: was gemacht wurde, was neu ist, welche Fehler behoben sind.
