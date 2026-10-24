"""El informe semanal como mail (HTML + texto) con una planilla CSV de los posteos de la semana."""
from __future__ import annotations

import csv
import html
import io
import smtplib
import ssl
from datetime import timedelta
from email.message import EmailMessage
from email.utils import formataddr

from utils.config import get

from . import analisis as A
from . import etiquetas as ET

NARANJA, GRIS, VERDE, ROJO = "#E8590C", "#6b6b6b", "#1b7f3b", "#b42318"
MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre")
RED_NOMBRE = {"instagram": "Instagram", "facebook": "Facebook"}
CUENTA = [("seguidores", "Seguidores al cierre"), ("neto", "Seguidores ganados (neto)"), ("nuevos", "Nuevos seguidores"),
          ("bajas", "Dejaron de seguir"), ("alcance", "Espectadores (cuentas alcanzadas)"), ("vistas", "Visualizaciones"),
          ("interacciones", "Interacciones"), ("visitas_perfil", "Visitas al perfil")]


def e(t) -> str:
    return html.escape(str(t if t is not None else ""))


def rango(ini, fin) -> str:
    ult = fin - timedelta(days=1)
    if ini.month == ult.month:
        return f"lunes {ini.day} al domingo {ult.day} de {MESES[ult.month - 1]}"
    return f"lunes {ini.day} de {MESES[ini.month - 1]} al domingo {ult.day} de {MESES[ult.month - 1]}"


def _color(v, al_reves=False):
    if v is None:
        return GRIS
    return (ROJO if v >= 0 else VERDE) if al_reves else (VERDE if v >= 0 else ROJO)


def _tabla(cabeza, filas) -> str:
    th = "".join(f"<th style='text-align:left;padding:6px 8px;border-bottom:2px solid {NARANJA};font-size:13px'>{c}</th>"
                 for c in cabeza)
    tr = "".join("<tr>" + "".join(f"<td style='padding:6px 8px;border-bottom:1px solid #eee;font-size:13px;"
                                  f"vertical-align:top'>{c}</td>" for c in f) + "</tr>" for f in filas)
    return f"<table style='border-collapse:collapse;width:100%;margin:6px 0 14px'>{th and '<tr>' + th + '</tr>'}{tr}</table>"


def _h2(t):
    return f"<h2 style='color:{NARANJA};font-size:19px;margin:28px 0 6px;border-bottom:1px solid #f3c9b3'>{t}</h2>"


def _h3(t):
    return f"<h3 style='font-size:15px;margin:18px 0 4px'>{t}</h3>"


def _p(t, chico=False):
    return f"<p style='margin:4px 0;font-size:{13 if chico else 14}px;color:{GRIS if chico else '#222'}'>{t}</p>"


def _link(p) -> str:
    t = e(p.get("titulo") or "(sin texto)")
    return f"<a href='{e(p['link'])}' style='color:#222'>{t}</a>" if p.get("link") else t


def _desc_posteo(p) -> str:
    return (f"{_link(p)}<br><span style='color:{GRIS};font-size:12px'>{e(p.get('origen'))} · "
            f"{e(A.tipo_de(p))} · {e(p.get('dia'))} {p.get('hora', 0):02d} h</span>")


# ---------------------------------------------------------------- resumen
def resumen(inf: dict) -> list:
    lineas = []
    for red, r in inf["redes"].items():
        c, c0 = r["cuenta"], r["cuenta_ant"]
        if c.get("neto") is not None:
            t = f"{RED_NOMBRE[red]}: {A.signo(c['neto'])} seguidores"
            if c.get("nuevos") is not None and c.get("bajas") is not None:
                t += f" ({A.fmt(c['nuevos'])} nuevos y {A.fmt(c['bajas'])} que se fueron)"
            if c0.get("neto") is not None:
                t += f"; la semana anterior {A.signo(c0['neto'])}"
            lineas.append(t + ".")
        for m in ("vistas", "alcance", "likes"):
            if (r["metricas"].get(m) or {}).get("texto"):
                lineas.append(f"{RED_NOMBRE[red]}: {r['metricas'][m]['texto']}")
                break
        cambios = [f for f in r["formato_corrida"] if f["cambiar"]]
        if cambios:
            f = cambios[0]
            lineas.append(f"{RED_NOMBRE[red]}, {f['grupo']}: {f['veredicto']}.")
        hs = [h for h in r["horarios"] if h["recomendacion"].startswith("probar")]
        if hs:
            lineas.append(f"{RED_NOMBRE[red]}, {hs[0]['grupo']}: {hs[0]['recomendacion']}.")
    return lineas


# ---------------------------------------------------------------- secciones
def _seccion_cuenta(r) -> str:
    c, c0 = r["cuenta"], r["cuenta_ant"]
    filas = []
    for k, nombre in CUENTA:
        if c.get(k) is None and c0.get(k) is None:
            continue
        ch = A.cambio(c.get(k), c0.get(k)) if k not in ("seguidores",) else None
        filas.append([nombre, A.fmt(c.get(k)), A.fmt(c0.get(k)),
                      f"<span style='color:{_color(ch, k == 'bajas')}'>{A.pct(ch)}</span>"])
    filas.append(["Posteos publicados (sin historias)", A.fmt(r["n"]), A.fmt(r["n_ant"]),
                  A.pct(A.cambio(r["n"], r["n_ant"]))])
    if r.get("rend_semana") is not None:
        ch = A.cambio(r["rend_semana"], r["rend_semana_ant"])
        filas.append(["Rendimiento del posteo típico (1,0 = normal del mes)", A.fmt(r["rend_semana"], 2),
                      A.fmt(r["rend_semana_ant"], 2), f"<span style='color:{_color(ch)}'>{A.pct(ch)}</span>"])
    return _tabla(["", "Esta semana", "Anterior", "Cambio"], filas)


def _seccion_por_que(r) -> str:
    out = []
    for m, nombre in A.METRICAS + [("dejar_de_seguir", "Dejaron de seguir"), ("ocultar", "Ocultaron")]:
        desc = r["metricas"].get(m)
        if not desc:
            continue
        if desc.get("texto"):
            extra = ""
            for lado, titulo in (("suben", "Sumaron"), ("bajan", "Restaron")):
                gs = desc.get(lado) or []
                if gs:
                    extra += (f"<br><span style='color:{GRIS};font-size:12px'>{titulo}: " + "; ".join(
                        f"{e(g['grupo'])} {A.signo(g['delta'])}" for g in gs) + "</span>")
            out.append(f"<li style='margin:6px 0;font-size:14px'>{e(desc['texto'])}{extra}</li>")
        else:
            out.append(f"<li style='margin:6px 0;font-size:14px'>{nombre}: {A.fmt(desc['total'])} en total "
                       f"(la semana anterior no estaba medido).</li>")
    return f"<ul style='padding-left:18px'>{''.join(out)}</ul>" if out else _p("Todavía no hay dos semanas medidas para comparar.", True)


def _seccion_seguidores(r, red) -> str:
    out = []
    sp = r["seguidores_posteos"]
    if sp:
        out.append(_h3("Los posteos que más seguidores trajeron"))
        out.append(_tabla(["Posteo", "Seguidores nuevos", "Vistas"],
                          [[_desc_posteo(p), A.fmt(A.valor(p, "seguidores")), A.fmt(A.valor(p, "vistas"))] for p in sp]))
    rk = r["rank_seguidores"]
    if rk:
        out.append(_h3("Qué tipo de posteo trae más seguidores (últimas 4 semanas, promedio por posteo)"))
        out.append(_tabla(["Tipo", "Seguidores por posteo", "Posteos"],
                          [[e(g), A.fmt(v, 2), n] for g, v, n in rk[:6]]))
    dm = r["dias_malos"]
    que = "bajas" if dm["tipo"] == "bajas" else "caída neta de seguidores"
    if dm["dias"]:
        out.append(_h3("Por qué se fueron seguidores: los días con más " + que))
        for x in dm["dias"]:
            dia = x["dia"]
            t = (f"<b>{e(_dia_largo(dia))}</b>: {A.fmt(x['valor'])} ({'bajas' if dm['tipo'] == 'bajas' else 'menos'}; "
                 f"un día normal {A.fmt(x['media'])}). Ese día salieron {x['publicados']} posteos "
                 f"(lo normal son {A.fmt(x['publicados_prom'])})")
            if x["excesos"]:
                t += ", con más de lo habitual de: " + "; ".join(
                    f"{e(g['grupo'])} ({g['n']} contra {A.fmt(g['promedio'])} de promedio)" for g in x["excesos"])
            out.append(_p(t + "."))
    rg = r["regresion"]
    if rg["coef"]:
        malos = [c for c in rg["coef"] if c["por_posteo"] >= 0.3][:3]
        buenos = [c for c in reversed(rg["coef"]) if c["por_posteo"] <= -0.3][:2]
        if malos or buenos:
            out.append(_h3(f"Qué corrida se asocia con más {que} (estimación con {rg['dias']} días)"))
            filas = [[e(c["grupo"]), f"<span style='color:{ROJO}'>+{A.fmt(c['por_posteo'], 1)} por posteo</span>"] for c in malos]
            filas += [[e(c["grupo"]), f"<span style='color:{VERDE}'>{A.fmt(c['por_posteo'], 1)} por posteo</span>"] for c in buenos]
            out.append(_tabla(["Corrida", f"Efecto en {que}"], filas))
            out.append(_p("Meta no dice quién se fue ni por qué posteo: esto cruza los días en que se fue gente con lo "
                          "que se publicó esos días. Es una pista, no una prueba; gana peso semana a semana.", True))
    elif rg["dias"] < 14:
        out.append(_p(f"La estimación de qué corrida hace perder seguidores necesita 14 días con datos "
                      f"(hay {rg['dias']}).", True))
    neg = [(m, nombre, r["negativos"][m]) for m, nombre in A.NEGATIVAS if r["negativos"].get(m)]
    if neg:
        out.append(_h3("Señales negativas directas"))
        filas = []
        for m, nombre, ps in neg:
            for p in ps:
                filas.append([e(nombre), _desc_posteo(p), A.fmt(A.valor(p, m))])
        out.append(_tabla(["Señal", "Posteo", "Cantidad"], filas))
    if not out:
        out.append(_p("Sin datos de seguidores por posteo todavía (ver «Lo que falta medir»).", True))
    return "".join(out)


def _dia_largo(f: str) -> str:
    from datetime import date
    x = date.fromisoformat(f)
    return f"{ET.DIAS[x.weekday()]} {x.day}/{x.month}"


def _seccion_rankings(r) -> str:
    out = []
    filas = []
    for m, nombre in A.METRICAS:
        rk = r["rankings"].get(m)
        if not rk or len(rk) < 2:
            continue
        g1, v1, n1 = rk[0]
        g0, v0, n0 = rk[-1]
        dec = 1 if m in A.ESCASAS else 0
        filas.append([nombre, f"<b>{e(g1)}</b><br><span style='color:{GRIS};font-size:12px'>{A.fmt(v1, dec)} por posteo · {n1} posteos</span>",
                      f"{e(g0)}<br><span style='color:{GRIS};font-size:12px'>{A.fmt(v0, dec)} por posteo · {n0} posteos</span>"])
    if filas:
        out.append(_h3("Por categoría y formato"))
        out.append(_tabla(["Métrica", "Genera MÁS", "Genera MENOS"], filas))
    filas = []
    for m, nombre in A.METRICAS:
        rk = r["rankings_corrida"].get(m)
        if not rk or len(rk) < 2:
            continue
        filas.append([nombre, f"<b>{e(rk[0][0])}</b> ({A.fmt(rk[0][1])})", f"{e(rk[-1][0])} ({A.fmt(rk[-1][1])})"])
    if filas:
        out.append(_h3("Por corrida"))
        out.append(_tabla(["Métrica", "Genera MÁS", "Genera MENOS"], filas))
    filas = []
    for m, nombre in A.NEGATIVAS:
        rk = r["rankings"].get(m)
        if rk:
            filas.append([nombre, f"<b>{e(rk[0][0])}</b> ({A.fmt(rk[0][1], 1)} por posteo)",
                          f"{e(rk[-1][0])} ({A.fmt(rk[-1][1], 1)})" if len(rk) > 1 else "—"])
    if filas:
        out.append(_h3("Lo negativo"))
        out.append(_tabla(["Señal", "Genera MÁS", "Genera MENOS"], filas))
    return "".join(out) or _p(f"Hace falta que cada tipo tenga al menos {A.MIN_N} posteos medidos.", True)


def _seccion_formato(r) -> str:
    out = []
    for titulo, clave in (("Por corrida", "formato_corrida"), ("Por categoría", "formato_categoria")):
        filas = []
        for f in r[clave]:
            formatos = " · ".join(f"{ET.FORMATOS.get(k, k)} {A.fmt(v[0], 2)} ({v[1]})" for k, v in f["formatos"].items())
            ver = f"<b style='color:{NARANJA}'>{e(f['veredicto'])}</b>" if f["cambiar"] else e(f["veredicto"])
            filas.append([e(f["grupo"]), formatos, ver])
        if filas:
            out.append(_h3(titulo))
            out.append(_tabla(["", "Rendimiento por formato (posteos)", "Veredicto"], filas))
    if out:
        out.append(_p("Rendimiento 1,0 = un posteo típico de la red en el último mes; 2,0 = el doble. Se recomienda "
                      f"cambiar solo si la diferencia es de {round((A.VENTAJA - 1) * 100)} % o más y hay {A.MIN_N} posteos "
                      "o más de cada formato. En naranja: lo que conviene cambiar.", True))
    return "".join(out) or _p("Todavía no hay suficientes posteos de distintos formatos para comparar.", True)


def _seccion_horarios(r, audiencia) -> str:
    filas = []
    for h in r["horarios"] + r["horas_general"]:
        habit = ", ".join(f"{x:02d}" for x in h["habituales"])
        mejor = f"{h['mejor_hora']:02d} h ({A.fmt(h['mejor_hora_rend'], 2)})" if h["mejor_hora"] is not None else "—"
        rec = h["recomendacion"]
        rec = f"<b style='color:{NARANJA}'>{e(rec)}</b>" if rec.startswith("probar") else e(rec or "faltan datos")
        filas.append([e(h["grupo"]), habit, mejor, rec, e(ET.FORMATOS.get(h["mejor_formato"], "—"))])
    out = _tabla(["Corrida", "Sale a las", "Hora que mejor rinde", "Recomendación", "Formato que mejor rinde"], filas) if filas else ""
    if audiencia:
        top = sorted(audiencia.items(), key=lambda kv: -kv[1])[:4]
        out += _p("Horas en que más seguidores de Instagram están conectados: " +
                  ", ".join(f"{int(h):02d} h" for h, _ in sorted(top, key=lambda kv: int(kv[0]))) + ".")
    return out or _p("Faltan posteos medidos para comparar horarios.", True)


def _seccion_posteos(r) -> str:
    out = []
    for titulo, clave in (("Los 5 mejores de la semana", "mejores"), ("Los 5 que menos rindieron", "peores")):
        if r[clave]:
            out.append(_h3(titulo))
            out.append(_tabla(["Posteo", "Rend.", "Vistas", "Me gusta", "Coment.", "Compart."],
                              [[_desc_posteo(p), A.fmt(p["_rend"], 2), A.fmt(A.valor(p, "vistas")),
                                A.fmt(A.valor(p, "likes")), A.fmt(A.valor(p, "comentarios")),
                                A.fmt(A.valor(p, "compartidas"))] for p in r[clave]]))
    hs = [p for p in r["historias"] if A.valor(p, "vistas") is not None]
    if hs:
        out.append(_h3(f"Historias ({len(hs)})"))
        filas = [[_desc_posteo(p), A.fmt(A.valor(p, "vistas")), A.fmt(A.valor(p, "salidas")),
                  A.fmt(A.valor(p, "respuestas"))] for p in sorted(hs, key=lambda p: -(A.valor(p, "vistas") or 0))[:8]]
        out.append(_tabla(["Historia", "Vistas", "Se fueron", "Respuestas"], filas))
    return "".join(out)


def faltantes(perm: dict) -> list:
    out = []
    if not perm.get("ig_insights"):
        out.append("Instagram: visualizaciones, espectadores, compartidas, guardados, reposts, seguidores por posteo, "
                   "bajas por día y horas de conexión. Falta el permiso «instagram_manage_insights» en el token.")
    if not perm.get("fb_insights"):
        out.append("Facebook: espectadores, seguidores por reel, bajas por día, ocultamientos y «dejar de seguir» por "
                   "posteo. Falta el permiso «read_insights».")
    if not perm.get("fb_posteos"):
        out.append("Facebook: los posteos que no son reels (fotos, carruseles, links). Falta «pages_read_user_content».")
    return out


def html_informe(inf: dict) -> str:
    partes = ["<div style='font-family:Arial,Helvetica,sans-serif;max-width:760px;margin:auto;color:#222'>",
              f"<div style='border-top:6px solid {NARANJA};padding:14px 0 4px'>"
              f"<div style='font-size:13px;color:{GRIS}'>@diarioyradio · Instagram y Facebook</div>"
              f"<h1 style='font-size:24px;margin:4px 0'>Métricas de la semana</h1>"
              f"<div style='font-size:15px'>{e(rango(inf['ini'], inf['fin']))}, comparada con la semana anterior</div></div>"]
    res = resumen(inf)
    if res:
        partes.append(_h2("En pocas palabras"))
        partes.append("<ul style='padding-left:18px'>" + "".join(f"<li style='margin:5px 0;font-size:14px'>{e(x)}</li>" for x in res) + "</ul>")
    for red, r in inf["redes"].items():
        n = RED_NOMBRE[red]
        partes += [f"<h1 style='font-size:22px;margin:34px 0 0;color:{NARANJA}'>{n}</h1>",
                   _h2("La cuenta"), _seccion_cuenta(r),
                   _h2("Qué subió, qué bajó y por qué"), _seccion_por_que(r),
                   _h2("Seguidores: por qué llegan y por qué se van"), _seccion_seguidores(r, red),
                   _h2("Qué tipo de posteo genera más (últimas 4 semanas)"), _seccion_rankings(r),
                   _h2("¿El formato es el correcto?"), _seccion_formato(r),
                   _h2("Mejor horario por corrida"), _seccion_horarios(r, inf["audiencia"] if red == "instagram" else None),
                   _h2("Posteos de la semana"), _seccion_posteos(r)]
    falta = faltantes(inf["permisos"])
    if falta:
        partes.append(_h2("Lo que falta medir"))
        partes.append("<ul style='padding-left:18px'>" + "".join(f"<li style='font-size:13px;margin:4px 0'>{e(x)}</li>" for x in falta) + "</ul>")
        partes.append(_p("Cuando se agreguen esos permisos al token, el informe los suma solo: no hay que tocar nada más.", True))
    partes.append(_p("Cada posteo se mide una vez, a las 48 horas de publicado, para comparar todos a la misma edad. "
                     "Este informe lo arma un programa con reglas fijas en GitHub: no usa inteligencia artificial "
                     "ni gasta tokens. Adjunto: la planilla con todos los posteos de la semana.", True))
    partes.append("</div>")
    return "".join(partes)


def texto_informe(inf: dict) -> str:
    lineas = [f"Métricas de la semana ({rango(inf['ini'], inf['fin'])})", ""]
    lineas += [f"- {x}" for x in resumen(inf)]
    lineas += ["", "El informe completo está en la versión HTML de este mail y en la planilla adjunta."]
    return "\n".join(lineas)


def csv_posteos(inf: dict, d: dict) -> bytes:
    campos = ["red", "fecha", "dia", "hora", "origen", "categoria", "formato", "titulo", "link", "rendimiento"] + \
             [m for m, _ in A.METRICAS] + ["tiempo_medio_s", "visitas_perfil", "dejar_de_seguir", "ocultar", "salidas",
                                           "respuestas", "edad_h"]
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(campos)
    refs = A.referencias([p for p in d["posteos"].values()
                          if (inf["fin"] - timedelta(days=A.DIAS_RANKING)).isoformat() <= (p.get("fecha") or "") < inf["fin"].isoformat()])
    for p in sorted(d["posteos"].values(), key=lambda p: p.get("cuando") or ""):
        if not (inf["ini"].isoformat() <= (p.get("fecha") or "") < inf["fin"].isoformat()):
            continue
        fila = {**p, **(p.get("m") or {}), "categoria": ET.nombre_categoria(p.get("categoria")),
                "formato": ET.FORMATOS.get(p.get("formato"), p.get("formato")),
                "rendimiento": A.fmt(A.rendimiento(p, refs), 2) if A.rendimiento(p, refs) is not None else ""}
        w.writerow([_celda(fila.get(c)) for c in campos])
    return ("﻿" + buf.getvalue()).encode("utf-8")       # BOM: Excel abre bien los acentos


def _celda(v):
    if v is None:
        return ""
    return str(v).replace(".", ",") if isinstance(v, float) else v


def asunto(inf: dict) -> str:
    partes = []
    for red, r in inf["redes"].items():
        neto = r["cuenta"].get("neto")
        v = (r["metricas"].get("vistas") or r["metricas"].get("likes") or {}).get("cambio")
        t = RED_NOMBRE[red]
        if neto is not None:
            t += f" {A.signo(neto)} seguidores"
        if v is not None:
            t += f", {'vistas' if r['metricas'].get('vistas') else 'me gusta'} {A.pct(v)}"
        partes.append(t)
    ult = inf["fin"] - timedelta(days=1)
    return f"Métricas de la semana {inf['ini'].day}/{inf['ini'].month} al {ult.day}/{ult.month}: " + " · ".join(partes)


def enviar(asunto_: str, html_: str, texto: str, adjunto: bytes, nombre_adjunto: str) -> bool:
    remitente, clave = get("MAIL_FROM"), get("MAIL_APP_PASSWORD")
    destino = (get("METRICAS_EMAIL") or get("VIDEOS_NOTIFY_EMAIL") or get("MAIL_FROM") or "").strip()
    if not remitente or not clave or not destino:
        print("[métricas] sin credenciales de mail (MAIL_FROM/MAIL_APP_PASSWORD): no se envía.")
        return False
    msg = EmailMessage()
    msg["From"] = formataddr((get("MAIL_FROM_NAME") or "Diario La Campaña", remitente))
    msg["To"] = destino
    msg["Subject"] = asunto_
    msg.set_content(texto)
    msg.add_alternative(html_, subtype="html")
    msg.add_attachment(adjunto, maintype="text", subtype="csv", filename=nombre_adjunto)
    with smtplib.SMTP(get("SMTP_HOST") or "smtp.gmail.com", int(get("SMTP_PORT") or 587), timeout=60) as s:
        s.starttls(context=ssl.create_default_context())
        s.login(remitente, clave)
        s.send_message(msg)
    print(f"[métricas] informe enviado a {destino}")
    return True
