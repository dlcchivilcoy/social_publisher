"""Métricas de @diarioyradio (Instagram y Facebook): foto diaria e informe semanal por mail.

    python -m metricas_redes.run --foto                 # la foto del día (00:15, workflow metricas.yml)
    python -m metricas_redes.run --informe              # foto + informe de la última semana completa
    python -m metricas_redes.run --informe --simular    # no guarda ni manda: deja el informe en logs/
    python -m metricas_redes.run --informe --hoy 2026-11-04   # como si fuera ese día

Sin IA y sin tokens: ver METRICAS_REDES.md.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from utils.config import get, load_config

load_config()

from . import analisis, etiquetas, informe, recolector  # noqa: E402
from .meta import Meta  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--foto", action="store_true")
    ap.add_argument("--informe", action="store_true")
    ap.add_argument("--simular", action="store_true")
    ap.add_argument("--sin-foto", action="store_true", help="el informe con lo ya guardado, sin leer Meta")
    ap.add_argument("--hoy", default="")
    a = ap.parse_args(argv)
    if not (a.foto or a.informe):
        ap.error("decí --foto o --informe")
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    d = recolector.cargar()
    token = get("FACEBOOK_PAGE_ACCESS_TOKEN") or get("INSTAGRAM_ACCESS_TOKEN")
    if not a.sin_foto:
        if not token:
            print("Falta FACEBOOK_PAGE_ACCESS_TOKEN en el .env")
            return 1
        meta = Meta(token, invalidas=d["invalidas"])
        idx = etiquetas.indice(etiquetas.cargar_registros())
        print(f"Registros de policiales-bsas: {len(idx)} ids conocidos")
        hecho = recolector.foto(meta, d, get("INSTAGRAM_USER_ID"), get("FACEBOOK_PAGE_ID"), idx)
        print(f"Foto: {hecho['posteos_medidos']} posteos medidos, {hecho['historias']} historias, "
              f"{meta.llamadas} consultas a Meta")
        for f in hecho["fallas"][:15]:
            print(f"  ! {f}")
        if len(hecho["fallas"]) > 15:
            print(f"  ! … y {len(hecho['fallas']) - 15} más")
        if not a.simular:
            recolector.guardar(d)

    if a.informe:
        hoy = date.fromisoformat(a.hoy) if a.hoy else datetime.now(timezone.utc).astimezone(recolector.TZ_AR).date()
        inf = analisis.informe(d, hoy)
        html_ = informe.html_informe(inf)
        texto = informe.texto_informe(inf)
        adj = informe.csv_posteos(inf, d)
        nombre = f"posteos_{inf['ini'].isoformat()}.csv"
        print(informe.asunto(inf))
        print(texto)
        if a.simular:
            carpeta = Path(__file__).resolve().parent.parent / "logs"
            carpeta.mkdir(exist_ok=True)
            (carpeta / "informe_metricas.html").write_text(html_, encoding="utf-8")
            (carpeta / nombre).write_bytes(adj)
            print(f"(simulación) informe en {carpeta / 'informe_metricas.html'}")
        else:
            informe.enviar(informe.asunto(inf), html_, texto, adj, nombre)
            d["semanas"][inf["ini"].isoformat()] = {
                red: {"seguidores": r["cuenta"].get("seguidores"), "neto": r["cuenta"].get("neto"), "posteos": r["n"],
                      "rendimiento": r.get("rend_semana")} for red, r in inf["redes"].items()}
            recolector.guardar(d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
