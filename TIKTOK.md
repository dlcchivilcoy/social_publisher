# TikTok — el reel del bot a @diarioyradio

**Estado al 2026-09-16: la app está LIVE, pero la auditoría de Direct Post está RECHAZADA.**
Los reels caen en los **borradores** de TikTok y el editor los termina de publicar a mano
desde el celular. No es un estado de transición: es el régimen normal hasta nuevo aviso.

El rechazo del 16/9 **no es de formulario** (los nueve anteriores sí lo eran: Website URL,
ícono, la pantalla de confirmación). Es de política, textual del revisor:

> App will not be approved for personal or company internal use. TikTok for Developers
> currently does not support personal or internal company use. **Not acceptable:** Display
> posts from the TikTok account(s) you or your team manage on your website.

Es un renglón de las App Review Guidelines: TikTok aprueba herramientas que le sirven a
muchos creadores de terceros, y esta maneja la cuenta propia. **No hay campo que tocar ni
video que regrabar.** Hay un pedido de aclaración abierto con soporte (16/9).

---

## Cómo funciona HOY

TikTok es **una red más del bot**, al lado de Facebook, Instagram y YouTube. Corre en la
nube, con el resto.

- `transcriber._publicar_tiktok()` es el punto único de entrada. Lo llaman los tres caminos
  de `transcriber.py` (video aprobado, corresponsal, foto-nota) y los dos de
  `transcriber_radio.py` (los reels de la radio van a la **misma** cuenta).
- `platforms/tiktok.py` intenta **publicación directa** y, si TikTok la rechaza, cae solo a
  la **bandeja/borradores** y lo avisa. Nunca se pierde un reel.
- Hoy la directa siempre falla con `403 unaudited_client_can_only_post_to_private_accounts`,
  así que en la práctica todo va a borradores.
- **Freno propio: 4 borradores cada 24 h.** TikTok admite 5 subidas sin publicar por día y
  después rechaza todo con `spam_risk_too_many_pending_share`. El freno saltea **solo la
  bandeja**, nunca la directa. Tope en `TIKTOK_MAX_BORRADORES` (`0` lo apaga).
- **El token vive en Supabase** (tabla `tiktok_token`), no en el disco: el refresh token
  ROTA en cada uso y cada corrida de Actions arranca limpia. El archivo local
  `.tiktok_token.json` es solo el respaldo de cuando no hay credenciales de Supabase.
- Kill-switch: `TIKTOK_ENABLED=0`. Sin credenciales se saltea solo.

### Perillas del `.env`

`TIKTOK_CLIENT_KEY`, `TIKTOK_CLIENT_SECRET`, `TIKTOK_REDIRECT_URI`, `TIKTOK_SCOPES`,
`TIKTOK_ENABLED`, `TIKTOK_PRIVACIDAD`, `TIKTOK_MAX_BORRADORES`, `TIKTOK_CONFIRMADO`,
`TIKTOK_COMENTARIOS`, `TIKTOK_DUO`, `TIKTOK_STITCH`, `TIKTOK_MARCA_PROPIA`,
`TIKTOK_MARCA_TERCEROS`.

### La pantalla de confirmación

Las Content Sharing Guidelines exigen que una persona elija cuenta, privacidad, divulgación
comercial e interacciones antes de **cada** publicación directa. Está construida: el botón
«Aprobar» del mail abre `diario_web/src/lib/tiktok-confirmacion.js` en vez de publicar.
Sin confirmación, el bot va derecho a borradores. Detalle: la pantalla pregunta qué
**permitir** y la API pide qué **deshabilitar** (`disable_comment` y compañía van invertidos).

También se puede a mano:
`main.py --tiktok-opciones "priv=...,com=1,duo=0,stitch=0,propia=0,terceros=0"`.

---

## ⚠️ Lo que quedó muerto (no usarlo)

- **`tiktok_reel.py` + `run_tiktok_reel.bat`** — subían el reel de las «5 más leídas» desde
  un GitHub Release. Ese reel está apagado desde el 2026-07-02 y el camino lo reemplazó
  `transcriber._publicar_tiktok()`.
- **La tarea de Windows «Diario TikTok Reel 2015»** — no existe (verificado con `schtasks`
  el 16/9). Todas las tareas locales se desactivaron el 2026-07-21.
- **`tiktok_produccion.py`** — era el pasaje de sandbox a producción. Ya se hizo.

`tiktok_auth.py` **sí** sigue vivo: es el que re-autoriza si hay que rehacer el token.

---

## Datos del portal (verificados el 2026-09-16)

App ID `7652372530199644167`. Auditoría de Direct Post, referencia `20260910143956`.

| Campo | Valor |
|---|---|
| App name | Diario La Campaña — Publicador |
| Category | News |
| Platforms | **Web ✅ + Desktop ✅** |
| Terms of Service URL | `https://www.xn--diariolacampaa-2nb.com.ar/terminos` |
| Privacy Policy URL | `https://www.xn--diariolacampaa-2nb.com.ar/privacidad` |
| Web URL | `https://diarioweb.vercel.app/` (†) |
| App icon | `tiktok_app_icon.png` (la «C» naranja, cuadrado) (†) |

(†) Estos dos no entraban en la captura del 16/9; vienen del arreglo de agosto. El resto se
leyó del portal ese día.

**Description** (116 de 120 caracteres, tal cual está cargada):

> Local news outlet in Argentina. Our editors review our own news videos and post them to
> our TikTok from our web app.

**Scopes:** `user.info.basic`, `video.upload`, `video.publish`.
Falta `video.list`, que es aparte: sin él `tiktok.metricas()` devuelve `{}` y TikTok no suma
al ranking de corresponsales. Cuando se habilite, entra solo.

> 🚫 **Si alguna vez hay que reenviar algo, NO copies textos de versiones viejas de este
> archivo.** La descripción de julio empezaba con «Internal publishing tool…» — «internal»
> es la palabra exacta con la que TikTok nombra la categoría que rechaza.

---

## Gotchas que costaron tiempo

- 🔑 **Ante cualquier error raro, lo PRIMERO es verificar que `TIKTOK_CLIENT_KEY` sea la de
  la app que estás mirando en el portal.** Nueve intentos se fueron en esto: el `.env` tenía
  la clave de otra app, y TikTok rechazaba Redirect URIs perfectamente escritas.
- **Los errores de TikTok nombran el campo equivocado.** Dijo «scope» cuando fallaba el
  `redirect_uri`, y «redirect_uri» cuando el problema era el `client_key`. Descartar por
  partes, cambiando una cosa por vez.
- **El redirect Desktop solo acepta `localhost`/`127.0.0.1` con puerto.** El nuestro es
  `http://localhost:8723/callback/`; `tiktok_auth.py` levanta un servidor local y toma el
  código solo.
- **PKCE con SHA256 en HEXADECIMAL**, no base64url: TikTok se aparta del RFC 7636 y lo
  documenta en su guía de login-kit-desktop.
- **El ícono tiene que ser cuadrado** (1024×1024) y coincidir con el favicon y el logo
  visible del sitio. `logo.png` es un banner de 6170×830 y no sirve.
- ⚠️ **«Return to Draft» VACÍA el App icon.** Hay que volver a subirlo antes de reenviar.
- **Un MP4 recortado con `ffmpeg -c copy` lo RECHAZA el portal** (el índice `moov` queda al
  final). Recodificar con `-movflags +faststart`. Los videos demo son de **5 MB** máximo,
  no 50 como dice el texto de arriba del formulario.
- **El video que entra por la API NO aparece en «Borradores» del perfil.** La notificación
  ES el borrador: hay que tocarla y abre el editor con el video cargado.
- **El tope de 5 no se destraba vaciando los borradores**: cuenta las subidas de las últimas
  24 h, publiques o borres después. Se libera de a una.
- `.env.bak-*` está en `.gitignore`: **este repo es público**.
