"""Compositor de imágenes para Historias (stories) 9:16 — 1080x1920.

Las Historias por API NO muestran caption ni stickers, así que TODO el texto
(resumen, dirección web, título) se dibuja DENTRO de la imagen con Pillow.

Funciones públicas:
  - compose_note_story(photo_path, volanta, titular, resumen, site_url) -> Path
  - compose_youtube_story(thumb_path, titulo, etiqueta) -> Path
Ambas devuelven la ruta a un JPG en historias_preview/.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from utils.logger import get_logger

logger = get_logger("story_image")

# --- Lienzo ---
W, H = 1080, 1920
MARGIN = 70

# --- Paleta (marca) ---
BG = (17, 19, 26)          # fondo oscuro
ACCENT = (214, 40, 40)     # rojo del diario
WHITE = (245, 245, 245)
GRAY = (188, 192, 200)

PREVIEW_DIR = Path(__file__).parent / "historias_preview"

# Fuentes de Windows (con fallback a la default de Pillow)
_FONT_PATHS = {
    "bold": [r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\Arialbd.ttf"],
    "regular": [r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\Arial.ttf"],
}


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    for p in _FONT_PATHS["bold" if bold else "regular"]:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    """Parte el texto en líneas que entren en max_w píxeles."""
    text = (text or "").strip()
    if not text:
        return []
    lines, line = [], ""
    for word in text.split():
        probe = (line + " " + word).strip()
        if draw.textlength(probe, font=font) <= max_w:
            line = probe
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _draw_block(draw, lines, font, x, y, fill, line_gap) -> int:
    """Dibuja varias líneas y devuelve la Y debajo del bloque."""
    for ln in lines:
        draw.text((x, y), ln, font=font, fill=fill)
        bbox = font.getbbox(ln)
        y += (bbox[3] - bbox[1]) + line_gap
    return y


def _cover(img: Image.Image, box_w: int, box_h: int) -> Image.Image:
    """Escala la imagen para CUBRIR el box y recorta el sobrante (crop centrado)."""
    img = img.convert("RGB")
    src_w, src_h = img.size
    scale = max(box_w / src_w, box_h / src_h)
    new = img.resize((max(1, round(src_w * scale)), max(1, round(src_h * scale))), Image.LANCZOS)
    nw, nh = new.size
    left = (nw - box_w) // 2
    top = (nh - box_h) // 2
    return new.crop((left, top, left + box_w, top + box_h))


def _contain(img: Image.Image, box_w: int, box_h: int) -> Image.Image:
    """Escala la imagen para ENTRAR en el box sin recortar (mantiene proporción)."""
    img = img.convert("RGB")
    out = img.copy()
    out.thumbnail((box_w, box_h), Image.LANCZOS)
    return out


def _new_canvas() -> Image.Image:
    return Image.new("RGB", (W, H), BG)


def _save(img: Image.Image, stem: str) -> Path:
    PREVIEW_DIR.mkdir(exist_ok=True)
    out = PREVIEW_DIR / f"{stem}.jpg"
    img.save(out, "JPEG", quality=90)
    logger.debug(f"Historia compuesta: {out.name}")
    return out


def _safe_stem(text: str, fallback: str) -> str:
    base = "".join(c if c.isalnum() else "_" for c in (text or "")).strip("_")[:40]
    return base or fallback


# ---------------------------------------------------------------------------
# Historia de NOTICIA: foto (cover, full-bleed arriba) + texto abajo
# ---------------------------------------------------------------------------
def compose_note_story(photo_path: Path, volanta: str, titular: str,
                       resumen: str, site_url: str) -> Path:
    canvas = _new_canvas()
    draw = ImageDraw.Draw(canvas)

    # Encabezado de marca
    f_brand = _font(34, bold=True)
    draw.text((MARGIN, 60), "DIARIO LA CAMPAÑA", font=f_brand, fill=ACCENT)

    # Foto full-bleed (cover) en la franja superior
    photo_top = 130
    photo_h = 1080
    try:
        photo = _cover(Image.open(photo_path), W, photo_h)
        canvas.paste(photo, (0, photo_top))
    except Exception as e:
        logger.warning(f"No se pudo abrir la foto {getattr(photo_path,'name',photo_path)}: {e}")
        photo_h = 0

    # Bloque de texto
    text_x = MARGIN
    text_w = W - 2 * MARGIN
    y = photo_top + photo_h + 50

    f_volanta = _font(34, bold=True)
    f_titular = _font(58, bold=True)
    f_resumen = _font(38, bold=False)
    f_footer = _font(34, bold=True)

    if volanta:
        vlines = _wrap(draw, volanta.upper(), f_volanta, text_w)[:1]
        y = _draw_block(draw, vlines, f_volanta, text_x, y, ACCENT, 8)
        y += 6

    if titular:
        tlines = _wrap(draw, titular, f_titular, text_w)[:3]
        y = _draw_block(draw, tlines, f_titular, text_x, y, WHITE, 10)
        y += 18

    # Reservar lugar para el pie (footer) abajo
    footer_text = f"Leé la nota completa en {site_url}"
    footer_lines = _wrap(draw, footer_text, f_footer, text_w)
    footer_h = sum((f_footer.getbbox(l)[3] - f_footer.getbbox(l)[1]) + 12 for l in footer_lines)
    footer_y = H - MARGIN - footer_h

    if resumen:
        # Cuántas líneas de resumen entran antes del footer
        max_y = footer_y - 40
        rlines = _wrap(draw, resumen, f_resumen, text_w)
        fitted = []
        yy = y
        for ln in rlines:
            h = (f_resumen.getbbox(ln)[3] - f_resumen.getbbox(ln)[1]) + 12
            if yy + h > max_y:
                if fitted:
                    fitted[-1] = fitted[-1].rstrip(" .,;:") + "…"
                break
            fitted.append(ln)
            yy += h
        _draw_block(draw, fitted, f_resumen, text_x, y, GRAY, 12)

    # Línea separadora + footer
    draw.line((MARGIN, footer_y - 26, W - MARGIN, footer_y - 26), fill=(60, 64, 74), width=2)
    _draw_block(draw, footer_lines, f_footer, text_x, footer_y, WHITE, 12)

    stem = "nota_" + _safe_stem(titular or volanta, "nota")
    return _save(canvas, stem)


# ---------------------------------------------------------------------------
# Historia de YOUTUBE: miniatura (16:9) centrada + título + pie
# ---------------------------------------------------------------------------
def compose_youtube_story(thumb_path: Path, titulo: str, etiqueta: str) -> Path:
    canvas = _new_canvas()
    draw = ImageDraw.Draw(canvas)

    # Encabezado
    f_brand = _font(34, bold=True)
    draw.text((MARGIN, 60), "RADIO DEL CENTRO", font=f_brand, fill=ACCENT)

    # Miniatura: ancho completo, 16:9 → 1080x607, centrada verticalmente arriba
    thumb_w = W
    thumb_h = round(W * 9 / 16)
    thumb_top = 360
    try:
        thumb = _cover(Image.open(thumb_path), thumb_w, thumb_h)
        canvas.paste(thumb, (0, thumb_top))
    except Exception as e:
        logger.warning(f"No se pudo abrir la miniatura: {e}")

    # Botón play (círculo rojo + triángulo) en el centro de la miniatura
    cx, cy, r = W // 2, thumb_top + thumb_h // 2, 70
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=ACCENT)
    tri = [(cx - 22, cy - 36), (cx - 22, cy + 36), (cx + 40, cy)]
    draw.polygon(tri, fill=WHITE)

    # Título debajo
    text_x = MARGIN
    text_w = W - 2 * MARGIN
    y = thumb_top + thumb_h + 70

    f_titulo = _font(56, bold=True)
    tlines = _wrap(draw, titulo, f_titulo, text_w)[:4]
    y = _draw_block(draw, tlines, f_titulo, text_x, y, WHITE, 12)

    # Pie: triángulo "play" dibujado + texto (sin emojis, para que Arial lo renderice)
    f_footer = _font(40, bold=True)
    footer_text = f"{etiqueta} en YouTube"
    flines = _wrap(draw, footer_text, f_footer, text_w - 70)
    footer_h = sum((f_footer.getbbox(l)[3] - f_footer.getbbox(l)[1]) + 12 for l in flines)
    footer_y = H - MARGIN - footer_h
    draw.line((MARGIN, footer_y - 26, W - MARGIN, footer_y - 26), fill=(60, 64, 74), width=2)
    # triángulo a la izquierda de la primera línea
    fh = f_footer.getbbox("Ay")[3] - f_footer.getbbox("Ay")[1]
    ty = footer_y + 4
    draw.polygon([(text_x, ty), (text_x, ty + fh), (text_x + fh * 0.85, ty + fh / 2)], fill=ACCENT)
    _draw_block(draw, flines, f_footer, text_x + 70, footer_y, ACCENT, 12)

    stem = "yt_" + _safe_stem(titulo, "video")
    return _save(canvas, stem)
