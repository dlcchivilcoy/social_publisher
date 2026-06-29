"""Desgrabador audiovisual → nota web (Wix) + reel a Facebook e Instagram.

Pensado para correr en la NUBE (GitHub Actions), disparado por un Google Apps Script
cuando un colaborador sube un video (o una SUBCARPETA con video + fotos + texto) a la
carpeta de Drive «videos notas actualidad». NO usa tokens de Claude: la desgrabación la
hace Gemini (gratis), que recibe el VIDEO COMPLETO (audio + texto en pantalla + subtítulos
+ imágenes) más el contexto adjunto.

Dos etapas (flujo CON revisión):

  ETAPA 1 — `run_transcribe_video(file, uploader)`  (al subir):
    1. junta los adjuntos de la subcarpeta (fotos + texto) como contexto
    2. Gemini desgraba el video → {hay_noticia, volanta, titulo, texto, resumen, mejor_momento_seg}
    3. saca la foto de portada en el segundo más representativo que indica Gemini
    4. arma el reel vertical 9:16 (si no hay noticia, recortado a 1 min) y lo sube a un Release
    5. SI HAY NOTICIA: crea la nota como BORRADOR en Wix (foto + video nativo) y avisa
       SI NO HAY: NO crea nota web; deja listo solo el reel y avisa para decidir
    6. registra la fila de contabilidad

  ETAPA 2 — `run_publish_video(file)`  (al mover el video a APROBADAS):
    - Con noticia: publica la nota web + reel a FB/IG con el resumen de caption.
    - Sin noticia: la web queda SUSPENDIDA; sale SOLO el reel (sin texto).
"""
import json
import re
import smtplib
import ssl
import time
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from html import escape as _hesc
from pathlib import Path
from urllib.parse import quote

import requests

from platforms import facebook, instagram, wix
from utils.config import get
from utils.gemini import transcribe_to_nota
from utils.logger import get_logger
from utils.video_host import upload_reel
from video import best_parts_clip, duration_seconds, frame_at, remux_mp4, to_vertical_reel

logger = get_logger("transcriber")

LEDGER = Path(__file__).parent / ".videos_contabilidad.json"
WORK_DIR = Path(__file__).parent / "videos_preview"
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mpg", ".mpeg"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
TEXT_EXTS = {".txt", ".md", ".docx"}
REEL_MAX_SIN_NOTICIA = 60  # segundos: tope del reel cuando no se pudo desgrabar


# ── Helpers de entorno ────────────────────────────────────────────────────────
def _site() -> str:
    return get("STORY_SITE_URL") or "www.diariolacampaña.com.ar"


def _platforms() -> list[str]:
    raw = get("STORIES_PLATFORMS") or "instagram,facebook"
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _videos_folder() -> Path:
    return Path(get("VIDEOS_FOLDER") or (Path(__file__).parent / "videos"))


def _find_video(name: str) -> Path | None:
    """Ubica el video bajado de Drive por nombre (en la raíz o en una subcarpeta);
    si no, agarra el más nuevo."""
    folder = _videos_folder()
    if not folder.exists():
        return None
    if name:
        cand = folder / name
        if cand.exists():
            return cand
        for p in folder.rglob("*"):
            if p.is_file() and p.name == name:
                return p
    vids = [p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS]
    return max(vids, key=lambda p: p.stat().st_mtime) if vids else None


def _slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (s or "")).strip("-").lower()
    return s[:40] or "reel"


IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}


def _find_folder(name: str) -> Path | None:
    """Ubica la SUBCARPETA de una nota-placa (Word + foto, sin video) por nombre."""
    base = _videos_folder()
    if not base.exists():
        return None
    if name:
        cand = base / name
        if cand.is_dir():
            return cand
        for p in base.rglob("*"):
            if p.is_dir() and p.name == name:
                return p
    return None


def _parse_word(path: Path) -> tuple[str, str, list[str]]:
    """(volanta, titular, cuerpo) desde un .docx o .txt. 1er párrafo corto = volanta."""
    if path.suffix.lower() == ".txt":
        paras = [ln.strip() for ln in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines() if ln.strip()]
    else:
        from docx import Document
        paras = [p.text.strip() for p in Document(str(path)).paragraphs if p.text.strip()]
    if not paras:
        return "", "", []
    es_vol = len(paras) >= 2 and (len(paras[0]) <= 55 or len(paras[0].split()) <= 7)
    if es_vol:
        return paras[0], paras[1], paras[2:]
    return "", paras[0], paras[1:]


# ── Adjuntos de la subcarpeta (fotos + texto de contexto) ─────────────────────
def _leer_texto(path: Path) -> str:
    try:
        if path.suffix.lower() == ".docx":
            from docx import Document
            return "\n".join(p.text for p in Document(str(path)).paragraphs if p.text.strip())
        return path.read_text(encoding="utf-8-sig", errors="ignore")
    except Exception as e:
        logger.warning(f"No se pudo leer el contexto {path.name}: {e}")
        return ""


def _recolectar_adjuntos(video: Path) -> tuple[str, list[Path]]:
    """Si el video está en una SUBCARPETA (no en la raíz de videos/), junta las fotos y
    textos hermanos como contexto. En la raíz no junta nada (evita mezclar notas)."""
    folder = video.parent
    try:
        if folder.resolve() == _videos_folder().resolve():
            return "", []
    except Exception:
        return "", []
    textos, imgs = [], []
    for p in sorted(folder.iterdir()):
        if not p.is_file() or p == video:
            continue
        ext = p.suffix.lower()
        if ext in TEXT_EXTS:
            t = _leer_texto(p)
            if t.strip():
                textos.append(t.strip())
        elif ext in IMAGE_EXTS:
            imgs.append(p)
    if textos or imgs:
        logger.info(f"Adjuntos en «{folder.name}»: {len(textos)} texto(s), {len(imgs)} foto(s)")
    return "\n\n".join(textos), imgs


# ── Ledger de contabilidad ────────────────────────────────────────────────────
def _leer_ledger() -> list[dict]:
    if not LEDGER.exists():
        return []
    try:
        return list(json.loads(LEDGER.read_text(encoding="utf-8-sig")))
    except Exception:
        return []


def _guardar_ledger(rows: list[dict]) -> None:
    LEDGER.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def _buscar_fila(rows: list[dict], file: str) -> dict | None:
    for row in rows:
        if row.get("file") == file:
            return row
    return None


# ── Aviso por mail ────────────────────────────────────────────────────────────
def _boton(url: str, texto: str, color: str = "#e2620c") -> str:
    return (f'<a href="{url}" style="display:inline-block;background:{color};color:#fff;'
            f'text-decoration:none;padding:12px 20px;border-radius:6px;font-family:Arial;'
            f'font-size:16px;margin:6px 6px 6px 0">{texto}</a>')


def _html_aviso(intro_html: str, name: str, reel_url: str, draft_id: str, hay: bool) -> str:
    """Arma el cuerpo HTML del aviso con los botones (si hay APPROVE_WEBAPP_URL)."""
    webapp = get("APPROVE_WEBAPP_URL")
    tok = get("WEBAPP_TOKEN")
    t = f"&token={quote(tok)}" if tok else ""
    botones = ""
    if webapp:
        botones += _boton(f"{webapp}?action=approve&name={quote(name)}{t}", "✅ Aprobar y publicar")
        if reel_url:
            # Reproduce el reel dentro del navegador (web app) en vez de descargarlo.
            botones += _boton(f"{webapp}?action=preview&url={quote(reel_url)}{t}",
                              "👁️ Previsualizar video", color="#444")
        if hay and draft_id:
            botones += _boton(f"{webapp}?action=edit&draft={draft_id}{t}", "✏️ Corregir texto", color="#444")
            botones += _boton(f"{webapp}?action=delete&post={quote(draft_id)}{t}", "🗑️ Borrar borrador", color="#b00020")
    elif reel_url:
        botones += _boton(reel_url, "👁️ Ver el reel", color="#444")
    return (f'<div style="font-family:Arial;max-width:600px;color:#222;font-size:16px">'
            f'{intro_html}<div style="margin:22px 0">{botones}</div>'
            f'<p style="color:#777;font-size:13px">Si no ves los botones, aprobá moviendo el '
            f'video a la subcarpeta APROBADAS en Drive.</p></div>')


def _enviar_aviso(asunto: str, cuerpo: str, html: str | None = None) -> None:
    """Manda un mail al diario (reusa el SMTP del mailer). Best-effort. Si se pasa `html`,
    va como alternativa HTML (con botones)."""
    remitente = get("MAIL_FROM")
    password = get("MAIL_APP_PASSWORD")
    destino = get("VIDEOS_NOTIFY_EMAIL") or remitente
    if not remitente or not password or not destino:
        logger.warning("Sin credenciales de mail (MAIL_FROM/MAIL_APP_PASSWORD): no se manda el aviso.")
        return
    host = get("SMTP_HOST") or "smtp.gmail.com"
    port = int(get("SMTP_PORT") or 587)
    nombre_from = get("MAIL_FROM_NAME") or "Diario La Campaña"
    msg = EmailMessage()
    msg["From"] = formataddr((nombre_from, remitente))
    msg["To"] = destino
    msg["Subject"] = asunto
    msg.set_content(cuerpo)
    if html:
        msg.add_alternative(html, subtype="html")
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=60) as server:
            server.starttls(context=ctx)
            server.login(remitente, password)
            server.send_message(msg)
        logger.info(f"Aviso enviado a {destino}")
    except Exception as e:
        logger.error(f"No se pudo enviar el aviso por mail: {e}")


def _descargar(url: str, destino: Path) -> Path:
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=180)
    r.raise_for_status()
    destino.write_bytes(r.content)
    return destino


# ── ETAPA 1: preparar ─────────────────────────────────────────────────────────
def run_transcribe_video(file: str = "", uploader: str = "", dry_run: bool = False) -> None:
    modo = "SIMULACIÓN (dry-run)" if dry_run else "PROCESO REAL"
    logger.info(f"=== Desgrabar video [{modo}] — file='{file}' uploader='{uploader}' ===")

    video = _find_video(file)
    if not video:
        logger.error(f"No se encontró el video '{file}' en {_videos_folder()}.")
        return
    logger.info(f"Video: {video}")

    rows = _leer_ledger()
    fila = _buscar_fila(rows, video.name)
    YA = ("borrador", "solo_reel", "publicado", "publicado_solo_reel")
    if not dry_run and fila and fila.get("estado") in YA:
        logger.info(f"El video '{video.name}' ya fue procesado (estado={fila['estado']}). Nada que hacer.")
        return

    WORK_DIR.mkdir(exist_ok=True)
    extra_text, imgs = _recolectar_adjuntos(video)
    nota = transcribe_to_nota(video, extra_text=extra_text, image_paths=imgs)

    hay = nota["hay_noticia"]
    volanta, titulo = nota["volanta"], nota["titulo"]
    texto, resumen = nota["texto"], nota["resumen"]

    cover = frame_at(video, nota["mejor_momento_seg"], WORK_DIR / "portada.jpg")
    slug = _slug(video.stem)

    # Reel para redes: VIDEO COMPLETO, sin recorte (pedido del usuario 2026-06-28).
    # Antes se recortaba a ~60s (mejores partes con noticia, o 60s sin noticia); se anuló:
    # ahora va el video entero, solo reencuadrado a vertical 9:16 para el formato reel.
    reel_path = WORK_DIR / f"reel_{slug}.mp4"
    reel = to_vertical_reel(video, reel_path)

    if dry_run:
        logger.info(f"[dry-run] hay_noticia={hay} | tramos={len(nota.get('segmentos', []))}\n"
                    f"  VOLANTA: {volanta}\n  TÍTULO: {titulo}\n  RESUMEN: {resumen}\n  TEXTO:\n{texto}")
        logger.info(f"[dry-run] Portada: {cover}  Reel: {reel}")
        logger.info("=== Desgrabar video: fin (dry-run) ===")
        return

    reel_url = upload_reel(reel)

    draft_id = ""
    if hay:
        # La WEB lleva el video COMPLETO (no el reel recortado): se hostea aparte y se embebe.
        web_video_url = reel_url
        try:
            full = remux_mp4(video, WORK_DIR / f"video_{slug}.mp4")
            web_video_url = upload_reel(full)
        except Exception as e:
            logger.warning(f"No se pudo hostear el video completo para la web ({e}); uso el reel.")
        title = f"{volanta} — {titulo}" if volanta else titulo
        body = titulo + ("\n\n" + texto if texto else "")
        info = wix.crear_borrador(title, body, cover, page=0, description=resumen, video_url=web_video_url)
        draft_id = info["draft_id"]
        estado = "borrador"
    else:
        estado = "solo_reel"

    if fila is None:
        fila = {"file": video.name}
        rows.append(fila)
    fila.update({
        "uploader": uploader or fila.get("uploader", ""),
        "fecha_recibido": datetime.now().isoformat(timespec="seconds"),
        "hay_noticia": hay, "volanta": volanta, "titulo": titulo, "resumen": resumen,
        "texto": texto, "draft_id": draft_id, "reel_url": reel_url, "estado": estado,
    })
    _guardar_ledger(rows)
    logger.info(f"Registrado (estado={estado}, draft_id={draft_id or '—'}).")

    if hay:
        cuerpo = (
            f"Llegó un video para revisar: «{titulo}»\n"
            f"Enviado por: {uploader or 'desconocido'}\n\n"
            f"VOLANTA: {volanta}\nTÍTULO: {titulo}\n\nRESUMEN: {resumen}\n\n"
            f"Está cargado como BORRADOR en Wix (Blog → Borradores) con la foto de portada y el video.\n\n"
            f"➡️ Para PUBLICARLO en la web y mandar el reel a Facebook e Instagram, "
            f"mové el video «{video.name}» a la subcarpeta APROBADAS dentro de «videos notas actualidad»."
        )
        intro = (f"<h2 style='color:#e2620c'>Nota por revisar</h2>"
                 f"<p style='color:#888;font-size:13px'>{_hesc(volanta)} · enviado por {_hesc(uploader or 'desconocido')}</p>"
                 f"<p style='font-size:19px'><b>{_hesc(titulo)}</b></p>"
                 f"<p>{_hesc(resumen)}</p>"
                 f"<p>Está como <b>borrador en Wix</b> con foto + video. Revisalo y:</p>")
        _enviar_aviso(f"Nota por revisar: {titulo}", cuerpo,
                      html=_html_aviso(intro, video.name, reel_url, draft_id, hay=True))
    else:
        cuerpo = (
            f"Llegó un video pero NO pude desgrabarlo: «{video.name}»\n"
            f"Enviado por: {uploader or 'desconocido'}\n\n"
            f"No encontré información suficiente (ni en el audio, ni en el texto en pantalla, ni en "
            f"subtítulos o adjuntos) para armar la nota. Por eso la NOTA WEB queda SUSPENDIDA.\n\n"
            f"➡️ Si querés que igual SALGA EL REEL (recortado a 1 minuto, sin texto) a Facebook e "
            f"Instagram, mové el video «{video.name}» a la subcarpeta APROBADAS.\n"
            f"➡️ Si no, borralo. (Tip: podés re-subirlo en una subcarpeta con un .txt o fotos de "
            f"contexto para que pueda armar la nota.)"
        )
        intro = (f"<h2 style='color:#e2620c'>Video sin desgrabar</h2>"
                 f"<p>No pude armar la nota de «{_hesc(video.name)}» (no había info suficiente). "
                 f"La <b>nota web queda suspendida</b>.</p>"
                 f"<p>Si querés que igual salga <b>solo el reel</b> (1 min, sin texto):</p>")
        _enviar_aviso(f"Video sin desgrabar: {video.name}", cuerpo,
                      html=_html_aviso(intro, video.name, reel_url, "", hay=False))
    logger.info("=== Desgrabar video: fin ===")


# ── ETAPA 2: publicar (al aprobar) ────────────────────────────────────────────
def _caption(titulo: str, resumen: str) -> str:
    site = _site()
    return (
        f"{titulo}\n\n{resumen}\n\n"
        f"📲 Seguí leyendo en {site}\n\n"
        f"#Chivilcoy #DiarioLaCampaña #Actualidad #Noticias"
    )


def _retry(fn, intentos: int = 3, espera: int = 5, etiqueta: str = ""):
    """Ejecuta `fn` con reintentos automáticos (backoff lineal). Re-lanza el último
    error si agota los intentos. Se usa para YouTube y Wix (red/cuota intermitente)."""
    ultimo = None
    for i in range(max(1, intentos)):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — queremos reintentar ante cualquier fallo de red/API
            ultimo = e
            logger.warning(f"{etiqueta or 'tarea'}: intento {i + 1}/{intentos} falló: {e}")
            if i < intentos - 1:
                time.sleep(espera * (i + 1))
    raise ultimo


# Hashtags locales SIEMPRE presentes (SEO local de Chivilcoy) + los temáticos de la nota.
_HASHTAGS_LOCALES = ["#Chivilcoy", "#NoticiasChivilcoy", "#DiarioLaCampaña", "#Actualidad"]


def _yt_enabled() -> bool:
    return (get("YT_SHORTS_ENABLED") or "1").strip().lower() not in ("0", "false", "no", "off")


def _hashtag(palabra: str) -> str:
    """Convierte 'radio del centro' → '#RadioDelCentro' (sin acentos/espacios)."""
    limpio = re.sub(r"[^0-9A-Za-zñÑáéíóúÁÉÍÓÚ ]+", "", palabra or "").strip()
    return "#" + "".join(p.capitalize() for p in limpio.split())


def _youtube_meta(volanta: str, titulo: str, resumen: str, texto: str) -> dict:
    """Arma título + descripción (formato periodístico, SEO local) + tags + hashtags del
    Short, REUTILIZANDO la lógica SEO de Gemini (`seo_youtube`). Si Gemini falla, cae a un
    armado determinístico con los datos de la nota. Nunca tira excepción."""
    site = _site()
    seo = {}
    try:
        from utils import gemini
        cuerpo_ctx = (resumen + ("\n\n" + texto if texto else "")).strip()
        seo = gemini.seo_youtube(titulo, cuerpo_ctx) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[youtube] SEO con Gemini falló ({e}); uso los datos de la nota.")

    yt_titulo = (seo.get("titulo") or (f"{volanta}: {titulo}" if volanta else titulo) or titulo)[:100]

    # Hashtags: locales fijos + los temáticos que sugirió Gemini (de sus tags).
    tags = [t for t in (seo.get("tags") or []) if t]
    topicos = []
    for t in tags:
        h = _hashtag(t)
        if len(h) > 2 and h.lower() not in (x.lower() for x in _HASHTAGS_LOCALES + topicos):
            topicos.append(h)
    hashtags = _HASHTAGS_LOCALES + topicos[:6]
    linea_hashtags = " ".join(hashtags)

    # Descripción periodística: bajada (Gemini o resumen) + CTA web/suscripción + hashtags.
    bajada = (seo.get("descripcion") or resumen or titulo).strip()
    # La descripción de Gemini ya puede traer su propia línea de hashtags: la sacamos para no
    # duplicar y dejamos la nuestra (con los locales garantizados).
    bajada = re.sub(r"\n?#[\wñÑáéíóúÁÉÍÓÚ]+(?:\s+#[\wñÑáéíóúÁÉÍÓÚ]+)*\s*$", "", bajada).strip()
    descripcion = (
        f"{bajada}\n\n"
        f"📲 Seguí leyendo la nota completa en {site}\n"
        f"🔔 Suscribite al canal para más noticias de Chivilcoy y la región.\n\n"
        f"{linea_hashtags}"
    )

    # Tags de YouTube (campo Tags): los de Gemini + locales, sin '#', deduplicados.
    base_tags = ["chivilcoy", "noticias chivilcoy", "diario la campaña", "radio del centro", "actualidad"]
    final_tags, vistos = [], set()
    for t in (tags + base_tags):
        k = t.strip().lower()
        if k and k not in vistos:
            vistos.add(k)
            final_tags.append(t.strip())
    return {
        "titulo": yt_titulo,
        "descripcion": descripcion,
        "tags": final_tags[:15],
        "hashtags": linea_hashtags,
        "category_id": (get("YT_SHORTS_CATEGORY") or "25").strip(),
    }


def run_publish_video(file: str = "", dry_run: bool = False) -> None:
    modo = "SIMULACIÓN (dry-run)" if dry_run else "PUBLICACIÓN REAL"
    logger.info(f"=== Publicar video aprobado [{modo}] — file='{file}' ===")

    rows = _leer_ledger()
    fila = _buscar_fila(rows, file) if file else None
    if fila is None:
        pendientes = [r for r in rows if r.get("estado") in ("borrador", "solo_reel")]
        fila = pendientes[-1] if pendientes else None
    if fila is None:
        logger.error(f"No hay nada pendiente para '{file}'. Nada que publicar.")
        return
    if fila.get("estado") in ("publicado", "publicado_solo_reel"):
        logger.info(f"El video '{fila['file']}' ya estaba publicado. Nada que hacer.")
        return

    hay = fila.get("hay_noticia", True)
    draft_id = fila.get("draft_id")
    reel_url = fila.get("reel_url")
    volanta = fila.get("volanta", "")
    titulo = fila.get("titulo", "")
    resumen = fila.get("resumen", "")
    caption = _caption(titulo, resumen) if hay else ""

    if dry_run:
        meta = _youtube_meta(volanta, titulo, resumen, fila.get("texto", "")) if hay else {}
        logger.info(f"[dry-run] hay_noticia={hay}. Publicaría reel={reel_url} + draft={draft_id or '—'}\n"
                    f"Caption FB/IG:\n{caption or '(sin texto)'}\n"
                    f"YouTube Short: {'(omitido)' if not (hay and _yt_enabled()) else meta.get('titulo')}\n"
                    f"Descripción YT:\n{meta.get('descripcion', '(omitida)')}")
        return

    plats = _platforms()
    # Estado de publicación por canal (el «panel» que pide el flujo: IG/FB/YouTube/Wix).
    estado_canales = {"instagram": "omitido", "facebook": "omitido",
                      "youtube": "omitido", "wix": "omitido"}

    # Bajamos el reel UNA sola vez (lo reusan Facebook y YouTube).
    local_reel = None
    if reel_url:
        try:
            WORK_DIR.mkdir(exist_ok=True)
            local_reel = _descargar(reel_url, WORK_DIR / "reel_pub.mp4")
        except Exception as e:
            logger.error(f"No se pudo bajar el reel ({reel_url}): {e}")

    # 1) Instagram (reel remoto).
    if "instagram" in plats and reel_url:
        try:
            instagram.publish_reel(reel_url, caption)
            estado_canales["instagram"] = "ok"
            logger.info("[instagram] reel OK")
        except Exception as e:
            estado_canales["instagram"] = f"falló: {e}"
            logger.error(f"[instagram] reel FALLÓ: {e}")

    # 2) Facebook (archivo local).
    if "facebook" in plats and local_reel:
        try:
            facebook.publish_video(caption, local_reel)
            estado_canales["facebook"] = "ok"
            logger.info("[facebook] video OK")
        except Exception as e:
            estado_canales["facebook"] = f"falló: {e}"
            logger.error(f"[facebook] video FALLÓ: {e}")

    # 3) YouTube Shorts (mismo archivo vertical) — solo si hay noticia (necesita SEO).
    yt_info = {}
    if hay and _yt_enabled() and local_reel:
        try:
            from platforms import youtube_api
            meta = _youtube_meta(volanta, titulo, resumen, fila.get("texto", ""))
            privacy = (get("YT_SHORTS_PRIVACY") or "public").strip()
            yt_info = _retry(
                lambda: youtube_api.upload_short(
                    local_reel, meta["titulo"], meta["descripcion"],
                    tags=meta["tags"], category_id=meta["category_id"], privacy=privacy),
                etiqueta="[youtube] subir Short")
            estado_canales["youtube"] = "ok"
            logger.info(f"[youtube] Short OK: {yt_info.get('short_url')}")
        except Exception as e:
            estado_canales["youtube"] = f"falló: {e}"
            logger.error(f"[youtube] Short FALLÓ tras reintentos: {e}")
    elif hay and not _yt_enabled():
        logger.info("[youtube] desactivado (YT_SHORTS_ENABLED=0).")

    # 4) Nota web: embeber el YouTube (si salió) y PUBLICAR (al final del flujo).
    post_url = ""
    if hay and draft_id:
        if yt_info.get("url"):
            try:
                _retry(lambda: wix.insertar_video_youtube(draft_id, yt_info["url"]),
                       etiqueta="[wix] embeber YouTube")
            except Exception as e:
                logger.error(f"[wix] no se pudo embeber el YouTube (la nota igual sale con el "
                             f"video nativo): {e}")
        try:
            res = _retry(lambda: wix.publicar_borrador(draft_id), etiqueta="[wix] publicar")
            post_url = res.get("url", "")
            estado_canales["wix"] = "ok"
            logger.info(f"[wix] nota publicada: {post_url}")
        except Exception as e:
            estado_canales["wix"] = f"falló: {e}"
            logger.error(f"[wix] no se pudo publicar el borrador: {e}")
    else:
        logger.info("Sin desgrabación: la nota web queda SUSPENDIDA, sale solo el reel (sin texto).")

    # Registro: estado + datos del Short (URL, ID, fecha, título; métricas a futuro).
    fila.update({
        "estado": "publicado" if hay else "publicado_solo_reel",
        "fecha_publicado": datetime.now().isoformat(timespec="seconds"),
        "post_url": post_url,
        "estado_canales": estado_canales,
    })
    if yt_info:
        fila.update({
            "yt_video_id": yt_info.get("id", ""),
            "yt_url": yt_info.get("short_url") or yt_info.get("url", ""),
            "yt_watch_url": yt_info.get("watch_url", ""),
            "yt_titulo": titulo,
            "yt_privacy": yt_info.get("privacy", ""),
            "fecha_youtube": datetime.now().isoformat(timespec="seconds"),
            "yt_metrics": fila.get("yt_metrics", {}),  # se completan luego (--videos-report)
        })
    _guardar_ledger(rows)

    # Aviso de estado por canal (el «panel» de publicación).
    _avisar_estado(fila, estado_canales, post_url, yt_info)

    algun_ok = any(v == "ok" for v in estado_canales.values())
    if algun_ok:
        logger.info(f"Publicado y registrado. Estado por canal: {estado_canales}")
    else:
        logger.error("No se pudo publicar en ningún canal — revisar credenciales.")
    logger.info("=== Publicar video aprobado: fin ===")


def _placa_datos(carpeta: Path):
    """(docx, fotos, volanta, titular, resumen, texto, title, body) de una carpeta foto-nota."""
    from carrusel_notas import _resumen_caption
    docx = next((p for p in sorted(carpeta.iterdir())
                 if p.is_file() and p.suffix.lower() in (".docx", ".txt")), None)
    fotos = [p for p in sorted(carpeta.iterdir()) if p.is_file() and p.suffix.lower() in IMG_EXTS]
    if not docx or not fotos:
        return None
    volanta, titular, cuerpo = _parse_word(docx)
    if not titular:
        return None
    resumen = _resumen_caption(cuerpo[0], max_chars=280) if cuerpo else titular
    texto = "\n\n".join(cuerpo)
    title = f"{volanta} — {titular}" if volanta else titular
    body = titular + ("\n\n" + texto if texto else "")
    return docx, fotos, volanta, titular, resumen, texto, title, body


def run_placa(folder: str = "", uploader: str = "", dry_run: bool = False) -> None:
    """ETAPA 1 de la FOTO-NOTA: subcarpeta de «videos notas actualidad» con Word + foto(s)
    SIN video. Crea la nota como BORRADOR en Wix (la/s foto/s TAL CUAL + el texto) y avisa
    por mail para revisar. NO publica nada. Al aprobar (mover la carpeta a APROBADAS, o el
    botón) se publica la nota web y se postea la FOTO a FB/IG con TODO el texto en el caption."""
    modo = "SIMULACIÓN (dry-run)" if dry_run else "PROCESO REAL"
    logger.info(f"=== Foto-nota (etapa 1) [{modo}] — folder='{folder}' ===")
    carpeta = _find_folder(folder)
    if not carpeta:
        logger.error(f"No encontré la carpeta '{folder}' en {_videos_folder()}.")
        return
    datos = _placa_datos(carpeta)
    if not datos:
        logger.error(f"'{folder}' necesita un Word/txt con título + al menos una foto.")
        return
    _docx, fotos, volanta, titular, resumen, texto, title, body = datos

    rows = _leer_ledger()
    fila = _buscar_fila(rows, carpeta.name)
    if not dry_run and fila and fila.get("estado") in ("borrador_placa", "publicado_placa"):
        logger.info(f"La foto-nota '{carpeta.name}' ya fue procesada (estado={fila['estado']}).")
        return

    if dry_run:
        logger.info(f"[dry-run] foto-nota «{title}»: {len(fotos)} foto(s) tal cual + texto.\n"
                    f"  VOLANTA: {volanta}\n  TÍTULO: {titular}")
        return

    draft_id = ""
    try:
        info = wix.crear_borrador_galeria(title, body, fotos, video_urls=[], page=0, description=resumen)
        draft_id = info["draft_id"]
    except Exception as e:
        logger.error(f"[wix] no se pudo crear el borrador: {e}")
        return

    if fila is None:
        fila = {"file": carpeta.name}
        rows.append(fila)
    fila.update({
        "uploader": uploader or fila.get("uploader", ""),
        "fecha_recibido": datetime.now().isoformat(timespec="seconds"),
        "hay_noticia": True, "es_placa": True, "volanta": volanta, "titulo": titular,
        "resumen": resumen, "texto": texto, "draft_id": draft_id, "estado": "borrador_placa",
    })
    _guardar_ledger(rows)
    logger.info(f"Foto-nota registrada como BORRADOR (draft_id={draft_id}).")

    webapp = get("APPROVE_WEBAPP_URL")
    tok = get("WEBAPP_TOKEN")
    t = f"&token={quote(tok)}" if tok else ""
    botones = ""
    if webapp:
        botones += _boton(f"{webapp}?action=approve&name={quote(carpeta.name)}&kind=folder{t}", "✅ Aprobar y publicar")
        if draft_id:
            botones += _boton(f"{webapp}?action=delete&post={quote(draft_id)}{t}", "🗑️ Borrar borrador", color="#b00020")
    html = (f"<div style='font-family:Arial;max-width:600px;color:#222;font-size:16px'>"
            f"<h2 style='color:#e2620c'>Foto-nota por revisar</h2>"
            f"<p style='color:#888;font-size:13px'>{_hesc(volanta)} · {len(fotos)} foto(s)</p>"
            f"<p style='font-size:19px'><b>{_hesc(titular)}</b></p>"
            f"<p style='white-space:pre-wrap'>{_hesc(texto or resumen)}</p>"
            f"<p>Está como <b>borrador en Wix</b> con la foto y el texto. Para PUBLICAR "
            f"(nota web + un REEL de la foto a Facebook/Instagram con todo el texto en el pie):</p>"
            f"<div style='margin:18px 0'>{botones}</div>"
            f"<p style='color:#777;font-size:13px'>Si no ves los botones, aprobá moviendo la "
            f"carpeta «{_hesc(carpeta.name)}» a APROBADAS en Drive.</p></div>")
    _enviar_aviso(f"Foto-nota por revisar: {titular}",
                  f"Foto-nota para revisar: «{title}».\nPara publicarla, mové la carpeta "
                  f"«{carpeta.name}» a APROBADAS.", html=html)
    logger.info("=== Foto-nota (etapa 1): fin ===")


def run_placa_publish(folder: str = "", dry_run: bool = False) -> None:
    """ETAPA 2 de la FOTO-NOTA (al aprobar): publica la nota web + postea la/s FOTO/s a FB/IG
    con TODO el texto en el caption. Sin gráfica, sin video."""
    modo = "SIMULACIÓN (dry-run)" if dry_run else "PUBLICACIÓN REAL"
    logger.info(f"=== Foto-nota (etapa 2: publicar) [{modo}] — folder='{folder}' ===")
    rows = _leer_ledger()
    fila = _buscar_fila(rows, folder) if folder else None
    if fila is None:
        pend = [r for r in rows if r.get("es_placa") and r.get("estado") == "borrador_placa"]
        fila = pend[-1] if pend else None
    if fila is None:
        logger.error(f"No hay foto-nota pendiente para '{folder}'.")
        return
    if fila.get("estado") == "publicado_placa":
        logger.info(f"La foto-nota '{fila['file']}' ya estaba publicada.")
        return

    carpeta = _find_folder(fila["file"])
    fotos = [p for p in sorted(carpeta.iterdir())
             if p.is_file() and p.suffix.lower() in IMG_EXTS] if carpeta else []
    if not fotos:
        logger.error(f"No encontré las fotos de '{fila['file']}' para postear.")
        return

    titular = fila.get("titulo", ""); volanta = fila.get("volanta", "")
    texto = fila.get("texto", ""); resumen = fila.get("resumen", "")
    draft_id = fila.get("draft_id", "")
    title = f"{volanta} — {titular}" if volanta else titular
    site = _site()
    # TODO el texto en el caption (pedido del usuario): título + cuerpo completo + CTA.
    caption = f"{titular}\n\n{texto}\n\n📲 Seguí leyendo en {site}".strip()

    if dry_run:
        logger.info(f"[dry-run] publicaría foto-nota «{title}» con {len(fotos)} foto(s) + caption completo.")
        return

    plats = _platforms()
    estado = {"instagram": "omitido", "facebook": "omitido", "wix": "omitido"}
    post_url = ""
    if draft_id:
        try:
            res = _retry(lambda: wix.publicar_borrador(draft_id), etiqueta="[wix] publicar")
            post_url = (res or {}).get("url", "")
            estado["wix"] = "ok"
            logger.info(f"[wix] nota publicada: {post_url}")
        except Exception as e:
            estado["wix"] = f"falló: {e}"; logger.error(f"[wix] FALLÓ: {e}")

    # REEL: la/s foto/s encuadrada/s a 9:16 (SIN gráfica) como video vertical + caption completo.
    reel_url, reel_local = "", None
    try:
        from story_image import compose_foto_reel
        from video import build_slideshow
        WORK_DIR.mkdir(exist_ok=True)
        slides = [compose_foto_reel(f) for f in fotos]
        reel_local = build_slideshow(slides, WORK_DIR / f"placa_{_slug(fila['file'])}.mp4")
        reel_url = upload_reel(reel_local)
    except Exception as e:
        logger.error(f"No se pudo armar el reel de la foto-nota: {e}")

    if "instagram" in plats and reel_url:
        try:
            instagram.publish_reel(reel_url, caption); estado["instagram"] = "ok"
            logger.info("[instagram] reel OK")
        except Exception as e:
            estado["instagram"] = f"falló: {e}"; logger.error(f"[instagram] reel FALLÓ: {e}")
    if "facebook" in plats and reel_local:
        try:
            facebook.publish_video(caption, reel_local); estado["facebook"] = "ok"
            logger.info("[facebook] reel OK")
        except Exception as e:
            estado["facebook"] = f"falló: {e}"; logger.error(f"[facebook] reel FALLÓ: {e}")

    fila.update({"estado": "publicado_placa", "post_url": post_url,
                 "fecha_publicado": datetime.now().isoformat(timespec="seconds"),
                 "estado_canales": estado})
    _guardar_ledger(rows)

    borrar = ""
    webapp = get("APPROVE_WEBAPP_URL")
    if webapp and draft_id and estado["wix"] == "ok":
        tok = get("WEBAPP_TOKEN"); t = f"&token={quote(tok)}" if tok else ""
        borrar = (f"<div style='margin:14px 0'>"
                  f"{_boton(f'{webapp}?action=delete&post={quote(draft_id)}{t}', '🗑️ Borrar de la web', color='#b00020')}"
                  f"</div>")
    html = (f"<div style='font-family:Arial;max-width:600px;color:#222;font-size:16px'>"
            f"<h2 style='color:#e2620c'>Foto-nota publicada</h2>"
            f"<p style='font-size:18px'><b>{_hesc(titular)}</b></p>"
            f"<ul style='line-height:1.8;list-style:none;padding:0'>"
            f"<li>{'✅' if estado['instagram'] == 'ok' else '❌'} Instagram</li>"
            f"<li>{'✅' if estado['facebook'] == 'ok' else '❌'} Facebook</li>"
            f"<li>{'✅' if estado['wix'] == 'ok' else '❌'} Web"
            + (f" — <a href='{post_url}'>{_hesc(post_url)}</a>" if post_url else "") + "</li>"
            f"</ul>{borrar}</div>")
    _enviar_aviso(f"Foto-nota publicada: {titular}",
                  f"Se publicó «{title}» (reel de la foto a FB/IG + nota web).\n{post_url}", html=html)
    logger.info("=== Foto-nota (etapa 2): fin ===")


def _avisar_estado(fila: dict, estado: dict, post_url: str, yt_info: dict) -> None:
    """Manda un mail con el ESTADO de publicación por canal (IG/FB/YouTube/Wix)."""
    titulo = fila.get("titulo") or fila.get("file", "")
    iconos = {"ok": "✅", "omitido": "➖"}

    def _li(nombre: str, clave: str, extra: str = "") -> str:
        st = estado.get(clave, "omitido")
        ico = iconos.get(st, "❌")
        txt = "publicado" if st == "ok" else st
        return f"<li>{ico} <b>{nombre}:</b> {_hesc(txt)}{extra}</li>"

    yt_extra = ""
    if yt_info.get("short_url"):
        priv = yt_info.get("privacy", "")
        nota_priv = " <i>(privado — falta auditoría de la API)</i>" if priv and priv != "public" else ""
        yt_extra = f' — <a href="{yt_info["short_url"]}">{_hesc(yt_info["short_url"])}</a>{nota_priv}'
    wix_extra = f' — <a href="{post_url}">{_hesc(post_url)}</a>' if post_url else ""

    # Botón «Borrar de la web»: borra SOLO la nota de Wix (no FB/IG/YouTube).
    borrar = ""
    webapp = get("APPROVE_WEBAPP_URL")
    pid = fila.get("draft_id")
    if webapp and pid and estado.get("wix") == "ok":
        tok = get("WEBAPP_TOKEN")
        t = f"&token={quote(tok)}" if tok else ""
        borrar = (f"<div style='margin:14px 0'>"
                  f"{_boton(f'{webapp}?action=delete&post={quote(pid)}{t}', '🗑️ Borrar de la web', color='#b00020')}"
                  f"</div><p style='color:#999;font-size:12px'>Solo borra la nota de la web; "
                  f"el reel de FB/IG y el Short de YouTube se borran a mano.</p>")

    html = (
        f"<div style='font-family:Arial;max-width:600px;color:#222;font-size:16px'>"
        f"<h2 style='color:#e2620c'>Estado de publicación</h2>"
        f"<p style='font-size:18px'><b>{_hesc(titulo)}</b></p>"
        f"<ul style='line-height:1.8;list-style:none;padding:0'>"
        f"{_li('Instagram', 'instagram')}"
        f"{_li('Facebook', 'facebook')}"
        f"{_li('YouTube Shorts', 'youtube', yt_extra)}"
        f"{_li('Web (Wix)', 'wix', wix_extra)}"
        f"</ul>{borrar}</div>"
    )
    cuerpo = (f"Estado de publicación de «{titulo}»:\n"
              f"- Instagram: {estado.get('instagram')}\n"
              f"- Facebook: {estado.get('facebook')}\n"
              f"- YouTube: {estado.get('youtube')}"
              + (f" ({yt_info.get('short_url')})" if yt_info.get('short_url') else "") + "\n"
              f"- Web (Wix): {estado.get('wix')}" + (f" ({post_url})" if post_url else "") + "\n")
    _enviar_aviso(f"Estado de publicación: {titulo}", cuerpo, html=html)
