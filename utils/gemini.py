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
               "GEMINI_API_KEY_4", "GEMINI_API_KEY_YT",
               # Claves de radiodelcentro (desgrabador de la radio). Se suman al pool
               # compartido como respaldo; el flujo radio las pasa como `primary`, así que
               # las usa PRIMERO (ver transcribe_to_nota(api_key=...)).
               "GEMINI_API_KEY_RADIO", "GEMINI_API_KEY_RADIO_2", "GEMINI_API_KEY_RADIO_3"]
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


def _generate(model: str, payload: dict, key: str = "", timeout: int = 120, key_pool=None):
    """POST a generateContent con reintentos + ROTACIÓN de CLAVES y de MODELO ante 429.
    Prioridad: agota las claves con el modelo bueno (calidad) y recién ahí cae al modelo de
    respaldo (que tiene cupo aparte). Ante 500/503 (servidor saturado) espera (15→60s) y
    reintenta. Devuelve la respuesta OK; lanza si termina en error.

    `key_pool`: si se pasa una lista, se usan EXACTAMENTE esas claves (sin sumar el pool del .env).
    El desgrabador de la radio lo usa con UNA sola clave (`[k]`) para que la rotación entre
    PROYECTOS la maneje afuera: los archivos de la Files API son por proyecto, así que no se puede
    subir el video con una clave y desgrabarlo con otra (403). Ver `transcriber_radio`."""
    keys = list(key_pool) if key_pool else (_gemini_keys(key) or [key])
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


def _quitar_chivilcoy_titulo(titulo: str) -> str:
    """Saca «Chivilcoy» de un título y limpia el conector/puntuación que queda colgando.
    Se usa cuando la localidad NO está justificada por el material: la IA tiende a meter
    «Chivilcoy» por costumbre aunque el tema o el entrevistado sean de otro lugar. Cubre las
    formas típicas: «Chivilcoy: …» / «Chivilcoy - …» al inicio, «… en/de Chivilcoy» y
    «… - / , / | Chivilcoy» como cola, y cualquier «Chivilcoy» suelto restante."""
    import re as _re
    t = titulo or ""
    t = _re.sub(r"^\s*chivilcoy\s*[:\-–—|,]+\s*", "", t, flags=_re.IGNORECASE)          # prefijo
    t = _re.sub(r"\s+(?:en|de|del|desde|para|hacia)\s+chivilcoy\b", "", t, flags=_re.IGNORECASE)
    t = _re.sub(r"\s*[\-–—|,]\s*chivilcoy\b", "", t, flags=_re.IGNORECASE)               # cola con separador
    t = _re.sub(r"\bchivilcoy\b", "", t, flags=_re.IGNORECASE)                           # suelto
    t = _re.sub(r"\s+([:;,.])", r"\1", t)   # espacio colgado antes de puntuación
    t = _re.sub(r"\s{2,}", " ", t)
    return t.strip(" \t:–—-|,·").strip()


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
    # NO forzar «Chivilcoy» (pedido del usuario 2026-07-23): la IA tiende a meter la ciudad en
    # CADA título aunque el entrevistado o el tema sean de otro lado. Si «Chivilcoy» NO aparece
    # en el material de referencia (título/descripción originales), la sacamos del título, de los
    # hashtags y de los tags. Regla del usuario: mejor un título SIN ciudad que con la equivocada.
    fuente = f"{titulo_actual or ''} {descripcion_actual or ''}".lower()
    if "chivilcoy" not in fuente:
        if "chivilcoy" in titulo.lower():
            limpio = _quitar_chivilcoy_titulo(titulo)
            if limpio:
                logger.info(f"SEO: saqué «Chivilcoy» del título (no está en el material) → «{limpio}»")
                titulo = limpio
        import re as _re
        descripcion = _re.sub(r"\s*#chivilcoy\b", "", descripcion, flags=_re.IGNORECASE).strip()
        tags = [t for t in tags if "chivilcoy" not in t.lower()]
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


def _post_json(parts: list, key: str, model: str, schema: dict, temperature: float = 0.3,
               key_pool=None) -> dict:
    """Llama a Gemini generateContent con esos `parts` pidiendo JSON con `schema`, reintentando
    ante 429/500/503 (modelo gratis sobrecargado). Devuelve el JSON crudo (dict)."""
    payload = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": temperature,
            "response_mime_type": "application/json",
            "response_schema": schema,
        },
    }
    r = _generate(model, payload, key, timeout=300, key_pool=key_pool)
    try:
        cand = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(cand)
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Respuesta de Gemini ininteligible: {e}")


def _post_generate(parts: list, key: str, model: str, temperature: float = 0.3, key_pool=None) -> dict:
    """Atajo histórico: pide el JSON de NOTA completo (`_SCHEMA`) en UNA sola pasada.
    Lo usan el camino legacy (tiro único) y `reescribir_a_dos_paginas`."""
    return _post_json(parts, key, model, _SCHEMA, temperature=temperature, key_pool=key_pool)


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


# ── Desgrabación en 3 PASOS (transcribir → redactar anclado → verificar) ──────
# Anti-alucinación: en vez de pedirle a Gemini que "mire el video y escriba la nota" en un
# solo tiro (donde rellena huecos con conocimiento del mundo y exagera), se separa en pasos a
# temperatura 0:
#   1) TRANSCRIBIR: audio/video → texto LITERAL (+ elige portada y segmentos, que necesitan
#      VER el video). No interpreta ni resume.
#   2) REDACTAR: escribe la nota USANDO SOLO el texto de la transcripción. Como trabaja sobre
#      texto (no "escucha"), no confunde nombres y se le puede prohibir agregar lo que no esté.
#   3) VERIFICAR: compara la nota contra la transcripción y saca/suaviza lo que no esté
#      respaldado (contexto inventado, cifras/campeonatos/nombres que nadie dijo).
# Kill-switch: GEMINI_NOTA_MULTIPASO=0 vuelve al tiro único (legacy). Ante un error que NO es de
# cuota, el multipaso cae solo al legacy (nunca queda peor que antes); si es de cuota, deja subir
# el error para que el llamador (radio) rote de proyecto/clave.

_TRANSCRIBIR_PROMPT = (
    "Sos un transcriptor profesional. Te paso un VIDEO (o audio). Tu ÚNICA tarea es TRANSCRIBIR "
    "en español rioplatense (es-AR) EXACTAMENTE lo que se dice y lo que aparece escrito en "
    "pantalla, palabra por palabra.\n"
    "REGLAS ESTRICTAS (respetalas siempre):\n"
    "• NO resumas, NO interpretes, NO corrijas, NO 'mejores' ni completes lo que se dice.\n"
    "• NO agregues NADA que no esté en el audio o la pantalla: ni contexto, ni antecedentes, ni "
    "datos, ni cifras, ni nombres, ni campeonatos, ni fechas, ni lugares.\n"
    "• Respetá los NOMBRES PROPIOS, NÚMEROS y SIGLAS tal como se dicen. Si dudás de cómo se "
    "escribe un nombre, transcribilo como suena; NO lo cambies por otro que 'te suene'.\n"
    "• Si un tramo es inaudible o no se entiende, escribí [inaudible]; NO adivines.\n"
    "• Marcá los cambios de hablante si se distinguen (ej. «Entrevistador:», «Entrevistado:»).\n"
    "Devolvé EXACTAMENTE estos campos:\n"
    "- transcripcion: la transcripción textual COMPLETA (todo lo hablado + texto en pantalla + "
    "subtítulos). Si hay fotos de contexto con texto legible (carteles, placas, nombres), sumalo "
    "al final bajo «CONTEXTO DE LAS FOTOS:», sin inventar.\n"
    "- hay_audio: true si hay habla o texto legible suficiente para armar una nota; false si es "
    "solo música, ruido o imágenes sin información.\n"
    "- mejor_momento_seg: el SEGUNDO del video (número entero) con el cuadro más representativo, "
    "llamativo o polémico, idealmente con TEXTO en pantalla que se entienda de qué trata. Si no lo "
    "podés determinar, devolvé 0.\n"
    "- segmentos_destacados: SOLO si el video dura MÁS de 60 segundos. Elegí POCOS tramos (1 a 3) "
    "{inicio, fin} en segundos con las mejores partes para entender la noticia. Cada tramo debe "
    "EMPEZAR y TERMINAR en puntos naturales (una pausa, el final de una frase o idea, un cambio de "
    "plano), NUNCA a mitad de palabra o frase; de al menos 8 segundos; en orden cronológico y sin "
    "solaparse; juntos deben sumar entre 45 y 60 segundos. Si dura 60s o menos, devolvé [].\n"
)

_TRANSCRIPCION_SCHEMA = {
    "type": "object",
    "properties": {
        "transcripcion": {"type": "string"},
        "hay_audio": {"type": "boolean"},
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
    "required": ["transcripcion", "hay_audio", "mejor_momento_seg", "segmentos_destacados"],
}

_REDACTAR_PROMPT = (
    "Sos el editor del «Diario La Campaña» / «Radio del Centro» de Chivilcoy (Argentina). Te paso "
    "la TRANSCRIPCIÓN TEXTUAL de un video. Con ESO —y SOLO eso— armá UNA noticia en español "
    "rioplatense (es-AR), estilo periodístico, tercera persona.\n"
    "REGLA DE ORO — FIDELIDAD ABSOLUTA A LA TRANSCRIPCIÓN:\n"
    "• Usá ÚNICAMENTE información EXPLÍCITA en la transcripción (y en el contexto que aporte el "
    "redactor). PROHIBIDO agregar contexto histórico, antecedentes, cifras, fechas, nombres, "
    "cargos, lugares, campeonatos, récords o cualquier dato que la transcripción NO diga. Si no "
    "está, NO existe para la nota.\n"
    "• PROHIBIDO exagerar, dramatizar o endurecer lo dicho. Mantené la magnitud y el tono "
    "originales: si alguien dice «jugué algunos partidos», NO escribas «brilló» ni «fue figura».\n"
    "• Copiá los NOMBRES PROPIOS, NÚMEROS y SIGLAS TAL CUAL están en la transcripción.\n"
    "• Ante la duda, poné MENOS: mejor una nota más corta y 100% fiel que una más larga con un "
    "dato inventado.\n"
    "Devolvé EXACTAMENTE estos campos:\n"
    "- hay_noticia: true si la transcripción alcanza para una nota REAL; false si no.\n"
    "- volanta: antetítulo corto (2 a 5 palabras), sin punto final. Vacío si hay_noticia es false.\n"
    "- titulo: titular claro y fiel (máx ~90 caracteres), sin punto final. Puede ser una cita "
    "breve y textual. Vacío si false.\n"
    "- texto: cuerpo en párrafos separados por línea en blanco (\\n\\n). ORDENALO POR TEMAS, no "
    "minuto a minuto. Cerrá recuperando una idea fuerte o un dato del entrevistado. La extensión "
    "la manda el material: si es breve, priorizá FIDELIDAD antes que extensión, sin rellenar ni "
    "repetir. Vacío si false.\n"
    "- resumen: resumen para redes (máx 280 caracteres): quién habla, qué sostiene y por qué "
    "importa. Vacío si false.\n"
    "- zocalo: texto del zócalo del reel, MÁXIMO 5 PALABRAS, sin punto ni comillas: el nombre y "
    "apellido de quien habla (y cargo si entra), o de qué se trata el hecho («Choque en Ruta 30»). "
    "Vacío si hay_noticia es false.\n"
    "CRITERIO EDITORIAL: usá comillas solo para frases claras de la transcripción; si una frase "
    "suena dudosa o cortada, parafraseala. No agregues firma ni autor.\n"
)

_REDACCION_SCHEMA = {
    "type": "object",
    "properties": {
        "hay_noticia": {"type": "boolean"},
        "volanta": {"type": "string"},
        "titulo": {"type": "string"},
        "texto": {"type": "string"},
        "resumen": {"type": "string"},
        "zocalo": {"type": "string"},
    },
    "required": ["hay_noticia", "volanta", "titulo", "texto", "resumen", "zocalo"],
}

_VERIFICAR_PROMPT = (
    "Sos un EDITOR VERIFICADOR estricto de «Diario La Campaña» / «Radio del Centro». Te paso una "
    "TRANSCRIPCIÓN textual y una NOTA redactada a partir de ella. Revisá la nota AFIRMACIÓN POR "
    "AFIRMACIÓN contra la transcripción y devolvé la MISMA nota pero CORREGIDA de modo que CADA "
    "dato quede respaldado por la transcripción:\n"
    "• ELIMINÁ todo lo que la transcripción NO diga: contexto histórico, antecedentes, cifras, "
    "fechas, campeonatos, récords, cargos, lugares o nombres inventados o supuestos.\n"
    "• SUAVIZÁ cualquier exageración o dramatización hasta dejarla igual de fuerte que en la "
    "transcripción (ni más ni menos).\n"
    "• CORREGÍ los nombres propios, números y siglas que no coincidan con la transcripción.\n"
    "• NO inventes datos nuevos para 'tapar' lo que sacaste: si al quitar algo el párrafo queda "
    "corto, dejalo corto. Conservá el estilo, la volanta y el título salvo que tengan un error.\n"
    "• Si tras la limpieza no queda información suficiente para una nota, poné hay_noticia=false.\n"
    "Devolvé EXACTAMENTE: hay_noticia, volanta, titulo, texto, resumen, zocalo (mismos campos y "
    "límites que la nota original) y ADEMÁS:\n"
    "- correcciones: lista breve (puede ser []) de qué sacaste, suavizaste o corregiste y por qué "
    "(ej. «saqué 'Mundial femenino': no se menciona en la transcripción»).\n"
)

_VERIF_SCHEMA = {
    "type": "object",
    "properties": {
        "hay_noticia": {"type": "boolean"},
        "volanta": {"type": "string"},
        "titulo": {"type": "string"},
        "texto": {"type": "string"},
        "resumen": {"type": "string"},
        "zocalo": {"type": "string"},
        "correcciones": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["hay_noticia", "volanta", "titulo", "texto", "resumen", "zocalo", "correcciones"],
}


def _multipaso_on() -> bool:
    """True si la desgrabación en 3 pasos está activa (default). GEMINI_NOTA_MULTIPASO=0 vuelve
    al tiro único legacy."""
    return str(get("GEMINI_NOTA_MULTIPASO", "1")).strip().lower() not in ("0", "no", "false", "off")


def _es_cuota(e: Exception) -> bool:
    """Detecta un error de CUOTA/saturación de Gemini (mismo criterio que transcriber_radio).
    Ante cuota NO caemos a legacy: dejamos subir el error para que el llamador rote de clave."""
    m = str(e).lower()
    return any(s in m for s in ("429", "quota", "resource_exhausted", "exhausted", "rate limit"))


def _nota_multipaso(media_part: dict, img_parts: list, extra_text: str, key: str, model: str,
                    key_pool=None, instrucciones: str = "") -> dict:
    """Desgraba en 3 pasos (transcribir → redactar anclado → verificar), todo a temperatura 0.
    `media_part` es la parte de Gemini con el video/audio (subido o file_uri); `img_parts` son las
    fotos de contexto ya convertidas a partes. Devuelve la nota normalizada por `_parse_nota`."""
    # Paso 1 — transcripción literal + elección de portada/segmentos (mira el video).
    t_prompt = _TRANSCRIBIR_PROMPT
    if (extra_text or "").strip():
        t_prompt += ("\nCONTEXTO (usalo SOLO para escribir bien nombres propios, cargos, lugares y "
                     "siglas; NO para agregar hechos):\n" + extra_text.strip())
    t_raw = _post_json([{"text": t_prompt}, media_part] + list(img_parts), key, model,
                       _TRANSCRIPCION_SCHEMA, temperature=0.0, key_pool=key_pool)
    transcripcion = str(t_raw.get("transcripcion", "")).strip()
    hay_audio = bool(t_raw.get("hay_audio", True))
    try:
        momento = float(t_raw.get("mejor_momento_seg") or 0)
    except (TypeError, ValueError):
        momento = 0.0
    segmentos = t_raw.get("segmentos_destacados") or []
    logger.info(f"  Paso 1/3 (transcribir): {len(transcripcion)} chars · hay_audio={hay_audio} · "
                f"mejor_seg={momento:.0f} · {len(segmentos)} segmento(s)")
    if not hay_audio or len(transcripcion) < 25:
        return _parse_nota({"hay_noticia": False, "mejor_momento_seg": momento,
                            "segmentos_destacados": segmentos})

    # Paso 2 — redacción anclada (SOLO sobre el texto de la transcripción).
    r_prompt = _REDACTAR_PROMPT
    if (instrucciones or "").strip():
        r_prompt += "\nINSTRUCCIÓN ADICIONAL DE REDACCIÓN (respetala):\n" + instrucciones.strip()
    if (extra_text or "").strip():
        r_prompt += "\nDATOS/CONTEXTO que aportó el redactor (podés usarlos como fuente):\n" + extra_text.strip()
    r_prompt += "\n\nTRANSCRIPCIÓN (única fuente de la nota):\n" + transcripcion
    r_raw = _post_json([{"text": r_prompt}], key, model, _REDACCION_SCHEMA, temperature=0.0,
                       key_pool=key_pool)
    logger.info(f"  Paso 2/3 (redactar): hay_noticia={bool(r_raw.get('hay_noticia'))} · "
                f"«{str(r_raw.get('titulo', ''))[:60]}»")

    # Paso 3 — verificación anti-alucinación (best-effort; si falla, queda el paso 2).
    nota_raw = dict(r_raw)
    if bool(r_raw.get("hay_noticia")):
        try:
            borrador = {k: r_raw.get(k, "") for k in ("volanta", "titulo", "texto", "resumen", "zocalo")}
            v_prompt = (_VERIFICAR_PROMPT + "\n\nTRANSCRIPCIÓN:\n" + transcripcion +
                        "\n\nNOTA A VERIFICAR (JSON):\n" + json.dumps(borrador, ensure_ascii=False))
            v_raw = _post_json([{"text": v_prompt}], key, model, _VERIF_SCHEMA, temperature=0.0,
                               key_pool=key_pool)
            correcciones = [str(c).strip() for c in (v_raw.get("correcciones") or []) if str(c).strip()]
            if (v_raw.get("texto") or "").strip():
                nota_raw = dict(v_raw)
            if correcciones:
                logger.info(f"  Paso 3/3 (verificar): {len(correcciones)} corrección(es) → "
                            + " | ".join(correcciones[:6]))
            else:
                logger.info("  Paso 3/3 (verificar): sin observaciones.")
        except Exception as e:  # noqa: BLE001
            if _es_cuota(e):
                raise
            logger.warning(f"  Paso 3/3 (verificar) falló ({e}); uso la nota del paso 2.")

    nota_raw["mejor_momento_seg"] = momento
    nota_raw["segmentos_destacados"] = segmentos
    return _parse_nota(nota_raw)


def _generar_nota(media_part: dict, img_parts: list, extra_text: str, key: str, model: str,
                  key_pool=None, legacy_temp: float = 0.4, instrucciones: str = "") -> dict:
    """Genera la nota a partir de la parte de medio ya construida. Usa el flujo en 3 pasos
    (default) y, ante un error que NO es de cuota, cae al tiro ÚNICO legacy (PROMPT_BASE) para no
    quedar nunca peor que antes. El error de cuota se re-lanza para que el llamador rote de clave."""
    if _multipaso_on():
        try:
            return _nota_multipaso(media_part, img_parts, extra_text, key, model, key_pool, instrucciones)
        except Exception as e:  # noqa: BLE001
            if _es_cuota(e):
                raise
            logger.warning(f"Desgrabación en 3 pasos falló ({e}); caigo al tiro único (legacy).")
    prompt = PROMPT_BASE
    if (instrucciones or "").strip():
        prompt += "\nINSTRUCCIÓN ADICIONAL DE REDACCIÓN (respetala):\n" + instrucciones.strip()
    if (extra_text or "").strip():
        prompt += ("\nDATOS/CONTEXTO que aportó el redactor (tenelo MUY en cuenta para la nota):\n"
                   + extra_text.strip())
    raw = _post_generate([{"text": prompt}, media_part] + list(img_parts), key, model,
                         temperature=legacy_temp, key_pool=key_pool)
    return _parse_nota(raw)


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
    media_part = {"file_data": {"file_uri": url}}
    logger.info(f"Gemini: desgrabando YouTube {url} con {model} (sin descargar)…")
    # Flujo en 3 pasos (transcribir → redactar anclado → verificar), a temperatura 0: prioriza
    # fidelidad (nombres bien escritos) y evita invenciones. Legacy (tiro único) con temp 0.3.
    nota = _generar_nota(media_part, [], extra_text or "", key, model, key_pool=None,
                         legacy_temp=0.3, instrucciones=instrucciones)
    logger.info(f"Gemini OK (YouTube): hay_noticia={nota['hay_noticia']} | "
                f"«{nota['volanta']} — {nota['titulo']}»")
    return nota


_REESCRIBIR_LARGO_BASE = (
    "Sos el editor del «Diario La Campaña» / «Radio del Centro» de Chivilcoy (Argentina). "
    "Te paso una nota YA redactada a partir de un video. Tu ÚNICA tarea es dejar el CUERPO "
    "(campo «texto») con el LARGO justo, SIN cambiar el tema, la volanta ni el título y SIN "
    "inventar datos. Devolvé el mismo JSON de nota, con hay_noticia=true y la MISMA volanta y "
    "título; lo único que reescribís es «texto»."
)


def reescribir_a_dos_paginas(url: str, nota: dict, min_palabras: int, max_palabras: int,
                             objetivo: int, extra_text: str = "", api_key: str = "") -> dict:
    """Segundo pase de LARGO para el desgrabador: si el cuerpo quedó fuera del rango (~2
    páginas de Word), reescribe la nota para dejarla entre `min_palabras` y `max_palabras`.

    - Si quedó CORTA: se vuelve a MIRAR el video (file_uri) para desarrollar con material
      REAL (declaraciones, contexto, datos) sin inventar nada.
    - Si quedó LARGA: se sintetiza a partir del propio texto (sin volver a gastar el video).

    Best-effort: ante cualquier error devuelve la nota tal como estaba. Conserva
    volanta/título; solo cambia «texto»."""
    key = (api_key or "").strip() or get("GEMINI_API_KEY")
    if not key:
        return nota
    model = get("GEMINI_MODEL") or "gemini-2.5-flash"
    palabras = len((nota.get("texto") or "").split())
    corta = palabras < min_palabras
    guia = (
        f"La nota quedó {'DEMASIADO CORTA' if corta else 'DEMASIADO LARGA'} "
        f"({palabras} palabras). Reescribí el cuerpo para que tenga entre {min_palabras} y "
        f"{max_palabras} palabras (objetivo {objetivo}: DOS páginas de Word, ni media ni tres). "
    )
    if corta:
        guia += ("MIRÁ DE NUEVO EL VIDEO adjunto y desarrollá EN SERIO lo que SÍ está en el "
                 "material: más declaraciones y citas textuales, contexto, antecedentes, el "
                 "porqué y el para qué, consecuencias y próximos pasos. NO inventes NADA que no "
                 "esté en el video; no rellenes con vueltas ni repitas la misma idea. ")
    else:
        guia += ("Sintetizá: sacá lo redundante y las vueltas y quedate con lo importante. NO "
                 "agregues nada que no esté ya en la nota ni cambies los datos. ")
    guia += ("Prosa periodística seria, párrafos bien desarrollados, español rioplatense "
             "impecable.\n\n"
             f"VOLANTA: {nota.get('volanta', '')}\nTÍTULO: {nota.get('titulo', '')}\n"
             f"CUERPO ACTUAL ({palabras} palabras):\n{nota.get('texto', '')}")
    prompt = _REESCRIBIR_LARGO_BASE + "\n\n" + guia
    if (extra_text or "").strip():
        prompt += "\n\nCONTEXTO ADICIONAL:\n" + extra_text.strip()
    parts = [{"text": prompt}]
    if corta and url:
        parts.append({"file_data": {"file_uri": url}})  # re-mira el video: desarrolla sin inventar
    try:
        raw = _post_generate(parts, key, model, temperature=0.3)
        nuevo = _parse_nota(raw)
    except Exception as e:
        logger.warning(f"Segundo pase de largo falló ({e}); dejo la nota como estaba.")
        return nota
    txt = (nuevo.get("texto") or "").strip()
    if not txt:
        return nota
    ajustada = dict(nota)
    ajustada["texto"] = txt
    logger.info(f"    Segundo pase de largo: {palabras} → {len(txt.split())} palabras.")
    return ajustada


def transcribe_to_nota(media_path, extra_text: str = "", image_paths=None,
                       api_key: str = "", model: str = "", key_pool=None) -> dict:
    """Desgraba un VIDEO (o audio) + contexto opcional y devuelve la nota.

    extra_text: texto que aportó el colaborador (archivo de la carpeta).
    image_paths: fotos anexadas (contexto). Devuelve dict con hay_noticia (bool),
    volanta, titulo, texto, resumen y mejor_momento_seg (float, segundos).
    api_key: clave Gemini a usar como PRIMARIA (para el desgrabador de la radio, que usa
    las claves de radiodelcentro `GEMINI_API_KEY_RADIO`). Si viene vacía, usa GEMINI_API_KEY.
    En ambos casos se rota al resto del pool ante 429 (ver `_gemini_keys`).
    model: modelo a usar. Vacío = GEMINI_MODEL o gemini-2.5-flash (diario). El desgrabador de la
    radio pasa `gemini-flash-latest`: los proyectos NUEVOS de Google (como el de radiodelcentro)
    reciben 404 «no longer available to new users» con el pineado gemini-2.5-flash, pero el alias
    gemini-flash-latest sí funciona.
    """
    media_path = Path(media_path)
    key = (api_key or "").strip() or get("GEMINI_API_KEY")
    if not key:
        raise ValueError("Falta GEMINI_API_KEY en .env (clave gratis de Google AI Studio).")
    model = (model or "").strip() or get("GEMINI_MODEL") or "gemini-2.5-flash"
    mime = _mime(media_path)

    # Parte del medio: si es video, se sube a la Files API; si es audio, va inline.
    file_name = None
    if media_path.suffix.lower() in _VIDEO_EXT:
        logger.info(f"Gemini: subiendo video {media_path.name} ({media_path.stat().st_size//1024} KB) a la Files API…")
        info = _subir_archivo(media_path, mime, key)
        info = _esperar_activo(info["name"], key)
        file_name = info["name"]
        media_part = {"file_data": {"mime_type": mime, "file_uri": info["uri"]}}
    else:  # audio u otro: inline base64
        b64 = base64.b64encode(media_path.read_bytes()).decode("ascii")
        media_part = {"inline_data": {"mime_type": mime, "data": b64}}

    img_parts = []
    for img in (image_paths or [])[:3]:
        try:
            img_parts.append(_img_part(Path(img)))
        except Exception as e:
            logger.warning(f"No se pudo adjuntar la foto de contexto {img}: {e}")

    logger.info(f"Gemini: desgrabando con {model} (contexto: {len(extra_text or '')} chars, "
                f"{len(image_paths or [])} foto(s))…")
    try:
        nota = _generar_nota(media_part, img_parts, extra_text or "", key, model,
                             key_pool=key_pool, legacy_temp=0.4)
    finally:
        if file_name:  # borrar el archivo subido (best-effort)
            try:
                requests.delete(f"{API_BASE}/{file_name}?key={key}", timeout=30)
            except Exception:
                pass

    # nota ya viene normalizada por _generar_nota (_parse_nota).
    logger.info(f"Gemini OK: hay_noticia={nota['hay_noticia']} | «{nota['volanta']} — {nota['titulo']}» "
                f"| mejor_seg={nota['mejor_momento_seg']:.0f}")
    return nota
