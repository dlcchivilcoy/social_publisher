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


def _firma_drawtext(texto: str, in_label: str, work_dir: Path) -> tuple[str, str]:
    """Devuelve (fragmento_de_filtro, etiqueta_de_salida) que superpone una banda
    inferior semitransparente con `texto` (la firma del corresponsal) sobre `in_label`.
    El texto va por `textfile=` para no pelear con tildes/guiones/'·' en el filtergraph.
    Si no hay fuente disponible, no dibuja nada (devuelve el label original)."""
    font = _font_file()
    if not font:
        logger.warning("Sin fuente para la firma del reel; se omite el drawtext.")
        return "", in_label
    work_dir.mkdir(parents=True, exist_ok=True)
    firma_txt = work_dir / "firma.txt"
    # Envuelve a ~34 caracteres por línea para que entre en los 1080 de ancho.
    wrapped = "\n".join(textwrap.wrap(texto.strip(), width=34)) or texto.strip()
    firma_txt.write_text(wrapped, encoding="utf-8")
    draw = (
        f"{in_label}drawtext=textfile='{_esc_ff(firma_txt)}'"
        f":fontfile='{_esc_ff(font)}':fontcolor=white:fontsize=33:line_spacing=8"
        f":box=1:boxcolor=black@0.55:boxborderw=24"
        f":x=(w-text_w)/2:y=h-text_h-90[vf]"
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


def _armar_reel(src: Path, salida: Path, *, audio: bool, max_seconds: float | None,
                firma: str | None, fondo: Path | None, logo_png: Path | None,
                overlay: Path | None, placa: Path | None, seg_placa: float,
                recorte: tuple[int, int, int, int] | None = None) -> None:
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
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(salida)]
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
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(salida)]
    _run_ffmpeg(cmd, "reel vertical + placa")


def to_vertical_reel(src, salida, *, audio: bool = True, max_seconds: float | None = None,
                     firma: str | None = None, logo: bool = True,
                     placa_final: bool = True, zocalo: str | None = None) -> Path:
    """Convierte un video cualquiera a un reel vertical 1080x1920 (9:16).

    El video se escala ENTERO (sin recortar) y se centra sobre un fondo borroso de
    sí mismo (misma estética que las historias, story_image._fit_blur). Mantiene el
    audio por defecto. Si se pasa `max_seconds`, recorta el reel a esa duración
    (ej. 60 para los reels sin desgrabar). Si se pasa `firma`, estampa una banda
    inferior con ese texto (la firma de la Red de Corresponsales).

    `logo=True` estampa el isotipo del diario arriba a la izquierda, el OVERLAY del diario
    (marco + caja del zócalo + barra con la web y las redes) va siempre con el `zocalo`
    escrito adentro, y `placa_final=True` agrega al final la placa "Seguinos en redes"
    (5 s). Si el video trae BARRAS NEGRAS horneadas (apaisado dentro de un cuadro vertical,
    o directamente apaisado), se las recorta y el marco naranja tapa ese negro. Todo se
    apaga o se cambia por `.env` (REEL_LOGO / REEL_FONDO / REEL_OVERLAY / REEL_PLACA_FINAL /
    REEL_PLACA_SEG / REEL_RECORTE_NEGRO). Devuelve el .mp4.
    """
    src, salida = Path(src), Path(salida)
    logo_png = _asset("REEL_LOGO", LOGO_REEL) if logo else None
    placa = _asset("REEL_PLACA_FINAL", PLACA_FINAL) if placa_final else None
    seg_placa = float(_cfg("REEL_PLACA_SEG", str(PLACA_SEG)))
    overlay = overlay_con_zocalo(zocalo or "", salida.parent / f"overlay_{salida.stem}.png")
    # Contenido real del video (sin las barras negras) → con eso se calcula el marco.
    recorte = detectar_recorte(src)
    cont_w, cont_h = (recorte[0], recorte[1]) if recorte else _dimensiones(src)
    fondo = fondo_enmarcado(cont_w, cont_h, salida.parent / f"fondo_{salida.stem}.png")
    marca = dict(fondo=fondo, logo_png=logo_png, overlay=overlay, placa=placa,
                 seg_placa=seg_placa, recorte=recorte)
    try:
        _armar_reel(src, salida, audio=audio, max_seconds=max_seconds, firma=firma, **marca)
    except Exception as e:
        if not (fondo or logo_png or overlay or placa or recorte):
            raise
        # Si la marca hiciera fallar el filtergraph, el reel PELADO igual sale: nunca
        # se pierde la publicación por el fondo, el recorte, el logo, el overlay o la placa.
        logger.warning(f"El reel con marca falló ({e}); lo rehago pelado.")
        _armar_reel(src, salida, audio=audio, max_seconds=max_seconds, firma=firma,
                    fondo=None, logo_png=None, overlay=None, placa=None, seg_placa=0,
                    recorte=None)
        marca = dict(fondo=None, logo_png=None, overlay=None, placa=None, seg_placa=0,
                     recorte=None)
    logger.info(
        f"Reel vertical armado: {salida}"
        + (f" (recortado a {max_seconds}s)" if max_seconds else "")
        + (" + recorte-negro" if marca["recorte"] else "")
        + (" + fondo" if marca["fondo"] else "")
        + (" + logo" if marca["logo_png"] else "")
        + (" + overlay" if marca["overlay"] else "")
        + (f" + placa final {seg_placa:.0f}s" if marca["placa"] else "")
    )
    return salida


def frame_at(src, seconds, salida) -> Path:
    """Extrae el frame del video en el segundo indicado (el que Gemini marca como el
    más representativo). Si falla o el segundo es 0, cae a best_frame(). Devuelve el .jpg."""
    src, salida = Path(src), Path(salida)
    seconds = max(0.0, float(seconds or 0))
    if seconds <= 0:
        return best_frame(src, salida)
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
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "44100", str(out)]
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
                   "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(salida)]
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
        _run_ffmpeg([ff, "-y", "-i", str(src), "-c:v", "libx264", "-pix_fmt", "yuv420p",
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


def build_slideshow(imagenes, salida, *, seg: float = 3.5, fade: float = 0.6, fps: int = 30) -> Path:
    """imagenes: lista de Paths (cada una una placa 9:16). Devuelve el .mp4."""
    imgs = [str(p) for p in imagenes]
    n = len(imgs)
    salida = Path(salida)
    ff = _ffmpeg()
    if n == 0:
        raise ValueError("No hay imágenes para el reel")

    inputs = []
    for p in imgs:
        inputs += ["-loop", "1", "-t", str(seg), "-i", p]

    fc = [_norm(i, fps) for i in range(n)]
    if n == 1:
        last = "s0"
    else:
        prev = "s0"
        for i in range(1, n):
            off = round(i * (seg - fade), 3)
            tr = TRANS[(i - 1) % len(TRANS)]
            out = f"v{i}"
            fc.append(f"[{prev}][s{i}]xfade=transition={tr}:duration={fade}:offset={off}[{out}]")
            prev = out
        last = prev

    cmd = [ff, "-y", *inputs, "-filter_complex", ";".join(fc), "-map", f"[{last}]",
           "-r", str(fps), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(salida)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        logger.error("ffmpeg falló:\n" + (r.stderr or "")[-1200:])
        raise RuntimeError("ffmpeg error al armar el reel")
    logger.info(f"Reel armado: {salida} ({n} placas)")
    return salida
