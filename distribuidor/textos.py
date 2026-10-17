"""Textos para X y TikTok a partir de lo que YA generó cada sistema.

Formato (pedido del 17/10/2026): SOLO título + bajada + link (TikTok suma sus hashtags).
La bajada va SIEMPRE en oraciones completas: si no entra entera se resume con datos del
propio texto (resumen.py). Nunca una oración cortada con «…». No se inventa nada.
"""
from __future__ import annotations

import re
import unicodedata

from . import config, resumen

X_MAX = 280
X_URL = 23                 # X cuenta todo link como 23
TIKTOK_MAX = 2200
TIKTOK_BAJADA_MAX = 450     # la bajada en TikTok: corta y entera
VERSION = 2                # textos: 2 = título + bajada completa + link (17/10/2026)

_URL = re.compile(r"https?://\S+|www\.\S+", re.I)
_HASHTAG = re.compile(r"(?<!\w)#[\wÁÉÍÓÚÜÑáéíóúüñ]+")
_RENGLON_RELLENO = re.compile(
    r"(seguí|segui|seguinos|leé|lee|más info|mas info|nota completa|para continuar leyendo|"
    r"escuchá|mirá la entrevista|link en|enlace en)", re.I)


def _peso(c: str) -> int:
    """Peso de un carácter para X (twitter-text v3): latinos 1, emojis/CJK 2."""
    o = ord(c)
    if o <= 4351 or 8192 <= o <= 8205 or 8208 <= o <= 8223 or 8242 <= o <= 8247:
        return 1
    return 2


def largo_x(texto: str) -> int:
    total, ultimo = 0, 0
    for m in _URL.finditer(texto):
        total += sum(_peso(c) for c in texto[ultimo:m.start()]) + X_URL
        ultimo = m.end()
    total += sum(_peso(c) for c in texto[ultimo:])
    return total


def limpiar(texto: str, sin_links: bool = True, sin_hashtags: bool = True) -> str:
    """Saca links, hashtags y renglones de relleno; normaliza espacios."""
    renglones = []
    for r in (texto or "").replace("\r", "").split("\n"):
        r2 = r
        if sin_links:
            r2 = _URL.sub("", r2)
        if sin_hashtags:
            r2 = _HASHTAG.sub("", r2)
        r2 = re.sub(r"[ \t]+", " ", r2).strip(" ·|-–—")
        if not r2:
            renglones.append("")
            continue
        if _RENGLON_RELLENO.search(r2) and len(r2) < 140 and (sin_links and r != r2 or ":" in r2):
            continue  # «📲 Seguí leyendo en …» sin el link no dice nada
        renglones.append(r2)
    out = "\n".join(renglones)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def _hashtag(palabra: str) -> str:
    """«San Andrés de Giles» → «#SanAndrésDeGiles»."""
    limpio = re.sub(r"[^\wÁÉÍÓÚÜÑáéíóúüñ ]", " ", palabra or "")
    partes = [p[:1].upper() + p[1:] for p in limpio.split() if p]
    return "#" + "".join(partes) if partes else ""


def _sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def _titulo(ev: dict) -> str:
    t = limpiar(ev.get("titulo") or "", sin_links=True, sin_hashtags=True)
    return re.sub(r"\s+", " ", t).strip()


def _cuerpo(ev: dict) -> str:
    d = limpiar(ev.get("descripcion") or "")
    t = _titulo(ev)
    # Si la descripción arranca repitiendo el título, se saca la repetición.
    if t and _sin_tildes(d.lower()).startswith(_sin_tildes(t.lower())[:60]):
        d = d[len(t):].lstrip(" .:—-\n")
    return d


def link_de(ev: dict) -> str:
    """El link que acompaña: la entrevista completa (recortes) o la nota de la web."""
    if ev.get("origen") == "entrevistas" and ev.get("youtube_url"):
        return ev["youtube_url"]
    return ev.get("nota_url") or ev.get("youtube_url") or ""


def texto_x(ev: dict) -> str:
    """X: SOLO título + bajada + link (pedido del 17/10/2026). La bajada va en oraciones
    completas: si no entra entera, se resume (ver resumen.py). Nunca una oración cortada."""
    titulo = _titulo(ev)
    url = link_de(ev)
    fijo = titulo + (("\n\n" + url) if url else "")
    lugar = X_MAX - largo_x(fijo) - 2
    baj = resumen.bajada(_cuerpo(ev), lugar, largo_x, titulo)
    return (titulo + (("\n\n" + baj) if baj else "") + (("\n\n" + url) if url else "")).strip()


def hashtags_tiktok(ev: dict) -> list[str]:
    fijos = list(config.TIKTOK_HASHTAGS_FIJOS)
    extra = []
    for h in (ev.get("hashtags") or []):
        h = "#" + h.lstrip("#").strip()
        if len(h) > 1 and h.lower() not in [x.lower() for x in fijos + extra]:
            extra.append(h)
    if ev.get("origen") == "nacionales" and ev.get("categoria"):
        h = _hashtag(ev["categoria"]).lower()
        if h and h not in [x.lower() for x in fijos + extra]:
            extra.insert(0, h)
    if ev.get("origen") == "policiales":
        for h in (_hashtag(ev.get("localidad") or "").lower(), "#policiales"):
            if h and h not in [x.lower() for x in fijos + extra]:
                extra.append(h)
    return fijos + extra[:2]


def texto_tiktok(ev: dict) -> str:
    """TikTok: título + bajada (oraciones completas, resumida si es larga) + link + hashtags."""
    titulo = _titulo(ev)
    url = link_de(ev)
    baj = resumen.bajada(_cuerpo(ev), TIKTOK_BAJADA_MAX, len, titulo)
    tags = " ".join(hashtags_tiktok(ev))
    partes = [titulo, baj, url, tags]
    return "\n\n".join(p for p in partes if p).strip()
