"""Cliente de ESCRITURA de YouTube (Data API v3) — cambia título, descripción,
tags y miniatura de videos YA publicados del canal Radio del Centro.

⚠️ Corre SOLO en local: usa OAuth (consentimiento del dueño del canal) y el token
se guarda en `.yt_token.json` (gitignored). El refresh token NO va al repo público.

Setup (una vez):
  1. Google Cloud Console: habilitar YouTube Data API v3, crear credenciales OAuth 2.0
     tipo "Desktop app", descargar el client_secret.json a la carpeta del proyecto.
  2. En el .env (local): YT_OAUTH_CLIENT=client_secret.json (ruta; opcional, ese es el default).
     YT_CHANNEL_ID ya existe.
  3. python yt_auth.py  (abre el navegador, autorizás con la cuenta del canal).

Scope: youtube.force-ssl (leer + actualizar metadatos + subir miniatura).
"""
from pathlib import Path

from utils.config import get
from utils.logger import get_logger

logger = get_logger("youtube_api")

SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
_ROOT = Path(__file__).resolve().parent.parent
# Token del optimizador SEO → canal RADIO DEL CENTRO.
TOKEN_FILE = _ROOT / ".yt_token.json"
# Token de los Shorts del desgrabador → canal DIARIO LA CAMPAÑA (distinto canal, token
# aparte para no pisar el de Radio del Centro).
SHORTS_TOKEN_FILE = _ROOT / ".yt_token_diario.json"


def _client_secret_path() -> Path:
    raw = get("YT_OAUTH_CLIENT") or "client_secret.json"
    p = Path(raw)
    if not p.is_absolute():
        p = _ROOT / p
    return p


def _shorts_token_path() -> Path:
    raw = get("YT_SHORTS_TOKEN_FILE")
    if not raw:
        return SHORTS_TOKEN_FILE
    p = Path(raw)
    return p if p.is_absolute() else _ROOT / p


def _load_creds(token_file: Path, env_name: str, auth_hint: str):
    """Carga credenciales OAuth desde un archivo de token o, si no está, desde una env
    var con el JSON (para la NUBE). Refresca el access token si venció. El refresh token
    de YouTube (force-ssl) NO rota, así que el mismo token sirve en cada corrida."""
    import json as _json

    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    info = None
    if token_file.exists():
        info = _json.loads(token_file.read_text(encoding="utf-8"))
    else:
        raw = get(env_name)
        if raw:
            try:
                info = _json.loads(raw)
            except _json.JSONDecodeError as e:
                raise RuntimeError(f"{env_name} no es JSON válido: {e}")
    if not info:
        raise RuntimeError(f"No hay token de YouTube ({token_file.name}). {auth_hint}")

    creds = Credentials.from_authorized_user_info(info, SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            if token_file.exists():  # en la nube no hay archivo: el refresh es por corrida
                token_file.write_text(creds.to_json(), encoding="utf-8")
            logger.debug(f"Token de YouTube refrescado ({token_file.name})")
        else:
            raise RuntimeError(f"Token de YouTube inválido ({token_file.name}). {auth_hint}")
    return creds


def _credentials():
    """Credenciales del canal RADIO DEL CENTRO (optimizador SEO)."""
    return _load_creds(TOKEN_FILE, "YT_TOKEN_JSON",
                       "Corré `python yt_auth.py` (local) o cargá YT_TOKEN_JSON (nube).")


def _shorts_credentials():
    """Credenciales del canal DIARIO LA CAMPAÑA (Shorts del desgrabador)."""
    return _load_creds(_shorts_token_path(), "YT_SHORTS_TOKEN_JSON",
                       "Corré `python yt_auth.py diario` (local) o cargá YT_SHORTS_TOKEN_JSON (nube).")


def _service(creds=None):
    from googleapiclient.discovery import build
    return build("youtube", "v3", credentials=creds or _credentials(), cache_discovery=False)


def list_recent_videos(limit: int = 15) -> list[dict]:
    """Devuelve los últimos `limit` videos del canal (más reciente primero) con
    [{id, title, description, tags, categoryId, thumbnail_url, publishedAt}]."""
    yt = _service()
    ch_id = get("YT_CHANNEL_ID")
    if not ch_id:
        raise ValueError("Falta YT_CHANNEL_ID en el .env")

    ch = yt.channels().list(part="contentDetails", id=ch_id).execute()
    items = ch.get("items", [])
    if not items:
        raise RuntimeError(f"No se encontró el canal {ch_id}")
    uploads = items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    vids: list[str] = []
    page_token = None
    while len(vids) < limit:
        pl = yt.playlistItems().list(
            part="contentDetails", playlistId=uploads,
            maxResults=min(50, limit - len(vids)), pageToken=page_token,
        ).execute()
        for it in pl.get("items", []):
            vids.append(it["contentDetails"]["videoId"])
        page_token = pl.get("nextPageToken")
        if not page_token:
            break
    vids = vids[:limit]
    if not vids:
        return []

    out: list[dict] = []
    for i in range(0, len(vids), 50):
        chunk = vids[i:i + 50]
        resp = yt.videos().list(part="snippet", id=",".join(chunk)).execute()
        for v in resp.get("items", []):
            sn = v.get("snippet", {})
            th = sn.get("thumbnails", {})
            thumb = (th.get("maxres") or th.get("standard") or th.get("high")
                     or th.get("medium") or th.get("default") or {})
            out.append({
                "id": v["id"],
                "title": sn.get("title", ""),
                "description": sn.get("description", ""),
                "tags": sn.get("tags", []),
                "categoryId": sn.get("categoryId", "25"),  # 25 = News & Politics
                "thumbnail_url": thumb.get("url", ""),
                "publishedAt": sn.get("publishedAt", ""),
            })
    order = {vid: i for i, vid in enumerate(vids)}
    out.sort(key=lambda d: order.get(d["id"], 9999))
    return out


def get_videos(ids) -> list[dict]:
    """Trae el snippet (title, description, tags, categoryId, thumbnail) de los videos
    con esos IDs. Mismo formato que list_recent_videos."""
    ids = list(ids)
    if not ids:
        return []
    yt = _service()
    out = []
    for i in range(0, len(ids), 50):
        resp = yt.videos().list(part="snippet", id=",".join(ids[i:i + 50])).execute()
        for v in resp.get("items", []):
            sn = v.get("snippet", {})
            th = sn.get("thumbnails", {})
            thumb = (th.get("maxres") or th.get("standard") or th.get("high")
                     or th.get("medium") or th.get("default") or {})
            out.append({
                "id": v["id"],
                "title": sn.get("title", ""),
                "description": sn.get("description", ""),
                "tags": sn.get("tags", []),
                "categoryId": sn.get("categoryId", "25"),
                "thumbnail_url": thumb.get("url", ""),
                "publishedAt": sn.get("publishedAt", ""),
            })
    return out


def update_video_metadata(video_id: str, title: str, description: str, tags=None) -> dict:
    """Actualiza título/descripción/tags de un video.

    ⚠️ videos.update(part=snippet) BORRA los campos del snippet que no se reenvían y
    EXIGE categoryId + title. Por eso traemos el snippet actual, mutamos solo lo que
    cambia y lo reenviamos entero."""
    yt = _service()
    resp = yt.videos().list(part="snippet", id=video_id).execute()
    items = resp.get("items", [])
    if not items:
        raise RuntimeError(f"No se encontró el video {video_id}")
    sn = items[0]["snippet"]
    sn["title"] = (title or "")[:100]
    sn["description"] = description or ""
    if tags is not None:
        sn["tags"] = tags
    body = {"id": video_id, "snippet": sn}
    return yt.videos().update(part="snippet", body=body).execute()


def set_thumbnail(video_id: str, image_path) -> dict:
    """Sube una miniatura custom (JPEG/PNG, <2MB). ⚠️ Requiere canal VERIFICADO por
    teléfono; si no lo está, la API devuelve 403."""
    from googleapiclient.http import MediaFileUpload
    yt = _service()
    media = MediaFileUpload(str(image_path), mimetype="image/jpeg")
    return yt.thumbnails().set(videoId=video_id, media_body=media).execute()


def upload_short(video_path, title: str, description: str, tags=None,
                 category_id: str = "25", privacy: str = "public",
                 made_for_kids: bool = False) -> dict:
    """Sube el reel vertical como YouTube Short (videos.insert, subida reanudable).

    El video lo clasifica YouTube como Short por ser VERTICAL y ≤3 min (el reel del
    diario es 9:16 y ≤60s). Sumamos «#Shorts» a la descripción como refuerzo.

    Devuelve {id, url, watch_url, embed_url, privacy}.

    ⚠️ GOTCHA importante: si el PROYECTO de Google Cloud que usa este OAuth todavía NO
    pasó la auditoría de la YouTube Data API, YouTube fuerza los videos subidos por API a
    PRIVADO sin importar `privacy`. En ese caso el embed no se ve público hasta aprobar la
    auditoría (o hasta hacerlo público a mano). videos.insert cuesta ~1600 unidades de
    cuota (de 10.000/día por defecto) → alcanza para varias notas por día.
    """
    from googleapiclient.http import MediaFileUpload
    yt = _service(_shorts_credentials())  # canal Diario La Campaña

    desc = description or ""
    if "#Shorts" not in desc and "#shorts" not in desc:
        desc = (desc + "\n\n#Shorts").strip()

    body = {
        "snippet": {
            "title": (title or "")[:100],   # YouTube tope duro 100 chars
            "description": desc[:5000],      # tope 5000 chars
            "tags": list(tags or [])[:500],
            "categoryId": category_id or "25",
            "defaultLanguage": "es",
            "defaultAudioLanguage": "es",
        },
        "status": {
            "privacyStatus": privacy or "public",
            "selfDeclaredMadeForKids": bool(made_for_kids),
            "embeddable": True,
        },
    }
    media = MediaFileUpload(str(video_path), mimetype="video/mp4",
                            chunksize=-1, resumable=True)
    req = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    resp = None
    while resp is None:
        _status, resp = req.next_chunk()
    vid = resp["id"]
    logger.info(f"YouTube Short subido: id={vid} (privacy={body['status']['privacyStatus']})")
    return {
        "id": vid,
        "url": f"https://youtu.be/{vid}",
        "watch_url": f"https://www.youtube.com/watch?v={vid}",
        "embed_url": f"https://www.youtube.com/embed/{vid}",
        "short_url": f"https://www.youtube.com/shorts/{vid}",
        "privacy": resp.get("status", {}).get("privacyStatus", body["status"]["privacyStatus"]),
    }


def get_video_stats(ids, shorts: bool = False) -> dict:
    """Métricas (views/likes/comments) de uno o varios videos. Devuelve
    {video_id: {views, likes, comments}}. Útil para guardar las «métricas futuras»
    de los Shorts publicados. `shorts=True` usa el token del canal Diario La Campaña."""
    ids = [i for i in (ids if isinstance(ids, (list, tuple)) else [ids]) if i]
    if not ids:
        return {}
    yt = _service(_shorts_credentials() if shorts else None)
    out: dict[str, dict] = {}
    for i in range(0, len(ids), 50):
        resp = yt.videos().list(part="statistics", id=",".join(ids[i:i + 50])).execute()
        for v in resp.get("items", []):
            st = v.get("statistics", {})
            out[v["id"]] = {
                "views": int(st.get("viewCount", 0) or 0),
                "likes": int(st.get("likeCount", 0) or 0),
                "comments": int(st.get("commentCount", 0) or 0),
            }
    return out
