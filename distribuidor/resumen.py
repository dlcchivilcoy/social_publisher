"""Bajada en ORACIONES COMPLETAS que entre en el lugar que hay (pedido del 17/10/2026).

Nunca se corta una oración con «…». En orden:
  1. la bajada entera, si entra;
  2. un resumen de Gemini con SOLO datos del texto (se valida: entra, termina en punto, no
     trae números que el original no tenga, no trae «…»);
  3. las primeras oraciones enteras que entren;
  4. nada (queda título + link).

Gemini se usa con freno: si falla una vez o la pasada ya gastó demasiado tiempo resumiendo,
el resto de la pasada va directo al paso 3. Así una caída de Gemini nunca traba el reparto.
"""
from __future__ import annotations

import re
import time

from utils.logger import get_logger

logger = get_logger("distribuidor_resumen")

# Fin de oración: . ! ? (con comillas de cierre opcionales) seguido de espacio + mayúscula,
# comilla o signo de apertura, o fin del texto. Así «21.40» o «S.A. de» no cortan.
_FIN = re.compile(r'[.!?][»"”\')]*(?=\s+[A-ZÁÉÍÓÚÑÜ¿¡«"“(\d]|\s*$)')
_NUM = re.compile(r"\d+(?:[.,]\d+)*")

MAX_SEGUNDOS = 300          # tiempo total de Gemini por pasada
_inicio = None
_roto = False
usados = 0


def oraciones(texto: str) -> list[str]:
    texto = re.sub(r"\s+", " ", (texto or "")).strip()
    out, desde = [], 0
    for m in _FIN.finditer(texto):
        o = texto[desde:m.end()].strip()
        if o:
            out.append(o)
        desde = m.end()
    resto = texto[desde:].strip()
    if resto:
        out.append(resto)    # sin punto final: se trata como incompleta (ver completas)
    return out


def completa(o: str) -> bool:
    return bool(re.search(r'[.!?][»"”\')]*$', o.strip())) and not o.strip().endswith("…")


def completas(texto: str, maximo: int, medir=len) -> str:
    """Las primeras oraciones ENTERAS que entren (o "" si ni la primera entra)."""
    out = ""
    for o in oraciones(texto):
        if not completa(o):
            break
        cand = (out + " " + o).strip()
        if medir(cand) > maximo:
            break
        out = cand
    return out


def _gemini_disponible() -> bool:
    global _inicio
    if _roto:
        return False
    if _inicio is None:
        _inicio = time.monotonic()
    return time.monotonic() - _inicio < MAX_SEGUNDOS


def _valido(resumen: str, original: str, maximo: int, medir) -> bool:
    if not resumen or medir(resumen) > maximo or "…" in resumen or "..." in resumen:
        return False
    if not all(completa(o) for o in oraciones(resumen)):
        return False
    if re.search(r"https?://|#\w", resumen):
        return False
    nums_orig = {n.replace(",", ".") for n in _NUM.findall(original)}
    return all(n.replace(",", ".") in nums_orig for n in _NUM.findall(resumen))


def _pedir_gemini(texto: str, titulo: str, maximo: int) -> str:
    from utils import gemini
    from utils.config import get
    prompt = (
        "Sos editor de un diario. Resumí la BAJADA de esta noticia para una red social.\n"
        f"- Máximo {maximo} caracteres en total (contá bien; si dudás, más corto).\n"
        "- Una o dos oraciones COMPLETAS, cada una terminada en punto. Nada de puntos suspensivos.\n"
        "- Usá SOLO datos que están en el texto. No agregues nombres, cifras, fechas ni opiniones.\n"
        "- Si el texto atribuye algo a alguien (una empresa, un funcionario), mantené la atribución.\n"
        "- No repitas el título. Sin emojis, sin hashtags, sin links, sin comillas alrededor.\n"
        "- Respondé SOLO con el resumen.\n\n"
        f"TÍTULO: {titulo}\n\nTEXTO:\n{texto[:4000]}")
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "generationConfig": {"maxOutputTokens": 400}}
    r = gemini._generate(get("GEMINI_MODEL") or gemini._MODELO_DEFAULT, payload, timeout=60,
                         key_pool=gemini._gemini_keys()[:3])
    partes = (((r.json().get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    return re.sub(r"\s+", " ", " ".join(p.get("text", "") for p in partes)).strip().strip('"«»')


def bajada(texto: str, maximo: int, medir=len, titulo: str = "") -> str:
    """La bajada que entra en `maximo`, siempre en oraciones completas (o "")."""
    global _roto, usados
    texto = re.sub(r"\s+", " ", (texto or "")).strip()
    if not texto or maximo < 40:
        return ""
    if medir(texto) <= maximo and all(completa(o) for o in oraciones(texto)):
        return texto
    if _gemini_disponible():
        for intento in range(2):
            try:
                tope = maximo - (15 if intento else 0)     # el 2.º pide un poco más corto
                r = _pedir_gemini(texto, titulo, tope)
                usados += 1
                if _valido(r, texto, maximo, medir):
                    return r
                logger.info(f"resumen descartado (no cumple): {r[:120]!r}")
            except Exception as e:  # noqa: BLE001 — sin Gemini, se sigue con oraciones enteras
                _roto = True
                logger.warning(f"Gemini no pudo resumir ({e}); sigo con oraciones enteras")
                break
    return completas(texto, maximo, medir)
