from pathlib import Path

import tweepy

from utils.config import get
from utils.logger import get_logger

logger = get_logger("twitter")

MAX_TWEET_LEN = 280


def publish(body: str, image_path: Path, title: str = "", wix_url: str = "") -> dict:
    api_key    = get("TWITTER_API_KEY")
    api_secret = get("TWITTER_API_SECRET")
    access_token  = get("TWITTER_ACCESS_TOKEN")
    access_secret = get("TWITTER_ACCESS_TOKEN_SECRET")

    if not all([api_key, api_secret, access_token, access_secret]):
        raise ValueError("Credenciales de Twitter incompletas en .env")

    client = tweepy.Client(
        consumer_key=api_key,
        consumer_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_secret,
    )

    # Tweet = título + link a Wix (si hay URL disponible) o solo el cuerpo truncado
    if title and wix_url:
        # Formato: "Título\n\nhttps://..." — respeta límite de 280 chars
        link_part = f"\n\n{wix_url}"
        max_title = MAX_TWEET_LEN - len(link_part)
        tweet_text = title[:max_title] + link_part
    elif title:
        tweet_text = title[:MAX_TWEET_LEN]
    else:
        tweet_text = body[:MAX_TWEET_LEN]

    response = client.create_tweet(text=tweet_text)
    tweet_id = response.data["id"]
    logger.debug(f"Tweet publicado id={tweet_id} — {tweet_text[:60]}")
    return {"success": True, "id": tweet_id}
