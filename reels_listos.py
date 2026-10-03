"""Reels LISTOS — piezas terminadas AFUERA del bot (el chat de Codex) que se publican TAL CUAL.

Pedido del usuario 2026-10-03: el chat de Codex arma el reel, su portada y la descripción,
los deja en Drive y el bot los sube a las redes sin tocarles nada. Ni marca, ni placa, ni
texto: la estética la decide Codex y acá solo se publica.

EL BUZÓN es la carpeta `reels listos` en la raíz del Drive del diario (dlc.chivilcoy).
Cada publicación es UNA subcarpeta con nombre único (fecha y hora adelante):

    reels listos/2026-10-03_1830_sandalias/
        reel.mp4           el video terminado (puede faltar si la pieza es solo una imagen)
        portada.jpg        la portada del reel, o la imagen si no hay video (opcional)
        publicacion.json   el pedido. Se escribe ÚLTIMO.

publicacion.json:
    {
      "titulo":      "Sandalias para el Día de la Madre",    # mail, YouTube (opcional)
      "descripcion": "El texto del posteo, con sus hashtags",  # OBLIGATORIO
      "video":       "reel.mp4",
      "portada":     "portada.jpg",
      "redes":       ["instagram", "facebook"],              # + "youtube", "tiktok"
      "historias":   true,                                   # además, como historia
      "archivos":    {"reel.mp4": 18342211, "portada.jpg": 412330}
    }

`archivos` lleva el peso EXACTO en bytes. Drive para escritorio sube primero lo chico, así
que el pedido puede llegar antes que el video: mientras el peso no coincida la carpeta se
espera (hasta ESPERA_MAX_SEG). Nunca se publica un video a medio subir.

El disparo lo hace el propio Codex al terminar:
    gh workflow run publicador.yml --repo dlcchivilcoy/social_publisher --ref main \
        -f args="--reels-listos"

Al publicar, el bot deja `resultado.json` en la carpeta y la mueve a `reels listos/publicados`
(o a `reels listos/con error` si no salió en ninguna red). Codex lee ese resultado para
confirmar, y el buzón queda limpio. Llega además un mail con el estado por red.

Anti-repetición: `.reels_listos.json` (la nube lo guarda en `state/`) anota el id de cada
red APENAS sale. Si una corrida se corta a la mitad, la siguiente publica solo lo que faltó;
y si una carpeta con el mismo nombre vuelve a aparecer, lo que ya salió no se repite.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from html import escape as _hesc
from pathlib import Path

from utils.config import get
from utils.logger import get_logger

logger = get_logger("reels_listos")

AR = timezone(timedelta(hours=-3))
LEDGER = Path(__file__).parent / ".reels_listos.json"
PEDIDO = "publicacion.json"
RESULTADO = "resultado.json"
PUBLICADOS = "publicados"
CON_ERROR = "con error"
REDES = ("instagram", "facebook", "youtube", "tiktok")
REDES_DEFAULT = ["instagram", "facebook"]
EXT_VIDEO = (".mp4", ".mov", ".m4v")
EXT_IMAGEN = (".jpg", ".jpeg", ".png", ".webp")
ESPERA_MAX_SEG = 600        # cuánto se espera, como mucho, a que Drive termine de subir
ESPERA_PASO_SEG = 30
RECIENTE_MIN = 60           # una carpeta todavía sin pedido se espera solo si es reciente
SHORT_MAX_SEG = 180         # YouTube toma como Short lo vertical de hasta 3 minutos

NOMBRES = {
    "ig_reel": "Instagram (reel)", "fb_reel": "Facebook (reel)",
    "ig_post": "Instagram (publicación)", "fb_post": "Facebook (publicación)",
    "ig_historia": "Historia de Instagram", "fb_historia": "Historia de Facebook",
    "youtube": "YouTube Short", "tiktok": "TikTok",
}


def ahora() -> datetime:
    return datetime.now(AR)


# ── Drive (por rclone, el mismo remoto que usa el resto del publicador) ──────────
def _buzon() -> str:
    return (get("REELS_LISTOS_DRIVE") or "gdrive:reels listos").rstrip("/")


def _rclone(*args, timeout: int = 300) -> str:
    r = subprocess.run(["rclone", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"rclone {args[0]}: {(r.stderr or r.stdout).strip()[-400:]}")
    return r.stdout


def _fecha_rclone(valor: str) -> datetime | None:
    """El ModTime de `rclone lsjson` trae nanosegundos; fromisoformat acepta hasta micro."""
    if not valor:
        return None
    v = valor.replace("Z", "+00:00")
    v = re.sub(r"(\.\d{6})\d+", r"\1", v)
    try:
        return datetime.fromisoformat(v)
    except ValueError:
        return None


def _listar() -> dict[str, dict]:
    """Lo que espera en el buzón: {carpeta: {"archivos": {nombre: bytes}, "mod": datetime}}.
    `publicados` y `con error` no cuentan; un archivo suelto en la raíz tampoco.

    El buzón NO se crea desde acá a propósito: Drive admite dos carpetas con el mismo
    nombre, y si la nube creara una mientras Drive para escritorio sube la suya, cada lado
    miraría una distinta. La carpeta se crea una sola vez, desde la PC."""
    try:
        crudo = json.loads(_rclone("lsjson", "-R", "--max-depth", "2", _buzon()) or "[]")
    except RuntimeError as e:
        if "not found" in str(e).lower():
            logger.info(f"El buzón «{_buzon()}» todavía no existe en Drive.")
            return {}
        raise
    out: dict[str, dict] = {}
    for it in crudo:
        partes = str(it.get("Path", "")).split("/")
        if not partes[0] or partes[0] in (PUBLICADOS, CON_ERROR):
            continue
        if len(partes) == 1 and not it.get("IsDir"):
            continue
        c = out.setdefault(partes[0], {"archivos": {}, "mod": None})
        if len(partes) == 2 and not it.get("IsDir"):
            c["archivos"][partes[1]] = int(it.get("Size") or 0)
        mod = _fecha_rclone(it.get("ModTime", ""))
        if mod and (c["mod"] is None or mod > c["mod"]):
            c["mod"] = mod
    return out


def _leer_pedido(carpeta: str) -> dict:
    txt = _rclone("cat", f"{_buzon()}/{carpeta}/{PEDIDO}", timeout=120)
    datos = json.loads(txt.lstrip("﻿"))
    if not isinstance(datos, dict):
        raise ValueError("publicacion.json tiene que ser un objeto JSON.")
    return datos


def _archivar(carpeta: str, fila: dict, dry: bool = False) -> None:
    """Deja `resultado.json` en la carpeta y la saca del buzón. Si moverla falla, el
    pedido queda donde estaba, pero el registro ya sabe qué salió: no se repite nada."""
    destino = PUBLICADOS if fila.get("estado") != "con_error" else CON_ERROR
    if dry:
        logger.info(f"[dry-run] «{carpeta}» iría a «{destino}».")
        return
    resultado = {
        "estado": fila.get("estado"),
        "fecha": fila.get("publicado") or ahora().isoformat(timespec="seconds"),
        "canales": fila.get("canales", {}),
        "links": fila.get("links", {}),
        "error": fila.get("error", ""),
    }
    try:
        subprocess.run(["rclone", "rcat", f"{_buzon()}/{carpeta}/{RESULTADO}"],
                       input=json.dumps(resultado, ensure_ascii=False, indent=2).encode("utf-8"),
                       capture_output=True, timeout=120, check=True)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"No pude escribir {RESULTADO} en «{carpeta}»: {e}")
    nombre = carpeta
    try:
        ya = json.loads(_rclone("lsjson", "--dirs-only", f"{_buzon()}/{destino}") or "[]")
        if any(d.get("Path") == carpeta for d in ya):
            nombre = f"{carpeta}_{ahora():%H%M%S}"
    except Exception:  # noqa: BLE001 — la carpeta de destino todavía no existe
        pass
    try:
        _rclone("moveto", f"{_buzon()}/{carpeta}", f"{_buzon()}/{destino}/{nombre}", timeout=300)
        logger.info(f"«{carpeta}» → «{destino}/{nombre}».")
    except Exception as e:  # noqa: BLE001
        logger.error(f"No pude mover «{carpeta}» a «{destino}»: {e}")


# ── el registro anti-repetición ─────────────────────────────────────────────────
def _leer_ledger() -> dict:
    try:
        datos = json.loads(LEDGER.read_text(encoding="utf-8-sig"))
        return datos if isinstance(datos, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as e:
        logger.error(f"No pude leer {LEDGER.name}: {e}")
        return {}


def _guardar_ledger(ledger: dict) -> None:
    LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ── el pedido ───────────────────────────────────────────────────────────────────
def _normalizar(crudo: dict, archivos: dict) -> dict:
    """Valida el pedido y completa lo que falte. Un ValueError es un pedido mal armado:
    esperar no lo arregla, así que la carpeta va a «con error» con el motivo."""
    texto = str(crudo.get("descripcion") or "").strip()
    if not texto:
        raise ValueError("Falta la «descripcion» (el texto del posteo).")

    def _buscar(clave: str, extensiones: tuple) -> str:
        nombre = str(crudo.get(clave) or "").strip()
        if nombre:
            return nombre
        candidatos = [n for n in archivos if n.lower().endswith(extensiones)]
        return candidatos[0] if len(candidatos) == 1 else ""

    video = _buscar("video", EXT_VIDEO)
    portada = _buscar("portada", EXT_IMAGEN)
    if not video and not portada:
        raise ValueError("No hay nada para publicar: ni «video» ni «portada».")
    if video and not video.lower().endswith(EXT_VIDEO):
        raise ValueError(f"El video tiene que ser .mp4 o .mov (llegó «{video}»).")
    if portada and not portada.lower().endswith(EXT_IMAGEN):
        raise ValueError(f"La portada tiene que ser .jpg o .png (llegó «{portada}»).")

    pedidas = crudo.get("redes") or REDES_DEFAULT
    if isinstance(pedidas, str):
        pedidas = re.split(r"[,\s]+", pedidas)
    redes = []
    for r in pedidas:
        r = str(r).strip().lower()
        if r in REDES and r not in redes:
            redes.append(r)
        elif r:
            logger.warning(f"Red desconocida en el pedido: «{r}» (se ignora).")
    if not redes:
        raise ValueError(f"«redes» no trae ninguna red válida ({', '.join(REDES)}).")

    pesos = crudo.get("archivos") if isinstance(crudo.get("archivos"), dict) else {}
    pesos = {str(k): v for k, v in pesos.items()}
    for n in (video, portada):
        if n and n not in pesos:
            pesos[n] = None             # sin peso declarado: alcanza con que esté

    titulo = str(crudo.get("titulo") or "").strip() or texto.splitlines()[0].strip()[:90]
    return {"titulo": titulo, "descripcion": texto, "video": video, "portada": portada,
            "redes": redes, "historias": crudo.get("historias", True) is not False,
            "archivos": pesos}


def _completo(pedido: dict, archivos: dict) -> tuple[bool, str]:
    for nombre, peso in pedido["archivos"].items():
        if nombre not in archivos:
            return False, f"falta «{nombre}»"
        if peso not in (None, "") and int(archivos[nombre]) != int(peso):
            return False, f"«{nombre}» va {archivos[nombre]} de {peso} bytes"
    return True, ""


def _slug(s: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return s[:60] or "reel"


# ── publicar ────────────────────────────────────────────────────────────────────
def _id(res) -> str:
    if isinstance(res, dict):
        return str(res.get("id") or res.get("post_id") or res.get("publish_id") or "")
    return ""


def _paso(fila: dict, estado: dict, clave: str, hacer) -> dict | None:
    """Un posteo, con su anotación. Lo que ya salió (tiene id) no se vuelve a publicar."""
    ids = fila.setdefault("ids", {})
    if ids.get(clave):
        estado[clave] = "ok"
        return None
    try:
        res = hacer()
        ids[clave] = _id(res) or "ok"
        estado[clave] = "ok"
        logger.info(f"{NOMBRES.get(clave, clave)}: OK ({ids[clave]})")
        return res if isinstance(res, dict) else {}
    except Exception as e:  # noqa: BLE001 — cada red por separado: que falle una no frena al resto
        estado[clave] = f"falló: {e}"
        logger.error(f"{NOMBRES.get(clave, clave)} FALLÓ: {e}")
        return None


def _publicar_video(video: Path, portada: Path | None, pedido: dict, fila: dict,
                    estado: dict) -> None:
    import transcriber as tr
    from platforms import facebook, instagram
    from utils.image_host import upload_to_imgbb
    from utils.video_host import upload_reel
    from video import _dimensiones, duration_seconds

    redes, texto = pedido["redes"], pedido["descripcion"]
    w, h = _dimensiones(video)
    dur = float(duration_seconds(video) or 0)
    apaisado = bool(w and h and w > h)

    def url_video() -> str:
        if not fila.get("video_url"):
            fila["video_url"] = upload_reel(video)
        return fila["video_url"]

    def url_portada() -> str:
        if portada and not fila.get("portada_url"):
            try:
                fila["portada_url"] = upload_to_imgbb(portada)
            except Exception as e:  # noqa: BLE001 — sin portada el reel sale igual
                logger.warning(f"No pude subir la portada ({e}); el reel sale con la de IG.")
                fila["portada_url"] = ""
        return fila.get("portada_url") or ""

    if "instagram" in redes:
        def ig_reel():
            tapa = url_portada()
            try:
                return instagram.publish_reel(url_video(), texto, cover_url=tapa)
            except instagram.ContenedorFallo as e:
                if not tapa:
                    raise
                # El contenedor no llegó a publicarse: reintentar sin la portada no duplica.
                logger.warning(f"Instagram rechazó el reel con la portada ({e}); va sin ella.")
                fila.setdefault("notas", []).append("Instagram no aceptó la portada: salió con "
                                                    "el primer cuadro del video.")
                return instagram.publish_reel(url_video(), texto)
        _paso(fila, estado, "ig_reel", ig_reel)

    if "facebook" in redes:
        def fb_reel():
            res = facebook.publish_video(texto, video, preferir_reel=not apaisado)
            if portada and _id(res):
                try:
                    facebook.poner_miniatura(_id(res), portada)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"Facebook no tomó la portada ({e}); queda la automática.")
                    fila.setdefault("notas", []).append("Facebook no tomó la portada.")
            return res
        _paso(fila, estado, "fb_reel", fb_reel)

    historias = [r for r in ("instagram", "facebook") if r in redes]
    if pedido["historias"] and historias:
        try:
            tr._publicar_historias(url_video() if "instagram" in historias else "",
                                   video, historias, estado, fila)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Historias: {e}")
        for red, clave in (("instagram", "ig_historia"), ("facebook", "fb_historia")):
            if red in historias and fila.get(f"{clave}_id"):
                fila["ids"][clave] = fila[f"{clave}_id"]

    if "youtube" in redes:
        if not tr._yt_enabled():
            estado["youtube"] = "omitido (YouTube apagado con YT_SHORTS_ENABLED)"
        elif apaisado or dur > SHORT_MAX_SEG:
            estado["youtube"] = "omitido (un Short tiene que ser vertical y de hasta 3 minutos)"
        else:
            from platforms import youtube_api
            privacy = (get("YT_SHORTS_PRIVACY") or "public").strip()
            res = _paso(fila, estado, "youtube", lambda: tr._retry(
                lambda: youtube_api.upload_short(
                    video, pedido["titulo"][:100], texto,
                    category_id=get("YT_SHORTS_CATEGORY") or "25", privacy=privacy),
                etiqueta="[youtube] subir Short"))
            if res and res.get("short_url"):
                fila.setdefault("links", {})["youtube"] = res["short_url"]

    if "tiktok" in redes:
        if fila["ids"].get("tiktok"):
            estado["tiktok"] = "ok"
        else:
            res = tr._publicar_tiktok(video, texto, estado) or {}
            if res.get("publish_id"):
                fila["ids"]["tiktok"] = res["publish_id"]
            estado.setdefault("tiktok", "omitido (TikTok apagado o sin credenciales)")


def _publicar_imagen(imagen: Path, pedido: dict, fila: dict, estado: dict) -> None:
    from platforms import facebook, instagram

    redes, texto = pedido["redes"], pedido["descripcion"]
    if "instagram" in redes:
        _paso(fila, estado, "ig_post", lambda: instagram.publish(texto, imagen))
    if "facebook" in redes:
        _paso(fila, estado, "fb_post", lambda: facebook.publish(texto, imagen))
    if pedido["historias"]:
        if "instagram" in redes:
            _paso(fila, estado, "ig_historia", lambda: instagram.publish_story(imagen))
        if "facebook" in redes:
            _paso(fila, estado, "fb_historia", lambda: facebook.publish_story(imagen))
    for red in ("youtube", "tiktok"):
        if red in redes:
            estado[red] = "omitido (es una imagen, no un video)"


def _links(fila: dict) -> dict:
    """Los links públicos de lo que salió (best-effort: sin link el posteo igual existe)."""
    from platforms import facebook, instagram
    links = fila.setdefault("links", {})
    ids = fila.get("ids", {})
    for clave, red, fn in (("ig_reel", "instagram", instagram.permalink),
                           ("ig_post", "instagram", instagram.permalink),
                           ("fb_reel", "facebook", facebook.permalink),
                           ("fb_post", "facebook", facebook.permalink)):
        if ids.get(clave) and ids[clave] != "ok" and not links.get(red):
            try:
                links[red] = fn(ids[clave]) or ""
            except Exception:  # noqa: BLE001
                pass
    return links


def _procesar(carpeta: str, pedido: dict, ledger: dict, dry: bool) -> dict:
    fila = ledger.setdefault(carpeta, {"carpeta": carpeta, "creado":
                                       ahora().isoformat(timespec="seconds"), "ids": {}})
    fila.update(titulo=pedido["titulo"], redes=pedido["redes"])
    pieza = pedido["video"] or pedido["portada"]
    if dry:
        logger.info(f"[dry-run] «{carpeta}»: {pieza} → {', '.join(pedido['redes'])}"
                    f"{' + historias' if pedido['historias'] else ''}. "
                    f"Texto: {pedido['descripcion'][:120]!r}")
        return fila
    fila["intentos"] = int(fila.get("intentos") or 0) + 1
    _guardar_ledger(ledger)

    taller = Path(tempfile.mkdtemp(prefix="reel_listo_"))
    try:
        _rclone("copy", f"{_buzon()}/{carpeta}", str(taller), "--exclude", RESULTADO,
                timeout=900)
    except Exception as e:  # noqa: BLE001 — un hipo de Drive: el pedido queda donde está
        shutil.rmtree(taller, ignore_errors=True)
        logger.error(f"«{carpeta}»: no pude bajarlo de Drive ({e}); queda para la próxima.")
        _avisar_trabado(carpeta, f"no se pudo bajar de Drive ({e})")
        return fila

    estado: dict = {}
    previos = {k for k, v in fila.get("ids", {}).items() if v}
    try:
        if pedido["video"]:
            # El Release de GitHub pisa los archivos con el mismo nombre, y todos los
            # pedidos traen «reel.mp4»: se renombra con la carpeta para que no se mezclen.
            original = taller / pedido["video"]
            video = original.with_name(f"listo_{_slug(carpeta)}{original.suffix.lower()}")
            original.rename(video)
            portada = taller / pedido["portada"] if pedido["portada"] else None
            _publicar_video(video, portada, pedido, fila, estado)
        else:
            _publicar_imagen(taller / pedido["portada"], pedido, fila, estado)
        fila["error"] = ""
    except Exception as e:  # noqa: BLE001
        fila["error"] = str(e)
        logger.error(f"«{carpeta}»: {e}")
    finally:
        _guardar_ledger(ledger)
        shutil.rmtree(taller, ignore_errors=True)

    algo = any(v == "ok" for v in estado.values())
    fallas = [k for k, v in estado.items() if str(v).startswith("falló")]
    if previos and all(k in previos for k, v in estado.items() if v == "ok"):
        fila["notas"] = ["Esta carpeta ya se había publicado antes: no se repitió nada."]
    fila["canales"] = estado
    fila["estado"] = ("publicado" if algo and not fallas and not fila.get("error")
                      else "parcial" if algo else "con_error")
    fila["publicado"] = ahora().isoformat(timespec="seconds")
    try:
        _links(fila)
    except Exception:  # noqa: BLE001
        pass
    _guardar_ledger(ledger)
    _archivar(carpeta, fila)
    _avisar(fila)
    return fila


def _cerrar_mal_armado(carpeta: str, motivo: str, ledger: dict, dry: bool) -> None:
    logger.error(f"«{carpeta}»: pedido mal armado — {motivo}")
    if dry:
        return
    fila = ledger.setdefault(carpeta, {"carpeta": carpeta, "ids": {}})
    fila.update(estado="con_error", error=motivo, canales={},
                publicado=ahora().isoformat(timespec="seconds"))
    _guardar_ledger(ledger)
    _archivar(carpeta, fila)
    _avisar(fila)


# ── el mail ─────────────────────────────────────────────────────────────────────
def _avisar(fila: dict) -> None:
    from transcriber import _enviar_aviso
    titulo = fila.get("titulo") or fila.get("carpeta", "")
    est = fila.get("estado")
    asunto = {"publicado": "Reel publicado (Codex): ",
              "parcial": "Reel publicado con fallas (Codex): ",
              }.get(est, "Reel NO publicado (Codex): ") + titulo
    links = fila.get("links", {})
    renglones, items = [], []
    for clave, st in (fila.get("canales") or {}).items():
        nombre = NOMBRES.get(clave, clave)
        ico = "✅" if st == "ok" else ("➖" if str(st).startswith("omitid") else "❌")
        texto = "publicado" if st == "ok" else st
        renglones.append(f"{ico} {nombre}: {texto}")
        items.append(f"<li>{ico} <b>{_hesc(nombre)}:</b> {_hesc(str(texto))}</li>")
    for red, url in links.items():
        if url:
            renglones.append(f"   {red}: {url}")
            items.append(f'<li>🔗 {_hesc(red)}: <a href="{_hesc(url)}">{_hesc(url)}</a></li>')
    extra = list(fila.get("notas") or [])
    if fila.get("error"):
        extra.insert(0, f"Error: {fila['error']}")
    if est == "con_error":
        extra.append("La carpeta quedó en «reels listos/con error». Para reintentar, "
                     "corregí lo que haga falta y volvé a dejarla en «reels listos».")
    cuerpo = (f"«{titulo}» llegó desde Codex (carpeta «{fila.get('carpeta', '')}»).\n\n"
              + "\n".join(renglones) + ("\n\n" + "\n".join(extra) if extra else ""))
    html = (f"<h2 style='color:#e2620c'>Reel desde Codex</h2>"
            f"<p style='font-size:18px'><b>{_hesc(titulo)}</b></p>"
            f"<ul>{''.join(items)}</ul>"
            + "".join(f"<p style='color:#888'>{_hesc(x)}</p>" for x in extra))
    _enviar_aviso(asunto, cuerpo, html=html)


def _avisar_trabado(carpeta: str, motivo: str) -> None:
    from transcriber import _enviar_aviso
    _enviar_aviso(f"Reel de Codex esperando: {carpeta}",
                  f"La carpeta «{carpeta}» de «reels listos» tiene el pedido, pero después de "
                  f"{ESPERA_MAX_SEG // 60} minutos todavía {motivo}.\n\n"
                  "Suele ser Drive para escritorio que no terminó de subir el video. Cuando "
                  "termine, volvé a disparar la publicación desde Codex.")


# ── la corrida ──────────────────────────────────────────────────────────────────
def _reciente(info: dict) -> bool:
    mod = info.get("mod")
    return bool(mod and ahora() - mod < timedelta(minutes=RECIENTE_MIN))


def correr(dry: bool = False) -> dict:
    """Publica todo lo que esté completo en el buzón. Lo que todavía se está subiendo se
    espera hasta ESPERA_MAX_SEG; si no termina, queda para la próxima corrida."""
    ledger = _leer_ledger()
    hechos: list[str] = []
    pendientes: dict[str, str] = {}
    limite = time.monotonic() + (0 if dry else ESPERA_MAX_SEG)
    while True:
        try:
            carpetas = _listar()
        except Exception as e:  # noqa: BLE001
            logger.error(f"No pude leer el buzón «{_buzon()}»: {e}")
            break
        pendientes = {}
        for carpeta, info in sorted(carpetas.items()):
            if carpeta in hechos:
                continue
            if PEDIDO not in info["archivos"]:
                if _reciente(info):
                    pendientes[carpeta] = f"falta {PEDIDO}"
                else:
                    logger.info(f"«{carpeta}» no tiene {PEDIDO}: no es un pedido, se deja.")
                continue
            try:
                pedido = _normalizar(_leer_pedido(carpeta), info["archivos"])
            except (ValueError, json.JSONDecodeError) as e:
                if isinstance(e, json.JSONDecodeError) and _reciente(info):
                    pendientes[carpeta] = f"{PEDIDO} no se puede leer todavía"
                    continue
                _cerrar_mal_armado(carpeta, str(e), ledger, dry)
                hechos.append(carpeta)
                continue
            except Exception as e:  # noqa: BLE001 — rclone: se reintenta en la vuelta
                pendientes[carpeta] = f"no pude leer el pedido ({e})"
                continue
            ok, motivo = _completo(pedido, info["archivos"])
            if not ok:
                if _reciente(info):
                    pendientes[carpeta] = motivo
                else:   # hace más de una hora que no cambia: no va a llegar, está mal nombrado
                    _cerrar_mal_armado(carpeta, motivo, ledger, dry)
                    hechos.append(carpeta)
                continue
            _procesar(carpeta, pedido, ledger, dry)
            hechos.append(carpeta)
        if not pendientes or time.monotonic() >= limite:
            break
        logger.info(f"Esperando a Drive: {pendientes}")
        time.sleep(ESPERA_PASO_SEG)

    for carpeta, motivo in pendientes.items():
        logger.warning(f"«{carpeta}» queda para la próxima: {motivo}.")
        if not dry and not motivo.startswith("falta " + PEDIDO):
            _avisar_trabado(carpeta, motivo)
    if not hechos and not pendientes:
        logger.info("El buzón «reels listos» está vacío.")
    return {"publicados": hechos, "pendientes": pendientes}
