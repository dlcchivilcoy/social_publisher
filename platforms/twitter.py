from pathlib import Path

import tweepy

from utils.config import get
from utils.logger import get_logger

logger = get_logger("twitter")

MAX_TWEET_LEN = 280


def publish(body: str, image_path: Path) -> dict:
    api_key = get("TWITTER_API_KEY")
    api_secret = get("TWITTER_API_SECRET")
    access_token = get("TWITTER_ACCESS_TOKEN")
    access_secret = get("TWITTER_ACCESS_TOKEN_SECRET")

    if not all([api_key, api_secret, access_token, access_secret]):
        raise ValueError("Credenciales de Twitter incompletas en .env")

    # v1.1 API (para subir media)
    auth = tweepy.OAuth1UserHandler(api_key, api_secret, access_token, access_secret)
    api_v1 = tweepy.API(auth)

    media = api_v1.media_upload(filename=str(image_path))
    media_id = str(media.media_id)
    logger.debug(f"Twitter media_id={media_id}")

    # v2 API (para crear el tweet)
    client = tweepy.Client(
        consumer_key=api_key,
        consumer_secret=api_secret,
        access_token=access_token,
        access_token_secret=access_secret,
    )

    tweet_text = body[:MAX_TWEET_LEN]
    response = client.create_tweet(text=tweet_text, media_ids=[media_id])
    tweet_id = response.data["id"]
    logger.debug(f"Tweet publicado id={tweet_id}")
    return {"success": True, "id": tweet_id}
