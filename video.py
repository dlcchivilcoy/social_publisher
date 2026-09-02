"""Arma un video vertical (reel) 1080x1920 a partir de imágenes, con transiciones
crossfade (xfade) entre placas, SIN audio. Usa el ffmpeg de imageio_ffmpeg (local)
o el del sistema (en la nube)."""
import re
import subprocess
import textwrap
from pathlib import Path

from utils.config import get
from utils.logger import get_logger

logger = get_logger("video")

# --- Marca del reel: logo + overlay con zócalo + placa de cierre --------------
ASSETS = Path(__file__).parent
LOGO_REEL = ASSETS / "logo_reel.png"        # isotipo 'C' con fondo transparente
PLACA_FINAL = ASSETS / "placa_final.png"    # placa de cierre 1080x1920 ("Seguinos en redes")
OVERLAY_REEL = ASSETS / "overlay_reel.png"  # marco 1080x1920 (esquinas + caja + barra web)
FONDO_REEL = ASSETS / "fondo_reel.png"      # degradado naranja que enmarca el video
FUENTE_ZOCALO = ASSETS / "fonts" / "Montserrat-Bold.ttf"
PLACA_SEG = 5.0                             # cuánto dura la placa de cierre
FONDO_DIFUMINADO = 80                       # px de transición entre el video y el fondo
# Rectángulo ÚTIL de la caja negra del overlay (medido sobre el PNG, en 1080x1920): es la
# parte donde la caja es negra en TODAS sus filas, así el texto nunca se escapa por los
# bordes en diagonal. (x, y, ancho, alto).
ZOCALO_CAJA = (175, 1481, 670, 71)
ZOCALO_COLOR = (247, 127, 0)  # el naranja de la marca
ZOCALO_PALABRAS = 5           # tope de palabras del zócalo


def _cfg(clave: str, default: str) -> str:
    return (get(clave, "") or default).strip()


def _asset(clave: str, default: Path) -> Path | None:
    """Ruta del asset (logo / placa). Se puede pisar por `.env`: si la variable trae
    una ruta se usa esa; si vale 0/no/off se apaga. None = no dibujar nada."""
    valor = _cfg(clave, "")
    if valor.lower() in ("0", "no", "off", "false"):
        return None
    ruta = Path(valor) if valor else default
    if not ruta.exists():
        logger.warning(f"Falta el asset del reel ({ruta}); se omite.")
        return None
    return ruta


def has_audio(src) -> bool:
    """True si el archivo trae pista de audio (parseando la salida de ffmpeg)."""
    r = subprocess.run([_ffmpeg(), "-i", str(src)], capture_output=True, text=True)
    return "Audio:" in (r.stderr or "")


def _dimensiones(src) -> tuple[int, int]:
    """Ancho y alto del video (parseando la salida de ffmpeg). (0, 0) si no se puede."""
    r = subprocess.run([_ffmpeg(), "-i", str(src)], capture_output=True, text=True)
    m = re.search(r"Video:.*?[\s,](\d{2,5})x(\d{2,5})[\s,]", r.stderr or "")
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def detectar_recorte(src) -> tuple[int, int, int, int] | None:
    """Detecta las BARRAS NEGRAS pegadas dentro del cuadro (letterbox arriba/abajo o
    pillarbox a los costados) con el `cropdetect` de ffmpeg y devuelve (w, h, x, y) del
    contenido REAL, o None si el video ya llena su propio cuadro. Sirve para los videos
    que vienen verticales pero con mucho negro adentro: así el marco naranja tapa el
    negro en vez de dejarlo. `reset=0` hace que cropdetect acumule el área más grande de
    todo el clip (une el contenido de todos los cuadros), así una escena oscura no lo
    engaña recortando de más."""
    if _cfg("REEL_RECORTE_NEGRO", "1").lower() in ("0", "no", "off", "false"):
        return None
    w, h = _dimensiones(src)
    if not (w and h):
        return None
    dur = duration_seconds(src) or 0
    args = [_ffmpeg(), "-hide_banner"]
    if dur > 16:
        args += ["-t", "16"]           # con 16 s alcanza para fijar las barras; no gasta de más
    args += ["-i", str(src), "-vf", "fps=3,cropdetect=limit=24:round=2:reset=0",
             "-f", "null", "-"]
    r = subprocess.run(args, capture_output=True, text=True)
    m = re.findall(r"crop=(\d+):(\d+):(-?\d+):(-?\d+)", r.stderr or "")
    if not m:
        return None
    cw, ch, cx, cy = (int(v) for v in m[-1])
    if not (0 < cw <= w and 0 < ch <= h and 0 <= cx and 0 <= cy
            and cx + cw <= w and cy + ch <= h):
        return None
    # Recortamos cada eje SOLO si la barra es grande (≥6% del lado). Así una barra real
    # (letterbox/pillarbox ocupa bastante) se saca, pero una esquina apenas oscura del
    # contenido NO se recorta. Si ningún eje tiene barra, no tocamos nada.
    recorta_w = (w - cw) >= 0.06 * w
    recorta_h = (h - ch) >= 0.06 * h
    if not (recorta_w or recorta_h):
        return None
    if not recorta_w:
        cw, cx = w, 0
    if not recorta_h:
        ch, cy = h, 0
    cw -= cw % 2; ch -= ch % 2  # libx264 necesita dimensiones pares
    cx -= cx % 2; cy -= cy % 2
    logger.info(f"Barras negras detectadas: contenido {cw}x{ch} en ({cx},{cy}) de {w}x{h}")
    return (cw, ch, cx, cy)


def fondo_enmarcado(cont_w: int, cont_h: int, salida) -> Path | None:
    """Devuelve el FONDO del reel ya recortado con una máscara: OPACO en los bordes del
    cuadro y TRANSPARENTE donde va el video, con una transición difuminada en el medio.
    Así el degradado naranja contornea el video hasta los bordes y el marco se ajusta
    solo al tamaño del CONTENIDO real de cada video: bandas arriba y abajo si viene
    apaisado (o si venía vertical con negro que ya recortamos), apenas un halo si llena
    el cuadro. `cont_w`/`cont_h` son las dimensiones del contenido SIN las barras negras.
    None si no hay fondo o no se pasaron dimensiones."""
    base = _asset("REEL_FONDO", FONDO_REEL)
    if not base:
        return None
    w, h = cont_w, cont_h
    if not (w and h):
        logger.warning("No pude medir el video; el reel va sin el fondo naranja.")
        return None
    from PIL import Image, ImageDraw, ImageFilter
    fondo = Image.open(base).convert("RGB")
    if fondo.size != (1080, 1920):
        fondo = fondo.resize((1080, 1920), Image.LANCZOS)
    # El mismo encuadre que hace ffmpeg: el video entero centrado dentro de 1080x1920.
    esc = min(1080 / w, 1920 / h)
    vw, vh = round(w * esc), round(h * esc)
    x0, y0 = (1080 - vw) // 2, (1920 - vh) // 2
    dif = int(float(_cfg("REEL_FONDO_DIFUMINADO", str(FONDO_DIFUMINADO))))
    op = max(0.0, min(1.0, float(_cfg("REEL_FONDO_OPACIDAD", "1"))))
    mascara = Image.new("L", (1080, 1920), round(255 * op))
    ImageDraw.Draw(mascara).rectangle([x0, y0, x0 + vw - 1, y0 + vh - 1], fill=0)
    if dif > 0:
        # El desenfoque reparte la transición a los dos lados del borde del video: el
        # naranja entra un poco sobre el video y se apaga hacia adentro.
        mascara = mascara.filter(ImageFilter.GaussianBlur(dif / 2))
    fondo.putalpha(mascara)
    salida = Path(salida)
    fondo.save(salida)
    logger.info(f"Fondo del reel: video {vw}x{vh} centrado, difuminado {dif}px")
    return salida


def _zocalo_texto(texto: str) -> str:
    """Deja el zócalo en 5 palabras como mucho y en mayúsculas (estilo placa de TV)."""
    palabras = [p for p in re.split(r"\s+", (texto or "").strip()) if p]
    return " ".join(palabras[:ZOCALO_PALABRAS]).upper().strip(" ,;:-–—")


def overlay_con_zocalo(texto: str, salida) -> Path | None:
    """Devuelve el PNG del overlay con el ZÓCALO escrito dentro de la caja negra de
    abajo: naranja, Montserrat, hasta 5 palabras, achicando la tipografía hasta que
    entre en `ZOCALO_CAJA` (nunca se escapa del recuadro). Sin overlay devuelve None;
    sin texto (o sin fuente) devuelve el overlay pelado."""
    base = _asset("REEL_OVERLAY", OVERLAY_REEL)
    if not base:
        return None
    from PIL import Image, ImageDraw, ImageFont
    img = Image.open(base).convert("RGBA")
    if img.size != (1080, 1920):
        img = img.resize((1080, 1920), Image.LANCZOS)
    texto = _zocalo_texto(texto)
    if texto and not FUENTE_ZOCALO.exists():
        logger.warning(f"Falta la fuente {FUENTE_ZOCALO}; el zócalo va vacío.")
    elif texto:
        x, y, w, h = ZOCALO_CAJA
        dib = ImageDraw.Draw(img)
        cuerpo = h + 24
        while True:
            fuente = ImageFont.truetype(str(FUENTE_ZOCALO), cuerpo)
            izq, arr, der, aba = dib.textbbox((0, 0), texto, font=fuente)
            if (der - izq <= w and aba - arr <= h) or cuerpo <= 14:
                break
            cuerpo -= 2
        # Se descuenta el offset del bbox para apoyar el texto exacto contra la caja.
        dib.text((x - izq, y + (h - (aba - arr)) / 2 - arr), texto, font=fuente,
                 fill=ZOCALO_COLOR)
        logger.info(f"Zócalo del reel: «{texto}» (cuerpo {cuerpo}px)")
    salida = Path(salida)
    img.save(salida)
    return salida

# Fuentes candidatas para el drawtext de la firma (Windows local + Ubuntu de la nube).
_FIRMA_FONTS = [
    r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\Arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
]


def _font_file() -> str | None:
    """Primera fuente existente de la lista (bold). None si no hay ninguna."""
    for p in _FIRMA_FONTS:
        if Path(p).exists():
            return p
    return None


def _esc_ff(path: str) -> str:
    """Escapa una ruta para usarla DENTRO de un filtergraph de ffmpeg (drawtext
    fontfile=/textfile=): barras hacia adelante y se escapa el ':' del 'C:'."""
    return str(path).replace("\\", "/").replace(":", "\\:")


def _dos_renglones(texto: str) -> str:
    """Parte el texto en EXACTAMENTE 2 renglones lo más parejos posible (corte por
    palabra más cercano a la mitad). Con una sola palabra lo deja como está."""
    palabras = texto.split()
    if len(palabras) < 2:
        return texto
    total = sum(len(p) for p in palabras) + len(palabras) - 1
    acum, corte, mejor = 0, 1, total
    for i in range(1, len(palabras)):
        acum += len(palabras[i - 1]) + 1
        dif = abs(acum - (total - acum))
        if dif < mejor:
            mejor, corte = dif, i
    return " ".join(palabras[:corte]) + "\n" + " ".join(palabras[corte:])


def _firma_drawtext(texto: str, in_label: str, work_dir: Path) -> tuple[str, str]:
    """Devuelve (fragmento_de_filtro, etiqueta_de_salida) que estampa la firma del
    corresponsal ARRIBA, a la derecha del logo, en 2 renglones, sobre una caja
    semitransparente (para que se lea sobre el fondo difuminado). El texto va por
    `textfile=` para no pelear con tildes/guiones/'·' en el filtergraph. Si no hay fuente
    disponible, no dibuja nada (devuelve el label original)."""
    font = _font_file()
    if not font:
        logger.warning("Sin fuente para la firma del reel; se omite el drawtext.")
        return "", in_label
    work_dir.mkdir(parents=True, exist_ok=True)
    firma_txt = work_dir / "firma.txt"
    firma_txt.write_text(_dos_renglones(texto.strip()), encoding="utf-8")
    # Arranca a la derecha del logo (mismo margen de arriba, corrido por el ancho del logo).
    mx = int(float(_cfg("REEL_LOGO_MARGEN_X", "48")))
    my = int(float(_cfg("REEL_LOGO_MARGEN_Y", "110")))
    ancho = int(float(_cfg("REEL_LOGO_ANCHO", "150")))
    x = mx + ancho + 22
    y = my + 6
    draw = (
        f"{in_label}drawtext=textfile='{_esc_ff(firma_txt)}'"
        f":fontfile='{_esc_ff(font)}':fontcolor=white:fontsize=26:line_spacing=6"
        f":box=1:boxcolor=black@0.5:boxborderw=14"
        f":x={x}:y={y}[vf]"
    )
    return draw, "[vf]"

# Transiciones que se van alternando entre placas (variedad visual).
TRANS = ["fade", "wipeleft", "slideup", "circleopen", "fadeblack", "wiperight", "slideleft"]


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"  # en la nube viene en el sistema


def _norm(idx: int, fps: int) -> str:
    # Escala/encuadra cada imagen a 1080x1920 exactas y fija sar/fps para xfade.
    return (f"[{idx}:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
            f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2:white,setsar=1,fps={fps}[s{idx}]")


def _run_ffmpeg(cmd: list, paso: str) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        logger.error(f"ffmpeg falló ({paso}):\n" + (r.stderr or "")[-1200:])
        raise RuntimeError(f"ffmpeg error: {paso}")


def _tiene_audio(src) -> bool:
    """True si el video tiene al menos una pista de audio (mira el `ffmpeg -i`)."""
    try:
        r = subprocess.run([_ffmpeg(), "-i", str(src)], capture_output=True, text=True)
        return "Audio:" in (r.stderr or "")
    except Exception:
        return False


def concat_videos(paths, salida, *, w: int = 1080, h: int = 1920):
    """Une varios videos cortos en UNO (para el corresponsal que manda 2-3 clips). Normaliza cada
    clip a la MISMA resolución (con pad), 30 fps, H.264 + AAC estéreo (silencio si al clip le falta
    audio) y después los concatena sin re-encodear. Devuelve `salida` (o el único video si es uno)."""
    ps = [Path(p) for p in paths if Path(p).is_file()]
    if not ps:
        raise ValueError("concat_videos: no hay videos para unir.")
    if len(ps) == 1:
        return ps[0]
    salida = Path(salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    partes_dir = salida.parent / (salida.stem + "_parts")
    partes_dir.mkdir(exist_ok=True)
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps=30,format=yuv420p")
    partes = []
    for i, p in enumerate(ps):
        out = partes_dir / f"n{i:02d}.mp4"
        if _tiene_audio(p):
            cmd = [_ffmpeg(), "-y", "-i", str(p), "-vf", vf,
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-ar", "44100", "-ac", "2", str(out)]
        else:  # sin audio: le pego una pista de silencio para que el concat no se rompa
            cmd = [_ffmpeg(), "-y", "-i", str(p), "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                   "-vf", vf, "-map", "0:v:0", "-map", "1:a:0", "-shortest",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-ar", "44100", "-ac", "2", str(out)]
        _run_ffmpeg(cmd, f"concat-normalizar {p.name}")
        partes.append(out)
    lista = partes_dir / "lista.txt"
    lista.write_text("".join(f"file '{q.as_posix()}'\n" for q in partes), encoding="utf-8")
    _run_ffmpeg([_ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(lista),
                 "-c", "copy", str(salida)], "concat-unir")
    logger.info(f"concat_videos: uní {len(ps)} clips → {salida.name}")
    return salida


def _fullbleed_on() -> bool:
    """Full bleed: el video/foto LLENA el cuadro 9:16 recortando lo que sobra.

    APAGADO por default desde el 2026-09-02 (pedido del usuario): el material que mandan
    los corresponsales va ENTERO, a su proporción, escalado hasta tocar los márgenes del
    reel, sobre el fondo difuminado. Recortar a 9:16 se comía gente, carteles y patentes.
    `REEL_FULLBLEED=1` lo vuelve a prender."""
    return str(_cfg("REEL_FULLBLEED", "0")).strip().lower() not in ("0", "no", "false", "off")


def _fullbleed_aplica(w: int, h: int) -> bool:
    """Full bleed SOLO si el material NO es HORIZONTAL (o sea: vertical o cuadrado).

    En un apaisado, recortar a 9:16 se come ~68% del ancho y deja al sujeto fuera de cuadro,
    así que los horizontales vuelven al fondo difuminado de siempre (pedido 2026-08-25).
    El corte se puede mover con `REEL_FULLBLEED_MAX_AR` (default 1.0 = hasta cuadrado)."""
    if not _fullbleed_on() or w <= 0 or h <= 0:
        return False
    try:
        max_ar = float(_cfg("REEL_FULLBLEED_MAX_AR", "1.0"))
    except ValueError:
        max_ar = 1.0
    return (w / h) <= max_ar


def _encuadre_fullbleed(src: Path, cont_w: int, cont_h: int, recorte, work_dir: Path):
    """Devuelve (nw, nh, x, y): a cuánto escalar el video para LLENAR 1080x1920 y desde
    dónde recortarlo, ENCUADRADO EN EL SUJETO.

    Busca caras en 3 fotogramas (reusa el detector de las placas) y se queda con el
    fotograma más representativo (el de mayor superficie de caras). El recorte se centra
    en el centro PONDERADO por el tamaño de cada cara (el primer plano pesa más) y deja
    aire arriba para no cortar cabezas. Sin caras (paisaje/objeto) o ante cualquier error:
    recorte centrado con leve sesgo hacia arriba (mismo criterio que `story_image._encuadrar`)."""
    W, H = 1080, 1920
    escala = max(W / max(1, cont_w), H / max(1, cont_h))
    nw = max(W, int(round(cont_w * escala)))
    nh = max(H, int(round(cont_h * escala)))
    x = (nw - W) // 2
    y = max(0, min(int((nh - H) * 0.30), nh - H))
    try:
        from PIL import Image
        from story_image import _caras_principales, _detect_faces  # detector de las placas
        dur = duration_seconds(src) or 0.0
        momentos = [dur * f for f in (0.25, 0.5, 0.75)] if dur > 1 else [0.0]
        mejor = []
        for i, seg in enumerate(momentos):
            tmp = work_dir / f"_enc_{i}_{src.stem[:20]}.jpg"
            try:
                frame_at(src, seg, tmp)
                img = Image.open(tmp)
                if recorte:  # mirar SOLO el contenido real (sin las barras negras)
                    rw, rh, rx, ry = recorte
                    img = img.crop((rx, ry, rx + rw, ry + rh))
                caras = _caras_principales(_detect_faces(img))
                if caras and sum(c[2] * c[3] for c in caras) > sum(c[2] * c[3] for c in mejor):
                    mejor = caras
            except Exception:
                continue
            finally:
                try:
                    tmp.unlink()
                except Exception:
                    pass
        if mejor:
            tot = sum(c[2] * c[3] for c in mejor)
            cx = sum((c[0] + c[2] / 2) * c[2] * c[3] for c in mejor) / tot
            y_top = min(c[1] for c in mejor)
            x = max(0, min(int(round(cx * escala - W / 2)), nw - W))
            y = max(0, min(int(round(y_top * escala - H * 0.14)), nh - H))
            logger.info(f"Encuadre full bleed: {len(mejor)} cara(s) detectada(s) → recorte x={x} y={y}")
        else:
            logger.info("Encuadre full bleed: sin caras (paisaje/objeto) → recorte centrado.")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"No pude calcular el encuadre del sujeto ({e}); recorte centrado.")
    return nw, nh, x, y


def _armar_reel(src: Path, salida: Path, *, audio: bool, max_seconds: float | None,
                firma: str | None, fondo: Path | None, logo_png: Path | None,
                overlay: Path | None, placa: Path | None, seg_placa: float,
                recorte: tuple[int, int, int, int] | None = None,
                encuadre: tuple[int, int, int, int] | None = None) -> None:
    """Arma el reel vertical en UNA sola pasada de ffmpeg (un único re-encode, para
    no pagar el doble de CPU en la nube): fondo borroso + video + logo + firma, y
    al final la placa de cierre concatenada. Si `recorte` (w,h,x,y) viene dado, primero
    le saca las barras negras al video para que el marco naranja tape ese negro."""
    ff = _ffmpeg()
    fps = 30
    con_audio = audio and has_audio(src)
    # Si el video trae barras negras horneadas, se las sacamos ANTES de todo, así el
    # contenido real es lo que se escala y el fondo naranja ocupa donde estaba el negro.
    pre = f"[0:v]crop={recorte[0]}:{recorte[1]}:{recorte[2]}:{recorte[3]}[src0];" if recorte else ""
    v0 = "[src0]" if recorte else "[0:v]"
    if encuadre:
        # FULL BLEED: el video LLENA el cuadro 9:16 (nada de franjas ni fondo borroso). Se
        # escala hasta cubrir y se recorta en el punto que calculó `_encuadre_fullbleed`
        # (centrado en el sujeto/las caras, con aire arriba).
        nw, nh, cx, cy = encuadre
        vf = f"{pre}{v0}scale={nw}:{nh},setsar=1,crop=1080:1920:{cx}:{cy}[v]"
    else:
        # Fondo: el propio video escalado a llenar + recortado + desenfocado.
        # Primer plano: el video escalado a entrar dentro de 1080x1920. Se superponen.
        vf = (
            f"{pre}{v0}split=2[bg][fg];"
            "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=luma_radius=40:luma_power=1,setsar=1[bgb];"
            "[fg]scale=1080:1920:force_original_aspect_ratio=decrease,setsar=1[fgs];"
            "[bgb][fgs]overlay=(W-w)/2:(H-h)/2[v]"
        )
    out_label = "[v]"
    inputs = ["-i", str(src)]
    n_in = 1  # cuántos INPUTS lleva ffmpeg (no alcanza con contar los argumentos)
    if fondo:
        # Degradado naranja que tapa el fondo borroso alrededor del video y se funde
        # con él en el borde. Va ANTES del logo y del overlay para no taparlos.
        idx = n_in
        inputs += ["-i", str(fondo)]
        n_in += 1
        vf += (f";[{idx}:v]scale=1080:1920,format=rgba[fd];"
               f"{out_label}[fd]overlay=0:0[vfd]")
        out_label = "[vfd]"
    if logo_png:
        # Marca de agua: el isotipo arriba a la izquierda, debajo de la barra de la app.
        idx = n_in
        inputs += ["-i", str(logo_png)]
        n_in += 1
        ancho = int(float(_cfg("REEL_LOGO_ANCHO", "150")))
        mx = int(float(_cfg("REEL_LOGO_MARGEN_X", "48")))
        my = int(float(_cfg("REEL_LOGO_MARGEN_Y", "110")))
        op = float(_cfg("REEL_LOGO_OPACIDAD", "0.92"))
        vf += (f";[{idx}:v]scale={ancho}:-1,format=rgba,colorchannelmixer=aa={op}[lg];"
               f"{out_label}[lg]overlay={mx}:{my}[vl]")
        out_label = "[vl]"
    if overlay:
        # Marco del diario (esquinas + caja del zócalo + barra con la web y las redes).
        idx = n_in
        inputs += ["-i", str(overlay)]
        n_in += 1
        vf += (f";[{idx}:v]scale=1080:1920,format=rgba[ov];"
               f"{out_label}[ov]overlay=0:0[vo]")
        out_label = "[vo]"
    if firma:
        draw, out_label = _firma_drawtext(firma, out_label, salida.parent)
        if draw:
            vf += ";" + draw

    if not placa:
        cmd = [ff, "-y", *inputs, "-filter_complex", vf, "-map", out_label]
        cmd += (["-map", "0:a?", "-c:a", "aac", "-b:a", "128k"] if audio else ["-an"])
        if max_seconds:
            cmd += ["-t", str(float(max_seconds))]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(salida)]
        _run_ffmpeg(cmd, "reel vertical")
        return

    # Con placa: el recorte va por `trim` (el -t de salida cortaría también la placa).
    corte = f",trim=duration={float(max_seconds)},setpts=PTS-STARTPTS" if max_seconds else ""
    vf += f";{out_label}fps={fps},setsar=1,format=yuv420p{corte}[vmain]"
    i_placa = n_in
    inputs += ["-loop", "1", "-t", str(seg_placa), "-i", str(placa)]
    n_in += 1
    vf += (f";[{i_placa}:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
           f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,setsar=1,fps={fps},"
           f"fade=t=in:st=0:d=0.4,format=yuv420p[vplaca]")
    if con_audio:
        # La placa va con silencio; el audio del video se normaliza para que concat
        # no se queje de formatos distintos entre las dos pistas.
        i_sil = n_in
        inputs += ["-f", "lavfi", "-t", str(seg_placa),
                   "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
        n_in += 1
        acorte = f",atrim=duration={float(max_seconds)},asetpts=PTS-STARTPTS" if max_seconds else ""
        vf += (f";[0:a]aresample=44100,aformat=sample_fmts=fltp:channel_layouts=stereo"
               f"{acorte}[amain]")
        vf += f";[vmain][amain][vplaca][{i_sil}:a]concat=n=2:v=1:a=1[vout][aout]"
        maps = ["-map", "[vout]", "-map", "[aout]", "-c:a", "aac", "-b:a", "128k"]
    else:
        vf += ";[vmain][vplaca]concat=n=2:v=1[vout]"
        maps = ["-map", "[vout]", "-an"]
    cmd = [ff, "-y", *inputs, "-filter_complex", vf, *maps,
           "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(salida)]
    _run_ffmpeg(cmd, "reel vertical + placa")


def to_vertical_reel(src, salida, *, audio: bool = True, max_seconds: float | None = None,
                     firma: str | None = None, logo: bool = True,
                     placa_final: bool = True, zocalo: str | None = None,
                     overlay: bool = True) -> Path:
    """Convierte un video cualquiera a un reel vertical 1080x1920 (9:16).

    El video se escala ENTERO (sin recortar) y se centra sobre un fondo borroso de
    sí mismo (misma estética que las historias, story_image._fit_blur). Mantiene el
    audio por defecto. Si se pasa `max_seconds`, recorta el reel a esa duración
    (ej. 60 para los reels sin desgrabar). Si se pasa `firma`, estampa una banda
    inferior con ese texto (la firma de la Red de Corresponsales).

    `logo=True` estampa el isotipo del diario arriba a la izquierda, el OVERLAY del diario
    (marco + caja del zócalo + barra con la web y las redes) va con el `zocalo` escrito
    adentro SOLO si `overlay=True` (default; `overlay=False` saca el marco y el texto del
    zócalo de una), y `placa_final=True` agrega al final la placa "Seguinos en redes"
    (5 s). Si el video trae BARRAS NEGRAS horneadas (apaisado dentro de un cuadro vertical,
    o directamente apaisado), se las recorta y el marco naranja tapa ese negro. Todo se
    apaga o se cambia por `.env` (REEL_LOGO / REEL_FONDO / REEL_OVERLAY / REEL_PLACA_FINAL /
    REEL_PLACA_SEG / REEL_RECORTE_NEGRO). Devuelve el .mp4.
    """
    src, salida = Path(src), Path(salida)
    logo_png = _asset("REEL_LOGO", LOGO_REEL) if logo else None
    placa = _asset("REEL_PLACA_FINAL", PLACA_FINAL) if placa_final else None
    seg_placa = float(_cfg("REEL_PLACA_SEG", str(PLACA_SEG)))
    # `overlay=False` saca el marco del diario (esquinas + caja del zócalo + barra web/redes)
    # Y con él el texto del zócalo (va dibujado adentro). Lo usa el diario; la radio deja True.
    overlay_png = (overlay_con_zocalo(zocalo or "", salida.parent / f"overlay_{salida.stem}.png")
                   if overlay else None)
    # Contenido real del video (sin las barras negras) → con eso se calcula el marco.
    recorte = detectar_recorte(src)
    cont_w, cont_h = (recorte[0], recorte[1]) if recorte else _dimensiones(src)
    fondo = fondo_enmarcado(cont_w, cont_h, salida.parent / f"fondo_{salida.stem}.png")
    # Por default el video va ENTERO, a su proporción, escalado hasta tocar los márgenes,
    # sobre el fondo difuminado. Con `REEL_FULLBLEED=1` los verticales/cuadrados se recortan
    # a 9:16 encuadrando el sujeto (los horizontales nunca: ver `_fullbleed_aplica`).
    if _fullbleed_aplica(cont_w, cont_h):
        encuadre = _encuadre_fullbleed(src, cont_w, cont_h, recorte, salida.parent)
    else:
        encuadre = None
        if _fullbleed_on():
            logger.info(f"Video horizontal ({cont_w}x{cont_h}): sin full bleed, va con fondo difuminado.")
    marca = dict(fondo=fondo, logo_png=logo_png, overlay=overlay_png, placa=placa,
                 seg_placa=seg_placa, recorte=recorte, encuadre=encuadre)
    try:
        _armar_reel(src, salida, audio=audio, max_seconds=max_seconds, firma=firma, **marca)
    except Exception as e:
        if not (fondo or logo_png or overlay_png or placa or recorte):
            raise
        # Si la marca hiciera fallar el filtergraph, el reel PELADO igual sale: nunca
        # se pierde la publicación por el fondo, el recorte, el logo, el overlay o la placa.
        logger.warning(f"El reel con marca falló ({e}); lo rehago pelado.")
        _armar_reel(src, salida, audio=audio, max_seconds=max_seconds, firma=firma,
                    fondo=None, logo_png=None, overlay=None, placa=None, seg_placa=0,
                    recorte=None, encuadre=None)
        marca = dict(fondo=None, logo_png=None, overlay=None, placa=None, seg_placa=0,
                     recorte=None, encuadre=None)
    logger.info(
        f"Reel vertical armado: {salida}"
        + (f" (recortado a {max_seconds}s)" if max_seconds else "")
        + (" + full-bleed" if marca["encuadre"] else "")
        + (" + recorte-negro" if marca["recorte"] else "")
        + (" + fondo" if marca["fondo"] else "")
        + (" + logo" if marca["logo_png"] else "")
        + (" + overlay" if marca["overlay"] else "")
        + (f" + placa final {seg_placa:.0f}s" if marca["placa"] else "")
    )
    return salida


def _foto_a_clip(foto, salida, seg: float, fps: int = 30) -> Path:
    """Loopea una FOTO a un .mp4 de `seg` segundos, sin audio. Por default la foto va
    ENTERA, escalada a su proporción hasta tocar los márgenes del reel. Con
    `REEL_FULLBLEED=1` se recorta a 9:16 encuadrando el sujeto (`story_image._encuadrar`).
    Sirve de 'video fuente' para pasarlo por `to_vertical_reel` y que reciba EXACTAMENTE
    el mismo branding que los videos (logo + overlay + placa)."""
    ff = _ffmpeg()
    fuente = Path(foto)
    if _fullbleed_on():
        try:
            from PIL import Image
            from story_image import _encuadrar  # cover a sangre enfocado en el sujeto
            img = Image.open(fuente)
            if _fullbleed_aplica(img.width, img.height):  # solo verticales/cuadradas
                enc = _encuadrar(img, 1080, 1920)
                fb = Path(salida).parent / f"_fb_{Path(salida).stem}.jpg"
                enc.save(fb, quality=92)
                fuente = fb
                logger.info("Foto encuadrada full bleed (9:16, centrada en el sujeto).")
            else:
                logger.info(f"Foto horizontal ({img.width}x{img.height}): sin full bleed, "
                            "va entera sobre fondo difuminado.")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"No pude encuadrar la foto full bleed ({e}); la dejo entera.")
    vf = ("scale=1080:1920:force_original_aspect_ratio=decrease,"
          "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1,format=yuv420p")
    cmd = [ff, "-y", "-loop", "1", "-t", f"{float(seg):.3f}", "-i", str(fuente),
           "-vf", vf, "-r", str(fps),
           "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(salida)]
    _run_ffmpeg(cmd, "foto→clip base")
    logger.info(f"Foto a clip base: {salida} ({float(seg):.0f}s)")
    return Path(salida)


def foto_a_reel(fotos, salida, *, seg: float | None = None, zocalo: str | None = None,
                firma: str | None = None, overlay: bool = True) -> Path:
    """Convierte una FOTO (o varias) de una nota en un reel vertical 9:16 con el MISMO
    criterio estético que los videos: fondo naranja que enmarca, logo arriba a la
    izquierda, overlay del diario con el ZÓCALO escrito, y la placa de cierre «Seguinos
    en redes» al final. Reusa `to_vertical_reel` (mismo re-encode/branding) pasándole un
    'video fuente' armado con la/s foto/s.

    - Una foto → se muestra fija, enmarcada en naranja (bandas si es apaisada/vertical).
    - Varias → slideshow con transiciones, todas branded.
    `seg` es la DURACIÓN TOTAL apuntada del reel (default `REEL_FOTO_SEG`, 30s): se le
    descuentan los segundos de la placa para que el total quede en ~`seg`. Devuelve el .mp4.
    """
    fotos = [Path(f) for f in fotos]
    salida = Path(salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    if not fotos:
        raise ValueError("foto_a_reel: no hay fotos para armar el reel")
    seg_total = float(seg if seg is not None else _cfg("REEL_FOTO_SEG", "30"))
    # La placa se suma al final; descontamos sus segundos para apuntar al total pedido.
    placa_on = _asset("REEL_PLACA_FINAL", PLACA_FINAL) is not None
    placa_seg = float(_cfg("REEL_PLACA_SEG", str(PLACA_SEG))) if placa_on else 0.0
    seg_cont = max(4.0, seg_total - placa_seg)
    base = salida.parent / f"_base_{salida.stem}.mp4"
    if len(fotos) == 1:
        _foto_a_clip(fotos[0], base, seg_cont)
    else:
        # Cada foto encuadrada a 1080x1920 (fondo desenfocado de sí misma) y unidas en un
        # slideshow que reparte los `seg_cont` segundos, con crossfade entre placas.
        from story_image import compose_foto_reel
        slides = [compose_foto_reel(f) for f in fotos]
        por = max(3.0, (seg_cont + (len(slides) - 1) * 0.6) / len(slides))
        build_slideshow(slides, base, seg=por, fade=0.6)
    logger.info(f"Foto-reel: {len(fotos)} foto(s) → {seg_cont:.0f}s de contenido + placa "
                f"(branding igual que los videos)")
    return to_vertical_reel(base, salida, audio=False, firma=firma, zocalo=zocalo or "",
                            overlay=overlay)


def frame_at(src, seconds, salida) -> Path:
    """Extrae el frame del video en el segundo indicado (el que Gemini marca como el más
    representativo). Si el segundo es 0 —o sea: nadie miró el video, que es lo que pasa cuando
    transcribe Groq— la portada se ELIGE SOLA con `mejor_frame` (caras + nitidez + exposición).
    Devuelve el .jpg."""
    src, salida = Path(src), Path(salida)
    seconds = max(0.0, float(seconds or 0))
    if seconds <= 0:
        return mejor_frame(src, salida)
    ff = _ffmpeg()
    cmd = [ff, "-y", "-ss", str(seconds), "-i", str(src), "-frames:v", "1", "-q:v", "2", str(salida)]
    try:
        _run_ffmpeg(cmd, f"frame en {seconds:.0f}s")
        if salida.exists() and salida.stat().st_size > 0:
            logger.info(f"Foto de portada extraída en {seconds:.0f}s: {salida}")
            return salida
    except Exception as e:
        logger.warning(f"No se pudo extraer el frame en {seconds:.0f}s ({e}); uso best_frame.")
    return best_frame(src, salida)


def duration_seconds(src) -> float:
    """Duración del video en segundos (parseando la salida de ffmpeg). 0 si no se puede."""
    ff = _ffmpeg()
    r = subprocess.run([ff, "-i", str(src)], capture_output=True, text=True)
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", r.stderr or "")
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def best_parts_clip(src, segmentos, salida, *, max_total: float = 60.0) -> Path | None:
    """Recorta los tramos destacados (lista de {inicio,fin} en segundos) y los une en un
    solo clip de COMO MÁXIMO `max_total` segundos, en orden. Devuelve el .mp4 unido, o
    None si no hay tramos válidos. Pensado para resumir videos largos a las mejores partes."""
    src, salida = Path(src), Path(salida)
    ff = _ffmpeg()
    dur = duration_seconds(src)
    tmpdir = salida.parent
    partes, total = [], 0.0
    for i, seg in enumerate(segmentos or []):
        ini = max(0.0, float(seg.get("inicio", 0)))
        fin = float(seg.get("fin", 0))
        if dur:
            fin = min(fin, dur)
        if total + (fin - ini) > max_total:
            fin = ini + (max_total - total)  # recorta el último tramo para no pasar del tope
        if fin <= ini:
            continue
        out = tmpdir / f"_seg{i}.mp4"
        d = fin - ini
        fo = max(0.0, d - 0.3)  # fade-out: arranca 0.3s antes del final
        vf = f"fade=t=in:st=0:d=0.3,fade=t=out:st={fo:.2f}:d=0.3"
        af = f"afade=t=in:st=0:d=0.3,afade=t=out:st={fo:.2f}:d=0.3"
        # -ss DESPUÉS de -i = corte preciso al frame (el tramo arranca/termina donde dijo Gemini).
        cmd = [ff, "-y", "-i", str(src), "-ss", str(ini), "-t", str(d),
               "-vf", vf, "-af", af,
               "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "44100", str(out)]
        try:
            _run_ffmpeg(cmd, f"tramo {i}")
            partes.append(out)
            total += (fin - ini)
        except Exception as e:
            logger.warning(f"Tramo {i} omitido: {e}")
        if total >= max_total:
            break
    if not partes:
        return None
    if len(partes) == 1:
        partes[0].replace(salida)
    else:
        lista = tmpdir / "_concat.txt"
        lista.write_text("".join(f"file '{p.name}'\n" for p in partes), encoding="utf-8")
        cmd = [ff, "-y", "-f", "concat", "-safe", "0", "-i", str(lista), "-c", "copy", str(salida)]
        try:
            _run_ffmpeg(cmd, "unir tramos")
        except Exception:
            cmd = [ff, "-y", "-f", "concat", "-safe", "0", "-i", str(lista),
                   "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(salida)]
            _run_ffmpeg(cmd, "unir tramos (re-encode)")
    logger.info(f"Clip de mejores partes: {salida} ({total:.0f}s, {len(partes)} tramo(s))")
    return salida


def remux_mp4(src, salida) -> Path:
    """Asegura un .mp4 (para hostear el video COMPLETO de la web). Copia si ya es mp4;
    si no, lo remuxea (o re-encodea como fallback)."""
    src, salida = Path(src), Path(salida)
    if src.suffix.lower() == ".mp4":
        import shutil
        shutil.copy(src, salida)
        return salida
    ff = _ffmpeg()
    try:
        _run_ffmpeg([ff, "-y", "-i", str(src), "-c", "copy", str(salida)], "remux mp4")
    except Exception:
        _run_ffmpeg([ff, "-y", "-i", str(src), "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                     "-c:a", "aac", str(salida)], "re-encode mp4")
    return salida


def best_frame(src, salida) -> Path:
    """Extrae el frame más representativo del video (filtro `thumbnail` de ffmpeg)
    como foto de portada. Devuelve el .jpg de salida."""
    src, salida = Path(src), Path(salida)
    ff = _ffmpeg()
    cmd = [ff, "-y", "-i", str(src), "-vf", "thumbnail=n=300",
           "-frames:v", "1", "-q:v", "2", str(salida)]
    _run_ffmpeg(cmd, "frame de portada")
    logger.info(f"Foto de portada extraída: {salida}")
    return salida


def _puntuar_frame(jpg: Path) -> float:
    """Puntúa un cuadro como candidato a PORTADA. Pesa, en orden: CARAS (grandes y centradas
    = alguien hablando en primer plano), NITIDEZ (descarta los cuadros movidos) y EXPOSICIÓN
    (descarta negros y quemados). Devuelve 0 si no se puede analizar."""
    try:
        import cv2
        import numpy as np
        from PIL import Image
        from story_image import _caras_principales, _detect_faces
    except Exception:  # noqa: BLE001
        return 0.0
    try:
        img = Image.open(jpg)
        gris = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2GRAY)
        h, w = gris.shape[:2]
        area = float(max(1, w * h))
        # Nitidez: varianza del Laplaciano (un cuadro movido da muy poca).
        nitidez = min(cv2.Laplacian(gris, cv2.CV_64F).var() / 500.0, 1.0)
        # Exposición: penaliza lo muy oscuro o quemado.
        brillo = float(gris.mean())
        exposicion = 1.0 if 45 <= brillo <= 210 else max(0.0, 1 - abs(brillo - 127) / 127)
        # Caras: cuánto ocupan y qué tan centradas están.
        caras = _caras_principales(_detect_faces(img))
        if caras:
            ocupacion = min(sum(c[2] * c[3] for c in caras) / area * 6.0, 1.0)
            mayor = max(caras, key=lambda c: c[2] * c[3])
            cx = (mayor[0] + mayor[2] / 2) / w
            centrado = 1.0 - min(abs(cx - 0.5) * 2, 1.0)
            caras_score = 0.65 * ocupacion + 0.35 * centrado
        else:
            caras_score = 0.0
        return 0.55 * caras_score + 0.30 * nitidez + 0.15 * exposicion
    except Exception:  # noqa: BLE001
        return 0.0


def _procesable(src: Path) -> bool:
    """¿ffmpeg puede DECODIFICAR este video? Prueba sacar 1 cuadro y descartarlo (rápido).

    Existe porque hay videos de celular cuyos metadatos de color son inválidos (el stream dice
    `reserved`) y ffmpeg falla con «Invalid color range» al inicializar su grafo interno —que
    arma SIEMPRE, aunque uno no pase ningún `-vf`—. Sin este chequeo, el error aparecía recién
    al armar la portada o el reel, con la nota ya desgrabada."""
    try:
        _run_ffmpeg([_ffmpeg(), "-v", "error", "-i", str(src), "-frames:v", "1",
                     "-f", "null", "-"], "chequeo de video")
        return True
    except Exception:  # noqa: BLE001
        return False


def reparar_metadatos(src, salida) -> Path | None:
    """Reescribe los METADATOS de color del H.264 sin tocar el video (stream copy + bitstream
    filter). Devuelve el archivo reparado o None.

    Es la cura del «Invalid color range»: NO decodifica (por eso no puede fallar por el mismo
    motivo) y deja el color declarado como BT.709 rango limitado, que es lo normal. El video y
    el audio quedan intactos, bit por bit."""
    src, salida = Path(src), Path(salida)
    meta = ("h264_metadata=video_full_range_flag=0:colour_primaries=1:"
            "transfer_characteristics=1:matrix_coefficients=1")
    try:
        _run_ffmpeg([_ffmpeg(), "-y", "-i", str(src), "-c", "copy", "-bsf:v", meta,
                     str(salida)], "reparar metadatos de color")
        if salida.exists() and salida.stat().st_size > 0:
            return salida
    except Exception as e:  # noqa: BLE001
        logger.warning(f"No pude reparar los metadatos por bitstream ({e}); pruebo re-codificando.")
    # Respaldo: re-codificar forzando el color (más lento, pero salva videos muy rotos).
    try:
        _run_ffmpeg([_ffmpeg(), "-y", "-i", str(src),
                     "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                     "-color_range", "tv", "-colorspace", "bt709",
                     "-color_primaries", "bt709", "-color_trc", "bt709",
                     "-c:a", "aac", str(salida)], "re-codificar video roto")
        if salida.exists() and salida.stat().st_size > 0:
            return salida
    except Exception as e:  # noqa: BLE001
        logger.error(f"Tampoco pude re-codificar el video roto: {e}")
    return None


def asegurar_procesable(src, work_dir=None) -> Path:
    """Devuelve un video que ffmpeg SÍ puede procesar (el mismo, o una copia reparada).

    Se llama UNA vez, al principio: normalizar la fuente en la puerta de entrada evita que el
    problema aparezca después en cada consumidor (portada, reel, audio…). Si no se puede
    reparar, devuelve el original (que cada paso maneje su error como pueda)."""
    src = Path(src)
    if _procesable(src):
        return src
    logger.warning(f"«{src.name}»: ffmpeg no lo puede procesar (metadatos rotos); lo reparo…")
    destino = Path(work_dir or src.parent) / f"_fix_{src.stem}.mp4"
    fijo = reparar_metadatos(src, destino)
    if fijo and _procesable(fijo):
        logger.info(f"Video reparado OK: {fijo.name} (se usa este de acá en adelante).")
        return fijo
    logger.error(f"No se pudo reparar «{src.name}»; sigo con el original.")
    return src


def _extraer_frame(src: Path, t: float, salida: Path, *, escala: int = 0,
                   etiqueta: str = "frame") -> bool:
    """Extrae UN cuadro. Devuelve True/False — NO lanza.

    Clave: hay videos (típicos de celular por WhatsApp) con metadatos de color rotos que hacen
    fallar CUALQUIER filtro de ffmpeg («Invalid color range» → «Error reinitializing filters»).
    Por eso, si el intento CON filtro falla, se reintenta SIN filtros: la extracción cruda
    (`-ss` + `-frames:v 1`) sobrevive a esos metadatos."""
    ff = _ffmpeg()
    intentos = []
    if escala:
        intentos.append(["-vf", f"scale={escala}:-2", "-q:v", "5"])
    intentos.append(["-q:v", "2"])  # sin filtros: el camino que aguanta metadatos rotos
    for extra in intentos:
        try:
            _run_ffmpeg([ff, "-y", "-ss", f"{float(t):.2f}", "-i", str(src),
                         "-frames:v", "1", *extra, str(salida)], etiqueta)
            if salida.exists() and salida.stat().st_size > 0:
                return True
        except Exception:  # noqa: BLE001
            continue
    return False


def portada_segura(src, salida, *, muestras: int = 8):
    """Portada del video que NUNCA rompe la publicación. Devuelve Path o None.

    Una nota NO se puede perder porque no se pudo elegir un cuadro: es un paso cosmético.
    Cascada, de mejor a peor:
      1. `mejor_frame`  — elección por caras/nitidez/exposición (usa filtros).
      2. cuadro simple SIN filtros al 25% del video (aguanta metadatos de color rotos).
      3. `best_frame`   — el filtro `thumbnail` de ffmpeg.
    Si todo falla devuelve None y el llamador sigue sin portada (o la saca del reel, que al
    estar re-codificado tiene metadatos limpios)."""
    src, salida = Path(src), Path(salida)
    try:
        out = mejor_frame(src, salida, muestras=muestras)
        if out and Path(out).exists() and Path(out).stat().st_size > 0:
            return Path(out)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Portada: la elección inteligente falló ({e}); pruebo un cuadro simple.")
    try:
        dur = duration_seconds(src) or 0
    except Exception:  # noqa: BLE001
        dur = 0
    for t in (dur * 0.25 if dur > 2 else 1.0, 0.0):
        if _extraer_frame(src, t, salida, etiqueta=f"portada simple en {t:.0f}s"):
            logger.info(f"Portada: cuadro simple en {t:.0f}s (sin filtros).")
            return salida
    try:
        out = best_frame(src, salida)
        if out and Path(out).exists() and Path(out).stat().st_size > 0:
            return Path(out)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Portada: el thumbnail de ffmpeg también falló ({e}).")
    logger.error(f"Portada: no se pudo sacar NINGÚN cuadro de «{Path(src).name}».")
    return None


def mejor_frame(src, salida, *, muestras: int = 8) -> Path:
    """Elige la FOTO DE PORTADA sin que una IA tenga que MIRAR el video.

    Muestrea `muestras` cuadros repartidos (saltea el arranque y el final, que suelen ser
    cortinas o el cámara acomodándose), los puntúa con `_puntuar_frame` (caras + nitidez +
    exposición) y extrae el ganador en calidad plena. Ante cualquier problema cae a
    `best_frame` (el filtro `thumbnail` de ffmpeg). Devuelve el .jpg."""
    src, salida = Path(src), Path(salida)
    try:
        dur = duration_seconds(src)
        if dur <= 2:
            return best_frame(src, salida)
        ini, fin = dur * 0.08, dur * 0.92  # sin cortinas ni cierre
        paso = (fin - ini) / max(1, muestras - 1)
        tmp_dir = salida.parent
        mejor_t, mejor_p = None, -1.0
        for i in range(muestras):
            t = ini + paso * i
            chico = tmp_dir / f"_cand_{i}_{salida.stem}.jpg"
            try:
                # Chico (ancho 480) = analizar es barato; el ganador se extrae en calidad plena.
                # `_extraer_frame` reintenta SIN filtros si el video trae metadatos rotos.
                if not _extraer_frame(src, t, chico, escala=480, etiqueta=f"candidato {i}"):
                    continue
                p = _puntuar_frame(chico)
                if p > mejor_p:
                    mejor_t, mejor_p = t, p
            except Exception:  # noqa: BLE001
                continue
            finally:
                try:
                    chico.unlink()
                except Exception:  # noqa: BLE001
                    pass
        if mejor_t is None:
            return best_frame(src, salida)
        if not _extraer_frame(src, mejor_t, salida, etiqueta="portada elegida"):
            return best_frame(src, salida)
        logger.info(f"Portada elegida sola en {mejor_t:.0f}s (puntaje {mejor_p:.2f}).")
        return salida
    except Exception as e:  # noqa: BLE001
        logger.warning(f"No pude elegir la portada por puntaje ({e}); uso el thumbnail de ffmpeg.")
        return best_frame(src, salida)


def extract_audio(src, salida) -> Path:
    """Extrae el audio del video a mono 16 kHz (liviano para mandar a Gemini).
    Devuelve el archivo de audio (.mp3 según la extensión de `salida`)."""
    src, salida = Path(src), Path(salida)
    ff = _ffmpeg()
    cmd = [ff, "-y", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
           "-b:a", "64k", str(salida)]
    _run_ffmpeg(cmd, "extraer audio")
    logger.info(f"Audio extraído: {salida}")
    return salida


def _duraciones_parejas(n: int, seg: float, fade: float) -> list[float]:
    """Cuánto dura cada placa para que TODAS se vean el MISMO tiempo (2026-09-01).

    El problema: con `xfade`, la primera y la última placa tienen UN fundido (de salida y
    de entrada) mientras que las del medio tienen DOS. Si todas duraran lo mismo, las del
    medio se verían solas `seg - 2*fade` y las de las puntas `seg - fade`: la primera
    parecía durar más, que es justo lo que se veía.

    Solución: darle a cada placa el tiempo solo que le corresponde MÁS los fundidos que le
    tocan. Así el tiempo VISIBLE es idéntico para todas y la duración total no cambia."""
    if n <= 1:
        return [seg]
    solo = seg - 2 * (n - 1) * fade / n          # mantiene el total en n*seg - (n-1)*fade
    return [round(solo + (fade if i in (0, n - 1) else 2 * fade), 3) for i in range(n)]


def build_slideshow(imagenes, salida, *, seg: float = 3.5, fade: float = 0.6, fps: int = 30) -> Path:
    """imagenes: lista de Paths (cada una una placa 9:16). Devuelve el .mp4.

    `seg` es la duración MEDIA por placa: el reparto real lo hace `_duraciones_parejas`
    para que todas se vean el mismo tiempo. El total sigue siendo `n*seg - (n-1)*fade`."""
    imgs = [str(p) for p in imagenes]
    n = len(imgs)
    salida = Path(salida)
    ff = _ffmpeg()
    if n == 0:
        raise ValueError("No hay imágenes para el reel")

    dur = _duraciones_parejas(n, seg, fade)
    inputs = []
    for p, d in zip(imgs, dur):
        inputs += ["-loop", "1", "-t", str(d), "-i", p]

    fc = [_norm(i, fps) for i in range(n)]
    if n == 1:
        last = "s0"
    else:
        prev = "s0"
        for i in range(1, n):
            # El fundido arranca `fade` antes de que termine lo acumulado hasta acá.
            off = round(sum(dur[:i]) - i * fade, 3)
            tr = TRANS[(i - 1) % len(TRANS)]
            out = f"v{i}"
            fc.append(f"[{prev}][s{i}]xfade=transition={tr}:duration={fade}:offset={off}[{out}]")
            prev = out
        last = prev

    cmd = [ff, "-y", *inputs, "-filter_complex", ";".join(fc), "-map", f"[{last}]",
           "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(salida)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        logger.error("ffmpeg falló:\n" + (r.stderr or "")[-1200:])
        raise RuntimeError("ffmpeg error al armar el reel")
    logger.info(f"Reel armado: {salida} ({n} placas)")
    return salida
