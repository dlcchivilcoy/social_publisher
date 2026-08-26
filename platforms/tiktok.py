"""Cliente de TikTok — publica el reel DIRECTO en el perfil (Direct Post).

Flujo de la Content Posting API:
  1. `creator_info/query` → datos del creador y privacidades habilitadas (TikTok EXIGE
     consultarlo antes de un Direct Post).
  2. `video/init` con `post_info` → publica derecho en el perfil. Necesita el scope
     `video.publish`, que TikTok habilita recién tras aprobar la app.
  3. PUT del .mp4 a la `upload_url`.
Si la app NO tiene `video.publish`, cae solo al modo BANDEJA (`inbox/video/init`, scope
`video.upload`): el video queda en los borradores y se publica desde la app.

⚠️ EL REFRESH TOKEN DE TIKTOK **ROTA EN CADA USO**: si se pierde el valor nuevo, hay que
volver a autorizar a mano. Por eso el token vive en un ALMACÉN COMPARTIDO:
  - Supabase (tabla `tiktok_token`) si están `SUPABASE_URL` + `SUPABASE_SERVICE_KEY` → es lo
    que permite correr en la NUBE (cada corrida de GitHub Actions arranca limpia).
  - Si no hay Supabase, el archivo local `.tiktok_token.json` (gitignored) — solo sirve local.

Credenciales (.env): TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, TIKTOK_REDIRECT_URI.
El token inicial se saca UNA vez con `tiktok_auth.py`.
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
DIRECT_INIT_URL = f"{OPEN_API}/v2/post/publish/video/init/"
CREATOR_INFO_URL = f"{OPEN_API}/v2/post/publish/creator_info/query/"
STATUS_URL = f"{OPEN_API}/v2/post/publish/status/fetch/"
# `video.publish` = Direct Post (publicar derecho). `video.upload` = bandeja/borradores.
# OJO: TikTok RECHAZA la autorización con error «scope» si la app no tiene ESE permiso habilitado
# en el portal (developers.tiktok.com → tu app → Scopes), aunque el review ya esté aprobado. Para
# autorizar con menos permisos mientras se habilita: TIKTOK_SCOPES="user.info.basic,video.upload".
_SCOPES_DEFAULT = "user.info.basic,video.upload,video.publish"


def scopes() -> str:
    """Scopes a pedir en la autorización. OJO: se lee EN EL MOMENTO, no al importar — el
    `.env` suele cargarse DESPUÉS de los imports, así que una constante de módulo se
    quedaba con el default y TikTok rechazaba la autorización con error «scope»."""
    return (get("TIKTOK_SCOPES") or _SCOPES_DEFAULT).strip()


SCOPES = _SCOPES_DEFAULT  # compat: preferí scopes() (lee el .env en el momento)

TOKEN_FILE = Path(__file__).resolve().parent.parent / ".tiktok_token.json"
TOKEN_TABLE = "tiktok_token"
TOKEN_ID = "diario"  # una sola cuenta de TikTok para todo el ecosistema


def _client() -> tuple[str, str]:
    ck = get("TIKTOK_CLIENT_KEY")
    cs = get("TIKTOK_CLIENT_SECRET")
    if not ck or not cs:
        raise ValueError("Falta TIKTOK_CLIENT_KEY o TIKTOK_CLIENT_SECRET en el .env")
    return ck, cs


# ── Almacén del token (Supabase si hay; si no, archivo local) ─────────────────
def _supabase():
    url, key = (get("SUPABASE_URL") or "").rstrip("/"), get("SUPABASE_SERVICE_KEY")
    return (url, key) if url and key else None


def _sb_headers(key: str) -> dict:
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _load_token() -> dict:
    sb = _supabase()
    if sb:
        url, key = sb
        try:
            r = requests.get(f"{url}/rest/v1/{TOKEN_TABLE}",
                             params={"id": f"eq.{TOKEN_ID}", "select": "data"},
                             headers=_sb_headers(key), timeout=30)
            if r.ok and r.json():
                return r.json()[0]["data"]
            logger.warning("Supabase todavía no tiene el token de TikTok; pruebo el archivo local.")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"No pude leer el token de TikTok de Supabase ({e}); pruebo local.")
    if TOKEN_FILE.exists():
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    raise RuntimeError("No hay token de TikTok. Corré primero: python tiktok_auth.py")


def _save_token(data: dict) -> None:
    """Guarda el token en Supabase (fuente de verdad para la nube) y también en el archivo
    local. Si Supabase falla se avisa FUERTE: perder el refresh token rotado obliga a
    re-autorizar a mano."""
    sb = _supabase()
    if sb:
        url, key = sb
        try:
            r = requests.post(f"{url}/rest/v1/{TOKEN_TABLE}",
                              params={"on_conflict": "id"},
                              headers={**_sb_headers(key), "Prefer": "resolution=merge-duplicates"},
                              json={"id": TOKEN_ID, "data": data}, timeout=30)
            if not r.ok:
                logger.error(f"TikTok: NO pude guardar el token en Supabase ({r.status_code}): "
                             f"{r.text[:200]}. Si se pierde, hay que re-autorizar.")
        except Exception as e:  # noqa: BLE001
            logger.error(f"TikTok: NO pude guardar el token en Supabase ({e}).")
    try:
        TOKEN_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"No pude escribir {TOKEN_FILE.name} ({e}).")


def save_initial_token(token_resp: dict) -> None:
    """Lo usa tiktok_auth.py tras el primer intercambio de código."""
    _persist(token_resp)


def _persist(tr: dict) -> dict:
    data = {
        "access_token": tr["access_token"],
        "refresh_token": tr["refresh_token"],  # TikTok ROTA este valor en cada refresh
        "access_expires_at": int(time.time()) + int(tr.get("expires_in", 0)) - 60,
        "open_id": tr.get("open_id", ""),
        "scope": tr.get("scope", ""),
    }
    _save_token(data)
    return data


def _access_token() -> str:
    """Access token válido, refrescándolo si hace falta (y persistiendo el NUEVO refresh
    token, porque TikTok lo rota en cada uso)."""
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


def scopes_otorgados() -> str:
    """Scopes que TikTok concedió en la última autorización (vacío si no figuran)."""
    try:
        return (_load_token().get("scope") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _puede_publicar_directo() -> bool:
    sc = scopes_otorgados()
    return ("video.publish" in sc) if sc else True  # sin el dato: se intenta igual


def creator_info(token: str = "") -> dict:
    """Datos del creador (TikTok EXIGE consultarlo antes de un Direct Post): privacidades
    habilitadas, duración máxima, apodo, etc."""
    token = token or _access_token()
    r = requests.post(CREATOR_INFO_URL, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
    }, json={}, timeout=60)
    if not r.ok:
        raise RuntimeError(f"TikTok creator_info falló ({r.status_code}): {r.text[:300]}")
    return r.json().get("data", {})


def _subir_archivo(upload_url: str, video_path: Path, size: int) -> None:
    with open(video_path, "rb") as f:
        data = f.read()
    put = requests.put(upload_url, headers={
        "Content-Type": "video/mp4",
        "Content-Range": f"bytes 0-{size - 1}/{size}",
        "Content-Length": str(size),
    }, data=data, timeout=300)
    if put.status_code not in (200, 201):
        raise RuntimeError(f"TikTok subida del video falló ({put.status_code}): {put.text[:300]}")


def _titulo_tiktok(texto: str) -> str:
    """Caption de TikTok: máximo 2200 caracteres (los hashtags cuentan)."""
    return " ".join((texto or "").split()).strip()[:2200]


def upload_to_inbox(video_path: Path, titulo: str = "") -> dict:
    """Sube el .mp4 a la BANDEJA del creador (borradores): se termina de publicar desde la
    app de TikTok. Es el modo que NO necesita `video.publish`. (`titulo` se ignora: en la
    bandeja el texto se escribe en la app.)"""
    video_path = Path(video_path)
    size = video_path.stat().st_size
    token = _access_token()
    init = requests.post(INBOX_INIT_URL, headers={
        "Authorization": f"Bearer {token}", "Content-Type": "application/json",
    }, json={"source_info": {
        "source": "FILE_UPLOAD", "video_size": size,
        "chunk_size": size, "total_chunk_count": 1,  # el reel pesa poco => un solo chunk
    }}, timeout=60)
    if not init.ok:
        raise RuntimeError(f"TikTok init falló ({init.status_code}): {init.text[:300]}")
    d = init.json().get("data", {})
    publish_id, upload_url = d.get("publish_id"), d.get("upload_url")
    if not upload_url:
        raise RuntimeError(f"TikTok no devolvió upload_url: {init.text[:300]}")
    _subir_archivo(upload_url, video_path, size)
    logger.info(f"Reel enviado a la BANDEJA de TikTok (publish_id={publish_id}). Publicalo desde la app.")
    return {"success": True, "modo": "bandeja", "publish_id": publish_id}


def publish(video_path, titulo: str = "", *, privacidad: str = "") -> dict:
    """PUBLICA el reel derecho en el perfil de TikTok (Direct Post), con `titulo` de caption.

    Si la app no tiene el scope `video.publish`, o TikTok rechaza el Direct Post, cae solo al
    modo BANDEJA (borradores) para no perder la publicación, y lo avisa en el resultado."""
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"No existe el reel para TikTok: {video_path}")
    if not _puede_publicar_directo():
        logger.warning("TikTok: la app no tiene permiso de publicación directa; va a la bandeja.")
        return upload_to_inbox(video_path, titulo)

    size = video_path.stat().st_size
    token = _access_token()
    try:
        info = creator_info(token)
        opciones = info.get("privacy_level_options") or []
        nick = info.get("creator_nickname", "")
        # Preferimos público; si la app quedara sin auditar, TikTok solo deja SELF_ONLY.
        pref = (privacidad or get("TIKTOK_PRIVACIDAD") or "PUBLIC_TO_EVERYONE").strip()
        nivel = pref if pref in opciones else (opciones[0] if opciones else "SELF_ONLY")
        if nivel != pref:
            logger.warning(f"TikTok: «{pref}» no está habilitada; publico como «{nivel}».")
        dur_max = info.get("max_video_post_duration_sec")
        logger.info(f"TikTok: publicando en @{nick} (privacidad {nivel}"
                    + (f", máx {dur_max}s" if dur_max else "") + ")…")

        init = requests.post(DIRECT_INIT_URL, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=UTF-8",
        }, json={
            "post_info": {
                "title": _titulo_tiktok(titulo),
                "privacy_level": nivel,
                "disable_duet": False,
                "disable_comment": False,
                "disable_stitch": False,
                "video_cover_timestamp_ms": 1000,
            },
            "source_info": {
                "source": "FILE_UPLOAD", "video_size": size,
                "chunk_size": size, "total_chunk_count": 1,
            },
        }, timeout=60)
        if not init.ok:
            raise RuntimeError(f"TikTok direct init falló ({init.status_code}): {init.text[:300]}")
        d = init.json().get("data", {})
        publish_id, upload_url = d.get("publish_id"), d.get("upload_url")
        if not upload_url:
            raise RuntimeError(f"TikTok no devolvió upload_url: {init.text[:300]}")
        _subir_archivo(upload_url, video_path, size)
        logger.info(f"Reel PUBLICADO en TikTok (publish_id={publish_id}, privacidad={nivel}).")
        return {"success": True, "modo": "directo", "publish_id": publish_id, "privacidad": nivel}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"TikTok: la publicación directa falló ({e}); lo mando a la bandeja.")
        out = upload_to_inbox(video_path, titulo)
        out["error_directo"] = str(e)[:300]
        return out


def estado(publish_id: str) -> dict:
    """Estado de una publicación (PROCESSING / PUBLISH_COMPLETE / FAILED)."""
    r = requests.post(STATUS_URL, headers={
        "Authorization": f"Bearer {_access_token()}", "Content-Type": "application/json; charset=UTF-8",
    }, json={"publish_id": publish_id}, timeout=60)
    if not r.ok:
        raise RuntimeError(f"TikTok status falló ({r.status_code}): {r.text[:300]}")
    return r.json().get("data", {})
