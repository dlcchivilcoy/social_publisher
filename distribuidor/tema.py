"""¿Una publicación es POLICIAL? Para el intercalado de X y TikTok (17/10/2026, PDF «Prompt integral»).

Regla: en cada canal nunca dos policiales seguidos; entre uno y otro, al menos
MIN_NON_POLICE_POSTS_BETWEEN_POLICE publicaciones de otra temática (1 por defecto).

Policial es todo lo del origen «policiales» y, de los demás orígenes (el bot, las entrevistas, las
notas web o lo que se cargó a mano en Buffer), lo que en el TÍTULO nombra un hecho policial: el bot
también publica choques, robos y operativos de Chivilcoy. La lista son los términos fuertes del
diccionario de policiales-bsas (scraper/keywords.py), recortados a los que casi nunca aparecen en
otra clase de nota.
"""
from __future__ import annotations

import re
import unicodedata

_TERMINOS = (
    r"rob(o|os|aron|ado|ada|ados|adas)", r"hurt(o|aron)", r"asalt(o|aron|ante)", r"entradera",
    r"motochorros?", r"abigeato", r"estafa(dor)?", r"homicidio", r"asesina(to|ron|do|da)",
    r"femicidio", r"apunala(do|da)?", r"balacera", r"tiroteo", r"balead[oa]", r"golpiza",
    r"violencia de genero", r"narco(trafico|menudeo)", r"estupefacientes", r"cocaina",
    r"siniestro vial", r"choque", r"chocaron", r"colision", r"vuelco", r"volco", r"atropell(o|ado|ada|aron)",
    r"accidente (fatal|de transito)", r"incendio", r"se incendio", r"detenid[oa]s?", r"detuvieron",
    r"aprehendid[oa]s?", r"aprehendieron", r"allanamientos?", r"prision preventiva", r"imputad[oa]",
    r"profugo", r"secuestr(o|aron)", r"incautaron", r"operativo policial", r"condenad[oa]",
    r"suicidio", r"sin vida", r"cadaver", r"desaparecid[oa]", r"abuso sexual", r"policiales?",
)
_RE = re.compile(r"\b(" + "|".join(_TERMINOS) + r")\b")


def _plano(texto: str) -> str:
    t = unicodedata.normalize("NFKD", (texto or "").lower())
    return " ".join("".join(c for c in t if not unicodedata.combining(c)).split())


def es_policial_texto(texto: str) -> bool:
    return bool(_RE.search(_plano(texto)))


def es_policial(pub: dict) -> bool:
    """Una publicación del distribuidor (o un post de Buffer ajeno, con `texto`)."""
    if pub.get("origen") == "policiales" or pub.get("policial"):
        return True
    titulo = pub.get("titulo") or ""
    if not titulo:                       # lo ajeno de Buffer: la primera línea del texto
        titulo = next((l for l in (pub.get("texto") or "").splitlines() if l.strip()), "")
    return es_policial_texto(titulo)
