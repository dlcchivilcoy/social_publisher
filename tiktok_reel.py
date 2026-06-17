"""Manda el reel del día a la BANDEJA de TikTok (corre LOCAL, después de las 20:00).

Baja el reel.mp4 que la nube ya generó (asset del GitHub Release `reel-latest`,
el mismo que salió en FB/IG) y lo sube a la bandeja de TikTok. Después abrís
TikTok, le ponés la canción que quieras y publicás. Una vez por día (ledger).

Tarea de Windows sugerida: diaria ~20:15 (unos minutos después del reel de la nube).
"""
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

import requests

from platforms import tiktok
from utils.config import load_config
from utils.logger import get_logger

logger = get_logger("tiktok_reel")

REEL_URL = "https://github.com/dlcchivilcoy/social_publisher/releases/download/reel-latest/reel.mp4"
LEDGER = Path(__file__).resolve().parent / ".tiktok_reel.json"


def _ya_subido_hoy() -> bool:
    if not LEDGER.exists():
        return False
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8")).get("last") == date.today().isoformat()
    except Exception:
        return False


def _marcar() -> None:
    LEDGER.write_text(json.dumps({"last": date.today().isoformat()}, ensure_ascii=False), encoding="utf-8")


def run(force: bool = False) -> None:
    load_config()
    if not force and _ya_subido_hoy():
        logger.info("El reel de hoy ya se mandó a TikTok. Nada que hacer.")
        return

    logger.info("Bajando el reel del día desde el GitHub Release…")
    r = requests.get(REEL_URL, timeout=120)
    r.raise_for_status()
    tmp = Path(tempfile.gettempdir()) / "tiktok_reel.mp4"
    tmp.write_bytes(r.content)
    logger.info(f"Reel bajado ({len(r.content)//1024} KB). Subiendo a la bandeja de TikTok…")

    tiktok.upload_to_inbox(tmp)
    _marcar()
    logger.info("Listo. Abrí TikTok (vas a tener una notificación), ponele música y publicá.")


if __name__ == "__main__":
    run(force="--force" in sys.argv)
