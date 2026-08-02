"""TAPA + FARMACIAS (08:00) — SOLO HISTORIAS.

Publica en Facebook + Instagram DOS historias: la tapa del diario y las farmacias
de turno de hoy. NO publica nada en el feed (el posteo/carrusel quedó anulado).
"""
import json
import smtplib
import ssl
from datetime import date
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

import farmacias as farm
import tapa as tapa_mod
from platforms import facebook, instagram
from story_image import compose_tapa_story
from utils.config import get
from utils.logger import get_logger

logger = get_logger("carrusel_tf")

LEDGER = Path(__file__).parent / ".carrusel_tf.json"
# Ledger aparte para el aviso "sin datos": manda a lo sumo UN mail por día
# (así, si el mes viene sin cargar, es un recordatorio diario hasta resolverlo,
# pero sin duplicar el aviso si la corrida se reintenta el mismo día).
AVISO_LEDGER = Path(__file__).parent / ".farmacias_aviso.json"


def _ya_avise_hoy(hoy: date) -> bool:
    try:
        if AVISO_LEDGER.exists():
            return json.loads(AVISO_LEDGER.read_text(encoding="utf-8")).get("fecha") == hoy.isoformat()
    except Exception:
        pass
    return False


def _marcar_aviso(hoy: date) -> None:
    try:
        AVISO_LEDGER.write_text(json.dumps({"fecha": hoy.isoformat()}, ensure_ascii=False),
                                encoding="utf-8")
    except Exception as e:
        logger.warning(f"No se pudo guardar el ledger del aviso de farmacias: {e}")


def _avisar_sin_datos(hoy: date, motivo: str) -> None:
    """Manda un mail al diario cuando farmacias se queda sin datos y no se publica.
    Best-effort y a lo sumo una vez por día. Reusa el SMTP del .env (mismo patrón
    que el desgrabador)."""
    if _ya_avise_hoy(hoy):
        return
    remitente = get("MAIL_FROM")
    password = get("MAIL_APP_PASSWORD")
    destino = (get("FARMACIAS_NOTIFY_EMAIL") or get("VIDEOS_NOTIFY_EMAIL") or remitente or "").strip()
    if not remitente or not password or not destino:
        logger.warning("Sin credenciales de mail (MAIL_FROM/MAIL_APP_PASSWORD): no se manda el aviso de farmacias.")
        return
    host = get("SMTP_HOST") or "smtp.gmail.com"
    port = int(get("SMTP_PORT") or 587)
    nombre_from = get("MAIL_FROM_NAME") or "Diario La Campaña"
    fecha = farm._fecha_larga(hoy).capitalize()
    asunto = f"⚠️ Farmacias sin datos — NO salió la historia ({fecha})"
    cuerpo = (
        f"Hoy ({fecha}) no se publicaron las historias de farmacias de turno "
        f"(ni en Instagram ni en Facebook), porque el sistema no tiene el cronograma del mes.\n\n"
        f"Motivo: {motivo}\n\n"
        f"Suele pasar cuando el Colegio manda el turno del mes como IMAGEN (o con otro formato) "
        f"en vez del Excel que el robot sabe leer.\n\n"
        f"Qué hacer: cargar el cronograma del mes a mano en turnos_farmacias.json "
        f"(o pedírselo a Claude, que lee la imagen del mail y lo carga). Mientras tanto, "
        f"las farmacias NO se publican para no dar datos sin verificar.\n\n"
        f"— Publicador Diario La Campaña"
    )
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
        logger.info(f"Aviso de farmacias sin datos enviado a {destino}")
        _marcar_aviso(hoy)
    except Exception as e:
        logger.error(f"No se pudo enviar el aviso de farmacias sin datos: {e}")


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

    es_finde = hoy.weekday() >= 5  # 5=sábado, 6=domingo

    # 1) Farmacias de hoy (SIEMPRE, también fin de semana: hay farmacia de turno).
    feed_farm, story_farm, lineas_cap, nombres, es_cambio = farm.farmacias_feed_de_hoy(hoy)
    if not story_farm:
        motivo = lineas_cap if isinstance(lineas_cap, str) else str(lineas_cap)
        logger.error(f"Sin datos de farmacias: {motivo}. No se publican las historias.")
        if not dry_run:
            _avisar_sin_datos(hoy, motivo)  # aviso por mail (1 vez por día)
        return
    logger.info(f"Farmacias: {', '.join(nombres)}")

    fecha = farm._fecha_larga(hoy).capitalize()
    historias = []

    # 2) Tapa SOLO de lunes a viernes (sáb/dom no hay edición → desactivada).
    if not es_finde:
        folder = Path(get("TAPA_FOLDER") or tapa_mod.DEFAULT_FOLDER)
        tapa_img = tapa_mod._resolver_tapa(folder)
        if tapa_img:
            logger.info(f"Tapa: {tapa_img.name}")
            historias.append(("tapa", compose_tapa_story(tapa_img, fecha)))
        else:
            logger.warning(f"No hay imagen de tapa en {folder}; se publica solo farmacias.")
    else:
        logger.info("Fin de semana: no se publica la tapa (solo farmacias).")

    historias.append(("farmacias", story_farm))

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
