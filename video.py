"""Arma un video vertical (reel) 1080x1920 a partir de imágenes, con transiciones
crossfade (xfade) entre placas, SIN audio. Usa el ffmpeg de imageio_ffmpeg (local)
o el del sistema (en la nube)."""
import functools
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
# Tres tipografías, una por trabajo (2026-09-18). Montserrat es LA MARCA y no se toca; el
# titular y el resumen usan otras porque tienen otro problema que resolver:
#   · el TITULAR tiene que entrar en 2 renglones → una angosta permite letra más grande;
#   · el RESUMEN se lee chico sobre una foto → una humanista aguanta mejor ese tamaño.
# Si el archivo no está, se cae a Montserrat y el reel sale igual (solo lo avisa el log).
# Las dos vienen de Google Fonts y son VARIABLES: un solo archivo con todos los pesos
# adentro. Hay que pedir la instancia; si no, sale la Regular, demasiado fina para leerse
# sobre una foto. Se elige con `_tipo`, tanto al MEDIR como al DIBUJAR — si se midiera con
# una y se dibujara con otra, el texto no entraría donde dice que entra.
#
# 2026-09-25: las dos pasan a ser GOOGLE SANS, la letra de los reels de referencia que armó
# el usuario a mano (medida sobre sus capturas: el ancho de cada renglón coincide al píxel).
# Es de Google Fonts con licencia OFL, así que puede viajar en este repo público. Es variable
# en PESO (400 a 700): el titular va en 540 —entre Regular y Medium, lo que dio la medición—
# y la bajada en Regular. `_tipo` acepta el peso por NÚMERO además de por nombre.
FUENTE_PLACA = ASSETS / "fonts" / "GoogleSans-Variable.ttf"
FUENTE_TITULAR = FUENTE_PLACA
FUENTE_RESUMEN = FUENTE_PLACA
# El titular de la referencia va en un peso INTERMEDIO (500) y con las letras más JUNTAS que
# las de fábrica: «reducir subsidios» mide 875 px donde Google Sans suelta da 930. Eso es un
# interletrado de −2,5% del cuerpo, que se escribe después de la barra: «peso/milésimas».
PESO_TITULAR = "500/-25"
PESO_RESUMEN = "400"
PESO_VOLANTA = "500"
PESO_MARCA = "400"
PLACA_SEG = 5.0                             # cuánto dura la placa de cierre
FONDO_DIFUMINADO = 80                       # px de transición entre el video y el fondo
# Rectángulo ÚTIL de la caja negra del overlay (medido sobre el PNG, en 1080x1920): es la
# parte donde la caja es negra en TODAS sus filas, así el texto nunca se escapa por los
# bordes en diagonal. (x, y, ancho, alto).
ZOCALO_CAJA = (175, 1481, 670, 71)
ZOCALO_COLOR = (247, 127, 0)  # el naranja de la marca
ZOCALO_PALABRAS = 5           # tope de palabras del zócalo
# Texto de marca que va arriba, del lado contrario al isologo (2026-09-14). Los dos
# medios van uno DEBAJO del otro: el «|» separa renglones, no es un carácter a dibujar.
MARCA_TEXTO = "DIARIO LA CAMPAÑA|RADIO DEL CENTRO"
# En la placa la marca va como en la referencia: «Diario La Campaña | Radio del Centro».
PLACA_MARCA_TEXTO = "Diario La Campaña|Radio del Centro"
MARCA_USUARIO = "@diarioyradio"
MARCA_TAM_MAX = 40            # cuerpo de los renglones de la marca; baja solo si no entra
MARCA_TAM_MIN = 18
# Titular arriba y resumen abajo, con la imagen en el medio (2026-09-18). Los cuerpos son
# TOPES: si el texto no entra en sus renglones, la tipografía baja sola hasta el mínimo.
TITULAR_RENGLONES = 2
TITULAR_TAM_MAX = 64
TITULAR_TAM_MIN = 32
RESUMEN_RENGLONES = 3
RESUMEN_TAM_MAX = 38
RESUMEN_TAM_MIN = 20
BANDA_MX = 56                 # margen lateral del texto de las bandas
BANDA_AIRE = 18               # aire entre el titular y el borde de arriba de la imagen
BANDA_GAP_MARCA = 16          # aire entre el bloque de marca y el titular
BANDA_ALTO_MIN = 420          # la imagen nunca queda más chata que esto
# El resumen va apenas más chico que el titular, no con un cuerpo fijo: con 38 contra 64
# quedaba ilegible al lado del título. Si con ese cuerpo no entra todo, se RECORTA el texto
# (pedido del usuario: antes menos palabras que letra chica).
RESUMEN_DELTA = 2

# ── Zonas que TAPAN las apps (pedido del usuario 2026-09-20) ─────────────────
# Ningún texto se dibuja acá adentro. La imagen sí puede llegar: lo que molesta es que la
# app tape una palabra, no que tape un pedazo de foto.
#   · ARRIBA: en TikTok van las solapas «Siguiendo / Para vos» y en Instagram el encabezado.
#   · ABAJO: en las dos van el usuario, el texto del posteo y los botones.
#   · DERECHA: la columna de botones (me gusta / comentar / compartir), que arranca por la
#     mitad del alto. Arriba de eso la derecha está libre, y ahí es donde va el isologo.
SEGURO_ARRIBA = 150
BANDA_SEGURO = 330
SEGURO_DERECHA = 150          # ancho de la columna de botones
SEGURO_DERECHA_DESDE = 900    # a partir de qué altura aparece esa columna

# ── Estilo «placa» ────────────────────────────────────────────────────────────
# El texto va ARRIBA, en pocos renglones y grande, y la imagen va abajo, fundiéndose con el
# fondo por el borde de arriba.
#
# Por qué poco texto: los resúmenes reales tienen 255 caracteres de mediana. Medido sobre
# las 269 notas del ledger, en 3 renglones NO entran a ningún cuerpo legible. El problema
# nunca fue el cuerpo: era el LARGO. Así que va POCO texto y GRANDE, cortado por oración.
#
# 2026-09-25 — COPIA DE LOS REELS DE REFERENCIA DEL USUARIO. Él armó a mano tres reels
# («Zona Fría», «Un policía herido», «Un auto se incendió») y pidió que el bot salga igual.
# Todas las medidas de abajo salen de MEDIR esas capturas llevadas a 1080x1920 (el ancho de
# cada renglón contra Google Sans, al píxel):
#   · todo alineado a la IZQUIERDA sobre un margen de 108 px;
#   · marca chica arriba a la izquierda —«Diario La Campaña | Radio del Centro» y
#     «@diarioyradio»— y el isologo a la derecha, centrado contra ese bloque;
#   · volanta NARANJA; titular, bajada y todo lo demás en BLANCO;
#   · titular enorme (104 a 123 en las capturas) con el interlineado apretado (1,05);
#   · fondo carbón con textura de humo, más claro arriba (lo eligió el usuario el mismo día).
# Lo único que se corrió: todo baja 30 px para que el texto no entre en la franja de arriba
# que tapan Instagram y TikTok (`SEGURO_ARRIBA`, pedido del 2026-09-20).
PLACA_MX = 108                # margen izquierdo de TODO el texto
PLACA_MX_DER = 70             # margen derecho: los renglones largos llegan hasta x=1010
PLACA_Y0 = SEGURO_ARRIBA      # dónde arranca la marca (debajo del techo de las apps)
PLACA_MARCA_TAM = 32
PLACA_MARCA_SALTO = 37        # de línea base a línea base entre los dos renglones de marca
# De la línea base del último renglón de marca al TOPE de la volanta. Es mucho aire, pero es
# el de la referencia: separa la firma del medio de la noticia.
PLACA_GAP_MARCA = 134
PLACA_VOLANTA_TAM = 50
PLACA_VOLANTA_MIN = 38        # antes que cortarla con «…», la volanta se achica
PLACA_GAP_VOLANTA = 38        # de la línea base de la volanta al tope de las mayúsculas del titular
PLACA_TITULAR_TAM = 118       # tope; baja solo si no entra (la referencia: 117)
PLACA_TITULAR_MIN = 46
PLACA_TITULAR_RENGLONES = 4   # con Google Sans (más ancha) el titular de 110 caracteres no
                              # entraba en 3 a un cuerpo legible
PLACA_TITULAR_SALTO = 1.03
PLACA_GAP_TITULAR = 60        # de la línea base del titular al tope de la bajada
PLACA_BAJADA_TAM = 60
PLACA_BAJADA_MIN = 42
PLACA_BAJADA_SALTO = 1.12
# Tres renglones, no dos (2026-09-20): la bajada tiene que cerrar en punto y con dos
# renglones una primera oración de largo normal no entraba, así que salía cortada.
PLACA_BAJADA_RENGLONES = 3
# Texto de PIE: la primera oración fuerte de la nota, en el fondo que queda DEBAJO de una
# foto apaisada (pedido del usuario 2026-09-20). Va del MISMO tamaño que la bajada.
PLACA_PIE_TAM = PLACA_BAJADA_TAM
PLACA_PIE_MIN = PLACA_BAJADA_MIN
PLACA_PIE_RENGLONES = 3
PLACA_PIE_MIN_ALTO = 60       # ni un renglón del cuerpo más chico entra: no se dibuja
# Cuántos puntos de titular estamos dispuestos a resignar con tal de no partir un nombre
# entre dos renglones. Hasta 8 no se nota; más abajo sí, y ahí conviene el titular grande
# aunque el apellido caiga al renglón siguiente.
PLACA_NOMBRE_COSTO = 8
# La imagen nunca ocupa menos que esto. En la referencia de «Zona Fría», con volanta, tres
# renglones de titular, bajada y un dato, la foto arranca en y≈1000: 920 px. Si el texto
# viene más largo, se achica EL TEXTO (ver `placa_layout`), no la foto.
PLACA_IMG_MIN = 900
PLACA_AIRE_IMG = 8            # de la última letra al borde (transparente) de la imagen
# px del desvanecido entre el fondo y la imagen. Con curva SUAVE (smoothstep): arranca y
# termina sin escalón, así la unión no se ve y los primeros 40 px quedan casi transparentes
# —la foto «aparece» a unos 50 px de la última letra, como en la referencia—.
# 2026-09-20 se había bajado de 240 a 110 porque el desvanecido LINEAL se comía los márgenes
# y el cuarto de arriba llegaba lavado. Con la curva suave eso no pasa: la mitad de arriba
# del tramo es casi transparente y la de abajo casi opaca, así que no hay banda «lavada».
PLACA_FUNDIDO = 200
PLACA_FUNDIDO_ABAJO = 170     # el sombreado del borde de abajo de una foto apaisada
# Fondo: carbón con textura de humo, más claro arriba (lo eligió el usuario el 2026-09-25,
# copiado de sus reels de referencia). `REEL_PLACA_FONDO` cambia el color base (admite
# `0x1E1D22`, `#1E1D22` o `black`) y `REEL_PLACA_FONDO_AUTO=1` vuelve a sacar el color de la
# propia imagen, como del 20/9 al 25/9. `REEL_PLACA_HUMO=0` deja el color liso.
PLACA_FONDO = "0x18171C"
# A cuánto se lleva el color sacado de la imagen (solo con `REEL_PLACA_FONDO_AUTO=1`): se le
# respeta el MATIZ y se le imponen la luz y la saturación, para que se lea como un fondo y no
# como un color. Peor caso medido: blanco 11,5:1 y naranja 4,4:1 (umbral de texto grande 3:1).
PLACA_FONDO_LUZ = 0.16
PLACA_FONDO_SAT = 0.45
# Isologo: tamaño y margen. Estaban repetidos como literales en cuatro funciones; ahora
# salen de acá, así la caja que calcula `_logo_caja` no se puede desfasar de lo que dibuja
# el filtergraph (era eso lo que dejaba al titular pisándolo).
# 2026-09-25: medido sobre la referencia, la «C» blanca ocupa 115x139 y su centro queda a la
# altura del centro del bloque de marca. Con el PNG del repo (la «C» ocupa 455 de 514 px de
# ancho) eso es un PNG de 130 de ancho, a 102 del borde derecho y 113 de arriba.
LOGO_ANCHO = 130
LOGO_MX = 102
LOGO_MY = 113
NARANJA = (247, 127, 0, 255)  # el naranja de la marca
BLANCO = (255, 255, 255, 255)
GRIS = (236, 236, 240, 255)   # la marca, apenas apagada


def _cfg(clave: str, default: str) -> str:
    return (get(clave, "") or default).strip()


def _num(clave: str, default: float) -> float:
    """Una perilla numérica del `.env`. Si trae cualquier cosa, manda el default: una
    variable mal escrita no puede cambiar cómo sale un reel sin que nadie se entere."""
    try:
        return float(_cfg(clave, str(default)))
    except ValueError:
        logger.warning(f"{clave} no es un número; uso {default}.")
        return default


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


def _logo_a_la_derecha() -> bool:
    """¿De qué lado va el isologo del reel? DERECHA por default (pedido 2026-09-14).
    `REEL_LOGO_LADO=izquierda` en el `.env` lo devuelve al lugar de antes."""
    return _cfg("REEL_LOGO_LADO", "derecha").lower() not in ("izq", "izquierda", "left")


def _logo_caja() -> tuple[int, int, int, int] | None:
    """(x0, y0, x1, y1): el rectángulo que ocupa el isologo. None si el reel va sin logo.

    El alto se MIDE del PNG en vez de fijarlo, porque el filtergraph lo escala con
    `scale={ancho}:-1` y el alto real recién se conoce ahí. Gracias a esto, el texto de
    arriba sabe exactamente hasta dónde llega el logo y puede esquivarlo."""
    ruta = _asset("REEL_LOGO", LOGO_REEL)
    if not ruta:
        return None
    ancho = int(float(_cfg("REEL_LOGO_ANCHO", str(LOGO_ANCHO))))
    mx = int(float(_cfg("REEL_LOGO_MARGEN_X", str(LOGO_MX))))
    my = int(float(_cfg("REEL_LOGO_MARGEN_Y", str(LOGO_MY))))
    try:
        from PIL import Image
        with Image.open(ruta) as im:
            alto = round(ancho * im.height / max(1, im.width))
    except Exception:                                            # noqa: BLE001
        alto = round(ancho * 1.25)   # sin PIL: asumo alargado, que es el caso que molesta
    x0 = (1080 - ancho - mx) if _logo_a_la_derecha() else mx
    return x0, my, x0 + ancho, my + alto


def has_audio(src) -> bool:
    """True si el archivo trae pista de audio (parseando la salida de ffmpeg)."""
    r = subprocess.run([_ffmpeg(), "-i", str(src)], capture_output=True, text=True, errors="replace")
    return "Audio:" in (r.stderr or "")


def _sin_giro() -> str:
    """Filtro que BORRA la «matriz de pantalla» de los cuadros. `null` si no está.

    Un video de celular puede venir grabado apaisado y traer aparte un cartel que dice
    «mostrame girado 90°». ffmpeg lo aplica solo al decodificar (los cuadros salen
    derechos) pero después ARRASTRA el cartel hasta la salida, así que el reel termina
    derecho por dentro y acostado en la pantalla. Y si ese archivo se vuelve a procesar,
    lo gira otra vez.

    Ojo: `-metadata:s:v:0 rotate=0` NO alcanza (probado 2026-09-18) porque el cartel viaja
    como side data del cuadro, no como metadato del contenedor. Hay que borrarlo del cuadro.

    Va SOLO donde se re-codifica. Donde se copia el video tal cual (`-c copy`) el cartel
    tiene que quedarse: ahí los cuadros siguen guardados de costado y es el cartel el que
    los endereza."""
    return ("sidedata=mode=delete:type=DISPLAYMATRIX"
            if tiene_filtro("sidedata") else "null")


def _dimensiones(src) -> tuple[int, int]:
    """Ancho y alto del video TAL COMO SE VE. (0, 0) si no se puede.

    Con un video girado (ver `_sin_giro`) la ficha del archivo miente: dice 1920x1080 y los
    cuadros salen 1080x1920. Si se le cree a la ficha, el reel sale ARRUINADO y sin avisar:
    el código lo toma por apaisado, lo aplasta a un tercio de su alto y encima la salida
    hereda el giro, con lo cual queda todo acostado (probado 2026-09-18)."""
    # `errors="replace"`: ffmpeg escribe la ficha en la codificación de la consola y un
    # solo byte que no entre en ella hacía explotar la LECTURA (no el video), y de ahí
    # salía un (0,0) que arrastraba todo lo demás.
    r = subprocess.run([_ffmpeg(), "-i", str(src)], capture_output=True, text=True,
                       errors="replace")
    err = r.stderr or ""
    m = re.search(r"Video:.*?[\s,](\d{2,5})x(\d{2,5})[\s,]", err)
    if not m:
        return (0, 0)
    ancho, alto = int(m.group(1)), int(m.group(2))
    giro = re.search(r"rotation of\s+(-?[\d.]+)\s+degrees", err)
    if giro and round(abs(float(giro.group(1)))) % 180 == 90:
        logger.info(f"El video viene girado {giro.group(1)}°: se ve {alto}x{ancho} y no "
                    f"{ancho}x{alto}. Lo trato por cómo se ve.")
        return alto, ancho
    return ancho, alto


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
    r = subprocess.run(args, capture_output=True, text=True, errors="replace")
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


def fondo_enmarcado(cont_w: int, cont_h: int, salida, *,
                    dest_y: int = 0, dest_h: int = 1920) -> Path | None:
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
    # El mismo encuadre que hace ffmpeg: el video entero centrado dentro del HUECO. Sin
    # bandas el hueco es el cuadro entero; con titular y resumen es la franja del medio.
    esc = min(1080 / w, dest_h / h)
    vw, vh = round(w * esc), round(h * esc)
    x0, y0 = (1080 - vw) // 2, dest_y + (dest_h - vh) // 2
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


def _fuente_marca() -> str | None:
    """Montserrat Bold (la tipografía de la marca, commiteada para que la nube la tenga).
    Si faltara, cae a la misma lista de respaldo que la firma."""
    return str(FUENTE_ZOCALO) if FUENTE_ZOCALO.exists() else _font_file()


_FUENTES_AVISADAS: set = set()


def _fuente_usable(ruta: Path) -> bool:
    """¿PIL puede ABRIR de verdad este archivo como tipografía?

    No alcanza con que exista: una descarga cortada o una página de error guardada con
    nombre .ttf existen igual, y ahí PIL tira «unknown file format». Pasó el 2026-09-18 y
    el reel salió SIN titular ni resumen — por un archivo basura de 268 KB."""
    try:
        from PIL import ImageFont
        ImageFont.truetype(str(ruta), 24)
        return True
    except Exception:                                            # noqa: BLE001
        return False


def _fuente_banda(clave: str, archivo: Path) -> str | None:
    """Tipografía del titular o del resumen, con respaldo.

    Si el `.ttf` no está —o está pero no se puede abrir— cae a la de la marca en vez de
    dejar el reel sin texto: falta una tipografía, no una noticia. Avisa UNA sola vez por
    corrida para no llenar el log de la nube con el mismo renglón."""
    valor = _cfg(clave, "")
    ruta = Path(valor) if valor else archivo
    if ruta.exists() and _fuente_usable(ruta):
        return str(ruta)
    if str(ruta) not in _FUENTES_AVISADAS:
        _FUENTES_AVISADAS.add(str(ruta))
        motivo = "no se puede abrir (¿descarga cortada?)" if ruta.exists() else "falta"
        logger.warning(f"La tipografía {ruta.name} {motivo}; ese texto va con la de la marca. "
                       f"Para usarla, dejá un .ttf válido en {ruta.parent} y commiteá.")
    return _fuente_marca()


@functools.lru_cache(maxsize=512)
def _tipo(ruta: str, cuerpo: int, peso: str = ""):
    """Abre la tipografía en el CUERPO y el PESO pedidos.

    `peso` solo aplica a las variables (Google Sans, Archivo Narrow, Libre Franklin): puede
    ser el NOMBRE de una instancia («Medium») o un NÚMERO del eje de peso («540»), que es lo
    que permite el grosor exacto de la referencia. Si esta build de PIL no sabe de variables,
    queda la instancia por defecto: más fina, pero el reel sale igual. Montserrat es estática
    y no usa `peso`.

    Queda en caché: el armado mide cientos de veces el mismo renglón (búsquedas binarias de
    cuerpo y de ancho) y Google Sans pesa 5 MB; abrirla en cada medición costaba segundos.
    Nadie modifica la fuente que devuelve, así que compartirla es seguro."""
    from PIL import ImageFont
    f = ImageFont.truetype(ruta, cuerpo)
    peso = _interletra(peso)[0]
    if peso:
        try:
            if str(peso).replace(".", "", 1).isdigit():
                ejes = f.get_variation_axes()
                valores = [e.get("default", 0) for e in ejes]
                for i, e in enumerate(ejes):
                    nombre = e.get("name")
                    nombre = nombre.decode() if isinstance(nombre, bytes) else str(nombre)
                    if nombre.lower() in ("weight", "wght"):
                        valores[i] = max(e["minimum"], min(e["maximum"], float(peso)))
                f.set_variation_by_axes(valores)
            else:
                f.set_variation_by_name(peso)
        except Exception:                                        # noqa: BLE001
            pass
    return f


# Une las palabras que NO se pueden separar en dos renglones. Para el ojo es un espacio
# común (se cambia por uno antes de dibujar); para el cortador, la pareja es UNA palabra.
_PEGA = "\u0001"


def _es_propio(palabra: str) -> bool:
    """¿Esta palabra parece parte de un nombre propio? (`Seba`, `Bravo`, `San`…)

    Arranca en mayúscula y no cierra frase: si termina en punto, coma o dos puntos, ahí SÍ
    se puede cortar, porque lo que sigue ya es otra idea («Chivilcoy: El intendente…»)."""
    limpia = palabra.strip("«»\"'¿¡()[]—-")
    if not limpia or not limpia[0].isupper():
        return False
    return limpia[-1] not in ".,;:!?…"


def _unir_nombres(texto: str) -> str:
    """Pega las tiras de palabras que empiezan en mayúscula seguidas.

    Un nombre partido entre dos renglones se lee mal: «Memi Mesplet y Seba / Bravo presentan»
    hace que el apellido parezca colgar de otra cosa (pedido del usuario 2026-09-18).

    Se pegan SOLO mayúsculas consecutivas, sin partículas en el medio. Así «Seba Bravo» y
    «San Luis» quedan juntos, pero «Memi Mesplet y Seba Bravo» no se convierte en un ladrillo
    de cinco palabras: la «y» minúscula corta la tira.

    Si el texto viene TODO EN MAYÚSCULAS no se pega nada: ahí la mayúscula no distingue un
    nombre de una palabra cualquiera, y pegar todo terminaría en un renglón imposible."""
    if not any(c.islower() for c in (texto or "")):
        return (texto or "").strip()
    palabras = [p for p in re.split(r"\s+", (texto or "").strip()) if p]
    salida: list = []
    for p in palabras:
        if salida and _es_propio(p) and _es_propio(salida[-1].rsplit(_PEGA, 1)[-1]):
            salida[-1] += _PEGA + p
        else:
            salida.append(p)
    return " ".join(salida)


def _palabras(texto: str, fuente: str, cuerpo: int, ancho: int, peso: str = "",
              pegar: bool = True) -> list:
    """Las palabras a repartir en renglones, con los nombres propios ya pegados.

    Si una tira pegada no entra ELLA SOLA en el renglón, no se hace polvo: se parte en los
    pedazos más grandes que sí entren. «Domingo Faustino Sarmiento» prefiere quedar como
    «Domingo Faustino» + «Sarmiento» antes que soltar las tres palabras por separado."""
    if not pegar:
        return [p for p in re.split(r"\s+", (texto or "").strip()) if p]
    palabras: list = []
    for p in re.split(r"\s+", _unir_nombres(texto)):
        if not p:
            continue
        if _PEGA not in p or _ancho_texto(p, fuente, cuerpo, peso) <= ancho:
            palabras.append(p)
            continue
        trozo = ""
        for palabra in p.split(_PEGA):
            prueba = f"{trozo}{_PEGA}{palabra}" if trozo else palabra
            if trozo and _ancho_texto(prueba, fuente, cuerpo, peso) > ancho:
                palabras.append(trozo)
                trozo = palabra
            else:
                trozo = prueba
        if trozo:
            palabras.append(trozo)
    return palabras


def _despegar(renglones: list) -> list:
    """Saca el pegamento: lo que se dibuja lleva espacios de verdad."""
    return [r.replace(_PEGA, " ") for r in renglones]


def _interletra(peso: str) -> tuple:
    """`"500/-25"` → `("500", -0.025)`: el peso y el interletrado en fracción del cuerpo.

    El interletrado viaja pegado al peso a propósito: el peso ya acompaña a cada renglón por
    todas las funciones que miden, cortan y dibujan, así que nadie puede medir con una
    separación y dibujar con otra (que es como un texto termina saliéndose del margen)."""
    peso = str(peso or "")
    if "/" not in peso:
        return peso, 0.0
    base, _, milesimas = peso.partition("/")
    try:
        return base, float(milesimas) / 1000
    except ValueError:
        return base, 0.0


def _ancho_texto(texto: str, fuente: str, cuerpo: int, peso: str = "") -> int:
    """Ancho en px de ese texto, con su interletrado. Sin PIL devuelve una estimación (no
    rompe el reel)."""
    texto = texto.replace(_PEGA, " ")     # el pegamento de los nombres mide como un espacio
    extra = _interletra(peso)[1] * cuerpo * max(0, len(texto) - 1)
    try:
        return int(_tipo(fuente, cuerpo, peso).getlength(texto) + extra)
    except Exception:  # noqa: BLE001
        return int(len(texto) * cuerpo * 0.62 + extra)


def _cuerpo_que_entra(texto: str, fuente: str, ancho: int, maximo: int) -> int:
    """El cuerpo más grande (≤ `maximo`) con el que el texto entra en `ancho`.

    Se mide en vez de fijarlo: si mañana se cambia `REEL_MARCA_TEXTO` por uno más
    largo, el renglón se achica solo en lugar de meterse abajo del isologo."""
    for cuerpo in range(maximo, MARCA_TAM_MIN - 1, -1):
        if _ancho_texto(texto, fuente, cuerpo) <= ancho:
            return cuerpo
    return MARCA_TAM_MIN


def _marca_layout(fuente: str) -> tuple[list, int, int, int]:
    """Dónde y de qué tamaño va cada renglón de la marca: (renglones, x, borde, ancho_util).

    Cada renglón es `(texto, cuerpo, y)`. Se calcula acá, en un solo lugar, porque lo
    usaban el dibujo y la medición y era fácil que se despegaran.
    """
    texto = _cfg("REEL_MARCA_TEXTO", MARCA_TEXTO)
    usuario = _cfg("REEL_MARCA_USUARIO", MARCA_USUARIO)
    mx = int(float(_cfg("REEL_LOGO_MARGEN_X", str(LOGO_MX))))
    my = int(float(_cfg("REEL_LOGO_MARGEN_Y", str(LOGO_MY))))
    ancho_logo = int(float(_cfg("REEL_LOGO_ANCHO", str(LOGO_ANCHO))))
    borde = int(float(_cfg("REEL_MARCA_BORDE", "3")))

    # Espacio libre: el cuadro menos los dos márgenes y la franja del isologo.
    hueco = 1080 - 2 * mx - ancho_logo - 24
    x = mx if _logo_a_la_derecha() else mx + ancho_logo + 24

    lineas = [l.strip() for l in texto.split("|") if l.strip()]
    if not lineas:
        return [], x, borde, hueco
    # Un solo cuerpo para toda la marca: el que hace entrar al renglón MÁS LARGO.
    cuerpo = min(_cuerpo_que_entra(l, fuente, hueco, MARCA_TAM_MAX) for l in lineas)
    cuerpo2 = max(MARCA_TAM_MIN, round(cuerpo * 0.75))
    salto = round(cuerpo * 1.2)
    # Centrado contra el isologo (514x568 px de origen → alto = ancho * 568/514).
    alto_logo = round(ancho_logo * 568 / 514)
    alto_texto = len(lineas) * salto + round(cuerpo2 * 1.2)
    y0 = my + max(0, (alto_logo - alto_texto) // 2)

    renglones = [(l, cuerpo, y0 + i * salto) for i, l in enumerate(lineas)]
    if usuario:
        renglones.append((usuario, cuerpo2, y0 + len(lineas) * salto))
    return renglones, x, borde, hueco


def _alto_linea(fuente: str, cuerpo: int, peso: str = "") -> int:
    """Alto REAL de un renglón: ascendente + descendente de esa tipografía en ese cuerpo.

    Hace falta para apoyar el último renglón contra el borde de abajo de la foto. El `salto`
    entre renglones NO sirve para eso: mide de una línea base a la siguiente y se olvida de
    la cola de la «g», la «p» o la «j» del último renglón, que sobresale por debajo. Con
    `n * salto` el texto se pasaba de la foto (visto el 2026-09-18)."""
    try:
        a, d = _tipo(fuente, cuerpo, peso).getmetrics()
        return int(a + d)
    except Exception:                                            # noqa: BLE001
        return int(cuerpo * 1.2)


def _alto_bloque(n: int, salto: int, fuente: str, cuerpo: int, peso: str = "") -> int:
    """Alto de un bloque de `n` renglones, desde el tope del primero hasta el pie del último."""
    return (n - 1) * salto + _alto_linea(fuente, cuerpo, peso) if n else 0


def _envolver(texto: str, fuente: str, cuerpo: int, ancho: int, maximo: int,
              peso: str = "", pegar: bool = True) -> list:
    """Parte `texto` en renglones que entren en `ancho`, hasta `maximo` renglones.

    Si sobra texto, el último renglón termina en «…»: así se ve que quedó cortado, en vez
    de que el titular parezca decir otra cosa. Con `pegar` (lo normal) nombre y apellido
    viajan juntos: ver `_unir_nombres`."""
    palabras = _palabras(texto, fuente, cuerpo, ancho, peso, pegar)
    if not palabras:
        return []
    renglones: list = []
    actual = ""
    sobra = False
    for p in palabras:
        prueba = f"{actual} {p}".strip()
        if actual and _ancho_texto(prueba, fuente, cuerpo, peso) > ancho:
            renglones.append(actual)
            actual = p
            if len(renglones) == maximo:
                sobra = True
                actual = ""
                break
        else:
            actual = prueba
    if actual:
        if len(renglones) < maximo:
            renglones.append(actual)
        else:
            sobra = True
    if not renglones:
        return []
    if sobra:
        ultimo = renglones[-1]
        while ultimo and _ancho_texto(ultimo + "…", fuente, cuerpo, peso) > ancho:
            ultimo = ultimo.rsplit(" ", 1)[0] if " " in ultimo else ultimo[:-1]
        renglones[-1] = (ultimo + "…") if ultimo else "…"
    return _despegar(renglones)


def _cortar_libre(texto: str, fuente: str, cuerpo: int, ancho: int, peso: str = "",
                  palabras: list | None = None) -> list:
    """Corta el texto en cuantos renglones haga falta para que entren en `ancho`. Sin tope.

    `palabras` viene de afuera cuando quien llama ya las calculó con el ancho DE VERDAD
    (ver `_emparejar`): así una prueba con un ancho angosto no despega un nombre."""
    palabras = palabras if palabras is not None else _palabras(texto, fuente, cuerpo, ancho, peso)
    renglones, actual = [], ""
    for p in palabras:
        prueba = f"{actual} {p}".strip()
        if actual and _ancho_texto(prueba, fuente, cuerpo, peso) > ancho:
            renglones.append(actual)
            actual = p
        else:
            actual = prueba
    if actual:
        renglones.append(actual)
    return _despegar(renglones)


def _emparejar(texto: str, fuente: str, cuerpo: int, ancho: int, maximo: int,
               peso: str = "", pegar: bool = True) -> list:
    """Reparte el texto en renglones PAREJOS, sin cambiar cuántos son.

    El corte normal es glotón: llena cada renglón hasta el tope y lo que sobra cae al
    siguiente. Con un titular de 7 palabras eso deja seis arriba y **una sola colgando**
    abajo, que se ve feo (pedido del usuario 2026-09-18).

    El truco: si con todo el ancho entra en N renglones, se busca el ancho MÁS ANGOSTO con
    el que sigue entrando en N. Al apretarlo, el texto se reparte solo y los renglones
    quedan de largo parecido. Búsqueda binaria: ~10 pasadas, nada de fuerza bruta."""
    # Las palabras se arman UNA sola vez y con el ancho REAL: si se recalcularan en cada
    # prueba, un ancho angosto despegaría los nombres y la búsqueda elegiría justo eso.
    palabras = _palabras(texto, fuente, cuerpo, ancho, peso, pegar)
    libres = _cortar_libre(texto, fuente, cuerpo, ancho, peso, palabras)
    if len(libres) <= 1 or len(libres) > maximo:
        # Una sola línea, o no entra y hay que recortar: de eso se encarga `_envolver`.
        return _envolver(texto, fuente, cuerpo, ancho, maximo, peso, pegar)
    objetivo = len(libres)
    bajo, alto, mejor = 1, ancho, libres
    while bajo <= alto:
        medio = (bajo + alto) // 2
        prueba = _cortar_libre(texto, fuente, cuerpo, medio, peso, palabras)
        if prueba and len(prueba) <= objetivo:
            mejor, alto = prueba, medio - 1
        else:
            bajo = medio + 1
    return mejor


def _mas_grande_que_entra(texto: str, fuente: str, ancho: int, maximo: int,
                          tam_max: int, tam_min: int, peso: str, pegar: bool) -> tuple:
    """El cuerpo más grande con el que el texto entra ENTERO. `(None, [])` si no entra."""
    for cuerpo in range(tam_max, tam_min - 1, -2):
        renglones = _envolver(texto, fuente, cuerpo, ancho, maximo, peso, pegar)
        if renglones and not renglones[-1].endswith("…"):
            return cuerpo, renglones
    return None, []


def _cuerpo_para(texto: str, fuente: str, ancho: int, maximo: int,
                 tam_max: int, tam_min: int, peso: str = "") -> tuple:
    """El cuerpo más grande (≤ `tam_max`) con el que el texto entra ENTERO en `maximo`
    renglones. Si ni con el mínimo entra, va el mínimo y el texto recortado con «…».

    Se prueba de las dos maneras: con el nombre y el apellido pegados y con ellos sueltos.
    Pegados es más lindo, pero a veces obliga a achicar la tipografía; si eso cuesta más de
    `PLACA_NOMBRE_COSTO` puntos, gana el titular grande. Un apellido en el renglón de abajo
    se nota menos que un titular chico.

    Devuelve `(cuerpo, renglones, pegar)`: `pegar` es lo que decidió acá, y quien llama tiene
    que repetírselo a `_emparejar` para que no vuelva a pegar lo que acá se soltó."""
    cuerpo_p, ren_p = _mas_grande_que_entra(texto, fuente, ancho, maximo,
                                            tam_max, tam_min, peso, True)
    cuerpo_s, ren_s = _mas_grande_que_entra(texto, fuente, ancho, maximo,
                                            tam_max, tam_min, peso, False)
    if cuerpo_p and (not cuerpo_s or cuerpo_p >= cuerpo_s - PLACA_NOMBRE_COSTO):
        return cuerpo_p, ren_p, True
    if cuerpo_s:
        if cuerpo_p:
            logger.info(f"Dejar el nombre entero obligaba a bajar el titular de {cuerpo_s} a "
                        f"{cuerpo_p}: lo dejo grande y el nombre parte de renglón.")
        else:
            logger.info("El titular no entraba con el nombre y el apellido juntos; los separo "
                        "para no recortar texto.")
        return cuerpo_s, ren_s, False
    return tam_min, _envolver(texto, fuente, tam_min, ancho, maximo, peso, False), False


def _color_fondo(auto: str = "") -> str:
    """El color de fondo de la placa, en el formato que entiende ffmpeg (`0xRRGGBB`).

    Manda el `.env` si alguien fijó `REEL_PLACA_FONDO`; si no, el color que se sacó de la
    propia imagen (`auto`); y si tampoco, el gris de siempre."""
    fijo = (get("REEL_PLACA_FONDO", "") or "").strip()
    c = fijo or (auto or "").strip() or PLACA_FONDO
    return ("0x" + c[1:]) if c.startswith("#") else c


def _fondo_auto_on() -> bool:
    """¿El fondo de la placa se saca del video/foto? NO por default desde el 2026-09-25: el
    usuario eligió el carbón con humo de sus reels de referencia. Entre el 20/9 y el 25/9 sí
    se sacaba; `REEL_PLACA_FONDO_AUTO=1` lo vuelve a prender (y el humo se tiñe de ese color)."""
    return _cfg("REEL_PLACA_FONDO_AUTO", "0").lower() not in ("0", "no", "false", "off")


def _rgb_de(color: str) -> tuple:
    """`0xRRGGBB` / `#RRGGBB` → (r, g, b). Un nombre de ffmpeg («black») va a negro."""
    c = (color or "").strip().lower().replace("#", "0x")
    try:
        v = int(c, 16)
        return (v >> 16) & 255, (v >> 8) & 255, v & 255
    except ValueError:
        return (0, 0, 0)


def fondo_placa_png(salida, color: str = "") -> Path | None:
    """El FONDO del reel estilo placa: carbón con textura de humo, más claro arriba.

    Copiado de los reels de referencia del usuario (2026-09-25). Medido ahí: arriba de todo
    ≈(48,47,52), a la altura de la volanta ≈(30,29,34) y hacia el medio ≈(24,24,28), con humo
    que se nota arriba y se aquieta hacia abajo. Esto da (49,48,53) / (31,30,35) / (22,21,26).

    Se genera cada vez con PIL + numpy (0,6 s) y con SEMILLA FIJA, así todos los reels salen
    con el mismo humo y lo que se prueba en la PC es lo que sale en la nube. El grano final
    (±1 nivel) evita que el degradé se vea en escalones en la pantalla del celular.

    `color` es el tono de base (el que sacó `_color_dominante`, si está prendido); vacío = el
    carbón de la referencia. None si no se pudo: el reel cae al color liso de siempre."""
    try:
        import numpy as np
        from PIL import Image, ImageFilter
    except Exception:                                            # noqa: BLE001
        return None
    try:
        w, h = 1080, 1920
        base = np.array(_rgb_de(_color_fondo(color)), np.float32)
        rng = np.random.default_rng(7)
        t = np.zeros((h, w), np.float32)
        if _cfg("REEL_PLACA_HUMO", "1").lower() not in ("0", "no", "false", "off"):
            # Ruido fractal: octavas de ruido suave, de las manchas grandes a las chicas.
            for paso, amp in ((420, 1.0), (210, 0.55), (105, 0.3), (52, 0.15)):
                cw, ch = w // paso + 3, h // paso + 3
                chico = Image.fromarray((rng.random((ch, cw)) * 255).astype(np.uint8))
                grande = chico.resize((cw * paso, ch * paso), Image.BICUBIC)
                a = np.asarray(grande.filter(ImageFilter.GaussianBlur(paso * 0.35)),
                               np.float32)[:h, :w] / 255
                t += amp * (a - 0.5)
            t /= max(1e-6, float(np.abs(t).max()))
            t = np.sign(t) * np.abs(t) ** 0.8          # realza las crestas: jirones de humo
        y = np.arange(h, dtype=np.float32)[:, None]
        luz = 24 * np.exp(-y / 140)                   # más claro arriba, como la referencia
        humo = 5 + 11 * np.exp(-y / 380)              # humo arriba, casi liso hacia abajo
        rgb = base[None, None, :] + (luz + humo * t)[..., None]
        rgb += rng.normal(0, 0.9, rgb.shape).astype(np.float32)
        salida = Path(salida)
        salida.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).save(salida)
        return salida
    except Exception as e:                                       # noqa: BLE001
        logger.warning(f"No pude armar el fondo de humo ({e}); va el color liso.")
        return None


def _apagar_color(r: int, g: int, b: int) -> str:
    """Baja un color a un tono OSCURO y APAGADO que sirva de fondo, conservando su tinte.

    Se le respeta el MATIZ (lo que hace que se sienta «el mismo color que el video») y se le
    imponen la luz y la saturación: un cielo celeste y un pasto verde terminan los dos en un
    tono profundo sobre el que el blanco y el naranja de la marca se leen igual de bien.
    Sin esto, un fondo claro se comería el titular blanco y uno naranja, a la marca."""
    import colorsys
    h, _l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    # La saturación se IMPONE, no se recorta. Antes iba `min(s, tope)` y ahí estaba la falla:
    # el material real viene lavado (una calle, una vereda, una noche), así que el `min` lo
    # dejaba en nada y todos los fondos salían el mismo casi-negro. Fijándola, el matiz que se
    # rescató de la imagen se ve de verdad.
    r2, g2, b2 = colorsys.hls_to_rgb(h, PLACA_FONDO_LUZ, PLACA_FONDO_SAT)
    return "0x%02X%02X%02X" % (round(r2 * 255), round(g2 * 255), round(b2 * 255))


# Debajo de esto un píxel es negro de sombra, no un color: su matiz es ruido de compresión.
# Estaba en 28 y por eso los fondos salían todos iguales — en casi cualquier foto la masa
# oscura gana por superficie, así que el «color dominante» terminaba siendo un casi-negro
# (medido en producción: 29,24,15 y 45,42,41 en dos reels seguidos).
FONDO_MIN_LUZ = 55
FONDO_MAX_LUZ = 225
# Saturación mínima del ganador para creerle el matiz. Abajo de esto el material es gris de
# verdad (una cámara de seguridad, un blanco y negro) y el fondo va al gris de siempre en vez
# de inventarle un color a partir de ruido de compresión. Bajó de 0,10 a 0,06 el 22/9: con
# 0,10 se iban al gris fotos que SÍ tienen tinte (una sala con luz cálida, un cielo plomizo).
# Medido sobre 62 fotos reales de notas: con 0,06 quedan grises 2, y las dos lo son de verdad.
FONDO_MIN_SAT = 0.06


def _color_dominante(src, work_dir) -> str:
    """El color principal del video/foto, ya apagado para usarlo de fondo. "" si no se pudo.

    Mira TRES cuadros repartidos (uno solo puede caer en un plano negro o en un flash) y se
    queda con el color más representativo. «Representativo» NO es «el que más superficie
    ocupa»: eso daba siempre un casi-negro, porque las sombras ocupan media foto y no tienen
    matiz. Se pondera la superficie por cuánto COLOR tiene cada tono, así el verde de una
    cancha le gana a la tribuna en sombra aunque ocupe menos.

    Nunca lanza: si algo falla, o si la imagen es gris de verdad, el reel sale con el gris
    de siempre."""
    if not _fondo_auto_on():
        return ""
    try:
        import colorsys
        from PIL import Image
        src, work_dir = Path(src), Path(work_dir)
        dur = duration_seconds(src) or 0.0
        momentos = [dur * f for f in (0.2, 0.5, 0.8)] if dur > 1 else [0.0]
        cuenta: dict = {}
        for i, seg in enumerate(momentos):
            tmp = work_dir / f"_color_{i}_{src.stem[:20]}.jpg"
            try:
                if not _extraer_frame(src, seg, tmp, escala=160, etiqueta="color de fondo"):
                    continue
                img = Image.open(tmp).convert("RGB").resize((80, 80))
                # A 24 colores: junta los tonos parecidos en uno, que es justo lo que se busca
                # (el «verde de la cancha», no los 4.000 verdes distintos que tiene el pasto).
                pal = img.quantize(colors=24, method=Image.Quantize.FASTOCTREE)
                crudo = pal.getpalette() or []
                for n, idx in pal.getcolors(80 * 80) or []:
                    r, g, b = crudo[idx * 3:idx * 3 + 3]
                    if max(r, g, b) < FONDO_MIN_LUZ or min(r, g, b) > FONDO_MAX_LUZ:
                        continue                    # sombra o reventado: no aportan matiz
                    _h, _l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
                    # Superficie PONDERADA por color: un gris grande no le gana a un tono
                    # mediano pero vivo. El peso es la saturación PELADA, sin base: con el
                    # «0,25 +» que había antes, el asfalto o una pared descascarada (s≈0,03)
                    # seguían ganando por superficie y el fondo terminaba yéndose al gris de
                    # descarte — medido en producción el 22/9, (97,93,91) en el reel de un
                    # corresponsal. Sin base, un gris necesita DIEZ VECES más superficie que
                    # un tono vivo para imponerse, y como las áreas son del mismo orden, no
                    # pasa. Contra 62 fotos reales de notas: los fondos grises bajaron de 29%
                    # a 8% y los colores distintos subieron de 37 a 46.
                    cuenta[(r, g, b)] = cuenta.get((r, g, b), 0) + n * s
            except Exception:                       # noqa: BLE001
                continue
            finally:
                try:
                    tmp.unlink()
                except Exception:                   # noqa: BLE001
                    pass
        if not cuenta:
            logger.info("La imagen es toda sombra o todo blanco: el fondo va gris.")
            return ""
        (r, g, b), _ = max(cuenta.items(), key=lambda kv: kv[1])
        _h, _l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        if s < FONDO_MIN_SAT:
            logger.info(f"El color que manda en la imagen es gris ({r},{g},{b}): no le invento "
                        f"un matiz, el fondo va gris.")
            return ""
        color = _apagar_color(r, g, b)
        logger.info(f"Fondo de la placa sacado de la imagen: {r},{g},{b} → {color}.")
        return color
    except Exception as e:                          # noqa: BLE001
        logger.warning(f"No pude sacarle el color a la imagen ({e}); el fondo va gris.")
        return ""


_FIN_ORACION = ".!?"

# Palabras «de enganche»: anuncian que viene algo más. Una frase recortada NO puede terminar
# en una de estas —«La obra se estrenó el año pasado en el.» se lee roto—, así que se siguen
# sacando hasta llegar a una palabra con contenido.
_COLGADAS = {
    "a", "al", "ante", "bajo", "cabe", "con", "contra", "de", "del", "desde", "durante",
    "en", "entre", "hacia", "hasta", "mediante", "para", "por", "según", "segun", "sin",
    "so", "sobre", "tras", "versus", "vía", "via",
    "el", "la", "los", "las", "un", "una", "unos", "unas", "lo",
    "mi", "tu", "su", "mis", "tus", "sus", "nuestro", "nuestra", "nuestros", "nuestras",
    "este", "esta", "estos", "estas", "ese", "esa", "esos", "esas", "aquel", "aquella",
    "y", "e", "o", "u", "ni", "pero", "sino", "aunque", "porque", "pues", "que", "qué",
    "quien", "quién", "cuyo", "cuya", "cual", "cuál", "donde", "dónde", "cuando", "cuándo",
    "como", "cómo", "si", "muy", "más", "mas", "menos", "tan", "también", "tambien",
    "se", "le", "les", "me", "te", "nos", "es", "son", "fue", "era", "está", "esta",
}


def _sin_colgar(texto: str) -> str:
    """Saca del final las palabras que dejan la frase colgada (preposiciones, artículos,
    conjunciones). Devuelve "" si al terminar no queda nada con sentido."""
    palabras = (texto or "").strip().rstrip(" ,;:.").split()
    while palabras and palabras[-1].lower().strip(",;:.") in _COLGADAS:
        palabras.pop()
    return " ".join(palabras)


def _oraciones(texto: str) -> list:
    """Parte el texto en oraciones enteras. No corta en «Sr.», «Dr.» ni en un número."""
    limpio = " ".join((texto or "").split())
    if not limpio:
        return []
    crudas = re.split(r"(?<=[.!?])\s+(?=[«\"'(¿¡A-ZÁÉÍÓÚÑ0-9])", limpio)
    salida: list = []
    for o in crudas:
        o = o.strip()
        if not o:
            continue
        # Una «oración» de dos letras es una abreviatura que se coló («Dr.»): se pega
        # a la anterior en vez de quedar suelta.
        if salida and len(o.split()) <= 1 and len(o) <= 4:
            salida[-1] += " " + o
        else:
            salida.append(o)
    return salida


def _cerrar(texto: str) -> str:
    """Devuelve el texto terminado en punto. Si ya cierra solo, no lo toca.

    Un «…» al final NO cuenta como cierre: se lo saca y pone el punto. Es la garantía de
    que ninguna frase que pase por acá puede quedar a mitad de idea."""
    texto = (texto or "").strip().rstrip("…").strip().rstrip(" ,;:")
    if not texto:
        return ""
    if texto[-1] in _FIN_ORACION:
        return texto
    limpio = _sin_colgar(texto)
    return (limpio + ".") if limpio else ""


def _texto_cerrado(texto: str, fuente: str, cuerpo: int, ancho: int, maximo: int,
                   peso: str) -> list:
    """Los renglones que entran, cortando por ORACIÓN ENTERA y cerrando en punto.

    `[]` si no entra ni la primera oración: NUNCA devuelve un renglón terminado en «…». O
    cierra la idea, o no devuelve nada y el que llama baja el cuerpo y vuelve a probar
    (pedido del usuario 2026-09-20: «la bajada tiene que ser una oración concluyente en un
    punto final, no podés dejarla cortada»)."""
    acum = ""
    mejor: list = []
    for o in _oraciones(texto):
        prueba = (acum + " " + o).strip()
        renglones = _envolver(prueba, fuente, cuerpo, ancho, maximo, peso)
        if not renglones or renglones[-1].endswith("…"):
            break          # esta oración ya no entra: me quedo con lo anterior, cerrado
        acum, mejor = prueba, renglones
    return _envolver(_cerrar(acum), fuente, cuerpo, ancho, maximo, peso) if acum else mejor


# Palabras que ABREN una idea nueva: justo antes de una de estas, la frase ya cerró algo y
# se puede cortar sin romperla. «…se concentró frente al palacio municipal | para reclamar
# por el estado de las calles» — cortando ahí queda una oración completa.
_ENGANCHES = {
    "y", "e", "o", "u", "ni", "pero", "sino", "aunque", "porque", "pues", "mientras",
    "que", "quien", "quienes", "donde", "cuando", "como", "según", "segun", "si",
    "para", "tras", "hasta", "desde", "durante", "con", "sin", "sobre", "además",
    "ademas", "también", "tambien", "luego", "después", "despues", "antes", "ya",
}


def _cortes_naturales(frase: str) -> list:
    """Dónde se puede cortar la frase sin romperla, de la más larga a la más corta.

    Dos clases de corte: la PUNTUACIÓN (coma, punto y coma, dos puntos), que es la señal más
    clara, y el lugar justo ANTES de una palabra que abre una idea nueva («y», «que»,
    «para», «hasta»…). Sin la segunda clase, una oración larga y SIN COMAS no tenía dónde
    cortarse y el texto desaparecía entero."""
    palabras = frase.split()
    cortes = []
    for i in range(1, len(palabras)):
        anterior = palabras[i - 1]
        if anterior[-1:] in ",;:":
            cortes.append(" ".join(palabras[:i]))
        elif palabras[i].lower().strip(".,;:«»\"'") in _ENGANCHES:
            cortes.append(" ".join(palabras[:i]))
    return sorted(set(cortes), key=len, reverse=True)


def _recorte_limpio(texto: str, fuente: str, cuerpo: int, ancho: int, maximo: int,
                    peso: str) -> list:
    """Último recurso: ni la primera oración entra al cuerpo más chico.

    Corta SOLO donde la frase respira (ver `_cortes_naturales`) y cierra en punto. `[]` si
    ningún corte de esos entra: **mejor nada que una frase rota**.

    Por qué no se recorta palabra por palabra: probado, deja cosas como «La obra se estrenó
    el año pasado en el Teatro Español y ya recorrió varias.» o «…de la ciudad junto.». El
    castellano no se puede cortar en cualquier lado, y una lista de palabras prohibidas al
    final nunca alcanza (ahí el problema era «varias» y «junto», no una preposición)."""
    primera = (_oraciones(texto) or [" ".join((texto or "").split())])[0]
    for trozo in _cortes_naturales(primera):
        cerrado = _cerrar(trozo)
        if len(cerrado.split()) < 5:     # menos de cinco palabras ya no dice nada
            continue
        renglones = _envolver(cerrado, fuente, cuerpo, ancho, maximo, peso)
        if renglones and not renglones[-1].endswith("…"):
            return renglones
    return []


def _bajada(texto: str, fuente: str, ancho: int, maximo: int, tam_max: int,
            tam_min: int, peso: str) -> tuple:
    """(cuerpo, renglones) de la bajada. SIEMPRE cierra en punto; nunca corta una frase.

    Va del cuerpo más grande al más chico y se queda con el PRIMERO en el que entra al
    menos una oración entera — así el texto es lo más grande que se pueda leyéndose
    completo. Si no entra ni la primera oración ni en el más chico, se recorta por coma."""
    for cuerpo in range(tam_max, tam_min - 1, -2):
        renglones = _texto_cerrado(texto, fuente, cuerpo, ancho, maximo, peso)
        if renglones:
            return cuerpo, renglones
    recortada = _recorte_limpio(texto, fuente, tam_min, ancho, maximo, peso)
    logger.info("La primera oración de la bajada no entra ni en el cuerpo más chico: "
                + ("la corto donde la frase respira y la cierro en punto."
                   if recortada else "y no tiene dónde cortarla sin romperla, así que el "
                                     "reel va sin bajada."))
    return tam_min, recortada


def oraciones_utiles(cuerpo: str, ya_dicho: str = "", cuantas: int = 3) -> list:
    """Las primeras oraciones FUERTES de la nota, para el pie del reel.

    Saltea lo que ya está en la bajada (no tiene sentido repetirlo tres centímetros más
    abajo) y las oraciones demasiado cortas para aportar algo. Es la misma idea que la
    descripción de SEO: la frase que cuenta la noticia si solo se lee una.

    Devuelve VARIAS porque el hueco del pie es chico: si la primera es larguísima y no tiene
    ni una coma donde cortarla, se prueba con la que sigue en vez de dejar el fondo vacío."""
    def clave(s: str) -> str:
        return re.sub(r"[^a-z0-9áéíóúñ ]", "", s.lower())

    dicho = clave(ya_dicho)
    salida: list = []
    for o in _oraciones(cuerpo):
        if len(o.split()) < 6:
            continue
        k = clave(o)
        if dicho and (k[:50] in dicho or dicho[:50] in k):
            continue
        salida.append(_cerrar(o))
        if len(salida) >= cuantas:
            break
    return salida


def primera_oracion_util(cuerpo: str, ya_dicho: str = "") -> str:
    """La primera de `oraciones_utiles`, o "" si no hay ninguna."""
    return (oraciones_utiles(cuerpo, ya_dicho, 1) or [""])[0]


def _metricas(fuente: str, cuerpo: int, peso: str = "") -> tuple:
    """(ascendente, alto de mayúscula) de esa tipografía en ese cuerpo.

    La referencia se midió por el TOPE DE LAS MAYÚSCULAS (es lo que el ojo ve como «donde
    empieza el renglón»), así que los aires entre bloques se cuentan desde ahí. Y PIL dibuja
    desde el ascendente, que queda más arriba: con los dos números se pasa de uno al otro."""
    try:
        f = _tipo(fuente, cuerpo, peso)
        asc = f.getmetrics()[0]
        mayus = -f.getbbox("HÁ|", anchor="ls")[1]
        return int(asc), int(mayus)
    except Exception:                                            # noqa: BLE001
        return int(cuerpo * 0.95), int(cuerpo * 0.8)


def _pie_de_tinta(texto: str, fuente: str, cuerpo: int, peso: str = "") -> int:
    """Cuánto baja la tinta de ESTE renglón por debajo de su línea base: la cola de la «p» o
    de la «g» si la tiene, casi nada si no. Así el aire hasta la foto es el que se ve."""
    try:
        return max(0, int(_tipo(fuente, cuerpo, peso).getbbox(texto or "x", anchor="ls")[3]))
    except Exception:                                            # noqa: BLE001
        return int(cuerpo * 0.22)


# Con cuántos renglones va el titular. La referencia prefiere POCOS renglones con letra un
# poco más chica antes que muchos con la letra al tope: «Un auto se incendió en la Ruta 30»
# va en DOS renglones a ~100, no en tres a 112. Entonces: la menor cantidad de renglones que
# todavía deje la letra en al menos este porcentaje del tope.
PLACA_TITULAR_PREFIERE = 0.86


def _titular_cuerpo(texto: str, fuente: str, ancho: int, maximo: int, tope: int,
                    minimo: int, peso: str) -> tuple:
    """(cuerpo, renglones, pegar) del titular. Ver `PLACA_TITULAR_PREFIERE`.

    Si ningún corte llega a esa letra, gana el de letra más grande, pero un renglón más
    solo si agranda la letra al menos un 8%: si no, se nota el renglón y no la letra."""
    mejor = None
    for n in range(1, maximo + 1):
        cuerpo, ren, pegar = _cuerpo_para(texto, fuente, ancho, n, tope, minimo, peso)
        if not ren or ren[-1].endswith("…"):
            continue                              # en n renglones no entra entero
        if cuerpo >= PLACA_TITULAR_PREFIERE * tope:
            return cuerpo, ren, pegar
        if mejor is None or cuerpo >= mejor[0] * 1.08:
            mejor = (cuerpo, ren, pegar)
    return mejor or _cuerpo_para(texto, fuente, ancho, maximo, tope, minimo, peso)


def _pie_bloques(frases, zona: tuple, fuente: str, peso: str) -> list:
    """El texto de PIE en `zona=(desde, hasta)`: la primera frase que entre ENTERA, con el
    cuerpo más grande posible (de `PLACA_PIE_TAM` a `PLACA_PIE_MIN`). Arranca arriba de la
    zona, pegado a la foto, como un epígrafe. `[]` si no entra ninguna."""
    frases = [" ".join(f.split()) for f in ([frases] if isinstance(frases, str) else list(frases))
              if (f or "").strip()]
    desde, hasta = zona
    hueco = hasta - desde
    if not frases or hueco < PLACA_PIE_MIN_ALTO:
        if frases:
            logger.info(f"Bajo la foto quedan {hueco}px libres: no alcanza para el pie.")
        return []
    ancho = 1080 - PLACA_MX - PLACA_MX_DER
    tmax = int(_num("REEL_PLACA_PIE_TAM", PLACA_PIE_TAM))
    # El hueco es chico y fijo, así que acá manda el hueco: para cada cuerpo se calcula
    # CUÁNTOS renglones entran y recién ahí se prueba el texto.
    elegido = None
    mayor = None                     # el cuerpo más grande en el que entra AL MENOS uno
    for cuerpo in range(tmax, PLACA_PIE_MIN - 1, -2):
        salto = round(cuerpo * PLACA_BAJADA_SALTO)
        alto_l = _alto_linea(fuente, cuerpo, peso)
        if alto_l > hueco:
            continue
        cabe = min(PLACA_PIE_RENGLONES, 1 + max(0, (hueco - alto_l) // salto))
        mayor = mayor or (cuerpo, salto, cabe)
        for frase in frases:
            renglones = _texto_cerrado(frase, fuente, cuerpo, ancho, cabe, peso)
            if renglones:
                elegido = (cuerpo, renglones, salto)
                break
        if elegido:
            break
    if not elegido and mayor and mayor[2] >= 2:
        # No entra entera en ningún cuerpo: se recorta por donde la frase respira y se
        # cierra en punto. Con UN solo renglón no: de ahí sale un muñón, no una frase.
        cuerpo, salto, cabe = mayor
        for frase in frases:
            renglones = _recorte_limpio(frase, fuente, cuerpo, ancho, cabe, peso)
            if renglones:
                elegido = (cuerpo, renglones, salto)
                break
    if not elegido:
        logger.info(f"La frase del pie no entra ni recortada en {hueco}px: va sin pie.")
        return []
    cuerpo, renglones, salto = elegido
    logger.info(f"Pie del reel: {len(renglones)} renglón/es de cuerpo {cuerpo} bajo la foto.")
    return [(l, cuerpo, desde + i * salto, fuente, peso, GRIS, False)
            for i, l in enumerate(renglones)]


def _cortar_en_dos_puntos(titular: str, lineas: list, fuente: str, cuerpo: int, ancho: int,
                          peso: str, pegar: bool) -> tuple:
    """«Zona Fría: el Senado aprobó reducir subsidios» → «Zona Fría:» solo en el primer
    renglón, como en la referencia. Los dos puntos separan el TEMA de la noticia, y cortar
    ahí se lee mejor que repartir parejo.

    Nunca cuesta un renglón más, y letra lo MÍNIMO: se prueba con el mismo cuerpo y, si el
    resto no entra, bajando hasta un 7% (a 118 eso es 110, no se nota). Devuelve
    `(cuerpo, renglones)`; si no se puede, los de entrada sin tocar."""
    cabeza, dos, resto = titular.partition(": ")
    if not dos or len(lineas) < 2 or not resto.strip() or len(cabeza.split()) > 5:
        return cuerpo, lineas
    cabeza = cabeza + ":"
    for c in range(cuerpo, int(cuerpo * 0.93) - 1, -2):
        if _ancho_texto(cabeza, fuente, c, peso) > ancho:
            continue
        cola = _envolver(resto, fuente, c, ancho, len(lineas) - 1, peso, pegar)
        if cola and not cola[-1].endswith("…"):
            cola = _emparejar(resto, fuente, c, ancho, len(cola), peso, pegar) or cola
            return c, [cabeza] + cola
    return cuerpo, lineas


# Hasta qué cuerpo se achica la bajada con tal de quedar en dos renglones.
PLACA_BAJADA_CORTA_MIN = 50


def _bajada_corta(texto: str, fuente: str, ancho: int, maximo: int, tam_max: int,
                  tam_min: int, peso: str) -> tuple:
    """Como `_bajada`, pero prefiriendo DOS renglones con letra grande, como la referencia
    («Chivilcoy quedaría alcanzada / por el cambio.»): la bajada acompaña al titular, no
    compite con él. Recién si la primera oración no entra en dos renglones a buen tamaño se
    usan los `maximo`."""
    if maximo > 2:
        for cuerpo in range(tam_max, max(tam_min, PLACA_BAJADA_CORTA_MIN) - 1, -2):
            renglones = _texto_cerrado(texto, fuente, cuerpo, ancho, 2, peso)
            if renglones:
                return cuerpo, renglones
    return _bajada(texto, fuente, ancho, maximo, tam_max, tam_min, peso)


def placa_layout(volanta: str, titular: str, resumen: str, f_titular: str, f_resumen: str,
                 p_titular: str = "", p_resumen: str = "", pie="",
                 pie_zona: tuple | None = None) -> dict:
    """El bloque de texto de arriba y dónde empieza la imagen.

    Devuelve `{bloques, y_img, bajada}`; cada bloque es
    `(texto, cuerpo, y, fuente, peso, color, centrado)`, con `y` en el ASCENDENTE del
    renglón (el ancla «la» de PIL). Todo va a la IZQUIERDA, sobre `PLACA_MX`, como en los
    reels de referencia del usuario (2026-09-25): marca chica arriba, volanta NARANJA,
    titular y bajada BLANCOS. `bajada` son los renglones de la bajada (para los chequeos).

    Nada se dibuja dentro de las zonas que tapan Instagram y TikTok (ver `SEGURO_ARRIBA` y
    `BANDA_SEGURO`) ni encima del isologo.

    Si el texto viene largo NO se achica la foto por debajo de `PLACA_IMG_MIN`: se prueban
    escalones cada vez más compactos (titular más chico, bajada en dos renglones, menos aire)
    hasta que entre. Con `pie` y `pie_zona=(desde, hasta)` se escribe además una frase en el
    hueco que queda bajo una foto apaisada."""
    ancho = 1080 - PLACA_MX - PLACA_MX_DER
    p_volanta = _cfg("REEL_PESO_VOLANTA", PESO_VOLANTA)
    p_marca = _cfg("REEL_PESO_MARCA", PESO_MARCA)
    f_marca = f_resumen

    # ── Marca: los dos medios en UN renglón y el usuario abajo, chiquitos. ──────────────
    marca: list = []
    nombres = " | ".join(l.strip() for l in
                         _cfg("REEL_PLACA_MARCA_TEXTO", PLACA_MARCA_TEXTO).split("|")
                         if l.strip())
    usuario = _cfg("REEL_MARCA_USUARIO", MARCA_USUARIO)
    tam = int(_num("REEL_PLACA_MARCA_TAM", PLACA_MARCA_TAM))
    # Que no se meta debajo del isologo si alguien pone un nombre más largo.
    caja_logo = _logo_caja()
    borde = (caja_logo[0] - 24) if (caja_logo and _logo_a_la_derecha()) else 1080 - PLACA_MX_DER
    while tam > MARCA_TAM_MIN and nombres and _ancho_texto(nombres, f_marca, tam, p_marca) > borde - PLACA_MX:
        tam -= 1
    asc_m, may_m = _metricas(f_marca, tam, p_marca)
    base = PLACA_Y0 + may_m
    fin_marca = PLACA_Y0
    for txt in (nombres, usuario):
        if txt:
            marca.append((txt, tam, base - asc_m, f_marca, p_marca, GRIS, False))
            fin_marca = base
            base += round(PLACA_MARCA_SALTO * tam / PLACA_MARCA_TAM)
    # Con el isologo a la IZQUIERDA (`REEL_LOGO_LADO`) la marca le queda al lado y el texto
    # editorial tiene que arrancar por debajo de él.
    piso_logo = (caja_logo[3] + 20) if (caja_logo and not _logo_a_la_derecha()) else 0

    def componer(tit_tope: int, baj_renglones: int, gap_marca: int, vol_tope: int):
        bloques = list(marca)
        tinta = fin_marca                      # hasta dónde llega la tinta hasta acá
        linea = fin_marca                      # última línea base dibujada
        gap = gap_marca

        # Volanta (NARANJA). Es corta por naturaleza (mediana de 23 caracteres). Cuando
        # viene larga NO se corta con «…»: se achica, y si no, se va a dos renglones.
        if volanta:
            v, lineas = _mas_grande_que_entra(volanta, f_resumen, ancho, 1, vol_tope,
                                              PLACA_VOLANTA_MIN, p_volanta, True)
            if not v:
                v, lineas = _mas_grande_que_entra(volanta, f_resumen, ancho, 2, vol_tope,
                                                  PLACA_VOLANTA_MIN, p_volanta, True)
            if not v:
                v, lineas = PLACA_VOLANTA_MIN, _envolver(volanta, f_resumen, PLACA_VOLANTA_MIN,
                                                         ancho, 2, p_volanta)
            lineas = _emparejar(volanta, f_resumen, v, ancho, len(lineas) or 1,
                                p_volanta) or lineas
            asc, may = _metricas(f_resumen, v, p_volanta)
            b = max(linea + gap + may, piso_logo + may)
            for i, l in enumerate(lineas):
                bl = b + i * round(v * 1.15)
                bloques.append((l, v, bl - asc, f_resumen, p_volanta, NARANJA, False))
                linea, tinta = bl, bl + _pie_de_tinta(l, f_resumen, v, p_volanta)
            gap = PLACA_GAP_VOLANTA

        # Titular (BLANCO), lo más grande que entre en los renglones que prefiere.
        if titular:
            c, lineas, pegar = _titular_cuerpo(titular, f_titular, ancho,
                                               PLACA_TITULAR_RENGLONES, tit_tope,
                                               PLACA_TITULAR_MIN, p_titular)
            # Mismos renglones, repartidos parejo. `pegar` va tal cual: si arriba se decidió
            # soltar el nombre, acá no se puede volver a pegar.
            lineas = _emparejar(titular, f_titular, c, ancho, len(lineas) or 1,
                                p_titular, pegar) or lineas
            c, lineas = _cortar_en_dos_puntos(titular, lineas, f_titular, c, ancho,
                                              p_titular, pegar)
            asc, may = _metricas(f_titular, c, p_titular)
            salto = round(c * PLACA_TITULAR_SALTO)
            b = max(linea + gap + may, piso_logo + may)
            for i, l in enumerate(lineas):
                bl = b + i * salto
                bloques.append((l, c, bl - asc, f_titular, p_titular, BLANCO, False))
                linea, tinta = bl, bl + _pie_de_tinta(l, f_titular, c, p_titular)
            gap = PLACA_GAP_TITULAR

        # Bajada (BLANCA): oraciones enteras, SIEMPRE cerrada en punto (nunca un «…»).
        lineas_b: list = []
        if resumen:
            tmax = int(_num("REEL_PLACA_BAJADA_TAM", PLACA_BAJADA_TAM))
            c, lineas_b = _bajada_corta(resumen, f_resumen, ancho, baj_renglones, tmax,
                                        PLACA_BAJADA_MIN, p_resumen)
            if lineas_b and len(lineas_b[-1].split()) < 2:
                # La bajada va con el primer renglón LLENO, como en la referencia
                # («Chivilcoy quedaría alcanzada / por el cambio.»). Solo si el último
                # renglón queda con una palabra colgando se reparte parejo.
                parejo = _emparejar(" ".join(lineas_b), f_resumen, c, ancho, len(lineas_b),
                                    p_resumen)
                if parejo and not parejo[-1].endswith("…"):
                    lineas_b = parejo
            if lineas_b:
                asc, may = _metricas(f_resumen, c, p_resumen)
                salto = round(c * PLACA_BAJADA_SALTO)
                b = max(linea + gap + may, piso_logo + may)
                for i, l in enumerate(lineas_b):
                    bl = b + i * salto
                    bloques.append((l, c, bl - asc, f_resumen, p_resumen, BLANCO, False))
                    linea, tinta = bl, bl + _pie_de_tinta(l, f_resumen, c, p_resumen)
        return bloques, tinta + PLACA_AIRE_IMG, lineas_b

    tope = int(_num("REEL_PLACA_TITULAR_TAM", PLACA_TITULAR_TAM))
    vol = int(_num("REEL_PLACA_VOLANTA_TAM", PLACA_VOLANTA_TAM))
    # Del más generoso (el de la referencia) al más compacto. Se usa el PRIMERO en el que la
    # foto conserve al menos `PLACA_IMG_MIN`.
    # Primero se achica el AIRE entre la marca y la volanta (es lo que menos se extraña),
    # después la letra del titular, y recién al final la bajada pierde un renglón.
    escalones = [(tope, 3, PLACA_GAP_MARCA, vol)]
    for t, r, g, v in ((112, 3, 96, 50), (112, 3, 70, 50), (104, 3, 64, 50),
                       (96, 3, 60, 48), (88, 3, 56, 48), (88, 2, 56, 46), (80, 2, 52, 46),
                       (72, 2, 50, 44), (64, 2, 48, 42), (56, 2, 46, 40),
                       (PLACA_TITULAR_MIN, 1, 44, 38)):
        if t <= tope:
            escalones.append((t, r, min(g, PLACA_GAP_MARCA), min(v, vol)))
    limite = 1920 - int(_num("REEL_PLACA_IMG_MIN", PLACA_IMG_MIN))
    for i, esc in enumerate(escalones):
        bloques, y_img, lineas_b = componer(*esc)
        if y_img <= limite:
            if i:
                logger.info(f"El texto venía largo: titular a {esc[0]} como tope y bajada en "
                            f"{esc[1]} renglón/es para que la foto conserve su lugar.")
            break
    else:
        logger.warning(f"Ni en el escalón más compacto el texto deja {1920 - limite}px de foto: "
                       f"queda de {1920 - y_img}px.")

    pie_b = _pie_bloques(pie, pie_zona, f_resumen, p_resumen) if (pie and pie_zona) else []
    return dict(bloques=bloques + pie_b, y_img=y_img, bajada=lineas_b, pie=len(pie_b))


def placa_texto_png(volanta: str, titular: str, resumen: str, salida, *,
                    f_titular: str = "", f_resumen: str = "",
                    p_titular: str = "", p_resumen: str = "",
                    pie: str = "", pie_zona: tuple | None = None):
    """Dibuja TODO el texto del reel (marca + volanta + titular + bajada, y el pie si se
    pide) en un PNG transparente de 1080x1920. Devuelve `(png, y_img, pie)`: dónde empieza
    la imagen y cuántos renglones de pie se dibujaron (0 si no entró).

    Se llama DOS veces cuando hay pie: la primera sin pie, para saber dónde arranca la
    imagen y calcular cuánto fondo queda abajo; la segunda ya con `pie_zona`. La segunda
    pasada no mueve nada de arriba (el bloque es idéntico), solo agrega la frase del pie.

    Va como imagen y no con `drawtext` por lo de siempre: al ffmpeg de Linux de la nube le
    falta libfreetype. Con `overlay` anda igual acá que allá."""
    volanta = " ".join((volanta or "").split())
    titular = " ".join((titular or "").split())
    resumen = " ".join((resumen or "").split())
    if not (titular or resumen or volanta):
        return None
    f_titular = f_titular or _fuente_banda("REEL_FUENTE_TITULAR", FUENTE_TITULAR)
    f_resumen = f_resumen or _fuente_banda("REEL_FUENTE_RESUMEN", FUENTE_RESUMEN)
    if not (f_titular and f_resumen):
        logger.warning("Sin tipografía para la placa del reel; el reel va sin texto.")
        return None
    p_titular = p_titular or _cfg("REEL_PESO_TITULAR", PESO_TITULAR)
    p_resumen = p_resumen or _cfg("REEL_PESO_RESUMEN", PESO_RESUMEN)
    try:
        from PIL import Image, ImageDraw
    except Exception as e:                                       # noqa: BLE001
        logger.warning(f"Sin PIL para dibujar la placa ({e}); el reel va sin texto.")
        return None

    caja = placa_layout(volanta, titular, resumen, f_titular, f_resumen, p_titular,
                        p_resumen, pie=pie, pie_zona=pie_zona)
    if not caja["bloques"]:
        return None
    try:
        lienzo = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
        dibujar_bloques(ImageDraw.Draw(lienzo), caja["bloques"])
        salida = Path(salida)
        salida.parent.mkdir(parents=True, exist_ok=True)
        lienzo.save(salida, "PNG")
    except Exception as e:                                       # noqa: BLE001
        logger.warning(f"No pude dibujar la placa del reel ({e}); va sin texto.")
        return None
    logger.info(f"Placa del reel: {len(caja['bloques'])} renglón/es · la imagen arranca "
                f"en y={caja['y_img']}")
    return salida, caja["y_img"], caja["pie"]


def dibujar_bloques(dib, bloques) -> None:
    """Dibuja los renglones que arma `placa_layout` / `_pie_bloques` sobre un ImageDraw."""
    for texto, cuerpo, y, fuente, peso, color, centrado in bloques:
        f = _tipo(fuente, cuerpo, peso)
        # `anchor="la"`: la `y` es el ascendente del renglón. Centrado (lo usaba el estilo
        # anterior) ancla por el medio y la x pasa a ser el centro.
        x, anchor = (540, "ma") if centrado else (PLACA_MX, "la")
        track = _interletra(peso)[1] * cuerpo
        if not track or centrado:
            # Sin sombra: contra el fondo liso solo ensuciaba el contorno de la letra.
            dib.text((x, y), texto, font=f, fill=color, anchor=anchor)
            continue
        # Con interletrado, letra por letra. La posición de cada una sale de medir el texto
        # HASTA ella inclusive menos su propio ancho: así se respeta el kerning de la pareja
        # que forma con la anterior (medir solo el prefijo lo perdería).
        for i, letra in enumerate(texto):
            if letra == " ":
                continue
            pos = f.getlength(texto[:i + 1]) - f.getlength(letra) + i * track
            dib.text((x + pos, y), letra, font=f, fill=color, anchor="la")


def _curva_suave(t: float) -> float:
    """0→1 sin escalón en ninguna punta (smootherstep). Es lo que hace que el desvanecido no
    se VEA: una curva lineal deja una línea donde arranca y otra donde termina."""
    t = max(0.0, min(1.0, t))
    return t * t * t * (t * (t * 6 - 15) + 10)


def mascara_fundido(ancho: int, alto: int, *, arriba: int = 0, abajo: int = 0):
    """Máscara «L» (0 = transparente, 255 = opaco) que DISUELVE los bordes de la imagen:
    `arriba` px en el borde de arriba y `abajo` px en el de abajo, con curva suave.

    El desvanecido NUNCA se come más de un tercio de la foto por borde (la mitad si se funde
    uno solo). Sin ese freno, una panorámica muy ancha (4000x800 entra como una tira de
    216px) quedaba más baja que el desvanecido y DESAPARECÍA (encontrado auditando,
    2026-09-18)."""
    from PIL import Image
    ancho, alto = max(2, int(ancho)), max(2, int(alto))
    # Con los DOS bordes fundidos (una apaisada entera, que suele ser baja) cada uno se queda
    # con a lo sumo un 22% del alto: con un tercio, una foto 16:9 quedaba nítida solo en la
    # franja del medio.
    techo = max(1, int(alto * 0.22) if (arriba and abajo) else alto // 2)
    arriba, abajo = min(int(arriba), techo), min(int(abajo), techo)
    col = [255] * alto
    for y in range(arriba):
        col[y] = round(255 * _curva_suave(y / arriba))
    for y in range(abajo):
        fila = alto - 1 - y
        col[fila] = min(col[fila], round(255 * _curva_suave(y / abajo)))
    return Image.frombytes("L", (1, alto), bytes(col)).resize((ancho, alto), Image.NEAREST)


def fundido_png(alto: int, salida, *, abajo: bool = False, ancho: int = 1080) -> Path | None:
    """Máscara en escala de grises para fundir los BORDES de la imagen con el fondo.

    Negro (transparente) → blanco (opaco) a los `PLACA_FUNDIDO` px, con curva suave.
    `alphamerge` la usa como canal alfa de la imagen, y así el corte deja de ser una línea
    recta: la foto se DISUELVE en el fondo en vez de terminar de golpe.

    `abajo=True` agrega el SOMBREADO del borde de abajo (`PLACA_FUNDIDO_ABAJO`): hace falta
    cuando la imagen NO llega al pie del cuadro —una foto o un video apaisado— y debajo
    queda el fondo (pedido del usuario 2026-09-25). `ancho` es el de la imagen: cuando va
    ENTERA y más angosta que el cuadro, la máscara tiene que medir lo mismo o `alphamerge`
    se queja de que no coinciden."""
    try:
        m = mascara_fundido(ancho, alto,
                            arriba=int(_num("REEL_PLACA_FUNDIDO", PLACA_FUNDIDO)),
                            abajo=int(_num("REEL_PLACA_FUNDIDO_ABAJO", PLACA_FUNDIDO_ABAJO))
                            if abajo else 0)
        salida = Path(salida)
        m.save(salida)
        return salida
    except Exception as e:                                       # noqa: BLE001
        logger.warning(f"No pude armar el fundido de la imagen ({e}); va con borde duro.")
        return None


def marca_texto_png(salida) -> Path | None:
    """Dibuja el TEXTO de marca en un PNG transparente de 1080x1920, para superponerlo
    con `overlay`.

    Antes esto se hacía con el filtro `drawtext` de ffmpeg, y el 2026-09-17 se descubrió
    por qué eso era frágil: **`drawtext` necesita libfreetype y no todas las builds lo
    traen**. El ffmpeg que se instala en el Linux de la nube NO lo tiene (el de Windows
    sí), así que los reels salían sin nada de marca — y encima era irreproducible en la
    PC. `overlay`, en cambio, está en todas las builds.

    Beneficio de fondo: lo que se prueba en la PC ahora es lo mismo que corre en la nube.
    """
    salida = Path(salida)
    texto = _cfg("REEL_MARCA_TEXTO", MARCA_TEXTO)
    if texto.lower() in ("0", "no", "off", "false"):
        return None
    fuente = _fuente_marca()
    if not fuente:
        logger.warning("Sin tipografía para el texto de marca del reel; se omite.")
        return None
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception as e:                                       # noqa: BLE001
        logger.warning(f"Sin PIL para dibujar el texto de marca ({e}); se omite.")
        return None

    renglones, x, borde, _ = _marca_layout(fuente)
    if not renglones:
        return None

    # Los mismos colores que tenía el drawtext: blanco, con contorno y sombra oscuros
    # para que se lea sobre cualquier foto (el blanco pelado sobre un fondo claro
    # desaparecía). 0.45 de opacidad = 115 de alfa.
    NEGRO = (0, 0, 0, 115)
    BLANCO = (255, 255, 255, 255)
    try:
        lienzo = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
        dibujo = ImageDraw.Draw(lienzo)
        for linea, cuerpo, y in renglones:
            f = _tipo(fuente, cuerpo)
            # Sombra primero, corrida 2px, igual que shadowx/shadowy del filtro.
            dibujo.text((x + 2, y + 2), linea, font=f, fill=NEGRO, anchor="la")
            dibujo.text((x, y), linea, font=f, fill=BLANCO, anchor="la",
                        stroke_width=borde, stroke_fill=NEGRO)
        salida.parent.mkdir(parents=True, exist_ok=True)
        lienzo.save(salida, "PNG")
    except Exception as e:                                       # noqa: BLE001
        logger.warning(f"No pude dibujar el texto de marca ({e}); el reel va sin él.")
        return None
    logger.info(f"Texto de marca dibujado: {len(renglones)} renglón/es")
    return salida


def _marca_drawtext(in_label: str, work_dir: Path) -> tuple[str, str]:
    """(fragmento_de_filtro, etiqueta_de_salida) con el nombre de los dos medios —uno
    debajo del otro— y, abajo de todo, el usuario de las redes. Va arriba, del lado
    LIBRE (el contrario al isologo) y centrado a la altura del isologo. Blanco, con
    contorno y sombra oscuros para que se lea sobre cualquier foto.

    En `REEL_MARCA_TEXTO` el «|» SEPARA RENGLONES (no se dibuja). El texto va por
    `textfile=` y no inline porque la Ñ de «CAMPAÑA» pelea con el parser del
    filtergraph de ffmpeg. Se apaga con `REEL_MARCA_TEXTO=0`."""
    texto = _cfg("REEL_MARCA_TEXTO", MARCA_TEXTO)
    usuario = _cfg("REEL_MARCA_USUARIO", MARCA_USUARIO)
    if texto.lower() in ("0", "no", "off", "false"):
        return "", in_label
    if not _hay_drawtext():
        return "", in_label
    fuente = _fuente_marca()
    if not fuente:
        logger.warning("Sin tipografía para el texto de marca del reel; se omite.")
        return "", in_label

    mx = int(float(_cfg("REEL_LOGO_MARGEN_X", str(LOGO_MX))))
    my = int(float(_cfg("REEL_LOGO_MARGEN_Y", str(LOGO_MY))))
    ancho_logo = int(float(_cfg("REEL_LOGO_ANCHO", str(LOGO_ANCHO))))
    # Espacio libre: todo el cuadro menos los dos márgenes y la franja del isologo.
    hueco = 1080 - 2 * mx - ancho_logo - 24
    x = mx if _logo_a_la_derecha() else mx + ancho_logo + 24

    lineas = [l.strip() for l in texto.split("|") if l.strip()]
    # Un solo cuerpo para toda la marca: el que hace entrar al renglón MÁS LARGO.
    cuerpo = min(_cuerpo_que_entra(l, fuente, hueco, MARCA_TAM_MAX) for l in lineas)
    cuerpo2 = max(MARCA_TAM_MIN, round(cuerpo * 0.75))
    salto = round(cuerpo * 1.2)
    # Centrado contra el isologo (514x568 px de origen → alto = ancho * 568/514).
    alto_logo = round(ancho_logo * 568 / 514)
    alto_texto = len(lineas) * salto + round(cuerpo2 * 1.2)
    y = my + max(0, (alto_logo - alto_texto) // 2)

    # Contorno oscuro: el blanco PELADO sobre una foto clara desaparece (probado sobre un
    # fondo casi blanco: quedaba solo la sombra). El contorno lo deja legible sobre
    # cualquier imagen sin tener que meterle una caja negra detrás.
    borde = int(float(_cfg("REEL_MARCA_BORDE", "3")))

    work_dir.mkdir(parents=True, exist_ok=True)
    renglones = [(l, cuerpo, i * salto) for i, l in enumerate(lineas)]
    if usuario:
        renglones.append((usuario, cuerpo2, len(lineas) * salto))
    frag, label = "", in_label
    for i, (linea, cuerpo_i, dy) in enumerate(renglones):
        if not linea:
            continue
        archivo = work_dir / f"marca{i}.txt"
        archivo.write_text(linea, encoding="utf-8")
        out = f"[vm{i}]"
        frag += (f"{';' if frag else ''}{label}drawtext=textfile='{_esc_ff(archivo)}'"
                 f":fontfile='{_esc_ff(fuente)}':fontcolor=white:fontsize={cuerpo_i}"
                 f":borderw={borde}:bordercolor=black@0.45"
                 f":shadowcolor=black@0.45:shadowx=2:shadowy=2:x={x}:y={y + dy}{out}")
        label = out
    return frag, label


def _firma_drawtext(texto: str, in_label: str, work_dir: Path) -> tuple[str, str]:
    """Devuelve (fragmento_de_filtro, etiqueta_de_salida) que estampa la firma del
    corresponsal ARRIBA, del lado libre del logo, en 2 renglones, sobre una caja
    semitransparente (para que se lea sobre el fondo difuminado). El texto va por
    `textfile=` para no pelear con tildes/guiones/'·' en el filtergraph. Si no hay fuente
    disponible, no dibuja nada (devuelve el label original).

    ⚠️ OJO: la usa UN solo llamado, `transcriber_radio.run_video_radio` cuando el video es
    de un corresponsal de la radio. Y comparte el lugar EXACTO con el texto de marca
    (`marca_texto_png`, mismos mx/my): si las dos salen, se pisan. Hoy no se nota porque
    el ffmpeg de Linux de la nube no trae `drawtext` y la firma no se dibuja nunca allá.
    Si se vuelve a prender de verdad, hay que correrla hacia abajo."""
    if not _hay_drawtext():
        return "", in_label
    font = _font_file()
    if not font:
        logger.warning("Sin fuente para la firma del reel; se omite el drawtext.")
        return "", in_label
    work_dir.mkdir(parents=True, exist_ok=True)
    firma_txt = work_dir / "firma.txt"
    firma_txt.write_text(_dos_renglones(texto.strip()), encoding="utf-8")
    # Va del lado LIBRE: con el logo a la derecha arranca contra el margen izquierdo;
    # con el logo a la izquierda, corrida por el ancho del logo (como era antes).
    mx = int(float(_cfg("REEL_LOGO_MARGEN_X", str(LOGO_MX))))
    my = int(float(_cfg("REEL_LOGO_MARGEN_Y", str(LOGO_MY))))
    ancho = int(float(_cfg("REEL_LOGO_ANCHO", str(LOGO_ANCHO))))
    x = mx if _logo_a_la_derecha() else mx + ancho + 22
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


_FILTROS: set | None = None


def _filtros_disponibles() -> set:
    """Los filtros que trae ESTE ffmpeg. Se pregunta una sola vez por corrida."""
    global _FILTROS
    if _FILTROS is None:
        try:
            r = subprocess.run([_ffmpeg(), "-hide_banner", "-filters"],
                               capture_output=True, text=True, errors="replace", timeout=60)
            # Cada línea útil es «  T.. nombre  entradas->salidas  descripción»
            _FILTROS = {l.split()[1] for l in (r.stdout or "").splitlines()
                        if len(l.split()) > 2 and l[:1] in " TSC."}
        except Exception as e:                                   # noqa: BLE001
            logger.warning(f"No pude listar los filtros de ffmpeg ({e}); asumo que están todos.")
            _FILTROS = set()
    return _FILTROS


def tiene_filtro(nombre: str) -> bool:
    """¿Este ffmpeg trae el filtro? Si no se pudo averiguar, se asume que sí (y si no
    estaba, lo agarra la degradación por escalones)."""
    filtros = _filtros_disponibles()
    return (nombre in filtros) if filtros else True


def _hay_drawtext() -> bool:
    """`drawtext` necesita que ffmpeg esté compilado con libfreetype, y NO todas las
    builds lo traen. El binario que `imageio-ffmpeg` instala en el Linux de la nube no
    lo tiene, aunque el de Windows sí — por eso esto anduvo meses en la PC y fallaba
    allá (2026-09-17: reels publicados sin isologo ni placa).

    Sin `drawtext` el reel se arma igual: el isologo y la placa se dibujan con
    `overlay`, que está en todas las builds. Lo único que se pierde es el TEXTO de la
    marca, que es lo menos importante de los tres."""
    if not tiene_filtro("drawtext"):
        logger.warning("Este ffmpeg NO trae «drawtext» (le falta libfreetype): el reel "
                       "va SIN el texto de marca. El isologo y la placa sí van.")
        return False
    return True


def _norm(idx: int, fps: int) -> str:
    # Escala/encuadra cada imagen a 1080x1920 exactas y fija sar/fps para xfade.
    return (f"[{idx}:v]scale=1080:1920:force_original_aspect_ratio=decrease,"
            f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2:white,setsar=1,fps={fps}[s{idx}]")


# Frases con las que ffmpeg nombra lo que salió mal. Las suyas van al PRINCIPIO del
# stderr y el final es el filtergraph entero —2000 caracteres o más—, así que recortar
# por el final, como se hacía antes, dejaba afuera justo el motivo. Pasó el 2026-09-17:
# un reel salió sin logo ni placa y el log solo mostraba «Filter not found» sin decir
# CUÁL filtro.
_MOTIVOS_FFMPEG = (
    "No such filter", "Error applying", "Error initializing", "Error opening",
    "Cannot load", "Unable to", "Invalid", "not found", "No such file",
    "Conversion failed", "Error while", "Impossible to convert",
)


def _motivo_ffmpeg(stderr: str) -> str:
    """Las líneas del stderr que explican el fallo, sin el filtergraph al lado."""
    lineas = []
    for l in (stderr or "").splitlines():
        l = l.strip()
        if not l or len(l) > 300:      # una línea larguísima ES el filtergraph
            continue
        if any(k in l for k in _MOTIVOS_FFMPEG):
            lineas.append(l)
    # Sin repetidos y en orden
    vistas, limpias = set(), []
    for l in lineas:
        if l not in vistas:
            vistas.add(l)
            limpias.append(l)
    return "\n".join(limpias[:8])


# Tope de tiempo para UNA pasada de ffmpeg. Un reel largo en la nube tarda minutos, así
# que el tope es generoso; lo que corta es el caso patológico. Se regula con `REEL_TIMEOUT`.
#
# ⚠️ Por qué existe: con `-loop 1` sobre una imagen ILEGIBLE (una placa corrupta, por
# ejemplo), ffmpeg no falla — se queda en un bucle escupiendo errores para siempre. Y
# `subprocess.run(capture_output=True)` va acumulando esa salida en memoria hasta que el
# proceso muere por MemoryError. Sin reloj, eso cuelga la corrida hasta el timeout del
# workflow (horas) y no publica nada más ese día.
def _timeout_ffmpeg() -> float | None:
    try:
        seg = float(_cfg("REEL_TIMEOUT", "900"))
    except ValueError:
        seg = 900.0
    return seg if seg > 0 else None


def _run_ffmpeg(cmd: list, paso: str) -> None:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, errors="replace",
                           timeout=_timeout_ffmpeg())
    except subprocess.TimeoutExpired:
        logger.error(f"ffmpeg se colgó ({paso}): pasó el tope de "
                     f"{_timeout_ffmpeg():.0f}s y lo corté.")
        raise RuntimeError(f"ffmpeg error: {paso} — se colgó y lo corté") from None
    except MemoryError:
        # ffmpeg en bucle escribiendo errores: la salida no entra en memoria.
        logger.error(f"ffmpeg ({paso}) escribió más errores de los que entran en memoria; "
                     f"casi seguro es un archivo de entrada ilegible.")
        raise RuntimeError(f"ffmpeg error: {paso} — entrada ilegible") from None
    if r.returncode != 0:
        err = r.stderr or ""
        motivo = _motivo_ffmpeg(err)
        logger.error(
            f"ffmpeg falló ({paso}).\n"
            + (f"  MOTIVO:\n    " + motivo.replace("\n", "\n    ") + "\n" if motivo else
               "  (ffmpeg no dio un motivo reconocible)\n")
            + "  ÚLTIMAS LÍNEAS:\n    " + err[-700:].replace("\n", "\n    ")
        )
        corto = motivo.splitlines()[0] if motivo else ""
        raise RuntimeError(f"ffmpeg error: {paso}" + (f" — {corto}" if corto else ""))


def _tiene_audio(src) -> bool:
    """True si el video tiene al menos una pista de audio (mira el `ffmpeg -i`)."""
    try:
        r = subprocess.run([_ffmpeg(), "-i", str(src)], capture_output=True, text=True, errors="replace")
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
    vf = (f"{_sin_giro()},scale={w}:{h}:force_original_aspect_ratio=decrease,"
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


def _bandas_on() -> bool:
    """¿El reel lleva titular arriba y resumen abajo? Sí por default (pedido 2026-09-18).
    `REEL_BANDAS=0` lo apaga y la imagen vuelve a ocupar el cuadro entero."""
    return str(_cfg("REEL_BANDAS", "1")).strip().lower() not in ("0", "no", "false", "off")


# Una GRÁFICA (afiche, flyer, placa de Canva, captura de pantalla) tiene RELLENOS SÓLIDOS:
# corridas largas de píxeles exactamente del mismo color. Una foto de celular no, ni siquiera
# en un cielo liso: siempre tiene grano. Eso es lo que separa una cosa de la otra, y de paso
# aguanta el viaje por ffmpeg (medido: los valores no se mueven al pasar por el clip h264).
# Se mide con DOS varas, porque un afiche no siempre es una placa lisa: puede ser una FOTO
# a sangre con el texto encima (un jugador con su nombre en letras grandes). Ahí el relleno
# sólido es solo el de las letras, o sea poquito, y lo que delata la gráfica son los bordes
# DUROS del texto, que una foto no tiene.
GRAFICA_IGUALES = 16            # relleno de placa: 16 píxeles seguidos iguales
GRAFICA_TRAZO = 6               # relleno de LETRA: el ancho de un trazo tipográfico
GRAFICA_PLANO_SEGURO = 0.35     # tan plana que no hace falta mirar nada más
GRAFICA_RELLENO = 0.18          # mucho relleno de letra: ya es una gráfica aunque no se
                                # detecte el texto (placas con tipografía fina)
GRAFICA_PLANO = 0.02            # algo de relleno…
GRAFICA_BORDES = 0.010          # …y además bordes duros, o sea TEXTO
GRAFICA_FILAS = 240             # filas que se miran, repartidas por toda la imagen


def _corrida(tramo, desde: int, hasta: int):
    """Estira la máscara de «píxeles iguales al de al lado» hasta corridas de `hasta`.

    Va doblando: «este y el siguiente», después «…y los dos de más allá», etc. Devuelve la
    máscara nueva y hasta dónde llegó, para poder seguir estirándola sin empezar de cero."""
    while desde < hasta:
        n = min(desde, hasta - desde)
        tramo = tramo[:, :-n] & tramo[:, n:]
        desde += n
    return tramo, desde


def _es_grafica(src: Path, work_dir: Path) -> bool:
    """¿El material es un AFICHE/placa/captura en vez de una foto?

    Un afiche NO se puede recortar: el texto es la noticia. Una foto sí, porque lo que se
    va son bordes (pedido del usuario 2026-09-22).

    Se miran DOS cuadros y tienen que dar gráfica LOS DOS, así un solo fotograma raro de un
    video (un fundido a negro, una placa de TV) no manda a todo el material al camino
    equivocado. Nunca lanza: ante la duda, foto.

    Calibrado contra material real y etiquetado a mano: 40 publicidades del diario y 62
    fotos de notas publicadas, de las cuales 7 eran afiches colados. Detecta **38 de 40**
    publicidades y **6 de los 7** afiches, y marca mal **5 de las 55 fotos** —de esas 5,
    dos son casos de borde (un banner de la policía, que es una gráfica fotografiada, y un
    recorte de cielo liso sin nada adentro).

    ⚠️ Lo que se le escapa es el afiche que es una FOTO a sangre con el texto sobre un
    fondo con degradé: ahí no hay relleno sólido NI bordes duros que medir. Para bajar más
    el corte están `REEL_GRAFICA_RELLENO` y `REEL_GRAFICA_BORDES`, pero cada escalón se
    lleva puestas fotos con paredes lisas, que pasarían a salir enteras en vez de a sangre."""
    try:
        import numpy as np
        from PIL import Image
    except Exception:                                   # noqa: BLE001
        return False                                    # sin numpy: todo es foto (como antes)
    src, work_dir = Path(src), Path(work_dir)
    dur = duration_seconds(src) or 0.0
    momentos = [dur * f for f in (0.35, 0.65)] if dur > 1 else [0.0]
    veredictos = []
    for i, seg in enumerate(momentos):
        tmp = work_dir / f"_graf_{i}_{src.stem[:20]}.jpg"
        try:
            if not _extraer_frame(src, seg, tmp, etiqueta="¿afiche?"):
                continue
            a = np.asarray(Image.open(tmp).convert("RGB"), dtype=np.uint8)
            if a.shape[1] < GRAFICA_IGUALES * 2:
                continue
            a = a[::max(1, a.shape[0] // GRAFICA_FILAS)]
            # Primero las corridas cortas (un trazo de letra) y desde ahí, sin recalcular,
            # las largas (el relleno de una placa).
            tramo, largo = _corrida(np.all(a[:, 1:] == a[:, :-1], axis=2), 1,
                                    GRAFICA_TRAZO - 1)
            trazo = float(tramo.mean())
            tramo, largo = _corrida(tramo, largo, GRAFICA_IGUALES - 1)
            plano = float(tramo.mean())
            gris = a.astype(np.int16).mean(axis=2)
            bordes = float((np.abs(np.diff(gris, axis=1)) >= 60).mean())
            veredictos.append((plano >= GRAFICA_PLANO_SEGURO
                               or trazo >= _num("REEL_GRAFICA_RELLENO", GRAFICA_RELLENO)
                               or (trazo >= GRAFICA_PLANO
                                   and bordes >= _num("REEL_GRAFICA_BORDES", GRAFICA_BORDES)),
                               trazo, bordes))
        except Exception:                               # noqa: BLE001
            continue
        finally:
            try:
                tmp.unlink()
            except Exception:                           # noqa: BLE001
                pass
    if not veredictos or not all(v[0] for v in veredictos):
        return False
    p, b = veredictos[0][1], veredictos[0][2]
    logger.info(f"El material es una GRÁFICA (relleno macizo {p:.0%}, bordes duros {b:.1%}): "
                f"va entero, porque recortarlo se comería el texto.")
    return True


# Cuánto de una foto estamos dispuestos a tirar con tal de que llene el hueco. No es un solo
# número, porque según la forma no se pierde lo mismo:
#   · APAISADA: el recorte se lleva los COSTADOS, que es donde están la gente, los carteles y
#     las patentes. Se aguanta poco.
#   · VERTICAL o CUADRADA: el recorte se lleva ARRIBA y ABAJO —cielo, techo, piso, asfalto— y
#     encima el encuadre GARANTIZA que las caras queden dentro (`_encuadre_fullbleed`), así
#     que la noticia sobrevive. Va SIEMPRE a sangre (tope 1,00), que es el pedido del usuario
#     del 2026-09-22: una foto vertical tiene que llenar el cuadro, no quedar chiquita en el
#     medio. Lo que no se recorta nunca es una GRÁFICA: ver `_es_grafica`.
PLACA_RECORTE_MAX = 0.25
PLACA_RECORTE_MAX_VERTICAL = 1.00


def _llena_el_cuadro(w: int, h: int, hueco: int = 0, *, grafica: bool = False) -> bool:
    """¿Esta imagen LLENA el hueco (recortando lo que sobra) o va ENTERA?

    Tres reglas, en este orden:
      1. una GRÁFICA va siempre ENTERA —un afiche recortado pierde justo el texto, que es
         la noticia (pedido del usuario 2026-09-22);
      2. una foto VERTICAL o CUADRADA va siempre A SANGRE: lo que se recorta es cielo y
         piso, y el encuadre garantiza que las caras queden dentro;
      3. una foto APAISADA llena solo si el recorte se lleva menos de `PLACA_RECORTE_MAX`,
         porque ahí lo que se va son los costados: gente, carteles, patentes.

    `REEL_RECORTE_MAX` y `REEL_RECORTE_MAX_VERTICAL` mueven cada corte; en `0` no se
    recorta NUNCA."""
    if w <= 0 or h <= 0 or hueco <= 0 or grafica:
        return False
    vertical = w <= h
    clave = "REEL_RECORTE_MAX_VERTICAL" if vertical else "REEL_RECORTE_MAX"
    base = PLACA_RECORTE_MAX_VERTICAL if vertical else PLACA_RECORTE_MAX
    try:
        tope = float(_cfg(clave, str(base)))
    except ValueError:
        tope = base
    escala = max(1080 / w, hueco / h)             # cuánto hay que agrandarla para llenar
    visible = (1080 * hueco) / (w * escala * h * escala)
    return (1 - visible) <= tope


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


def _entre(ideal: float, minimo: float, maximo: float, piso: float, techo: float) -> int:
    """El valor más cercano a `ideal` que cumpla DOS condiciones a la vez: quedar en
    [minimo, maximo] —lo que hace falta para no cortar al sujeto— y no salirse de la
    imagen, [piso, techo]. Si las dos no se pueden, manda no salirse de la imagen."""
    lo, hi = max(minimo, piso), min(maximo, techo)
    if lo > hi:                       # no se tocan: la imagen manda
        return int(round(max(piso, min(ideal, techo))))
    return int(round(max(lo, min(ideal, hi))))


def _ventana_caras(caras, cont_w: int, cont_h: int, W: int, H: int, *,
                   fundido: int = 0) -> tuple:
    """(nw, nh, x, y): a cuánto escalar el material para LLENAR `W`x`H` y desde dónde
    recortarlo, con las `caras` (x, y, w, h en píxeles del material) ENTERAS adentro.

    El recorte NO se limita a apuntar a las caras: se GARANTIZA que entren, con aire arriba
    de la cabeza y un poco de cuello abajo (pedido del usuario 2026-09-22). Dentro de ese
    margen se elige el encuadre más parecido al ideal: centrado en el sujeto y con la cara en
    el tercio de arriba. Si las caras están tan repartidas que no entran todas, se suelta la
    más chica (la del fondo) antes que cortar a la principal.

    `fundido` son los px de arriba que se DISUELVEN en el fondo (estilo placa): el ideal
    empuja la cara por debajo de esa franja, para que no quede lavada (2026-09-25). Es un
    ideal, no una regla: si no hay lugar, la cara entera sigue mandando.

    Sin caras (paisaje/objeto): recorte centrado con leve sesgo hacia arriba (mismo criterio
    que `story_image._encuadrar`)."""
    escala = max(W / max(1, cont_w), H / max(1, cont_h))
    nw = max(W, int(round(cont_w * escala)))
    nh = max(H, int(round(cont_h * escala)))
    x = (nw - W) // 2
    y = max(0, min(int((nh - H) * 0.30), nh - H))
    if not caras:
        return nw, nh, x, y
    # Las caras, ya en píxeles del material escalado, la más grande primero.
    cajas = sorted(((c[0] * escala, c[1] * escala, c[2] * escala, c[3] * escala)
                    for c in caras), key=lambda c: -c[2] * c[3])
    # La caja que devuelve el detector es la CARA pelada: no trae ni la frente ni el pelo,
    # así que hay que pedir aire arriba o el recorte corta la cabeza.
    aire, cuello = cajas[0][3] * 0.7, cajas[0][3] * 0.3
    while len(cajas) > 1:
        ancho = max(c[0] + c[2] for c in cajas) - min(c[0] for c in cajas)
        altura = (max(c[1] + c[3] for c in cajas) + cuello
                  - min(c[1] for c in cajas) + aire)
        if ancho <= W and altura <= H:
            break
        fuera = cajas.pop()
        logger.info(f"Encuadre: las {len(cajas) + 1} caras no entran juntas; suelto "
                    f"la más chica ({int(fuera[2])}x{int(fuera[3])} px) y sigo.")
    tot = sum(c[2] * c[3] for c in cajas)
    cx = sum((c[0] + c[2] / 2) * c[2] * c[3] for c in cajas) / tot
    arriba = min(c[1] for c in cajas)
    # Lo que la ventana TIENE que cubrir…
    izq, der = min(c[0] for c in cajas), max(c[0] + c[2] for c in cajas)
    aba = max(c[1] + c[3] for c in cajas) + cuello
    # …y dentro de eso, lo más parecido al encuadre lindo: centrado en el sujeto, con la cara
    # en el tercio de arriba y, si la imagen se funde arriba, por debajo del desvanecido.
    x = _entre(cx - W / 2, der - W, izq, 0, nw - W)
    y = _entre(arriba - max(H * 0.14, fundido * 0.75 + aire), aba - H, arriba - aire,
               0, nh - H)
    logger.info(f"Encuadre: {len(cajas)} cara(s) → recorte x={x} y={y}, con las caras "
                f"enteras y aire arriba de la cabeza.")
    return nw, nh, x, y


def _encuadre_fullbleed(src: Path, cont_w: int, cont_h: int, recorte, work_dir: Path,
                        *, alto: int = 1920, fundido: int = 0):
    """Devuelve (nw, nh, x, y): a cuánto escalar el VIDEO para LLENAR 1080x`alto` y desde
    dónde recortarlo, ENCUADRADO EN EL SUJETO.

    Busca caras en 3 fotogramas (reusa el detector de las placas) y se queda con el
    fotograma más representativo (el de mayor superficie de caras); el recorte lo decide
    `_ventana_caras`. Ante cualquier error: recorte centrado."""
    W, H = 1080, alto          # `alto` < 1920 cuando el reel lleva el texto arriba
    mejor: list = []
    try:
        from PIL import Image
        from story_image import _caras_principales, _detect_faces  # detector de las placas
        dur = duration_seconds(src) or 0.0
        momentos = [dur * f for f in (0.25, 0.5, 0.75)] if dur > 1 else [0.0]
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
        if not mejor:
            logger.info("Encuadre full bleed: sin caras (paisaje/objeto) → recorte centrado.")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"No pude calcular el encuadre del sujeto ({e}); recorte centrado.")
        mejor = []
    return _ventana_caras(mejor, cont_w, cont_h, W, H, fundido=fundido)


def _calidad() -> list:
    """Control de tasa del reel. Sin esto, libx264 deja un minuto de 1080x1920 en más de
    20 MB, que tarda una eternidad en abrirse desde el celular para revisarlo (y es lo
    mismo que después sube a las redes, donde igual lo vuelven a comprimir).

    CRF 26 con techo de 3,5 Mb/s baja el archivo a menos de la mitad sin diferencia
    visible en un teléfono. Se regula con `REEL_CRF` y `REEL_MAXRATE` (`0` los apaga)."""
    crf = str(_cfg("REEL_CRF", "26")).strip()
    if crf in ("0", "no", "off", ""):
        return []
    maxrate = str(_cfg("REEL_MAXRATE", "3500k")).strip()
    args = ["-crf", crf]
    if maxrate not in ("0", "no", "off", ""):
        args += ["-maxrate", maxrate, "-bufsize", "7000k"]
    return args


def _armar_reel(src: Path, salida: Path, *, audio: bool, max_seconds: float | None,
                firma: str | None, fondo: Path | None, logo_png: Path | None,
                overlay: Path | None, placa: Path | None, seg_placa: float,
                recorte: tuple[int, int, int, int] | None = None,
                encuadre: tuple[int, int, int, int] | None = None,
                marca_texto: bool = False,
                texto_placa: tuple | None = None,
                color_fondo: str = "",
                fondo_placa: Path | None = None,
                capa_texto: Path | None = None) -> None:
    """Arma el reel vertical en UNA sola pasada de ffmpeg (un único re-encode, para
    no pagar el doble de CPU en la nube): fondo borroso + video + logo + firma, y
    al final la placa de cierre concatenada. Si `recorte` (w,h,x,y) viene dado, primero
    le saca las barras negras al video para que el marco naranja tape ese negro.

    `fondo_placa` es el PNG de humo (`fondo_placa_png`) que va detrás de la placa; sin él,
    color liso. `capa_texto` es un PNG de 1080x1920 que se pega ARRIBA de todo (después del
    isologo): lo usan los reels de FOTOS, que llegan con el fondo y la imagen ya compuestos
    cuadro por cuadro y solo les falta el texto (ver `foto_a_reel`)."""
    ff = _ffmpeg()
    fps = 30
    con_audio = audio and has_audio(src)
    # Si el video trae barras negras horneadas, se las sacamos ANTES de todo, así el
    # contenido real es lo que se escala y el fondo naranja ocupa donde estaba el negro.
    # Primer paso SIEMPRE: sacarle el cartel de giro al video (ver `_sin_giro`), porque si
    # no, el reel entero sale acostado en Instagram y Facebook.
    cadena = _sin_giro()
    if recorte:
        cadena += f",crop={recorte[0]}:{recorte[1]}:{recorte[2]}:{recorte[3]}"
    pre = f"[0:v]{cadena}[src0];"
    v0 = "[src0]"
    # ESTILO PLACA: el texto va arriba y la imagen FULL BLEED abajo, fundida con el fondo
    # por su borde de arriba. Sin placa, la imagen ocupa el cuadro entero como siempre.
    ym = texto_placa[1] if texto_placa else 0
    mh = texto_placa[3] if texto_placa else 1920        # alto REAL de la imagen
    # Ancho REAL. Es 1080 salvo cuando la imagen va ENTERA y es más alta que ancha: ahí entra
    # completa y más angosta, y a los costados queda el color del fondo.
    mw = (texto_placa[5] if (texto_placa and len(texto_placa) > 5) else 1080) or 1080
    mascara = texto_placa[2] if texto_placa else None
    if texto_placa:
        # Fondo: un GRIS OSCURO SÓLIDO detrás de TODO el cuadro (pedido del usuario
        # 2026-09-18). Antes iba la propia imagen ampliada y desenfocada, pero el video
        # borroso repetido arriba ensuciaba el texto y se notaba la repetición. Un plano liso
        # es más fino y hace que el naranja y el blanco salten.
        #
        # `drawbox ... t=fill` pinta el cuadro ENTERO respetando la duración del video, así que
        # no hace falta sumar un input ni un generador `color` infinito. Y de paso nos ahorramos
        # el `boxblur`, que era con diferencia el filtro más caro de la cadena.
        if tiene_filtro("drawbox"):
            relleno = (f"scale=1080:1920,drawbox=x=0:y=0:w=1080:h=1920:"
                       f"color={_color_fondo(color_fondo)}@1:t=fill")
        else:
            # Misma historia que `drawtext` (2026-09-17): no todas las builds traen todo, y la
            # de Linux de la nube es más pelada que la de Windows. `drawbox` no depende de
            # ninguna librería externa, así que esto no debería pasar nunca — pero si pasa, el
            # reel sale con el fondo borroso de antes en vez de no salir.
            logger.warning("Este ffmpeg NO trae «drawbox»: el fondo de la placa va borroso.")
            relleno = ("scale=1080:1920:force_original_aspect_ratio=increase,"
                       "crop=1080:1920,boxblur=luma_radius=40:luma_power=1")
        vf = f"{pre}{v0}split=2[bg][fg];[bg]{relleno},setsar=1[bgb]"
        if fondo_placa:
            # Carbón con humo (2026-09-25). Es una imagen FIJA: `overlay` repite su único
            # cuadro todo lo que dure el video (eof_action=repeat es el default), así que no
            # hace falta `-loop` ni un generador infinito. Tapa entero al relleno liso, que
            # queda solo para darle al fondo la duración y el ritmo del video.
            vf = vf[:-len("[bgb]")] + "[bgl];[FONDOPL:v]format=rgb24[fdp];[bgl][fdp]overlay=0:0[bgb]"
        if encuadre:                        # llena el hueco: amplía y recorta a medida
            nw, nh, cx, cy = encuadre
            vf += f";[fg]scale={nw}:{nh},setsar=1,crop={mw}:{mh}:{cx}:{cy},format=rgba[fgc]"
        else:                               # ENTERA: a su proporción, sin deformarse
            vf += f";[fg]scale={mw}:{mh},setsar=1,format=rgba[fgc]"

        etiqueta_img = "[fgc]"
        if mascara:
            # `alphamerge` toma el brillo de la máscara como canal alfa: negro arriba =
            # transparente, y de ahí a blanco. El borde de la foto deja de ser una línea.
            vf += f";[MASK:v]format=gray,scale={mw}:{mh}[mk];[fgc][mk]alphamerge[fga]"
            etiqueta_img = "[fga]"
        # Centrada a lo ancho: cuando la imagen va ENTERA y es más angosta que el cuadro
        # (una foto de celular 9:16, por ejemplo), a los costados queda el color del fondo.
        vf += f";[bgb]{etiqueta_img}overlay={(1080 - mw) // 2}:{ym}[v]"
    elif encuadre:
        # FULL BLEED clásico: el video LLENA el cuadro 9:16, sin franjas ni fondo borroso.
        nw, nh, cx, cy = encuadre
        vf = f"{pre}{v0}scale={nw}:{nh},setsar=1,crop=1080:1920:{cx}:{cy}[v]"
    else:
        # Fondo: el propio video escalado a llenar + recortado + desenfocado.
        # Primer plano: el video escalado a entrar dentro del cuadro. Se superponen.
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
    if texto_placa and mascara:
        inputs += ["-i", str(mascara)]
        vf = vf.replace("[MASK:v]", f"[{n_in}:v]")
        n_in += 1
    if texto_placa and fondo_placa:
        inputs += ["-i", str(fondo_placa)]
        vf = vf.replace("[FONDOPL:v]", f"[{n_in}:v]")
        n_in += 1
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
        # Marca de agua: el isotipo arriba, debajo de la barra de la app.
        idx = n_in
        inputs += ["-i", str(logo_png)]
        n_in += 1
        ancho = int(float(_cfg("REEL_LOGO_ANCHO", str(LOGO_ANCHO))))
        mx = int(float(_cfg("REEL_LOGO_MARGEN_X", str(LOGO_MX))))
        my = int(float(_cfg("REEL_LOGO_MARGEN_Y", str(LOGO_MY))))
        op = float(_cfg("REEL_LOGO_OPACIDAD", "0.92"))
        # `W-w` = ancho del cuadro menos el del logo. Se deja que lo calcule ffmpeg en vez
        # de hacer la cuenta acá: el `scale={ancho}:-1` define el alto —y por lo tanto el
        # ancho final— recién al ejecutar, según la proporción del PNG.
        lx = f"W-w-{mx}" if _logo_a_la_derecha() else str(mx)
        vf += (f";[{idx}:v]scale={ancho}:-1,format=rgba,colorchannelmixer=aa={op}[lg];"
               f"{out_label}[lg]overlay={lx}:{my}[vl]")
        out_label = "[vl]"
    if marca_texto and not texto_placa:
        # Nombre de los dos medios + usuario de las redes, del lado libre del isologo.
        # Va como IMAGEN superpuesta, no con `drawtext`: ver `marca_texto_png`.
        marca_png = marca_texto_png(salida.parent / f"marca_{salida.stem}.png")
        if marca_png:
            idx = n_in
            inputs += ["-i", str(marca_png)]
            n_in += 1
            vf += (f";[{idx}:v]scale=1080:1920,format=rgba[mk];"
                   f"{out_label}[mk]overlay=0:0[vmk]")
            out_label = "[vmk]"
    if texto_placa:
        # Marca + volanta + titular + bajada, ya dibujados en un PNG transparente por
        # `placa_texto_png`. Van DESPUÉS del logo para que nada los tape.
        idx = n_in
        inputs += ["-i", str(texto_placa[0])]
        n_in += 1
        vf += (f";[{idx}:v]scale=1080:1920,format=rgba[pl];"
               f"{out_label}[pl]overlay=0:0[vpl]")
        out_label = "[vpl]"
    if capa_texto:
        # Reel de FOTOS: fondo e imagen ya vienen compuestos en cada cuadro; acá solo se le
        # pega el texto de arriba, una vez, para que quede QUIETO mientras las fotos pasan.
        idx = n_in
        inputs += ["-i", str(capa_texto)]
        n_in += 1
        vf += (f";[{idx}:v]scale=1080:1920,format=rgba[ct];"
               f"{out_label}[ct]overlay=0:0[vct]")
        out_label = "[vct]"
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
        cmd += ["-c:v", "libx264", "-preset", "veryfast", *_calidad(),
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(salida)]
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
           "-c:v", "libx264", "-preset", "veryfast", *_calidad(),
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(salida)]
    _run_ffmpeg(cmd, "reel vertical + placa")


# Filtros de ffmpeg de los que depende el reel. Si falta alguno, el reel sale peor (o no
# sale). Se listan acá para poder preguntarle a la nube qué tiene ANTES de que se rompa una
# publicación de verdad: el ffmpeg de Linux NO es el mismo binario que el de Windows y ya
# nos mordió una vez (drawtext, 2026-09-17).
FILTROS_QUE_USA = {
    "overlay": "pegar el logo, la placa de texto y la imagen",
    "scale": "llevar todo a 1080x1920",
    "crop": "el encuadre full bleed",
    "split": "separar fondo y primer plano",
    "concat": "pegarle la placa de cierre al final",
    "fade": "la entrada de la placa de cierre",
    "alphamerge": "el desvanecido de la foto contra el fondo",
    "drawbox": "el gris sólido de atrás del texto",
    "sidedata": "sacarle el giro a los videos de celular",
    "boxblur": "el fondo borroso (solo en los reels sin placa)",
    "trim": "recortar el video sin recortar la placa",
    "anullsrc": "el silencio de la placa de cierre",
}


def autochequeo() -> bool:
    """Prueba de humo del armador de reels: ffmpeg, filtros, tipografías y un reel REAL.

    Está para correrlo en la nube (`main.py --chequeo-reel`) y enterarnos de un problema
    ANTES de que se caiga una publicación. Devuelve False si algo está mal."""
    import tempfile
    ok = True
    print("\n=== ffmpeg ===")
    try:
        exe = _ffmpeg()
        r = subprocess.run([exe, "-hide_banner", "-version"], capture_output=True, text=True, errors="replace")
        print(f"  binario: {exe}")
        print(f"  {(r.stdout or '').splitlines()[0]}")
    except Exception as e:                                       # noqa: BLE001
        print(f"  ROTO: no pude correr ffmpeg: {e}")
        return False

    print("\n=== filtros que necesita el reel ===")
    disponibles = _filtros_disponibles()
    if not disponibles:
        print("  (no pude listar los filtros; asumo que están todos)")
    else:
        for nombre, para_que in sorted(FILTROS_QUE_USA.items()):
            hay = nombre in disponibles
            print(f"  {'OK  ' if hay else 'FALTA'} {nombre:12} — {para_que}")
            ok = ok and hay
        hay_dt = "drawtext" in disponibles
        print(f"  {'OK  ' if hay_dt else 'no  '} drawtext     — no hace falta: el texto se "
              f"dibuja con PIL{'' if hay_dt else ' (es lo esperado en Linux)'}")

    print("\n=== tipografías ===")
    for clave, ruta in (("titular", _fuente_banda("REEL_FUENTE_TITULAR", FUENTE_TITULAR)),
                        ("resumen", _fuente_banda("REEL_FUENTE_RESUMEN", FUENTE_RESUMEN)),
                        ("marca", _fuente_marca())):
        if not ruta:
            print(f"  FALTA {clave}: no hay ninguna tipografía usable")
            ok = False
            continue
        try:
            fino = _ancho_texto("Chivilcoy ñÁÉÍ", str(ruta), 50, "400")
            grueso = _ancho_texto("Chivilcoy ñÁÉÍ", str(ruta), 50, "700")
            peso = "variable" if fino != grueso else "un solo peso"
            print(f"  OK   {clave:8} {Path(ruta).name} ({peso})")
        except Exception as e:                                   # noqa: BLE001
            print(f"  ROTO {clave}: {Path(ruta).name} no se puede usar: {e}")
            ok = False

    print("\n=== maqueta del texto (zonas que tapan IG y TikTok, isologo) ===")
    caja_logo = _logo_caja()
    if caja_logo and caja_logo[3] > SEGURO_DERECHA_DESDE and caja_logo[2] > 1080 - SEGURO_DERECHA:
        print(f"  MAL  el isologo baja hasta y={caja_logo[3]}: ahí IG y TikTok ponen su "
              f"columna de botones y lo taparían. Subilo con REEL_LOGO_MARGEN_Y.")
        ok = False
    print("  isologo: "
          + ("x %d..%d  y %d..%d" % (caja_logo[0], caja_logo[2], caja_logo[1], caja_logo[3])
             if caja_logo else "(el reel va sin logo)"))
    f_tit = _fuente_banda("REEL_FUENTE_TITULAR", FUENTE_TITULAR)
    f_res = _fuente_banda("REEL_FUENTE_RESUMEN", FUENTE_RESUMEN)
    p_tit = _cfg("REEL_PESO_TITULAR", PESO_TITULAR)
    p_res = _cfg("REEL_PESO_RESUMEN", PESO_RESUMEN)
    MAQUETAS = [
        ("texto corto", "Deportes", "Racing de Chivilcoy ascendió",
         "El ascenso se definió el domingo."),
        ("texto largo", "Encuentro regional de instituciones de bien público",
         "El intendente Guillermo Britos y el gobernador encabezaron el acto central por el "
         "aniversario número 171 de la ciudad",
         "Participaron autoridades provinciales, concejales y vecinos. Hubo desfile y "
         "espectáculos musicales hasta la medianoche."),
        ("TODO EN MAYÚSCULAS", "URGENTE",
         "EL MUNICIPIO ANUNCIÓ OBRAS PARA EL BARRIO NORTE",
         "LA INVERSIÓN SUPERA LOS 200 MILLONES DE PESOS."),
        # El titular más largo que el desgrabador puede llegar a mandar: 110 caracteres
        # (`transcriber.TITULAR_MAX`). Tiene que entrar ENTERO, sin un «…» al final: de eso
        # depende que la regla de deducir el titular sirva para algo.
        ("titular al límite (110)", "Institucionales",
         "Extraordinariamente, la superintendencia interjurisdiccional desaconsejó "
         "responsabilizar a los transportistas",
         "La resolución se publicó el viernes."),
    ]
    for nombre, vol, tit, res in MAQUETAS:
        fallas = []
        caja = placa_layout(vol, tit, res, f_tit, f_res, p_tit, p_res,
                            pie="El intendente recorrió la obra y adelantó que estará "
                                "terminada antes de fin de año.",
                            pie_zona=(1300, 1920 - BANDA_SEGURO))
        for texto, cuerpo, y, fuente, peso, color, centrado in caja["bloques"]:
            # La caja de TINTA de verdad (no la del renglón, que incluye el ascendente vacío
            # por encima de las mayúsculas): lo que tapan las apps es lo que se ve.
            x = 540 if centrado else PLACA_MX
            bx0, by0, bx1, by1 = _tipo(fuente, cuerpo, peso).getbbox(
                texto, anchor="ma" if centrado else "la")
            # Con interletrado negativo el renglón dibujado es más angosto que el que mide PIL.
            bx1 += _interletra(peso)[1] * cuerpo * max(0, len(texto) - 1)
            r = (x + bx0, y + by0, x + bx1, y + by1)
            if r[2] > 1080 - 40:
                fallas.append(f"«{texto[:22]}» se sale por la derecha (x={r[2]})")
            if r[1] < SEGURO_ARRIBA:
                fallas.append(f"«{texto[:22]}» entra en la franja de arriba (y={r[1]})")
            if r[3] > 1920 - BANDA_SEGURO:
                fallas.append(f"«{texto[:22]}» entra en la franja de abajo (y={r[3]})")
            if caja_logo and not (r[2] <= caja_logo[0] or r[0] >= caja_logo[2]
                                  or r[3] <= caja_logo[1] or r[1] >= caja_logo[3]):
                fallas.append(f"«{texto[:22]}» SE SUPERPONE CON EL ISOLOGO")
        bajada = caja.get("bajada") or []
        if res and bajada and bajada[-1][-1] not in ".!?»":
            fallas.append(f"la bajada queda cortada: «…{bajada[-1][-24:]}»")
        if res and not bajada:
            fallas.append("la bajada no entró")
        if tit and any(b[0].endswith("…") for b in caja["bloques"] if b[5] == BLANCO):
            fallas.append("el titular queda cortado con «…»")
        if 1920 - caja["y_img"] < 700:
            fallas.append(f"a la foto le quedan solo {1920 - caja['y_img']}px")
        ok = ok and not fallas
        print(f"  {'OK  ' if not fallas else 'MAL '} {nombre}: {len(caja['bloques'])} "
              f"renglón/es, la imagen arranca en y={caja['y_img']}")
        for f in fallas:
            print(f"        → {f}")

    print("\n=== reel de prueba (el camino completo, como en una publicación) ===")
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        base, giro = tmp / "base.mp4", tmp / "giro.mp4"
        try:
            subprocess.run([exe, "-y", "-f", "lavfi", "-i",
                            "testsrc=size=1920x1080:rate=25:duration=3",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                            str(base)], capture_output=True, check=True)
            # El mismo video pero "grabado con el celular de costado".
            subprocess.run([exe, "-y", "-display_rotation", "90", "-i", str(base),
                            "-c", "copy", str(giro)], capture_output=True, check=True)
        except Exception as e:                                   # noqa: BLE001
            print(f"  ROTO: no pude armar el video de prueba: {e}")
            return False

        for etiqueta, fuente in (("video derecho", base), ("video de celular girado", giro)):
            salida = tmp / f"reel_{etiqueta.split()[1]}.mp4"
            try:
                to_vertical_reel(fuente, salida,
                                 titular="Memi Mesplet y Seba Bravo presentan «Habladurías»",
                                 resumen="La función será el domingo 27 en Casa vieja San Luis.",
                                 volanta="Ciclo de teatro independiente",
                                 cuerpo="La obra se estrenó el año pasado en el Teatro "
                                        "Español y ya recorrió varias localidades.")
                w, h = _dimensiones(salida)
                dur = duration_seconds(salida) or 0
                falta = ultimo_reel_degradado()
                bien = (w, h) == (1080, 1920) and dur > 1 and not falta
                ok = ok and bien
                detalle = f"{w}x{h}, {dur:.1f}s"
                if (w, h) != (1080, 1920):
                    detalle += "  ← TENDRÍA QUE SER 1080x1920 (¿sale acostado?)"
                if falta:
                    detalle += f"  ← se cayó a «{falta}»"
                print(f"  {'OK  ' if bien else 'MAL '} {etiqueta}: {detalle}")
            except Exception as e:                               # noqa: BLE001
                ok = False
                print(f"  ROTO {etiqueta}: {type(e).__name__}: {e}")

        # Reel de VARIAS FOTOS de formas distintas (2026-09-25): cada una se compone por
        # separado, así que es otro camino —PIL + pase de fotos + texto encima— y conviene
        # probarlo entero en la nube también.
        try:
            from PIL import Image, ImageDraw
            fotos = []
            for nombre, (w, h) in (("vertical", (900, 1600)), ("apaisada", (1920, 1080)),
                                   ("panoramica", (2400, 700))):
                im = Image.new("RGB", (w, h), (70, 110, 150))
                d = ImageDraw.Draw(im)
                for k in range(0, max(w, h), 60):       # textura: que no parezca un afiche
                    d.line((k, 0, k - h, h), fill=(90 + k % 80, 130, 170 - k % 60), width=25)
                ruta = tmp / f"foto_{nombre}.jpg"
                im.save(ruta, quality=90)
                fotos.append(ruta)
            salida = tmp / "reel_fotos.mp4"
            foto_a_reel(fotos, salida, seg=14, overlay=False,
                        titular="Un auto se incendió en la Ruta 30",
                        resumen="La conductora resultó ilesa.", volanta="Kilómetro 480",
                        cuerpo="Dos dotaciones de Bomberos Voluntarios trabajaron en la "
                               "extinción del fuego durante más de una hora.")
            w, h = _dimensiones(salida)
            dur = duration_seconds(salida) or 0
            falta = ultimo_reel_degradado()
            bien = (w, h) == (1080, 1920) and dur > 8 and not falta
            ok = ok and bien
            print(f"  {'OK  ' if bien else 'MAL '} tres fotos de formas distintas: "
                  f"{w}x{h}, {dur:.1f}s" + (f"  ← se cayó a «{falta}»" if falta else ""))
        except Exception as e:                                   # noqa: BLE001
            ok = False
            print(f"  ROTO tres fotos de formas distintas: {type(e).__name__}: {e}")

    print("\n" + ("=== TODO EN ORDEN: el próximo reel puede salir tranquilo ===" if ok else
                  "=== HAY ALGO MAL: mirá las líneas de arriba ==="))
    return ok


# Qué le faltó al último reel armado. Lo lee `transcriber` para avisarlo en el mail de
# revisión: un reel sin marca que sale en silencio es peor que uno que no sale.
_DEGRADADO: dict = {}


def ultimo_reel_degradado() -> dict:
    """`{}` si el último reel salió completo; si no, `{nivel, motivo}`."""
    return dict(_DEGRADADO)


def _probar_escalones(src: Path, salida: Path, escalones: list, *, audio: bool,
                      max_seconds: float | None, firma: str | None) -> dict:
    """Arma el reel probando los `escalones` en orden hasta que uno salga.

    Si la marca hace fallar el filtergraph, el reel igual sale: nunca se pierde una
    publicación por el fondo, el logo, el overlay o la placa. Pero se baja DE A UN ESCALÓN,
    no de golpe: antes, un problema con la placa se llevaba puesto también al isologo y el
    reel salía sin ninguna marca (2026-09-17). Devuelve los argumentos que funcionaron."""
    _DEGRADADO.clear()
    ultimo = None
    for i, (nombre, args) in enumerate(escalones):
        if callable(args):        # escalón perezoso: recién acá se arma lo que necesita
            args = args()
        try:
            _armar_reel(src, salida, audio=audio, max_seconds=max_seconds, firma=firma, **args)
        except Exception as e:                                   # noqa: BLE001
            ultimo = e
            if i == len(escalones) - 1:
                raise
            logger.error(f"El reel {nombre} falló: {e}. Pruebo bajando un escalón.")
            continue
        if i:
            _DEGRADADO.update(nivel=nombre, motivo=str(ultimo))
            logger.error(f"⚠️ El reel salió {nombre.upper()}. Motivo: {ultimo}")
        return args
    return {}


# Cuánto aire se deja abajo cuando una imagen APAISADA va entera y sin pie: se centra entre
# el texto y esta altura, en vez de quedar pegada al texto con un pozo vacío abajo.
PLACA_PISO_ENTERA = 180
# Margen lateral de una GRÁFICA (afiche/flyer): va entera, con borde duro y una sombra suave,
# así que necesita aire a los costados para que la sombra se vea.
PLACA_GRAFICA_MX = 48


def _y_entera(y_img: int, alto: int, con_pie: bool) -> int:
    """Dónde va una imagen que NO llena el hueco (apaisada o gráfica).

    Con pie, pegada al texto y la frase debajo, como un epígrafe. Sin pie, CENTRADA entre el
    texto y `PLACA_PISO_ENTERA`: pegada arriba dejaba un pozo de fondo vacío abajo."""
    if con_pie:
        return y_img
    return y_img + max(0, (1920 - PLACA_PISO_ENTERA - y_img - alto) // 2)


def to_vertical_reel(src, salida, *, audio: bool = True, max_seconds: float | None = None,
                     es_foto: bool = False,
                     firma: str | None = None, logo: bool = True,
                     placa_final: bool = True, zocalo: str | None = None,
                     overlay: bool = True, titular: str = "", resumen: str = "",
                     volanta: str = "", cuerpo: str = "",
                     compuesto: Path | None = None) -> Path:
    """Convierte un video cualquiera a un reel vertical 1080x1920 (9:16).

    El video se escala ENTERO (sin recortar) y se centra sobre un fondo borroso de
    sí mismo (misma estética que las historias, story_image._fit_blur). Mantiene el
    audio por defecto. Si se pasa `max_seconds`, recorta el reel a esa duración
    (ej. 60 para los reels sin desgrabar). Si se pasa `firma`, estampa una banda
    inferior con ese texto (la firma de la Red de Corresponsales).

    `logo=True` estampa el isotipo del diario arriba a la DERECHA (perilla `REEL_LOGO_LADO`;
    `izquierda` lo devuelve al lugar de antes) y, del lado libre, el TEXTO de marca
    («Diario La Campaña | Radio del Centro» + «@diarioyradio»; `REEL_MARCA_TEXTO=0` lo
    apaga). El OVERLAY del diario
    (marco + caja del zócalo + barra con la web y las redes) va con el `zocalo` escrito
    adentro SOLO si `overlay=True` (default; `overlay=False` saca el marco y el texto del
    zócalo de una), y `placa_final=True` agrega al final la placa "Seguinos en redes"
    (5 s). Si el video trae BARRAS NEGRAS horneadas (apaisado dentro de un cuadro vertical,
    o directamente apaisado), se las recorta. Todo se apaga o se cambia por `.env`
    (REEL_LOGO / REEL_FONDO / REEL_OVERLAY / REEL_PLACA_FINAL / REEL_PLACA_SEG /
    REEL_RECORTE_NEGRO). Devuelve el .mp4.

    Con `volanta`/`titular`/`resumen` el reel sale en el estilo PLACA, copiado de los reels
    de referencia del usuario (2026-09-25): el texto arriba a la izquierda (volanta NARANJA,
    titular y bajada BLANCOS) sobre carbón con humo, y la imagen abajo, disuelta en el fondo
    por su borde de arriba. Según su FORMA:
      · vertical o cuadrada → A SANGRE en todo el espacio de abajo, encuadrada en las caras;
      · apaisada que perdería poco recortándola → también a sangre;
      · MUY apaisada → ENTERA a lo ancho, con el borde de abajo sombreado hacia el fondo y la
        primera oración del `cuerpo` como pie (o centrada, si no hay pie que entre).
    El texto ya lo escribió Gemini al redactar la nota: acá no se le pide nada, solo se
    dibuja. Se apaga con `REEL_BANDAS=0`.

    `es_foto` avisa que atrás de este 'video' hay una FOTO. Solo en ese caso se mira si el
    material es un AFICHE —que no se puede recortar, ver `_es_grafica`—: un video de celular
    nunca lo es, y uno nocturno muy comprimido tiene manchones planos que lo harían pasar
    por gráfica sin serlo.

    `compuesto` es el PNG de texto de un reel de FOTOS que ya viene armado cuadro por cuadro
    (fondo + foto de cada placa, ver `foto_a_reel`): acá solo se le pegan el texto, el
    isologo y la placa de cierre.
    """
    src, salida = Path(src), Path(salida)
    logo_png = _asset("REEL_LOGO", LOGO_REEL) if logo else None
    placa_cierre = _asset("REEL_PLACA_FINAL", PLACA_FINAL) if placa_final else None
    seg_placa = float(_cfg("REEL_PLACA_SEG", str(PLACA_SEG)))

    if compuesto is not None:
        # Las fotos ya vienen compuestas a 1080x1920: NADA de buscar barras negras (el fondo
        # carbón las «tendría» arriba y abajo y las recortaría) ni de volver a encuadrar.
        base = dict(fondo=None, logo_png=logo_png, overlay=None, placa=placa_cierre,
                    seg_placa=seg_placa, recorte=None, encuadre=(1080, 1920, 0, 0),
                    marca_texto=False, texto_placa=None, color_fondo="",
                    fondo_placa=None, capa_texto=compuesto)
        escalones = [("completo", base)]
        if placa_cierre:
            escalones.append(("sin la placa de cierre", {**base, "placa": None, "seg_placa": 0.0}))
        escalones.append(("sin el texto de arriba", {**base, "placa": None, "seg_placa": 0.0,
                                                     "capa_texto": None}))
        escalones.append(("pelado, sin ninguna marca", {**base, "placa": None, "seg_placa": 0.0,
                                                        "capa_texto": None, "logo_png": None}))
        usado = _probar_escalones(src, salida, escalones, audio=audio,
                                  max_seconds=max_seconds, firma=firma)
        logger.info(f"Reel vertical armado: {salida} (fotos compuestas)"
                    + (" + logo" if usado.get("logo_png") else "")
                    + (" + texto" if usado.get("capa_texto") else "")
                    + (f" + placa final {seg_placa:.0f}s" if usado.get("placa") else ""))
        return salida

    # `overlay=False` saca el marco del diario (esquinas + caja del zócalo + barra web/redes)
    # Y con él el texto del zócalo (va dibujado adentro). Lo usa el diario; la radio deja True.
    # El marco del diario (esquinas + caja del zócalo + barra) NO convive con la placa: se
    # pisarían. Con placa, el marco se apaga solo.
    overlay_png = (overlay_con_zocalo(zocalo or "", salida.parent / f"overlay_{salida.stem}.png")
                   if (overlay and not _bandas_on()) else None)
    # Contenido real del video (sin las barras negras) → con eso se calcula el marco.
    recorte = detectar_recorte(src)
    cont_w, cont_h = (recorte[0], recorte[1]) if recorte else _dimensiones(src)
    if cont_w <= 0 or cont_h <= 0:
        # `_dimensiones` devuelve (0,0) a propósito cuando no puede leer la ficha del
        # archivo. De acá para abajo se divide por el ancho para encuadrar, así que sin
        # este piso el reel se cae entero en vez de salir un poco peor (pasó probando con
        # un nombre de archivo acentuado). Lo trato como lo más probable: celular vertical.
        logger.warning(f"No pude leer el tamaño de {src.name}; lo trato como 1080x1920 "
                       f"(vertical de celular) para no quedarme sin reel.")
        cont_w, cont_h = 1080, 1920
    # PLACA: el texto de arriba se arma PRIMERO porque define dónde empieza la imagen, y de
    # ahí sale el encuadre full bleed y el fundido del borde.
    placa = None
    fondo_pl = None
    # Solo con `REEL_PLACA_FONDO_AUTO=1`: el tono del fondo sale del PROPIO material. Se
    # calcula una sola vez y ANTES de los escalones, así los reintentos no lo repiten.
    color_fondo = _color_dominante(src, salida.parent) if _bandas_on() else ""
    # ¿Es un afiche? Decide si la imagen se puede recortar o no. Se calcula acá, una sola
    # vez, por lo mismo que el color: los reintentos por degradado no tienen que repetirlo.
    grafica = _es_grafica(src, salida.parent) if (_bandas_on() and es_foto) else False
    if _bandas_on():
        armada = placa_texto_png(volanta, titular, resumen,
                                 salida.parent / f"placa_{salida.stem}.png")
        if armada:
            png, y_img = armada[0], armada[1]
            hueco = 1920 - y_img
            # ¿Llena el hueco recortando, o va entera? Un afiche va siempre entero, una
            # foto vertical siempre a sangre, y una apaisada según cuánto habría que
            # tirarle (ver `_llena_el_cuadro`).
            llena = _llena_el_cuadro(cont_w, cont_h, hueco, grafica=grafica)
            if llena:
                ancho_foto, alto_foto = 1080, hueco
            else:
                # ENTERA: entra completa sin deformarse. Escala por el lado que apriete —el
                # ancho en una apaisada, el alto en una vertical— y lo que sobra es fondo.
                esc = min(1080 / cont_w, hueco / cont_h)
                ancho_foto = max(2, int(round(cont_w * esc)))
                alto_foto = max(2, int(round(cont_h * esc)))
            ancho_foto -= ancho_foto % 2
            alto_foto -= alto_foto % 2
            _esc = max(1080 / cont_w, hueco / cont_h)
            perdida = round(100 * (1 - (1080 * hueco) / (cont_w * _esc * cont_h * _esc)))
            if llena:
                logger.info(f"Material {cont_w}x{cont_h}: va A SANGRE en el hueco "
                            f"(1080x{hueco}), recortando el {perdida}% y encuadrando el "
                            f"sujeto.")
            elif grafica:
                logger.info(f"Material {cont_w}x{cont_h}: es una gráfica, así que va ENTERA "
                            f"({ancho_foto}x{alto_foto}) —recortarla se llevaría el "
                            f"{perdida}% y con eso, el texto—.")
            else:
                logger.info(f"Material {cont_w}x{cont_h}: recortarlo para llenar el hueco se "
                            f"comería el {perdida}% de la imagen, así que va ENTERO "
                            f"({ancho_foto}x{alto_foto}) con el borde de abajo sombreado.")
            y_media = y_img
            if not llena:
                # Segunda pasada: ahora que sé dónde termina la foto, sé cuánto fondo queda
                # abajo y puedo escribir ahí. El bloque de arriba sale idéntico.
                con_pie = False
                if cuerpo:
                    zona = (y_img + alto_foto + 24, 1920 - BANDA_SEGURO)
                    # Van VARIAS candidatas: el hueco es chico y si la primera oración es
                    # larguísima y sin comas, se pasa a la siguiente.
                    frases = oraciones_utiles(cuerpo, resumen)
                    if frases and zona[1] - zona[0] >= PLACA_PIE_MIN_ALTO:
                        otra = placa_texto_png(volanta, titular, resumen,
                                               salida.parent / f"placa_{salida.stem}.png",
                                               pie=frases, pie_zona=zona)
                        if otra and otra[2]:
                            png, con_pie = otra[0], True
                y_media = _y_entera(y_img, alto_foto, con_pie)
            # El borde de abajo se sombrea solo si la imagen NO llega al pie del cuadro: una
            # que llega no tiene con qué fundirse y perdería foto contra el filo.
            funde_abajo = (y_media + alto_foto) < 1918
            placa = (png, y_media,
                     fundido_png(alto_foto, salida.parent / f"fundido_{salida.stem}.png",
                                 abajo=funde_abajo, ancho=ancho_foto),
                     alto_foto, llena, ancho_foto)
            fondo_pl = fondo_placa_png(salida.parent / f"fondo_placa_{salida.stem}.png",
                                       color_fondo)
    # Con placa el marco naranja no va: el fondo es el carbón que pinta el filtergraph.
    fondo = None if placa else fondo_enmarcado(
        cont_w, cont_h, salida.parent / f"fondo_{salida.stem}.png")
    # Por default el video va ENTERO, a su proporción, escalado hasta tocar los márgenes,
    # sobre el fondo difuminado. Con `REEL_FULLBLEED=1` los verticales/cuadrados se recortan
    # a 9:16 encuadrando el sujeto (los horizontales nunca: ver `_fullbleed_aplica`).
    if placa and placa[4]:
        # Cuadrada o vertical: llena el hueco, encuadrando el sujeto (busca caras).
        encuadre = _encuadre_fullbleed(src, cont_w, cont_h, recorte, salida.parent,
                                       alto=placa[3],
                                       fundido=int(_num("REEL_PLACA_FUNDIDO", PLACA_FUNDIDO)))
    elif placa:
        encuadre = None                     # apaisada: entera, sin recortar nada
    elif _fullbleed_aplica(cont_w, cont_h):
        encuadre = _encuadre_fullbleed(src, cont_w, cont_h, recorte, salida.parent)
    else:
        encuadre = None
        if _fullbleed_on():
            logger.info(f"Video horizontal ({cont_w}x{cont_h}): sin full bleed, va con fondo difuminado.")

    marca = dict(fondo=fondo, logo_png=logo_png, overlay=overlay_png, placa=placa_cierre,
                 seg_placa=seg_placa, recorte=recorte, encuadre=encuadre,
                 marca_texto=logo, texto_placa=placa, color_fondo=color_fondo,
                 fondo_placa=fondo_pl, capa_texto=None)
    pelado = dict(fondo=None, logo_png=None, overlay=None, placa=None, seg_placa=0.0,
                  recorte=None, encuadre=None, marca_texto=False, texto_placa=None,
                  color_fondo="", fondo_placa=None, capa_texto=None)
    escalones = [("completo", marca)]
    if placa_cierre:
        escalones.append(("sin la placa de cierre", {**marca, "placa": None, "seg_placa": 0.0}))
    if fondo_pl:
        # El humo es una entrada más del filtergraph: si ESO molesta, se pierde el humo y
        # nada más (queda el carbón liso), no el texto ni el isologo.
        escalones.append(("sin el humo del fondo", {**marca, "fondo_placa": None}))
    if placa:
        # Un escalón propio: si lo que molesta es la placa, el reel conserva el isologo, el
        # texto de marca y la placa de cierre. La imagen vuelve al cuadro entero, así que se
        # recalculan fondo y encuadre — pero SOLO si se llega a usar este escalón: cuesta
        # PIL y detección de caras, y el 99% de las veces el reel sale completo de una.
        def sin_placa():
            return {**marca, "texto_placa": None, "marca_texto": bool(logo_png),
                    "fondo_placa": None,
                    "fondo": fondo_enmarcado(cont_w, cont_h,
                                             salida.parent / f"fondo2_{salida.stem}.png"),
                    "encuadre": None}
        escalones.append(("sin el texto de arriba", sin_placa))
    if fondo or logo_png or overlay_png or placa_cierre or recorte or placa:
        escalones.append(("pelado, sin ninguna marca", pelado))

    marca = _probar_escalones(src, salida, escalones, audio=audio,
                              max_seconds=max_seconds, firma=firma)
    logger.info(
        f"Reel vertical armado: {salida}"
        + (f" (recortado a {max_seconds}s)" if max_seconds else "")
        + (" + full-bleed" if marca.get("encuadre") else "")
        + (" + recorte-negro" if marca.get("recorte") else "")
        + (" + fondo" if marca.get("fondo") else "")
        + (" + humo" if (marca.get("fondo_placa") and marca.get("texto_placa")) else "")
        + (" + logo" if marca.get("logo_png") else "")
        + (" + texto de marca" if (marca.get("marca_texto") and not marca.get("texto_placa")) else "")
        + (" + placa de texto" if marca.get("texto_placa") else "")
        + (" + overlay" if marca.get("overlay") else "")
        + (f" + placa final {seg_placa:.0f}s" if marca.get("placa") else "")
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


def _placa_de_foto(foto, y_img: int, fondo, frases: list, work_dir: Path, clave: str,
                   f_resumen: str, p_resumen: str) -> Path:
    """UNA foto compuesta como un cuadro entero del reel estilo placa: el fondo de humo y la
    foto acomodada SEGÚN SU PROPIA FORMA en el espacio de abajo (desde `y_img`). El texto de
    arriba NO va acá: se pega después, una sola vez, para que quede quieto entre foto y foto.

    Por qué cada foto por separado (pedido del usuario 2026-09-25, «si suben varias fotos de
    distintos tamaños que queden todas bien hechas»): antes el pase de fotos se armaba con
    cada foto en un 9:16 con su propio fondo borroso, y DESPUÉS se recortaba el video entero
    para el hueco de abajo, como si fuera una sola imagen vertical. Las apaisadas terminaban
    como una franja con bandas borrosas adentro y todas compartían un único encuadre.

    Reglas, las mismas que para un video (`_llena_el_cuadro`):
      · vertical / cuadrada / apenas apaisada → A SANGRE, encuadrada en las caras y disuelta
        por arriba;
      · MUY apaisada → ENTERA a lo ancho, disuelta arriba y SOMBREADA abajo, con el pie de
        texto debajo si entra (si no, centrada);
      · GRÁFICA (afiche) → ENTERA, sin desvanecer —se comería el texto del afiche—, con
        borde neto y una sombra suave."""
    from PIL import Image, ImageDraw, ImageFilter, ImageOps
    foto = Path(foto)
    # `exif_transpose`: la foto de celular viene «acostada» con una marca de giro; sin esto,
    # PIL la ve de costado y la encuadraría mal.
    img = ImageOps.exif_transpose(Image.open(foto)).convert("RGB")
    w, h = img.size
    hueco = 1920 - y_img
    # El detector de afiches se calibró (40 publicidades y 62 fotos reales) mirando la foto
    # DESPUÉS de convertirla en un clip de video al tamaño del reel: el codificador aplana los
    # rellenos lisos y eso es parte de lo que mide. Sobre el JPG original, con su grano, un
    # afiche de 2400x3000 pasaba por foto y se recortaba. Se le da el mismo clip que veía.
    muestra = Path(work_dir) / f"_graf_{clave}.mp4"
    _foto_a_clip(foto, muestra, 1.0)
    try:
        grafica = _es_grafica(muestra, work_dir)
    finally:
        try:
            muestra.unlink()
        except Exception:                                        # noqa: BLE001
            pass
    llena = _llena_el_cuadro(w, h, hueco, grafica=grafica)
    lienzo = fondo.copy()
    arriba = int(_num("REEL_PLACA_FUNDIDO", PLACA_FUNDIDO))
    abajo = int(_num("REEL_PLACA_FUNDIDO_ABAJO", PLACA_FUNDIDO_ABAJO))
    if llena:
        try:
            from story_image import _caras_principales, _detect_faces
            caras = _caras_principales(_detect_faces(img))
        except Exception:                                        # noqa: BLE001
            caras = []
        nw, nh, x, y = _ventana_caras(caras, w, h, 1080, hueco, fundido=arriba)
        media = img.resize((nw, nh), Image.LANCZOS).crop((x, y, x + 1080, y + hueco))
        lienzo.paste(media, (0, y_img), mascara_fundido(1080, hueco, arriba=arriba))
        logger.info(f"Foto {foto.name} ({w}x{h}): A SANGRE en 1080x{hueco}.")
    else:
        margen = PLACA_GRAFICA_MX if grafica else 0
        esc = min((1080 - 2 * margen) / w, (hueco - 2 * margen) / h)
        aw, ah = max(2, round(w * esc)), max(2, round(h * esc))
        media = img.resize((aw, ah), Image.LANCZOS)
        x0 = (1080 - aw) // 2
        pie: list = []
        if frases and not grafica:
            pie = _pie_bloques(frases, (y_img + ah + 24, 1920 - BANDA_SEGURO),
                               f_resumen, p_resumen)
        ym = _y_entera(y_img + margen, ah, bool(pie))
        if grafica:
            # Sombra suave debajo del afiche: lo despega del fondo sin tocarle un píxel.
            pad = 60
            sombra = Image.new("L", (aw + 2 * pad, ah + 2 * pad), 0)
            ImageDraw.Draw(sombra).rectangle((pad, pad, pad + aw, pad + ah), fill=170)
            sombra = sombra.filter(ImageFilter.GaussianBlur(22))
            lienzo.paste(Image.new("RGB", sombra.size, (0, 0, 0)), (x0 - pad, ym - pad + 16),
                         sombra)
            lienzo.paste(media, (x0, ym))
            logger.info(f"Foto {foto.name} ({w}x{h}): es una GRÁFICA, va entera ({aw}x{ah}).")
        else:
            funde_abajo = abajo if (ym + ah) < 1918 else 0
            lienzo.paste(media, (x0, ym), mascara_fundido(aw, ah, arriba=arriba,
                                                           abajo=funde_abajo))
            logger.info(f"Foto {foto.name} ({w}x{h}): muy apaisada, va ENTERA ({aw}x{ah}) "
                        f"con el borde de abajo sombreado" + (" y pie." if pie else "."))
        if pie:
            dibujar_bloques(ImageDraw.Draw(lienzo), pie)
    salida = Path(work_dir) / f"_placa_foto_{clave}.jpg"
    lienzo.save(salida, quality=93)
    return salida


def _fotos_compuestas(fotos: list, base: Path, seg_cont: float, *, titular: str,
                      resumen: str, volanta: str, cuerpo: str, work_dir: Path,
                      clave: str) -> Path | None:
    """Arma el 'video fuente' de un reel de FOTOS en el estilo placa: cada foto compuesta
    por separado (`_placa_de_foto`) y unidas con un fundido encadenado. Devuelve el PNG del
    texto de arriba (lo pega `to_vertical_reel`), o None si no hay texto que dibujar."""
    from PIL import Image
    armada = placa_texto_png(volanta, titular, resumen, work_dir / f"placa_{clave}.png")
    if not armada:
        return None
    png, y_img = armada[0], armada[1]
    color = _color_dominante(fotos[0], work_dir)          # "" salvo REEL_PLACA_FONDO_AUTO=1
    fondo_png = fondo_placa_png(work_dir / f"fondo_placa_{clave}.png", color)
    fondo = (Image.open(fondo_png).convert("RGB") if fondo_png else
             Image.new("RGB", (1080, 1920), _rgb_de(_color_fondo(color))))
    f_resumen = _fuente_banda("REEL_FUENTE_RESUMEN", FUENTE_RESUMEN)
    p_resumen = _cfg("REEL_PESO_RESUMEN", PESO_RESUMEN)
    frases = oraciones_utiles(cuerpo, resumen) if cuerpo else []
    placas = []
    for i, f in enumerate(fotos):
        try:
            placas.append(_placa_de_foto(f, y_img, fondo, frases, work_dir, f"{clave}_{i}",
                                         f_resumen, p_resumen))
        except Exception as e:                                   # noqa: BLE001
            logger.warning(f"No pude componer la foto {Path(f).name} ({e}); la salteo.")
    if not placas:
        raise RuntimeError("ninguna foto se pudo componer")
    # Solo FUNDIDO entre fotos: el fondo y el texto no cambian de una a otra, así que una
    # cortina o un deslizamiento moverían el humo por detrás del texto quieto.
    por = (seg_cont if len(placas) == 1 else
           max(3.0, (seg_cont + (len(placas) - 1) * 0.6) / len(placas)))
    build_slideshow(placas, base, seg=por, fade=0.6, transiciones=["fade"])
    return png


def foto_a_reel(fotos, salida, *, seg: float | None = None, zocalo: str | None = None,
                firma: str | None = None, overlay: bool = True,
                titular: str = "", resumen: str = "", volanta: str = "",
                cuerpo: str = "") -> Path:
    """Convierte una FOTO (o varias) de una nota en un reel vertical 9:16 con el MISMO
    criterio estético que los videos: logo arriba a la derecha, texto de la nota arriba y
    la placa de cierre «Seguinos en redes» al final.

    En el estilo placa (con texto, lo normal) cada foto se compone POR SEPARADO según su
    forma (`_placa_de_foto`) y el texto se pega una sola vez encima, quieto. Si eso fallara,
    o sin texto, va el armado de antes: reusa `to_vertical_reel` pasándole un 'video
    fuente' armado con la/s foto/s.

    - Una foto → se muestra fija.
    - Varias → pase de fotos con fundido encadenado, todas branded.
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
    if _bandas_on() and (titular or resumen or volanta):
        capa = None
        try:
            capa = _fotos_compuestas(fotos, base, seg_cont, titular=titular, resumen=resumen,
                                     volanta=volanta, cuerpo=cuerpo, work_dir=salida.parent,
                                     clave=salida.stem)
        except Exception as e:                                   # noqa: BLE001
            logger.error(f"No pude componer las fotos en el estilo placa ({e}); van con el "
                         f"armado de antes.")
        if capa:
            logger.info(f"Foto-reel: {len(fotos)} foto(s) compuestas una por una → "
                        f"{seg_cont:.0f}s de contenido + placa")
            return to_vertical_reel(base, salida, audio=False, firma=firma, compuesto=capa)
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
                            overlay=overlay, titular=titular, resumen=resumen,
                            volanta=volanta, cuerpo=cuerpo, es_foto=True)


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
    r = subprocess.run([ff, "-i", str(src)], capture_output=True, text=True, errors="replace")
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
        vf = f"{_sin_giro()},fade=t=in:st=0:d=0.3,fade=t=out:st={fo:.2f}:d=0.3"
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
        _run_ffmpeg([ff, "-y", "-i", str(src), "-vf", _sin_giro(),
                     "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
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
        _run_ffmpeg([_ffmpeg(), "-y", "-i", str(src), "-vf", _sin_giro(),
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


def build_slideshow(imagenes, salida, *, seg: float = 3.5, fade: float = 0.6, fps: int = 30,
                    transiciones: list | None = None) -> Path:
    """imagenes: lista de Paths (cada una una placa 9:16). Devuelve el .mp4.

    `seg` es la duración MEDIA por placa: el reparto real lo hace `_duraciones_parejas`
    para que todas se vean el mismo tiempo. El total sigue siendo `n*seg - (n-1)*fade`.
    `transiciones` son los efectos de `xfade` que se van rotando (default `TRANS`)."""
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
            efectos = transiciones or TRANS
            tr = efectos[(i - 1) % len(efectos)]
            out = f"v{i}"
            fc.append(f"[{prev}][s{i}]xfade=transition={tr}:duration={fade}:offset={off}[{out}]")
            prev = out
        last = prev

    cmd = [ff, "-y", *inputs, "-filter_complex", ";".join(fc), "-map", f"[{last}]",
           "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(salida)]
    r = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if r.returncode != 0:
        logger.error("ffmpeg falló:\n" + (r.stderr or "")[-1200:])
        raise RuntimeError("ffmpeg error al armar el reel")
    logger.info(f"Reel armado: {salida} ({n} placas)")
    return salida
