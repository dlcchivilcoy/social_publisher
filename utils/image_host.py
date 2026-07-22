import base64
import time
from pathlib import Path

import requests

from utils.config import get
from utils.logger import get_logger

logger = get_logger("image_host")

IMGBB_URL = "https://api.imgbb.com/1/upload"


def upload_to_imgbb(image_path: Path, intentos: int = 4) -> str:
    """Sube la imagen a ImgBB y devuelve una URL pública HTTPS (expira en 10 min).

    REINTENTA ante fallos transitorios de ImgBB (400/429/5xx o corte de red): antes, un
    solo tropiezo tumbaba TODO el carrusel de Instagram (2026-07-22: «400 Bad Request»
    en una corrida, y a los minutos ImgBB andaba perfecto). Si agota los intentos, lanza
    con el MENSAJE que devolvió ImgBB (no solo el código), para poder diagnosticar.
    """
    api_key = get("IMGBB_API_KEY")
    if not api_key:
        raise ValueError("IMGBB_API_KEY not set in .env")

    with open(image_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")

    nombre = Path(image_path).name
    ultimo = ""
    for i in range(max(1, intentos)):
        try:
            resp = requests.post(
                IMGBB_URL,
                data={"key": api_key, "image": encoded, "expiration": 600},
                timeout=60,
            )
            if resp.ok:
                url = resp.json()["data"]["url"]
                logger.debug(f"ImgBB upload OK: {url}")
                return url
            ultimo = f"HTTP {resp.status_code}: {resp.text[:200]}"
        except Exception as e:  # noqa: BLE001 — red/timeout: se reintenta igual
            ultimo = str(e)
        if i < intentos - 1:
            espera = 5 * (i + 1)
            logger.warning(f"ImgBB falló subiendo «{nombre}» ({ultimo}); "
                           f"reintento {i + 1}/{intentos - 1} en {espera}s…")
            time.sleep(espera)

    raise RuntimeError(f"ImgBB no pudo subir «{nombre}» tras {intentos} intentos — {ultimo}")
