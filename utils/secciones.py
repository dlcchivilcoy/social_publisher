"""A qué SECCIÓN de la web va cada nota (Locales, Deportes, Campo, Opinión, Nacionales).

POR QUÉ EXISTE (2026-09-13)
---------------------------
La sección salía del NÚMERO DE PÁGINA del diario de papel: 8 y 9 eran Deportes,
2/3/5/7 eran Locales (`wix._category_ids`). Cuando la edición pasó a «notas
sueltas numeradas» en la raíz de la carpeta del día, el escáner dejó de saber la
página y empezó a mandar `page=0` para TODAS (`carrusel_notas._find_notes`).
Desde entonces cada nota se publicó únicamente en «Inicio»: 886 notas entre el
17/6/2026 y el 13/9/2026 quedaron sin sección, y /seccion/locales, /deportes,
/campo, /opinion y /nacionales se congelaron con lo último que había.

Ahora la sección se decide por el CONTENIDO, que es lo único que siempre está:

  1. REGLAS (acá abajo). Deterministas, gratis y sin red. Alcanzan para la enorme
     mayoría: la volanta del diario ya dice «Fútbol —», «Básquet —», «Manejo
     ganadero —», «Opinión —».
  2. IA (Gemini) solo cuando las reglas NO están seguras — típicamente el límite
     fino entre Locales y Nacionales («Espinoza pasó por Chivilcoy», «trasladar la
     capital bonaerense»). Una sola llamada, timeout corto, sin esperas largas: si
     Gemini no contesta, mandan las reglas. Nunca puede colgar una publicación.

Kill-switch: SECCION_IA=0 deja solo las reglas.

Convención de los diccionarios: un término que termina en «-» matchea por PREFIJO
(«ganader-» agarra ganadero/ganadería); el resto matchea la palabra ENTERA. Esto
no es un detalle: sin el borde derecho, «inta» matcheaba «intachable» y «rinde»
—que ni siquiera es agro— mandaba a Campo «La Ronda Cultural rinde homenaje a
Atahualpa Yupanqui».
"""
import json
import re
import unicodedata

import requests

from utils.config import get
from utils.logger import get_logger

logger = get_logger("secciones")

LOCALES = "locales"
DEPORTES = "deportes"
CAMPO = "campo"
OPINION = "opinion"
NACIONALES = "nacionales"

SLUGS = (LOCALES, DEPORTES, CAMPO, OPINION, NACIONALES)

# Valor especial: la nota va a la portada y a NINGUNA sección.
SIN_SECCION = "inicio"

# Listados de servicio, no noticias. Van sin sección: la recategorización masiva los
# mandó a Locales (hablan de Chivilcoy, claro) y no es ahí donde tienen que estar —
# Locales se llenaría de avisos fúnebres y de farmacias de turno.
_SERVICIO = ("sepelios", "farmacias de turno", "turnos de farmacia")

# Variable del .env con el id de categoría de Wix de cada sección, y el id que se usa
# si esa variable no está cargada. Los ids VAN EN EL CÓDIGO a propósito: no son
# secretos (sin la WIX_API_KEY no sirven de nada, y la web los tiene igual en
# diario_web/src/lib/wix.js), y si dependieran solo del .env bastaría con olvidarse de
# resincronizar el secret ENV_FILE para que la nube volviera a publicar todo en
# «Inicio» —que es exactamente el problema que este módulo vino a arreglar—.
ENV_CATEGORIA = {
    LOCALES: "WIX_CAT_LOCALES",
    DEPORTES: "WIX_CAT_DEPORTES",
    CAMPO: "WIX_CAT_CAMPO",
    OPINION: "WIX_CAT_OPINION",
    NACIONALES: "WIX_CAT_NACIONALES",
}
ID_CATEGORIA = {
    LOCALES: "4558c237-6a14-4ed8-b17f-cd4fd84010f2",
    DEPORTES: "094f631e-1b56-485f-8777-6e167802455e",
    CAMPO: "0b4eafce-3bb8-44df-96dd-43e53fa24a9c",
    OPINION: "08b921b7-df16-4926-a467-214c95f0e2e9",
    NACIONALES: "b646fede-00cb-4f10-aca5-56fdda96094a",
}
ID_INICIO = "9bcc12a0-51c9-451c-8611-2b06153c9f58"

ETIQUETA = {
    LOCALES: "Locales", DEPORTES: "Deportes", CAMPO: "Campo",
    OPINION: "Opinión", NACIONALES: "Nacionales",
}


def _norm(texto: str) -> str:
    """Minúsculas y sin tildes, para que «básquet» y «basquet» sean lo mismo."""
    t = unicodedata.normalize("NFD", (texto or "").lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def _hay(texto: str, terminos) -> list:
    """Términos presentes. «xxx-» = por prefijo; el resto, palabra entera."""
    out = []
    for t in terminos:
        raiz = t[:-1] if t.endswith("-") else t
        fin = "" if t.endswith("-") else r"(?![a-z0-9])"
        if re.search(rf"(?<![a-z0-9]){re.escape(raiz)}{fin}", texto):
            out.append(t)
    return out


# ── Diccionarios ────────────────────────────────────────────────────────────────
# Deportes: el nombre de la disciplina es la señal fuerte (la volanta la usa casi
# siempre). «club» y «equipo» quedan AFUERA a propósito: «un auto terminó
# incrustado en la sede del Club Argentino» es un choque, no deporte.
_DEPORTES_FUERTE = (
    "futbol", "basquet", "basket", "voley", "volley", "hockey", "handball", "handbol",
    "tenis", "padel", "paddle", "atletismo", "natacion", "boxeo", "ajedrez",
    "automovilismo", "karting", "ciclismo", "rugby", "patinaje", "taekwondo",
    "karate", "judo", "softbol", "beisbol", "motociclismo", "equitacion",
    "pesca deportiva", "triatlon", "maraton", "gimnasia artistica", "gimnasia ritmica",
    "newcom", "bochas", "tiro federal", "esgrima", "kickboxing", "patin artistico",
)
_DEPORTES_APOYO = (
    "torneo", "campeonato", "fixture", "liga nacional", "torneo federal",
    "juegos bonaerenses", "odesur", "panamericano", "deportiv-", "deportista-",
    "semifinal", "cuartos de final", "podio", "medalla", "hinchada", "arbitro",
    "entrenador", "goleador", "subcampeon", "campeon",
)

# Campo: agro en sentido amplio (producción, entidades, clima productivo, mercado).
# OJO con los términos que también existen fuera del agro: «hacienda» es también la
# Secretaría de Hacienda del Municipio y «remate» es también el remate de chatarra,
# así que van calificados.
_CAMPO_FUERTE = (
    "agropecuari-", "agroindustri-", "agricultura", "agricol-", "ganader-", "soja",
    "maiz", "trigo", "girasol", "cebada", "sorgo", "cosecha-", "cosechador-",
    "siembra", "sembr-", "rendimiento-", "retenciones", "senasa", "carbap", "inta",
    "sociedad rural", "asociacion rural", "expo rural", "exposicion rural",
    "productor-", "bovino-", "vacuno-", "rodeo-", "tambo-", "forraj-", "silaje",
    "mercado de hacienda", "remate de hacienda", "remate ganadero", "abigeato",
    "silobolsa", "fitosanitari-", "agroquimic-", "maleza-", "apicultura", "colmena-",
    "cerealera-", "acopiador-", "campana gruesa", "campana fina", "caminos rurales",
    "escuela agraria", "chacra-", "cultivo-", "emergencia agropecuaria", "faena",
    "frigorific-", "lecheria", "hectarea-", "pulverizacion", "genetica animal",
)

# Opinión: SOLO lo que se publica firmado como mirada propia. Se mira únicamente la
# VOLANTA, nunca el cuerpo: «dio su opinión» aparece en media redacción, y
# «editorial» matchea «Fondo Editorial Municipal» y «dinámica editorial».
_OPINION_VOLANTA = (
    "opinion", "editorial", "columna", "pedido de publicacion", "carta de lectores",
    "carta al director", "efemerides",
)

# Nacionales: país y provincia. Señal NECESARIA pero no suficiente (ver `_puntajes`).
_NACIONAL_FUERTE = (
    "milei", "casa rosada", "gobierno nacional", "poder ejecutivo nacional",
    "congreso de la nacion", "diputados de la nacion", "senado de la nacion",
    "indec", "anses", "afip", "arca", "banco central", "fmi", "inflacion",
    "jubilaciones", "jubilados", "paritaria nacional", "paritaria docente",
    "kicillof", "gobernador", "la plata", "legislatura bonaerense",
    "gobierno bonaerense", "provincia de buenos aires", "nivel nacional",
    "en todo el pais", "dolar", "ministerio de economia", "corte suprema",
    "decreto nacional", "veto presidencial", "paro nacional", "suteba",
)

# Señales de que la nota es NUESTRA. Pesan más que lo nacional: una nota sobre el
# intendente y una ley nacional sigue siendo Locales.
_LOCAL_FUERTE = (
    "chivilcoy", "chivilcoyan-", "municipio", "municipal", "intendente", "britos",
    "concejo deliberante", "concejal-", "palacio municipal", "moquehua",
    "gorostiaga", "ramon biaus", "la rica", "emilio ayarza", "indacochea",
    "bomberos voluntarios", "hospital municipal", "barrio-", "vecino-",
    "plaza moreno", "parque industrial", "nuestra ciudad", "de la ciudad",
)


def _puntajes(texto: str) -> dict:
    """Puntaje de cada sección para ese texto (0 = ninguna señal)."""
    dep = 3 * len(_hay(texto, _DEPORTES_FUERTE)) + len(_hay(texto, _DEPORTES_APOYO))
    campo = 3 * len(_hay(texto, _CAMPO_FUERTE))
    nac = 2 * len(_hay(texto, _NACIONAL_FUERTE))
    local = len(_hay(texto, _LOCAL_FUERTE))
    # Lo nacional solo gana si la nota NO es claramente de acá.
    nac = max(0, nac - 2 * local)
    return {DEPORTES: dep, CAMPO: campo, OPINION: 0, NACIONALES: nac, LOCALES: local}


def _volanta(titulo: str) -> str:
    """La volanta del diario: lo que va antes del em-dash del título de Wix."""
    for sep in (" — ", " – ", " - "):
        if sep in (titulo or ""):
            izq = titulo.split(sep, 1)[0].strip()
            return izq if len(izq) <= 60 else ""
    return ""


def por_reglas(titulo: str, cuerpo: str = "") -> tuple:
    """(slug, seguro, puntajes). `seguro` = las reglas alcanzan y no hace falta la IA.

    El título pesa el doble que el cuerpo: una nota de policiales puede nombrar de
    paso una cancha, pero el título dice de qué se trata."""
    t_tit, t_cue = _norm(titulo), _norm(cuerpo)[:4000]
    p_tit, p_cue = _puntajes(t_tit), _puntajes(t_cue)
    total = {s: 2 * p_tit[s] + p_cue[s] for s in SLUGS}

    # La volanta es lo más limpio que hay: el diario la escribe justamente para decir
    # de qué sección es la nota.
    vol = _norm(_volanta(titulo))
    if vol:
        # La marca de opinión tiene que ABRIR la volanta. Si solo aparece adentro, no
        # cuenta: «Dinámica editorial — Oche Califa…» es una entrevista, no una columna.
        if any(vol.startswith(m) for m in _OPINION_VOLANTA):
            return OPINION, True, total
        if _hay(vol, _DEPORTES_FUERTE):
            return DEPORTES, True, total

    orden = sorted((s for s in SLUGS if s not in (LOCALES, OPINION)),
                   key=lambda s: total[s], reverse=True)
    primera, segunda = orden[0], orden[1]
    gana, escolta = total[primera], total[segunda]

    if gana >= 6 and gana >= escolta * 2:
        return primera, True, total          # señal fuerte y sin competencia
    if gana == 0 and total[LOCALES] >= 3:
        return LOCALES, True, total          # habla de Chivilcoy y de nada más
    if gana == 0:
        return LOCALES, False, total         # sin señales: Locales, pero que mire la IA
    return (primera if gana > escolta else LOCALES), False, total


# ── IA (desempate) ──────────────────────────────────────────────────────────────
_CRITERIO = (
    "Sos el editor de secciones de Diario La Campaña, de Chivilcoy (Buenos Aires, "
    "Argentina). Asigná a cada nota UNA sola sección:\n"
    "- locales: la vida de Chivilcoy y su zona. Municipio, Concejo Deliberante, obras, "
    "servicios, salud, educación, policiales y judiciales de acá, cultura, teatro, "
    "música, libros y escritores, entidades, comercios, barrios y localidades del "
    "partido. Es la sección POR DEFECTO: ante cualquier duda, va acá.\n"
    "- deportes: cualquier disciplina deportiva, local o no, incluidos clubes, torneos, "
    "resultados y deportistas chivilcoyanos compitiendo afuera.\n"
    "- campo: actividad agropecuaria. Cultivos, cosecha, clima productivo, ganadería, "
    "maquinaria, precios y mercados, retenciones, entidades del agro (Sociedad o "
    "Asociación Rural, CARBAP, INTA, Senasa), caminos rurales y Expo Rural.\n"
    "- opinion: SOLO columnas, editoriales y textos donde el autor OPINA en primera "
    "persona. NO es opinión una entrevista, una nota cultural, la presentación de un "
    "libro, un homenaje ni la declaración de un dirigente: todo eso es información y va "
    "a locales. Si dudás entre opinion y locales, elegí locales.\n"
    "- nacionales: política y economía de la Argentina o de la provincia de Buenos Aires "
    "cuando el hecho NO pasó en Chivilcoy ni lo protagoniza el Municipio. Si el hecho "
    "pasó acá, o el protagonista es una autoridad o entidad de Chivilcoy, es locales "
    "aunque se hable de una ley nacional o provincial."
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "secciones": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "seccion": {"type": "string", "enum": list(SLUGS)},
                },
                "required": ["n", "seccion"],
            },
        }
    },
    "required": ["secciones"],
}


def _ia_on() -> bool:
    return (get("SECCION_IA", "1") or "1").strip().lower() not in ("0", "false", "no")


def por_ia(notas: list, timeout: int = 25) -> dict:
    """{índice: slug} para esas notas (cada una: {"titulo":…, "texto":…}).

    Una pasada por clave, SIN dormir entre intentos: esto corre dentro de la
    publicación y no puede demorarla. Si no sale, devuelve {} y decide la regla."""
    from utils.gemini import API_BASE, _MODELO_DEFAULT, _gemini_keys

    if not notas or not _ia_on():
        return {}
    claves = _gemini_keys()
    if not claves:
        return {}

    listado = "\n\n".join(
        f"[{i}] TÍTULO: {(n.get('titulo') or '').strip()[:220]}\n"
        f"TEXTO: {' '.join((n.get('texto') or '').split())[:700]}"
        for i, n in enumerate(notas))
    payload = {
        "contents": [{"role": "user", "parts": [{"text":
            f"{_CRITERIO}\n\nDevolvé la sección de CADA nota, usando el número [n] que "
            f"tiene al lado.\n\n{listado}"}]}],
        "generationConfig": {"temperature": 0, "response_mime_type": "application/json",
                             "response_schema": _SCHEMA},
    }

    modelo = get("GEMINI_MODEL") or _MODELO_DEFAULT
    # Cinco claves y no todo el pool: un 429 vuelve en milisegundos, así que probar
    # varias no cuesta nada, pero el tope existe para que un Gemini caído (que sí agota
    # el timeout) no sume nueve esperas dentro de una publicación.
    for clave in claves[:5]:
        try:
            r = requests.post(f"{API_BASE}/models/{modelo}:generateContent?key={clave}",
                              json=payload, timeout=timeout)
            if r.status_code in (429, 503):
                continue  # esa clave está sin cupo o saturada: pruebo la siguiente
            r.raise_for_status()
            crudo = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            datos = json.loads(crudo).get("secciones") or []
            return {int(d["n"]): d["seccion"] for d in datos
                    if d.get("seccion") in SLUGS and 0 <= int(d.get("n", -1)) < len(notas)}
        except Exception as e:
            logger.warning(f"No se pudo clasificar con IA ({e}); mandan las reglas.")
            return {}
    return {}


def es_servicio(titulo: str) -> bool:
    """¿Es un listado de servicio (sepelios, farmacias) y no una noticia?"""
    t = _norm(titulo).lstrip("“\"' ")
    return any(t.startswith(m) for m in _SERVICIO)


def clasificar(titulo: str, cuerpo: str = "", pagina: int = 0) -> str:
    """Sección de UNA nota, o SIN_SECCION. `pagina` es el viejo dato del diario de
    papel: si viene 8 o 9 es Deportes y no hace falta pensar (compatibilidad)."""
    if es_servicio(titulo):
        return SIN_SECCION
    if pagina in (8, 9):
        return DEPORTES
    slug, seguro, _ = por_reglas(titulo, cuerpo)
    if seguro:
        return slug
    elegido = por_ia([{"titulo": titulo, "texto": cuerpo}]).get(0)
    if elegido and elegido != slug:
        logger.info(f"Sección por IA: «{ETIQUETA[elegido]}» (la regla decía "
                    f"«{ETIQUETA[slug]}») — {titulo[:60]}")
    return elegido or slug


def id_inicio() -> str:
    """Id de la categoría «Inicio» (la portada). El .env puede pisarlo."""
    return (get("WIX_CAT_INICIO") or "").strip() or ID_INICIO


def ids_de_categoria(slug: str) -> list:
    """IDs de Wix para esa sección: «Inicio» + la sección. Con SIN_SECCION, solo Inicio."""
    propia = ((get(ENV_CATEGORIA.get(slug, "")) or "").strip()
              or ID_CATEGORIA.get(slug, ""))
    return [c for c in (id_inicio(), propia) if c]
