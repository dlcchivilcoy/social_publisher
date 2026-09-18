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
# Tres tipografías, una por trabajo (2026-09-18). Montserrat es LA MARCA y no se toca; el
# titular y el resumen usan otras porque tienen otro problema que resolver:
#   · el TITULAR tiene que entrar en 2 renglones → una angosta permite letra más grande;
#   · el RESUMEN se lee chico sobre una foto → una humanista aguanta mejor ese tamaño.
# Si el archivo no está, se cae a Montserrat y el reel sale igual (solo lo avisa el log).
# Las dos vienen de Google Fonts y son VARIABLES: un solo archivo con todos los pesos
# adentro. Hay que pedir la instancia; si no, sale la Regular, demasiado fina para leerse
# sobre una foto. Se elige con `_tipo`, tanto al MEDIR como al DIBUJAR — si se midiera con
# una y se dibujara con otra, el texto no entraría donde dice que entra.
FUENTE_TITULAR = ASSETS / "fonts" / "ArchivoNarrow-Variable.ttf"
FUENTE_RESUMEN = ASSETS / "fonts" / "LibreFranklin-Variable.ttf"
PESO_TITULAR = "SemiBold"     # instancias que traen: Regular, Medium, SemiBold, Bold
PESO_RESUMEN = "SemiBold"
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

# ── Estilo «placa» (2026-09-18) ───────────────────────────────────────────────
# El texto va ARRIBA, en pocos renglones y grande, alternando NARANJA y BLANCO, y la
# imagen va FULL BLEED abajo, fundiéndose con el fondo por el borde de arriba.
#
# Por qué se cambió: los resúmenes reales tienen 255 caracteres de mediana. Medido sobre
# las 269 notas del ledger, en 3 renglones NO entran a ningún cuerpo legible — a 34 entran
# enteros el 14%, y para que entre la mayoría hay que bajar a 22, que es ilegible en un
# celular. El problema nunca fue el cuerpo: era el LARGO. Así que ahora va POCO texto y
# GRANDE, y lo que no entra se corta por oración.
PLACA_MX = 72                 # margen izquierdo del bloque de texto
PLACA_Y0 = 104                # dónde arranca la marca
PLACA_MARCA_TAM = 28
PLACA_VOLANTA_TAM = 42
PLACA_TITULAR_TAM = 88        # tope; baja solo si no entra
PLACA_TITULAR_MIN = 46
PLACA_TITULAR_RENGLONES = 3
PLACA_BAJADA_TAM = 44
PLACA_BAJADA_MIN = 30
PLACA_BAJADA_RENGLONES = 2
# Cuántos puntos de titular estamos dispuestos a resignar con tal de no partir un nombre
# entre dos renglones. Hasta 8 no se nota; más abajo sí, y ahí conviene el titular grande
# aunque el apellido caiga al renglón siguiente.
PLACA_NOMBRE_COSTO = 8
PLACA_IMG_MIN = 980           # la imagen nunca ocupa menos que esto (51% del cuadro)
PLACA_FUNDIDO = 240           # px de transición entre el fondo y la imagen
# Gris oscuro SÓLIDO detrás del texto de arriba (pedido del usuario 2026-09-18). Es un gris
# apenas frío: sobre él el naranja de la marca y el blanco del titular saltan, y no compite
# con la foto. Se cambia con `REEL_PLACA_FONDO` (admite `0x22252B`, `#22252B` o `black`).
PLACA_FONDO = "0x22252B"
NARANJA = (247, 127, 0, 255)  # el naranja de la marca
BLANCO = (255, 255, 255, 255)
GRIS = (233, 236, 240, 255)   # la marca, apenas apagada
# Franja de abajo que tapan los controles de Instagram y Facebook (autor, texto, botones).
# El resumen nunca baja de acá: si la imagen es vertical y llega hasta el piso, el resumen
# SUBE y se apoya sobre la parte de abajo de la imagen, que es donde sí se ve.
BANDA_SEGURO = 330


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


def _logo_a_la_derecha() -> bool:
    """¿De qué lado va el isologo del reel? DERECHA por default (pedido 2026-09-14).
    `REEL_LOGO_LADO=izquierda` en el `.env` lo devuelve al lugar de antes."""
    return _cfg("REEL_LOGO_LADO", "derecha").lower() not in ("izq", "izquierda", "left")


def has_audio(src) -> bool:
    """True si el archivo trae pista de audio (parseando la salida de ffmpeg)."""
    r = subprocess.run([_ffmpeg(), "-i", str(src)], capture_output=True, text=True)
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
    r = subprocess.run([_ffmpeg(), "-i", str(src)], capture_output=True, text=True)
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


def _tipo(ruta: str, cuerpo: int, peso: str = ""):
    """Abre la tipografía en el CUERPO y el PESO pedidos.

    `peso` solo aplica a las variables (Archivo Narrow, Libre Franklin). Si esta build de
    PIL no sabe de variables, queda la instancia por defecto: más fina, pero el reel sale
    igual. Montserrat es estática y no usa `peso`."""
    from PIL import ImageFont
    f = ImageFont.truetype(ruta, cuerpo)
    if peso:
        try:
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


def _ancho_texto(texto: str, fuente: str, cuerpo: int, peso: str = "") -> int:
    """Ancho en px de ese texto. Sin PIL devuelve una estimación (no rompe el reel)."""
    texto = texto.replace(_PEGA, " ")     # el pegamento de los nombres mide como un espacio
    try:
        return int(_tipo(fuente, cuerpo, peso).getlength(texto))
    except Exception:  # noqa: BLE001
        return int(len(texto) * cuerpo * 0.62)


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
    mx = int(float(_cfg("REEL_LOGO_MARGEN_X", "72")))
    my = int(float(_cfg("REEL_LOGO_MARGEN_Y", "124")))
    ancho_logo = int(float(_cfg("REEL_LOGO_ANCHO", "150")))
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


def _color_fondo() -> str:
    """El gris oscuro de la placa, en el formato que entiende ffmpeg (`0xRRGGBB`)."""
    c = (_cfg("REEL_PLACA_FONDO", PLACA_FONDO) or PLACA_FONDO).strip()
    return ("0x" + c[1:]) if c.startswith("#") else c


def _por_oracion(texto: str, fuente: str, cuerpo: int, ancho: int, maximo: int,
                 peso: str = "") -> list:
    """Corta el texto en `maximo` renglones cerrando por ORACIÓN cuando se puede.

    `_envolver` corta donde se le acaba el lugar, aunque sea a mitad de idea. Acá se prueba
    primero con oraciones enteras: es la diferencia entre «…se llevó adelante en las
    instalaciones del Club…» y una frase que se entiende sola."""
    oraciones = [o.strip() for o in re.split(r"(?<=[.!?])\s+", (texto or "").strip()) if o.strip()]
    acum = ""
    for o in oraciones:
        prueba = (acum + " " + o).strip()
        renglones = _envolver(prueba, fuente, cuerpo, ancho, maximo, peso)
        if renglones and renglones[-1].endswith("…"):
            break
        acum = prueba
    if acum:
        return _envolver(acum, fuente, cuerpo, ancho, maximo, peso)
    return _envolver(texto, fuente, cuerpo, ancho, maximo, peso)   # ni la 1ª oración entra


def placa_layout(volanta: str, titular: str, resumen: str, f_titular: str, f_resumen: str,
                 p_titular: str = "", p_resumen: str = "") -> dict:
    """El bloque de texto de arriba y dónde empieza la imagen.

    Devuelve `{bloques, y_img}`; cada bloque es
    `(texto, cuerpo, y, fuente, peso, color, centrado)`. Los colores ALTERNAN naranja →
    blanco → naranja de arriba hacia abajo, y todo va CENTRADO salvo la marca, que queda
    a la izquierda haciendo pareja con el isologo de la derecha."""
    ancho = 1080 - 2 * PLACA_MX
    f_marca = _fuente_marca()
    bloques: list = []
    y = PLACA_Y0

    # Marca: los dos medios en UN renglón y el usuario abajo, chiquitos y apagados.
    nombres = " | ".join(l.strip() for l in _cfg("REEL_MARCA_TEXTO", MARCA_TEXTO).split("|")
                         if l.strip())
    usuario = _cfg("REEL_MARCA_USUARIO", MARCA_USUARIO)
    tam = int(float(_cfg("REEL_PLACA_MARCA_TAM", str(PLACA_MARCA_TAM))))
    for txt in (nombres, usuario):
        if txt:
            bloques.append((txt, tam, y, f_marca, "", GRIS, False))
            y += round(tam * 1.25)
    y += 46

    # Volanta (NARANJA). Es corta por naturaleza: mediana de 23 caracteres en el ledger.
    if volanta:
        tam = int(float(_cfg("REEL_PLACA_VOLANTA_TAM", str(PLACA_VOLANTA_TAM))))
        for l in _emparejar(volanta, f_resumen, tam, ancho, 1, p_resumen):
            bloques.append((l, tam, y, f_resumen, p_resumen, NARANJA, True))
            y += round(tam * 1.2)
        y += 8

    # Titular (BLANCO), lo más grande que entre.
    if titular:
        tmax = int(float(_cfg("REEL_PLACA_TITULAR_TAM", str(PLACA_TITULAR_TAM))))
        cuerpo, lineas, pegar = _cuerpo_para(titular, f_titular, ancho,
                                             PLACA_TITULAR_RENGLONES, tmax,
                                             PLACA_TITULAR_MIN, p_titular)
        # Mismo cuerpo y mismos renglones, pero repartidos parejo. `pegar` va tal cual: si
        # arriba se decidió soltar el nombre, acá no se puede volver a pegar.
        lineas = _emparejar(titular, f_titular, cuerpo, ancho, PLACA_TITULAR_RENGLONES,
                            p_titular, pegar) or lineas
        salto = round(cuerpo * 1.08)          # interlineado apretado, como la referencia
        for i, l in enumerate(lineas):
            bloques.append((l, cuerpo, y + i * salto, f_titular, p_titular, BLANCO, True))
        y += (len(lineas) - 1) * salto + _alto_linea(f_titular, cuerpo, p_titular) + 22

    # Bajada (NARANJA), pocas líneas y cortada por oración.
    if resumen:
        tmax = int(float(_cfg("REEL_PLACA_BAJADA_TAM", str(PLACA_BAJADA_TAM))))
        cuerpo, lineas = tmax, []
        while cuerpo >= PLACA_BAJADA_MIN:
            lineas = _por_oracion(resumen, f_resumen, cuerpo, ancho,
                                  PLACA_BAJADA_RENGLONES, p_resumen)
            if lineas and not lineas[-1].endswith("…"):
                break
            cuerpo -= 2
        cuerpo = max(cuerpo, PLACA_BAJADA_MIN)
        if lineas and not lineas[-1].endswith("…"):
            # Ya sabemos QUÉ texto entra; ahora se reparte parejo entre los mismos renglones.
            lineas = _emparejar(" ".join(lineas), f_resumen, cuerpo, ancho,
                                PLACA_BAJADA_RENGLONES, p_resumen) or lineas
        salto = round(cuerpo * 1.26)
        for i, l in enumerate(lineas):
            bloques.append((l, cuerpo, y + i * salto, f_resumen, p_resumen, NARANJA, True))
        if lineas:
            y += (len(lineas) - 1) * salto + _alto_linea(f_resumen, cuerpo, p_resumen)

    y_img = min(y + 46, 1920 - PLACA_IMG_MIN)
    return dict(bloques=bloques, y_img=max(PLACA_Y0, y_img))


def placa_texto_png(volanta: str, titular: str, resumen: str, salida, *,
                    f_titular: str = "", f_resumen: str = "",
                    p_titular: str = "", p_resumen: str = ""):
    """Dibuja TODO el bloque de arriba (marca + volanta + titular + bajada) en un PNG
    transparente de 1080x1920. Devuelve `(png, y_img)`: dónde empieza la imagen.

    Va como imagen y no con `drawtext` por lo de siempre: al ffmpeg de Linux de la nube le
    falta libfreetype. Con `overlay` anda igual acá que allá."""
    volanta = " ".join((volanta or "").split())
    titular = " ".join((titular or "").split())
    resumen = " ".join((resumen or "").split())
    if not (titular or resumen or volanta):
        return None
    if not _fuente_marca():
        logger.warning("Sin tipografía para la placa del reel; el reel va sin texto.")
        return None
    f_titular = f_titular or _fuente_banda("REEL_FUENTE_TITULAR", FUENTE_TITULAR)
    f_resumen = f_resumen or _fuente_banda("REEL_FUENTE_RESUMEN", FUENTE_RESUMEN)
    p_titular = p_titular or _cfg("REEL_PESO_TITULAR", PESO_TITULAR)
    p_resumen = p_resumen or _cfg("REEL_PESO_RESUMEN", PESO_RESUMEN)
    try:
        from PIL import Image, ImageDraw
    except Exception as e:                                       # noqa: BLE001
        logger.warning(f"Sin PIL para dibujar la placa ({e}); el reel va sin texto.")
        return None

    caja = placa_layout(volanta, titular, resumen, f_titular, f_resumen, p_titular, p_resumen)
    if not caja["bloques"]:
        return None
    try:
        lienzo = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
        dib = ImageDraw.Draw(lienzo)
        for texto, cuerpo, y, fuente, peso, color, centrado in caja["bloques"]:
            f = _tipo(fuente, cuerpo, peso)
            # `anchor="ma"` ancla el renglón por su MEDIO, así la x pasa a ser el centro.
            x, anchor = (540, "ma") if centrado else (PLACA_MX, "la")
            # Sin sombra: contra el gris liso solo ensuciaba el contorno de la letra. Antes
            # hacía falta porque atrás había una foto borrosa con manchas claras y oscuras.
            dib.text((x, y), texto, font=f, fill=color, anchor=anchor)
        salida = Path(salida)
        salida.parent.mkdir(parents=True, exist_ok=True)
        lienzo.save(salida, "PNG")
    except Exception as e:                                       # noqa: BLE001
        logger.warning(f"No pude dibujar la placa del reel ({e}); va sin texto.")
        return None
    logger.info(f"Placa del reel: {len(caja['bloques'])} renglón/es · la imagen arranca "
                f"en y={caja['y_img']}")
    return salida, caja["y_img"]


def fundido_png(alto: int, salida, *, abajo: bool = False) -> Path | None:
    """Máscara en escala de grises para fundir los BORDES de la imagen con el fondo.

    Negro (transparente) → blanco (opaco) a los `PLACA_FUNDIDO` px. `alphamerge` la usa como
    canal alfa de la imagen, y así el corte deja de ser una línea recta: la foto se DISUELVE
    en el gris oscuro de arriba en vez de terminar de golpe.

    `abajo=True` funde también el borde de abajo: hace falta cuando la imagen NO llega al
    pie del cuadro (material apaisado) y debajo de ella queda el gris de la placa.

    El desvanecido NUNCA se come más de un tercio de la foto por borde. Sin ese freno, una
    panorámica muy ancha (4000x800 entra como una tira de 216px) quedaba más baja que los
    240px del fundido y DESAPARECÍA: el reel salía sin foto y la corrida daba «success»
    igual (encontrado auditando, 2026-09-18)."""
    try:
        from PIL import Image
    except Exception:                                            # noqa: BLE001
        return None
    try:
        fundido = max(1, int(float(_cfg("REEL_PLACA_FUNDIDO", str(PLACA_FUNDIDO)))))
        # Con fundido arriba Y abajo, cada borde se queda a lo sumo con un tercio: así el
        # medio de la foto SIEMPRE llega opaco, por finita que sea la tira.
        techo = max(1, alto // (3 if abajo else 2))
        if fundido > techo:
            logger.info(f"La imagen mide {alto}px de alto: achico el desvanecido de "
                        f"{fundido} a {techo}px para que no se coma la foto.")
            fundido = techo
        m = Image.new("L", (1080, alto), 255)
        px = m.load()
        for y in range(min(fundido, alto)):
            v = round(255 * (y / fundido) ** 1.6)   # arranca lento: la unión se nota menos
            for x in range(1080):
                px[x, y] = v
        if abajo:
            for y in range(min(fundido, alto)):
                v = round(255 * (y / fundido) ** 1.6)
                fila = alto - 1 - y
                for x in range(1080):
                    if v < px[x, fila]:
                        px[x, fila] = v
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

    mx = int(float(_cfg("REEL_LOGO_MARGEN_X", "72")))
    my = int(float(_cfg("REEL_LOGO_MARGEN_Y", "124")))
    ancho_logo = int(float(_cfg("REEL_LOGO_ANCHO", "150")))
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
    mx = int(float(_cfg("REEL_LOGO_MARGEN_X", "72")))
    my = int(float(_cfg("REEL_LOGO_MARGEN_Y", "124")))
    ancho = int(float(_cfg("REEL_LOGO_ANCHO", "150")))
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
                               capture_output=True, text=True, timeout=60)
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
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=_timeout_ffmpeg())
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


def _llena_el_cuadro(w: int, h: int) -> bool:
    """¿Se puede AMPLIAR esta imagen hasta llenar el hueco sin perder información?

    Sí si es CUADRADA o VERTICAL: ahí ampliar recorta poco y de los costados, donde casi
    nunca pasa nada. Una APAISADA es otra cosa: llevarla a un hueco casi cuadrado le come
    la mitad del ancho, y en una foto de varios chicos jugando eso se lleva medio equipo.
    Esas van ENTERAS, y abajo queda el gris de la placa (pedido del usuario 2026-09-18).
    Ese gris del pie no es espacio perdido: es justo la franja que Instagram y Facebook tapan
    con su propio texto y sus botones.

    El corte se mueve con `REEL_FULLBLEED_MAX_AR` (default 1.0 = hasta cuadrada)."""
    if w <= 0 or h <= 0:
        return False
    try:
        max_ar = float(_cfg("REEL_FULLBLEED_MAX_AR", "1.0"))
    except ValueError:
        max_ar = 1.0
    return (w / h) <= max_ar


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


def _encuadre_fullbleed(src: Path, cont_w: int, cont_h: int, recorte, work_dir: Path,
                        *, alto: int = 1920):
    """Devuelve (nw, nh, x, y): a cuánto escalar el video para LLENAR 1080x1920 y desde
    dónde recortarlo, ENCUADRADO EN EL SUJETO.

    Busca caras en 3 fotogramas (reusa el detector de las placas) y se queda con el
    fotograma más representativo (el de mayor superficie de caras). El recorte se centra
    en el centro PONDERADO por el tamaño de cada cara (el primer plano pesa más) y deja
    aire arriba para no cortar cabezas. Sin caras (paisaje/objeto) o ante cualquier error:
    recorte centrado con leve sesgo hacia arriba (mismo criterio que `story_image._encuadrar`)."""
    W, H = 1080, alto          # `alto` < 1920 cuando el reel lleva titular y resumen
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
                texto_placa: tuple | None = None) -> None:
    """Arma el reel vertical en UNA sola pasada de ffmpeg (un único re-encode, para
    no pagar el doble de CPU en la nube): fondo borroso + video + logo + firma, y
    al final la placa de cierre concatenada. Si `recorte` (w,h,x,y) viene dado, primero
    le saca las barras negras al video para que el marco naranja tape ese negro."""
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
                       f"color={_color_fondo()}@1:t=fill")
        else:
            # Misma historia que `drawtext` (2026-09-17): no todas las builds traen todo, y la
            # de Linux de la nube es más pelada que la de Windows. `drawbox` no depende de
            # ninguna librería externa, así que esto no debería pasar nunca — pero si pasa, el
            # reel sale con el fondo borroso de antes en vez de no salir.
            logger.warning("Este ffmpeg NO trae «drawbox»: el fondo de la placa va borroso.")
            relleno = ("scale=1080:1920:force_original_aspect_ratio=increase,"
                       "crop=1080:1920,boxblur=luma_radius=40:luma_power=1")
        vf = f"{pre}{v0}split=2[bg][fg];[bg]{relleno},setsar=1[bgb]"
        if encuadre:                        # cuadrada/vertical: amplía y recorta a medida
            nw, nh, cx, cy = encuadre
            vf += f";[fg]scale={nw}:{nh},setsar=1,crop=1080:{mh}:{cx}:{cy},format=rgba[fgc]"
        else:                               # apaisada: entera, a lo ancho del cuadro
            vf += f";[fg]scale=1080:{mh},setsar=1,format=rgba[fgc]"

        etiqueta_img = "[fgc]"
        if mascara:
            # `alphamerge` toma el brillo de la máscara como canal alfa: negro arriba =
            # transparente, y de ahí a blanco. El borde de la foto deja de ser una línea.
            vf += f";[MASK:v]format=gray,scale=1080:{mh}[mk];[fgc][mk]alphamerge[fga]"
            etiqueta_img = "[fga]"
        vf += f";[bgb]{etiqueta_img}overlay=0:{ym}[v]"
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
        ancho = int(float(_cfg("REEL_LOGO_ANCHO", "150")))
        mx = int(float(_cfg("REEL_LOGO_MARGEN_X", "72")))
        my = int(float(_cfg("REEL_LOGO_MARGEN_Y", "124")))
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
        r = subprocess.run([exe, "-hide_banner", "-version"], capture_output=True, text=True)
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
            fino = _ancho_texto("Chivilcoy ñÁÉÍ", str(ruta), 50, "Regular")
            grueso = _ancho_texto("Chivilcoy ñÁÉÍ", str(ruta), 50, "SemiBold")
            peso = "variable" if fino != grueso else "un solo peso"
            print(f"  OK   {clave:8} {Path(ruta).name} ({peso})")
        except Exception as e:                                   # noqa: BLE001
            print(f"  ROTO {clave}: {Path(ruta).name} no se puede usar: {e}")
            ok = False

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
                                 volanta="Ciclo de teatro independiente")
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

    print("\n" + ("=== TODO EN ORDEN: el próximo reel puede salir tranquilo ===" if ok else
                  "=== HAY ALGO MAL: mirá las líneas de arriba ==="))
    return ok


# Qué le faltó al último reel armado. Lo lee `transcriber` para avisarlo en el mail de
# revisión: un reel sin marca que sale en silencio es peor que uno que no sale.
_DEGRADADO: dict = {}


def ultimo_reel_degradado() -> dict:
    """`{}` si el último reel salió completo; si no, `{nivel, motivo}`."""
    return dict(_DEGRADADO)


def to_vertical_reel(src, salida, *, audio: bool = True, max_seconds: float | None = None,
                     firma: str | None = None, logo: bool = True,
                     placa_final: bool = True, zocalo: str | None = None,
                     overlay: bool = True, titular: str = "", resumen: str = "",
                     volanta: str = "") -> Path:
    """Convierte un video cualquiera a un reel vertical 1080x1920 (9:16).

    El video se escala ENTERO (sin recortar) y se centra sobre un fondo borroso de
    sí mismo (misma estética que las historias, story_image._fit_blur). Mantiene el
    audio por defecto. Si se pasa `max_seconds`, recorta el reel a esa duración
    (ej. 60 para los reels sin desgrabar). Si se pasa `firma`, estampa una banda
    inferior con ese texto (la firma de la Red de Corresponsales).

    `logo=True` estampa el isotipo del diario arriba a la DERECHA (perilla `REEL_LOGO_LADO`;
    `izquierda` lo devuelve al lugar de antes) y, del lado libre, el TEXTO de marca
    («DIARIO LA CAMPAÑA | RADIO DEL CENTRO» + «@diarioyradio»; `REEL_MARCA_TEXTO=0` lo
    apaga). El OVERLAY del diario
    (marco + caja del zócalo + barra con la web y las redes) va con el `zocalo` escrito
    adentro SOLO si `overlay=True` (default; `overlay=False` saca el marco y el texto del
    zócalo de una), y `placa_final=True` agrega al final la placa "Seguinos en redes"
    (5 s). Si el video trae BARRAS NEGRAS horneadas (apaisado dentro de un cuadro vertical,
    o directamente apaisado), se las recorta y el marco naranja tapa ese negro. Todo se
    apaga o se cambia por `.env` (REEL_LOGO / REEL_FONDO / REEL_OVERLAY / REEL_PLACA_FINAL /
    REEL_PLACA_SEG / REEL_RECORTE_NEGRO). Devuelve el .mp4.

    Con `volanta`/`titular`/`resumen` el reel sale en el estilo PLACA: el texto arriba,
    alternando NARANJA y BLANCO (volanta → titular → bajada), y la imagen FULL BLEED abajo,
    fundida con el fondo por su borde de arriba. El texto ya lo escribió Gemini al redactar
    la nota: acá no se le pide nada, solo se dibuja. Se apaga con `REEL_BANDAS=0`.
    """
    src, salida = Path(src), Path(salida)
    logo_png = _asset("REEL_LOGO", LOGO_REEL) if logo else None
    placa_cierre = _asset("REEL_PLACA_FINAL", PLACA_FINAL) if placa_final else None
    seg_placa = float(_cfg("REEL_PLACA_SEG", str(PLACA_SEG)))
    # `overlay=False` saca el marco del diario (esquinas + caja del zócalo + barra web/redes)
    # Y con él el texto del zócalo (va dibujado adentro). Lo usa el diario; la radio deja True.
    # El marco del diario (esquinas + caja del zócalo + barra) NO convive con la placa: se
    # pisarían. Con placa, el marco se apaga solo.
    overlay_png = (overlay_con_zocalo(zocalo or "", salida.parent / f"overlay_{salida.stem}.png")
                   if (overlay and not _bandas_on()) else None)
    # Contenido real del video (sin las barras negras) → con eso se calcula el marco.
    recorte = detectar_recorte(src)
    cont_w, cont_h = (recorte[0], recorte[1]) if recorte else _dimensiones(src)
    # PLACA: el texto de arriba se arma PRIMERO porque define dónde empieza la imagen, y de
    # ahí sale el encuadre full bleed y el fundido del borde.
    placa = None
    if _bandas_on():
        armada = placa_texto_png(volanta, titular, resumen,
                                 salida.parent / f"placa_{salida.stem}.png")
        if armada:
            png, y_img = armada
            hueco = 1920 - y_img
            # Cuadrada o vertical: la imagen LLENA el hueco. Apaisada: va entera, pegada
            # arriba, y abajo queda el gris de la placa — recortarla perdería los costados.
            llena = _llena_el_cuadro(cont_w, cont_h)
            alto_foto = hueco if llena else min(hueco, int(round(1080 * cont_h / cont_w)))
            alto_foto = max(2, alto_foto - alto_foto % 2)
            if not llena:
                logger.info(f"Material apaisado ({cont_w}x{cont_h}): va ENTERO "
                            f"({alto_foto}px de los {hueco} del hueco) y abajo queda el "
                            f"gris de la placa, para no recortarle los costados.")
            placa = (png, y_img,
                     fundido_png(alto_foto, salida.parent / f"fundido_{salida.stem}.png",
                                 abajo=not llena),
                     alto_foto, llena)
    y_media, alto_media = (placa[1], 1920 - placa[1]) if placa else (0, 1920)
    # Con placa el marco naranja no va: el fondo es el gris liso que pinta el filtergraph.
    fondo = None if placa else fondo_enmarcado(
        cont_w, cont_h, salida.parent / f"fondo_{salida.stem}.png")
    # Por default el video va ENTERO, a su proporción, escalado hasta tocar los márgenes,
    # sobre el fondo difuminado. Con `REEL_FULLBLEED=1` los verticales/cuadrados se recortan
    # a 9:16 encuadrando el sujeto (los horizontales nunca: ver `_fullbleed_aplica`).
    if placa and placa[4]:
        # Cuadrada o vertical: llena el hueco, encuadrando el sujeto (busca caras).
        encuadre = _encuadre_fullbleed(src, cont_w, cont_h, recorte, salida.parent,
                                       alto=placa[3])
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
                 marca_texto=logo, texto_placa=placa)
    # Si la marca hace fallar el filtergraph, el reel igual sale: nunca se pierde una
    # publicación por el fondo, el logo, el overlay o la placa. Pero se baja DE A UN
    # ESCALÓN, no de golpe: antes, un problema con la placa se llevaba puesto también al
    # isologo y el reel salía sin ninguna marca (2026-09-17). Ahora, si la placa molesta,
    # el reel conserva el logo y el texto.
    _DEGRADADO.clear()
    pelado = dict(fondo=None, logo_png=None, overlay=None, placa=None, seg_placa=0.0,
                  recorte=None, encuadre=None, marca_texto=False, texto_placa=None)
    escalones = [("completo", marca)]
    if placa_cierre:
        escalones.append(("sin la placa de cierre", {**marca, "placa": None, "seg_placa": 0.0}))
    if placa:
        # Un escalón propio: si lo que molesta es la placa, el reel conserva el isologo, el
        # texto de marca y la placa de cierre. La imagen vuelve al cuadro entero, así que se
        # recalculan fondo y encuadre — pero SOLO si se llega a usar este escalón: cuesta
        # PIL y detección de caras, y el 99% de las veces el reel sale completo de una.
        def sin_placa():
            return {**marca, "texto_placa": None, "marca_texto": bool(logo_png),
                    "fondo": fondo_enmarcado(cont_w, cont_h,
                                             salida.parent / f"fondo2_{salida.stem}.png"),
                    "encuadre": None}
        escalones.append(("sin el texto de arriba", sin_placa))
    if fondo or logo_png or overlay_png or placa_cierre or recorte or placa:
        escalones.append(("pelado, sin ninguna marca", pelado))

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
        marca = args
        if i:
            _DEGRADADO.update(nivel=nombre, motivo=str(ultimo))
            logger.error(f"⚠️ El reel salió {nombre.upper()}. Motivo: {ultimo}")
        break
    logger.info(
        f"Reel vertical armado: {salida}"
        + (f" (recortado a {max_seconds}s)" if max_seconds else "")
        + (" + full-bleed" if marca["encuadre"] else "")
        + (" + recorte-negro" if marca["recorte"] else "")
        + (" + fondo" if marca["fondo"] else "")
        + (" + logo" if marca["logo_png"] else "")
        + (" + texto de marca" if (marca["marca_texto"] and not placa) else "")
        + (" + placa de texto" if placa else "")
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
                firma: str | None = None, overlay: bool = True,
                titular: str = "", resumen: str = "", volanta: str = "") -> Path:
    """Convierte una FOTO (o varias) de una nota en un reel vertical 9:16 con el MISMO
    criterio estético que los videos: fondo naranja que enmarca, logo arriba a la
    derecha, overlay del diario con el ZÓCALO escrito, y la placa de cierre «Seguinos
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
                            overlay=overlay, titular=titular, resumen=resumen,
                            volanta=volanta)


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
