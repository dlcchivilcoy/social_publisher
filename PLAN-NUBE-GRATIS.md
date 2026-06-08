# Plan: Publicador en la nube — 100% gratis

## Objetivo
Que las automatizaciones del **publicador** (`social_publisher`) corran solas en la nube,
**sin depender de que la PC esté prendida**, sin costo. Las notas + fotos llegan por una
**carpeta de Google Drive sincronizada**.

### Alcance
- ✅ **Se migra:** todo `social_publisher` (feed Wix/FB/IG, historias, sepelios, farmacias,
  tapa, mail del PDF a clientes).
- ❌ **Queda local:** el **maquetador** (app .exe que armás a mano).
- ⏸️ **Por ahora afuera:** **WhatsApp** (`whatsapp_diario`) — se decide más adelante.

## Arquitectura nueva
```
PC (vos)                         Google Drive               VPS gratis (Linux, 24/7)
─────────                        ────────────               ────────────────────────
Armás las notas .docx + fotos →  Carpeta "Diario/"   ←sync─  rclone baja la carpeta del día
y la tapa/PDF, y los guardás     (NOTAS AUTOMATICAS,         cron dispara a la hora exacta
en la carpeta de Drive           DIARIO PDF)                 python main.py --run-now ...
                                                             → publica en Wix / FB / IG
```
La PC solo **produce y sube archivos** (vía Google Drive para Escritorio). El **server**
hace todo lo demás, esté tu PC prendida o no.

## Piezas (todas gratis)
| Necesidad | Solución gratis |
|---|---|
| Máquina 24/7 | **Oracle Cloud — Always Free** (VM Ubuntu, gratis para siempre) |
| Llevar las notas al server | **rclone** + **Google Drive** (15 GB gratis) |
| Programar horarios | **cron** (reemplaza al Programador de Tareas de Windows) |
| Secretos (`.env`) | Archivo en el server con permisos `600` (no se sube a ningún lado) |

> Alternativa sin servidor: **GitHub Actions** (cron gratis, cero mantenimiento). Contras:
> los *ledgers* anti-repetición hay que persistirlos commiteándolos de vuelta, y el horario
> del cron puede atrasarse 5–15 min. Para horarios exactos y estado en disco, el VPS gana.

## Por qué entra en el server más chico
`requirements.txt` = `requests, Pillow, python-docx, openpyxl, beautifulsoup4, lxml,
qrcode, APScheduler`. **No hay navegador** (Playwright es del maquetador, que no migra).
Con ~1 GB de RAM sobra.

## Pasos de implementación

### Fase 0 — Google Drive como puente (en la PC)
1. Instalar **Google Drive para Escritorio** logueado con `dlc.chivilcoy@gmail.com`.
2. Crear en Drive una carpeta `Diario/` con `NOTAS AUTOMATICAS/` y `DIARIO PDF/` adentro.
3. Cambiar tu rutina: en vez de guardar las notas/fotos/tapa/PDF en el Escritorio, guardarlas
   (o copiarlas) en esa carpeta de Drive. Drive las sincroniza solo.
   - ⚠️ TODO lo que el server necesita leer (notas, fotos, tapa, PDF de clientes) tiene que
     vivir en esa carpeta de Drive.

### Fase 1 — Crear el VPS gratis
1. Cuenta en **Oracle Cloud** (pide tarjeta para validar, **no cobra**; elegir "Always Free").
2. Crear una **VM Always Free** con **Ubuntu 22.04**, en una región cercana (ej. São Paulo).
3. Guardar la clave SSH para entrar.

### Fase 2 — Instalar el publicador en el server
```bash
sudo apt update && sudo apt install -y python3-venv python3-pip git fonts-dejavu fonts-liberation
git clone <repo de social_publisher>        # o subirlo por scp (sin el .env)
cd social_publisher && python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```
- ⚠️ **Fuentes:** `story_image.py` usa rutas de fuente de Windows (`C:\Windows\Fonts\...`).
  En Linux hay que **instalar fuentes** (arriba: DejaVu/Liberation) y **ajustar esas rutas**
  en `story_image.py` (o confiar en el fallback, pero queda más feo). Afecta a las imágenes
  9:16 (historias/sepelios/farmacias/tapa); el feed con foto real no se ve afectado.

### Fase 3 — Conectar Google Drive (rclone)
```bash
sudo -v ; curl https://rclone.org/install.sh | sudo bash
rclone config        # remote tipo "drive", auth headless (pega el token desde tu PC)
```
Probar: `rclone lsd gdrive:Diario`

### Fase 4 — Configurar el `.env`
- Copiar el `.env` al server (scp), `chmod 600 .env`. **Nunca** subirlo a git.
- Ajustar las rutas a la **ruta Linux sincronizada**, por ejemplo:
  - `POSTS_FOLDER=/home/ubuntu/diario/NOTAS AUTOMATICAS`
  - `TAPA_FOLDER=/home/ubuntu/diario/DIARIO PDF`
  - `MAIL_PDF_PATH=/home/ubuntu/diario/DIARIO PDF/diario_hoy.pdf`
- El resto del `.env` (claves Wix/FB/IG, horarios, ALLOWED_PAGES, etc.) queda **igual**.

### Fase 5 — Programar con cron
Un wrapper que **primero baja la carpeta del día desde Drive** y después publica:
```bash
# /home/ubuntu/correr.sh
#!/bin/bash
cd /home/ubuntu/social_publisher
rclone sync gdrive:Diario /home/ubuntu/diario      # trae notas/fotos/tapa/PDF
./venv/bin/python main.py "$@" >> logs/cron.log 2>&1
```
`crontab -e` (con zona horaria Argentina; ver nota):
```
CRON_TZ=America/Argentina/Buenos_Aires
0  7 * * 1-5 /home/ubuntu/correr.sh --run-now --pages 3,5,7
15 7 * * 1-5 /home/ubuntu/correr.sh --news-stories
0 13 * * 1-5 /home/ubuntu/correr.sh --run-now --pages 8,9
30 13 * * 1-5 /home/ubuntu/correr.sh --yt-notes
0  8 * * 1-5 /home/ubuntu/correr.sh --farmacias
# (replicar el resto de las tareas de Windows: --yt-live 10:45, --canal-story 17:00, etc.)
```
- Asegurar la zona horaria del server: `sudo timedatectl set-timezone America/Argentina/Buenos_Aires`.

### Fase 6 — Probar y cortar lo local
1. En el server: `./correr.sh --dry-run` y revisar `logs/cron.log` + `historias_preview/`.
2. Una corrida real controlada (confirmando que publica bien).
3. **Deshabilitar las Tareas Programadas de Windows** (`Disable-ScheduledTask ...`) para que
   **no se publique dos veces** (local + nube). Este es el paso crítico al hacer el corte.

## Cuidados
- **No duplicar:** mientras pruebo en la nube, las tareas locales quedan activas; recién al
  confirmar, se apagan las locales.
- **Ledgers:** `.publicado.json`, `.historias.json`, etc. quedan en el disco del VPS y persisten
  entre corridas (igual que hoy en local). No se mezclan con los de tu PC.
- **Zona horaria:** clave para que 7:00/13:00 sean hora de Argentina.
- **Seguridad:** `.env` solo en el server (chmod 600); nunca en git ni en Drive.

## Costo total
**$0.** Oracle Always Free (VM gratis para siempre) + Google Drive 15 GB gratis + rclone/cron
(open source). Único "costo": validar tarjeta en Oracle (no cobra).

## Decisiones abiertas
- Confirmar región del VPS (São Paulo suele ser la más cercana con buena latencia).
- Confirmar que 15 GB de Drive alcanzan (las notas/fotos diarias son livianas; el histórico se
  puede ir limpiando).
- WhatsApp: si más adelante se quiere migrar, necesita Chromium en el server (otro plan).
