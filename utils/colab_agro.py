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
import time

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


def activo() -> bool:
    return (get("COLAB_AGRO") or "1").strip().lower() not in ("0", "false", "no", "off")


def es_del_campo(texto: str) -> bool:
    t = _norm(texto or "")
    return len(_hay(t, FIRMES)) >= 1


def colaborador_para(texto: str, user_id: str = "") -> str:
    """«diarioyradioagro» si este posteo de @diarioyradio es del campo; "" si no corresponde."""
    if not activo():
        return ""
    if (user_id or get("INSTAGRAM_USER_ID") or "").strip() != (get("DIARIO_IG_ID") or DIARIO_IG):
        return ""
    return CUENTA_AGRO if es_del_campo(texto) else ""


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
