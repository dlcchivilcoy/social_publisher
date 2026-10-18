"""Reels de DECLARACIONES: un audio con la foto o el video de quien habla (pedido 2026-10-10).

Lo que pidió el usuario: «aceptar archivos de audio y unirlos con foto/video y agregar nombre de la
persona con su cargo y texto de lo que dijo y un espectro de frecuencias animado color naranja en
consonancia con mi estética, además que si es una foto o video horizontal o cuadrado que se lleve a
4:5». Referencia: el reel de @diarioyradio del 9/10/2026 (Néstor Dabi, el helicóptero).

Cómo queda el cuadro (mismo sistema visual que lo que llega por WhatsApp, `video.CORR_*`):
  · la marca arriba (`video._marca_corr`) y el isologo (lo pega `to_vertical_reel`);
  · la imagen: horizontal o cuadrada → reel 4:5 (1080x1350) con la imagen a todo el ancho;
    vertical → reel 9:16 a sangre, encuadrado en las caras;
  · el bloque, centrado en el eje de las tarjetas (X 492): el CARGO en mayúsculas chicas, el NOMBRE
    en la caja naranja, la FRASE en la caja grafito (cambia con el audio), el ESPECTRO naranja que
    se mueve con la voz y la línea de progreso;
  · de fondo, la misma imagen difuminada y oscurecida.
Desde el 2026-10-12 el 4:5 va en el estilo BLOQUE de `video._plan_bloque` (pedido del usuario): la
imagen desde el borde de arriba, la línea naranja y debajo el bloque blanco o grafito (`tema`, se
alternan de un posteo al otro) con el isologo y el logo, el NOMBRE en naranja, el cargo, la frase,
el espectro y el progreso. `REEL_BLOQUE_4X5=0` vuelve al de antes.

El audio manda la duración (el tramo elegido, hasta MAX_SEG). Después `video.to_vertical_reel(
compuesto=True, estilo="corresponsal")` le pega el isologo y la placa de cierre, igual que al montaje.
Lo usan el desgrabador (corresponsales por WhatsApp) y policiales (copia en estetica_bot/).
"""
import json
import subprocess
import tempfile
from pathlib import Path

import video as V
from utils.logger import get_logger

logger = get_logger("declaracion")

MAX_SEG = 55.0                  # + la placa de cierre (5 s) = 60
MIN_SEG = 4.0
FPS = 30
AR_4X5 = 0.95                   # ancho/alto desde el que el reel sale 4:5
ALTO_4X5 = V.CORR_ALTO_APAISADO  # 1350
EJE_X = V.CORR_X + V.CORR_CAJA_ANCHO // 2   # 492: el eje de las cajas, lejos de los botones de IG
MARGEN_ABAJO = 30
TOPE_IMAGEN = V.CORR_TOPE_IMAGEN            # 290: la imagen arranca debajo del isologo
CARGO = (26, 21, "500")         # cuerpo, mínimo, peso (mayúsculas)
NOMBRE = (30, 56, "500")        # cuerpo, alto de la caja, peso (mayúsculas, como la volanta)
GAP_CARGO = 10
GAP = 12
GAP_ESPECTRO = 22
ESPECTRO = (760, 84)            # ancho y alto de las barras
BARRAS = 56
GAP_PROGRESO = 14
PROGRESO = 4
Y_ABAJO_9X16 = 1660             # en 9:16 el bloque termina acá: deja lugar al texto de IG
# 4:5 estilo bloque
NOMBRE_BLOQUE = (36, 26, "700")  # cuerpo, mínimo, peso (mayúsculas, naranja: como la volanta)
GAP_NOMBRE = 12
GAP_FRASE = 26
FRASE_BLOQUE = (60, 40)         # tope y piso de la frase (todas con la misma letra)
RECORTE_MIN = 0.5               # la imagen conserva al menos la mitad de su alto
OSCURO_FONDO = 0.42
FADE_IN, FADE_OUT = 0.15, 0.4


class DeclaracionError(Exception):
    pass


# =============================================================================
# Texto: frases y tramo
# =============================================================================

def tramo_valido(tramo, dur_audio: float) -> tuple:
    """(desde, hasta) dentro del audio, de MIN_SEG a MAX_SEG. Si el audio entra entero, entero; si
    la IA no dio un tramo que sirva, los primeros MAX_SEG."""
    dur = max(0.0, float(dur_audio or 0))
    if dur <= MAX_SEG:
        return 0.0, dur
    try:
        d = max(0.0, float((tramo or {}).get("desde")))
        h = min(dur, float((tramo or {}).get("hasta")))
    except (TypeError, ValueError, AttributeError):
        return 0.0, MAX_SEG
    if h - d < MIN_SEG * 2:
        return 0.0, MAX_SEG
    return d, min(h, d + MAX_SEG)


def _lectura(texto: str) -> float:
    return max(2.5, len((texto or "").split()) / 2.6)


def frases_validas(propuestas, desde: float, hasta: float, respaldo: str = "") -> list:
    """[(inicio, fin, texto)] relativas al tramo [desde, hasta] del audio. Cada frase se queda al
    menos lo que se tarda en leerla; la primera arranca en 0 (es la portada). Sin ninguna que
    sirva, una sola con `respaldo` todo el reel."""
    dur = max(0.1, hasta - desde)
    lista = []
    for p in sorted(propuestas or [], key=lambda p: float((p or {}).get("desde") or 0)):
        try:
            t = float(p.get("desde")) - desde
        except (TypeError, ValueError, AttributeError):
            continue
        texto = " ".join(str(p.get("texto") or "").split()).strip(" .")
        if not (2 <= len(texto.split()) <= 9) or any(texto.lower() == x[1].lower() for x in lista):
            continue
        if t > dur - 1.5:
            continue
        t = 0.0 if not lista else max(t, 0.0)
        if lista and t < lista[-1][0] + _lectura(lista[-1][1]):
            continue
        lista.append((t, texto))
    if not lista and respaldo:
        lista = [(0.0, " ".join(respaldo.split()))]
    return [(t, (lista[i + 1][0] if i + 1 < len(lista) else dur), x) for i, (t, x) in enumerate(lista)]


# =============================================================================
# Diseño
# =============================================================================

def _cargo_renglones(cargo: str, f: dict) -> tuple:
    """(cuerpo, [renglones]) del cargo en mayúsculas: un renglón que entre en 784 px achicando
    hasta el mínimo; si no, dos."""
    texto = " ".join((cargo or "").split()).upper()
    if not texto:
        return 0, []
    tam, minimo, peso = CARGO
    for c in range(tam, minimo - 1, -1):
        if V._ancho_texto(texto, f["f_r"], c, peso) <= V.CORR_CAJA_ANCHO:
            return c, [texto]
    palabras, uno = texto.split(), []
    while palabras and V._ancho_texto(" ".join(uno + [palabras[0]]), f["f_r"], minimo, peso) <= V.CORR_CAJA_ANCHO:
        uno.append(palabras.pop(0))
    return minimo, [" ".join(uno), " ".join(palabras)][:2]


def _nombre_caja(nombre: str, f: dict) -> tuple:
    """(cuerpo, texto, ancho de la caja) del nombre en mayúsculas."""
    texto = " ".join((nombre or "").split()).upper()
    if not texto:
        return 0, "", 0
    tam, _alto, peso = NOMBRE
    c = tam
    while c > V.CORR_VOLANTA_MIN and V._ancho_texto(texto, f["f_r"], c, peso) > V.CORR_CAJA_ANCHO - 40:
        c -= 1
    return c, texto, min(V.CORR_CAJA_ANCHO, round(V._ancho_texto(texto, f["f_r"], c, peso)) + 40)


def _alto_frase(texto: str, f: dict) -> int:
    t = V._tarjeta_corr("", texto, f, 0)
    return t["rect"][3] - t["rect"][1]


def bloque(nombre: str, cargo: str, frases: list, f: dict, y: int) -> dict:
    """Cargo, nombre, el lugar de la frase, el espectro y el progreso desde `y`. Devuelve los
    renglones y cajas de lo FIJO (cargo y nombre) y las posiciones de lo que se mueve."""
    bloques, cajas, y0 = [], [], y
    c, renglones = _cargo_renglones(cargo, f)
    if renglones:
        _t, _m, peso = CARGO
        asc, may = V._metricas(f["f_r"], c, peso)
        salto = round(c * 1.25)
        for i, r in enumerate(renglones):
            bloques.append((r, c, y + i * salto - asc + may, f["f_r"], peso, V.CORR_BLANCO2,
                            ("centro", EJE_X)))
        y += (len(renglones) - 1) * salto + may + GAP_CARGO
    cn, tn, wn = _nombre_caja(nombre, f)
    if tn:
        _t, alto, peso = NOMBRE
        x0 = EJE_X - wn // 2
        cajas.append((x0, y, x0 + wn, y + alto, V.CORR_NARANJA_CAJA))
        asc, may = V._metricas(f["f_r"], cn, peso)
        bloques.append((tn, cn, round(y + (alto - may) / 2 - asc + may), f["f_r"], peso, V.BLANCO,
                        ("centro", EJE_X)))
        y += alto + GAP
    y_frase = y
    alto_frase = max([_alto_frase(t, f) for _a, _b, t in frases] or [0])
    y += alto_frase + GAP_ESPECTRO
    y_espectro = y
    y += ESPECTRO[1] + GAP_PROGRESO
    y_progreso = y
    y += PROGRESO
    return dict(bloques=bloques, cajas=cajas, y=y0, y_frase=y_frase, y_espectro=y_espectro,
                y_progreso=y_progreso, fin=y, alto=y - y0)


def bloque_4x5(nombre: str, cargo: str, frases: list, f: dict, tema: str, y: int) -> dict:
    """El contenido del bloque del 4:5 estilo bloque desde `y` (el tope del logo), a la izquierda
    en X 80: el logo, el NOMBRE en naranja, el cargo, el lugar de la frase (todas con la misma
    letra, la más grande que les entra a todas), el espectro y el progreso."""
    t = V.BLOQUE_TEMAS[tema]
    bloques, yy = [], y + V.BLOQUE_ISOLOGO + V.BLOQUE_GAP_LOGO
    tn = " ".join((nombre or "").split()).upper()
    if tn:
        c, minimo, peso = NOMBRE_BLOQUE
        while c > minimo and V._ancho_texto(tn, f["f_r"], c, peso) > V.BLOQUE_ANCHO:
            c -= 1
        asc, may = V._metricas(f["f_r"], c, peso)
        bloques.append((tn, c, yy - asc + may, f["f_r"], peso, t["volanta"], V.BLOQUE_X))
        yy += may + GAP_NOMBRE
    c, renglones = _cargo_renglones(cargo, f)
    if renglones:
        peso = CARGO[2]
        asc, may = V._metricas(f["f_r"], c, peso)
        salto = round(c * 1.25)
        for i, r in enumerate(renglones):
            bloques.append((r, c, yy + i * salto - asc + may, f["f_r"], peso, t["titular"],
                            V.BLOQUE_X))
        yy += (len(renglones) - 1) * salto + may
    yy += GAP_FRASE
    tope, piso = FRASE_BLOQUE
    medidas = [V._texto_bloque("", x, f, tema, 0, tope=tope, piso=piso) for _a, _b, x in frases]
    cuerpo = min([m["cuerpo"] for m in medidas if m["cuerpo"]] or [tope])
    alto_frase = max([V._texto_bloque("", x, f, tema, 0, tope=cuerpo, piso=cuerpo)["alto"]
                      for _a, _b, x in frases] or [0])
    y_frase = yy
    yy += alto_frase + GAP_ESPECTRO
    y_espectro = yy
    yy += ESPECTRO[1] + GAP_PROGRESO
    y_progreso = yy
    yy += PROGRESO
    return dict(bloques=bloques, cajas=[], y=y, y_logo=y, y_frase=y_frase, cuerpo_frase=cuerpo,
                y_espectro=y_espectro, y_progreso=y_progreso, fin=yy, alto=yy - y)


def plan(w: int, h: int, nombre: str, cargo: str, frases: list, caras=None, tema: str = "") -> dict:
    """Dónde va cada cosa para una imagen de w x h. `lienzo`, `media` (x, y, ancho, alto, cover),
    `bloque` y si hay que sombrear detrás del texto (cuando va encima de la imagen). Con material
    horizontal y el estilo bloque prendido, además `tema` y `cajas` (la línea y el bloque)."""
    f = V._fuentes_placa()
    w, h = max(1, int(w)), max(1, int(h))
    if V._bloque_on() and w / h < AR_4X5:
        # VERTICAL (2026-10-18, «todos los posteos con el texto abajo»): 9:16 con el bloque
        # abajo, como las fotos y los videos verticales del bot; la imagen ENTERA, achicada y
        # centrada arriba, sobre sí misma difuminada (`_fondo`). Si el bloque es alto (cargo
        # largo, frase larga), la imagen cede alto antes de que el texto baje a la franja de IG.
        tema = tema if tema in V.BLOQUE_TEMAS else V.tema_siguiente("")
        V._TEMA_USADO[0] = tema
        H = 1920
        pad_a, _pad_b = V.BLOQUE_PAD
        alto_b = bloque_4x5(nombre, cargo, frases, f, tema, 0)["alto"]
        hm = min(V.BLOQUE_9X16_ENTERA, Y_ABAJO_9X16 - alto_b - pad_a - V.BLOQUE_LINEA)
        hm = max(round(V.BLOQUE_9X16_ENTERA * RECORTE_MIN), hm) // 2 * 2
        esc = min(1080 / w, hm / h)
        wi, hi = max(2, round(w * esc) // 2 * 2), max(2, round(h * esc) // 2 * 2)
        extra = max(0, Y_ABAJO_9X16 - hm - V.BLOQUE_LINEA - pad_a - alto_b)
        b = bloque_4x5(nombre, cargo, frases, f, tema,
                       hm + V.BLOQUE_LINEA + pad_a + round(extra * 0.38))
        cajas = [(0, hm, 1080, hm + V.BLOQUE_LINEA, V.CORR_NARANJA),
                 (0, hm + V.BLOQUE_LINEA, 1080, H, V.BLOQUE_TEMAS[tema]["fondo"])]
        return dict(lienzo=(1080, H), media=((1080 - wi) // 2, (hm - hi) // 2, wi, hi, False),
                    bloque=b, encima=False, fuentes=f, tema=tema, cajas=cajas)
    if w / h >= AR_4X5 and V._bloque_on():
        tema = tema if tema in V.BLOQUE_TEMAS else V.tema_siguiente("")
        V._TEMA_USADO[0] = tema
        H = ALTO_4X5
        am = round(1080 * h / w) // 2 * 2
        pad_a, pad_b = V.BLOQUE_PAD
        fijo = (V.BLOQUE_LINEA + pad_a + pad_b
                + bloque_4x5(nombre, cargo, frases, f, tema, 0)["alto"])
        hm = max(round(am * RECORTE_MIN), min(am, H - fijo)) // 2 * 2
        extra = max(0, H - hm - fijo)
        b = bloque_4x5(nombre, cargo, frases, f, tema,
                       hm + V.BLOQUE_LINEA + pad_a + round(extra * 0.38))
        cajas = [(0, hm, 1080, hm + V.BLOQUE_LINEA, V.CORR_NARANJA),
                 (0, hm + V.BLOQUE_LINEA, 1080, H, V.BLOQUE_TEMAS[tema]["fondo"])]
        return dict(lienzo=(1080, H), media=(0, 0, 1080, hm, hm < am), bloque=b, encima=False,
                    fuentes=f, tema=tema, cajas=cajas)
    prueba = bloque(nombre, cargo, frases, f, 0)
    if w / h >= AR_4X5:
        H = ALTO_4X5
        am = round(1080 * h / w) // 2 * 2
        libre = H - MARGEN_ABAJO - TOPE_IMAGEN
        if am + 20 + prueba["alto"] <= libre:
            # Entra todo: imagen entera debajo de la marca y el bloque debajo, centrado en el alto.
            y_img = TOPE_IMAGEN + (libre - am - 20 - prueba["alto"]) // 2
            y_b, encima = y_img + am + 20, False
        else:
            # Una 4:3 o una cuadrada: el bloque va sobre la parte de abajo de la imagen.
            y_img = max(0, min(TOPE_IMAGEN, H - am))
            y_b, encima = H - MARGEN_ABAJO - prueba["alto"], True
        media = (0, y_img, 1080, am, False)
    else:
        H = 1920
        media = (0, 0, 1080, 1920, True)
        y_b, encima = Y_ABAJO_9X16 - prueba["alto"], True
        cabezas = V._caras_en_cuadro(caras, w, h, media[:4], True) if caras else []
        if cabezas and V._cuanto_tapa((V.CORR_X, y_b, V.CORR_X + V.CORR_CAJA_ANCHO, y_b + prueba["alto"]),
                                      cabezas) > 0.15:
            y_b = V.CORR_Y_ARRIBA
    b = bloque(nombre, cargo, frases, f, y_b)
    return dict(lienzo=(1080, H), media=media, bloque=b, encima=encima, fuentes=f)


# =============================================================================
# Capas
# =============================================================================

def _foto_rgb(ruta):
    from PIL import Image, ImageOps
    return ImageOps.exif_transpose(Image.open(ruta)).convert("RGB")


def _fondo(img, lienzo: tuple, salida: Path) -> Path:
    """La imagen difuminada y oscurecida, a todo el cuadro."""
    from PIL import ImageEnhance, ImageFilter, ImageOps
    W, H = lienzo
    fondo = ImageOps.fit(img, (W, H), method=3)
    fondo = fondo.resize((W // 4, H // 4)).filter(ImageFilter.GaussianBlur(10)).resize((W, H), 3)
    fondo = ImageEnhance.Brightness(fondo).enhance(OSCURO_FONDO)
    fondo.save(salida, quality=90)
    return salida


def _media_foto(img, p: dict, salida: Path) -> Path:
    """La foto al tamaño de su lugar: a todo el ancho (4:5) o a sangre en las caras (9:16)."""
    x, y, w, h, cover = p["media"]
    if cover:
        from story_image import _encuadrar
        out = _encuadrar(img, w, h)
    else:
        out = img.resize((w, h), 3)
    out.save(salida, quality=92)
    return salida


def _base_png(p: dict, salida: Path) -> Path:
    """Lo fijo: la marca, el cargo y el nombre (y la sombra detrás del bloque si va encima)."""
    from PIL import Image
    W, H = p["lienzo"]
    b, f = p["bloque"], p["fuentes"]
    marca = list(V._marca_corr(f))
    lienzo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    if p.get("tema"):
        # 4:5 estilo bloque: la línea, el bloque, el logo, el nombre y el cargo.
        V.pintar_placa(lienzo, dict(bloques=b["bloques"], cajas=p["cajas"],
                                    logo_bloque=(p["tema"], V.BLOQUE_X, b["y_logo"])))
        lienzo.save(salida)
        return salida
    V.sombrear_arriba(lienzo)
    if p["encima"]:
        V._sombrear_banda(lienzo, max(0, b["y"] - 40), min(H, b["fin"] + 20), tope=0.62, borde=200)
    # Halo en la marca y en el cargo (van sin caja); el nombre ya tiene la suya.
    cargo = [x for x in b["bloques"] if x[5] == V.CORR_BLANCO2]
    V.pintar_placa(lienzo, dict(bloques=marca + b["bloques"], cajas=b["cajas"], halo=marca + cargo))
    lienzo.save(salida)
    return salida


def _frase_png(texto: str, p: dict, salida: Path) -> Path:
    from PIL import Image
    if p.get("tema"):
        b = p["bloque"]
        t = V._texto_bloque("", texto, p["fuentes"], p["tema"], b["y_frase"],
                            tope=b["cuerpo_frase"], piso=b["cuerpo_frase"])
        t["cajas"] = []
    else:
        t = V._tarjeta_corr("", texto, p["fuentes"], p["bloque"]["y_frase"])
    capa = Image.new("RGBA", tuple(p["lienzo"]), (0, 0, 0, 0))
    V.pintar_placa(capa, dict(bloques=t["bloques"], cajas=t["cajas"]))
    capa.save(salida)
    return salida


# =============================================================================
# Espectro
# =============================================================================

def _pcm(audio: Path, desde: float, dur: float, sr: int = 22050):
    import numpy as np
    r = subprocess.run([V._ffmpeg(), "-v", "error", "-ss", f"{desde:.3f}", "-t", f"{dur:.3f}",
                        "-i", str(audio), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                       capture_output=True)
    if r.returncode != 0 or not r.stdout:
        raise DeclaracionError(f"no pude leer el audio: {r.stderr.decode(errors='replace')[-200:]}")
    return np.frombuffer(r.stdout, dtype=np.float32), sr


def alturas(muestras, sr: int, n_cuadros: int, barras: int = BARRAS):
    """Matriz (cuadros x barras) de 0 a 1: la energía de cada banda (logarítmicas de 80 Hz a
    8 kHz) cuadro a cuadro, normalizada POR BANDA (si no, la voz solo mueve las graves) y con
    subida inmediata y bajada suave."""
    import numpy as np
    n = 2048
    ventana = np.hanning(n)
    bordes = np.geomspace(80, min(8000, sr / 2 - 1), barras + 1)
    frec = np.fft.rfftfreq(n, 1 / sr)
    idx = [np.where((frec >= bordes[i]) & (frec < bordes[i + 1]))[0] for i in range(barras)]
    idx = [i if len(i) else np.array([min(len(frec) - 1, np.searchsorted(frec, bordes[k]))])
           for k, i in enumerate(idx)]
    relleno = np.concatenate([np.zeros(n // 2, np.float32), muestras, np.zeros(n, np.float32)])
    crudo = np.zeros((n_cuadros, barras), np.float32)
    nivel = np.zeros(n_cuadros, np.float32)
    for c in range(n_cuadros):
        centro = int(c * sr / FPS) + n // 2
        trozo = relleno[centro - n // 2: centro + n // 2]
        if len(trozo) < n:
            break
        nivel[c] = float(np.sqrt(np.mean(trozo ** 2)))
        mag = np.abs(np.fft.rfft(trozo * ventana))
        crudo[c] = [float(mag[i].mean()) for i in idx]
    crudo = np.log1p(crudo * 10)
    techo = np.percentile(crudo, 96, axis=0) + 1e-6
    out = np.clip(crudo / techo, 0, 1) ** 1.3
    # Silencio de verdad (respiración, pausa): las barras bajan a la rayita.
    umbral = max(1e-4, float(np.percentile(nivel, 60)) * 0.12)
    out[nivel < umbral] *= 0.15
    suave = np.zeros_like(out)
    for c in range(n_cuadros):
        prev = suave[c - 1] if c else 0
        suave[c] = np.maximum(out[c], prev * 0.78)
    return suave


def _espectro_mov(audio: Path, desde: float, dur: float, salida: Path,
                  pista: tuple = (255, 255, 255, 110)) -> tuple:
    """El espectro y la línea de progreso, cuadro a cuadro, en un .mov con transparencia.
    Devuelve (archivo, ancho, alto)."""
    from PIL import Image, ImageDraw
    muestras, sr = _pcm(audio, desde, dur)
    n = max(1, int(round(dur * FPS)))
    alt = alturas(muestras, sr, n)
    W, Hb = ESPECTRO
    H = Hb + GAP_PROGRESO + PROGRESO
    paso = W / BARRAS
    ancho_barra = max(3, round(paso * 0.46))
    medio = Hb / 2
    naranja = V.CORR_NARANJA
    cmd = [V._ffmpeg(), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-", "-c:v", "png", "-pix_fmt", "rgba", str(salida)]
    with tempfile.TemporaryFile() as err:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=err)
        try:
            for c in range(n):
                img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                d = ImageDraw.Draw(img)
                for i in range(BARRAS):
                    a = max(3.0, float(alt[c, i]) * (Hb - 4))
                    x = round(i * paso + (paso - ancho_barra) / 2)
                    d.rounded_rectangle((x, round(medio - a / 2), x + ancho_barra - 1, round(medio + a / 2)),
                                        radius=ancho_barra // 2, fill=naranja)
                y = Hb + GAP_PROGRESO
                d.rectangle((0, y + 1, W - 1, y + 2), fill=pista)
                fin = round(W * (c + 1) / n)
                if fin > 0:
                    d.rectangle((0, y, fin - 1, y + PROGRESO - 1), fill=naranja)
                proc.stdin.write(img.tobytes())
            proc.stdin.close()
        except BrokenPipeError:
            pass
        if proc.wait() != 0:
            err.seek(0)
            raise DeclaracionError(f"ffmpeg no armó el espectro: {err.read().decode(errors='replace')[-300:]}")
    return salida, W, H


# =============================================================================
# Armado
# =============================================================================

def armar(audio, visual, salida, *, nombre: str, cargo: str = "", frases=None, tramo=None,
          es_foto: bool = True, respaldo: str = "", work_dir=None, tema: str = "") -> dict:
    """El contenido del reel (sin isologo ni placa de cierre): imagen, marca, cargo, nombre,
    frases que cambian, espectro y el audio. `frases` = [{desde, texto}] con los segundos del
    audio ORIGINAL; `tramo` = {desde, hasta} (si el audio pasa de MAX_SEG); `tema` = los colores
    del 4:5 estilo bloque. Devuelve {archivo, alto, dur, frases, tramo, tema}."""
    audio, visual, salida = Path(audio), Path(visual), Path(salida)
    work = Path(work_dir or salida.parent)
    work.mkdir(parents=True, exist_ok=True)
    dur_audio = float(V.duration_seconds(audio) or 0)
    if dur_audio < MIN_SEG:
        raise DeclaracionError(f"el audio dura {dur_audio:.1f} s: muy corto")
    desde, hasta = tramo_valido(tramo, dur_audio)
    dur = hasta - desde
    lista = frases_validas(frases, desde, hasta, respaldo=respaldo or nombre)
    clave = salida.stem
    if es_foto:
        img = _foto_rgb(visual)
        w, h = img.size
    else:
        w, h = V._dimensiones(visual)
        if not w or not h:
            raise DeclaracionError("no pude leer el tamaño del video")
        img = _foto_rgb(V.frame_at(visual, min(1.0, max(0.0, float(V.duration_seconds(visual) or 0) / 3)),
                                   work / f"_cuadro_{clave}.jpg"))
    caras = V._caras_de_foto(img) if w / h < AR_4X5 else None
    p = plan(w, h, nombre, cargo, lista, caras, tema=tema)
    W, H = p["lienzo"]
    x, y, mw, mh, cover = p["media"]
    fondo = _fondo(img, (W, H), work / f"_fondo_{clave}.jpg")
    base = _base_png(p, work / f"_base_{clave}.png")
    pngs = [(_frase_png(t, p, work / f"_frase_{i}_{clave}.png"), a, b)
            for i, (a, b, t) in enumerate(lista)]
    if p.get("tema"):
        esp, ew, eh = _espectro_mov(audio, desde, dur, work / f"_espectro_{clave}.mov",
                                    pista=V.BLOQUE_TEMAS[p["tema"]]["tenue"])
        ex = V.BLOQUE_X
    else:
        esp, ew, eh = _espectro_mov(audio, desde, dur, work / f"_espectro_{clave}.mov")
        ex = EJE_X - ew // 2
    ey = p["bloque"]["y_espectro"]

    entradas = ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", str(fondo)]
    if es_foto:
        media = _media_foto(img, p, work / f"_media_{clave}.jpg")
        entradas += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", str(media)]
        vm = f"[1:v]format=rgba[m]"
    else:
        entradas += ["-stream_loop", "-1", "-t", f"{dur:.3f}", "-i", str(visual)]
        if cover:
            vm = (f"[1:v]{V._sin_giro()},scale={mw}:{mh}:force_original_aspect_ratio=increase,"
                  f"crop={mw}:{mh},setsar=1,fps={FPS}[m]")
        else:
            vm = f"[1:v]{V._sin_giro()},scale={mw}:{mh},setsar=1,fps={FPS}[m]"
    entradas += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", str(base)]
    fc = [vm, f"[0:v][m]overlay={x}:{y}:shortest=1[v0]", "[v0][2:v]overlay=0:0[v1]"]
    ult, n_in = "[v1]", 3
    fd = V.FUNDIDO_TARJETA
    for i, (png, a, b) in enumerate(pngs):
        entradas += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", str(png)]
        cad = "format=rgba"
        if i > 0:
            cad += f",fade=t=in:st={a:.3f}:d={fd}:alpha=1"
        if i + 1 < len(pngs):
            cad += f",fade=t=out:st={max(a, b - fd):.3f}:d={fd}:alpha=1"
        fc.append(f"[{n_in}:v]{cad}[f{i}]")
        fc.append(f"{ult}[f{i}]overlay=0:0:enable='between(t,{a:.3f},{b:.3f})'[vf{i}]")
        ult, n_in = f"[vf{i}]", n_in + 1
    entradas += ["-i", str(esp)]
    fc.append(f"{ult}[{n_in}:v]overlay={ex}:{ey}:eof_action=pass,format=yuv420p[v]")
    n_in += 1
    entradas += ["-ss", f"{desde:.3f}", "-t", f"{dur:.3f}", "-i", str(audio)]
    fo = max(0.0, dur - FADE_OUT)
    fc.append(f"[{n_in}:a]aresample=44100,loudnorm=I=-16:TP=-1.5:LRA=11,aresample=44100,"
              f"afade=t=in:st=0:d={FADE_IN},afade=t=out:st={fo:.3f}:d={FADE_OUT}[a]")
    cmd = [V._ffmpeg(), "-y", *entradas, "-filter_complex", ";".join(fc), "-map", "[v]", "-map", "[a]",
           "-t", f"{dur:.3f}", "-r", str(FPS), "-c:v", "libx264", "-preset", "veryfast", *V._calidad(),
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
           "-movflags", "+faststart", str(salida)]
    V._run_ffmpeg(cmd, "declaración")
    logger.info(f"Declaración armada: {W}x{H}, {dur:.1f} s (audio {desde:.1f}-{hasta:.1f} de "
                f"{dur_audio:.1f}), {len(lista)} frase(s): "
                + " · ".join(f"{a:.0f}s «{t[:28]}»" for a, _b, t in lista))
    return {"archivo": str(salida), "alto": H, "dur": round(dur, 2), "tramo": [desde, hasta],
            "frases": [[round(a, 2), round(b, 2), t] for a, b, t in lista],
            "tema": p.get("tema", "")}


def reel(audio, visual, salida, *, nombre: str, cargo: str = "", frases=None, tramo=None,
         es_foto: bool = True, respaldo: str = "", work_dir=None, tema: str = "") -> dict:
    """El reel TERMINADO: `armar` + isologo y placa de cierre (`video.to_vertical_reel`). En el 4:5
    estilo bloque el isologo ya va en el bloque."""
    salida = Path(salida)
    work = Path(work_dir or salida.parent)
    contenido = work / f"_decl_{salida.stem}.mp4"
    info = armar(audio, visual, contenido, nombre=nombre, cargo=cargo, frases=frases, tramo=tramo,
                 es_foto=es_foto, respaldo=respaldo, work_dir=work, tema=tema)
    V.to_vertical_reel(contenido, salida, compuesto=True, estilo="corresponsal", alto=info["alto"],
                       logo=not info.get("tema"))
    info["archivo"] = str(salida)
    return info


# =============================================================================
# Autoprueba (sin red ni IA)
# =============================================================================

def autoprueba(tmp=None) -> bool:
    """Arma dos declaraciones con audio y fotos sintéticos (una apaisada → 4:5 y una vertical →
    9:16) y mira tamaño, duración y que tenga sonido."""
    from PIL import Image, ImageDraw
    tmp = Path(tmp or tempfile.mkdtemp(prefix="decl_"))
    tmp.mkdir(parents=True, exist_ok=True)
    audio = tmp / "voz.m4a"
    V._run_ffmpeg([V._ffmpeg(), "-y", "-f", "lavfi", "-i",
                   "sine=frequency=220:duration=9,volume='0.5+0.5*sin(2*PI*t*1.3)':eval=frame",
                   "-c:a", "aac", str(audio)], "audio de prueba")
    bien = True
    for nombre_f, tam in (("apaisada.jpg", (1600, 900)), ("vertical.jpg", (900, 1600))):
        foto = tmp / nombre_f
        img = Image.new("RGB", tam, (70, 90, 120))
        ImageDraw.Draw(img).ellipse((tam[0] // 3, tam[1] // 5, tam[0] * 2 // 3, tam[1] // 2), fill=(200, 170, 140))
        img.save(foto)
        try:
            info = reel(audio, foto, tmp / f"decl_{foto.stem}.mp4", nombre="Néstor Dabi",
                        cargo="Secretario de Seguridad municipal",
                        frases=[{"desde": 0, "texto": "Refuerzo aéreo para Chivilcoy"},
                                {"desde": 4.2, "texto": "Patrullaje nocturno en ciudad y zona rural"}],
                        work_dir=tmp)
            w, h = V._dimensiones(info["archivo"])
            esperado = 1350 if tam[0] > tam[1] else 1920
            ok = (w, h) == (1080, esperado) and V.has_audio(info["archivo"]) and len(info["frases"]) == 2
            print(f"  {'OK  ' if ok else 'MAL '} declaración {foto.stem}: {w}x{h}, "
                  f"{V.duration_seconds(info['archivo']):.1f} s, frases {len(info['frases'])}")
            bien &= ok
        except Exception as e:                                   # noqa: BLE001
            print(f"  ROTO declaración {foto.stem}: {type(e).__name__}: {e}")
            bien = False
    return bien


if __name__ == "__main__":
    import sys
    print(json.dumps({"ok": autoprueba(sys.argv[1] if len(sys.argv) > 1 else None)}))
