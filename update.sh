#!/bin/bash
# Morgenzeitung – Update im LXC, als root ausführen:  bash update.sh
# Übernimmt Gmail-Adresse, App-Passwort und HA-Token aus der bisherigen config.toml.
set -euo pipefail
ZIEL=/opt/morgenzeitung
QUELLE="$(cd "$(dirname "$0")" && pwd)"

cp "$ZIEL/config.toml" "$ZIEL/config.toml.bak"
cp "$QUELLE/morgenzeitung.py" "$ZIEL/"
"$ZIEL/venv/bin/pip" install -q feedparser requests trafilatura googlenewsdecoder

"$ZIEL/venv/bin/python" - "$ZIEL/config.toml.bak" "$QUELLE/config.toml" "$ZIEL/config.toml" <<'EOF'
import re, sys, tomllib
alt_pfad, neu_pfad, ziel = sys.argv[1:4]
alt = tomllib.load(open(alt_pfad, "rb"))
text = open(neu_pfad, encoding="utf-8").read()
werte = {
    "absender": alt["mail"]["absender"],
    "app_passwort": alt["mail"]["app_passwort"],
    "token": alt["homeassistant"]["token"],
}
for key, wert in werte.items():
    text, n = re.subn(rf'^{key}\s*=\s*"[^"]*"', lambda m: f'{key} = "{wert}"', text, count=1, flags=re.M)
    if n != 1:
        sys.exit(f"Schlüssel {key} nicht gefunden")
open(ziel, "w", encoding="utf-8").write(text)
tomllib.load(open(ziel, "rb"))  # Prüfen, ob die neue Datei gültig ist
print("Konfiguration übernommen.")
EOF
chmod 600 "$ZIEL/config.toml"
echo "Update fertig. Alte Konfiguration liegt in $ZIEL/config.toml.bak"
echo "Test:  $ZIEL/venv/bin/python $ZIEL/morgenzeitung.py --nosend"
