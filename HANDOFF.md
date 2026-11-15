# Übergabe: Morgenzeitung – Stand 15.11.2026

Diese Datei ist der Einstiegspunkt für jede weitere Arbeit an der Morgenzeitung. Sie beschreibt, was gebaut ist, wie es bei Luka läuft, was bekannt wackelig ist und was als Nächstes ansteht. Wer weiterarbeitet (Luka oder Claude in einem neuen Chat), sollte diese Datei zuerst komplett lesen.

## 1. Worum es geht

Luka bekommt jeden Morgen um 05:30 Uhr eine personalisierte Zeitung als EPUB auf seinen Kindle (11. Generation, 6 Zoll, Schwarz-Weiß). Inspiriert vom TikTok-Video von „JonathansBuild" (Heimserver, der morgens eine Zeitung auf den E-Reader schickt). Keine Werbung, kein Endlos-Scrollen – wenn die Zeitung zu Ende ist, ist sie zu Ende.

Inhalt einer Ausgabe:
- **Titelseite:** Wetter (aktuell + 4 Tage), Termine heute/morgen aus mehreren HA-Kalendern, Feiertage Hessen 7 Tage im Voraus, anklickbares Inhaltsverzeichnis.
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

Secrets (Gmail-Adresse, App-Passwort, HA-Token) stehen **nur** in `/opt/morgenzeitung/config.toml` im LXC (chmod 600). Im Repo stehen Platzhalter. Nie echte Werte committen.

### Home-Assistant-Entitäten, die genutzt werden
- `weather.forecast_home` – Wetter + `weather.get_forecasts` (daily)
- `calendar.claude` – lokaler HA-Kalender, in den Claude Termine einträgt
- `calendar.google_luka` – Google-Kalender per Integration „Remote Calendar“ (iCal-Adresse, nur lesend; Aktualisierung mit einigen Stunden Verzögerung)
- `calendar.abfall` – Müllabfuhr
- `calendar.app_deutschland_he` – Feiertage Deutschland/Hessen (Integration „Holiday“)

## 3. Aufbau des Codes

Alles steckt in `morgenzeitung.py` (eine Datei, ~550 Zeilen, Python 3.13, Abhängigkeiten: `feedparser`, `requests`, `trafilatura`, `googlenewsdecoder`).

Ablauf von `zeitung_bauen(cfg)`:
1. Titelseite: `wetter_html`, `termine_html`, `feiertage_html` über die HA-REST-API (Klasse `HA`).
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
| `stichwoerter` | nur Einträge, deren Titel/Kurztext ein Stichwort enthält (Wortgrenzen, case-insensitive) |
| `typ = "hessenschau_termine"` + `orte` + `mit_maerkten` | Flohmarkt-Terminübersicht der hessenschau, gefiltert nach Orten |

## 4. Besonderheiten und warum sie so gebaut sind

### 4.1 Google-News-Links auflösen (`echter_link`, `google_link_aufloesen`)
Google-News-RSS liefert nur verschlüsselte Weiterleitungslinks. Aus Deutschland antwortet Google mit 302 auf die Cookie-Zustimmung. Lösung: eigene Implementierung mit Cookies `CONSENT`/`SOCS`, liest `data-n-a-sg`/`data-n-a-ts` und fragt `.../DotsSplashUi/data/batchexecute` (`Fbv4je`/`garturlreq`). Fallback: Paket `googlenewsdecoder`. **Inoffizielle Schnittstelle** – kann jederzeit brechen. Symptom im Log: „Google-Link nicht auflösbar“ / „Google-Seite ohne Signatur“ → Rubriken mit `nur_mit_text = true` werden dann leer bzw. zeigen nur Überschriften.

### 4.2 Bezahlschranken (`volltext`)
Manche Lokalzeitungen liefern hinter der Paywall Platzhaltertext („Lorem ipsum … consetetur sadipscing …“). Alles ab dem ersten Platzhalter-Absatz wird verworfen; bleiben < 60 Wörter, gilt der Artikel als „kein Text“, sonst wird „(Rest hinter Bezahlschranke)“ angehängt. Log: „Bezahlschranke erkannt“.

### 4.3 Zeichenkodierung
`trafilatura.extract()` bekommt **Bytes** (`r.content`), nicht `r.text`. Sonst kommen bei Seiten ohne Charset-Header (z. B. heise) kaputte Umlaute („Ã¼“).

### 4.4 Flohmärkte (`termine_sammeln`, `termine_parsen`)
Quelle: wöchentliche Übersicht „Flohmärkte, Märkte und Feste in Hessen“ der hessenschau, gefunden über `https://www.hessenschau.de/freizeit/index.rss` (Link enthält `flohmaerkte`). Parser zerlegt Text in Orts-Überschriften und Einträge, zwei Formate werden erkannt: „Datum“-Zeile + „- Text“-Zeile, oder „Datum - Text“ in einer Zeile. Ortsabgleich über `ort_treffer` („Rüsselsheim am Main“ passt zu „Rüsselsheim“, „Langenselbold“ nicht zu „Langen“). Abschnitt „Märkte und Feste“ nur mit `mit_maerkten = true`. Übersicht deckt immer nur bis zum kommenden Sonntag ab. **Nur mit nachgebauter Seite getestet** – Luka hatte beim ersten echten Lauf einen Fehler, den er selbst gefunden hat (Details unbekannt). Bei Problemen zuerst den echten Seitentext ansehen.

Die Google-Rubrik „Flohmarkt-News“ liefert oft 0 Treffer (Google hält `when:14d` bei komplexen Suchen nicht ein, Treffer sind meist alt). Das ist erwartetes Verhalten; leere Rubriken entfallen automatisch.

### 4.5 Hacks & Entdeckungen
Hacker News (`hnrss.org/newest?q=…&points=30`), HN-Startseite, Hackaday – lokal gefiltert über `stichwoerter`. Mehrdeutige Begriffe („Echo“, „Ring“, „Nest“, „Matter“, „Hue“) bewusst nur in eindeutiger Form. Reddit-RSS wurde von Reddit am 13.11.2026 eingestellt – nicht verwenden.

### 4.6 Layout (Kindle 11. Gen, 6")
CSS in `CSS` im Skript: linksbündig statt Blocksatz (Luka fand Blocksatz unschön), Zeitungskopf mit Linien, Titelseiten-„Kästen“ mit Spalten (`.zeile b { min-width: 5em }`), Rubriken mit Balken, Artikel mit Trennlinien. Titel 1.45em, damit „Lukas Morgenzeitung“ in eine Zeile passt. Keine Bilder (Graustufen-Gerät).

## 5. Update einspielen (bewährter Ablauf)

Luka arbeitet unter Windows (PowerShell) und in der Proxmox-Shell. Befehle **jeweils in eigenem Codeblock**, alle auf einmal (nicht in einem großen Block, nicht einzeln mit Warten). Nach `pct enter 105` erst weiterkopieren, wenn `root@morgenzeitung` erscheint.

1. PC (PowerShell), nimmt automatisch die neueste ZIP aus Downloads:
   ```powershell
   $zip = Get-ChildItem "$env:USERPROFILE\Downloads\morgenzeitung*.zip" | Sort-Object LastWriteTime | Select-Object -Last 1; $zip.Name; scp $zip.FullName root@192.168.178.103:/root/morgenzeitung.zip
   ```
2. Proxmox-Host: `pct push 105 /root/morgenzeitung.zip /root/morgenzeitung.zip`
3. `pct enter 105`
4. `cd /root && rm -rf morgenzeitung && unzip -o morgenzeitung.zip && bash morgenzeitung/update.sh`
   `update.sh` sichert `config.toml` als `.bak`, kopiert neues Skript und neue `config.toml` und **übernimmt Gmail-Adresse, App-Passwort und HA-Token automatisch** aus der alten Konfiguration.
5. Test: `/opt/morgenzeitung/venv/bin/python /opt/morgenzeitung/morgenzeitung.py --nosend 2>&1 | grep "Rubrik"`
6. Senden: `systemctl start morgenzeitung.service && journalctl -u morgenzeitung -n 15 --no-pager`

Achtung: `update.sh` ersetzt die komplette `config.toml` durch die Repo-Version (nur die drei Secrets bleiben). Änderungen, die Luka direkt im LXC gemacht hat, gehen dabei verloren → vorher ins Repo übernehmen. Stand 15.11.: Repo-`config.toml` enthält alle bisher im LXC gemachten Änderungen (Volltext für Rechenzentrum/Smart Home/Gaming/Releases/TA/Rocket League, Flohmärkte, Hacks & Entdeckungen).

Mit dem Repo kann der Ablauf künftig auch `git pull` im LXC sein statt ZIP – noch nicht eingerichtet (siehe 7).

## 6. Testen ohne Lukas Server

`tests/mock_lauf.py` baut eine Ausgabe mit nachgebauten Feeds, HA-Antworten und Artikeln (kein Netz nötig) und schreibt nach `tests/out/`. Nützlich nach jeder Änderung:
```bash
pip install feedparser requests trafilatura googlenewsdecoder
python3 tests/mock_lauf.py
```
Prüft nur Struktur und Layout; echte Feeds/Google/hessenschau verhalten sich anders. Vorschau einer Seite: EPUB entpacken und `OEBPS/titel.xhtml` im Browser öffnen (headless Chrome-Screenshot bei 600×1000 ähnelt dem Kindle).

## 7. Offene Punkte und Ideen

- [ ] Prüfen, ob die Flohmarkt-Rubrik mit der echten hessenschau-Seite zuverlässig Treffer liefert (Lukas Fix von 13.11. nachvollziehen und ins Repo übernehmen).
- [ ] Deployment per `git pull` statt ZIP (Deploy-Key im LXC, `update.sh` liest dann aus dem Checkout).
- [ ] Lauf-Überwachung: bei Fehler im Timer-Lauf Push über Home Assistant (z. B. `OnFailure=` im Service → Webhook an HA).
- [ ] Optional KI-Auswahl/Zusammenfassung (wie im Video): lokal über Ollama auf Lukas Desktop (RTX 3070 Ti, muss laufen) oder per API. Bisher bewusst ohne KI.
- [ ] Optional: Mails (Gmail) als Rubrik „Wichtiges aus dem Postfach“.
- [ ] E-Ink-Displays (Garderobe: Kalender/Wetter) sind ein **separates** Projekt; Hardware: 2× Seeed TRMNL 7.5" (OG) DIY Kit über ESPHome. Black-Friday-Preiswächter läuft als geplante Aufgabe bis 02.12.2026.

## 8. Wie Luka zusammenarbeiten möchte

- Deutsch, klar und knapp, keine Emojis, keine geschönten Antworten, nichts erfinden – lieber nachfragen.
- Vor Annahmen über seine Ausstattung erst fragen.
- Shell-Anleitungen: alle Befehle auf einmal, jeder in einem eigenen Codeblock.
- Bei Rückmeldungen zu Updates: was gemacht wurde, was neu ist, welche Fehler behoben sind.
