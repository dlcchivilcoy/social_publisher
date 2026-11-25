# -*- coding: utf-8 -*-
"""Pruebas sin red (25/11/2026): limpieza de los Release de hosting y reintento ante el 422 (Release lleno).

    python utils/test_release_limpieza.py
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from utils import release_limpieza as RL  # noqa: E402

bien = mal = 0


def chequear(nombre, cond):
    global bien, mal
    if cond:
        bien += 1
    else:
        mal += 1
        print("FALLA:", nombre)


AHORA = datetime(2026, 11, 25, 19, 0, tzinfo=timezone.utc)


def asset(i, dias_atras, nombre=None):
    c = (AHORA - timedelta(days=dias_atras)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": i, "name": nombre or f"a{i}.mp4", "created_at": c}


# Pocos y nuevos: no borra nada.
pocos = [asset(i, i * 0.5) for i in range(10)]
chequear("pocos y recientes: nada", RL.elegir(pocos, 14, 30, ahora=AHORA) == [])
# Los de más de 14 días, los más viejos primero y con tope por vez.
mezcla = [asset(i, d) for i, d in enumerate([1, 20, 30, 2, 15, 40])]
b = RL.elegir(mezcla, 14, 2, ahora=AHORA)
chequear("viejos primero, hasta el tope por vez", [a["id"] for a in b] == [5, 2])
chequear("sin tope: los cuatro de más de 14 días",
         {a["id"] for a in RL.elegir(mezcla, 14, 30, ahora=AHORA)} == {1, 2, 4, 5})
# El mismo nombre se borra aunque sea nuevo (GitHub no admite duplicados).
chequear("mismo nombre: se borra", [a["id"] for a in RL.elegir(pocos, 14, 30, ahora=AHORA, nombre="a3.mp4")] == [3])
# Release lleno (1.000) con todo de menos de 14 días: libera lugar con los de más de un día.
lleno = [asset(i, 0.2 if i < 50 else 2 + i / 100) for i in range(1000)]
b = RL.elegir(lleno, 14, 30, ahora=AHORA)
chequear("lleno: deja 150 libres", len(lleno) - len(b) == 1000 - RL.MARGEN)
chequear("lleno: nunca borra los de las últimas 24 h", all(a["id"] >= 50 for a in b))
chequear("lleno: borra los más viejos", max(a["id"] for a in b) == 999)

# limpiar() con red simulada: pagina y borra.
paginas = [lleno[i:i + 100] for i in range(0, 1000, 100)]


def get(url, headers=None, params=None, timeout=None):
    p = params["page"]
    return mock.Mock(ok=True, json=lambda: paginas[p - 1] if p <= len(paginas) else [])


borrados = []
with mock.patch.object(RL, "datetime", wraps=datetime) as dt:
    dt.now = lambda tz=None: AHORA
    n = RL.limpiar("o/r", {}, 1, dias=14, get=get, delete=lambda url, **k: borrados.append(url) or mock.Mock(ok=True))
chequear("limpiar: borra lo elegido", n == 150 and len(borrados) == 150)
chequear("limpiar: si falla la red, sigue sin explotar",
         RL.limpiar("o/r", {}, 1, dias=14, get=mock.Mock(side_effect=requests.ConnectionError())) == 0)

# video_host: el 422 (Release lleno) limpia fuerte y reintenta.
from utils import video_host as VH  # noqa: E402

tmp = Path(__file__).resolve().parent / "_prueba_422.mp4"
tmp.write_bytes(b"x")
posts, limpiezas = [], []


def post(url, **k):
    posts.append(url)
    r = mock.Mock()
    if len(posts) == 1:
        resp = mock.Mock(status_code=422)
        r.raise_for_status.side_effect = requests.HTTPError("422 Unprocessable Entity", response=resp)
    else:
        r.raise_for_status.return_value = None
        r.json.return_value = {"browser_download_url": "https://x/ok.mp4"}
    return r


try:
    with mock.patch.object(VH, "_token", return_value="t"), \
            mock.patch.object(VH, "_ensure_release", return_value={"id": 1, "assets": []}), \
            mock.patch.object(VH.requests, "post", side_effect=post), \
            mock.patch("utils.release_limpieza.limpiar", side_effect=lambda *a, **k: limpiezas.append(k)), \
            mock.patch.object(VH.time, "sleep"):
        url = VH.upload_reel(tmp)
finally:
    tmp.unlink()
chequear("video: tras el 422 sube igual", url == "https://x/ok.mp4" and len(posts) == 2)
chequear("video: la 2ª limpieza es fuerte y con el nombre",
         [k["max_borrar"] for k in limpiezas] == [30, 200] and limpiezas[0]["nombre"] == "_prueba_422.mp4"
         and limpiezas[0]["dias"] == 14)

print(f"--- {bien}/{bien + mal} correctos ---")
sys.exit(1 if mal else 0)
