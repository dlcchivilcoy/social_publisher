"""Para los sistemas que corren en ESTE repo (el bot): dejar un contenido en el buzón.

Un archivo por contenido en `state/buzon/` (lo commitea el paso «Guardar registros» del
workflow). El distribuidor lo levanta en su próxima pasada. Nunca tira excepción: si algo
falla, el sistema que llama sigue publicando en sus redes como siempre.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from utils.logger import get_logger

logger = get_logger("distribuidor_buzon")
BUZON = Path(__file__).resolve().parent.parent / "state" / "buzon"


def _nombre(cid: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", cid)[:150] + ".json"


def dejar(evento: dict) -> bool:
    try:
        if not evento.get("id") or not evento.get("origen"):
            raise ValueError("el evento necesita id y origen")
        evento.setdefault("generado", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"))
        BUZON.mkdir(parents=True, exist_ok=True)
        destino = BUZON / _nombre(evento["id"])
        if destino.exists():
            return True          # idempotente: ya estaba
        destino.write_text(json.dumps(evento, ensure_ascii=False, indent=1), encoding="utf-8")
        logger.info(f"[distribuidor] en el buzón: {evento['id']}")
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[distribuidor] no pude dejar {evento.get('id')!r} en el buzón: {e}")
        return False
