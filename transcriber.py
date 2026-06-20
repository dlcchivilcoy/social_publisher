"""Desgrabador audiovisual → nota web (Wix) + reel a Facebook e Instagram.

Pensado para correr en la NUBE (GitHub Actions), disparado por un Google Apps Script
cuando un colaborador sube un video a la carpeta de Drive «videos notas actualidad».
NO usa tokens de Claude: la desgrabación + redacción la hace Gemini (gratis).

Dos etapas (flujo CON revisión):

  ETAPA 1 — `run_transcribe_video(file, uploader)`  (al subir el video):
    1. extrae el audio del video
    2. Gemini desgraba → {volanta, titulo, texto, resumen}
    3. saca el frame más representativo = foto de portada
    4. arma el reel vertical 9:16 y lo sube a un GitHub Release (URL pública)
    5. crea la nota como BORRADOR en Wix (foto de portada + video nativo embebido)
    6. registra la fila de contabilidad (.videos_contabilidad.json)
    7. avisa por mail al diario con el dato para revisar/aprobar

  ETAPA 2 — `run_publish_video(file)`  (cuando el editor mueve el video a APROBADAS):
    1. publica el borrador de Wix
    2. postea el reel a Facebook (video) e Instagram (reel) con el resumen de caption
    3. marca la fila de contabilidad como publicada
"""
import json
import smtplib
import ssl
import tempfile
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

import requests

from platforms import facebook, instagram, wix
from utils.config import get
from utils.gemini import transcribe_to_nota
from utils.logger import get_logger
from utils.video_host import upload_reel
from video import best_frame, extract_audio, to_vertical_reel

logger = get_logger("transcriber")

LEDGER = Path(__file__).parent / ".videos_contabilidad.json"
WORK_DIR = Path(__file__).parent / "videos_preview"
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mpg", ".mpeg"}


# ── Helpers de entorno ────────────────────────────────────────────────────────
def _site() -> str:
    return get("STORY_SITE_URL") or "www.diariolacampaña.com.ar"


def _platforms() -> list[str]:
    raw = get("STORIES_PLATFORMS") or "instagram,facebook"
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _videos_folder() -> Path:
    return Path(get("VIDEOS_FOLDER") or (Path(__file__).parent / "videos"))


def _find_video(name: str) -> Path | None:
    """Ubica el video bajado de Drive por nombre; si no, agarra el más nuevo."""
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
def _enviar_aviso(asunto: str, cuerpo: str) -> None:
    """Manda un mail simple al diario (reusa el SMTP del mailer). Best-effort."""
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


# ── ETAPA 1: preparar borrador ────────────────────────────────────────────────
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
    if not dry_run and fila and fila.get("estado") in ("borrador", "publicado"):
        logger.info(f"El video '{video.name}' ya fue procesado (estado={fila['estado']}). Nada que hacer.")
        return

    WORK_DIR.mkdir(exist_ok=True)
    audio = extract_audio(video, WORK_DIR / "audio.mp3")
    nota = transcribe_to_nota(audio)

    cover = best_frame(video, WORK_DIR / "portada.jpg")
    reel = to_vertical_reel(video, WORK_DIR / "reel.mp4")

    volanta, titulo = nota["volanta"], nota["titulo"]
    texto, resumen = nota["texto"], nota["resumen"]
    title = f"{volanta} — {titulo}" if volanta else titulo
    body = titulo + ("\n\n" + texto if texto else "")

    if dry_run:
        logger.info(f"[dry-run] Nota:\n  VOLANTA: {volanta}\n  TÍTULO: {titulo}\n"
                    f"  RESUMEN: {resumen}\n  TEXTO:\n{texto}")
        logger.info(f"[dry-run] Portada: {cover}  Reel: {reel}")
        logger.info("=== Desgrabar video: fin (dry-run) ===")
        return

    # Subir el reel a una URL pública (la usa Instagram y también Wix para el video nativo).
    reel_url = upload_reel(reel)

    # Crear la nota como BORRADOR (sin publicar) con foto + video nativo.
    info = wix.crear_borrador(title, body, cover, page=0, description=resumen, video_url=reel_url)
    draft_id = info["draft_id"]

    # Registrar la fila de contabilidad.
    if fila is None:
        fila = {"file": video.name}
        rows.append(fila)
    fila.update({
        "uploader": uploader or fila.get("uploader", ""),
        "fecha_recibido": datetime.now().isoformat(timespec="seconds"),
        "volanta": volanta, "titulo": titulo, "resumen": resumen,
        "draft_id": draft_id, "reel_url": reel_url, "estado": "borrador",
    })
    _guardar_ledger(rows)
    logger.info(f"Borrador creado y registrado (draft_id={draft_id}).")

    # Avisar al diario para revisar/aprobar.
    cuerpo = (
        f"Llegó un video para revisar: «{titulo}»\n"
        f"Enviado por: {uploader or 'desconocido'}\n\n"
        f"VOLANTA: {volanta}\nTÍTULO: {titulo}\n\nRESUMEN: {resumen}\n\n"
        f"Está cargado como BORRADOR en Wix (Blog → Borradores) con la foto de portada y el video.\n\n"
        f"➡️ Para PUBLICARLO en la web y mandar el reel a Facebook e Instagram, "
        f"mové el video «{video.name}» a la subcarpeta APROBADAS dentro de "
        f"«videos notas actualidad» en Google Drive."
    )
    _enviar_aviso(f"Nota por revisar: {titulo}", cuerpo)
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
    # Buscar por nombre exacto; si no, la última fila en estado borrador.
    fila = _buscar_fila(rows, file) if file else None
    if fila is None:
        borradores = [r for r in rows if r.get("estado") == "borrador"]
        fila = borradores[-1] if borradores else None
    if fila is None:
        logger.error(f"No hay un borrador pendiente para '{file}'. Nada que publicar.")
        return
    if fila.get("estado") == "publicado":
        logger.info(f"El video '{fila['file']}' ya estaba publicado. Nada que hacer.")
        return

    draft_id = fila.get("draft_id")
    reel_url = fila.get("reel_url")
    titulo = fila.get("titulo", "")
    resumen = fila.get("resumen", "")
    caption = _caption(titulo, resumen)

    if dry_run:
        logger.info(f"[dry-run] Publicaría draft={draft_id} + reel={reel_url}\nCaption:\n{caption}")
        return

    # 1) Publicar la nota en la web.
    post_url = ""
    try:
        res = wix.publicar_borrador(draft_id)
        post_url = res.get("url", "")
        logger.info(f"[wix] nota publicada: {post_url}")
    except Exception as e:
        logger.error(f"[wix] no se pudo publicar el borrador: {e}")

    # 2) Reel a las redes. Instagram baja el mp4 de la URL; Facebook necesita el archivo.
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

    # 3) Marcar como publicado.
    fila.update({
        "estado": "publicado",
        "fecha_publicado": datetime.now().isoformat(timespec="seconds"),
        "post_url": post_url,
    })
    _guardar_ledger(rows)

    if algun_ok or post_url:
        logger.info("Video publicado (web y/o redes) y registrado.")
    else:
        logger.error("No se pudo publicar en ninguna parte — revisar credenciales.")
    logger.info("=== Publicar video aprobado: fin ===")
