# -*- coding: utf-8 -*-
"""HISTORIA DE LOS ENTREVISTADOS DE «LA MAÑANA DEL CENTRO» (14-15/11/2026, pedidos del usuario).

Todos los días a las 19:00, una historia 9:16 en Instagram y Facebook, y el mismo video como REEL en las dos
(pedido del 15/11/2026: «que también salga como reel»; HIST_ENTREVISTADOS_REEL=0 lo saca):

- LUNES A VIERNES: «Hoy tuvimos estos entrevistados en nuestro programa La Mañana del Centro», con los reels
  de las entrevistas del día (los recortes que deja el pipeline de la PC de la radio en el buzón del
  distribuidor: state/buzon/entrevistas_*.json).
- SÁBADO Y DOMINGO: «Esta semana tuvimos estos entrevistados…», con TODOS los de lunes a viernes, de a un
  día por pantalla (con el día arriba de la grilla).

Cómo es:
- Los reels entran uno por uno en PANTALLA DIVIDIDA, muteados y con un «pop» cada uno. Cada cuadro muestra el
  reel ENTERO, en su 9:16 original (con su cartel interno con el nombre), lo más grande que entra, con el
  mismo aire arriba (hasta el título) y abajo (hasta la tarjeta).
- Las filas, como pidió el usuario: 1 → 1; 2 → 1/1; 3 → 1/2; 4 → 2/2; 5 → 2/3; 6 → 3/3; 7 → 2/2/3; 8 → 2/2/2/2;
  9 → 3/3/3 (filas_de).
- Un EFECTO DE ENTRADA distinto por día de la semana (EFECTOS: órbita, ruptura de cuadro, zoom digital,
  interfaces flotantes, garabatos, muro de titulares y vidrio), inspirados en el reel de efectos que mandó.
- Después se TIPEA, en dos renglones y con sonido de máquina de escribir, «Contanos a quién te gustaría que
  entrevistemos, / dejanos tu comentario...», en la tipografía del logo (Libre Franklin).
- Al final, una flecha sutil y animada apunta a la caja de «Enviar mensaje» de la historia.
- Fondo naranja vivo; arriba, el isologo y el logo en blanco. Nunca más de 30 segundos (TOPE_SEG).

Los sonidos (pop, teclas, campanita, soplido) se GENERAN acá, sin bajar audio de nadie.

Lo dispara cron-job.org a las 19:00 con `--historia-entrevistados`. Una por día (.historia_entrevistados.json);
si no hubo entrevistas, no sale nada. HIST_ENTREVISTADOS=0 la apaga; HIST_ENTREVISTADOS_FB=0 la saca de
Facebook; HIST_ENTREVISTADOS_EFECTO fija un efecto (para probar).
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import tempfile
import wave
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from utils.config import get
from utils.logger import get_logger

logger = get_logger("historia_entrevistados")

AR = timezone(timedelta(hours=-3))
RAIZ = Path(__file__).parent
BUZON = RAIZ / "state" / "buzon"
LEDGER = RAIZ / ".historia_entrevistados.json"

W, H, FPS, SR = 1080, 1920, 30, 44100
TOPE_SEG = 30                     # pedido: la historia nunca dura más de 30 segundos
MAX_POR_DIA = 9
NARANJA = (253, 133, 17)          # el naranja del isologo (#FD8511)
GRAFITO = (28, 29, 34)
BLANCO = (255, 255, 255)
PROGRAMA = "La Mañana del Centro"
TITULO_DIA = "Hoy tuvimos estos entrevistados"
TITULO_SEMANA = "Esta semana tuvimos estos entrevistados"
SUBTITULO = "en nuestro programa"
# El texto final, en DOS renglones (pedido del 15/11/2026); lo de naranja es RESALTADO.
RENGLONES_CTA = ("Contanos a quién te gustaría que entrevistemos,", "dejanos tu comentario...")
TEXTO_CTA = " ".join(RENGLONES_CTA)
RESALTADO = "dejanos tu comentario..."
DIAS = ("LUNES", "MARTES", "MIÉRCOLES", "JUEVES", "VIERNES", "SÁBADO", "DOMINGO")

# Tiempos (segundos)
T_LOGO, T_TITULO, PASO_PALABRA = 0.0, 0.35, 0.10
PASO_VIDEO = 0.55                 # historia del día: un video nuevo cada tanto, con su pop
PASO_VIDEO_SEMANA = 0.28          # historia de la semana: más rápido (son muchos)
PAGINA_SEMANA_MIN = 3.3           # lo mínimo que se ve cada día en la de la semana
CPS = 17                          # letras por segundo del tipeo
DUR_POP = 0.38
DUR_ENTRADA = 0.75                # lo que dura el efecto de entrada de cada video

# Zonas de la pantalla (Instagram tapa los primeros ~150 px y los últimos ~230 con su interfaz)
Y_LOGO = 160
Y_TARJETA_FIN = 1700              # donde termina la tarjeta del texto (debajo, la flecha)
AIRE = 34                         # el aire mínimo entre los videos y los textos de arriba y de abajo (es el mismo)
SEP = 14
INICIO_CLIP = 1.5                 # de cada reel se salta el primer segundo y medio
# El reel ENTERO, en su tamaño original 9:16 (15/11/2026, pedido del usuario: «los recortes en su tamaño
# original, porque los de videollamadas salen con la cara cortada»): sin recorte, con su cartel interno.
RECORTE_Y0, RECORTE_Y1 = 0, 1920
ASPECTO_CELDA = 1080 / (RECORTE_Y1 - RECORTE_Y0)

# Un efecto por día de la semana (lunes = 0), del reel de efectos que mandó el usuario el 15/11/2026.
EFECTOS = ("orbita", "ruptura", "zoom", "flotar", "garabato", "muro", "vidrio")
NOMBRE_EFECTO = {"orbita": "órbita", "ruptura": "ruptura de cuadro", "zoom": "zoom digital",
                 "flotar": "interfaces flotantes", "garabato": "garabatos animados",
                 "muro": "muro de titulares", "vidrio": "tipografía de vidrio"}


def activo() -> bool:
    return (get("HIST_ENTREVISTADOS") or "1").strip() != "0"


def efecto_de(dia: date) -> str:
    fijo = (get("HIST_ENTREVISTADOS_EFECTO") or "").strip().lower()
    return fijo if fijo in EFECTOS else EFECTOS[dia.weekday() % len(EFECTOS)]


# ─────────────────────────────────────────────────────────────────────────────────────────────
# Las entrevistas
# ─────────────────────────────────────────────────────────────────────────────────────────────
def entrevistas_del_dia(dia: date, carpeta: Path = BUZON) -> list:
    """Las entrevistas que llegaron al buzón ese día (hora argentina), en el orden en que llegaron. Un
    recorte rehecho (job00310-r1) reemplaza al original en el mismo lugar."""
    grupos: dict = {}
    for f in sorted(Path(carpeta).glob("entrevistas_*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            g = datetime.fromisoformat(str(d["generado"]).replace("Z", "+00:00")).astimezone(AR)
        except Exception:  # noqa: BLE001 — una ficha rota no frena las otras
            continue
        if d.get("origen") != "entrevistas" or d.get("tipo") != "video" or not d.get("video_url"):
            continue
        if g.date() != dia:
            continue
        base = re.sub(r"-r\d+$", "", str(d.get("id") or f.stem))
        x = grupos.setdefault(base, {"primero": g, "ultimo": None, "datos": None})
        x["primero"] = min(x["primero"], g)
        if x["ultimo"] is None or g >= x["ultimo"]:
            x["ultimo"], x["datos"] = g, d
    salida = []
    for x in sorted(grupos.values(), key=lambda v: v["primero"]):
        d = x["datos"]
        salida.append({"entrevistado": (d.get("entrevistado") or "").strip(), "titulo": d.get("titulo") or "",
                       "video_url": d["video_url"], "id": d.get("id") or ""})
    return salida[:MAX_POR_DIA]


def paginas_de(dia: date, carpeta: Path = BUZON) -> list:
    """[{"etiqueta", "entrevistas"}]: lunes a viernes, una sola (las del día); sábado y domingo, una por cada
    día hábil de esa semana que tuvo entrevistas, con «LUNES 10/11» de etiqueta."""
    if dia.weekday() < 5:
        e = entrevistas_del_dia(dia, carpeta)
        return [{"etiqueta": "", "entrevistas": e}] if e else []
    lunes = dia - timedelta(days=dia.weekday())
    paginas = []
    for k in range(5):
        d = lunes + timedelta(days=k)
        e = entrevistas_del_dia(d, carpeta)
        if e:
            paginas.append({"etiqueta": f"{DIAS[k]} {d.day}/{d.month}", "entrevistas": e})
    return paginas


# ─────────────────────────────────────────────────────────────────────────────────────────────
# Sonidos (generados acá)
# ─────────────────────────────────────────────────────────────────────────────────────────────
def _t(seg: float) -> np.ndarray:
    return np.arange(int(SR * seg)) / SR


def sonido_pop(tono: float = 1.0) -> np.ndarray:
    """Un «pop» de burbuja: un tono que cae rápido, con un golpecito al principio."""
    t = _t(0.11)
    f = (820 * tono) * np.exp(-t * 28) + 190 * tono
    fase = 2 * np.pi * np.cumsum(f) / SR
    s = np.sin(fase) * np.exp(-t * 38)
    clic = np.random.default_rng(int(tono * 1000)).normal(0, 1, t.size) * np.exp(-t * 900) * 0.35
    ataque = np.minimum(1, t / 0.002)
    return (s + clic) * ataque


def sonido_tecla(rng) -> np.ndarray:
    """Una tecla de máquina de escribir: el clac seco y el golpe del tipo."""
    t = _t(0.06)
    ruido = rng.normal(0, 1, t.size)
    ruido = np.diff(np.concatenate([[0], ruido]))          # más agudo: el «clac»
    clac = ruido * np.exp(-t * rng.uniform(170, 230)) * 0.55
    golpe = np.sin(2 * np.pi * rng.uniform(140, 190) * t) * np.exp(-t * 70) * 0.5
    return (clac + golpe) * rng.uniform(0.75, 1.0)


def sonido_campanita() -> np.ndarray:
    """La campanita del fin de renglón."""
    t = _t(0.9)
    s = (np.sin(2 * np.pi * 2093 * t) + 0.45 * np.sin(2 * np.pi * 4186 * t) + 0.2 * np.sin(2 * np.pi * 3136 * t))
    return s * np.exp(-t * 5.5) * np.minimum(1, t / 0.003) * 0.5


def sonido_brisa(seg: float = 0.35) -> np.ndarray:
    """Un soplido corto y suave (la flecha, el cambio de día)."""
    t = _t(seg)
    ruido = np.random.default_rng(7).normal(0, 1, t.size)
    suave = np.convolve(ruido, np.ones(40) / 40, mode="same")
    env = np.sin(np.pi * np.clip(t / seg, 0, 1)) ** 2
    return suave * env * 0.9


def mezclar(eventos: list, duracion: float) -> np.ndarray:
    """[(segundo, señal, volumen)] → pista estéreo float32 normalizada."""
    pista = np.zeros(int(SR * duracion) + SR)
    for seg, senal, vol in eventos:
        i = int(seg * SR)
        if i >= pista.size or i < 0:
            continue
        n = min(senal.size, pista.size - i)
        pista[i:i + n] += senal[:n] * vol
    pista = pista[:int(SR * duracion)]
    pico = float(np.max(np.abs(pista))) or 1.0
    pista = pista / pico * 0.85
    fade = int(SR * 0.4)
    if pista.size > fade:
        pista[-fade:] *= np.linspace(1, 0, fade)
    return np.stack([pista, pista], axis=1).astype(np.float32)


def guardar_wav(pista: np.ndarray, destino: Path) -> Path:
    datos = (np.clip(pista, -1, 1) * 32767).astype("<i2")
    with wave.open(str(destino), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(datos.tobytes())
    return destino


# ─────────────────────────────────────────────────────────────────────────────────────────────
# Dibujo: fondo, logo, título, tarjeta, flecha
# ─────────────────────────────────────────────────────────────────────────────────────────────
def _fuentes():
    import story_image as S
    return S


def _ease_back(x: float) -> float:
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


def _ease_out(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


def escala_pop(t: float, t0: float, dur: float = DUR_POP) -> float:
    """0 antes de aparecer; rebota un poco por encima de 1 y queda en 1."""
    if t < t0:
        return 0.0
    return _ease_back(min(1.0, (t - t0) / dur))


def fondo() -> Image.Image:
    """Naranja vivo (15/11/2026, pedido del usuario: «un fondo naranja más llamativo»): más claro y cálido en
    el centro, más profundo en los bordes, con un brillo arriba y grano fino."""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    d = np.sqrt(((xx - W * 0.5) / (W * 0.85)) ** 2 + ((yy - H * 0.42) / (H * 0.62)) ** 2)
    a = np.clip(d, 0, 1)[..., None]
    centro = np.array((255, 158, 38), dtype=np.float32)
    borde = np.array((226, 88, 6), dtype=np.float32)
    img = centro * (1 - a) + borde * a
    brillo = np.clip(1 - np.sqrt(((xx - W * 0.78) / 520) ** 2 + ((yy - H * 0.06) / 420) ** 2), 0, 1) ** 2 * 0.35
    img = img * (1 - brillo[..., None]) + 255 * brillo[..., None]
    ruido = np.random.default_rng(3).normal(0, 3.0, (H, W, 1))
    return Image.fromarray(np.clip(img + ruido, 0, 255).astype(np.uint8), "RGB").convert("RGBA")


def _blanquear(im: Image.Image) -> Image.Image:
    """La misma imagen, toda en blanco (conserva la transparencia): el logo sobre el naranja."""
    blanco = Image.new("RGBA", im.size, (255, 255, 255, 255))
    blanco.putalpha(im.getchannel("A"))
    return blanco


def capa_logo() -> Image.Image:
    """Isologo + logo, en blanco y centrados, en una capa transparente del tamaño de la pantalla."""
    S = _fuentes()
    capa = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    lock = _blanquear(S._lockup_marca(360))
    iso_h = lock.height
    iso = S._iso(False)
    iso_w = round(iso.width * iso_h / iso.height) if iso else 0
    total = iso_w + 22 + lock.width
    x = (W - total) // 2
    if iso:
        capa.alpha_composite(iso.convert("RGBA").resize((iso_w, iso_h), Image.LANCZOS), (x, Y_LOGO))
    capa.alpha_composite(lock, (x + iso_w + 22, Y_LOGO))
    return capa


def _sprite_texto(texto: str, fuente, color, fondo_pill=None, pad=(30, 14)) -> Image.Image:
    m = ImageDraw.Draw(Image.new("L", (1, 1)))
    x0, y0, x1, y1 = m.textbbox((0, 0), texto, font=fuente)
    w, h = x1 - x0, y1 - y0
    if fondo_pill:
        im = Image.new("RGBA", (w + 2 * pad[0], h + 2 * pad[1]), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rounded_rectangle((0, 0, im.width - 1, im.height - 1), radius=im.height // 2, fill=fondo_pill)
        d.text((pad[0] - x0, pad[1] - y0), texto, font=fuente, fill=color)
        return im
    # Todas las palabras con el mismo alto (ascendente + descendente): así comparten la línea de base.
    asc, desc = fuente.getmetrics()
    im = Image.new("RGBA", (w + 8, asc + desc + 8), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((4 - x0, 4 + asc), texto, font=fuente, fill=color, anchor="ls")
    return im


def plan_titulo(titulo: str = TITULO_DIA) -> list:
    """[(sprite, centro, t0, es_pill)]: las palabras del título, una por una; «en nuestro programa»; y el
    programa, más grande y en su propio renglón (pedido del 15/11/2026). El último elemento es el más bajo."""
    S = _fuentes()
    m = ImageDraw.Draw(Image.new("L", (1, 1)))
    # En un solo renglón si entra con letra digna (así los videos tienen más lugar); si no, en dos.
    tam = 64
    while tam > 54 and m.textlength(titulo, font=S._sans(tam, 800)) > W - 90:
        tam -= 1
    f1 = S._sans(tam, 800)
    if m.textlength(titulo, font=f1) <= W - 90:
        renglones = [titulo.split()]
    else:
        f1 = S._sans(62, 800)
        renglones, actual = [], []
        for p in titulo.split():
            prueba = " ".join(actual + [p])
            if actual and m.textlength(prueba, font=f1) > W - 120:
                renglones.append(actual)
                actual = [p]
            else:
                actual.append(p)
        renglones.append(actual)
    f2, f3 = S._sans(40, 600), S._sans(60, 800)
    piezas = []
    y = 268
    t = T_TITULO
    esp = m.textlength(" ", font=f1)
    for r in renglones:
        sprites = [_sprite_texto(p, f1, BLANCO) for p in r]
        ancho = sum(sp.width - 8 for sp in sprites) + esp * (len(r) - 1)
        x = (W - ancho) / 2
        for sp in sprites:
            piezas.append((sp, (x + (sp.width - 8) / 2, y + sp.height / 2), t, False))
            x += sp.width - 8 + esp
            t += PASO_PALABRA
        y += round(f1.size * 1.15)
    sub = _sprite_texto(SUBTITULO, f2, BLANCO)
    piezas.append((sub, (W / 2, y + sub.height / 2 + 4), t, False))
    t += PASO_PALABRA * 2
    y += sub.height + 14
    pill = _sprite_texto(f"«{PROGRAMA}»", f3, NARANJA, fondo_pill=BLANCO, pad=(36, 14))
    piezas.append((pill, (W / 2, y + pill.height / 2), t, True))
    return piezas


def fin_titulo(piezas: list) -> int:
    sprite, centro, _, _ = piezas[-1]
    return int(centro[1] + sprite.height / 2)


def plan_tarjeta() -> dict:
    """Los dos renglones del texto que se tipea, con la letra más grande que entre en la tarjeta."""
    S = _fuentes()
    m = ImageDraw.Draw(Image.new("L", (1, 1)))
    ancho = W - 2 * 70 - 2 * 44
    tam = 50
    while tam > 26 and max(m.textlength(r, font=S._franklin(tam, 800)) for r in RENGLONES_CTA) > ancho:
        tam -= 1
    f = S._franklin(tam, 800)
    paso = round(tam * 1.32)
    pad = 34
    inicio_resaltado = TEXTO_CTA.index(RESALTADO)
    return {"fuente": f, "renglones": list(RENGLONES_CTA), "alto": len(RENGLONES_CTA) * paso + 2 * pad,
            "paso": paso, "pad": pad, "tam": tam,
            "resaltado": (inicio_resaltado, inicio_resaltado + len(RESALTADO))}


def dibujar_tarjeta(capa: Image.Image, plan: dict, letras: int, cursor: bool, escala: float, y_tarjeta: int):
    """La tarjeta blanca con el texto tipeado hasta `letras` y el cursor naranja."""
    if escala <= 0:
        return
    tw, th = W - 140, plan["alto"]
    tarjeta = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    d = ImageDraw.Draw(tarjeta)
    d.rounded_rectangle((0, 0, tw - 1, th - 1), radius=38, fill=(255, 255, 255, 250))
    f, pad = plan["fuente"], plan["pad"]
    x0 = 44
    y = pad
    hecho = 0
    a, b = plan["resaltado"]
    x_cursor, y_cursor = x0, y
    for r in plan["renglones"]:
        x = x0
        for i, ch in enumerate(r):
            pos = hecho + i
            if pos >= letras:
                break
            d.text((x, y), ch, font=f, fill=NARANJA if a <= pos < b else GRAFITO)
            x += d.textlength(ch, font=f)
            x_cursor, y_cursor = x, y
        if hecho + len(r) >= letras:
            break
        hecho += len(r) + 1                      # el espacio entre los dos renglones
        y += plan["paso"]
        x_cursor, y_cursor = x0, y
    if cursor:
        d.rounded_rectangle((x_cursor + 4, y_cursor + 4, x_cursor + 10, y_cursor + plan["tam"] + 6), radius=3,
                            fill=NARANJA)
    sombra = Image.new("RGBA", (tw + 60, th + 60), (0, 0, 0, 0))
    ImageDraw.Draw(sombra).rounded_rectangle((30, 40, tw + 30, th + 40), radius=38, fill=(110, 40, 0, 110))
    sombra = sombra.filter(ImageFilter.GaussianBlur(18))
    sombra.alpha_composite(tarjeta, (30, 30))
    _pegar_escalado(capa, sombra, (W / 2, y_tarjeta + th / 2), escala)


def _pegar_escalado(capa: Image.Image, im: Image.Image, centro: tuple, escala: float, alfa: float = 1.0):
    if escala <= 0 or alfa <= 0:
        return
    if abs(escala - 1) > 0.002:
        im = im.resize((max(1, round(im.width * escala)), max(1, round(im.height * escala))), Image.BILINEAR)
    if alfa < 0.999:
        im = im.copy()
        im.putalpha(im.getchannel("A").point(lambda v, a=alfa: int(v * a)))
    capa.alpha_composite(im, (round(centro[0] - im.width / 2), round(centro[1] - im.height / 2)))


def _bezier(p0, p1, p2, p3, n=60):
    pts = []
    for i in range(n + 1):
        u = i / n
        a, b, c, d = (1 - u) ** 3, 3 * u * (1 - u) ** 2, 3 * u ** 2 * (1 - u), u ** 3
        pts.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return pts


def dibujar_flecha(capa: Image.Image, avance: float, t: float, y_tarjeta_fin: int):
    """Una flecha fina y curva que baja hacia la caja de «Enviar mensaje» (abajo a la izquierda). Se dibuja
    sola (`avance` de 0 a 1) y después se mece suave."""
    if avance <= 0:
        return
    mece = math.sin(2 * math.pi * t / 1.3)
    dx, dy = -7 * mece, 7 * mece
    # Baja desde abajo de la tarjeta, hace una curva y termina apuntando abajo a la izquierda, donde
    # Instagram pone la caja de «Enviar mensaje».
    y0 = y_tarjeta_fin + 22
    y_fin = max(1792, y0 + 80)                   # siempre termina bajando hacia la caja de mensaje
    pts = _bezier((610, y0), (612, y0 + 0.55 * (y_fin - y0) + 50), (345, y_fin - 70), (232 + dx, y_fin + dy))
    k = max(2, int(len(pts) * min(1.0, avance)))
    d = ImageDraw.Draw(capa)
    d.line(pts[:k], fill=(255, 255, 255, 235), width=7, joint="curve")
    for x, y in (pts[0], pts[k - 1]):
        d.ellipse((x - 3.5, y - 3.5, x + 3.5, y + 3.5), fill=(255, 255, 255, 235))
    if avance >= 1:
        (xa, ya), (xb, yb) = pts[-4], pts[-1]
        ang = math.atan2(yb - ya, xb - xa)
        for s in (+1, -1):
            a2 = ang + math.pi + s * math.radians(32)
            d.line([(xb, yb), (xb + 38 * math.cos(a2), yb + 38 * math.sin(a2))], fill=(255, 255, 255, 235), width=7)
            d.ellipse((xb + 38 * math.cos(a2) - 3.5, yb + 38 * math.sin(a2) - 3.5,
                       xb + 38 * math.cos(a2) + 3.5, yb + 38 * math.sin(a2) + 3.5), fill=(255, 255, 255, 235))
        r = 10 + 4 * (0.5 + 0.5 * math.sin(2 * math.pi * t / 0.9))
        d.ellipse((xb - 46 - r, yb + 34 - r, xb - 46 + r, yb + 34 + r), outline=(255, 255, 255, 210), width=4)


# ─────────────────────────────────────────────────────────────────────────────────────────────
# La pantalla dividida
# ─────────────────────────────────────────────────────────────────────────────────────────────
def filas_de(n: int) -> list:
    """Cuántos cuadros por fila, de arriba abajo (pedidos del usuario del 15/11/2026). Los impares, con menos
    arriba (7 = 2/2/3); los pares, parejos; 8, de a dos en cuatro filas."""
    fijas = {1: [1], 2: [1, 1], 3: [1, 2], 4: [2, 2], 5: [2, 3], 6: [3, 3], 7: [2, 2, 3], 8: [2, 2, 2, 2],
             9: [3, 3, 3]}
    if n in fijas:
        return fijas[n]
    k = math.ceil(n / 4)
    base, extra = divmod(n, k)
    return [base] * (k - extra) + [base + 1] * extra


def grilla(n: int, caja: tuple, sep: int = SEP) -> list:
    """Las n celdas (x, y, ancho, alto) de la pantalla dividida con las filas de filas_de(), todas con la forma
    del recorte del reel (ASPECTO_CELDA), lo más grandes posible, cada fila centrada y la grilla arriba."""
    if n <= 0:
        return []
    x0, y0, x1, y1 = caja
    cw, ch = x1 - x0, y1 - y0
    filas = filas_de(n)
    cols = max(filas)
    w = min((cw - sep * (cols - 1)) / cols, (ch - sep * (len(filas) - 1)) / len(filas) * ASPECTO_CELDA)
    w = int(w) // 2 * 2
    h = int(w / ASPECTO_CELDA) // 2 * 2
    celdas = []
    y = y0
    for en_fila in filas:
        ancho_fila = en_fila * w + sep * (en_fila - 1)
        x = x0 + (cw - ancho_fila) // 2
        for _ in range(en_fila):
            celdas.append((x, y, w, h))
            x += w + sep
        y += h + sep
    return celdas


def _recorte() -> tuple:
    """La franja del reel que entra en la celda (ancho completo, de la cara al cartel con el nombre)."""
    return 1080, (RECORTE_Y1 - RECORTE_Y0) // 2 * 2, 0, RECORTE_Y0


class Clip:
    """Lee los cuadros de un reel ya recortados a su celda, de a uno, desde que aparece en pantalla."""

    def __init__(self, ruta: Path, w: int, h: int, segundos: float):
        self.ruta, self.w, self.h, self.seg = ruta, w, h, segundos
        self.proc, self.ultimo = None, None

    def cuadro(self) -> Image.Image:
        if self.proc is None:
            from video import _ffmpeg
            cw, ch, cx, cy = _recorte()
            filtro = f"crop={cw}:{ch}:{cx}:{cy},scale={self.w}:{self.h}:flags=lanczos,fps={FPS},format=rgb24"
            self.proc = subprocess.Popen(
                [_ffmpeg(), "-loglevel", "error", "-i", str(self.ruta), "-t", str(self.seg + 1), "-an", "-vf", filtro,
                 "-f", "rawvideo", "pipe:1"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        n = self.w * self.h * 3
        datos = self.proc.stdout.read(n) if self.proc.stdout else b""
        if len(datos) == n:
            self.ultimo = Image.frombytes("RGB", (self.w, self.h), datos)
        if self.ultimo is None:
            self.ultimo = Image.new("RGB", (self.w, self.h), GRAFITO)
        return self.ultimo

    def cerrar(self):
        if self.proc:
            try:
                self.proc.kill()
            except Exception:  # noqa: BLE001
                pass
            self.proc = None


BORDE_SOMBRA = 26                 # lugar alrededor de cada cuadro para el borde y la sombra


def _marco(w: int, h: int, efecto: str) -> Image.Image:
    """El fondo de una celda: sombra suave y el borde (blanco; esmerilado en el efecto «vidrio»)."""
    b = BORDE_SOMBRA
    im = Image.new("RGBA", (w + 2 * b, h + 2 * b), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle((b - 4, b + 6, b + w + 4, b + h + 14), radius=30, fill=(120, 40, 0, 120))
    im = im.filter(ImageFilter.GaussianBlur(10))
    d = ImageDraw.Draw(im)
    if efecto == "vidrio":
        capa = Image.new("RGBA", im.size, (0, 0, 0, 0))
        dc = ImageDraw.Draw(capa)
        dc.rounded_rectangle((b - 14, b - 14, b + w + 13, b + h + 13), radius=36, fill=(255, 255, 255, 95))
        dc.rounded_rectangle((b - 14, b - 14, b + w + 13, b + h + 13), radius=36, outline=(255, 255, 255, 230),
                             width=2)
        dc.rounded_rectangle((b - 8, b - 10, b + w + 7, b + h // 3), radius=30, fill=(255, 255, 255, 45))
        im.alpha_composite(capa)
    else:
        d.rounded_rectangle((b - 6, b - 6, b + w + 5, b + h + 5), radius=30, fill=(255, 255, 255, 255))
    return im


def _mascara(w: int, h: int, r: int) -> Image.Image:
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=255)
    return m


def transformar(efecto: str, u: float, t: float, i: int, n: int, celda: tuple, centro_grilla: tuple) -> dict:
    """Dónde y cómo se ve el cuadro i a `u` segundos de su entrada: {escala, dx, dy, angulo, alfa, desenfoque}."""
    x, y, w, h = celda
    cx, cy = x + w / 2, y + h / 2
    e = _ease_out(u / DUR_ENTRADA)
    r = {"escala": 1.0, "dx": 0.0, "dy": 0.0, "angulo": 0.0, "alfa": 1.0, "desenfoque": 0.0}
    if u < 0:
        r["alfa"] = 0.0
        return r
    if efecto == "orbita":
        # Llega girando alrededor del centro de la grilla, en espiral, hasta su lugar.
        th = 2 * math.pi * i / max(1, n) + 2.2 * math.pi * (1 - e)
        rad = 420 * (1 - e)
        ox, oy = centro_grilla[0] + rad * math.cos(th), centro_grilla[1] + rad * math.sin(th) * 0.55
        r.update(dx=(ox - cx) * (1 - e), dy=(oy - cy) * (1 - e), escala=0.3 + 0.7 * _ease_back(min(1, u / DUR_ENTRADA)),
                 alfa=min(1.0, u / 0.15))
    elif efecto == "ruptura":
        # Rompe un marco: entra desde abajo, pasa de largo y se acomoda.
        r.update(escala=0.55 + 0.45 * _ease_back(min(1, u / 0.5)), dy=70 * (1 - e), alfa=min(1.0, u / 0.12))
    elif efecto == "zoom":
        # Zoom digital: desde muy cerca hasta su lugar.
        r.update(escala=1 + 1.4 * (1 - e) ** 2, alfa=min(1.0, u / 0.25), desenfoque=6 * (1 - e) ** 2)
    elif efecto == "flotar":
        # Interfaces flotando: suben y quedan meciéndose suave.
        r.update(dy=110 * (1 - e) + 7 * math.sin(2 * math.pi * (t / 2.6 + i * 0.37)),
                 dx=3 * math.sin(2 * math.pi * (t / 3.1 + i * 0.21)), escala=0.92 + 0.08 * e, alfa=min(1.0, u / 0.3))
    elif efecto == "garabato":
        r.update(escala=escala_pop(u, 0.0))
    elif efecto == "muro":
        # Muro de titulares: caen como figuritas, inclinadas, y se enderezan.
        lado = -1 if i % 2 else 1
        r.update(dy=-320 * (1 - _ease_back(min(1, u / 0.6))), angulo=lado * 11 * (1 - e), alfa=min(1.0, u / 0.1))
    elif efecto == "vidrio":
        # Vidrio: aparece desenfocado y se aclara.
        r.update(escala=1.06 - 0.06 * e, alfa=e, desenfoque=10 * (1 - e))
    else:
        r.update(escala=escala_pop(u, 0.0))
    return r


def decorar(capa: Image.Image, efecto: str, u: float, t: float, i: int, celda: tuple, cuadro_n: int):
    """Lo que el efecto dibuja alrededor del cuadro (anillos, esquinas de cámara, garabatos)."""
    x, y, w, h = celda
    if u < 0:
        return
    d = ImageDraw.Draw(capa)
    if efecto == "ruptura" and u < 0.7:
        k = u / 0.7
        m = 12 + 60 * k
        a = int(230 * (1 - k))
        d.rounded_rectangle((x - m, y - m, x + w + m, y + h + m), radius=34, outline=(255, 255, 255, a), width=5)
    elif efecto == "zoom" and u < 1.3:
        k = _ease_out(u / DUR_ENTRADA)
        m = 10 + 50 * (1 - k)
        a = int(240 * max(0.0, 1 - max(0.0, u - 0.8) / 0.5))
        L = 46
        for (px, py, sx, sy) in ((x - m, y - m, 1, 1), (x + w + m, y - m, -1, 1), (x - m, y + h + m, 1, -1),
                                 (x + w + m, y + h + m, -1, -1)):
            d.line([(px, py + sy * L), (px, py), (px + sx * L, py)], fill=(255, 255, 255, a), width=6)
        if u < 0.6:
            cx, cy = x + w / 2, y + h / 2
            d.line([(cx - 26, cy), (cx + 26, cy)], fill=(255, 255, 255, a), width=4)
            d.line([(cx, cy - 26), (cx, cy + 26)], fill=(255, 255, 255, a), width=4)
    elif efecto == "orbita" and u < DUR_ENTRADA:
        a = int(110 * (1 - u / DUR_ENTRADA))
        cx, cy = x + w / 2, y + h / 2
        d.ellipse((cx - w * 0.8, cy - h * 0.8, cx + w * 0.8, cy + h * 0.8), outline=(255, 255, 255, a), width=3)


def decorar_grilla(capa: Image.Image, caja: tuple, alfa: float, cuadro_n: int):
    """«Garabatos animados» (doodle boil): un contorno a mano alrededor de TODA la pantalla dividida y unas
    rayitas de brillo en dos esquinas, que «hierven» (cambian un poquito cada 4 cuadros)."""
    if alfa <= 0:
        return
    x0, y0, x1, y1 = caja
    m = 20
    rng = np.random.default_rng(cuadro_n // 4 * 131 + 7)
    d = ImageDraw.Draw(capa)
    a = int(235 * alfa)
    for _ in range(2):
        pts = []
        for (px, py) in ((x0 - m, y0 - m), ((x0 + x1) / 2, y0 - m - 6), (x1 + m, y0 - m), (x1 + m + 4, (y0 + y1) / 2),
                         (x1 + m, y1 + m), ((x0 + x1) / 2, y1 + m + 6), (x0 - m, y1 + m), (x0 - m - 4, (y0 + y1) / 2),
                         (x0 - m, y0 - m)):
            pts.append((px + rng.uniform(-5, 5), py + rng.uniform(-5, 5)))
        d.line(pts, fill=(255, 255, 255, a), width=5, joint="curve")
    for (ex, ey, sg) in ((x1 + m, y0 - m, 1), (x0 - m, y1 + m, -1)):
        for ang in (-70, -30, 10):
            ra = math.radians(ang)
            l0, l1 = 16 + rng.uniform(-2, 2), 44 + rng.uniform(-3, 3)
            d.line([(ex + sg * l0 * math.cos(ra), ey + sg * l0 * math.sin(ra)),
                    (ex + sg * l1 * math.cos(ra), ey + sg * l1 * math.sin(ra))], fill=(255, 255, 255, a), width=5)


def _cuadro_celda(base_marco: Image.Image, foto: Image.Image, mascara: Image.Image, tr: dict) -> Image.Image:
    celda = base_marco.copy()
    celda.paste(foto, (BORDE_SOMBRA, BORDE_SOMBRA), mascara)
    if tr["desenfoque"] > 0.4:
        celda = celda.filter(ImageFilter.GaussianBlur(tr["desenfoque"]))
    if abs(tr["angulo"]) > 0.2:
        celda = celda.rotate(tr["angulo"], resample=Image.BICUBIC, expand=True)
    return celda


# ─────────────────────────────────────────────────────────────────────────────────────────────
# La línea de tiempo y el video
# ─────────────────────────────────────────────────────────────────────────────────────────────
def linea_de_tiempo(paginas: list, semanal: bool) -> dict:
    """Cuándo aparece cada cosa y cuánto dura la historia (nunca más de TOPE_SEG)."""
    titulo = plan_titulo(TITULO_SEMANA if semanal else TITULO_DIA)
    t_videos = titulo[-1][2] + DUR_POP + 0.35
    paso = PASO_VIDEO_SEMANA if semanal else PASO_VIDEO
    pags, t = [], t_videos
    for p in paginas:
        n = len(p["entrevistas"])
        dur = max(PAGINA_SEMANA_MIN, n * paso + 2.3) if semanal else 0.0
        pags.append({"t0": t, "t1": t + dur, "entradas": [t + 0.25 * bool(semanal) + k * paso for k in range(n)]})
        t += dur
    ultima_entrada = pags[0]["entradas"][-1] if pags else t_videos
    t_tarjeta = ultima_entrada + 0.6
    t_tipeo = t_tarjeta + 0.45
    t_fin_tipeo = t_tipeo + len(TEXTO_CTA) / CPS
    t_flecha = t_fin_tipeo + 0.5
    total = max(16.0, t_flecha + 5.0, (pags[-1]["t0"] + 3.0) if pags else 0)
    if semanal:
        total = max(total, pags[-1]["t1"] if pags else 0)
    total = min(TOPE_SEG - 0.5, total)
    if pags:
        pags[-1]["t1"] = total + 1                   # la última página queda hasta el final
    return {"titulo": titulo, "t_videos": t_videos, "paginas": pags, "t_tarjeta": t_tarjeta, "t_tipeo": t_tipeo,
            "t_fin_tipeo": t_fin_tipeo, "t_flecha": t_flecha, "total": total}


def sonidos_de(tl: dict, seed: int = 1) -> list:
    rng = np.random.default_rng(seed)
    ev = [(t0, sonido_pop(1.25), 0.20 if not pill else 0.55) for _, _, t0, pill in tl["titulo"]]
    for k, p in enumerate(tl["paginas"]):
        if k:
            ev.append((p["t0"] - 0.1, sonido_brisa(0.45), 0.35))
        ev += [(te, sonido_pop(0.9 + 0.08 * (i % 4)), 0.7) for i, te in enumerate(p["entradas"])]
    ev.append((tl["t_tarjeta"], sonido_pop(0.8), 0.45))
    for i, ch in enumerate(TEXTO_CTA):
        if ch != " ":
            ev.append((tl["t_tipeo"] + i / CPS + rng.uniform(-0.008, 0.008), sonido_tecla(rng), 0.5))
    ev.append((tl["t_fin_tipeo"] + 0.05, sonido_campanita(), 0.35))
    ev.append((tl["t_flecha"], sonido_brisa(), 0.25))
    return ev


def armar(paginas: list, salida: Path, efecto: str, semanal: bool = False, carpeta: Path | None = None) -> Path:
    """El .mp4 9:16 de la historia. `paginas[k]["entrevistas"][i]["archivo"]`: el tramo del reel ya bajado."""
    from video import _ffmpeg
    salida = Path(salida)
    carpeta = Path(carpeta or salida.parent)
    tl = linea_de_tiempo(paginas, semanal)
    total = tl["total"]
    tarjeta = plan_tarjeta()
    S = _fuentes()
    arriba = fin_titulo(tl["titulo"])
    etiquetas = []
    if semanal:
        # El día, con la misma estética que el programa: píldora blanca con letras naranjas.
        for p in paginas:
            etiquetas.append(_sprite_texto(p["etiqueta"], S._sans(46, 800), NARANJA, fondo_pill=BLANCO,
                                           pad=(30, 12)))
        y_etiqueta = arriba + 22 + etiquetas[0].height // 2
        arriba += 22 + etiquetas[0].height
    y_tarjeta = Y_TARJETA_FIN - tarjeta["alto"]
    # Cada día su grilla, lo más grande que entra entre el texto de arriba y la tarjeta con AIRE de cada lado, y
    # centrada en ese hueco: el mismo aire arriba y abajo (pedido del 15/11/2026).
    grillas = []
    for p in paginas:
        g = grilla(len(p["entrevistas"]), (24, arriba + AIRE, W - 24, y_tarjeta - AIRE))
        alto_g = max(c[1] + c[3] for c in g) - min(c[1] for c in g)
        y0 = arriba + round((y_tarjeta - arriba - alto_g) / 2)
        dy = y0 - min(c[1] for c in g)
        grillas.append([(x, y + dy, w, h) for (x, y, w, h) in g])
    plan = []
    for p, celdas in zip(paginas, grillas):
        xs = [c[0] for c in celdas] + [c[0] + c[2] for c in celdas]
        ys = [c[1] for c in celdas] + [c[1] + c[3] for c in celdas]
        plan.append({"celdas": celdas, "centro": ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2),
                     "clips": [Clip(Path(e["archivo"]), c[2], c[3], total) for e, c in zip(p["entrevistas"], celdas)],
                     "marcos": [_marco(c[2], c[3], efecto) for c in celdas],
                     "mascaras": [_mascara(c[2], c[3], 26) for c in celdas]})
    base = fondo()
    logo = capa_logo()
    wav = guardar_wav(mezclar(sonidos_de(tl), total), carpeta / "historia_entrevistados.wav")
    cuadros = int(total * FPS)
    proc = subprocess.Popen(
        [_ffmpeg(), "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
         "-r", str(FPS), "-i", "pipe:0", "-i", str(wav), "-map", "0:v", "-map", "1:a", "-c:v", "libx264",
         "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
         "-ar", str(SR), "-shortest", "-movflags", "+faststart", str(salida)],
        stdin=subprocess.PIPE)
    fijo_titulo = None
    try:
        for k in range(cuadros):
            t = k / FPS
            cuadro = base.copy()
            # Logo: baja y aparece.
            a = min(1.0, max(0.0, (t - T_LOGO) / 0.45))
            if a >= 1:
                cuadro.alpha_composite(logo)
            elif a > 0:
                capa = logo.copy()
                capa.putalpha(capa.getchannel("A").point(lambda v, a=a: int(v * a)))
                cuadro.alpha_composite(capa, (0, round(-18 * (1 - a))))
            # Título: palabra por palabra (cuando terminó, queda fijo en una capa).
            if fijo_titulo is not None:
                cuadro.alpha_composite(fijo_titulo)
            else:
                capa = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                for sprite, centro, t0, _ in tl["titulo"]:
                    _pegar_escalado(capa, sprite, centro, escala_pop(t, t0))
                cuadro.alpha_composite(capa)
                if t > tl["titulo"][-1][2] + DUR_POP:
                    fijo_titulo = capa
            # La página (el día) que toca.
            deco = None
            for pi, (pag, pl) in enumerate(zip(tl["paginas"], plan)):
                if not (pag["t0"] <= t < pag["t1"]):
                    if t >= pag["t1"]:
                        for c in pl["clips"]:
                            c.cerrar()
                    continue
                salida_k = 1.0
                if t > pag["t1"] - 0.3 and pi < len(plan) - 1:
                    salida_k = max(0.0, (pag["t1"] - t) / 0.3)      # se van achicando y apagando
                if semanal:
                    _pegar_escalado(cuadro, etiquetas[pi], (W / 2, y_etiqueta), escala_pop(t, pag["t0"]) * (
                        0.9 + 0.1 * salida_k), salida_k)
                n = len(pl["celdas"])
                for i, cel in enumerate(pl["celdas"]):
                    u = t - pag["entradas"][i]
                    if u < 0:
                        continue
                    tr = transformar(efecto, u, t, i, n, cel, pl["centro"])
                    if tr["alfa"] <= 0 or tr["escala"] <= 0:
                        continue
                    foto = pl["clips"][i].cuadro()
                    celda = _cuadro_celda(pl["marcos"][i], foto, pl["mascaras"][i], tr)
                    x, y, w, h = cel
                    _pegar_escalado(cuadro, celda, (x + w / 2 + tr["dx"], y + h / 2 + 6 + tr["dy"]),
                                    tr["escala"] * (0.85 + 0.15 * salida_k), tr["alfa"] * salida_k)
                    if salida_k > 0.5:
                        if deco is None:
                            deco = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                        decorar(deco, efecto, u, t, i, (x + tr["dx"], y + tr["dy"], w, h), k)
            if efecto == "garabato":
                for pag, pl in zip(tl["paginas"], plan):
                    if pag["t0"] <= t < pag["t1"] and pl["celdas"]:
                        a = min(1.0, max(0.0, (t - pag["entradas"][0] - 0.3) / 0.4))
                        if t > pag["t1"] - 0.3 and pag is not tl["paginas"][-1]:
                            a *= max(0.0, (pag["t1"] - t) / 0.3)
                        cs = pl["celdas"]
                        if deco is None:
                            deco = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                        decorar_grilla(deco, (min(c[0] for c in cs), min(c[1] for c in cs),
                                              max(c[0] + c[2] for c in cs), max(c[1] + c[3] for c in cs)), a, k)
            if deco is not None:
                cuadro.alpha_composite(deco)
            # El texto que se tipea.
            letras = int(max(0.0, t - tl["t_tipeo"]) * CPS)
            escribiendo = tl["t_tipeo"] <= t < tl["t_fin_tipeo"]
            cursor = escribiendo or (int(t * 2) % 2 == 0)
            dibujar_tarjeta(cuadro, tarjeta, min(letras, len(TEXTO_CTA)), cursor and t >= tl["t_tarjeta"] + 0.2,
                            escala_pop(t, tl["t_tarjeta"]), y_tarjeta)
            # La flecha hacia el mensaje directo.
            dibujar_flecha(cuadro, (t - tl["t_flecha"]) / 0.55, t, y_tarjeta + tarjeta["alto"])
            # Fundido final.
            falta = total - t
            if falta < 0.4:
                cuadro = Image.blend(cuadro, Image.new("RGBA", (W, H), (214, 80, 4, 255)), 1 - falta / 0.4)
            proc.stdin.write(cuadro.convert("RGB").tobytes())
    finally:
        for pl in plan:
            for c in pl["clips"]:
                c.cerrar()
        if proc.stdin:
            proc.stdin.close()
        proc.wait()
    if proc.returncode != 0 or not salida.is_file():
        raise RuntimeError("ffmpeg no armó la historia de los entrevistados")
    return salida


# ─────────────────────────────────────────────────────────────────────────────────────────────
# La corrida de las 19
# ─────────────────────────────────────────────────────────────────────────────────────────────
def _leer_ledger() -> dict:
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else {}
    except Exception:  # noqa: BLE001
        return {}


def _guardar_ledger(d: dict):
    claves = sorted(d)[-60:]
    LEDGER.write_text(json.dumps({k: d[k] for k in claves}, ensure_ascii=False, indent=2), encoding="utf-8")


def _bajar_tramo(url: str, destino: Path, segundos: float) -> Path:
    """Solo el tramo que se usa del reel (desde INICIO_CLIP, `segundos`), sin bajar el archivo entero: con
    los de toda la semana serían cientos de megas. Si ffmpeg no puede leer la URL, se baja entero."""
    from video import _ffmpeg
    r = subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-ss", str(INICIO_CLIP), "-i", url, "-t",
                        f"{segundos:.1f}", "-an", "-c", "copy", str(destino)], capture_output=True, timeout=180)
    if r.returncode == 0 and destino.is_file() and destino.stat().st_size > 20_000:
        return destino
    import requests
    entero = destino.with_name(destino.stem + "_entero.mp4")
    with requests.get(url, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        with open(entero, "wb") as f:
            for trozo in resp.iter_content(1 << 20):
                f.write(trozo)
    r = subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-ss", str(INICIO_CLIP), "-i", str(entero), "-t",
                        f"{segundos:.1f}", "-an", "-c", "copy", str(destino)], capture_output=True, timeout=180)
    entero.unlink(missing_ok=True)
    if r.returncode != 0 or not destino.is_file():
        raise RuntimeError("no se pudo cortar el tramo del reel")
    return destino


def preparar(dia: date, carpeta: Path) -> list:
    """Las páginas con el tramo de cada reel ya bajado (lo que no se pudo bajar queda afuera)."""
    salida = []
    for k, p in enumerate(paginas_de(dia)):
        buenas = []
        for i, e in enumerate(p["entrevistas"]):
            try:
                e["archivo"] = str(_bajar_tramo(e["video_url"], carpeta / f"entrevista_{k}_{i:02d}.mp4", TOPE_SEG))
                buenas.append(e)
            except Exception as ex:  # noqa: BLE001
                logger.warning(f"No pude bajar la entrevista de {e['entrevistado'] or e['id']}: {ex}")
        if buenas:
            salida.append(dict(p, entrevistas=buenas))
    return salida


HASHTAGS = "#Chivilcoy #DiarioLaCampaña #RadioDelCentro #LaMañanaDelCentro #Entrevistas"


def _lista(nombres: list) -> str:
    nombres = [n for n in nombres if n]
    if len(nombres) <= 1:
        return "".join(nombres)
    return ", ".join(nombres[:-1]) + " y " + nombres[-1]


def texto_reel(nombres: list, semanal: bool) -> str:
    """El texto del reel (sin link, como todo lo de Instagram)."""
    cuando = "Esta semana" if semanal else "Hoy"
    return (f"🎙️ {cuando} en nuestro programa «{PROGRAMA}» estuvieron {_lista(nombres)}.\n\n"
            f"¿A quién te gustaría que entrevistemos? Dejanos tu comentario 👇\n\n"
            f"📻 Radio del Centro · Diario La Campaña\n\n{HASHTAGS}")


def correr(dry: bool = False, dia: date | None = None, forzar: bool = False, salida: Path | None = None) -> dict:
    """Arma y publica la historia del día (o la de la semana, el finde). Devuelve un informe."""
    if not activo():
        logger.info("Historia de los entrevistados apagada (HIST_ENTREVISTADOS=0).")
        return {"estado": "apagada"}
    dia = dia or datetime.now(AR).date()
    clave = dia.isoformat()
    semanal = dia.weekday() >= 5
    ledger = _leer_ledger()
    if (ledger.get(clave) or {}).get("estado") == "publicada" and not forzar:
        logger.info(f"La historia de los entrevistados del {clave} ya salió.")
        return {"estado": "ya_salio"}
    tmp = Path(tempfile.mkdtemp(prefix="hist_entrev_"))
    try:
        paginas = preparar(dia, tmp)
        if not paginas:
            logger.info(f"El {clave} no hay entrevistas {'de la semana' if semanal else 'del día'}: no sale la historia.")
            return {"estado": "sin_entrevistas"}
        efecto = efecto_de(dia)
        nombres = [e["entrevistado"] or "?" for p in paginas for e in p["entrevistas"]]
        logger.info(f"Historia de los entrevistados del {clave} ({'semana' if semanal else 'día'}, efecto "
                    f"{NOMBRE_EFECTO[efecto]}): {len(nombres)} ({', '.join(nombres)}).")
        mp4 = armar(paginas, tmp / f"historia_entrevistados_{dia:%Y%m%d}.mp4", efecto, semanal, tmp)
        if salida:
            shutil.copy(mp4, salida)
        if dry:
            logger.info(f"[dry-run] Historia armada: {salida or mp4}")
            return {"estado": "simulada", "entrevistas": len(nombres), "efecto": efecto, "archivo": str(salida or mp4)}
        from platforms import facebook, instagram
        from utils.video_host import upload_reel
        url = upload_reel(mp4)
        informe = {"estado": "publicada", "cuando": datetime.now(AR).isoformat(timespec="seconds"),
                   "semanal": semanal, "efecto": efecto, "entrevistados": nombres}
        res = instagram.publish_video_story(url)
        informe["instagram"] = (res or {}).get("id") or "ok"
        logger.info(f"[instagram] historia de los entrevistados OK ({informe['instagram']})")
        if (get("HIST_ENTREVISTADOS_FB") or "1").strip() != "0":
            try:
                rf = facebook.publish_video_story(mp4)
                informe["facebook"] = (rf or {}).get("id") or "ok"
                logger.info("[facebook] historia de los entrevistados OK")
            except Exception as e:  # noqa: BLE001 — Facebook no frena la de Instagram
                informe["facebook"] = f"falló: {str(e)[:160]}"
                logger.warning(f"[facebook] historia de los entrevistados: {e}")
        if (get("HIST_ENTREVISTADOS_REEL") or "1").strip() != "0":
            texto = texto_reel(nombres, semanal)
            tl = linea_de_tiempo(paginas, semanal)
            portada_ms = int(min(tl["total"] - 0.6, tl["t_fin_tipeo"] + 0.4) * 1000)
            try:
                rr = instagram.publish_reel(url, texto, colab=False, portada_ms=portada_ms)
                informe["instagram_reel"] = (rr or {}).get("id") or "ok"
                logger.info(f"[instagram] reel de los entrevistados OK ({informe['instagram_reel']})")
            except Exception as e:  # noqa: BLE001 — la historia ya salió: el reel no la voltea
                informe["instagram_reel"] = f"falló: {str(e)[:160]}"
                logger.warning(f"[instagram] reel de los entrevistados: {e}")
            if (get("HIST_ENTREVISTADOS_FB") or "1").strip() != "0":
                try:
                    rfr = facebook.publish_video(texto, mp4)
                    informe["facebook_reel"] = (rfr or {}).get("id") or "ok"
                    logger.info("[facebook] reel de los entrevistados OK")
                except Exception as e:  # noqa: BLE001
                    informe["facebook_reel"] = f"falló: {str(e)[:160]}"
                    logger.warning(f"[facebook] reel de los entrevistados: {e}")
        ledger[clave] = informe
        _guardar_ledger(ledger)
        return informe
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
