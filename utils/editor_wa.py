"""APROBAR POR WHATSAPP (07/11/2026, pedido del usuario: «que la aprobación de los demás números que no
tienen publicación directa pasen a través de mi número»).

Lo que mandan los corresponsales le llega al EDITOR por WhatsApp, desde el número del bot:

- Revisión (números sin publicación directa): el reel, el título y tres botones: Publicar, Corregir y
  Descartar. Es además del mail de siempre, que queda de respaldo.
- Publicación directa (`CORRESPONSALES_DIRECTO`): «se publica en 10 minutos», con Frenar y Publicar ya.
  Si lo frena, pasa a revisión como cualquier otro.
- Al publicarse: «✅ Publicado» con el link.

Los botones los atiende el receptor de WhatsApp en Supabase (`corresponsales-webhook`): «Publicar» llama
al MISMO link firmado que el botón del mail (`/api/aprobar-video`), así que no hace falta ninguna clave
nueva. El estado de cada aviso vive en la tabla `corresponsales_aprobaciones`, y el número del editor en
`corresponsales_config` (clave `editor_wa`): NO en el código, porque el repositorio es público.

WhatsApp deja mandar mensajes con botones GRATIS si el editor le escribió al bot en las últimas 24 h. Si
no, Meta los rechaza (131047) y se usa una plantilla aprobada (`revision_corresponsal`,
`freno_corresponsal`), que se cobra como «utilidad» (unos centavos de dólar). `EDITOR_WA_PLANTILLAS=0`
deja solo lo gratis. `EDITOR_WA=0` apaga todo: queda el mail, como antes.

Nada de esto frena una publicación: si falla, queda en el log y sigue el mail.
"""
import re
import secrets
from datetime import datetime, timedelta, timezone

import requests

from utils.config import get
from utils.logger import get_logger

logger = get_logger("editor_wa")

G = "https://graph.facebook.com/v21.0"
MINUTOS_FRENO = 10
FUERA_DE_VENTANA = 131047      # Meta: pasaron más de 24 h desde el último mensaje del editor
PLANTILLA_REVISION = "revision_corresponsal"
PLANTILLA_FRENO = "freno_corresponsal"


def activo() -> bool:
    return (get("EDITOR_WA") or "1").strip() != "0"


# ── Supabase ──────────────────────────────────────────────────────────────────────────────────────
def _sb():
    url, key = (get("SUPABASE_URL") or "").rstrip("/"), (get("SUPABASE_SERVICE_KEY") or "").strip()
    if not url or not key:
        raise RuntimeError("faltan SUPABASE_URL / SUPABASE_SERVICE_KEY")
    return url + "/rest/v1", {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def numero_editor() -> str:
    """El WhatsApp del editor (con 549…), de `corresponsales_config`. "" si no está."""
    base, h = _sb()
    r = requests.get(f"{base}/corresponsales_config", headers=h,
                     params={"clave": "eq.editor_wa", "select": "valor"}, timeout=20)
    r.raise_for_status()
    filas = r.json()
    return re.sub(r"\D", "", filas[0]["valor"]) if filas else ""


def _guardar(fila: dict) -> None:
    base, h = _sb()
    r = requests.post(f"{base}/corresponsales_aprobaciones", headers=dict(h, Prefer="return=minimal"),
                      json=fila, timeout=20)
    r.raise_for_status()


def _leer(id_: str) -> dict | None:
    base, h = _sb()
    r = requests.get(f"{base}/corresponsales_aprobaciones", headers=h,
                     params={"id": f"eq.{id_}", "select": "*"}, timeout=20)
    r.raise_for_status()
    filas = r.json()
    return filas[0] if filas else None


def _cambiar(id_: str, desde: tuple, estado: str) -> bool:
    """Pasa el aviso a `estado` solo si está en uno de `desde` (así dos toques no hacen dos cosas)."""
    base, h = _sb()
    r = requests.patch(f"{base}/corresponsales_aprobaciones", headers=dict(h, Prefer="return=representation"),
                       params={"id": f"eq.{id_}", "estado": f"in.({','.join(desde)})"},
                       json={"estado": estado, "actualizado": datetime.now(timezone.utc).isoformat()}, timeout=20)
    r.raise_for_status()
    return bool(r.json())


def _ultimo_de(nombre: str) -> dict | None:
    base, h = _sb()
    r = requests.get(f"{base}/corresponsales_aprobaciones", headers=h,
                     params={"nombre": f"eq.{nombre}", "select": "*", "order": "creado.desc", "limit": "1"},
                     timeout=20)
    r.raise_for_status()
    filas = r.json()
    return filas[0] if filas else None


# ── WhatsApp ──────────────────────────────────────────────────────────────────────────────────────
class FueraDeVentana(Exception):
    pass


def _enviar(to: str, cuerpo: dict) -> str:
    token = (get("WHATSAPP_TOKEN") or "").strip()
    phone_id = (get("WHATSAPP_PHONE_NUMBER_ID") or "").strip()
    if not token or not phone_id:
        raise RuntimeError("faltan WHATSAPP_TOKEN / WHATSAPP_PHONE_NUMBER_ID")
    r = requests.post(f"{G}/{phone_id}/messages", headers={"Authorization": f"Bearer {token}"},
                      json=dict({"messaging_product": "whatsapp", "to": _a_54(to)}, **cuerpo), timeout=30)
    if r.status_code // 100 == 2:
        return ((r.json().get("messages") or [{}])[0]).get("id", "")
    err = (r.json() if r.content else {}).get("error", {})
    if err.get("code") == FUERA_DE_VENTANA:
        raise FueraDeVentana(err.get("message", ""))
    raise RuntimeError(f"WhatsApp {r.status_code}: {err.get('code')} {str(err.get('message', ''))[:200]}")


def _a_54(numero: str) -> str:
    """Los mensajes llegan de 549…, pero para escribir a un celular argentino va 54… sin el 9."""
    n = re.sub(r"\D", "", numero or "")
    return "54" + n[3:] if n.startswith("549") and len(n) == 13 else n


def _corto(texto: str, tope: int) -> str:
    t = " ".join((texto or "").split())
    return t if len(t) <= tope else t[: tope - 1].rstrip() + "…"


MAX_VIDEO = 15 * 1024 * 1024   # WhatsApp baja el video del encabezado hasta 16 MB; si pesa más, el
                                # mensaje se acepta y después NO LLEGA (falla en silencio)


def _video_apto(url: str) -> bool:
    try:
        r = requests.head(url, allow_redirects=True, timeout=20)
        largo = int(r.headers.get("Content-Length") or 0)
        return r.status_code // 100 == 2 and 0 < largo <= MAX_VIDEO
    except Exception:  # noqa: BLE001
        return False


def _botones(cuerpo: str, botones: list, video_url: str = "") -> dict:
    inter = {"type": "button", "body": {"text": _corto(cuerpo, 1000)},
             "action": {"buttons": [{"type": "reply", "reply": {"id": i, "title": t}} for i, t in botones]}}
    if video_url and _video_apto(video_url):
        inter["header"] = {"type": "video", "video": {"link": video_url}}
    return {"type": "interactive", "interactive": inter}


def _plantilla(nombre: str, titulo: str, quien: str, ids: list) -> dict:
    return {"type": "template", "template": {
        "name": nombre, "language": {"code": "es_AR"},
        "components": [{"type": "body", "parameters": [{"type": "text", "text": _corto(titulo, 120) or "sin título"},
                                                       {"type": "text", "text": _corto(quien, 60) or "un corresponsal"}]}]
        + [{"type": "button", "sub_type": "quick_reply", "index": str(i), "parameters": [{"type": "payload", "payload": p}]}
           for i, p in enumerate(ids)]}}


def _mandar(to: str, libre: dict, plantilla: dict) -> str:
    """El mensaje libre (gratis). Fuera de la ventana de 24 h, la plantilla (paga); si el video del
    encabezado no pasa, sin video."""
    try:
        return _enviar(to, libre)
    except FueraDeVentana:
        if (get("EDITOR_WA_PLANTILLAS") or "1").strip() == "0":
            raise
        logger.info("[editor] pasaron más de 24 h desde tu último mensaje al bot: va la plantilla (paga).")
        return _enviar(to, plantilla)
    except RuntimeError as e:
        if "header" not in libre.get("interactive", {}):
            raise
        logger.warning(f"[editor] no pude mandar el aviso con el video ({e}); va sin video.")
        sin_video = dict(libre, interactive={k: v for k, v in libre["interactive"].items() if k != "header"})
        return _mandar(to, sin_video, plantilla)


# ── Lo que usa el desgrabador ─────────────────────────────────────────────────────────────────────
def _links(nombre: str, tipo: str, draft_id: str) -> tuple:
    """(aprobar, corregir): los MISMOS links firmados de los botones del mail."""
    from urllib.parse import quote
    webapp, tok = get("APPROVE_WEBAPP_URL"), get("WEBAPP_TOKEN")
    if not webapp:
        return "", ""
    t = f"&token={quote(tok)}" if tok else ""
    kind = "&kind=folder" if tipo == "carpeta" else ""
    aprobar = f"{webapp}?action=approve&name={quote(nombre)}{kind}{t}"
    corregir = f"{webapp}?action=edit&draft={quote(draft_id)}{t}" if draft_id else ""
    return aprobar, corregir


def avisar_revision(*, nombre: str, tipo: str, titulo: str, resumen: str = "", quien: str = "",
                    reel_url: str = "", draft_id: str = "") -> str:
    """El aviso de revisión al editor, con Publicar, Corregir y Descartar. Devuelve el id o ""."""
    if not activo():
        return ""
    try:
        editor = numero_editor()
        aprobar, corregir = _links(nombre, tipo, draft_id)
        if not editor or not aprobar:
            logger.info("[editor] sin número de editor o sin link de aprobación: queda solo el mail.")
            return ""
        id_ = secrets.token_hex(4)
        _guardar({"id": id_, "nombre": nombre, "tipo": tipo, "titulo": _corto(titulo, 300), "estado": "pendiente",
                  "url_aprobar": aprobar, "url_corregir": corregir})
        cuerpo = (f"📰 *{_corto(titulo, 200)}*\n\n{_corto(resumen, 500)}\n\n"
                  f"📲 Lo mandó {quien or 'un corresponsal'}. ¿Lo publicamos?")
        libre = _botones(cuerpo, [(f"ap:{id_}", "✅ Publicar"), (f"co:{id_}", "✏️ Corregir"),
                                  (f"de:{id_}", "🗑️ Descartar")], reel_url)
        _mandar(editor, libre, _plantilla(PLANTILLA_REVISION, titulo, quien, [f"ap:{id_}", f"co:{id_}", f"de:{id_}"]))
        logger.info(f"[editor] aviso de revisión enviado por WhatsApp (id {id_}).")
        return id_
    except Exception as e:  # noqa: BLE001 — el mail ya salió: esto es un extra
        logger.warning(f"[editor] no pude avisar por WhatsApp ({e}); queda el mail.")
        return ""


def freno(*, nombre: str, tipo: str, titulo: str, quien: str = "", reel_url: str = "", draft_id: str = "",
          minutos: int = MINUTOS_FRENO) -> bool:
    """Publicación directa con freno: avisa «se publica en 10 minutos» y NO espera acá (una corrida que
    espera tranca la fila de videos de GitHub y puede hacer cancelar otra). El reloj vive en Supabase:
    `corresponsales_frenos_vencidos()` corre cada minuto y llama al link de aprobar de lo que venció; «Publicar
    ya» lo llama al toque y «Frenar» lo pasa a revisión (corresponsales-webhook).

    True = quedó avisado: el que llama NO publica. False = no se pudo avisar (o está apagado): el que
    llama publica directo, como antes."""
    if not activo():
        return False
    try:
        editor = numero_editor()
        aprobar, corregir = _links(nombre, tipo, draft_id)
        if not editor or not aprobar:
            return False
        id_ = secrets.token_hex(4)
        hasta = datetime.now(timezone.utc) + timedelta(minutes=minutos)
        _guardar({"id": id_, "nombre": nombre, "tipo": tipo, "titulo": _corto(titulo, 300), "estado": "esperando",
                  "url_aprobar": aprobar, "url_corregir": corregir, "publicar_a": hasta.isoformat()})
        cuerpo = (f"⏱️ En {minutos} minutos se publica directo:\n\n📰 *{_corto(titulo, 200)}*\n\n"
                  f"📲 Lo mandó {quien or 'un corresponsal'}. Si querés revisarlo antes, tocá *Frenar*.")
        libre = _botones(cuerpo, [(f"fr:{id_}", "✋ Frenar"), (f"ya:{id_}", "✅ Publicar ya")], reel_url)
        try:
            _mandar(editor, libre, _plantilla(PLANTILLA_FRENO, titulo, quien, [f"fr:{id_}", f"ya:{id_}"]))
        except Exception:
            _cambiar(id_, ("esperando",), "sin_aviso")     # que el reloj no lo publique: lo publica el que llama
            raise
        logger.info(f"[editor] freno de {minutos} min avisado por WhatsApp (id {id_}): lo publica Supabase.")
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[editor] no pude avisar el freno ({e}): se publica directo, como antes.")
        return False


def confirmar_publicado(nombre: str, link: str = "") -> None:
    """«✅ Publicado» al editor, si ese envío pasó por WhatsApp. Solo gratis: si la ventana cerró, nada."""
    if not activo():
        return
    try:
        fila = _ultimo_de(nombre)
        if not fila or fila.get("estado") in ("descartada", "publicada"):
            return
        _cambiar(fila["id"], ("pendiente", "esperando", "publicar", "frenada"), "publicada")
        editor = numero_editor()
        if editor:
            _enviar(editor, {"type": "text", "text": {"body": f"✅ Publicado: {_corto(fila.get('titulo', ''), 200)}"
                                                               + (f"\n{link}" if link else "")}})
    except FueraDeVentana:
        pass
    except Exception as e:  # noqa: BLE001
        logger.info(f"[editor] no pude confirmar la publicación por WhatsApp ({e}).")
