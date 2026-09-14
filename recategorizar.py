"""Pone en su sección las notas del blog que quedaron sin una (o revisa todas).

PARA QUÉ: el 17/6/2026 el sistema dejó de saber a qué sección mandar cada nota (la
sacaba del número de página del diario de papel, que desapareció) y durante tres meses
publicó 886 notas solo en «Inicio». Este comando es el que las reparó, y el que sirve
si vuelve a pasar o si se suma una sección nueva.

    python recategorizar.py --dry              ver qué haría, sin tocar nada
    python recategorizar.py                    arreglar las notas SIN sección
    python recategorizar.py --todas --dry      revisar también las que ya tienen una
    python recategorizar.py --desde 2026-06-01 acotar por fecha
    python recategorizar.py --limite 50        tocar como mucho 50

Es reanudable: `.recategorizado.json` guarda lo hecho, así una corrida cortada sigue
donde iba. Cambiar la categoría NO republica la nota: se verificó que
`firstPublishedDate`, `lastPublishedDate`, el slug, la portada, el cuerpo y las vistas
quedan idénticos.
"""
import argparse
import json
import time
from pathlib import Path

import requests

from utils.config import get, load_config
from utils.logger import get_logger
from utils import secciones as S

logger = get_logger("recategorizar")

LEDGER = Path(__file__).parent / ".recategorizado.json"
QUERY_URL = "https://www.wixapis.com/blog/v3/posts/query"
DRAFTS_URL = "https://www.wixapis.com/blog/v3/draft-posts"
LOTE_IA = 20          # cuántas notas se le preguntan a Gemini de una sola vez
PAUSA = 0.6           # segundos entre notas, para no apurar la API de Wix


def _headers() -> dict:
    return {"Authorization": get("WIX_API_KEY"), "wix-site-id": get("WIX_SITE_ID"),
            "Content-Type": "application/json"}


def _bajar_notas(desde: str, hasta_n: int) -> list:
    """Notas publicadas, de la más nueva a la más vieja, hasta `desde` (o `hasta_n`)."""
    headers, notas, offset = _headers(), [], 0
    while offset < hasta_n:
        cuerpo = {"query": {"paging": {"limit": 100, "offset": offset},
                            "sort": [{"fieldName": "firstPublishedDate", "order": "DESC"}]}}
        r = requests.post(QUERY_URL, headers=headers, json=cuerpo, timeout=60)
        r.raise_for_status()
        pagina = r.json().get("posts", [])
        if not pagina:
            break
        for p in pagina:
            fecha = (p.get("firstPublishedDate") or "")[:10]
            if desde and fecha and fecha < desde:
                return notas
            notas.append({"id": p.get("id"), "fecha": fecha, "titulo": p.get("title") or "",
                          "texto": (p.get("excerpt") or "").replace("\n", " ")[:600],
                          "cats": list(p.get("categoryIds") or [])})
        offset += 100
    return notas


def _seccion_de(cats: list) -> str:
    """Qué sección tiene hoy esa nota ('' si solo está en Inicio)."""
    inicio = S.id_inicio()
    for slug in S.SLUGS:
        propios = [c for c in S.ids_de_categoria(slug) if c != inicio]
        if any(c in cats for c in propios):
            return slug
    return ""


def _decidir(notas: list) -> None:
    """Le pone a cada nota su sección propuesta y si la decisión es FIRME.

    Firme = la regla estaba segura o contestó la IA. Una regla dudosa sin respuesta de
    la IA (porque Gemini está sin cupo, por ejemplo) NO alcanza para pisar una sección
    que ya existe: se probó y la primera nota que tocaba —un partido de la Sub-19 ya
    puesto en Deportes— se volvía a Locales.

    Se le pregunta a la IA EN LOTE (una llamada cada 20 notas, no una por nota)."""
    dudosas = []
    for n in notas:
        if S.es_servicio(n["titulo"]):        # sepelios/farmacias: nunca van a una sección
            n["propuesta"], n["firme"] = S.SIN_SECCION, True
            continue
        slug, seguro, _ = S.por_reglas(n["titulo"], n["texto"])
        n["propuesta"], n["firme"] = slug, seguro
        if not seguro:
            dudosas.append(n)
    for i in range(0, len(dudosas), LOTE_IA):
        tanda = dudosas[i:i + LOTE_IA]
        res = S.por_ia([{"titulo": n["titulo"], "texto": n["texto"]} for n in tanda],
                       timeout=90)
        for j, n in enumerate(tanda):
            if j in res:
                n["propuesta"], n["firme"] = res[j], True
        logger.info(f"IA: lote {i // LOTE_IA + 1} -> {len(res)}/{len(tanda)} resueltas")


def _aplicar(post_id: str, slug: str) -> None:
    headers = _headers()
    r = requests.patch(f"{DRAFTS_URL}/{post_id}", headers=headers, timeout=45, json={
        "draftPost": {"id": post_id, "categoryIds": S.ids_de_categoria(slug)},
        "fieldMask": ["categoryIds"]})
    if not r.ok:
        raise RuntimeError(f"PATCH {r.status_code}: {r.text[:160]}")
    p = requests.post(f"{DRAFTS_URL}/{post_id}/publish", headers=headers, json={}, timeout=45)
    if not p.ok:
        raise RuntimeError(f"PUBLISH {p.status_code}: {p.text[:160]}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Recategoriza notas del blog por contenido.")
    ap.add_argument("--dry", action="store_true", help="mostrar, sin tocar Wix")
    ap.add_argument("--todas", action="store_true",
                    help="revisar también las notas que YA tienen sección")
    ap.add_argument("--desde", default="", help="fecha mínima, YYYY-MM-DD")
    ap.add_argument("--limite", type=int, default=0, help="tope de notas a modificar")
    ap.add_argument("--max-notas", type=int, default=3000,
                    help="cuántas notas del archivo se revisan como mucho")
    args = ap.parse_args()

    load_config()
    hechas = set(json.loads(LEDGER.read_text(encoding="utf-8"))) if LEDGER.exists() else set()

    notas = _bajar_notas(args.desde, args.max_notas)
    logger.info(f"{len(notas)} nota(s) revisadas del archivo.")

    candidatas = []
    for n in notas:
        if n["id"] in hechas:
            continue
        actual = _seccion_de(n["cats"])
        if actual and not args.todas:
            continue
        n["actual"] = actual or S.SIN_SECCION
        candidatas.append(n)
    if not candidatas:
        logger.info("No hay nada que recategorizar.")
        return

    logger.info(f"{len(candidatas)} nota(s) a decidir…")
    _decidir(candidatas)
    # Pisar una sección que YA existe pide una decisión firme; llenar una vacía, no.
    tenia = lambda n: n["actual"] != S.SIN_SECCION
    cambios = [n for n in candidatas if n["propuesta"] != n["actual"]
               and (n["firme"] or not tenia(n))]
    dudadas = sum(1 for n in candidatas
                  if n["propuesta"] != n["actual"] and tenia(n) and not n["firme"])
    if dudadas:
        logger.info(f"{dudadas} nota(s) con sección ya puesta se dejan como están "
                    f"(la decisión no fue firme).")
    if args.limite:
        cambios = cambios[:args.limite]

    reparto = {}
    for n in cambios:
        reparto[n["propuesta"]] = reparto.get(n["propuesta"], 0) + 1
    logger.info("Reparto propuesto: " + ", ".join(
        f"{S.ETIQUETA.get(s, 'Solo Inicio')} {reparto[s]}"
        for s in list(S.SLUGS) + [S.SIN_SECCION] if reparto.get(s)))

    ok = fallos = 0
    for i, n in enumerate(cambios, 1):
        etiqueta = (f"[{i}/{len(cambios)}] {n['fecha']} "
                    f"{S.ETIQUETA.get(n['propuesta'], 'Solo Inicio'):<11} {n['titulo'][:60]}")
        if args.dry:
            logger.info("(dry) " + etiqueta)
            continue
        try:
            _aplicar(n["id"], n["propuesta"])
            hechas.add(n["id"])
            LEDGER.write_text(json.dumps(sorted(hechas)), encoding="utf-8")
            ok += 1
            logger.info(etiqueta)
        except Exception as e:
            fallos += 1
            logger.error(f"FALLÓ {etiqueta}: {e}")
        time.sleep(PAUSA)

    if args.dry:
        logger.info(f"=== Simulación: {len(cambios)} nota(s) cambiarían de sección ===")
    else:
        logger.info(f"=== {ok} nota(s) recategorizadas · {fallos} con error ===")


if __name__ == "__main__":
    main()
