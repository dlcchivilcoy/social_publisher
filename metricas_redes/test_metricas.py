"""Pruebas de las métricas contra un Meta FALSO (no toca la red ni manda mails).

    venv\\Scripts\\python.exe -m metricas_redes.test_metricas
"""
from __future__ import annotations

import random
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from metricas_redes import analisis as A
from metricas_redes import etiquetas as ET
from metricas_redes import informe as INF
from metricas_redes import recolector as R
from metricas_redes.meta import Meta, SinPermiso

OK, MAL = [], []


def chequear(nombre, cond, detalle=""):
    (OK if cond else MAL).append(nombre)
    print(("  ok   " if cond else "  MAL  ") + nombre + ("" if cond else f"  → {detalle}"))


AHORA = datetime(2026, 10, 28, 3, 15, tzinfo=timezone.utc)          # miércoles 00:15 de Argentina


def ts(horas_atras):
    return (AHORA - timedelta(hours=horas_atras)).strftime("%Y-%m-%dT%H:%M:%S+0000")


# ------------------------------------------------------------------ Meta falso
class MetaFalso:
    def __init__(self, scopes, invalidas_ig=("reposts",)):
        self.scopes = scopes
        self.invalidas_ig = set(invalidas_ig)
        self.pedidos = []

    def __call__(self, url, params):
        ruta = url.split("/v26.0/", 1)[1]
        self.pedidos.append((ruta, dict(params, access_token="")))
        if ruta == "debug_token":
            return {"data": {"scopes": self.scopes}}
        if ruta == "IG":
            return {"followers_count": 9900}
        if ruta == "FB":
            return {"followers_count": 20800}
        if ruta == "IG/media":
            return {"data": [
                {"id": "m1", "timestamp": ts(50), "media_type": "VIDEO", "media_product_type": "REELS",
                 "caption": "Detuvieron a dos jóvenes tras un allanamiento", "like_count": 40, "comments_count": 3},
                {"id": "m2", "timestamp": ts(60), "media_type": "CAROUSEL_ALBUM", "media_product_type": "FEED",
                 "caption": "FÚTBOL | 📰 Racing ganó el clásico", "like_count": 10, "comments_count": 1},
                {"id": "m3", "timestamp": ts(5), "media_type": "VIDEO", "media_product_type": "REELS",
                 "caption": "Muy nuevo", "like_count": 1, "comments_count": 0},
            ], "paging": {}}
        if ruta == "IG/stories":
            return {"data": [{"id": "s1", "timestamp": ts(8), "media_type": "IMAGE", "media_product_type": "STORY",
                              "caption": ""}]}
        if ruta.endswith("/insights") and ruta.split("/")[0] in ("m1", "m2", "s1"):
            if "instagram_manage_insights" not in self.scopes:
                return {"error": {"code": 10, "message": "Application does not have permission for this action"}}
            pedidas = params["metric"].split(",")
            if self.invalidas_ig & set(pedidas):
                return {"error": {"code": 100, "message": "metric not supported"}}
            if pedidas == ["navigation"]:
                return {"data": [{"name": "navigation", "total_value": {"breakdowns": [{"results": [
                    {"dimension_values": ["TAP_EXIT"], "value": 4}, {"dimension_values": ["SWIPE_FORWARD"], "value": 2},
                    {"dimension_values": ["TAP_FORWARD"], "value": 9}]}]}}]}
            return {"data": [{"name": m, "values": [{"value": {"views": 1000, "reach": 700, "follows": 5,
                                                                "ig_reels_avg_watch_time": 6400}.get(m, 7)}]}
                             for m in pedidas]}
        if ruta == "IG/insights":
            if "instagram_manage_insights" not in self.scopes:
                return {"error": {"code": 10, "message": "no permission"}}
            if params.get("metric") == "follows_and_unfollows":
                return {"data": [{"name": "follows_and_unfollows", "total_value": {"breakdowns": [{"results": [
                    {"dimension_values": ["FOLLOWER"], "value": 30}, {"dimension_values": ["NON_FOLLOWER"], "value": 12}]}]}}]}
            if params.get("metric") == "online_followers":
                return {"data": [{"name": "online_followers", "values": [{"value": {"16": 500, "17": 900}}]}]}
            if params.get("metric") == "follower_count":
                return {"data": [{"name": "follower_count", "values": [{"value": 30, "end_time": "2026-10-27T07:00:00+0000"}]}]}
            return {"data": [{"name": m, "total_value": {"value": 100}} for m in params["metric"].split(",")]}
        if ruta == "FB/video_reels":
            return {"data": [{"id": "v1", "created_time": ts(55), "description": "Gran festival en la plaza",
                              "views": 3000, "length": 31.5}]}
        if ruta == "v1":
            return {"likes": {"summary": {"total_count": 25}}, "comments": {"summary": {"total_count": 4}}}
        if ruta == "v1/video_insights":
            return {"data": [{"name": "post_video_followers", "values": [{"value": 3}]}]}
        if ruta == "FB/published_posts":
            if "pages_read_user_content" not in self.scopes:
                return {"error": {"code": 10, "message": "requires pages_read_user_content"}}
            return {"data": [
                {"id": "FB_p1", "created_time": ts(55), "message": "Gran festival en la plaza", "status_type": "added_video",
                 "attachments": {"data": [{"media_type": "video", "type": "video_inline", "target": {"id": "v1"}}]},
                 "reactions": {"summary": {"total_count": 26}}, "comments": {"summary": {"total_count": 4}}},
                {"id": "FB_p2", "created_time": ts(70), "message": "Farmacias de turno de hoy", "status_type": "added_photos",
                 "attachments": {"data": [{"media_type": "photo", "type": "photo", "target": {"id": "f9"}}]},
                 "reactions": {"summary": {"total_count": 8}}, "shares": {"count": 2}}]}
        if ruta == "FB_p2/insights":
            return {"data": [{"name": "post_media_view", "values": [{"value": 900}]},
                             {"name": "post_clicks", "values": [{"value": 12}]}]}
        if ruta == "FB/insights":
            return {"data": []}
        return {"error": {"code": 100, "message": f"ruta desconocida {ruta}"}}


def correr_foto(scopes, d=None):
    falso = MetaFalso(scopes)
    d = d or R.cargar(Path(tempfile.mkdtemp()) / "no_existe.json")
    meta = Meta("TOKEN", invalidas=d["invalidas"], get=falso, pausa=0)
    hecho = R.foto(meta, d, "IG", "FB", {"m1": {"origen": "Policiales", "categoria": "policiales", "titulo": "Dos detenidos"}},
                   ahora=AHORA, log=lambda m: None)
    return d, hecho, falso


# ------------------------------------------------------------------ 1. Meta adaptativo
print("Meta: métricas que cambian y permisos")
falso = MetaFalso({"instagram_manage_insights"})
m = Meta("T", get=falso, pausa=0)
r = m.insights("m1", ["views", "reposts", "reach"], "ig_REELS")
chequear("una métrica rechazada se saca y las demás se leen", r == {"views": 1000, "reach": 700}, r)
chequear("la rechazada queda anotada para no volver a pedirla", "reposts" in m.invalidas["ig_REELS"])
n = len(falso.pedidos)
m.insights("m1", ["views", "reposts", "reach"], "ig_REELS")
chequear("la segunda vez sale en una sola consulta", len(falso.pedidos) == n + 1)
try:
    Meta("T", get=MetaFalso(set()), pausa=0).insights("m1", ["views"], "ig_REELS")
    chequear("sin permiso avisa SinPermiso", False)
except SinPermiso:
    chequear("sin permiso avisa SinPermiso", True)

# ------------------------------------------------------------------ 2. etiquetas
print("Etiquetas")
reg = {"publicados.json": {"u1": {"titular": "T1", "instagram": {"id": "i1"}, "facebook": {"id": "f1"}, "distancia_km": 40}},
       "nacionales.json": {"piezas": {"u2": {"titular": "T2", "categoria": "", "subgeneral": "economia",
                                             "instagram": {"id": "i2"}, "facebook": {"id": "145_999"}}},
                           "carruseles": {"2026-10-20": {"id": "c1", "diapositivas": 10}}},
       "archivo.json": {"publicaciones": [["2026-10-23:pregunta", {"subtipo": "pregunta", "id": "a1", "titular": "¿Qué?"}]]}}
idx = ET.indice(reg)
chequear("policiales, nacionales, carrusel de las 22 y pregunta del día por id",
         idx["i1"]["origen"] == "Policiales" and idx["i2"]["categoria"] == "economia" and idx["999"]["origen"] == "Nacionales"
         and idx["c1"]["origen"].startswith("Nacionales · carrusel") and idx["a1"]["origen"] == "Pregunta del día")
chequear("el resto por el texto: notas de la mañana, entrevistas, notas en reel, agro",
         ET.origen_por_texto("FÚTBOL | 📰 Racing ganó", "carrusel")[0].startswith("Notas del diario")
         and ET.origen_por_texto("Poggio: «Basta»\n\nEn Chivilcoy, el concejal…", "reel")[0].startswith("Videos propios")
         and ET.origen_por_texto("El intendente anunció que la obra de la plaza termina en noviembre y que…", "reel")[0].startswith("Notas locales")
         and ET.origen_por_texto("🌾 EL CAMPO HOY · Sábado", "carrusel")[1] == "agro")
chequear("categoría por palabras: «liga» no toma «obliga», «arma» no toma «farmacia»",
         ET.categoria_por_texto("Racing ganó por la Liga") == "deportes"
         and ET.categoria_por_texto("Se obliga a los comercios") != "deportes"
         and ET.categoria_por_texto("Farmacias de turno de hoy") == "servicios")
et = ET.etiquetar("instagram", "zz", datetime(2026, 10, 24, 23, 30, tzinfo=timezone.utc), "reel", "Choque en la ruta", {})
chequear("hora y día en hora de Argentina", et["hora"] == 20 and et["dia"] == "sábado" and et["finde"])

# ------------------------------------------------------------------ 3. la foto sin permisos de estadísticas
print("Foto sin permisos de estadísticas (como hoy)")
d, hecho, falso = correr_foto({"instagram_basic", "pages_read_engagement"})
chequear("guarda los seguidores del día (cierre del martes)",
         d["cuentas"]["instagram"]["2026-10-27"]["seguidores"] == 9900 and d["cuentas"]["facebook"]["2026-10-27"]["seguidores"] == 20800)
chequear("mide los posteos de 48 h o más y no el de 5 h", "ig:m1" in d["posteos"] and "ig:m2" in d["posteos"]
         and "ig:m3" not in d["posteos"])
chequear("Instagram sin permiso: me gusta y comentarios igual", d["posteos"]["ig:m1"]["m"] == {"likes": 40, "comentarios": 3})
chequear("etiqueta del registro (policiales) y por el texto (carrusel de la mañana)",
         d["posteos"]["ig:m1"]["origen"] == "Policiales" and d["posteos"]["ig:m2"]["origen"].startswith("Notas del diario")
         and d["posteos"]["ig:m2"]["formato"] == "carrusel")
chequear("Facebook sin permiso de posteos: los reels igual, con vistas", d["posteos"]["fb:v1"]["m"].get("vistas") == 3000
         and d["posteos"]["fb:v1"]["m"].get("likes") == 25 and d["posteos"]["fb:v1"]["formato"] == "reel")
chequear("sin permiso no pide estadísticas ni historias",
         not any(r_.endswith("/insights") for r_, _ in falso.pedidos) and not any(r_ == "IG/stories" for r_, _ in falso.pedidos))
chequear("anota qué permisos faltan", d["permisos"]["ig_insights"] is False and len(INF.faltantes(d["permisos"])) == 3)
n = len(d["posteos"])
d, _, _ = correr_foto({"instagram_basic"}, d)
chequear("un posteo se mide una sola vez", len(d["posteos"]) == n)

# ------------------------------------------------------------------ 4. con permisos
print("Foto con permisos")
d, hecho, falso = correr_foto({"instagram_manage_insights", "read_insights"})
mm = d["posteos"]["ig:m1"]["m"]
chequear("Instagram: vistas, alcance, seguidores y tiempo medio", mm.get("vistas") == 1000 and mm.get("alcance") == 700
         and mm.get("seguidores") == 5 and mm.get("tiempo_medio_s") == 6.4 and "reposts" not in mm, mm)
chequear("historias: se miden vivas, con salidas", d["posteos"]["ig:s1"]["formato"] == "historia"
         and d["posteos"]["ig:s1"]["m"].get("salidas") == 6)
dia = d["diario"]["instagram"].get("2026-10-27") or {}
chequear("la cuenta por día: nuevos y bajas", dia.get("nuevos") == 30 and dia.get("bajas") == 12 and dia.get("alcance") == 100, dia)
chequear("horas de conexión pasadas a Argentina", d["audiencia"]["instagram"]["por_hora"] == {"20": 500, "21": 900})
chequear("Facebook: seguidores que trajo el reel", d["posteos"]["fb:v1"]["m"].get("seguidores") == 3)

print("Cuando llegan los permisos")
d, _, _ = correr_foto({"instagram_basic"})
chequear("antes: el posteo quedó medido sin estadísticas", "vistas" not in d["posteos"]["ig:m1"]["m"])
d, _, _ = correr_foto({"instagram_manage_insights", "read_insights", "pages_read_user_content"}, d)
chequear("después: se vuelve a medir con estadísticas, una sola vez", d["posteos"]["ig:m1"]["m"].get("vistas") == 1000
         and d["posteos"]["ig:m1"].get("con_insights") and d["completo"] == {"instagram": True, "facebook": True})
fbs = [k for k, v in d["posteos"].items() if v["red"] == "facebook"]
chequear("el reel de Facebook no se duplica (lista de reels y posteo son el mismo)",
         sorted(fbs) == ["fb:FB_p2", "fb:v1"] and d["posteos"]["fb:v1"]["m"].get("seguidores") == 3, fbs)
chequear("las fotos de Facebook entran con vistas y clics", d["posteos"]["fb:FB_p2"]["m"].get("vistas") == 900
         and d["posteos"]["fb:FB_p2"]["formato"] == "foto" and d["posteos"]["fb:FB_p2"]["m"].get("compartidas") == 2)
d_viejo = {"posteos": {"fb:1456_77": {"red": "facebook", "reel_id": "v5", "m": {"a": 1, "b": 2}},
                       "fb:v5": {"red": "facebook", "reel_id": "v5", "m": {"a": 1}}}}
R._unificar_reels_fb(d_viejo)
chequear("los reels guardados con el id del posteo se pasan al id del video",
         list(d_viejo["posteos"]) == ["fb:v5"] and d_viejo["posteos"]["fb:v5"]["m"] == {"a": 1, "b": 2})

# ------------------------------------------------------------------ 5. guardar
with tempfile.TemporaryDirectory() as tmp:
    ruta = Path(tmp) / "m.json"
    R.guardar(d, ruta)
    texto = ruta.read_text(encoding="utf-8")
    d2 = R.cargar(ruta)
    chequear("se guarda un posteo por línea y se vuelve a leer igual",
             d2["posteos"] == d["posteos"] and sum(1 for l in texto.splitlines() if l.startswith('  "ig:')) == 3)

# ------------------------------------------------------------------ 6. análisis con una historia armada
print("Análisis")
random.seed(3)
hoy = date(2026, 10, 28)                      # miércoles → semana del 19 al 25
ini, fin = A.semana_de(hoy)
chequear("la semana es la última completa ya medida (lunes a domingo)", (ini, fin) == (date(2026, 10, 19), date(2026, 10, 26)))
chequear("el lunes todavía no: es la anterior", A.semana_de(date(2026, 10, 26))[0] == date(2026, 10, 12))

est = {"posteos": {}, "cuentas": {"instagram": {}}, "diario": {"instagram": {}}, "permisos": {"ig_insights": True},
       "audiencia": {}, "semanas": {}, "invalidas": {}}
k = 0
seg = 9000
for n_dia in range(42, 0, -1):
    f = fin - timedelta(days=n_dia)
    semana_actual = f >= ini
    pol = 6 if f.day % 3 == 0 else 2          # días con muchos policiales
    bajas = 5 + 3 * pol + random.randint(0, 2)
    seg += 40 - bajas
    est["cuentas"]["instagram"][f.isoformat()] = {"seguidores": seg}
    est["diario"]["instagram"][f.isoformat()] = {"bajas": bajas, "nuevos": 40}
    piezas = [("Policiales", "policiales", "reel", 21)] * pol + [("Nacionales", "politica", "reel", 22)] * 2 \
        + [("Nacionales", "politica", "carrusel", 22)] * 2 + [("Videos propios", "deportes", "reel", 15)] * (3 if semana_actual else 2)
    for origen, cat, formato, hora in piezas:
        k += 1
        base = {"reel": 1000, "carrusel": 300}[formato] * (2 if origen == "Videos propios" else 1)
        base *= 1.3 if hora == 15 else 1
        est["posteos"][f"ig:{k}"] = {"red": "instagram", "id": str(k), "cuando": f"{f.isoformat()}T{hora:02d}:00:00+00:00",
                                     "fecha": f.isoformat(), "hora": hora, "dia": ET.DIAS[f.weekday()], "formato": formato,
                                     "origen": origen, "categoria": cat, "titulo": f"{origen} {k}", "link": "",
                                     "m": {"vistas": base * random.uniform(0.8, 1.2), "likes": base / 50,
                                           "seguidores": 2 if origen == "Videos propios" else 0.5}}
inf = A.informe(est, hoy)
r = inf["redes"]["instagram"]
v = r["metricas"]["vistas"]
chequear("vistas: sube y la causa es el volumen de Videos propios",
         v["cambio"] > 0 and v["suben"][0]["grupo"] == "Videos propios" and "Videos propios" in v["texto"], v.get("texto"))
fc = {f["grupo"]: f for f in r["formato_categoria"]}
chequear("formato: en política conviene reel (rinde más que el carrusel)",
         fc["Política"]["mejor"] == "reel" and "conviene reel" in fc["Política"]["veredicto"], fc.get("Política"))
coef = {c["grupo"]: c["por_posteo"] for c in r["regresion"]["coef"]}
chequear("bajas: la regresión encuentra que cada policial se asocia a ~3 bajas",
         2.0 <= coef.get("Policiales", 0) <= 4.0 and r["regresion"]["coef"][0]["grupo"] == "Policiales", coef)
chequear("bajas: los días malos muestran más policiales de lo habitual",
         r["dias_malos"]["dias"] and r["dias_malos"]["dias"][0]["excesos"][0]["grupo"] == "Policiales", r["dias_malos"])
rk = r["rankings"]["seguidores"]
chequear("ranking de nuevos seguidores: deportes en reel primero", rk[0][0] == "Deportes · Reel", rk)
hs = {h["grupo"]: h for h in r["horarios"]}
chequear("horarios: un grupo con una sola hora no recibe una recomendación de cambio",
         not hs["Policiales"]["recomendacion"].startswith("probar"), hs["Policiales"])
chequear("seguidores netos de la semana", r["cuenta"]["neto"] is not None and r["cuenta"]["bajas"] > 0)

# ------------------------------------------------------------------ 7. el mail
html_ = INF.html_informe(inf)
chequear("el informe se arma: secciones y sin IA", all(t in html_ for t in ("Qué subió, qué bajó y por qué", "Seguidores: por qué",
                                                                          "¿El formato es el correcto?", "Mejor horario",
                                                                          "no usa inteligencia artificial")))
csv_ = INF.csv_posteos(inf, est)
chequear("la planilla trae los posteos de la semana, lista para Excel",
         csv_.startswith("﻿".encode("utf-8")) and csv_.decode("utf-8").count("\n") == 1 + sum(
             1 for p in est["posteos"].values() if ini.isoformat() <= p["fecha"] < fin.isoformat()))
chequear("asunto con seguidores y vistas", "seguidores" in INF.asunto(inf) and "vistas" in INF.asunto(inf), INF.asunto(inf))

print(f"\n{len(OK)}/{len(OK) + len(MAL)} ok")
sys.exit(1 if MAL else 0)
