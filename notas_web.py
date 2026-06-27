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
_IMG_EXT = {".jpg", ".jpeg", ".png", ".webp"}
_DOC_EXT = {".docx"}


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


def _primero(carpeta: Path, exts: set) -> Path | None:
    for p in sorted(carpeta.iterdir()):
        if p.is_file() and p.suffix.lower() in exts:
            return p
    return None


def _parse_docx(docx_path: Path) -> tuple[str, str, list[str]]:
    """(volanta, titular, cuerpo_parrafos). Reusa la heurística del carrusel."""
    from carrusel_notas import _parse_nota
    return _parse_nota(docx_path)


def run_notas_web(dry_run: bool = False) -> None:
    """Publica a la web (y reel a FB/IG) las notas nuevas de la carpeta «notas para web».
    Cada subcarpeta = una nota. Idempotente."""
    from transcriber import (_caption, _enviar_aviso, _hesc, _retry, _slug,
                             _yt_enabled, _youtube_meta)
    from carrusel_notas import _resumen_caption
    from platforms import facebook, instagram, wix
    from video import remux_mp4, to_vertical_reel

    modo = "SIMULACIÓN (dry-run)" if dry_run else "PUBLICACIÓN REAL"
    base = _folder()
    logger.info(f"=== Notas para la web [{modo}] — carpeta: {base} ===")
    if not base.exists():
        logger.info(f"La carpeta «{base}» no existe todavía. Nada que hacer.")
        return

    subcarpetas = [d for d in sorted(base.iterdir()) if d.is_dir()
                   and d.name.upper() not in ("PUBLICADAS", "APROBADAS")]
    if not subcarpetas:
        logger.info("No hay notas (subcarpetas) en «notas para web».")
        return

    ledger = _leer_ledger()
    work = Path(__file__).parent / "videos_preview"
    plats = [p.strip().lower() for p in (get("STORIES_PLATFORMS") or "instagram,facebook").split(",") if p.strip()]

    hechas = 0
    for carpeta in subcarpetas:
        key = carpeta.name
        if key in ledger:
            continue
        docx = _primero(carpeta, _DOC_EXT)
        foto = _primero(carpeta, _IMG_EXT)
        video = _primero(carpeta, _VIDEO_EXT)
        if not docx or not foto:
            logger.warning(f"«{key}»: falta el Word o la foto (docx={bool(docx)}, foto={bool(foto)}). Se saltea.")
            continue

        volanta, titular, cuerpo = _parse_docx(docx)
        if not titular:
            logger.warning(f"«{key}»: el Word no tiene título legible. Se saltea.")
            continue
        resumen = _resumen_caption(cuerpo[0], max_chars=280) if cuerpo else titular
        title = f"{volanta} — {titular}" if volanta else titular
        body = titular + ("\n\n" + "\n\n".join(cuerpo) if cuerpo else "")

        if dry_run:
            logger.info(f"[dry-run] «{key}» → Wix: «{title}» | foto={foto.name} | "
                        f"video={video.name if video else '—'} | reel={'sí' if video else 'no'}")
            continue

        # Reel + hosting del video (si hay), para embeber en la web y postear a redes.
        work.mkdir(exist_ok=True)
        reel_url, local_reel, web_video_url = "", None, ""
        if video:
            try:
                from utils.video_host import upload_reel
                slug = _slug(carpeta.name)
                local_reel = to_vertical_reel(video, work / f"reel_web_{slug}.mp4")
                reel_url = upload_reel(local_reel)
                web_video_url = reel_url
                try:  # la web lleva el video completo (no el reel recortado)
                    full = remux_mp4(video, work / f"video_web_{slug}.mp4")
                    web_video_url = upload_reel(full)
                except Exception as e:
                    logger.warning(f"«{key}»: no pude hostear el video completo ({e}); uso el reel.")
            except Exception as e:
                logger.error(f"«{key}»: falló el armado/subida del reel: {e}. Sigo con la nota web sin video.")

        # 1) Nota web (Wix) con foto + video embebido → publicar.
        post_url = ""
        try:
            info = wix.crear_borrador(title, body, foto, page=0, description=resumen,
                                      video_url=web_video_url)
            draft_id = info["draft_id"]
            # YouTube Short opcional: si está activo y hay video, reemplaza el video nativo.
            if video and local_reel and _yt_enabled():
                try:
                    from platforms import youtube_api
                    meta = _youtube_meta(volanta, titular, resumen, body)
                    privacy = (get("YT_SHORTS_PRIVACY") or "public").strip()
                    yt = _retry(lambda: youtube_api.upload_short(
                        local_reel, meta["titulo"], meta["descripcion"],
                        tags=meta["tags"], category_id=meta["category_id"], privacy=privacy),
                        etiqueta="[youtube] subir Short")
                    if yt.get("url"):
                        _retry(lambda: wix.insertar_video_youtube(draft_id, yt["url"]),
                               etiqueta="[wix] embeber YouTube")
                except Exception as e:
                    logger.warning(f"«{key}»: YouTube Short falló ({e}); la nota sale con el video nativo.")
            res = _retry(lambda: wix.publicar_borrador(draft_id), etiqueta="[wix] publicar")
            post_url = (res or {}).get("url", "")
            logger.info(f"[wix] nota publicada: {post_url or '(sin URL)'}")
        except Exception as e:
            logger.error(f"«{key}»: NO se pudo publicar la nota en Wix: {e}. Se reintenta la próxima.")
            continue  # no se marca: se reintenta

        # 2) Reel a FB/IG (solo si hay video).
        caption = _caption(titular, resumen)
        if video and reel_url and "instagram" in plats:
            try:
                instagram.publish_reel(reel_url, caption)
                logger.info("[instagram] reel OK")
            except Exception as e:
                logger.error(f"[instagram] reel FALLÓ: {e}")
        if video and local_reel and "facebook" in plats:
            try:
                facebook.publish_video(caption, local_reel)
                logger.info("[facebook] reel OK")
            except Exception as e:
                logger.error(f"[facebook] reel FALLÓ: {e}")

        ledger.add(key)
        _guardar_ledger(ledger)
        hechas += 1

        # Aviso por mail (best-effort).
        intro = (f"<h2 style='color:#e2620c'>Nota publicada en la web</h2>"
                 f"<p style='color:#888;font-size:13px'>{_hesc(volanta)}</p>"
                 f"<p style='font-size:19px'><b>{_hesc(titular)}</b></p>"
                 f"<p>{_hesc(resumen)}</p>"
                 + (f"<p><a href='{_hesc(post_url)}'>{_hesc(post_url)}</a></p>" if post_url else "")
                 + (f"<p>Reel publicado en Facebook e Instagram.</p>" if video else ""))
        _enviar_aviso(f"Nota web publicada: {titular}",
                      f"Se publicó «{title}» en la web{' + reel a FB/IG' if video else ''}.\n{post_url}",
                      html=intro)

    logger.info(f"=== Notas para la web: {hechas} nota(s) publicada(s) ===")
