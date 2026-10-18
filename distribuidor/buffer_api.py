"""Cliente mínimo de la API GraphQL de Buffer (https://api.buffer.com).

Ahorra cuota a propósito: el plan Free da 250 solicitudes por día y 3.000 por mes, y
varias mutaciones en UNA solicitud cuentan como una (verificado el 11/10/2026). Por eso
`crear_lote` manda hasta 10 createPost (y deletePost) juntos con alias.

Cada respuesta trae los encabezados `RateLimit` (`r` = restantes, `t` = segundos hasta que
se renueva la ventana); `cuota` guarda lo último que se vio para que el distribuidor sepa
cuánto le queda sin gastar una consulta en averiguarlo.
"""
from __future__ import annotations

import json
import re
import time

import requests

from utils.config import get

from . import config

TIMEOUT = 60
LOTE_MAX = 10


class BufferError(Exception):
    """Error de la API entera (no de una publicación puntual)."""

    def __init__(self, mensaje: str, pasajero: bool = False, http: int = 0):
        super().__init__(mensaje)
        self.pasajero = pasajero
        self.http = http


def _clave() -> str:
    k = (get("BUFFER_API_KEY") or "").strip()
    if not k:
        raise BufferError("falta BUFFER_API_KEY en el .env (doble clic en «Cargar clave Buffer.bat»)")
    return k


def _leer_cuota(headers) -> dict:
    """{"15m": (restantes, seg), "24h": (...), "30d": (...)} desde los encabezados RateLimit."""
    out = {}
    crudo = ", ".join(v for k, v in headers.items() if k.lower() == "ratelimit")
    for m in re.finditer(r'"(\d+)-in-(\w+)";\s*r=(\d+);\s*t=(\d+)', crudo):
        ventana = {"15min": "15m", "1day": "24h", "30days": "30d"}.get(m.group(2), m.group(2))
        out[ventana] = {"restantes": int(m.group(3)), "segundos": int(m.group(4)),
                        "tope": int(m.group(1))}
    return out


cuota: dict = {}
solicitudes = 0


def gql(query: str, variables: dict | None = None, intentos: int = 3) -> dict:
    """Una solicitud. Devuelve `data`. Reintenta solo lo pasajero (red, 5xx, 429 corto)."""
    global solicitudes
    ultimo = None
    for i in range(intentos):
        try:
            r = requests.post(config.BUFFER_API, timeout=TIMEOUT,
                              headers={"Authorization": f"Bearer {_clave()}",
                                       "Content-Type": "application/json"},
                              json={"query": query, "variables": variables or {}})
        except requests.RequestException as e:
            ultimo = BufferError(f"sin conexión con Buffer: {e}", pasajero=True)
            time.sleep(5 * (i + 1))
            continue
        if r.status_code != 429:
            solicitudes += 1          # un 429 no consume cuota (doc de Buffer)
        cuota.update(_leer_cuota(r.headers))
        if r.status_code == 429:
            espera = int(r.headers.get("Retry-After") or 0)
            ventana = ""
            try:
                ventana = r.json()["errors"][0]["extensions"].get("window", "")
            except Exception:  # noqa: BLE001
                pass
            # Solo vale la pena esperar si es la ventana de 15 minutos y es poco.
            if ventana in ("15m", "") and 0 < espera <= 90 and i < intentos - 1:
                time.sleep(espera)
                continue
            raise BufferError(f"cuota de la API agotada (ventana {ventana or '?'}, "
                              f"se renueva en {espera // 60} min)", pasajero=True, http=429)
        if r.status_code >= 500:
            ultimo = BufferError(f"Buffer devolvió HTTP {r.status_code}", pasajero=True,
                                 http=r.status_code)
            time.sleep(5 * (i + 1))
            continue
        try:
            datos = r.json()
        except ValueError:
            raise BufferError(f"respuesta ilegible de Buffer (HTTP {r.status_code})",
                              http=r.status_code)
        if r.status_code in (401, 403):
            raise BufferError("Buffer rechazó la API key (¿la borraron o venció?)", http=r.status_code)
        if datos.get("errors") and not datos.get("data"):
            raise BufferError("Buffer: " + "; ".join(e.get("message", "?") for e in datos["errors"]),
                              http=r.status_code)
        return datos.get("data") or {}
    raise ultimo or BufferError("Buffer no respondió", pasajero=True)


# ------------------------------------------------------------------------------- lecturas
def estado_general(desde_iso: str, hasta_iso: str) -> dict:
    """UNA solicitud con todo lo que hace falta para sincronizar:
    canales (¿desconectados?), límites diarios y las publicaciones de la ventana."""
    canales = [config.canal(p) for p in config.PLATAFORMAS]
    q = """query($o:OrganizationId!, $c:[ChannelId!]!, $d:DateTime, $h:DateTime){
      channels(input:{organizationId:$o}){ id service name isDisconnected isLocked isQueuePaused }
      dailyPostingLimits(input:{channelIds:$c}){ channelId isAtLimit limit scheduled sent }
      posts(first:100, input:{organizationId:$o,
            filter:{channelIds:$c, dueAt:{start:$d, end:$h}}}){
        edges{ node{ id channelId status dueAt sentAt externalLink text
                     error{ message rawError } } }
      }
    }"""
    d = gql(q, {"o": config.ORGANIZACION, "c": canales, "d": desde_iso, "h": hasta_iso})
    posts = [e["node"] for e in ((d.get("posts") or {}).get("edges") or [])]
    return {"canales": d.get("channels") or [], "limites": d.get("dailyPostingLimits") or [],
            "posts": posts}


def metricas_enviados(desde_iso: str, hasta_iso: str) -> dict:
    """{buffer_id: {tipo: valor}} de lo ENVIADO en la ventana (17/10/2026, panel de la etapa 7). Lo que
    Buffer tenga: si el plan no trae métricas, la lista viene vacía (o la consulta falla y quien
    llama lo anota como «no disponible»)."""
    canales = [config.canal(p) for p in config.PLATAFORMAS]
    q = """query($o:OrganizationId!, $c:[ChannelId!]!, $d:DateTime, $h:DateTime){
      posts(first:100, input:{organizationId:$o, filter:{channelIds:$c, status:[sent], dueAt:{start:$d, end:$h}}}){
        edges{ node{ id metricsUpdatedAt metrics{ type value } } }
      }
    }"""
    d = gql(q, {"o": config.ORGANIZACION, "c": canales, "d": desde_iso, "h": hasta_iso})
    return {e["node"]["id"]: {m["type"]: m["value"] for m in e["node"].get("metrics") or []}
            for e in ((d.get("posts") or {}).get("edges") or [])}


# ------------------------------------------------------------------------------ escrituras
_SEL = ("{ __typename ... on PostActionSuccess{ post{ id status dueAt } } "
        "... on MutationError{ message } }")


def _entrada(pub: dict) -> dict:
    """Arma el CreatePostInput de una publicación ya decidida."""
    plataforma = pub["plataforma"]
    entrada = {
        "channelId": config.canal(plataforma),
        "text": pub["texto"],
        "schedulingType": "automatic",
        "mode": "customScheduled",
        "dueAt": pub["dueAt"],
        "needsApproval": False,
        "source": "diario-distribuidor",
        "assets": [],
    }
    if pub.get("video_url"):
        video = {"url": pub["video_url"]}
        if plataforma == "tiktok":
            video["metadata"] = {"thumbnailOffset": int(pub.get("portada_ms") or 1000)}
        entrada["assets"] = [{"video": video}]
    elif pub.get("imagen_url"):
        entrada["assets"] = [{"image": {"url": pub["imagen_url"]}}]
    if plataforma == "tiktok":
        entrada["metadata"] = {"tiktok": {"isAiGenerated": False}}
    if pub.get("ahora"):
        # Publicar en el momento (pruebas aprobadas a mano).
        entrada["mode"] = "shareNow"
        entrada.pop("dueAt", None)
    if pub.get("borrador"):
        # Para revisar en Buffer antes de publicar (pruebas): queda en Borradores.
        entrada.update(mode="addToQueue", saveToDraft=True)
        entrada.pop("dueAt", None)
    return entrada


def crear_lote(pubs: list, borrar: list | None = None) -> dict:
    """Programa hasta LOTE_MAX publicaciones (y borra `borrar`) en UNA solicitud.

    Devuelve {clave: {"ok": bool, "id": str, "error": str, "tope": bool}} por publicación.
    Cada publicación es independiente: si una falla, las otras quedan programadas.
    """
    if not pubs and not borrar:
        return {}
    if len(pubs) > LOTE_MAX:
        raise ValueError(f"máximo {LOTE_MAX} publicaciones por solicitud")
    partes, variables, decl = [], {}, []
    # Los borrados VAN PRIMERO: las mutaciones corren en orden, y así el lugar que liberan
    # (10 programadas por canal en el plan Free) ya está libre para las altas que siguen.
    for n, pid in enumerate(borrar or []):
        decl.append(f"$b{n}: PostId!")
        variables[f"b{n}"] = pid
        partes.append(f"b{n}: deletePost(input: {{id: $b{n}}}) "
                      "{ __typename ... on MutationError{ message } }")
    for n, pub in enumerate(pubs):
        decl.append(f"$i{n}: CreatePostInput!")
        variables[f"i{n}"] = _entrada(pub)
        partes.append(f"c{n}: createPost(input: $i{n}) {_SEL}")
    q = f"mutation({', '.join(decl)}) {{ {' '.join(partes)} }}"
    d = gql(q, variables, intentos=1)   # una mutación NO se reintenta a ciegas (duplicaría)
    out = {}
    for n, pub in enumerate(pubs):
        res = d.get(f"c{n}") or {}
        tipo = res.get("__typename", "")
        if tipo == "PostActionSuccess":
            out[pub["clave"]] = {"ok": True, "id": res["post"]["id"], "error": "", "tope": False}
        else:
            msg = res.get("message") or f"respuesta inesperada ({tipo or 'vacía'})"
            out[pub["clave"]] = {
                "ok": False, "id": "", "error": msg,
                "tope": tipo == "LimitReachedError" or "limit" in msg.lower(),
                "pasajero": tipo in ("UnexpectedError", "RestProxyError"),
            }
    out["_borrados"] = [pid for n, pid in enumerate(borrar or [])
                        if (d.get(f"b{n}") or {}).get("__typename") == "DeletePostSuccess"]
    return out


def resumen_cuota() -> str:
    if not cuota:
        return "sin datos de cuota todavía"
    return " · ".join(f"{v}: quedan {cuota[v]['restantes']}/{cuota[v]['tope']}"
                      for v in ("15m", "24h", "30d") if v in cuota)


if __name__ == "__main__":
    from utils.config import load_config
    load_config()
    print(json.dumps(estado_general("2026-01-01T00:00:00Z", "2027-01-01T00:00:00Z"),
                     indent=1, ensure_ascii=False)[:3000])
    print(resumen_cuota())
