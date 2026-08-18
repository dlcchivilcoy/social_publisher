"""Desgrabador de RADIO DEL CENTRO — clon SOLO REDES (sin Wix) del desgrabador del diario.

Toma videos/fotos que los colaboradores suben al Drive de **radiodelcentro** (carpeta
`VIDEOS_RADIO_FOLDER`), los desgraba con las claves Gemini de radiodelcentro
(`GEMINI_API_KEY_RADIO`) y publica **solo en redes**:
  - reel branded (MISMA marca del diario) a **Instagram y Facebook @diarioyradio**,
  - **YouTube Short al canal RADIO DEL CENTRO** (www.youtube.com/radiodelcentro).
NO crea nota web (Wix). Reutiliza casi todo de `transcriber.py` (reel, mail, captions, SEO).

Modelo SIN Google Apps Script (todo nube/web):
  - **Detección (etapa 1):** un escaneo programado (cron-job.org → workflow) corre
    `--transcribe-video-radio` SIN `--file` y procesa los videos nuevos de la carpeta
    (el ledger evita reprocesar). Con `--file <n>` procesa uno solo.
  - **Aprobación (etapa 2):** el botón «Aprobar» del mail apunta al endpoint web de Vercel
    `APPROVE_WEBAPP_URL_RADIO`, que dispara `--publish-video-radio --file <n>`.

Ledger propio: `.videos_radio.json` (no se pisa con el `.videos_contabilidad.json` del diario).
"""
import time
from datetime import datetime
from html import escape as _hesc
from pathlib import Path
from urllib.parse import quote

import transcriber as tr
from platforms import facebook, instagram, youtube_api
from utils.config import get
from utils.gemini import transcribe_to_nota
from utils.logger import get_logger
from utils.video_host import upload_reel
from video import foto_a_reel, frame_at, to_vertical_reel  # noqa: F401 (frame_at reservado)

logger = get_logger("transcriber_radio")

LEDGER_RADIO = Path(__file__).parent / ".videos_radio.json"
# Estados que YA fueron procesados en la etapa 1 (no volver a desgrabar en el escaneo).
# "descartado" = el usuario tocó «Borrar» en el mail → no se publica ni se re-escanea.
YA_ETAPA1 = ("listo", "solo_reel", "publicado", "publicado_solo_reel",
             "borrador_placa", "publicado_placa", "descartado")


# ── Config de la radio ────────────────────────────────────────────────────────
def _radio_folder() -> Path:
    return Path(get("VIDEOS_RADIO_FOLDER") or (Path(__file__).parent / "videos_radio"))


def _gemini_pool() -> list:
    """Claves de Gemini EN ORDEN para rotar entre PROYECTOS cuando una se satura (429): las 3 de
    radiodelcentro primero y, como último recurso, las del diario. Deduplicadas, sin vacías.
    La rotación se hace RE-SUBIENDO el video a cada proyecto: los archivos de la Files API son
    por proyecto (subir con una clave y desgrabar con otra da 403), así que no alcanza con rotar
    solo la desgrabación."""
    nombres = ["GEMINI_API_KEY_RADIO", "GEMINI_API_KEY_RADIO_2", "GEMINI_API_KEY_RADIO_3",
               "GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3", "GEMINI_API_KEY_4",
               "GEMINI_API_KEY_YT"]
    out, seen = [], set()
    for n in nombres:
        k = (get(n) or "").strip()
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    if not (get("GEMINI_API_KEY_RADIO") or "").strip():
        logger.warning("Sin GEMINI_API_KEY_RADIO en el .env: uso las claves generales "
                       "(cargá las de radiodelcentro para separar la cuota).")
    return out


def _es_error_cuota(e: Exception) -> bool:
    m = str(e).lower()
    return any(s in m for s in ("429", "quota", "resource_exhausted", "exhausted", "rate limit"))


def _gemini_model() -> str:
    """Modelo Gemini para la radio. El proyecto NUEVO de radiodelcentro recibe 404 con el
    pineado gemini-2.5-flash («no longer available to new users»); el alias gemini-flash-latest
    sí funciona (verificado 2026-07-26). Configurable con GEMINI_MODEL_RADIO."""
    return get("GEMINI_MODEL_RADIO") or "gemini-flash-latest"


def _notify() -> str:
    """Destinatario del aviso de la radio (default: el general / MAIL_FROM)."""
    return get("VIDEOS_RADIO_NOTIFY_EMAIL") or ""


def _yt_enabled() -> bool:
    return (get("YT_RADIO_SHORTS_ENABLED") or "1").strip().lower() not in ("0", "false", "no", "off")


def _approve_url() -> str:
    return get("APPROVE_WEBAPP_URL_RADIO") or ""


# ── Mail de la radio con los 4 botones (Aprobar / Editar / Previsualizar / Borrar) ──
def _html_aviso(intro_html: str, name: str, reel_url: str, kind: str = "",
                titulo: str = "", texto: str = "", editable: bool = True) -> str:
    """Mail de revisión de la radio con los 4 botones. Como la radio es SOLO REDES (no hay borrador
    de Wix de dónde leer), «Editar» lleva el título+texto ACTUALES en el propio link (los prellenan
    el formulario web) y «Borrar» DESCARTA el reel antes de publicar (no se publica ni se re-escanea).
    `texto` = el cuerpo que alimenta el caption (resumen en videos, texto en foto-notas)."""
    webapp = _approve_url()
    tok = get("WEBAPP_TOKEN")
    t = f"&token={quote(tok)}" if tok else ""
    k = f"&kind={kind}" if kind else ""
    botones = ""
    if webapp:
        botones += tr._boton(f"{webapp}?action=approve&name={quote(name)}{k}{t}",
                             "✅ Aprobar y publicar")
        if editable:
            botones += tr._boton(
                f"{webapp}?action=edit&name={quote(name)}{t}"
                f"&titulo={quote(titulo)}&texto={quote(texto)}",
                "✏️ Editar texto", color="#2a6bb8")
        if reel_url:
            botones += tr._boton(f"{webapp}?action=preview&url={quote(reel_url)}{t}",
                                 "👁️ Previsualizar video", color="#444")
        botones += tr._boton(f"{webapp}?action=delete&name={quote(name)}{t}",
                             "🗑️ Borrar (descartar)", color="#b00020")
    elif reel_url:
        botones += tr._boton(reel_url, "👁️ Ver el reel", color="#444")
    return (f'<div style="font-family:Arial;max-width:600px;color:#222;font-size:16px">'
            f'{intro_html}<div style="margin:22px 0">{botones}</div>'
            f'<p style="color:#777;font-size:13px">Radio del Centro · solo redes (sin nota web). '
            f'«Editar» corrige el texto que va al caption de IG/FB y a YouTube; «Borrar» descarta el '
            f'reel (no se publica).</p></div>')


# ── Desgrabación con ROTACIÓN de proyectos ante saturación (429) ──────────────
def _desgrabar_rotando(video: Path, extra_text: str, imgs) -> dict:
    """Desgraba el video probando las claves del pool EN ORDEN. Cada intento sube el video a ESE
    proyecto y lo desgraba con esa MISMA clave (`key_pool=[k]`, sin rotación interna cruzada:
    los archivos de Gemini son por proyecto). Si una clave está saturada (429), pasa a la
    siguiente. Ante un error que NO es de cuota, corta y lo re-lanza (lo maneja el llamador)."""
    pool = _gemini_pool()
    model = _gemini_model()
    if not pool:  # sin ninguna clave configurada: que falle con el mensaje claro de gemini.py
        return transcribe_to_nota(video, extra_text=extra_text, image_paths=imgs, model=model)
    ultimo = None
    for i, k in enumerate(pool):
        try:
            return transcribe_to_nota(video, extra_text=extra_text, image_paths=imgs,
                                      api_key=k, model=model, key_pool=[k])
        except Exception as e:  # noqa: BLE001
            ultimo = e
            if _es_error_cuota(e) and i < len(pool) - 1:
                logger.warning(f"Gemini: clave/proyecto {i + 1}/{len(pool)} saturado (429); "
                               f"reintento con la siguiente clave…")
                continue
            raise
    raise ultimo  # todas saturadas


# ── ETAPA 1: preparar (desgrabar + armar reel + avisar). Sin Wix. ─────────────
def _procesar_video(video: Path, uploader: str, dry_run: bool, rows: list[dict]) -> None:
    fila = tr._buscar_fila(rows, video.name)
    if not dry_run and fila and fila.get("estado") in YA_ETAPA1:
        logger.info(f"'{video.name}' ya fue procesado (estado={fila['estado']}). Nada que hacer.")
        return

    tr.WORK_DIR.mkdir(exist_ok=True)
    extra_text, imgs = tr._recolectar_adjuntos(video)

    # Contexto del corresponsal (contexto.txt que deja el bot de WhatsApp), si lo hay.
    ctx = tr._leer_contexto(video.parent)
    es_corresponsal = bool(ctx and "corresponsal" in (ctx.get("origen", "").lower()))
    if ctx:
        partes = []
        if ctx.get("lugar"):
            partes.append(f"Lugar del hecho: {ctx['lugar']}")
        if ctx.get("descripcion"):
            partes.append(f"Descripción aportada por el colaborador: {ctx['descripcion']}")
        if partes:
            extra_text = (extra_text + "\n\n" + "\n".join(partes)).strip()

    try:
        nota = _desgrabar_rotando(video, extra_text, imgs)
    except Exception as e:  # noqa: BLE001 — no tirar la corrida si Gemini está saturado
        logger.error(f"No se pudo desgrabar «{video.name}» (Gemini falló tras reintentos): {e}")
        if not dry_run:
            tr._enviar_aviso(
                f"No pude desgrabar el video de la radio (reintentá): {video.name}",
                f"Gemini estaba saturado y no pude desgrabar «{video.name}» esta vez.\n\n"
                f"No se perdió nada: se reintenta solo en el próximo escaneo (o volvé a subirlo).\n\n"
                f"Detalle técnico: {str(e)[:200]}", destino=_notify())
        return

    hay = nota["hay_noticia"]
    volanta, titulo = nota["volanta"], nota["titulo"]
    texto, resumen = nota["texto"], nota["resumen"]
    slug = tr._slug(video.stem)

    # Reel vertical branded SIN overlay ni zócalo (pedido del usuario 2026-07-29): queda igual
    # que el diario → fondo naranja + logo + placa final. Nombre único con prefijo «radio» para
    # que no pise en el GitHub Release al reel del diario.
    firma = tr._firma_texto() if es_corresponsal else None
    reel = to_vertical_reel(video, tr.WORK_DIR / f"reel_radio_{slug}.mp4",
                            firma=firma, overlay=False)

    if dry_run:
        logger.info(f"[dry-run] hay_noticia={hay}\n  VOLANTA: {volanta}\n  TÍTULO: {titulo}\n"
                    f"  RESUMEN: {resumen}\n  ZÓCALO: {nota.get('zocalo', '')}")
        logger.info(f"[dry-run] Reel: {reel} (Radio: solo redes, NO se toca Wix).")
        return

    reel_url = upload_reel(reel)
    estado = "listo" if hay else "solo_reel"

    if fila is None:
        fila = {"file": video.name}
        rows.append(fila)
    fila.update({
        "canal": "radio",
        "uploader": uploader or fila.get("uploader", ""),
        "fecha_recibido": datetime.now().isoformat(timespec="seconds"),
        "hay_noticia": hay, "volanta": volanta, "titulo": titulo, "resumen": resumen,
        "texto": texto, "zocalo": nota.get("zocalo", ""), "reel_url": reel_url, "estado": estado,
    })
    if ctx:
        fila.update({"origen": ctx.get("origen", ""),
                     "corresponsal_nombre": ctx.get("nombre", "")})
    tr._guardar_ledger(rows, LEDGER_RADIO)
    logger.info(f"Radio: registrado «{video.name}» (estado={estado}).")

    if hay:
        intro = (f"<h2 style='color:#e2620c'>Reel por revisar · Radio del Centro</h2>"
                 f"<p style='color:#888;font-size:13px'>{_hesc(volanta)}</p>"
                 f"<p style='font-size:19px'><b>{_hesc(titulo)}</b></p>"
                 f"<p>{_hesc(resumen)}</p>"
                 f"<p>Al aprobar, sale el reel a <b>Instagram y Facebook @diarioyradio</b> y como "
                 f"<b>Short en el canal de YouTube Radio del Centro</b>.</p>")
        asunto = f"Reel por revisar (Radio): {titulo}"
    else:
        intro = (f"<h2 style='color:#e2620c'>Video sin desgrabar · Radio del Centro</h2>"
                 f"<p>No pude armar el texto de «{_hesc(video.name)}» (no había info suficiente). "
                 f"Si aprobás, sale <b>solo el reel</b> (sin texto) a Instagram y Facebook.</p>")
        asunto = f"Video sin desgrabar (Radio): {video.name}"
    tr._enviar_aviso(asunto,
                     f"Reel de la radio por revisar: «{titulo or video.name}». "
                     f"Aprobalo desde el botón del mail.",
                     html=_html_aviso(intro, video.name, reel_url,
                                      titulo=titulo, texto=resumen, editable=hay),
                     destino=_notify())


def run_transcribe_video_radio(file: str = "", uploader: str = "", dry_run: bool = False) -> None:
    """ETAPA 1 de la radio. Con `--file` procesa ese video; SIN `--file` hace un ESCANEO de
    toda la carpeta y procesa cada video nuevo (el disparo lo hace cron-job.org, no Apps Script)."""
    modo = "SIMULACIÓN (dry-run)" if dry_run else "PROCESO REAL"
    base = _radio_folder()
    logger.info(f"=== Desgrabar video RADIO [{modo}] — file='{file or '(scan)'}' base={base} ===")
    if not base.exists():
        logger.error(f"No existe la carpeta de videos de la radio: {base}")
        return

    rows = tr._leer_ledger(LEDGER_RADIO)

    if file:
        video = tr._find_video(file, base=base)
        if not video:
            logger.error(f"No se encontró el video '{file}' en {base}.")
            return
        _procesar_video(video, uploader, dry_run, rows)
        logger.info("=== Desgrabar video RADIO: fin ===")
        return

    # MODO SCAN: todos los videos nuevos de la carpeta (más viejos primero).
    vids = sorted([p for p in base.rglob("*")
                   if p.is_file() and p.suffix.lower() in tr.VIDEO_EXTS],
                  key=lambda p: p.stat().st_mtime)
    if not vids:
        logger.info("Scan radio: no hay videos en la carpeta.")
        return
    nuevos = 0
    for v in vids:
        fila = tr._buscar_fila(rows, v.name)
        if fila and fila.get("estado") in YA_ETAPA1:
            continue
        # Evitar agarrar una subida a medio terminar: rclone preserva la fecha de Drive, así que
        # si se modificó hace <60s lo dejamos para el próximo escaneo (se auto-corrige solo).
        if not dry_run and (time.time() - v.stat().st_mtime) < 60:
            logger.info(f"Scan radio: «{v.name}» recién modificado (<60s); lo dejo para el próximo escaneo.")
            continue
        logger.info(f"Scan radio: procesando «{v.name}»…")
        try:
            _procesar_video(v, uploader, dry_run, rows)
            nuevos += 1
        except Exception as e:  # noqa: BLE001 — un video roto NO frena a los demás
            logger.error(f"Scan radio: «{v.name}» falló pero sigo con los demás: {e}")
            if not dry_run:
                tr._enviar_aviso(
                    f"No pude procesar un video de la radio (seguí con los demás): {v.name}",
                    f"Falló el procesamiento de «{v.name}»: {str(e)[:300]}\n\n"
                    f"El resto se procesó igual. Reintentá éste re-subiéndolo.", destino=_notify())
    logger.info(f"=== Scan radio: fin ({nuevos} video(s) nuevo(s)) ===")


# ── ETAPA 2: publicar (al aprobar por el botón del mail). Solo redes. ─────────
def _avisar_estado_radio(fila: dict, estado: dict, yt_info: dict) -> None:
    titulo = fila.get("titulo") or fila.get("file", "")

    def _li(nombre: str, clave: str, extra: str = "") -> str:
        st = estado.get(clave, "omitido")
        ico = "✅" if st == "ok" else ("➖" if st == "omitido" else "❌")
        return f"<li>{ico} <b>{nombre}:</b> {_hesc('publicado' if st == 'ok' else st)}{extra}</li>"

    yt_extra = ""
    if yt_info.get("short_url"):
        priv = yt_info.get("privacy", "")
        nota_priv = " <i>(privado — falta auditoría de la API)</i>" if priv and priv != "public" else ""
        yt_extra = f' — <a href="{yt_info["short_url"]}">{_hesc(yt_info["short_url"])}</a>{nota_priv}'

    html = (f"<div style='font-family:Arial;max-width:600px;color:#222;font-size:16px'>"
            f"<h2 style='color:#e2620c'>Publicado · Radio del Centro</h2>"
            f"<p style='font-size:18px'><b>{_hesc(titulo)}</b></p>"
            f"<ul style='line-height:1.8;list-style:none;padding:0'>"
            f"{_li('Instagram', 'instagram')}{_li('Facebook', 'facebook')}"
            f"{_li('YouTube (Radio del Centro)', 'youtube', yt_extra)}"
            f"</ul></div>")
    tr._enviar_aviso(f"Publicado (Radio): {titulo}",
                     f"Se publicó «{titulo}» a IG/FB @diarioyradio + YouTube (Radio del Centro).",
                     html=html, destino=_notify())


def run_publish_video_radio(file: str = "", dry_run: bool = False) -> None:
    """ETAPA 2 de la radio: publica el reel aprobado a IG/FB + Short al canal Radio del Centro."""
    modo = "SIMULACIÓN (dry-run)" if dry_run else "PUBLICACIÓN REAL"
    logger.info(f"=== Publicar video RADIO [{modo}] — file='{file}' ===")

    rows = tr._leer_ledger(LEDGER_RADIO)
    fila = tr._buscar_fila(rows, file) if file else None
    if fila is None:
        pend = [r for r in rows if r.get("estado") in ("listo", "solo_reel")]
        fila = pend[-1] if pend else None
    if fila is None:
        logger.error(f"No hay nada pendiente para '{file}'. Nada que publicar.")
        return
    if fila.get("estado") in ("publicado", "publicado_solo_reel"):
        logger.info(f"El video '{fila['file']}' ya estaba publicado. Nada que hacer.")
        return

    hay = fila.get("hay_noticia", True)
    reel_url = fila.get("reel_url")
    volanta = fila.get("volanta", "")
    titulo = fila.get("titulo", "")
    resumen = fila.get("resumen", "")
    caption = tr._caption(titulo, resumen) if hay else ""

    if dry_run:
        meta = tr._youtube_meta(volanta, titulo, resumen, fila.get("texto", "")) if hay else {}
        logger.info(f"[dry-run] hay={hay}. Publicaría reel={reel_url}\nCaption FB/IG:\n{caption or '(sin texto)'}\n"
                    f"YouTube Short: {'(omitido)' if not (hay and _yt_enabled()) else meta.get('titulo')}")
        return

    plats = tr._platforms()
    estado = {"instagram": "omitido", "facebook": "omitido", "youtube": "omitido"}

    local_reel = None
    if reel_url:
        try:
            tr.WORK_DIR.mkdir(exist_ok=True)
            local_reel = tr._descargar(reel_url, tr.WORK_DIR / "reel_radio_pub.mp4")
        except Exception as e:  # noqa: BLE001
            logger.error(f"No se pudo bajar el reel ({reel_url}): {e}")

    ig_media_id = fb_video_id = ""
    if "instagram" in plats and reel_url:
        try:
            res = instagram.publish_reel(reel_url, caption)
            ig_media_id = (res or {}).get("id", "")
            estado["instagram"] = "ok"
            logger.info(f"[instagram] reel OK (id={ig_media_id})")
        except Exception as e:  # noqa: BLE001
            estado["instagram"] = f"falló: {e}"
            logger.error(f"[instagram] reel FALLÓ: {e}")

    if "facebook" in plats and local_reel:
        try:
            res = facebook.publish_video(caption, local_reel)
            fb_video_id = (res or {}).get("id", "")
            estado["facebook"] = "ok"
            logger.info(f"[facebook] video OK (id={fb_video_id})")
        except Exception as e:  # noqa: BLE001
            estado["facebook"] = f"falló: {e}"
            logger.error(f"[facebook] video FALLÓ: {e}")

    yt_info = {}
    if hay and _yt_enabled() and local_reel:
        try:
            meta = tr._youtube_meta(volanta, titulo, resumen, fila.get("texto", ""))
            privacy = (get("YT_SHORTS_PRIVACY") or "public").strip()
            yt_info = tr._retry(
                lambda: youtube_api.upload_short(
                    local_reel, meta["titulo"], meta["descripcion"],
                    tags=meta["tags"], category_id=meta["category_id"], privacy=privacy,
                    creds=youtube_api.radio_credentials()),  # canal RADIO DEL CENTRO
                etiqueta="[youtube-radio] subir Short")
            estado["youtube"] = "ok"
            logger.info(f"[youtube] Short OK (Radio del Centro): {yt_info.get('short_url')}")
        except Exception as e:  # noqa: BLE001
            estado["youtube"] = f"falló: {e}"
            logger.error(f"[youtube] Short FALLÓ tras reintentos: {e}")
    elif hay and not _yt_enabled():
        logger.info("[youtube] desactivado (YT_RADIO_SHORTS_ENABLED=0).")

    fila.update({
        "estado": "publicado" if hay else "publicado_solo_reel",
        "fecha_publicado": datetime.now().isoformat(timespec="seconds"),
        "estado_canales": estado,
        "ig_media_id": ig_media_id or fila.get("ig_media_id", ""),
        "fb_video_id": fb_video_id or fila.get("fb_video_id", ""),
    })
    if yt_info:
        fila.update({
            "yt_video_id": yt_info.get("id", ""),
            "yt_url": yt_info.get("short_url") or yt_info.get("url", ""),
            "yt_titulo": titulo, "yt_privacy": yt_info.get("privacy", ""),
            "fecha_youtube": datetime.now().isoformat(timespec="seconds"),
        })
    tr._guardar_ledger(rows, LEDGER_RADIO)
    _avisar_estado_radio(fila, estado, yt_info)

    if any(v == "ok" for v in estado.values()):
        logger.info(f"Radio publicado y registrado. Estado por canal: {estado}")
    else:
        logger.error("Radio: no se pudo publicar en ningún canal — revisar credenciales.")
    logger.info("=== Publicar video RADIO: fin ===")


# ── IMÁGENES (foto-nota) de la radio → reel branded a IG/FB. Sin Wix. ─────────
def _placa_datos_radio(carpeta: Path):
    """(fotos, volanta, titular, texto) de una subcarpeta con foto(s) y texto OPCIONAL.
    A diferencia del diario, acepta carpetas SOLO con foto (título = nombre de la carpeta)."""
    fotos = [p for p in sorted(carpeta.iterdir())
             if p.is_file() and p.suffix.lower() in tr.IMG_EXTS]
    if not fotos:
        return None
    docx = next((p for p in sorted(carpeta.iterdir())
                 if p.is_file() and p.suffix.lower() in (".docx", ".txt")), None)
    if docx:
        volanta, titular, cuerpo = tr._parse_word(docx)
    else:
        volanta, titular, cuerpo = "", carpeta.name, []
    return fotos, volanta, (titular or carpeta.name), "\n\n".join(cuerpo)


def run_placa_radio(folder: str = "", uploader: str = "", dry_run: bool = False) -> None:
    """ETAPA 1 de la foto-nota de la radio: subcarpeta con foto(s) (+ texto opcional) →
    arma el reel branded de la foto y avisa para revisar. Sin Wix."""
    modo = "SIMULACIÓN (dry-run)" if dry_run else "PROCESO REAL"
    base = _radio_folder()
    logger.info(f"=== Foto-nota RADIO (etapa 1) [{modo}] — folder='{folder}' ===")
    carpeta = tr._find_folder(folder, base=base)
    if not carpeta:
        logger.error(f"No encontré la carpeta '{folder}' en {base}.")
        return
    datos = _placa_datos_radio(carpeta)
    if not datos:
        logger.error(f"'{folder}' necesita al menos una foto.")
        return
    fotos, volanta, titular, texto = datos

    rows = tr._leer_ledger(LEDGER_RADIO)
    fila = tr._buscar_fila(rows, carpeta.name)
    if not dry_run and fila and fila.get("estado") in ("borrador_placa", "publicado_placa"):
        logger.info(f"La foto-nota '{carpeta.name}' ya fue procesada (estado={fila['estado']}).")
        return

    if dry_run:
        logger.info(f"[dry-run] foto-nota radio «{titular}»: {len(fotos)} foto(s). VOLANTA={volanta}")
        return

    reel_url = ""
    try:
        tr.WORK_DIR.mkdir(exist_ok=True)
        reel = foto_a_reel(fotos, tr.WORK_DIR / f"placa_radio_{tr._slug(carpeta.name)}.mp4",
                           overlay=False)
        reel_url = upload_reel(reel)
    except Exception as e:  # noqa: BLE001
        logger.error(f"No se pudo armar el reel de la foto-nota: {e}")
        return

    if fila is None:
        fila = {"file": carpeta.name}
        rows.append(fila)
    fila.update({
        "canal": "radio", "es_placa": True,
        "uploader": uploader or fila.get("uploader", ""),
        "fecha_recibido": datetime.now().isoformat(timespec="seconds"),
        "hay_noticia": True, "volanta": volanta, "titulo": titular, "texto": texto,
        "reel_url": reel_url, "estado": "borrador_placa",
    })
    tr._guardar_ledger(rows, LEDGER_RADIO)
    logger.info(f"Foto-nota radio registrada (estado=borrador_placa).")

    intro = (f"<h2 style='color:#e2620c'>Foto-nota por revisar · Radio del Centro</h2>"
             f"<p style='color:#888;font-size:13px'>{_hesc(volanta)} · {len(fotos)} foto(s)</p>"
             f"<p style='font-size:19px'><b>{_hesc(titular)}</b></p>"
             f"<p style='white-space:pre-wrap'>{_hesc(texto)}</p>"
             f"<p>Al aprobar, sale un <b>reel de la foto</b> a Instagram y Facebook @diarioyradio.</p>")
    tr._enviar_aviso(f"Foto-nota por revisar (Radio): {titular}",
                     f"Foto-nota de la radio por revisar: «{titular}». Aprobala desde el botón del mail.",
                     html=_html_aviso(intro, carpeta.name, reel_url, kind="folder",
                                      titulo=titular, texto=texto, editable=True),
                     destino=_notify())
    logger.info("=== Foto-nota RADIO (etapa 1): fin ===")


def run_placa_radio_publish(folder: str = "", dry_run: bool = False) -> None:
    """ETAPA 2 de la foto-nota de la radio: postea el reel de la foto a IG/FB. Sin Wix."""
    modo = "SIMULACIÓN (dry-run)" if dry_run else "PUBLICACIÓN REAL"
    logger.info(f"=== Foto-nota RADIO (etapa 2) [{modo}] — folder='{folder}' ===")
    rows = tr._leer_ledger(LEDGER_RADIO)
    fila = tr._buscar_fila(rows, folder) if folder else None
    if fila is None:
        pend = [r for r in rows if r.get("es_placa") and r.get("estado") == "borrador_placa"]
        fila = pend[-1] if pend else None
    if fila is None:
        logger.error(f"No hay foto-nota pendiente para '{folder}'.")
        return
    if fila.get("estado") == "publicado_placa":
        logger.info(f"La foto-nota '{fila['file']}' ya estaba publicada.")
        return

    titular = fila.get("titulo", "")
    texto = fila.get("texto", "")
    reel_url = fila.get("reel_url", "")
    caption = f"{titular}\n\n{texto}\n\n📲 {tr._site()}".strip()

    if dry_run:
        logger.info(f"[dry-run] publicaría foto-nota radio «{titular}» (reel a IG/FB).")
        return

    plats = tr._platforms()
    estado = {"instagram": "omitido", "facebook": "omitido", "youtube": "omitido"}
    local_reel = None
    if reel_url:
        try:
            tr.WORK_DIR.mkdir(exist_ok=True)
            local_reel = tr._descargar(reel_url, tr.WORK_DIR / "placa_radio_pub.mp4")
        except Exception as e:  # noqa: BLE001
            logger.error(f"No se pudo bajar el reel ({reel_url}): {e}")

    if "instagram" in plats and reel_url:
        try:
            instagram.publish_reel(reel_url, caption); estado["instagram"] = "ok"
            logger.info("[instagram] reel OK")
        except Exception as e:  # noqa: BLE001
            estado["instagram"] = f"falló: {e}"; logger.error(f"[instagram] reel FALLÓ: {e}")
    if "facebook" in plats and local_reel:
        try:
            facebook.publish_video(caption, local_reel); estado["facebook"] = "ok"
            logger.info("[facebook] reel OK")
        except Exception as e:  # noqa: BLE001
            estado["facebook"] = f"falló: {e}"; logger.error(f"[facebook] reel FALLÓ: {e}")

    fila.update({"estado": "publicado_placa",
                 "fecha_publicado": datetime.now().isoformat(timespec="seconds"),
                 "estado_canales": estado})
    tr._guardar_ledger(rows, LEDGER_RADIO)
    _avisar_estado_radio(fila, estado, {})
    logger.info("=== Foto-nota RADIO (etapa 2): fin ===")


# ── BORRAR = DESCARTAR antes de publicar (botón «Borrar» del mail) ────────────
def run_descartar_video_radio(file: str = "", dry_run: bool = False) -> None:
    """Descarta un reel de la radio ANTES de publicar: lo marca 'descartado' en el ledger (el
    escaneo lo saltea) y NO publica nada. Equivale al «borrar borrador» del diario, pero como la
    radio no tiene nota web, sólo evita que ese reel salga. No toca posteos ya publicados."""
    logger.info(f"=== Descartar RADIO — file='{file}' dry_run={dry_run} ===")
    rows = tr._leer_ledger(LEDGER_RADIO)
    fila = tr._buscar_fila(rows, file) if file else None
    if fila is None:
        logger.error(f"No encontré «{file}» en el ledger de la radio. Nada que descartar.")
        return
    if fila.get("estado") in ("publicado", "publicado_solo_reel", "publicado_placa"):
        logger.info(f"«{file}» ya estaba publicado; no se descarta (los posteos online no se tocan).")
        return
    if dry_run:
        logger.info(f"[dry-run] descartaría «{file}».")
        return
    fila.update({"estado": "descartado",
                 "fecha_descartado": datetime.now().isoformat(timespec="seconds")})
    tr._guardar_ledger(rows, LEDGER_RADIO)
    logger.info(f"Radio: «{file}» descartado (no se publica ni se re-escanea).")
    tr._enviar_aviso(
        f"Descartado (Radio): {fila.get('titulo') or file}",
        f"Se descartó «{fila.get('titulo') or file}». No se publicó nada y no se vuelve a escanear.",
        destino=_notify())
    logger.info("=== Descartar RADIO: fin ===")


# ── EDITAR el texto antes de publicar (botón «Editar» del mail) ───────────────
def run_editar_video_radio(file: str = "", titulo: str = "", texto: str = "",
                           publicar: bool = False, dry_run: bool = False) -> None:
    """Corrige el TEXTO de un reel de la radio. Como no hay borrador de Wix, guarda los cambios en el
    LEDGER. El cuerpo editado va al campo que alimenta el caption de cada tipo: `texto` en foto-notas,
    `resumen` en videos (ver _caption / run_placa_radio_publish). El formulario web manda todo en base64.

    Con `publicar=True` (botón «Guardar y publicar») EDITA Y PUBLICA en la MISMA corrida: así no hay
    carrera entre dos Actions asíncronas (editar vs aprobar). Como el reel no cambia al editar el texto
    —solo cambia el caption— no hace falta re-previsualizar."""
    logger.info(f"=== Editar RADIO — file='{file}' publicar={publicar} dry_run={dry_run} ===")
    rows = tr._leer_ledger(LEDGER_RADIO)
    fila = tr._buscar_fila(rows, file) if file else None
    if fila is None:
        logger.error(f"No encontré «{file}» en el ledger de la radio. Nada que editar.")
        return
    if fila.get("estado") in ("publicado", "publicado_solo_reel", "publicado_placa"):
        logger.info(f"«{file}» ya está publicado; no se edita (los posteos online no se tocan).")
        return
    titulo, texto = (titulo or "").strip(), (texto or "").strip()
    if not titulo and not texto:
        logger.error("Editar RADIO: vinieron vacíos título y texto; no toco nada.")
        return
    if dry_run:
        logger.info(f"[dry-run] editaría «{file}»: titulo='{titulo[:60]}' cuerpo={len(texto)} chars "
                    f"(publicar={publicar}).")
        return
    if titulo:
        fila["titulo"] = titulo
    if texto:
        # El caption usa 'texto' en las foto-notas y 'resumen' en los videos.
        fila["texto" if fila.get("es_placa") else "resumen"] = texto
    fila["fecha_editado"] = datetime.now().isoformat(timespec="seconds")
    tr._guardar_ledger(rows, LEDGER_RADIO)
    logger.info(f"Radio: texto de «{file}» actualizado.")

    if publicar:
        logger.info(f"Radio: publico «{file}» con el texto corregido (misma corrida, sin carrera).")
        if fila.get("es_placa"):
            run_placa_radio_publish(folder=file, dry_run=dry_run)
        else:
            run_publish_video_radio(file=file, dry_run=dry_run)
    else:
        logger.info("Radio: guardado. Falta aprobar desde el mail para publicar.")
    logger.info("=== Editar RADIO: fin ===")
