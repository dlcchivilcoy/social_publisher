"""Cliente de X (Twitter) de @diarioyradio.

Postea cada nota como un tweet individual: **título + resumen + foto + link** a la
nota, recortado a 280 caracteres (en X cualquier link ocupa 23 caracteres fijos).
Usa la API oficial: v1.1 `media_upload` para la foto + v2 `create_tweet` con
`media_ids`. Las 4 claves TWITTER_* viven en .env (y en el secret de la nube).
"""
from pathlib import Path

import tweepy

from utils.config import get
from utils.logger import get_logger

logger = get_logger("twitter")

MAX_TWEET_LEN = 280
TCO_LEN = 23  # cualquier URL ocupa 23 caracteres en X (acortador t.co)


def _clients():
    """Devuelve (client_v2, api_v1). v2 publica el tweet; v1.1 sube la foto."""
    api_key = get("TWITTER_API_KEY")
    api_secret = get("TWITTER_API_SECRET")
    access_token = get("TWITTER_ACCESS_TOKEN")
    access_secret = get("TWITTER_ACCESS_TOKEN_SECRET")

    if not all([api_key, api_secret, access_token, access_secret]):
        raise ValueError("Credenciales de Twitter incompletas en .env")

    client = tweepy.Client(
        consumer_key=api_key,
        consumer_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_secret,
    )
    auth = tweepy.OAuth1UserHandler(api_key, api_secret, access_token, access_secret)
    api_v1 = tweepy.API(auth)
    return client, api_v1


def _recortar(texto: str, limite: int) -> str:
    t = (texto or "").strip()
    if len(t) <= limite:
        return t
    corte = t[:limite].rsplit(" ", 1)[0].rstrip(" ,.;:")
    return (corte + "…") if corte else t[:limite]


def armar_tweet(titulo: str, resumen: str, wix_url: str) -> str:
    """Arma el texto del tweet: título + resumen + link, recortado a 280.

    El link siempre va completo (X lo cuenta como 23 chars). El resumen se mete
    solo si entra; si el título solo ya llena el espacio, se recorta el título."""
    url = (wix_url or "").strip()
    reserva_url = (TCO_LEN + 2) if url else 0  # link + "\n\n"
    disponible = MAX_TWEET_LEN - reserva_url

    titulo = (titulo or "").strip()
    if len(titulo) >= disponible:
        cuerpo = _recortar(titulo, disponible)
    else:
        resto = disponible - len(titulo) - 2  # "\n\n" entre título y resumen
        resumen_corto = _recortar(resumen, resto) if resto >= 30 else ""
        cuerpo = titulo + (f"\n\n{resumen_corto}" if resumen_corto else "")

    return cuerpo + (f"\n\n{url}" if url else "")


def publish(titulo: str, resumen: str = "", image_path: Path = None,
            wix_url: str = "", dry_run: bool = False) -> dict:
    """Publica un tweet con título + resumen + foto + link a la nota."""
    text = armar_tweet(titulo, resumen, wix_url)

    if dry_run:
        logger.info(f"[dry-run][x] tweet ({len(text)} chars) foto={image_path}:\n{text}")
        return {"success": True, "dry_run": True, "text": text}

    client, api_v1 = _clients()

    media_ids = None
    if image_path and Path(image_path).exists():
        try:
            media = api_v1.media_upload(filename=str(image_path))
            media_ids = [media.media_id]
        except Exception as e:
            logger.error(f"[x] no se pudo subir la foto, el tweet va sin imagen: {e}")

    response = client.create_tweet(text=text, media_ids=media_ids)
    tweet_id = response.data["id"]
    logger.debug(f"Tweet publicado id={tweet_id} — {text[:60]}")
    return {"success": True, "id": tweet_id, "text": text}
