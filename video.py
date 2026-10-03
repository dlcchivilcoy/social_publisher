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
# La volanta va SIEMPRE en UN renglón (pedido del usuario 2026-09-26): si no entra, se
# achica hasta este cuerpo, y recién ahí pierde las últimas palabras (nunca con «…»).
PLACA_VOLANTA_MIN = 32
PLACA_GAP_VOLANTA = 38        # de la línea base de la volanta al tope de las mayúsculas del titular
# …pero proporcional al titular (38 es con el de 118): con un titular de 76, ese aire quedaba
# enorme y se comía la franja de arriba del reel vertical.
PLACA_GAP_VOLANTA_REL = 0.32
PLACA_TITULAR_TAM = 118       # tope; baja solo si no entra (la referencia: 117)
PLACA_TITULAR_MIN = 46
# Máximo 3 renglones (pedido del usuario 2026-09-26: «si no ocupa mucho espacio»). El
# titular de 110 caracteres entra en 3 a cuerpo 46.
PLACA_TITULAR_RENGLONES = 3
PLACA_TITULAR_SALTO = 1.03
PLACA_GAP_TITULAR = 60        # de la línea base del titular al tope de la bajada
PLACA_BAJADA_TAM = 60
PLACA_BAJADA_MIN = 42
PLACA_BAJADA_SALTO = 1.12
# Tres renglones, no dos (2026-09-20): la bajada tiene que cerrar en punto y con dos
# renglones una primera oración de largo normal no entraba, así que salía cortada.
PLACA_BAJADA_RENGLONES = 3
# Desde el 2026-09-26 la bajada va DEBAJO de la imagen apaisada (antes iba bajo el titular y
# abajo de la foto iba un «pie» con la primera oración del cuerpo, que se sacó). Aire entre
# el borde de abajo de la foto y el tope de la bajada:
PLACA_BAJADA_AIRE = 34

# ── Tres FORMAS de reel (pedido del usuario 2026-09-26) ────────────────────────
# · VERTICAL (foto o video más alto que ancho, o cuadrado): la imagen ocupa los 3/4 de ABAJO
#   del cuadro, a todo el ancho. Arriba, la marca, una volanta de un renglón y el titular en
#   dos renglones; SIN bajada. Si hay que recortar para llenar, se recorta ABAJO: el usuario vio
#   que se estaban cortando las cabezas («cortás bastante margen superior»).
# · HORIZONTAL: el sistema de siempre (texto arriba, imagen a todo el ancho), con el titular
#   en hasta 3 renglones y la bajada DEBAJO de la imagen.
# · AFICHE vertical: ocupa todo el cuadro, y encima solo la marca y el isologo con un
#   sombreado mínimo arriba para que se lean.
# Hasta acá (ancho/alto) es «vertical». Las CUADRADAS (y casi cuadradas) van por el sistema
# horizontal: para llenar los 3/4 de abajo perderían los costados, que es donde suele estar la
# gente; en el horizontal pierden, como mucho, un poco de abajo.
PLACA_VERTICAL_AR = 0.9
PLACA_VERTICAL_MEDIA = 0.75       # la imagen vertical ocupa los 3/4 de abajo
PLACA_VERTICAL_TITULAR_TAM = 96   # tope del titular en vertical (la franja de texto es chica)
PLACA_VERTICAL_TITULAR_RENGLONES = 2
# En vertical el titular se achica hasta este cuerpo con tal de quedar en DOS renglones (así
# entra uno de ~95 caracteres); recién debajo pasa a tres.
PLACA_VERTICAL_TITULAR_MIN = 38
# La volanta nunca más grande que el titular: como mucho, este porcentaje de su cuerpo.
PLACA_VOLANTA_REL = 0.62
PLACA_VERTICAL_FUNDIDO = 90       # desvanecido corto: arriba de la foto suele estar la cabeza
PLACA_VERTICAL_GAP_MARCA = 42     # aire mínimo entre la marca y la volanta en vertical
PLACA_AFICHE_TOPE = 260           # un afiche que no llena el alto arranca debajo de la marca
PLACA_AFICHE_SOMBRA = 0.40        # opacidad del sombreado de arriba (arriba de todo)
PLACA_AFICHE_SOMBRA_ALTO = 380    # hasta dónde baja ese sombreado

# ── Vertical a PANTALLA COMPLETA (pedido del usuario 2026-09-27) ──────────────
# «Que los videos/imágenes/afiches verticales en 9:16 salgan completos: anular la parte de
# arriba con fondo gris grafito porque me corta margen del video, y dejar solo logo, redes,
# isologo, volanta y título, sin tapar el video». Con la franja de arriba, un 9:16 perdía un
# cuarto de su alto para caber en los 3/4 de abajo. Ahora lo que es más angosto que
# `PLACA_PANTALLA_AR` (9:16, 2:3) va ENTERO a todo el cuadro, y encima:
#   · la marca y el isologo arriba, con el sombreado mínimo del afiche y un halo en la letra;
#   · la volanta y el titular en CAJAS (la idea de la referencia que mandó), en el tercio de
#     abajo —donde no suele haber caras— y por encima de lo que tapan las apps. Si justo ahí
#     hay una cara, suben debajo de la marca (`plan_placa`, `caras`).
# Los de 3:4 y 4:5 siguen con la franja de arriba: caben en los 3/4 de abajo casi sin recorte.
PLACA_PANTALLA_AR = 0.72
# Si para llenar el cuadro hay que recortar a lo sumo esto, se llena; si no, va entero con
# humo a los costados (un material MÁS alto que 9:16, raro) o arriba (un 2:3).
PLACA_PANTALLA_TOLERANCIA = 0.07
# Estilo del texto encima del material (`REEL_PLACA_CAJAS`): «grafito» (caja carbón
# traslúcida con la letra blanca), «blanca» (caja blanca con la letra carbón, como la
# referencia) o «sombra» (sin cajas: la letra de siempre con un sombreado suave detrás). En
# las dos primeras la volanta va en una caja naranja con la letra blanca.
PLACA_CAJAS = "grafito"
PLACA_CAJA_PISO = 380          # del pie del cuadro al pie de la caja del titular
PLACA_CAJA_MX = 80             # borde izquierdo de las cajas (el texto sigue en PLACA_MX)
PLACA_CAJA_DER = 1080 - SEGURO_DERECHA   # las cajas no entran en la columna de botones
PLACA_CAJA_TITULAR_TAM = 84
PLACA_CAJA_TITULAR_MIN = 44    # en DOS renglones; recién debajo de esto pasa a tres
PLACA_CAJA_TITULAR_MIN3 = 38
PLACA_CAJA_SALTO = 1.10
PLACA_CAJA_VOLANTA_TAM = 40
PLACA_CAJA_OPACIDAD = 0.86     # la caja grafito deja ver apenas el video de atrás

# ── Reels de lo que llega por WhatsApp (corresponsales) ───────────────────────────────────
# 2026-10-02: el usuario mandó una «especificación editorial y visual v1.0» y el 2026-10-03 el
# «prompt detallado para editar reels», que define las CAJAS y el encabezado de estos reels
# (las medidas de acá salen de ese documento, en px de 1080x1920). Y pidió:
#   · fotos y videos VERTICALES (o cuadrados): a sangre en 9:16, con la volanta y el titular
#     en cajas abajo —si tapan una cara, suben—, sin bajada;
#   · fotos y videos HORIZONTALES: ENTEROS, sin recortar, en un reel MÁS CUADRADO, 4:5
#     (1080x1350): marca arriba, la imagen a todo el ancho y las cajas debajo, sin el hueco de
#     abajo que dejaba el 9:16.
# Los demás reels (videos del diario, foto-notas, radio) siguen con el estilo placa de arriba.
CORR_NARANJA = (239, 150, 60, 255)      # #EF963C: acentos y separadores finos
CORR_NARANJA_CAJA = (179, 91, 24, 255)  # #B35B18: la caja (opaca) de la volanta
CORR_GRAFITO = (32, 34, 38, 255)        # #202226: la caja principal y el fondo
CORR_BLANCO2 = (240, 240, 240, 255)     # #F0F0F0: el usuario
CORR_X = 100                            # margen izquierdo de la marca y de las cajas
CORR_MARCA = ((130, 29), (172, 26))     # (tope de las mayúsculas, cuerpo): nombre y usuario
CORR_LOGO = (128, 80, 130)              # isologo: ancho, margen derecho (X 872), Y
CORR_CAJA_ANCHO = 784                   # cajas de X 100 a 884: lejos de los botones de la derecha
CORR_CAJA_PAD = (26, 20)                # relleno de la caja principal: costados, arriba/abajo
CORR_CAJA_OPACIDAD = 0.94
CORR_VOLANTA = (30, 56, "500")          # cuerpo, alto de su caja, peso (en mayúsculas)
CORR_VOLANTA_MIN = 24
CORR_TITULAR = (54, 42, "400")          # cuerpo, mínimo en dos renglones, peso
CORR_SALTO = 1.18                       # interlineado
CORR_GAP = 12                           # entre la caja de la volanta y la principal
CORR_Y_ABAJO = 1058                     # tope de la volanta en 9:16 (la principal, en 1126)
CORR_Y_ARRIBA = 360                     # la posición alternativa, si abajo hay una cara
CORR_TOPE_IMAGEN = 290                  # en lo apaisado, la imagen arranca debajo del isologo
CORR_ALTO_APAISADO = 1350               # el reel de lo horizontal: 4:5
CORR_AR_APAISADO = 1.0                  # más ancho que alto = «apaisado»
# Cuántos puntos de titular estamos dispuestos a resignar con tal de no partir un nombre
# entre dos renglones. Hasta 8 no se nota; más abajo sí, y ahí conviene el titular grande
# aunque el apellido caiga al renglón siguiente.
PLACA_NOMBRE_COSTO = 8
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
GRAFITO = (24, 23, 28, 255)   # el carbón del fondo (`PLACA_FONDO`)


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


def _logo_geo() -> tuple[int, int, int]:
    """(ancho, margen lateral, margen de arriba) del isologo de siempre (`REEL_LOGO_*`)."""
    return (int(float(_cfg("REEL_LOGO_ANCHO", str(LOGO_ANCHO)))),
            int(float(_cfg("REEL_LOGO_MARGEN_X", str(LOGO_MX)))),
            int(float(_cfg("REEL_LOGO_MARGEN_Y", str(LOGO_MY)))))


def _logo_caja(geo: tuple | None = None) -> tuple[int, int, int, int] | None:
    """(x0, y0, x1, y1): el rectángulo que ocupa el isologo. None si el reel va sin logo.

    El alto se MIDE del PNG en vez de fijarlo, porque el filtergraph lo escala con
    `scale={ancho}:-1` y el alto real recién se conoce ahí. Gracias a esto, el texto de
    arriba sabe exactamente hasta dónde llega el logo y puede esquivarlo."""
    ruta = _asset("REEL_LOGO", LOGO_REEL)
    if not ruta:
        return None
    ancho, mx, my = geo or _logo_geo()
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


def forma_de(w: int, h: int, grafica: bool = False) -> str:
    """«pantalla», «vertical», «horizontal» o «afiche» (ver `PLACA_VERTICAL_AR` y
    `PLACA_PANTALLA_AR`). Un afiche apaisado se trata como horizontal: va entero, con sombra,
    y la bajada debajo. Un afiche vertical va siempre entero, sea 9:16 o 4:5."""
    ar = (w / h) if (w > 0 and h > 0) else 99.0
    vertical = ar <= PLACA_VERTICAL_AR
    if vertical and grafica:
        return "afiche"
    if vertical and ar < _num("REEL_PLACA_PANTALLA_AR", PLACA_PANTALLA_AR):
        return "pantalla"
    return "vertical" if vertical else "horizontal"


def _volanta_renglon(volanta: str, fuente: str, ancho: int, tope: int, peso: str) -> tuple:
    """(cuerpo, texto) de la volanta en UN solo renglón (pedido del usuario 2026-09-26).

    Se achica hasta `PLACA_VOLANTA_MIN`. Si ni así entra —pasa con volantas de ocho o diez
    palabras, que en realidad son un título— pierde las últimas palabras, sin «…»: una
    volanta es una etiqueta y se lee bien incompleta; un renglón de más, no."""
    volanta = " ".join((volanta or "").split())
    if not volanta:
        return 0, ""
    for c in range(tope, PLACA_VOLANTA_MIN - 1, -2):
        if _ancho_texto(volanta, fuente, c, peso) <= ancho:
            return c, volanta
    palabras = volanta.split()
    while len(palabras) > 1 and _ancho_texto(" ".join(palabras), fuente, PLACA_VOLANTA_MIN,
                                             peso) > ancho:
        palabras.pop()
    corta = " ".join(palabras).rstrip(",;:-–—")
    logger.info(f"La volanta «{volanta[:50]}» no entra en un renglón: queda «{corta}».")
    return PLACA_VOLANTA_MIN, corta


def _fuentes_placa() -> dict:
    """Tipografías y pesos de la placa, en un solo lugar."""
    return dict(f_t=_fuente_banda("REEL_FUENTE_TITULAR", FUENTE_TITULAR),
                f_r=_fuente_banda("REEL_FUENTE_RESUMEN", FUENTE_RESUMEN),
                p_t=_cfg("REEL_PESO_TITULAR", PESO_TITULAR),
                p_r=_cfg("REEL_PESO_RESUMEN", PESO_RESUMEN),
                p_v=_cfg("REEL_PESO_VOLANTA", PESO_VOLANTA),
                p_m=_cfg("REEL_PESO_MARCA", PESO_MARCA))


def _marca_bloques(f: dict) -> tuple:
    """Los dos renglones de marca («Diario La Campaña | Radio del Centro» y @diarioyradio),
    arriba a la izquierda. Devuelve `(bloques, línea base del último renglón)`."""
    nombres = " | ".join(l.strip() for l in
                         _cfg("REEL_PLACA_MARCA_TEXTO", PLACA_MARCA_TEXTO).split("|")
                         if l.strip())
    usuario = _cfg("REEL_MARCA_USUARIO", MARCA_USUARIO)
    tam = int(_num("REEL_PLACA_MARCA_TAM", PLACA_MARCA_TAM))
    # Que no se meta debajo del isologo si alguien pone un nombre más largo.
    caja_logo = _logo_caja()
    borde = (caja_logo[0] - 24) if (caja_logo and _logo_a_la_derecha()) else 1080 - PLACA_MX_DER
    while (tam > MARCA_TAM_MIN and nombres
           and _ancho_texto(nombres, f["f_r"], tam, f["p_m"]) > borde - PLACA_MX):
        tam -= 1
    asc, may = _metricas(f["f_r"], tam, f["p_m"])
    base = PLACA_Y0 + may
    fin = PLACA_Y0
    bloques = []
    for txt in (nombres, usuario):
        if txt:
            bloques.append((txt, tam, base - asc, f["f_r"], f["p_m"], GRIS, False))
            fin = base
            base += round(PLACA_MARCA_SALTO * tam / PLACA_MARCA_TAM)
    return bloques, fin


def _tope_volanta(tope: int, cuerpo_titular: int) -> int:
    """La volanta nunca más grande que el titular (ver `PLACA_VOLANTA_REL`)."""
    if not cuerpo_titular:
        return tope
    return max(PLACA_VOLANTA_MIN, min(tope, round(cuerpo_titular * PLACA_VOLANTA_REL)))


def _titulo_en(titular: str, f: dict, ancho: int, maximo: int, tope: int,
               minimo: int = PLACA_TITULAR_MIN, mayor: bool = False) -> tuple:
    """(cuerpo, renglones) del titular: lo más grande que entre en `maximo` renglones,
    repartido parejo y cortado en los dos puntos si los hay. Renglones vacíos si no hay.

    `mayor=True` busca la letra MÁS GRANDE que entre en hasta `maximo` renglones, sin la
    preferencia por pocos renglones de `_titular_cuerpo`: en vertical la franja es chica y
    un renglón con letra chica se veía peor que dos con letra grande."""
    if not titular:
        return 0, []
    if mayor:
        c, lineas, pegar = _cuerpo_para(titular, f["f_t"], ancho, maximo, tope, minimo,
                                        f["p_t"])
    else:
        c, lineas, pegar = _titular_cuerpo(titular, f["f_t"], ancho, maximo, tope,
                                           minimo, f["p_t"])
    # Mismos renglones, repartidos parejo. `pegar` va tal cual: si arriba se decidió soltar
    # el nombre, acá no se puede volver a pegar.
    lineas = _emparejar(titular, f["f_t"], c, ancho, len(lineas) or 1, f["p_t"], pegar) or lineas
    return _cortar_en_dos_puntos(titular, lineas, f["f_t"], c, ancho, f["p_t"], pegar)


def _volanta_y_titulo(vol: tuple, tit: tuple, f: dict, y_cap: int) -> tuple:
    """Dibuja volanta (naranja) + titular (blanco) desde `y_cap` (tope de las mayúsculas de
    la primera línea). Devuelve `(bloques, hasta dónde llega la tinta)`."""
    bloques, tinta, y = [], y_cap, y_cap
    v, vtxt = vol
    if vtxt:
        asc, may = _metricas(f["f_r"], v, f["p_v"])
        bl = y + may
        bloques.append((vtxt, v, bl - asc, f["f_r"], f["p_v"], NARANJA, False))
        tinta = bl + _pie_de_tinta(vtxt, f["f_r"], v, f["p_v"])
        y = bl + (min(PLACA_GAP_VOLANTA, round(tit[0] * PLACA_GAP_VOLANTA_REL))
                  if tit[0] else PLACA_GAP_VOLANTA)
    c, lineas = tit
    if lineas:
        asc, may = _metricas(f["f_t"], c, f["p_t"])
        salto = round(c * PLACA_TITULAR_SALTO)
        for i, l in enumerate(lineas):
            bl = y + may + i * salto
            bloques.append((l, c, bl - asc, f["f_t"], f["p_t"], BLANCO, False))
            tinta = bl + _pie_de_tinta(l, f["f_t"], c, f["p_t"])
    return bloques, tinta


def _arriba_vertical(volanta: str, titular: str, f: dict, fin_marca: int, techo: int) -> tuple:
    """Volanta + titular para material VERTICAL: entran en la franja de arriba (hasta
    `techo`, donde empieza la imagen) y se APOYAN sobre la imagen; el aire que sobra queda
    entre la marca y la volanta, como en la referencia.

    El titular va en DOS renglones (pedido 2026-09-26); se achica hasta que la franja lo
    contenga, y solo si ni con la letra más chica entra en dos, usa un tercero."""
    ancho = 1080 - PLACA_MX - PLACA_MX_DER
    vtope = int(_num("REEL_PLACA_VOLANTA_TAM", PLACA_VOLANTA_TAM))
    piso = fin_marca + PLACA_VERTICAL_GAP_MARCA
    tope = int(_num("REEL_PLACA_VERTICAL_TITULAR_TAM", PLACA_VERTICAL_TITULAR_TAM))
    elegido = None
    vol = _volanta_renglon(volanta, f["f_r"], ancho, vtope, f["p_v"])
    for maximo, minimo in ((PLACA_VERTICAL_TITULAR_RENGLONES, PLACA_VERTICAL_TITULAR_MIN),
                           (PLACA_VERTICAL_TITULAR_RENGLONES + 1, PLACA_TITULAR_MIN)):
        for t in range(tope, minimo - 1, -2):
            tit = _titulo_en(titular, f, ancho, maximo, t, minimo, mayor=True)
            if not titular or (tit[1] and not tit[1][-1].endswith("…")):
                vol = _volanta_renglon(volanta, f["f_r"], ancho,
                                       _tope_volanta(vtope, tit[0]), f["p_v"])
                bloques, tinta = _volanta_y_titulo(vol, tit, f, piso)
                if tinta + PLACA_AIRE_IMG <= techo:
                    elegido = tit
                    break
        if elegido:
            break
    if elegido is None:
        # Ni en tres renglones a la letra más chica: va igual (con «…» si hace falta) y la
        # imagen arranca donde termine el texto. Con los títulos reales (≤110 caracteres) no
        # pasa; está por si llega un parte policial entero como título.
        elegido = _titulo_en(titular, f, ancho, PLACA_VERTICAL_TITULAR_RENGLONES + 1,
                             PLACA_TITULAR_MIN)
        logger.warning("El titular no entra en la franja de arriba del reel vertical.")
    bloques, tinta = _volanta_y_titulo(vol, elegido, f, piso)
    # Apoyado sobre la imagen: se baja todo lo que sobre.
    corrida = max(0, techo - PLACA_AIRE_IMG - tinta)
    if corrida:
        bloques, tinta = _volanta_y_titulo(vol, elegido, f, piso + corrida)
    return bloques, tinta


# Del más generoso (el de la referencia) al más compacto: (tope del titular, aire entre la
# marca y la volanta, tope de la volanta). Primero se achica el AIRE, que es lo que menos se
# extraña, y después la letra del titular.
_ESCALONES_HORIZONTAL = ((118, 134, 50), (112, 96, 50), (112, 70, 50), (104, 64, 50),
                         (96, 60, 48), (88, 56, 48), (80, 52, 46), (72, 50, 44),
                         (64, 48, 42), (56, 46, 40), (PLACA_TITULAR_MIN, 44, 38))


def _arriba_horizontal(volanta: str, titular: str, f: dict, fin_marca: int,
                       escalon: tuple) -> tuple:
    """Volanta + titular (hasta 3 renglones) para material HORIZONTAL, colgados de la marca."""
    ancho = 1080 - PLACA_MX - PLACA_MX_DER
    t, gap, v = escalon
    t = min(t, int(_num("REEL_PLACA_TITULAR_TAM", PLACA_TITULAR_TAM)))
    v = min(v, int(_num("REEL_PLACA_VOLANTA_TAM", PLACA_VOLANTA_TAM)))
    tit = _titulo_en(titular, f, ancho, PLACA_TITULAR_RENGLONES, t)
    vol = _volanta_renglon(volanta, f["f_r"], ancho, _tope_volanta(v, tit[0]), f["p_v"])
    return _volanta_y_titulo(vol, tit, f, fin_marca + min(gap, PLACA_GAP_MARCA))


def _bajada_abajo(resumen: str, desde: int, hasta: int, f: dict) -> tuple:
    """La BAJADA debajo de una imagen apaisada, con el tope de sus mayúsculas en `desde` y sin
    pasar de `hasta` (donde las apps ponen sus botones). Siempre cierra en punto: oraciones
    enteras, o cortada donde la frase respira. Prefiere DOS renglones con letra grande.
    Devuelve `(bloques, renglones)`; vacío si no entra de ninguna manera."""
    resumen = " ".join((resumen or "").split())
    if not resumen or hasta - desde < 40:
        return [], []
    ancho = 1080 - PLACA_MX - PLACA_MX_DER
    tmax = int(_num("REEL_PLACA_BAJADA_TAM", PLACA_BAJADA_TAM))

    def armar(c: int, lineas: list):
        asc, may = _metricas(f["f_r"], c, f["p_r"])
        salto = round(c * PLACA_BAJADA_SALTO)
        bl0 = desde + may
        fin = bl0 + (len(lineas) - 1) * salto + _pie_de_tinta(lineas[-1], f["f_r"], c, f["p_r"])
        if fin > hasta:
            return None
        return [(l, c, bl0 + i * salto - asc, f["f_r"], f["p_r"], BLANCO, False)
                for i, l in enumerate(lineas)]

    for maximo, tmin in ((2, 50), (PLACA_BAJADA_RENGLONES, PLACA_BAJADA_MIN),
                         (2, PLACA_BAJADA_MIN), (1, PLACA_BAJADA_MIN)):
        for c in range(tmax, tmin - 1, -2):
            lineas = _texto_cerrado(resumen, f["f_r"], c, ancho, maximo, f["p_r"])
            if lineas:
                # Primer renglón LLENO, salvo que el último quede con una palabra sola.
                if len(lineas) > 1 and len(lineas[-1].split()) < 2:
                    parejo = _emparejar(" ".join(lineas), f["f_r"], c, ancho, len(lineas),
                                        f["p_r"])
                    if parejo and not parejo[-1].endswith("…"):
                        lineas = parejo
                b = armar(c, lineas)
                if b:
                    return b, lineas
    for maximo in (2, 1):
        lineas = _recorte_limpio(resumen, f["f_r"], PLACA_BAJADA_MIN, ancho, maximo, f["p_r"])
        b = armar(PLACA_BAJADA_MIN, lineas) if lineas else None
        if b:
            return b, lineas
    return [], []


def _estilo_cajas() -> str:
    """Ver `PLACA_CAJAS`."""
    e = _cfg("REEL_PLACA_CAJAS", PLACA_CAJAS).strip().lower()
    return e if e in ("grafito", "blanca", "sombra") else PLACA_CAJAS


def _texto_encima(volanta: str, titular: str, f: dict, desde: int | None = None) -> dict:
    """Volanta + titular ENCIMA de un material a pantalla completa, en cajas (ver
    `PLACA_CAJAS`): la volanta en UN renglón y el titular en DOS (tres solo si no entra).

    Sin `desde`, el bloque se APOYA abajo: el pie de la caja del titular queda a
    `PLACA_CAJA_PISO` del borde, arriba del texto del posteo y los botones de las apps. Con
    `desde`, CUELGA de esa altura (arriba, debajo de la marca: se usa cuando abajo hay una
    cara). Devuelve `cajas`, `bloques`, `halo` (lo que va con sombra en la letra), `banda`
    (dónde va el sombreado del estilo «sombra»), `rect` (lo que ocupa todo), y el texto:
    `titulo` (renglones) y `volanta`."""
    estilo = _estilo_cajas()
    caja_mx, texto_x = PLACA_CAJA_MX, PLACA_MX
    naranja, carbon = NARANJA, GRAFITO
    campo = False                                # margen de siempre (ver `_x_renglon`)
    pad_x = texto_x - caja_mx
    ancho = PLACA_CAJA_DER - pad_x - texto_x
    tope = int(_num("REEL_PLACA_CAJA_TITULAR_TAM", PLACA_CAJA_TITULAR_TAM))
    tit = (0, [])
    for maximo, minimo in ((2, PLACA_CAJA_TITULAR_MIN), (3, PLACA_CAJA_TITULAR_MIN3)):
        tit = _titulo_en(titular, f, ancho, maximo, tope, minimo, mayor=True)
        if not titular or (tit[1] and not tit[1][-1].endswith("…")):
            break
    c, lineas = tit
    vtope = (min(PLACA_CAJA_VOLANTA_TAM, max(PLACA_VOLANTA_MIN, round(c * 0.66)))
             if c else PLACA_CAJA_VOLANTA_TAM)
    v, vtxt = _volanta_renglon(volanta, f["f_r"], ancho, vtope, f["p_v"])

    # Altos: el mismo aire arriba de las mayúsculas que debajo de la línea base, que es lo
    # que el ojo lee como «centrado» (la cola de la «g» cae dentro de ese aire).
    alto_t = alto_v = 0
    if lineas:
        asc_t, may_t = _metricas(f["f_t"], c, f["p_t"])
        salto = round(c * PLACA_CAJA_SALTO)
        pad_t = round(c * 0.40)
        alto_t = 2 * pad_t + may_t + (len(lineas) - 1) * salto
    if vtxt:
        asc_v, may_v = _metricas(f["f_r"], v, f["p_v"])
        pad_v = round(v * 0.46)
        alto_v = 2 * pad_v + may_v
    if desde is None:
        y_t = 1920 - int(_num("REEL_PLACA_CAJA_PISO", PLACA_CAJA_PISO)) - alto_t
        y_v = y_t - alto_v
    else:
        y_v = desde
        y_t = y_v + alto_v

    caja_t = carbon[:3] + (round(255 * _num("REEL_PLACA_CAJA_OPACIDAD",
                                               PLACA_CAJA_OPACIDAD)),)
    color_t, color_v = BLANCO, BLANCO
    if estilo == "blanca":
        caja_t, color_t = (255, 255, 255, 255), carbon
    elif estilo == "sombra":
        color_v = naranja
    cajas, bloques = [], []
    x1_max = caja_mx
    if vtxt:
        bl = y_v + pad_v + may_v
        bloques.append((vtxt, v, bl - asc_v, f["f_r"], f["p_v"], color_v, campo))
        x1 = texto_x + _ancho_texto(vtxt, f["f_r"], v, f["p_v"]) + pad_x
        x1_max = max(x1_max, x1)
        if estilo != "sombra":
            cajas.append((caja_mx, y_v, x1, y_v + alto_v, naranja))
    if lineas:
        largo = max(_ancho_texto(l, f["f_t"], c, f["p_t"]) for l in lineas)
        x1 = texto_x + largo + pad_x
        x1_max = max(x1_max, x1)
        for i, l in enumerate(lineas):
            bl = y_t + pad_t + may_t + i * salto
            bloques.append((l, c, bl - asc_t, f["f_t"], f["p_t"], color_t, campo))
        if estilo != "sombra":
            cajas.append((caja_mx, y_t, x1, y_t + alto_t, caja_t))
    rect = (caja_mx, y_v if vtxt else y_t, x1_max, y_t + alto_t)
    return dict(cajas=cajas, bloques=bloques, rect=rect, titulo=lineas, volanta=vtxt,
                halo=list(bloques) if estilo == "sombra" else [],
                banda=(rect[1], rect[3]) if (estilo == "sombra" and bloques) else None)


def _caras_en_cuadro(caras, w: int, h: int, media: tuple, cover: bool) -> list:
    """Las `caras` (x, y, ancho, alto en píxeles del material) llevadas al cuadro del reel,
    con la CABEZA entera (la caja del detector es la cara pelada: se le suma frente, pelo y
    un poco de cuello, como en `_ventana_caras`)."""
    if not caras:
        return []
    x, y, wm, hm = media
    if cover:
        nw, nh, cx, cy = _ventana_caras(caras, w, h, wm, hm, arriba_primero=True)
        esc, dx, dy = nw / max(1, w), x - cx, y - cy
    else:
        esc, dx, dy = wm / max(1, w), x, y
    out = []
    for fx, fy, fw, fh in caras:
        fx, fy, fw, fh = fx * esc + dx, fy * esc + dy, fw * esc, fh * esc
        out.append((fx - fw * 0.15, fy - fh * 0.7, fx + fw * 1.15, fy + fh * 1.3))
    return out


def _cuanto_tapa(rect: tuple, cabezas: list) -> float:
    """Qué fracción de la cabeza más tapada queda debajo de `rect`."""
    peor = 0.0
    for c in cabezas:
        ix = max(0.0, min(rect[2], c[2]) - max(rect[0], c[0]))
        iy = max(0.0, min(rect[3], c[3]) - max(rect[1], c[1]))
        area = max(1.0, (c[2] - c[0]) * (c[3] - c[1]))
        peor = max(peor, ix * iy / area)
    return peor


def _marca_corr(f: dict) -> list:
    """El encabezado de los reels de WhatsApp: «Diario La Campaña | Radio del Centro» (29,
    blanco, tope en Y 130) y «@diarioyradio» (26, #F0F0F0, Y 172), en X 100. Va una sola vez
    sobre todo el cuadro; el isologo lo pega ffmpeg (`CORR_LOGO`)."""
    nombres = " | ".join(l.strip() for l in
                         _cfg("REEL_PLACA_MARCA_TEXTO", PLACA_MARCA_TEXTO).split("|")
                         if l.strip())
    usuario = _cfg("REEL_MARCA_USUARIO", MARCA_USUARIO)
    bloques = []
    for (y, tam), txt, color in zip(CORR_MARCA, (nombres, usuario), (BLANCO, CORR_BLANCO2)):
        if txt:
            asc, may = _metricas(f["f_r"], tam, "400")
            bloques.append((txt, tam, y - asc + may, f["f_r"], "400", color, CORR_X))
    return bloques


def _volanta_corr(volanta: str, f: dict) -> tuple:
    """(cuerpo, texto) de la volanta: MAYÚSCULAS breves, un renglón que entre en la caja."""
    texto = " ".join((volanta or "").split()).upper()
    if not texto:
        return 0, ""
    tam, _alto, peso = CORR_VOLANTA
    ancho = CORR_CAJA_ANCHO - 40
    for c in range(tam, CORR_VOLANTA_MIN - 1, -1):
        if _ancho_texto(texto, f["f_r"], c, peso) <= ancho:
            return c, texto
    palabras = texto.split()
    while len(palabras) > 1 and _ancho_texto(" ".join(palabras), f["f_r"], CORR_VOLANTA_MIN,
                                             peso) > ancho:
        palabras.pop()
    return CORR_VOLANTA_MIN, " ".join(palabras).rstrip(",;:·-–— ")


def _tarjeta_corr(volanta: str, titular: str, f: dict, y: int) -> dict:
    """Una TARJETA del prompt del usuario, con la volanta arriba en `y`:
      · volanta: caja naranja #B35B18 opaca de 56 px de alto, ancho = texto + 40, mayúsculas
        de 30 px en blanco, centradas;
      · 12 px más abajo, la caja principal: grafito #202226 al 94 %, 784 px de ancho (X 100 a
        884), relleno de 26 a los costados y 20 arriba y abajo, el titular en 54 px peso 400,
        hasta dos renglones, cada renglón centrado sobre el eje X 492 y el bloque centrado en el
        alto. Si no entra en dos renglones se achica; recién por debajo de 42, va en tres.
    Esquinas rectas, sin borde. Devuelve `cajas`, `bloques`, `rect`, `titulo` y `volanta`."""
    pad_x, pad_y = CORR_CAJA_PAD
    cajas, bloques, y0 = [], [], y
    v, vtxt = _volanta_corr(volanta, f)
    if vtxt:
        _t, valto, vpeso = CORR_VOLANTA
        vw = min(CORR_CAJA_ANCHO, round(_ancho_texto(vtxt, f["f_r"], v, vpeso)) + 40)
        cajas.append((CORR_X, y, CORR_X + vw, y + valto, CORR_NARANJA_CAJA))
        asc, may = _metricas(f["f_r"], v, vpeso)
        tope = y + (valto - may) / 2
        bloques.append((vtxt, v, round(tope - asc + may), f["f_r"], vpeso, BLANCO,
                        ("centro", CORR_X + vw // 2)))
        y += valto + CORR_GAP
    tam, minimo, peso = CORR_TITULAR
    ft = dict(f, p_t=peso)
    c, lineas = 0, []
    if titular:
        for maximo, piso in ((2, minimo), (3, 34)):
            c, lineas = _titulo_en(titular, ft, CORR_CAJA_ANCHO - 2 * pad_x, maximo, tam, piso,
                                   mayor=True)
            if lineas and not lineas[-1].endswith("…"):
                break
    if lineas:
        salto = round(c * CORR_SALTO)
        alto = 2 * pad_y + len(lineas) * salto
        cajas.append((CORR_X, y, CORR_X + CORR_CAJA_ANCHO, y + alto,
                      CORR_GRAFITO[:3] + (round(255 * CORR_CAJA_OPACIDAD),)))
        asc, may = _metricas(f["f_t"], c, peso)
        tope = y + (alto - ((len(lineas) - 1) * salto + may)) / 2
        for i, l in enumerate(lineas):
            bloques.append((l, c, round(tope + i * salto - asc + may), f["f_t"], peso, BLANCO,
                            ("centro", CORR_X + CORR_CAJA_ANCHO // 2)))
        y += alto
    return dict(cajas=cajas, bloques=bloques, rect=(CORR_X, y0, CORR_X + CORR_CAJA_ANCHO, y),
                titulo=lineas, volanta=vtxt)


def _plan_corr(volanta: str, titular: str, f: dict, w: int, h: int, grafica: bool, caras,
               alto: int = 0) -> dict:
    """Un cuadro de un reel de lo que llega por WhatsApp (ver `CORR_NARANJA`), foto o video.

    · VERTICAL o cuadrado: a sangre en 1080x1920 (se recorta buscando a los sujetos) con el
      encabezado arriba —sombreado mínimo y halo— y la tarjeta abajo, en Y 1058; si tapa una
      cara, sube a Y 360.
    · APAISADO: ENTERO, a todo el ancho, en un cuadro 1080x1350 (`alto` lo fuerza: en un pase
      de fotos con alguna vertical, todas van en 1920), con la tarjeta debajo de la imagen y el
      conjunto centrado en el alto. Si no hay lugar debajo (una casi cuadrada), la tarjeta va
      sobre la parte de abajo de la imagen.
    · AFICHE vertical: entero, con solo la marca, como siempre.
    `lienzo` del plan dice el tamaño del cuadro."""
    forma = forma_de(w, h, grafica)
    w, h = max(1, w), max(1, h)
    marca = _marca_corr(f)
    plan = dict(forma=forma, bloques=list(marca), bajada=[], cover=False, fundido=(0, 0),
                sombra_arriba=False, grafica=grafica, estilo="corresponsal", logo=CORR_LOGO,
                halo=[], cajas=[], titulo=[], volanta="", lienzo=(1080, 1920),
                texto=(CORR_X, CORR_MARCA[0][0], CORR_X + CORR_CAJA_ANCHO, CORR_MARCA[1][0] + 30))
    if forma == "afiche":
        esc = min(1080 / w, 1920 / h)
        aw, ah = max(2, round(w * esc)) // 2 * 2, max(2, round(h * esc)) // 2 * 2
        y = max(0, min(PLACA_AFICHE_TOPE, 1920 - ah))
        plan.update(media=((1080 - aw) // 2, y, aw, ah), sombra_arriba=True, halo=list(marca))
        return plan
    if w / h > CORR_AR_APAISADO:
        lienzo = alto or CORR_ALTO_APAISADO
        am = round(1080 * h / w) // 2 * 2
        alto_t = _tarjeta_corr(volanta, titular, f, 0)["rect"][3]
        if lienzo >= 1920:
            # En 9:16 (un pase con alguna vertical): la tarjeta en su lugar de siempre y la
            # imagen justo arriba.
            y_img = max(CORR_TOPE_IMAGEN, CORR_Y_ABAJO - 24 - am)
            y_t = max(CORR_Y_ABAJO, y_img + am + 24)
        else:
            libre = lienzo - 40 - CORR_TOPE_IMAGEN
            if am + 24 + alto_t <= libre:
                y_img = CORR_TOPE_IMAGEN + (libre - am - 24 - alto_t) // 2
                y_t = y_img + am + 24
            else:
                y_img = CORR_TOPE_IMAGEN
                y_t = lienzo - 40 - alto_t
        t = _tarjeta_corr(volanta, titular, f, y_t)
        plan.update(media=(0, y_img, 1080, am), lienzo=(1080, lienzo), cajas=t["cajas"],
                    bloques=marca + t["bloques"], titulo=t["titulo"], volanta=t["volanta"],
                    texto=t["rect"])
        return plan
    media = (0, 0, 1080, 1920)
    cover = abs(1080 * h / w - 1920) > 2
    t = _tarjeta_corr(volanta, titular, f, CORR_Y_ABAJO)
    cabezas = _caras_en_cuadro(caras, w, h, media, cover)
    if cabezas and _cuanto_tapa(t["rect"], cabezas) > 0.15:
        arriba = _tarjeta_corr(volanta, titular, f, CORR_Y_ARRIBA)
        if _cuanto_tapa(arriba["rect"], cabezas) < _cuanto_tapa(t["rect"], cabezas):
            logger.info("La tarjeta abajo tapaba una cara: va arriba (Y 360).")
            t = arriba
    plan.update(media=media, cover=cover, sombra_arriba=True, halo=list(marca),
                cajas=t["cajas"], bloques=marca + t["bloques"], titulo=t["titulo"],
                volanta=t["volanta"], texto=t["rect"])
    return plan


def plan_placa(volanta: str, titular: str, resumen: str, w: int, h: int, *,
               grafica: bool = False, modo_texto: str = "", caras=None,
               estilo: str = "", alto: int = 0) -> dict:
    """TODO lo que va en un cuadro del reel estilo placa, según la FORMA del material
    (pedido del usuario 2026-09-26; ver `PLACA_VERTICAL_AR`):

      · «vertical»: marca + volanta (1 renglón) + titular (2 renglones) arriba, SIN bajada, y
        la imagen en los 3/4 de abajo a todo el ancho (`cover`: se recorta, desde ABAJO);
      · «horizontal»: marca + volanta + titular (hasta 3 renglones) arriba, la imagen entera a
        todo el ancho y la BAJADA debajo de ella;
      · «afiche» (gráfica vertical): el afiche ocupa todo el cuadro y encima van solo la marca
        (y el isologo, que pega ffmpeg) con un sombreado mínimo;
      · «pantalla» (9:16, 2:3; 2026-09-27): el material ENTERO a todo el cuadro, sin franja de
        arriba, y la volanta y el titular encima, en cajas (`_texto_encima`), abajo. Si ahí
        hay una de las `caras` (x, y, ancho, alto en píxeles del material) y arriba no, el
        bloque sube debajo de la marca.

    `modo_texto` fuerza el bloque de arriba: en un reel de VARIAS fotos, si alguna es
    vertical, todas usan el bloque vertical para que el texto no salte entre foto y foto (una
    «pantalla» va entonces entera, más angosta, en los 3/4 de abajo).

    Devuelve un dict con `forma`, `bloques` (todo el texto), `bajada` (sus renglones),
    `media=(x, y, ancho, alto)` (dónde va la imagen), `cover` (si hay que recortarla para
    llenar), `fundido=(arriba, abajo)`, `sombra_arriba` y `grafica`; en «pantalla», además,
    `cajas`, `halo`, `banda`, `titulo`, `volanta` y `texto` (el rectángulo que ocupa).

    `estilo="corresponsal"` (lo que llega por WhatsApp, 2026-10-03): `_plan_corr`, igual para
    fotos y videos; `alto` fuerza el alto del cuadro en un pase de fotos (ver ahí)."""
    f = _fuentes_placa()
    if estilo == "corresponsal":
        return _plan_corr(volanta, titular, f, w, h, grafica, caras, alto)
    forma = forma_de(w, h, grafica)
    modo = modo_texto or forma
    marca, fin_marca = _marca_bloques(f)
    plan = dict(forma=forma, bloques=list(marca), bajada=[], cover=False, fundido=(0, 0),
                sombra_arriba=False, grafica=grafica)
    w, h = max(1, w), max(1, h)

    if forma == "afiche":
        esc = min(1080 / w, 1920 / h)
        aw, ah = max(2, round(w * esc)), max(2, round(h * esc))
        aw, ah = aw - aw % 2, ah - ah % 2
        # Si no llena el alto, arranca debajo de la marca; si lo llena, la marca va encima
        # con el sombreado.
        y = max(0, min(PLACA_AFICHE_TOPE, 1920 - ah))
        plan.update(media=((1080 - aw) // 2, y, aw, ah), sombra_arriba=True)
        return plan

    if forma == "pantalla" and modo != "vertical":
        return _plan_pantalla(plan, volanta, titular, f, fin_marca, w, h, caras)

    techo = 1920 - round(1920 * PLACA_VERTICAL_MEDIA)
    if modo == "vertical":
        arriba, tinta = _arriba_vertical(volanta, titular, f, fin_marca, techo)

    if forma == "pantalla":
        # En un reel de varias fotos con alguna 3:4: el texto va arriba como en las demás, y
        # esta va ENTERA en los 3/4 de abajo, más angosta, con humo a los costados.
        plan["bloques"] += arriba
        y = max(techo, tinta + PLACA_AIRE_IMG)
        alto = 1920 - y
        aw = min(1080, round(w * alto / h))
        aw -= aw % 2
        plan.update(media=((1080 - aw) // 2, y, aw, alto),
                    fundido=(int(_num("REEL_PLACA_VERTICAL_FUNDIDO", PLACA_VERTICAL_FUNDIDO)), 0))
        return plan

    if forma == "vertical":
        plan["bloques"] += arriba
        y = max(techo, tinta + PLACA_AIRE_IMG)
        alto = 1920 - y
        plan.update(media=(0, y, 1080, alto), cover=True,
                    fundido=(int(_num("REEL_PLACA_VERTICAL_FUNDIDO", PLACA_VERTICAL_FUNDIDO)), 0))
        return plan

    # HORIZONTAL: la imagen entera a todo el ancho y la bajada debajo. Si no entra todo, se
    # prueba con el texto de arriba más compacto; después, recortando la imagen de alto (hasta
    # 20%, sin tocar la parte de arriba); y por último, sin bajada.
    margen = PLACA_GRAFICA_MX if grafica else 0
    aw0 = 1080 - 2 * margen
    natural = round(aw0 * h / w)
    hasta = 1920 - BANDA_SEGURO
    escalones = [None] if modo == "vertical" else list(_ESCALONES_HORIZONTAL)
    # Cómo se le hace lugar a la bajada: a una FOTO se le recorta alto (desde abajo, hasta un
    # 20%); a un AFICHE no se le puede recortar nada, así que se lo ACHICA entero.
    factores = (1.0, 0.9, 0.8, 0.7) if grafica else (1.0, 0.9, 0.8)
    elegido = None
    for esc in escalones:
        if esc is not None:
            arriba, tinta = _arriba_horizontal(volanta, titular, f, fin_marca, esc)
        y = tinta + PLACA_AIRE_IMG + margen
        for factor in factores:
            am = max(2, round(natural * factor))
            am -= am % 2
            aw = (max(2, round(aw0 * factor)) // 2 * 2) if grafica else aw0
            bajada, lineas = _bajada_abajo(resumen, y + am + PLACA_BAJADA_AIRE, hasta, f)
            if (lineas or not resumen) and y + am <= 1920:
                elegido = (arriba, y, aw, am, bajada, lineas)
                break
        if elegido:
            break
    if elegido is None:
        arriba, tinta = (_arriba_horizontal(volanta, titular, f, fin_marca, escalones[0])
                         if escalones[0] is not None else (arriba, tinta))
        y = tinta + PLACA_AIRE_IMG + margen
        am = min(natural if grafica else round(natural * 0.8), 1920 - y)
        am -= am % 2
        aw = (max(2, round(aw0 * am / max(1, natural))) // 2 * 2) if grafica else aw0
        elegido = (arriba, y, aw, am, [], [])
        if resumen:
            logger.info("La bajada no entra debajo de la imagen: el reel va sin bajada.")
    arriba, y, aw, am, bajada, lineas = elegido
    plan["bloques"] += arriba + bajada
    plan["bajada"] = lineas
    # El sombreado de abajo solo si la imagen no llega al pie del cuadro.
    abajo = int(_num("REEL_PLACA_FUNDIDO_ABAJO", PLACA_FUNDIDO_ABAJO)) if y + am < 1918 else 0
    plan.update(media=((1080 - aw) // 2, y, aw, am),
                # Recorte solo si le falta alto de verdad (el redondeo a par no cuenta).
                cover=(not grafica) and am < natural - 2,
                fundido=(0, 0) if grafica else
                (int(_num("REEL_PLACA_FUNDIDO", PLACA_FUNDIDO)), abajo))
    return plan


def _plan_pantalla(plan: dict, volanta: str, titular: str, f: dict, fin_marca: int,
                   w: int, h: int, caras) -> dict:
    """La forma «pantalla» de `plan_placa` (pedido del usuario 2026-09-27): ver
    `PLACA_PANTALLA_AR`."""
    tol = _num("REEL_PLACA_PANTALLA_TOLERANCIA", PLACA_PANTALLA_TOLERANCIA)
    alto_natural = 1080 * h / w
    if alto_natural >= 1920 * (1 - tol) and alto_natural <= 1920 * (1 + tol):
        # 9:16 o casi: llena el cuadro; lo poco que sobra se va por los costados o por abajo.
        media, cover = (0, 0, 1080, 1920), abs(alto_natural - 1920) > 2
    elif alto_natural > 1920:
        # Más alto que 9:16: entero, tocando arriba y abajo, con humo a los costados.
        aw = round(w * 1920 / h)
        aw -= aw % 2
        media, cover = ((1080 - aw) // 2, 0, aw, 1920), False
    else:
        # Un 2:3: entero a todo el ancho, apoyado abajo. Arriba queda humo con la marca.
        am = round(alto_natural)
        am -= am % 2
        media, cover = (0, 1920 - am, 1080, am), False
    fundido = (int(_num("REEL_PLACA_VERTICAL_FUNDIDO", PLACA_VERTICAL_FUNDIDO))
               if media[1] > 0 else 0, 0)
    texto = _texto_encima(volanta, titular, f)
    cabezas = _caras_en_cuadro(caras, w, h, media, cover)
    if cabezas and _cuanto_tapa(texto["rect"], cabezas) > 0.15:
        # Abajo tapa una cara: se prueba colgado debajo de la marca (y del isologo).
        caja_logo = _logo_caja()
        desde = max(fin_marca + 56, (caja_logo[3] + 24) if caja_logo else 0)
        arriba = _texto_encima(volanta, titular, f, desde=desde)
        if _cuanto_tapa(arriba["rect"], cabezas) < _cuanto_tapa(texto["rect"], cabezas):
            logger.info("El titular abajo tapaba una cara: va arriba, debajo de la marca.")
            texto = arriba
    marca = list(plan["bloques"])            # hasta acá, solo los renglones de marca
    plan["bloques"] = marca + texto["bloques"]
    plan.update(media=media, cover=cover, fundido=fundido, sombra_arriba=True,
                cajas=texto["cajas"], halo=marca + texto["halo"],
                banda=texto["banda"], titulo=texto["titulo"], volanta=texto["volanta"],
                texto=texto["rect"])
    return plan


def placa_layout(volanta: str, titular: str, resumen: str, w: int = 1920, h: int = 1080,
                 **kw) -> dict:
    """Compatibilidad: el texto de la placa para un material de `w`x`h` (ver `plan_placa`).
    Devuelve `{bloques, y_img, bajada}` como antes."""
    plan = plan_placa(volanta, titular, resumen, w, h, **kw)
    return dict(bloques=plan["bloques"], y_img=plan["media"][1], bajada=plan["bajada"],
                plan=plan)


def placa_png(plan: dict, salida) -> Path | None:
    """Dibuja el texto de un `plan_placa` (y el sombreado de arriba de un afiche) en un PNG
    transparente de 1080x1920, para superponerlo con `overlay`.

    Va como imagen y no con `drawtext` por lo de siempre: al ffmpeg de Linux de la nube le
    falta libfreetype. Con `overlay` anda igual acá que allá."""
    if not plan.get("bloques"):
        return None
    try:
        from PIL import Image, ImageDraw
        lienzo = Image.new("RGBA", tuple(plan.get("lienzo") or (1080, 1920)), (0, 0, 0, 0))
        pintar_placa(lienzo, plan)
        salida = Path(salida)
        salida.parent.mkdir(parents=True, exist_ok=True)
        lienzo.save(salida, "PNG")
    except Exception as e:                                       # noqa: BLE001
        logger.warning(f"No pude dibujar la placa del reel ({e}); va sin texto.")
        return None
    logger.info(f"Placa del reel ({plan['forma']}): {len(plan['bloques'])} renglón/es · la "
                f"imagen va en {plan['media']}")
    return salida


def pintar_placa(lienzo, plan: dict) -> None:
    """Pinta sobre `lienzo` (RGB o RGBA) todo lo de un `plan_placa` que va encima de la
    imagen: los sombreados, el halo de la letra, las cajas y el texto, en ese orden."""
    from PIL import Image, ImageDraw
    if plan.get("sombra_arriba"):
        sombrear_arriba(lienzo)
    if plan.get("banda"):
        _sombrear_banda(lienzo, *plan["banda"])
    if plan.get("halo"):
        _halo(lienzo, plan["halo"])
    if plan.get("cajas"):
        capa = Image.new("RGBA", lienzo.size, (0, 0, 0, 0))
        dib = ImageDraw.Draw(capa)
        for x0, y0, x1, y1, color in plan["cajas"]:
            dib.rectangle((x0, y0, x1 - 1, y1 - 1), fill=tuple(color))
        _componer(lienzo, capa)
    dibujar_bloques(ImageDraw.Draw(lienzo), plan["bloques"])


def _componer(lienzo, capa) -> None:
    """Pega una capa RGBA sobre un lienzo RGB o RGBA respetando su transparencia."""
    if lienzo.mode == "RGBA":
        lienzo.alpha_composite(capa)
    else:
        lienzo.paste(capa.convert("RGB"), (0, 0), capa.getchannel("A"))


def _halo(lienzo, bloques, fuerza: float = 0.75) -> None:
    """Una sombra difusa pegada a la letra: lo que hace que el texto sin caja se lea encima
    de un cielo o una pared blanca sin oscurecer la imagen entera (pedido 2026-09-27: «un
    leve sombreado para que contraste si el fondo es muy claro»). El difuminado sigue al
    cuerpo de cada renglón, así la marca chica no queda con una mancha de titular."""
    from PIL import Image, ImageChops, ImageDraw, ImageFilter
    alfa = Image.new("L", lienzo.size, 0)
    for cuerpo in sorted({b[1] for b in bloques}):
        capa = Image.new("RGBA", lienzo.size, (0, 0, 0, 0))
        dibujar_bloques(ImageDraw.Draw(capa), [b[:5] + ((0, 0, 0, 255),) + b[6:]
                                               for b in bloques if b[1] == cuerpo])
        a = capa.getchannel("A").filter(ImageFilter.GaussianBlur(max(3, round(cuerpo * 0.16))))
        alfa = ImageChops.lighter(alfa, a)
    alfa = alfa.point(lambda p: min(255, round(p * 2.2 * fuerza)))
    negro = Image.new("RGBA", lienzo.size, (0, 0, 0, 255))
    negro.putalpha(alfa)
    _componer(lienzo, negro)


def _sombrear_banda(lienzo, y0: int, y1: int, tope: float = 0.55, borde: int = 170) -> None:
    """Sombreado suave detrás del bloque de texto del estilo «sombra»: negro que llega a
    `tope` entre `y0` e `y1` y se desvanece `borde` px para arriba y para abajo."""
    from PIL import Image
    col = []
    for y in range(lienzo.height):
        if y < y0:
            k = _curva_suave(1 - (y0 - y) / borde)
        elif y > y1:
            k = _curva_suave(1 - (y - y1) / borde)
        else:
            k = 1.0
        col.append(round(255 * tope * k))
    alfa = Image.frombytes("L", (1, lienzo.height), bytes(col)).resize(lienzo.size, Image.NEAREST)
    negro = Image.new("RGBA", lienzo.size, (0, 0, 0, 255))
    negro.putalpha(alfa)
    _componer(lienzo, negro)


def sombrear_arriba(lienzo) -> None:
    """El sombreado MÍNIMO de arriba de un afiche (pedido 2026-09-26): negro que se desvanece
    hacia abajo, lo justo para que la marca y el isologo se lean encima de cualquier afiche.
    Se pinta sobre `lienzo` (RGB o RGBA)."""
    from PIL import Image
    alto = PLACA_AFICHE_SOMBRA_ALTO
    tope = _num("REEL_PLACA_AFICHE_SOMBRA", PLACA_AFICHE_SOMBRA)
    col = bytes(round(255 * tope * (1 - _curva_suave(y / alto))) for y in range(alto))
    alfa = Image.frombytes("L", (1, alto), col).resize((lienzo.width, alto), Image.NEAREST)
    negro = Image.new("RGBA", (lienzo.width, alto), (0, 0, 0, 255))
    negro.putalpha(alfa)
    if lienzo.mode == "RGBA":
        lienzo.alpha_composite(negro, (0, 0))
    else:
        lienzo.paste(negro.convert("RGB"), (0, 0), alfa)


def _x_renglon(campo) -> tuple:
    """(x, anchor, centrado) del 7º campo de un renglón: `True` = centrado en el cuadro (lo
    usaba el estilo anterior), `("centro", x)` = centrado sobre el eje x (las cajas de los
    corresponsales), un NÚMERO = la x de su margen izquierdo, `False` = `PLACA_MX`."""
    if campo is True:
        return 540, "ma", True
    if isinstance(campo, tuple) and campo and campo[0] == "centro":
        return int(campo[1]), "ma", True
    if isinstance(campo, (int, float)) and not isinstance(campo, bool) and campo:
        return int(campo), "la", False
    return PLACA_MX, "la", False


def dibujar_bloques(dib, bloques) -> None:
    """Dibuja los renglones que arma `plan_placa` sobre un ImageDraw."""
    for texto, cuerpo, y, fuente, peso, color, campo in bloques:
        f = _tipo(fuente, cuerpo, peso)
        # `anchor="la"`: la `y` es el ascendente del renglón. Centrado ancla por el medio y la
        # x pasa a ser el centro.
        x, anchor, centrado = _x_renglon(campo)
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


def fundido_png(alto: int, salida, *, arriba: int = 0, abajo: int = 0,
                ancho: int = 1080) -> Path | None:
    """Máscara en escala de grises para fundir los BORDES de la imagen con el fondo.

    Negro (transparente) → blanco (opaco) en `arriba` px por el borde de arriba y `abajo`
    px por el de abajo (el SOMBREADO de una imagen apaisada que no llega al pie), con curva
    suave. `alphamerge` la usa como canal alfa de la imagen, y así el corte deja de ser una
    línea recta: la foto se DISUELVE en el fondo en vez de terminar de golpe. Los valores
    salen de `plan_placa`. `ancho` es el de la imagen: la máscara tiene que medir lo mismo o
    `alphamerge` se queja de que no coinciden."""
    try:
        m = mascara_fundido(ancho, alto, arriba=arriba, abajo=abajo)
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


def _norm(idx: int, fps: int, alto: int = 1920) -> str:
    # Escala/encuadra cada imagen a 1080x`alto` exactas y fija sar/fps para xfade.
    return (f"[{idx}:v]scale=1080:{alto}:force_original_aspect_ratio=decrease,"
            f"pad=1080:{alto}:(ow-iw)/2:(oh-ih)/2:white,setsar=1,fps={fps}[s{idx}]")


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
                   fundido: int = 0, arriba_primero: bool = False) -> tuple:
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
    que `story_image._encuadrar`).

    `arriba_primero` (reel estilo placa, 2026-09-26): lo que sobra de alto se saca de ABAJO.
    El usuario vio que se estaban cortando las cabezas: con el sesgo de siempre se tiraba un
    30% del sobrante por arriba y el detector de caras no siempre las encuentra. Ahora el
    recorte arranca en el borde de arriba del material, y solo baja lo justo si hay una cara
    tan abajo que si no, quedaría afuera."""
    escala = max(W / max(1, cont_w), H / max(1, cont_h))
    nw = max(W, int(round(cont_w * escala)))
    nh = max(H, int(round(cont_h * escala)))
    x = (nw - W) // 2
    y = 0 if arriba_primero else max(0, min(int((nh - H) * 0.30), nh - H))
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
    ideal = 0 if arriba_primero else arriba - max(H * 0.14, fundido * 0.75 + aire)
    y = _entre(ideal, aba - H, arriba - aire, 0, nh - H)
    logger.info(f"Encuadre: {len(cajas)} cara(s) → recorte x={x} y={y}, con las caras "
                f"enteras y aire arriba de la cabeza.")
    return nw, nh, x, y


def _encuadre_fullbleed(src: Path, cont_w: int, cont_h: int, recorte, work_dir: Path,
                        *, alto: int = 1920, fundido: int = 0, arriba_primero: bool = False,
                        caras=None):
    """Devuelve (nw, nh, x, y): a cuánto escalar el VIDEO para LLENAR 1080x`alto` y desde
    dónde recortarlo, ENCUADRADO EN EL SUJETO.

    Las caras salen de `_caras_de_video` (o vienen ya buscadas en `caras`); el recorte lo
    decide `_ventana_caras`."""
    W, H = 1080, alto          # `alto` < 1920 cuando el reel lleva el texto arriba
    if caras is None:
        caras = _caras_de_video(src, recorte, work_dir)
    return _ventana_caras(caras, cont_w, cont_h, W, H, fundido=fundido,
                          arriba_primero=arriba_primero)


def _caras_de_video(src: Path, recorte, work_dir: Path) -> list:
    """Busca caras en 3 fotogramas (reusa el detector de las placas) y se queda con el
    fotograma más representativo (el de mayor superficie de caras). En píxeles del contenido
    real (sin las barras negras de `recorte`). Ante cualquier error, ninguna."""
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
            logger.info("Encuadre: sin caras (paisaje/objeto).")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"No pude buscar las caras del video ({e}).")
        mejor = []
    return mejor


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
                capa_texto: Path | None = None,
                logo_geo: tuple | None = None,
                alto: int = 1920) -> None:
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
    # `alto`: el del CUADRO. 1920 salvo los reels apaisados de los corresponsales (1350).
    mh = texto_placa[3] if texto_placa else alto        # alto REAL de la imagen
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
            relleno = (f"scale=1080:{alto},drawbox=x=0:y=0:w=1080:h={alto}:"
                       f"color={_color_fondo(color_fondo)}@1:t=fill")
        else:
            # Misma historia que `drawtext` (2026-09-17): no todas las builds traen todo, y la
            # de Linux de la nube es más pelada que la de Windows. `drawbox` no depende de
            # ninguna librería externa, así que esto no debería pasar nunca — pero si pasa, el
            # reel sale con el fondo borroso de antes en vez de no salir.
            logger.warning("Este ffmpeg NO trae «drawbox»: el fondo de la placa va borroso.")
            relleno = (f"scale=1080:{alto}:force_original_aspect_ratio=increase,"
                       f"crop=1080:{alto},boxblur=luma_radius=40:luma_power=1")
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
        vf = f"{pre}{v0}scale={nw}:{nh},setsar=1,crop=1080:{alto}:{cx}:{cy}[v]"
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
        vf += (f";[{idx}:v]scale=1080:{alto},format=rgba[fd];"
               f"{out_label}[fd]overlay=0:0[vfd]")
        out_label = "[vfd]"
    if logo_png:
        # Marca de agua: el isotipo arriba, debajo de la barra de la app.
        idx = n_in
        inputs += ["-i", str(logo_png)]
        n_in += 1
        # `logo_geo`: el estilo de los corresponsales lo lleva a 80 px de los bordes.
        ancho, mx, my = logo_geo or _logo_geo()
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
            vf += (f";[{idx}:v]scale=1080:{alto},format=rgba[mk];"
                   f"{out_label}[mk]overlay=0:0[vmk]")
            out_label = "[vmk]"
    if texto_placa:
        # Marca + volanta + titular + bajada, ya dibujados en un PNG transparente por
        # `placa_png`. Van DESPUÉS del logo para que nada los tape.
        idx = n_in
        inputs += ["-i", str(texto_placa[0])]
        n_in += 1
        vf += (f";[{idx}:v]scale=1080:{alto},format=rgba[pl];"
               f"{out_label}[pl]overlay=0:0[vpl]")
        out_label = "[vpl]"
    if capa_texto:
        # Reel de FOTOS: fondo e imagen ya vienen compuestos en cada cuadro; acá solo se le
        # pega el texto de arriba, una vez, para que quede QUIETO mientras las fotos pasan.
        idx = n_in
        inputs += ["-i", str(capa_texto)]
        n_in += 1
        vf += (f";[{idx}:v]scale=1080:{alto},format=rgba[ct];"
               f"{out_label}[ct]overlay=0:0[vct]")
        out_label = "[vct]"
    if overlay:
        # Marco del diario (esquinas + caja del zócalo + barra con la web y las redes).
        idx = n_in
        inputs += ["-i", str(overlay)]
        n_in += 1
        vf += (f";[{idx}:v]scale=1080:{alto},format=rgba[ov];"
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
    if alto == 1920:
        ajuste = ("scale=1080:1920:force_original_aspect_ratio=decrease,"
                  "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black")
    else:
        # Cuadro más bajo (el 4:5 de lo apaisado): la placa —fondo crema parejo— se RECORTA en
        # alto alrededor del logo, el @diarioyradio y las redes, en vez de achicarse con bandas.
        ajuste = f"scale=1080:-2,crop=1080:{alto}:0:(ih-{alto})*0.42"
    vf += (f";[{i_placa}:v]{ajuste},setsar=1,fps={fps},"
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
    # Cada maqueta se prueba en las formas que decide `plan_placa` (2026-09-26).
    FORMAS = (("horizontal 16:9", 1920, 1080, False), ("horizontal 4:3", 1600, 1200, False),
              ("vertical 3:4", 960, 1280, False), ("pantalla 9:16", 1080, 1920, False),
              ("pantalla 2:3", 1080, 1620, False), ("afiche 4:5", 1080, 1350, True))
    for nombre, vol, tit, res in MAQUETAS:
        for etiqueta, w, h, graf in FORMAS:
            fallas = []
            plan = plan_placa(vol, tit, res, w, h, grafica=graf)
            forma = plan["forma"]
            _x, y_media, wm, hm = plan["media"]
            for texto, cuerpo, y, fuente, peso, color, centrado in plan["bloques"]:
                # La caja de TINTA de verdad (no la del renglón, que incluye el ascendente
                # vacío por encima de las mayúsculas): lo que tapan las apps es lo que se ve.
                x, anchor, centrado = _x_renglon(centrado)
                bx0, by0, bx1, by1 = _tipo(fuente, cuerpo, peso).getbbox(texto, anchor=anchor)
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
                if (forma not in ("afiche", "pantalla") and color != GRIS
                        and y_media <= r[1] < y_media + hm):
                    if not (plan["bajada"] and texto in plan["bajada"]):
                        fallas.append(f"«{texto[:22]}» cae encima de la imagen")
            blancos = [b[0] for b in plan["bloques"] if b[5] == BLANCO]
            naranjas = [b[0] for b in plan["bloques"] if b[5] == NARANJA]
            titulo = [t for t in blancos if t not in plan["bajada"]]
            bajada = plan["bajada"]
            if forma == "pantalla":
                # Encima del material el color depende del estilo de caja: se cuenta lo que
                # dice el plan.
                titulo = list(plan["titulo"])
                naranjas = [plan["volanta"]] if plan["volanta"] else []
                blancos = titulo
                cx0, cy0, cx1, cy1 = plan["texto"]
                if cx1 > PLACA_CAJA_DER:
                    fallas.append(f"las cajas entran en la columna de botones (x={cx1})")
                if cy1 > 1920 - BANDA_SEGURO:
                    fallas.append(f"las cajas entran en la franja de abajo (y={cy1})")
                if caja_logo and not (cx1 <= caja_logo[0] or cx0 >= caja_logo[2]
                                      or cy1 <= caja_logo[1] or cy0 >= caja_logo[3]):
                    fallas.append("las cajas SE SUPERPONEN CON EL ISOLOGO")
            if vol and len(naranjas) != 1 and forma != "afiche":
                fallas.append(f"la volanta va en {len(naranjas)} renglones (tiene que ser 1)")
            if any(t.endswith("…") for t in titulo):
                fallas.append("el titular queda cortado con «…»")
            if forma == "horizontal":
                if len(titulo) > PLACA_TITULAR_RENGLONES:
                    fallas.append(f"el titular va en {len(titulo)} renglones (máximo 3)")
                if res and not bajada:
                    fallas.append("la bajada no entró debajo de la imagen")
                if bajada and bajada[-1][-1] not in ".!?»":
                    fallas.append(f"la bajada queda cortada: «…{bajada[-1][-24:]}»")
            elif forma == "pantalla":
                if bajada:
                    fallas.append("un reel a pantalla completa no lleva bajada")
                if len(titulo) > 3:
                    fallas.append(f"el titular va en {len(titulo)} renglones")
                if plan["cover"] or wm != 1080 or hm != round(1080 * h / w) // 2 * 2:
                    fallas.append(f"el material no va entero ({wm}x{hm}, "
                                  f"{'recortado' if plan['cover'] else 'sin recortar'})")
            elif forma == "vertical":
                if bajada:
                    fallas.append("un reel vertical no lleva bajada")
                if len(titulo) > PLACA_VERTICAL_TITULAR_RENGLONES + 1:
                    fallas.append(f"el titular va en {len(titulo)} renglones")
                if hm < 1440 * 0.95 or wm != 1080:
                    fallas.append(f"la imagen vertical ocupa {wm}x{hm} (tiene que ser 1080x1440)")
            else:
                if blancos or naranjas:
                    fallas.append("un afiche lleva solo la marca")
                if wm != 1080:
                    fallas.append(f"el afiche no llega a los márgenes ({wm} de ancho)")
            ok = ok and not fallas
            print(f"  {'OK  ' if not fallas else 'MAL '} {nombre} · {etiqueta}: "
                  f"«{forma}», {len(titulo)} renglón/es de titular, bajada {len(bajada)}, "
                  f"imagen {wm}x{hm} desde y={y_media}")
            for f in fallas:
                print(f"        → {f}")

    print("\n=== reels de WhatsApp (corresponsales: cajas y 4:5 para lo apaisado) ===")
    caja_logo_c = _logo_caja(CORR_LOGO)
    for nombre, vol, tit, res in MAQUETAS:
        for etiqueta, w, h, graf in (("vertical 9:16", 1080, 1920, False),
                                     ("vertical 3:4", 960, 1280, False),
                                     ("cuadrada", 1200, 1200, False),
                                     ("apaisada 16:9", 1920, 1080, False),
                                     ("apaisada 4:3", 1600, 1200, False),
                                     ("casi cuadrada 5:4", 1250, 1000, False),
                                     ("afiche 4:5", 1080, 1350, True)):
            fallas = []
            plan = plan_placa(vol, tit, res, w, h, grafica=graf, estilo="corresponsal")
            lw, lh = plan["lienzo"]
            piso = lh - (BANDA_SEGURO if lh >= 1920 else 30)
            for texto, cuerpo, y, fuente, peso, color, campo in plan["bloques"]:
                x, anchor, _c = _x_renglon(campo)
                bx0, by0, bx1, by1 = _tipo(fuente, cuerpo, peso).getbbox(texto, anchor=anchor)
                r = (x + bx0, y + by0, x + bx1, y + by1)
                if r[0] < CORR_X - 4 or r[2] > CORR_X + CORR_CAJA_ANCHO + 4:
                    fallas.append(f"«{texto[:22]}» se sale de X 100–884 ({r[0]:.0f}..{r[2]:.0f})")
                if r[3] > piso:
                    fallas.append(f"«{texto[:22]}» entra en la franja de abajo (y={r[3]})")
                if caja_logo_c and not (r[2] <= caja_logo_c[0] or r[0] >= caja_logo_c[2]
                                        or r[3] <= caja_logo_c[1] or r[1] >= caja_logo_c[3]):
                    fallas.append(f"«{texto[:22]}» SE SUPERPONE CON EL ISOLOGO")
            for x0, y0, x1, y1, _col in plan["cajas"]:
                if x0 < CORR_X or x1 > CORR_X + CORR_CAJA_ANCHO or y1 > piso:
                    fallas.append(f"una caja se sale de su zona ({x0},{y0})–({x1},{y1})")
            titulo = plan["titulo"]
            mx, my, mw, mh = plan["media"]
            if plan["forma"] == "afiche":
                if titulo or plan["cajas"]:
                    fallas.append("un afiche lleva solo la marca")
            else:
                if vol and (not plan["volanta"] or plan["volanta"] != plan["volanta"].upper()):
                    fallas.append(f"volanta mal: «{plan['volanta']}»")
                if len(titulo) > (3 if len(tit) > 100 else 2) or any(
                        l.endswith("…") for l in titulo):
                    fallas.append(f"titular mal: {titulo}")
                if plan["bajada"]:
                    fallas.append("no lleva bajada")
                if w > h:
                    if (lw, lh) != (1080, CORR_ALTO_APAISADO):
                        fallas.append(f"lo apaisado va en 1080x1350, no {lw}x{lh}")
                    if plan["cover"] or mw != 1080 or abs(mh - 1080 * h / w) > 2:
                        fallas.append(f"lo apaisado va ENTERO a todo el ancho ({mw}x{mh})")
                    if my < (caja_logo_c[3] if caja_logo_c else 0):
                        fallas.append(f"la imagen se mete debajo del isologo (y={my})")
                    alto_t = plan["texto"][3] - plan["texto"][1]
                    if (mh + 24 + alto_t <= lh - 40 - CORR_TOPE_IMAGEN
                            and plan["texto"][1] < my + mh):
                        fallas.append("la tarjeta tapa la imagen aunque había lugar debajo")
                elif (lw, lh) != (1080, 1920) or (mw, mh) != (1080, 1920):
                    fallas.append(f"lo vertical va a sangre en 1080x1920 ({mw}x{mh})")
            ok = ok and not fallas
            print(f"  {'OK  ' if not fallas else 'MAL '} {nombre} · {etiqueta}: {lw}x{lh}, "
                  f"imagen {mw}x{mh} en y={my}, titular {len(titulo)} renglón/es, "
                  f"tarjeta y={plan['texto'][1]}..{plan['texto'][3]}")
            for f_ in fallas:
                print(f"        → {f_}")

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
            # Y uno vertical de celular: va a pantalla completa con el texto en cajas.
            vert = tmp / "vert.mp4"
            subprocess.run([exe, "-y", "-f", "lavfi", "-i",
                            "testsrc=size=1080x1920:rate=25:duration=3",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                            str(vert)], capture_output=True, check=True)
        except Exception as e:                                   # noqa: BLE001
            print(f"  ROTO: no pude armar el video de prueba: {e}")
            return False

        for etiqueta, fuente, estilo in (("video derecho", base, ""),
                                         ("video de celular girado", giro, ""),
                                         ("video vertical 9:16", vert, ""),
                                         ("video corresponsal 16:9", base, "corresponsal"),
                                         ("video corresponsal 9:16", vert, "corresponsal")):
            salida = tmp / f"reel_{len(estilo)}_{etiqueta.split()[-1].replace(':', '')}.mp4"
            try:
                to_vertical_reel(fuente, salida, estilo=estilo,
                                 titular="Memi Mesplet y Seba Bravo presentan «Habladurías»",
                                 resumen="La función será el domingo 27 en Casa vieja San Luis.",
                                 volanta="Ciclo de teatro independiente",
                                 cuerpo="La obra se estrenó el año pasado en el Teatro "
                                        "Español y ya recorrió varias localidades.")
                w, h = _dimensiones(salida)
                dur = duration_seconds(salida) or 0
                falta = ultimo_reel_degradado()
                # Lo apaisado de los corresponsales sale 4:5 (pedido 2026-10-03).
                esperado = (1080, CORR_ALTO_APAISADO if etiqueta.endswith("16:9") else 1920)
                bien = (w, h) == esperado and dur > 1 and not falta
                ok = ok and bien
                detalle = f"{w}x{h}, {dur:.1f}s"
                if (w, h) != esperado:
                    detalle += f"  ← TENDRÍA QUE SER {esperado[0]}x{esperado[1]}"
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
        # Las mismas fotos con el estilo de los corresponsales: con una vertical van todas en
        # 9:16; solo las apaisadas, en 4:5.
        for etiqueta, lote, esperado in (("fotos de corresponsal mezcladas", fotos, 1920),
                                         ("fotos de corresponsal apaisadas", fotos[1:],
                                          CORR_ALTO_APAISADO)):
            try:
                salida = tmp / f"reel_corr_{len(lote)}.mp4"
                foto_a_reel(lote, salida, seg=14, overlay=False, estilo="corresponsal",
                            titular="Un auto se incendió en la Ruta 30",
                            resumen="La conductora resultó ilesa.", volanta="Chivilcoy · Accidente")
                w, h = _dimensiones(salida)
                dur = duration_seconds(salida) or 0
                falta = ultimo_reel_degradado()
                bien = (w, h) == (1080, esperado) and dur > 8 and not falta
                ok = ok and bien
                print(f"  {'OK  ' if bien else 'MAL '} {etiqueta}: {w}x{h}, {dur:.1f}s"
                      + (f"  ← se cayó a «{falta}»" if falta else "")
                      + ("" if h == esperado else f"  ← TENDRÍA QUE SER 1080x{esperado}"))
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


# Margen lateral de una GRÁFICA apaisada: va entera, con borde duro y una sombra suave,
# así que necesita aire a los costados para que la sombra se vea.
PLACA_GRAFICA_MX = 48


def to_vertical_reel(src, salida, *, audio: bool = True, max_seconds: float | None = None,
                     es_foto: bool = False,
                     firma: str | None = None, logo: bool = True,
                     placa_final: bool = True, zocalo: str | None = None,
                     overlay: bool = True, titular: str = "", resumen: str = "",
                     volanta: str = "", cuerpo: str = "",
                     compuesto=None, estilo: str = "", alto: int = 0) -> Path:
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
    titular y bajada BLANCOS) sobre carbón con humo, y la imagen disuelta en el fondo por su
    borde de arriba. Cómo se acomoda según la FORMA del material lo decide `plan_placa`
    (2026-09-26): vertical en los 3/4 de abajo sin bajada, horizontal entera con la bajada
    debajo, afiche vertical a cuadro completo. El texto ya lo escribió Gemini al redactar la
    nota: acá no se le pide nada, solo se dibuja. Se apaga con `REEL_BANDAS=0`.
    `cuerpo` ya no se usa (era el «pie» bajo las apaisadas, que reemplazó la bajada); queda
    en la firma para no romper a quien lo pasa.

    `es_foto` avisa que atrás de este 'video' hay una FOTO. Solo en ese caso se mira si el
    material es un AFICHE —que no se puede recortar, ver `_es_grafica`—: un video de celular
    nunca lo es, y uno nocturno muy comprimido tiene manchones planos que lo harían pasar
    por gráfica sin serlo.

    `compuesto` avisa que el 'video' es un reel de FOTOS que ya viene armado cuadro por
    cuadro, con fondo, foto y texto (ver `foto_a_reel`): acá solo se le pegan el isologo y la
    placa de cierre. Si es un PNG, además se le pega ese texto encima.

    `estilo="corresponsal"` (lo que llega por WhatsApp): el diseño de `_plan_corr`, con el
    isologo en X 872 · Y 130. Lo apaisado sale en 1080x1350; `alto` es el de un reel de fotos
    ya compuesto.
    """
    src, salida = Path(src), Path(salida)
    logo_geo = CORR_LOGO if estilo == "corresponsal" else None
    logo_png = _asset("REEL_LOGO", LOGO_REEL) if logo else None
    placa_cierre = _asset("REEL_PLACA_FINAL", PLACA_FINAL) if placa_final else None
    seg_placa = float(_cfg("REEL_PLACA_SEG", str(PLACA_SEG)))

    if compuesto is not None:
        # Las fotos ya vienen compuestas a 1080x1920: NADA de buscar barras negras (el fondo
        # carbón las «tendría» arriba y abajo y las recortaría) ni de volver a encuadrar.
        base = dict(fondo=None, logo_png=logo_png, overlay=None, placa=placa_cierre,
                    seg_placa=seg_placa, recorte=None, encuadre=(1080, alto or 1920, 0, 0),
                    marca_texto=False, texto_placa=None, color_fondo="",
                    fondo_placa=None,
                    capa_texto=compuesto if isinstance(compuesto, Path) else None,
                    logo_geo=logo_geo, alto=alto or 1920)
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
    # PLACA: `plan_placa` decide, según la forma del material, qué texto va, dónde va la
    # imagen, si se recorta y cómo se funde. Con eso se dibuja el PNG del texto.
    placa = None
    fondo_pl = None
    plan = None
    # Solo con `REEL_PLACA_FONDO_AUTO=1`: el tono del fondo sale del PROPIO material. Se
    # calcula una sola vez y ANTES de los escalones, así los reintentos no lo repiten.
    color_fondo = _color_dominante(src, salida.parent) if _bandas_on() else ""
    # ¿Es un afiche? Decide si la imagen se puede recortar o no. Se calcula acá, una sola
    # vez, por lo mismo que el color: los reintentos por degradado no tienen que repetirlo.
    # SOLO para fotos: un afiche llega como imagen, nunca como video, y un video nocturno
    # muy comprimido tiene manchones planos que lo harían pasar por gráfica sin serlo.
    grafica = _es_grafica(src, salida.parent) if (_bandas_on() and es_foto) else False
    caras = None
    espec = estilo == "corresponsal"
    if _bandas_on() and (titular or resumen or volanta):
        # A pantalla completa el titular va ENCIMA del video: se buscan las caras antes, para
        # no taparlas (y el encuadre las reusa). Con la especificación, toda foto va a sangre.
        if forma_de(cont_w, cont_h, grafica) == "pantalla" or (espec and not grafica):
            caras = _caras_de_video(src, recorte, salida.parent)
        plan = plan_placa(volanta, titular, resumen, cont_w, cont_h, grafica=grafica,
                          caras=caras, estilo=estilo)
        png = placa_png(plan, salida.parent / f"placa_{salida.stem}.png")
        if png:
            _x, y_media, ancho_foto, alto_foto = plan["media"]
            arriba, abajo = plan["fundido"]
            logger.info(f"Material {cont_w}x{cont_h} → forma «{plan['forma']}»: la imagen va "
                        f"en {ancho_foto}x{alto_foto} desde y={y_media}"
                        + (" (recortada desde abajo)" if plan["cover"] else " (entera)"))
            mascara = (fundido_png(alto_foto, salida.parent / f"fundido_{salida.stem}.png",
                                   arriba=arriba, abajo=abajo, ancho=ancho_foto)
                       if (arriba or abajo) else None)
            placa = (png, y_media, mascara, alto_foto, plan["cover"], ancho_foto)
            if plan.get("estilo") == "corresponsal":
                # Grafito liso, sin humo.
                color_fondo = "0x%02X%02X%02X" % CORR_GRAFITO[:3]
            else:
                fondo_pl = fondo_placa_png(salida.parent / f"fondo_placa_{salida.stem}.png",
                                           color_fondo)
    # Con placa el marco naranja no va: el fondo es el carbón que pinta el filtergraph.
    fondo = None if placa else fondo_enmarcado(
        cont_w, cont_h, salida.parent / f"fondo_{salida.stem}.png")
    # Por default el video va ENTERO, a su proporción, escalado hasta tocar los márgenes,
    # sobre el fondo difuminado. Con `REEL_FULLBLEED=1` los verticales/cuadrados se recortan
    # a 9:16 encuadrando el sujeto (los horizontales nunca: ver `_fullbleed_aplica`).
    if placa and placa[4]:
        # Hay que recortar para llenar (vertical, o una apaisada un poco alta): se busca a los
        # sujetos y lo que sobra se saca de ABAJO, así no se cortan cabezas.
        encuadre = _encuadre_fullbleed(src, cont_w, cont_h, recorte, salida.parent,
                                       alto=placa[3], fundido=plan["fundido"][0],
                                       arriba_primero=True, caras=caras)
    elif placa:
        encuadre = None                     # entera, sin recortar nada
    elif _fullbleed_aplica(cont_w, cont_h):
        encuadre = _encuadre_fullbleed(src, cont_w, cont_h, recorte, salida.parent)
    else:
        encuadre = None
        if _fullbleed_on():
            logger.info(f"Video horizontal ({cont_w}x{cont_h}): sin full bleed, va con fondo difuminado.")

    marca = dict(fondo=fondo, logo_png=logo_png, overlay=overlay_png, placa=placa_cierre,
                 seg_placa=seg_placa, recorte=recorte, encuadre=encuadre,
                 marca_texto=logo, texto_placa=placa, color_fondo=color_fondo,
                 fondo_placa=fondo_pl, capa_texto=None, logo_geo=logo_geo,
                 alto=(plan.get("lienzo") or (1080, 1920))[1] if (plan and placa) else 1920)
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
                    "encuadre": None, "alto": 1920}
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


def a_9x16(src, salida) -> Path:
    """Copia de un reel que NO es 9:16 (el 4:5 de lo apaisado de los corresponsales) llevada a
    1080x1920 con bandas grafito arriba y abajo, para las HISTORIAS, que son 9:16. El audio va
    tal cual."""
    color = "0x%02X%02X%02X" % CORR_GRAFITO[:3]
    cmd = [_ffmpeg(), "-y", "-i", str(src),
           "-vf", f"scale=1080:-2,pad=1080:1920:0:(oh-ih)/2:color={color},setsar=1",
           "-c:v", "libx264", "-preset", "veryfast", *_calidad(), "-pix_fmt", "yuv420p",
           "-c:a", "copy", "-movflags", "+faststart", str(salida)]
    _run_ffmpeg(cmd, "reel a 9:16 para historias")
    return Path(salida)


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


def _mirar_foto(foto, work_dir: Path, clave: str) -> tuple:
    """(imagen RGB ya enderezada, ¿es un afiche?) de una foto.

    `exif_transpose`: la foto de celular viene «acostada» con una marca de giro; sin esto, PIL
    la ve de costado y la mediría y encuadraría mal.

    El detector de afiches se calibró (40 publicidades y 62 fotos reales) mirando la foto
    DESPUÉS de convertirla en un clip de video al tamaño del reel: el codificador aplana los
    rellenos lisos y eso es parte de lo que mide. Sobre el JPG original, con su grano, un
    afiche de 2400x3000 pasaba por foto y se recortaba. Se le da el mismo clip que veía."""
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(foto)).convert("RGB")
    muestra = Path(work_dir) / f"_graf_{clave}.mp4"
    try:
        _foto_a_clip(foto, muestra, 1.0)
        grafica = _es_grafica(muestra, work_dir)
    except Exception:                                            # noqa: BLE001
        grafica = False
    finally:
        try:
            muestra.unlink()
        except Exception:                                        # noqa: BLE001
            pass
    return img, grafica


def _placa_de_foto(img, plan: dict, fondo, salida: Path, nombre: str = "",
                   caras=None) -> Path:
    """UNA foto compuesta como un cuadro ENTERO del reel estilo placa, según su `plan`
    (`plan_placa`): el fondo de humo, la foto donde y como dice el plan, y el texto.

    Por qué cada foto por separado (pedido del usuario 2026-09-25, «si suben varias fotos de
    distintos tamaños que queden todas bien hechas»): antes el pase de fotos se armaba con
    cada foto en un 9:16 con su propio fondo borroso, y DESPUÉS se recortaba el video entero
    como si fuera una sola imagen vertical.

    El texto va en cada cuadro (no encima, una sola vez) porque desde el 2026-09-26 cambia
    según la forma: un afiche lleva solo la marca, y una apaisada lleva la bajada debajo. Donde
    dos cuadros seguidos tienen el mismo texto, el fundido entre ellos lo deja quieto."""
    from PIL import Image, ImageDraw, ImageFilter
    w, h = img.size
    x, y, wm, hm = plan["media"]
    lienzo = fondo.copy()
    arriba, abajo = plan["fundido"]
    if plan["cover"]:
        if caras is None:
            caras = _caras_de_foto(img)
        nw, nh, cx, cy = _ventana_caras(caras, w, h, wm, hm, fundido=arriba,
                                        arriba_primero=True)
        media = img.resize((nw, nh), Image.LANCZOS).crop((cx, cy, cx + wm, cy + hm))
    else:
        media = img.resize((wm, hm), Image.LANCZOS)
    if plan["grafica"] and plan["forma"] != "afiche":
        # Afiche apaisado: entero, con borde neto y una sombra suave que lo despega del fondo.
        pad = 60
        sombra = Image.new("L", (wm + 2 * pad, hm + 2 * pad), 0)
        ImageDraw.Draw(sombra).rectangle((pad, pad, pad + wm, pad + hm), fill=170)
        sombra = sombra.filter(ImageFilter.GaussianBlur(22))
        lienzo.paste(Image.new("RGB", sombra.size, (0, 0, 0)), (x - pad, y - pad + 16), sombra)
        lienzo.paste(media, (x, y))
    elif arriba or abajo:
        lienzo.paste(media, (x, y), mascara_fundido(wm, hm, arriba=arriba, abajo=abajo))
    else:
        lienzo.paste(media, (x, y))
    pintar_placa(lienzo, plan)
    logger.info(f"Foto {nombre} ({w}x{h}) → «{plan['forma']}»: {wm}x{hm} desde y={y}"
                + (" (recortada desde abajo)" if plan["cover"] else ""))
    salida = Path(salida)
    lienzo.save(salida, quality=93)
    return salida


def _caras_de_foto(img) -> list:
    """Las caras del primer plano de una foto (ver `story_image._caras_principales`)."""
    try:
        from story_image import _caras_principales, _detect_faces
        return _caras_principales(_detect_faces(img))
    except Exception:                                            # noqa: BLE001
        return []


def _fotos_compuestas(fotos: list, base: Path, seg_cont: float, *, titular: str,
                      resumen: str, volanta: str, work_dir: Path, clave: str,
                      estilo: str = "") -> int:
    """Arma el 'video fuente' de un reel de FOTOS en el estilo placa: cada foto compuesta
    ENTERA por separado (`_placa_de_foto`, con su texto) y unidas con un fundido encadenado.

    El bloque de arriba tiene que ser el MISMO en todas las fotos, o el texto saltaría de una
    a otra: si alguna foto es VERTICAL, todas usan el bloque vertical (volanta + titular en dos
    renglones, que entra en la franja chica de arriba). Los afiches van con solo la marca."""
    from PIL import Image
    alto = 1920
    if estilo == "corresponsal":
        # Si TODAS son apaisadas, el reel sale 4:5 (1080x1350); si no, 9:16 para todas. Grafito
        # liso, sin humo.
        try:
            from PIL import ImageOps
            todas = [ImageOps.exif_transpose(Image.open(f)).size for f in fotos]
            if todas and all(w / max(1, h) > CORR_AR_APAISADO for w, h in todas):
                alto = CORR_ALTO_APAISADO
        except Exception:                                        # noqa: BLE001
            pass
        fondo = Image.new("RGB", (1080, alto), CORR_GRAFITO[:3])
    else:
        color = _color_dominante(fotos[0], work_dir)      # "" salvo REEL_PLACA_FONDO_AUTO=1
        fondo_png = fondo_placa_png(work_dir / f"fondo_placa_{clave}.png", color)
        fondo = (Image.open(fondo_png).convert("RGB") if fondo_png else
                 Image.new("RGB", (1080, 1920), _rgb_de(_color_fondo(color))))
    miradas = []
    for i, f in enumerate(fotos):
        try:
            miradas.append((f, *_mirar_foto(f, work_dir, f"{clave}_{i}")))
        except Exception as e:                                   # noqa: BLE001
            logger.warning(f"No pude abrir la foto {Path(f).name} ({e}); la salteo.")
    if not miradas:
        raise RuntimeError("ninguna foto se pudo abrir")
    formas = [forma_de(img.width, img.height, g) for _f, img, g in miradas]
    # Una «pantalla» lleva el texto abajo y encima; si convive con fotos que lo llevan arriba,
    # todas van con el bloque de arriba (el texto no puede saltar de un lugar a otro).
    modo = ("vertical" if ("vertical" in formas or
                           ("pantalla" in formas and "horizontal" in formas)) else "")
    placas = []
    for i, (f, img, grafica) in enumerate(miradas):
        try:
            forma = formas[i]
            # Las que se recortan o llevan texto encima buscan caras acá, una sola vez. Con
            # la especificación TODAS se recortan (van a sangre), salvo los afiches.
            recorta = (estilo == "corresponsal" and not grafica) or forma in ("pantalla",
                                                                               "vertical")
            caras = _caras_de_foto(img) if recorta else None
            plan = plan_placa(volanta, titular, resumen, img.width, img.height,
                              grafica=grafica, modo_texto=modo, caras=caras, estilo=estilo,
                              alto=alto if estilo == "corresponsal" else 0)
            placas.append(_placa_de_foto(img, plan, fondo,
                                         work_dir / f"_placa_foto_{clave}_{i}.jpg",
                                         Path(f).name, caras=caras))
        except Exception as e:                                   # noqa: BLE001
            logger.warning(f"No pude componer la foto {Path(f).name} ({e}); la salteo.")
    if not placas:
        raise RuntimeError("ninguna foto se pudo componer")
    # Solo FUNDIDO entre fotos: una cortina o un deslizamiento moverían el humo y el texto.
    por = (seg_cont if len(placas) == 1 else
           max(3.0, (seg_cont + (len(placas) - 1) * 0.6) / len(placas)))
    build_slideshow(placas, base, seg=por, fade=0.6, transiciones=["fade"], alto=alto)
    return alto


def foto_a_reel(fotos, salida, *, seg: float | None = None, zocalo: str | None = None,
                firma: str | None = None, overlay: bool = True,
                titular: str = "", resumen: str = "", volanta: str = "",
                cuerpo: str = "", estilo: str = "") -> Path:
    """Convierte una FOTO (o varias) de una nota en un reel vertical 9:16 con el MISMO
    criterio estético que los videos: logo arriba a la derecha, texto de la nota arriba y
    la placa de cierre «Seguinos en redes» al final.

    En el estilo placa (con texto, lo normal) cada foto se compone POR SEPARADO según su
    forma (`plan_placa` + `_placa_de_foto`). Si eso fallara, o sin texto, va el armado de
    antes: reusa `to_vertical_reel` pasándole un 'video fuente' armado con la/s foto/s.

    - Una foto → se muestra fija.
    - Varias → pase de fotos con fundido encadenado, todas branded.
    `seg` es la DURACIÓN TOTAL apuntada del reel (default `REEL_FOTO_SEG`, 30s): se le
    descuentan los segundos de la placa para que el total quede en ~`seg`. Devuelve el .mp4.
    `cuerpo` ya no se usa (era el «pie» de las apaisadas, que reemplazó la bajada).
    `estilo="corresponsal"`: lo que llega por WhatsApp (`_plan_corr`).
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
        listo = False
        try:
            listo = _fotos_compuestas(fotos, base, seg_cont, titular=titular, resumen=resumen,
                                      volanta=volanta, work_dir=salida.parent,
                                      clave=salida.stem, estilo=estilo)
        except Exception as e:                                   # noqa: BLE001
            logger.error(f"No pude componer las fotos en el estilo placa ({e}); van con el "
                         f"armado de antes.")
        if listo:
            logger.info(f"Foto-reel: {len(fotos)} foto(s) compuestas una por una → "
                        f"{seg_cont:.0f}s de contenido + placa (1080x{listo})")
            return to_vertical_reel(base, salida, audio=False, firma=firma, compuesto=True,
                                    estilo=estilo, alto=listo)
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
                            volanta=volanta, es_foto=True, estilo=estilo)


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
                    transiciones: list | None = None, alto: int = 1920) -> Path:
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

    fc = [_norm(i, fps, alto) for i in range(n)]
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
