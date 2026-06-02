# Social Media Auto-Publisher — Diario La Campaña
## Contexto del proyecto

Sistema de automatización que publica las notas del diario en Wix, Facebook,
Instagram y X (Twitter) todos los días a las **7:00 AM**.

---

## Estructura de carpetas del usuario

```
C:\Users\Diario\Desktop\NOTAS AUTOMATICAS\
└── diario para 29 de mayo\          ← carpeta de la edición del día
    └── diario para 29 de mayo\      ← a veces hay un nivel extra de carpeta
        ├── la pagina 2\
        │   ├── nota.docx
        │   └── foto.png
        ├── la pagina 3\
        ├── la pagina 5\
        ├── la pagina 7\
        └── la pagina 9\
```

El nombre de la carpeta de edición sigue el patrón:
`"diario para [el] DD de MMMM"` (con o sin "el", mes en español).

---

## Reglas de negocio críticas

### Plataformas (estado actual)
- **Wix Blog** → API automático ✅
- **Facebook** (página *diariodechivilcoy*) → API automático ✅
- **Instagram** (@dlcchivilcoy) → API automático ✅
- **X (Twitter)** → **manual desde Wix** (Marketing → Marketing en redes sociales).
  El plan gratis de Wix permite 1 cuenta conectada = X. No es automático, se postea a mano.
  Wix NO tiene acción de "compartir en redes" en Automatizaciones, por eso no se pudo automatizar gratis.
  El código de `platforms/twitter.py` quedó SIN usar (X no se llama desde `publisher.py`).

### Páginas a publicar
- `ALLOWED_PAGES = 2, 3, 5, 7, 8, 9` (en .env)
- Páginas **8 y 9** → categoría **Deportes** + **Inicio** en Wix
- Páginas **2, 3, 5, 7** → categoría **Locales** + **Inicio** en Wix
- **TODAS las notas se marcan `featured=True`** → la portada (Inicio) del sitio muestra
  solo las destacadas, así que sin featured no aparecían en Inicio. Ahora todas aparecen.

### Lógica de fechas (MUY IMPORTANTE)
- El sistema **solo publica la edición de HOY**.
- Detecta la carpeta cuyo nombre contiene la fecha de hoy (día + mes en español).
- Si no hay carpeta para hoy → no publica nada (no es un error).
- Si la carpeta del día anterior sigue en la carpeta, la **ignora** (no coincide con hoy).
- El ledger `.publicado.json` evita republicar notas ya publicadas aunque el sistema
  corra varias veces el mismo día.

### Emparejado nota↔foto
- Los nombres de archivo .docx y foto **no son idénticos** pero son parecidos:
  - "Florencia Salinardi.docx" ↔ "salinardi.png" (similitud por tokens)
  - "camion1.docx" ↔ "camion.png"
- Algoritmo: `SequenceMatcher` + bonus por tokens contenidos. Umbral mínimo: 0.40.

### Estructura de los .docx (formato periodístico)
```
línea 0: VOLANTA / CATEGORIA  (ej: "RUTA 5", "Fútbol", "Dirección de Género")
línea 1: TITULAR              (ej: "Triple choque de camiones en el km 138")
línea 2+: CUERPO DE LA NOTA
```
- **Título en Wix** = "volanta — titular"
- **Cuerpo en Wix** = titular + cuerpo completo
- **Texto en redes (FB/IG)** = bloque corto armado por `_social_caption()` en publisher.py:
  ```
  {emoji} {volanta}
  📰 {titular}

  📝 {resumen breve — primer párrafo, máx ~300 chars}

  🔗 Leé la nota completa 👉 {URL de Wix}

  {hashtags}
  ```
  - Emoji de categoría: ⚽ deportes / 📣 locales
  - Hashtags automáticos: siempre `#Chivilcoy #DiarioLaCampaña` + según categoría/tema
    (#Deportes #Fútbol #Básquet / #Noticias #Actualidad #Policiales #Política)
  - En Instagram los links NO son clickeables (limitación de IG), igual va el texto.

---

## Credenciales y configuración (.env)

```
# Wix
WIX_API_KEY=IST.eyJ...          ← nueva clave generada el 29/05/2026
WIX_SITE_ID=1b7c2923-...        ← ID del sitio diariolacampana.com.ar
WIX_MEMBER_ID=82cea546-...      ← ID del autor (tomado de posts existentes)
WIX_CAT_INICIO=9bcc12a0-...     ← categoría "Inicio"
WIX_CAT_LOCALES=4558c237-...    ← categoría "Locales"
WIX_CAT_DEPORTES=094f631e-...   ← categoría "Deportes"

# Facebook / Instagram (mismo token permanente, app "diario")
FACEBOOK_PAGE_ID=1456376377985890          ← página "Diario La Campaña De Chivilcoy"
INSTAGRAM_USER_ID=17841405971293744        ← @dlcchivilcoy
# Token permanente de página (no vence). App de Meta: "diario" (id 983887861017378)
# Permisos: pages_show_list, pages_read_engagement, pages_manage_posts,
#           instagram_basic, instagram_content_publish, business_management

# Twitter/X → NO se usa por API (ver arriba: X es manual desde Wix)
# Las claves quedan en .env pero publisher.py no llama a twitter.publish().

# ImgBB
IMGBB_API_KEY=f1fb42f5...        ← relay de imágenes para Wix e Instagram
```

---

## Wix — detalles técnicos

### Endpoints usados
- Importar imagen: `POST https://www.wixapis.com/site-media/v1/files/import`
  (⚠️ NO usar `media/v1/files/import` — ese da 404)
- Crear borrador: `POST https://www.wixapis.com/blog/v3/draft-posts`
- Publicar: `POST https://www.wixapis.com/blog/v3/draft-posts/{id}/publish`
- Borrar post: `DELETE https://www.wixapis.com/blog/v3/posts/{id}`

### Flujo de publicación
1. Subir imagen a ImgBB (URL pública temporal)
2. Importar al Media Manager de Wix → obtener `file_id`
3. Crear borrador con `title`, `memberId`, `categoryIds`, `featured`, `richContent`
4. Publicar el borrador

### Instagram
- Requiere imagen en **formato JPG** (el código convierte PNG→JPG automáticamente)
- El caption tiene límite de **2200 caracteres**
- **Proporción de aspecto resuelta** ✅: si la foto está fuera del rango que IG acepta
  (4:5 a 1.91:1), `_as_jpeg()` agrega **borde blanco** para encuadrarla SIN recortar.

---

## Historias (stories) en Instagram + Facebook
Además del feed, el sistema publica **Historias automáticas** (foto/miniatura + texto quemado, 9:16).
Las Historias por API **no llevan caption ni stickers**: por eso NO hay link tocable; se quema en la
imagen una invitación a entrar a la web (`STORY_SITE_URL`).

- **Noticias** (`--news-stories`, 07:15): una historia por cada nota de hoy (reusa `find_notes`).
  Foto + volanta + titular + resumen + *"Leé la nota completa en www.diariolacampana.com.ar"*.
  Ledger `.historias.json` (en POSTS_FOLDER) para no repetir.
- **YouTube vivo** (`--yt-live`, 10:35): si el canal está en vivo, miniatura + título + "Mirá el vivo".
- **YouTube notas** (`--yt-notes`, 13:30): historias de los videos del día, EXCLUYENDO el programa
  completo (`STORY_EXCLUDE_TITLE`, ej. "MAÑANA DEL CENTRO"). Ledger `youtube-historias.json`.

Archivos: `story_image.py` (compositor Pillow 9:16 → `historias_preview/`), `youtube.py` (RSS/vivo),
`stories.py` (orquestadores), `platforms/{instagram,facebook}.py::publish_story()`.
Probar sin publicar: `python main.py --news-stories --dry-run` (genera los JPG en `historias_preview/`).

⚠️ Facebook *photo_stories* por API es más nuevo y puede requerir elegibilidad extra de la página; si
falla, se loguea y sigue (Instagram no se ve afectado). El token de Meta ya tiene los permisos.

Tareas de Windows: `"Historias Noticias 0715"`, `"Historias YouTube Vivo 1035"`, `"Historias YouTube Notas 1330"`.

## Sepelios y Farmacias (scraping → muro + historia)
Dos automatizaciones diarias que publican en **Wix + Facebook + Instagram** (muro + historia 9:16).
Scraping con `utils/scrape.py` (User-Agent de navegador; dechivilcoy bloquea el UA por defecto).

- **Sepelios** (`--sepelios`, 21:00 — tarea `"Sepelios Chivilcoy 2100"`): scrapea las necrológicas de
  **San Nicolás** (`empresasannicolas.com/sepelios/`, `div.slide-content`) y **Visión**
  (`grupovisionargentina.com`, bloque "Necrológicas" de la home). **Solo Chivilcoy**. Un único posteo +
  historia con **solo los NUEVOS del día** (anti-repetición por nombre normalizado en `.sepelios.json`).
  El **posteo** incluye un **breve resumen de cada uno** (`detalle`): San Nicolás → "Falleció en {lugar} el
  {fecha}"; Visión → "Sepelio en {localidad} · {fecha}". La imagen se mantiene sobria (solo nombres).
  Módulo `sepelios.py`; imágenes `compose_sepelios_feed/story` en `story_image.py`.
- **Farmacias** (`--farmacias`, **08:00** — tarea `"Farmacias Turno 0800"`): el cronograma de turnos de
  `dechivilcoy.com.ar/farmacias/` es **una imagen mensual** (`TURNOS-{MES}-{AÑO}.jpg`) y el OCR NO es
  confiable. Por eso el cronograma vive curado en **`turnos_farmacias.json`** (día → terna de 3 farmacias;
  las 2 primeras 8:30→8:30, la última 8:30→22 hs). El listado (dirección/teléfono) sí se scrapea de la
  tabla `<li>`. Cada día busca la terna de hoy, le pega dirección/teléfono y **el horario específico de
  cada una** (las 2 primeras `8:30 a 8:30 hs (24 hs)`, la última `8:30 a 22 hs`) y publica. El horario se
  muestra resaltado (verde, vía `sub2` en `_compose_listado`) bajo cada farmacia en la imagen y en el
  texto del posteo. Ledger `.farmacias.json`
  (no repite el mismo día). **Al cambiar de mes** detecta que falta el cronograma del mes (o que cambió la
  imagen) → avisa en el log y NO publica datos sin verificar: hay que leer la imagen y cargar el mes nuevo
  en `turnos_farmacias.json` (~1 min). Módulo `farmacias.py`; imágenes `compose_farmacias_feed/story`.

Probar sin publicar: `python main.py --sepelios --dry-run` y `python main.py --farmacias --dry-run`
(generan los JPG en `historias_preview/`). `turnos_farmacias.json` SÍ se versiona; los ledgers no.
Horarios de las tareas: **Sepelios 21:00**, **Farmacias 08:00**.

## Tarea programada de Windows
- Nombre: `"Publicador Diario LC"`
- Horario: **todos los días a las 07:00**
- Comando: `run_publisher.bat` → `python main.py --run-now`
- Estado actual: **HABILITADA** ✅ — corre todos los días a las 07:00

Para habilitar/cambiar hora (con cmdlets, NO piden contraseña):
```powershell
$t = New-ScheduledTaskTrigger -Daily -At 7:00am
Set-ScheduledTask -TaskName "Publicador Diario LC" -Trigger $t
Enable-ScheduledTask -TaskName "Publicador Diario LC"
# (evitar `schtasks /change /enable` → pide la contraseña del usuario de forma interactiva)
```

---

## Comandos útiles

```powershell
cd C:\Users\Diario\social_publisher

# Ver qué se publicaría hoy SIN publicar nada
.\venv\Scripts\python.exe main.py --dry-run

# Publicar ahora mismo
.\venv\Scripts\python.exe main.py --run-now

# Publicar solo una página (ej: solo página 9)
# → cambiar ALLOWED_PAGES=9 en .env, correr, volver a poner 2,3,5,7,8,9

# Verificar credenciales
.\venv\Scripts\python.exe main.py --check-config

# Ver logs
notepad logs\publisher.log
```

---

## Pendientes
- [x] ~~Twitter/X por API~~ → se descartó; X es **manual desde Wix** (plan gratis = 1 cuenta)
- [x] ~~Instagram proporción de aspecto~~ → resuelto con borde blanco automático
- [x] ~~Habilitar tarea programada~~ → habilitada a las 07:00
- [x] ~~Redes apuntando a Radio del Centro~~ → cambiadas a Diario La Campaña
- [ ] Recordatorio operativo: postear X a mano desde Wix cuando se publique la edición
- [ ] Si algún día se quiere X automático: pasar la cuenta de developer.x.com al plan **Free**
      (gratis, 500 tweets/mes) y volver a habilitar `twitter.publish()` en publisher.py
