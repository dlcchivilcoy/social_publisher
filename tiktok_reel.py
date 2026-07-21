"""Manda el reel más reciente a la BANDEJA de TikTok (corre LOCAL).

Busca en el GitHub Release `reel-latest` el asset `reel*.mp4` más nuevo — el
mismo que salió en FB/IG, venga del reel de las 5 más leídas o de una
desgrabación — y lo sube a la bandeja de TikTok. Después abrís TikTok, le ponés
la canción que quieras y publicás.

No sube dos veces el mismo reel (ledger por nombre de asset) ni sube uno viejo
(si el más nuevo tiene más de MAX_DIAS_REEL días no hace nada): así, los días
sin reel nuevo, la tarea corre y no molesta.

Tarea de Windows sugerida: diaria ~20:15 (unos minutos después del reel de la nube).
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import requests

from platforms import tiktok
from utils.config import get, load_config
from utils.logger import get_logger

logger = get_logger("tiktok_reel")

API = "https://api.github.com"
TAG = "reel-latest"
MAX_DIAS_REEL = 2
LEDGER = Path(__file__).resolve().parent / ".tiktok_reel.json"


def _repo() -> str:
    return os.environ.get("GITHUB_REPOSITORY") or get("REEL_REPO") or "dlcchivilcoy/social_publisher"


def _ultimo_reel() -> dict | None:
    """El asset `reel*.mp4` más nuevo del Release, o None si no hay ninguno."""
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    tok = os.environ.get("GITHUB_TOKEN") or get("GITHUB_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    r = requests.get(f"{API}/repos/{_repo()}/releases/tags/{TAG}", headers=h, timeout=30)
    r.raise_for_status()
    reels = [a for a in r.json().get("assets", [])
             if a["name"].startswith("reel") and a["name"].endswith(".mp4")]
    if not reels:
        return None
    return max(reels, key=lambda a: a["created_at"])


def _dias(iso: str) -> float:
    creado = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - creado).total_seconds() / 86400


def _ya_subido(nombre: str) -> bool:
    if not LEDGER.exists():
        return False
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8")).get("last") == nombre
    except Exception:
        return False


def _marcar(nombre: str) -> None:
    LEDGER.write_text(json.dumps({"last": nombre}, ensure_ascii=False), encoding="utf-8")


def run(force: bool = False) -> None:
    load_config()

    asset = _ultimo_reel()
    if not asset:
        logger.info("No hay ningún reel en el Release. Nada que hacer.")
        return

    nombre, dias = asset["name"], _dias(asset["created_at"])
    if not force and _ya_subido(nombre):
        logger.info(f"El reel «{nombre}» ya se mandó a TikTok. Nada que hacer.")
        return
    if not force and dias > MAX_DIAS_REEL:
        logger.info(f"El reel más nuevo («{nombre}») es de hace {dias:.1f} días: no subo uno viejo. "
                    "Si igual lo querés, corré con --force.")
        return

    logger.info(f"Bajando «{nombre}» ({asset['size'] // 1024} KB, de hace {dias:.1f} días)…")
    r = requests.get(asset["browser_download_url"], timeout=300)
    r.raise_for_status()
    tmp = Path(tempfile.gettempdir()) / "tiktok_reel.mp4"
    tmp.write_bytes(r.content)
    logger.info("Subiendo a la bandeja de TikTok…")

    tiktok.upload_to_inbox(tmp)
    _marcar(nombre)
    logger.info("Listo. Abrí TikTok (vas a tener una notificación), ponele música y publicá.")


if __name__ == "__main__":
    run(force="--force" in sys.argv)
