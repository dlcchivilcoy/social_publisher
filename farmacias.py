"""Farmacias de turno de Chivilcoy → muro (Wix/FB/IG) + historia.

- El cronograma mensual vive en turnos_farmacias.json (cargado leyendo la imagen
  oficial, porque el OCR de esa imagen no es confiable).
- Cada día busca la terna de HOY, le pega dirección y teléfono (scrapeadas del
  listado de dechivilcoy.com.ar/farmacias/) y arma el posteo + historia.
- Si llega un mes sin cargar (o cambia la imagen del cronograma), AVISA en el log
  y NO publica datos sin verificar.
"""
import json
import re
import unicodedata
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup

from platforms import facebook, instagram, wix
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


# ── Cronograma del mes ────────────────────────────────────────────────────────
def _cargar_cronograma() -> dict:
    try:
        return json.loads(CRONOGRAMA.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error(f"No se pudo leer turnos_farmacias.json: {e}")
        return {}


def _imagen_cronograma_actual() -> str | None:
    """Busca en la página el nombre de archivo de la imagen del cronograma (para detectar cambios)."""
    try:
        soup = BeautifulSoup(fetch_text(URL), "lxml")
        for img in soup.find_all("img"):
            src = img.get("src", "")
            base = src.rsplit("/", 1)[-1]
            # El cronograma real se llama TURNOS-{MES}-{AÑO}.jpg (no el thumbnail "farmacias-de-turno-...")
            if base.upper().startswith("TURNOS") and base.lower().endswith((".jpg", ".jpeg", ".png")):
                return base
    except Exception:
        pass
    return None


def terna_de_hoy(hoy: date):
    """Devuelve (lista_de_nombres, aviso) para el día de hoy, o (None, motivo)."""
    data = _cargar_cronograma()
    mes_key = f"{hoy.year}-{hoy.month:02d}"
    mes = data.get(mes_key)
    if not mes:
        return None, (f"FALTA cargar el cronograma de farmacias para {MESES[hoy.month-1]} "
                      f"{hoy.year} (clave {mes_key}). Leé la imagen de {URL} y cargá turnos_farmacias.json.")
    dias = mes.get("dias", {})
    nombres = dias.get(str(hoy.day))
    if not nombres:
        return None, f"El cronograma de {mes_key} no tiene el día {hoy.day}."

    # Aviso si la imagen del cronograma en la web cambió respecto a la cargada.
    fuente_cargada = mes.get("fuente", "")
    actual = _imagen_cronograma_actual()
    if actual and fuente_cargada and _norm(actual) != _norm(fuente_cargada):
        logger.warning(f"⚠ La imagen del cronograma cambió en la web ({actual}) respecto a la "
                       f"cargada ({fuente_cargada}). Verificá turnos_farmacias.json.")
    return nombres, None


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


# ── Orquestador ──────────────────────────────────────────────────────────────
def run_farmacias(dry_run: bool = False) -> None:
    modo = "SIMULACIÓN (dry-run)" if dry_run else "PUBLICACIÓN REAL"
    hoy = date.today()
    logger.info(f"=== Farmacias de turno [{modo}] — {hoy.isoformat()} ===")

    if not dry_run and _ya_publicado_hoy(hoy):
        logger.info("Las farmacias de turno de hoy ya se publicaron. Se omite.")
        return

    nombres, aviso = terna_de_hoy(hoy)
    if not nombres:
        logger.error(aviso)  # mes sin cargar → avisa y NO publica
        return

    listado = scrap_listado()
    fecha = _fecha_larga(hoy)

    items, lineas_cap = [], []
    for i, nom in enumerate(nombres):
        info = listado.get(_norm(nom), {})
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

    caption = ("💊 Farmacias de turno — " + fecha.capitalize() + "\n\n"
               + "\n".join(lineas_cap)
               + "\n\nLas dos primeras están de turno las 24 hs; la última, hasta las 22 hs."
                 "\nFuente: dechivilcoy.com.ar")

    logger.info(f"Farmacias de hoy: {', '.join(nombres)}")
    for l in lineas_cap:
        logger.info(f"   {l}")

    try:
        feed_img = compose_farmacias_feed(items, fecha.capitalize())
        story_img = compose_farmacias_story(items, fecha.capitalize())
    except Exception as e:
        logger.error(f"No se pudieron componer las imágenes de farmacias: {e}")
        return

    if dry_run:
        logger.info(f"   [dry-run] muro Wix/{'/'.join(_platforms())} + historia listos (NO se publica).")
        logger.info(f"   imágenes: {feed_img.name} / {story_img.name}")
        logger.info("=== Farmacias: fin (dry-run) ===")
        return

    plats = _platforms()
    algun_ok = False

    try:
        desc_seo = (f"Farmacias de turno en Chivilcoy — {fecha.capitalize()}: "
                    + ", ".join(nombres))
        wix.publish(f"Farmacias de turno — {fecha.capitalize()}", caption, feed_img, page=0,
                    description=desc_seo)
        algun_ok = True
        logger.info("   [wix] farmacias publicadas OK")
    except Exception as e:
        logger.error(f"   [wix] FALLÓ: {e}")

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
