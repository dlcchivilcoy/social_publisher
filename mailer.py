"""
Envío del PDF del diario por correo a los clientes que tienen MAIL en la planilla.

- Lee la planilla Excel (GRUPO CLIENTES PRIVADO) y toma a TODOS los que tengan
  una dirección de correo en la columna MAIL.
- Resuelve el PDF de hoy igual que el sistema de WhatsApp: usa el nombre fijo si
  existe, si no agarra el PDF más nuevo de la carpeta.
- Verifica que el PDF no sea viejo (máx. 24 h por defecto).
- Memoria anti-repetición: no reenvía el mismo PDF dos veces (identidad = nombre|tamaño|fecha).
- Envía por SMTP (Gmail por defecto) con el PDF adjunto.

Las credenciales salen de .env (NUNCA se commitean):
  MAIL_FROM            cuenta desde la que se envía (ej. diario@gmail.com)
  MAIL_APP_PASSWORD    contraseña de aplicación (Gmail, 16 caracteres)
  SMTP_HOST            por defecto smtp.gmail.com
  SMTP_PORT            por defecto 587 (STARTTLS)
  CLIENTES_XLSX        ruta a la planilla
  MAIL_PDF_PATH        ruta del PDF (nombre fijo; si no existe, toma el más nuevo de su carpeta)
  MAIL_SUBJECT         asunto (admite {fecha})
  MAIL_BODY            cuerpo del mail (admite {fecha})
"""
import json
import smtplib
import ssl
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

import openpyxl

from utils.config import get
from utils.logger import get_logger

logger = get_logger("mailer")

LEDGER_NAME = ".mail.json"
MAX_PDF_HOURS = 24

DEFAULT_XLSX = r"C:\Users\Diario\Desktop\GRUPO CLIENTES PRIVADO\CLIENTES DIARIO DIGITAL.xlsx"
DEFAULT_PDF = r"C:\Users\Diario\Desktop\DIARIO PDF\diario_hoy.pdf"

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
         "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def _fecha_larga(d: date) -> str:
    return f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]} de {d.year}"


# ── PDF ───────────────────────────────────────────────────────────────────────
def _resolver_pdf(ruta: Path) -> Path | None:
    """Usa el nombre fijo si existe; si no, el PDF más nuevo de la misma carpeta."""
    if ruta.exists():
        return ruta
    carpeta = ruta.parent
    if not carpeta.exists():
        return None
    pdfs = [p for p in carpeta.iterdir()
            if p.is_file() and p.suffix.lower() == ".pdf"]
    if not pdfs:
        return None
    return sorted(pdfs, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def _pdf_horas(pdf: Path) -> float:
    return (datetime.now().timestamp() - pdf.stat().st_mtime) / 3600.0


def _identidad(pdf: Path) -> str:
    st = pdf.stat()
    return f"{pdf.name}|{st.st_size}|{round(st.st_mtime * 1000)}"


# ── Ledger anti-repetición ──────────────────────────────────────────────────────
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


def _guardar_ledger(ids: list[str]) -> None:
    _ledger_path().write_text(
        json.dumps(ids[-50:], ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── Planilla ────────────────────────────────────────────────────────────────────
def _es_mail(valor) -> bool:
    return bool(valor) and "@" in str(valor) and "." in str(valor)


def _leer_destinatarios(xlsx: Path) -> list[tuple[str, str]]:
    """
    Devuelve [(nombre, email)] de las filas que tengan un correo válido.
    Busca la columna MAIL por encabezado; si no la encuentra, escanea celdas con '@'.
    """
    wb = openpyxl.load_workbook(xlsx, data_only=True)
    ws = wb.worksheets[0]
    filas = list(ws.iter_rows(values_only=True))
    if not filas:
        return []

    # Detectar columna de nombre (0) y de mail por encabezado.
    encabezado = [str(c).strip().upper() if c is not None else "" for c in filas[0]]
    col_mail = None
    for i, h in enumerate(encabezado):
        if "MAIL" in h or "CORREO" in h or "EMAIL" in h:
            col_mail = i
            break

    destinatarios: list[tuple[str, str]] = []
    vistos = set()
    for row in filas[1:]:
        nombre = str(row[0]).strip() if row and row[0] is not None else ""
        email = None
        if col_mail is not None and col_mail < len(row):
            if _es_mail(row[col_mail]):
                email = str(row[col_mail]).strip()
        if email is None:  # fallback: cualquier celda con un mail
            for c in row:
                if _es_mail(c):
                    email = str(c).strip()
                    break
        if email and email.lower() not in vistos:
            vistos.add(email.lower())
            destinatarios.append((nombre or email, email))
    return destinatarios


# ── Envío SMTP ────────────────────────────────────────────────────────────────
def _enviar_uno(server, remitente, nombre_from, nombre, email,
                asunto, cuerpo, pdf: Path, adjunto_nombre: str) -> None:
    msg = EmailMessage()
    msg["From"] = formataddr((nombre_from, remitente))
    msg["To"] = formataddr((nombre, email))
    msg["Subject"] = asunto
    msg.set_content(cuerpo)
    data = pdf.read_bytes()
    msg.add_attachment(data, maintype="application", subtype="pdf",
                       filename=adjunto_nombre)
    server.send_message(msg)


def run_mail(dry_run: bool = False) -> None:
    modo = "SIMULACIÓN (dry-run)" if dry_run else "ENVÍO REAL"
    logger.info(f"=== Envío del diario por mail [{modo}] ===")

    xlsx = Path(get("CLIENTES_XLSX") or DEFAULT_XLSX)
    pdf_cfg = Path(get("MAIL_PDF_PATH") or DEFAULT_PDF)

    if not xlsx.exists():
        logger.error(f"No se encontró la planilla: {xlsx}")
        return

    pdf = _resolver_pdf(pdf_cfg)
    if not pdf:
        logger.error(f"No hay ningún PDF en la carpeta: {pdf_cfg.parent}")
        return

    horas = _pdf_horas(pdf)
    if horas > MAX_PDF_HOURS:
        logger.warning(
            f"El PDF «{pdf.name}» tiene {horas:.1f}h de antigüedad (máx {MAX_PDF_HOURS}h). "
            "No se envía. Actualizá el PDF y reintentá."
        )
        return

    destinatarios = _leer_destinatarios(xlsx)
    if not destinatarios:
        logger.info("No hay clientes con correo en la planilla.")
        return

    logger.info(f"PDF: {pdf.name} ({horas:.1f}h) — {len(destinatarios)} destinatario(s):")
    for nombre, email in destinatarios:
        logger.info(f"   • {nombre} <{email}>")

    # Anti-repetición por identidad del PDF.
    identidad = _identidad(pdf)
    ledger = _leer_ledger()
    if identidad in ledger:
        logger.info(
            f"El PDF «{pdf.name}» ya se envió por mail y no cambió. NO se reenvía. "
            "(Reemplazá el PDF por uno nuevo para que se mande.)"
        )
        return

    fecha = _fecha_larga(date.today())
    asunto = (get("MAIL_SUBJECT") or "Diario La Campaña — Edición de hoy ({fecha})").format(fecha=fecha)
    cuerpo = (get("MAIL_BODY") or
              "¡Buenos días!\n\nAdjuntamos la edición de hoy del Diario La Campaña.\n\n"
              "Que tengas un gran día.\nDiario La Campaña").format(fecha=fecha)
    adjunto_nombre = f"Diario La Campaña {fecha}.pdf"

    if dry_run:
        logger.info("--- DRY-RUN: no se envía nada ---")
        logger.info(f"Asunto: {asunto}")
        logger.info(f"Adjunto: {adjunto_nombre}")
        logger.info(f"Cuerpo:\n{cuerpo}")
        return

    remitente = get("MAIL_FROM")
    password = get("MAIL_APP_PASSWORD")
    host = get("SMTP_HOST") or "smtp.gmail.com"
    port = int(get("SMTP_PORT") or 587)
    if not remitente or not password:
        logger.error("Faltan MAIL_FROM y/o MAIL_APP_PASSWORD en .env — no se puede enviar.")
        return

    nombre_from = get("MAIL_FROM_NAME") or "Diario La Campaña"

    enviados, fallidos = 0, 0
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(host, port, timeout=60) as server:
            server.starttls(context=ctx)
            server.login(remitente, password)
            for nombre, email in destinatarios:
                try:
                    _enviar_uno(server, remitente, nombre_from, nombre, email,
                                asunto, cuerpo, pdf, adjunto_nombre)
                    logger.info(f"[OK] enviado a {nombre} <{email}>")
                    enviados += 1
                except Exception as e:
                    logger.error(f"[FALLÓ] {nombre} <{email}>: {e}")
                    fallidos += 1
    except Exception as e:
        logger.error(f"No se pudo conectar/autenticar al SMTP ({host}:{port}): {e}")
        return

    logger.info(f"=== Resumen: {enviados} enviado(s), {fallidos} fallido(s) ===")

    # Marca el PDF como enviado solo si al menos uno salió.
    if enviados:
        ledger.append(identidad)
        _guardar_ledger(ledger)
        logger.info(f"PDF «{pdf.name}» registrado como enviado por mail.")
