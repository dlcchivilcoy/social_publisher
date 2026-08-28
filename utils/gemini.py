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
import os
import time
from pathlib import Path

import requests

from utils.config import get
from utils.logger import get_logger

logger = get_logger("gemini")

API_BASE = "https://generativelanguage.googleapis.com/v1beta"
UPLOAD_URL = "https://generativelanguage.googleapis.com/upload/v1beta/files"


def _suf_num(nombre: str) -> int:
    """Sufijo numérico de un nombre tipo GEMINI_API_KEY_5 → 5 (0 si no termina en número).
    Para ordenar las claves nuevas de forma natural (_5 antes que _10)."""
    cola = nombre.rsplit("_", 1)[-1]
    return int(cola) if cola.isdigit() else 0


def _gemini_keys(primary: str = "") -> list:
    """Claves Gemini a usar, EN ORDEN, para ROTAR ante 429 (cuota agotada de UNA clave):
    la clave primaria (si se pasa) + TODAS las GEMINI_API_KEY* cargadas en el .env. Así, si una
    clave se queda sin cuota, se sigue con la siguiente. Deduplicadas, sin vacías.

    DINÁMICO (2026-08-09): para sumar cupo alcanza con crear más claves —cada una de un PROYECTO
    distinto de Google AI Studio, que tiene su propio cupo gratis— y cargarlas en el .env como
    GEMINI_API_KEY_5 / _6 / _7 … : se enganchan SOLAS al pool, sin tocar el código (y hay que
    resincronizar el secret de la nube: `gh secret set ENV_FILE < .env`). [ver _fallback_models]

    Orden: primero las conocidas del diario (compat), después cualquier clave NUEVA del diario, y
    al final las de radiodelcentro (respaldo compartido; el flujo radio las pasa como `primary`,
    así que las usa PRIMERO — ver transcribe_to_nota(api_key=...))."""
    # GEMINI_API_KEY_YT es la clave con FACTURACIÓN activa (proyecto "Desgrabador YouTube",
    # Nivel 1 · Prepago) → va PRIMERA a propósito: la usa de arranque todo video (máxima
    # fiabilidad en videos largos 3-5 min), y recién si falla cae a las gratis. Para volver a
    # "gratis primero, paga de respaldo": dejar GEMINI_API_KEY al frente de la lista.
    diario_fijas = ["GEMINI_API_KEY_YT", "GEMINI_API_KEY", "GEMINI_API_KEY_2",
                    "GEMINI_API_KEY_3", "GEMINI_API_KEY_4"]
    radio_fijas = ["GEMINI_API_KEY_RADIO", "GEMINI_API_KEY_RADIO_2", "GEMINI_API_KEY_RADIO_3"]
    fijas = set(diario_fijas + radio_fijas)
    # Cualquier GEMINI_API_KEY* del .env que NO esté en las listas fijas se suma sola al pool.
    nuevas = sorted((n for n in os.environ if n.startswith("GEMINI_API_KEY") and n not in fijas),
                    key=lambda n: (_suf_num(n), n))
    nuevas_diario = [n for n in nuevas if "RADIO" not in n]
    nuevas_radio = [n for n in nuevas if "RADIO" in n]
    nombres = diario_fijas + nuevas_diario + radio_fijas + nuevas_radio
    cand = [primary] + [get(n) or "" for n in nombres]
    out, visto = [], set()
    for k in cand:
        k = (k or "").strip()
        if k and k not in visto:
            visto.add(k)
            out.append(k)
    return out


def _clave_por_defecto() -> str:
    """Clave a usar cuando el llamador NO especifica ninguna: la PRIMERA del pool.

    Antes cada flujo caía a `GEMINI_API_KEY` por su cuenta, y esa variable NO es la primera
    del pool: el desgrabador de videos arrancaba con una clave gratis mientras el de YouTube
    arrancaba con la paga. Distinto orden para el mismo trabajo, sin que nadie lo decidiera.
    Con esto, la prioridad vive en UN solo lugar (`_gemini_keys`) y vale para todos."""
    pool = _gemini_keys()
    return pool[0] if pool else (get("GEMINI_API_KEY") or "")


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


# Cuántas veces se espera sobre la MISMA clave+modelo ante un 503 antes de rotar. Bajo a
# propósito: esperar sobre un proyecto saturado no lo desatasca; probar otro proyecto, sí.
# Modelo principal por defecto. gemini-2.5-flash quedo RETIRADO el 2026-08-27: devuelve
# 404 "no longer available to new users" en TODAS las claves menos la del proyecto mas
# viejo, y esa estaba saturada (503) -> el bot tenia un solo camino posible y tapado.
# Configurable con GEMINI_MODEL en el .env.
_MODELO_DEFAULT = "gemini-3.6-flash"


_ESPERAS_POR_COMBO = 2


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
    # 503/500 = servidor SOBRECARGADO. Antes se esperaba siempre sobre la MISMA clave y el mismo
    # modelo: el 2026-08-27 eso dejó al desgrabador de YouTube 90 minutos reintentando hasta que
    # GitHub cortó la corrida, teniendo 9 claves y un modelo de respaldo sin usar. La capacidad se
    # asigna por PROYECTO y por MODELO, así que ante un 503 que insiste conviene MOVERSE, no
    # esperar. Se espera `_ESPERAS_POR_COMBO` veces en el mismo combo y después se rota.
    esperas_aqui = 0
    intentos = max(9, len(combos) * (_ESPERAS_POR_COMBO + 1) + 3)
    for intento in range(intentos):
        m, k = combos[ci]
        r = requests.post(f"{API_BASE}/models/{m}:generateContent?key={k}",
                          json=payload, timeout=timeout)
        if r.status_code == 429 and ci < len(combos) - 1:
            ci += 1  # 429 → probar la siguiente combinación (otra clave, o el modelo de respaldo)
            esperas_aqui = 0
            nuevo_m = combos[ci][0]
            if nuevo_m != m:
                logger.warning(f"Gemini 429; cambio al modelo de respaldo «{nuevo_m}»…")
            else:
                logger.warning(f"Gemini 429 (cuota de una clave); roto de clave (combo {ci + 1}/{len(combos)})…")
            continue
        if r.status_code in (429, 500, 503) and intento < intentos - 1:
            if esperas_aqui < _ESPERAS_POR_COMBO or ci >= len(combos) - 1:
                espera = min(60, 15 * (esperas_aqui + 1))
                esperas_aqui += 1
                logger.warning(f"Gemini {r.status_code} (sobrecargado); reintento en {espera}s…")
                time.sleep(espera)
                continue
            ci += 1  # sigue sobrecargado acá → me muevo a otra clave/modelo en vez de esperar
            esperas_aqui = 0
            nuevo_m = combos[ci][0]
            logger.warning(f"Gemini {r.status_code} sigue sobrecargado; me paso a "
                           + (f"el modelo de respaldo «{nuevo_m}»" if nuevo_m != m else "otra clave")
                           + f" (combo {ci + 1}/{len(combos)})…")
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

# Regla dura de imparcialidad + privacidad — se pega a todos los prompts que redactan la nota. El
# usuario pidió (2026-08-06/08) cero sesgo/invención/exageración, PERO conservar la info bien contada;
# y omitir solo datos sensibles (DNI, nombres de menores), dejando patentes y nombres/edades de adultos.
_NEUTRAL_RULE = (
    "• IMPARCIALIDAD Y CERO EXAGERACIÓN (regla dura): contá los hechos de forma NEUTRAL y sin "
    "sesgo, con lenguaje sobrio. Describí SOLO lo que dice la fuente (audio/texto/descripción del "
    "vecino). NO agregues adjetivos, dramatismo, calificativos ni valoraciones que la fuente no "
    "diga. NO cuantifiques lo que no está cuantificado: si no se dice cuánta gente había, NO "
    "escribas «una multitud», «muchísima gente», «un gran operativo» ni un número; describilo en "
    "neutro o no lo menciones. Ante la duda con un dato NO respaldado, poné MENOS.\n"
    "• PERO NO RECORTES la información que SÍ está en la fuente y está bien contada: conservá esos "
    "datos. La sobriedad es para lo inventado o exagerado, NO para achicar lo que realmente pasó.\n"
    "• PRIVACIDAD (datos personales): NUNCA publiques números de DNI/documento (omitilos siempre). "
    "NO pongas el NOMBRE de personas MENORES de edad (referilas como «un menor» o «una adolescente "
    "de 15 años», sin nombre). SÍ podés incluir PATENTES de vehículos y NOMBRES y EDADES de "
    "personas MAYORES de edad cuando la fuente los aporte.\n"
)

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
    "PALABRAS, sin punto final, sin comillas. PRIORIDAD: si hay una persona identificada por su "
    "nombre (quien habla, el entrevistado o el protagonista nombrado del hecho), poné su NOMBRE Y "
    "APELLIDO (y su cargo solo si entra en las 5 palabras, ej. «Juan Pérez, intendente»). SOLO si "
    "NO hay ninguna persona nombrada, poné de qué se trata el hecho en pocas palabras (ej. «Choque "
    "en Ruta 30», «Robo en un comercio»). NUNCA inventes un nombre: si no estás seguro de la "
    "grafía, usá el nombre tal como aparece en el contexto/título, y si no hay forma de "
    "confirmarlo, poné el hecho. Vacío si hay_noticia es false.\n"
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
    + _NEUTRAL_RULE
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
    "- descripcion: 2 a 4 frases con las palabras clave naturales (qué se ve y por qué importa). "
    "NO escribas la dirección de la web ni links ni hashtags: el sistema los agrega solo al final "
    "(si los escribís vos, el dominio sale mal escrito). "
    "FORMATO (importante para que se lea fácil): escribí CADA oración o idea como un PÁRRAFO "
    "APARTE, separados por un RENGLÓN EN BLANCO (o sea, punto y aparte con una línea vacía en "
    "medio). NO entregues todo junto en un solo bloque de texto. "
    "TERMINÁ CON LA ÚLTIMA FRASE DEL TEXTO: no agregues hashtags, ni links, ni invitaciones a la "
    "web o al canal de YouTube. Ese cierre lo escribe el sistema y va DESPUÉS del texto; si lo "
    "escribís vos también, sale DUPLICADO en la descripción.\n"
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
    key = _clave_por_defecto()
    if not key:
        raise ValueError("Falta GEMINI_API_KEY en .env (clave gratis de Google AI Studio).")
    model = get("GEMINI_MODEL") or _MODELO_DEFAULT
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
            "mediaResolution": _media_resolution(),
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
    # `descripcion` es SOLO EL CUERPO. La invitación al canal, la de la web y los hashtags los
    # agrega quien publica (`branding.cierre_youtube` / `transcriber._descripcion_social`), una
    # sola vez y en el orden pedido. Antes se agregaba también acá y salía DUPLICADA.
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
    key = _clave_por_defecto()
    if not key:
        raise ValueError("Falta GEMINI_API_KEY en .env.")
    model = get("GEMINI_MODEL") or _MODELO_DEFAULT
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
            "mediaResolution": _media_resolution(),
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


def _media_resolution() -> str:
    """Resolución con la que Gemini muestrea imágenes/VIDEO. LOW usa ~4x menos tokens que media/alta
    (clave para videos largos: menos consumo, menos 429), con impacto casi nulo al desgrabar audio +
    escena. Configurable con GEMINI_MEDIA_RESOLUTION=low|medium|high (default low)."""
    v = (get("GEMINI_MEDIA_RESOLUTION") or "low").strip().lower()
    return {"low": "MEDIA_RESOLUTION_LOW", "medium": "MEDIA_RESOLUTION_MEDIUM",
            "high": "MEDIA_RESOLUTION_HIGH"}.get(v, "MEDIA_RESOLUTION_LOW")


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
            "mediaResolution": _media_resolution(),
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

# Regla compartida (redacción + verificación) para NOMBRES PROPIOS y SIGLAS. El reconocimiento de
# voz los escribe fonéticos (ej. «CASMA» por «CAZMA»); la grafía correcta la manda el CONTEXTO/
# TÍTULO original del video, no el audio. Y si no hay seguridad total, no se escribe (pedido del
# usuario 2026-07-29).
_SIGLAS_RULE = (
    "\nNOMBRES PROPIOS Y SIGLAS — LA GRAFÍA LA MANDA EL CONTEXTO/TÍTULO, NO EL AUDIO (el "
    "reconocimiento de voz los escribe fonéticos y los equivoca mucho):\n"
    "• Si un nombre propio o una sigla que aparece en la transcripción figura escrito distinto de "
    "como está en el CONTEXTO o en el TÍTULO ORIGINAL del video (ej. «CASMA» en el audio vs "
    "«CAZMA» en el título), usá SIEMPRE la grafía del contexto/título, NO la fonética de la "
    "transcripción. (Esto es solo para CORREGIR la escritura de algo que YA está en la "
    "transcripción; NO habilita agregar nombres nuevos que no se mencionen.)\n"
    "• Sigla que NO esté en el contexto/título: deducí sus letras de las INICIALES de las palabras "
    "del nombre de la organización tal como lo pronuncian (si dicen «Cámara Argentina de ... Zona "
    "... Metropolitana ...», la sigla se arma con esas iniciales).\n"
    "• Si NO tenés seguridad TOTAL de la grafía —ni por el contexto/título ni por las iniciales del "
    "nombre pronunciado— NO escribas la sigla ni el nombre: usá una forma genérica («la cámara», "
    "«la institución», «la entidad», «el organismo», «el club») u omitilo. SIEMPRE es preferible la "
    "nota SIN la sigla que con la sigla equivocada.\n"
)


_TRANSCRIBIR_PROMPT = (
    "Sos un transcriptor profesional. Te paso un VIDEO (o audio). Tu ÚNICA tarea es TRANSCRIBIR "
    "en español rioplatense (es-AR) EXACTAMENTE lo que se dice y lo que aparece escrito en "
    "pantalla, palabra por palabra.\n"
    "REGLAS ESTRICTAS (respetalas siempre):\n"
    "• NO resumas, NO interpretes, NO corrijas, NO 'mejores' ni completes lo que se dice.\n"
    "• NO agregues NADA que no esté en el audio o la pantalla: ni contexto, ni antecedentes, ni "
    "datos, ni cifras, ni nombres, ni campeonatos, ni fechas, ni lugares.\n"
    "• NOMBRES PROPIOS y SIGLAS: si en el CONTEXTO o el TÍTULO ORIGINAL que se te pasa figura la "
    "grafía de un nombre o una sigla que estás escuchando, escribilo con ESA grafía. Si no está "
    "ahí y dudás, transcribilo como suena (no lo cambies por otro que 'te suene'); los pasos "
    "siguientes deciden si se confirma u omite. NÚMEROS: tal como se dicen.\n"
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
    "• NÚMEROS: copialos TAL CUAL la transcripción. NOMBRES PROPIOS y SIGLAS: la grafía la manda el "
    "CONTEXTO/TÍTULO (ver la regla del final), no la fonética de la transcripción.\n"
    "• Ante la duda, poné MENOS: mejor una nota más corta y 100% fiel que una más larga con un "
    "dato inventado o una sigla mal escrita.\n"
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
    "- zocalo: texto del zócalo del reel, MÁXIMO 5 PALABRAS, sin punto ni comillas. PRIORIZÁ "
    "SIEMPRE el NOMBRE Y APELLIDO de la persona central (quien habla, el entrevistado o el "
    "protagonista nombrado del hecho), con el cargo solo si entra en las 5 palabras («Juan Pérez, "
    "intendente»). SOLO si NO hay ninguna persona nombrada, poné de qué se trata el hecho («Choque "
    "en Ruta 30»). No inventes un nombre: la grafía la manda el contexto/título (ver la regla del "
    "final); si no se puede confirmar el nombre, poné el hecho. Vacío si hay_noticia es false.\n"
    "CRITERIO EDITORIAL: usá comillas solo para frases claras de la transcripción; si una frase "
    "suena dudosa o cortada, parafraseala. No agregues firma ni autor.\n"
    + _NEUTRAL_RULE
) + _SIGLAS_RULE

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
    "• QUITÁ adjetivos, calificativos, valoraciones o cuantificadores que la fuente NO respalde "
    "(ej. «una multitud» si no se dijo cuánta gente había): dejá una descripción neutra e imparcial.\n"
    "• PRIVACIDAD: sacá números de DNI/documento y el NOMBRE de personas menores de edad; dejá las "
    "patentes y los nombres/edades de adultos. NO recortes datos reales bien contados: solo lo "
    "inventado, exagerado o sensible.\n"
    "• NÚMEROS: que coincidan con la transcripción. NOMBRES PROPIOS y SIGLAS: corregí la grafía "
    "según el CONTEXTO/TÍTULO (ver la regla del final); si el audio la transcribió fonética "
    "(ej. «CASMA» por «CAZMA»), dejá la del contexto/título, y si no se puede confirmar, omitila.\n"
    "• NO inventes datos nuevos para 'tapar' lo que sacaste: si al quitar algo el párrafo queda "
    "corto, dejalo corto. Conservá el estilo, la volanta y el título salvo que tengan un error.\n"
    "• Si tras la limpieza no queda información suficiente para una nota, poné hay_noticia=false.\n"
    "Devolvé EXACTAMENTE: hay_noticia, volanta, titulo, texto, resumen, zocalo (mismos campos y "
    "límites que la nota original) y ADEMÁS:\n"
    "- correcciones: lista breve (puede ser []) de qué sacaste, suavizaste o corregiste y por qué "
    "(ej. «corregí 'CASMA' → 'CAZMA' según el título» o «saqué la sigla: no la pude confirmar»).\n"
) + _SIGLAS_RULE

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


# ── ¿Manda el TEXTO del colaborador o el AUDIO? (pedido 2026-08-23) ───────────
# Si el colaborador ya escribió la info BIEN REDACTADA Y COMPLETA, esa es la FUENTE PRINCIPAL y el
# audio del video pasa a ser COMPLEMENTO (aporta detalles, citas y color). Si el texto es flaco, se
# mantiene el comportamiento de siempre (el audio manda). El corte es por cantidad de palabras
# ÚTILES del aporte: configurable con TEXTO_PRIORITARIO_MIN_PALABRAS; kill-switch TEXTO_PRIORITARIO=0.
_CTX_ETIQUETAS = ("lugar del hecho:", "descripción aportada por el colaborador:",
                  "descripcion aportada por el colaborador:")


def _palabras_utiles(texto: str) -> int:
    """Palabras REALES del aporte del colaborador: descuenta las etiquetas que agrega el bot
    («Lugar del hecho:», «Descripción aportada por el colaborador:») para no inflar la cuenta."""
    total = 0
    for linea in (texto or "").splitlines():
        limpia = linea.strip()
        bajo = limpia.lower()
        for etiqueta in _CTX_ETIQUETAS:
            if bajo.startswith(etiqueta):
                limpia = limpia[len(etiqueta):]
                break
        total += len(limpia.split())
    return total


def _umbral_texto() -> int:
    try:
        return max(1, int(get("TEXTO_PRIORITARIO_MIN_PALABRAS") or 60))
    except ValueError:
        return 60


def _texto_manda(extra_text: str) -> bool:
    """True si el texto del colaborador alcanza para ser la FUENTE PRINCIPAL de la nota."""
    if str(get("TEXTO_PRIORITARIO", "1")).strip().lower() in ("0", "no", "false", "off"):
        return False
    return _palabras_utiles(extra_text) >= _umbral_texto()


# Redacción con el TEXTO del colaborador como fuente principal (el audio complementa).
_REDACTAR_PROMPT_TEXTO = (
    "Sos el editor del «Diario La Campaña» / «Radio del Centro» de Chivilcoy (Argentina). Un "
    "colaborador mandó un TEXTO ya redactado con la información del hecho y, además, un video del que "
    "te paso la TRANSCRIPCIÓN. Armá UNA noticia en español rioplatense (es-AR), estilo periodístico, "
    "tercera persona.\n"
    "JERARQUÍA DE FUENTES (regla principal):\n"
    "• El TEXTO DEL COLABORADOR es la FUENTE PRINCIPAL: es información de primera mano y ya viene "
    "redactada. La nota se apoya en ÉL: respetá sus datos, su enfoque y TODA su información.\n"
    "• La TRANSCRIPCIÓN del audio es COMPLEMENTARIA: usala para ENRIQUECER la nota (declaraciones "
    "textuales, precisiones, detalles, color) SOLO cuando aporte algo que el texto no dice.\n"
    "• Si el texto y el audio se CONTRADICEN, MANDA EL TEXTO del colaborador.\n"
    "• Si el audio no agrega nada nuevo, la nota puede salir prácticamente solo con el texto: NO "
    "rellenes ni estires con material del audio que no suma.\n"
    "REGLA DE ORO — CERO INVENCIÓN: usá ÚNICAMENTE información EXPLÍCITA en alguna de las dos "
    "fuentes. PROHIBIDO agregar contexto histórico, antecedentes, cifras, fechas, nombres, cargos, "
    "lugares, campeonatos o récords que NINGUNA de las dos diga. Si no está, NO existe para la nota.\n"
    "• PROHIBIDO exagerar, dramatizar o endurecer: mantené la magnitud y el tono de las fuentes.\n"
    "• NÚMEROS: copialos TAL CUAL. NOMBRES PROPIOS y SIGLAS: la grafía la manda el TEXTO del "
    "colaborador, NO la fonética de la transcripción.\n"
    "Devolvé EXACTAMENTE estos campos:\n"
    "- hay_noticia: true si entre las dos fuentes hay una nota REAL; false si no.\n"
    "- volanta: antetítulo corto (2 a 5 palabras), sin punto final. Vacío si hay_noticia es false.\n"
    "- titulo: titular claro y fiel (máx ~90 caracteres), sin punto final. Puede ser una cita "
    "breve y textual. Vacío si false.\n"
    "- texto: cuerpo en párrafos separados por línea en blanco (\\n\\n). ORDENALO POR TEMAS, no "
    "minuto a minuto. La extensión la manda el material: si es breve, priorizá FIDELIDAD antes que "
    "extensión, sin rellenar ni repetir. Vacío si false.\n"
    "- resumen: resumen para redes (máx 280 caracteres): qué pasó y por qué importa. Vacío si false.\n"
    "- zocalo: texto del zócalo del reel, MÁXIMO 5 PALABRAS, sin punto ni comillas. PRIORIZÁ "
    "SIEMPRE el NOMBRE Y APELLIDO de la persona central (quien habla, el entrevistado o el "
    "protagonista nombrado del hecho), con el cargo solo si entra en las 5 palabras («Juan Pérez, "
    "intendente»). SOLO si NO hay ninguna persona nombrada, poné de qué se trata el hecho («Choque "
    "en Ruta 30»). No inventes un nombre; si no se puede confirmar, poné el hecho. Vacío si false.\n"
    "CRITERIO EDITORIAL: usá comillas solo para frases claras y textuales (de la transcripción o del "
    "texto); si una frase suena dudosa o cortada, parafraseala. No agregues firma ni autor.\n"
    + _NEUTRAL_RULE
) + _SIGLAS_RULE

# Regla extra para el VERIFICADOR cuando el texto del colaborador es la fuente principal: sin esto,
# el paso 3 BORRA todo lo que no esté en el audio (y se comería justo la info del texto).
_VERIF_TEXTO_RULE = (
    "\nIMPORTANTE — HAY DOS FUENTES VÁLIDAS: además de la transcripción, el colaborador aportó un "
    "TEXTO redactado que es la FUENTE PRINCIPAL (información de primera mano). Un dato está "
    "RESPALDADO si aparece en CUALQUIERA de las dos. NO elimines lo que dice el texto del colaborador "
    "por el solo hecho de no estar en el audio. Si las dos se contradicen, MANDA EL TEXTO. Todo lo "
    "demás (no inventar, no exagerar, privacidad, números y siglas) sigue igual.\n"
)


def _multipaso_on() -> bool:
    """True si la desgrabación en 3 pasos está activa (default). GEMINI_NOTA_MULTIPASO=0 vuelve
    al tiro único legacy."""
    return str(get("GEMINI_NOTA_MULTIPASO", "1")).strip().lower() not in ("0", "no", "false", "off")


def _es_cuota(e: Exception) -> bool:
    """Detecta un error de CUOTA/saturación de Gemini (mismo criterio que transcriber_radio).
    Ante cuota NO caemos a legacy: dejamos subir el error para que el llamador rote de clave."""
    m = str(e).lower()
    return any(s in m for s in ("429", "quota", "resource_exhausted", "exhausted", "rate limit"))


# ── Glosario de nombres propios locales (Fase 2) ──────────────────────────────
def _glosario() -> str:
    """Lee el glosario de nombres propios locales (una línea por término, '#' = comentario).
    Ruta: DESGRABADOR_GLOSARIO o `glosario.txt` en la raíz del repo. Devuelve los términos unidos
    por ', ' (o '' si no hay). Se inyecta en la transcripción para escribir bien los propios;
    mejora solo cada vez que el usuario agrega un nombre al archivo (no hace falta tocar código)."""
    ruta = (get("DESGRABADOR_GLOSARIO") or "").strip()
    p = Path(ruta) if ruta else (Path(__file__).resolve().parent.parent / "glosario.txt")
    try:
        if not p.exists():
            return ""
        terminos = [s.strip() for s in p.read_text(encoding="utf-8").splitlines()
                    if s.strip() and not s.strip().startswith("#")]
        return ", ".join(terminos)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"No pude leer el glosario {p}: {e}")
        return ""


# ── Groq Whisper como motor de transcripción del Paso 1 (Fase 2, OPCIONAL) ────
# Se activa SOLO si hay GROQ_API_KEY (gratis en console.groq.com). Sin la clave, todo sigue con
# la transcripción de Gemini (Fase 1). Groq da mejores NOMBRES PROPIOS/números que la ASR interna
# de Gemini, pero solo transcribe AUDIO (no lee el texto en pantalla). La portada y los segmentos
# los sigue eligiendo Gemini mirando el video.
GROQ_ASR_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


def _groq_key() -> str:
    return (get("GROQ_API_KEY") or "").strip()


def _audio_first_on() -> bool:
    """AUDIO-FIRST: transcribir con Groq el AUDIO y NO subir el video a Gemini (default ON si hay
    GROQ_API_KEY). Es lo que evita la subida del video —lo más lento, lo que satura y lo que traía
    el 403 de la Files API—: Gemini queda solo para redactar (llamadas de TEXTO, baratas), y la
    portada la elige `video.mejor_frame`. `ASR_AUDIO_FIRST=0` vuelve a que Gemini mire el video."""
    return str(get("ASR_AUDIO_FIRST", "1")).strip().lower() not in ("0", "no", "false", "off")


def _groq_on() -> bool:
    return bool(_groq_key()) and str(get("ASR_GROQ", "1")).strip().lower() not in ("0", "no", "false", "off")


def _groq_model() -> str:
    """Modelo de Groq. Default whisper-large-v3-turbo (más velocidad y MÁS CUOTA gratis, ideal para
    videos largos; calidad casi igual). Para máxima calidad: GROQ_ASR_MODEL=whisper-large-v3."""
    return get("GROQ_ASR_MODEL") or "whisper-large-v3-turbo"


def _extraer_audio(media_local_path):
    """Extrae el audio de un video/audio local a un mp3 chico (mono 16 kHz) para mandarlo a Groq
    por debajo de su límite de tamaño. Devuelve la ruta temporal o None si falla."""
    import subprocess
    import tempfile
    try:
        from video import _ffmpeg  # import perezoso: evita cualquier import circular
        ff = _ffmpeg()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Groq: no ubico ffmpeg ({e}); sigo con la transcripción de Gemini.")
        return None
    src = Path(media_local_path)
    out = Path(tempfile.gettempdir()) / f"asr_{src.stem}_{int(time.time())}.mp3"
    cmd = [ff, "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
           "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k", str(out)]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            logger.warning("Groq: ffmpeg no pudo extraer el audio; sigo con la transcripción de Gemini.")
            return None
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Groq: error extrayendo audio ({e}); sigo con la transcripción de Gemini.")
        return None


def _audio_de_youtube(url: str):
    """Baja SOLO la pista de audio de un video de YouTube a un archivo temporal.

    Es lo que le faltaba al desgrabador de YouTube para poder usar el MISMO camino
    AUDIO-FIRST que el desgrabador de videos: con el audio en disco, la transcripción la hace
    Groq y a Gemini le llega SOLO TEXTO. Así se deja de depender de la ingesta de video de
    Gemini, que es la parte que se satura (503) y la que más consume de la clave paga.

    Best-effort: ante cualquier error devuelve None y se sigue con el camino clásico."""
    import tempfile
    try:
        import yt_dlp
    except ImportError:
        logger.warning("yt-dlp no está instalado; sigo con Gemini mirando el video de YouTube.")
        return None
    destino = Path(tempfile.gettempdir()) / f"yt_asr_{int(time.time())}"
    opciones = {
        "format": "bestaudio/best",
        "outtmpl": str(destino) + ".%(ext)s",
        "quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
        # Sin postprocesado: el mp3 chico que necesita Groq lo arma después `_extraer_audio`.
        "postprocessors": [],
    }
    try:
        with yt_dlp.YoutubeDL(opciones) as ydl:
            info = ydl.extract_info(url, download=True)
            archivo = Path(ydl.prepare_filename(info))
        if not archivo.exists() or archivo.stat().st_size == 0:
            logger.warning("yt-dlp no dejó el audio; sigo con Gemini mirando el video.")
            return None
        logger.info(f"  Paso 1a: audio de YouTube bajado ({archivo.stat().st_size // 1024} KB).")
        return archivo
    except Exception as e:  # noqa: BLE001 — bajar el audio nunca puede voltear el desgrabe
        logger.warning(f"No pude bajar el audio de YouTube ({e}); sigo con Gemini mirando el video.")
        return None


# Groq RECHAZA (400) una pista de más de 896 caracteres. Antes se cortaba en 1000 y la
# llamada fallaba entera: se perdía la transcripción de Groq y se caía a que Gemini mirara
# el video —justo lo que queremos evitar—, en silencio. Se corta con margen y por palabra.
_GROQ_PROMPT_MAX = 850


def _pista_groq(prompt_hint: str) -> str:
    """Recorta la pista (glosario + contexto) al límite que acepta Groq, sin cortar palabras.

    El glosario va primero en la pista, así que si algo se cae es el contexto largo — que es
    lo menos útil para el reconocimiento de voz (Whisper solo pondera ~224 tokens)."""
    pista = " ".join((prompt_hint or "").split())
    if len(pista) <= _GROQ_PROMPT_MAX:
        return pista
    corte = pista[:_GROQ_PROMPT_MAX]
    espacio = corte.rfind(" ")
    corte = corte[:espacio] if espacio > _GROQ_PROMPT_MAX // 2 else corte
    logger.debug(f"Groq: pista recortada de {len(pista)} a {len(corte)} caracteres.")
    return corte


def _transcribir_groq(media_local_path, prompt_hint: str = ""):
    """Transcribe el AUDIO de un archivo local con Groq Whisper (español, temperatura 0).
    `prompt_hint` (el glosario) sesga hacia los nombres propios locales. Best-effort: cualquier
    error devuelve None y se usa la transcripción de Gemini."""
    key = _groq_key()
    if not key:
        return None
    audio = _extraer_audio(media_local_path)
    if audio is None:
        return None
    try:
        with open(audio, "rb") as fh:
            files = {"file": (audio.name, fh, "audio/mpeg")}
            data = {"model": _groq_model(), "language": "es", "temperature": "0",
                    "response_format": "json"}
            pista = _pista_groq(prompt_hint)
            if pista:
                data["prompt"] = pista
            logger.info(f"  Paso 1b (Groq {_groq_model()}): transcribiendo audio…")
            r = requests.post(GROQ_ASR_URL, headers={"Authorization": f"Bearer {key}"},
                              files=files, data=data, timeout=300)
        if r.status_code >= 400:
            logger.warning(f"Groq {r.status_code}: {r.text[:200]}; sigo con la transcripción de Gemini.")
            return None
        return str(r.json().get("text", "")).strip() or None
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Groq falló ({e}); sigo con la transcripción de Gemini.")
        return None
    finally:
        try:
            audio.unlink()
        except Exception:
            pass


def _nota_multipaso(media_part: dict, img_parts: list, extra_text: str, key: str, model: str,
                    key_pool=None, instrucciones: str = "", media_local_path=None) -> dict:
    """Desgraba en 3 pasos (transcribir → redactar anclado → verificar), todo a temperatura 0.
    `media_part` es la parte de Gemini con el video/audio (subido o file_uri); `img_parts` son las
    fotos de contexto ya convertidas a partes. `media_local_path`: ruta local del medio (si la hay);
    con GROQ_API_KEY se usa Groq Whisper para la transcripción del Paso 1 (mejores nombres propios).
    Devuelve la nota normalizada por `_parse_nota`."""
    glosario = _glosario()
    # Paso 1 — transcripción literal + elección de portada/segmentos (mira el video).
    t_prompt = _TRANSCRIBIR_PROMPT
    if glosario:
        t_prompt += ("\nNOMBRES PROPIOS LOCALES (si aparecen en el audio, escribilos EXACTAMENTE "
                     "así; son de la zona y se suelen transcribir mal):\n" + glosario)
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

    # Paso 1b (opcional) — re-transcribir el AUDIO con Groq Whisper para clavar nombres/números
    # (Gemini ya dio portada y segmentos). Solo si hay GROQ_API_KEY y una ruta local del medio.
    if media_local_path and _groq_on():
        # Hint para Whisper: glosario + contexto/título (así clava las siglas/nombres del título).
        hint = "\n".join(x for x in (glosario, (extra_text or "").strip()) if x)
        g_txt = _transcribir_groq(media_local_path, hint)
        if g_txt and len(g_txt) >= 25:
            logger.info(f"  Paso 1b (Groq): transcripción reemplazada por la de Groq ({len(g_txt)} chars).")
            transcripcion = g_txt

    return _redactar_y_verificar(transcripcion, extra_text, key, model, key_pool,
                                 instrucciones, momento, segmentos)


def _redactar_y_verificar(transcripcion: str, extra_text: str, key: str, model: str,
                          key_pool, instrucciones: str, momento: float, segmentos: list) -> dict:
    """Pasos 2 y 3 (redactar anclado + verificar), SOLO con texto: no necesita el video.

    Está aparte para que lo use tanto el flujo clásico (Gemini mira el video) como el
    AUDIO-FIRST (Groq transcribe el audio y el video NUNCA se sube a Gemini)."""
    # Paso 2 — redacción anclada. Si el colaborador mandó un texto COMPLETO, ese texto es la fuente
    # PRINCIPAL y la transcripción COMPLEMENTA; si el texto es flaco (o no hay), manda la transcripción.
    texto_manda = _texto_manda(extra_text)
    r_prompt = _REDACTAR_PROMPT_TEXTO if texto_manda else _REDACTAR_PROMPT
    if (instrucciones or "").strip():
        r_prompt += "\nINSTRUCCIÓN ADICIONAL DE REDACCIÓN (respetala):\n" + instrucciones.strip()
    if texto_manda:
        logger.info(f"  Paso 2/3: TEXTO del colaborador como fuente PRINCIPAL "
                    f"({_palabras_utiles(extra_text)} palabras ≥ {_umbral_texto()}); el audio complementa.")
        r_prompt += ("\n\nTEXTO DEL COLABORADOR (FUENTE PRINCIPAL):\n" + extra_text.strip() +
                     "\n\nTRANSCRIPCIÓN DEL AUDIO (fuente COMPLEMENTARIA):\n" + transcripcion)
    else:
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
            if texto_manda:
                # Con el texto como fuente principal, el verificador tiene que tratarlo como fuente
                # VÁLIDA: si no, borraría del borrador todo lo que aportó el colaborador y no está
                # en el audio (justo la información que el usuario quiere priorizar).
                v_prompt = (_VERIFICAR_PROMPT + _VERIF_TEXTO_RULE +
                            "\n\nTEXTO DEL COLABORADOR (FUENTE PRINCIPAL):\n" + extra_text.strip() +
                            "\n\nTRANSCRIPCIÓN (fuente complementaria):\n" + transcripcion +
                            "\n\nNOTA A VERIFICAR (JSON):\n" + json.dumps(borrador, ensure_ascii=False))
            else:
                ctx = (("\n\nCONTEXTO/TÍTULO (autoridad para la grafía de nombres y siglas):\n"
                        + extra_text.strip()) if (extra_text or "").strip() else "")
                v_prompt = (_VERIFICAR_PROMPT + ctx + "\n\nTRANSCRIPCIÓN:\n" + transcripcion +
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
                  key_pool=None, legacy_temp: float = 0.4, instrucciones: str = "",
                  media_local_path=None) -> dict:
    """Genera la nota a partir de la parte de medio ya construida. Usa el flujo en 3 pasos
    (default) y, ante un error que NO es de cuota, cae al tiro ÚNICO legacy (PROMPT_BASE) para no
    quedar nunca peor que antes. El error de cuota se re-lanza para que el llamador rote de clave.
    `media_local_path`: ruta local del medio (si la hay), para el motor Groq del Paso 1."""
    if _multipaso_on():
        try:
            return _nota_multipaso(media_part, img_parts, extra_text, key, model, key_pool,
                                   instrucciones, media_local_path=media_local_path)
        except Exception as e:  # noqa: BLE001
            if _es_cuota(e):
                raise
            logger.warning(f"Desgrabación en 3 pasos falló ({e}); caigo al tiro único (legacy).")
    prompt = PROMPT_BASE
    if (instrucciones or "").strip():
        prompt += "\nINSTRUCCIÓN ADICIONAL DE REDACCIÓN (respetala):\n" + instrucciones.strip()
    if (extra_text or "").strip():
        if _texto_manda(extra_text):
            # Texto completo del colaborador → es la FUENTE PRINCIPAL; el video complementa.
            prompt += ("\nTEXTO DEL COLABORADOR — ES LA FUENTE PRINCIPAL de la nota: respetá SU "
                       "información, sus datos y su enfoque. El audio/video COMPLEMENTA (detalles, "
                       "citas y color) y NO puede contradecirlo; si se contradicen, MANDA EL TEXTO. "
                       "Si el video no agrega nada, la nota sale prácticamente solo con el texto:\n"
                       + extra_text.strip())
        else:
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
    key = (api_key or "").strip() or _clave_por_defecto()
    if not key:
        raise ValueError("Falta GEMINI_API_KEY en .env (clave gratis de Google AI Studio).")
    model = get("GEMINI_MODEL") or _MODELO_DEFAULT

    # ── AUDIO-FIRST (2026-08-27): el MISMO camino que ya usa el desgrabador de videos ─────
    # Se baja SOLO el audio, lo transcribe Groq y a Gemini le llega TEXTO. Antes se le pasaba
    # el file_uri de YouTube y Gemini tenía que INGERIR el video: eso es lo que se satura
    # (503 en cadena) y lo que más consume de la clave paga. Si algo falla, cae SOLO al
    # camino clásico de abajo: nunca queda peor que antes.
    if _groq_on() and _audio_first_on():
        audio = _audio_de_youtube(url)
        if audio is not None:
            hint = "\n".join(x for x in (_glosario(), (extra_text or "").strip()) if x)
            try:
                g_txt = _transcribir_groq(audio, hint)
            except Exception as e:  # noqa: BLE001
                g_txt = None
                logger.warning(f"Groq falló ({e}); caigo a Gemini mirando el video.")
            finally:
                try:
                    audio.unlink()
                except Exception:  # noqa: BLE001
                    pass
            if g_txt and len(g_txt) >= 25:
                logger.info(f"Audio-first (YouTube): Groq transcribió {len(g_txt)} chars; "
                            f"Gemini solo redacta (sin ingerir el video).")
                nota = _redactar_y_verificar(g_txt, extra_text or "", key, model,
                                             _gemini_keys(key), instrucciones=instrucciones,
                                             momento=0.0, segmentos=[])
                # La transcripción viaja con la nota: el segundo pase de largo la usa como
                # fuente en vez de volver a pedirle el video a Gemini.
                nota["transcripcion"] = g_txt
                logger.info(f"Gemini OK (YouTube, audio-first): hay_noticia={nota['hay_noticia']} "
                            f"| «{nota['volanta']} — {nota['titulo']}»")
                return nota
            logger.info("Audio-first (YouTube): sin transcripción de Groq; sigo con Gemini.")

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
                             objetivo: int, extra_text: str = "", api_key: str = "",
                             transcripcion: str = "") -> dict:
    """Segundo pase de LARGO para el desgrabador: si el cuerpo quedó fuera del rango (~2
    páginas de Word), reescribe la nota para dejarla entre `min_palabras` y `max_palabras`.

    - Si quedó CORTA: se vuelve a MIRAR el video (file_uri) para desarrollar con material
      REAL (declaraciones, contexto, datos) sin inventar nada.
    - Si quedó LARGA: se sintetiza a partir del propio texto (sin volver a gastar el video).

    Best-effort: ante cualquier error devuelve la nota tal como estaba. Conserva
    volanta/título; solo cambia «texto»."""
    key = (api_key or "").strip() or _clave_por_defecto()
    if not key:
        return nota
    model = get("GEMINI_MODEL") or _MODELO_DEFAULT
    palabras = len((nota.get("texto") or "").split())
    corta = palabras < min_palabras
    guia = (
        f"La nota quedó {'DEMASIADO CORTA' if corta else 'DEMASIADO LARGA'} "
        f"({palabras} palabras). Reescribí el cuerpo para que tenga entre {min_palabras} y "
        f"{max_palabras} palabras (objetivo {objetivo}: DOS páginas de Word, ni media ni tres). "
    )
    # Si hay TRANSCRIPCIÓN (camino audio-first), se desarrolla desde ella: tiene todo lo que se
    # dijo y no hay que volver a pedirle el video a Gemini (que es lo que se satura).
    usa_transcripcion = corta and bool((transcripcion or "").strip())
    if usa_transcripcion:
        guia += ("Desarrollá EN SERIO a partir de la TRANSCRIPCIÓN de abajo, que es lo que "
                 "REALMENTE se dijo: más declaraciones y citas textuales, contexto, antecedentes, "
                 "el porqué y el para qué, consecuencias y próximos pasos. NO inventes NADA que no "
                 "esté en la transcripción; no rellenes con vueltas ni repitas la misma idea. ")
    elif corta:
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
    if usa_transcripcion:
        prompt += "\n\nTRANSCRIPCIÓN LITERAL DEL VIDEO:\n" + transcripcion.strip()
    parts = [{"text": prompt}]
    if corta and url and not usa_transcripcion:
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
    key = (api_key or "").strip() or _clave_por_defecto()
    if not key:
        raise ValueError("Falta GEMINI_API_KEY en .env (clave gratis de Google AI Studio).")
    model = (model or "").strip() or get("GEMINI_MODEL") or _MODELO_DEFAULT
    mime = _mime(media_path)

    # Fotos de contexto (independientes de la clave).
    img_parts = []
    for img in (image_paths or [])[:3]:
        try:
            img_parts.append(_img_part(Path(img)))
        except Exception as e:
            logger.warning(f"No se pudo adjuntar la foto de contexto {img}: {e}")

    # AUDIO u otro: va INLINE (base64), sin Files API → no hay archivo atado a una clave, así que la
    # rotación de clave normal es segura (sin riesgo de 403).
    if media_path.suffix.lower() not in _VIDEO_EXT:
        b64 = base64.b64encode(media_path.read_bytes()).decode("ascii")
        media_part = {"inline_data": {"mime_type": mime, "data": b64}}
        logger.info(f"Gemini: desgrabando con {model} (contexto: {len(extra_text or '')} chars, "
                    f"{len(image_paths or [])} foto(s))…")
        nota = _generar_nota(media_part, img_parts, extra_text or "", key, model,
                             key_pool=key_pool, legacy_temp=0.4, media_local_path=media_path)
        logger.info(f"Gemini OK: hay_noticia={nota['hay_noticia']} | «{nota['volanta']} — {nota['titulo']}» "
                    f"| mejor_seg={nota['mejor_momento_seg']:.0f}")
        return nota

    # ── AUDIO-FIRST: Groq transcribe el AUDIO y el video NO se sube a Gemini ──────────────
    # Es el camino rápido y barato: se saltea la subida del video (lo más lento, lo que satura
    # y lo que traía el 403 de la Files API) y Gemini queda solo para REDACTAR (texto). La foto
    # de portada la elige `video.mejor_frame` (mejor_momento_seg=0 se la pide). Si Groq no puede,
    # cae SOLO al camino clásico de abajo (Gemini mirando el video): nunca queda peor que antes.
    if _groq_on() and _audio_first_on():
        hint = "\n".join(x for x in (_glosario(), (extra_text or "").strip()) if x)
        try:
            g_txt = _transcribir_groq(media_path, hint)
        except Exception as e:  # noqa: BLE001
            g_txt = None
            logger.warning(f"Groq falló ({e}); caigo a Gemini mirando el video.")
        if g_txt and len(g_txt) >= 25:
            logger.info(f"Audio-first: Groq transcribió {len(g_txt)} chars SIN subir el video. "
                        f"Gemini solo redacta; la portada la elige el sistema.")
            nota = _redactar_y_verificar(g_txt, extra_text or "", key, model,
                                         key_pool or _gemini_keys(key), instrucciones="",
                                         momento=0.0, segmentos=[])
            logger.info(f"Gemini OK: hay_noticia={nota['hay_noticia']} | «{nota['volanta']} — "
                        f"{nota['titulo']}»")
            return nota
        logger.info("Audio-first: sin transcripción de Groq; sigo con Gemini mirando el video.")

    # VIDEO: la Files API ATA el archivo subido a la clave que lo subió. Por eso subimos Y desgrabamos
    # con la MISMA clave; si esa clave se queda sin cuota (429) probamos con la SIGUIENTE clave
    # RE-SUBIENDO el video (NO se puede reusar el archivo con otra clave → 403 PERMISSION_DENIED, el bug
    # que fallaba «algunos videos» al rotar de clave a mitad del desgrabe). La rotación de MODELO sí
    # sigue adentro de _generate (misma clave, así el archivo se sigue viendo). La radio pasa
    # key_pool=[k] (una sola) y rota afuera; el diario (key_pool=None) recorre todo el pool acá.
    keys_video = list(key_pool) if key_pool else _gemini_keys(key)
    if not keys_video:
        keys_video = [key]
    size_kb = media_path.stat().st_size // 1024
    ultimo_err = None
    for idx, k in enumerate(keys_video):
        file_name = None
        try:
            logger.info(f"Gemini: subiendo video {media_path.name} ({size_kb} KB) a la Files API… "
                        f"(clave {idx + 1}/{len(keys_video)})")
            info = _subir_archivo(media_path, mime, k)
            info = _esperar_activo(info["name"], k)
            file_name = info["name"]
            media_part = {"file_data": {"mime_type": mime, "file_uri": info["uri"]}}
            logger.info(f"Gemini: desgrabando con {model} (contexto: {len(extra_text or '')} chars, "
                        f"{len(image_paths or [])} foto(s))…")
            nota = _generar_nota(media_part, img_parts, extra_text or "", k, model,
                                 key_pool=[k], legacy_temp=0.4, media_local_path=media_path)
            logger.info(f"Gemini OK: hay_noticia={nota['hay_noticia']} | «{nota['volanta']} — "
                        f"{nota['titulo']}» | mejor_seg={nota['mejor_momento_seg']:.0f}")
            return nota
        except Exception as e:  # noqa: BLE001
            if _es_cuota(e) and idx < len(keys_video) - 1:
                logger.warning(f"Gemini sin cuota en la clave {idx + 1}/{len(keys_video)}; "
                               f"re-subo el video con la siguiente clave…")
                ultimo_err = e
                continue
            raise
        finally:
            if file_name:  # borrar el archivo subido con ESA clave (best-effort)
                try:
                    requests.delete(f"{API_BASE}/{file_name}?key={k}", timeout=30)
                except Exception:
                    pass
    # Solo se llega acá si se agotaron todas las claves por cuota.
    raise ultimo_err if ultimo_err is not None else RuntimeError("Gemini: sin claves para el video")


# Corrección de la descripción del vecino: NO reescribir/acortar/extender — solo corregir la
# gramática y redactarla bien (pedido del usuario 2026-08-09). Se usa en foto (con la foto de apoyo)
# y en el video de corresponsal sin desgrabar (sin foto).
_CORREGIR_PROMPT = (
    "Sos el editor del «Diario La Campaña» de Chivilcoy (Argentina). Un vecino corresponsal escribió "
    "una DESCRIPCIÓN de un hecho. Tu ÚNICA tarea es dejarla BIEN REDACTADA, SIN cambiar la información:\n"
    "• NO acortes ni resumas, NO extiendas ni agregues NADA: mantené TODA la info del vecino y "
    "aproximadamente el MISMO largo. NO agregues datos, contexto, adjetivos ni opiniones.\n"
    "• Corregí ortografía, gramática, puntuación, tildes y mayúsculas, y mejorá la redacción si está "
    "mal escrita, para que se lea prolija y periodística — pero SIN alterar el sentido, los hechos ni "
    "las cifras. Si ya está bien redactada, dejala casi igual.\n"
    "• PRIVACIDAD: NO publiques números de DNI ni el NOMBRE de personas menores de edad; SÍ dejá "
    "patentes y nombres/edades de adultos.\n"
    "{FOTO}"
    "Devolvé EXACTAMENTE estos campos:\n"
    "- hay_noticia: true.\n"
    "- volanta: \"\".\n"
    "- titulo: un título corto y FIEL (máx ~80 caracteres) que resuma el hecho, sin punto final (es "
    "lo único que redactás de cero, y tiene que ser fiel a lo que dice el vecino).\n"
    "- texto: la descripción del vecino YA CORREGIDA (mismo contenido y largo), en párrafos separados "
    "por una línea en blanco si corresponde.\n"
    "- resumen: los primeros ~280 caracteres del texto corregido.\n"
    "- zocalo: \"\".\n- mejor_momento_seg: 0.\n- segmentos_destacados: [].\n"
)


def corregir_texto(descripcion: str, lugar: str = "", foto_path=None,
                   api_key: str = "", model: str = "", key_pool=None) -> dict:
    """Deja BIEN REDACTADA la descripción del vecino (gramática/ortografía/redacción) SIN cambiar la
    información ni el largo (pedido 2026-08-09). Si se pasa `foto_path`, la adjunta como apoyo visual
    (no para agregar hechos). Devuelve el mismo dict que `transcribe_to_nota` (hay_noticia=True)."""
    key = (api_key or "").strip() or _clave_por_defecto()
    if not key:
        raise ValueError("Falta GEMINI_API_KEY en .env (clave gratis de Google AI Studio).")
    model = (model or "").strip() or get("GEMINI_MODEL") or _MODELO_DEFAULT
    ctx = (descripcion or "").strip()
    if (lugar or "").strip():
        ctx = f"Lugar del hecho: {lugar.strip()}\n{ctx}"
    img_parts, foto_nota = [], ""
    if foto_path:
        try:
            img_parts.append(_img_part(Path(foto_path)))
            foto_nota = ("• La FOTO adjunta es SOLO apoyo visual: NO la uses para agregar hechos "
                         "(no cuentes personas, no deduzcas causas ni magnitudes).\n")
        except Exception as e:
            logger.warning(f"No pude adjuntar la foto: {e}")
    prompt = _CORREGIR_PROMPT.replace("{FOTO}", foto_nota) + \
        "\nDESCRIPCIÓN DEL VECINO (corregila, NO la cambies):\n" + ctx
    logger.info(f"Gemini: corrigiendo la descripción del vecino ({len(ctx)} chars) con {model}…")
    raw = _post_generate([{"text": prompt}] + img_parts, key, model,
                         temperature=0.2, key_pool=key_pool or _gemini_keys(key))
    nota = _parse_nota(raw)
    nota["hay_noticia"] = True
    # Salvavidas: si Gemini no devolvió algún campo, caigo al texto crudo del vecino (nunca vacío).
    if not (nota.get("texto") or "").strip():
        nota["texto"] = ctx
    if not (nota.get("titulo") or "").strip():
        nota["titulo"] = (ctx.split("\n")[0][:80].strip() or "Envío de un corresponsal")
    if not (nota.get("resumen") or "").strip():
        nota["resumen"] = (nota["texto"] or ctx)[:280]
    logger.info(f"Gemini OK (corrección): «{nota['titulo']}»")
    return nota


_RESUMEN_SEO_PROMPT = (
    "Sos el community manager del «Diario La Campaña» de Chivilcoy (Argentina). Te paso el TÍTULO y "
    "el TEXTO de una nota. Escribí la BAJADA que va como descripción del REEL en Instagram/Facebook.\n"
    "OBJETIVO — que se entienda de un vistazo y que POSICIONE (SEO):\n"
    "• MÁXIMO {MAX} caracteres. Es un tope duro: si no entra, sacá lo secundario.\n"
    "• Los PRIMEROS ~100 caracteres son los que se ven antes del «… más»: meté AHÍ lo más importante "
    "y las PALABRAS CLAVE (qué pasó + dónde + quién). Nada de arranques vacíos tipo «Enterate de…».\n"
    "• Usá las palabras que la gente buscaría: el lugar (Chivilcoy y el barrio/calle si está), los "
    "NOMBRES PROPIOS, la institución y el tema (ej. «choque», «inauguración», «paro»).\n"
    "• 2 o 3 oraciones cortas, en español rioplatense, tono informativo y sobrio. Se puede usar UN "
    "emoji como mucho, y solo si suma.\n"
    "• PROHIBIDO: hashtags, links, «click acá», clickbait, signos de admiración de más, MAYÚSCULAS "
    "sostenidas, y CUALQUIER dato que no esté en el texto (no inventes cifras, nombres ni causas).\n"
    "• No repitas el título palabra por palabra: complementalo con lo más jugoso del cuerpo.\n"
    "Devolvé SOLO el texto de la bajada, sin comillas ni etiquetas."
)


def _cortar_prolijo(texto: str, max_chars: int) -> str:
    """Corta en el último final de ORACIÓN que entre; si no hay, en la última palabra. Sin
    puntos suspensivos (mismo criterio que `carrusel_notas._resumen_caption`)."""
    t = " ".join((texto or "").split()).strip()
    if len(t) <= max_chars:
        return t
    corte = t[:max_chars]
    for fin in (". ", "? ", "! "):
        i = corte.rfind(fin)
        if i >= max_chars * 0.5:
            return corte[:i + 1].strip()
    i = corte.rfind(" ")
    return (corte[:i] if i > 0 else corte).strip()


def resumen_seo(titulo: str, texto: str, max_chars: int = 300, lugar: str = "",
                api_key: str = "", model: str = "", key_pool=None) -> str:
    """Bajada SEO para la descripción del REEL en IG/FB cuando el texto es largo.

    Resume el cuerpo priorizando las palabras clave al principio (qué + dónde + quién) y respeta
    el tope de caracteres. Best-effort: ante cualquier error devuelve un corte prolijo por
    oración/palabra; nunca rompe la publicación ni devuelve vacío."""
    base = " ".join((texto or "").split()).strip()
    if not base:
        return ""
    try:
        key = (api_key or "").strip() or _clave_por_defecto()
        model = (model or "").strip() or get("GEMINI_MODEL") or _MODELO_DEFAULT
        ctx = f"LUGAR: {lugar.strip()}\n" if (lugar or "").strip() else ""
        prompt = (_RESUMEN_SEO_PROMPT.replace("{MAX}", str(int(max_chars))) +
                  f"\n\n{ctx}TÍTULO: {(titulo or '').strip()}\n\nTEXTO:\n{base}")
        r = _generate(model, {"contents": [{"parts": [{"text": prompt}]}],
                              "generationConfig": {"temperature": 0.3}},
                      key, timeout=120, key_pool=key_pool or _gemini_keys(key))
        out = ""
        for part in (r.json().get("candidates") or [{}])[0].get("content", {}).get("parts", []):
            out += part.get("text", "")
        out = " ".join(out.split()).strip().strip('"').strip()
        if out:
            if len(out) > max_chars:  # por las dudas, si Gemini se pasó del tope
                out = _cortar_prolijo(out, max_chars)
            logger.info(f"Resumen SEO para el reel: {len(base)} → {len(out)} chars.")
            return out
    except Exception as e:  # noqa: BLE001
        logger.warning(f"No pude generar el resumen SEO ({e}); corto el texto prolijo.")
    return _cortar_prolijo(base, max_chars)


def nota_desde_foto(descripcion: str, foto_path, lugar: str = "",
                    api_key: str = "", model: str = "", key_pool=None) -> dict:
    """Corrige la descripción que escribió el corresponsal (gramática/redacción, SIN cambiar la info
    ni el largo) usando la FOTO como apoyo visual. Devuelve el mismo dict que `transcribe_to_nota`."""
    return corregir_texto(descripcion, lugar=lugar, foto_path=foto_path,
                          api_key=api_key, model=model, key_pool=key_pool)
