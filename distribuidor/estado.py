"""Cola propia del distribuidor: `state/distribuidor.json` (se commitea al repo).

Dos tablas:
  - contenidos:     {id: evento normalizado}            — lo que llegó de cada sistema.
  - publicaciones:  {"id|plataforma": publicación}       — una fila por contenido y red.

La clave "id|plataforma" es la IDEMPOTENCIA: si un sistema vuelve a mandar el mismo
contenido (reintento, corrida repetida), ya existe y no se encola de nuevo. Además se guarda
`vistos` (id → fecha) por 30 días, así un contenido podado de la cola tampoco vuelve a entrar.

Estados de una publicación:
  pendiente   → espera en la cola propia (todavía no está en Buffer)
  enviando    → se está mandando a Buffer en esta corrida (si la corrida muere acá, la
                siguiente busca en Buffer por canal + dueAt antes de reenviar: no duplica)
  programado  → Buffer la tiene, con su `buffer_id` y `dueAt`
  enviado     → Buffer confirmó que salió (con `link` cuando lo da)
  error       → Buffer o la red la rechazaron; `error` dice por qué
  vencido     → pasó su vencimiento sin lugar para salir (queda informado, no se borra)
  descartado  → no corresponde publicarla (regla editorial, video inválido, cupo del día)
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ARCHIVO = RAIZ / "state" / "distribuidor.json"
PANEL = RAIZ / "state" / "distribuidor_panel.md"
BUZON = RAIZ / "state" / "buzon"

ACTIVOS = ("pendiente", "enviando", "programado")
FINALES = ("enviado", "error", "vencido", "descartado")


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def de_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def vacio() -> dict:
    return {"version": 1, "contenidos": {}, "publicaciones": {}, "vistos": {},
            "cuota": {}, "ultima_sync": "", "avisos": {}, "canales": {}, "registro": []}


def cargar(ruta: Path | None = None) -> dict:
    ruta = ruta or ARCHIVO
    if not ruta.exists():
        return vacio()
    try:
        d = json.loads(ruta.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        # Un archivo roto NO puede hacer que se republique todo: se aparta y se frena.
        raise RuntimeError(f"{ruta} está dañado; revisalo antes de seguir (no se publica nada)")
    base = vacio()
    base.update(d)
    return base


def guardar(est: dict, ruta: Path | None = None) -> None:
    ruta = ruta or ARCHIVO
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=ruta.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(est, f, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, ruta)


def clave(cid: str, plataforma: str) -> str:
    return f"{cid}|{plataforma}"


def anotar(est: dict, evento: str, **datos) -> None:
    """Registro de lo que pasó (se guardan los últimos 600 eventos)."""
    est["registro"].append({"cuando": iso(ahora()), "evento": evento, **datos})
    del est["registro"][:-600]


def podar(est: dict, dias: int = 10) -> int:
    """Saca lo terminado hace más de `dias` (el panel ya lo contó); `vistos` dura 30 días."""
    limite = ahora() - timedelta(days=dias)
    fuera = 0
    for k, p in list(est["publicaciones"].items()):
        cuando = de_iso(p.get("actualizado") or p.get("creado"))
        if p.get("estado") in FINALES and cuando and cuando < limite:
            del est["publicaciones"][k]
            fuera += 1
    usados = {k.split("|", 1)[0] for k in est["publicaciones"]}
    for cid in list(est["contenidos"]):
        if cid not in usados:
            del est["contenidos"][cid]
    limite_vistos = ahora() - timedelta(days=30)
    for cid, f in list(est["vistos"].items()):
        if (de_iso(f) or ahora()) < limite_vistos:
            del est["vistos"][cid]
    return fuera
