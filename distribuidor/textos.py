"""Textos para X y TikTok a partir de lo que YA generó cada sistema.

Regla de oro: no se inventa nada. Solo se recorta, se reordena y se sacan cosas que en
cada red no sirven (links no clicables en TikTok, hashtags de más, la invitación a «seguir
leyendo» con el dominio pelado). Si el texto original no alcanza, sale más corto.
"""
from __future__ import annotations

import re
import unicodedata

from . import config

X_MAX = 280
X_URL = 23                 # X cuenta todo link como 23
TIKTOK_MAX = 2200

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


def _cortar(texto: str, maximo: int, medir=len) -> str:
    """Corta en el último fin de oración que entre; si no, en palabra + «…»."""
    texto = texto.strip()
    if medir(texto) <= maximo:
        return texto
    corte = ""
    for m in re.finditer(r"[.!?…](?=\s|$)", texto):
        cand = texto[:m.end()]
        if medir(cand) <= maximo:
            corte = cand
        else:
            break
    if corte and medir(corte) >= maximo * 0.45:
        return corte.strip()
    palabras, out = texto.split(), ""
    for p in palabras:
        cand = (out + " " + p).strip()
        if medir(cand + "…") > maximo:
            break
        out = cand
    return (out.rstrip(",;:") + "…") if out else ""


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


def link_de(ev: dict) -> tuple[str, str]:
    """(etiqueta, url) del link que acompaña en X, o ("", "")."""
    if ev.get("origen") == "entrevistas" and ev.get("youtube_url"):
        return "▶️ La entrevista completa:", ev["youtube_url"]
    if ev.get("nota_url"):
        return ("📲 Nota completa:" if ev.get("tipo") == "video" else ""), ev["nota_url"]
    if ev.get("youtube_url"):
        return "▶️ Video completo:", ev["youtube_url"]
    return "", ""


def texto_x(ev: dict) -> str:
    """Texto para X: título, lo que entre de la bajada, el link y (si suma) un hashtag."""
    if ev.get("texto_x"):
        return _cortar(ev["texto_x"], X_MAX, largo_x)
    titulo = _titulo(ev)
    cuerpo = _cuerpo(ev)
    etiqueta, url = link_de(ev)
    pie = (f"{etiqueta} {url}".strip()) if url else ""
    prefijo = "📰 " if ev.get("tipo") == "nota" else ("🎥 " if ev.get("relacionado_nota") else "")

    tag = ""
    # Notas web: sin hashtag. La sección no dice de qué localidad es (hay notas de Suipacha o
    # nacionales) y un #Chivilcoy equivocado es engañoso. Las producciones propias sí lo llevan.
    if ev.get("origen") == "web" or ev.get("categoria") == "nacionales":
        tag = ""
    elif ev.get("origen") in ("bot", "entrevistas") and "chivilcoy" not in (
            _sin_tildes(titulo + " " + cuerpo).lower()):
        tag = "#Chivilcoy"
    elif ev.get("origen") == "policiales" and ev.get("localidad"):
        loc = ev["localidad"]
        if _sin_tildes(loc).lower() not in _sin_tildes(titulo).lower():
            tag = _hashtag(loc)

    cabeza = _cortar(prefijo + titulo, X_MAX - (largo_x(pie) + 2 if pie else 0), largo_x)
    fijo = cabeza + (("\n\n" + pie) if pie else "")
    lugar = X_MAX - largo_x(fijo) - 2
    # Lugar para el hashtag, salvo que deje la bajada demasiado corta (la bajada informa más).
    if tag and lugar - (largo_x(tag) + 1) >= 80:
        lugar -= largo_x(tag) + 1
    bajada = _cortar(cuerpo.replace("\n", " "), lugar, largo_x) if (cuerpo and lugar > 40) else ""
    texto = cabeza + (("\n\n" + bajada) if bajada else "") + (("\n\n" + pie) if pie else "")
    if tag and largo_x(texto + "\n" + tag) <= X_MAX:
        texto += "\n" + tag
    return texto.strip()


def hashtags_tiktok(ev: dict) -> list[str]:
    fijos = list(config.TIKTOK_HASHTAGS_FIJOS)
    extra = []
    for h in (ev.get("hashtags") or []):
        h = "#" + h.lstrip("#").strip()
        if len(h) > 1 and h.lower() not in [x.lower() for x in fijos + extra]:
            extra.append(h)
    if ev.get("origen") == "policiales":
        for h in (_hashtag(ev.get("localidad") or "").lower(), "#policiales"):
            if h and h not in [x.lower() for x in fijos + extra]:
                extra.append(h)
    return fijos + extra[:2]


def texto_tiktok(ev: dict) -> str:
    """Descripción para TikTok: título + bajada, sin links (no se tocan), y 5 hashtags máx."""
    tags = " ".join(hashtags_tiktok(ev))
    if ev.get("texto_tiktok"):
        base = limpiar(ev["texto_tiktok"])
    else:
        titulo, cuerpo = _titulo(ev), _cuerpo(ev)
        base = titulo + (("\n\n" + cuerpo) if cuerpo else "")
    if ev.get("origen") in ("bot", "policiales", "web") and ev.get("nota_url"):
        base += f"\n\n📲 La nota completa, en {config.SITIO}"
    elif ev.get("origen") == "entrevistas":
        base += "\n\n▶️ La entrevista completa, en YouTube: Radio del Centro"
    base = _cortar(base, TIKTOK_MAX - len(tags) - 2)
    return f"{base}\n\n{tags}".strip()
