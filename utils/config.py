import os
from pathlib import Path
from dotenv import load_dotenv

_REQUIRED = [
    "WIX_API_KEY",
    "WIX_SITE_ID",
    "FACEBOOK_PAGE_ID",
    "FACEBOOK_PAGE_ACCESS_TOKEN",
    "INSTAGRAM_USER_ID",
    "INSTAGRAM_ACCESS_TOKEN",
    "TWITTER_API_KEY",
    "TWITTER_API_SECRET",
    "TWITTER_ACCESS_TOKEN",
    "TWITTER_ACCESS_TOKEN_SECRET",
    "IMGBB_API_KEY",
]

def load_config() -> None:
    env_path = Path(__file__).parent.parent / ".env"
    load_dotenv(dotenv_path=env_path, override=False)

def validate_config() -> list[str]:
    """Returns a list of missing variable names."""
    load_config()
    return [key for key in _REQUIRED if not os.getenv(key)]

def get(key: str, default: str = "") -> str:
    return os.getenv(key, default)
