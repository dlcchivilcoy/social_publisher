"""Reparación de texto MAL CODIFICADO (mojibake).

Por qué existe (2026-08-26): el dominio del diario salía en Facebook como
`www.diariolacampaÃ±a.com.ar` en vez de `www.diariolacampaña.com.ar`.

Causa raíz: el `.env` está en UTF-8, pero se sincroniza al secret `ENV_FILE` desde
PowerShell 5.1, que al leer un archivo SIN BOM asume la página de códigos ANSI
(Windows-1252). Cada `ñ` (bytes C3 B1) se lee como los dos caracteres `Ã` + `±` y
se vuelve a guardar en UTF-8 → C3 83 C2 B1. Eso es "mojibake": texto UTF-8 leído
como Latin-1 y re-codificado.

La reparación es exacta y reversible: se re-codifica a Windows-1252 (los mismos
bytes que se malinterpretaron) y se vuelve a decodificar como UTF-8. Si ese
viaje de ida y vuelta no da un UTF-8 válido, el texto NO se toca."""
from __future__ import annotations

# Señales de mojibake. `Ã`/`Â` aparecen al leer UTF-8 como Latin-1; `â€` es el
# arranque de comillas/guiones largos; `ï»¿` es un BOM leído como texto.
_SENALES = ("Ã", "Â", "â€", "â”", "â†", "ï»¿")


def parece_mojibake(texto: str) -> bool:
    """True si el texto tiene pinta de UTF-8 leído como Latin-1."""
    return isinstance(texto, str) and any(s in texto for s in _SENALES)


def reparar_mojibake(texto: str, vueltas: int = 3) -> str:
    """Devuelve el texto con los acentos/la Ñ bien escritos.

    Conservador a propósito: solo repara si el viaje cp1252 → UTF-8 es válido de
    punta a punta. Ante cualquier duda devuelve el texto ORIGINAL, así una clave
    de API o un token (que son ASCII) nunca se tocan. Se repite hasta `vueltas`
    veces porque un texto puede haber pasado dos veces por el mismo error."""
    if not isinstance(texto, str):  # None y demás pasan de largo, sin convertirse a ""
        return texto
    actual = texto
    for _ in range(max(1, vueltas)):
        if not parece_mojibake(actual):
            break
        try:
            arreglado = actual.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            try:
                arreglado = actual.encode("latin-1").decode("utf-8")
            except (UnicodeEncodeError, UnicodeDecodeError):
                break
        if arreglado == actual:
            break
        actual = arreglado
    return actual
