"""Parámetros del distribuidor a X y TikTok vía Buffer. TODO lo configurable vive acá.

Cada valor se puede pisar con una variable del .env (secret ENV_FILE) sin tocar código:
el nombre de la variable va al lado de cada parámetro.

Medido en la cuenta real el 11/10/2026 (API de Buffer, organización 617c4811…):
  - X @diarioyradio sin verificar (subscriptionType None) → Buffer admite 50/24 h.
  - TikTok @diarioyradio → Buffer admite 25/24 h, pero la doc de TikTok dice que el tope
    de Direct Post es «típicamente ~15 por día por cuenta», compartido entre todas las
    apps, y no hay forma de consultarlo antes de chocarlo. Por eso el tope propio es 15.
  - Plan Free: 10 publicaciones programadas POR CANAL (verificado: con 10 en X el 11.º dio
    LimitReachedError y el de TikTok entró igual). 3 canales (hay 2 en uso).
  - Cuota de la API: 100 / 15 min, 250 / 24 h, 3.000 / 30 días. Varias mutaciones en una
    sola solicitud cuentan como UNA (verificado con 10 createPost juntos).
"""
from __future__ import annotations

import os

from utils.config import get

BUFFER_API = "https://api.buffer.com"
ZONA = "America/Argentina/Buenos_Aires"

# IDs reales de la cuenta (leídos de la API el 11/10/2026). Se pueden pisar por .env si se
# reconecta un canal (Buffer le da un id nuevo).
ORGANIZACION = "617c4811d6012f22471bf0a5"
CANALES_DEFAULT = {"x": "6acbfabe778b240e2fdcc1b9", "tiktok": "6acbfa43778b240e2fdcb2d5"}
SERVICIO = {"x": "twitter", "tiktok": "tiktok"}
NOMBRE = {"x": "X", "tiktok": "TikTok"}
PLATAFORMAS = ("x", "tiktok")

# Orígenes de contenido (los cuatro sistemas).
# «nacionales» (17/10/2026, PDF «Prompt integral» etapa 6): los videos de nacionales y de sus
# categorías (deportes, espectáculos, cine y series, tecnología) del repo policiales-bsas. Solo
# TikTok: a X ya llegan como notas de la web. Llegan por el mismo buzón que policiales.
ORIGENES = ("entrevistas", "bot", "policiales", "web", "nacionales")
NOMBRE_ORIGEN = {"entrevistas": "Recortes de entrevistas", "bot": "Reels del bot",
                 "policiales": "Reels policiales", "web": "Notas web",
                 "nacionales": "Nacionales y categorías"}


def _int(nombre: str, defecto: int) -> int:
    v = (get(nombre) or os.environ.get(nombre) or "").strip()
    try:
        return int(v) if v != "" else defecto
    except ValueError:
        return defecto


def _bool(nombre: str, defecto: bool) -> bool:
    v = (get(nombre) or os.environ.get(nombre) or "").strip().lower()
    if v == "":
        return defecto
    return v not in ("0", "no", "false", "off")


def canal(plataforma: str) -> str:
    return (get(f"BUFFER_CANAL_{plataforma.upper()}") or "").strip() or CANALES_DEFAULT[plataforma]


# ---------------------------------------------------------------- matriz de distribución
# Máximos por día (calendario de Argentina). Son TOPES, no objetivos: nunca se rellena.
# Variable: CUPO_<ORIGEN>_<PLATAFORMA>, p. ej. CUPO_POLICIALES_TIKTOK=8
MATRIZ_DEFAULT = {
    "entrevistas": {"x": 8, "tiktok": 0},
    "bot":         {"x": 10, "tiktok": 5},    # TikTok: 5 + 5 + 5 (TIKTOK_REPARTO, 20/10/2026)
    "policiales":  {"x": 6, "tiktok": 5},
    "web":         {"x": 15, "tiktok": 0},   # las notas web NUNCA van a TikTok (ver puede_ir)
    # Dentro del MISMO tope de TikTok (15): reemplaza parte de lo policial, no suma (17/10/2026).
    # X (19/10/2026): las nacionales salen con su VIDEO y su nota no se repite (x_sincro).
    "nacionales":  {"x": 12, "tiktok": 5},   # en TikTok, solo los virales (TIKTOK_REPARTO)
}


def cupo(origen: str, plataforma: str) -> int:
    if origen == "web" and plataforma == "tiktok":
        return 0
    return _int(f"CUPO_{origen.upper()}_{plataforma.upper()}", MATRIZ_DEFAULT[origen][plataforma])


# Tope TOTAL por plataforma en 24 h móviles (suma de todos los orígenes).
#   X: Buffer admite 50 a una cuenta sin verificar → 47 deja margen para posteos a mano.
#   TikTok: 20 desde el 21/10/2026 (5 bot + 5 policiales + 10 virales; antes 15). Si TikTok frena antes
#   («esperá 24 h»), el distribuidor anota el tope que midió (pausas.tiktok.tope_medido) y lo respeta 7 días.
def tope_24h(plataforma: str) -> int:
    return _int(f"TOPE_{plataforma.upper()}_24H", {"x": 47, "tiktok": 20}[plataforma])


# Programadas a la vez en Buffer por canal. El plan Free admite 10; se dejan 2 libres para
# lo que el equipo programe a mano desde Buffer.
def max_pendientes(plataforma: str) -> int:
    return _int(f"BUFFER_MAX_PENDIENTES_{plataforma.upper()}", 8)


# Minutos mínimos entre dos publicaciones del mismo canal.
def separacion_min(plataforma: str) -> int:
    return _int(f"SEPARACION_{plataforma.upper()}_MIN", {"x": 15, "tiktok": 40}[plataforma])


# Intercalado (17/10/2026, PDF «Prompt integral»): en cada canal, entre dos policiales al menos
# esta cantidad de publicaciones de otra temática. INTERCALADO=0 lo apaga. Se leen al usarse.
def intercalado() -> bool:
    return _bool("INTERCALADO", True)


def min_entre_policiales() -> int:
    return max(1, _int("MIN_NON_POLICE_POSTS_BETWEEN_POLICE", 1))


# TIKTOK EN TANDAS (pedido del editor, 19/10/2026: «hacé un equilibrio en los posteos de TikTok: subí a
# Buffer lo más viral; del máximo permitido dividí los posteos en 3 corridas (10, 18 y 21) y ahí cargá lo
# más viral de cada contenido que subiste en el momento a mis otras redes»). En cada tanda entra el tope
# de 24 h dividido por la cantidad de tandas (15 / 3 = 5), elegido por viralidad, con no más de
# TIKTOK_TANDA_MAX_ORIGEN del mismo origen si hay otra cosa. TIKTOK_TANDAS=0 vuelve al goteo de antes.
def tiktok_tandas() -> tuple:
    crudo = (get("TIKTOK_TANDAS") or "10,18,21").strip()
    if crudo in ("", "0", "no", "off"):
        return ()
    try:
        return tuple(sorted({int(x) for x in crudo.replace(" ", "").split(",") if x}))
    except ValueError:
        return (10, 18, 21)


# REPARTO DE TIKTOK (pedido del editor, 20/10/2026: «si el máximo por día son 15 posteos, 5 tienen que estar
# habilitados para el bot, 5 para policiales y 5 para virales; todo haciendo énfasis en video / reels»).
# Por día (calendario de Argentina): cada categoría tiene SUS lugares y no se los presta a otra. A TikTok solo
# van videos. Virales = las nacionales de la categoría virales o de mundo (internacionales virales con video)
# y cualquier video de potencial 9-10; salen TODOS los días (el resto de nacionales no va a TikTok).
# TIKTOK_REPARTO="bot:5,policiales:5,virales:10"; "0" lo apaga (vuelve a lo más viral de todo).
# 21/10/2026 («de TikTok agregá 5 más de virales»): virales 5 → 10, y el tope de 24 h 15 → 20.
TIKTOK_REPARTO_DEFAULT = {"bot": 5, "policiales": 5, "virales": 10}
NAC_VIRALES_TIKTOK = {"virales", "internacionales"}


def tiktok_reparto() -> dict:
    crudo = (get("TIKTOK_REPARTO") or "").strip()
    if crudo.lower() in ("0", "no", "off"):
        return {}
    if not crudo:
        return dict(TIKTOK_REPARTO_DEFAULT)
    salida = {}
    for parte in crudo.split(","):
        k, _, v = parte.partition(":")
        try:
            salida[k.strip().lower()] = max(0, int(v))
        except ValueError:
            continue
    return salida or dict(TIKTOK_REPARTO_DEFAULT)


def tanda_ventana_min() -> int:
    """Una tanda se arma en la primera corrida del distribuidor (cada 20 min) dentro de esta ventana."""
    return _int("TIKTOK_TANDA_VENTANA_MIN", 50)


def tanda_max_origen() -> int:
    return max(1, _int("TIKTOK_TANDA_MAX_ORIGEN", 2))


def tanda_separacion_min() -> int:
    return max(5, _int("TIKTOK_TANDA_SEPARACION_MIN", 20))


# Una nota web y el reel de la MISMA nota en X: separados al menos esto (y con otro texto).
SEPARACION_MISMA_NOTA_MIN = _int("SEPARACION_MISMA_NOTA_MIN", 180)

# Horario en que se publica (hora de Argentina). Fuera de la franja, se corre al inicio.
def franja(plataforma: str) -> tuple[int, int]:
    """(minuto de inicio, minuto de fin) del día. Fin puede pasar de 1440 (madrugada)."""
    ini = _int(f"FRANJA_{plataforma.upper()}_INICIO", {"x": 7 * 60, "tiktok": 8 * 60}[plataforma])
    fin = _int(f"FRANJA_{plataforma.upper()}_FIN", {"x": 24 * 60, "tiktok": 23 * 60 + 30}[plataforma])
    return ini, fin


# Cuánto adelante se programa en Buffer (el resto espera en la cola propia).
HORIZONTE_MIN = _int("DISTRIBUIDOR_HORIZONTE_MIN", 360)

# Vencimiento: pasado esto sin salir, el contenido ya no se publica (y queda informado).
# Policiales: los videos del Release se borran a las 24 h → nunca más de 20 h.
VENCE_HORAS = {
    "entrevistas": _int("VENCE_ENTREVISTAS_HORAS", 48),
    "bot": _int("VENCE_BOT_HORAS", 48),
    "policiales": min(_int("VENCE_POLICIALES_HORAS", 18), 20),
    "web": _int("VENCE_WEB_HORAS", 24),
    "nacionales": min(_int("VENCE_NACIONALES_HORAS", 18), 20),   # su video vive en el mismo Release
}

# Peso editorial de cada origen al ordenar la cola (más = antes), además de la prioridad
# de cada pieza (1-10) y lo urgente. Lo propio y local va antes que lo regional.
PESO_ORIGEN = {"bot": 30, "entrevistas": 25, "web": 20, "nacionales": 15, "policiales": 10}
BONO_URGENTE = 1000

# Reintentos ante errores pasajeros de Buffer (5xx, red). Un error de datos no se reintenta.
MAX_INTENTOS = _int("DISTRIBUIDOR_MAX_INTENTOS", 3)

# Consulta de estados (cuánto esperar entre una y otra): la cuota es chica.
SINCRONIZAR_CADA_MIN = _int("DISTRIBUIDOR_SINCRONIZAR_MIN", 60)

# Reserva de cuota: por debajo de esto no se gasta en consultas, solo en publicar.
RESERVA_24H = _int("DISTRIBUIDOR_RESERVA_24H", 20)
RESERVA_30D = _int("DISTRIBUIDOR_RESERVA_30D", 150)

# Límites de los videos (los más estrictos de X y TikTok vía API).
X_VIDEO_MAX_SEG = 140          # X sin suscripción: 2:20
X_VIDEO_MAX_MB = 512
TIKTOK_VIDEO_MIN_SEG = 3
TIKTOK_VIDEO_MAX_SEG = 600

# Notas web: qué entra.
WEB_INCLUIR_REGION = _bool("WEB_X_INCLUIR_REGION", False)  # Región = policiales: ya van como reel
WEB_CAT_REGION = "55c5c23e-4ac1-43c4-ae24-125fce001311"
WEB_CAT_NACIONALES_AUTO = "b4cdfc99-ec74-4581-8ce1-3de7d5d1f59b"
WEB_CAT_NACIONALES = (get("WIX_CAT_NACIONALES") or "").strip() or "-"
WEB_VENTANA_HORAS = 24
# QUÉ NO VA A X (pedido del editor, 19/10/2026: «descartá agro, archivo literario, tapa, farmacias,
# sepelios, clima»). Agro publica sus notas en la sección Agro de la web; sepelios, una nota diaria.
WEB_CATS_EXCLUIDAS = {"0b4eafce-3bb8-44df-96dd-43e53fa24a9c"}       # Agro (ex Campo)
WEB_TITULOS_EXCLUIDOS = (r"^\s*(sepelios|farmacias?( de turno)?|clima|pron[oó]stico|tapa|archivo literario|"
                         r"pregunta del d[ií]a|un d[ií]a como hoy|efem[eé]ride)\b")
# Las categorías nacionales que van a X SOLO el sábado y el domingo («ia, espectáculos, cine y series,
# deportes: dejalo para el finde»). En la web: la sección de la categoría + «Nacionales automáticas».
NAC_SOLO_FINDE = {"deportes", "espectaculos", "cine_series", "tecnologia_ia"}
WEB_CATS_SOLO_FINDE = {"094f631e-1b56-485f-8777-6e167802455e",   # Deportes
                       "973d6c3f-8893-4d88-92fe-78c91ab6d1ce",   # Espectáculos
                       "03a75413-7258-4eca-b163-2f931193635c",   # Cine y series
                       "5b9d1e36-6d94-48ab-b5f6-6f9cb1b8fcaf"}   # Tecnología (IA)
# Los VIRALES («si queda lugar, dale espacio a los reels de videos virales»): la categoría virales de
# nacionales y cualquier video de potencial 9 o 10.
NAC_VIRALES = {"virales"}
# 20/10/2026: las nacionales salen solo como REEL con video y llegan a X por el buzón de policiales-bsas;
# su nota de la web ya no va (era la misma noticia dos veces y se comía el tope de X).
X_SIN_NOTAS_NACIONALES = _bool("X_SIN_NOTAS_NACIONALES", True)
# ENTRE SEMANA, SOLO LO PROPIO (pedido del editor, 20/10/2026: «solo dejá las notas de Chivilcoy de mi diario
# que se publican a la mañana, el espacio para las publicaciones del bot como prioridad y que aparezcan
# también mis recortes de entrevistas; virales, deportes, espectáculos, IA, generales, nacionales, etc.,
# dejalo para el fin de semana»). De lunes a viernes, X y TikTok llevan el diario (X), el bot, los recortes y
# los policiales («los policiales dejalos», 20/10/2026); las nacionales, solo el sábado y el domingo.
# DIST_SOLO_FINDE="" lo apaga.
SOLO_FINDE = {o.strip() for o in (os.environ["DIST_SOLO_FINDE"] if "DIST_SOLO_FINDE" in os.environ
                                  else "nacionales").split(",") if o.strip()}
# De la web, a X solo van las notas del diario de la mañana (las del carrusel, con su hora de Instagram).
X_WEB_SOLO_DIARIO = _bool("X_WEB_SOLO_DIARIO", True)
# El bot, primero (X y TikTok): no lo frenan la reserva de virales ni el cupo de su origen.
BOT_PRIMERO = _bool("BOT_PRIMERO", True)
VIRAL_PRIORIDAD = 9
# La nota en X sale con su foto destacada adjunta (más visible que la tarjeta del link,
# que además X puede no leer en una nota recién publicada). 0 = solo el link.
WEB_X_CON_FOTO = _bool("WEB_X_CON_FOTO", True)

# Sitio: los links que se publican tienen que ser de acá.
SITIO = "www.diariolacampaña.com.ar"
SITIO_PUNY = "www.xn--diariolacampaa-2nb.com.ar"

# Hashtags fijos de TikTok (hasta 5 en total: estos 3 + 2 del tema).
TIKTOK_HASHTAGS_FIJOS = ["#chivilcoy", "#diariolacampaña", "#radiodelcentro"]

# Fuente remota del buzón de policiales (repo público).
BUZON_POLICIALES_URL = (get("BUZON_POLICIALES_URL") or
                        "https://raw.githubusercontent.com/dlcchivilcoy/policiales-bsas/main/"
                        "estado/buzon_distribuidor.json")


# ---------------------------------------------------------------- interruptores
def activo() -> bool:
    """Interruptor general. Apagado → no toca Buffer (sigue juntando la cola)."""
    return _bool("DISTRIBUIDOR_ACTIVO", False)


def plataforma_activa(plataforma: str) -> bool:
    return _bool(f"DISTRIBUIDOR_{plataforma.upper()}", True)


def origen_activo(origen: str) -> bool:
    return _bool(f"DISTRIBUIDOR_ORIGEN_{origen.upper()}", True)


def puede_ir(origen: str, plataforma: str) -> bool:
    return (plataforma_activa(plataforma) and origen_activo(origen)
            and cupo(origen, plataforma) > 0)
