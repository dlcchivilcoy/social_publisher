"""Ranking mensual del Programa de Corresponsales «Chivilcoy en Acción».

A fin de mes junta las estadísticas de cada nota de corresponsal publicada
(vistas de Wix + insights de Facebook e Instagram del reel), arma un puntaje por
colaborador (engagement + un plus por cantidad de notas), define el podio 1°/2°/3°
con sus premios y manda un mail transparente con el detalle + un Excel, y (opcional)
crea un BORRADOR de nota en Wix con el ranking para publicarlo.

Pensado para dispararse el 1° de cada mes desde cron-job.org con:
    python main.py --corresponsales-ranking            (mes anterior)
    python main.py --corresponsales-ranking --mes 2026-07

Puntaje (todo configurable por .env):
    score_nota   = vistas_totales + interacciones * RANKING_PESO_INTERACCION
    puntos_colab = Σ score_nota + RANKING_BONUS_NOTA * cantidad_de_notas
El podio se define por `puntos_colab`. Premios en RANKING_PREMIOS (default 100k/50k/25k).
"""
import json
import smtplib
import ssl
import tempfile
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import formataddr
from html import escape as _hesc
from pathlib import Path

import openpyxl
from openpyxl.styles import Font

from platforms import facebook, instagram, wix
from utils.config import get
from utils.logger import get_logger

logger = get_logger("ranking")

LEDGER = Path(__file__).parent / ".videos_contabilidad.json"
LOGO = Path(__file__).parent / "logo.png"
PUBLICADOS = ("publicado", "publicado_solo_reel", "publicado_placa")
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
         "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


# ── Config ────────────────────────────────────────────────────────────────────
def _mes_anterior(hoy: date) -> str:
    y, m = (hoy.year - 1, 12) if hoy.month == 1 else (hoy.year, hoy.month - 1)
    return f"{y:04d}-{m:02d}"


def _premios() -> list[int]:
    raw = get("RANKING_PREMIOS") or "100000,50000,25000"
    return [int(x.strip()) for x in raw.split(",") if x.strip().lstrip("-").isdigit()]


def _peso_interaccion() -> int:
    try:
        return int(get("RANKING_PESO_INTERACCION") or 10)
    except ValueError:
        return 10


def _bonus_nota() -> int:
    try:
        return int(get("RANKING_BONUS_NOTA") or 200)
    except ValueError:
        return 200


def _wix_borrador() -> bool:
    return (get("RANKING_WIX_BORRADOR") or "1").strip().lower() not in ("0", "false", "no", "off")


def _pesos(n: int) -> str:
    return "$" + f"{int(n):,}".replace(",", ".")


def _mes_largo(mes: str) -> str:
    y, m = mes.split("-")
    return f"{MESES[int(m) - 1]} {y}"


# ── Ledger ────────────────────────────────────────────────────────────────────
def _leer_ledger() -> list[dict]:
    if not LEDGER.exists():
        return []
    try:
        return list(json.loads(LEDGER.read_text(encoding="utf-8-sig")))
    except Exception:
        return []


def _guardar_ledger(rows: list[dict]) -> None:
    LEDGER.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def _filas_del_mes(rows: list[dict], mes: str) -> list[dict]:
    """Notas de CORRESPONSALES publicadas en el mes (por fecha_recibido)."""
    return [r for r in rows
            if (r.get("fecha_recibido", "") or "")[:7] == mes
            and r.get("corresponsal_nombre")
            and r.get("estado") in PUBLICADOS]


# ── Métricas por nota ─────────────────────────────────────────────────────────
def _metricas_de_nota(fila: dict) -> dict:
    """Junta vistas (Wix + FB + IG) e interacciones (likes+comentarios+shares) de la nota."""
    wix_views = wix.views_de_post(fila.get("draft_id") or "")
    fb = facebook.video_insights(fila.get("fb_video_id") or "") or {}
    ig = instagram.media_insights(fila.get("ig_media_id") or "") or {}
    vistas = wix_views + int(fb.get("vistas", 0)) + int(ig.get("vistas", 0))
    interacciones = (int(fb.get("likes", 0)) + int(fb.get("comentarios", 0)) + int(fb.get("shares", 0))
                     + int(ig.get("likes", 0)) + int(ig.get("comentarios", 0)) + int(ig.get("shares", 0)))
    score = vistas + interacciones * _peso_interaccion()
    return {"wix_views": wix_views, "fb": fb, "ig": ig,
            "vistas": vistas, "interacciones": interacciones, "score": score}


# ── Excel ─────────────────────────────────────────────────────────────────────
def _armar_excel(ranking: list[dict], filas: list[dict], mes: str) -> Path:
    wb = openpyxl.Workbook()
    top = wb.active
    top.title = "Ranking"
    top.append(["Puesto", "Colaborador", "Celular", "Notas", "Vistas", "Interacciones", "Puntos", "Premio"])
    for c in top[1]:
        c.font = Font(bold=True)
    for r in ranking:
        top.append([r["puesto"], r["nombre"], r["celular"], r["notas"], r["vistas"],
                    r["interacciones"], r["puntos"], _pesos(r["premio"]) if r["premio"] else ""])
    for col, ancho in zip("ABCDEFGH", (8, 28, 16, 8, 12, 14, 12, 12)):
        top.column_dimensions[col].width = ancho

    det = wb.create_sheet("Detalle por nota")
    det.append(["Fecha", "Colaborador", "Título", "Vistas Wix", "Vistas totales",
                "Interacciones", "Score", "Link"])
    for c in det[1]:
        c.font = Font(bold=True)
    for f in sorted(filas, key=lambda x: x.get("_score", 0), reverse=True):
        m = f.get("_m", {})
        det.append([(f.get("fecha_recibido", "") or "")[:10], f.get("corresponsal_nombre", ""),
                    f.get("titulo", ""), m.get("wix_views", 0), m.get("vistas", 0),
                    m.get("interacciones", 0), f.get("_score", 0), f.get("post_url", "")])
    for col, ancho in zip("ABCDEFGH", (12, 26, 44, 12, 14, 14, 10, 40)):
        det.column_dimensions[col].width = ancho

    salida = Path(tempfile.gettempdir()) / f"ranking_corresponsales_{mes}.xlsx"
    wb.save(salida)
    return salida


# ── Mail ──────────────────────────────────────────────────────────────────────
def _tabla_html(ranking: list[dict]) -> str:
    medallas = {1: "🥇", 2: "🥈", 3: "🥉"}
    filas = ""
    for r in ranking:
        pr = f"<b>{_hesc(_pesos(r['premio']))}</b>" if r["premio"] else "—"
        m = medallas.get(r["puesto"], f"{r['puesto']}°")
        filas += (f"<tr><td style='padding:6px 10px'>{m}</td>"
                  f"<td style='padding:6px 10px'>{_hesc(r['nombre'])}</td>"
                  f"<td style='padding:6px 10px;text-align:center'>{r['notas']}</td>"
                  f"<td style='padding:6px 10px;text-align:right'>{r['vistas']:,}</td>".replace(",", ".") +
                  f"<td style='padding:6px 10px;text-align:right'>{r['interacciones']}</td>"
                  f"<td style='padding:6px 10px;text-align:right'>{r['puntos']:,}</td>".replace(",", ".") +
                  f"<td style='padding:6px 10px;text-align:right'>{pr}</td></tr>")
    return (
        "<table style='border-collapse:collapse;width:100%;font-family:Arial;font-size:14px'>"
        "<tr style='background:#e2620c;color:#fff'>"
        "<th style='padding:8px 10px;text-align:left'>Puesto</th>"
        "<th style='padding:8px 10px;text-align:left'>Colaborador</th>"
        "<th style='padding:8px 10px'>Notas</th><th style='padding:8px 10px'>Vistas</th>"
        "<th style='padding:8px 10px'>Interac.</th><th style='padding:8px 10px'>Puntos</th>"
        "<th style='padding:8px 10px'>Premio</th></tr>" + filas + "</table>")


def _enviar_mail(asunto: str, html: str, texto: str, xlsx: Path | None) -> None:
    remitente = get("MAIL_FROM")
    password = get("MAIL_APP_PASSWORD")
    destino = get("RANKING_EMAIL") or get("VIDEOS_REPORT_EMAIL") or get("VIDEOS_NOTIFY_EMAIL") or remitente
    if not remitente or not password or not destino:
        logger.error("Faltan credenciales de mail: no se manda el ranking.")
        return
    host = get("SMTP_HOST") or "smtp.gmail.com"
    port = int(get("SMTP_PORT") or 587)
    msg = EmailMessage()
    msg["From"] = formataddr((get("MAIL_FROM_NAME") or "Diario La Campaña", remitente))
    msg["To"] = destino
    msg["Subject"] = asunto
    msg.set_content(texto)
    msg.add_alternative(html, subtype="html")
    if xlsx and xlsx.exists():
        msg.add_attachment(xlsx.read_bytes(), maintype="application",
                           subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                           filename=xlsx.name)
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.starttls(context=ctx)
            s.login(remitente, password)
            s.send_message(msg)
        logger.info(f"Ranking enviado a {destino}")
    except Exception as e:
        logger.error(f"No se pudo enviar el ranking: {e}")


# ── Nota transparente en Wix (borrador) ───────────────────────────────────────
def _crear_borrador_wix(ranking: list[dict], mes: str) -> str:
    """Crea un BORRADOR en Wix con el ranking del mes (para revisar y publicar).
    Devuelve el draft_id o '' si no se pudo. Usa el logo como portada."""
    if not LOGO.exists():
        logger.warning("No hay logo.png para la portada del ranking; se omite el borrador Wix.")
        return ""
    titulo = f"Ranking de Corresponsales «Chivilcoy en Acción» — {_mes_largo(mes)}"
    lineas = [f"Estas son las estadísticas del Programa de Corresponsales de {_mes_largo(mes)}, "
              f"con total transparencia. ¡Gracias a todos los que colaboraron!", ""]
    medallas = {1: "🥇", 2: "🥈", 3: "🥉"}
    for r in ranking:
        prem = f" — premio {_pesos(r['premio'])}" if r["premio"] else ""
        lineas.append(f"{medallas.get(r['puesto'], str(r['puesto']) + '°')} {r['nombre']}: "
                      f"{r['notas']} nota(s), {r['vistas']} vistas, {r['interacciones']} interacciones, "
                      f"{r['puntos']} puntos{prem}.")
    body = "\n".join(lineas)
    try:
        info = wix.crear_borrador(titulo, body, LOGO, page=0,
                                  description=f"Ranking mensual del Programa de Corresponsales — {_mes_largo(mes)}.")
        return info.get("draft_id", "")
    except Exception as e:
        logger.error(f"No se pudo crear el borrador Wix del ranking: {e}")
        return ""


# ── Entry point ───────────────────────────────────────────────────────────────
def run_corresponsales_ranking(mes: str | None = None, dry_run: bool = False) -> None:
    mes = mes or _mes_anterior(date.today())
    logger.info(f"=== Ranking de corresponsales {mes} {'(dry-run)' if dry_run else ''} ===")

    rows = _leer_ledger()
    filas = _filas_del_mes(rows, mes)
    if not filas:
        logger.info(f"No hubo notas de corresponsales publicadas en {mes}. No se manda ranking.")
        return

    # 1) Métricas por nota (y se guardan en el ledger para dejar registro).
    for f in filas:
        m = _metricas_de_nota(f)
        f["_m"] = m
        f["_score"] = m["score"]
        f["ranking_metrics"] = {"mes": mes, "wix_views": m["wix_views"], "vistas": m["vistas"],
                                "interacciones": m["interacciones"], "score": m["score"],
                                "fb": m["fb"], "ig": m["ig"],
                                "calculado": datetime.now().isoformat(timespec="seconds")}
    if not dry_run:
        _guardar_ledger(rows)

    # 2) Agregado por colaborador.
    por_colab: dict[str, dict] = {}
    for f in filas:
        nombre = (f.get("corresponsal_nombre") or "—").strip()
        d = por_colab.setdefault(nombre, {"nombre": nombre, "celular": f.get("corresponsal_celular", ""),
                                          "notas": 0, "vistas": 0, "interacciones": 0, "score": 0})
        d["celular"] = f.get("corresponsal_celular") or d["celular"]
        d["notas"] += 1
        d["vistas"] += f["_m"]["vistas"]
        d["interacciones"] += f["_m"]["interacciones"]
        d["score"] += f["_score"]

    # 3) Puntaje final (engagement + plus por cantidad) y podio con premios.
    ranking = list(por_colab.values())
    for d in ranking:
        d["puntos"] = d["score"] + _bonus_nota() * d["notas"]
    ranking.sort(key=lambda d: (d["puntos"], d["vistas"]), reverse=True)
    premios = _premios()
    for i, d in enumerate(ranking):
        d["puesto"] = i + 1
        d["premio"] = premios[i] if i < len(premios) else 0

    # 4) Salidas: Excel + mail + (opcional) borrador Wix.
    xlsx = _armar_excel(ranking, filas, mes)
    ganadores = [d for d in ranking if d["premio"]]
    resumen_txt = "\n".join(
        f"{d['puesto']}° {d['nombre']}: {d['puntos']} puntos ({d['notas']} nota/s, "
        f"{d['vistas']} vistas) — {_pesos(d['premio']) if d['premio'] else 'sin premio'}"
        for d in ranking)

    if dry_run:
        logger.info(f"[dry-run] Ranking {mes}:\n{resumen_txt}\nExcel: {xlsx}")
        return

    draft_id = _crear_borrador_wix(ranking, mes) if _wix_borrador() else ""
    intro_wix = (f"<p>📝 Dejé un <b>borrador en Wix</b> con el ranking para revisar y publicar "
                 f"(draft {draft_id}).</p>" if draft_id else "")
    podio = "".join(f"<li><b>{d['puesto']}°</b> {_hesc(d['nombre'])} — <b>{_hesc(_pesos(d['premio']))}</b></li>"
                    for d in ganadores)
    html = (
        f"<div style='font-family:Arial;max-width:680px;color:#222'>"
        f"<h2 style='color:#e2620c'>🏆 Ranking de Corresponsales — {_mes_largo(mes)}</h2>"
        f"<p>Podio del mes (premios):</p><ul style='font-size:16px'>{podio or '<li>—</li>'}</ul>"
        f"{_tabla_html(ranking)}"
        f"<p style='color:#777;font-size:13px;margin-top:14px'>Puntos = vistas (Wix+FB+IG) + "
        f"interacciones×{_peso_interaccion()} + {_bonus_nota()} por nota enviada. Detalle por nota en el Excel adjunto.</p>"
        f"{intro_wix}</div>")
    _enviar_mail(f"🏆 Ranking Corresponsales — {_mes_largo(mes)}", html, resumen_txt, xlsx)
    logger.info(f"Ranking {mes} listo. Podio: "
                + " | ".join(f"{d['puesto']}° {d['nombre']}" for d in ganadores))
    logger.info("=== Ranking de corresponsales: fin ===")
