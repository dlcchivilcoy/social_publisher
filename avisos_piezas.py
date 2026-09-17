"""Piezas de un aviso: medir lo que mandó el anunciante y adaptarlo a cada formato.

El anunciante manda lo que tiene —una foto apaisada, ocho fotos de distinto tamaño,
un video vertical— y cada formato de red pide algo distinto:

  - **carrusel**: Instagram exige que TODAS las fotos tengan la MISMA proporción. Si
    no, recorta cada una por su lado y el carrusel sale desparejo.
  - **reel / historia**: 9:16. Si son varias fotos hay que pegarlas en un video.
  - **publicación simple**: la foto va como vino.

Acá vive esa adaptación. A diferencia de los reels de las notas, **los avisos NO
llevan branding**: ni isotipo, ni overlay, ni la placa «Seguinos en redes». El aviso
es del anunciante y lo que se ve tiene que ser lo que pagó.
"""
from __future__ import annotations

from pathlib import Path

from utils.logger import get_logger

logger = get_logger("avisos")

MAX_FOTOS = 10          # el tope del carrusel de Instagram
SEG_POR_FOTO = 3.0      # cuánto se ve cada foto en el video pegado
FADE = 0.6              # el fundido entre una foto y la siguiente
SEG_MAX = 60.0          # tope del video: más largo que esto Instagram no lo toma

# Los tres lienzos que Instagram admite para el feed, de más alto a más ancho.
LIENZOS = {
    "alta":     (1080, 1350),   # 4:5, el máximo vertical que permite el feed
    "cuadrada": (1080, 1080),
    "ancha":    (1080, 566),    # 1.91:1
}


def _medidas(foto: Path) -> tuple[int, int]:
    from PIL import Image
    with Image.open(foto) as im:
        return im.size


def forma_de(ancho: int, alto: int) -> str:
    """El mismo criterio que usa `avisos_web.medir` para la web, así una pieza cae
    siempre en la misma categoría la mire quien la mire."""
    if ancho <= 0 or alto <= 0:
        return "ancha"
    prop = ancho / alto
    if prop >= 1.25:
        return "ancha"
    if prop <= 0.85:
        return "alta"
    return "cuadrada"


def forma_del_conjunto(fotos: list[Path]) -> str:
    """La forma que le toca a un carrusel: la que MÁS se repite entre sus fotos.

    Se elige por mayoría y no por la primera, porque encajar ocho fotos verticales en
    un lienzo apaisado —solo porque la primera lo era— deja siete con bandas enormes.
    Empate: gana la más alta, que es la que más pantalla ocupa en el celular.
    """
    if not fotos:
        return "cuadrada"
    cuenta: dict[str, int] = {}
    for f in fotos:
        try:
            forma = forma_de(*_medidas(f))
        except Exception:                                        # noqa: BLE001
            forma = "cuadrada"
        cuenta[forma] = cuenta.get(forma, 0) + 1
    orden = {"alta": 0, "cuadrada": 1, "ancha": 2}   # el desempate
    return min(cuenta, key=lambda f: (-cuenta[f], orden[f]))


def normalizar_carrusel(fotos, destino: Path | None = None) -> list[Path]:
    """Deja todas las fotos en el MISMO lienzo, que es lo que Instagram exige.

    La foto nunca se recorta: se escala entera y lo que sobra se rellena con una
    versión borrosa de ella misma (`story_image._fit_blur`, el mismo criterio que las
    historias). Recortar sería más lindo de ver pero en un aviso se come el teléfono
    o el nombre del comercio.

    Con una sola foto no hay nada que emparejar y se devuelve tal cual.
    """
    from PIL import Image
    from story_image import _fit_blur

    fotos = [Path(f) for f in fotos][:MAX_FOTOS]
    if len(fotos) < 2:
        return fotos

    forma = forma_del_conjunto(fotos)
    ancho, alto = LIENZOS[forma]
    destino = Path(destino) if destino else fotos[0].parent / "_carrusel"
    destino.mkdir(parents=True, exist_ok=True)

    salidas: list[Path] = []
    for i, f in enumerate(fotos, 1):
        try:
            with Image.open(f) as im:
                lienzo = _fit_blur(im.convert("RGB"), ancho, alto)
            out = destino / f"carrusel_{i:02d}.jpg"
            lienzo.save(out, "JPEG", quality=90)
            salidas.append(out)
        except Exception as e:                                   # noqa: BLE001
            # Que una foto rara no tumbe el carrusel entero: va como vino y que IG
            # haga lo que pueda con ella.
            logger.warning(f"No pude emparejar «{f.name}» ({e}); va sin tocar.")
            salidas.append(f)
    logger.info(f"Carrusel emparejado: {len(salidas)} fotos a {ancho}x{alto} ({forma})")
    return salidas


def fotos_a_video(fotos, salida, *, seg_por_foto: float = SEG_POR_FOTO) -> Path:
    """Pega las fotos en un video 9:16 con los tiempos REPARTIDOS PAREJO.

    Lo usa el reel (varias fotos) y también la historia de un carrusel, que es el
    mismo video: así el seguidor ve todas las fotos en una sola historia en vez de
    comerse ocho seguidas.

    Sin branding: `build_slideshow` arma el slideshow y nada más. Los reels de las
    notas pasan además por `to_vertical_reel`, que estampa isotipo y placa final; un
    aviso no.
    """
    from video import build_slideshow
    from story_image import compose_foto_reel

    fotos = [Path(f) for f in fotos][:MAX_FOTOS]
    salida = Path(salida)
    salida.parent.mkdir(parents=True, exist_ok=True)
    if not fotos:
        raise ValueError("No hay fotos para armar el video del aviso.")

    # El total no puede pasar de SEG_MAX: con 10 fotos a 3 s son 30 s, pero si algún
    # día se sube el tiempo por foto, esto lo frena antes de que Instagram lo rechace.
    seg = min(float(seg_por_foto), SEG_MAX / len(fotos))
    slides = [compose_foto_reel(f) for f in fotos]
    build_slideshow(slides, salida, seg=seg, fade=FADE if len(slides) > 1 else 0.0)
    logger.info(f"Video del aviso: {len(fotos)} foto(s) a {seg:.1f}s cada una → {salida.name}")
    return salida


def como_sale(tipo: str, formatos: list[str], cuantas: int) -> str:
    """Una línea en castellano de lo que va a salir, para mostrar en el editor antes
    de guardar. Que el usuario lea lo que va a pasar es más barato que deshacerlo."""
    partes: list[str] = []
    if "feed" in formatos:
        if tipo == "video":
            partes.append("video en el muro")
        elif cuantas > 1:
            partes.append(f"carrusel de {cuantas} fotos")
        else:
            partes.append("publicación")
    if "reel" in formatos:
        partes.append("reel" if tipo == "video" or cuantas == 1
                      else f"reel con las {cuantas} fotos")
    if "historia" in formatos:
        if tipo == "video" or cuantas > 1:
            partes.append("historia con el video")
        else:
            partes.append("historia")
    return " + ".join(partes) if partes else "nada (no elegiste ningún formato)"
