"""Limpieza de los GitHub Release que usamos como hosting temporal (reel-latest, img-latest).

25/11/2026: GitHub admite como mucho 1.000 archivos por Release. `reel-latest` nunca borraba
nada (había videos desde junio), llegó a 1.000 y la subida del video de un corresponsal falló con
«422 Unprocessable Entity». Estos archivos son temporales: Instagram y Facebook bajan el video en
minutos y Wix lo copia a su Media Manager, así que borrar los viejos no rompe ninguna nota.

`limpiar()` borra los de más de `dias` días y, si igual quedan cerca del tope, también los más
viejos que tengan más de un día. Si algo falla, sigue: es limpieza, no es lo importante.
"""
from datetime import datetime, timedelta, timezone

import requests

API = "https://api.github.com"
TOPE = 1000           # máximo de archivos que GitHub acepta en un Release
MARGEN = 150          # se intenta dejar siempre al menos este lugar libre


def _creado(a: dict) -> datetime:
    return datetime.fromisoformat(a["created_at"].replace("Z", "+00:00"))


def listar(repo: str, h: dict, release_id: int, get=requests.get) -> list:
    todos = []
    for pagina in range(1, 12):
        r = get(f"{API}/repos/{repo}/releases/{release_id}/assets", headers=h,
                params={"per_page": 100, "page": pagina}, timeout=30)
        if not r.ok:
            break
        lote = r.json()
        if not lote:
            break
        todos.extend(lote)
    return todos


def elegir(assets: list, dias: float, max_borrar: int, ahora: datetime = None, tope: int = TOPE,
           margen: int = MARGEN, nombre: str = "") -> list:
    """Qué borrar (sin red): los de otro día con el mismo `nombre`, los de más de `dias` días (los
    más viejos primero, hasta `max_borrar`) y, si así y todo no queda `margen` de lugar, los más
    viejos que tengan más de un día (nunca los de las últimas 24 h, que pueden estar en uso)."""
    ahora = ahora or datetime.now(timezone.utc)
    orden = sorted(assets, key=_creado)
    borrar = [a for a in orden if nombre and a.get("name") == nombre]
    limite = ahora - timedelta(days=dias)
    for a in orden:
        if len(borrar) >= max_borrar:
            break
        if _creado(a) < limite and a not in borrar:
            borrar.append(a)
    falta = len(assets) - len(borrar) - (tope - margen)
    if falta > 0:
        ayer = ahora - timedelta(days=1)
        extra = [a for a in orden if a not in borrar and _creado(a) < ayer]
        borrar += extra[:falta]
    return borrar


def limpiar(repo: str, h: dict, release_id: int, dias: float, max_borrar: int = 30, nombre: str = "",
            logger=None, get=requests.get, delete=requests.delete) -> int:
    try:
        assets = listar(repo, h, release_id, get=get)
        borrar = elegir(assets, dias, max_borrar, nombre=nombre)
        hechos = 0
        for a in borrar:
            r = delete(f"{API}/repos/{repo}/releases/assets/{a['id']}", headers=h, timeout=30)
            if getattr(r, "ok", True):
                hechos += 1
        if hechos and logger:
            logger.info(f"Limpieza del Release: {hechos} archivo(s) viejo(s) borrado(s) "
                        f"(había {len(assets)} de {TOPE}).")
        return hechos
    except Exception as e:  # noqa: BLE001
        if logger:
            logger.warning(f"Limpieza del Release: {e}")
        return 0
