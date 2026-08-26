"""Registro de TRABAJOS del desgrabador en Supabase — la "cola" del Bloque B.

Es la red de seguridad y el panel: cada video/foto-nota que entra queda anotado con su estado
(`en_proceso` → `hecho` / `error`), cuántos intentos lleva y el último error. Con eso:
  - el VIGÍA (`main.py --watchdog`) avisa por mail lo que quedó trabado o falla seguido;
  - se puede ver de un vistazo qué entró, qué salió y qué se rompió.

NO reemplaza al disparador rápido (Apps Script cada 1 min): lo acompaña.

REGLA DE ORO: esto NUNCA puede romper una publicación. Todo es best-effort — si Supabase no está
o falla, se loguea y se sigue como si nada (las funciones devuelven False, jamás lanzan).
"""
from __future__ import annotations

import requests

from utils.config import get
from utils.logger import get_logger

logger = get_logger("trabajos")

TABLA = "trabajos"


def _sb():
    url, key = (get("SUPABASE_URL") or "").rstrip("/"), get("SUPABASE_SERVICE_KEY")
    return (url, key) if url and key else None


def _headers(key: str) -> dict:
    return {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def activo() -> bool:
    return _sb() is not None


def empezar(clave: str, tipo: str, args: str = "") -> bool:
    """Anota que un trabajo ARRANCÓ (o que se reintenta): estado `en_proceso` y +1 intento.
    Si ya existía, conserva `creado` y suma el intento (así se ve lo que falla seguido)."""
    sb = _sb()
    if not sb or not clave:
        return False
    url, key = sb
    try:
        # Leemos los intentos previos para incrementarlos (PostgREST no hace x = x + 1 en upsert).
        prev = requests.get(f"{url}/rest/v1/{TABLA}", params={"clave": f"eq.{clave}", "select": "intentos"},
                            headers=_headers(key), timeout=20)
        intentos = (prev.json()[0]["intentos"] + 1) if (prev.ok and prev.json()) else 1
        r = requests.post(f"{url}/rest/v1/{TABLA}", params={"on_conflict": "clave"},
                          headers={**_headers(key), "Prefer": "resolution=merge-duplicates"},
                          json={"clave": clave, "tipo": tipo, "args": args[:500],
                                "estado": "en_proceso", "intentos": intentos}, timeout=20)
        return r.ok
    except Exception as e:  # noqa: BLE001
        logger.debug(f"No pude anotar el inicio de «{clave}»: {e}")
        return False


def terminar(clave: str, ok: bool = True, detalle: str = "") -> bool:
    """Cierra el trabajo: `hecho` o `error` (con el detalle recortado)."""
    sb = _sb()
    if not sb or not clave:
        return False
    url, key = sb
    try:
        r = requests.patch(f"{url}/rest/v1/{TABLA}", params={"clave": f"eq.{clave}"},
                           headers=_headers(key),
                           json={"estado": "hecho" if ok else "error",
                                 "detalle": (detalle or "")[:500]}, timeout=20)
        return r.ok
    except Exception as e:  # noqa: BLE001
        logger.debug(f"No pude cerrar «{clave}»: {e}")
        return False


def trabados(minutos: int = 30) -> list:
    """Trabajos que quedaron `en_proceso` hace más de `minutos` (la corrida murió sin cerrarlos)
    o que terminaron en `error`. Es lo que mira el vigía. Lista vacía si Supabase no está."""
    sb = _sb()
    if not sb:
        return []
    url, key = sb
    from datetime import datetime, timedelta, timezone
    corte = (datetime.now(timezone.utc) - timedelta(minutes=minutos)).isoformat()
    out = []
    try:
        for params in (
            {"estado": "eq.en_proceso", "actualizado": f"lt.{corte}"},
            {"estado": "eq.error"},
        ):
            r = requests.get(f"{url}/rest/v1/{TABLA}",
                             params={**params, "select": "clave,tipo,estado,intentos,detalle,actualizado",
                                     "order": "actualizado.desc", "limit": "50"},
                             headers=_headers(key), timeout=20)
            if r.ok:
                out.extend(r.json())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"No pude leer los trabajos trabados: {e}")
    return out


def resumen_hoy() -> dict:
    """Conteo por estado de las últimas 24 h (para el mail del vigía)."""
    sb = _sb()
    if not sb:
        return {}
    url, key = sb
    from datetime import datetime, timedelta, timezone
    desde = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    try:
        r = requests.get(f"{url}/rest/v1/{TABLA}",
                         params={"actualizado": f"gte.{desde}", "select": "estado", "limit": "500"},
                         headers=_headers(key), timeout=20)
        if not r.ok:
            return {}
        cuenta: dict = {}
        for fila in r.json():
            cuenta[fila["estado"]] = cuenta.get(fila["estado"], 0) + 1
        return cuenta
    except Exception as e:  # noqa: BLE001
        logger.warning(f"No pude armar el resumen de trabajos: {e}")
        return {}
