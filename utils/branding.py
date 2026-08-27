"""Marca / llamadas a la acción compartidas por todas las publicaciones."""
import re

from utils.config import get
from utils.texto import reparar_mojibake

# Dominio del diario: ÚNICA fuente de verdad. Todo lo que se publica (redes, web,
# YouTube, TikTok, placas) sale de acá y no de una constante suelta por archivo.
SITIO_CANONICO = "www.diariolacampaña.com.ar"

# Cualquier forma en la que el dominio puede aparecer MAL escrito:
#   • sin la Ñ:        diariolacampana / diariolacampaa / diariolacampania
#   • mal codificado:  diariolacampaÃ±a   (UTF-8 leído como Latin-1)
#   • en punycode:     xn--diariolacampaa-2nb   (así lo devuelve Wix)
#   • sin el .ar, con o sin http://, con o sin www.
_RE_DOMINIO = re.compile(
    r"(?:https?://)?(?:www\.)?"
    r"(?:diariolacampa(?:ñ|Ñ|Ã±|Ã‘|ni|ny|n)?a|xn--diariolacampaa-2nb)"
    r"\.com(?:\.ar)?/?",
    re.IGNORECASE,
)


def normalizar_sitio(texto: str) -> str:
    """Reescribe cualquier variante del dominio del diario a la forma canónica.

    Se aplica a lo que redacta la IA (que a veces escribe el dominio sin la Ñ) y a
    lo que sale del `.env` (que puede llegar mal codificado desde el secret)."""
    if not texto:
        return texto
    return _RE_DOMINIO.sub(SITIO_CANONICO, reparar_mojibake(texto))


def canal_yt_url() -> str:
    """URL del canal de YouTube (Radio del Centro). Configurable en CANAL_YT_URL."""
    return (get("CANAL_YT_URL") or "youtube.com/radiodelcentro").strip()


def linea_canal_yt() -> str:
    """Línea estándar que invita al canal de YouTube. Se agrega a los posteos de
    FB/IG, a las notas de la web y a la descripción de los videos de YouTube."""
    return f"📺 Todas las notas completas en nuestro canal de YouTube 👉 {canal_yt_url()}"


def sitio_web() -> str:
    """Dominio del diario, BIEN escrito (con la Ñ). Configurable en STORY_SITE_URL.

    Nunca devuelve el valor crudo: lo pasa por `normalizar_sitio`, así una Ñ rota o
    un punycode en el `.env` NO puede terminar publicado. Si `STORY_SITE_URL` apunta
    a otro dominio distinto del diario, se respeta tal cual."""
    return normalizar_sitio((get("STORY_SITE_URL") or "").strip()) or SITIO_CANONICO


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


# ── Reconocer NUESTRO propio cierre dentro de un texto ────────────────────────
# Cuando se reprocesa una descripción que YA publicamos (o cuando la IA copia el
# formato), hay que poder distinguir el CUERPO del CIERRE, para no dejarlo duplicado.
_CIERRES_CONOCIDOS = (
    "👉 Visitá nuestra web {sitio} y suscribite para no perderte ninguna noticia.",
    "📺 Todas las notas completas podes verlas en nuestro canal de YouTube: {canal}",
    "📺 Todas las notas completas en nuestro canal de YouTube 👉 {canal}",
    "📲 Seguí leyendo la nota completa en {sitio}",
    "📲 Seguí leyendo en {sitio}",
    "📲 Seguí leyendo cada nota completa en nuestra web 👉 {sitio}",
    "🔔 Suscribite al canal para más noticias de Chivilcoy y la región.",
)


def _nucleo(linea: str) -> str:
    """Reduce una línea a sus PALABRAS, sin emojis, direcciones ni puntuación.

    Así una llamada a la acción se reconoce aunque cambie el dominio, el emoji o el
    signo de puntuación con el que se escribió."""
    sin_urls = re.sub(r"\S*(?:diariolacampa\S*|youtube\.com\S*|youtu\.be\S*|radiodelcentro)\S*",
                      " ", linea or "", flags=re.IGNORECASE)
    # Los `{sitio}` / `{canal}` de las plantillas de arriba NO son palabras del texto.
    sin_urls = re.sub(r"\{[a-z_]+\}", " ", sin_urls)
    return " ".join(re.sub(r"[^0-9A-Za-zÁÉÍÓÚÜÑáéíóúüñ]+", " ", sin_urls).lower().split())


_NUCLEOS_CIERRE = frozenset(_nucleo(l) for l in _CIERRES_CONOCIDOS) - {""}


def es_linea_de_cierre(linea: str) -> bool:
    """True si la línea NO es cuerpo del texto sino parte del cierre que arma el sistema:
    una línea de puros hashtags, una invitación al canal de YouTube, o una de nuestras
    llamadas a la acción. Sirve para limpiar un texto antes de volver a armarlo."""
    l = (linea or "").strip()
    if not l:
        return False
    if re.fullmatch(r"(?:#[^\s#]+\s*)+", l):                       # solo hashtags
        return True
    if re.search(r"youtube\.com|youtu\.be|canal de YouTube", l, re.IGNORECASE):
        return True
    if _nucleo(l) in _NUCLEOS_CIERRE:                              # una CTA nuestra
        return True
    # Invitación a la web escrita por la IA: la línea es casi solo el dominio.
    if _RE_DOMINIO.search(l):
        return len(_RE_DOMINIO.sub(" ", l).split()) <= 6
    return False
