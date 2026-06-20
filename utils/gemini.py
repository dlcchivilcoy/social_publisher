"""Desgrabación periodística con Google Gemini (gratis, NO consume tokens de Claude).

Toma el AUDIO extraído de un video (o el video mismo) y devuelve una nota lista para
publicar: {volanta, titulo, texto, resumen}. Usa la API REST de Gemini directamente
con `requests` (sin SDK extra) y fuerza salida JSON estricta con response_schema.

Clave: GEMINI_API_KEY (gratis, Google AI Studio). Modelo configurable con GEMINI_MODEL
(por defecto gemini-2.0-flash, free tier). El audio va inline en base64: para clips de
unos minutos en mono 16 kHz pesa muy poco (~1 MB/min), bien por debajo del límite.
"""
import base64
import json
from pathlib import Path

import requests

from utils.config import get
from utils.logger import get_logger

logger = get_logger("gemini")

API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# Tipos MIME que Gemini acepta para audio/video, por extensión.
_MIME = {
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".wav": "audio/wav", ".ogg": "audio/ogg", ".flac": "audio/flac",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
}

PROMPT = (
    "Sos el editor del «Diario La Campaña» de Chivilcoy (Argentina). Te paso el "
    "audio de un video que mandó un colaborador. Escuchalo y redactá UNA noticia "
    "en español rioplatense (es-AR), en estilo periodístico, tercera persona, "
    "fiel al contenido. NO inventes datos, nombres ni cifras que no estén en el "
    "audio. Devolvé EXACTAMENTE estos campos:\n"
    "- volanta: antetítulo corto (2 a 5 palabras), sin punto final.\n"
    "- titulo: titular atractivo y claro (máx ~90 caracteres), sin punto final.\n"
    "- texto: cuerpo de la nota en párrafos separados por una línea en blanco "
    "(\\n\\n). Bien redactado, sin muletillas del habla.\n"
    "- resumen: resumen breve para redes sociales, máximo 280 caracteres.\n"
    "Si el audio no tiene contenido noticioso claro, igual hacé tu mejor esfuerzo "
    "con lo que se entienda."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "volanta": {"type": "string"},
        "titulo": {"type": "string"},
        "texto": {"type": "string"},
        "resumen": {"type": "string"},
    },
    "required": ["volanta", "titulo", "texto", "resumen"],
}


def _mime(path: Path) -> str:
    return _MIME.get(path.suffix.lower(), "application/octet-stream")


def transcribe_to_nota(media_path: Path) -> dict:
    """Desgraba `media_path` (audio o video) y devuelve {volanta, titulo, texto, resumen}.

    Lanza RuntimeError si falla la API o ValueError si falta la clave.
    """
    media_path = Path(media_path)
    api_key = get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("Falta GEMINI_API_KEY en .env (clave gratis de Google AI Studio).")
    model = get("GEMINI_MODEL") or "gemini-2.0-flash"

    data_b64 = base64.b64encode(media_path.read_bytes()).decode("ascii")
    payload = {
        "contents": [{
            "role": "user",
            "parts": [
                {"text": PROMPT},
                {"inline_data": {"mime_type": _mime(media_path), "data": data_b64}},
            ],
        }],
        "generationConfig": {
            "temperature": 0.4,
            "response_mime_type": "application/json",
            "response_schema": _SCHEMA,
        },
    }

    url = f"{API_BASE}/{model}:generateContent?key={api_key}"
    logger.info(f"Gemini: desgrabando {media_path.name} con {model} ({len(data_b64)//1024} KB b64)…")
    r = requests.post(url, json=payload, timeout=300)
    if r.status_code >= 400:
        raise RuntimeError(f"Gemini {r.status_code}: {r.text[:300]}")

    try:
        cand = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        nota = json.loads(cand)
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Respuesta de Gemini ininteligible: {e} — {r.text[:300]}")

    nota = {k: (str(nota.get(k, "")).strip()) for k in ("volanta", "titulo", "texto", "resumen")}
    if not nota["titulo"] and not nota["texto"]:
        raise RuntimeError("Gemini no devolvió ni título ni texto.")
    logger.info(f"Gemini OK: «{nota['volanta']} — {nota['titulo']}»")
    return nota
