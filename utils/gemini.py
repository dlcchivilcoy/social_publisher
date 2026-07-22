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


def _gemini_keys(primary: str = "") -> list:
    """Claves Gemini a usar, en orden, para ROTAR ante 429 (cuota agotada de UNA clave):
    la clave primaria (si se pasa) + las que estén cargadas en el .env. Así, si una clave
    se queda sin cuota, se sigue con la siguiente. Deduplicadas, sin vacías.
    Para sumar más margen: cargar GEMINI_API_KEY_2 / _3 / _4 en el .env. [ver _fallback_models]"""
    nombres = ["GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3",
               "GEMINI_API_KEY_4", "GEMINI_API_KEY_YT"]
    cand = [primary] + [get(n) or "" for n in nombres]
    out, visto = [], set()
    for k in cand:
        k = (k or "").strip()
        if k and k not in visto:
            visto.add(k)
            out.append(k)
    return out


def _fallback_models(primary: str = "") -> list:
    """Modelos a probar, EN ORDEN, para caer a un modelo alternativo ante 429 cuando ya se
    agotaron las claves con el modelo bueno. Cada modelo del plan gratis tiene su PROPIO cupo
    (RPM/TPM/RPD), así que un 2º modelo suma capacidad. Default de respaldo:
    gemini-flash-lite-latest (alias que se auto-actualiza al flash-lite disponible; gratis,
    multimodal, aguanta video —verificado 2026-07-10). Configurable/desactivable con
    GEMINI_MODEL_FALLBACK (vacío = sin respaldo; coma para varios). ⚠️ NO usar gemini-2.0-flash
    (sin cupo gratis) ni el pineado gemini-2.5-flash-lite (404 'no longer available for new users')."""
    raw = get("GEMINI_MODEL_FALLBACK", "gemini-flash-lite-latest")
    fb = [m.strip() for m in raw.split(",") if m.strip() and m.strip() != primary]
    return ([primary] if primary else []) + fb


def _generate(model: str, payload: dict, key: str = "", timeout: int = 120):
    """POST a generateContent con reintentos + ROTACIÓN de CLAVES y de MODELO ante 429.
    Prioridad: agota las claves con el modelo bueno (calidad) y recién ahí cae al modelo de
    respaldo (que tiene cupo aparte). Ante 500/503 (servidor saturado) espera (15→60s) y
    reintenta. Devuelve la respuesta OK; lanza si termina en error."""
    keys = _gemini_keys(key) or [key]
    modelos = _fallback_models(model) or [model]
    combos = [(m, k) for m in modelos for k in keys]  # (modelo, clave): modelo bueno primero
    ci, r = 0, None
    intentos = max(7, len(combos) + 3)
    for intento in range(intentos):
        m, k = combos[ci]
        r = requests.post(f"{API_BASE}/models/{m}:generateContent?key={k}",
                          json=payload, timeout=timeout)
        if r.status_code == 429 and ci < len(combos) - 1:
            ci += 1  # 429 → probar la siguiente combinación (otra clave, o el modelo de respaldo)
            nuevo_m = combos[ci][0]
            if nuevo_m != m:
                logger.warning(f"Gemini 429; cambio al modelo de respaldo «{nuevo_m}»…")
            else:
                logger.warning(f"Gemini 429 (cuota de una clave); roto de clave (combo {ci + 1}/{len(combos)})…")
            continue
        if r.status_code in (429, 500, 503) and intento < intentos - 1:
            espera = min(60, 15 * (intento + 1))
            logger.warning(f"Gemini {r.status_code} (sobrecargado); reintento en {espera}s…")
            time.sleep(espera)
            continue
        break
    if r is None or r.status_code >= 400:
        raise RuntimeError(f"Gemini {r.status_code if r is not None else '???'}: "
                           f"{r.text[:300] if r is not None else ''}")
    return r

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
    "- zocalo: el texto del ZÓCALO del reel (la placa de abajo, como en la tele). MÁXIMO 5 "
    "PALABRAS, sin punto final, sin comillas. Si es una entrevista o una declaración, el "
    "NOMBRE Y APELLIDO de quien habla (y su cargo solo si entra en las 5 palabras, ej. "
    "«Juan Pérez, intendente»). Si es un hecho (accidente, robo, incendio, temporal, corte "
    "de calle, acto), de qué se trata en pocas palabras (ej. «Choque en Ruta 30», «Robo en "
    "un comercio»). Vacío si hay_noticia es false.\n"
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
        "zocalo": {"type": "string"},
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
    "required": ["hay_noticia", "volanta", "titulo", "texto", "resumen", "zocalo",
                 "mejor_momento_seg", "segmentos_destacados"],
}


SEO_PROMPT = (
    "Sos el editor de «Radio del Centro» / «Diario La Campaña» de Chivilcoy (Argentina), "
    "un medio LOCAL de noticias con canal de YouTube. Te paso el TÍTULO y la DESCRIPCIÓN "
    "actuales de un video YA PUBLICADO. Reescribilos para que el algoritmo de YouTube los "
    "muestre más y para que la gente haga clic, SIN inventar datos ni cambiar el tema del video.\n"
    "Devolvé EXACTAMENTE estos campos:\n"
    "- titulo: título atractivo y claro en español rioplatense, MÁXIMO 70 caracteres, con la "
    "palabra clave principal al principio.\n"
    "  SOBRE LA LOCALIDAD (MUY IMPORTANTE): NO agregues «Chivilcoy» —ni ninguna ciudad— por "
    "defecto ni por costumbre. Poné una localidad en el título SOLO si el CONTENIDO de la nota la "
    "justifica de forma clara: el hecho ocurre en ese lugar, o la persona entrevistada es de / "
    "trabaja en / representa a ese lugar. Si el entrevistado o el tema NO es de Chivilcoy (por "
    "ejemplo, alguien de otra ciudad o de la región, o un tema provincial/nacional), NO escribas "
    "Chivilcoy: usá la localidad correcta SOLO si aparece explícita en el contenido. NUNCA inventes, "
    "supongas ni asignes una localidad al azar; ante la MÍNIMA duda, dejá el título SIN ciudad. "
    "Un título sin ciudad es preferible a uno con la ciudad equivocada.\n"
    "  Sin clickbait engañoso, sin MAYÚSCULAS sostenidas, sin punto final.\n"
    "- bajada: una BAJADA corta y LLAMATIVA para la miniatura (1 sola frase, máximo 60 caracteres), "
    "que genere intriga o debate, picante pero SIN difamar, sin inventar y fiel al tema del video. "
    "Puede ser una pregunta fuerte o una afirmación que invite a hacer clic. Sin hashtags, sin punto "
    "final. Distinta del título (no lo repitas).\n"
    "- descripcion: 2 a 4 frases con las palabras clave naturales (qué se ve y por qué importa), "
    "más una llamada a la acción a la web www.diariolacampaña.com.ar y a suscribirse al canal. "
    "FORMATO (importante para que se lea fácil): escribí CADA oración o idea como un PÁRRAFO "
    "APARTE, separados por un RENGLÓN EN BLANCO (o sea, punto y aparte con una línea vacía en "
    "medio). NO entregues todo junto en un solo bloque de texto. "
    "Terminá con una línea de hashtags relevantes: MÁXIMO 5 hashtags (entre 3 y 5, nunca más de 5). "
    "Incluí #Chivilcoy SOLO si la nota es de/sobre Chivilcoy; misma regla de localidad que el título.\n"
    "- tags: lista de 8 a 12 etiquetas (palabras o frases cortas) para el campo Tags de YouTube, "
    "en minúsculas, con términos temáticos del video y «radio del centro»; sumá «chivilcoy» u otra "
    "localidad SOLO si corresponde al contenido (misma regla que el título, no la pongas por defecto).\n"
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


def _formatear_descripcion(desc: str, max_hashtags: int = 5) -> str:
    """Deja la descripción de YouTube LEGIBLE y consistente, pase lo que pase con Gemini:

    - cada oración queda como un PÁRRAFO aparte, separados por un renglón en blanco
      (punto y aparte con línea vacía en medio);
    - los hashtags se juntan en UNA sola línea al final, sin repetidos y con tope
      (`max_hashtags`). Se sacan del cuerpo aunque vengan pegados al último punto
      (Gemini a veces devuelve «...com.ar.#Chivilcoy #Deportes»).
    """
    import re as _re
    texto = (desc or "").strip()
    if not texto:
        return ""
    # 1) Separar los hashtags del cuerpo.
    hashtags = _re.findall(r"#[^\s#]+", texto)
    cuerpo = _re.sub(r"#[^\s#]+", " ", texto)
    # 2) Normalizar espacios/saltos para partir parejo.
    cuerpo = _re.sub(r"\s+", " ", cuerpo).strip()
    # 3) Una oración por párrafo (corta después de . ! ? …).
    oraciones = [o.strip() for o in _re.split(r"(?<=[.!?…])\s+", cuerpo) if o.strip()]
    out = "\n\n".join(oraciones)
    # 4) Línea final de hashtags: sin duplicados y con tope.
    vistos, limpios = set(), []
    for h in hashtags:
        k = h.lower()
        if k in vistos:
            continue
        vistos.add(k)
        limpios.append(h)
        if len(limpios) >= max_hashtags:
            break
    if limpios:
        out += "\n\n" + " ".join(limpios)
    return out.strip()


def seo_youtube(titulo_actual: str, descripcion_actual: str, youtube_url: str = "") -> dict:
    """Reescribe el título/descripción/tags de un video de YouTube para SEO/algoritmo.
    Si se pasa `youtube_url`, Gemini MIRA el video (primeros minutos) y se basa en lo que
    REALMENTE se dice — clave para no equivocar el tema cuando el título original es vago o
    ambiguo (ej.: «Cerámica» es un CLUB de fútbol, no la industria del cerámico; sin ver el
    video la IA lo tomaba como economía). Devuelve {titulo, bajada, descripcion, tags}."""
    key = get("GEMINI_API_KEY")
    if not key:
        raise ValueError("Falta GEMINI_API_KEY en .env (clave gratis de Google AI Studio).")
    model = get("GEMINI_MODEL") or "gemini-2.5-flash"
    prompt = SEO_PROMPT
    if youtube_url:
        prompt += ("\nMIRÁ EL VIDEO ADJUNTO y basate en lo que REALMENTE se dice ahí (personas, "
                   "tema, lugar). El título y la descripción de abajo son solo una REFERENCIA y "
                   "pueden estar equivocados, incompletos o ser AMBIGUOS (por ejemplo, el nombre de "
                   "un club, comercio o persona que parece otra cosa): si el video contradice ese "
                   "texto, corregí y usá lo del video. NO inventes nada que no esté en el video.\n")
    prompt += ("\nTÍTULO ACTUAL:\n" + (titulo_actual or "(vacío)") +
               "\n\nDESCRIPCIÓN ACTUAL:\n" + (descripcion_actual or "(vacía)"))
    parts = [{"text": prompt}]
    if youtube_url:
        # Primeros 5 min en baja resolución: alcanza para identificar tema/personas sin
        # gastar la cuota de un video largo entero.
        parts.append({"file_data": {"file_uri": youtube_url},
                      "video_metadata": {"end_offset": "300s"}})
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0.4,
            "response_mime_type": "application/json",
            "response_schema": _SEO_SCHEMA,
        },
    }
    con_video = "MIRANDO el video" if youtube_url else "solo texto"
    logger.info(f"Gemini SEO YouTube con {model} ({con_video}) para «{(titulo_actual or '')[:50]}»…")
    r = _generate(model, payload, key, timeout=180)
    try:
        cand = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        raw = json.loads(cand)
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Respuesta de Gemini ininteligible: {e}")
    titulo = str(raw.get("titulo", "")).strip()[:100]  # YouTube tope duro 100 chars
    bajada = str(raw.get("bajada", "")).strip().rstrip(".")
    # Una oración por párrafo (renglón en blanco en medio) + máximo 5 hashtags al final.
    descripcion = _formatear_descripcion(str(raw.get("descripcion", "")), max_hashtags=5)
    tags = [str(t).strip() for t in (raw.get("tags") or []) if str(t).strip()][:15]
    # Invitación fija al canal (por si Gemini no la incluyó).
    from utils.branding import canal_yt_url, linea_canal_yt
    if canal_yt_url().lower() not in descripcion.lower():
        descripcion = (descripcion + "\n\n" + linea_canal_yt()).strip()
    return {"titulo": titulo, "bajada": bajada, "descripcion": descripcion, "tags": tags}


GANCHO_PROMPT = (
    "Actuás como DIRECTOR CREATIVO de crecimiento orgánico de YouTube (NO como redacción "
    "periodística). Tu única misión: definir el GANCHO de la MINIATURA para MAXIMIZAR el CTR.\n"
    "Análisis previo OBLIGATORIO: detectá (a) la EMOCIÓN dominante, (b) la DECLARACIÓN o dato MÁS "
    "FUERTE, (c) la frase con mayor potencial VIRAL y de CURIOSIDAD. Elegí la que genere MÁS clics.\n"
    "En entrevistas: el ENTREVISTADO y su DECLARACIÓN son protagonistas. La miniatura JAMÁS debe "
    "transmitir 'dos personas conversando'; debe transmitir 'esta persona acaba de revelar algo "
    "importante'.\n"
    "Prioridad ante conflicto: CTR > curiosidad > retención > SEO. Español rioplatense. PROHIBIDO el "
    "clickbait mentiroso o difamatorio: el gancho debe ser fiel a lo que realmente se dice.\n"
    "Control de calidad: '¿yo haría clic en esto sin conocer a nadie de la imagen?'. Si no, rehacelo.\n"
    "Devolvé EXACTAMENTE:\n"
    "- gancho: texto CORTO para la miniatura (MÁXIMO 42 caracteres) que combine GANCHO de curiosidad "
    "CON SEO: incluí la palabra clave principal o el nombre del protagonista/tema, y sumale intriga. "
    "Formato ideal 'CLAVE: hook' (ej. 'Vaccarezza: ¿vocación o interés?'). Sin punto final, sin comillas.\n"
    "- keyword: UNA sola palabra del gancho (la más fuerte) para resaltar; tiene que estar TAL CUAL "
    "dentro del gancho.\n"
    "- emocion: 1 palabra con la emoción dominante.\n"
)

_GANCHO_SCHEMA = {
    "type": "object",
    "properties": {
        "gancho": {"type": "string"},
        "keyword": {"type": "string"},
        "emocion": {"type": "string"},
    },
    "required": ["gancho", "keyword", "emocion"],
}


def gancho_miniatura(youtube_url: str, titulo: str, descripcion: str, usar_video: bool = True) -> dict:
    """Genera el GANCHO de la miniatura (CTR-first). Si usar_video, Gemini analiza el
    video de YouTube directo (primeros minutos, para acotar la cuota) y saca la frase/
    emoción más fuerte; si no, trabaja con título+descripción. Devuelve {gancho, keyword,
    emocion}."""
    key = get("GEMINI_API_KEY")
    if not key:
        raise ValueError("Falta GEMINI_API_KEY en .env.")
    model = get("GEMINI_MODEL") or "gemini-2.5-flash"
    instruc = (GANCHO_PROMPT + "\nTÍTULO: " + (titulo or "") +
               "\nDESCRIPCIÓN: " + (descripcion or "")[:600])
    parts = [{"text": instruc}]
    if usar_video and youtube_url:
        # primeros 4 min, baja resolución → ~70k tokens en vez de ~400k
        parts.append({"file_data": {"file_uri": youtube_url},
                      "video_metadata": {"end_offset": "240s"}})
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0.75,
            "response_mime_type": "application/json",
            "response_schema": _GANCHO_SCHEMA,
        },
    }
    logger.info(f"Gemini gancho miniatura (video={usar_video and bool(youtube_url)})…")
    r = _generate(model, payload, key, timeout=300)
    try:
        cand = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        raw = json.loads(cand)
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Respuesta de Gemini ininteligible: {e}")
    gancho = str(raw.get("gancho", "")).strip().strip('"').rstrip(".")[:42]
    keyword = str(raw.get("keyword", "")).strip().strip('"')
    emocion = str(raw.get("emocion", "")).strip()
    return {"gancho": gancho, "keyword": keyword, "emocion": emocion}


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


def _post_generate(parts: list, key: str, model: str, temperature: float = 0.3) -> dict:
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
    r = _generate(model, payload, key, timeout=300)
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
    # Zócalo del reel: si Gemini no lo devuelve, la volanta ya es un antetítulo de 2 a 5
    # palabras, así que sirve de reemplazo natural.
    nota["zocalo"] = str(raw.get("zocalo", "")).strip() or nota["volanta"]
    if nota["hay_noticia"] and not nota["titulo"] and not nota["texto"]:
        nota["hay_noticia"] = False
    return nota


def transcribe_youtube_url(url: str, extra_text: str = "", instrucciones: str = "",
                           api_key: str = "") -> dict:
    """Desgraba un video de YouTube PÚBLICO pasándole la URL DIRECTA a Gemini (sin bajar
    nada): Gemini ingiere el video desde YouTube y devuelve la misma nota
    {hay_noticia, volanta, titulo, texto, resumen, mejor_momento_seg}. Gratis.

    `instrucciones`: directiva extra de redacción (ej. pedir un cuerpo más largo).
    `api_key`: clave Gemini a usar; si viene vacía cae a GEMINI_API_KEY del .env. Sirve
    para que el desgrabador de YouTube use una clave DEDICADA (su propia cuota gratis)."""
    key = (api_key or "").strip() or get("GEMINI_API_KEY")
    if not key:
        raise ValueError("Falta GEMINI_API_KEY en .env (clave gratis de Google AI Studio).")
    model = get("GEMINI_MODEL") or "gemini-2.5-flash"
    prompt = PROMPT_BASE
    if (instrucciones or "").strip():
        prompt += ("\nINSTRUCCIÓN ADICIONAL DE REDACCIÓN (respetala):\n" + instrucciones.strip())
    if (extra_text or "").strip():
        prompt += ("\nDATOS/CONTEXTO adicional (tenelo MUY en cuenta para la nota):\n"
                   + extra_text.strip())
    parts = [{"text": prompt}, {"file_data": {"file_uri": url}}]
    logger.info(f"Gemini: desgrabando YouTube {url} con {model} (sin descargar)…")
    # Temperatura baja: prioriza fidelidad (nombres bien escritos) y evita repeticiones.
    raw = _post_generate(parts, key, model, temperature=0.3)
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
