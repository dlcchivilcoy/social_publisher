# -*- coding: utf-8 -*-
"""Cómo se escriben los nombres propios y las siglas: manda SIEMPRE lo ESCRITO.

Por qué existe esto (pedido del usuario 2026-09-20): la información que se estampa en el
reel —volanta, titular y bajada— no puede salir de la desgrabación del audio, porque el
reconocimiento de voz escribe los nombres como suenan y los equivoca: «Zeballos» por
«Ceballos», «CASMA» por «CAZMA», «Vritos» por «Britos». En un titular quemado en el video
eso no se puede corregir después: ya salió publicado.

A Gemini se le pide que respete la grafía del texto del colaborador, y lo hace casi
siempre. Este módulo es la red debajo: **no le cree a nadie y compara**. Toma cada nombre
propio y cada sigla del texto generado, y si suena igual que uno del texto ESCRITO pero
está escrito distinto, lo reemplaza por el del escrito. Es puramente mecánico: no consulta
ninguna IA, no puede inventar nada y no puede tardar.

La clave es `_plegar`: reduce una palabra a cómo SUENA en castellano rioplatense (b=v,
s=z=c, ll=y, h muda, qu=c=k, g=j ante e/i). Dos grafías distintas del mismo nombre pliegan
igual; dos nombres distintos, no. Por eso el reemplazo solo se hace ante coincidencia
EXACTA del plegado — nada de «parecidos», que es como se cambia un apellido por otro.
"""
import re
import unicodedata

from utils.logger import get_logger

logger = get_logger("grafia")

# Palabras que empiezan con mayúscula por estar al principio de una oración, no por ser
# nombres propios. Nunca se tocan: cambiarlas sería corregir algo que no está mal.
_COMUNES = {
    "el", "la", "los", "las", "un", "una", "unos", "unas", "este", "esta", "estos", "estas",
    "ese", "esa", "esos", "esas", "aquel", "aquella", "su", "sus", "mi", "mis", "tu", "tus",
    "de", "del", "al", "en", "por", "para", "con", "sin", "sobre", "desde", "hasta", "entre",
    "que", "como", "cuando", "donde", "porque", "pero", "aunque", "también", "tambien",
    "no", "sí", "si", "ya", "muy", "más", "mas", "menos", "todo", "toda", "todos", "todas",
    "otro", "otra", "otros", "otras", "cada", "hay", "hubo", "fue", "fueron", "era", "eran",
    "es", "son", "está", "esta", "están", "estan", "será", "sera", "serán", "seran",
    "tras", "durante", "según", "segun", "ante", "bajo", "hacia", "mientras", "además",
    "ademas", "luego", "después", "despues", "antes", "ahora", "hoy", "ayer", "mañana",
    "manana", "así", "asi", "solo", "sólo", "aún", "aun", "tan", "tanto", "casi", "nunca",
    "siempre", "cerca", "lejos", "dentro", "fuera", "arriba", "abajo", "primero", "primera",
    "segundo", "segunda", "tercero", "tercera", "última", "ultima", "último", "ultimo",
    "lunes", "martes", "miércoles", "miercoles", "jueves", "viernes", "sábado", "sabado",
    "domingo", "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "setiembre", "octubre", "noviembre", "diciembre",
}

# Longitud mínima para animarse a tocar una palabra. Abajo de esto las coincidencias de
# sonido son casualidad («Vos» / «Bos») y el riesgo de romper algo bueno es mayor que el
# de dejar un nombre mal escrito.
_MIN_LARGO = 3

_PALABRA = re.compile(r"[A-ZÁÉÍÓÚÜÑ][A-Za-zÁÉÍÓÚÜÑáéíóúüñ'’·.-]*")


def _sin_tildes(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def _plegar(palabra: str) -> str:
    """Reduce la palabra a cómo SUENA. Dos grafías del mismo nombre dan el mismo plegado."""
    p = _sin_tildes(palabra.lower())
    p = re.sub(r"[^a-zñ]", "", p)          # fuera puntos, guiones y apóstrofos de las siglas
    if not p:
        return ""
    p = p.replace("qu", "k")               # ANTES que c→k, si no «que» quedaría «kue»
    p = p.replace("gue", "ge").replace("gui", "gi")   # la u muda no suena
    p = re.sub(r"c([ei])", r"s\1", p)      # «cebolla» suena «sebolla»
    p = re.sub(r"g([ei])", r"j\1", p)      # «Gerardo» suena «Jerardo»
    p = p.replace("c", "k").replace("q", "k")
    p = p.replace("z", "s")                # seseo: Zeballos = Ceballos = Seballos
    p = p.replace("v", "b")                # Bravo = Brabo
    p = p.replace("h", "")                 # muda: Hernández = Ernández
    p = p.replace("ll", "y")               # yeísmo: Ceballos = Cebayos
    p = re.sub(r"(.)\1+", r"\1", p)        # letras dobles: Bonnet = Bonet
    return p


def _vocabulario(fuente: str) -> dict:
    """`{plegado: como está escrito}` de todos los nombres propios y siglas del texto
    escrito. Si el mismo sonido aparece con dos grafías, gana la PRIMERA."""
    vocab: dict = {}
    for m in _PALABRA.finditer(fuente or ""):
        palabra = m.group(0).strip(".·-'’")
        if len(palabra) < _MIN_LARGO or palabra.lower() in _COMUNES:
            continue
        clave = _plegar(palabra)
        if clave and clave not in vocab:
            vocab[clave] = palabra
    return vocab


def corregir(texto: str, fuente: str) -> tuple:
    """Devuelve `(texto corregido, [(antes, después), ...])`.

    Solo cambia una palabra si: es un nombre propio o una sigla, NO aparece tal cual en el
    texto escrito, y suena EXACTAMENTE igual que una que sí aparece. Todo lo demás queda
    intacto — incluidos los nombres que solo dijo el audio, que no tienen con qué
    compararse y por lo tanto no se tocan."""
    if not texto or not fuente:
        return texto or "", []
    vocab = _vocabulario(fuente)
    if not vocab:
        return texto, []
    # Lo que ya está escrito IGUAL en la fuente es correcto por definición: ni se mira.
    literales = {m.group(0).strip(".·-'’") for m in _PALABRA.finditer(fuente)}
    cambios: list = []

    def _una(m):
        cruda = m.group(0)
        palabra = cruda.strip(".·-'’")
        cola = cruda[len(palabra):]
        if len(palabra) < _MIN_LARGO or palabra in literales or palabra.lower() in _COMUNES:
            return cruda
        buena = vocab.get(_plegar(palabra))
        if not buena or buena == palabra:
            return cruda
        # Un titular puede venir todo en mayúsculas: se respeta esa forma.
        if palabra.isupper() and not buena.isupper():
            buena = buena.upper()
        if buena == palabra:
            return cruda
        cambios.append((palabra, buena))
        return buena + cola

    corregido = _PALABRA.sub(_una, texto)
    return corregido, cambios


def corregir_campos(campos: dict, fuente: str) -> dict:
    """Corrige varios campos de una vez y deja UNA línea en el log con lo que cambió.

    `campos` es `{nombre: texto}` y devuelve lo mismo, ya corregido."""
    salida, todo = {}, []
    for nombre, valor in campos.items():
        nuevo, cambios = corregir(valor, fuente)
        salida[nombre] = nuevo
        todo += [f"{a} → {b} (en {nombre})" for a, b in cambios]
    if todo:
        logger.info("Grafía según el texto escrito: " + "; ".join(dict.fromkeys(todo)))
    return salida
