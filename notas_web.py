"""Notas para la WEB (carpeta «notas para web»).

El colaborador deja, por cada nota, UNA SUBCARPETA dentro de «notas para web» con:
  - un Word (.docx) ya redactado en formato periodístico: 1er párrafo corto = VOLANTA,
    luego el TÍTULO y los PÁRRAFOS del cuerpo (mismo formato que el carrusel),
  - una FOTO (.jpg/.png) de portada,
  - opcionalmente un VIDEO (.mp4/...).

Por cada subcarpeta nueva:
  1. publica la NOTA en la web (Wix) con la foto de portada y, si hay video, el video
     embebido (nativo, o YouTube Short si YT_SHORTS_ENABLED=1),
  2. si hay video, arma un REEL vertical y lo postea a Facebook e Instagram @diarioyradio.

A diferencia del desgrabador, acá el TEXTO ya viene escrito (no lo arma Gemini) y se
publica de una (sin etapa de revisión). Un registro (`.notas_web.json`) evita repetir.

Config (.env): NOTAS_WEB_FOLDER (carpeta local; en la nube la baja el workflow a notas_web/).
Reusa el SMTP/Wix/FB/IG/YouTube del resto del sistema.
"""
import json
from pathlib import Path

from utils.config import get
from utils.logger import get_logger

logger = get_logger("notas_web")

LEDGER = Path(__file__).parent / ".notas_web.json"

_VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".mpg", ".mpeg"}
_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
# Texto de la nota: Word (.docx; un Google Doc llega como .docx por rclone) o .txt.
_DOC_EXT = {".docx", ".txt"}


def _folder() -> Path:
    raw = get("NOTAS_WEB_FOLDER")
    if raw:
        return Path(raw)
    # En la nube el workflow baja la carpeta de Drive a ./notas_web
    return Path(__file__).parent / "notas_web"


def _leer_ledger() -> set:
    try:
        if LEDGER.exists():
            return set(json.loads(LEDGER.read_text(encoding="utf-8")))
    except Exception:
        logger.warning("No se pudo leer .notas_web.json; se asume vacío.")
    return set()


def _guardar_ledger(keys: set) -> None:
    LEDGER.write_text(json.dumps(sorted(keys)[-1000:], ensure_ascii=False, indent=2),
                      encoding="utf-8")


def _todos(carpeta: Path, exts: set) -> list[Path]:
    """Todos los archivos de la carpeta con esas extensiones, ordenados por nombre."""
    return [p for p in sorted(carpeta.iterdir())
            if p.is_file() and p.suffix.lower() in exts]


def _parse_docx(docx_path: Path) -> tuple[str, str, list[str]]:
    """(volanta, titular, cuerpo_parrafos) desde un .docx o un .txt. Misma heurística que
    el carrusel: 1er párrafo corto = volanta; si no, el 1ro es el titular."""
    if docx_path.suffix.lower() == ".txt":
        raw = docx_path.read_text(encoding="utf-8", errors="replace")
        paras = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        if not paras:
            return "", "", []
        es_volanta = len(paras) >= 2 and (len(paras[0]) <= 55 or len(paras[0].split()) <= 7)
        if es_volanta:
            return paras[0], paras[1], paras[2:]
        return "", paras[0], paras[1:]
    from carrusel_notas import _parse_nota
    return _parse_nota(docx_path)


def _boton_borrar(draft_id: str) -> str:
    """Botón HTML «Borrar de la web» (web app de Apps Script). Vacío si no hay web app."""
    from urllib.parse import quote
    from transcriber import _boton
    webapp = get("APPROVE_WEBAPP_URL")
    tok = get("WEBAPP_TOKEN")
    if not webapp or not draft_id:
        return ""
    t = f"&token={quote(tok)}" if tok else ""
    return _boton(f"{webapp}?action=delete&post={quote(draft_id)}{t}",
                  "🗑️ Borrar de la web", color="#b00020")


def run_notas_web(dry_run: bool = False) -> None:
    """Publica SOLO en la web (Wix) las notas nuevas de «notas para web». Cada subcarpeta
    = una nota: Word (texto) + TODAS las fotos (galería) + TODOS los videos COMPLETOS
    (embebidos, sin recorte). NO va a redes. Manda un mail con botón «Borrar de la web».
    Idempotente."""
    from transcriber import _enviar_aviso, _hesc, _retry, _slug
    from carrusel_notas import _resumen_caption
    from platforms import wix
    from utils.video_host import upload_reel
    from video import remux_mp4

    modo = "SIMULACIÓN (dry-run)" if dry_run else "PUBLICACIÓN REAL"
    base = _folder()
    logger.info(f"=== Notas para la web [{modo}] — carpeta: {base} ===")
    if not base.exists():
        logger.info(f"La carpeta «{base}» no existe todavía. Nada que hacer.")
        return

    subcarpetas = [d for d in sorted(base.iterdir()) if d.is_dir()
                   and d.name.upper() not in ("PUBLICADAS", "APROBADAS", "BORRAR")]
    if not subcarpetas:
        logger.info("No hay notas (subcarpetas) en «notas para web».")
        return

    ledger = _leer_ledger()
    work = Path(__file__).parent / "videos_preview"
    hechas = 0
    for carpeta in subcarpetas:
        key = carpeta.name
        if key in ledger:
            continue
        docx = (_todos(carpeta, _DOC_EXT) or [None])[0]
        fotos = _todos(carpeta, _IMG_EXT)
        videos = _todos(carpeta, _VIDEO_EXT)
        if not docx or not fotos:
            logger.warning(f"«{key}»: falta el Word o las fotos (docx={bool(docx)}, "
                           f"fotos={len(fotos)}). Se saltea.")
            continue

        volanta, titular, cuerpo = _parse_docx(docx)
        if not titular:
            logger.warning(f"«{key}»: el Word no tiene título legible. Se saltea.")
            continue
        resumen = _resumen_caption(cuerpo[0], max_chars=280) if cuerpo else titular
        title = f"{volanta} — {titular}" if volanta else titular
        body = titular + ("\n\n" + "\n\n".join(cuerpo) if cuerpo else "")

        if dry_run:
            logger.info(f"[dry-run] «{key}» → Wix (solo web): «{title}» | "
                        f"{len(fotos)} foto/s | {len(videos)} video/s completo/s")
            continue

        # Hostear los videos COMPLETOS (sin recorte) para embeberlos como video nativo.
        work.mkdir(exist_ok=True)
        slug = _slug(carpeta.name)
        video_urls = []
        for i, v in enumerate(videos):
            try:
                full = remux_mp4(v, work / f"web_{slug}_{i}.mp4")
                video_urls.append(upload_reel(full))
            except Exception as e:
                logger.error(f"«{key}»: no pude hostear el video {v.name}: {e} (se omite ese video).")

        # Publicar SOLO en la web (Wix): galería de fotos + videos completos + texto.
        try:
            info = _retry(lambda: wix.crear_borrador_galeria(
                title, body, fotos, video_urls=video_urls, page=0, description=resumen),
                etiqueta="[wix] crear galería")
            draft_id = info["draft_id"]
            res = _retry(lambda: wix.publicar_borrador(draft_id), etiqueta="[wix] publicar")
            post_url = (res or {}).get("url", "")
            logger.info(f"[wix] nota web publicada: {post_url or '(sin URL)'}")
        except Exception as e:
            logger.error(f"«{key}»: NO se pudo publicar en Wix: {e}. Se reintenta la próxima.")
            continue  # no se marca: se reintenta

        ledger.add(key)
        _guardar_ledger(ledger)
        hechas += 1

        # Mail con botón «Borrar de la web».
        intro = (f"<h2 style='color:#e2620c'>Nota publicada en la web</h2>"
                 f"<p style='color:#888;font-size:13px'>{_hesc(volanta)}</p>"
                 f"<p style='font-size:19px'><b>{_hesc(titular)}</b></p>"
                 f"<p>{_hesc(resumen)}</p>"
                 f"<p>{len(fotos)} foto/s en galería · {len(video_urls)} video/s completo/s.</p>"
                 + (f"<p><a href='{_hesc(post_url)}'>{_hesc(post_url)}</a></p>" if post_url else "")
                 + f"<div style='margin:18px 0'>{_boton_borrar(draft_id)}</div>")
        _enviar_aviso(f"Nota web publicada: {titular}",
                      f"Se publicó «{title}» en la web.\n{post_url}\n\n"
                      f"Para borrarla, abrí este mail en formato HTML y tocá «Borrar de la web».",
                      html=intro)

    logger.info(f"=== Notas para la web: {hechas} nota(s) publicada(s) ===")
