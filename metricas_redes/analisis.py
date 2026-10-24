"""Las cuentas del informe: comparación con la semana anterior, causas, rankings, formato y horarios.

Funciones puras sobre el estado (`state/metricas_redes.json`): no tocan la red ni usan IA. Las
reglas para decir «por qué» son cuentas a la vista:
- Un total que cambia se parte en lo que se debe a publicar más o menos (volumen) y lo que se debe
  a que cada posteo rindió más o menos (rendimiento), y en cuánto aportó cada corrida.
- «Rendimiento» de un posteo = sus números divididos por la mediana de su red en las últimas 4
  semanas, con pesos (una compartida cuesta más que un me gusta). 1,0 = un posteo típico.
- Las bajas de seguidores Meta no las da por posteo: se cruzan los días con más bajas con lo que se
  publicó esos días, y se estima cuánto pesa cada corrida con una regresión sobre 5 semanas. Es una
  estimación y el informe lo dice así.
"""
from __future__ import annotations

import statistics
from datetime import date, timedelta

from . import etiquetas as ET

METRICAS = [("vistas", "Visualizaciones"), ("alcance", "Espectadores (cuentas alcanzadas)"),
            ("likes", "Me gusta"), ("comentarios", "Comentarios"), ("compartidas", "Compartidas"),
            ("reposts", "Reposts"), ("guardados", "Guardados"), ("seguidores", "Nuevos seguidores")]
NEGATIVAS = [("dejar_de_seguir", "Dejaron de seguir la página desde el posteo (FB)"),
             ("ocultar", "Ocultaron el posteo (FB)"), ("denuncias", "Lo denunciaron (FB)"),
             ("salidas", "Se fueron de la historia (IG)")]
PESOS = {"vistas": 1, "alcance": 1, "likes": 1, "comentarios": 2, "compartidas": 3, "reposts": 3,
         "guardados": 2, "seguidores": 4}
TOPE_RATIO = 10
MIN_N = 3                 # posteos mínimos de un grupo para entrar en un ranking
MIN_N_HORA = 5            # posteos mínimos en una hora para recomendar pasarse a esa hora
VENTAJA = 1.15            # cuánto mejor tiene que rendir algo para recomendar cambiarlo
DIAS_RANKING = 28
DIAS_REGRESION = 35
REDES = ("instagram", "facebook")


# ---------------------------------------------------------------- utilidades
def mediana(v):
    v = [x for x in v if isinstance(x, (int, float))]
    return statistics.median(v) if v else None


def suma(v):
    v = [x for x in v if isinstance(x, (int, float))]
    return sum(v) if v else None


def cambio(nuevo, viejo):
    if not isinstance(nuevo, (int, float)) or not isinstance(viejo, (int, float)) or viejo == 0:
        return None
    return (nuevo - viejo) / abs(viejo) * 100


def semana_de(hoy: date, dias_maduracion: int = 2) -> tuple:
    """(lunes, lunes siguiente) de la última semana completa cuyos posteos ya tienen la edad de medida."""
    fin = hoy - timedelta(days=hoy.weekday())          # el lunes de esta semana
    if (hoy - fin).days < dias_maduracion:
        fin -= timedelta(days=7)
    return fin - timedelta(days=7), fin


def en(posteos, ini: date, fin: date, red=None, historias=False):
    out = []
    for p in posteos:
        f = p.get("fecha") or ""
        if not (ini.isoformat() <= f < fin.isoformat()):
            continue
        if red and p.get("red") != red:
            continue
        if (p.get("formato") == "historia") != historias:
            continue
        out.append(p)
    return out


def valor(p, m):
    v = (p.get("m") or {}).get(m)
    return v if isinstance(v, (int, float)) else None


def tiene(posteos, m, minimo=0.5) -> bool:
    """¿La métrica está medida en suficientes posteos como para compararla?"""
    if not posteos:
        return False
    return sum(1 for p in posteos if valor(p, m) is not None) >= minimo * len(posteos)


# ---------------------------------------------------------------- rendimiento
def referencias(posteos) -> dict:
    """{red: {métrica: mediana}} para normalizar."""
    out = {}
    for red in REDES:
        ps = [p for p in posteos if p.get("red") == red and p.get("formato") != "historia"]
        out[red] = {m: mediana([valor(p, m) for p in ps]) for m, _ in METRICAS}
    return out


def rendimiento(p, refs) -> float | None:
    ref = refs.get(p.get("red")) or {}
    num = den = 0.0
    for m, w in PESOS.items():
        v, r = valor(p, m), ref.get(m)
        if v is None or not r:
            continue
        num += w * min(v / r, TOPE_RATIO)
        den += w
    return round(num / den, 2) if den else None


def puntuar(posteos, refs):
    for p in posteos:
        p["_rend"] = rendimiento(p, refs)


# ---------------------------------------------------------------- cuentas
def _seg(d, red, dia: date):
    """Seguidores al cierre de `dia` (o el último dato anterior, hasta 3 días)."""
    serie = d["cuentas"].get(red) or {}
    for n in range(4):
        v = (serie.get((dia - timedelta(days=n)).isoformat()) or {}).get("seguidores")
        if isinstance(v, (int, float)):
            return v
    return None


def cuenta(d, red, ini: date, fin: date) -> dict:
    seg_ini, seg_fin = _seg(d, red, ini - timedelta(days=1)), _seg(d, red, fin - timedelta(days=1))
    diario = d["diario"].get(red) or {}
    dias = [(ini + timedelta(days=i)).isoformat() for i in range((fin - ini).days)]
    out = {"seguidores": seg_fin, "neto": (seg_fin - seg_ini) if seg_ini is not None and seg_fin is not None else None}
    for k in ("nuevos", "bajas", "alcance", "vistas", "interacciones", "visitas_perfil", "compartidas", "guardados"):
        vals = [(diario.get(x) or {}).get(k) for x in dias]
        out[k] = suma(vals) if sum(1 for v in vals if isinstance(v, (int, float))) >= 5 else None
    return out


# ---------------------------------------------------------------- qué cambió y por qué
def descomponer(act, ant, m, por="origen") -> dict | None:
    """Total de la métrica esta semana vs la anterior, partido en volumen y rendimiento, y por grupo."""
    if not tiene(act, m) or not tiene(ant, m):
        return None
    t1, t0 = suma(valor(p, m) for p in act) or 0, suma(valor(p, m) for p in ant) or 0
    n1, n0 = len(act), len(ant)
    prom0 = t0 / n0 if n0 else 0
    prom1 = t1 / n1 if n1 else 0
    grupos = {}
    for p, lado in [(p, 1) for p in act] + [(p, 0) for p in ant]:
        g = grupos.setdefault(p.get(por) or "Otros", {"t": [0, 0], "n": [0, 0], "med": [[], []]})
        v = valor(p, m)
        g["n"][lado] += 1
        if v is not None:
            g["t"][lado] += v
            g["med"][lado].append(v)
    aportes = sorted(({"grupo": k, "delta": g["t"][1] - g["t"][0], "n": g["n"][1], "n_ant": g["n"][0],
                       "med": mediana(g["med"][1]), "med_ant": mediana(g["med"][0])} for k, g in grupos.items()),
                     key=lambda x: x["delta"])
    return {"total": t1, "total_ant": t0, "cambio": cambio(t1, t0), "n": n1, "n_ant": n0,
            "por_posteo": prom1, "por_posteo_ant": prom0,
            "efecto_volumen": (n1 - n0) * prom0, "efecto_rendimiento": n1 * (prom1 - prom0),
            "suben": [a for a in reversed(aportes) if a["delta"] > 0][:3],
            "bajan": [a for a in aportes if a["delta"] < 0][:3]}


def explicar(desc: dict, nombre: str) -> str:
    """Una frase con la causa principal del cambio de una métrica."""
    if not desc or desc["cambio"] is None:
        return ""
    sube = desc["cambio"] >= 0
    vol, ren = desc["efecto_volumen"], desc["efecto_rendimiento"]
    if abs(vol) >= abs(ren):
        causa = (f"sobre todo porque se publicó {'más' if desc['n'] > desc['n_ant'] else 'menos'} "
                 f"({desc['n']} posteos contra {desc['n_ant']})")
    else:
        causa = (f"sobre todo porque cada posteo rindió {'más' if ren > 0 else 'menos'} "
                 f"({fmt(desc['por_posteo'])} por posteo contra {fmt(desc['por_posteo_ant'])})")
    lideres = desc["suben"] if sube else desc["bajan"]
    extra = ""
    if lideres:
        g = lideres[0]
        detalle = f"{g['n_ant']}→{g['n']} posteos"
        if g["med"] is not None and g["med_ant"] is not None:
            detalle += f", mediana {fmt(g['med_ant'])}→{fmt(g['med'])}"
        extra = f" Lo que más {'empujó' if sube else 'tiró para abajo'}: {g['grupo']} ({signo(g['delta'])}; {detalle})."
    return f"{nombre} {'subieron' if sube else 'bajaron'} {pct(desc['cambio'])}, {causa}.{extra}"


# ---------------------------------------------------------------- seguidores
def por_dia(posteos, ini: date, fin: date, por="origen") -> dict:
    out = {}
    for p in posteos:
        f = p.get("fecha")
        if f and ini.isoformat() <= f < fin.isoformat() and p.get("formato") != "historia":
            out.setdefault(f, {}).setdefault(p.get(por) or "Otros", 0)
            out[f][p.get(por) or "Otros"] += 1
    return out


def serie_bajas(d, red, ini: date, fin: date) -> tuple:
    """({fecha: valor}, qué es): bajas si Meta las da; si no, la caída neta de seguidores día a día."""
    diario = d["diario"].get(red) or {}
    dias = [(ini + timedelta(days=i)) for i in range((fin - ini).days)]
    bajas = {x.isoformat(): (diario.get(x.isoformat()) or {}).get("bajas") for x in dias}
    if sum(1 for v in bajas.values() if isinstance(v, (int, float))) >= len(dias) * 0.6:
        return {k: v for k, v in bajas.items() if isinstance(v, (int, float))}, "bajas"
    neto = {}
    for x in dias:
        a, b = _seg(d, red, x), _seg(d, red, x - timedelta(days=1))
        if a is not None and b is not None and (d["cuentas"].get(red) or {}).get(x.isoformat()):
            neto[x.isoformat()] = b - a                 # positivo = se perdieron seguidores
    return neto, "caida_neta"


def dias_malos(d, red, posteos, ini: date, fin: date) -> dict:
    """Los días de la semana con más bajas y qué se publicó de distinto esos días."""
    serie, tipo = serie_bajas(d, red, ini, fin)
    if len(serie) < 3:
        return {"tipo": tipo, "dias": []}
    hist = por_dia([p for p in posteos if p.get("red") == red], fin - timedelta(days=DIAS_RANKING), fin)
    n_dias = max(len(hist), 1)
    prom = {}
    for conteo in hist.values():
        for g, n in conteo.items():
            prom[g] = prom.get(g, 0) + n / n_dias
    media = statistics.mean(serie.values())
    malos = sorted(serie.items(), key=lambda kv: -kv[1])[:2]
    out = []
    for dia, v in malos:
        if v <= media or v <= 0:
            continue
        conteo = hist.get(dia, {})
        dif = sorted(((g, conteo.get(g, 0), prom.get(g, 0)) for g in set(conteo) | set(prom)),
                     key=lambda x: -(x[1] - x[2]))
        excesos = [{"grupo": g, "n": n, "promedio": round(pr, 1)} for g, n, pr in dif if n - pr >= 1.5][:3]
        out.append({"dia": dia, "valor": v, "media": round(media, 1), "excesos": excesos,
                    "publicados": sum(conteo.values()), "publicados_prom": round(sum(prom.values()), 1)})
    return {"tipo": tipo, "dias": out}


def regresion_bajas(d, red, posteos, fin: date) -> dict:
    """Cuántas bajas (o caída neta) se asocian a cada posteo de cada corrida, con 5 semanas de datos."""
    ini = fin - timedelta(days=DIAS_REGRESION)
    serie, tipo = serie_bajas(d, red, ini, fin)
    conteos = por_dia([p for p in posteos if p.get("red") == red], ini, fin)
    dias = sorted(serie)
    if len(dias) < 14:
        return {"tipo": tipo, "dias": len(dias), "coef": []}
    grupos = sorted({g for c in conteos.values() for g in c
                     if sum(1 for dd in dias if (conteos.get(dd) or {}).get(g)) >= 5})
    if not grupos:
        return {"tipo": tipo, "dias": len(dias), "coef": []}
    X = [[1.0] + [float((conteos.get(dd) or {}).get(g, 0)) for g in grupos] for dd in dias]
    y = [float(serie[dd]) for dd in dias]
    beta = _ridge(X, y, lam=1.0)
    coef = sorted(({"grupo": g, "por_posteo": round(b, 2)} for g, b in zip(grupos, beta[1:])),
                  key=lambda x: -x["por_posteo"])
    return {"tipo": tipo, "dias": len(dias), "coef": coef, "base": round(beta[0], 1)}


def _ridge(X, y, lam=1.0):
    k = len(X[0])
    A = [[sum(X[r][i] * X[r][j] for r in range(len(X))) + (lam if i == j and i > 0 else 0) for j in range(k)]
         for i in range(k)]
    b = [sum(X[r][i] * y[r] for r in range(len(X))) for i in range(k)]
    for c in range(k):                                         # Gauss con pivoteo parcial
        piv = max(range(c, k), key=lambda r: abs(A[r][c]))
        if abs(A[piv][c]) < 1e-12:
            continue
        A[c], A[piv], b[c], b[piv] = A[piv], A[c], b[piv], b[c]
        for r in range(k):
            if r != c and A[r][c]:
                f = A[r][c] / A[c][c]
                A[r] = [a - f * ac for a, ac in zip(A[r], A[c])]
                b[r] -= f * b[c]
    return [b[i] / A[i][i] if abs(A[i][i]) > 1e-12 else 0.0 for i in range(k)]


# ---------------------------------------------------------------- rankings
def grupos(posteos, clave) -> dict:
    out = {}
    for p in posteos:
        out.setdefault(clave(p), []).append(p)
    return out


def tipo_de(p) -> str:
    return f"{ET.nombre_categoria(p.get('categoria'))} · {ET.FORMATOS.get(p.get('formato'), p.get('formato'))}"


def ranking(posteos, m, clave=tipo_de, n_min=MIN_N) -> list:
    """[(grupo, mediana, n)] de mayor a menor, solo grupos con n_min posteos medidos."""
    filas = []
    for g, ps in grupos(posteos, clave).items():
        vals = [valor(p, m) for p in ps if valor(p, m) is not None]
        if len(vals) >= n_min:
            filas.append((g, mediana(vals), len(vals)))
    return sorted(filas, key=lambda x: -x[1])


def mejor_formato(posteos, clave) -> list:
    """Por grupo (categoría u origen): el rendimiento de cada formato y el veredicto."""
    out = []
    for g, ps in sorted(grupos(posteos, clave).items()):
        por_formato = {}
        for f, pf in grupos(ps, lambda p: p.get("formato")).items():
            r = [p["_rend"] for p in pf if p.get("_rend") is not None]
            if len(r) >= MIN_N:
                por_formato[f] = (mediana(r), len(r))
        if not por_formato:
            continue
        orden = sorted(por_formato.items(), key=lambda kv: -kv[1][0])
        if len(orden) == 1:
            veredicto = f"solo hay {ET.FORMATOS.get(orden[0][0], orden[0][0]).lower()}: no hay contra qué comparar"
        elif orden[0][1][0] >= orden[1][1][0] * VENTAJA:
            veredicto = (f"conviene {ET.FORMATOS.get(orden[0][0], orden[0][0]).lower()} "
                         f"({cuanto_mas(orden[0][1][0], orden[1][1][0])} que "
                         f"{ET.FORMATOS.get(orden[1][0], orden[1][0]).lower()})")
        else:
            veredicto = "parejo: el formato no cambia el resultado"
        usado = max(por_formato.items(), key=lambda kv: kv[1][1])[0]
        out.append({"grupo": g, "formatos": {f: v for f, v in orden}, "veredicto": veredicto,
                    "mas_usado": usado, "mejor": orden[0][0],
                    "cambiar": len(orden) > 1 and orden[0][0] != usado and orden[0][1][0] >= por_formato[usado][0] * VENTAJA})
    return out


def cuanto_mas(a: float, b: float) -> str:
    """«rinde 40 % más», «el doble», «más del triple»: cómo se dice la ventaja de a sobre b."""
    if not b:
        return "rinde bastante más"
    r = a / b
    if r >= 3:
        return "rinde más del triple"
    if r >= 1.9:
        return "rinde el doble" if r < 2.5 else "rinde dos veces y media"
    return f"rinde {round((r - 1) * 100)} % más"


def horarios(posteos, clave=lambda p: p.get("origen"), desde: str = "") -> list:
    """Por corrida: a qué horas sale ahora (posteos desde `desde`), cuál rinde mejor y con qué formato."""
    out = []
    for g, ps in sorted(grupos(posteos, clave).items()):
        con = [p for p in ps if p.get("_rend") is not None]
        if len(con) < MIN_N:
            continue
        por_hora = {}
        for p in con:
            por_hora.setdefault(p["hora"], []).append(p["_rend"])
        horas = {h: (mediana(v), len(v)) for h, v in por_hora.items()}
        recientes = [p for p in con if (p.get("fecha") or "") >= desde] or con
        cuenta_h = {}
        for p in recientes:
            cuenta_h[p["hora"]] = cuenta_h.get(p["hora"], 0) + 1
        habituales = sorted(cuenta_h, key=lambda h: -cuenta_h[h])[:3]
        validas = {h: v for h, v in horas.items() if v[1] >= MIN_N_HORA}
        mejor = max(validas, key=lambda h: validas[h][0]) if validas else None
        base = mediana([p["_rend"] for p in con])
        recomendacion = ""
        if (mejor is not None and base is not None and mejor not in habituales
                and validas[mejor][0] >= base * VENTAJA and validas[mejor][0] - base >= 0.3):
            recomendacion = f"probar más a las {mejor:02d} h: {cuanto_mas(validas[mejor][0], base)} que el promedio de la corrida"
        elif mejor is not None:
            recomendacion = "el horario actual está bien"
        formatos = {f: mediana([p["_rend"] for p in pf]) for f, pf in grupos(con, lambda p: p.get("formato")).items()
                    if len(pf) >= MIN_N}
        out.append({"grupo": g, "n": len(con), "rend": base, "habituales": sorted(habituales), "mejor_hora": mejor,
                    "mejor_hora_rend": validas[mejor][0] if mejor is not None else None,
                    "por_hora": dict(sorted(horas.items())), "recomendacion": recomendacion,
                    "mejor_formato": max(formatos, key=formatos.get) if formatos else None})
    return sorted(out, key=lambda x: -x["n"])


# ---------------------------------------------------------------- el informe entero
def informe(d: dict, hoy: date) -> dict:
    ini, fin = semana_de(hoy)
    ini0 = ini - timedelta(days=7)
    todos = [dict(p) for p in d["posteos"].values()]       # copias: el puntaje no se guarda
    ventana = [p for p in todos if (fin - timedelta(days=DIAS_RANKING)).isoformat() <= (p.get("fecha") or "") < fin.isoformat()]
    refs = referencias(ventana)
    puntuar(todos, refs)
    out = {"ini": ini, "fin": fin, "permisos": d.get("permisos") or {}, "redes": {},
           "audiencia": ((d.get("audiencia") or {}).get("instagram") or {}).get("por_hora")}
    for red in REDES:
        act, ant = en(todos, ini, fin, red), en(todos, ini0, ini, red)
        hist_red = [p for p in ventana if p.get("red") == red and p.get("formato") != "historia"]
        r = {"cuenta": cuenta(d, red, ini, fin), "cuenta_ant": cuenta(d, red, ini0, ini),
             "n": len(act), "n_ant": len(ant), "metricas": {}, "historias": en(todos, ini, fin, red, historias=True)}
        for m, nombre in METRICAS + [("dejar_de_seguir", "Dejaron de seguir"), ("ocultar", "Ocultaron")]:
            desc = descomponer(act, ant, m)
            if desc:
                desc["texto"] = explicar(desc, nombre)
                r["metricas"][m] = desc
            elif tiene(act, m):
                r["metricas"][m] = {"total": suma(valor(p, m) for p in act), "n": len(act), "texto": "",
                                    "cambio": None}
        r["seguidores_posteos"] = sorted((p for p in act if valor(p, "seguidores")), key=lambda p: -valor(p, "seguidores"))[:5]
        r["rank_seguidores"] = ranking(hist_red, "seguidores")
        r["dias_malos"] = dias_malos(d, red, todos, ini, fin)
        r["regresion"] = regresion_bajas(d, red, todos, fin)
        r["negativos"] = {m: sorted((p for p in act if valor(p, m)), key=lambda p: -valor(p, m))[:3] for m, _ in NEGATIVAS}
        r["rankings"] = {m: ranking(hist_red, m) for m, _ in METRICAS + NEGATIVAS if tiene(hist_red, m, 0.3)}
        r["rankings_corrida"] = {m: ranking(hist_red, m, clave=lambda p: p.get("origen")) for m, _ in METRICAS
                                 if tiene(hist_red, m, 0.3)}
        r["formato_categoria"] = mejor_formato(hist_red, lambda p: ET.nombre_categoria(p.get("categoria")))
        r["formato_corrida"] = mejor_formato(hist_red, lambda p: p.get("origen"))
        r["horarios"] = horarios(hist_red, desde=ini.isoformat())
        r["horas_general"] = horarios(hist_red, clave=lambda p: "Todas las publicaciones", desde=ini.isoformat())
        con = [p for p in act if p.get("_rend") is not None]
        r["mejores"] = sorted(con, key=lambda p: -p["_rend"])[:5]
        r["peores"] = sorted(con, key=lambda p: p["_rend"])[:5]
        r["rend_semana"], r["rend_semana_ant"] = mediana([p["_rend"] for p in con]), mediana(
            [p["_rend"] for p in ant if p.get("_rend") is not None])
        out["redes"][red] = r
    return out


# ---------------------------------------------------------------- formato de números
def fmt(v, dec=0) -> str:
    if v is None:
        return "—"
    if isinstance(v, float) and dec == 0 and abs(v) < 10 and v != int(v):
        dec = 1
    s = f"{v:,.{dec}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def pct(v) -> str:
    if v is None:
        return "—"
    return f"{'▲' if v >= 0 else '▼'} {fmt(abs(v), 1 if abs(v) < 10 else 0)} %"


def signo(v) -> str:
    return ("+" if v >= 0 else "−") + fmt(abs(v))
