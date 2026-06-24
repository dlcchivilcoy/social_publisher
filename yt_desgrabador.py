"""Desgrabador de las notas de YouTube de Radio del Centro → texto + screenshot, LOCAL.

Toma las notas que el canal Radio del Centro subió a YouTube ese día (excluye el programa
completo «La Mañana del Centro»), las desgraba a TEXTO PERIODÍSTICO con Google Gemini
(gratis, SIN tokens de Claude, sin descargar el video: Gemini lee la URL directa), y deja
por cada nota un `.txt` (volanta + título + cuerpo) y un `.png` (la miniatura del video) en
una carpeta del ESCRITORIO, organizada por fecha:

    Escritorio\\DESGRABACIONES RADIO\\2026-06-23\\
        Resultados muy auspiciosos para el girasol.txt
        Resultados muy auspiciosos para el girasol.png

Pensado para una TAREA DE WINDOWS diaria a las 13:30 (corre local porque deja archivos en
el escritorio; la nube no puede). Un registro (`.yt_desgrabaciones.json`) evita repetir.

Configuración (.env, opcional):
  YT_DESGRABAR_FOLDER  — carpeta destino (por defecto: Escritorio\\DESGRABACIONES RADIO)
Reusa: YT_CHANNEL_ID, STORY_EXCLUDE_TITLE, GEMINI_API_KEY (ya en el .env).
"""
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from utils.config import get
from utils.logger import get_logger

logger = get_logger("yt_desgrabador")

LEDGER = Path(__file__).parent / ".yt_desgrabaciones.json"


def _carpeta_base() -> Path:
    raw = get("YT_DESGRABAR_FOLDER")
    if raw:
        return Path(raw)
    return Path.home() / "Desktop" / "DESGRABACIONES RADIO"


def _slug(s: str, max_len: int = 80) -> str:
    """Nombre de archivo limpio: sin acentos/ñ, sin caracteres prohibidos en Windows."""
    t = unicodedata.normalize("NFD", s or "")
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = t.replace("ñ", "n").replace("Ñ", "n")
    t = re.sub(r'[\\/:*?"<>|]+', " ", t)       # prohibidos en Windows
    t = re.sub(r"\s+", " ", t).strip().strip(".")
    if len(t) > max_len:
        t = t[:max_len].rsplit(" ", 1)[0]
    return t or "nota"


def _leer_ledger() -> set:
    try:
        if LEDGER.exists():
            return set(json.loads(LEDGER.read_text(encoding="utf-8")))
    except Exception:
        pass
    return set()


def _guardar_ledger(ids: set) -> None:
    LEDGER.write_text(json.dumps(sorted(ids)[-1000:], ensure_ascii=False, indent=2),
                      encoding="utf-8")


def _excluir() -> list[str]:
    """Títulos a excluir (el PROGRAMA COMPLETO). Robusto al .env: aunque
    STORY_EXCLUDE_TITLE venga con la ñ corrupta (mojibake), siempre incluye la forma sin
    acentos «manana del centro», que matchea el título normalizado del programa."""
    import youtube
    raw = get("STORY_EXCLUDE_TITLE") or ""
    items = [youtube.normalizar(x) for x in raw.split(",") if x.strip()]
    items.append("manana del centro")  # fallback que NO depende de la ñ del .env
    limpios: list[str] = []
    for x in items:
        x = re.sub(r"[^a-z0-9 ]+", "", x).strip()  # descarta mojibake/símbolos
        if x and x not in limpios:
            limpios.append(x)
    return limpios


def _png_miniatura(video_id: str, destino: Path) -> Path | None:
    """Baja la miniatura del video y la guarda como PNG en `destino`. Best-effort."""
    import youtube
    from PIL import Image
    try:
        jpg = youtube.descargar_miniatura(video_id)
        with Image.open(jpg) as im:
            im.convert("RGB").save(destino, "PNG")
        return destino
    except Exception as e:
        logger.warning(f"No se pudo guardar la miniatura PNG de {video_id}: {e}")
        return None


# Pedido de redacción: nota larga (~1300 palabras / 2 hojas y media), formato periodístico.
INSTRUCCION_LARGO = (
    "El cuerpo de la nota (campo «texto») debe ser EXTENSO: aproximadamente 1300 palabras "
    "(unas 2 hojas y media de Word), escrito en formato periodístico y organizado en "
    "PÁRRAFOS bien desarrollados. Profundizá y desarrollá TODOS los temas que se tratan en "
    "el video, con citas textuales cuando sean claras y confiables, contexto y datos "
    "concretos. Mantené la fidelidad al material: NO inventes, no rellenes ni repitas; si el "
    "material no alcanzara para 1300 palabras, desarrollá todo lo posible siendo fiel."
)


def _escribir_docx(nota: dict, video: dict, path: Path) -> None:
    """Escribe la nota como Word (.docx) en formato periodístico: volanta + título +
    cuerpo en párrafos. Sin fuente ni fecha de procesado."""
    from docx import Document
    from docx.shared import Pt, RGBColor

    doc = Document()
    if nota.get("volanta"):
        p = doc.add_paragraph()
        run = p.add_run(nota["volanta"].upper())
        run.bold = True
        run.font.size = Pt(11)
        run.font.color.rgb = RGBColor(0xE2, 0x62, 0x0C)  # naranja del diario

    titulo = nota.get("titulo") or video.get("titulo", "")
    pt = doc.add_paragraph()
    rt = pt.add_run(titulo)
    rt.bold = True
    rt.font.size = Pt(18)

    doc.add_paragraph()  # línea en blanco entre título y cuerpo
    for parrafo in (nota.get("texto", "") or "").split("\n\n"):
        parrafo = parrafo.strip()
        if parrafo:
            doc.add_paragraph(parrafo)

    doc.save(str(path))


def run_yt_desgrabar(dry_run: bool = False) -> None:
    """Desgraba a texto + miniatura las notas de YouTube de hoy de Radio del Centro y las
    deja en la carpeta del escritorio. Idempotente (no repite las ya procesadas)."""
    import youtube
    from utils import gemini

    modo = "SIMULACIÓN (dry-run)" if dry_run else "PROCESO REAL"
    logger.info(f"=== Desgrabar notas de YouTube (Radio del Centro) [{modo}] ===")

    cid = get("YT_CHANNEL_ID") or "UCqiTJ2oRBLNO1ZzfrdiyjTw"
    videos = youtube.videos_de_hoy(cid)
    if not videos:
        logger.info("No hay videos subidos hoy en el canal. Nada que desgrabar.")
        return

    excluir = _excluir()
    notas = [v for v in videos
             if not any(x and x in youtube.normalizar(v["titulo"]) for x in excluir)]
    logger.info(f"Videos de hoy: {len(videos)} | notas (sin el programa completo): {len(notas)}")

    ledger = _leer_ledger()
    pendientes = [v for v in notas if v["id"] not in ledger]
    if not pendientes:
        logger.info("Todas las notas de hoy ya estaban desgrabadas. Nada que hacer.")
        return

    fecha = datetime.now().strftime("%Y-%m-%d")
    destino = _carpeta_base() / fecha
    if not dry_run:
        destino.mkdir(parents=True, exist_ok=True)
    logger.info(f"{len(pendientes)} nota(s) por desgrabar → {destino}")

    hechas = 0
    for v in pendientes:
        logger.info(f"  Desgrabando: «{v['titulo'][:60]}» ({v['url']})")
        try:
            nota = gemini.transcribe_youtube_url(v["url"], instrucciones=INSTRUCCION_LARGO)
        except Exception as e:
            logger.error(f"    Gemini falló (se reintenta en la próxima corrida): {e}")
            continue  # NO se marca: se reintenta

        if not nota.get("hay_noticia"):
            logger.info("    Sin noticia aprovechable (música/sin datos). Se saltea y se marca.")
            ledger.add(v["id"])
            if not dry_run:
                _guardar_ledger(ledger)
            continue

        slug = _slug(nota.get("titulo") or v["titulo"])
        docx_path = destino / f"{slug}.docx"
        png_path = destino / f"{slug}.png"
        # Evita pisar si dos notas dieran el mismo nombre
        if docx_path.exists():
            slug = f"{slug} ({v['id'][:6]})"
            docx_path = destino / f"{slug}.docx"
            png_path = destino / f"{slug}.png"

        palabras = len((nota.get("texto") or "").split())
        if dry_run:
            logger.info(f"    [dry-run] Guardaría: {docx_path.name} + {png_path.name} "
                        f"(~{palabras} palabras)\n"
                        f"      VOLANTA: {nota['volanta']}\n      TÍTULO: {nota['titulo']}")
            continue

        _escribir_docx(nota, v, docx_path)
        _png_miniatura(v["id"], png_path)
        ledger.add(v["id"])
        _guardar_ledger(ledger)
        hechas += 1
        logger.info(f"    ✓ {docx_path.name} (~{palabras} palabras)"
                    + (f" + {png_path.name}" if png_path.exists() else ""))

    if dry_run:
        logger.info("=== Desgrabar notas de YouTube: fin (dry-run) ===")
    else:
        logger.info(f"=== Desgrabar notas de YouTube: {hechas} nota(s) en {destino} ===")
