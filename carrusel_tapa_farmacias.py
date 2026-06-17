"""TAPA + FARMACIAS (08:00) — SOLO HISTORIAS.

Publica en Facebook + Instagram DOS historias: la tapa del diario y las farmacias
de turno de hoy. NO publica nada en el feed (el posteo/carrusel quedó anulado).
"""
import json
from datetime import date
from pathlib import Path

import farmacias as farm
import tapa as tapa_mod
from platforms import facebook, instagram
from story_image import compose_tapa_story
from utils.config import get
from utils.logger import get_logger

logger = get_logger("carrusel_tf")

LEDGER = Path(__file__).parent / ".carrusel_tf.json"


def _site() -> str:
    return get("STORY_SITE_URL") or "www.diariolacampaña.com.ar"


def _platforms() -> list[str]:
    raw = get("STORIES_PLATFORMS") or "instagram,facebook"
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _ya_hoy(hoy: date) -> bool:
    try:
        if LEDGER.exists():
            return json.loads(LEDGER.read_text(encoding="utf-8")).get("fecha") == hoy.isoformat()
    except Exception:
        pass
    return False


def _marcar(hoy: date) -> None:
    LEDGER.write_text(json.dumps({"fecha": hoy.isoformat()}, ensure_ascii=False), encoding="utf-8")


def run_tapa_farmacias(dry_run: bool = False) -> None:
    modo = "SIMULACIÓN (dry-run)" if dry_run else "PUBLICACIÓN REAL"
    hoy = date.today()
    logger.info(f"=== Tapa+Farmacias (HISTORIAS) [{modo}] — {hoy.isoformat()} ===")

    if not dry_run and _ya_hoy(hoy):
        logger.info("Las historias de tapa+farmacias de hoy ya se publicaron. Se omite.")
        return

    # 1) Tapa (imagen más nueva de TAPA_FOLDER)
    folder = Path(get("TAPA_FOLDER") or tapa_mod.DEFAULT_FOLDER)
    tapa_img = tapa_mod._resolver_tapa(folder)
    if not tapa_img:
        logger.error(f"No hay imagen de tapa en {folder}. No se publican las historias.")
        return
    logger.info(f"Tapa: {tapa_img.name}")

    # 2) Farmacias de hoy (imagen de historia + datos)
    feed_farm, story_farm, lineas_cap, nombres, es_cambio = farm.farmacias_feed_de_hoy(hoy)
    if not story_farm:
        logger.error(f"Sin datos de farmacias: {lineas_cap}. No se publican las historias.")
        return
    logger.info(f"Farmacias: {', '.join(nombres)}")

    fecha = farm._fecha_larga(hoy).capitalize()

    # SOLO HISTORIAS (el posteo/carrusel al feed quedó anulado): tapa + farmacias.
    story_tapa = compose_tapa_story(tapa_img, fecha)
    historias = [("tapa", story_tapa), ("farmacias", story_farm)]

    if dry_run:
        logger.info(f"[dry-run] historias {[n for n, _ in historias]} "
                    f"({[p.name for _, p in historias]}) en FB+IG (NO se publica)")
        logger.info("=== Tapa+Farmacias (historias): fin (dry-run) ===")
        return

    plats = _platforms()
    algun_ok = False
    for etiqueta, img in historias:
        for name in plats:
            fn = {"instagram": instagram.publish_story, "facebook": facebook.publish_story}.get(name)
            if not fn:
                continue
            try:
                fn(img)
                algun_ok = True
                logger.info(f"[{name}] historia {etiqueta} OK")
            except Exception as e:
                logger.error(f"[{name}] historia {etiqueta} FALLÓ: {e}")

    if algun_ok:
        _marcar(hoy)
        logger.info("Tapa+Farmacias (historias) registrado como publicado hoy.")
    else:
        logger.error("No se pudo publicar ninguna historia — se reintentará la próxima corrida.")

    logger.info("=== Tapa+Farmacias (historias): fin ===")
