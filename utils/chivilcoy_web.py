"""Las notas LOCALES dicen «Chivilcoy» en el título y en la primera línea (solo en la web).

Por qué (informe de Google Noticias del 30/10/2026): en la búsqueda «Chivilcoy» de Google Noticias
aparecen La Razón (26), De Chivilcoy (17) y Hoy Chivilcoy (9), y La Campaña 0, aunque Google ya tiene
guardadas más de 90 notas del diario de octubre. Esos medios dicen «Chivilcoy» en el título y en la
primera línea; las nuestras casi nunca. El caso típico es la volanta: «Chivilcoy · Gremiales — Amarilla
Gas cierra…» parece decirlo, pero la web separa la volanta corta (≤ 25 caracteres, diario_web
src/lib/wix.js `normalizar`) y el título que ve Google (h1, NewsArticle.headline) es solo «Amarilla
Gas cierra…».

Qué hace, para las notas de acá (sección Locales, Policiales, Campo o Deportes por las reglas de
utils/secciones.py, y que no hablan de otra ciudad):
  • el TITULAR que ve Google arranca con «Chivilcoy: » si no lo nombra ya;
  • el PRIMER PÁRRAFO arranca con la data «CHIVILCOY.– » si no lo nombra ya.
Lo usan platforms/wix.crear_borrador y crear_borrador_galeria: la carga de la mañana (notas_web) y el
bot de corresponsales. Las redes no cambian: `deshacer` saca las dos marcas al releer el borrador
(wix.leer_borrador_texto), así el texto de FB/IG/YouTube queda como antes.

Interruptor: CHIVILCOY_WEB=0.
"""
from __future__ import annotations

from utils.config import get
from utils.logger import get_logger

logger = get_logger("chivilcoy_web")

PREFIJO_TITULO = "Chivilcoy: "
DATA = "CHIVILCOY.– "
VOLANTA_MAX = 25                    # igual que diario_web/src/lib/wix.js (normalizar)
SECCIONES_LOCALES = ("locales", "policiales", "campo", "deportes")
# Ciudades de la zona: si el título o el arranque nombran una y la nota no dice «Chivilcoy», es de otra
# ciudad (una nota de la región) y no se le pone «Chivilcoy». Las localidades del partido (Moquehuá,
# Gorostiaga, Ramón Biaus, Benítez, Emilio Ayarza, Indacochea, San Sebastián, La Rica…) SÍ son de acá.
LOCALIDADES_DEL_PARTIDO = ("moquehua", "gorostiaga", "ramon biaus", "benitez", "emilio ayarza", "indacochea",
                           "san sebastian", "la rica", "henry bell", "palemon huergo")
# Afuera, a propósito, los nombres que también son otra cosa: Mercedes, Rojas, Moreno, Salto («dio un
# salto»), Lobos, y La Plata o Buenos Aires (una nota de acá nombra seguido a la Provincia).
OTRAS_CIUDADES = (
    "bragado", "alberti", "suipacha", "junin", "navarro", "25 de mayo", "chacabuco",
    "9 de julio", "nueve de julio", "saladillo", "carmen de areco", "lujan", "pergamino",
    "olavarria", "lincoln", "mar del plata", "rosario", "bahia blanca", "tandil", "pehuajo",
    "trenque lauquen", "general viamonte", "los toldos", "mechita", "general rodriguez",
)


def activo() -> bool:
    return (get("CHIVILCOY_WEB") or "1").strip().lower() not in ("0", "false", "no", "off")


def _partir(title: str) -> tuple:
    """(volanta, titular, resto) EXACTAMENTE como los separa la web (diario_web normalizar): la primera
    parte es volanta solo si tiene ≤ 25 caracteres; si no, la primera parte es el TÍTULO que ve Google
    (h1) y lo que sigue queda de bajada («Acuerdo en el Ministerio de Trabajo — Amarilla Gas cierra…»)."""
    partes = [p.strip() for p in (title or "").strip().split(" — ")]
    volanta = partes.pop(0) if len(partes) > 1 and len(partes[0]) <= VOLANTA_MAX else ""
    return volanta, partes[0], partes[1:]


def _unir(volanta: str, titular: str, resto=()) -> str:
    return " — ".join(([volanta] if volanta else []) + [titular] + list(resto))


def es_local(title: str, body: str, seccion: str = "") -> bool:
    """Hace falta PRUEBA de que es de acá: que nombre Chivilcoy o una localidad del partido. Medido sobre
    las últimas 40 notas de la web (30/10/2026): la sección por reglas manda a «Locales» todo lo que no
    tiene otra señal, y con eso solo salían «Chivilcoy: El día que la URSS hizo estallar la bomba…» y
    las nacionales de la edición en papel. Tampoco si el TÍTULO (con la volanta) nombra otra ciudad
    («BRAGADO · Policiales — …»), aunque el cuerpo mencione la comisaría de Chivilcoy."""
    from utils import secciones as S
    slug = (seccion or "").strip().lower()
    if slug not in S.SLUGS:
        try:
            slug = S.por_reglas(title, body)[0]        # solo reglas: sin gastar IA dos veces
        except Exception:  # noqa: BLE001
            return False
    if slug not in SECCIONES_LOCALES:
        return False
    if S._hay(S._norm(title), OTRAS_CIUDADES):
        return False
    return bool(S._hay(S._norm(f"{title}\n{body}"), ("chivilcoy",) + LOCALIDADES_DEL_PARTIDO))


def _dice_chivilcoy(texto: str) -> bool:
    from utils import secciones as S
    return "chivilcoy" in S._norm(texto)


def ajustar(title: str, body: str, page: int = 0, seccion: str = "") -> tuple:
    """(título, cuerpo) para la WEB. Nunca lanza: ante cualquier duda devuelve lo mismo que recibió."""
    try:
        if not activo() or (seccion or "").strip().lower() == "inicio" or not es_local(title, body, seccion):
            return title, body
        volanta, titular, resto = _partir(title)
        nuevo_titular = titular if _dice_chivilcoy(titular) else PREFIJO_TITULO + titular
        lineas = (body or "").split("\n")
        hechas = 0
        for i, linea in enumerate(lineas):
            limpia = linea.strip()
            if not limpia:
                continue
            if limpia == titular.strip():
                # El cuerpo repite el titular arriba (las fotos de corresponsal): va igual que el título.
                lineas[i] = linea.replace(limpia, nuevo_titular)
                continue
            if not _dice_chivilcoy(limpia):
                lineas[i] = linea.replace(limpia, DATA + limpia, 1)
            hechas = 1
            break
        if nuevo_titular != titular or hechas:
            logger.info(f"[chivilcoy web] «{nuevo_titular[:80]}»")
        return _unir(volanta, nuevo_titular, resto), "\n".join(lineas)
    except Exception as e:  # noqa: BLE001 — esto nunca puede impedir que la nota salga
        logger.warning(f"[chivilcoy web] no se ajustó ({e}); la nota sale como vino")
        return title, body


def deshacer(title: str, texto: str) -> tuple:
    """Lo contrario de `ajustar`, para releer el borrador sin que las redes hereden las marcas de la web."""
    volanta, titular, resto = _partir(title)
    if titular.startswith(PREFIJO_TITULO):
        titular = titular[len(PREFIJO_TITULO):]
    lineas = (texto or "").split("\n")
    for i, linea in enumerate(lineas):
        limpia = linea.strip()
        if not limpia:
            continue
        if limpia.startswith(PREFIJO_TITULO) and limpia[len(PREFIJO_TITULO):] == titular:
            lineas[i] = linea.replace(limpia, titular)
            continue
        if limpia.startswith(DATA):
            lineas[i] = linea.replace(DATA, "", 1)
        break
    return _unir(volanta, titular, resto), "\n".join(lineas)
