"""Cliente de TikTok — sube el reel a la BANDEJA del creador (no publica directo).

Flujo "Upload to TikTok (draft)" de la Content Posting API: el video se sube al
inbox de la cuenta y el creador lo termina de publicar desde la app (ahí puede
ponerlo público y agregarle la canción trending). NO requiere la auditoría de
TikTok (esa es solo para Direct Post público).

⚠️ Corre SOLO en local: el refresh token de TikTok ROTA en cada uso y se guarda
en `.tiktok_token.json` (gitignored). NO subir ese archivo al repo público.

Credenciales (en el .env local): TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET,
TIKTOK_REDIRECT_URI. El token inicial se obtiene una vez con `tiktok_auth.py`.
"""
import json
import time
from pathlib import Path

import requests

from utils.config import get
from utils.logger import get_logger

logger = get_logger("tiktok")

OPEN_API = "https://open.tiktokapis.com"
TOKEN_URL = f"{OPEN_API}/v2/oauth/token/"
INBOX_INIT_URL = f"{OPEN_API}/v2/post/publish/inbox/video/init/"
SCOPES = "user.info.basic,video.upload"

TOKEN_FILE = Path(__file__).resolve().parent.parent / ".tiktok_token.json"


def _client() -> tuple[str, str]:
    ck = get("TIKTOK_CLIENT_KEY")
    cs = get("TIKTOK_CLIENT_SECRET")
    if not ck or not cs:
        raise ValueError("Falta TIKTOK_CLIENT_KEY o TIKTOK_CLIENT_SECRET en el .env")
    return ck, cs


def _load_token() -> dict:
    if not TOKEN_FILE.exists():
        raise RuntimeError("No hay token de TikTok. Corré primero: python tiktok_auth.py")
    return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))


def _save_token(data: dict) -> None:
    TOKEN_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def save_initial_token(token_resp: dict) -> None:
    """Lo usa tiktok_auth.py tras el primer intercambio de código."""
    _persist(token_resp)


def _persist(tr: dict) -> dict:
    data = {
        "access_token": tr["access_token"],
        "refresh_token": tr["refresh_token"],  # TikTok rota este valor en cada refresh
        "access_expires_at": int(time.time()) + int(tr.get("expires_in", 0)) - 60,
        "open_id": tr.get("open_id", ""),
    }
    _save_token(data)
    return data


def _access_token() -> str:
    """Devuelve un access token válido, refrescándolo si hace falta (y persistiendo
    el NUEVO refresh token, porque TikTok lo rota)."""
    tok = _load_token()
    if time.time() < tok.get("access_expires_at", 0):
        return tok["access_token"]
    ck, cs = _client()
    r = requests.post(TOKEN_URL, data={
        "client_key": ck, "client_secret": cs,
        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
    }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
    if not r.ok:
        raise RuntimeError(f"TikTok: no se pudo refrescar el token ({r.status_code}): {r.text[:200]}")
    tok = _persist(r.json())
    logger.debug("Access token de TikTok refrescado")
    return tok["access_token"]


def upload_to_inbox(video_path: Path) -> dict:
    """Sube el .mp4 a la bandeja del creador (subida directa del archivo en 1 chunk;
    el reel pesa pocos MB). Devuelve el publish_id. El creador lo publica desde la app."""
    video_path = Path(video_path)
    size = video_path.stat().st_size
    token = _access_token()

    init = requests.post(INBOX_INIT_URL, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json",
    }, json={"source_info": {
        "source": "FILE_UPLOAD", "video_size": size,
        "chunk_size": size, "total_chunk_count": 1,  # archivo chico => un solo chunk
    }}, timeout=60)
    if not init.ok:
        raise RuntimeError(f"TikTok init falló ({init.status_code}): {init.text[:300]}")
    d = init.json().get("data", {})
    publish_id, upload_url = d.get("publish_id"), d.get("upload_url")
    if not upload_url:
        raise RuntimeError(f"TikTok no devolvió upload_url: {init.text[:300]}")

    with open(video_path, "rb") as f:
        data = f.read()
    put = requests.put(upload_url, headers={
        "Content-Type": "video/mp4",
        "Content-Range": f"bytes 0-{size - 1}/{size}",
        "Content-Length": str(size),
    }, data=data, timeout=300)
    if put.status_code not in (200, 201):
        raise RuntimeError(f"TikTok subida del video falló ({put.status_code}): {put.text[:300]}")

    logger.info(f"Reel enviado a la bandeja de TikTok (publish_id={publish_id}). Publicalo desde la app.")
    return {"success": True, "publish_id": publish_id}
