"""PUBLICACIONES + HISTORIAS DE LA MAÑANA (08:00): TAPA → FARMACIAS → SEPELIOS → CLIMA.

Cada pieza sale como publicación en el MURO de Facebook (pedido 2026-10-04; reemplaza el
posteo combinado tapa+farmacias de las 00:00) y como HISTORIA en Facebook + Instagram
(STORIES_PLATFORMS; clima y sepelios se pueden acotar con CLIMA_SEPELIOS_PLATFORMS).
Nada va al feed de Instagram.
"""
import json
import smtplib
import ssl
import time
from datetime import date
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

import farmacias as farm
import tapa as tapa_mod
from platforms import facebook, instagram
from publisher import _prepare_image
from story_image import (compose_clima_feed, compose_clima_story, compose_sepelios_feed,
                         compose_sepelios_story, compose_tapa_story)
from utils.branding import sitio_web
from utils.config import get
from utils.logger import get_logger

logger = get_logger("carrusel_tf")

LEDGER = Path(__file__).parent / ".carrusel_tf.json"
# Ledger aparte para el aviso "sin datos": manda a lo sumo UN mail por día
# (así, si el mes viene sin cargar, es un recordatorio diario hasta resolverlo,
# pero sin duplicar el aviso si la corrida se reintenta el mismo día).
AVISO_LEDGER = Path(__file__).parent / ".farmacias_aviso.json"
# Ledger del aviso "no se pudo publicar tras reintentos": dedup por (día + combos que
# fallaron), así no repite el mismo aviso el mismo día pero sí avisa si cambia lo que falla.
FALLO_LEDGER = Path(__file__).parent / ".carrusel_tf_aviso.json"


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


def _destino_mail() -> str:
    return (get("FARMACIAS_NOTIFY_EMAIL") or get("VIDEOS_NOTIFY_EMAIL") or get("MAIL_FROM") or "").strip()


def _enviar_mail(asunto: str, cuerpo: str) -> bool:
    """Manda un mail al diario por SMTP (mismo patrón que el desgrabador). Best-effort:
    devuelve True si salió, False si no (sin romper la corrida)."""
    remitente = get("MAIL_FROM")
    password = get("MAIL_APP_PASSWORD")
    destino = _destino_mail()
    if not remitente or not password or not destino:
        logger.warning("Sin credenciales de mail (MAIL_FROM/MAIL_APP_PASSWORD): no se manda el aviso.")
        return False
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
        return True
    except Exception as e:
        logger.error(f"No se pudo enviar el aviso por mail: {e}")
        return False


def _avisar_sin_datos(hoy: date, motivo: str) -> None:
    """Manda un mail al diario cuando farmacias se queda sin datos y no se publica.
    Best-effort y a lo sumo una vez por día."""
    if _ya_avise_hoy(hoy):
        return
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
    if _enviar_mail(asunto, cuerpo):
        _marcar_aviso(hoy)


def _ya_avise_fallo(hoy: date, combos: list[str]) -> bool:
    """True si ya se avisó hoy por EXACTAMENTE estos combos fallidos (evita repetir)."""
    try:
        if FALLO_LEDGER.exists():
            d = json.loads(FALLO_LEDGER.read_text(encoding="utf-8"))
            return d.get("fecha") == hoy.isoformat() and set(d.get("combos", [])) == set(combos)
    except Exception:
        pass
    return False


def _marcar_aviso_fallo(hoy: date, combos: list[str]) -> None:
    try:
        FALLO_LEDGER.write_text(json.dumps({"fecha": hoy.isoformat(), "combos": sorted(combos)},
                                           ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning(f"No se pudo guardar el ledger del aviso de fallo: {e}")


def _avisar_fallo_publicacion(hoy: date, combos: list[str]) -> None:
    """Avisa por mail cuando historias NO salieron tras agotar los reintentos. Best-effort;
    dedup por (día + combos), así no repite el mismo aviso pero sí avisa si cambia lo que falla."""
    if not combos or _ya_avise_fallo(hoy, combos):
        return
    fecha = farm._fecha_larga(hoy).capitalize()
    lista = "\n".join(f"  • {c.replace('|', ' → ')}" for c in sorted(combos))
    asunto = f"⚠️ Publicaciones/historias de la mañana que NO salieron ({fecha})"
    cuerpo = (
        f"Hoy ({fecha}) esto no se pudo publicar tras varios reintentos:\n\n"
        f"{lista}\n\n"
        f"(Cada línea es pieza → red; «muro» es la publicación en el muro de Facebook, "
        f"instagram/facebook son las historias.) Lo demás sí salió. Suele ser un problema "
        f"transitorio de la red social o del hosting de imágenes.\n\n"
        f"El sistema reintenta SOLO lo que falta en la próxima corrida, sin duplicar lo ya "
        f"publicado. Si querés que salga ya, se puede republicar a mano solo lo pendiente.\n\n"
        f"— Publicador Diario La Campaña"
    )
    if _enviar_mail(asunto, cuerpo):
        _marcar_aviso_fallo(hoy, combos)


def _site() -> str:
    return sitio_web()


def _platforms() -> list[str]:
    raw = get("STORIES_PLATFORMS") or "instagram,facebook"
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _clave(etiqueta: str, plataforma: str) -> str:
    return f"{etiqueta}|{plataforma}"


def _estado_hoy(hoy: date) -> set[str]:
    """Conjunto de 'etiqueta|plataforma' que YA se publicaron hoy (p. ej. 'tapa|facebook').
    Vacío si el ledger no es de hoy. Con esto, si una historia ya salió en una red no se
    vuelve a publicar (no duplica), pero la que falló (p. ej. IG) se reintenta."""
    try:
        if LEDGER.exists():
            d = json.loads(LEDGER.read_text(encoding="utf-8"))
            if d.get("fecha") == hoy.isoformat():
                return set(d.get("hechos", []))
    except Exception:
        pass
    return set()


def _guardar_estado(hoy: date, hechos: set[str]) -> None:
    try:
        LEDGER.write_text(json.dumps({"fecha": hoy.isoformat(), "hechos": sorted(hechos)},
                                     ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        logger.warning(f"No se pudo guardar el ledger de tapa+farmacias: {e}")


def _extra_platforms() -> list[str]:
    """Redes de las historias de CLIMA y SEPELIOS. Arrancaron solo en Instagram
    (2026-10-03) y el mismo día el usuario las pidió también en Facebook: por defecto van
    a las mismas redes que tapa y farmacias (STORIES_PLATFORMS). CLIMA_SEPELIOS_PLATFORMS
    las acota (ej. =instagram)."""
    raw = get("CLIMA_SEPELIOS_PLATFORMS") or get("STORIES_PLATFORMS") or "instagram,facebook"
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


def _clima_activo() -> bool:
    return (get("CLIMA_HISTORIA") or "1").strip().lower() in ("1", "true", "si", "sí", "on")


def _muro_activo() -> bool:
    """Publicaciones en el MURO de Facebook, una por pieza (pedido 2026-10-04).
    MURO_FB=0 las apaga y quedan solo las historias."""
    return (get("MURO_FB") or "1").strip().lower() in ("1", "true", "si", "sí", "on")


def _muro_delay() -> int:
    """Separación mínima entre dos publicaciones del muro (anti-ráfaga de Facebook)."""
    try:
        return max(0, int(get("MURO_DELAY_SECONDS") or get("POST_DELAY_SECONDS") or 300))
    except ValueError:
        return 300


def _tapa_de_hoy(hoy: date) -> Path | None:
    """La tapa de hoy, solo lun–vie y solo si es FRESCA (subida en las últimas
    TAPA_FRESCA_HORAS, 20 por defecto: la del día se sube la noche anterior). Así un
    feriado sin edición no publica la tapa vieja en el muro ni en la historia."""
    if hoy.weekday() >= 5:
        logger.info("Fin de semana: no se publica la tapa.")
        return None
    folder = Path(get("TAPA_FOLDER") or tapa_mod.DEFAULT_FOLDER)
    tapa = tapa_mod._resolver_tapa(folder)
    if not tapa:
        logger.warning(f"No hay imagen de tapa en {folder}; sale el resto sin la tapa.")
        return None
    try:
        limite = float(get("TAPA_FRESCA_HORAS") or 20)
    except ValueError:
        limite = 20.0
    horas = (time.time() - tapa.stat().st_mtime) / 3600.0
    if horas > limite:
        logger.warning(f"La tapa más nueva ({tapa.name}) tiene {horas:.0f} h (> {limite:.0f} h): "
                       f"es vieja (¿feriado o todavía no se subió?). Sale el resto sin la tapa.")
        return None
    return tapa


def _caption_tapa(fecha: str) -> str:
    return (f"📰 Tapa de hoy — Diario La Campaña · {fecha}\n\n"
            f"Las noticias de Chivilcoy, todos los días en nuestra web y en el diario impreso.")


def _caption_farmacias(fecha: str, lineas: list[str], es_cambio: bool) -> str:
    cabecera = "⚠️ *CAMBIO de turno de hoy*\n\n" if es_cambio else ""
    return (cabecera + f"💊 Farmacias de turno — {fecha}\n\n" + "\n".join(lineas)
            + "\n\nLas dos primeras están de turno las 24 hs; la última, hasta las 22 hs.")


def _caption_sepelios(fecha: str, lista: list[dict]) -> str:
    renglones = [f"• {s['nombre']} ({s['empresa']}, {s['fecha'].strftime('%d/%m')})" for s in lista]
    return (f"🕯️ Sepelios — {fecha}\n\nQ.E.P.D.\n\n" + "\n".join(renglones)
            + "\n\nDiario La Campaña acompaña a las familias.\n"
              "Información: Grupo Visión y Empresa San Nicolás.")


def _caption_clima(fecha: str, d: dict) -> str:
    lluvia = d.get("lluvia") or 0
    agua = (f"☔ Lluvia prevista: {str(lluvia).replace('.', ',')} mm" if lluvia >= 0.2
            else "☔ Sin lluvias previstas")
    ahora = d.get("ahora") or {}
    return (f"🌤️ El clima hoy en Chivilcoy — {fecha}\n\n"
            f"{d['estado']['texto']}. Máxima de {d['max']}° y mínima de {d['min']}°.\n"
            f"💨 Viento del {ahora.get('rumbo')}, hasta {d.get('viento_max')} km/h · "
            f"💧 Humedad {ahora.get('humedad')} %\n{agua}\n\n"
            f"El pronóstico extendido, en nuestra web.\n"
            f"Datos: Instituto Meteorológico de Noruega (MET Norway).")


def run_tapa_farmacias(dry_run: bool = False) -> None:
    """Publicaciones + historias de la mañana (08:00), en este orden:
    TAPA → FARMACIAS → SEPELIOS → CLIMA (pedido 2026-10-04).

    Cada pieza sale como PUBLICACIÓN en el muro de Facebook (foto 4:5 + texto) y,
    enseguida, como HISTORIA en Instagram y Facebook. Tapa solo lun–vie; farmacias,
    sepelios y clima todos los días. Si una no se puede armar (sin datos de farmacias,
    MET caído, ningún sepelio nuevo…), las demás salen igual. Reemplaza el posteo
    combinado tapa+farmacias que salía a las 00:00 (muro_tapa_farmacias.py)."""
    import clima
    import sepelios as sep

    modo = "SIMULACIÓN (dry-run)" if dry_run else "PUBLICACIÓN REAL"
    hoy = date.today()
    logger.info(f"=== Publicaciones e historias de la mañana [{modo}] — {hoy.isoformat()} ===")

    fecha = farm._fecha_larga(hoy).capitalize()
    plats, extra = _platforms(), _extra_platforms()
    # Cada pieza: (etiqueta, imagen de historia, redes de la historia, imagen del muro, texto)
    piezas = []
    sepelios_hoy = []

    # 1) Tapa
    tapa = _tapa_de_hoy(hoy)
    if tapa:
        logger.info(f"Tapa: {tapa.name}")
        try:
            tapa_feed = Path(_prepare_image(tapa))
        except Exception as e:
            logger.error(f"No se pudo preparar la tapa para el muro: {e}")
            tapa_feed = tapa
        piezas.append(("tapa", compose_tapa_story(tapa, fecha), plats, tapa_feed, _caption_tapa(fecha)))

    # 2) Farmacias de hoy (SIEMPRE, también fin de semana: hay farmacia de turno).
    feed_farm, story_farm, lineas_cap, nombres, es_cambio = farm.farmacias_feed_de_hoy(hoy)
    if story_farm:
        logger.info(f"Farmacias: {', '.join(nombres)}")
        piezas.append(("farmacias", story_farm, plats, feed_farm,
                       _caption_farmacias(fecha, lineas_cap, es_cambio)))
    else:
        motivo = lineas_cap if isinstance(lineas_cap, str) else str(lineas_cap)
        logger.error(f"Sin datos de farmacias: {motivo}. No salen las farmacias.")
        if not dry_run:
            _avisar_sin_datos(hoy, motivo)  # aviso por mail (1 vez por día)

    # 3) Sepelios nuevos (los que todavía no salieron).
    if sep.historia_activa():
        try:
            sepelios_hoy = sep.pendientes_historia(hoy)
            if sepelios_hoy:
                logger.info(f"Sepelios: {', '.join(s['nombre'] for s in sepelios_hoy)}")
                nombres_sep = [s["nombre"] for s in sepelios_hoy]
                subs = [f"{s['empresa']} · {s['fecha'].strftime('%d/%m')}" for s in sepelios_hoy]
                piezas.append(("sepelios", compose_sepelios_story(nombres_sep, fecha, subs), extra,
                               compose_sepelios_feed(nombres_sep, fecha, subs),
                               _caption_sepelios(fecha, sepelios_hoy)))
            else:
                logger.info("Sepelios: ninguno nuevo de Chivilcoy; hoy no salen.")
        except Exception as e:
            logger.error(f"No se pudieron armar los sepelios: {e}")

    # 4) Clima de hoy en Chivilcoy (MET Norway, la misma fuente que /clima de la web).
    if _clima_activo():
        try:
            datos = clima.pronostico_hoy()
            logger.info(f"Clima: {datos['estado']['texto']}, máx {datos['max']}° / mín {datos['min']}°")
            piezas.append(("clima", compose_clima_story(datos, fecha, _site()), extra,
                           compose_clima_feed(datos, fecha, _site()), _caption_clima(fecha, datos)))
        except Exception as e:
            logger.error(f"No se pudo armar el clima: {e}")

    if not piezas:
        logger.error("No hay nada para publicar hoy.")
        return

    muro = _muro_activo()
    story_fns = {"instagram": instagram.publish_story, "facebook": facebook.publish_story}

    # Acciones en ORDEN: por cada pieza, primero la publicación del muro y después sus
    # historias. Cada una es (etiqueta, red, función); red "muro" = muro de Facebook.
    acciones = []
    for etiqueta, story_img, redes, feed_img, caption in piezas:
        if muro and feed_img:
            acciones.append((etiqueta, "muro",
                             lambda c=caption, f=feed_img: facebook.publish(c, Path(f))))
        for red in redes:
            if red in story_fns:
                acciones.append((etiqueta, red, lambda fn=story_fns[red], i=story_img: fn(i)))

    if dry_run:
        for etiqueta, red, _ in acciones:
            que = "publicación en el muro de Facebook" if red == "muro" else f"historia {red}"
            logger.info(f"[dry-run] {etiqueta}: {que} (NO se publica)")
        for etiqueta, story_img, _, feed_img, caption in piezas:
            logger.info(f"[dry-run] {etiqueta}: historia {Path(story_img).name}, "
                        f"muro {Path(feed_img).name}\n{caption}")
        logger.info("=== Publicaciones e historias de la mañana: fin (dry-run) ===")
        return

    # Salteando lo que YA salió hoy (ledger por pieza y red): si FB salió pero IG falló,
    # se reintenta SOLO IG, sin volver a publicar lo de FB.
    hechos = _estado_hoy(hoy)
    pendientes = [(e, r, fn) for (e, r, fn) in acciones if _clave(e, r) not in hechos]
    if not pendientes:
        logger.info("Todo lo de hoy ya está publicado. Se omite.")
        return
    if hechos:
        logger.info(f"Ya publicadas hoy (no se repiten): {', '.join(sorted(hechos))}")

    # Reintenta EN LA MISMA corrida lo que falle (p. ej. IG con un hipo transitorio). Si
    # igual queda algo, el ledger deja registrado lo hecho y la próxima corrida retoma
    # SOLO lo pendiente.
    rondas = max(1, int(get("STORY_RETRY_ROUNDS") or 3))
    espera = max(0, int(get("STORY_RETRY_WAIT") or 150))
    separacion = _muro_delay()
    ultimo_muro = None
    salieron_ahora = set()  # etiquetas publicadas (en alguna red) en ESTA corrida

    for ronda in range(1, rondas + 1):
        siguen = []
        # ORDEN: si en una red falla algo, lo que va después en esa misma red espera a la
        # ronda siguiente, para no salir desordenado. En la última ronda sale todo lo
        # que se pueda.
        trabadas = set()
        for etiqueta, red, fn in pendientes:
            if red in trabadas and ronda < rondas:
                siguen.append((etiqueta, red, fn))
                continue
            if red == "muro" and ultimo_muro is not None:
                falta = separacion - (time.time() - ultimo_muro)
                if falta > 0:
                    logger.info(f"Espero {falta:.0f}s antes de la próxima publicación del muro…")
                    time.sleep(falta)
            try:
                fn()
                if red == "muro":
                    ultimo_muro = time.time()
                hechos.add(_clave(etiqueta, red))
                salieron_ahora.add(etiqueta)
                _guardar_estado(hoy, hechos)  # persistir apenas sale cada una
                logger.info(f"[{red}] {etiqueta} OK")
            except Exception as e:
                logger.error(f"[{red}] {etiqueta} FALLÓ (ronda {ronda}/{rondas}): {e}")
                siguen.append((etiqueta, red, fn))
                trabadas.add(red)
        pendientes = siguen
        if not pendientes:
            break
        if ronda < rondas:
            faltan = ", ".join(_clave(e, r) for e, r, _ in pendientes)
            logger.warning(f"Quedan pendientes: {faltan}. Reintento en {espera}s…")
            time.sleep(espera)

    # Los sepelios quedan registrados (no se repiten otro día) apenas salieron EN ESTA
    # corrida en alguna red: si una red falló, mañana no se repiten en las otras. (Si los
    # de hoy ya habían salido antes, los nuevos que aparecieron después no se marcan:
    # entran mañana.)
    if "sepelios" in salieron_ahora:
        sep.marcar_historia(sepelios_hoy)

    if not pendientes:
        logger.info("Publicaciones e historias de la mañana: todo publicado.")
    else:
        combos_fallidos = [_clave(e, r) for e, r, _ in pendientes]
        logger.error(f"Siguen sin publicarse: {', '.join(combos_fallidos)} (tras {rondas} rondas). "
                     f"La próxima corrida retoma SOLO lo pendiente (sin duplicar lo ya publicado).")
        _avisar_fallo_publicacion(hoy, combos_fallidos)  # aviso por mail (dedup por día+combos)

    if tapa and tapa_feed != tapa:
        try:
            tapa_feed.unlink(missing_ok=True)  # temporal de _prepare_image
        except Exception:
            pass
    logger.info("=== Publicaciones e historias de la mañana: fin ===")
