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

import re
import unicodedata
from datetime import datetime, timedelta

from . import config, estado

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

    cola = sorted((p for p in pubs if p["estado"] == "pendiente"),
                  key=lambda p: (not p.get("urgente"), estado.de_iso(p.get("generado")) or ahora))
    asignadas, espera = [], {}
    for pub in cola:
        if libres <= 0:
            espera[pub["clave"]] = "Buffer lleno (10 programadas por canal en el plan Free)"
            continue
        t = max(ahora + timedelta(minutes=3), estado.de_iso(pub.get("generado")) or ahora)
        if pub["origen"] == "web" and ultima_web:
            t = max(t, ultima_web + ritmo_web)
        t = PL.en_franja(t, "x")
        policial = tema.es_policial(pub)
        for _ in range(24 * 60):
            libre = all(abs(t - d) >= sep for d in ocupadas)
            if libre and not (policial and intercalar and PL.pegado(linea, t, n_inter)):
                break
            t = PL.en_franja(t + timedelta(minutes=1), "x")
        if t > horizonte:
            espera[pub["clave"]] = "espera su momento (se programa cerca de la hora en que sale en las otras redes)"
            continue
        dia = PL._dia_ar(t)
        if por_dia.get((dia, pub["origen"]), 0) >= config.cupo(pub["origen"], "x"):
            espera[pub["clave"]] = (f"cupo diario de {config.NOMBRE_ORIGEN[pub['origen']]} en X completo "
                                    f"({config.cupo(pub['origen'], 'x')})")
            continue
        if sum(1 for d in ocupadas if t - timedelta(hours=24) < d <= t) >= tope24:
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
