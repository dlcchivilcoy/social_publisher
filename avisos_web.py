"""Gestión de las PUBLICIDADES (avisos de anunciantes) del pie de la web.

Los avisos NO viven en Wix, sino en el repo de la web nueva (Astro/Vercel):
  <web>/src/data/avisos.json   → lista [{ "nombre", "img"|"video", "forma", "link"? }, ...]
  <web>/public/avisos/         → los archivos de imagen o video

Agregar o borrar un aviso = tocar ese JSON + copiar/borrar el archivo + hacer
`git commit` y `git push` al repo dlcchivilcoy/diario_web. Vercel deploya solo
(la portada se sirve dinámica → el cambio aparece en ~1-2 minutos).

Lo usa el «Editor de notas» (pestaña Publicidades). No necesita claves nuevas.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import unicodedata
from pathlib import Path

# Rutas relativas dentro del repo de la web
_REL_JSON = "src/data/avisos.json"
_REL_AVISOS = "public/avisos"

# Dónde está el repo de la web. Se puede overridear con la variable de entorno
# DIARIO_WEB_DIR o con un archivo «.web_dir.txt» junto a este módulo (lo escribe
# el botón «Cambiar carpeta…» de la app). Si no, se usa la ruta conocida.
_DEFAULT_WEB = Path(r"E:\CLAUDE PROYECTOS\diario_web")
_WEB_DIR_FILE = Path(__file__).parent / ".web_dir.txt"

VIDEO_EXTS = {".mp4", ".webm", ".mov"}
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif"}

# Autor de los commits: Vercel (Hobby) EXIGE que sea un usuario de GitHub del repo.
_GIT_USER = "dlcchivilcoy"
_GIT_EMAIL = "dlc.chivilcoy@gmail.com"


# ── ubicación del repo de la web ──────────────────────────────────────────────
def _candidatos():
    env = (os.getenv("DIARIO_WEB_DIR") or "").strip()
    if env:
        yield Path(env)
    try:
        if _WEB_DIR_FILE.exists():
            txt = _WEB_DIR_FILE.read_text(encoding="utf-8").strip()
            if txt:
                yield Path(txt)
    except OSError:
        pass
    yield _DEFAULT_WEB


def _es_repo_web(p: Path) -> bool:
    try:
        return (p / _REL_JSON).is_file()
    except OSError:
        return False


def web_dir() -> Path:
    """Carpeta del repo de la web. Lanza si no la encuentra."""
    for c in _candidatos():
        if _es_repo_web(c):
            return c
    raise FileNotFoundError(
        "No encuentro la carpeta de la web (diario_web).\n\n"
        "Usá el botón «Cambiar carpeta…» y elegí la carpeta del proyecto de la web "
        "(la que tiene «src/data/avisos.json»)."
    )


def set_web_dir(ruta: str) -> Path:
    """Guarda (y valida) la carpeta del repo de la web para próximas veces."""
    p = Path(ruta)
    if not _es_repo_web(p):
        raise ValueError("Esa carpeta no parece la de la web: no tiene «src/data/avisos.json».")
    _WEB_DIR_FILE.write_text(str(p), encoding="utf-8")
    return p


def _json_path(web: Path) -> Path:
    return web / _REL_JSON


def _avisos_dir(web: Path) -> Path:
    return web / _REL_AVISOS


# ── lectura / escritura del JSON ──────────────────────────────────────────────
def _leer(web: Path) -> list[dict]:
    return json.loads(_json_path(web).read_text(encoding="utf-8"))


def _limpio(a: dict) -> dict:
    """Saca los campos auxiliares (empiezan con «_») que agrega cargar_avisos()."""
    return {k: v for k, v in a.items() if not str(k).startswith("_")}


def _fmt_obj(a: dict) -> str:
    campos = ", ".join(
        f"{json.dumps(k, ensure_ascii=False)}: {json.dumps(v, ensure_ascii=False)}"
        for k, v in _limpio(a).items()
    )
    return "{ " + campos + " }"


def _escribir(web: Path, data: list[dict]) -> None:
    # Un objeto por línea, mismo estilo que el archivo original (diffs chicos).
    cuerpo = ",\n  ".join(_fmt_obj(a) for a in data)
    _json_path(web).write_text("[\n  " + cuerpo + "\n]\n", encoding="utf-8")


def _ahora_ar():
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=-3)))


def _fecha_ar(valor):
    """ISO -> datetime con huso argentino. None si está vacío o ilegible."""
    if not valor:
        return None
    from datetime import datetime, timedelta, timezone
    try:
        d = datetime.fromisoformat(str(valor))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone(timedelta(hours=-3)))


def estado_de(aviso: dict, ahora=None) -> str:
    """«vigente», «programada» (todavía no arrancó) o «vencida»."""
    ahora = ahora or _ahora_ar()
    hasta = _fecha_ar(aviso.get("hasta"))
    if hasta and hasta <= ahora:
        return "vencida"
    desde = _fecha_ar(aviso.get("desde"))
    if desde and desde > ahora:
        return "programada"
    return "vigente"


def cargar_avisos() -> list[dict]:
    """Lista de avisos con campos extra: «_archivo», «_es_video» y «_estado»."""
    web = web_dir()
    data = _leer(web)
    ahora = _ahora_ar()
    for a in data:
        rel = a.get("img") or a.get("video") or ""
        a["_es_video"] = bool(a.get("video"))
        a["_archivo"] = str((web / "public" / rel.lstrip("/"))) if rel else ""
        a["_estado"] = estado_de(a, ahora)
    return data


# ── vista previa de videos (un cuadro/frame como miniatura) ───────────────────
def _ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"  # fallback al del sistema


def miniatura_video(video_path: str, salida=None):
    """Saca un cuadro del video a un PNG (temporal, cacheado) y devuelve su ruta.
    Devuelve None si no se pudo. Se usa para previsualizar los avisos de video."""
    src = Path(video_path or "")
    if not src.is_file():
        return None
    if salida is None:
        import hashlib
        import tempfile
        h = hashlib.md5(str(src.resolve()).encode("utf-8")).hexdigest()[:12]
        salida = Path(tempfile.gettempdir()) / f"aviso_video_{h}.png"
    salida = Path(salida)
    # cache: si ya lo extrajimos y el video no cambió después, reusar
    try:
        if salida.exists() and salida.stat().st_size > 0 and \
                salida.stat().st_mtime >= src.stat().st_mtime:
            return salida
    except OSError:
        pass
    ff = _ffmpeg_exe()
    # 1) cuadro alrededor del segundo 1 (evita frames negros del arranque)
    intentos = [
        [ff, "-y", "-ss", "1", "-i", str(src), "-frames:v", "1", str(salida)],
        [ff, "-y", "-i", str(src), "-frames:v", "1", str(salida)],  # 2) primer cuadro
    ]
    for cmd in intentos:
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=40)
            if salida.exists() and salida.stat().st_size > 0:
                return salida
        except Exception:
            continue
    return None


# ── compresión automática ─────────────────────────────────────────────────────
# Un aviso pesado se paga en tráfico. El 2/8/2026 el crawler de Facebook se bajó
# 7,08 GB en UN día pidiendo los videos de los avisos una y otra vez, y hubo que
# recomprimirlos a mano (12,3 MB → 4,1 MB). Desde entonces todo lo que entra se
# recomprime ANTES de subirlo, así no depende de que alguien se acuerde.
IMG_LADO_MAX = 1600      # ningún aviso se muestra más grande que esto
IMG_CALIDAD = 82
VIDEO_ANCHO_MAX = 720    # en el pie se ve chico; 720 sobra
VIDEO_CRF = 28           # liviano y sin artefactos visibles a ese tamaño
VIDEO_FPS_MAX = 30       # los de 60 fps pesan el doble por nada


def _peso(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.1f} MB".replace(".", ",")
    return f"{n / 1024:.0f} KB"


def _comprimir_imagen(src: Path, tmp: Path):
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    try:
        with Image.open(src) as im:
            im = ImageOps.exif_transpose(im)
            alfa = im.mode in ("RGBA", "LA") or (
                im.mode == "P" and "transparency" in im.info)
            im.thumbnail((IMG_LADO_MAX, IMG_LADO_MAX), Image.LANCZOS)
            if alfa:
                salida = tmp / (src.stem + ".png")
                im.convert("RGBA").save(salida, "PNG", optimize=True)
            else:
                salida = tmp / (src.stem + ".jpg")
                im.convert("RGB").save(salida, "JPEG", quality=IMG_CALIDAD,
                                       optimize=True, progressive=True)
        return salida if salida.is_file() and salida.stat().st_size else None
    except Exception:
        return None


def _comprimir_video(src: Path, tmp: Path):
    salida = tmp / (src.stem + ".mp4")
    escala = f"scale='min({VIDEO_ANCHO_MAX},iw)':-2:flags=lanczos"
    # 1º con tope de cuadros por segundo; si esta ffmpeg no entiende la expresión,
    # 2º sin ella (igual comprime, solo que menos).
    for vf in (f"{escala},fps=fps=min({VIDEO_FPS_MAX}\\,source_fps)", escala):
        cmd = [
            _ffmpeg_exe(), "-y", "-i", str(src),
            "-map", "0:v:0", "-map", "0:a?",
            "-vf", vf,
            "-c:v", "libx264", "-preset", "slow", "-crf", str(VIDEO_CRF),
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-c:a", "aac", "-b:a", "96k", "-ac", "2",
            "-map_metadata", "-1",
            str(salida),
        ]
        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        except Exception:
            return None
        if salida.is_file() and salida.stat().st_size:
            return salida
    return None


def _optimizar(src: Path):
    """Recomprime el archivo antes de subirlo.

    Devuelve `(ruta_a_usar, detalle, es_temporal)`. Si algo falla, o si el
    resultado NO queda más chico, devuelve el original tal cual: la compresión
    nunca empeora un archivo ni hace fracasar una carga.
    """
    import tempfile
    ext = src.suffix.lower()
    if ext == ".gif":
        return src, "", False          # animado: recomprimirlo lo rompe
    try:
        antes = src.stat().st_size
    except OSError:
        return src, "", False
    tmp = Path(tempfile.mkdtemp(prefix="aviso_"))
    hacer = _comprimir_video if ext in VIDEO_EXTS else _comprimir_imagen
    salida = hacer(src, tmp)
    if salida is None:
        shutil.rmtree(tmp, ignore_errors=True)
        return src, "", False
    despues = salida.stat().st_size
    if despues >= antes:               # ya venía bien comprimido
        shutil.rmtree(tmp, ignore_errors=True)
        return src, "", False
    return salida, f"{_peso(antes)} → {_peso(despues)}", True


def _copiar_optimizado(src: Path, destino_dir: Path, base: str):
    """Comprime `src` y lo copia a `destino_dir`. Devuelve `(nombre, detalle)`."""
    listo, detalle, es_temp = _optimizar(src)
    try:
        fname = _nombre_unico(destino_dir, base, listo.suffix.lower())
        shutil.copy2(listo, destino_dir / fname)
    finally:
        if es_temp:
            shutil.rmtree(listo.parent, ignore_errors=True)
    return fname, detalle


# ── git ───────────────────────────────────────────────────────────────────────
def _git(web: Path, *args, timeout: int = 180) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", str(web), *args],
            capture_output=True, text=True, timeout=timeout,
        )
    except FileNotFoundError:
        raise RuntimeError("No está instalado git (hace falta para publicar en la web).")
    except subprocess.TimeoutExpired:
        raise RuntimeError("git tardó demasiado (¿sin internet o pidiendo credenciales?).")
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "").strip() or f"git {args[0]} falló")
    return (r.stdout or "").strip()


def _hay_staged(web: Path) -> bool:
    r = subprocess.run(["git", "-C", str(web), "diff", "--cached", "--quiet"])
    return r.returncode != 0  # 1 = hay cambios en el índice


def _commit_push(web: Path, rutas_rel: list[str], mensaje: str) -> None:
    _git(web, "add", "--", *rutas_rel)
    if not _hay_staged(web):
        raise RuntimeError("No hubo cambios para publicar.")
    _git(web, "-c", f"user.name={_GIT_USER}", "-c", f"user.email={_GIT_EMAIL}",
         "commit", "-m", mensaje)
    try:
        _git(web, "push")
    except RuntimeError as e:
        msg = str(e).lower()
        # Repo atrasado respecto de origin → traer y reintentar una vez.
        if any(k in msg for k in ("rejected", "fetch first", "non-fast-forward", "behind")):
            _git(web, "pull", "--rebase", "--autostash")
            _git(web, "push")
        else:
            raise


# ── nombres de archivo ────────────────────────────────────────────────────────
def _slug(texto: str) -> str:
    t = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii").lower()
    out = []
    for ch in t:
        if ch.isalnum():
            out.append(ch)
        elif ch in " -_":
            out.append("-")
    slug = "".join(out)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "aviso"


def _nombre_unico(dir_: Path, base: str, ext: str) -> str:
    cand = f"{base}{ext}"
    i = 2
    while (dir_ / cand).exists():
        cand = f"{base}-{i}{ext}"
        i += 1
    return cand


# ── alta / baja ───────────────────────────────────────────────────────────────
def agregar_aviso(nombre: str, archivo: str, link: str = "", forma: str = "ancha",
                  desde: str | None = None, hasta: str | None = None) -> dict:
    """Copia el archivo a public/avisos/, agrega la entrada al JSON y publica (git push)."""
    nombre = (nombre or "").strip()
    link = (link or "").strip()
    src = Path(archivo)
    if not nombre:
        raise ValueError("Escribí el nombre del anunciante.")
    if not src.is_file():
        raise ValueError("Elegí un archivo de imagen o video válido.")
    ext = src.suffix.lower()
    es_video = ext in VIDEO_EXTS
    if not es_video and ext not in IMG_EXTS:
        raise ValueError(
            f"Formato no soportado: {ext}. Usá imagen (jpg, png, gif, webp) o video (mp4, webm)."
        )

    web = web_dir()
    dir_ = _avisos_dir(web)
    dir_.mkdir(parents=True, exist_ok=True)
    fname, detalle = _copiar_optimizado(src, dir_, _slug(nombre))

    entry: dict = {"nombre": nombre}
    if es_video:
        entry["video"] = f"/avisos/{fname}"
    else:
        entry["img"] = f"/avisos/{fname}"
    entry["forma"] = forma or "ancha"
    if link:
        entry["link"] = link
    # Vigencia: la web sola muestra u oculta el aviso segun estas fechas
    # (diario_web/src/lib/avisos.js). Sin fechas, esta siempre visible.
    if desde:
        entry["desde"] = desde
    if hasta:
        entry["hasta"] = hasta

    data = _leer(web)
    data.insert(0, entry)   # los avisos nuevos van PRIMEROS (arriba-izquierda del footer)
    _escribir(web, data)

    _commit_push(
        web,
        [_REL_JSON, f"{_REL_AVISOS}/{fname}"],
        f"Publicidad: agregar {nombre}",
    )
    entry["_optimizado"] = detalle   # «_» = no se escribe en el JSON (ver _limpio)
    return entry


def editar_aviso(indice: int, nombre_esperado: str | None, nombre: str,
                 link: str = "", nuevo_archivo: str | None = None,
                 forma: str | None = None, desde: str | None = None,
                 hasta: str | None = None, tocar_fechas: bool = False) -> dict:
    """Edita el aviso `indice`: cambia el nombre, el link (vacío = quitar el link) y,
    opcionalmente, REEMPLAZA el archivo (imagen o video). Mantiene su posición en la
    lista. Publica (git push). Borra el archivo viejo si nadie más lo usa."""
    nombre = (nombre or "").strip()
    link = (link or "").strip()
    if not nombre:
        raise ValueError("Escribí el nombre del anunciante.")

    web = web_dir()
    data = _leer(web)
    if indice < 0 or indice >= len(data):
        raise ValueError("Publicidad no encontrada. Actualizá la lista y probá de nuevo.")
    if nombre_esperado is not None and data[indice].get("nombre", "") != nombre_esperado:
        raise ValueError("La lista cambió. Actualizá la lista y probá de nuevo.")

    entry = data[indice]
    archivo_viejo = (entry.get("img") or entry.get("video") or "").lstrip("/").split("/")[-1]
    rutas_rel = [_REL_JSON]

    # ¿reemplazar el archivo?
    nuevo_fname = None
    detalle = ""
    es_video = bool(entry.get("video"))
    if nuevo_archivo:
        src = Path(nuevo_archivo)
        if not src.is_file():
            raise ValueError("El archivo nuevo no existe.")
        ext = src.suffix.lower()
        es_video = ext in VIDEO_EXTS
        if not es_video and ext not in IMG_EXTS:
            raise ValueError(
                f"Formato no soportado: {ext}. Usá imagen (jpg, png, gif, webp) o video (mp4, webm)."
            )
        dir_ = _avisos_dir(web)
        dir_.mkdir(parents=True, exist_ok=True)
        nuevo_fname, detalle = _copiar_optimizado(src, dir_, _slug(nombre))
        rutas_rel.append(f"{_REL_AVISOS}/{nuevo_fname}")

    # Reconstruir la entrada en orden canónico: nombre, img|video, forma, link
    nueva: dict = {"nombre": nombre}
    if nuevo_fname:
        nueva["video" if es_video else "img"] = f"/avisos/{nuevo_fname}"
    elif entry.get("video"):
        nueva["video"] = entry["video"]
    elif entry.get("img"):
        nueva["img"] = entry["img"]
    nueva["forma"] = forma or entry.get("forma") or "ancha"
    if link:
        nueva["link"] = link
    # `tocar_fechas` distingue "no me las pasaron" de "borralas": sin el, no habria
    # forma de sacarle la vigencia a un aviso y dejarlo fijo.
    d = desde if tocar_fechas else entry.get("desde")
    h = hasta if tocar_fechas else entry.get("hasta")
    if d:
        nueva["desde"] = d
    if h:
        nueva["hasta"] = h

    data[indice] = nueva
    _escribir(web, data)

    # Borrar el archivo viejo si lo reemplazamos y nadie más lo usa
    if nuevo_fname and archivo_viejo and archivo_viejo != nuevo_fname:
        sigue_usado = any(
            (a.get("img") or a.get("video") or "").lstrip("/").split("/")[-1] == archivo_viejo
            for a in data
        )
        if not sigue_usado:
            viejo = _avisos_dir(web) / archivo_viejo
            if viejo.exists():
                viejo.unlink()
            rutas_rel.append(f"{_REL_AVISOS}/{archivo_viejo}")

    _commit_push(web, rutas_rel, f"Publicidad: editar {nombre}")
    nueva["_optimizado"] = detalle
    return nueva


def borrar_aviso(indice: int, nombre_esperado: str | None = None) -> dict:
    """Saca la entrada `indice` del JSON, borra su archivo (si nadie más lo usa) y publica."""
    web = web_dir()
    data = _leer(web)
    if indice < 0 or indice >= len(data):
        raise ValueError("Publicidad no encontrada. Actualizá la lista y probá de nuevo.")
    if nombre_esperado is not None and data[indice].get("nombre", "") != nombre_esperado:
        raise ValueError("La lista cambió. Actualizá la lista y probá de nuevo.")

    quitado = data.pop(indice)
    _escribir(web, data)

    rutas_rel = [_REL_JSON]
    rel = quitado.get("img") or quitado.get("video") or ""
    fname = rel.lstrip("/").split("/")[-1] if rel else ""
    if fname:
        # ¿algún otro aviso sigue usando el mismo archivo?
        sigue_usado = any(
            (a.get("img") or a.get("video") or "").lstrip("/").split("/")[-1] == fname
            for a in data
        )
        if not sigue_usado:
            archivo = _avisos_dir(web) / fname
            if archivo.exists():
                archivo.unlink()
            rutas_rel.append(f"{_REL_AVISOS}/{fname}")

    _commit_push(web, rutas_rel, f"Publicidad: quitar {quitado.get('nombre', 'aviso')}")
    # Si ademas tenia un posteo AGENDADO en redes, se cancela. Si no, borrariamos el
    # aviso de la web y Facebook lo publicaria igual el mes que viene.
    quitado["_cancelados"] = cancelar_en_cola(rel)
    return quitado


# ── campañas programadas (web + redes) ───────────────────────────────────────
def _base_web() -> str:
    """Dominio ASCII de la web. Meta tropieza con la ñ del dominio real, así que
    para las URLs que le pasamos usamos siempre el .vercel.app, que es el mismo
    sitio (ver el historial de rechazos de TikTok por lo mismo)."""
    base = os.getenv("WEB_BASE_URL") or ""
    if not base:
        try:
            from utils.config import get
            base = get("WEB_BASE_URL") or ""
        except Exception:
            base = ""
    return (base or "https://diarioweb.vercel.app").rstrip("/")


def subir_pieza(nombre: str, archivo: str) -> tuple[str, str]:
    """Sube SOLO el archivo a `public/avisos/`, sin meterlo en la lista de
    anunciantes. Es hospedaje puro: le da a Facebook y a Instagram una URL
    pública y estable para cuando llegue la hora de postear.

    Queda accesible pero sin que lo referencie nadie, así que no se ve en la web.
    """
    src = Path(archivo)
    if not src.is_file():
        raise ValueError("Elegí un archivo de imagen o video válido.")
    ext = src.suffix.lower()
    if ext not in VIDEO_EXTS and ext not in IMG_EXTS:
        raise ValueError(
            f"Formato no soportado: {ext}. Usá imagen (jpg, png, gif, webp) o video (mp4, webm)."
        )
    web = web_dir()
    dir_ = _avisos_dir(web)
    dir_.mkdir(parents=True, exist_ok=True)
    fname, detalle = _copiar_optimizado(src, dir_, _slug(nombre))
    _commit_push(web, [f"{_REL_AVISOS}/{fname}"], f"Publicidad: subir la pieza de {nombre}")
    return fname, detalle


def programar(nombre: str, archivo: str, forma: str = "ancha", link: str = "",
              desde: str | None = None, hasta: str | None = None,
              en_web: bool = True, redes: list[str] | None = None,
              texto: str = "") -> dict:
    """Carga una campaña. Los tres destinos son INDEPENDIENTES entre sí.

    - `en_web`: entra al espacio de anunciantes, con sus fechas. Acepta cualquier
      archivo, imagen o video, del tamaño que sea; la `forma` dice en qué hueco
      encaja. La web lo enciende y lo apaga sola: no hay reloj de por medio.
    - `redes`: Facebook y/o Instagram. Ahí sí importa si es foto (publicación +
      historia) o video (reel + historia), porque son formatos distintos.

    Se puede pedir solo la web, solo las redes, o las tres cosas.
    """
    redes = [r for r in (redes or []) if r]
    if not en_web and not redes:
        raise ValueError("Elegí al menos un destino: la web, Facebook o Instagram.")

    if en_web:
        entry = agregar_aviso(nombre, archivo, link, forma, desde=desde, hasta=hasta)
        rel = entry.get("img") or entry.get("video") or ""
        detalle = entry.get("_optimizado", "")
        es_video = bool(entry.get("video"))
    else:
        # Sin web, el archivo igual se sube: es de donde lo toman FB e IG.
        fname, detalle = subir_pieza(nombre, archivo)
        rel = f"/avisos/{fname}"
        es_video = Path(fname).suffix.lower() in VIDEO_EXTS
        entry = {"nombre": nombre}
        if desde:
            entry["desde"] = desde
        if hasta:
            entry["hasta"] = hasta

    url = _base_web() + rel
    salida = {"aviso": entry, "url": url, "en_web": en_web,
              "programado": None, "_optimizado": detalle}

    if redes:
        import publicidades_programadas as pp
        cuando = desde or pp.ahora().isoformat(timespec="minutes")
        salida["programado"] = pp.agregar(
            nombre=nombre,
            tipo="video" if es_video else "foto",
            url=url, texto=texto, cuando=cuando, destinos=redes, baja=hasta,
        )
        pp.publicar_cola()
    return salida


def limpiar_vencidas(ahora=None) -> list[str]:
    """Saca de la web las campañas cuya fecha «hasta» ya pasó y borra sus archivos.

    El FRENO ya lo hace la fecha: desde el «hasta», la web no las muestra más.
    Esto es la limpieza posterior, para que el archivo no quede dando vueltas en
    el sistema. Va acá y no en la nube porque el repositorio de la web es privado
    y el token del workflow solo alcanza a social_publisher.

    Devuelve los nombres de lo que sacó.
    """
    from datetime import datetime, timedelta, timezone
    ar = timezone(timedelta(hours=-3))
    ahora = ahora or datetime.now(ar)

    web = web_dir()
    data = _leer(web)
    quedan: list[dict] = []
    fuera: list[dict] = []
    for a in data:
        h = a.get("hasta")
        vencida = False
        if h:
            try:
                d = datetime.fromisoformat(str(h))
                vencida = (d if d.tzinfo else d.replace(tzinfo=ar)) <= ahora
            except ValueError:
                vencida = False      # fecha ilegible: no la toco, que la mire un humano
        (fuera if vencida else quedan).append(a)

    if not fuera:
        return []

    _escribir(web, quedan)
    rutas = [_REL_JSON]
    nombres = []
    for a in fuera:
        nombres.append(a.get("nombre", "(sin nombre)"))
        fname = (a.get("img") or a.get("video") or "").lstrip("/").split("/")[-1]
        if not fname:
            continue
        sigue_usado = any(
            (b.get("img") or b.get("video") or "").lstrip("/").split("/")[-1] == fname
            for b in quedan
        )
        if sigue_usado:
            continue
        archivo = _avisos_dir(web) / fname
        if archivo.exists():
            archivo.unlink()
        rutas.append(f"{_REL_AVISOS}/{fname}")

    _commit_push(web, rutas, "Publicidades: limpiar las campanas vencidas")
    return nombres


def vencidas(ahora=None) -> list[str]:
    """Los nombres de las campañas ya vencidas, para preguntar antes de limpiar."""
    from datetime import datetime, timedelta, timezone
    ar = timezone(timedelta(hours=-3))
    ahora = ahora or datetime.now(ar)
    out = []
    for a in _leer(web_dir()):
        h = a.get("hasta")
        if not h:
            continue
        try:
            d = datetime.fromisoformat(str(h))
        except ValueError:
            continue
        if (d if d.tzinfo else d.replace(tzinfo=ar)) <= ahora:
            out.append(a.get("nombre", "(sin nombre)"))
    return out


# ── borrado manual, en cualquier momento ─────────────────────────────────────
def cancelar_en_cola(rel_o_nombre_archivo: str) -> int:
    """Saca de la cola de redes los posteos que apuntan a ese archivo.

    Devuelve cuántos canceló. Solo toca los que TODAVIA no salieron: si ya se
    publicó, el posteo existe en Facebook o Instagram y sacarlo de la cola no lo
    borraría — para eso hay que ir a la red. Por eso los publicados se dejan.
    """
    try:
        import publicidades_programadas as pp
    except Exception:
        return 0
    fname = (rel_o_nombre_archivo or "").split("/")[-1]
    if not fname:
        return 0
    trabajos = pp.leer()
    quedan = [t for t in trabajos
              if not ((t.get("url") or "").split("/")[-1] == fname
                      and t.get("estado") == "pendiente")]
    cancelados = len(trabajos) - len(quedan)
    if cancelados:
        pp.guardar(quedan)
        pp.publicar_cola()
    return cancelados


def borrar_pieza(nombre_archivo: str) -> dict:
    """Borra del sistema un archivo subido que NO está en la lista de anunciantes.

    Es el caso de una campaña que iba solo a redes: el archivo vive en
    `public/avisos/` para que Meta lo pueda tomar, pero no figura en el JSON. Sin
    esto no habría forma de sacarlo.
    """
    fname = (nombre_archivo or "").split("/")[-1]
    if not fname:
        raise ValueError("No sé qué archivo borrar.")
    web = web_dir()
    en_uso = any((a.get("img") or a.get("video") or "").split("/")[-1] == fname
                 for a in _leer(web))
    if en_uso:
        raise ValueError("Ese archivo lo está usando una publicidad de la web. "
                         "Borrá la publicidad desde la lista.")
    cancelados = cancelar_en_cola(fname)
    archivo = _avisos_dir(web) / fname
    if archivo.exists():
        archivo.unlink()
        _commit_push(web, [f"{_REL_AVISOS}/{fname}"], f"Publicidad: borrar la pieza {fname}")
    return {"archivo": fname, "cancelados": cancelados}


def programadas() -> list[dict]:
    """La cola de posteos en redes, con un campo «_en_web» para saber si además
    el aviso está cargado en el sitio."""
    try:
        import publicidades_programadas as pp
    except Exception:
        return []
    try:
        en_web = {(a.get("img") or a.get("video") or "").split("/")[-1]
                  for a in _leer(web_dir())}
    except Exception:
        en_web = set()
    out = []
    for t in pp.leer():
        t = dict(t)
        t["_archivo"] = (t.get("url") or "").split("/")[-1]
        t["_en_web"] = t["_archivo"] in en_web
        out.append(t)
    return out


def cancelar_programada(id_: str, borrar_archivo: bool = False) -> dict:
    """Cancela un posteo agendado. Si se pide, borra también el archivo subido."""
    import publicidades_programadas as pp
    trabajo = next((t for t in pp.leer() if t.get("id") == id_), None)
    if trabajo is None:
        raise ValueError("Ese posteo ya no está en la cola. Actualizá la lista.")
    pp.quitar(id_)
    pp.publicar_cola()
    salida = {"nombre": trabajo.get("nombre", ""), "archivo_borrado": False}
    if borrar_archivo:
        fname = (trabajo.get("url") or "").split("/")[-1]
        try:
            borrar_pieza(fname)
            salida["archivo_borrado"] = True
        except ValueError:
            pass          # lo usa un aviso de la web: se deja
    return salida
