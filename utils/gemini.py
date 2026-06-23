"""Desgrabación periodística con Google Gemini (gratis, NO consume tokens de Claude).

Le manda a Gemini el VIDEO COMPLETO (no solo el audio) vía la Files API, así
aprovecha TODO: lo que se habla, el texto en pantalla, los subtítulos y lo que se ve.
Acepta además contexto extra: un texto y/o fotos que el colaborador anexe en la carpeta.

Devuelve {hay_noticia, volanta, titulo, texto, resumen, mejor_momento_seg}:
- hay_noticia: si pudo extraer info real para una nota.
- mejor_momento_seg: el segundo del cuadro más representativo (para la foto de portada).

Clave: GEMINI_API_KEY (gratis, Google AI Studio). Modelo configurable con GEMINI_MODEL
(por defecto gemini-2.5-flash; OJO: gemini-2.0-flash ya no tiene cuota gratis, 429 limit:0).
"""
import base64
import json
import time
from pathlib import Path

import requests

from utils.config import get
from utils.logger import get_logger

logger = get_logger("gemini")

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
UPLOAD_URL = "https://generativelanguage.googleapis.com/upload/v1beta/files"

_VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v", ".mpg", ".mpeg"}
_MIME = {
    ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".wav": "audio/wav", ".ogg": "audio/ogg", ".flac": "audio/flac",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm",
    ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
    ".mpg": "video/mpeg", ".mpeg": "video/mpeg", ".m4v": "video/x-m4v",
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
}

PROMPT_BASE = (
    "Sos el editor del «Diario La Campaña» de Chivilcoy (Argentina). Un colaborador "
    "mandó un VIDEO (y a veces fotos y/o un texto con datos). Analizá TODO el material: "
    "lo que se HABLA en el audio, el TEXTO que aparece en pantalla, los SUBTÍTULOS, lo "
    "que se VE en las imágenes, y el texto/fotos de contexto si los hay. Con eso armá "
    "UNA noticia en español rioplatense (es-AR), estilo periodístico, tercera persona, "
    "fiel al material. NO inventes datos, nombres ni cifras que no estén en el material. "
    "NUNCA entregues la transcripción cruda: reescribí con criterio editorial.\n"
    "Devolvé EXACTAMENTE estos campos:\n"
    "- hay_noticia: true si pudiste extraer información REAL y suficiente para una nota; "
    "false si el material no alcanza (p.ej. solo música, imágenes sin datos, nada legible).\n"
    "- volanta: antetítulo corto (2 a 5 palabras) que dé contexto, sin punto final. "
    "Vacío si hay_noticia es false.\n"
    "- titulo: titular atractivo, claro y fiel al contenido (máx ~90 caracteres), sin punto "
    "final. Puede ser una cita breve y textual si representa bien lo central. Vacío si false.\n"
    "- texto: cuerpo de la nota en párrafos separados por una línea en blanco (\\n\\n). "
    "ORDENALO POR TEMAS, no minuto a minuto: agrupá lo que se dice por asunto. Párrafos de "
    "lectura ágil y extensión variada. Cerrá recuperando una idea fuerte, un dato de agenda "
    "o una definición del entrevistado. La extensión la manda el material: si hay mucho, "
    "desarrollá; si es breve, priorizá FIDELIDAD antes que extensión, sin rellenar ni "
    "repetir. Vacío si false.\n"
    "- resumen: resumen breve para redes (máximo 280 caracteres) que diga quién habla, qué "
    "sostiene y por qué importa. Vacío si false.\n"
    "- mejor_momento_seg: el SEGUNDO del video (número entero) con el cuadro más "
    "representativo, llamativo o polémico, idealmente con TEXTO en pantalla que se entienda "
    "de qué trata la nota. Si no lo podés determinar, devolvé 0.\n"
    "- segmentos_destacados: SOLO si el video dura MÁS de 60 segundos. Elegí POCOS tramos "
    "(1 a 3) {inicio, fin} en segundos con las mejores partes para entender la noticia. "
    "REGLAS para que el corte quede BIEN HECHO: cada tramo debe EMPEZAR y TERMINAR en puntos "
    "naturales (una pausa, el final de una frase o de una idea, un cambio de plano), NUNCA a "
    "mitad de una palabra, frase o acción; cada tramo de al menos 8 segundos; en orden "
    "cronológico y sin solaparse; juntos deben sumar entre 45 y 60 segundos y dejar clara la "
    "noticia de principio a fin. Si el video dura 60s o menos, devolvé una lista vacía [].\n"
    "CRITERIO EDITORIAL (respetalo siempre):\n"
    "• Usá comillas SOLO para frases claras y confiables del material. Si una frase del "
    "audio/subtítulo suena dudosa o mal transcripta, PARAFRASEALA en vez de citarla.\n"
    "• Confirmá nombres propios, cargos e instituciones con el contexto. Si no podés "
    "confirmarlos, evitá afirmarlos con seguridad (o no los pongas).\n"
    "• Convertí las fechas relativas («ayer», «el martes») en fechas o referencias concretas "
    "cuando se pueda deducir del material.\n"
    "• No exageres ni endurezcas las opiniones del entrevistado: mantené el tono y el sentido "
    "originales. Sacá muletillas solo si no alteran el sentido.\n"
    "• No agregues firma, autor ni línea tipo «Por Radio del Centro».\n"
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "hay_noticia": {"type": "boolean"},
        "volanta": {"type": "string"},
        "titulo": {"type": "string"},
        "texto": {"type": "string"},
        "resumen": {"type": "string"},
        "mejor_momento_seg": {"type": "number"},
        "segmentos_destacados": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"inicio": {"type": "number"}, "fin": {"type": "number"}},
                "required": ["inicio", "fin"],
            },
        },
    },
    "required": ["hay_noticia", "volanta", "titulo", "texto", "resumen", "mejor_momento_seg",
                 "segmentos_destacados"],
}


SEO_PROMPT = (
    "Sos el editor de «Radio del Centro» / «Diario La Campaña» de Chivilcoy (Argentina), "
    "un medio LOCAL de noticias con canal de YouTube. Te paso el TÍTULO y la DESCRIPCIÓN "
    "actuales de un video YA PUBLICADO. Reescribilos para que el algoritmo de YouTube los "
    "muestre más y para que la gente haga clic, SIN inventar datos ni cambiar el tema del video.\n"
    "Devolvé EXACTAMENTE estos campos:\n"
    "- titulo: título atractivo y claro en español rioplatense, MÁXIMO 70 caracteres, con la "
    "palabra clave principal al principio y mención local (Chivilcoy/la región) si corresponde. "
    "Sin clickbait engañoso, sin MAYÚSCULAS sostenidas, sin punto final.\n"
    "- bajada: una BAJADA corta y LLAMATIVA para la miniatura (1 sola frase, máximo 60 caracteres), "
    "que genere intriga o debate, picante pero SIN difamar, sin inventar y fiel al tema del video. "
    "Puede ser una pregunta fuerte o una afirmación que invite a hacer clic. Sin hashtags, sin punto "
    "final. Distinta del título (no lo repitas).\n"
    "- descripcion: 2 a 4 frases con las palabras clave naturales (qué se ve y por qué importa), "
    "más una llamada a la acción a la web www.diariolacampaña.com.ar y a suscribirse al canal. "
    "Terminá con una línea de 3 a 6 hashtags relevantes (incluí #Chivilcoy).\n"
    "- tags: lista de 8 a 12 etiquetas (palabras o frases cortas) para el campo Tags de YouTube, "
    "en minúsculas, mezclando términos locales (chivilcoy, radio del centro) y temáticos del video.\n"
)

_SEO_SCHEMA = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string"},
        "bajada": {"type": "string"},
        "descripcion": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["titulo", "bajada", "descripcion", "tags"],
}


def seo_youtube(titulo_actual: str, descripcion_actual: str) -> dict:
    """Reescribe (texto puro, sin bajar el video) el título y la descripción de un
    video de YouTube para SEO/algoritmo. Devuelve {titulo, descripcion, tags}."""
    key = get("GEMINI_API_KEY")
    if not key:
        raise ValueError("Falta GEMINI_API_KEY en .env (clave gratis de Google AI Studio).")
    model = get("GEMINI_MODEL") or "gemini-2.5-flash"
    prompt = (SEO_PROMPT + "\nTÍTULO ACTUAL:\n" + (titulo_actual or "(vacío)") +
              "\n\nDESCRIPCIÓN ACTUAL:\n" + (descripcion_actual or "(vacía)"))
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.5,
            "response_mime_type": "application/json",
            "response_schema": _SEO_SCHEMA,
        },
    }
    logger.info(f"Gemini SEO YouTube con {model} para «{(titulo_actual or '')[:50]}»…")
    url = f"{API_BASE}/models/{model}:generateContent?key={key}"
    r = None
    for intento in range(4):  # reintenta ante 503/429 (modelo gratis sobrecargado)
        r = requests.post(url, json=payload, timeout=120)
        if r.status_code in (429, 500, 503) and intento < 3:
            espera = 5 * (intento + 1)
            logger.warning(f"Gemini {r.status_code} (sobrecargado); reintento en {espera}s…")
            time.sleep(espera)
            continue
        break
    if r.status_code >= 400:
        raise RuntimeError(f"Gemini {r.status_code}: {r.text[:300]}")
    try:
        cand = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        raw = json.loads(cand)
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Respuesta de Gemini ininteligible: {e}")
    titulo = str(raw.get("titulo", "")).strip()[:100]  # YouTube tope duro 100 chars
    bajada = str(raw.get("bajada", "")).strip().rstrip(".")
    descripcion = str(raw.get("descripcion", "")).strip()
    tags = [str(t).strip() for t in (raw.get("tags") or []) if str(t).strip()][:15]
    return {"titulo": titulo, "bajada": bajada, "descripcion": descripcion, "tags": tags}


def _mime(path: Path) -> str:
    return _MIME.get(path.suffix.lower(), "application/octet-stream")


def _subir_archivo(path: Path, mime: str, key: str) -> dict:
    """Sube un archivo grande (video) a la Files API de Gemini (subida reanudable).
    Devuelve el recurso file {name, uri, state, ...}."""
    n = path.stat().st_size
    start = requests.post(
        f"{UPLOAD_URL}?key={key}",
        headers={
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(n),
            "X-Goog-Upload-Header-Content-Type": mime,
            "Content-Type": "application/json",
        },
        json={"file": {"display_name": path.name}},
        timeout=60,
    )
    start.raise_for_status()
    upload_url = start.headers.get("X-Goog-Upload-URL")
    if not upload_url:
        raise RuntimeError("Gemini Files: no devolvió URL de subida")
    up = requests.post(
        upload_url,
        headers={"Content-Length": str(n), "X-Goog-Upload-Offset": "0",
                 "X-Goog-Upload-Command": "upload, finalize"},
        data=path.read_bytes(),
        timeout=600,
    )
    up.raise_for_status()
    return up.json()["file"]


def _esperar_activo(file_name: str, key: str, timeout: int = 300) -> dict:
    """Espera a que Gemini termine de procesar el video (state ACTIVE)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = requests.get(f"{API_BASE}/{file_name}?key={key}", timeout=30)
        r.raise_for_status()
        data = r.json()
        st = data.get("state")
        if st == "ACTIVE":
            return data
        if st == "FAILED":
            raise RuntimeError("Gemini Files: el procesamiento del video FALLÓ")
        time.sleep(3)
    raise RuntimeError("Gemini Files: timeout esperando que el video quede ACTIVE")


def _img_part(path: Path) -> dict:
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"inline_data": {"mime_type": _mime(path), "data": b64}}


def _post_generate(parts: list, key: str, model: str, temperature: float = 0.4) -> dict:
    """Llama a Gemini generateContent con esos `parts` y el schema de nota, reintentando
    ante 429/500/503 (modelo gratis sobrecargado). Devuelve el JSON crudo (dict)."""
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": temperature,
            "response_mime_type": "application/json",
            "response_schema": _SCHEMA,
        },
    }
    url = f"{API_BASE}/models/{model}:generateContent?key={key}"
    r = None
    for intento in range(4):
        r = requests.post(url, json=payload, timeout=300)
        if r.status_code in (429, 500, 503) and intento < 3:
            espera = 5 * (intento + 1)
            logger.warning(f"Gemini {r.status_code} (sobrecargado); reintento en {espera}s…")
            time.sleep(espera)
            continue
        break
    if r.status_code >= 400:
        raise RuntimeError(f"Gemini {r.status_code}: {r.text[:300]}")
    try:
        cand = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(cand)
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Respuesta de Gemini ininteligible: {e}")


def _parse_nota(raw: dict) -> dict:
    """Normaliza el JSON crudo de Gemini al dict de nota que usa el sistema."""
    try:
        momento = float(raw.get("mejor_momento_seg") or 0)
    except (TypeError, ValueError):
        momento = 0.0
    segmentos = []
    for s in (raw.get("segmentos_destacados") or []):
        try:
            ini, fin = float(s.get("inicio")), float(s.get("fin"))
            if fin > ini >= 0:
                segmentos.append({"inicio": ini, "fin": fin})
        except (TypeError, ValueError, AttributeError):
            continue
    nota = {
        "hay_noticia": bool(raw.get("hay_noticia")),
        "volanta": str(raw.get("volanta", "")).strip(),
        "titulo": str(raw.get("titulo", "")).strip(),
        "texto": str(raw.get("texto", "")).strip(),
        "resumen": str(raw.get("resumen", "")).strip(),
        "mejor_momento_seg": max(0.0, momento),
        "segmentos": segmentos,
    }
    if nota["hay_noticia"] and not nota["titulo"] and not nota["texto"]:
        nota["hay_noticia"] = False
    return nota


def transcribe_youtube_url(url: str, extra_text: str = "") -> dict:
    """Desgraba un video de YouTube PÚBLICO pasándole la URL DIRECTA a Gemini (sin bajar
    nada): Gemini ingiere el video desde YouTube y devuelve la misma nota
    {hay_noticia, volanta, titulo, texto, resumen, mejor_momento_seg}. Gratis."""
    key = get("GEMINI_API_KEY")
    if not key:
        raise ValueError("Falta GEMINI_API_KEY en .env (clave gratis de Google AI Studio).")
    model = get("GEMINI_MODEL") or "gemini-2.5-flash"
    prompt = PROMPT_BASE
    if (extra_text or "").strip():
        prompt += ("\nDATOS/CONTEXTO adicional (tenelo MUY en cuenta para la nota):\n"
                   + extra_text.strip())
    parts = [{"text": prompt}, {"file_data": {"file_uri": url}}]
    logger.info(f"Gemini: desgrabando YouTube {url} con {model} (sin descargar)…")
    raw = _post_generate(parts, key, model, temperature=0.4)
    nota = _parse_nota(raw)
    logger.info(f"Gemini OK (YouTube): hay_noticia={nota['hay_noticia']} | "
                f"«{nota['volanta']} — {nota['titulo']}»")
    return nota


def transcribe_to_nota(media_path, extra_text: str = "", image_paths=None) -> dict:
    """Desgraba un VIDEO (o audio) + contexto opcional y devuelve la nota.

    extra_text: texto que aportó el colaborador (archivo de la carpeta).
    image_paths: fotos anexadas (contexto). Devuelve dict con hay_noticia (bool),
    volanta, titulo, texto, resumen y mejor_momento_seg (float, segundos).
    """
    media_path = Path(media_path)
    key = get("GEMINI_API_KEY")
    if not key:
        raise ValueError("Falta GEMINI_API_KEY en .env (clave gratis de Google AI Studio).")
    model = get("GEMINI_MODEL") or "gemini-2.5-flash"
    mime = _mime(media_path)

    prompt = PROMPT_BASE
    if (extra_text or "").strip():
        prompt += ("\nDATOS/CONTEXTO que aportó el redactor (tenelo MUY en cuenta para la nota):\n"
                   + extra_text.strip())

    parts = [{"text": prompt}]
    file_name = None
    if media_path.suffix.lower() in _VIDEO_EXT:
        logger.info(f"Gemini: subiendo video {media_path.name} ({media_path.stat().st_size//1024} KB) a la Files API…")
        info = _subir_archivo(media_path, mime, key)
        info = _esperar_activo(info["name"], key)
        file_name = info["name"]
        parts.append({"file_data": {"mime_type": mime, "file_uri": info["uri"]}})
    else:  # audio u otro: inline base64
        b64 = base64.b64encode(media_path.read_bytes()).decode("ascii")
        parts.append({"inline_data": {"mime_type": mime, "data": b64}})

    for img in (image_paths or [])[:3]:
        try:
            parts.append(_img_part(Path(img)))
        except Exception as e:
            logger.warning(f"No se pudo adjuntar la foto de contexto {img}: {e}")

    logger.info(f"Gemini: desgrabando con {model} (contexto: {len(extra_text or '')} chars, "
                f"{len(image_paths or [])} foto(s))…")
    try:
        raw = _post_generate(parts, key, model, temperature=0.4)
    finally:
        if file_name:  # borrar el archivo subido (best-effort)
            try:
                requests.delete(f"{API_BASE}/{file_name}?key={key}", timeout=30)
            except Exception:
                pass

    nota = _parse_nota(raw)
    logger.info(f"Gemini OK: hay_noticia={nota['hay_noticia']} | «{nota['volanta']} — {nota['titulo']}» "
                f"| mejor_seg={nota['mejor_momento_seg']:.0f}")
    return nota
