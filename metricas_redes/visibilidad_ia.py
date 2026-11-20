"""¿Las IA usan al diario para hablar de Chivilcoy? (20/11/2026)

Pedido del editor: «un sistema para que ChatGPT / Claude / Gemini me usen para dar información de noticias de
Chivilcoy». La web ya tiene lo suyo (llms.txt, RSS, «Chivilcoy hoy», el lugar de cada nota en los datos
estructurados y el aviso a Bing con IndexNow; ver diario_web). Esto MIDE si funciona: una vez por semana, con el
informe de métricas de los miércoles, se le hacen a Gemini con búsqueda de Google (la API, con `google_search`)
las mismas preguntas que haría un vecino y se anota si citó a diariolacampaña.com.ar y a qué otros sitios.

ChatGPT y Claude no se miden acá (sus APIs con búsqueda web son pagas). Gemini mira el índice de Google; ChatGPT
se apoya en Bing (por eso el IndexNow) y Claude en su propio buscador.
Son 8 consultas por semana. VISIBILIDAD_IA=0 lo apaga.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlparse

from utils.config import get

PREGUNTAS = (
    "¿Qué noticias hay hoy de Chivilcoy, provincia de Buenos Aires?",
    "¿Qué pasó esta semana en Chivilcoy?",
    "¿Cuáles son las últimas noticias policiales de Chivilcoy?",
    "¿Qué farmacias están de turno hoy en Chivilcoy?",
    "¿Va a llover en Chivilcoy esta semana?",
    "¿Cuántos habitantes tiene Chivilcoy?",
    "¿Cuánto rindió la soja en Chivilcoy en la última campaña?",
    "¿Qué medios de comunicación hay en Chivilcoy?",
)
NUESTRO = re.compile(r"diariolacampa|xn--diariolacampaa|la campaña|radio del centro", re.I)


def activo() -> bool:
    return (os.environ.get("VISIBILIDAD_IA") or "1").strip() != "0"


def _dominio(chunk: dict) -> str:
    """El sitio de una fuente de Gemini: la URI es un redireccionador de Google, el sitio viene en `title`."""
    w = chunk.get("web") or {}
    t = (w.get("title") or "").strip().lower()
    if t and "." in t and " " not in t:
        return t.removeprefix("www.")
    return (urlparse(w.get("uri") or "").netloc or t or "?").removeprefix("www.")


def preguntar(pregunta: str, generar=None) -> dict:
    """{pregunta, citado, menciona, fuentes [dominios], error}."""
    if generar is None:
        from utils.gemini import _generate as generar
    payload = {"contents": [{"role": "user", "parts": [{"text": pregunta}]}],
               "tools": [{"google_search": {}}]}
    try:
        r = generar(get("GEMINI_MODEL", "gemini-3.8-flash"), payload, timeout=90)
        d = r.json()
    except Exception as e:                                 # noqa: BLE001 — una pregunta que falla no tira el informe
        return {"pregunta": pregunta, "citado": False, "menciona": False, "fuentes": [], "error": str(e)[:160]}
    cand = (d.get("candidates") or [{}])[0]
    texto = " ".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts") or [])
    fuentes = []
    for ch in (cand.get("groundingMetadata") or {}).get("groundingChunks") or []:
        dom = _dominio(ch)
        if dom not in fuentes:
            fuentes.append(dom)
    citado = any(NUESTRO.search(f) for f in fuentes)
    return {"pregunta": pregunta, "citado": citado, "menciona": bool(NUESTRO.search(texto)), "fuentes": fuentes,
            "error": ""}


def medir(generar=None) -> dict:
    res = [preguntar(p, generar) for p in PREGUNTAS]
    return {"preguntas": res, "citado": sum(r["citado"] for r in res), "total": len(res),
            "errores": sum(1 for r in res if r["error"])}
