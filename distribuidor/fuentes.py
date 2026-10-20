"""De dónde llega el contenido. Cada fuente devuelve eventos con la forma normalizada:

    {
      "id":          "policiales:abc123"   (único y estable: es la clave de idempotencia)
      "origen":      "entrevistas" | "bot" | "policiales" | "web" | "nacionales"
      "tipo":        "video" | "nota"
      "generado":    ISO 8601
      "titulo", "descripcion"
      "texto_x", "texto_tiktok"   (ya NO se usan desde el 17/10/2026: el texto es siempre
                                   título + bajada + link, armado en textos.py)
      "video_url":   URL pública HTTPS del MP4 (videos)
      "video_path":  ruta local en la máquina que lo generó (solo informativa)
      "duracion_seg", "ancho", "alto"  (opcionales, para validar)
      "nota_url":    link a la nota en la web (opcional)
      "youtube_url": link al video en YouTube (opcional)
      "imagen_url":  foto destacada (notas)
      "prioridad":   1-10 (relevancia editorial)
      "urgente":     bool
      "plataformas": ["x", "tiktok"]  (opcional: si no viene, las de la matriz)
      "hashtags":    temas para TikTok (opcional)
      "localidad", "categoria"        (opcionales)
    }

Fuentes:
  1. `state/buzon/*.json` en este repo: un archivo por contenido. Lo escriben el bot
     (directo, en la misma corrida) y el pipeline de entrevistas de la PC de la radio
     (por la API de GitHub). Un archivo por contenido = dos sistemas nunca chocan.
  2. Policiales: `estado/buzon_distribuidor.json` del repo público policiales-bsas.
  3. Notas web: la API del blog de Wix (el CMS de la web), lo publicado en 24 h.
"""
from __future__ import annotations

import json
import re
from datetime import timedelta
from urllib.parse import urlparse

import requests

from utils.logger import get_logger

from . import config, estado

logger = get_logger("distribuidor_fuentes")


def _http_ok(url: str) -> bool:
    return bool(url) and url.lower().startswith("https://")


def normalizar(ev: dict) -> tuple[dict | None, str]:
    """Valida un evento. Devuelve (evento, "") o (None, motivo)."""
    if not isinstance(ev, dict):
        return None, "no es un objeto JSON"
    e = dict(ev)
    e["id"] = str(e.get("id") or "").strip()
    e["origen"] = str(e.get("origen") or "").strip()
    e["tipo"] = str(e.get("tipo") or ("nota" if e.get("origen") == "web" else "video")).strip()
    if not e["id"]:
        return None, "sin id"
    if e["origen"] not in config.ORIGENES:
        return None, f"origen desconocido: {e['origen']!r}"
    if not (e.get("titulo") or e.get("texto_x") or e.get("descripcion")):
        return None, "sin título ni texto"
    if e["tipo"] == "video" and not _http_ok(e.get("video_url", "")):
        return None, "video sin URL pública https"
    # Radio de 100 km de policiales (17/10/2026): si el buzón trae un hecho que no lo cumple
    # (un evento viejo, o un error del otro lado), no sale ni en X ni en TikTok.
    if e["origen"] == "policiales":
        try:
            lejos = float(e.get("distancia_km")) > 100
        except (TypeError, ValueError):
            lejos = False
        if e.get("radio_ok") is False or lejos:
            return None, f"policial fuera del radio de 100 km de Chivilcoy ({e.get('localidad') or 's/d'})"
    if e["tipo"] == "nota" and not _http_ok(e.get("nota_url", "")):
        return None, "nota sin URL"
    try:
        e["prioridad"] = max(1, min(10, int(e.get("prioridad") or 5)))
    except (TypeError, ValueError):
        e["prioridad"] = 5
    e["urgente"] = bool(e.get("urgente"))
    e["generado"] = e.get("generado") or estado.iso(estado.ahora())
    pl = e.get("plataformas") or list(config.PLATAFORMAS)
    e["plataformas"] = [p for p in pl if p in config.PLATAFORMAS]
    return e, ""


# ------------------------------------------------------------------------------ buzón local
def buzon_local() -> list[dict]:
    out = []
    if not estado.BUZON.exists():
        return out
    for f in sorted(estado.BUZON.glob("*.json")):
        try:
            datos = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError) as e:
            logger.warning(f"buzón: {f.name} ilegible ({e}); se saltea")
            continue
        for ev in (datos if isinstance(datos, list) else [datos]):
            if isinstance(ev, dict):
                ev.setdefault("_archivo", f.name)
                out.append(ev)
    return out


def limpiar_buzon(est: dict, dias: int = 4) -> int:
    """Borra archivos del buzón ya encolados y viejos (el commit del distribuidor los saca)."""
    if not estado.BUZON.exists():
        return 0
    limite = estado.ahora() - timedelta(days=dias)
    n = 0
    for f in estado.BUZON.glob("*.json"):
        try:
            datos = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        evs = datos if isinstance(datos, list) else [datos]
        ids = [str(ev.get("id")) for ev in evs if isinstance(ev, dict)]
        viejo = all((estado.de_iso(ev.get("generado")) or estado.ahora()) < limite
                    for ev in evs if isinstance(ev, dict))
        if viejo and all(i in est["vistos"] for i in ids):
            f.unlink()
            n += 1
    return n


# ------------------------------------------------------------------------------ policiales
def buzon_policiales() -> list[dict]:
    try:
        r = requests.get(config.BUZON_POLICIALES_URL, timeout=30)
        if r.status_code == 404:
            return []
        r.raise_for_status()
        datos = r.json()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"policiales: no pude leer el buzón ({e}); se reintenta en la próxima")
        return []
    evs = datos.get("eventos") if isinstance(datos, dict) else datos
    return [ev for ev in (evs or []) if isinstance(ev, dict)]


# ------------------------------------------------------------------------------ notas web
def _foto(post: dict) -> str:
    media = post.get("media") or {}
    url = (((media.get("wixMedia") or {}).get("image") or {}).get("url")) or ""
    if url.startswith("http"):
        return url
    m = re.match(r"^wix:image://v1/([^/#]+)", url)
    return f"https://static.wixstatic.com/media/{m.group(1)}/v1/fill/w_1200,h_750,q_85/img.jpg" if m else ""


def notas_web() -> list[dict]:
    """Notas publicadas en las últimas 24 h, desde la API del blog de Wix (máx. 100)."""
    from platforms import wix
    from utils.branding import link_nota
    desde = estado.ahora() - timedelta(hours=config.WEB_VENTANA_HORAS)
    filtro = {"firstPublishedDate": {"$gte": estado.iso(desde)}}
    if not config.WEB_INCLUIR_REGION:
        filtro = {"$and": [filtro, {"$not": {"categoryIds": {"$hasSome": [config.WEB_CAT_REGION]}}}]}
    body = {"query": {"filter": filtro,
                      "sort": [{"fieldName": "firstPublishedDate", "order": "DESC"}],
                      "paging": {"limit": 100}},   # nunca más de 100 a Wix
            "fieldsets": ["URL"]}
    try:
        r = requests.post(wix.POSTS_QUERY_URL, headers=wix._headers(), json=body, timeout=40)
        r.raise_for_status()
        posts = r.json().get("posts", [])
    except Exception as e:  # noqa: BLE001
        logger.warning(f"web: no pude consultar Wix ({e}); se reintenta en la próxima")
        return []
    out = []
    excluir_titulo = re.compile(config.WEB_TITULOS_EXCLUIDOS, re.I)
    for p in posts:
        if set(p.get("categoryIds") or []) & config.WEB_CATS_EXCLUIDAS:
            continue                                   # agro: tiene su propia cuenta
        if excluir_titulo.search(p.get("title") or ""):
            continue                                   # sepelios, farmacias, clima, tapa, Archivo
        u = p.get("url") or {}
        url_wix = (u.get("base", "") + u.get("path", "")) or ""
        link = link_nota(url_wix) if url_wix else ""
        if not link:
            continue
        cats = p.get("categoryIds") or []
        partes = [t.strip() for t in (p.get("title") or "").split(" — ")]
        volanta = partes.pop(0) if len(partes) > 1 and len(partes[0]) <= 25 else ""
        titulo = partes[0] if partes else (p.get("title") or "")
        bajada = " — ".join(partes[1:]) or (p.get("excerpt") or "")
        nacional = bool({config.WEB_CAT_NACIONALES_AUTO, config.WEB_CAT_NACIONALES} & set(cats))
        if nacional and config.X_SIN_NOTAS_NACIONALES:
            continue                                   # las nacionales van a X como reel (buzón)
        solo_finde = config.WEB_CAT_NACIONALES_AUTO in cats and bool(set(cats) & config.WEB_CATS_SOLO_FINDE)
        texto = f"{titulo} {bajada}".lower()
        out.append({
            "id": f"web:{p.get('id')}",
            "origen": "web", "tipo": "nota",
            "generado": p.get("firstPublishedDate") or "",
            "titulo": titulo, "descripcion": bajada, "volanta": volanta,
            "nota_url": link, "url_canonica": url_wix,
            "imagen_url": _foto(p),
            "categoria": "nacionales" if nacional else "",
            "solo_finde": solo_finde,
            # Lo nacional automático (Infobae reescrito) va después de lo propio.
            "prioridad": 3 if nacional else 6,
            "urgente": bool(re.search(r"\b(urgente|último momento|ultimo momento)\b", texto)),
            "plataformas": ["x"],
        })
    return out


def nota_verificada(url: str) -> tuple[bool, str]:
    """El link abre (200) y termina en NUESTRO sitio (sigue la redirección de /n/)."""
    try:
        r = requests.get(url, timeout=30, allow_redirects=True,
                         headers={"User-Agent": "diario-distribuidor/1.0"})
    except requests.RequestException as e:
        return False, f"no abre: {e}"
    host = (urlparse(r.url).hostname or "").lower()
    if host not in (config.SITIO_PUNY, "diarioweb.vercel.app") and not host.endswith(
            "xn--diariolacampaa-2nb.com.ar"):
        return False, f"redirige a otro sitio ({host})"
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}"
    return True, ""


def video_verificado(ev: dict) -> tuple[bool, str, dict]:
    """El MP4 se puede bajar sin login y cumple lo mínimo de X y TikTok."""
    url = ev.get("video_url", "")
    try:
        r = requests.head(url, timeout=30, allow_redirects=True)
        if r.status_code in (403, 405):        # algunos hosts no aceptan HEAD
            r = requests.get(url, timeout=30, stream=True, headers={"Range": "bytes=0-1023"})
            r.close()
    except requests.RequestException as e:
        return False, f"el video no se puede bajar: {e}", {}
    if r.status_code not in (200, 206):
        return False, f"el video da HTTP {r.status_code}", {}
    tipo = (r.headers.get("Content-Type") or "").lower()
    if tipo and not (tipo.startswith("video/") or "octet-stream" in tipo):
        return False, f"no es un video ({tipo})", {}
    nbytes = int(r.headers.get("Content-Length") or 0)
    mb = nbytes / 1_048_576
    info = {"mb": round(mb, 1), "bytes": nbytes}
    if mb and mb > config.X_VIDEO_MAX_MB:
        return False, f"pesa {mb:.0f} MB", info
    return True, "", info


def apto_para(ev: dict, plataforma: str) -> tuple[bool, str]:
    """Reglas por red que dependen del contenido (duración, tipo)."""
    if ev["tipo"] == "nota" and plataforma == "tiktok":
        return False, "las notas web no van a TikTok"
    dur = ev.get("duracion_seg")
    try:
        dur = float(dur) if dur not in (None, "") else None
    except (TypeError, ValueError):
        dur = None
    if ev["tipo"] == "video" and dur is not None:
        if plataforma == "x" and dur > config.X_VIDEO_MAX_SEG:
            return False, f"dura {dur:.0f} s y X admite {config.X_VIDEO_MAX_SEG} s sin suscripción"
        if plataforma == "tiktok" and not (config.TIKTOK_VIDEO_MIN_SEG <= dur <= config.TIKTOK_VIDEO_MAX_SEG):
            return False, f"dura {dur:.0f} s (TikTok: {config.TIKTOK_VIDEO_MIN_SEG}-{config.TIKTOK_VIDEO_MAX_SEG} s)"
    return True, ""


# ------------------------------------------------------------------------------ YouTube
def _token_youtube() -> str:
    """Token de acceso del canal Radio del Centro (el mismo YT_TOKEN_JSON del publicador)."""
    import os
    crudo = os.environ.get("YT_TOKEN_JSON") or ""
    if not crudo:
        ruta = estado.RAIZ / ".yt_token.json"
        crudo = ruta.read_text(encoding="utf-8") if ruta.exists() else ""
    if not crudo:
        return ""
    t = json.loads(crudo)
    r = requests.post(t.get("token_uri") or "https://oauth2.googleapis.com/token", timeout=30, data={
        "client_id": t["client_id"], "client_secret": t["client_secret"],
        "refresh_token": t["refresh_token"], "grant_type": "refresh_token"})
    r.raise_for_status()
    return r.json()["access_token"]


def privacidad_youtube(ids: list[str]) -> dict:
    """{video_id: "public" | "unlisted" | "private"} (1 unidad de cuota por 50 videos).
    Si no se puede consultar, devuelve {} y el contenido sigue esperando."""
    ids = [i for i in dict.fromkeys(ids) if i]
    if not ids:
        return {}
    try:
        tok = _token_youtube()
        if not tok:
            logger.warning("YouTube: sin YT_TOKEN_JSON; las entrevistas esperan")
            return {}
        out = {}
        for i in range(0, len(ids), 50):
            r = requests.get("https://www.googleapis.com/youtube/v3/videos", timeout=30,
                             headers={"Authorization": f"Bearer {tok}"},
                             params={"part": "status", "id": ",".join(ids[i:i + 50])})
            r.raise_for_status()
            for v in r.json().get("items", []):
                out[v["id"]] = (v.get("status") or {}).get("privacyStatus", "")
        return out
    except Exception as e:  # noqa: BLE001
        logger.warning(f"YouTube: no pude consultar la privacidad ({e}); se reintenta")
        return {}


def limpiar_release(est: dict, dias: int = 5) -> int:
    """Borra del Release `buzon-distribuidor` (MP4 de entrevistas) lo de más de `dias` que
    ninguna publicación activa usa. Una vez por día. Necesita GITHUB_TOKEN (lo da Actions)."""
    import os
    tok = os.environ.get("GITHUB_TOKEN") or ""
    hoy = estado.iso(estado.ahora())[:10]
    if not tok or est.get("ultima_limpieza_release") == hoy:
        return 0
    repo = os.environ.get("GITHUB_REPOSITORY") or "dlcchivilcoy/social_publisher"
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
    en_uso = {p.get("video_url") for p in est["publicaciones"].values() if p["estado"] in estado.ACTIVOS}
    limite = estado.ahora() - timedelta(days=dias)
    n = 0
    try:
        r = requests.get(f"https://api.github.com/repos/{repo}/releases/tags/buzon-distribuidor",
                         headers=h, timeout=30)
        if r.status_code == 404:
            est["ultima_limpieza_release"] = hoy
            return 0
        r.raise_for_status()
        for a in r.json().get("assets", []):
            if a.get("browser_download_url") in en_uso:
                continue
            if (estado.de_iso(a.get("created_at")) or estado.ahora()) < limite:
                requests.delete(f"https://api.github.com/repos/{repo}/releases/assets/{a['id']}",
                                headers=h, timeout=30)
                n += 1
        est["ultima_limpieza_release"] = hoy
    except Exception as e:  # noqa: BLE001
        logger.warning(f"limpieza del Release: {e}")
    if n:
        logger.info(f"Release buzon-distribuidor: {n} video(s) viejos borrados")
    return n


def todas() -> list[dict]:
    evs = []
    for nombre, fn in (("buzón", buzon_local), ("policiales", buzon_policiales), ("web", notas_web)):
        try:
            lote = fn()
            logger.info(f"fuente {nombre}: {len(lote)} evento(s)")
            evs.extend(lote)
        except Exception as e:  # noqa: BLE001 — una fuente caída no frena a las otras
            logger.warning(f"fuente {nombre} falló: {e}")
    return evs
