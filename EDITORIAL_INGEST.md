# Ingreso editorial directo — Diario La Campaña / Radio del Centro

Entrada HTTP autenticada para material preparado por la redacción o por un asistente.
Evita depender de una sesión de WhatsApp Web. El bot de vecinos conserva su recorrido.

**Estado de este cambio:** función, migración, cliente y pruebas preparados; no desplegados.
El conector para operarlo desde un chat (ChatGPT o Claude) está en `mcp-editorial/` (ver su README):
invoca esta API con sus credenciales en el servidor. El cliente incluido permite probar el contrato sin él.

## Comportamiento

1. `POST` multipart con el texto completo, la autorización expresa y de 1 a 8 archivos.
2. La identidad del editor se toma de secretos del servidor, no del contenido del pedido.
3. Se reserva un UUID en Supabase antes de hacer cambios en Drive.
4. Se crea una carpeta en **staging**, fuera del árbol que vigila el desgrabador.
5. Se suben `contexto.txt`, `ingreso.json` (auditoría y SHA-256) y los archivos sin recodificarlos.
6. Se mueve la carpeta completa a `DRIVE_CORRESPONSALES_FOLDER_ID`.
7. Se devuelve un recibo persistente; `GET ?id=<uuid>` consulta el mismo envío.

El `contexto.txt` usa `ORIGEN: corresponsal-editorial-directo`, nombre, celular,
descripción, autorización y numeración por llegada, compatibles con `transcriber.py`.
Los nombres llevan el orden de recepción mezclando fotos/videos/audios.

**El recibo confirma depósito, no publicación.** Sigue el tratamiento de corresponsales ya
configurado en el desgrabador, incluida la lista `CORRESPONSALES_DIRECTO` si el número del
editor pertenece a ella. El texto enviado es contexto para Gemini: esta entrada no garantiza
su conservación palabra por palabra. Para conservar una nota final exactamente, hace falta
un modo editorial adicional en el desgrabador; no se cambió ese comportamiento aquí.

## Configuración y despliegue

Aplicar `supabase/migrations/0008_editorial_ingresos.sql`. La tabla tiene RLS activado, sin
políticas públicas, y permisos solo para `service_role`.

En Supabase, configurar como secretos:

- `EDITORIAL_INGEST_TOKEN`: credencial aleatoria de al menos 32 caracteres, exclusiva de esta API.
- `EDITORIAL_AUTHOR_NAME`: nombre del editor autorizado.
- `EDITORIAL_AUTHOR_PHONE`: celular completo, solo dígitos, como lo espera el pipeline existente.
- `EDITORIAL_STAGING_FOLDER_ID`: carpeta temporal privada **fuera de la carpeta vigilada y de
  todas sus subcarpetas**, en el mismo Drive que la carpeta destino. No usar `APROBADAS`.

- `EDITORIAL_TARGET_FOLDER_ID` (opcional, para el ENSAYO): otra carpeta fuera del árbol vigilado. Mientras esté
  cargada, los envíos terminan ahí y el desgrabador no los toca. Al pasar a producción se quita
  (`npx supabase secrets unset EDITORIAL_TARGET_FOLDER_ID`) y el destino vuelve a ser la carpeta de corresponsales.

`python tools/editorial_secretos.py` carga estos secretos sin mostrarlos, también los del conector MCP
(`mcp-editorial/`, en Vercel), hace la carga de prueba a la carpeta de ensayo y, si sale bien, pasa a producción.

Reutiliza los secretos existentes `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`,
`GOOGLE_OAUTH_REFRESH_TOKEN`, `DRIVE_CORRESPONSALES_FOLDER_ID`, `SUPABASE_URL` y
`SUPABASE_SERVICE_ROLE_KEY`. No se entregan estas claves al cliente.

Desplegar solo la nueva función:

```bash
supabase functions deploy editorial-ingest --no-verify-jwt
```

La verificación JWT del gateway se desactiva **solo en esta función** porque valida su propio
Bearer obligatorio antes de leer el cuerpo. Con token ausente o corto responde 503 y no acepta
cargas. No cambiar el despliegue ni la verificación de firmas del webhook de Meta.

## Contrato de la API

Cabeceras para `POST`:

- `Authorization: Bearer <EDITORIAL_INGEST_TOKEN>`
- `Idempotency-Key: <UUID>`
- `Content-Type: multipart/form-data; boundary=...`

Campos:

- `metadata`: JSON con `descripcion` (texto completo), `autorizacion: "ACEPTO"` y, opcionalmente,
  `lugar`, `habla` (nombre/cargo de cada audio) y `fuente_url` (referencia interna).
- `file`: un campo por archivo, en el orden deseado. El primer visual es la portada.

JPEG, PNG, WebP, MP4, MOV moderno con cabecera ftyp, WebM, MP3, M4A, OGG y WAV.
Se validan tipos MIME, cabeceras básicas y tamaños. Un audio necesita una imagen/video y `habla`.
El total multipart admite hasta **32 MiB** por pedido. No se comprime nada para ajustarse al límite;
para videos mayores se necesita una ruta de carga por partes antes de ampliar este servicio.

`201 received`: depósito completo. `200 received`: mismo envío ya recibido.
`202 processing`: consultar el recibo; no significa publicación ni error definitivo.
`409`: UUID reutilizado con otro contenido. `502 failed`: carga incompleta aislada en staging.
El mismo UUID nunca inicia otro depósito. Los errores no devuelven respuestas externas con claves.

Tras un timeout en el movimiento final a Drive, `GET` verifica el padre de la carpeta y reconcilia
el recibo si la carpeta ya llegó al destino. Si el proceso muere antes del movimiento, queda
`processing` en staging: revisar esa carpeta antes de crear otro envío. No se reintenta a ciegas.

## Cliente de prueba

Python estándar, sin n8n, Make ni librerías adicionales. Configurar por el gestor de secretos o
el entorno local `EDITORIAL_INGEST_URL` (URL HTTPS de la función desplegada) y
`EDITORIAL_INGEST_TOKEN`. No pegar claves en el chat ni versionarlas.

```bash
python tools/editorial_ingest.py --nota /ruta/nota.txt \
  --archivo /ruta/foto1.jpg --archivo /ruta/foto2.png \
  --lugar Chivilcoy --acepto --recibo /ruta/recibo-nota-001.json

python tools/editorial_ingest.py --estado --recibo /ruta/recibo-nota-001.json
```

Para reintentar el mismo contenido, reutilizar el mismo archivo de recibo. Para otra nota, usar
otro archivo de recibo. El cliente persiste el UUID antes de la red, sin guardar el token.

## Validación sin publicar

```bash
node --test supabase/functions/editorial-ingest/core.test.mjs
python -m py_compile tools/editorial_ingest.py
```

Las pruebas no usan credenciales ni servicios reales. Cubren autorización, suplantación de autor,
bytes originales, orden, rótulos de contexto, concurrencia, fallos parciales, recuperación del recibo,
límites y el contrato HTTP de Drive. La migración y el despliegue requieren una prueba real en un
proyecto/carpeta de ensayo antes de activar el ingreso de producción.

Referencias de despliegue y límites:
- https://supabase.com/docs/guides/functions/function-configuration
- https://supabase.com/docs/guides/functions/limits
- https://developers.google.com/workspace/drive/api/guides/folder
