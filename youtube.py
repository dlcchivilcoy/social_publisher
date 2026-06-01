"""Lectura del canal de YouTube (Radio del Centro) para las Historias.

Port mínimo de whatsapp_diario/youtube.js a Python:
  - videos_de_hoy(channel_id): notas subidas HOY (RSS, sin API key).
  - vivo_actual(handle): detecta si el canal está EN VIVO ahora.
  - descargar_miniatura(video_id): baja la miniatura (la "captura") del video.
  - normalizar(s): para el filtro de exclusión por título.

Ledger propio (youtube-historias.json), separado del de WhatsApp, para no pisarse.
"""
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import requests

from utils.logger import get_logger

logger = get_logger("youtube")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

LEDGER = Path(__file__).parent / "youtube-historias.json"


def normalizar(s: str) -> str:
    s = (s or "").lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n")):
        s = s.replace(a, b)
    return s


def leer_ledger() -> set[str]:
    try:
        if LEDGER.exists():
            return set(json.loads(LEDGER.read_text(encoding="utf-8")))
    except Exception:
        pass
    return set()


def guardar_ledger(ids: set[str]) -> None:
    # conserva solo los últimos 200 para que no crezca infinito
    arr = list(ids)[-200:]
    LEDGER.write_text(json.dumps(arr, ensure_ascii=False, indent=2), encoding="utf-8")


def _es_mismo_dia_local(fecha_iso: str) -> bool:
    try:
        d = datetime.fromisoformat(fecha_iso.replace("Z", "+00:00")).astimezone()
    except Exception:
        return False
    hoy = datetime.now().astimezone()
    return (d.year, d.month, d.day) == (hoy.year, hoy.month, hoy.day)


def videos_de_hoy(channel_id: str) -> list[dict]:
    """Devuelve [{id, titulo, url, published}] de los videos subidos HOY."""
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    res = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    res.raise_for_status()
    xml = res.text

    videos = []
    for e in xml.split("<entry>")[1:]:
        m_id = re.search(r"<yt:videoId>([^<]+)</yt:videoId>", e)
        m_tit = re.search(r"<title>([^<]*)</title>", e)
        m_pub = re.search(r"<published>([^<]+)</published>", e)
        if not (m_id and m_pub):
            continue
        vid = m_id.group(1)
        titulo = _decode_entities(m_tit.group(1) if m_tit else "")
        published = m_pub.group(1)
        if _es_mismo_dia_local(published):
            videos.append({"id": vid, "titulo": titulo, "published": published,
                           "url": f"https://youtu.be/{vid}"})
    return videos


def vivo_actual(handle: str) -> dict | None:
    """Detecta si el canal está EN VIVO ahora. Devuelve {id, titulo, url} o None."""
    url = f"https://www.youtube.com/@{handle}/live"
    res = requests.get(url, headers={"User-Agent": UA}, timeout=30, allow_redirects=True)
    html = res.text
    m_id = re.search(r'rel="canonical" href="https://www\.youtube\.com/watch\?v=([0-9A-Za-z_-]{11})"', html)
    es_vivo = ('"isLiveBroadcast":true' in html or "hlsManifestUrl" in html
               or '"isLiveNow":true' in html)
    if m_id and es_vivo:
        vid = m_id.group(1)
        m_tit = re.search(r'<meta name="title" content="([^"]*)"', html)
        titulo = _decode_entities(m_tit.group(1)) if m_tit else "La Mañana del Centro"
        return {"id": vid, "titulo": titulo, "url": f"https://youtu.be/{vid}"}
    return None


def descargar_miniatura(video_id: str) -> Path:
    """Baja la miniatura del video (maxres, con fallback a hq). Devuelve la ruta a un JPG temporal."""
    for calidad in ("maxresdefault", "hqdefault"):
        url = f"https://img.youtube.com/vi/{video_id}/{calidad}.jpg"
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
            # YouTube devuelve una imagen gris de 120x90 cuando no existe la maxres
            if r.status_code == 200 and len(r.content) > 2000:
                tmp = Path(tempfile.gettempdir()) / f"yt_{video_id}_{calidad}.jpg"
                tmp.write_bytes(r.content)
                return tmp
        except Exception as e:
            logger.warning(f"Fallo bajando {calidad} de {video_id}: {e}")
    raise RuntimeError(f"No se pudo bajar la miniatura del video {video_id}")


def _decode_entities(s: str) -> str:
    s = s or ""
    s = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), s)
    s = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), s)
    return (s.replace("&quot;", '"').replace("&apos;", "'")
             .replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&"))
