#!/bin/bash
# Morgenzeitung – Installation bzw. Umstellung auf automatische Updates (LXC, Debian), als root:
#   curl -fsSL https://raw.githubusercontent.com/DerkannJa/Morgenzeitung/main/install.sh | bash
# Kann beliebig oft ausgeführt werden. Eine bestehende Installation wird übernommen:
# Gmail-Adresse, App-Passwort, HA-Token und AniList-Name wandern nach /opt/morgenzeitung/geheim.toml.
set -euo pipefail
ZIEL=/opt/morgenzeitung
APP=$ZIEL/app
REPO=https://github.com/DerkannJa/Morgenzeitung.git

export DEBIAN_FRONTEND=noninteractive
if [ -z "${SCHNELL:-}" ]; then
    apt-get update -qq
    apt-get install -y -qq git python3 python3-venv tzdata ca-certificates >/dev/null
fi
timedatectl set-timezone Europe/Berlin 2>/dev/null || ln -sf /usr/share/zoneinfo/Europe/Berlin /etc/localtime

mkdir -p "$ZIEL/ausgaben"
if [ -d "$APP/.git" ]; then
    [ -n "${SCHNELL:-}" ] || { git -C "$APP" fetch -q origin main && git -C "$APP" reset -q --hard origin/main; }
else
    git clone -q --branch main "$REPO" "$APP"
fi

# Zugangsdaten getrennt vom Repo ablegen
if [ ! -f "$ZIEL/geheim.toml" ]; then
    python3 - "$ZIEL/config.toml" "$ZIEL/geheim.toml" <<'EOF'
import json, sys, tomllib
from pathlib import Path
alt, ziel = Path(sys.argv[1]), Path(sys.argv[2])
c = tomllib.load(open(alt, "rb")) if alt.is_file() else {}
m, h, a = c.get("mail", {}), c.get("homeassistant", {}), c.get("anime", {})
q = json.dumps
zeilen = [
    "# Zugangsdaten der Morgenzeitung – wird von Updates nie überschrieben",
    "[mail]",
    f"absender = {q(m.get('absender', 'DEINE_GMAIL_ADRESSE@gmail.com'))}",
    f"app_passwort = {q(m.get('app_passwort', 'xxxx xxxx xxxx xxxx'))}",
    "",
    "[homeassistant]",
    f"token = {q(h.get('token', 'LANGLEBIGES_HA_TOKEN'))}",
]
n = a.get("anilist_nutzer", "")
if n and not n.startswith("DEIN"):
    zeilen += ["", "[anime]", f"anilist_nutzer = {q(n)}"]
ziel.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
tomllib.load(open(ziel, "rb"))
print("Zugangsdaten übernommen." if alt.is_file() else f"Bitte {ziel} ausfüllen.")
EOF
    chmod 600 "$ZIEL/geheim.toml"
fi

# Alte Einzeldateien aus der ZIP-Zeit wegräumen
if [ -f "$ZIEL/morgenzeitung.py" ] || [ -f "$ZIEL/config.toml" ]; then
    mkdir -p "$ZIEL/alt"
    mv -f "$ZIEL"/morgenzeitung.py "$ZIEL"/config.toml* "$ZIEL/alt/" 2>/dev/null || true
    chmod -R go-rwx "$ZIEL/alt"
fi

[ -x "$ZIEL/venv/bin/python" ] || python3 -m venv "$ZIEL/venv"
[ -n "${SCHNELL:-}" ] || "$ZIEL/venv/bin/pip" install -q --upgrade pip
"$ZIEL/venv/bin/pip" install -q -r "$APP/requirements.txt"

# Dateien atomar schreiben (neue Datei + mv), damit ein laufendes Skript nicht mitten im Lesen geändert wird
schreibe() { local ziel=$1 modus=$2; cat > "$ziel.neu"; chmod "$modus" "$ziel.neu"; mv -f "$ziel.neu" "$ziel"; }

# zeitung-holen: neuesten Stand von GitHub holen. Kaputter Stand -> zurück auf den alten + Meldung an HA.
# Mit --test: nach einem Update Probelauf ohne Versand, Ergebnis als Push über Home Assistant.
schreibe /usr/local/bin/zeitung-holen 755 <<SKRIPT
#!/bin/bash
APP=$APP
VENV=$ZIEL/venv
exec 9>/run/morgenzeitung.lock
flock -w 1800 9 || { echo "Anderer Lauf blockiert"; exit 0; }
PY="\$VENV/bin/python \$APP/morgenzeitung.py"
alt=\$(git -C "\$APP" rev-parse HEAD)
git -C "\$APP" fetch -q origin main || { echo "GitHub nicht erreichbar – nutze bisherigen Stand"; exit 0; }
neu=\$(git -C "\$APP" rev-parse origin/main)
[ "\$alt" = "\$neu" ] && { echo "Schon aktuell (\${alt:0:7})"; exit 0; }
# Schon als kaputt erkannten Stand nicht alle 15 Minuten erneut versuchen und melden
[ "\$neu" = "\$(cat $ZIEL/kaputt 2>/dev/null)" ] && { echo "Stand \${neu:0:7} ist fehlerhaft – bleibe auf \${alt:0:7}"; exit 0; }
git -C "\$APP" reset -q --hard "\$neu"
aenderungen=\$(git -C "\$APP" log --format='- %s' "\$alt..\$neu")
"\$VENV/bin/pip" install -q -r "\$APP/requirements.txt"
if ! "\$VENV/bin/python" -c "import sys, tomllib, py_compile; py_compile.compile(sys.argv[1], doraise=True); tomllib.load(open(sys.argv[2], 'rb'))" "\$APP/morgenzeitung.py" "\$APP/config.toml"; then
    echo "Neuer Stand fehlerhaft – zurück auf \${alt:0:7}"
    git -C "\$APP" reset -q --hard "\$alt"
    echo "\$neu" > $ZIEL/kaputt
    \$PY --nur-melden fehler "Morgenzeitung: Update fehlerhaft" "Stand \${neu:0:7} lässt sich nicht laden, es läuft weiter \${alt:0:7}.
\$aenderungen"
    exit 0
fi
echo "Aktualisiert: \${alt:0:7} -> \${neu:0:7}"
echo "\$aenderungen"
# Dienste/Timer/Skripte neu einrichten, falls sich das Installationsskript geändert hat
if ! git -C "\$APP" diff --quiet "\$alt" "\$neu" -- install.sh; then
    echo "install.sh geändert – richte Dienste neu ein"
    SCHNELL=1 bash "\$APP/install.sh" >/dev/null
fi
if [ "\${1:-}" = "--test" ]; then
    \$PY --test --notiz "Neu (\${neu:0:7}):
\$aenderungen" || true
fi
SKRIPT

# Sofort aktualisieren und Probelauf ohne Versand (zeigt Ergebnis im Terminal)
schreibe /usr/local/bin/zeitung-update 755 <<SKRIPT
#!/bin/bash
/usr/local/bin/zeitung-holen
echo "Probelauf ohne Versand ..."
$ZIEL/venv/bin/python $APP/morgenzeitung.py --nosend 2>&1 | grep -E "Rubrik|Game Pass|ERROR|Traceback|EPUB erstellt"
SKRIPT

schreibe /etc/systemd/system/morgenzeitung.service 644 <<SKRIPT
[Unit]
Description=Morgenzeitung bauen und an Kindle senden
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStartPre=-/usr/local/bin/zeitung-holen
ExecStart=/usr/bin/flock -w 1800 /run/morgenzeitung.lock $ZIEL/venv/bin/python $APP/morgenzeitung.py
WorkingDirectory=$ZIEL
SKRIPT

schreibe /etc/systemd/system/morgenzeitung.timer 644 <<SKRIPT
[Unit]
Description=Morgenzeitung jeden Morgen um 05:30

[Timer]
OnCalendar=*-*-* 05:30:00 Europe/Berlin
Persistent=true

[Install]
WantedBy=timers.target
SKRIPT

# Alle 15 Minuten nach Updates schauen; bei neuem Stand Probelauf + Meldung an HA
schreibe /etc/systemd/system/morgenzeitung-pruefen.service 644 <<SKRIPT
[Unit]
Description=Morgenzeitung: Updates holen und testen
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/zeitung-holen --test
WorkingDirectory=$ZIEL
SKRIPT

schreibe /etc/systemd/system/morgenzeitung-pruefen.timer 644 <<SKRIPT
[Unit]
Description=Morgenzeitung: alle 15 Minuten nach Updates schauen

[Timer]
OnBootSec=5min
OnUnitActiveSec=15min

[Install]
WantedBy=timers.target
SKRIPT

systemctl daemon-reload
systemctl enable -q --now morgenzeitung.timer morgenzeitung-pruefen.timer
echo
echo "Fertig. Stand: $(git -C "$APP" log --oneline -1)"
echo "Ab jetzt holt sich die Zeitung jeden Morgen vor dem Bauen selbst den neuesten Stand."
echo "Neue Stände werden alle 15 Minuten geholt, getestet und per Home Assistant gemeldet."
echo "Sofort aktualisieren + Probelauf:  zeitung-update"
