"""Marca / llamadas a la acción compartidas por todas las publicaciones."""
from utils.config import get


def canal_yt_url() -> str:
    """URL del canal de YouTube (Radio del Centro). Configurable en CANAL_YT_URL."""
    return (get("CANAL_YT_URL") or "youtube.com/radiodelcentro").strip()


def linea_canal_yt() -> str:
    """Línea estándar que invita al canal de YouTube. Se agrega a los posteos de
    FB/IG, a las notas de la web y a la descripción de los videos de YouTube."""
    return f"📺 Todas las notas completas en nuestro canal de YouTube 👉 {canal_yt_url()}"
