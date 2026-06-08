# Deploy del publicador en el server (Oracle Ubuntu) — guía paso a paso

Esta guía corre **en el servidor Linux** (Oracle Cloud Always Free, Ubuntu 22.04).
Las notas/fotos/tapa/PDF llegan por **Google Drive** (carpeta `Diario/`) y se publican con **cron**.

> Antes de empezar necesitás: (1) la VM creada y acceso por SSH, (2) el repo privado de GitHub
> con este proyecto, (3) la carpeta `Diario/` en Drive con `NOTAS AUTOMATICAS/` y `DIARIO PDF/`.

---

## 1) Sistema base
```bash
sudo timedatectl set-timezone America/Argentina/Buenos_Aires
sudo apt update
sudo apt install -y python3-venv python3-pip git fonts-liberation fonts-dejavu-core
```
> Las fuentes (`fonts-liberation`) son las que usan las imágenes 9:16 (historias, farmacias,
> canal, tapa). Sin ellas, los textos salen con una tipografía fea de respaldo.

## 2) Bajar el código e instalar dependencias
```bash
cd /home/ubuntu
git clone <URL-DEL-REPO-PRIVADO> social_publisher
cd social_publisher
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
mkdir -p logs /home/ubuntu/diario
chmod +x correr.sh
```

## 3) Conectar Google Drive (rclone)
```bash
sudo -v ; curl https://rclone.org/install.sh | sudo bash
rclone config
#   n) New remote
#   name> gdrive
#   Storage> drive            (Google Drive)
#   client_id/secret> Enter (vacío)
#   scope> 1  (acceso completo) o 2 (solo lectura) -- alcanza con lectura
#   Edit advanced config> n
#   Use auto config> n        (IMPORTANTE: es headless, sin navegador en el server)
#       -> te da un comando "rclone authorize ..." para correr en TU PC (con navegador),
#          te logueás con dlc.chivilcoy@gmail.com y pegás el token de vuelta en el server.
#   Shared Drive> n
rclone lsd gdrive:Diario      # debe listar: NOTAS AUTOMATICAS y DIARIO PDF
```

## 4) Configurar el `.env`
Copiá el `.env` de la PC al server por SCP (NUNCA por git). Desde tu PC (PowerShell):
```powershell
scp "C:\Users\Diario\social_publisher\.env" ubuntu@<IP-DEL-SERVER>:/home/ubuntu/social_publisher/.env
```
Y en el server, asegurá permisos y ajustá SOLO las rutas a las de Linux:
```bash
chmod 600 .env
nano .env
```
Cambiar estas dos líneas (el resto —claves Wix/FB/IG, ALLOWED_PAGES, delays— queda IGUAL):
```
POSTS_FOLDER=/home/ubuntu/diario/NOTAS AUTOMATICAS
TAPA_FOLDER=/home/ubuntu/diario/DIARIO PDF
```

## 5) Probar SIN publicar
```bash
./correr.sh --dry-run
cat logs/cron.log          # revisar que sincronizó Drive y emparejó notas+fotos sin error
ls historias_preview/      # generar/ver una historia de prueba con la tipografía correcta
```

## 6) Programar el cron
```bash
crontab crontab.server.txt   # instala los horarios (hora de Argentina)
crontab -l                   # verificar
```

## 7) El corte (cuando todo funcione) — EN LA PC, no en el server
Recién cuando confirmes que el server publica bien, deshabilitar en Windows las tareas que
migraron, para **no publicar dos veces**. En PowerShell de la PC:
```powershell
"Tapa Diario 0000","Publicador Manana 0700","Historias Noticias 0715","Farmacias Turno 0800",
"Historias YouTube Vivo 1045","Publicador Tarde 1300","Historias YouTube Notas 1330",
"Historia Canal WSP 1700" | ForEach-Object { Disable-ScheduledTask -TaskName $_ }
```
> Dejar ACTIVAS: `Diario Mail Clientes 0700` y todas las tareas de WhatsApp (node.exe).

---

## Actualizar el código más adelante
```bash
cd /home/ubuntu/social_publisher
git pull
./venv/bin/pip install -r requirements.txt   # solo si cambió requirements.txt
```

## Notas
- **Ledgers** (`.publicado.json`, `.historias.json`, etc.) viven en el disco del server y
  arrancan vacíos. Hacé el primer corte con cuidado (o `--dry-run`) para no re-publicar lo del día.
- La **tapa** (00:00) necesita la imagen en Drive *antes* de medianoche.
- Logs de cada corrida: `logs/cron.log`.
