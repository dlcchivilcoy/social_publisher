# -*- coding: utf-8 -*-
"""HISTORIA DIARIA DEL GRUPO DE CLASIFICADOS (14/11/2026, pedido del usuario).

«La invitación al grupo de clasificados: cambiá la imagen del QR por algo oficial del grupo de Facebook, como un
video que invite a la compra/venta de productos, con una pregunta: ¿tenés algún producto o servicio? Compartilo en
el grupo de clasificados de @diarioyradio, y que la historia salga todos los días.»

Un video 9:16 de ~13 s con la estética de las otras historias de la radio (historia_entrevistados.py): fondo
naranja, isologo y logo en blanco arriba, «pop» al aparecer y texto tipeado como máquina de escribir.

1. «CLASIFICADOS» en una píldora blanca y debajo «Compra · Venta · Alquiler · Servicios».
2. Un tablero de etiquetas que van cayendo con su pop («VENDO», «COMPRO», «SERVICIOS», «MUEBLES»…), como
   avisos pegados; cada día salen otras y en otro orden (`etiquetas_de`).
3. La pregunta, palabra por palabra: «¿Tenés algún producto o servicio?».
4. La tarjeta tipeada: «Compartilo en el grupo de Clasificados de @diarioyradio».
5. La tarjeta del GRUPO tal como se ve en Facebook: la portada, el nombre oficial del grupo
   («CLASIFICADOS CHIVILCOY - DIARIO Y RADIO»), «Grupo público de Facebook» y el botón azul «Unirte al grupo»,
   que se aprieta con un toque.

Lo publica stories.run_grupo_clasificados (todos los días la historia; los viernes, además, el video en el muro
de Facebook con el link del grupo).
"""
from __future__ import annotations

import math
import random
import subprocess
from datetime import date
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

import historia_entrevistados as HE

W, H, FPS, SR = HE.W, HE.H, HE.FPS, HE.SR
NARANJA, GRAFITO, BLANCO = HE.NARANJA, HE.GRAFITO, HE.BLANCO
AZUL_FB = (8, 102, 255)
CREMA = (255, 241, 222)

NOMBRE_GRUPO = "CLASIFICADOS CHIVILCOY - DIARIO Y RADIO"     # el nombre oficial del grupo en Facebook
ETIQUETA = "CLASIFICADOS"
SUBTITULO = "Compra · Venta · Alquiler · Servicios"
PREGUNTA = "¿Tenés algún producto o servicio?"
RENGLONES_CTA = ("Compartilo en el grupo de Clasificados", "de @diarioyradio")
TEXTO_CTA = " ".join(RENGLONES_CTA)
RESALTADO = "@diarioyradio"
FIJAS = ("VENDO", "COMPRO", "SERVICIOS")
OTRAS = ("ALQUILO", "PERMUTO", "EMPLEOS", "AUTOS Y MOTOS", "MUEBLES", "ROPA", "CELULARES", "HERRAMIENTAS",
         "BICICLETAS", "ELECTRODOMÉSTICOS", "CASAS Y TERRENOS", "MASCOTAS", "OFICIOS", "ANTIGÜEDADES",
         "JUGUETES", "PARA EL CAMPO")
N_ETIQUETAS = 10
ESTILOS = ((BLANCO, NARANJA), (GRAFITO, BLANCO), (CREMA, GRAFITO))       # (píldora, letra)

# Tiempos (s)
T_ETIQUETA, T_SUB = 0.15, 0.45
T_TABLERO, PASO_TABLERO = 0.9, 0.3
T_PREGUNTA, PASO_PALABRA = 4.3, 0.2
T_TARJETA = 5.9
CPS = HE.CPS
T_GRUPO_DESPUES = 0.35          # la tarjeta del grupo, después de terminar el tipeo
T_TOQUE_DESPUES = 1.0           # el toque al botón, después de que aparece la tarjeta del grupo
COLA = 2.1                      # lo que queda en pantalla después del toque

# Lugares (y): el logo termina en ~250, la caja de respuesta de Instagram empieza en ~1700.
Y_ETIQUETA, Y_SUB = 330, 418
TABLERO = (505, 975)
Y_PREGUNTA = 1000
AIRE = 36                       # entre la pregunta, la tarjeta y la tarjeta del grupo
Y_GRUPO_FIN = 1700


def etiquetas_de(dia: date) -> list:
    """Las etiquetas del tablero de ese día: siempre «VENDO», «COMPRO» y «SERVICIOS», y otras que cambian, en
    otro orden cada día."""
    rng = random.Random(dia.toordinal())
    elegidas = list(FIJAS) + rng.sample(OTRAS, N_ETIQUETAS - len(FIJAS))
    rng.shuffle(elegidas)
    return elegidas


def _sticker(texto: str, estilo: int) -> Image.Image:
    S = HE._fuentes()
    fondo, letra = ESTILOS[estilo % len(ESTILOS)]
    im = HE._sprite_texto(texto, S._franklin(46, 800), letra, fondo_pill=fondo, pad=(30, 16))
    sombra = Image.new("RGBA", (im.width + 40, im.height + 40), (0, 0, 0, 0))
    ImageDraw.Draw(sombra).rounded_rectangle((20, 28, im.width + 20, im.height + 28), radius=im.height // 2,
                                             fill=(110, 40, 0, 95))
    sombra = sombra.filter(ImageFilter.GaussianBlur(9))
    sombra.alpha_composite(im, (20, 20))
    return sombra


def plan_tablero(dia: date) -> list:
    """[(sprite, centro, giro, t0, fase)]: las etiquetas acomodadas en renglones centrados dentro del tablero,
    cada una un poco girada y corrida, como avisos pegados."""
    rng = random.Random(dia.toordinal() * 7 + 1)
    sprites = [_sticker(t, i) for i, t in enumerate(etiquetas_de(dia))]
    ancho_max = W - 2 * 60
    filas, fila, ancho = [], [], 0
    for sp in sprites:
        w = sp.width - 40 + 18
        if fila and ancho + w > ancho_max:
            filas.append(fila)
            fila, ancho = [], 0
        fila.append(sp)
        ancho += w
    if fila:
        filas.append(fila)
    alto_fila = (TABLERO[1] - TABLERO[0]) / max(1, len(filas))
    piezas, k = [], 0
    for r, fila in enumerate(filas):
        total = sum(sp.width - 40 for sp in fila) + 18 * (len(fila) - 1)
        x = (W - total) / 2
        cy = TABLERO[0] + alto_fila * (r + 0.5)
        for sp in fila:
            w = sp.width - 40
            centro = (x + w / 2 + rng.uniform(-10, 10), cy + rng.uniform(-14, 14))
            piezas.append((sp, centro, rng.uniform(-7, 7), T_TABLERO + k * PASO_TABLERO, rng.uniform(0, 6.28)))
            x += w + 18
            k += 1
    # El orden en que caen: mezclado, no de izquierda a derecha.
    orden = list(range(len(piezas)))
    rng.shuffle(orden)
    return [(piezas[i][0], piezas[i][1], piezas[i][2], T_TABLERO + j * PASO_TABLERO, piezas[i][4])
            for j, i in enumerate(orden)]


def plan_pregunta() -> list:
    """[(sprite, centro, t0)]: la pregunta, grande y blanca, en uno o dos renglones centrados."""
    S = HE._fuentes()
    m = ImageDraw.Draw(Image.new("L", (1, 1)))
    tam = 104
    while True:
        f = S._franklin(tam, 800)
        palabras = PREGUNTA.split()
        renglones, actual = [], []
        for p in palabras:
            prueba = " ".join(actual + [p])
            if actual and m.textlength(prueba, font=f) > W - 2 * 70:
                renglones.append(actual)
                actual = [p]
            else:
                actual.append(p)
        renglones.append(actual)
        if len(renglones) <= 2 or tam <= 70:
            break
        tam -= 4
    sp_espacio = m.textlength(" ", font=f)
    paso = round(tam * 1.12)
    piezas, k = [], 0
    for r, ren in enumerate(renglones):
        sprites = [HE._sprite_texto(p, f, BLANCO) for p in ren]
        total = sum(s.width - 8 for s in sprites) + sp_espacio * (len(sprites) - 1)
        x = (W - total) / 2
        cy = Y_PREGUNTA + paso * r + paso / 2
        for s in sprites:
            piezas.append((s, (x + (s.width - 8) / 2, cy), T_PREGUNTA + k * PASO_PALABRA))
            x += s.width - 8 + sp_espacio
            k += 1
    return piezas


def plan_tarjeta() -> dict:
    S = HE._fuentes()
    m = ImageDraw.Draw(Image.new("L", (1, 1)))
    ancho = W - 2 * 70 - 2 * 44
    tam = 50
    while tam > 26 and max(m.textlength(r, font=S._franklin(tam, 800)) for r in RENGLONES_CTA) > ancho:
        tam -= 1
    paso, pad = round(tam * 1.32), 30
    a = TEXTO_CTA.index(RESALTADO)
    return {"fuente": S._franklin(tam, 800), "renglones": list(RENGLONES_CTA), "paso": paso, "pad": pad,
            "tam": tam, "alto": len(RENGLONES_CTA) * paso + 2 * pad, "resaltado": (a, a + len(RESALTADO))}


def tarjeta_grupo() -> Image.Image:
    """La tarjeta del grupo como se ve en Facebook: portada, nombre, «Grupo público de Facebook» y el botón."""
    S = HE._fuentes()
    tw, th = W - 140, 210
    im = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, tw - 1, th - 1), radius=34, fill=(255, 255, 255, 252))
    # La portada: un cuadrado naranja con el isologo en blanco.
    lado = th - 2 * 24
    portada = Image.new("RGBA", (lado, lado), (0, 0, 0, 0))
    ImageDraw.Draw(portada).rounded_rectangle((0, 0, lado - 1, lado - 1), radius=24, fill=NARANJA + (255,))
    iso = S._iso(False)
    if iso:
        k = round(lado * 0.62)
        iw = round(iso.width * k / iso.height)
        portada.alpha_composite(HE._blanquear(iso.convert("RGBA").resize((iw, k), Image.LANCZOS)),
                                ((lado - iw) // 2, (lado - k) // 2))
    im.alpha_composite(portada, (24, 24))
    x0 = 24 + lado + 26
    ancho = tw - x0 - 26
    tam = 40
    while tam > 26 and d.textlength(NOMBRE_GRUPO, font=S._franklin(tam, 800)) > ancho * 2 - 40:
        tam -= 1
    f = S._franklin(tam, 800)
    # El nombre en uno o dos renglones, partido en el guion si hace falta.
    if d.textlength(NOMBRE_GRUPO, font=f) <= ancho:
        renglones = [NOMBRE_GRUPO]
    else:
        a, _, b = NOMBRE_GRUPO.partition(" - ")
        renglones = [a, "- " + b] if b else [NOMBRE_GRUPO]
    y = 26
    for r in renglones:
        d.text((x0, y), r, font=f, fill=GRAFITO)
        y += round(tam * 1.14)
    d.text((x0, y + 2), "Grupo público de Facebook", font=S._sans(28, 500), fill=(101, 103, 107))
    return im


def boton_unirse(apretado: float) -> Image.Image:
    """El botón azul «Unirte al grupo». `apretado` de 0 a 1: se achica y se oscurece un poco."""
    S = HE._fuentes()
    f = S._sans(31, 700)
    m = ImageDraw.Draw(Image.new("L", (1, 1)))
    tw = int(m.textlength("Unirte al grupo", font=f)) + 56
    th = 66
    im = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    color = tuple(int(c * (1 - 0.18 * apretado)) for c in AZUL_FB)
    d = ImageDraw.Draw(im)
    d.rounded_rectangle((0, 0, tw - 1, th - 1), radius=16, fill=color + (255,))
    d.text((tw / 2, th / 2), "Unirte al grupo", font=f, fill=BLANCO, anchor="mm")
    return im


def linea_de_tiempo() -> dict:
    t_fin_tipeo = T_TARJETA + 0.25 + len(TEXTO_CTA) / CPS
    t_grupo = t_fin_tipeo + T_GRUPO_DESPUES
    t_toque = t_grupo + T_TOQUE_DESPUES
    return {"t_tipeo": T_TARJETA + 0.25, "t_fin_tipeo": t_fin_tipeo, "t_grupo": t_grupo, "t_toque": t_toque,
            "total": t_toque + COLA}


def sonidos(tl: dict, tablero: list, pregunta: list) -> list:
    rng = np.random.default_rng(5)
    ev = [(T_ETIQUETA, HE.sonido_pop(1.0), 0.9)]
    for i, (_, _, _, t0, _) in enumerate(tablero):
        ev.append((t0, HE.sonido_pop(0.85 + 0.06 * (i % 5)), 0.75))
    for _, _, t0 in pregunta:
        ev.append((t0, HE.sonido_pop(1.25), 0.35))
    ev.append((T_TARJETA, HE.sonido_pop(0.9), 0.8))
    t = tl["t_tipeo"]
    for ch in TEXTO_CTA:
        if ch != " ":
            ev.append((t, HE.sonido_tecla(rng), 0.6))
        t += 1 / CPS
    ev.append((tl["t_fin_tipeo"], HE.sonido_campanita(), 0.5))
    ev.append((tl["t_grupo"], HE.sonido_pop(0.8), 0.9))
    ev.append((tl["t_toque"], HE.sonido_pop(1.4), 0.9))
    ev.append((tl["t_toque"] + 0.12, HE.sonido_campanita(), 0.35))
    return ev


def dibujar_tarjeta(capa, plan: dict, letras: int, cursor: bool, escala: float, y: int):
    """La tarjeta blanca con el texto tipeado (lo mismo que en la historia de los entrevistados)."""
    if escala <= 0:
        return
    tw, th = W - 140, plan["alto"]
    tarjeta = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    d = ImageDraw.Draw(tarjeta)
    d.rounded_rectangle((0, 0, tw - 1, th - 1), radius=34, fill=(255, 255, 255, 250))
    f, a, b = plan["fuente"], *plan["resaltado"]
    yy, hecho = plan["pad"], 0
    xc, yc = 44, yy
    for r in plan["renglones"]:
        x = 44
        for i, ch in enumerate(r):
            pos = hecho + i
            if pos >= letras:
                break
            d.text((x, yy), ch, font=f, fill=NARANJA if a <= pos < b else GRAFITO)
            x += d.textlength(ch, font=f)
            xc, yc = x, yy
        if hecho + len(r) >= letras:
            break
        hecho += len(r) + 1
        yy += plan["paso"]
        xc, yc = 44, yy
    if cursor:
        d.rounded_rectangle((xc + 4, yc + 4, xc + 10, yc + plan["tam"] + 6), radius=3, fill=NARANJA)
    HE._pegar_escalado(capa, _con_sombra(tarjeta), (W / 2, y + th / 2), escala)


def _con_sombra(im: Image.Image) -> Image.Image:
    s = Image.new("RGBA", (im.width + 60, im.height + 60), (0, 0, 0, 0))
    ImageDraw.Draw(s).rounded_rectangle((30, 40, im.width + 30, im.height + 40), radius=34, fill=(110, 40, 0, 110))
    s = s.filter(ImageFilter.GaussianBlur(18))
    s.alpha_composite(im, (30, 30))
    return s


def armar(salida: Path, dia: date | None = None) -> Path:
    from video import _ffmpeg
    dia = dia or date.today()
    salida = Path(salida)
    S = HE._fuentes()
    tl = linea_de_tiempo()
    total = tl["total"]
    tablero = plan_tablero(dia)
    pregunta = plan_pregunta()
    tarjeta = plan_tarjeta()
    grupo = _con_sombra(tarjeta_grupo())
    alto_grupo = grupo.height - 60
    # De arriba hacia abajo: la pregunta, la tarjeta tipeada y la del grupo, con el mismo aire; si no
    # entra antes de la caja de respuesta, se apoya en ella.
    fin_pregunta = max(c[1] + sp.height / 2 for sp, c, _ in pregunta)
    y_tarjeta = round(fin_pregunta + AIRE)
    y_grupo = y_tarjeta + tarjeta["alto"] + AIRE - 8
    if y_grupo + alto_grupo > Y_GRUPO_FIN:
        y_grupo = Y_GRUPO_FIN - alto_grupo
        y_tarjeta = min(y_tarjeta, y_grupo - AIRE - tarjeta["alto"])
    fondo = HE.fondo()
    logo = HE.capa_logo()
    etiqueta = HE._sprite_texto(ETIQUETA, S._franklin(64, 800), NARANJA, fondo_pill=BLANCO, pad=(40, 18))
    sub = HE._sprite_texto(SUBTITULO, S._sans(40, 600), BLANCO)
    wav = HE.guardar_wav(HE.mezclar(sonidos(tl, tablero, pregunta), total), salida.with_suffix(".wav"))
    proc = subprocess.Popen(
        [_ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
         "-r", str(FPS), "-i", "pipe:0", "-i", str(wav), "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
         "-movflags", "+faststart", "-shortest", str(salida)], stdin=subprocess.PIPE)
    giros = {}
    try:
        for k in range(int(total * FPS)):
            t = k / FPS
            cuadro = fondo.copy()
            cuadro.alpha_composite(logo)
            HE._pegar_escalado(cuadro, etiqueta, (W / 2, Y_ETIQUETA), HE.escala_pop(t, T_ETIQUETA))
            HE._pegar_escalado(cuadro, sub, (W / 2, Y_SUB), 1.0, HE._ease_out((t - T_SUB) / 0.4))
            # El tablero: cada etiqueta cae desde un poco más arriba, rebota y después flota.
            for i, (sp, (cx, cy), giro, t0, fase) in enumerate(tablero):
                if t < t0:
                    continue
                u = min(1.0, (t - t0) / 0.42)
                caida = (1 - HE._ease_out(u)) * -60
                flota = 5 * math.sin(2 * math.pi * (t / 2.6) + fase)
                g = giro + (1 - u) * 12 * (1 if i % 2 else -1)
                clave = (i, round(g, 1))
                if clave not in giros:
                    giros[clave] = sp.rotate(g, resample=Image.BICUBIC, expand=True)
                HE._pegar_escalado(cuadro, giros[clave], (cx, cy + caida + flota), HE.escala_pop(t, t0, 0.42),
                                   min(1.0, u * 2.5))
            for sp, c, t0 in pregunta:
                HE._pegar_escalado(cuadro, sp, c, HE.escala_pop(t, t0, 0.32))
            letras = int(max(0.0, t - tl["t_tipeo"]) * CPS)
            escribiendo = tl["t_tipeo"] <= t < tl["t_fin_tipeo"]
            cursor = (escribiendo or int(t * 2) % 2 == 0) and t >= T_TARJETA + 0.2
            dibujar_tarjeta(cuadro, tarjeta, min(letras, len(TEXTO_CTA)), cursor, HE.escala_pop(t, T_TARJETA),
                            y_tarjeta)
            # La tarjeta del grupo y el toque al botón.
            eg = HE.escala_pop(t, tl["t_grupo"])
            if eg > 0:
                capa = grupo.copy()
                u = t - tl["t_toque"]
                apretado = max(0.0, 1 - abs(u - 0.08) / 0.16) if -0.08 < u < 0.24 else 0.0
                bt = boton_unirse(apretado)
                esc_b = 1 - 0.06 * apretado
                if esc_b != 1:
                    bt = bt.resize((round(bt.width * esc_b), round(bt.height * esc_b)), Image.BILINEAR)
                bx = capa.width - 30 - 26 - bt.width // 2 - round((1 - esc_b) * 40)
                by = 30 + alto_grupo - 24 - bt.height // 2 - round((1 - esc_b) * 4) - 2
                bx0, by0 = bx - bt.width // 2, by - bt.height // 2
                capa.alpha_composite(bt, (bx0, by0))
                if u >= 0:
                    # La onda del toque: un círculo que se agranda y se apaga.
                    r = 20 + 160 * HE._ease_out(u / 0.6)
                    a = int(200 * max(0.0, 1 - u / 0.6))
                    if a > 0:
                        ImageDraw.Draw(capa).ellipse((bx - r, by - r, bx + r, by + r), outline=(255, 255, 255, a),
                                                     width=6)
                HE._pegar_escalado(cuadro, capa, (W / 2, y_grupo + alto_grupo / 2), eg)
            falta = total - t
            if falta < 0.4:
                cuadro = Image.blend(cuadro, Image.new("RGBA", (W, H), (214, 80, 4, 255)), 1 - falta / 0.4)
            proc.stdin.write(cuadro.convert("RGB").tobytes())
    finally:
        if proc.stdin:
            proc.stdin.close()
        proc.wait()
    if proc.returncode != 0 or not salida.is_file():
        raise RuntimeError("ffmpeg no armó la historia de clasificados")
    return salida


if __name__ == "__main__":
    import sys
    from utils.config import load_config
    load_config()
    print(armar(Path(sys.argv[1] if len(sys.argv) > 1 else "historia_clasificados.mp4")))
