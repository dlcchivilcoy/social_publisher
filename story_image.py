"""Compositor de imágenes para Historias (stories) 9:16 — 1080x1920.

Las Historias por API NO muestran caption ni stickers, así que TODO el texto
(resumen, dirección web, título) se dibuja DENTRO de la imagen con Pillow.

Funciones públicas:
  - compose_note_story(photo_path, volanta, titular, resumen, site_url) -> Path
  - compose_youtube_story(thumb_path, titulo, etiqueta) -> Path
Ambas devuelven la ruta a un JPG en historias_preview/.
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from utils.logger import get_logger

logger = get_logger("story_image")

# --- Lienzo ---
W, H = 1080, 1920
MARGIN = 70

# --- Paleta CLARA (identidad de la web: blanco + naranja + logo) ---
BG = (255, 255, 255)        # fondo blanco
ACCENT = (226, 98, 12)      # naranja del diario (ajustable)
# OJO: la variable se sigue llamando WHITE por compatibilidad con las funciones
# existentes, pero ahora vale NARANJA: los títulos (antes blancos sobre negro) pasan
# a ser naranjas sobre blanco. El cuerpo va en GRAY (gris oscuro, legible en blanco).
WHITE = (226, 98, 12)       # títulos → naranja
GRAY = (74, 78, 86)         # texto secundario → gris oscuro

PREVIEW_DIR = Path(__file__).parent / "historias_preview"
LOGO_PATH = Path(__file__).parent / "logo.png"
_logo_cache = None


def _paste_logo(canvas: "Image.Image", top: int, target_w: int) -> int:
    """Pega el logo del diario (masthead negro, transparente) centrado arriba.
    Devuelve la 'y' debajo del logo. Si no está el archivo, no rompe."""
    global _logo_cache
    if _logo_cache is None:
        try:
            _logo_cache = Image.open(LOGO_PATH).convert("RGBA")
        except Exception as e:
            logger.warning(f"No se pudo cargar el logo ({LOGO_PATH}): {e}")
            _logo_cache = False
    if not _logo_cache:
        return top
    w, h = _logo_cache.size
    nh = max(1, round(h * target_w / w))
    lg = _logo_cache.resize((target_w, nh), Image.LANCZOS)
    canvas.paste(lg, ((canvas.width - target_w) // 2, top), lg)
    return top + nh

# Fuentes (Windows primero, luego Linux; fallback a la default de Pillow).
# En el server Linux se usan Liberation Sans (métrica idéntica a Arial) o DejaVu.
_FONT_PATHS = {
    "bold": [
        r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\Arialbd.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ],
    "regular": [
        r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\Arial.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ],
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


def _resumen_lineas(draw, text, font, max_w, max_lines) -> list[str]:
    """Resumen recortado a `max_lines` líneas que entren en `max_w`, SIN puntos
    suspensivos. Si el texto no entra, corta en el límite de la última línea
    completa (y, si puede, en el final de una oración para que quede prolijo)."""
    text = (text or "").strip()
    if not text:
        return []
    lines = _wrap(draw, text, font, max_w)
    if len(lines) <= max_lines:
        return lines
    recorte = lines[:max_lines]
    # Intentar terminar en el final de una oración dentro del bloque permitido.
    bloque = " ".join(recorte)
    corte = max(bloque.rfind(". "), bloque.rfind("! "), bloque.rfind("? "))
    if corte >= len(bloque) * 0.5:  # solo si no perdemos demasiado texto
        bloque = bloque[:corte + 1]
        recorte = _wrap(draw, bloque, font, max_w)[:max_lines]
    return recorte


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


def _fit_blur(img: Image.Image, box_w: int, box_h: int) -> Image.Image:
    """Muestra la foto COMPLETA (sin recortar) dentro del box y rellena el fondo
    con una versión ampliada y desenfocada de la misma foto (estilo Instagram).
    Si la foto ya tiene la proporción del box, queda igual que antes (sin franjas);
    si tiene otra proporción, se ve entera y prolija sobre el fondo borroso."""
    img = img.convert("RGB")
    # Fondo: cubrir el box (recorte) + desenfoque fuerte + oscurecer un poco
    bg = _cover(img, box_w, box_h).filter(ImageFilter.GaussianBlur(40))
    bg = ImageEnhance.Brightness(bg).enhance(0.55)
    # Primer plano: la foto entera escalada para entrar, centrada
    fg = img.copy()
    fg.thumbnail((box_w, box_h), Image.LANCZOS)
    fw, fh = fg.size
    bg.paste(fg, ((box_w - fw) // 2, (box_h - fh) // 2))
    return bg


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

    # Foto en la franja superior: se ve ENTERA (sin recortar), con fondo borroso
    # para rellenar si la foto tiene otra proporción (evita "fuera de cuadro").
    photo_top = 130
    photo_h = 1080
    try:
        photo = _fit_blur(Image.open(photo_path), W, photo_h)
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
def compose_youtube_story(thumb_path: Path, titulo: str, etiqueta: str,
                          footer: str | None = None, en_vivo: bool = False) -> Path:
    canvas = _new_canvas()
    draw = ImageDraw.Draw(canvas)

    # Encabezado
    f_brand = _font(34, bold=True)
    draw.text((MARGIN, 60), "RADIO DEL CENTRO", font=f_brand, fill=ACCENT)

    # Cartel "EN VIVO" (rojo) cuando corresponde
    if en_vivo:
        f_live = _font(40, bold=True)
        txt = "EN VIVO"
        tw = draw.textlength(txt, font=f_live)
        bx0, by0 = MARGIN, 118
        pad, dot = 26, 16
        bx1 = bx0 + dot + 18 + tw + pad * 2
        by1 = by0 + 70
        draw.rounded_rectangle((bx0, by0, bx1, by1), radius=18, fill=ACCENT)
        cy = (by0 + by1) // 2
        draw.ellipse((bx0 + pad, cy - dot // 2, bx0 + pad + dot, cy + dot // 2), fill=WHITE)
        draw.text((bx0 + pad + dot + 18, by0 + 14), txt, font=f_live, fill=WHITE)

    # Miniatura: ancho completo, 16:9 → 1080x607, centrada verticalmente arriba
    thumb_w = W
    thumb_h = round(W * 9 / 16)
    thumb_top = 420 if en_vivo else 360
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
    footer_text = footer or f"{etiqueta} en YouTube"
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


# ---------------------------------------------------------------------------
# Historia RESUMEN de YouTube: UNA sola historia con TODAS las notas del día
#   (varias miniaturas + título) + CTA "Mirálas en nuestro canal de YouTube".
# ---------------------------------------------------------------------------
def compose_youtube_resumen_story(videos: list[dict],
                                  titulo_top: str = "NOTAS DE HOY",
                                  cta: str = "Mirálas en nuestro canal de YouTube",
                                  marca: str = "Radio del Centro") -> Path:
    """videos: lista de {"thumb": Path, "titulo": str}. Devuelve un JPG 9:16."""
    canvas = _new_canvas()
    draw = ImageDraw.Draw(canvas)
    m = MARGIN
    inner = W - 2 * m

    # Encabezado
    draw.text((m, 56), "RADIO DEL CENTRO", font=_font(34, True), fill=ACCENT)
    y = 130

    # Título grande
    f_t = _font(72, True)
    for ln in _wrap(draw, titulo_top, f_t, inner)[:2]:
        draw.text((m, y), ln, font=f_t, fill=WHITE)
        y += _line_h(f_t, "Ay") + 18

    # Subtítulo (cantidad de videos)
    f_s = _font(34, False)
    sub = f"{len(videos)} video{'s' if len(videos) != 1 else ''} de hoy"
    draw.text((m, y), sub, font=f_s, fill=ACCENT)
    y += _line_h(f_s, "Ay") + 22
    draw.line((m, y, W - m, y), fill=(60, 64, 74), width=2)
    y_start = y + 30

    # Pie (CTA en varias líneas + marca) — reservar su altura
    f_cta = _font(40, True)
    f_marca = _font(46, True)
    cta_lines = _wrap(draw, cta, f_cta, inner - 64)
    cta_h = sum(_line_h(f_cta, l) + 8 for l in cta_lines)
    footer_h = cta_h + _line_h(f_marca, "Ay") + 14
    footer_y = H - m - footer_h
    avail = (footer_y - 30) - y_start

    # Cuántas filas mostrar y con qué tamaño de miniatura entran
    GAP = 24
    MAXN = 6
    items = videos[:MAXN]
    extra = len(videos) - len(items)
    n = max(1, len(items))

    thumb_w = 240
    for tw in (460, 420, 380, 340, 300, 260, 240):
        th = round(tw * 9 / 16)
        total = n * th + (n - 1) * GAP + (44 if extra > 0 else 0)
        if total <= avail:
            thumb_w = tw
            break
    thumb_h = round(thumb_w * 9 / 16)

    f_titulo = _font(34, True)
    text_x = m + thumb_w + 28
    text_w = W - m - text_x

    yy = y_start
    for v in items:
        # Miniatura (cover, recorte centrado) con marco sutil
        try:
            th_img = _cover(Image.open(v["thumb"]), thumb_w, thumb_h)
            canvas.paste(th_img, (m, yy))
        except Exception as e:
            logger.warning(f"miniatura no disponible: {e}")
            draw.rectangle((m, yy, m + thumb_w, yy + thumb_h), fill=(40, 44, 54))
        draw.rectangle((m, yy, m + thumb_w, yy + thumb_h), outline=(70, 74, 84), width=2)

        # Botón play (círculo rojo + triángulo)
        bx, by, r = m + thumb_w // 2, yy + thumb_h // 2, 32
        draw.ellipse((bx - r, by - r, bx + r, by + r), fill=ACCENT)
        draw.polygon([(bx - 10, by - 16), (bx - 10, by + 16), (bx + 18, by)], fill=WHITE)

        # Título al costado, centrado vertical respecto a la miniatura
        tlines = _wrap(draw, v.get("titulo", ""), f_titulo, text_w)[:3]
        lh = _line_h(f_titulo, "Ay") + 4
        ty = yy + max(0, (thumb_h - len(tlines) * lh) // 2)
        for ln in tlines:
            draw.text((text_x, ty), ln, font=f_titulo, fill=WHITE)
            ty += lh
        yy += thumb_h + GAP

    if extra > 0:
        draw.text((m, yy), f"… y {extra} más en el canal", font=f_s, fill=GRAY)

    # Pie: triángulo play + CTA (blanco) + marca (acento)
    draw.line((m, footer_y - 20, W - m, footer_y - 20), fill=(60, 64, 74), width=2)
    fh = _line_h(f_cta, "Ay")
    draw.polygon([(m, footer_y + 4), (m, footer_y + 4 + fh),
                  (m + fh * 0.85, footer_y + 4 + fh / 2)], fill=ACCENT)
    yc = footer_y
    for i, ln in enumerate(cta_lines):
        x = m + (int(fh) + 18 if i == 0 else 0)
        draw.text((x, yc), ln, font=f_cta, fill=WHITE)
        yc += _line_h(f_cta, ln) + 8
    draw.text((m, yc + 4), marca, font=f_marca, fill=ACCENT)

    return _save(canvas, "yt_resumen")


# ---------------------------------------------------------------------------
# Historia de TAPA: la tapa del diario entera (contain, sin recortar) + fecha
# ---------------------------------------------------------------------------
def compose_tapa_story(cover_path: Path, fecha_str: str) -> Path:
    canvas = _new_canvas()
    draw = ImageDraw.Draw(canvas)

    # (Sin logo arriba — pedido del usuario.)
    # Pie (lo dibujamos al final, pero reservamos su altura). Texto CENTRADO.
    f_tapa = _font(56, bold=True)
    f_fecha = _font(40, bold=False)
    footer_block_h = 150
    footer_y = H - MARGIN - footer_block_h

    # Tapa contenida (sin recortar), centrada en TODO el espacio libre de arriba.
    box_top = 90
    box_h = footer_y - 40 - box_top
    box_w = W - 2 * MARGIN
    try:
        cover = _contain(Image.open(cover_path), box_w, box_h)
        cw, ch = cover.size
        cx = (W - cw) // 2
        cy = box_top + (box_h - ch) // 2
        # marco sutil
        draw.rectangle((cx - 4, cy - 4, cx + cw + 4, cy + ch + 4), outline=(70, 74, 84), width=3)
        canvas.paste(cover, (cx, cy))
    except Exception as e:
        logger.warning(f"No se pudo abrir la tapa: {e}")

    # Pie: "TAPA DE HOY" + fecha — CENTRADOS
    draw.line((MARGIN, footer_y - 20, W - MARGIN, footer_y - 20), fill=(60, 64, 74), width=2)
    y = _texto_centrado(draw, ["TAPA DE HOY"], f_tapa, footer_y, WHITE, gap=8)
    if fecha_str:
        _texto_centrado(draw, [fecha_str], f_fecha, y, ACCENT)

    return _save(canvas, "tapa")


# ---------------------------------------------------------------------------
# Compositor GENÉRICO de listados (sirve para sepelios y farmacias)
# ---------------------------------------------------------------------------
GREEN = (60, 175, 110)


def _line_h(font, sample="Ay") -> int:
    b = font.getbbox(sample)
    return b[3] - b[1]


def _draw_marker(draw, x, y, size, color, kind):
    """Dibuja un marcador: 'cross' (cruz sobria), 'plus' (cruz farmacia), 'dot'."""
    if kind == "cross":
        w = max(3, size // 5)
        cx = x + size // 2
        draw.rectangle((cx - w // 2, y, cx + w // 2, y + size), fill=color)          # vertical
        draw.rectangle((x, y + size // 4, x + size, y + size // 4 + w), fill=color)   # horizontal
    elif kind == "plus":
        w = max(4, size // 4)
        cx, cy = x + size // 2, y + size // 2
        draw.rectangle((cx - w // 2, y, cx + w // 2, y + size), fill=color)
        draw.rectangle((x, cy - w // 2, x + size, cy + w // 2), fill=color)
    else:  # dot
        draw.ellipse((x, y + size // 4, x + size // 2, y + size // 4 + size // 2), fill=color)


def _compose_listado(*, size, titulo, subtitulo, items, footer,
                     accent=ACCENT, marker="dot", stem="info",
                     logo=True, center=False) -> Path:
    """
    items: lista de dicts {"main": str, "sub": str (opcional)}.
    Ajusta el tamaño de fuente para que entren todos entre el encabezado y el pie.
    logo=False  → no dibuja el logo de arriba.
    center=True → centra TODO el texto horizontalmente y centra verticalmente la
                  lista de ítems en el espacio disponible (sin marcadores a la izq.).
    """
    W2, H2 = size
    m = 70
    inner = W2 - 2 * m
    canvas = Image.new("RGB", (W2, H2), BG)
    draw = ImageDraw.Draw(canvas)

    def _line(ln, font, yy, fill):
        lx = m + (inner - draw.textlength(ln, font=font)) / 2 if center else m
        draw.text((lx, yy), ln, font=font, fill=fill)

    # Marca (logo)
    if logo:
        _paste_logo(canvas, 40, 360)
        y = 116
    else:
        y = 90

    # Título grande
    f_t = _font(68, True)
    for ln in _wrap(draw, titulo, f_t, inner)[:2]:
        _line(ln, f_t, y, WHITE)
        y += _line_h(f_t, ln) + 16

    # Subtítulo (fecha)
    if subtitulo:
        f_s = _font(36, False)
        _line(subtitulo, f_s, y, accent)
        y += _line_h(f_s) + 24

    draw.line((m, y, W2 - m, y), fill=(60, 64, 74), width=2)
    y_start = y + 30

    # Pie (reservar altura)
    foot_lines = []
    foot_y = H2 - m
    if footer:
        f_foot = _font(26, False)
        foot_lines = _wrap(draw, footer, f_foot, inner)
        foot_h = sum(_line_h(f_foot, l) + 10 for l in foot_lines)
        foot_y = H2 - m - foot_h

    avail = (foot_y - 26) - y_start
    n = max(1, len(items))
    tiene_sub = any(it.get("sub") for it in items)
    GAP = 24  # espacio entre ítems

    def layout(main_sz):
        """Calcula fuentes, líneas por ítem y altura total para un tamaño dado."""
        f_main = _font(main_sz, True)
        f_sub = _font(max(22, main_sz - 18), False)
        mk = max(22, _line_h(f_main, "Ay"))
        text_w = inner - mk - 22
        mlh = _line_h(f_main, "Ay")
        slh = _line_h(f_sub, "Ay")
        filas, total = [], 0
        for it in items:
            mlines = _wrap(draw, it.get("main", ""), f_main, text_w)[:2]
            sub = it.get("sub", "")
            sline = _wrap(draw, sub, f_sub, text_w)[:1] if sub else []
            sub2 = it.get("sub2", "")           # línea resaltada (ej: horario)
            sline2 = _wrap(draw, sub2, f_sub, text_w)[:1] if sub2 else []
            h = (len(mlines) * (mlh + 2) + (slh + 4 if sline2 else 0)
                 + (slh + 4 if sline else 0) + GAP)
            filas.append((mlines, sline2, sline, h))
            total += h
        return f_main, f_sub, mk, mlh, slh, filas, total

    # Elegir el tamaño más grande que entre (contemplando nombres en 2 líneas)
    chosen = None
    for main_sz in (54, 48, 44, 40, 36, 32, 28, 24):
        res = layout(main_sz)
        if res[6] <= avail:
            chosen = res
            break
    if not chosen:
        chosen = layout(24)
    f_main, f_sub, mk, mlh, slh, filas, total = chosen

    # En modo centrado, arrancar la lista de modo que quede centrada verticalmente.
    yy = y_start + (max(0, (avail - total) // 2) if center else 0)
    for idx, (mlines, sline2, sline, h) in enumerate(filas):
        if yy + h > foot_y - 10:
            draw.text((m, yy), f"… y {n - idx} más", font=f_sub, fill=GRAY)
            break
        if center:
            # Texto centrado, sin marcador a la izquierda.
            ly = yy
            for ln in mlines:
                draw.text(((W2 - draw.textlength(ln, font=f_main)) / 2, ly), ln, font=f_main, fill=WHITE)
                ly += mlh + 2
            if sline2:
                draw.text(((W2 - draw.textlength(sline2[0], font=f_sub)) / 2, ly + 2), sline2[0], font=f_sub, fill=accent)
                ly += slh + 4
            if sline:
                draw.text(((W2 - draw.textlength(sline[0], font=f_sub)) / 2, ly + 2), sline[0], font=f_sub, fill=GRAY)
        else:
            _draw_marker(draw, m, yy + 4, mk, accent, marker)
            tx = m + mk + 22
            ly = yy
            for ln in mlines:
                draw.text((tx, ly), ln, font=f_main, fill=WHITE)
                ly += mlh + 2
            if sline2:   # horario u otra línea destacada → color de acento
                draw.text((tx, ly + 2), sline2[0], font=f_sub, fill=accent)
                ly += slh + 4
            if sline:
                draw.text((tx, ly + 2), sline[0], font=f_sub, fill=GRAY)
        yy += h

    # Pie
    if foot_lines:
        draw.line((m, foot_y - 16, W2 - m, foot_y - 16), fill=(60, 64, 74), width=2)
        f_foot = _font(26, False)
        if center:
            yy = foot_y
            for ln in foot_lines:
                draw.text(((W2 - draw.textlength(ln, font=f_foot)) / 2, yy), ln, font=f_foot, fill=GRAY)
                yy += _line_h(f_foot, ln) + 10
        else:
            _draw_block(draw, foot_lines, f_foot, m, foot_y, GRAY, 10)

    return _save(canvas, stem)


# ---- SEPELIOS ----
def compose_sepelios_feed(nombres: list[str], fecha_str: str) -> Path:
    items = [{"main": n} for n in nombres]
    return _compose_listado(
        size=(1080, 1350), titulo="SEPELIOS", subtitulo=fecha_str,
        items=items, footer="Q.E.P.D. · Diario La Campaña acompaña a las familias · Fuentes: Visión y San Nicolás",
        accent=GRAY, marker="cross", stem="sepelios_feed")


def compose_sepelios_story(nombres: list[str], fecha_str: str) -> Path:
    items = [{"main": n} for n in nombres]
    return _compose_listado(
        size=(W, H), titulo="SEPELIOS", subtitulo=fecha_str,
        items=items, footer="Q.E.P.D. · Diario La Campaña acompaña a las familias · Fuentes: Visión y San Nicolás",
        accent=GRAY, marker="cross", stem="sepelios_story")


# ---- FARMACIAS ----
def compose_farmacias_feed(items: list[dict], fecha_str: str) -> Path:
    return _compose_listado(
        size=(1080, 1350), titulo="FARMACIAS DE TURNO", subtitulo=fecha_str,
        items=items, footer="Turnos de 8:30 a 8:30 hs (la última, de 8:30 a 22 hs)",
        accent=GREEN, marker="plus", stem="farmacias_feed")


def compose_farmacias_story(items: list[dict], fecha_str: str) -> Path:
    return _compose_listado(
        size=(W, H), titulo="FARMACIAS DE TURNO", subtitulo=fecha_str,
        items=items, footer="Turnos de 8:30 a 8:30 hs (la última, de 8:30 a 22 hs)",
        accent=GREEN, marker="plus", stem="farmacias_story",
        logo=False, center=True)


# ---------------------------------------------------------------------------
# Historia PROMO del CANAL de WhatsApp: QR (escaneable) + invitación a seguirlo.
#   En historias el link no es tocable; el QR sí se escanea desde otro teléfono.
# ---------------------------------------------------------------------------
WHATSAPP_GREEN = (37, 211, 102)


def _texto_centrado(draw, lines, font, y, fill, gap=10):
    for ln in lines:
        w = draw.textlength(ln, font=font)
        draw.text(((W - w) // 2, y), ln, font=font, fill=fill)
        y += _line_h(font, "Ay") + gap
    return y


def compose_canal_story(url: str, *,
                        titulo="Seguinos en nuestro Canal de WhatsApp",
                        subtitulo="Toda la info del día, al instante 📲",
                        cta="Escaneá el código para seguirnos",
                        marca="Diario La Campaña · Radio del Centro") -> Path:
    import qrcode

    canvas = _new_canvas()
    draw = ImageDraw.Draw(canvas)
    m = MARGIN
    inner = W - 2 * m

    # Marca arriba (centrada)
    _texto_centrado(draw, ["DIARIO LA CAMPAÑA"], _font(36, True), 70, ACCENT)
    y = 175

    # Título (centrado)
    f_t = _font(64, True)
    y = _texto_centrado(draw, _wrap(draw, titulo, f_t, inner)[:3], f_t, y, WHITE, gap=12)
    y += 8

    # Subtítulo (centrado, verde WhatsApp). Sin emoji para que Arial no falle.
    sub = subtitulo.replace("📲", "").strip()
    f_s = _font(38, False)
    y = _texto_centrado(draw, _wrap(draw, sub, f_s, inner)[:2], f_s, y, WHATSAPP_GREEN, gap=8)
    y += 34

    # Panel blanco con el QR centrado
    panel = min(inner, 760)
    px = (W - panel) // 2
    py = y
    draw.rounded_rectangle((px, py, px + panel, py + panel), radius=44, fill=WHITE)

    qr = qrcode.QRCode(border=1, box_size=10,
                       error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color=(17, 19, 26), back_color=(255, 255, 255)).convert("RGB")
    qsize = panel - 96
    qr_img = qr_img.resize((qsize, qsize), Image.NEAREST)
    canvas.paste(qr_img, (px + (panel - qsize) // 2, py + (panel - qsize) // 2))
    y = py + panel + 44

    # CTA (centrada)
    f_cta = _font(44, True)
    y = _texto_centrado(draw, _wrap(draw, cta, f_cta, inner)[:2], f_cta, y, WHITE, gap=8)

    # Marca abajo (verde WhatsApp)
    f_m = _font(34, True)
    _texto_centrado(draw, [marca], f_m, H - m - _line_h(f_m, "Ay"), WHATSAPP_GREEN)

    return _save(canvas, "canal_wsp")


# ---------------------------------------------------------------------------
# Historia de REPOSTEO de publicidad: el flyer del comercio centrado (sin
# recortar) sobre el fondo de marca + un pie discreto. Sirve para cualquier
# proporción de flyer (cuadrado, 4:5, etc.) quedando siempre 9:16 prolijo.
# ---------------------------------------------------------------------------
def compose_repost_story(flyer_path: Path, pie: str = "Espacio publicitario",
                         marca: str = "DIARIO LA CAMPAÑA") -> Path:
    canvas = _new_canvas()
    draw = ImageDraw.Draw(canvas)

    _texto_centrado(draw, [marca], _font(34, True), 60, ACCENT)

    # Pie reservado
    f_pie = _font(32, False)
    pie_y = H - MARGIN - _line_h(f_pie, "Ay")

    # Flyer contenido (sin recortar) y centrado entre encabezado y pie
    box_top = 150
    box_h = (pie_y - 30) - box_top
    box_w = W - 2 * MARGIN
    try:
        flyer = _contain(Image.open(flyer_path), box_w, box_h)
        fw, fh = flyer.size
        cx = (W - fw) // 2
        cy = box_top + (box_h - fh) // 2
        draw.rectangle((cx - 3, cy - 3, cx + fw + 3, cy + fh + 3), outline=(70, 74, 84), width=2)
        canvas.paste(flyer, (cx, cy))
    except Exception as e:
        logger.warning(f"No se pudo abrir el flyer a repostear: {e}")

    if pie:
        _texto_centrado(draw, [pie], f_pie, pie_y, GRAY)

    return _save(canvas, "repost")


# ---------------------------------------------------------------------------
# CARRUSEL (feed 4:5): slide de NOTA, slide de TAPA y placa "Noticias de hoy".
# Las imágenes del carrusel se generan 1080x1350 (Instagram exige que TODAS las
# imágenes del carrusel tengan la misma proporción).
# ---------------------------------------------------------------------------
SLIDE_W, SLIDE_H = 1080, 1350


def _draw_titular_fill(draw, text, x, y, w, h, fill, *, max_size=100, min_size=44, center=False):
    """Dibuja el titular lo MÁS grande posible para llenar la caja (w x h),
    centrado verticalmente (y horizontalmente si center=True). Si no entra ni al
    tamaño mínimo, recorta con '…'."""
    text = (text or "").strip()
    if not text:
        return

    def _emit(f, lines, yy):
        for ln in lines:
            lx = x + (w - draw.textlength(ln, font=f)) / 2 if center else x
            draw.text((lx, yy), ln, font=f, fill=fill)
            yy += _line_h(f, "Ay") + 8

    for size in range(max_size, min_size - 1, -3):
        f = _font(size, bold=True)
        lines = _wrap(draw, text, f, w)
        lh = _line_h(f, "Ay") + 8
        if len(lines) * lh <= h:
            _emit(f, lines, y + max(0, (h - len(lines) * lh) // 2))
            return
    f = _font(min_size, bold=True)
    lh = _line_h(f, "Ay") + 8
    lines = _wrap(draw, text, f, w)[:max(1, h // lh)]
    if lines:
        lines[-1] = lines[-1].rstrip(" .,;:") + "…"
    _emit(f, lines, y)


def compose_note_slide(photo_path: Path, volanta: str, titular: str, site_url: str = "") -> Path:
    """Slide 4:5: logo + foto entera + TITULAR grande CENTRADO que llena la caja
    blanca, y al PIE 'Seguí leyendo la nota completa en {web}'.
    (La volanta ya NO se muestra — pedido del usuario; el parámetro se mantiene por
    compatibilidad con quien llama.)"""
    canvas = Image.new("RGB", (SLIDE_W, SLIDE_H), BG)
    draw = ImageDraw.Draw(canvas)

    logo_bottom = _paste_logo(canvas, 30, 460)
    x = MARGIN
    max_w = SLIDE_W - 2 * MARGIN
    y = logo_bottom + 18

    # Pie: "Seguí leyendo la nota completa en {web}" (en cada slide)
    box_bottom = SLIDE_H - 44
    f_cta = _font(30, bold=True)
    cta_lines = _wrap(draw, f"Seguí leyendo la nota completa en {site_url}", f_cta, max_w) if site_url else []
    cta_lh = _line_h(f_cta, "Ay") + 6
    cta_h = len(cta_lines) * cta_lh

    # Caja del titular (arriba del pie)
    titular_box_h = 360
    titular_box_bottom = box_bottom - (cta_h + 28 if cta_lines else 0)
    titular_box_top = titular_box_bottom - titular_box_h

    photo_top = y + 4
    photo_h = max(280, (titular_box_top - 24) - photo_top)
    try:
        canvas.paste(_fit_blur(Image.open(photo_path), SLIDE_W, photo_h), (0, photo_top))
    except Exception as e:
        logger.warning(f"No se pudo abrir la foto del slide {getattr(photo_path, 'name', photo_path)}: {e}")

    _draw_titular_fill(draw, titular, x, titular_box_top, max_w, titular_box_h, ACCENT, center=True)

    if cta_lines:
        sep_y = titular_box_bottom + 14
        draw.line((x, sep_y, SLIDE_W - MARGIN, sep_y), fill=ACCENT, width=3)
        yy = box_bottom - cta_h
        for ln in cta_lines:
            w = draw.textlength(ln, font=f_cta)
            draw.text(((SLIDE_W - w) // 2, yy), ln, font=f_cta, fill=ACCENT)
            yy += cta_lh

    return _save(canvas, "slide_" + _safe_stem(titular or volanta, "nota"))


def compose_tapa_slide(cover_path: Path) -> Path:
    """Slide 4:5 con la tapa entera (fondo desenfocado para entrar en 1080x1350)."""
    canvas = Image.new("RGB", (SLIDE_W, SLIDE_H), BG)
    try:
        canvas.paste(_fit_blur(Image.open(cover_path), SLIDE_W, SLIDE_H), (0, 0))
    except Exception as e:
        logger.warning(f"No se pudo abrir la tapa para el slide: {e}")
    return _save(canvas, "slide_tapa")


def _mosaico(photos: list, w: int, h: int) -> "Image.Image":
    """Collage tipo rompecabezas que CUBRE todo el lienzo w x h SIN huecos:
    rellena todas las celdas (cicla fotos si faltan) y las celdas se tocan
    exactamente (sin franjas en blanco), recortando cada foto a su celda."""
    canvas = Image.new("RGB", (w, h), BG)
    fotos = [p for p in (photos or []) if p]
    n = len(fotos)
    if n == 0:
        return canvas
    cols = 1 if n == 1 else (2 if n <= 6 else 3)
    rows = max(1, (n + cols - 1) // cols)
    idx = 0
    for r in range(rows):
        y0, y1 = r * h // rows, (r + 1) * h // rows
        for c in range(cols):
            x0, x1 = c * w // cols, (c + 1) * w // cols
            p = fotos[idx % n]
            idx += 1
            try:
                canvas.paste(_cover(Image.open(p), x1 - x0, y1 - y0), (x0, y0))
            except Exception:
                pass
    return canvas


def compose_noticias_hoy_story(fecha_str: str, site_url: str = "", photos: list = None) -> Path:
    """Placa 9:16 'NOTICIAS DE HOY' (la ÚNICA historia del carrusel de notas):
    las fotos de las noticias arman un rompecabezas de fondo, con un velo blanco
    para que se lea + logo + texto naranja."""
    base = _mosaico(photos, W, H).convert("RGBA")
    velo = Image.new("RGBA", (W, H), (255, 255, 255, 210))
    canvas = Image.alpha_composite(base, velo).convert("RGB")
    draw = ImageDraw.Draw(canvas)

    # Todo MÁS GRANDE para máxima legibilidad
    _paste_logo(canvas, 110, 860)
    _texto_centrado(draw, ["NUEVO POSTEO"], _font(70, bold=True), 390, GRAY)
    _texto_centrado(draw, ["NOTICIAS", "DE HOY"], _font(180, bold=True), 470, ACCENT, gap=2)
    if fecha_str:
        _texto_centrado(draw, [fecha_str], _font(64, bold=True), 940, GRAY)
    draw.line((MARGIN, 1090, W - MARGIN, 1090), fill=ACCENT, width=8)
    msg = "Deslizá nuestro posteo con todas las noticias del día en el perfil"
    _texto_centrado(draw, _wrap(draw, msg, _font(62, bold=True), W - 2 * MARGIN), _font(62, bold=True), 1330, GRAY, gap=16)
    if site_url:
        _texto_centrado(draw, [site_url], _font(58, bold=True), 1660, ACCENT)
    return _save(canvas, "noticias_hoy")


# ---------------------------------------------------------------------------
# REEL "Las 5 más leídas del día" — placas 9:16 (1080x1920) para armar el video.
# ---------------------------------------------------------------------------
def compose_reel_intro(fecha_str: str, photos: list = None) -> Path:
    """Portada del reel: fondo rompecabezas con las fotos + velo + logo + título."""
    base = _mosaico(photos, W, H).convert("RGBA")
    velo = Image.new("RGBA", (W, H), (255, 255, 255, 214))
    canvas = Image.alpha_composite(base, velo).convert("RGB")
    draw = ImageDraw.Draw(canvas)
    _paste_logo(canvas, 150, 820)
    _texto_centrado(draw, ["LAS 5 MÁS", "LEÍDAS", "DE HOY"], _font(148, bold=True), 470, ACCENT, gap=2)
    if fecha_str:
        _texto_centrado(draw, [fecha_str], _font(60, bold=True), 1180, GRAY)
    draw.line((MARGIN, 1330, W - MARGIN, 1330), fill=ACCENT, width=8)
    _texto_centrado(draw, ["Mirá el ranking del día"], _font(54, bold=True), 1440, GRAY)
    return _save(canvas, "reel_intro")


def compose_reel_slide(photo_path: Path, titular: str, resumen: str, rank: int, views: int = 0) -> Path:
    """Una placa del reel: badge de ranking + foto entera (bien encuadrada) +
    TITULAR grande (autoajustado, centrado) + DESCRIPCIÓN de hasta 3 líneas
    SIN puntos suspensivos + lecturas. Las cajas se reservan de abajo hacia arriba
    para que todo entre completo."""
    canvas = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(canvas)
    _paste_logo(canvas, 70, 560)
    _texto_centrado(draw, [f"N°{rank}  ·  LO MÁS LEÍDO"], _font(52, bold=True), 210, ACCENT)

    x, max_w = MARGIN, W - 2 * MARGIN

    # Lecturas reservadas abajo (solo si hay vistas > 0).
    views_h = 70 if (views and views > 0) else 0
    views_top = (H - 80) - views_h

    photo_top, photo_h = 300, 980
    try:
        canvas.paste(_fit_blur(Image.open(photo_path), W, photo_h), (0, photo_top))
    except Exception as e:
        logger.warning(f"No se pudo abrir la foto del reel: {e}")

    # Descripción: MÁXIMO 3 líneas (pedido del usuario), sin '…'. Se reserva su caja
    # justo encima de las lecturas; el titular ocupa lo que queda entre la foto y ella.
    f_res = _font(40)
    res_lines = _resumen_lineas(draw, resumen, f_res, max_w, max_lines=3)
    res_lh = _line_h(f_res, "Ay") + 10
    res_h = len(res_lines) * res_lh
    res_top = (views_top - (24 if res_h else 0)) - res_h

    titular_box_top = photo_top + photo_h + 30
    titular_box_h = max(220, (res_top - 24) - titular_box_top)
    _draw_titular_fill(draw, titular, x, titular_box_top, max_w, titular_box_h, ACCENT,
                       max_size=92, min_size=38, center=True)

    if res_lines:
        _texto_centrado(draw, res_lines, f_res, res_top, GRAY, gap=10)

    if views and views > 0:
        _texto_centrado(draw, [f"{views:,}".replace(",", ".") + " lecturas"], _font(34, bold=True), views_top, ACCENT)
    return _save(canvas, f"reel_{rank}")


def compose_reel_outro(site_url: str = "") -> Path:
    """Cierre del reel: logo + invitación a la web."""
    canvas = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(canvas)
    _paste_logo(canvas, 560, 780)
    _texto_centrado(draw, _wrap(draw, "Seguí informándote en", _font(66, bold=True), W - 2 * MARGIN),
                    _font(66, bold=True), 900, GRAY, gap=10)
    if site_url:
        _texto_centrado(draw, [site_url], _font(58, bold=True), 1060, ACCENT)
    return _save(canvas, "reel_outro")
