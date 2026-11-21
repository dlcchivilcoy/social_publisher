#!/usr/bin/env python3
"""Carga los secretos del ingreso editorial (Supabase) y del conector MCP (Vercel) SIN mostrarlos, y hace una
carga de prueba. Lo corre el editor en su terminal (escribe la clave del conector a ciegas).

Requisitos previos (una vez):
  npx supabase login                      (abre el navegador; entrás vos)
  la función `editorial-ingest` desplegada y la migración 0008 aplicada
  el proyecto de Vercel del conector vinculado en mcp-editorial/ (carpeta .vercel)

Uso:
  python tools/editorial_secretos.py

Qué hace:
  1. Genera EDITORIAL_INGEST_TOKEN y MCP_SIGNING_SECRET al azar (nunca se imprimen ni se guardan en disco,
     salvo un archivo temporal privado que se borra enseguida).
  2. Te pregunta: nombre y celular del editor, carpeta temporal (staging) y carpeta de ensayo de Drive,
     la URL pública del conector y la clave de acceso que vas a escribir en ChatGPT al conectarlo.
  3. Los carga en Supabase (`supabase secrets set`) y en Vercel (`vercel env add`, por stdin).
  4. Hace una carga de prueba (una imagen chiquita) a la carpeta de ENSAYO y consulta el recibo.
  5. Si salió bien y lo confirmás, quita la carpeta de ensayo: desde ahí los envíos van a corresponsales.
"""
import getpass
import os
import re
import secrets
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PROYECTO = os.environ.get("SUPABASE_PROJECT_REF", "uocprsnolcsvvtbxdqyp")
CONECTOR = RAIZ / "mcp-editorial"
NPX = shutil.which("npx.cmd") or shutil.which("npx") or "npx"
STAGING = "1NL_8fzpnQ3HI_ClPIeRnA3Hw4ztaKVzi"        # «Ingreso editorial - temporal» (Mi unidad)
ENSAYO = "1qhBnVLzQyN_kmPcZeHSfoGT7okys9irJ"         # «Ingreso editorial - ensayo» (Mi unidad)
URL_CONECTOR = "https://editorial-mcp-neon.vercel.app"
SECRETAS = {"MCP_SIGNING_SECRET", "MCP_ACCESS_KEY", "EDITORIAL_INGEST_TOKEN"}


def carpeta_id(texto: str) -> str:
    """Acepta el ID o el enlace de la carpeta de Drive."""
    m = re.search(r"/folders/([A-Za-z0-9_-]{10,})", texto) or re.fullmatch(r"\s*([A-Za-z0-9_-]{10,})\s*", texto)
    if not m:
        raise SystemExit("Eso no parece un ID ni un enlace de carpeta de Drive.")
    return m.group(1)


def preguntar(texto: str, validar=None) -> str:
    while True:
        valor = input(texto).strip()
        if not validar or validar(valor):
            return valor
        print("  No es válido, probá de nuevo.")


def clave_oculta(texto: str) -> str:
    """getpass, pero si vuelve vacío lo pide de nuevo: en la terminal de Windows el Enter de la pregunta anterior
    queda en el buffer y getpass lo lee como una respuesta vacía (21/11/2026)."""
    for _ in range(5):
        valor = getpass.getpass(texto)
        if valor:
            return valor
    raise SystemExit("No llegó ninguna clave.")


def correr(args, entrada: str = None, cwd=None) -> None:
    r = subprocess.run([NPX, "--yes", *args], input=entrada, text=True, cwd=cwd, capture_output=True)
    if r.returncode != 0:
        # La salida de las CLI no incluye los valores (van por archivo o stdin); igual se recorta.
        raise SystemExit(f"Falló `{' '.join(args[:3])}…`:\n{(r.stderr or r.stdout)[-600:]}")


def png_prueba() -> bytes:
    """Una imagen PNG de 1×1 naranja, válida, hecha con la biblioteca estándar."""
    def bloque(tipo, datos):
        return struct.pack(">I", len(datos)) + tipo + datos + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + bloque(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)) +
            bloque(b"IDAT", zlib.compress(b"\x00\xfd\x85\x11")) + bloque(b"IEND", b""))


def main() -> int:
    if not (CONECTOR / ".vercel" / "project.json").exists():
        print("Falta vincular el proyecto de Vercel del conector (mcp-editorial/.vercel). Pedíselo a Claude.")
        return 2
    print("Ingreso editorial: carga de secretos. Nada de lo que escribas se muestra ni se guarda.\n")
    nombre = preguntar("Nombre del editor (como firma, máx. 7 palabras): ",
                       lambda v: 0 < len(v) <= 60 and len(v.split()) <= 7)
    celular = re.sub(r"\D", "", preguntar("Celular del editor con código de país (ej. 5492346...): ",
                                          lambda v: 10 <= len(re.sub(r"\D", "", v)) <= 15))
    # 21/11/2026: las carpetas ya creadas en «Mi unidad» (fuera de «videos notas actualidad») y la URL del conector
    # publicado. Enter acepta el valor entre corchetes.
    staging = carpeta_id(preguntar(f"Carpeta TEMPORAL de Drive [{STAGING}]: ") or STAGING)
    ensayo = carpeta_id(preguntar(f"Carpeta de ENSAYO de Drive [{ENSAYO}]: ") or ENSAYO)
    if staging == ensayo:
        raise SystemExit("La carpeta temporal y la de ensayo tienen que ser distintas.")
    url = (preguntar(f"URL pública del conector [{URL_CONECTOR}]: ",
                     lambda v: not v or re.fullmatch(r"https://[A-Za-z0-9.-]+", v.rstrip("/")) is not None)
           or URL_CONECTOR).rstrip("/")
    print("\nAhora la clave del conector. Mientras la escribís NO aparece nada en pantalla: es normal.")
    clave = clave_oculta("Clave de acceso para conectar ChatGPT (mín. 12 caracteres): ")
    while len(clave) < 12:
        print("  Es muy corta (mínimo 12 caracteres).")
        clave = clave_oculta("Clave de acceso: ")
    if clave != clave_oculta("Repetila: "):
        raise SystemExit("Las dos claves no coinciden. Volvé a correr el script.")

    token = secrets.token_urlsafe(48)
    firma = secrets.token_urlsafe(48)
    ingreso = f"https://{PROYECTO}.supabase.co/functions/v1/editorial-ingest"

    privada = Path(tempfile.mkdtemp(prefix="editorial-"))
    try:
        env_file = privada / "secretos.env"
        fd = os.open(env_file, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for k, v in (("EDITORIAL_INGEST_TOKEN", token), ("EDITORIAL_AUTHOR_NAME", nombre),
                         ("EDITORIAL_AUTHOR_PHONE", celular), ("EDITORIAL_STAGING_FOLDER_ID", staging),
                         ("EDITORIAL_TARGET_FOLDER_ID", ensayo)):
                f.write(f'{k}="{v}"\n')
        print("→ Supabase: cargando secretos…")
        correr(["supabase", "secrets", "set", "--env-file", str(env_file), "--project-ref", PROYECTO, "--yes"])
    finally:
        shutil.rmtree(privada, ignore_errors=True)

    print("→ Vercel: cargando variables del conector…")
    correr(["vercel", "whoami"], cwd=CONECTOR)              # renueva la sesión de la CLI si venció
    for k, v in (("MCP_PUBLIC_URL", url), ("MCP_SIGNING_SECRET", firma), ("MCP_ACCESS_KEY", clave),
                 ("EDITORIAL_INGEST_URL", ingreso), ("EDITORIAL_INGEST_TOKEN", token),
                 ("MCP_FILE_HOSTS", "oaiusercontent.com,openai.com,chatgpt.com,anthropic.com,claude.ai")):
        correr(["vercel", "env", "add", k, "production", "--force",
                "--sensitive" if k in SECRETAS else "--no-sensitive"], entrada=v, cwd=CONECTOR)
    print("→ Vercel: publicando el conector con las variables nuevas…")
    correr(["vercel", "deploy", "--prod", "--yes"], cwd=CONECTOR)

    print("\n→ Prueba: carga de una imagen chiquita a la carpeta de ENSAYO…")
    sys.path.insert(0, str(RAIZ / "tools"))
    import editorial_ingest as EI
    prueba = Path(tempfile.mkdtemp(prefix="editorial-prueba-"))
    try:
        (prueba / "nota.txt").write_text("PRUEBA del ingreso editorial — no publicar.", encoding="utf-8")
        (prueba / "foto.png").write_bytes(png_prueba())
        os.environ.update(EDITORIAL_INGEST_URL=ingreso, EDITORIAL_INGEST_TOKEN=token)
        recibo = prueba / "recibo.json"
        ok = EI.run(["--nota", str(prueba / "nota.txt"), "--archivo", str(prueba / "foto.png"), "--lugar", "Chivilcoy",
                     "--acepto", "--recibo", str(recibo)]) == 0
        ok = ok and EI.run(["--estado", "--recibo", str(recibo)]) == 0
    finally:
        os.environ.pop("EDITORIAL_INGEST_TOKEN", None)
        shutil.rmtree(prueba, ignore_errors=True)
    if not ok:
        print("\nLa prueba NO salió bien: el ingreso sigue apuntando a la carpeta de ensayo. Avisale a Claude.")
        return 2
    print("\nLa prueba salió bien: en la carpeta de ENSAYO de Drive tiene que haber una carpeta nueva con "
          "contexto.txt, ingreso.json y la imagen.")
    if input("¿Paso el ingreso a producción (los envíos van a la carpeta de corresponsales)? [s/N] ").strip().lower() == "s":
        correr(["supabase", "secrets", "unset", "EDITORIAL_TARGET_FOLDER_ID", "--project-ref", PROYECTO, "--yes"])
        print("Listo: el ingreso editorial quedó en producción.")
    else:
        print("Queda en ensayo. Para pasarlo después: npx supabase secrets unset EDITORIAL_TARGET_FOLDER_ID")
    print(f"\nPara conectarlo en ChatGPT: {url}/mcp  (te va a pedir la clave que acabás de elegir).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
