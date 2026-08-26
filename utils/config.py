import os
from pathlib import Path
from dotenv import load_dotenv

from utils.texto import reparar_mojibake

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
    """Lee una variable de entorno REPARANDO la codificación si hace falta.

    El `.env` viaja a la nube dentro del secret `ENV_FILE`, y si se sincroniza con
    una herramienta que no respeta UTF-8 (PowerShell 5.1 es el caso típico) los
    acentos y la Ñ llegan rotos: `Diario La Campaña` → `Diario La CampaÃ±a`. Eso
    salía publicado tal cual en Facebook, YouTube y la web.

    Reparar acá cubre TODO el sistema de una sola vez: ningún módulo puede volver a
    publicar un valor mal codificado, venga el `.env` como venga. Las claves y
    tokens son ASCII, así que `reparar_mojibake` los deja intactos."""
    return reparar_mojibake(os.getenv(key, default))
