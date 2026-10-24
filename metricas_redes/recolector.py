"""La foto diaria: seguidores, números del día de cada cuenta y cada posteo medido a la misma edad.

Corre una vez por día (00:15 de Argentina) y antes de cada informe. Todo queda en
`state/metricas_redes.json`, un posteo por línea para que el historial de git no engorde.

Edad fija: cada posteo se mide UNA vez, la primera pasada en que tiene EDAD_HORAS (48) o más. Así se
compara un posteo con otro de la misma edad, no uno de hace una hora con uno de hace tres días. Las
historias duran 24 h: se miden en la pasada que las encuentra vivas (la de las 00:15 las ve casi enteras).
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import etiquetas as ET
from .meta import PERMISO_FB, PERMISO_FB_POSTEOS, PERMISO_IG, TZ_AR, ErrorMeta, Meta, SinPermiso, _fecha

RAIZ = Path(__file__).resolve().parent.parent
ARCHIVO = RAIZ / "state" / "metricas_redes.json"
EDAD_HORAS = int(os.getenv("METRICAS_EDAD_HORAS", "48"))
DIAS_LISTA = 10           # hasta dónde se mira para atrás buscando posteos sin medir
DIAS_PRIMERA = 35         # la primera vez: 5 semanas, para que el primer informe ya tenga con qué comparar
DIAS_GUARDA = 120
DIAS_CUENTA = 28          # días de números de la cuenta que se completan si faltan

# ---------------------------------------------------------------- métricas pedidas a Meta → nombre propio
IG_POSTEO = {
    "REELS": ["views", "reach", "likes", "comments", "shares", "saved", "total_interactions", "reposts",
              "follows", "profile_visits", "ig_reels_avg_watch_time"],
    "FEED": ["views", "reach", "likes", "comments", "shares", "saved", "total_interactions", "reposts",
             "follows", "profile_visits"],
    "STORY": ["views", "reach", "replies", "shares", "follows", "profile_visits", "total_interactions"],
}
IG_NOMBRES = {"views": "vistas", "reach": "alcance", "likes": "likes", "comments": "comentarios",
              "shares": "compartidas", "saved": "guardados", "total_interactions": "interacciones",
              "reposts": "reposts", "follows": "seguidores", "profile_visits": "visitas_perfil",
              "replies": "respuestas"}
IG_CUENTA = ["reach", "views", "accounts_engaged", "total_interactions", "likes", "comments", "shares", "saves",
             "replies", "reposts", "profile_links_taps"]
IG_CUENTA_NOMBRES = {"reach": "alcance", "views": "vistas", "accounts_engaged": "cuentas_que_interactuaron",
                     "total_interactions": "interacciones", "likes": "likes", "comments": "comentarios",
                     "shares": "compartidas", "saves": "guardados", "replies": "respuestas", "reposts": "reposts",
                     "profile_links_taps": "toques_en_links"}
FB_POSTEO = ["post_media_view", "post_total_media_view_unique", "post_impressions_unique", "post_clicks",
             "post_negative_feedback_by_type_unique", "post_video_views"]
FB_REEL = ["fb_reels_total_plays", "blue_reels_play_count", "post_impressions_unique",
           "post_video_avg_time_watched", "post_video_followers"]
FB_PAGINA = ["page_daily_follows_unique", "page_daily_unfollows_unique", "page_media_view",
             "page_total_media_view_unique", "page_post_engagements", "page_views_total", "page_video_views",
             "page_impressions_unique"]
FB_PAGINA_NOMBRES = {"page_daily_follows_unique": "nuevos", "page_daily_unfollows_unique": "bajas",
                     "page_media_view": "vistas", "page_total_media_view_unique": "alcance",
                     "page_impressions_unique": "alcance_viejo", "page_post_engagements": "interacciones",
                     "page_views_total": "visitas_perfil", "page_video_views": "vistas_video"}


# ---------------------------------------------------------------- estado
def cargar(ruta: Path | None = None) -> dict:
    try:
        d = json.loads((ruta or ARCHIVO).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    for k in ("posteos", "cuentas", "diario", "invalidas", "audiencia", "semanas", "permisos"):
        d.setdefault(k, {})
    return d


def guardar(d: dict, ruta: Path | None = None) -> None:
    """JSON con un posteo por línea: el diff de cada día son solo las líneas nuevas."""
    ruta = ruta or ARCHIVO
    limite = (datetime.now(timezone.utc) - timedelta(days=DIAS_GUARDA)).isoformat()
    d["posteos"] = {k: v for k, v in d["posteos"].items() if (v.get("cuando") or "") >= limite}
    for red in d["cuentas"].values():
        for k in [k for k in red if k < limite[:10]]:
            red.pop(k)
    for red in d["diario"].values():
        for k in [k for k in red if k < limite[:10]]:
            red.pop(k)
    partes = []
    for k, v in d.items():
        if k == "posteos":
            filas = ",\n".join(f"  {json.dumps(pk, ensure_ascii=False)}: {json.dumps(pv, ensure_ascii=False, sort_keys=True)}"
                               for pk, pv in sorted(v.items(), key=lambda kv: kv[1].get("cuando") or ""))
            partes.append(f'"posteos": {{\n{filas}\n }}')
        else:
            partes.append(f"{json.dumps(k)}: {json.dumps(v, ensure_ascii=False, sort_keys=True)}")
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text("{\n " + ",\n ".join(partes) + "\n}\n", encoding="utf-8")


# ---------------------------------------------------------------- la foto
def foto(meta: Meta, d: dict, ig_id: str, fb_id: str, idx: dict, ahora: datetime | None = None, log=print) -> dict:
    """Completa `d` (no guarda). Devuelve un resumen de lo hecho para el log."""
    ahora = ahora or datetime.now(timezone.utc)
    viejo = (ahora - timedelta(days=DIAS_LISTA)).isoformat()
    dias = DIAS_LISTA if any((p.get("cuando") or "") < viejo for p in d["posteos"].values()) else DIAS_PRIMERA
    hecho = {"posteos_medidos": 0, "historias": 0, "fallas": []}
    try:
        permisos = meta.permisos()
    except ErrorMeta as e:
        hecho["fallas"].append(f"no pude leer los permisos del token: {e}")
        permisos = set()
    d["permisos"] = {"ig_insights": PERMISO_IG in permisos, "fb_insights": PERMISO_FB in permisos,
                     "fb_posteos": PERMISO_FB_POSTEOS in permisos, "revisado": ahora.isoformat(timespec="seconds")}
    p = d["permisos"]
    log(f"Permisos: estadísticas de Instagram {'sí' if p['ig_insights'] else 'NO'} · de Facebook "
        f"{'sí' if p['fb_insights'] else 'NO'} · leer posteos de Facebook {'sí' if p['fb_posteos'] else 'NO'}")

    _seguidores(meta, d, ig_id, fb_id, ahora, hecho)
    if p["ig_insights"]:
        _cuenta_ig(meta, d, ig_id, ahora, hecho, log)
    if p["fb_insights"]:
        _cuenta_fb(meta, d, fb_id, ahora, hecho)
    _posteos_ig(meta, d, ig_id, idx, ahora, hecho, p["ig_insights"], dias)
    _posteos_fb(meta, d, fb_id, idx, ahora, hecho, p, dias)
    d["ultima_foto"] = ahora.isoformat(timespec="seconds")
    return hecho


def _dia_corte(ahora: datetime) -> str:
    """La foto de las 00:15 es el cierre del día anterior."""
    return (ahora.astimezone(TZ_AR) - timedelta(hours=1)).date().isoformat()


def _seguidores(meta, d, ig_id, fb_id, ahora, hecho):
    dia = _dia_corte(ahora)
    for red, obj, campos in (("instagram", ig_id, "followers_count,media_count"),
                             ("facebook", fb_id, "followers_count,fan_count")):
        try:
            r = meta.get(obj, fields=campos)
            d["cuentas"].setdefault(red, {})[dia] = {"seguidores": r.get("followers_count"),
                                                     "hora": ahora.isoformat(timespec="minutes")}
        except ErrorMeta as e:
            hecho["fallas"].append(f"seguidores de {red}: {e}")


def _rango_dia(dia: str):
    ini = datetime.fromisoformat(dia).replace(tzinfo=TZ_AR)
    return int(ini.timestamp()), int((ini + timedelta(days=1)).timestamp())


def _cuenta_ig(meta, d, ig_id, ahora, hecho, log):
    diario = d["diario"].setdefault("instagram", {})
    hoy = ahora.astimezone(TZ_AR).date()
    for n in range(DIAS_CUENTA, 0, -1):
        dia = (hoy - timedelta(days=n)).isoformat()
        if dia in diario and n > 3:            # los últimos 3 días se releen: Meta los termina de contar tarde
            continue
        since, until = _rango_dia(dia)
        try:
            r = meta.insights(ig_id, IG_CUENTA, "ig_cuenta", period="day", metric_type="total_value",
                              since=since, until=until)
            fila = {IG_CUENTA_NOMBRES[k]: v for k, v in r.items() if k in IG_CUENTA_NOMBRES}
            fu = meta.insights(ig_id, ["follows_and_unfollows"], "ig_cuenta_follows", period="day",
                               metric_type="total_value", breakdown="follow_type", since=since, until=until)
            desglose = fu.get("follows_and_unfollows")
            if isinstance(desglose, dict):
                fila["nuevos"] = desglose.get("FOLLOWER")
                fila["bajas"] = desglose.get("NON_FOLLOWER")
            if fila:
                diario[dia] = {**diario.get(dia, {}), **fila}
        except SinPermiso as e:
            hecho["fallas"].append(f"Instagram (cuenta): sin permiso: {e}")
            return
        except ErrorMeta as e:
            hecho["fallas"].append(f"Instagram (cuenta, {dia}): {e}")
    # Nuevos seguidores por día (serie de 30 días) por si el desglose no vino.
    try:
        s = meta.insights_serie(ig_id, ["follower_count"], "ig_follower_count", period="day",
                                since=int((ahora - timedelta(days=29)).timestamp()), until=int(ahora.timestamp()))
        for dia, v in (s.get("follower_count") or {}).items():
            diario.setdefault(dia, {}).setdefault("nuevos", v)
    except (ErrorMeta, SinPermiso) as e:
        hecho["fallas"].append(f"Instagram (nuevos por día): {e}")
    # Horas en que la audiencia está conectada: una vez por semana alcanza.
    aud = d["audiencia"].get("instagram") or {}
    if not aud or (aud.get("medido") or "") < (ahora - timedelta(days=6)).isoformat():
        try:
            r = meta.get(f"{ig_id}/insights", metric="online_followers", period="lifetime",
                         since=int((ahora - timedelta(days=29)).timestamp()), until=int(ahora.timestamp()))
            sumas, dias = {}, 0
            for it in r.get("data") or []:
                for v in it.get("values") or []:
                    if isinstance(v.get("value"), dict) and v["value"]:
                        dias += 1
                        for h, n in v["value"].items():
                            sumas[int(h)] = sumas.get(int(h), 0) + (n or 0)
            if dias:
                # Meta da la hora del Pacífico (UTC-7/-8): se pasa a la de Argentina (+4/+5 h). Se toma +4.
                por_hora = {str((h + 4) % 24): round(n / dias) for h, n in sumas.items()}
                d["audiencia"]["instagram"] = {"por_hora": por_hora, "medido": ahora.isoformat(timespec="seconds")}
        except ErrorMeta as e:
            hecho["fallas"].append(f"Instagram (horas de la audiencia): {e}")


def _cuenta_fb(meta, d, fb_id, ahora, hecho):
    diario = d["diario"].setdefault("facebook", {})
    try:
        s = meta.insights_serie(fb_id, FB_PAGINA, "fb_pagina", period="day",
                                since=int((ahora - timedelta(days=DIAS_CUENTA)).timestamp()),
                                until=int(ahora.timestamp()))
    except (ErrorMeta, SinPermiso) as e:
        hecho["fallas"].append(f"Facebook (página): {e}")
        return
    for metrica, por_dia in s.items():
        nombre = FB_PAGINA_NOMBRES.get(metrica)
        if not nombre:
            continue
        for dia, v in por_dia.items():
            if isinstance(v, (int, float)):
                diario.setdefault(dia, {})[nombre] = v
    for fila in diario.values():
        if "alcance" not in fila and "alcance_viejo" in fila:
            fila["alcance"] = fila["alcance_viejo"]


# ---------------------------------------------------------------- posteos
def _registro(red, mid, cuando, formato, texto, link, idx, ahora) -> dict:
    et = ET.etiquetar(red, mid, cuando, formato, texto, idx)
    return {"red": red, "id": str(mid), "cuando": cuando.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "formato": formato, "origen": et["origen"], "categoria": et.get("categoria") or "general",
            "titulo": et["titulo"], "hora": et["hora"], "dia": et["dia"], "fecha": et["fecha"],
            "km": et.get("km"), "link": link or "", "medido": ahora.isoformat(timespec="seconds"),
            "edad_h": round((ahora - cuando).total_seconds() / 3600, 1), "m": {}}


def _posteos_ig(meta, d, ig_id, idx, ahora, hecho, con_insights, dias=DIAS_LISTA):
    campos = "id,timestamp,media_type,media_product_type,caption,permalink,like_count,comments_count"
    desde = ahora - timedelta(days=dias)
    try:
        lista = list(meta.paginas(f"{ig_id}/media", desde, "timestamp", fields=campos))
    except ErrorMeta as e:
        hecho["fallas"].append(f"Instagram (lista de posteos): {e}")
        lista = []
    try:
        historias = list(meta.paginas(f"{ig_id}/stories", desde, "timestamp", fields=campos)) if con_insights else []
    except ErrorMeta as e:
        hecho["fallas"].append(f"Instagram (historias): {e}")
        historias = []
    for x in lista + historias:
        cuando = _fecha(x.get("timestamp"))
        if not cuando:
            continue
        clave = f"ig:{x['id']}"
        es_historia = x.get("media_product_type") == "STORY"
        edad = (ahora - cuando).total_seconds() / 3600
        if not es_historia and (clave in d["posteos"] or edad < EDAD_HORAS):
            continue
        formato = ET.formato_ig(x.get("media_type"), x.get("media_product_type"))
        reg = _registro("instagram", x["id"], cuando, formato, x.get("caption") or "", x.get("permalink"), idx, ahora)
        reg["m"] = {"likes": x.get("like_count"), "comentarios": x.get("comments_count")}
        if con_insights:
            tipo = "STORY" if es_historia else ("REELS" if x.get("media_product_type") == "REELS" else "FEED")
            try:
                r = meta.insights(x["id"], IG_POSTEO[tipo], f"ig_{tipo}")
                for k, v in r.items():
                    if k == "ig_reels_avg_watch_time" and isinstance(v, (int, float)):
                        reg["m"]["tiempo_medio_s"] = round(v / 1000, 1)
                    elif k in IG_NOMBRES and isinstance(v, (int, float)):
                        reg["m"][IG_NOMBRES[k]] = v
                if es_historia:
                    nav = meta.insights(x["id"], ["navigation"], "ig_STORY_nav",
                                        breakdown="story_navigation_action_type").get("navigation")
                    if isinstance(nav, dict):
                        reg["m"]["salidas"] = nav.get("SWIPE_FORWARD", 0) + nav.get("TAP_EXIT", 0)
                        reg["m"]["adelantos"] = nav.get("TAP_FORWARD")
            except SinPermiso as e:
                hecho["fallas"].append(f"Instagram (estadísticas del posteo): sin permiso: {e}")
                con_insights = False
            except ErrorMeta as e:
                hecho["fallas"].append(f"Instagram {x['id']}: {e}")
        d["posteos"][clave] = reg
        hecho["historias" if es_historia else "posteos_medidos"] += 1


def _posteos_fb(meta, d, fb_id, idx, ahora, hecho, permisos, dias=DIAS_LISTA):
    desde = ahora - timedelta(days=dias)
    reels = {}
    try:
        for v in meta.paginas(f"{fb_id}/video_reels", desde, "created_time",
                              fields="id,created_time,description,views,length,permalink_url"):
            reels[str(v["id"])] = v
    except ErrorMeta as e:
        hecho["fallas"].append(f"Facebook (reels): {e}")
    posteos = []
    if permisos.get("fb_posteos"):
        try:
            posteos = list(meta.paginas(
                f"{fb_id}/published_posts", desde, "created_time",
                fields="id,created_time,message,status_type,permalink_url,shares,attachments{media_type,type,target{id}},"
                       "reactions.summary(total_count).limit(0),comments.summary(total_count).limit(0)"))
        except ErrorMeta as e:
            hecho["fallas"].append(f"Facebook (posteos): {e}")
    vistos = set()
    for x in posteos:
        cuando = _fecha(x.get("created_time"))
        clave = f"fb:{x['id']}"
        if not cuando or clave in d["posteos"] or (ahora - cuando).total_seconds() / 3600 < EDAD_HORAS:
            continue
        adj = (((x.get("attachments") or {}).get("data")) or [{}])[0]
        destino = str((adj.get("target") or {}).get("id") or "")
        formato = "reel" if destino in reels else ET.formato_fb(x.get("status_type"), adj.get("media_type"), adj.get("type"))
        reg = _registro("facebook", x["id"], cuando, formato, x.get("message") or "", x.get("permalink_url"),
                        {**idx, **({x["id"]: idx[destino]} if destino in idx else {})}, ahora)
        reg["m"] = {"likes": _total(x.get("reactions")), "comentarios": _total(x.get("comments")),
                    "compartidas": (x.get("shares") or {}).get("count", 0)}
        if destino in reels:
            vistos.add(destino)
            _medir_reel_fb(meta, reels[destino], reg, permisos, hecho)
        elif permisos.get("fb_insights"):
            _medir_posteo_fb(meta, x["id"], reg, hecho)
        d["posteos"][clave] = reg
        hecho["posteos_medidos"] += 1
    # Sin permiso para leer posteos: los reels igual se leen por su lado.
    for vid, v in reels.items():
        cuando = _fecha(v.get("created_time"))
        clave = f"fb:{vid}"
        if vid in vistos or not cuando or clave in d["posteos"] or (ahora - cuando).total_seconds() / 3600 < EDAD_HORAS:
            continue
        if any(p.get("red") == "facebook" and p.get("reel_id") == vid for p in d["posteos"].values()):
            continue
        reg = _registro("facebook", vid, cuando, "reel", v.get("description") or "", v.get("permalink_url"), idx, ahora)
        try:
            r = meta.get(vid, fields="likes.summary(true).limit(0),comments.summary(true).limit(0)")
            reg["m"] = {"likes": _total(r.get("likes")), "comentarios": _total(r.get("comments"))}
        except ErrorMeta as e:
            hecho["fallas"].append(f"Facebook reel {vid}: {e}")
        _medir_reel_fb(meta, v, reg, permisos, hecho)
        d["posteos"][clave] = reg
        hecho["posteos_medidos"] += 1


def _total(x) -> int | None:
    return ((x or {}).get("summary") or {}).get("total_count") if isinstance(x, dict) else None


def _medir_reel_fb(meta, v, reg, permisos, hecho):
    reg["reel_id"] = str(v["id"])
    if isinstance(v.get("views"), (int, float)):
        reg["m"]["vistas"] = v["views"]
    if isinstance(v.get("length"), (int, float)):
        reg["duracion_s"] = round(v["length"], 1)
    if not permisos.get("fb_insights"):
        return
    try:
        r = meta.insights(v["id"], FB_REEL, "fb_reel", ruta="video_insights")
    except (ErrorMeta, SinPermiso) as e:
        hecho["fallas"].append(f"Facebook reel {v['id']} (estadísticas): {e}")
        return
    vistas = r.get("fb_reels_total_plays") or r.get("blue_reels_play_count")
    if isinstance(vistas, (int, float)) and not reg["m"].get("vistas"):
        reg["m"]["vistas"] = vistas
    if isinstance(r.get("post_impressions_unique"), (int, float)):
        reg["m"]["alcance"] = r["post_impressions_unique"]
    if isinstance(r.get("post_video_avg_time_watched"), (int, float)):
        reg["m"]["tiempo_medio_s"] = round(r["post_video_avg_time_watched"] / 1000, 1)
    if isinstance(r.get("post_video_followers"), (int, float)):
        reg["m"]["seguidores"] = r["post_video_followers"]


def _medir_posteo_fb(meta, pid, reg, hecho):
    try:
        r = meta.insights(pid, FB_POSTEO, "fb_posteo")
    except (ErrorMeta, SinPermiso) as e:
        hecho["fallas"].append(f"Facebook {pid} (estadísticas): {e}")
        return
    vistas = r.get("post_media_view") or r.get("post_video_views")
    if isinstance(vistas, (int, float)):
        reg["m"]["vistas"] = vistas
    alcance = r.get("post_total_media_view_unique") or r.get("post_impressions_unique")
    if isinstance(alcance, (int, float)):
        reg["m"]["alcance"] = alcance
    if isinstance(r.get("post_clicks"), (int, float)):
        reg["m"]["clics"] = r["post_clicks"]
    neg = r.get("post_negative_feedback_by_type_unique")
    if isinstance(neg, dict):
        reg["m"]["ocultar"] = (neg.get("hide_clicks") or 0) + (neg.get("hide_all_clicks") or 0)
        reg["m"]["dejar_de_seguir"] = neg.get("unlike_page_clicks") or 0
        reg["m"]["denuncias"] = neg.get("report_spam_clicks") or 0
