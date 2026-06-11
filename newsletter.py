"""
Newsletter de la mañana para los SUSCRIPTORES DE LA WEB.

Manda un correo con los titulares más recientes del diario (con link a cada nota
en la web) y el PDF del día adjunto. Reusa el motor SMTP del envío del PDF
(`mailer.py`); lo único distinto es de dónde salen los destinatarios y el cuerpo.

Fuentes:
- Destinatarios: tabla `suscriptores` de Supabase, leída por la API REST con la
  clave SERVICE (service_role / secret). Esa clave saltea RLS y SOLO se usa acá,
  del lado servidor (PC/nube), NUNCA en el navegador (el sitio usa la anon key,
  que solo puede insertar).
- Titulares: Blog de Wix (las mismas notas que publica el sistema) con su URL
  pública (`/single-post/...` en el dominio).
- PDF: el del día (carpeta de DIARIO PDF), igual criterio que el mailer.

Config (.env):
  SUPABASE_URL              https://xxxx.supabase.co
  SUPABASE_SERVICE_KEY      service_role / secret de Supabase (Project Settings → API)
  MAIL_FROM, MAIL_APP_PASSWORD, SMTP_HOST, SMTP_PORT, MAIL_FROM_NAME  (igual que el mailer)
  NEWSLETTER_PDF_PATH       opcional; si no, usa TAPA_FOLDER / MAIL_PDF_PATH
  NEWSLETTER_MAX_TITULARES  opcional (default 12)
  NEWSLETTER_SUBJECT        opcional, admite {fecha}
"""
import json
import re
import smtplib
import ssl
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import formataddr
from html import escape
from pathlib import Path

import requests

from mailer import _fecha_larga, _pdf_horas, MAX_PDF_HOURS
from platforms.wix import POSTS_QUERY_URL, _headers as _wix_headers, _sitio_url
from utils.config import get
from utils.logger import get_logger

logger = get_logger("newsletter")

LEDGER_NAME = ".newsletter.json"
EMAIL_OK = lambda e: bool(e) and "@" in e and "." in e.split("@")[-1]


# ── Suscriptores (Supabase) ───────────────────────────────────────────────────
def _suscriptores() -> list[str]:
    """Lee los emails de la tabla `suscriptores` con la clave SERVICE (saltea RLS)."""
    url = get("SUPABASE_URL")
    key = get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise ValueError("Faltan SUPABASE_URL / SUPABASE_SERVICE_KEY en .env")
    endpoint = url.rstrip("/") + "/rest/v1/suscriptores"
    r = requests.get(
        endpoint,
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
        params={"select": "email", "order": "creado.asc"},
        timeout=30,
    )
    if r.status_code >= 400:
        raise RuntimeError(f"Supabase ({r.status_code}): {r.text[:200]}")
    vistos, emails = set(), []
    for row in r.json():
        e = str(row.get("email", "")).strip().lower()
        if EMAIL_OK(e) and e not in vistos:
            vistos.add(e)
            emails.append(e)
    return emails


# ── Titulares del día (Wix) ───────────────────────────────────────────────────
def _titulo_limpio(raw: str, max_len: int = 110) -> str:
    """El título de Wix viene como "VOLANTA — título — bajada" todo junto (igual
    que en la web). Devuelve solo el titular: si el primer tramo es corto (≤25,
    es la volanta) toma el segundo; si no, el primero (lo demás suele ser bajada).
    Recorta prolijo si se pasa de max_len."""
    partes = [p.strip() for p in re.split(r"\s+[—–-]\s+", (raw or "").strip()) if p.strip()]
    if not partes:
        titulo = (raw or "").strip()
    elif len(partes) >= 2 and len(partes[0]) <= 25:
        titulo = partes[1]
    else:
        titulo = partes[0]
    titulo = re.sub(r"\s+", " ", titulo)
    if len(titulo) > max_len:
        titulo = titulo[:max_len].rsplit(" ", 1)[0].rstrip(" ,.;:") + "…"
    return titulo


def _titulares(limit: int) -> list[tuple[str, str]]:
    """Últimas notas del blog (título limpio + URL pública), las más nuevas primero."""
    body = {
        "query": {
            "sort": [{"fieldName": "firstPublishedDate", "order": "DESC"}],
            "paging": {"limit": limit},
        },
        "fieldsets": ["URL"],
    }
    r = requests.post(POSTS_QUERY_URL, headers=_wix_headers(), json=body, timeout=30)
    if r.status_code >= 400:
        raise RuntimeError(f"Wix titulares ({r.status_code}): {r.text[:200]}")
    out: list[tuple[str, str]] = []
    for p in r.json().get("posts", []):
        titulo = _titulo_limpio(p.get("title") or "")
        u = p.get("url") or {}
        link = (u.get("base", "") + u.get("path", "")).strip()
        if titulo and link:
            out.append((titulo, link))
    return out


# ── PDF del día ───────────────────────────────────────────────────────────────
def _pdf_del_dia() -> Path | None:
    """Resuelve el PDF: NEWSLETTER_PDF_PATH (archivo), si no el más nuevo de
    TAPA_FOLDER (carpeta, lo que usa la nube), si no MAIL_PDF_PATH (archivo)."""
    cand = get("NEWSLETTER_PDF_PATH")
    if cand and Path(cand).is_file():
        return Path(cand)
    carpeta = get("TAPA_FOLDER")
    if carpeta and Path(carpeta).is_dir():
        pdfs = [p for p in Path(carpeta).iterdir() if p.is_file() and p.suffix.lower() == ".pdf"]
        if pdfs:
            return max(pdfs, key=lambda p: p.stat().st_mtime)
    mp = get("MAIL_PDF_PATH")
    if mp and Path(mp).is_file():
        return Path(mp)
    return None


# ── Cuerpo del mail ───────────────────────────────────────────────────────────
def _texto(titulares: list[tuple[str, str]], fecha: str, con_pdf: bool) -> str:
    lineas = [f"Diario La Campaña — Noticias de hoy ({fecha})", ""]
    if titulares:
        lineas.append("Lo más reciente:")
        for titulo, link in titulares:
            lineas += [f"• {titulo}", f"  {link}"]
        lineas.append("")
    if con_pdf:
        lineas.append("Te adjuntamos la edición de hoy en PDF.")
        lineas.append("")
    lineas.append(f"Más noticias en {_sitio_url()}")
    lineas.append("Diario La Campaña — Chivilcoy")
    return "\n".join(lineas)


def _html(titulares: list[tuple[str, str]], fecha: str, con_pdf: bool) -> str:
    sitio = _sitio_url()
    items = "\n".join(
        f'<li style="margin:0 0 14px"><a href="{escape(link)}" '
        f'style="color:#F77F00;text-decoration:none;font-weight:700;font-size:16px;line-height:1.35">'
        f'{escape(titulo)}</a></li>'
        for titulo, link in titulares
    )
    bloque_titulares = (
        f'<p style="margin:0 0 10px;font-weight:700;color:#11131A;font-size:15px">Lo más reciente</p>'
        f'<ul style="margin:0 0 22px;padding:0 0 0 18px">{items}</ul>'
        if titulares else ""
    )
    bloque_pdf = (
        '<p style="margin:0 0 22px;color:#444;font-size:14px">📎 Te adjuntamos la '
        '<strong>edición de hoy en PDF</strong>.</p>' if con_pdf else ""
    )
    return f"""\
<!doctype html><html><body style="margin:0;background:#f4f4f6;padding:24px 0;font-family:Arial,Helvetica,sans-serif">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center">
    <table role="presentation" width="560" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:14px;overflow:hidden;border:1px solid #eee">
      <tr><td style="background:#11131A;padding:20px 28px">
        <span style="color:#fff;font-size:20px;font-weight:800;letter-spacing:-.3px">Diario La Campaña</span>
        <span style="color:#F77F00;font-size:20px;font-weight:800"> ·</span>
        <div style="color:#9aa;font-size:12px;margin-top:3px">Noticias de Chivilcoy — {escape(fecha)}</div>
      </td></tr>
      <tr><td style="padding:26px 28px">
        <p style="margin:0 0 18px;color:#11131A;font-size:16px">¡Buenos días! Estas son las novedades del diario.</p>
        {bloque_titulares}
        {bloque_pdf}
        <a href="{escape(sitio)}" style="display:inline-block;background:#F77F00;color:#fff;text-decoration:none;font-weight:700;padding:12px 22px;border-radius:10px;font-size:15px">Ver más noticias →</a>
      </td></tr>
      <tr><td style="padding:18px 28px;background:#fafafa;border-top:1px solid #eee;color:#888;font-size:12px">
        Recibís este correo porque te suscribiste en {escape(sitio)}.<br>Diario La Campaña — Chivilcoy, Buenos Aires.
      </td></tr>
    </table>
  </td></tr></table>
</body></html>"""


# ── Ledger anti-repetición (una vez por día) ──────────────────────────────────
def _ledger_path() -> Path:
    return Path(__file__).parent / LEDGER_NAME


def _leer_ledger() -> list[str]:
    p = _ledger_path()
    if not p.exists():
        return []
    try:
        return list(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return []


def _guardar_ledger(fechas: list[str]) -> None:
    _ledger_path().write_text(json.dumps(fechas[-60:], ensure_ascii=False, indent=2), encoding="utf-8")


# ── Envío ─────────────────────────────────────────────────────────────────────
def run_newsletter(dry_run: bool = False) -> None:
    modo = "SIMULACIÓN (dry-run)" if dry_run else "ENVÍO REAL"
    logger.info(f"=== Newsletter de la mañana [{modo}] ===")

    hoy_iso = date.today().isoformat()
    ledger = _leer_ledger()
    if hoy_iso in ledger and not dry_run:
        logger.info(f"El newsletter de hoy ({hoy_iso}) ya se envió. NO se reenvía.")
        return

    # Destinatarios
    try:
        emails = _suscriptores()
    except Exception as e:
        logger.error(f"No se pudieron leer los suscriptores: {e}")
        return
    if not emails:
        logger.info("No hay suscriptores todavía. Nada que enviar.")
        return

    # Titulares
    try:
        max_tit = int(get("NEWSLETTER_MAX_TITULARES") or 12)
    except ValueError:
        max_tit = 12
    try:
        titulares = _titulares(max_tit)
    except Exception as e:
        logger.warning(f"No se pudieron leer los titulares de Wix: {e}")
        titulares = []

    # PDF (se adjunta si existe y no está viejo)
    pdf = _pdf_del_dia()
    con_pdf = False
    if pdf:
        horas = _pdf_horas(pdf)
        if horas <= MAX_PDF_HOURS:
            con_pdf = True
            logger.info(f"PDF a adjuntar: {pdf.name} ({horas:.1f}h)")
        else:
            logger.warning(f"El PDF «{pdf.name}» tiene {horas:.1f}h (> {MAX_PDF_HOURS}h): no se adjunta.")
    else:
        logger.warning("No se encontró PDF del día: se manda solo con titulares.")

    if not titulares and not con_pdf:
        logger.warning("Sin titulares y sin PDF: no se envía nada (no tendría contenido).")
        return

    fecha = _fecha_larga(date.today())
    asunto = (get("NEWSLETTER_SUBJECT") or "Diario La Campaña — Noticias de hoy ({fecha})").format(fecha=fecha)
    texto = _texto(titulares, fecha, con_pdf)
    html = _html(titulares, fecha, con_pdf)

    logger.info(f"{len(emails)} suscriptor(es), {len(titulares)} titular(es), PDF={'sí' if con_pdf else 'no'}.")

    if dry_run:
        logger.info("--- DRY-RUN: no se envía nada ---")
        logger.info(f"Asunto: {asunto}")
        for t, l in titulares:
            logger.info(f"   • {t} → {l}")
        logger.info(f"Destinatarios ({len(emails)}): {', '.join(emails[:10])}{' …' if len(emails) > 10 else ''}")
        return

    remitente = get("MAIL_FROM")
    password = get("MAIL_APP_PASSWORD")
    host = get("SMTP_HOST") or "smtp.gmail.com"
    port = int(get("SMTP_PORT") or 587)
    if not remitente or not password:
        logger.error("Faltan MAIL_FROM y/o MAIL_APP_PASSWORD en .env — no se puede enviar.")
        return
    nombre_from = get("MAIL_FROM_NAME") or "Diario La Campaña"

    pdf_bytes = pdf.read_bytes() if con_pdf else None
    adjunto_nombre = f"Diario La Campaña {fecha}.pdf"

    enviados, fallidos = 0, 0
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=60) as server:
            server.starttls(context=ctx)
            server.login(remitente, password)
            for email in emails:
                try:
                    msg = EmailMessage()
                    msg["From"] = formataddr((nombre_from, remitente))
                    msg["To"] = email
                    msg["Subject"] = asunto
                    msg.set_content(texto)
                    msg.add_alternative(html, subtype="html")
                    if pdf_bytes is not None:
                        msg.add_attachment(pdf_bytes, maintype="application", subtype="pdf",
                                           filename=adjunto_nombre)
                    server.send_message(msg)
                    logger.info(f"[OK] enviado a {email}")
                    enviados += 1
                except Exception as e:
                    logger.error(f"[FALLÓ] {email}: {e}")
                    fallidos += 1
    except Exception as e:
        logger.error(f"No se pudo conectar/autenticar al SMTP ({host}:{port}): {e}")
        return

    logger.info(f"=== Resumen: {enviados} enviado(s), {fallidos} fallido(s) ===")
    if enviados:
        ledger.append(hoy_iso)
        _guardar_ledger(ledger)
        logger.info(f"Newsletter de {hoy_iso} registrado como enviado.")
