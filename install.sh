#!/bin/bash
# Morgenzeitung – Installation im LXC (Debian 13), als root ausführen:
#   bash install.sh
set -euo pipefail
ZIEL=/opt/morgenzeitung
QUELLE="$(cd "$(dirname "$0")" && pwd)"

apt-get update
apt-get install -y python3 python3-venv tzdata
timedatectl set-timezone Europe/Berlin 2>/dev/null || ln -sf /usr/share/zoneinfo/Europe/Berlin /etc/localtime

mkdir -p "$ZIEL/ausgaben"
cp "$QUELLE/morgenzeitung.py" "$ZIEL/"
# Konfiguration nur beim ersten Mal kopieren, damit Passwörter nicht überschrieben werden
[ -f "$ZIEL/config.toml" ] || cp "$QUELLE/config.toml" "$ZIEL/"
chmod 600 "$ZIEL/config.toml"

python3 -m venv "$ZIEL/venv"
"$ZIEL/venv/bin/pip" install --upgrade pip
"$ZIEL/venv/bin/pip" install feedparser requests trafilatura googlenewsdecoder

cat > /etc/systemd/system/morgenzeitung.service <<EOF
[Unit]
Description=Morgenzeitung bauen und an Kindle senden
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
ExecStart=$ZIEL/venv/bin/python $ZIEL/morgenzeitung.py
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
systemctl enable --now morgenzeitung.timer
echo
echo "Fertig. Jetzt $ZIEL/config.toml ausfüllen (Gmail-Adresse, App-Passwort, HA-Token)."
echo "Test ohne Versand:  $ZIEL/venv/bin/python $ZIEL/morgenzeitung.py --nosend"
echo "Test mit Versand:   systemctl start morgenzeitung.service && journalctl -u morgenzeitung -n 30"
