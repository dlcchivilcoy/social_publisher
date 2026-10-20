"""X SINCRONIZADO Y SIN REPETIDOS (pedido del editor, 19/10/2026).

«Necesito una estructura para X donde no se repitan posteos… que salgan al mismo tiempo las cargas de mis
notas web, los recortes de la radio, las publicaciones del bot y los reels de policiales y nacionales…
que trate de sincronizar los tiempos de posteo con las otras redes.»

Reglas:
  1. UNA NOTICIA, UN POSTEO. Cada publicación de X lleva su «noticia»: el slug de la nota de la web, o
     si no tiene nota, la huella del título. Si ya hay en X (pendiente, programada o enviada en las
     últimas 48 h) una publicación de la misma noticia —mismo slug o título casi igual—, la nueva no
     entra. Excepción: un REEL reemplaza a la NOTA de la misma noticia que todavía no llegó a Buffer
     (el video retiene más y lleva igual el link a la nota).
  2. AL MISMO TIEMPO QUE LAS OTRAS REDES. El evento llega al buzón cuando la pieza sale en Instagram,
     Facebook o YouTube; X se programa para ese momento (en la próxima corrida del distribuidor, cada
     20 min), con al menos X_SEPARACION_SINCRO_MIN entre dos posteos de X. Las notas web van al ritmo
     del carrusel del diario (X_WEB_RITMO_MIN, 15 min entre una nota y la siguiente).
  3. FUERA DE HORA, NO. Un reel que no pudo salir en X_VENTANA_REEL_H y una nota en X_VENTANA_NOTA_H
     vencen (no se publican tarde).
Siguen valiendo el tope de 24 h de X, el cupo diario por origen, la franja horaria, el lugar en Buffer
y el intercalado de policiales. X_SINCRO=0 vuelve al goteo de antes.
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

from zoneinfo import ZoneInfo

from . import config, estado

_AR = ZoneInfo(config.ZONA)

CUENTAN = ("enviando", "programado", "enviado")
ACTIVAS = ("pendiente",) + CUENTAN
VENTANA_DEDUP_H = 48
PARECIDO_TITULO = 0.6
_VACIAS = {"para", "como", "desde", "sobre", "entre", "hasta", "tras", "este", "esta", "estos", "estas",
           "pero", "cuando", "donde", "porque", "luego", "segun", "contra", "durante", "ante", "fueron",
           "tiene", "hacia", "todo", "todos", "otra", "otro", "mismo", "tambien", "despues", "aunque"}


def activo() -> bool:
    return config._bool("X_SINCRO", True)


def separacion_min() -> int:
    return max(1, config._int("X_SEPARACION_SINCRO_MIN", 5))


def ritmo_web_min() -> int:
    return max(1, config._int("X_WEB_RITMO_MIN", 15))


def horizonte_min() -> int:
    return config._int("X_HORIZONTE_SINCRO_MIN", 90)


def ventana_h(tipo: str) -> int:
    return config._int("X_VENTANA_NOTA_H", 4) if tipo == "nota" else config._int("X_VENTANA_REEL_H", 2)


# ------------------------------------------------------------------------------------ la noticia
def slug(url: str) -> str:
    return (url or "").rstrip("/").rsplit("/", 1)[-1].split("?")[0].lower()


def palabras(titulo: str) -> set:
    t = unicodedata.normalize("NFD", (titulo or "").lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return {w for w in re.findall(r"[a-z0-9]+", t) if len(w) >= 4 and w not in _VACIAS}


def clave_noticia(ev: dict) -> str:
    s = slug(ev.get("nota_url") or "")
    if s:
        return f"nota:{s}"
    w = sorted(palabras(ev.get("titulo") or ""))
    return f"titulo:{'-'.join(w[:10])}" if w else ""


def parecido(a: str, b: str) -> float:
    pa, pb = palabras(a), palabras(b)
    if not pa or not pb:
        return 0.0
    return len(pa & pb) / len(pa | pb)


def misma_noticia(est: dict, ev: dict, noticia: str, ahora: datetime) -> dict | None:
    """La publicación de X de la misma noticia que ya está (activa o enviada), o None."""
    desde = ahora - timedelta(hours=VENTANA_DEDUP_H)
    for p in est["publicaciones"].values():
        if p.get("plataforma") != "x" or p.get("estado") not in ACTIVAS or p.get("id") == ev.get("id"):
            continue
        if (estado.de_iso(p.get("generado")) or ahora) < desde:
            continue
        if noticia and p.get("noticia") == noticia:
            return p
        if parecido(p.get("titulo") or "", ev.get("titulo") or "") >= PARECIDO_TITULO:
            return p
    return None


def filtrar(est: dict, ev: dict, ahora: datetime) -> tuple[str, str]:
    """(noticia, motivo para NO publicarla en X o ""). Si un reel llega con su nota todavía en la cola,
    la nota se descarta y el reel ocupa su lugar."""
    noticia = clave_noticia(ev)
    if solo_finde(ev) and (estado.de_iso(ev.get("generado")) or ahora).astimezone(_AR).weekday() < 5:
        return noticia, "IA, espectáculos, cine y series y deportes van a X solo el sábado y el domingo"
    previa = misma_noticia(est, ev, noticia, ahora)
    if not previa:
        return noticia, ""
    if previa["estado"] == "pendiente" and previa.get("tipo") == "nota" and ev.get("tipo") == "video":
        previa.update(estado="descartado", actualizado=estado.iso(ahora),
                      error=f"reemplazada por el reel de la misma noticia ({ev.get('origen')})")
        estado.anotar(est, "reemplazada", clave=previa["clave"], motivo=previa["error"])
        return noticia, ""
    return noticia, (f"la misma noticia ya sale en X ({config.NOMBRE_ORIGEN.get(previa['origen'], previa['origen'])}, "
                     f"{previa['estado']})")


def es_viral(ev: dict) -> bool:
    """Un reel viral: la categoría virales de nacionales, o un video de potencial 9 o 10."""
    if (ev.get("categoria_nacional") or "") in config.NAC_VIRALES:
        return True
    return ev.get("tipo") == "video" and int(ev.get("prioridad") or 0) >= config.VIRAL_PRIORIDAD


def viral_tiktok(ev: dict) -> bool:
    """Un viral para el reparto de TikTok: nacionales virales o de mundo, o un video de potencial 9-10."""
    return (ev.get("categoria_nacional") or "") in config.NAC_VIRALES_TIKTOK or es_viral(ev)


def categoria_tiktok(pub: dict) -> str:
    """En qué lugar del reparto de TikTok entra (config.TIKTOK_REPARTO): «bot», «policiales», «virales» o ""."""
    if pub.get("origen") in ("bot", "policiales"):
        return pub["origen"]
    if pub.get("origen") == "nacionales" and pub.get("viral"):
        return "virales"
    return ""


def solo_finde(ev: dict) -> bool:
    return bool(ev.get("solo_finde")) or (ev.get("categoria_nacional") or "") in config.NAC_SOLO_FINDE


def reserva_virales() -> int:
    """Lugares del tope de 24 h que solo pueden usar los virales («si queda lugar, dale espacio…»)."""
    return config._int("X_RESERVA_VIRALES", 6)


# ------------------------------------------------------------------------------ a la par de Instagram
# (pedido del editor, 20/10/2026: «en X se publiquen en el mismo horario que se publica en IG los posteos,
# ya sea del diario y de los recortes de la radio»). El carrusel del diario deja su plan (qué nota sale a
# qué hora en Instagram) en state/carrusel_plan.json; cada nota del diario va a X a ESA hora. Los recortes
# salen apenas son públicos (el momento en que salen en Instagram). Los dos van antes que todo lo demás:
# no los frenan la reserva de los virales ni el cupo de su origen (solo el tope de 24 h de X).
PLAN_IG = Path(__file__).resolve().parent.parent / "state" / "carrusel_plan.json"
ESPERA_PLAN_H = 3


def a_la_par_activo() -> bool:
    return config._bool("X_A_LA_PAR_IG", True)


def plan_ig(ahora: datetime, ruta: Path = None) -> list:
    """[(titular, hora)] del plan del carrusel de hoy (horas de Argentina del mismo día)."""
    try:
        d = json.loads((ruta or PLAN_IG).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    hoy = ahora.astimezone(_AR).date()
    out = []
    for n in d.get("notas") or []:
        h = estado.de_iso(n.get("hora"))
        if h and h.astimezone(_AR).date() == hoy and n.get("titular"):
            out.append((n["titular"], h))
    return out


def _cubre(chico: str, grande: str) -> float:
    pa, pb = palabras(chico), palabras(grande)
    return len(pa & pb) / len(pa) if pa else 0.0


def hora_en_ig(pub: dict, plan: list):
    """La hora del plan de la nota (por el titular), o None."""
    mejor, hora = 0.0, None
    for titular, h in plan:
        s = max(parecido(titular, pub.get("titulo") or ""), _cubre(titular, pub.get("titulo") or ""))
        if s > mejor:
            mejor, hora = s, h
    return hora if mejor >= PARECIDO_TITULO else None


def prioritaria(pub: dict) -> bool:
    return (bool(pub.get("ig_hora")) or pub.get("origen") == "entrevistas"
            or (config.BOT_PRIMERO and pub.get("origen") == "bot"))


def fuera_de_regla(pub: dict, ahora: datetime, plan: list = None) -> str:
    """Por qué una publicación PENDIENTE ya no va (20/10/2026), o "".
      - entre semana, nacionales y policiales no van (son del fin de semana);
      - de la web, a X solo las notas del diario de la mañana: las del plan del carrusel o, si el plan
        no llegó, las cargadas a la mañana de un día hábil."""
    hora = (estado.de_iso(pub.get("dueAt")) if pub.get("estado") == "programado" else None) or ahora
    reparto = config.tiktok_reparto() if pub.get("plataforma") == "tiktok" else {}
    if reparto and not reparto.get(categoria_tiktok(pub)):
        return "a TikTok van solo el bot, los policiales y los virales (5 + 5 + 5 por día)"
    viral_tt = bool(reparto) and categoria_tiktok(pub) == "virales"      # los virales de TikTok, todos los días
    if pub.get("origen") in config.SOLO_FINDE and hora.astimezone(_AR).weekday() < 5 and not viral_tt:
        return "entre semana X y TikTok no llevan nacionales (van el finde: sábado y domingo)"
    if pub.get("origen") == "web" and pub.get("plataforma") == "x" and config.X_WEB_SOLO_DIARIO \
            and not pub.get("ig_hora"):
        gen = (estado.de_iso(pub.get("generado")) or ahora).astimezone(_AR)
        manana = gen.weekday() < 5 and 6 <= gen.hour < 11 and gen.date() == ahora.astimezone(_AR).date()
        if not manana:
            return "de la web, a X solo van las notas del diario de la mañana"
        if plan and ahora - gen > timedelta(minutes=40):
            return "no está en el carrusel del diario de hoy"
    return ""


def espera_plan(pub: dict, plan: list, ahora: datetime) -> str:
    """Una nota propia de la web cargada a la mañana de un día hábil (la carga de las 7) espera el plan
    del carrusel para salir en X a su hora de Instagram; si el plan no llega en ESPERA_PLAN_H, sale sola."""
    if pub.get("origen") != "web" or pub.get("ig_hora") or plan:
        return ""
    gen = (estado.de_iso(pub.get("generado")) or ahora).astimezone(_AR)
    if gen.weekday() >= 5 or not 6 <= gen.hour < 11 or ahora - gen > timedelta(hours=ESPERA_PLAN_H):
        return ""
    return "espera su hora en Instagram (el carrusel del diario todavía no dejó su plan)"


# ------------------------------------------------------------------------------------ cuándo sale
def planificar(est: dict, ahora: datetime) -> tuple[list, list, dict]:
    """(asignaciones, a_desprogramar, motivos_de_espera) para X: cada pieza a la hora en que salió en
    las otras redes (o lo antes posible), sin dos posteos de X a menos de X_SEPARACION_SINCRO_MIN."""
    from . import planificador as PL, tema
    pubs = [p for p in est["publicaciones"].values() if p["plataforma"] == "x"]
    sep = timedelta(minutes=separacion_min())
    ritmo_web = timedelta(minutes=ritmo_web_min())
    horizonte = ahora + timedelta(minutes=horizonte_min())
    ocupadas = sorted(estado.de_iso(p["dueAt"]) for p in pubs if p["estado"] in CUENTAN and p.get("dueAt"))
    web = [estado.de_iso(p["dueAt"]) for p in pubs
           if p["estado"] in CUENTAN and p.get("dueAt") and p["origen"] == "web"]
    ultima_web = max(web) if web else None
    en_buffer = [p for p in pubs if p["estado"] in ("programado", "enviando")
                 and (estado.de_iso(p.get("dueAt")) or ahora) > ahora - timedelta(minutes=2)]
    libres = config.max_pendientes("x") - len(en_buffer)
    tope24 = config.tope_24h("x")
    medido = (est.get("pausas") or {}).get("x") or {}
    if medido.get("tope_medido") and (estado.de_iso(medido.get("medido")) or ahora) > ahora - timedelta(days=7):
        tope24 = min(tope24, int(medido["tope_medido"]))
    por_dia = {}
    for p in pubs:
        if p["estado"] in CUENTAN and p.get("dueAt"):
            k = (PL._dia_ar(estado.de_iso(p["dueAt"])), p["origen"])
            por_dia[k] = por_dia.get(k, 0) + 1
    linea = PL.linea_del_canal(est, "x")
    intercalar, n_inter = config.intercalado(), config.min_entre_policiales()

    # A la par de Instagram (20/10/2026): cada nota del diario toma la hora del plan del carrusel.
    plan = plan_ig(ahora) if a_la_par_activo() else []
    for p in pubs:
        if p["estado"] == "pendiente" and p["origen"] == "web" and not p.get("ig_hora") and plan:
            h = hora_en_ig(p, plan)
            if h:
                p["ig_hora"] = estado.iso(h)
                p["vence"] = estado.iso(max(estado.de_iso(p.get("vence")) or h, h + timedelta(hours=1)))

    # Lo del diario y los recortes primero (a la par de IG); después lo urgente, los VIRALES (19/10/2026)
    # y el resto, por orden de llegada.
    cola = sorted((p for p in pubs if p["estado"] == "pendiente"),
                  key=lambda p: (not prioritaria(p), not p.get("urgente"), not p.get("viral"),
                                 estado.de_iso(p.get("ig_hora") or p.get("generado")) or ahora))
    reserva = reserva_virales()
    asignadas, espera = [], {}
    for pub in cola:
        if libres <= 0:
            espera[pub["clave"]] = "Buffer lleno (10 programadas por canal en el plan Free)"
            continue
        motivo = espera_plan(pub, plan, ahora) if a_la_par_activo() else ""
        if motivo:
            espera[pub["clave"]] = motivo
            continue
        policial = tema.es_policial(pub)
        if pub.get("ig_hora"):
            # A la hora exacta en que sale en Instagram (los demás se acomodan alrededor).
            t = max(ahora + timedelta(minutes=2), estado.de_iso(pub["ig_hora"]))
        else:
            t = max(ahora + timedelta(minutes=2 if prioritaria(pub) else 3),
                    estado.de_iso(pub.get("generado")) or ahora)
            if pub["origen"] == "web" and ultima_web:
                t = max(t, ultima_web + ritmo_web)
            t = PL.en_franja(t, "x")
            for _ in range(24 * 60):
                libre = all(abs(t - d) >= sep for d in ocupadas)
                if libre and not (policial and intercalar and PL.pegado(linea, t, n_inter)):
                    break
                t = PL.en_franja(t + timedelta(minutes=1), "x")
        if t > horizonte:
            espera[pub["clave"]] = "espera su momento (se programa cerca de la hora en que sale en las otras redes)"
            continue
        dia = PL._dia_ar(t)
        en24 = sum(1 for d in ocupadas if t - timedelta(hours=24) < d <= t)
        if not pub.get("viral") and not prioritaria(pub) and en24 >= tope24 - reserva:
            espera[pub["clave"]] = f"los últimos {reserva} lugares de las 24 h quedan para los reels virales"
            continue
        # Un viral no se frena por el cupo de su origen: solo por el tope total de X.
        if (not pub.get("viral") and not prioritaria(pub)
                and por_dia.get((dia, pub["origen"]), 0) >= config.cupo(pub["origen"], "x")):
            espera[pub["clave"]] = (f"cupo diario de {config.NOMBRE_ORIGEN[pub['origen']]} en X completo "
                                    f"({config.cupo(pub['origen'], 'x')})")
            continue
        if en24 >= tope24:
            espera[pub["clave"]] = f"tope de {tope24} publicaciones en 24 h de X"
            continue
        t = PL._segundo_fijo(t, pub["clave"])
        asignadas.append((pub["clave"], estado.iso(t)))
        ocupadas.append(t)
        ocupadas.sort()
        linea.append((t, policial))
        if pub["origen"] == "web":
            ultima_web = max(ultima_web or t, t)
        por_dia[(dia, pub["origen"])] = por_dia.get((dia, pub["origen"]), 0) + 1
        libres -= 1
    return asignadas, [], espera
