"""Marca / llamadas a la acción compartidas por todas las publicaciones."""
from utils.config import get


def canal_yt_url() -> str:
    """URL del canal de YouTube (Radio del Centro). Configurable en CANAL_YT_URL."""
    return (get("CANAL_YT_URL") or "youtube.com/radiodelcentro").strip()


def linea_canal_yt() -> str:
    """Línea estándar que invita al canal de YouTube. Se agrega a los posteos de
    FB/IG, a las notas de la web y a la descripción de los videos de YouTube."""
    return f"📺 Todas las notas completas en nuestro canal de YouTube 👉 {canal_yt_url()}"


def sitio_web() -> str:
    """Dominio del diario, BIEN escrito (con la Ñ). Configurable en STORY_SITE_URL."""
    return (get("STORY_SITE_URL") or "www.diariolacampaña.com.ar").strip()


def cierre_youtube(hashtags: str = "") -> str:
    """Cierre fijo de las descripciones de YouTube (pedido 2026-08-26): primero las dos
    llamadas a la acción (web + canal) y los HASHTAGS AL FINAL DE TODO.

    Lo arma el CÓDIGO, no Gemini: cuando la IA escribía la dirección, salía mal (sin la Ñ o
    en punycode). `hashtags` es la línea ya armada (máx 5)."""
    canal = canal_yt_url()
    if not canal.startswith(("http", "www.")):
        canal = f"www.{canal}"
    partes = [
        f"👉 Visitá nuestra web {sitio_web()} y suscribite para no perderte ninguna noticia.",
        f"📺 Todas las notas completas podes verlas en nuestro canal de YouTube: {canal}",
    ]
    if (hashtags or "").strip():
        partes.append(hashtags.strip())
    return "\n\n".join(partes)
