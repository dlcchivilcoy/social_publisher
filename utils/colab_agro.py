"""Lo del campo que publica @diarioyradio sale también como COLABORACIÓN con @diarioyradioagro.

Pedido del editor (27/10/2026): «las notas relacionadas al campo, ya sean del bot, carruseles del diario,
policiales o lo que fuese, subirlas también como colaboración a @diarioyradioagro». En Instagram eso es
el parámetro `collaborators` del contenedor (reel, carrusel o foto; las historias no admiten colab): el
posteo aparece en los dos perfiles cuando @diarioyradioagro ACEPTA la invitación, y eso se hace acá
mismo con la cuenta de agro (`/collaboration_invites`, API de dic-2025; el camino inverso —agro invita al
diario— anda todos los días a las 05 desde el 24/10). Si no se puede aceptar al momento, la corrida de
agro barre las invitaciones pendientes (policiales-bsas, agro/publicar.aceptar_invitaciones_del_diario).

¿Qué es «del campo»? El mismo diccionario que manda una nota a la sección Agro de la web
(utils/secciones._CAMPO_FUERTE), sin las palabras que también viven fuera del agro: «productora»
(música), «rendimiento» (escolar), «cultivo» (de marihuana), «hectárea», «chacra», «sembrar».
Una de las firmes alcanza (el texto del posteo es corto: titular + bajada); las ambiguas solas no cuentan.

Interruptor: COLAB_AGRO=0 lo apaga. Necesita AGRO_INSTAGRAM_USER_ID y AGRO_INSTAGRAM_ACCESS_TOKEN para
aceptar (sin ellas, la invitación queda para el barrido de agro).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import requests

from utils.config import get
from utils.logger import get_logger
from utils.secciones import _CAMPO_FUERTE, _hay, _norm

logger = get_logger("colab_agro")

CUENTA_AGRO = "diarioyradioagro"
DIARIO_IG = "17841405971293744"           # @diarioyradio: solo ESA cuenta invita (no la radio, no agro)
GRAPH = "https://graph.facebook.com/v24.0"
AMBIGUAS = {"productor-", "rendimiento-", "cultivo-", "hectarea-", "chacra-", "sembr-", "faena"}
FIRMES = tuple(t for t in _CAMPO_FUERTE if t not in AMBIGUAS)
ESPERAS_ACEPTAR = (5, 10, 20, 30, 60)


# 25/11/2026 (el editor: «sacá las colaboraciones automatizadas con la otra cuenta»): ninguna colab automática
# entre @diarioyradio y @diarioyradioagro. COLAB_AGRO=1 las vuelve a prender.
def activo() -> bool:
    return (get("COLAB_AGRO") or "0").strip().lower() in ("1", "true", "si", "sí", "on")


def es_del_campo(texto: str) -> bool:
    t = _norm(texto or "")
    return len(_hay(t, FIRMES)) >= 1


def colaborador_para(texto: str, user_id: str = "", siempre: bool = False) -> str:
    """«diarioyradioagro» si este posteo de @diarioyradio es del campo (o `siempre`: la nota fija de agro del
    Drive, ver notas_siempre); "" si no corresponde."""
    if not activo():
        return ""
    if (user_id or get("INSTAGRAM_USER_ID") or "").strip() != (get("DIARIO_IG_ID") or DIARIO_IG):
        return ""
    if siempre:
        return CUENTA_AGRO
    # 24/11/2026 (el editor: «poné en colab solo las de la página 6 de diarioyradio, porque hoy metiste una de un
    # médico que nada que ver»): por palabras del campo queda APAGADA; solo la nota 6 del Drive (`siempre`).
    # COLAB_AGRO_TEXTO=1 la vuelve a prender.
    if (get("COLAB_AGRO_TEXTO") or "0").strip() != "1":
        return ""
    return CUENTA_AGRO if es_del_campo(texto) else ""


# 23/11/2026 (el editor: «poné en colab las notas de la página 6 cargadas en el drive de diarioyradio con
# diarioyradioagro»; aclaró: «siempre la nota 6»): la nota del Drive cuyo archivo empieza con «6-» sale en colab
# con agro TODOS los días, sea del tema que sea. COLAB_AGRO_NOTAS cambia los números («6,9»; 0 = ninguna).
def notas_siempre() -> set:
    crudo = (get("COLAB_AGRO_NOTAS") or "6").strip()
    return {int(x) for x in re.findall(r"\d+", crudo) if int(x) > 0}


def numero_de_nota(nombre: str) -> int:
    """El número con que empieza el archivo de la nota («6-nota ornella mm.docx» → 6), o 0."""
    m = re.match(r"\s*0*(\d+)\s*[-_. ]", Path(str(nombre or "")).name)
    return int(m.group(1)) if m else 0


def colab_de_nota(note: dict):
    """Lo que va en `colab=` al publicar la nota del Drive en Instagram: "siempre" para la nota fija de agro,
    True (decide el texto: ¿es del campo?) para las demás."""
    archivo = (note or {}).get("docx") or (note or {}).get("file") or ""
    n = numero_de_nota(archivo)
    return "siempre" if n and n in notas_siempre() else True


def con_colab(data: dict, colaborador: str) -> dict:
    return dict(data, collaborators=json.dumps([colaborador])) if colaborador else data


def aceptar(media_id: str, esperas=ESPERAS_ACEPTAR, dormir=time.sleep) -> str:
    """Con la cuenta de @diarioyradioagro acepta la invitación a colaborar en `media_id`. Nunca lanza:
    devuelve "aceptada", "pendiente" o "sin credenciales"."""
    uid = (get("AGRO_INSTAGRAM_USER_ID") or "").strip()
    tok = (get("AGRO_INSTAGRAM_ACCESS_TOKEN") or get("AGRO_FACEBOOK_PAGE_ACCESS_TOKEN") or "").strip()
    if not (uid and tok and media_id):
        logger.info("[colab agro] sin credenciales de agro acá: la acepta el barrido de la corrida de agro")
        return "sin credenciales"
    url = f"{GRAPH}/{uid}/collaboration_invites"
    ultimo = ""
    for espera in (0,) + tuple(esperas):
        if espera:
            dormir(espera)
        try:
            r = requests.get(url, params={"access_token": tok}, timeout=30)
            if not r.ok:
                ultimo = f"HTTP {r.status_code}"
                continue
            ids = {str(x.get("media_id") or x.get("id") or "") for x in (r.json() or {}).get("data") or []}
            if str(media_id) not in ids:
                ultimo = "la invitación todavía no aparece"
                continue
            r = requests.post(url, params={"access_token": tok},
                              data={"media_id": str(media_id), "accept": "true"}, timeout=30)
            if r.ok and (r.json() or {}).get("success", True):
                logger.info(f"[colab agro] @{CUENTA_AGRO} aceptó la colaboración en {media_id}")
                return "aceptada"
            ultimo = f"HTTP {r.status_code}"
        except (requests.RequestException, ValueError) as e:
            ultimo = type(e).__name__
    logger.warning(f"[colab agro] no se pudo aceptar al momento ({ultimo}); la acepta la corrida de agro")
    return "pendiente"
