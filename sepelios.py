"""Scraping y publicación de SEPELIOS (necrológicas) de Chivilcoy.

Fuentes:
  - Empresa San Nicolás:  https://empresasannicolas.com/sepelios/
  - Grupo Visión:         https://grupovisionargentina.com/  (bloque "Necrológicas")

Reglas (definidas con el usuario):
  - Solo Chivilcoy (se descartan otras localidades).
  - Solo los NUEVOS del día (anti-repetición por nombre normalizado en .sepelios.json).
  - Un único posteo + historia que resume todos los sepelios nuevos del día.
  - Publica en Wix (muro/blog), Facebook e Instagram (muro + historia).
"""
import json
import re
import unicodedata
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

from platforms import facebook, instagram, wix
from story_image import compose_sepelios_feed, compose_sepelios_story
from utils.config import get
from utils.logger import get_logger
from utils.scrape import fetch_text

logger = get_logger("sepelios")

LEDGER = Path(__file__).parent / ".sepelios.json"

URL_SANNICOLAS = "https://empresasannicolas.com/sepelios/"
URL_VISION = "https://grupovisionargentina.com/"

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
         "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _fecha_larga(d: date) -> str:
    return f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]}"


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def _es_chivilcoy(texto: str) -> bool:
    return "chivilcoy" in _norm(texto)


def _limpiar_nombre(nombre: str) -> str:
    """Quita 'Q.E.P.D.', cruces y espacios repetidos; deja el nombre prolijo."""
    n = re.sub(r"q\.?\s*e\.?\s*p\.?\s*d\.?", "", nombre, flags=re.I)
    n = n.replace("†", "").replace("Vda.", "vda.")
    n = re.sub(r"\s+", " ", n).strip(" .-")
    # Capitalización tipo título, respetando partículas comunes
    return _titulo(n)


def _titulo(n: str) -> str:
    chicas = {"de", "del", "la", "las", "los", "y", "vda", "da"}
    out = []
    for w in n.split():
        wl = w.lower()
        out.append(wl if wl.strip(".") in chicas else wl.capitalize())
    return " ".join(out)


# ── Scrapers ─────────────────────────────────────────────────────────────────
def scrap_sannicolas() -> list[dict]:
    """Cada tarjeta: div.slide-content con h3 (nombre) + 'Falleció en {lugar} el {fecha}'."""
    out = []
    try:
        soup = BeautifulSoup(fetch_text(URL_SANNICOLAS), "lxml")
    except Exception as e:
        logger.error(f"No se pudo leer San Nicolás: {e}")
        return out
    for card in soup.select("div.slide-content"):
        h3 = card.find("h3")
        if not h3:
            continue
        nombre = h3.get_text(" ", strip=True)
        texto = card.get_text(" ", strip=True)
        if not _es_chivilcoy(texto):
            continue
        out.append({"nombre": _limpiar_nombre(nombre), "fuente": "San Nicolás"})
    logger.info(f"San Nicolás: {len(out)} sepelio(s) de Chivilcoy")
    return out


def scrap_vision() -> list[dict]:
    """Bloque 'Necrológicas' en la home: '† Q.E.P.D. / Nombre / dd/mm/aaaa - Servicio Localidad.'"""
    out = []
    try:
        soup = BeautifulSoup(fetch_text(URL_VISION), "lxml")
    except Exception as e:
        logger.error(f"No se pudo leer Visión: {e}")
        return out

    nodo = soup.find(string=re.compile("Necrol", re.I))
    cont = nodo.find_parent() if nodo else None
    for _ in range(4):
        if cont and cont.parent:
            cont = cont.parent
    texto = cont.get_text("\n", strip=True) if cont else ""

    # Las entradas vienen como: Nombre \n  dd/mm/aaaa - Servicio Localidad.
    lineas = [l.strip() for l in texto.split("\n") if l.strip()]
    for i, l in enumerate(lineas):
        m = re.match(r"(\d{2}/\d{2}/\d{4})\s*-\s*Servicio\s+(.+?)\.?$", l, re.I)
        if not m:
            continue
        localidad = m.group(2)
        if not _es_chivilcoy(localidad):
            continue
        # el nombre es la línea anterior que no sea "† Q.E.P.D." ni encabezado
        nombre = ""
        for j in range(i - 1, -1, -1):
            cand = lineas[j]
            if re.search(r"q\.?e\.?p\.?d", cand, re.I) or "necrol" in _norm(cand) or cand == "Cerrar":
                continue
            nombre = cand
            break
        if nombre:
            out.append({"nombre": _limpiar_nombre(nombre), "fuente": "Visión"})
    logger.info(f"Visión: {len(out)} sepelio(s) de Chivilcoy")
    return out


def recolectar() -> list[dict]:
    """Junta ambas fuentes y deduplica por nombre normalizado."""
    todos = scrap_sannicolas() + scrap_vision()
    vistos, unicos = set(), []
    for s in todos:
        k = _norm(s["nombre"])
        if k and k not in vistos:
            vistos.add(k)
            unicos.append(s)
    return unicos


# ── Ledger ───────────────────────────────────────────────────────────────────
def _leer_ledger() -> set[str]:
    try:
        if LEDGER.exists():
            return set(json.loads(LEDGER.read_text(encoding="utf-8")))
    except Exception:
        logger.warning("No se pudo leer .sepelios.json; se asume vacío.")
    return set()


def _guardar_ledger(claves: set[str]) -> None:
    # mantener acotado (últimos 400 nombres)
    LEDGER.write_text(json.dumps(sorted(claves)[-400:], ensure_ascii=False, indent=2),
                      encoding="utf-8")


def _platforms() -> list[str]:
    raw = get("STORIES_PLATFORMS") or "instagram,facebook"
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


# ── Orquestador ──────────────────────────────────────────────────────────────
def run_sepelios(dry_run: bool = False) -> None:
    modo = "SIMULACIÓN (dry-run)" if dry_run else "PUBLICACIÓN REAL"
    logger.info(f"=== Sepelios de Chivilcoy [{modo}] ===")

    sepelios = recolectar()
    if not sepelios:
        logger.info("No hay sepelios de Chivilcoy en las fuentes. Nada que publicar.")
        return

    ledger = _leer_ledger()
    nuevos = [s for s in sepelios if _norm(s["nombre"]) not in ledger]
    if not nuevos:
        logger.info(f"Los {len(sepelios)} sepelio(s) listados ya se publicaron antes. No hay nuevos hoy.")
        return

    logger.info(f"{len(nuevos)} sepelio(s) NUEVO(s) de Chivilcoy:")
    for s in nuevos:
        logger.info(f"   • {s['nombre']} ({s['fuente']})")

    fecha = _fecha_larga(date.today())
    nombres = [s["nombre"] for s in nuevos]

    # Leyenda (caption) sobria
    lineas = [f"🕯️ Sepelios — {fecha.capitalize()}", "", "Q.E.P.D."]
    lineas += [f"• {n}" for n in nombres]
    lineas += ["", "Diario La Campaña acompaña a las familias.",
               "Información: empresas Visión y San Nicolás."]
    caption = "\n".join(lineas)

    # Imágenes (muro + historia)
    try:
        feed_img = compose_sepelios_feed(nombres, fecha.capitalize())
        story_img = compose_sepelios_story(nombres, fecha.capitalize())
    except Exception as e:
        logger.error(f"No se pudieron componer las imágenes de sepelios: {e}")
        return

    plats = _platforms()
    algun_ok = False

    if dry_run:
        logger.info(f"   [dry-run] muro Wix/{'/'.join(plats)} y historia listos (NO se publica).")
        logger.info(f"   imágenes: {feed_img.name} / {story_img.name}")
        logger.info("   (dry-run) no se modifica el ledger.")
        logger.info("=== Sepelios: fin (dry-run) ===")
        return

    # 1) Wix (muro/blog)
    try:
        wix.publish(f"Sepelios — {fecha.capitalize()}", caption, feed_img, page=0)
        algun_ok = True
        logger.info("   [wix] sepelios publicados OK")
    except Exception as e:
        logger.error(f"   [wix] FALLÓ: {e}")

    # 2) Muro Facebook / Instagram
    feed_fns = {"facebook": lambda: facebook.publish(caption, feed_img),
                "instagram": lambda: instagram.publish(caption, feed_img)}
    for name in plats:
        fn = feed_fns.get(name)
        if not fn:
            continue
        try:
            fn(); algun_ok = True
            logger.info(f"   [{name}] muro OK")
        except Exception as e:
            logger.error(f"   [{name}] muro FALLÓ: {e}")

    # 3) Historia Facebook / Instagram
    story_fns = {"instagram": lambda: instagram.publish_story(story_img),
                 "facebook": lambda: facebook.publish_story(story_img)}
    for name in plats:
        fn = story_fns.get(name)
        if not fn:
            continue
        try:
            fn(); algun_ok = True
            logger.info(f"   [{name}] historia OK")
        except Exception as e:
            logger.error(f"   [{name}] historia FALLÓ: {e}")

    if algun_ok:
        for n in nombres:
            ledger.add(_norm(n))
        _guardar_ledger(ledger)
        logger.info("Sepelios registrados (no se repetirán).")
    else:
        logger.error("No se pudo publicar en ninguna red — se reintentará la próxima corrida.")

    logger.info("=== Sepelios: fin ===")
