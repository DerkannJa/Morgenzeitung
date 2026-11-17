# Hinweise für Claude

Projekt: Lukas Morgenzeitung (tägliches EPUB auf den Kindle). Vollständige Übergabe mit Aufbau, Betrieb und offenen Punkten: `HANDOFF.md` – zuerst lesen.

## Wie Luka arbeiten möchte
- Antworten auf Deutsch, kurz, klar und ungeschönt. Keine Emojis. Keine Fakten erfinden, lieber nachfragen.
- Automatisierung zuerst: Luka will so wenig wie möglich selbst machen. Immer zuerst den automatischen Weg wählen; Handarbeit nur als allerletzter Fallback.
- Vor Annahmen über seine Geräte oder sein Setup nachfragen.
- Shell-Anleitungen: alle Befehle auf einmal, jeder in einem eigenen Codeblock (nicht ein großer Block), nicht nach jedem Befehl auf Rückmeldung warten.
- Bei Update-Berichten nur sagen, was gemacht wurde, was neu ist und welche Fehler behoben sind – nicht den Push oder den Update-Ablauf erklären.

## Betrieb in Kürze
- Push auf `main` genügt: Der LXC holt neue Stände alle 15 Minuten selbst, testet sie und meldet das Ergebnis per Home Assistant aufs Handy (siehe HANDOFF.md, Abschnitt 5).
- Dieses Repo ist öffentlich: keine Zugangsdaten und keine persönlichen Daten committen. Zugangsdaten liegen nur in `/opt/morgenzeitung/geheim.toml` im LXC.
- Tests ohne Netz: `python3 tests/test_funktionen.py` und `python3 tests/mock_lauf.py`.
