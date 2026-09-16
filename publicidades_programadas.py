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

ADVERTENCIA: INSTAGRAM NO SE PUEDE BORRAR POR API. Su API no tiene endpoint para
eliminar una publicación ya hecha. Si la campaña pidió borrar en redes, se borra
lo de Facebook y el mail avisa que lo de Instagram hay que sacarlo a mano.
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


def agregar(nombre: str, tipo: str, url: str, texto: str, cuando: str,
            destinos: list[str], baja: str | None = None,
            borrar_en_redes: bool = False) -> dict:
    """Mete una campaña en la cola. `cuando` y `baja` van en ISO con huso."""
    if tipo not in ("foto", "video"):
        raise ValueError("El tipo tiene que ser «foto» o «video».")
    destinos = [d for d in (destinos or []) if d in ("facebook", "instagram")]
    if not destinos:
        raise ValueError("Elegí al menos una red (Facebook o Instagram).")
    if not _fecha(cuando):
        raise ValueError("La fecha de publicación no se entiende.")
    trabajo = {
        "id": uuid.uuid4().hex[:12],
        "nombre": nombre,
        "tipo": tipo,
        "url": url,
        "texto": texto or "",
        "cuando": cuando,
        "destinos": destinos,
        "baja": baja,
        "borrar_en_redes": bool(borrar_en_redes),
        "estado": "pendiente",
        "creado": ahora().isoformat(timespec="minutes"),
        "intentos": 0,
        "resultado": {},
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


def _publicar(trabajo: dict) -> tuple[dict, list[str]]:
    """Publica una campaña. Devuelve (ids, fallas).

    Cada red va en su propio try: que Instagram falle no tiene por qué dejar al
    anunciante sin el posteo de Facebook.
    """
    from platforms import facebook, instagram
    from utils.video_host import upload_reel

    tipo = trabajo.get("tipo")
    texto = trabajo.get("texto") or ""
    destinos = trabajo.get("destinos") or []
    ids: dict = {}
    fallas: list[str] = []

    pieza = _bajar(trabajo["url"])

    if "facebook" in destinos:
        try:
            if tipo == "foto":
                ids["fb_post"] = _id(facebook.publish(texto, pieza))
                ids["fb_historia"] = _id(facebook.publish_story(pieza))
            else:
                ids["fb_post"] = _id(facebook.publish_video(texto, pieza))
                ids["fb_historia"] = _id(facebook.publish_video_story(pieza))
        except Exception as e:                                   # noqa: BLE001
            fallas.append(f"Facebook: {e}")
            logger.error(f"Publicidad «{trabajo.get('nombre')}» falló en Facebook: {e}")

    if "instagram" in destinos:
        try:
            if tipo == "foto":
                ids["ig_post"] = _id(instagram.publish(texto, pieza))
                ids["ig_historia"] = _id(instagram.publish_story(pieza))
            else:
                # El reel de Instagram se pide por URL pública, no por archivo.
                url_reel = upload_reel(pieza)
                ids["ig_post"] = _id(instagram.publish_reel(url_reel, texto))
                ids["ig_historia"] = _id(instagram.publish_video_story(url_reel))
        except Exception as e:                                   # noqa: BLE001
            fallas.append(f"Instagram: {e}")
            logger.error(f"Publicidad «{trabajo.get('nombre')}» falló en Instagram: {e}")

    return ids, fallas


def _dar_de_baja(trabajo: dict) -> tuple[list[str], list[str]]:
    """Borra de las redes lo que se pueda. Devuelve (borrados, a_mano)."""
    from platforms import facebook

    ids = trabajo.get("resultado") or {}
    borrados: list[str] = []
    a_mano: list[str] = []
    for clave in ("fb_post", "fb_historia"):
        oid = ids.get(clave)
        if not oid:
            continue
        que = "posteo" if clave == "fb_post" else "historia"
        try:
            facebook.borrar(oid)
            borrados.append(f"Facebook ({que})")
        except Exception as e:                                   # noqa: BLE001
            a_mano.append(f"Facebook {que}: {e}")
    # Instagram NO tiene borrado por API. Se avisa y se hace a mano.
    if ids.get("ig_post") or ids.get("ig_historia"):
        a_mano.append("Instagram: su API no permite borrar; hay que sacarlo desde el celular")
    return borrados, a_mano


# ── el runner ─────────────────────────────────────────────────────────────────
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
        estado = t.get("estado")
        cuando = _fecha(t.get("cuando"))
        baja = _fecha(t.get("baja"))

        # ── alta ──
        if estado == "pendiente" and cuando and cuando <= hoy:
            if dry:
                logger.info(f"[dry] publicaría «{t.get('nombre')}» en {t.get('destinos')}")
                continue
            ids, fallas = _publicar(t)
            t["resultado"] = {**(t.get("resultado") or {}), **ids}
            if fallas:
                t["intentos"] = int(t.get("intentos") or 0) + 1
                t["ultimo_error"] = " | ".join(fallas)
                if t["intentos"] >= MAX_INTENTOS:
                    t["estado"] = "error"
                    errores += 1
                    avisos.append(f"NO SALIO: «{t.get('nombre')}» falló "
                                  f"{MAX_INTENTOS} veces. {t['ultimo_error']}")
                else:
                    avisos.append(f"Intento fallido ({t['intentos']}/{MAX_INTENTOS}) "
                                  f"en «{t.get('nombre')}»: {t['ultimo_error']}")
            elif ids:
                t["estado"] = "publicado"
                t["publicado_en"] = hoy.isoformat(timespec="minutes")
                hechas += 1
                logger.info(f"Publicidad «{t.get('nombre')}» publicada: {ids}")

        # ── baja ──
        elif estado == "publicado" and baja and baja <= hoy:
            if not t.get("borrar_en_redes"):
                # De la web sale sola por la fecha «hasta» del aviso.
                t["estado"] = "bajado"
                t["bajado_en"] = hoy.isoformat(timespec="minutes")
                bajas += 1
                continue
            if dry:
                logger.info(f"[dry] borraría de las redes «{t.get('nombre')}»")
                continue
            borrados, a_mano = _dar_de_baja(t)
            t["estado"] = "bajado"
            t["bajado_en"] = hoy.isoformat(timespec="minutes")
            t["baja_detalle"] = {"borrados": borrados, "a_mano": a_mano}
            bajas += 1
            if a_mano:
                avisos.append(f"Terminó «{t.get('nombre')}». Borrado: "
                              f"{', '.join(borrados) or 'nada'}. "
                              f"Queda a mano: {'; '.join(a_mano)}")

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
