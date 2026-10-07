"""Transcripciones que hace la PC de la RADIO, para el desgrabador de YouTube.

Desde la nube, YouTube no deja bajar el audio (bloquea las IP de GitHub) y los subtítulos
automáticos tardan horas: sin otra fuente, Gemini tiene que MIRAR cada video de 16-32 min,
que es lo que más gasta de la clave paga. Pero esos videos salen de la PC de la radio: una
tarea local los transcribe con Groq (gratis) a medida que se exportan y deja un JSON por
archivo en la carpeta de Drive «transcripciones yt». El workflow la baja a
YT_TRANSCRIPCIONES_DIR y acá se empareja cada video de YouTube con su transcripción.

Formato de cada JSON (lo escribe la PC de la radio):
    {"version": 1, "archivo": "Entrevista X.mp4", "duracion_seg": 1386.4,
     "modificado": "2026-10-07T16:40:12-03:00", "motor": "groq whisper-large-v3-turbo",
     "transcripcion": "…", "video_id": ""}

Emparejamiento: si el JSON trae `video_id`, por ID exacto; si no, por DURACIÓN (el video
de YouTube y el archivo exportado duran lo mismo, ±TOLERANCIA s). Cada transcripción se
usa una sola vez por corrida. Si no hay pareja segura, devuelve None y el desgrabador sigue
con los otros caminos (subtítulos de la API → Gemini mirando el video)."""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

from utils.config import get
from utils.logger import get_logger

logger = get_logger("transcripciones_pc")

TOLERANCIA_SEG = 3.0   # YouTube redondea la duración al segundo
MAX_DIAS = 3           # transcripciones más viejas no se consideran
MIN_CHARS = 200        # menos que esto no es una entrevista transcripta

_usadas: set[str] = set()


def activo() -> bool:
    return str(get("YT_TRANSCRIPCION_PC", "1")).strip().lower() not in ("0", "no", "false", "off")


def _carpeta() -> Path | None:
    ruta = (os.environ.get("YT_TRANSCRIPCIONES_DIR") or get("YT_TRANSCRIPCIONES_DIR") or "").strip()
    p = Path(ruta) if ruta else None
    return p if p and p.is_dir() else None


def _leer_todas(carpeta: Path) -> list[tuple[Path, dict]]:
    limite = datetime.now() - timedelta(days=MAX_DIAS)
    out = []
    for f in sorted(carpeta.rglob("*.json")):
        try:
            if datetime.fromtimestamp(f.stat().st_mtime) < limite:
                continue
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 — un archivo roto no frena a los demás
            logger.warning(f"Transcripción de la PC ilegible ({f.name}): {e}")
            continue
        if isinstance(d, dict) and len(str(d.get("transcripcion") or "")) >= MIN_CHARS:
            out.append((f, d))
    return out


def buscar(video_id: str, duracion_seg: float = 0) -> str | None:
    """Transcripción de la PC de la radio para ese video, o None si no hay una segura."""
    if not activo():
        return None
    carpeta = _carpeta()
    if carpeta is None:
        return None
    candidatas = [(f, d) for f, d in _leer_todas(carpeta) if str(f) not in _usadas]
    if not candidatas:
        logger.info("  PC radio: no hay transcripciones nuevas en Drive.")
        return None

    # 1) Por ID exacto (si la PC lo pudo averiguar).
    for f, d in candidatas:
        if video_id and str(d.get("video_id") or "").strip() == video_id:
            return _usar(f, d, "por ID")

    # 2) Por duración: la más cercana dentro de la tolerancia, y que no haya empate.
    if not duracion_seg:
        return None
    cerca = []
    for f, d in candidatas:
        try:
            dif = abs(float(d.get("duracion_seg") or 0) - float(duracion_seg))
        except (TypeError, ValueError):
            continue
        if dif <= TOLERANCIA_SEG:
            cerca.append((dif, f, d))
    if not cerca:
        logger.info(f"  PC radio: ninguna transcripción dura {duracion_seg:.0f} s (±{TOLERANCIA_SEG:.0f}).")
        return None
    cerca.sort(key=lambda x: x[0])
    if len(cerca) > 1 and cerca[1][0] - cerca[0][0] < 1.0:
        logger.warning(f"  PC radio: {len(cerca)} transcripciones duran casi lo mismo "
                       f"({', '.join(c[2].get('archivo', c[1].name) for c in cerca)}); "
                       "no adivino y sigo con los otros caminos.")
        return None
    return _usar(cerca[0][1], cerca[0][2], f"por duración (±{cerca[0][0]:.1f} s)")


def _usar(f: Path, d: dict, como: str) -> str:
    _usadas.add(str(f))
    txt = " ".join(str(d.get("transcripcion") or "").split())
    logger.info(f"  Paso 1a: transcripción de la PC de la radio «{d.get('archivo', f.name)}» "
                f"{como} ({len(txt.split())} palabras, {d.get('motor', '?')}).")
    return txt
