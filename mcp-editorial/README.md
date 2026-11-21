# Conector MCP del ingreso editorial (ChatGPT / Claude)

Permite que el editor, desde un chat de **ChatGPT** (modo desarrollador / Apps) o de **Claude**, mande una nota con
sus fotos, videos o audios al circuito de corresponsales del diario y consulte el recibo. Por detrás llama a la API
de ingreso editorial (`supabase/functions/editorial-ingest`, ver `EDITORIAL_INGEST.md`).

- **Herramientas:** `enviar_material` (texto + 1 a 8 archivos adjuntos en el chat + autorización expresa) y
  `consultar_envio` (el recibo por su id). El recibo confirma el **depósito**, no la publicación.
- **Credenciales en el servidor:** la clave de la API de ingreso vive solo en las variables de Vercel. ChatGPT recibe
  tokens propios de este conector (1 hora, renovables 30 días), firmados con `MCP_SIGNING_SECRET`.
- **Quién puede usarlo:** quien sepa la **clave de acceso del editor** (`MCP_ACCESS_KEY`), que se escribe una sola
  vez en la página de autorización al conectar el chat.
- **Por qué Vercel y no Supabase:** la página de autorización es HTML, y Supabase no deja servir HTML desde
  `*.supabase.co` sin dominio propio (pago). El resto (la API, la base y el Drive) sigue en Supabase.

## Cómo funciona

| Ruta | Para qué |
|---|---|
| `/.well-known/oauth-protected-resource` | ChatGPT descubre que hace falta OAuth y quién lo da |
| `/.well-known/oauth-authorization-server` | Metadatos OAuth (PKCE S256, registro dinámico, `iss`) |
| `/register` | Registro dinámico: solo acepta los retornos de ChatGPT y Claude |
| `/authorize` | Página donde el editor escribe la clave de acceso |
| `/token` | Canje del código (con PKCE) y renovación |
| `/mcp` | El servidor MCP (Streamable HTTP, respuestas JSON) |

Todo es **sin estado** (no usa base de datos): los clientes registrados, los códigos y los tokens van firmados.
- Para **cortar todos los accesos**: cambiar `MCP_SIGNING_SECRET` y volver a publicar (hay que reconectar el chat).
- Para **cortar las renovaciones** (por ejemplo si se filtró la clave): cambiar `MCP_ACCESS_KEY`.
- El código de autorización vale 2 minutos y exige PKCE.

Los archivos los baja de los enlaces temporales que da ChatGPT (`openai/fileParams`), reconoce el tipo **por los
bytes** (no por lo que diga el chat) y los manda sin recodificar. El id del envío sale del contenido: si el chat
reintenta el mismo envío, la API lo reconoce y no lo deposita dos veces.

## Variables (Vercel, Production)

| Variable | Qué es |
|---|---|
| `MCP_PUBLIC_URL` | La URL pública del conector, sin barra final (ej. `https://editorial-mcp.vercel.app`) |
| `MCP_SIGNING_SECRET` | Secreto al azar (≥ 32 caracteres) para firmar tokens |
| `MCP_ACCESS_KEY` | La clave que escribe el editor al conectar (≥ 12 caracteres) |
| `EDITORIAL_INGEST_URL` | `https://<proyecto>.supabase.co/functions/v1/editorial-ingest` |
| `EDITORIAL_INGEST_TOKEN` | El mismo valor que el secreto de Supabase |
| `MCP_FILE_HOSTS` | Opcional: dominios de donde se aceptan archivos (vacío = cualquiera por HTTPS) |
| `MCP_REDIRECTS_EXTRA` | Opcional: otros retornos OAuth permitidos, separados por coma |

`python tools/editorial_secretos.py` las carga todas sin mostrarlas (y también los secretos de Supabase).

## Conectarlo en ChatGPT

1. ChatGPT en la web → **Configuración → Apps → Configuración avanzada → Modo desarrollador** (activar).
2. **Crear app / conector** → URL: `https://<conector>/mcp` · Autenticación: **OAuth**.
3. Se abre la página «Ingreso editorial» → escribir la clave de acceso → **Autorizar**.
4. En un chat: adjuntar las fotos, escribir la nota y pedir «mandalo al diario». ChatGPT pide confirmación antes de
   usar `enviar_material`.

Para Claude: **Configuración → Conectores → Agregar conector personalizado** con la misma URL.

## Pruebas

```bash
node --test mcp-editorial/core.test.mjs
```

Recorren el OAuth completo (registro, clave incorrecta y correcta, PKCE, renovación, robo de refresh), el MCP
(`initialize`, `tools/list`, `tools/call`) y un envío de punta a punta contra la API de ingreso REAL (su núcleo,
con Drive y base en memoria). No usan credenciales ni red.
