"""MURO (feed) de FACEBOOK — 00:00 — tapa del día + foto de farmacias de turno.

Un solo posteo en el muro de la Página de Facebook con DOS fotos (galería): la tapa
del diario de hoy y la placa de farmacias de turno. SOLO Facebook (pedido del usuario).
Se programa a las 00:00 (arranque del día).

La tapa del día se sube la NOCHE ANTERIOR (~20 hs), así que a la medianoche ya está.
Si la tapa más nueva es vieja (fin de semana sin edición, o todavía no se subió), se
postea SOLO la foto de farmacias — nunca una tapa vieja.
"""
import json
import time
from datetime import date, datetime
from pathlib import Path

import farmacias as farm
import tapa as tapa_mod
from platforms import facebook
from publisher import _prepare_image
from utils.config import get
from utils.logger import get_logger

logger = get_logger("muro_tf")

LEDGER = Path(__file__).parent / ".muro_tf.json"


def _tapa_frescura_horas() -> float:
    try:
        return float(get("TAPA_FRESCA_HORAS") or 20)
    except ValueError:
        return 20.0


def _ya_hoy(hoy: date) -> bool:
    try:
        if LEDGER.exists():
            return json.loads(LEDGER.read_text(encoding="utf-8")).get("fecha") == hoy.isoformat()
    except Exception:
        pass
    return False


def _marcar(hoy: date) -> None:
    try:
        LEDGER.write_text(json.dumps({"fecha": hoy.isoformat()}, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning(f"No se pudo guardar el ledger del muro: {e}")


def _tapa_fresca(hoy: date) -> Path | None:
    """Devuelve la imagen de tapa SOLO si es reciente (subida la noche anterior).
    None si no hay, o si la más nueva es vieja (fin de semana / aún no subida)."""
    folder = Path(get("TAPA_FOLDER") or tapa_mod.DEFAULT_FOLDER)
    tapa = tapa_mod._resolver_tapa(folder)
    if not tapa:
        logger.info(f"No hay imagen de tapa en {folder}; se postea solo farmacias.")
        return None
    horas = (datetime.now().timestamp() - tapa.stat().st_mtime) / 3600.0
    if horas > _tapa_frescura_horas():
        logger.info(f"La tapa más nueva ({tapa.name}) tiene {horas:.0f}h de antigüedad "
                    f"(> {_tapa_frescura_horas():.0f}h): se considera vieja (fin de semana o aún "
                    f"no subida). Se postea solo farmacias.")
        return None
    return tapa


def run_muro_tapa_farmacias(dry_run: bool = False) -> None:
    modo = "SIMULACIÓN (dry-run)" if dry_run else "PUBLICACIÓN REAL"
    hoy = date.today()
    logger.info(f"=== Muro FB Tapa+Farmacias [{modo}] — {hoy.isoformat()} ===")

    if not dry_run and _ya_hoy(hoy):
        logger.info("El posteo del muro de hoy ya se publicó. Se omite.")
        return

    # 1) Farmacias de hoy — imagen de FEED (1080x1350) + líneas de texto.
    feed_farm, _story_farm, lineas_cap, nombres, es_cambio = farm.farmacias_feed_de_hoy(hoy)
    if not feed_farm:
        motivo = lineas_cap if isinstance(lineas_cap, str) else str(lineas_cap)
        logger.error(f"Sin datos de farmacias: {motivo}. No se publica el posteo del muro.")
        return
    logger.info(f"Farmacias: {', '.join(nombres)}")

    fecha = farm._fecha_larga(hoy).capitalize()

    # 2) Tapa (solo si es fresca) → preparada para el feed.
    imagenes: list[Path] = []
    tapa = _tapa_fresca(hoy)
    tapa_feed = None
    if tapa:
        logger.info(f"Tapa: {tapa.name}")
        try:
            tapa_feed = _prepare_image(tapa)
        except Exception as e:
            logger.error(f"No se pudo preparar la tapa para el muro: {e}")
            tapa_feed = tapa
        imagenes.append(Path(tapa_feed))
    imagenes.append(Path(feed_farm))

    # 3) Texto del posteo.
    partes = []
    if tapa:
        partes.append(f"📰 Tapa de hoy — Diario La Campaña · {fecha}")
    cabecera = "⚠️ *CAMBIO de turno de hoy*\n\n" if es_cambio else ""
    partes.append(cabecera + "💊 Farmacias de turno — " + fecha + "\n\n"
                  + "\n".join(lineas_cap)
                  + "\n\nLas dos primeras están de turno las 24 hs; la última, hasta las 22 hs.")
    caption = "\n\n".join(partes)

    if dry_run:
        logger.info(f"[dry-run] muro FB con {len(imagenes)} foto(s) "
                    f"({[p.name for p in imagenes]}) — NO se publica")
        for l in lineas_cap:
            logger.info(f"   {l}")
        _limpiar(tapa_feed, tapa)
        logger.info("=== Muro FB Tapa+Farmacias: fin (dry-run) ===")
        return

    # 4) Publicar (con reintentos por si FB tiene un hipo transitorio).
    intentos = max(1, int(get("MURO_RETRY_ROUNDS") or 3))
    espera = max(0, int(get("MURO_RETRY_WAIT") or 120))
    ok = False
    for i in range(1, intentos + 1):
        try:
            facebook.publish_multi(caption, imagenes)
            ok = True
            logger.info(f"Muro FB publicado con {len(imagenes)} foto(s).")
            break
        except Exception as e:
            logger.error(f"Muro FB FALLÓ (intento {i}/{intentos}): {e}")
            if i < intentos:
                time.sleep(espera)

    if ok:
        _marcar(hoy)
    else:
        logger.error("No se pudo publicar el muro FB — se reintentará la próxima corrida.")

    _limpiar(tapa_feed, tapa)
    logger.info("=== Muro FB Tapa+Farmacias: fin ===")


def _limpiar(tapa_feed, tapa) -> None:
    """_prepare_image puede crear un temporal; lo borra si es distinto de la tapa original."""
    try:
        if tapa_feed and tapa and Path(tapa_feed) != Path(tapa) and Path(tapa_feed).exists():
            Path(tapa_feed).unlink()
    except Exception:
        pass
