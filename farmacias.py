"""Farmacias de turno de Chivilcoy → muro (Wix/FB/IG) + historia.

- El cronograma mensual vive en turnos_farmacias.json (cargado leyendo la imagen
  oficial, porque el OCR de esa imagen no es confiable).
- Cada día busca la terna de HOY, le pega dirección y teléfono (scrapeadas del
  listado de dechivilcoy.com.ar/farmacias/) y arma el posteo + historia.
- Si llega un mes sin cargar (o cambia la imagen del cronograma), AVISA en el log
  y NO publica datos sin verificar.
"""
import difflib
import json
import re
import unicodedata
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

from platforms import facebook, instagram
from story_image import compose_farmacias_feed, compose_farmacias_story
from utils.config import get
from utils.logger import get_logger
from utils.scrape import fetch_text

logger = get_logger("farmacias")

URL = "https://dechivilcoy.com.ar/farmacias/"
CRONOGRAMA = Path(__file__).parent / "turnos_farmacias.json"
LEDGER = Path(__file__).parent / ".farmacias.json"

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
         "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _fecha_larga(d: date) -> str:
    return f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]}"


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip().lower()


def _platforms() -> list[str]:
    raw = get("STORIES_PLATFORMS") or "instagram,facebook"
    return [p.strip().lower() for p in raw.split(",") if p.strip()]


# ── Listado (directorio) → dirección + teléfono ───────────────────────────────
def scrap_listado() -> dict[str, dict]:
    """Devuelve {nombre_normalizado: {'nombre','direccion','telefono'}}."""
    salida = {}
    try:
        soup = BeautifulSoup(fetch_text(URL), "lxml")
    except Exception as e:
        logger.error(f"No se pudo leer el listado de farmacias: {e}")
        return salida
    tabla = soup.find("table")
    contenedor = tabla if tabla else soup
    # Los datos vienen como <li>Nombre | Dirección | Teléfono</li>
    for li in contenedor.find_all("li"):
        texto = li.get_text(" ", strip=True).replace("\xa0", " ")
        partes = [p.strip() for p in texto.split("|")]
        if len(partes) < 3:
            continue
        nombre, direccion, telefono = partes[0], partes[1], partes[2]
        if not nombre or "listado" in _norm(nombre):
            continue
        salida[_norm(nombre)] = {"nombre": nombre, "direccion": direccion, "telefono": telefono}
    logger.info(f"Listado de farmacias: {len(salida)} farmacias")
    return salida


# ── Cronograma del mes (MAIL del Colegio, con fallback a cache/JSON curado) ───
CACHE = Path(__file__).parent / ".farmacias_cache.json"


def _cargar_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _guardar_cache_mes(mes_key: str, dias: dict) -> None:
    cache = _cargar_json(CACHE)
    cache[mes_key] = {"dias": {str(k): v for k, v in dias.items()},
                      "fuente": "mail", "actualizado": date.today().isoformat()}
    try:
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"No se pudo guardar el cache de farmacias: {e}")


def _terna_desde_local(hoy: date) -> list | None:
    """Cache del mail (.farmacias_cache.json) y, si no, el JSON curado a mano."""
    mes_key = f"{hoy.year}-{hoy.month:02d}"
    for origen in (CACHE, CRONOGRAMA):
        dias = (_cargar_json(origen).get(mes_key) or {}).get("dias", {})
        nombres = dias.get(str(hoy.day))
        if nombres:
            return nombres
    return None


def _info_farmacia(listado: dict, nom: str) -> dict:
    """Dirección/teléfono del directorio; tolera variantes de escritura (fuzzy)."""
    key = _norm(nom)
    if key in listado:
        return listado[key]
    cerca = difflib.get_close_matches(key, list(listado.keys()), n=1, cutoff=0.82)
    return listado[cerca[0]] if cerca else {}


def terna_de_hoy(hoy: date):
    """Devuelve (nombres, aviso, es_cambio).

    Preferencia: 1) CAMBIO del día (mail), 2) cronograma mensual del mail (Excel,
    refresca el cache), 3) cache del mail o turnos_farmacias.json (curado).
    """
    mes_key = f"{hoy.year}-{hoy.month:02d}"
    try:
        import farmacias_mail as fmail
        cambio = fmail.cambio_del_dia(hoy)
        if cambio:
            logger.info(f"CAMBIO de turno del día (mail): {', '.join(cambio)}")
            return cambio, None, True
        dias = fmail.cronograma_mensual(hoy.year, hoy.month)
        if dias:
            _guardar_cache_mes(mes_key, dias)
            nombres = dias.get(hoy.day) or dias.get(str(hoy.day))
            if nombres:
                return nombres, None, False
    except Exception as e:
        logger.warning(f"No se pudo leer el mail de farmacias ({e}); uso el cronograma local.")

    nombres = _terna_desde_local(hoy)
    if nombres:
        return nombres, None, False
    return None, (f"No hay datos de farmacias para {MESES[hoy.month-1]} {hoy.year}: no se pudo "
                  f"leer el mail y no hay cache ni cronograma cargado."), False


# ── Ledger (no repetir el mismo día) ──────────────────────────────────────────
def _ya_publicado_hoy(hoy: date) -> bool:
    try:
        if LEDGER.exists():
            return json.loads(LEDGER.read_text(encoding="utf-8")).get("fecha") == hoy.isoformat()
    except Exception:
        pass
    return False


def _marcar(hoy: date, nombres: list[str]) -> None:
    LEDGER.write_text(json.dumps({"fecha": hoy.isoformat(), "farmacias": nombres},
                                 ensure_ascii=False, indent=2), encoding="utf-8")


def farmacias_feed_de_hoy(hoy: date):
    """Para el CARRUSEL: arma la imagen de farmacias (1080x1350) y las líneas de
    texto del día, reutilizando toda la lógica de turnos. Devuelve
    (feed_img:Path, lineas_cap:list[str], nombres:list[str], es_cambio:bool) o
    (None, aviso:str, None, False) si no hay datos."""
    nombres, aviso, es_cambio = terna_de_hoy(hoy)
    if not nombres:
        return None, aviso, None, False
    listado = scrap_listado()
    fecha = _fecha_larga(hoy)
    sufijo_cambio = " (CAMBIO)" if es_cambio else ""
    items, lineas_cap = [], []
    for i, nom in enumerate(nombres):
        info = _info_farmacia(listado, nom)
        direccion = info.get("direccion", "")
        telefono = info.get("telefono", "")
        ultima = (i == len(nombres) - 1)
        horario = "8:30 a 22 hs" if ultima else "8:30 a 8:30 hs (24 hs)"
        sub = " · ".join([x for x in [direccion, (f"Tel {telefono}" if telefono else "")] if x])
        items.append({"main": nom.upper(), "sub2": f"Horario: {horario}", "sub": sub})
        det = f"💊 {nom}"
        if direccion:
            det += f" — {direccion}"
        if telefono:
            det += f" — Tel: {telefono}"
        det += f"\n   🕒 Horario: {horario}"
        lineas_cap.append(det)
    feed_img = compose_farmacias_feed(items, fecha.capitalize() + sufijo_cambio)
    return feed_img, lineas_cap, nombres, es_cambio


# ── Orquestador ──────────────────────────────────────────────────────────────
def run_farmacias(dry_run: bool = False) -> None:
    modo = "SIMULACIÓN (dry-run)" if dry_run else "PUBLICACIÓN REAL"
    hoy = date.today()
    logger.info(f"=== Farmacias de turno [{modo}] — {hoy.isoformat()} ===")

    if not dry_run and _ya_publicado_hoy(hoy):
        logger.info("Las farmacias de turno de hoy ya se publicaron. Se omite.")
        return

    nombres, aviso, es_cambio = terna_de_hoy(hoy)
    if not nombres:
        logger.error(aviso)  # sin datos → avisa y NO publica
        return

    listado = scrap_listado()
    fecha = _fecha_larga(hoy)
    sufijo_cambio = " (CAMBIO)" if es_cambio else ""

    items, lineas_cap = [], []
    for i, nom in enumerate(nombres):
        info = _info_farmacia(listado, nom)
        direccion = info.get("direccion", "")
        telefono = info.get("telefono", "")
        # Las 2 primeras de la terna están de turno las 24 hs (8:30 a 8:30 del día
        # siguiente); la última, de 8:30 a 22 hs.
        ultima = (i == len(nombres) - 1)
        horario = "8:30 a 22 hs" if ultima else "8:30 a 8:30 hs (24 hs)"
        sub = " · ".join([x for x in [direccion, (f"Tel {telefono}" if telefono else "")] if x])
        # sub2 = horario, resaltado en color (verde) debajo del nombre.
        items.append({"main": nom.upper(), "sub2": f"Horario: {horario}", "sub": sub})
        det = f"💊 {nom}"
        if direccion:
            det += f" — {direccion}"
        if telefono:
            det += f" — Tel: {telefono}"
        det += f"\n   🕒 Horario: {horario}"
        lineas_cap.append(det)

    cabecera = ("⚠️ *CAMBIO de turno de hoy*\n\n" if es_cambio else "")
    caption = (cabecera + "💊 Farmacias de turno — " + fecha.capitalize() + "\n\n"
               + "\n".join(lineas_cap)
               + "\n\nLas dos primeras están de turno las 24 hs; la última, hasta las 22 hs.")

    logger.info(f"Farmacias de hoy: {', '.join(nombres)}")
    for l in lineas_cap:
        logger.info(f"   {l}")

    try:
        feed_img = compose_farmacias_feed(items, fecha.capitalize() + sufijo_cambio)
        story_img = compose_farmacias_story(items, fecha.capitalize() + sufijo_cambio)
    except Exception as e:
        logger.error(f"No se pudieron componer las imágenes de farmacias: {e}")
        return

    if dry_run:
        logger.info(f"   [dry-run] muro {'/'.join(_platforms())} + historia listos (NO se publica, NO va a Wix).")
        logger.info(f"   imágenes: {feed_img.name} / {story_img.name}")
        logger.info("=== Farmacias: fin (dry-run) ===")
        return

    plats = _platforms()
    algun_ok = False

    # Las farmacias de turno NO se publican en Wix; solo en redes sociales (FB/IG).
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
        _marcar(hoy, nombres)
        logger.info("Farmacias registradas como publicadas hoy.")
    else:
        logger.error("No se pudo publicar en ninguna red — se reintentará la próxima corrida.")

    logger.info("=== Farmacias: fin ===")
