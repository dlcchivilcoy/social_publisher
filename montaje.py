"""Montaje de VARIOS videos de un mismo hecho en un solo reel (pedido del usuario 2026-10-03).

El «prompt detallado para editar reels» que mandó el usuario pide que, cuando llegan varios
videos —varias cámaras de seguridad, o videos sueltos de un hecho—, el reel no sea uno pegado
detrás del otro sino un MONTAJE:

  · SIMULTÁNEO: 2 o 3 cámaras a la vez, sincronizadas por sus relojes. Con tres, una grande
    arriba (1080x1344) y dos chicas abajo (540x576); con dos, una arriba y otra abajo
    (1080x960). La grande cambia según dónde está la acción, y todas cortan juntas. Así quedó el
    reel de Lavilimp que el usuario armó a mano: ingreso → interior → exterior.
  · SECUENCIAL: un plano por vez, alternando videos con cortes, quitando esperas.

Lo decide Gemini mirando los videos (`gemini.plan_montaje`); acá se VALIDA ese plan (relojes,
tiempos, tope de 55 s + 5 de placa = 60) y se arma con ffmpeg, tramo por tramo. Cada tramo
lleva su tarjeta (volanta + texto, con el diseño de los reels de WhatsApp: `video._tarjeta_corr`),
la marca arriba y una etiqueta con lo que muestra cada cámara. El isologo y la placa de cierre los
pega después `video.to_vertical_reel(compuesto=True)`.

Si algo falla, `montar` devuelve None y el bot sigue como antes (los videos uno detrás del otro):
nunca se pierde el reel por el montaje."""
from __future__ import annotations

import subprocess
from pathlib import Path

import video as V
from utils.logger import get_logger

logger = get_logger("montaje")

MAX_SEG = 55.0                 # + 5 s de placa de cierre = 60, el tope del prompt
MIN_TRAMO = 3.0                # una tarjeta se lee al menos 3 s
VELOCIDADES = (1.0, 1.5, 2.0)
FPS = 30
# Paneles (x, y, ancho, alto) en 1080x1920. El primero es el PRINCIPAL.
PANELES = {3: ((0, 0, 1080, 1344), (0, 1344, 540, 576), (540, 1344, 540, 576)),
           2: ((0, 0, 1080, 960), (0, 960, 1080, 960))}
SEPARADOR = 2                  # px, naranja de acento (el prompt: «de 0 a 2 px»)
ETIQUETA_TAM = 22


# ── Plan ────────────────────────────────────────────────────────────────────────────
def _segundos(reloj: str) -> float | None:
    """«05:10:33» → segundos del día. None si no es una hora."""
    try:
        h, m, s = (float(x) for x in str(reloj).strip().split(":"))
        if 0 <= h < 24 and 0 <= m < 60 and 0 <= s < 61:
            return h * 3600 + m * 60 + s
    except (ValueError, TypeError):
        pass
    return None


def _cuadro(src: Path, t: float, salida: Path) -> Path:
    """Un cuadro del video en el segundo `t`, a 960 de ancho (para leer el reloj)."""
    subprocess.run([V._ffmpeg(), "-y", "-ss", f"{max(0.0, t):.2f}", "-i", str(src),
                    "-frames:v", "1", "-vf", "scale=960:-2", "-q:v", "3", str(salida)],
                   capture_output=True, check=True, timeout=120)
    return salida


def _foco(v) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.5


def _texto_minimo(tramos: list) -> list:
    """Una tarjeta tiene que poder leerse (3 s como mínimo, dice el prompt). Si una tarjeta
    —sumando los tramos seguidos que la repiten— queda menos que eso, en esos tramos NO va
    tarjeta: mejor ninguna que una que no corresponde a lo que se ve (heredar la anterior dejaba
    «una vecina dialogó…» encima de la multitud). La primera se deja siempre."""
    i = 0
    while i < len(tramos):
        j = i
        while j + 1 < len(tramos) and (tramos[j + 1]["texto"], tramos[j + 1]["volanta"]) == (
                tramos[i]["texto"], tramos[i]["volanta"]):
            j += 1
        if i and sum(t["dur_salida"] for t in tramos[i:j + 1]) < MIN_TRAMO:
            for t in tramos[i:j + 1]:
                t["texto"], t["volanta"] = "", ""
        i = j + 1
    return tramos


def _recortar_total(tramos: list) -> list:
    """Que el montaje no pase de `MAX_SEG`: se acortan los tramos más largos."""
    total = sum(t["dur_salida"] for t in tramos)
    while total > MAX_SEG + 0.05:
        largo = max(tramos, key=lambda t: t["dur_salida"])
        sobra = min(total - MAX_SEG, largo["dur_salida"] - MIN_TRAMO)
        if sobra <= 0:
            break
        largo["hasta"] -= sobra * largo["velocidad"]
        largo["dur_salida"] -= sobra
        total -= sobra
    return tramos


def validar(raw: dict, durs: list) -> dict | None:
    """Pasa el plan de Gemini en limpio. Devuelve `{modo, camaras, tramos}` o None.

    Simultáneo: cada cámara necesita su hora de arranque; el tramo de un acto se lleva al tiempo
    de cada cámara con esas horas y se recorta a lo que TODAS grabaron (si una no grabó ese
    momento, no se la congela ni se la repite). Si no queda nada usable, se prueba el
    secuencial."""
    n = len(durs)
    camaras = {}
    for c in raw.get("camaras") or []:
        try:
            k = int(c.get("video")) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= k < n:
            camaras[k] = dict(rol=(str(c.get("rol") or f"CÁMARA {k + 1}").strip().upper()[:24]),
                              reloj=_segundos(c.get("reloj_inicio", "")),
                              foco=_foco(c.get("foco_x", 0.5)))
    for k in range(n):
        camaras.setdefault(k, dict(rol=f"CÁMARA {k + 1}", reloj=None, foco=0.5))

    if str(raw.get("modo", "")).lower().startswith("simult"):
        con_reloj = [k for k in range(n) if camaras[k]["reloj"] is not None]
        tramos = []
        for a in raw.get("actos") or []:
            try:
                p = int(a.get("principal")) - 1
                d, h = float(a.get("desde")), float(a.get("hasta"))
            except (TypeError, ValueError):
                continue
            if p not in con_reloj or h <= d:
                continue
            vel = min(VELOCIDADES, key=lambda v: abs(v - float(a.get("velocidad") or 1)))
            # Al reloj (segundos del día), y de ahí a lo que grabaron TODAS las cámaras.
            ini = camaras[p]["reloj"] + max(0.0, d)
            fin = camaras[p]["reloj"] + min(durs[p], h)
            for k in con_reloj[:3]:
                ini = max(ini, camaras[k]["reloj"])
                fin = min(fin, camaras[k]["reloj"] + durs[k])
            if (fin - ini) / vel < MIN_TRAMO:
                continue
            tramos.append(dict(principal=p, reloj_ini=ini, desde=ini, hasta=fin, velocidad=vel,
                               dur_salida=(fin - ini) / vel,
                               volanta=str(a.get("volanta", "")).strip(),
                               texto=str(a.get("texto", "")).strip(),
                               foco=_foco(a.get("foco_x", camaras[p]["foco"])),
                               foco_fin=_foco(a.get("foco_x_fin",
                                                    a.get("foco_x", camaras[p]["foco"])))))
        if len(con_reloj) >= 2 and tramos:
            usadas = sorted(set(con_reloj[:3]), key=lambda k: -sum(
                t["dur_salida"] for t in tramos if t["principal"] == k))
            return dict(modo="simultaneo", camaras=camaras, usadas=usadas[:3],
                        tramos=_texto_minimo(_recortar_total(tramos)))
        logger.info("El plan simultáneo no se sostiene con los relojes: pruebo el secuencial.")

    tramos = []
    for p in raw.get("planos") or []:
        try:
            k = int(p.get("video")) - 1
            d, h = float(p.get("desde")), float(p.get("hasta"))
        except (TypeError, ValueError):
            continue
        if not 0 <= k < n:
            continue
        d, h = max(0.0, d), min(durs[k], h)
        if h - d < 1.0:
            continue
        tramos.append(dict(video=k, desde=d, hasta=h, velocidad=1.0, dur_salida=h - d,
                           volanta=str(p.get("volanta", "")).strip(),
                           texto=str(p.get("texto", "")).strip(),
                           foco=_foco(p.get("foco_x", camaras[k]["foco"])),
                           foco_fin=_foco(p.get("foco_x_fin",
                                                p.get("foco_x", camaras[k]["foco"])))))
    if not tramos:
        return None
    return dict(modo="secuencial", camaras=camaras, usadas=[], tramos=_texto_minimo(
        _recortar_total(tramos)))


# ── Dibujo de cada tramo ───────────────────────────────────────────────────────────
def _capa(paneles: list, roles: list, tramo: dict, salida: Path) -> tuple:
    """Los PNG transparentes que van encima de un tramo: `salida` con los separadores naranjas
    entre paneles, la etiqueta de cada cámara (arriba a la izquierda de su panel; en la
    principal, con la velocidad si va acelerada) y la marca; y aparte la TARJETA del tramo
    (`..._tarjeta.png`, o None si no lleva), que entra y sale con un fundido."""
    from PIL import Image, ImageDraw
    f = V._fuentes_placa()
    lienzo = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
    plan = dict(bloques=list(V._marca_corr(f)), cajas=[], halo=[], sombra_arriba=True)
    plan["halo"] = list(plan["bloques"])
    dib = ImageDraw.Draw(lienzo)
    for i, (x, y, w, h) in enumerate(paneles[1:], start=1):   # separadores
        if y > 0:
            dib.rectangle((x, y - SEPARADOR // 2, x + w - 1, y + SEPARADOR - 1),
                          fill=V.CORR_NARANJA)
        if x > 0:
            dib.rectangle((x - SEPARADOR // 2, y, x + SEPARADOR - 1, y + h - 1),
                          fill=V.CORR_NARANJA)
    for i, ((x, y, w, h), rol) in enumerate(zip(paneles, roles)):
        if not rol:
            continue
        txt = rol + (f" · {tramo['velocidad']:g}×".replace(".", ",")
                     if i == 0 and tramo["velocidad"] != 1 else "")
        # La principal arranca arriba de todo, debajo de la marca y el isologo.
        ex, ey = (x + 24, (300 if y == 0 else y + 20))
        ancho = round(V._ancho_texto(txt, f["f_r"], ETIQUETA_TAM, "500")) + 24
        alto = ETIQUETA_TAM + 18
        plan["cajas"].append((ex, ey, ex + ancho, ey + alto,
                              V.CORR_GRAFITO[:3] + (round(255 * 0.85),)))
        asc, may = V._metricas(f["f_r"], ETIQUETA_TAM, "500")
        plan["bloques"].append((txt, ETIQUETA_TAM, round(ey + (alto - may) / 2 - asc + may),
                                f["f_r"], "500", V.BLANCO, ("centro", ex + ancho // 2)))
    V.pintar_placa(lienzo, plan)
    lienzo.save(salida)
    tarjeta = None
    if tramo.get("texto") or tramo.get("volanta"):
        tarjeta = V.tarjeta_png(tramo.get("volanta", ""), tramo.get("texto", ""),
                                V.CORR_Y_ABAJO, (1080, 1920),
                                salida.with_name(salida.stem + "_tarjeta.png"))
    return salida, tarjeta


def _x_seguimiento(ancho: int, foco: float, foco_fin: float, dur: float) -> str:
    """La x del recorte que ACOMPAÑA la acción dentro del tramo (pedido 2026-10-03, «definí
    puntos de seguimiento e interpolá suavemente»): va de `foco` a `foco_fin` con una curva suave
    (arranca y frena despacio, sin paneos nerviosos). Si no se mueve, es fija."""
    if abs(foco_fin - foco) < 0.02 or dur <= 0:
        return f"(iw-{ancho})*{foco:.3f}"
    p = f"clip(t/{dur:.3f}\\,0\\,1)"
    return (f"(iw-{ancho})*({foco:.3f}+({foco_fin - foco:.3f})*({p}*{p}*(3-2*{p})))")


def _entrada(src: Path, recorte, ancho: int, alto: int, foco: float, vel: float,
             foco_fin: float | None = None, dur: float = 0.0) -> str:
    """Cadena de filtros de una cámara: sin giro, sin barras negras, a su velocidad, llenando su
    panel (escala = la mayor de ancho y alto) y corrida hacia la acción (`foco`; si viene
    `foco_fin`, el recorte la sigue a lo largo de los `dur` segundos del tramo)."""
    cad = V._sin_giro()
    if recorte:
        cad += f",crop={recorte[0]}:{recorte[1]}:{recorte[2]}:{recorte[3]}"
    x = _x_seguimiento(ancho, foco, foco if foco_fin is None else foco_fin, dur)
    return (f"{cad},setpts=(PTS-STARTPTS)/{vel:g},fps={FPS},"
            f"scale={ancho}:{alto}:force_original_aspect_ratio=increase,"
            f"crop={ancho}:{alto}:{x}:(ih-{alto})*0.5,setsar=1")


def _armar_tramo(entradas: list, paneles: list, capa: Path, dur: float, salida: Path,
                 audio_de: int | None, tarjeta: Path | None = None,
                 fundir: tuple = (False, False)) -> Path:
    """Un tramo: cada entrada (ruta, desde, recorte, foco, velocidad, foco_fin) en su panel, la
    capa encima, `dur` segundos. La `tarjeta` va aparte, con un fundido de entrada y/o salida
    (`fundir`) cuando cambia respecto del tramo vecino. Audio: el de `audio_de` (solo a
    velocidad normal) o silencio."""
    cmd = [V._ffmpeg(), "-y"]
    for src, desde, _rec, _foco, vel, _fin in entradas:
        cmd += ["-ss", f"{desde:.3f}", "-t", f"{dur * vel + 0.5:.3f}", "-i", str(src)]
    i_capa = len(entradas)
    cmd += ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(capa)]
    i_sig = i_capa + 1
    if tarjeta:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", str(tarjeta)]
        i_sig += 1
    color = "0x%02X%02X%02X" % V.CORR_GRAFITO[:3]
    fc = [f"color=c={color}:s=1080x1920:r={FPS}:d={dur:.3f}[b0]"]
    for i, ((src, _d, rec, foco, vel, fin), (x, y, w, h)) in enumerate(zip(entradas, paneles)):
        fc.append(f"[{i}:v]{_entrada(Path(src), rec, w, h, foco, vel, fin, dur)}[p{i}]")
        fc.append(f"[b{i}][p{i}]overlay={x}:{y}:eof_action=pass[b{i + 1}]")
    n = len(entradas)
    if tarjeta:
        f = V.FUNDIDO_TARJETA
        fundidos = ((f",fade=t=in:st=0:d={f}:alpha=1" if fundir[0] else "")
                    + (f",fade=t=out:st={max(0.0, dur - f):.3f}:d={f}:alpha=1"
                       if fundir[1] else ""))
        fc.append(f"[{i_capa}:v]format=rgba[cp];[b{n}][cp]overlay=0:0[bc]")
        fc.append(f"[{i_capa + 1}:v]format=rgba{fundidos}[tj];[bc][tj]overlay=0:0,"
                  f"format=yuv420p[v]")
    else:
        fc.append(f"[{i_capa}:v]format=rgba[cp];[b{n}][cp]overlay=0:0,format=yuv420p[v]")
    maps = ["-map", "[v]"]
    if audio_de is not None:
        fc.append(f"[{audio_de}:a]aresample=44100,aformat=sample_fmts=fltp:"
                  f"channel_layouts=stereo,asetpts=PTS-STARTPTS[a]")
        maps += ["-map", "[a]", "-c:a", "aac", "-b:a", "128k"]
    else:
        cmd += ["-f", "lavfi", "-t", f"{dur:.3f}", "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100"]
        maps += ["-map", f"{i_sig}:a", "-c:a", "aac", "-b:a", "128k"]
    cmd += ["-filter_complex", ";".join(fc), *maps, "-t", f"{dur:.3f}", "-r", str(FPS),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            str(salida)]
    V._run_ffmpeg(cmd, "tramo del montaje")
    return salida


def _unir(tramos: list, salida: Path) -> Path:
    """Une los tramos con CORTES directos (todos salen con los mismos parámetros)."""
    lista = salida.with_suffix(".txt")
    lista.write_text("".join(f"file '{Path(t).as_posix()}'\n" for t in tramos), encoding="utf-8")
    V._run_ffmpeg([V._ffmpeg(), "-y", "-f", "concat", "-safe", "0", "-i", str(lista),
                   "-c", "copy", "-movflags", "+faststart", str(salida)], "unir el montaje")
    return salida


def armar(plan: dict, videos: list, salida: Path, work_dir: Path) -> Path:
    """Arma un plan YA validado (`validar`): cada tramo por separado y después los une."""
    recortes = [V.detectar_recorte(v) for v in videos]
    con_audio = [V.has_audio(v) for v in videos]
    partes = []
    for j, t in enumerate(plan["tramos"]):
        capa_png = work_dir / f"_capa_{j}.png"
        if plan["modo"] == "simultaneo":
            # La principal primero; después las otras cámaras usadas, en su orden.
            orden = ([t["principal"]] + [k for k in plan["usadas"] if k != t["principal"]])[:3]
            paneles = PANELES[len(orden)]
            # La principal sigue la acción (foco → foco_fin); las chicas, encuadre fijo.
            entradas = [(videos[k], t["reloj_ini"] - plan["camaras"][k]["reloj"], recortes[k],
                         t["foco"] if k == t["principal"] else plan["camaras"][k]["foco"],
                         t["velocidad"],
                         t.get("foco_fin") if k == t["principal"] else None) for k in orden]
            roles = [plan["camaras"][k]["rol"] for k in orden]
            audio = 0 if (t["velocidad"] == 1 and con_audio[orden[0]]) else None
        else:
            k = t["video"]
            paneles = ((0, 0, 1080, 1920),)
            entradas = [(videos[k], t["desde"], recortes[k], t["foco"], 1.0,
                         t.get("foco_fin"))]
            roles = [""]
            audio = 0 if con_audio[k] else None
        _base, tarjeta = _capa(paneles, roles, t, capa_png)
        # Fundido de la tarjeta solo donde CAMBIA respecto del tramo vecino (si sigue la misma,
        # no parpadea), y nunca al arrancar el reel: el primer cuadro es la portada.
        tj = (t["volanta"], t["texto"])
        antes = (plan["tramos"][j - 1]["volanta"], plan["tramos"][j - 1]["texto"]) if j else tj
        despues = ((plan["tramos"][j + 1]["volanta"], plan["tramos"][j + 1]["texto"])
                   if j + 1 < len(plan["tramos"]) else tj)
        partes.append(_armar_tramo(entradas, paneles, capa_png, t["dur_salida"],
                                   work_dir / f"_tramo_{j}.mp4", audio, tarjeta,
                                   (j > 0 and antes != tj, despues != tj)))
    return _unir(partes, Path(salida))


def autoprueba(tmp: Path) -> bool:
    """Prueba de humo SIN Gemini (para `--chequeo-reel` en la nube): tres «cámaras» de prueba
    con un plan simultáneo y otro secuencial hechos a mano. Verifica que ffmpeg arme los paneles,
    las capas y la unión por cortes con el ffmpeg de ahí."""
    tmp = Path(tmp)
    vids = []
    for i, (tam, dur) in enumerate((("1280x720", 12), ("1280x720", 10), ("720x1280", 9))):
        v = tmp / f"cam{i}.mp4"
        subprocess.run([V._ffmpeg(), "-y", "-f", "lavfi", "-i",
                        f"testsrc=size={tam}:rate=25:duration={dur}", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", str(v)], capture_output=True, check=True)
        vids.append(v)
    durs = [12.0, 10.0, 9.0]
    raw = {"modo": "simultaneo",
           "camaras": [{"video": 1, "rol": "INGRESO", "reloj_inicio": "05:10:00", "foco_x": 0.5},
                       {"video": 2, "rol": "INTERIOR", "reloj_inicio": "05:10:02", "foco_x": 0.4},
                       {"video": 3, "rol": "CALLE", "reloj_inicio": "05:10:03", "foco_x": 0.6}],
           "actos": [{"principal": 1, "desde": 3, "hasta": 7, "velocidad": 1,
                      "volanta": "CHIVILCOY · PRUEBA",
                      "texto": "Forzaron la entrada del comercio", "foco_x": 0.2,
                      "foco_x_fin": 0.8},
                     {"principal": 2, "desde": 5, "hasta": 10, "velocidad": 1.5,
                      "volanta": "CÁMARAS DE SEGURIDAD", "texto": "Revisaron el interior",
                      "foco_x": 0.4}],
           "planos": []}
    secuencial = dict(raw, modo="secuencial", planos=[
        {"video": 1, "desde": 1, "hasta": 4, "volanta": "A", "texto": "Uno", "foco_x": 0.5},
        {"video": 3, "desde": 2, "hasta": 6, "volanta": "B", "texto": "Dos", "foco_x": 0.5}])
    ok = True
    for nombre, modo, plan in (("simultáneo", "simultaneo", validar(raw, durs)),
                               ("secuencial", "secuencial", validar(secuencial, durs))):
        try:
            if not plan or plan["modo"] != modo:
                raise RuntimeError(f"el plan de prueba no validó ({plan and plan['modo']})")
            sal = armar(plan, vids, tmp / f"montaje_{modo}.mp4", tmp)
            w, h = V._dimensiones(sal)
            dur = V.duration_seconds(sal) or 0
            esperado = sum(t["dur_salida"] for t in plan["tramos"])
            bien = (w, h) == (1080, 1920) and abs(dur - esperado) < 0.6
            print(f"  {'OK  ' if bien else 'MAL '} montaje {nombre}: {w}x{h}, {dur:.1f}s "
                  f"(esperado {esperado:.1f}), {len(plan['tramos'])} tramo(s)")
            ok = ok and bien
        except Exception as e:                                   # noqa: BLE001
            print(f"  ROTO montaje {nombre}: {type(e).__name__}: {e}")
            ok = False
    return ok


# ── Entrada ─────────────────────────────────────────────────────────────────────────
def montar(videos: list, salida: Path, *, fuentes: str, work_dir: Path) -> Path | None:
    """Arma el montaje de `videos` (varios, del mismo envío) en `salida` (1080x1920, hasta 55 s,
    con marca, etiquetas y tarjetas; sin isologo ni placa de cierre). None si no se pudo: el
    que llama usa el armado de siempre."""
    videos = [Path(v) for v in videos]
    if len(videos) < 2:
        return None
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        from utils import gemini
        durs = [float(V.duration_seconds(v) or 0) for v in videos]
        if min(durs) <= 0:
            raise RuntimeError("no pude medir algún video")
        cuadros = [(_cuadro(v, 0.3, work_dir / f"_ini_{i}.jpg"),
                    _cuadro(v, max(0.0, d - 0.5), work_dir / f"_fin_{i}.jpg"))
                   for i, (v, d) in enumerate(zip(videos, durs))]
        raw = gemini.plan_montaje(list(zip(videos, durs)), cuadros, fuentes, MAX_SEG)
        plan = validar(raw, durs)
        if not plan:
            raise RuntimeError("el plan de montaje no trajo tramos usables")
        armar(plan, videos, Path(salida), work_dir)
        total = sum(t["dur_salida"] for t in plan["tramos"])
        logger.info(f"Montaje {plan['modo']}: {len(plan['tramos'])} tramo(s), {total:.1f} s, "
                    f"{len(videos)} video(s) → {Path(salida).name}")
        return Path(salida)
    except Exception as e:  # noqa: BLE001
        logger.error(f"No pude montar los {len(videos)} videos ({e}); van uno detrás del otro, "
                     f"como antes.")
        return None
