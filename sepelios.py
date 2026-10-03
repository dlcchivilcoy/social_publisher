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
import html
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


def _detalle(texto: str, nombre: str, limit: int = 130) -> str:
    """Resumen breve a partir del texto de la tarjeta (sin el nombre ni Q.E.P.D.)."""
    t = (texto or "").replace(nombre, " ")
    t = re.sub(r"q\.?\s*e\.?\s*p\.?\s*d\.?", "", t, flags=re.I)
    t = t.replace("†", " ").replace("Sepelios", " ").replace("Necrológicas", " ")
    t = re.sub(r"\s+", " ", t).strip(" .-·,")
    if len(t) > limit:
        t = t[:limit].rsplit(" ", 1)[0].rstrip(" .,;:") + "…"
    return t


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
        out.append({"nombre": _limpiar_nombre(nombre),
                    "detalle": _detalle(texto, nombre),
                    "fuente": "San Nicolás"})
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
            loc = re.sub(r"\s+", " ", localidad).strip(" .")
            out.append({"nombre": _limpiar_nombre(nombre),
                        "detalle": f"Sepelio en {loc} · {m.group(1)}",
                        "fuente": "Visión"})
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


# ── Interruptor general ───────────────────────────────────────────────────────
# Los sepelios quedaron DESACTIVADOS por pedido del diario: no se publican más
# en NINGUNA red ni en Wix (ni posteo ni historia). La tarea programada de
# Windows también está deshabilitada. Para reactivarlos, poné SEPELIOS_ACTIVO=1
# en el .env (o cambiá este valor) y volvé a habilitar la tarea.
def _sepelios_activo() -> bool:
    return (get("SEPELIOS_ACTIVO") or "0").strip().lower() in ("1", "true", "si", "sí", "on")


# ── Historia diaria de Instagram (pedido 2026-10-03) ─────────────────────────
# Va dentro de la corrida de las 08:00 (carrusel_tapa_farmacias), entre el clima y las
# farmacias. Es APARTE de run_sepelios (posteo + Wix), que sigue apagado con
# SEPELIOS_ACTIVO=0. Lee las dos empresas con los mismos patrones que la página
# /sepelios de la web (diario_web/src/lib/sepelios.js), que traen la FECHA.
HISTORIA_LEDGER = Path(__file__).parent / ".sepelios_historia.json"
HISTORIA_DIAS = 2  # además de no repetir, solo entran los de hoy y los 2 días anteriores

_RE_SN = re.compile(r"<h3[^>]*>\s*<a\s+href=['\"][^'\"]+['\"][^>]*>([\s\S]*?)</a>\s*</h3>\s*"
                    r"<span[^>]*ciudad-fecha-deceso[^>]*>([\s\S]*?)</span>", re.I)
_RE_VI = re.compile(r'<span class="homenaje__nombre">([\s\S]*?)</span>\s*'
                    r'<span class="homenaje__fallecimiento">([\s\S]*?)</span>', re.I)
_CHICAS = {"de", "del", "la", "las", "los", "y", "vda", "vda.", "da"}


def _txt(fragmento: str) -> str:
    sin_tags = re.sub(r"<[^>]+>", " ", re.sub(r"<br\s*/?>", " ", fragmento or "", flags=re.I))
    return re.sub(r"\s+", " ", html.unescape(sin_tags)).strip()


def _nombre_lindo(n: str) -> str:
    """'TRUSSO WALTER DARIO (TONI) Q.E.P.D.' → 'Trusso Walter Dario (Toni)'."""
    n = re.sub(r"q\.?\s*e\.?\s*p\.?\s*d\.?", "", n, flags=re.I).replace("†", "")
    n = re.sub(r"\s+", " ", n).strip(" .-")
    out = []
    for w in n.split(" "):
        low = w.lower()
        out.append(low if low in _CHICAS else re.sub(r"[^\W\d_]", lambda m: m.group().upper(), low, count=1))
    return " ".join(out)


def _clave_persona(nombre: str) -> str:
    """La misma persona puede figurar en las dos empresas con nombre y apellido en otro
    orden: se compara el conjunto de palabras."""
    return " ".join(sorted(w for w in re.split(r"[^a-z]+", _norm(nombre))
                           if len(w) > 2 and w not in _CHICAS))


def recolectar_con_fecha() -> list[dict]:
    """[{nombre, fecha (date), empresa, clave}] de Chivilcoy, sin repetidos, más nuevos
    primero. Si una empresa no responde, sigue con la otra."""
    lista = []
    try:
        for m in _RE_SN.finditer(fetch_text(URL_SANNICOLAS)):
            f = re.search(r"falleci[oó]\s+en\s+(.+?)\s+el\s+(\d{2})/(\d{2})/(\d{4})", _txt(m.group(2)), re.I)
            if f and _es_chivilcoy(f.group(1)):
                lista.append({"nombre": _nombre_lindo(_txt(m.group(1))), "empresa": "Empresa San Nicolás",
                              "fecha": date(int(f.group(4)), int(f.group(3)), int(f.group(2)))})
    except Exception as e:
        logger.error(f"No se pudo leer San Nicolás: {e}")
    try:
        for m in _RE_VI.finditer(fetch_text(URL_VISION)):
            f = re.search(r"(\d{2})/(\d{2})/(\d{4})\s*-\s*Servicio\s+(.+?)\.?$", _txt(m.group(2)), re.I)
            if f and _es_chivilcoy(f.group(4)):
                lista.append({"nombre": _nombre_lindo(_txt(m.group(1))), "empresa": "Grupo Visión",
                              "fecha": date(int(f.group(3)), int(f.group(2)), int(f.group(1)))})
    except Exception as e:
        logger.error(f"No se pudo leer Visión: {e}")
    vistos, unicos = set(), []
    for s in sorted(lista, key=lambda s: s["fecha"], reverse=True):
        s["clave"] = _clave_persona(s["nombre"])
        if s["clave"] and s["clave"] not in vistos:
            vistos.add(s["clave"])
            unicos.append(s)
    return unicos


def historia_activa() -> bool:
    """SEPELIOS_HISTORIA=0 apaga solo la historia diaria (no toca SEPELIOS_ACTIVO)."""
    return (get("SEPELIOS_HISTORIA") or "1").strip().lower() in ("1", "true", "si", "sí", "on")


def _leer_ledger_historia() -> set[str]:
    try:
        if HISTORIA_LEDGER.exists():
            return set(json.loads(HISTORIA_LEDGER.read_text(encoding="utf-8")))
    except Exception:
        logger.warning("No se pudo leer .sepelios_historia.json; se asume vacío.")
    return set()


def pendientes_historia(hoy: date) -> list[dict]:
    """Los sepelios que todavía no salieron en una historia, de hoy o de los
    HISTORIA_DIAS días anteriores (sin el tope de días, el primer día saldría el listado
    viejo entero de las dos empresas)."""
    ya = _leer_ledger_historia()
    desde = date.fromordinal(hoy.toordinal() - HISTORIA_DIAS)
    return [s for s in recolectar_con_fecha() if s["clave"] not in ya and s["fecha"] >= desde]


def marcar_historia(sepelios: list[dict]) -> None:
    """Registra los que ya salieron en la historia (no se repiten otro día)."""
    claves = _leer_ledger_historia() | {s["clave"] for s in sepelios}
    HISTORIA_LEDGER.write_text(json.dumps(sorted(claves)[-400:], ensure_ascii=False, indent=2),
                               encoding="utf-8")


# ── Orquestador ──────────────────────────────────────────────────────────────
def run_sepelios(dry_run: bool = False) -> None:
    modo = "SIMULACIÓN (dry-run)" if dry_run else "PUBLICACIÓN REAL"
    logger.info(f"=== Sepelios de Chivilcoy [{modo}] ===")

    if not _sepelios_activo():
        logger.info("Sepelios DESACTIVADOS (SEPELIOS_ACTIVO!=1): no se publica nada "
                    "(ni Wix, ni Facebook, ni Instagram, ni historias). Se omite.")
        return

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

    # Leyenda (caption) sobria, con un breve resumen de cada uno
    lineas = [f"🕯️ Sepelios — {fecha.capitalize()}", "", "Q.E.P.D.", ""]
    for s in nuevos:
        lineas.append(f"• {s['nombre']}")
        det = (s.get("detalle") or "").strip()
        if det:
            lineas.append(f"   {det}")
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
        desc_seo = f"Sepelios de Chivilcoy — {fecha.capitalize()}: " + ", ".join(nombres)
        # seccion="inicio" = solo la portada, sin sección: los sepelios nunca estuvieron
        # en Locales y no tiene sentido que la sección se llene de avisos fúnebres.
        wix.publish(f"Sepelios — {fecha.capitalize()}", caption, feed_img, page=0,
                    description=desc_seo, seccion="inicio")
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
