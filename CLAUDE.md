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

### Páginas a publicar
- `ALLOWED_PAGES = 2, 3, 5, 7, 8, 9` (en .env)
- Páginas **8 y 9** → categoría **Deportes** + **Inicio** en Wix (featured=True)
- Páginas **2, 3, 5, 7** → categoría **Locales** + **Inicio** en Wix

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
- **Texto en redes** = titular + cuerpo completo (Instagram recorta a 2200 chars)

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

# Facebook / Instagram (mismo token)
FACEBOOK_PAGE_ID=101271341650483           ← página "Radio del Centro"
INSTAGRAM_USER_ID=17841427698320458        ← @radiodelcentro
# Token permanente de página (no vence)

# Twitter/X
# ⚠️ El Access Token generado antes estaba en solo lectura (read-only).
# Hay que regenerarlo en developer.twitter.com con permisos "Read and Write".
# La API Key y API Secret están bien — solo regenerar Access Token + Secret.

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
- Algunas fotos pueden fallar por **proporción de aspecto** (muy alargadas)
  → el código debería auto-recortar (pendiente mejorar)

---

## Tarea programada de Windows
- Nombre: `"Publicador Diario LC"`
- Horario: **todos los días a las 07:00**
- Comando: `run_publisher.bat` → `python main.py --run-now`
- Estado actual: **DESHABILITADA** (habilitar cuando esté todo probado)

Para habilitar:
```powershell
schtasks /change /tn "Publicador Diario LC" /enable
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
- [ ] **Twitter/X**: regenerar Access Token con "Read and Write" en developer.twitter.com
- [ ] **Instagram**: mejorar recorte automático de imágenes con proporción inválida
- [ ] **Habilitar tarea programada** cuando estén todos los errores resueltos
