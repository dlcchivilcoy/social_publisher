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
            botones += _boton(reel_url, "👁️ Previsualizar video", color="#444")
        if hay and draft_id:
            botones += _boton(f"{webapp}?action=edit&draft={draft_id}{t}", "✏️ Corregir texto", color="#444")
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

    # Reel para redes: si el original supera 1 min y Gemini marcó tramos, lo resume a las
    # MEJORES PARTES (≤60s); si no, va entero. Sin noticia: recortado a 60s.
    reel_path = WORK_DIR / f"reel_{slug}.mp4"
    fuente_reel = video
    if hay:
        dur = duration_seconds(video)
        if dur > 60 and nota.get("segmentos"):
            resumido = best_parts_clip(video, nota["segmentos"], WORK_DIR / f"resumen_{slug}.mp4", max_total=60)
            if resumido:
                fuente_reel = resumido
                logger.info(f"Video de {dur:.0f}s resumido a las mejores partes para el reel (≤60s).")
        reel = to_vertical_reel(fuente_reel, reel_path)
    else:
        reel = to_vertical_reel(video, reel_path, max_seconds=REEL_MAX_SIN_NOTICIA)

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
        "draft_id": draft_id, "reel_url": reel_url, "estado": estado,
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
    titulo = fila.get("titulo", "")
    resumen = fila.get("resumen", "")
    caption = _caption(titulo, resumen) if hay else ""

    if dry_run:
        logger.info(f"[dry-run] hay_noticia={hay}. Publicaría draft={draft_id or '—'} + reel={reel_url}\n"
                    f"Caption:\n{caption or '(sin texto)'}")
        return

    # 1) Nota web (solo si hay noticia).
    post_url = ""
    if hay and draft_id:
        try:
            res = wix.publicar_borrador(draft_id)
            post_url = res.get("url", "")
            logger.info(f"[wix] nota publicada: {post_url}")
        except Exception as e:
            logger.error(f"[wix] no se pudo publicar el borrador: {e}")
    else:
        logger.info("Sin desgrabación: la nota web queda SUSPENDIDA, sale solo el reel (sin texto).")

    # 2) Reel a las redes (con caption si hay noticia; vacío si no).
    plats = _platforms()
    algun_ok = False
    if "instagram" in plats and reel_url:
        try:
            instagram.publish_reel(reel_url, caption)
            algun_ok = True
            logger.info("[instagram] reel OK")
        except Exception as e:
            logger.error(f"[instagram] reel FALLÓ: {e}")
    if "facebook" in plats and reel_url:
        try:
            WORK_DIR.mkdir(exist_ok=True)
            local = _descargar(reel_url, WORK_DIR / "reel_pub.mp4")
            facebook.publish_video(caption, local)
            algun_ok = True
            logger.info("[facebook] video OK")
        except Exception as e:
            logger.error(f"[facebook] video FALLÓ: {e}")

    fila.update({
        "estado": "publicado" if hay else "publicado_solo_reel",
        "fecha_publicado": datetime.now().isoformat(timespec="seconds"),
        "post_url": post_url,
    })
    _guardar_ledger(rows)

    if algun_ok or post_url:
        logger.info("Publicado (web y/o reel) y registrado.")
    else:
        logger.error("No se pudo publicar en ninguna parte — revisar credenciales.")
    logger.info("=== Publicar video aprobado: fin ===")
