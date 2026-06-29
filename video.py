"""Arma un video vertical (reel) 1080x1920 a partir de imágenes, con transiciones
crossfade (xfade) entre placas, SIN audio. Usa el ffmpeg de imageio_ffmpeg (local)
o el del sistema (en la nube)."""
import re
import subprocess
import textwrap
from pathlib import Path

from utils.logger import get_logger

logger = get_logger("video")

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


def to_vertical_reel(src, salida, *, audio: bool = True, max_seconds: float | None = None,
                     firma: str | None = None) -> Path:
    """Convierte un video cualquiera a un reel vertical 1080x1920 (9:16).

    El video se escala ENTERO (sin recortar) y se centra sobre un fondo borroso de
    sí mismo (misma estética que las historias, story_image._fit_blur). Mantiene el
    audio por defecto. Si se pasa `max_seconds`, recorta el reel a esa duración
    (ej. 60 para los reels sin desgrabar). Si se pasa `firma`, estampa una banda
    inferior con ese texto (la firma de la Red de Corresponsales). Devuelve el .mp4.
    """
    src, salida = Path(src), Path(salida)
    ff = _ffmpeg()
    # Fondo: el propio video escalado a llenar + recortado + desenfocado.
    # Primer plano: el video escalado a entrar dentro de 1080x1920. Se superponen.
    vf = (
        "[0:v]split=2[bg][fg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=luma_radius=40:luma_power=1,setsar=1[bgb];"
        "[fg]scale=1080:1920:force_original_aspect_ratio=decrease,setsar=1[fgs];"
        "[bgb][fgs]overlay=(W-w)/2:(H-h)/2[v]"
    )
    out_label = "[v]"
    if firma:
        draw, out_label = _firma_drawtext(firma, "[v]", salida.parent)
        if draw:
            vf = vf + ";" + draw
    cmd = [ff, "-y", "-i", str(src), "-filter_complex", vf, "-map", out_label]
    if audio:
        # Mapea el audio si existe (el '?' evita fallar si el video no tiene pista).
        cmd += ["-map", "0:a?", "-c:a", "aac", "-b:a", "128k"]
    else:
        cmd += ["-an"]
    if max_seconds:
        cmd += ["-t", str(float(max_seconds))]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(salida)]
    _run_ffmpeg(cmd, "reel vertical")
    logger.info(f"Reel vertical armado: {salida}" + (f" (recortado a {max_seconds}s)" if max_seconds else ""))
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
