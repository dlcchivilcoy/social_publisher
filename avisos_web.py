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


def cargar_avisos() -> list[dict]:
    """Lista de avisos con campos extra: «_archivo» (ruta real) y «_es_video»."""
    web = web_dir()
    data = _leer(web)
    for a in data:
        rel = a.get("img") or a.get("video") or ""
        a["_es_video"] = bool(a.get("video"))
        a["_archivo"] = str((web / "public" / rel.lstrip("/"))) if rel else ""
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
def agregar_aviso(nombre: str, archivo: str, link: str = "", forma: str = "ancha") -> dict:
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
    fname = _nombre_unico(dir_, _slug(nombre), ext)
    shutil.copy2(src, dir_ / fname)

    entry: dict = {"nombre": nombre}
    if es_video:
        entry["video"] = f"/avisos/{fname}"
    else:
        entry["img"] = f"/avisos/{fname}"
    entry["forma"] = forma or "ancha"
    if link:
        entry["link"] = link

    data = _leer(web)
    data.insert(0, entry)   # los avisos nuevos van PRIMEROS (arriba-izquierda del footer)
    _escribir(web, data)

    _commit_push(
        web,
        [_REL_JSON, f"{_REL_AVISOS}/{fname}"],
        f"Publicidad: agregar {nombre}",
    )
    return entry


def editar_aviso(indice: int, nombre_esperado: str | None, nombre: str,
                 link: str = "", nuevo_archivo: str | None = None,
                 forma: str | None = None) -> dict:
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
        nuevo_fname = _nombre_unico(dir_, _slug(nombre), ext)
        shutil.copy2(src, dir_ / nuevo_fname)
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
    return quitado
