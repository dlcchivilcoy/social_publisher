"""Publicidades PROGRAMADAS — la parte de Facebook e Instagram.

La WEB no pasa por acá y no necesita ningún reloj. Cada aviso de `avisos.json`
lleva sus fechas «desde» y «hasta», y la web sola decide si lo muestra
(ver `diario_web/src/lib/avisos.js`): la portada se arma en cada visita y el
resto de las páginas se rehace cada 5 minutos. Nadie tiene que commitear nada
para que una campaña entre o salga.

Lo que SÍ necesita un reloj es el posteo en las redes: una publicación de
Instagram no se puede dejar programada desde afuera, hay que estar ahí a esa
hora. Por eso el editor deja acá una cola y la nube la mira cada tanto:

    python main.py --publicidades-programadas

El archivo de la cola es `state/.publicidades_programadas.json` y lo commitea la
propia nube al terminar (el workflow ya hace `git add -f state/`).

La fecha de baja es un FRENO, no un borrado: cuando llega, la campaña deja de
estar activa y no se postea nada más. Lo que ya salió en Facebook e Instagram se
queda donde está, como cualquier publicación vieja — nadie borra posteos.

El freno agarra de los dos lados:
  - si la campaña ya salió, se cierra y no vuelve a salir;
  - si por lo que fuera NO llegó a salir y la fecha de baja ya pasó, se marca
    vencida y NO se publica tarde. Una campaña que se vendió para el 20 no sirve
    el 25.

El archivo que se subió se saca aparte, con «Limpiar vencidas» del editor, que es
lo único que puede tocar el repositorio de la web.
"""
from __future__ import annotations

import json
import smtplib
import ssl
import subprocess
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

import requests

from utils.config import get
from utils.logger import get_logger

logger = get_logger("publicidades")

AR = timezone(timedelta(hours=-3))          # Argentina, sin horario de verano
RUTA = Path(__file__).parent / "state" / ".publicidades_programadas.json"
MAX_INTENTOS = 3                            # después de esto avisa y no insiste más
FORMATOS = ("feed", "reel", "historia")     # cómo puede salir una campaña en las redes
MAX_FOTOS = 10                              # el tope del carrusel de Instagram
DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
# Cuánto puede llegar tarde un horario y salir igual. La nube mira la cola cada 15
# minutos; si una corrida falla, la siguiente todavía alcanza a publicar. Pasado este
# rato el horario se da por perdido: un aviso de las 9 no sirve a las 13.
TOLERANCIA_MIN = 90


# ── la agenda semanal ─────────────────────────────────────────────────────────
def _agenda_valida(agenda) -> dict | None:
    """Normaliza la agenda de una campaña que se repite. None = no se repite.

    Formato: `{"dias": [0..6], "horas": ["09:00", "18:30"]}`, con 0 = lunes. Hace falta
    al menos un día y un horario; si falta alguno de los dos no hay nada que repetir y
    se trata como campaña de una sola salida.
    """
    if not isinstance(agenda, dict):
        return None
    dias = sorted({int(d) for d in (agenda.get("dias") or []) if 0 <= int(d) <= 6})
    horas = []
    for h in agenda.get("horas") or []:
        h = str(h).strip()
        try:
            hh, mm = int(h[:2]), int(h[3:5])
        except (ValueError, IndexError):
            continue
        if 0 <= hh <= 23 and 0 <= mm <= 59:
            horas.append(f"{hh:02d}:{mm:02d}")
    horas = sorted(set(horas))
    return {"dias": dias, "horas": horas} if dias and horas else None


def _slot_de(agenda: dict, momento: datetime) -> datetime | None:
    """El horario de la agenda que corresponde publicar AHORA, o None.

    Es el más reciente que ya pasó y todavía entra en la tolerancia. Se miran hoy y
    ayer porque un horario de las 23:50 le toca a la corrida de las 00:05, que ya es
    otro día.
    """
    limite = momento - timedelta(minutes=TOLERANCIA_MIN)
    mejor = None
    for atras in (0, 1):
        dia = momento - timedelta(days=atras)
        if dia.weekday() not in agenda["dias"]:
            continue
        for h in agenda["horas"]:
            slot = dia.replace(hour=int(h[:2]), minute=int(h[3:5]),
                               second=0, microsecond=0)
            if limite <= slot <= momento and (mejor is None or slot > mejor):
                mejor = slot
    return mejor


def describir_agenda(agenda) -> str:
    """La agenda en castellano, para mostrarla en el editor y en los mails."""
    a = _agenda_valida(agenda)
    if not a:
        return "una sola vez"
    if len(a["dias"]) == 7:
        dias = "todos los días"
    elif a["dias"] == [0, 1, 2, 3, 4]:
        dias = "de lunes a viernes"
    else:
        nombres = [DIAS[d] for d in a["dias"]]
        dias = ", ".join(nombres[:-1]) + " y " + nombres[-1] if len(nombres) > 1 else nombres[0]
    return f"{dias} a las {', '.join(a['horas'])}"


# ── la cola ───────────────────────────────────────────────────────────────────
def ahora() -> datetime:
    return datetime.now(AR)


def _fecha(valor) -> datetime | None:
    if not valor:
        return None
    try:
        d = datetime.fromisoformat(str(valor))
    except ValueError:
        logger.warning(f"Fecha ilegible en la cola de publicidades: {valor!r}")
        return None
    return d if d.tzinfo else d.replace(tzinfo=AR)


def leer() -> list[dict]:
    try:
        return json.loads(RUTA.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"No pude leer la cola de publicidades: {e}")
        return []


def guardar(trabajos: list[dict]) -> None:
    RUTA.parent.mkdir(parents=True, exist_ok=True)
    RUTA.write_text(json.dumps(trabajos, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


def agregar(nombre: str, tipo: str, texto: str = "", *,
            urls: list[str] | None = None, url: str = "",
            cuando: str = "", destinos: list[str] | None = None,
            baja: str | None = None, desde: str | None = None,
            orientacion: str = "", formatos: list[str] | None = None,
            agenda: dict | None = None) -> dict:
    """Mete una campaña en la cola. Las fechas van en ISO con huso.

    `urls` son las piezas ya subidas a la web (hasta 10 fotos, o UN video). `formatos`
    dice cómo sale: «feed» (publicación o carrusel), «reel», «historia» — se pueden
    combinar.

    Hay dos maneras de programarla y son excluyentes:
      - **una sola salida**: `cuando` con la fecha y hora exactas;
      - **repetida**: `agenda` con los días y horarios de la semana, entre `desde` y
        `baja`. La campaña queda viva y sale en cada horario hasta que la frena `baja`.
    """
    if tipo not in ("foto", "video"):
        raise ValueError("El tipo tiene que ser «foto» o «video».")
    destinos = [d for d in (destinos or []) if d in ("facebook", "instagram")]
    if not destinos:
        raise ValueError("Elegí al menos una red (Facebook o Instagram).")

    piezas = [u for u in (urls or ([url] if url else [])) if u]
    if not piezas:
        raise ValueError("No hay ninguna pieza para publicar.")
    if tipo == "video" and len(piezas) > 1:
        raise ValueError("Un video por campaña. Varias piezas solo se admiten en fotos.")
    piezas = piezas[:MAX_FOTOS]

    formatos = [f for f in (formatos or []) if f in FORMATOS]
    if not formatos:
        raise ValueError("Elegí al menos un formato: publicación, reel o historia.")

    agenda = _agenda_valida(agenda)
    if not agenda and not _fecha(cuando):
        raise ValueError("Falta cuándo sale: una fecha y hora, o los días y horarios "
                         "de la semana.")

    trabajo = {
        "id": uuid.uuid4().hex[:12],
        "nombre": nombre,
        "tipo": tipo,
        "urls": piezas,
        "texto": texto or "",
        "cuando": cuando or "",
        "destinos": destinos,
        "formatos": formatos,
        "agenda": agenda,                # None = sale una sola vez
        "desde": desde,                  # solo para las repetidas
        "baja": baja,
        "orientacion": orientacion,      # horizontal / vertical / cuadrada
        "estado": "activa" if agenda else "pendiente",
        "creado": ahora().isoformat(timespec="minutes"),
        "intentos": 0,
        "resultado": {},
        "salidas": [],                   # historial de las repetidas
        "ultima_salida": "",
        "ultimo_error": "",
    }
    trabajos = leer()
    trabajos.append(trabajo)
    guardar(trabajos)
    return trabajo


def quitar(id_: str) -> bool:
    trabajos = leer()
    quedan = [t for t in trabajos if t.get("id") != id_]
    if len(quedan) == len(trabajos):
        return False
    guardar(quedan)
    return True


# ── publicar ──────────────────────────────────────────────────────────────────
def _bajar(url: str) -> Path:
    """Trae el archivo de la web a un temporal. FB e IG necesitan el archivo local."""
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    sufijo = Path(url.split("?")[0]).suffix or ".jpg"
    tmp = Path(tempfile.mkdtemp(prefix="publi_")) / f"pieza{sufijo}"
    tmp.write_bytes(r.content)
    return tmp


def _id(res) -> str:
    if isinstance(res, dict):
        return str(res.get("id") or res.get("post_id") or "")
    return ""


def _urls_de(trabajo: dict) -> list[str]:
    """Las piezas de la campaña. Los trabajos viejos traían una sola, en `url`."""
    urls = trabajo.get("urls")
    if isinstance(urls, list) and urls:
        return [u for u in urls if u]
    return [trabajo["url"]] if trabajo.get("url") else []


def _formatos_de(trabajo: dict) -> list[str]:
    """Los formatos de la campaña. Los trabajos viejos no elegían: salían siempre como
    publicación + historia, así que esos conservan exactamente ese comportamiento."""
    elegidos = [f for f in (trabajo.get("formatos") or []) if f in FORMATOS]
    return elegidos or ["feed", "historia"]


def _intentar(ids: dict, fallas: list[str], clave: str, etiqueta: str, hacer) -> None:
    """Corre UN posteo y anota lo que pase. Cada formato va por separado a propósito:
    que falle la historia no tiene por qué dejar al anunciante sin el carrusel."""
    try:
        ids[clave] = _id(hacer())
    except Exception as e:                                       # noqa: BLE001
        fallas.append(f"{etiqueta}: {e}")
        logger.error(f"{etiqueta} falló: {e}")


def _publicar(trabajo: dict) -> tuple[dict, list[str]]:
    """Publica una campaña en todos sus destinos y formatos. Devuelve (ids, fallas)."""
    import avisos_piezas
    from platforms import facebook, instagram
    from utils.video_host import upload_reel

    tipo = trabajo.get("tipo")
    texto = trabajo.get("texto") or ""
    destinos = trabajo.get("destinos") or []
    formatos = _formatos_de(trabajo)
    ids: dict = {}
    fallas: list[str] = []

    piezas = [_bajar(u) for u in _urls_de(trabajo)]
    if not piezas:
        return {}, ["La campaña no tiene ninguna pieza."]
    taller = piezas[0].parent

    # ── se arma UNA sola vez lo que los formatos vayan a necesitar ──
    carrusel = None      # las fotos emparejadas, para el feed
    video = None         # el mp4: el del anunciante, o el pegado de las fotos
    quiere_video = ("reel" in formatos) or ("historia" in formatos and len(piezas) > 1)
    if tipo == "video":
        video = piezas[0]
    else:
        if "feed" in formatos and len(piezas) > 1:
            carrusel = avisos_piezas.normalizar_carrusel(piezas, taller / "carrusel")
        if quiere_video:
            video = avisos_piezas.fotos_a_video(piezas, taller / "aviso.mp4")

    # Instagram pide el video por URL pública, no por archivo. Se sube una sola vez
    # aunque lo usen el reel y la historia.
    cache_url: dict = {}

    def url_video() -> str:
        if "u" not in cache_url:
            cache_url["u"] = upload_reel(video)
        return cache_url["u"]

    if "facebook" in destinos:
        if "feed" in formatos:
            if tipo == "video":
                _intentar(ids, fallas, "fb_feed", "Facebook (video del muro)",
                          lambda: facebook.publish_video(texto, video, preferir_reel=False))
            elif carrusel:
                _intentar(ids, fallas, "fb_feed", "Facebook (carrusel)",
                          lambda: facebook.publish_multi(texto, carrusel))
            else:
                _intentar(ids, fallas, "fb_feed", "Facebook (publicación)",
                          lambda: facebook.publish(texto, piezas[0]))
        if "reel" in formatos and video:
            # El video pegado de las fotos SIEMPRE sale 9:16, así que va como reel. El
            # del anunciante puede ser apaisado, y como reel quedaría con bandas negras.
            apaisado = tipo == "video" and trabajo.get("orientacion") == "horizontal"
            _intentar(ids, fallas, "fb_reel", "Facebook (reel)",
                      lambda: facebook.publish_video(texto, video, preferir_reel=not apaisado))
        if "historia" in formatos:
            if video:
                _intentar(ids, fallas, "fb_historia", "Facebook (historia)",
                          lambda: facebook.publish_video_story(video))
            else:
                _intentar(ids, fallas, "fb_historia", "Facebook (historia)",
                          lambda: facebook.publish_story(piezas[0]))

    if "instagram" in destinos:
        # En Instagram un video del feed ES un reel: son el mismo posteo. Si pidieron
        # los dos, se publica UNO solo y no dos veces lo mismo.
        feed_ig = "feed" in formatos and not (tipo == "video" and "reel" in formatos)
        if feed_ig:
            if tipo == "video":
                _intentar(ids, fallas, "ig_feed", "Instagram (video)",
                          lambda: instagram.publish_reel(url_video(), texto))
            elif carrusel:
                _intentar(ids, fallas, "ig_feed", "Instagram (carrusel)",
                          lambda: instagram.publish_carousel(texto, carrusel))
            else:
                _intentar(ids, fallas, "ig_feed", "Instagram (publicación)",
                          lambda: instagram.publish(texto, piezas[0]))
        if "reel" in formatos and video:
            _intentar(ids, fallas, "ig_reel", "Instagram (reel)",
                      lambda: instagram.publish_reel(url_video(), texto))
        if "historia" in formatos:
            if video:
                _intentar(ids, fallas, "ig_historia", "Instagram (historia)",
                          lambda: instagram.publish_video_story(url_video()))
            else:
                _intentar(ids, fallas, "ig_historia", "Instagram (historia)",
                          lambda: instagram.publish_story(piezas[0]))

    return ids, fallas


# ── el runner ─────────────────────────────────────────────────────────────────
def _salir(t: dict, hoy: datetime, momento: str) -> dict:
    """Publica una campaña y anota el resultado. Devuelve el parte de la salida.

    ⚠️ El horario se marca como usado ANTES de saber si salió bien, y es a propósito:
    Instagram a veces devuelve error habiendo publicado (ver el falso 500 de
    `media_publish`). Reintentar el mismo horario duplicaría el posteo del anunciante.
    Un horario perdido se nota y se arregla; un aviso publicado dos veces, no.
    """
    ids, fallas = _publicar(t)
    t["resultado"] = {**(t.get("resultado") or {}), **ids}
    t["ultima_salida"] = momento
    t["salidas"] = (t.get("salidas") or [])[-19:] + [{
        "momento": momento, "ids": ids, "fallas": fallas,
    }]
    return {"ids": ids, "fallas": fallas}


def _tick_unica(t: dict, hoy: datetime, dry: bool) -> dict:
    """Campaña de UNA sola salida: el comportamiento de siempre."""
    parte = {"hechas": 0, "bajas": 0, "errores": 0, "avisos": []}
    estado = t.get("estado")
    cuando = _fecha(t.get("cuando"))
    baja = _fecha(t.get("baja"))

    # ── freno: venció antes de salir ──
    # Si la campaña terminaba antes de que llegara a publicarse, NO se publica tarde:
    # lo que se vendió para el 20 no sirve el 25.
    if estado == "pendiente" and baja and baja <= hoy:
        t["estado"] = "vencido"
        t["bajado_en"] = hoy.isoformat(timespec="minutes")
        parte["bajas"] += 1
        parte["avisos"].append(f"«{t.get('nombre')}» venció sin llegar a publicarse. "
                               f"No se postea tarde.")
        return parte

    # ── alta ──
    if estado == "pendiente" and cuando and cuando <= hoy:
        if dry:
            logger.info(f"[dry] publicaría «{t.get('nombre')}» en {t.get('destinos')}")
            return parte
        salida = _salir(t, hoy, hoy.isoformat(timespec="minutes"))
        if salida["fallas"]:
            t["intentos"] = int(t.get("intentos") or 0) + 1
            t["ultimo_error"] = " | ".join(salida["fallas"])
            if t["intentos"] >= MAX_INTENTOS:
                t["estado"] = "error"
                parte["errores"] += 1
                parte["avisos"].append(f"NO SALIÓ: «{t.get('nombre')}» falló "
                                       f"{MAX_INTENTOS} veces. {t['ultimo_error']}")
            else:
                parte["avisos"].append(f"Intento fallido ({t['intentos']}/{MAX_INTENTOS}) "
                                       f"en «{t.get('nombre')}»: {t['ultimo_error']}")
                t["ultima_salida"] = ""      # la única puede reintentarse; la agenda no
        if salida["ids"] and not salida["fallas"]:
            t["estado"] = "publicado"
            t["publicado_en"] = hoy.isoformat(timespec="minutes")
            parte["hechas"] += 1
            logger.info(f"Publicidad «{t.get('nombre')}» publicada: {salida['ids']}")
        elif salida["ids"]:
            # Salió en algunas redes y en otras no: queda publicada igual, porque
            # reintentar duplicaría lo que sí salió.
            t["estado"] = "publicado"
            t["publicado_en"] = hoy.isoformat(timespec="minutes")
            parte["hechas"] += 1
        return parte

    # ── freno: la campaña terminó ──
    # De la web sale sola por la fecha «hasta» del aviso. Lo que ya se posteó en
    # Facebook e Instagram SE QUEDA: no se borra nada.
    if estado == "publicado" and baja and baja <= hoy:
        t["estado"] = "bajado"
        t["bajado_en"] = hoy.isoformat(timespec="minutes")
        parte["bajas"] += 1
        parte["avisos"].append(f"Terminó la campaña de «{t.get('nombre')}». Ya salió de la "
                               f"web. Los posteos de las redes quedan como están. Para sacar "
                               f"el archivo del sistema: «Limpiar vencidas» en el editor.")
    return parte


def _tick_agenda(t: dict, hoy: datetime, dry: bool, agenda: dict) -> dict:
    """Campaña que se REPITE: sale en cada horario de la agenda hasta que la frena
    la fecha de baja."""
    parte = {"hechas": 0, "bajas": 0, "errores": 0, "avisos": []}
    estado = t.get("estado")
    if estado in ("bajado", "vencido", "error"):
        return parte

    baja = _fecha(t.get("baja"))
    desde = _fecha(t.get("desde"))

    # ── freno: la campaña terminó ──
    if baja and baja <= hoy:
        t["estado"] = "bajado"
        t["bajado_en"] = hoy.isoformat(timespec="minutes")
        parte["bajas"] += 1
        salidas = len(t.get("salidas") or [])
        parte["avisos"].append(f"Terminó la campaña de «{t.get('nombre')}» "
                               f"({salidas} salida/s). Deja de postearse y sale de la web. "
                               f"Lo ya publicado queda como está.")
        return parte

    if desde and hoy < desde:
        return parte

    slot = _slot_de(agenda, hoy)
    if not slot:
        return parte
    momento = slot.isoformat(timespec="minutes")
    if (t.get("ultima_salida") or "") >= momento:
        return parte                      # ese horario ya salió

    if dry:
        logger.info(f"[dry] «{t.get('nombre')}» saldría por el horario {momento}")
        return parte

    salida = _salir(t, hoy, momento)
    if salida["ids"]:
        t["intentos"] = 0                 # una salida buena limpia la racha
        t["ultimo_error"] = ""
        parte["hechas"] += 1
        logger.info(f"Publicidad «{t.get('nombre')}» ({momento}): {salida['ids']}")
    if salida["fallas"]:
        t["ultimo_error"] = " | ".join(salida["fallas"])
        if not salida["ids"]:
            # Solo cuentan las veces que no salió NADA, y seguidas: una campaña de tres
            # meses no se cancela porque se cayó Instagram tres martes sueltos.
            t["intentos"] = int(t.get("intentos") or 0) + 1
            if t["intentos"] >= MAX_INTENTOS:
                t["estado"] = "error"
                parte["errores"] += 1
                parte["avisos"].append(
                    f"FRENADA: «{t.get('nombre')}» falló {MAX_INTENTOS} horarios seguidos "
                    f"y no sale más hasta que la revises. {t['ultimo_error']}")
                return parte
        parte["avisos"].append(f"«{t.get('nombre')}» ({momento}): {t['ultimo_error']}")
    return parte


def correr(dry: bool = False) -> dict:
    """Publica lo que ya venció y da de baja lo que corresponda. Lo llama la nube."""
    trabajos = leer()
    if not trabajos:
        logger.info("No hay publicidades programadas.")
        return {"publicadas": 0, "bajas": 0, "errores": 0}

    hoy = ahora()
    hechas = bajas = errores = 0
    avisos: list[str] = []

    for t in trabajos:
        agenda = _agenda_valida(t.get("agenda"))
        parte = (_tick_agenda(t, hoy, dry, agenda) if agenda
                 else _tick_unica(t, hoy, dry))
        hechas += parte["hechas"]
        bajas += parte["bajas"]
        errores += parte["errores"]
        avisos += parte["avisos"]

    if not dry:
        guardar(trabajos)
    if avisos:
        _avisar("Publicidades programadas", "\n\n".join(avisos))

    logger.info(f"Publicidades: {hechas} publicada(s), {bajas} baja(s), {errores} con error.")
    return {"publicadas": hechas, "bajas": bajas, "errores": errores}


# ── aviso por mail (best-effort, mismo patrón que el resto del bot) ────────────
def _avisar(asunto: str, cuerpo: str) -> bool:
    remitente = get("MAIL_FROM")
    password = get("MAIL_APP_PASSWORD")
    destino = (get("PUBLICIDADES_NOTIFY_EMAIL") or get("VIDEOS_NOTIFY_EMAIL")
               or get("MAIL_FROM") or "").strip()
    if not remitente or not password or not destino:
        logger.warning("Sin credenciales de mail: no se manda el aviso de publicidades.")
        return False
    msg = EmailMessage()
    msg["From"] = formataddr((get("MAIL_FROM_NAME") or "Diario La Campaña", remitente))
    msg["To"] = destino
    msg["Subject"] = asunto
    msg.set_content(cuerpo)
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(get("SMTP_HOST") or "smtp.gmail.com",
                          int(get("SMTP_PORT") or 587), timeout=60) as server:
            server.starttls(context=ctx)
            server.login(remitente, password)
            server.send_message(msg)
        return True
    except Exception as e:                                       # noqa: BLE001
        logger.error(f"No se pudo avisar por mail: {e}")
        return False


# ── git (lo usa el editor de escritorio para que la nube vea la cola) ─────────
def publicar_cola() -> None:
    """Commitea y pushea la cola. Solo desde la PC: en la nube lo hace el workflow."""
    raiz = Path(__file__).parent
    rel = "state/.publicidades_programadas.json"

    def git(*args, timeout=180):
        r = subprocess.run(["git", "-C", str(raiz), *args],
                           capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout or "").strip() or f"git {args[0]} falló")
        return (r.stdout or "").strip()

    git("add", "-f", "--", rel)
    if subprocess.run(["git", "-C", str(raiz), "diff", "--cached", "--quiet"]).returncode == 0:
        return                                   # no cambió nada
    git("commit", "-m", "Publicidades programadas: cola actualizada [skip ci]")
    try:
        git("push")
    except RuntimeError as e:
        if any(k in str(e).lower()
               for k in ("rejected", "fetch first", "non-fast-forward", "behind")):
            git("pull", "--rebase", "--autostash")
            git("push")
        else:
            raise
