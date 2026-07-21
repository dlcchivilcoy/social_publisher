# TikTok — subir el reel a la bandeja del creador

Estado: **código listo y probado**, esperando el **review de Producción** de TikTok.
Corre **LOCAL** (el refresh token rota y no puede vivir en el repo público).

## Cómo funciona

`tiktok_reel.py` busca en el GitHub Release `reel-latest` el asset `reel*.mp4` **más
nuevo** (venga del reel de las 5 más leídas o de una desgrabación), lo baja y lo manda
a la **bandeja** de TikTok. Después abrís la app, le ponés la canción trending y publicás.

- No sube dos veces el mismo reel (ledger `.tiktok_reel.json`, por nombre de asset).
- No sube reels viejos: si el más nuevo tiene más de `MAX_DIAS_REEL` (2) días, no hace
  nada. Para forzarlo: `python tiktok_reel.py --force`.

Piezas: `platforms/tiktok.py` (token + `upload_to_inbox`), `tiktok_auth.py` (OAuth, una
vez), `tiktok_reel.py`, `run_tiktok_reel.bat`.

## Lo que falta: el review de Producción

TikTok **no** tiene atajo tipo Meta: toda app que postee automático pasa por review.
En sandbox la subida funciona del lado API, pero **el borrador no aparece en el celular**.

### 1. App details (developers.tiktok.com → app «automatizacion reels» → Production)

| Campo | Qué poner |
|---|---|
| App name | Diario La Campaña — Publicador |
| Category | News |
| Terms of Service URL | `https://www.diariolacampaña.com.ar/terminos` |
| Privacy Policy URL | `https://www.diariolacampaña.com.ar/privacidad` |
| App icon | `tiktok_app_icon.png` (512×512, la «C» naranja de la web) |

> ⚠️ NO uses `logo.png`: es un banner de 6170×830 y TikTok pide el ícono **cuadrado**.
> `tiktok_app_icon.png` es una copia de `icon-512.png` de la web (mismo ícono de la PWA).

> Si el formulario rechaza la ñ, usá la forma punycode:
> `https://www.xn--diariolacampaa-2nb.com.ar/terminos` y `.../privacidad`.
> Las dos resuelven al mismo sitio.

**Descripción** (pegar tal cual; el review se hace en inglés):

> Internal publishing tool for Diario La Campaña, a local news outlet in Chivilcoy,
> Argentina. The app uploads the newspaper's own short vertical videos — news reels
> produced by our newsroom — to the drafts inbox of our own TikTok account
> (@diarioyradio). Nothing is posted publicly by the app: our editor opens TikTok,
> adds music and publishes manually. It is used only by our newsroom, on our own
> account, with content we produce ourselves. It does not access third-party accounts
> and has no public sign-up.

**Scopes y justificación:**

- `video.upload` — *Uploads our own news videos to our own account's drafts inbox. The
  editor reviews and publishes them manually from the TikTok app.*
- `user.info.basic` — *Only to confirm the authenticated account is our own newsroom
  account before uploading.*

### 2. Video demo de la integración

Grabá la pantalla (2–3 min, sin cortes, mostrando lo que pasa):

1. La terminal: corré `python tiktok_reel.py --force` y que se vea el log
   («Bajando… / Subiendo a la bandeja de TikTok… / Listo»).
2. El celular: abrí TikTok → notificación → el borrador aparece en la bandeja.
3. Abrí el borrador, agregale música y publicá **a mano** — esto es clave: le muestra
   al revisor que la app **no publica sola**.
4. Mostrá la nota publicada en la web del diario, para que se vea que el video es propio.

> ⚠️ Del punto 2 **no vas a poder grabar nada mientras estés en sandbox** (ese es
> justamente el bloqueo). Grabá 1, 3 y 4 mostrando el reel ya publicado desde la app, y
> aclaralo en las notas del review.

### 3. Después de que aprueben

1. Producción te da **otra client key** (la de sandbox es `sbawn6kn6j52larqzo`).
   Actualizá `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET` en el `.env` local, y **también
   el redirect**:

   ```
   TIKTOK_REDIRECT_URI=http://localhost:8723/callback/
   ```

   ⚠️ La solapa **Desktop** del portal NO acepta direcciones web (solo `localhost` o
   `127.0.0.1`), así que en Producción el redirect es local, no la web del diario.
   `tiktok_auth.py` detecta solo cuál de los dos es: si es `localhost` levanta un
   servidor y toma el código automáticamente; si es `https://` te pide pegar la URL
   (que es como sigue andando el sandbox actual).
2. Re-autorizá: `python tiktok_auth.py` (se abre el navegador, una sola vez).
3. Creá la tarea de Windows diaria (~20:15). En PowerShell **como administrador**:

```powershell
schtasks /create /tn "Diario TikTok Reel 2015" /tr "C:\Users\Diario\social_publisher\run_tiktok_reel.bat" /sc daily /st 20:15 /f
```

Para probarla sin esperar: `schtasks /run /tn "Diario TikTok Reel 2015"`, y mirá el
resultado en `logs\task_scheduler.log`.

## Gotchas

- El **refresh token ROTA** en cada uso: se guarda en `.tiktok_token.json` (gitignored).
  Si alguna vez se automatiza en la nube, hay que guardarlo en un secret que se
  autoactualice (PAT con `secrets:write`), nunca en el repo.
- El redirect en el canje matchea con la forma con ñ; TikTok redirige al punycode.
- `user.info.basic` dio `scope_not_authorized` al pedir `display_name` en sandbox. No es
  crítico: `video.upload` funciona igual.
