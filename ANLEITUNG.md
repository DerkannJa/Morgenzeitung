# Morgenzeitung – Einrichtung

Jeden Morgen um 05:30 baut ein LXC auf Proxmox ein EPUB mit Wetter, Terminen (HA-Kalender „Claude“ und „Abfall“) und Nachrichten und schickt es per Gmail an deinen Kindle.

## 1. Vorbereitungen (einmalig)

**Gmail-App-Passwort**
1. Google-Konto → Sicherheit → Bestätigung in zwei Schritten muss aktiv sein.
2. Google-Konto → Sicherheit → „App-Passwörter“ → Name „Morgenzeitung“ → 16-stelliges Passwort notieren.

**Amazon: Absender freigeben**
Amazon → Meine Inhalte und Geräte → Einstellungen → Einstellungen für persönliche Dokumente → „Liste genehmigter E-Mail-Adressen für persönliche Dokumente“ → deine Gmail-Adresse hinzufügen.

**Home-Assistant-Token**
HA → Profil (unten links) → Sicherheit → Langlebige Zugriffstoken → „Token erstellen“ → Name „Morgenzeitung“ → kopieren.

## 2. LXC anlegen (Proxmox-Shell)

```bash
pveam update
pveam available | grep debian-13      # genauen Template-Namen ablesen
pveam download local <TEMPLATE-NAME>
pct create 104 local:vztmpl/<TEMPLATE-NAME> \
  --hostname morgenzeitung --memory 512 --cores 1 \
  --rootfs local-lvm:4 --net0 name=eth0,bridge=vmbr0,ip=dhcp \
  --unprivileged 1 --features nesting=1 --onboot 1
pct start 104
```
ID 104 und Storage `local-lvm` ggf. an dein System anpassen.

## 3. Dateien übertragen und installieren

Den Ordner `morgenzeitung` (morgenzeitung.py, config.toml, install.sh) auf den Proxmox-Host kopieren, z. B. nach `/root/morgenzeitung`, dann:

```bash
for f in morgenzeitung.py config.toml install.sh; do pct push 104 /root/morgenzeitung/$f /root/$f; done
pct enter 104
bash /root/install.sh
```

## 4. Konfiguration ausfüllen

```bash
nano /opt/morgenzeitung/config.toml
```
Eintragen: `absender` (Gmail-Adresse), `app_passwort`, `token` (HA).

## 5. Testen

```bash
/opt/morgenzeitung/venv/bin/python /opt/morgenzeitung/morgenzeitung.py --nosend   # nur bauen
systemctl start morgenzeitung.service                                               # bauen + senden
journalctl -u morgenzeitung -n 40                                                   # Log ansehen
systemctl list-timers | grep morgenzeitung                                          # nächster Lauf
```
Die Zeitung erscheint nach ein paar Minuten in der Kindle-Bibliothek, sobald der Kindle im WLAN ist.

## Anpassen

- **Themen/Quellen:** In `config.toml` Rubriken ändern, hinzufügen oder löschen. Beliebige Google-News-Suche als Feed:
  `https://news.google.com/rss/search?q=SUCHBEGRIFF+when:2d&hl=de&gl=DE&ceid=DE:de`
- **Uhrzeit:** `/etc/systemd/system/morgenzeitung.timer` → `OnCalendar`, danach `systemctl daemon-reload`.
- **Volltext vs. Kurzmeldung:** Tagesschau und heise kommen mit vollem Artikel, Google-News-Rubriken nur mit Überschrift und Kurztext.
