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
ORIGENES = ("entrevistas", "bot", "policiales", "web")
NOMBRE_ORIGEN = {"entrevistas": "Recortes de entrevistas", "bot": "Reels del bot",
                 "policiales": "Reels policiales", "web": "Notas web"}


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
    "entrevistas": {"x": 10, "tiktok": 0},
    "bot":         {"x": 10, "tiktok": 10},
    "policiales":  {"x": 12, "tiktok": 12},
    "web":         {"x": 15, "tiktok": 0},   # las notas web NUNCA van a TikTok (ver puede_ir)
}


def cupo(origen: str, plataforma: str) -> int:
    if origen == "web" and plataforma == "tiktok":
        return 0
    return _int(f"CUPO_{origen.upper()}_{plataforma.upper()}", MATRIZ_DEFAULT[origen][plataforma])


# Tope TOTAL por plataforma en 24 h móviles (suma de todos los orígenes).
#   X: Buffer admite 50 a una cuenta sin verificar → 47 deja margen para posteos a mano.
#   TikTok: 15 (ver arriba). Si TikTok admite más en la práctica, subir TOPE_TIKTOK_24H.
def tope_24h(plataforma: str) -> int:
    return _int(f"TOPE_{plataforma.upper()}_24H", {"x": 47, "tiktok": 15}[plataforma])


# Programadas a la vez en Buffer por canal. El plan Free admite 10; se dejan 2 libres para
# lo que el equipo programe a mano desde Buffer.
def max_pendientes(plataforma: str) -> int:
    return _int(f"BUFFER_MAX_PENDIENTES_{plataforma.upper()}", 8)


# Minutos mínimos entre dos publicaciones del mismo canal.
def separacion_min(plataforma: str) -> int:
    return _int(f"SEPARACION_{plataforma.upper()}_MIN", {"x": 15, "tiktok": 40}[plataforma])


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
}

# Peso editorial de cada origen al ordenar la cola (más = antes), además de la prioridad
# de cada pieza (1-10) y lo urgente. Lo propio y local va antes que lo regional.
PESO_ORIGEN = {"bot": 30, "entrevistas": 25, "web": 20, "policiales": 10}
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
