# Programa de Corresponsales «Chivilcoy en Acción»

Recepción de videos de vecinos por **WhatsApp (API oficial / Cloud API de Meta)** → formulario
con datos + **autorización legal** → depósito en la carpeta de Drive del desgrabador → **nota web +
reel con firma de corresponsal** (todo el back-end que ya existe) → aprobación del editor →
publicación. A fin de mes, ranking y premios (Etapa 2).

> El número del Diario (`2346529064`) se **migra a la Cloud API** y queda **dedicado a recibir
> corresponsales**. ⚠️ Al migrar, ese número **deja de poder mandar al grupo y al canal** de
> WhatsApp (la Cloud API no manda a grupos/canales). Por eso hay que **apagar las tareas viejas**
> (paso 6).

## Qué ya está hecho (código)
- **Firma del reel**: `video.to_vertical_reel(..., firma=)` estampa la banda inferior; lo activa
  `transcriber.py` cuando el video trae `contexto.txt` con `ORIGEN: corresponsal-*`.
- **Datos del corresponsal**: `transcriber._leer_contexto()` parsea el `contexto.txt`; se guardan
  `corresponsal_nombre/celular/lugar/autorizacion` en el ledger `.videos_contabilidad.json` y se
  usan en el mail "Nota por revisar". Los IDs de los reels (`ig_media_id`, `fb_video_id`) se
  guardan al publicar (para el ranking).
- **Excel base de datos**: `reporte.py` (`--videos-report`) agrega la hoja **Colaboradores**
  (Nombre · Celular · Lugar · Notas · Publicadas).
- **Webhook**: `supabase/functions/corresponsales-webhook/index.ts` (formulario + autorización +
  subida a Drive). Tablas en `supabase/migrations/0001_corresponsales.sql`.

## Setup (pasos manuales, una sola vez)

### 1. Meta Business + WhatsApp Cloud API
1. En [business.facebook.com](https://business.facebook.com) → **verificar el negocio** (Diario La
   Campaña).
2. En [developers.facebook.com](https://developers.facebook.com) → **crear App** tipo *Business* →
   agregar el producto **WhatsApp** → crear/asociar la **WABA**.
3. **Registrar/migrar el número `2346529064`** a la WABA (te pide un código por SMS/llamada).
4. Anotar: **Phone Number ID** y **WABA ID**. Generar un **token permanente** (Business Settings →
   *System users* → token con permisos `whatsapp_business_messaging` + `whatsapp_business_management`).
5. Copiar el **App Secret** (App → Settings → Basic) y elegir un **Verify Token** cualquiera (una
   palabra secreta que vas a repetir en el paso 5).

### 2. Service account de Google (para depositar el video en Drive)
1. En [console.cloud.google.com](https://console.cloud.google.com) (proyecto de
   `dlc.chivilcoy@gmail.com`) → habilitar **Google Drive API**.
2. Crear una **Service Account** → crear una **clave JSON** y descargarla.
3. En Google Drive, **compartir la carpeta `videos notas actualidad`** con el mail de la service
   account (`...@...iam.gserviceaccount.com`), rol **Editor**.
4. Anotar el **ID de la carpeta** (está en la URL de la carpeta en Drive).

### 3. Crear las tablas en Supabase
```bash
supabase link --project-ref <tu-ref>
supabase db push        # aplica supabase/migrations/0001_corresponsales.sql
```
(O pegar el contenido del `.sql` en el **SQL Editor** de Supabase.)

### 4. Cargar los secrets y deployar el webhook
```bash
supabase secrets set \
  WHATSAPP_TOKEN="EAAG..." \
  WHATSAPP_PHONE_NUMBER_ID="1234567890" \
  WHATSAPP_VERIFY_TOKEN="mi-palabra-secreta" \
  WHATSAPP_APP_SECRET="abc123..." \
  GOOGLE_SA_JSON='{"type":"service_account", ... }' \
  DRIVE_CORRESPONSALES_FOLDER_ID="1AbC..."

supabase functions deploy corresponsales-webhook --no-verify-jwt
```
> `--no-verify-jwt` es necesario: Meta llama sin JWT de Supabase (la seguridad la da la firma
> `X-Hub-Signature-256`, que el webhook valida con `WHATSAPP_APP_SECRET`).

La URL queda: `https://<tu-ref>.functions.supabase.co/corresponsales-webhook`

### 5. Conectar el webhook en Meta
En la App → WhatsApp → **Configuration → Webhook**:
- **Callback URL**: la URL de arriba.
- **Verify token**: el mismo `WHATSAPP_VERIFY_TOKEN` del paso 4.
- Suscribir el campo **`messages`**.
Meta hace un GET de verificación; el webhook responde el *challenge* automáticamente.

### 6. Apagar las tareas viejas del número (ya no andan en la Cloud API)
En el **Programador de tareas de Windows**, deshabilitar las que usan `whatsapp_diario`:
- envío PDF 7:00 (`enviar.js`), noticias al canal (`enviar-noticias.js`), vivo 10:35
  (`enviar-vivo.js`), videos 13:30 (`enviar-videos.js`), borrar PDF 23:59 (`borrar-pdf.js`).
- El código queda; solo se apagan los disparadores.

### 7. (Opcional) Firma a medida
En el `.env` del publicador y en el secret `ENV_FILE` de GitHub:
`CORRESPONSALES_FIRMA=Material enviado por ...` (si se deja vacío usa el texto por defecto).

## Probar
1. **Webhook**: en el panel de Meta debe quedar verificado (tilde verde).
2. **Flujo**: desde otro WhatsApp, mandar un **video** al número → el bot pide Nombre → Celular →
   Lugar → Descripción → autorización. Responder **ACEPTO**.
3. **Drive**: aparece la subcarpeta `corresponsal_<fecha>_<nombre>` con `contexto.txt` + video en
   `videos notas actualidad`; en Supabase, fila nueva en `corresponsales_colaboradores`.
4. **Pipeline**: el Apps Script del desgrabador dispara → llega el mail **"Nota por revisar"** (con
   el nombre del corresponsal) → aprobar → publica la nota Wix + el **reel firmado** a FB/IG/YouTube.
5. **Excel**: `python main.py --videos-report --dry-run --mes <YYYY-MM>` → hoja **Colaboradores**.

## Etapa 2 — Ranking y premios (pendiente)
Con `ig_media_id`/`fb_video_id` + `corresponsal_nombre` que ya se guardan: un `ranking.py`
(`--corresponsales-ranking`, 1° de cada mes) junta **Wix `metrics.views`** + **Meta Insights** de
cada reel → puntaje (cantidad de notas + engagement) → define 1°/2°/3° ($100k/$50k/$25k) y publica
un ranking transparente.
