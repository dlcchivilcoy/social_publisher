"""Decide QUÉ sale, en QUÉ red y CUÁNDO, respetando todos los topes.

Por cada red, en este orden:
  1. Ordena la cola: urgente primero, después peso del origen + prioridad − antigüedad.
  2. Busca horario: después de la última programada + la separación mínima, dentro de la
     franja horaria y del horizonte (lo que no entra en el horizonte espera en la cola).
     Lo URGENTE no hace fila: toma el primer hueco libre desde ya.
  3. Controla: cupo del origen en el día (matriz), tope total de 24 h de la red, lugar en
     Buffer (10 programadas por canal en el plan Free) y la separación entre el reel y la
     nota de la misma noticia en X.
  4. Si un urgente no encuentra lugar en Buffer, desprograma la programada NO urgente más
     lejana (vuelve a la cola, no se pierde).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from . import config, estado

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


def planificar(est: dict, plataforma: str, ahora: datetime) -> tuple[list, list, dict]:
    """Devuelve (asignaciones, a_desprogramar, motivos_de_espera)."""
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

    def en_24h(t):
        return sum(1 for d in ocupadas if t - timedelta(hours=24) < d <= t)

    def hueco_urgente():
        t = en_franja(inicio, plataforma)
        minimo = timedelta(minutes=5)
        for _ in range(24 * 60):
            if all(abs(t - d) >= minimo for d in ocupadas):
                return t
            t = en_franja(t + timedelta(minutes=1), plataforma)
        return None

    for pub in cola:
        if pub.get("urgente"):
            t = hueco_urgente()
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
            if not candidatas:
                espera[pub["clave"]] = "Buffer lleno y no hay nada no urgente para correr"
                continue
            desprogramar.append(candidatas[0]["clave"])
            ocupadas.remove(estado.de_iso(candidatas[0]["dueAt"]))
            libres += 1
        t = _segundo_fijo(t, pub["clave"])
        asignadas.append((pub["clave"], estado.iso(t)))
        ocupadas.append(t)
        ocupadas.sort()
        por_dia[(dia, pub["origen"])] = por_dia.get((dia, pub["origen"]), 0) + 1
        if slug:
            notas.setdefault(slug, []).append(t)
        libres -= 1
    return asignadas, desprogramar, espera
