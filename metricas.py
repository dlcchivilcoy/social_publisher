"""FOTO DE MÉTRICAS de cada reel de corresponsal, tomada a una EDAD FIJA.

Por qué existe (2026-08-31): el ranking mensual leía las métricas el día 1°, todas juntas.
Eso es injusto: un video del día 2 tuvo 29 días para juntar números y uno del día 30 tuvo
uno. Acá se toma una foto de cada video cuando cumple `RANKING_VENTANA_HORAS` (72 por
defecto) y ESA es la que se compara. Todos se miden con la misma vara.

Se guarda en el ledger `.videos_contabilidad.json`, dentro de la fila del video, como:

    "metricas": {
      "capturado": "2026-08-31T10:00:00", "horas": 74.2,
      "facebook":  {"vistas":243,"alcance":0,"likes":3,"comentarios":0,"compartidas":1},
      "instagram": {"vistas":892,"alcance":531,"likes":4,"comentarios":0,"compartidas":1},
      "youtube":   {"vistas":936,"alcance":0,"likes":1,"comentarios":0,"compartidas":0},
      "web":       {"vistas":4,"alcance":0,"likes":0,"comentarios":0,"compartidas":0}
    }

Solo se guardan las redes donde el video REALMENTE salió: si un reel no se publicó en
Instagram (o falló), esa red no figura y no penaliza al corresponsal en el puntaje.

Uso:  python main.py --metricas-videos          (toma las fotos pendientes)
      python main.py --metricas-videos --dry-run
"""
import json
from datetime import datetime
from pathlib import Path

from utils.config import get
from utils.logger import get_logger

logger = get_logger("metricas")

LEDGER = Path(__file__).parent / ".videos_contabilidad.json"
REDES = ("facebook", "instagram", "youtube", "web", "tiktok")
_VACIA = {"vistas": 0, "alcance": 0, "likes": 0, "comentarios": 0, "compartidas": 0, "guardados": 0}


def es_publicada(estado: str) -> bool:
    """Toda nota del ledger de videos cuyo estado empiece con «publicado».

    ⚠️ ÚNICA fuente de verdad de esto. Estaba escrito como una lista fija en TRES lugares
    (`ranking.py`, `reporte.py` y acá), y en las tres faltaba `publicado_foto_corr`, que es
    el estado MÁS COMÚN de las notas de corresponsales (42 de 88 al 2026-08-31): el ranking
    y la contabilidad venían ignorando más de la mitad de las notas. Con el prefijo entran
    todas las variantes (`publicado`, `_foto_corr`, `_placa`, `_solo_reel`) y quedan afuera
    los borradores. Si mañana aparece otro estado `publicado_algo`, entra solo."""
    return (estado or "").startswith("publicado")


def ventana_horas() -> float:
    """Edad a la que se mide cada video. Configurable con RANKING_VENTANA_HORAS."""
    try:
        return float(get("RANKING_VENTANA_HORAS") or 72)
    except ValueError:
        return 72.0


def _leer() -> list[dict]:
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else []
    except Exception as e:  # noqa: BLE001
        logger.error(f"No pude leer el registro de videos: {e}")
        return []


def _guardar(filas: list[dict]) -> None:
    LEDGER.write_text(json.dumps(filas, ensure_ascii=False, indent=2), encoding="utf-8")


def _horas_desde(iso: str) -> float:
    """Horas transcurridas desde una fecha ISO. -1 si no se puede leer."""
    try:
        return (datetime.now() - datetime.fromisoformat(iso)).total_seconds() / 3600
    except Exception:  # noqa: BLE001
        return -1.0


def _fb(fila: dict) -> dict | None:
    if not fila.get("fb_video_id"):
        return None
    from platforms import facebook
    d = facebook.video_insights(fila["fb_video_id"]) or {}
    if not d:
        return None
    return {**_VACIA, "vistas": d.get("vistas", 0), "alcance": d.get("alcance", 0),
            "likes": d.get("likes", 0), "comentarios": d.get("comentarios", 0),
            "compartidas": d.get("shares", 0)}


def _ig(fila: dict) -> dict | None:
    if not fila.get("ig_media_id"):
        return None
    from platforms import instagram
    d = instagram.media_insights(fila["ig_media_id"]) or {}
    if not d:
        return None
    return {**_VACIA, "vistas": d.get("vistas", 0), "alcance": d.get("reach", 0),
            "likes": d.get("likes", 0), "comentarios": d.get("comentarios", 0),
            "compartidas": d.get("shares", 0)}


def _yt(fila: dict) -> dict | None:
    if not fila.get("yt_video_id"):
        return None
    from platforms import youtube_api
    try:
        d = (youtube_api.get_video_stats([fila["yt_video_id"]], shorts=True) or {}) \
            .get(fila["yt_video_id"]) or {}
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[yt] {fila['yt_video_id']}: {e}")
        return None
    if not d:
        return None
    # YouTube no expone compartidas por la API pública.
    return {**_VACIA, "vistas": d.get("views", 0), "likes": d.get("likes", 0),
            "comentarios": d.get("comments", 0)}


def _web(fila: dict) -> dict | None:
    url = fila.get("post_url") or ""
    if not url:
        return None
    from platforms import web_metrics
    d = web_metrics.metricas(url)
    if not any(d.values()):
        return None      # sin datos no se suma la red (mejor que puntuar 0 y arrastrar)
    return {**_VACIA, "vistas": d["vistas"], "likes": d["likes"], "comentarios": d["comentarios"]}


def _tiktok(fila: dict) -> dict | None:
    """TikTok todavía NO suma: los reels van a BORRADORES, así que no hay publicación que
    medir. El día que se habilite Direct Post y se guarde el id del posteo, se lee acá y
    entra al puntaje solo — el resto del sistema no se toca."""
    return None


_FUENTES = {"facebook": _fb, "instagram": _ig, "youtube": _yt, "web": _web, "tiktok": _tiktok}


def capturar(fila: dict) -> dict:
    """Foto de métricas de UN video, red por red. Nunca lanza."""
    foto = {"capturado": datetime.now().isoformat(timespec="seconds"),
            "horas": round(_horas_desde(fila.get("fecha_publicado") or ""), 1)}
    for red, fn in _FUENTES.items():
        try:
            d = fn(fila)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{red}] «{(fila.get('titulo') or '')[:35]}»: {e}")
            d = None
        if d is not None:
            foto[red] = d
    return foto


def _pendientes(filas: list[dict], horas: float) -> list[dict]:
    """Videos publicados, ya maduros y sin foto todavía."""
    out = []
    for f in filas:
        if not es_publicada(f.get("estado")) or f.get("metricas"):
            continue
        edad = _horas_desde(f.get("fecha_publicado") or "")
        if edad >= horas:
            out.append(f)
    return out


def capturar_pendientes(dry_run: bool = False, forzar: bool = False) -> int:
    """Toma la foto de todos los videos que ya cumplieron la ventana y no la tienen.

    Idempotente: un video con foto NO se vuelve a medir (esa es la gracia — la foto es a
    edad fija). `forzar=True` la vuelve a tomar, solo para pruebas."""
    horas = ventana_horas()
    filas = _leer()
    pend = filas if forzar else _pendientes(filas, horas)
    if not pend:
        logger.info(f"Sin videos para medir (ventana: {horas:.0f} h).")
        return 0
    logger.info(f"Midiendo {len(pend)} video(s) que cumplieron las {horas:.0f} h…")
    hechos = 0
    for f in pend:
        foto = capturar(f)
        redes = [r for r in REDES if r in foto]
        logger.info(f"  «{(f.get('titulo') or '')[:45]}» ({foto['horas']:.0f} h) → "
                    f"{', '.join(redes) or 'sin datos'}")
        if dry_run:
            continue
        f["metricas"] = foto
        hechos += 1
        _guardar(filas)          # se guarda tras cada uno: si se corta, no se pierde lo hecho
    logger.info(f"=== Métricas: {hechos} video(s) medidos ===")
    return hechos
