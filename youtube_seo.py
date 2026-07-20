"""Optimización SEO de YouTube (Radio del Centro): reescribe título/descripción y
arma una miniatura de marca para los videos YA publicados, para que el algoritmo
los muestre más.

Flujo en 2 pasos (REVISAR y APROBAR — nada se cambia sin tu confirmación):
  1) run_generate(limit): trae los últimos N videos, los reescribe con Gemini (a partir
     del título/descripción actuales, sin bajar el video), arma la miniatura, y vuelca
     todo a youtube_seo/ (carpeta por video + index.html para revisar + propuestas.json).
  2) Abrís youtube_seo/index.html, ves ANTES/DESPUÉS, y en propuestas.json ponés
     "aplicar": true en los que quieras (podés editar el texto a mano).
  3) run_apply(): aplica en YouTube SOLO los marcados con "aplicar": true.

Corre LOCAL (usa el token OAuth de .yt_token.json, que rota).
"""
import html
import json
from pathlib import Path

from utils.logger import get_logger

logger = get_logger("youtube_seo")

OUT_DIR = Path(__file__).parent / "youtube_seo"
PROPUESTAS = OUT_DIR / "propuestas.json"
LEDGER = Path(__file__).parent / ".yt_seo.json"  # IDs ya procesados por el modo auto


def _leer_ledger() -> set:
    try:
        if LEDGER.exists():
            return set(json.loads(LEDGER.read_text(encoding="utf-8")))
    except Exception:
        pass
    return set()


def _guardar_ledger(ids: set) -> None:
    LEDGER.write_text(json.dumps(sorted(ids)[-500:], ensure_ascii=False, indent=2), encoding="utf-8")


def _es_programa(titulo_original: str) -> bool:
    """True si el video es el programa completo (La Mañana del Centro) → usa el layout
    limpio (card arriba / gancho abajo)."""
    from utils.config import get
    excl = (get("STORY_EXCLUDE_TITLE") or "MAÑANA DEL CENTRO").strip().lower()
    return excl in (titulo_original or "").lower()


def _gancho_de(vid: str, titulo: str, descripcion: str, prog: bool) -> dict:
    """Gancho de la miniatura (gancho + SEO) por TEXTO solo (título + descripción), para
    ahorrarle consumo a Gemini (NO transcribe el video). Si falla, cae al título.
    Devuelve {gancho, keyword}."""
    from utils import gemini
    try:
        g = gemini.gancho_miniatura("", titulo, descripcion, usar_video=False)
        if g.get("gancho"):
            return {"gancho": g["gancho"], "keyword": g.get("keyword", "")}
    except Exception as e:
        logger.warning(f"    Gancho falló para {vid}: {e} (uso el título)")
    return {"gancho": titulo, "keyword": ""}


def _leer_propuestas() -> dict:
    if PROPUESTAS.exists():
        try:
            return json.loads(PROPUESTAS.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("propuestas.json ilegible; se reinicia.")
    return {}


def _guardar_propuestas(data: dict) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    PROPUESTAS.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def run_generate(limit: int = 15, dry_run: bool = False) -> None:
    """Trae los últimos `limit` videos, propone SEO y arma miniaturas. NO toca YouTube."""
    from platforms import youtube_api
    from utils import gemini

    OUT_DIR.mkdir(exist_ok=True)
    videos = youtube_api.list_recent_videos(limit)
    # Solo videos/shorts: se EXCLUYEN los vivos / el programa completo.
    videos = [v for v in videos if not _es_programa(v["title"])]
    logger.info(f"Generando propuestas SEO (solo texto) para {len(videos)} video(s)…")
    data = _leer_propuestas()

    for v in videos:
        vid = v["id"]
        logger.info(f"  {vid} — «{v['title'][:60]}»")
        try:
            seo = gemini.seo_youtube(v["title"], v["description"], f"https://youtu.be/{vid}")
        except Exception as e:
            logger.warning(f"    Gemini falló para {vid}: {e}")
            continue

        # Solo SEO de texto: la miniatura gráfica está desactivada (pedido 2026-06-27).
        prev = data.get(vid, {})
        data[vid] = {
            "video_id": vid,
            "url": f"https://youtu.be/{vid}",
            "titulo_actual": v["title"],
            "descripcion_actual": v["description"],
            "tags_actuales": v.get("tags", []),
            "titulo_nuevo": seo["titulo"],
            "descripcion_nueva": seo["descripcion"],
            "tags_nuevos": seo["tags"],
            "miniatura": "",
            "aplicar": prev.get("aplicar", False),
            "aplicado": prev.get("aplicado", False),
        }
        _guardar_propuestas(data)

    _escribir_index(data)
    logger.info(f"Listo. Revisá {OUT_DIR / 'index.html'} y poné \"aplicar\": true en "
                f"{PROPUESTAS.name} para los que quieras aplicar.")


def run_apply(dry_run: bool = False) -> None:
    """Aplica en YouTube SOLO las propuestas con "aplicar": true (y no aplicadas aún)."""
    from platforms import youtube_api

    data = _leer_propuestas()
    if not data:
        logger.warning("No hay propuestas. Corré primero: python main.py --yt-seo")
        return

    aplicados = 0
    for vid, p in data.items():
        if not p.get("aplicar") or p.get("aplicado"):
            continue
        titulo = (p.get("titulo_nuevo") or "").strip()
        descripcion = (p.get("descripcion_nueva") or "").strip()
        tags = p.get("tags_nuevos") or None
        if not titulo:
            logger.warning(f"  {vid}: sin titulo_nuevo, lo salteo.")
            continue

        logger.info(f"  Aplicando {vid} — «{titulo[:60]}»")
        if dry_run:
            logger.info("    (dry-run: no se aplica nada)")
            continue
        try:
            youtube_api.update_video_metadata(vid, titulo, descripcion, tags)
        except Exception as e:
            logger.error(f"    Falló el update de {vid}: {e}")
            continue

        # Miniatura gráfica desactivada (pedido 2026-06-27): solo se aplica texto.
        p["aplicado"] = True
        aplicados += 1
        _guardar_propuestas(data)

    logger.info(f"Aplicado(s) {aplicados} video(s).")


def run_auto(dry_run: bool = False, limit: int = 15) -> None:
    """AUTOMÁTICO: procesa los videos RECIENTES que aún no se tocaron (no están en el
    registro) y les aplica título + descripción + tags + miniatura nuevos, SIN revisión.
    El registro `.yt_seo.json` está "sellado" con los videos viejos, así SOLO toca los
    NUEVOS que subas de ahora en más (sin importar el horario). Idempotente.
    Guarda igual el antes/después en youtube_seo/ por si querés auditar o revertir."""
    from platforms import youtube_api
    from utils import gemini

    recientes = youtube_api.list_recent_videos(limit)  # snippets completos, más reciente 1°
    ledger = _leer_ledger()
    # Solo videos/shorts: se EXCLUYEN los vivos / el programa completo (La Mañana del Centro).
    vids = [v for v in recientes if v["id"] not in ledger and not _es_programa(v["title"])]
    if not vids:
        logger.info(f"Sin videos nuevos para SEO (revisados los últimos {len(recientes)}; "
                    f"los vivos/programa se saltean).")
        return

    logger.info(f"AUTO: {len(vids)} video(s) NUEVO(s) sin procesar (dry_run={dry_run})…")
    data = _leer_propuestas()
    OUT_DIR.mkdir(exist_ok=True)

    for v in vids:
        vid = v["id"]
        logger.info(f"  {vid} — «{v['title'][:60]}»")
        try:
            seo = gemini.seo_youtube(v["title"], v["description"], f"https://youtu.be/{vid}")
        except Exception as e:
            logger.warning(f"    Gemini falló para {vid}: {e} (se reintenta en la próxima corrida)")
            continue

        # Solo SEO de texto (título + descripción + tags). La miniatura gráfica se desactivó
        # (pedido del usuario 2026-06-27): no se arma ni se aplica imagen.
        data[vid] = {
            "video_id": vid,
            "url": f"https://youtu.be/{vid}",
            "titulo_actual": v["title"],
            "descripcion_actual": v["description"],
            "tags_actuales": v.get("tags", []),
            "titulo_nuevo": seo["titulo"],
            "descripcion_nueva": seo["descripcion"],
            "tags_nuevos": seo["tags"],
            "miniatura": "",
            "aplicar": True,
            "aplicado": False,
        }

        if dry_run:
            logger.info("    (dry-run: no se aplica)")
            _guardar_propuestas(data)
            continue

        try:
            youtube_api.update_video_metadata(vid, seo["titulo"], seo["descripcion"], seo["tags"])
        except Exception as e:
            logger.error(f"    Falló el update de {vid}: {e}")
            _guardar_propuestas(data)
            continue
        data[vid]["aplicado"] = True
        ledger.add(vid)
        _guardar_propuestas(data)
        _guardar_ledger(ledger)
        logger.info("    ✓ aplicado (título + descripción + tags)")

    _escribir_index(data)
    logger.info("AUTO SEO de hoy: listo.")


def _escribir_index(data: dict) -> None:
    OUT_DIR.mkdir(exist_ok=True)
    cards = []
    for vid, p in data.items():
        if p.get("aplicado"):
            est, cls = "APLICADO", "ok"
        elif p.get("aplicar"):
            est, cls = "marcado para aplicar", "go"
        else:
            est, cls = "pendiente", ""
        nueva_img = p.get("miniatura") or ""
        cards.append(f"""
    <div class="card">
      <div class="head"><a href="{html.escape(p.get('url',''))}" target="_blank">{html.escape(vid)}</a>
        <span class="estado {cls}">{est}</span></div>
      <div class="cols">
        <div class="col">
          <h3>ANTES</h3>
          <img src="{html.escape(vid)}/miniatura_actual.jpg" onerror="this.style.display='none'">
          <p class="t">{html.escape(p.get('titulo_actual',''))}</p>
          <pre>{html.escape(p.get('descripcion_actual',''))}</pre>
        </div>
        <div class="col new">
          <h3>DESPUÉS (propuesta)</h3>
          <img src="{html.escape(nueva_img)}" onerror="this.style.display='none'">
          <p class="t">{html.escape(p.get('titulo_nuevo',''))}</p>
          <p class="gancho">Gancho miniatura: {html.escape(p.get('gancho_miniatura',''))} · resaltar: {html.escape(p.get('gancho_keyword',''))}</p>
          <pre>{html.escape(p.get('descripcion_nueva',''))}</pre>
          <p class="tags">{html.escape(', '.join(p.get('tags_nuevos',[])))}</p>
        </div>
      </div>
    </div>""")

    page = f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>YouTube SEO — propuestas</title><style>
body{{font-family:Arial,Helvetica,sans-serif;background:#f4f4f4;margin:0;padding:24px;color:#222}}
h1{{color:#e2620c}} .intro{{background:#fff;border-radius:8px;padding:12px 16px;margin-bottom:20px}}
.card{{background:#fff;border-radius:10px;padding:16px;margin:0 0 24px;box-shadow:0 1px 4px #0002}}
.head{{display:flex;justify-content:space-between;font-weight:bold;margin-bottom:12px}}
.estado{{color:#888}} .estado.go{{color:#e2620c}} .estado.ok{{color:#1a8a1a}}
.cols{{display:flex;gap:20px;flex-wrap:wrap}} .col{{flex:1;min-width:320px}}
.col.new{{border-left:3px solid #e2620c;padding-left:20px}}
img{{width:100%;max-width:560px;border-radius:6px;background:#eee;display:block}}
.gancho{{color:#e2620c;font-weight:bold;font-size:14px;margin:10px 0 0}}
.t{{font-weight:bold;font-size:17px;margin:6px 0 4px}}
pre{{white-space:pre-wrap;font-family:inherit;color:#444;background:#fafafa;padding:8px;border-radius:4px;margin:0}}
.tags{{color:#888;font-size:13px;margin-top:6px}}
</style></head><body>
<h1>YouTube SEO — {len(data)} video(s)</h1>
<div class="intro">Revisá cada propuesta. Para aplicar, abrí <b>propuestas.json</b> y poné
<code>"aplicar": true</code> en los que quieras (podés editar el texto a mano).
Después corré <code>python main.py --yt-seo-apply</code>.</div>
{''.join(cards)}
</body></html>"""
    (OUT_DIR / "index.html").write_text(page, encoding="utf-8")
