import base64
from pathlib import Path

import requests

from utils.config import get
from utils.logger import get_logger

logger = get_logger("image_host")


def upload_to_imgbb(image_path: Path) -> str:
    """Upload image to ImgBB and return a public HTTPS URL (expires in 10 min)."""
    api_key = get("IMGBB_API_KEY")
    if not api_key:
        raise ValueError("IMGBB_API_KEY not set in .env")

    with open(image_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode("utf-8")

    resp = requests.post(
        "https://api.imgbb.com/1/upload",
        data={"key": api_key, "image": encoded, "expiration": 600},
        timeout=30,
    )
    resp.raise_for_status()
    url = resp.json()["data"]["url"]
    logger.debug(f"ImgBB upload OK: {url}")
    return url
