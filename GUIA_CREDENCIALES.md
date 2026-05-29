# Guía para conseguir las credenciales

Hacé esto **una sola vez**. Cada valor que consigas, lo pegás en el archivo `.env`.
Te recomiendo empezar por las fáciles (ImgBB y X) para ganar confianza.

---

## 0. Primero: crear el archivo .env

En PowerShell:
```powershell
cd C:\Users\Diario\social_publisher
copy .env.example .env
notepad .env
```
Dejá el Bloc de notas abierto. Vas a ir pegando cada valor ahí.

---

## 1. ImgBB (5 minutos — lo más fácil)

Sirve para subir las imágenes temporalmente (lo necesitan Wix e Instagram).

1. Entrá a https://imgbb.com y creá una cuenta gratis.
2. Andá a https://api.imgbb.com → botón **"Get API key"**.
3. Copiá la clave y pegala en `.env`:
   ```
   IMGBB_API_KEY=tu_clave_aca
   ```

---

## 2. X / Twitter (15 minutos)

1. Entrá a https://developer.twitter.com y registrate como desarrollador (cuenta gratuita "Free").
2. En el **Developer Portal** creá un **Project** y dentro un **App**.
3. En la configuración de la App → **User authentication settings** → **Set up**:
   - App permissions: elegí **Read and Write**.
   - Type of App: **Web App / Automated App or Bot**.
   - Callback URL: poné `https://localhost` (no se usa, pero es obligatorio).
4. Andá a la pestaña **Keys and Tokens** y generá:
   - **API Key** y **API Key Secret** → estos son `TWITTER_API_KEY` y `TWITTER_API_SECRET`
   - **Access Token** y **Access Token Secret** → estos son `TWITTER_ACCESS_TOKEN` y `TWITTER_ACCESS_TOKEN_SECRET`

   ⚠️ Importante: generá los Access Token DESPUÉS de poner permisos "Read and Write".
   Si los generaste antes, regeneralos.

```
TWITTER_API_KEY=...
TWITTER_API_SECRET=...
TWITTER_ACCESS_TOKEN=...
TWITTER_ACCESS_TOKEN_SECRET=...
```

---

## 3. Facebook + Instagram (juntas, 30 minutos — las más laboriosas)

Facebook e Instagram comparten el mismo sistema (Meta). Requisitos previos:
- Tener una **Página de Facebook** (no un perfil personal).
- Tener una cuenta de **Instagram Profesional** (Creador o Empresa) **vinculada a esa página**.

### 3a. Crear la app de Meta
1. Entrá a https://developers.facebook.com → **My Apps** → **Create App**.
2. Elegí el tipo **Business**.
3. Agregá los productos: **Facebook Login** y **Instagram Graph API**.

### 3b. Conseguir el token y los IDs (con Graph API Explorer)
1. Andá a https://developers.facebook.com/tools/explorer
2. Arriba a la derecha, seleccioná tu App.
3. En "User or Page" elegí **Get Page Access Token** y seleccioná tu página.
4. Agregá estos permisos (botón "Add permissions"):
   - `pages_manage_posts`
   - `pages_read_engagement`
   - `instagram_basic`
   - `instagram_content_publish`
5. Click en **Generate Access Token** y aceptá los permisos en la ventana que abre.
6. Copiá ese token largo → es tu `FACEBOOK_PAGE_ACCESS_TOKEN` (y también `INSTAGRAM_ACCESS_TOKEN`, es el mismo).

### 3c. Conseguir el ID de la página de Facebook
En el Graph API Explorer, en la barra de consulta escribí `me/accounts` y dale "Submit".
Te devuelve un JSON con tus páginas; copiá el campo `id` → es tu `FACEBOOK_PAGE_ID`.

### 3d. Conseguir el ID de Instagram
En la misma consulta, buscá el campo `instagram_business_account` → su `id` es tu `INSTAGRAM_USER_ID`.
(Si no aparece, asegurate de que tu Instagram esté vinculado a la página y sea cuenta Profesional.)

```
FACEBOOK_PAGE_ID=...
FACEBOOK_PAGE_ACCESS_TOKEN=...
INSTAGRAM_USER_ID=...
INSTAGRAM_ACCESS_TOKEN=...   (el mismo token de Facebook)
```

### 3e. (Recomendado) Token de larga duración
El token que generás dura solo ~2 horas. Para que dure 60 días, usá la herramienta
"Access Token Debugger" → https://developers.facebook.com/tools/debug/accesstoken/
Pegá tu token y click en **Extend Access Token**.
Para uno permanente, hay que usar un "System User" en Business Manager (más avanzado).

---

## 4. Wix (15 minutos)

1. Entrá a https://manage.wix.com y seleccioná tu sitio.
2. Andá a **Configuración** → **Avanzado** → **Claves API**
   (o directo: https://manage.wix.com/account/api-keys).
3. Click en **Generar clave API**.
4. Dale permisos de **Blog** (lectura y escritura).
5. Copiá la clave → es tu `WIX_API_KEY`.
6. El **Site ID** lo ves en la URL de tu panel después de `dashboard/`,
   o en la sección de la API key. Pegalo en `WIX_SITE_ID`.

```
WIX_API_KEY=...
WIX_SITE_ID=...
```

---

## 5. Verificar que todo esté cargado

Guardá el `.env` y corré:
```powershell
.\venv\Scripts\python.exe main.py --check-config
```
Si ves `[OK] Todas las variables...` → ¡listo!

---

## 6. Primera prueba real

1. Poné en tu carpeta de posts dos archivos con el mismo nombre, por ejemplo:
   - `prueba.jpg` (una foto)
   - `prueba.txt` (con el texto; opcional primera línea `TITLE: Mi primer post`)
2. Corré:
   ```powershell
   .\venv\Scripts\python.exe main.py --run-now
   ```
3. Revisá `logs\publisher.log` para ver qué pasó en cada plataforma.
4. Si todo salió bien, los archivos se mueven solos a la subcarpeta `published\`.

---

## 7. Dejarlo automático (todos los días a las 10am)

```powershell
.\venv\Scripts\python.exe main.py
```
Dejalo corriendo. O usá el Programador de tareas de Windows con `run_publisher.bat`.
