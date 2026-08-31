"""Ranking mensual del Programa de Corresponsales «Chivilcoy en Acción».

A fin de mes puntúa cada nota de corresponsal con la FOTO de métricas que tomó
`metricas.py` a las 72 h de publicada (misma vara para todos), arma el podio y premia al
1° puesto, y manda un mail transparente con el detalle + un Excel; opcionalmente
crea un BORRADOR de nota en Wix con el ranking para publicarlo.

Pensado para dispararse el 1° de cada mes desde cron-job.org con:
    python main.py --corresponsales-ranking            (mes anterior)
    python main.py --corresponsales-ranking --mes 2026-07

PUNTAJE (todo configurable por .env; ver el bloque «Puntaje» más abajo):
    interacciones = likes + 2*comentarios + 3*compartidas + 2*guardados   (por red)
    indice_red    = 0.5 * (interacciones / mediana_del_mes_en_esa_red)
                  + 0.5 * (tasa          / mediana_de_la_tasa_en_esa_red)
    indice_nota   = promedio de las redes donde la nota REALMENTE salió
    puntos        = promedio(indice_nota) * (1 + log2(1 + cantidad_de_notas)) * 100
PREMIO: uno solo, $50.000 para el 1er puesto (RANKING_PREMIOS).
"""
import json
import math
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

from metricas import es_publicada, ventana_horas
from platforms import wix
from utils.config import get
from utils.logger import get_logger

logger = get_logger("ranking")

LEDGER = Path(__file__).parent / ".videos_contabilidad.json"
LOGO = Path(__file__).parent / "logo.png"
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
         "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


# ── Config ────────────────────────────────────────────────────────────────────
def _mes_anterior(hoy: date) -> str:
    y, m = (hoy.year - 1, 12) if hoy.month == 1 else (hoy.year, hoy.month - 1)
    return f"{y:04d}-{m:02d}"


def _premios() -> list[int]:
    """Premios por puesto, en orden. UN solo premio de $50.000 para el 1° (pedido
    2026-08-31). Configurable: `RANKING_PREMIOS=50000,25000` daría premio al 1° y al 2°.
    El podio se sigue mostrando completo aunque premie a uno solo."""
    raw = get("RANKING_PREMIOS") or "50000"
    return [int(x.strip()) for x in raw.split(",") if x.strip().lstrip("-").isdigit()]


def _wix_borrador() -> bool:
    return (get("RANKING_WIX_BORRADOR") or "1").strip().lower() not in ("0", "false", "no", "off")


def _pesos(n: int) -> str:
    return "$" + f"{int(n):,}".replace(",", ".")


def _ventana() -> float:
    """Edad a la que se mide cada reel (la define `metricas.py`; se muestra en el mail)."""
    return ventana_horas()


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


def _parece_nombre(n: str) -> bool:
    """Un nombre de persona: corto, de una línea y de pocas palabras.

    Hace falta porque en el registro hay filas donde `corresponsal_nombre` guardó el TEXTO
    ENTERO del mensaje de WhatsApp (200+ caracteres con saltos de línea y hashtags). Si eso
    se toma como nombre, aparece un «corresponsal» fantasma por cada mensaje raro."""
    return bool(n) and len(n) <= 60 and "\n" not in n and len(n.split()) <= 5


def _identidad(filas: list[dict]) -> dict:
    """{clave_de_identidad: nombre_para_mostrar} agrupando por CELULAR.

    El celular es la identidad REAL del corresponsal; el nombre lo escribe él y llega de mil
    formas. En agosto de 2026, una sola persona figuraba como «Matias zucchi», «Matias
    Zucchi» y dos veces con el texto del mensaje entero: agrupando por nombre, sus 73 notas
    se partían en cuatro «colaboradores» distintos y el ranking quedaba falseado."""
    por_clave: dict[str, list[str]] = {}
    for f in filas:
        clave = (f.get("corresponsal_celular") or "").strip() or \
                " ".join((f.get("corresponsal_nombre") or "—").split()).lower()
        nombre = " ".join((f.get("corresponsal_nombre") or "").split())
        por_clave.setdefault(clave, []).append(nombre)
    salida = {}
    for clave, nombres in por_clave.items():
        candidatos = [n for n in nombres if _parece_nombre(n)]
        if candidatos:
            # El más COMPLETO (nombre + apellido); a igual cantidad de palabras, el más
            # repetido. Al revés quedaba «Claudia» —escrito 5 veces— en vez de «Claudia
            # Roger», y este nombre se publica en la web y va en el premio.
            mejor = max(set(candidatos), key=lambda n: (len(n.split()), candidatos.count(n)))
            salida[clave] = mejor.title()
        else:
            salida[clave] = f"Corresponsal {clave[-4:]}"
    return salida


def _clave_de(fila: dict) -> str:
    return (fila.get("corresponsal_celular") or "").strip() or \
        " ".join((fila.get("corresponsal_nombre") or "—").split()).lower()


def _filas_del_mes(rows: list[dict], mes: str) -> list[dict]:
    """Notas de CORRESPONSALES publicadas en el mes (por fecha_recibido)."""
    return [r for r in rows
            if (r.get("fecha_recibido", "") or "")[:7] == mes
            and r.get("corresponsal_nombre")
            and es_publicada(r.get("estado"))]


# ── Puntaje ───────────────────────────────────────────────────────────────────
# Cómo se puntúa (2026-08-31). El puntaje viejo era `vistas + interacciones×10`, y en la
# práctica las vistas eran el ~96% del total: medía a quién le dio más alcance el algoritmo,
# no quién trajo mejor material. Además sumaba números de redes distintas como si fueran
# comparables. Ahora:
#   1. Cada interacción pesa según lo que CUESTA hacerla: compartir > guardar/comentar > like.
#   2. Cada red se compara CONTRA SÍ MISMA (la mediana del mes en esa red), así una red con
#      más público no le gana a otra por tamaño.
#   3. Se mezcla VOLUMEN (interacciones, el impacto real) con TASA (interacciones ÷ alcance,
#      la calidad), mitad y mitad: ni premia solo al que tuvo suerte con el alcance ni solo
#      al que llegó a poca gente pero muy fiel.
#   4. Se promedian SOLO las redes donde el video salió: si falló Instagram, no penaliza.
REDES = ("facebook", "instagram", "youtube", "web", "tiktok")


def _peso(nombre: str, default: float) -> float:
    try:
        return float(get(nombre) or default)
    except ValueError:
        return default


def _interacciones(m: dict) -> float:
    """Interacciones ponderadas de una red. Compartir es la señal más fuerte de que el
    material valió la pena; comentar y guardar cuestan más que un like."""
    return (m.get("likes", 0) * _peso("RANKING_PESO_LIKE", 1)
            + m.get("comentarios", 0) * _peso("RANKING_PESO_COMENTARIO", 2)
            + m.get("compartidas", 0) * _peso("RANKING_PESO_COMPARTIDA", 3)
            + m.get("guardados", 0) * _peso("RANKING_PESO_GUARDADO", 2))


def _base_alcance(m: dict) -> int:
    """Denominador de la tasa: el alcance si la red lo da, si no las vistas."""
    return int(m.get("alcance") or 0) or int(m.get("vistas") or 0)


def _mediana(valores: list[float]) -> float:
    """Mediana de los valores mayores que cero. 0 si no hay ninguno."""
    v = sorted(x for x in valores if x > 0)
    if not v:
        return 0.0
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def puntuar(filas: list[dict]) -> dict:
    """Calcula el índice de cada nota (queda en `fila["_indice"]`) y devuelve las medianas.

    Trabaja sobre la FOTO de métricas (`fila["metricas"]`, tomada a edad fija por
    `metricas.py`). Una nota sin foto queda en índice 0."""
    crudo: dict[str, list[float]] = {r: [] for r in REDES}
    tasas: dict[str, list[float]] = {r: [] for r in REDES}
    for f in filas:
        foto = f.get("metricas") or {}
        for red in REDES:
            m = foto.get(red)
            if not m:
                continue
            inter = _interacciones(m)
            crudo[red].append(inter)
            base = _base_alcance(m)
            if base > 0:
                tasas[red].append(inter / base)

    med_inter = {r: _mediana(v) for r, v in crudo.items()}
    med_tasa = {r: _mediana(v) for r, v in tasas.items()}
    p_vol, p_tasa = _peso("RANKING_PESO_VOLUMEN", 0.5), _peso("RANKING_PESO_TASA", 0.5)
    # TOPE por nota. Probado con agosto de 2026: una nota que se hizo viral sacó índice 12,4
    # (doce veces la mediana) y sola le levantaba el promedio del mes a su autor. Un video que
    # explota SÍ vale más, pero no puede decidir el ranking entero: se lo reconoce hasta
    # `RANKING_TOPE_INDICE` veces la mediana y de ahí no pasa.
    tope = _peso("RANKING_TOPE_INDICE", 5)

    for f in filas:
        foto = f.get("metricas") or {}
        indices, detalle = [], {}
        for red in REDES:
            m = foto.get(red)
            if not m:
                continue
            inter, base = _interacciones(m), _base_alcance(m)
            i_vol = inter / med_inter[red] if med_inter[red] > 0 else 0.0
            tasa = (inter / base) if base > 0 else 0.0
            i_tasa = tasa / med_tasa[red] if med_tasa[red] > 0 else 0.0
            idx = min(p_vol * i_vol + p_tasa * i_tasa, tope)
            indices.append(idx)
            detalle[red] = {"interacciones": round(inter, 1), "alcance": base,
                            "tasa": round(tasa, 4), "indice": round(idx, 3)}
        f["_indice"] = round(sum(indices) / len(indices), 3) if indices else 0.0
        f["_detalle"] = detalle
        f["_redes"] = len(indices)
        f["_vistas"] = sum(int((foto.get(r) or {}).get("vistas") or 0) for r in REDES)
        f["_inter"] = round(sum(_interacciones(foto[r]) for r in REDES if foto.get(r)), 1)
    return {"interacciones": med_inter, "tasa": med_tasa}


# ── Excel ─────────────────────────────────────────────────────────────────────
def _armar_excel(ranking: list[dict], filas: list[dict], mes: str) -> Path:
    wb = openpyxl.Workbook()
    top = wb.active
    top.title = "Ranking"
    top.append(["Puesto", "Colaborador", "Celular", "Notas", "Vistas", "Interacciones",
                "Indice promedio", "Puntos", "Premio"])
    for c in top[1]:
        c.font = Font(bold=True)
    for r in ranking:
        top.append([r["puesto"], r["nombre"], r["celular"], r["notas"], r["vistas"],
                    r["interacciones"], r["promedio"], r["puntos"],
                    _pesos(r["premio"]) if r["premio"] else ""])
    for col, ancho in zip("ABCDEFGHI", (8, 28, 16, 8, 12, 14, 15, 12, 12)):
        top.column_dimensions[col].width = ancho

    det = wb.create_sheet("Detalle por nota")
    det.append(["Fecha", "Colaborador", "Título", "Redes", "Vistas totales",
                "Interacciones", "Indice", "Link"])
    for c in det[1]:
        c.font = Font(bold=True)
    for f in sorted(filas, key=lambda x: x.get("_indice", 0), reverse=True):
        det.append([(f.get("fecha_recibido", "") or "")[:10], f.get("_nombre", ""),
                    f.get("titulo", ""), f.get("_redes", 0), f.get("_vistas", 0),
                    f.get("_inter", 0), f.get("_indice", 0), f.get("post_url", "")])
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
                  f"<td style='padding:6px 10px;text-align:right'>{r['promedio']}</td>"
                  f"<td style='padding:6px 10px;text-align:right'>{r['puntos']:,}</td>".replace(",", ".") +
                  f"<td style='padding:6px 10px;text-align:right'>{pr}</td></tr>")
    return (
        "<table style='border-collapse:collapse;width:100%;font-family:Arial;font-size:14px'>"
        "<tr style='background:#e2620c;color:#fff'>"
        "<th style='padding:8px 10px;text-align:left'>Puesto</th>"
        "<th style='padding:8px 10px;text-align:left'>Colaborador</th>"
        "<th style='padding:8px 10px'>Notas</th><th style='padding:8px 10px'>Vistas</th>"
        "<th style='padding:8px 10px'>Interac.</th><th style='padding:8px 10px'>Índice</th>"
        "<th style='padding:8px 10px'>Puntos</th>"
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

    # 1) Red de seguridad: si alguna nota se quedó sin su foto de métricas (porque el job
    # diario no corrió), se toma AHORA. Es menos justo —se mide más vieja— pero mucho mejor
    # que dejarla en cero. Queda registrado con qué edad se midió cada una.
    import metricas as met
    sin_foto = [f for f in filas if not f.get("metricas")]
    if sin_foto:
        logger.warning(f"{len(sin_foto)} nota(s) sin foto de métricas; las mido ahora "
                       f"(más viejas que la ventana de {met.ventana_horas():.0f} h).")
        for i, f in enumerate(sin_foto, 1):
            f["metricas"] = met.capturar(f)
            # Con muchas notas esto son cientos de llamadas a las APIs y tarda: sin avance
            # parece colgado. Se informa cada 10 para no llenar el log.
            if i % 10 == 0 or i == len(sin_foto):
                logger.info(f"  medidas {i}/{len(sin_foto)}…")

    # 2) Índice por nota, normalizado por red contra la mediana del mes.
    medianas = puntuar(filas)
    for f in filas:
        f["ranking_metrics"] = {"mes": mes, "indice": f["_indice"], "redes": f["_redes"],
                                "vistas": f["_vistas"], "interacciones": f["_inter"],
                                "detalle": f["_detalle"],
                                "calculado": datetime.now().isoformat(timespec="seconds")}
    if not dry_run:
        _guardar_ledger(rows)

    # 3) Agregado por colaborador.
    nombres = _identidad(filas)
    por_colab: dict[str, dict] = {}
    for f in filas:
        clave = _clave_de(f)
        f["_nombre"] = nombres[clave]          # nombre limpio, también para el Excel
        d = por_colab.setdefault(clave, {"nombre": nombres[clave],
                                         "celular": f.get("corresponsal_celular", ""),
                                         "notas": 0, "vistas": 0, "interacciones": 0,
                                         "indices": []})
        d["celular"] = f.get("corresponsal_celular") or d["celular"]
        d["notas"] += 1
        d["vistas"] += f["_vistas"]
        d["interacciones"] += f["_inter"]
        d["indices"].append(f["_indice"])

    # 4) Puntaje final. La CALIDAD manda (el promedio de los índices) y la CONSTANCIA suma
    # con rendimientos decrecientes: `log2(1+n)` hace que 2 notas valgan más que 1, pero que
    # 20 no valgan 20 veces. Así no gana el que inunda ni el que tuvo un solo video con suerte.
    ranking = list(por_colab.values())
    for d in ranking:
        promedio = sum(d["indices"]) / len(d["indices"])
        d["promedio"] = round(promedio, 3)
        d["puntos"] = round(promedio * (1 + math.log2(1 + d["notas"])) * 100, 1)
        d["interacciones"] = round(d["interacciones"], 1)
    ranking.sort(key=lambda d: (d["puntos"], d["interacciones"]), reverse=True)
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
        f"<p>Ganador del mes:</p><ul style='font-size:16px'>{podio or '<li>—</li>'}</ul>"
        f"{_tabla_html(ranking)}"
        f"<p style='color:#777;font-size:13px;margin-top:14px'><b>Cómo se calcula:</b> cada reel "
        f"se mide a las {int(_ventana())} horas de publicado (misma vara para todos). En cada red "
        f"se cuentan las interacciones dando más peso a lo que más cuesta: compartir vale 3, "
        f"comentar y guardar 2, un like 1. El resultado se compara contra la mediana del mes "
        f"<i>en esa misma red</i>, para que una red con más público no le gane a otra por tamaño, "
        f"y se promedian solo las redes donde el video salió. El puntaje final combina la calidad "
        f"promedio con la constancia (más notas suman, con rendimientos decrecientes). "
        f"Detalle nota por nota en el Excel adjunto.</p>"
        f"{intro_wix}</div>")
    _enviar_mail(f"🏆 Ranking Corresponsales — {_mes_largo(mes)}", html, resumen_txt, xlsx)
    logger.info(f"Ranking {mes} listo. Podio: "
                + " | ".join(f"{d['puesto']}° {d['nombre']}" for d in ganadores))
    logger.info("=== Ranking de corresponsales: fin ===")
