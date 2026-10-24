"""Decide QUÉ sale, en QUÉ red y CUÁNDO, respetando todos los topes.

Por cada red, en este orden:
  1. Ordena la cola: urgente primero, después peso del origen + prioridad − antigüedad.
  2. Busca horario: después de la última programada + la separación mínima, dentro de la
     franja horaria y del horizonte (lo que no entra en el horizonte espera en la cola).
     Lo URGENTE no hace fila: toma el primer hueco libre desde ya.
  3. Controla: cupo del origen en el día (matriz), tope total de 24 h de la red, lugar en
     Buffer (10 programadas por canal en el plan Free) y la separación entre el reel y la
     nota de la misma noticia en X.
  3b. Intercalado (17/10/2026): un policial nunca queda pegado a otro policial en el canal
     (lo nuestro programado o enviado y lo ajeno que Buffer muestra). Si no hay otra temática
     en medio, espera en la cola: nunca se rellena.
  4. Si un urgente no encuentra lugar en Buffer, desprograma la programada NO urgente más
     lejana (vuelve a la cola, no se pierde).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import config, estado, tema, x_sincro

AR = ZoneInfo(config.ZONA)
CUENTAN = ("enviando", "programado", "enviado")


def _slug(url: str) -> str:
    return (url or "").rstrip("/").rsplit("/", 1)[-1].split("?")[0].lower()


def puntaje(pub: dict, ahora: datetime) -> float:
    edad_h = max(0.0, (ahora - (estado.de_iso(pub.get("generado")) or ahora)).total_seconds() / 3600)
    return ((config.BONO_URGENTE if pub.get("urgente") else 0)
            + config.PESO_ORIGEN.get(pub["origen"], 0)
            + 5 * int(pub.get("prioridad") or 5)
            - 2 * edad_h)


def en_franja(t: datetime, plataforma: str) -> datetime:
    """Si `t` cae fuera de la franja horaria (hora AR), lo corre al próximo inicio."""
    ini, fin = config.franja(plataforma)
    local = t.astimezone(AR)
    minuto = local.hour * 60 + local.minute
    # La franja puede cruzar la medianoche (fin > 1440): 00:00-00:30 todavía vale.
    if fin > 1440 and minuto < fin - 1440:
        return t
    if ini <= minuto < min(fin, 1440):
        return t
    base = local.replace(hour=ini // 60, minute=ini % 60, second=0, microsecond=0)
    if minuto >= ini:
        base += timedelta(days=1)
    return base.astimezone(t.tzinfo)


def _dia_ar(t: datetime) -> str:
    return t.astimezone(AR).strftime("%Y-%m-%d")


def _segundo_fijo(t: datetime, clave: str) -> datetime:
    """dueAt con un segundo propio de cada publicación: canal + dueAt la identifica en Buffer
    (así una corrida cortada no reenvía lo que ya quedó programado)."""
    s = int(hashlib.sha1(clave.encode()).hexdigest(), 16) % 60
    return t.replace(second=s, microsecond=0)


def pegado(linea: list, t: datetime, n: int) -> bool:
    """¿Un policial en `t` quedaría con un policial entre las `n` publicaciones anteriores o las
    `n` siguientes del canal? `linea`: [(horario, es_policial)]."""
    antes = sorted((x for x in linea if x[0] <= t), reverse=True)[:n]
    despues = sorted(x for x in linea if x[0] > t)[:n]
    return any(p for _, p in antes) or any(p for _, p in despues)


def _sacarla_los_junta(linea: list, pub: dict, n: int) -> bool:
    """¿Desprogramar `pub` deja dos policiales pegados que antes no lo estaban? (Lo enviado antes
    del 17/10 puede tener policiales seguidos: eso no cuenta.)"""
    def pegados(lin):
        return sum(1 for t, pol in lin if pol and pegado([y for y in lin if y[0] != t], t, n))
    sale = estado.de_iso(pub.get("dueAt"))
    return pegados([x for x in linea if x[0] != sale]) > pegados(linea)


def intercalar_cola(cola: list, linea: list, n: int) -> list:
    """La cola ya ordenada, con los policiales intercalados: lo urgente primero (en su orden) y
    después `n` de otra temática, 1 policial, `n` de otra temática… Sin esto, las notas web
    (más peso) se programaban todas seguidas y el policial quedaba al final, solo."""
    urgentes = [p for p in cola if p.get("urgente")]
    resto = [p for p in cola if not p.get("urgente")]
    pol = [p for p in resto if tema.es_policial(p)]
    otros = [p for p in resto if not tema.es_policial(p)]
    # ¿Cuántos de otra temática hay desde el último policial del canal?
    desde = 0
    for _t, es_pol in sorted(linea, reverse=True):
        if es_pol:
            break
        desde += 1
    else:
        desde = n
    out = list(urgentes)
    while pol or otros:
        if pol and (desde >= n or not otros):
            out.append(pol.pop(0))
            desde = 0
        else:
            out.append(otros.pop(0))
            desde += 1
    return out


def linea_del_canal(est: dict, plataforma: str) -> list:
    """[(horario, es_policial)] de lo programado o enviado en el canal: lo nuestro y lo ajeno."""
    linea = [(estado.de_iso(p["dueAt"]), tema.es_policial(p)) for p in est["publicaciones"].values()
             if p["plataforma"] == plataforma and p["estado"] in CUENTAN and p.get("dueAt")]
    for a in (est.get("ajenos") or {}).get(plataforma) or []:
        t = estado.de_iso(a.get("dueAt"))
        if t:
            linea.append((t, bool(a.get("policial"))))
    return linea


def tanda_actual(plataforma: str, ahora: datetime) -> str:
    """El id de la tanda de TikTok en curso («2026-10-19 18»), o "" si ahora no hay tanda."""
    if plataforma != "tiktok":
        return ""
    local = ahora.astimezone(AR)
    for h in config.tiktok_tandas():
        inicio = local.replace(hour=h, minute=0, second=0, microsecond=0)
        if inicio <= local < inicio + timedelta(minutes=config.tanda_ventana_min()):
            return f"{local:%Y-%m-%d} {h:02d}"
    return ""


def puntaje_viral(pub: dict, ahora: datetime) -> float:
    """Lo más viral primero (la «prioridad» del buzón ES el potencial viral de la pieza, 1-10); a igual
    viralidad, lo más nuevo. Lo urgente, antes que todo."""
    edad_h = max(0.0, (ahora - (estado.de_iso(pub.get("generado")) or ahora)).total_seconds() / 3600)
    bot = 1000 if config.BOT_PRIMERO and pub.get("origen") == "bot" else 0     # el bot, primero (20/10/2026)
    return (bot + (config.BONO_URGENTE if pub.get("urgente") else 0) + 10 * int(pub.get("prioridad") or 5)
            - edad_h)


def elegir_tanda(cola: list, n: int, max_origen: int, ahora: datetime) -> list:
    """Las `n` más virales de la cola, sin más de `max_origen` del mismo origen mientras haya de otros
    (equilibrio entre policiales, nacionales, el bot…); si no alcanza, completa con las que quedan."""
    orden = sorted(cola, key=lambda p: puntaje_viral(p, ahora), reverse=True)
    elegidas, por_origen = [], {}
    for p in orden:
        if len(elegidas) >= n:
            break
        if por_origen.get(p["origen"], 0) < max_origen or (config.BOT_PRIMERO and p["origen"] == "bot"):
            elegidas.append(p)
            por_origen[p["origen"]] = por_origen.get(p["origen"], 0) + 1
    for p in orden:
        if len(elegidas) >= n:
            break
        if p not in elegidas:
            elegidas.append(p)
    return elegidas


def elegir_reparto(cola: list, n: int, limite: dict, ahora: datetime) -> list:
    """Las `n` más virales respetando el reparto de TikTok: primero hasta `limite[cat]["tanda"]` de cada
    categoría (su parte de esta tanda), después se completa con lo que quede sin pasar `limite[cat]["dia"]`
    (los lugares del día de cada una son suyos: nunca se los come otra)."""
    orden = sorted(cola, key=lambda p: puntaje_viral(p, ahora), reverse=True)
    elegidas, usadas = [], {}
    # La categoría «resto» (el bot desde el 24/10/2026) entra última: solo en «resto» tiene lugar.
    for clave in ("tanda", "dia", "resto"):
        for p in orden:
            if len(elegidas) >= n:
                break
            cat = x_sincro.categoria_tiktok(p)
            if p not in elegidas and usadas.get(cat, 0) < limite.get(cat, {}).get(clave, 0):
                elegidas.append(p)
                usadas[cat] = usadas.get(cat, 0) + 1
    return elegidas


def planificar_tanda(est: dict, plataforma: str, ahora: datetime) -> tuple[list, list, dict]:
    """TikTok en TANDAS (19/10/2026): solo a las 10, 18 y 21 se programa en Buffer, y en cada tanda entra
    lo más viral del momento (tope de 24 h / cantidad de tandas), una cada TIKTOK_TANDA_SEPARACION_MIN,
    sin dos policiales seguidos. Lo que no entra sigue en la cola para la tanda siguiente (o vence)."""
    pubs = est["publicaciones"]
    mias = [p for p in pubs.values() if p["plataforma"] == plataforma]
    pendientes = [p for p in mias if p["estado"] == "pendiente"]
    tanda = tanda_actual(plataforma, ahora)
    hechas = est.setdefault("tandas", {}).setdefault(plataforma, [])
    if not tanda or tanda in hechas:
        proxima = ", ".join(f"{h:02d}:00" for h in config.tiktok_tandas())
        return [], [], {p["clave"]: f"TikTok sale en tandas ({proxima}): espera la próxima" for p in pendientes}
    hechas.append(tanda)
    del hechas[:-30]                      # alcanza con recordar las últimas

    tope24 = config.tope_24h(plataforma)
    medido = (est.get("pausas") or {}).get(plataforma) or {}
    if medido.get("tope_medido") and (estado.de_iso(medido.get("medido")) or ahora) > ahora - timedelta(days=7):
        tope24 = min(tope24, int(medido["tope_medido"]))
    ocupadas = sorted(estado.de_iso(p["dueAt"]) for p in mias if p["estado"] in CUENTAN and p.get("dueAt"))
    en_24h = sum(1 for d in ocupadas if ahora - timedelta(hours=24) < d <= ahora + timedelta(hours=3))
    en_buffer = [p for p in mias if p["estado"] in ("programado", "enviando")
                 and (estado.de_iso(p.get("dueAt")) or ahora) > ahora - timedelta(minutes=2)]
    # Lo de cada tanda, redondeado para arriba (20 / 3 = 7): el tope de 24 h igual frena el total.
    n = min(max(1, -(-tope24 // max(1, len(config.tiktok_tandas())))),
            config.max_pendientes(plataforma) - len(en_buffer), max(0, tope24 - en_24h))
    espera = {}
    if n <= 0:
        return [], [], {p["clave"]: "tanda sin lugar (tope de 24 h o Buffer lleno)" for p in pendientes}

    reparto = config.tiktok_reparto()
    if reparto:
        # 5 del bot, 5 policiales y 5 virales por día (20/10/2026): lo que ya salió hoy de cada uno cuenta, y
        # en cada tanda le toca su parte de lo que le queda (en la última, todo).
        hoy = _dia_ar(ahora)
        hechas_hoy = {p["clave"]: x_sincro.categoria_tiktok(p) for p in mias if p["estado"] in CUENTAN
                      and p.get("dueAt") and _dia_ar(estado.de_iso(p["dueAt"])) == hoy}
        local = ahora.astimezone(AR)
        tandas_quedan = max(1, sum(1 for h in config.tiktok_tandas() if h >= local.hour))
        limite = {}
        for cat, total in reparto.items():
            if total == config.RESTO:
                continue
            queda = max(0, total - sum(1 for c in hechas_hoy.values() if c == cat))
            limite[cat] = {"dia": queda, "tanda": -(-queda // tandas_quedan), "resto": queda}
        # «Lo que sobra, para el bot» (24/10/2026): lo que queda del tope del día, menos los lugares que las
        # otras categorías todavía tienen guardados para lo que ya está en la cola.
        en_cola = {}
        for p in pendientes:
            c = x_sincro.categoria_tiktok(p)
            en_cola[c] = en_cola.get(c, 0) + 1
        guardados = sum(min(lim["dia"], en_cola.get(c, 0)) for c, lim in limite.items())
        for cat, total in reparto.items():
            if total == config.RESTO:
                libre = max(0, tope24 - len(hechas_hoy) - guardados)
                limite[cat] = {"dia": 0, "tanda": 0, "resto": libre}
        elegidas = elegir_reparto(pendientes, n, limite, ahora)
        for p in pendientes:
            cat = x_sincro.categoria_tiktok(p)
            if p in elegidas or cat not in limite:
                continue
            ya = sum(1 for e in elegidas if x_sincro.categoria_tiktok(e) == cat)
            if reparto[cat] != config.RESTO and limite[cat]["dia"] <= ya:
                espera[p["clave"]] = f"ya salieron los {reparto[cat]} de {cat} de hoy en TikTok"
            elif reparto[cat] == config.RESTO and limite[cat]["resto"] <= ya:
                espera[p["clave"]] = f"{cat}: el resto del tope de hoy ya está usado o guardado para los otros"
    else:
        elegidas = elegir_tanda(pendientes, n, config.tanda_max_origen(), ahora)
    linea = linea_del_canal(est, plataforma)
    intercalar, n_inter = config.intercalado(), config.min_entre_policiales()
    if intercalar:
        elegidas = intercalar_cola(sorted(elegidas, key=lambda p: puntaje_viral(p, ahora), reverse=True),
                                   linea, n_inter)
    sep = timedelta(minutes=config.tanda_separacion_min())
    t = en_franja(max([ahora + timedelta(minutes=3)] + [d + sep for d in ocupadas if d >= ahora - sep]),
                  plataforma)
    asignadas = []
    for pub in elegidas:
        if intercalar and tema.es_policial(pub) and pegado(linea, t, n_inter):
            espera[pub["clave"]] = "intercalado: quedaría pegado a otro policial; va en otra tanda"
            continue
        due = _segundo_fijo(t, pub["clave"])
        asignadas.append((pub["clave"], estado.iso(due)))
        linea.append((due, tema.es_policial(pub)))
        t = en_franja(t + sep, plataforma)
    for p in pendientes:
        if p["clave"] not in dict(asignadas) and p["clave"] not in espera:
            espera[p["clave"]] = f"no entró entre las {n} más virales de la tanda {tanda[-2:]} h"
    return asignadas, [], espera


def planificar(est: dict, plataforma: str, ahora: datetime) -> tuple[list, list, dict]:
    """Devuelve (asignaciones, a_desprogramar, motivos_de_espera)."""
    if plataforma == "tiktok" and config.tiktok_tandas():
        return planificar_tanda(est, plataforma, ahora)
    if plataforma == "x":
        from . import x_sincro
        if x_sincro.activo():
            return x_sincro.planificar(est, ahora)
    pubs = est["publicaciones"]
    mias = [p for p in pubs.values() if p["plataforma"] == plataforma]
    sep = timedelta(minutes=config.separacion_min(plataforma))
    horizonte = ahora + timedelta(minutes=config.HORIZONTE_MIN)
    inicio = ahora + timedelta(minutes=3)

    ocupadas = sorted(estado.de_iso(p["dueAt"]) for p in mias
                      if p["estado"] in CUENTAN and p.get("dueAt"))
    en_buffer = [p for p in mias if p["estado"] in ("programado", "enviando")
                 and (estado.de_iso(p.get("dueAt")) or ahora) > ahora - timedelta(minutes=2)]
    libres = config.max_pendientes(plataforma) - len(en_buffer)

    por_dia = {}
    for p in mias:
        if p["estado"] in CUENTAN and p.get("dueAt"):
            k = (_dia_ar(estado.de_iso(p["dueAt"])), p["origen"])
            por_dia[k] = por_dia.get(k, 0) + 1

    notas = {}   # slug de nota → horarios ya tomados en esta red
    for p in mias:
        if p["estado"] in CUENTAN and p.get("dueAt") and p.get("nota_url"):
            notas.setdefault(_slug(p["nota_url"]), []).append(estado.de_iso(p["dueAt"]))

    cola = sorted((p for p in mias if p["estado"] == "pendiente"),
                  key=lambda p: puntaje(p, ahora), reverse=True)
    asignadas, desprogramar, espera = [], [], {}
    tope24 = config.tope_24h(plataforma)
    # El tope que la red de verdad aguantó (run.registrar_pausa), durante 7 días.
    medido = (est.get("pausas") or {}).get(plataforma) or {}
    if medido.get("tope_medido") and (estado.de_iso(medido.get("medido")) or ahora) > ahora - timedelta(days=7):
        tope24 = min(tope24, int(medido["tope_medido"]))
    linea = linea_del_canal(est, plataforma)
    intercalar, n_inter = config.intercalado(), config.min_entre_policiales()
    if intercalar:
        cola = intercalar_cola(cola, linea, n_inter)

    def en_24h(t):
        return sum(1 for d in ocupadas if t - timedelta(hours=24) < d <= t)

    def hueco_urgente(policial=False):
        t = en_franja(inicio, plataforma)
        minimo = timedelta(minutes=5)
        for _ in range(24 * 60):
            if all(abs(t - d) >= minimo for d in ocupadas) and not (
                    policial and intercalar and pegado(linea, t, n_inter)):
                return t
            t = en_franja(t + timedelta(minutes=1), plataforma)
        return None

    for pub in cola:
        policial = tema.es_policial(pub)
        if pub.get("urgente"):
            t = hueco_urgente(policial)
        else:
            ultima = max([d for d in ocupadas if d >= ahora - sep] or [inicio - sep])
            t = en_franja(max(inicio, ultima + sep), plataforma)
        if t is None or t > horizonte:
            espera[pub["clave"]] = "fuera del horizonte de programación (espera en la cola)"
            if not pub.get("urgente"):
                break        # la cola está ordenada: lo que sigue tampoco entra
            continue
        # La misma noticia (nota y reel) en esta red: separadas.
        slug = _slug(pub.get("nota_url") or "")
        if slug and slug in notas:
            sep_n = timedelta(minutes=config.SEPARACION_MISMA_NOTA_MIN)
            for d in notas[slug]:
                if abs(t - d) < sep_n:
                    t = en_franja(d + sep_n, plataforma)
            if t > horizonte:
                espera[pub["clave"]] = "espera para separarse de la otra publicación de la misma noticia"
                continue
        # Intercalado: ni lo urgente se pega a otro policial (la cola sigue: lo de otra temática
        # que venga detrás se programa y en la corrida siguiente este ya tiene su hueco).
        if policial and intercalar and pegado(linea, t, n_inter):
            espera[pub["clave"]] = (f"intercalado: en {config.NOMBRE[plataforma]} quedaría pegado a otro "
                                    f"policial; espera una publicación de otra temática")
            continue
        dia = _dia_ar(t)
        if por_dia.get((dia, pub["origen"]), 0) >= config.cupo(pub["origen"], plataforma):
            espera[pub["clave"]] = (f"cupo diario de {config.NOMBRE_ORIGEN[pub['origen']]} en "
                                    f"{config.NOMBRE[plataforma]} completo "
                                    f"({config.cupo(pub['origen'], plataforma)})")
            continue
        if en_24h(t) >= tope24:
            espera[pub["clave"]] = f"tope de {tope24} publicaciones en 24 h de {config.NOMBRE[plataforma]}"
            continue
        if libres <= 0:
            if not pub.get("urgente"):
                espera[pub["clave"]] = "Buffer lleno (10 programadas por canal en el plan Free)"
                break
            # Urgente: se desprograma la NO urgente más lejana (vuelve a la cola).
            candidatas = sorted((p for p in en_buffer if p["estado"] == "programado"
                                 and not p.get("urgente") and p.get("buffer_id")
                                 and p["clave"] not in desprogramar),
                                key=lambda p: p["dueAt"], reverse=True)
            # La que se saca no puede ser la que separa dos policiales (intercalado).
            if intercalar:
                candidatas = [c for c in candidatas if not _sacarla_los_junta(linea, c, n_inter)]
            if not candidatas:
                espera[pub["clave"]] = "Buffer lleno y no hay nada no urgente para correr"
                continue
            desprogramar.append(candidatas[0]["clave"])
            sale = estado.de_iso(candidatas[0]["dueAt"])
            ocupadas.remove(sale)
            linea = [x for x in linea if x[0] != sale]
            libres += 1
        t = _segundo_fijo(t, pub["clave"])
        asignadas.append((pub["clave"], estado.iso(t)))
        ocupadas.append(t)
        ocupadas.sort()
        linea.append((t, policial))
        por_dia[(dia, pub["origen"])] = por_dia.get((dia, pub["origen"]), 0) + 1
        if slug:
            notas.setdefault(slug, []).append(t)
        libres -= 1
    return asignadas, desprogramar, espera
