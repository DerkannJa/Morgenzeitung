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
apt-get update -qq
apt-get install -y -qq git python3 python3-venv tzdata ca-certificates >/dev/null
timedatectl set-timezone Europe/Berlin 2>/dev/null || ln -sf /usr/share/zoneinfo/Europe/Berlin /etc/localtime

mkdir -p "$ZIEL/ausgaben"
if [ -d "$APP/.git" ]; then
    git -C "$APP" fetch -q origin main && git -C "$APP" reset -q --hard origin/main
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
"$ZIEL/venv/bin/pip" install -q --upgrade pip
"$ZIEL/venv/bin/pip" install -q -r "$APP/requirements.txt"

# Holt vor jedem Lauf den neuesten Stand von GitHub. Ist der neue Stand kaputt, bleibt der alte.
cat > /usr/local/bin/zeitung-holen <<EOF
#!/bin/bash
APP=$APP
VENV=$ZIEL/venv
alt=\$(git -C "\$APP" rev-parse HEAD)
git -C "\$APP" fetch -q origin main || { echo "GitHub nicht erreichbar – nutze bisherigen Stand"; exit 0; }
git -C "\$APP" reset -q --hard origin/main
neu=\$(git -C "\$APP" rev-parse HEAD)
[ "\$alt" = "\$neu" ] && { echo "Schon aktuell (\${neu:0:7})"; exit 0; }
"\$VENV/bin/pip" install -q -r "\$APP/requirements.txt"
if "\$VENV/bin/python" -c "import sys, tomllib, py_compile; py_compile.compile(sys.argv[1], doraise=True); tomllib.load(open(sys.argv[2], 'rb'))" "\$APP/morgenzeitung.py" "\$APP/config.toml"; then
    echo "Aktualisiert: \${alt:0:7} -> \${neu:0:7}"
    git -C "\$APP" log --oneline "\$alt..\$neu"
else
    echo "Neuer Stand fehlerhaft – zurück auf \${alt:0:7}"
    git -C "\$APP" reset -q --hard "\$alt"
fi
EOF
chmod 755 /usr/local/bin/zeitung-holen

# Sofort aktualisieren und Probelauf ohne Versand
cat > /usr/local/bin/zeitung-update <<EOF
#!/bin/bash
/usr/local/bin/zeitung-holen
echo "Probelauf ohne Versand ..."
$ZIEL/venv/bin/python $APP/morgenzeitung.py --nosend 2>&1 | grep -E "Rubrik|ERROR|Traceback|EPUB erstellt"
EOF
chmod 755 /usr/local/bin/zeitung-update

cat > /etc/systemd/system/morgenzeitung.service <<EOF
[Unit]
Description=Morgenzeitung bauen und an Kindle senden
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStartPre=-/usr/local/bin/zeitung-holen
ExecStart=$ZIEL/venv/bin/python $APP/morgenzeitung.py
WorkingDirectory=$ZIEL
EOF

cat > /etc/systemd/system/morgenzeitung.timer <<EOF
[Unit]
Description=Morgenzeitung jeden Morgen um 05:30

[Timer]
OnCalendar=*-*-* 05:30:00 Europe/Berlin
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable -q --now morgenzeitung.timer
echo
echo "Fertig. Stand: $(git -C "$APP" log --oneline -1)"
echo "Ab jetzt holt sich die Zeitung jeden Morgen vor dem Bauen selbst den neuesten Stand."
echo "Sofort aktualisieren + Probelauf:  zeitung-update"
