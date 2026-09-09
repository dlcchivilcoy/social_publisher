"""VIDEOS DE YOUTUBE → MURO DE FACEBOOK — lunes a viernes, 18:00 a 19:00.

Postea en la Página de Facebook los videos que Radio del Centro subió HOY, como LINK,
para que Facebook arme solo la tarjeta con la MINIATURA del video (verificado: devuelve
`i.ytimg.com/vi/<id>/maxresdefault.jpg`).

Qué NO se postea (lo resuelve `youtube_api.videos_seccion_de_hoy`):
  · los VIVOS («LA MAÑANA DEL CENTRO»), ni en curso ni ya terminados;
  · los SHORTS, que son los reels de corresponsales y YA salen solos a FB e IG.

El canal sube 4-5 videos por día, casi todos juntos entre las 15:40 y las 16:20. Para no
volcarlos de golpe en el muro, cada corrida postea UNO (`YT_FB_POR_CORRIDA`) y el cron
dispara cada 12 minutos entre las 18:00 y las 18:48. Si una corrida falla, la siguiente
levanta el que quedó: el registro es lo que manda, no el horario.
"""
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from platforms import facebook, youtube_api
from utils.config import get
from utils.logger import get_logger

logger = get_logger("yt_fb")

LEDGER = Path(__file__).parent / ".yt_facebook.json"
DIAS_MEMORIA = 10          # cuánto se recuerda un video ya posteado (después se poda)


def _on() -> bool:
    return str(get("YT_FB_ENABLED") or "1").strip().lower() not in ("0", "no", "false", "off")


def _por_corrida() -> int:
    try:
        return max(1, int(str(get("YT_FB_POR_CORRIDA") or "1").strip()))
    except ValueError:
        return 1


def _ventana() -> tuple[int, int]:
    """Horas permitidas (desde, hasta). Red de seguridad: si el cron se dispara con
    muchísimo retraso —al scheduler de GitHub le pasó— no queremos 5 posteos a las 4 de
    la mañana. `YT_FB_VENTANA=0-24` la desactiva."""
    try:
        desde, hasta = str(get("YT_FB_VENTANA") or "18-21").split("-")
        return int(desde), int(hasta)
    except ValueError:
        return 18, 21


def _solo_habiles() -> bool:
    return str(get("YT_FB_SOLO_HABILES") or "1").strip().lower() not in ("0", "no", "false", "off")


def _texto(titulo: str) -> str:
    plantilla = get("YT_FB_TEXTO") or "🎥 {titulo}\n\nMirá la nota completa en nuestro canal 👇"
    return plantilla.replace("{titulo}", titulo).strip()


def _leer() -> dict:
    try:
        if LEDGER.exists():
            d = json.loads(LEDGER.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"No pude leer {LEDGER.name} ({e}); arranco de cero.")
    return {}


def _guardar(posteados: dict) -> None:
    """Guarda {video_id: fecha} y poda lo viejo para que el archivo no crezca sin fin."""
    corte = (date.today() - timedelta(days=DIAS_MEMORIA)).isoformat()
    vivos = {k: v for k, v in posteados.items() if v >= corte}
    LEDGER.write_text(json.dumps({"posteados": vivos}, ensure_ascii=False, indent=2),
                      encoding="utf-8")


def run_yt_a_facebook(dry_run: bool = False, forzar: bool = False) -> None:
    """Postea al muro de Facebook los videos de hoy que todavía no salieron.

    `forzar` saltea las guardas de día y horario (para probar a mano).
    """
    if not _on():
        logger.info("YT→Facebook desactivado (YT_FB_ENABLED=0).")
        return

    ahora = datetime.now()
    if not forzar:
        if _solo_habiles() and ahora.weekday() >= 5:
            logger.info(f"Hoy es {'sábado' if ahora.weekday() == 5 else 'domingo'}: no se postea.")
            return
        desde, hasta = _ventana()
        if not (desde <= ahora.hour < hasta):
            logger.warning(f"Son las {ahora:%H:%M} y la ventana es {desde}-{hasta} h. "
                           "No posteo (el cron debe haberse disparado fuera de hora).")
            return

    try:
        videos = youtube_api.videos_seccion_de_hoy()
    except Exception as e:  # noqa: BLE001
        logger.error(f"No pude leer los videos de hoy de YouTube: {e}")
        return

    estado = _leer()
    posteados = estado.get("posteados") or {}
    # Del más viejo al más nuevo: así el último que se postea queda arriba en el muro.
    pendientes = [v for v in videos if v["id"] not in posteados]
    pendientes.sort(key=lambda v: v.get("published", ""))

    if not videos:
        logger.info("YouTube no tiene videos de hoy todavía (sin contar vivos ni shorts).")
        return
    if not pendientes:
        logger.info(f"Los {len(videos)} video(s) de hoy ya se postearon. Nada que hacer.")
        return

    cupo = _por_corrida()
    logger.info(f"{len(videos)} video(s) de hoy, {len(pendientes)} sin postear. "
                f"Posteo hasta {cupo} en esta corrida.")

    for v in pendientes[:cupo]:
        texto = _texto(v["titulo"])
        if dry_run:
            logger.info(f"[dry-run] postearía {v['url']} — {v['titulo'][:60]}")
            continue
        try:
            # Refresca la tarjeta antes de postear: el video es de hace unas horas y así
            # nos aseguramos de que Facebook tenga la miniatura buena y no una cacheada.
            try:
                prev = facebook.scrape_preview(v["url"])
                if not prev.get("image"):
                    logger.warning(f"Facebook no encontró miniatura para {v['url']}; posteo igual.")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"No pude refrescar la vista previa de {v['url']} ({e}); sigo.")

            res = facebook.publish_link(v["url"], texto)
            posteados[v["id"]] = date.today().isoformat()
            _guardar(posteados)          # se guarda de a uno: si el siguiente falla, este no se repite
            logger.info(f"Posteado en Facebook: {v['titulo'][:60]} → {res.get('id')}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"No pude postear {v['url']}: {e}")
            break                        # si Facebook está caído, no insistimos con el resto
