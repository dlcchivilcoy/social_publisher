"""Autorización de TikTok por ÚNICA VEZ (local).

Genera el link para autorizar la app con tu cuenta de TikTok, toma el código de
la redirección y lo canjea por el token, que se guarda en `.tiktok_token.json`.
Después de esto, el reel se puede mandar a la bandeja sin volver a autorizar (el
token se refresca solo).

Según cómo esté `TIKTOK_REDIRECT_URI` en el .env, hay dos modos:
  - `http://localhost:PUERTO/callback/` → levanta un servidor local, abre el
    navegador y toma el código solo (es lo que exige TikTok para apps de
    escritorio: la solapa Desktop del portal no acepta direcciones web).
  - cualquier `https://...` → modo manual: pegás la URL a la que te redirigió.

Uso:
  1. Cargá en el .env (local): TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, TIKTOK_REDIRECT_URI
  2. python tiktok_auth.py
"""
import http.server
import secrets
import sys
import time
import urllib.parse
import webbrowser

import requests

from platforms.tiktok import SCOPES, TOKEN_URL, save_initial_token
from utils.config import get, load_config

AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
ESPERA_SEG = 300

PAGINA_OK = """<!doctype html><html lang="es"><meta charset="utf-8">
<title>Listo</title><body style="font-family:system-ui;text-align:center;padding:60px">
<h2 style="color:#e2620c">✅ Autorización recibida</h2>
<p>Ya podés cerrar esta pestaña y volver al programa.</p></body></html>"""


class _Handler(http.server.BaseHTTPRequestHandler):
    """Atiende la vuelta de TikTok y guarda los parámetros en el servidor."""

    def do_GET(self):  # noqa: N802 (nombre impuesto por BaseHTTPRequestHandler)
        q = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(q)
        if "code" in params or "error" in params:
            self.server.recibido = {k: v[0] for k, v in params.items()}
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(PAGINA_OK.encode("utf-8"))
        else:
            self.send_response(204)  # favicon y demás ruido del navegador
            self.end_headers()

    def log_message(self, *_):
        pass  # sin ruido en la consola


def _esperar_en_localhost(url: str, redirect: str) -> dict:
    """Abre el navegador y espera la vuelta de TikTok en el puerto del redirect."""
    puerto = urllib.parse.urlparse(redirect).port or 80
    servidor = http.server.HTTPServer(("127.0.0.1", puerto), _Handler)
    servidor.recibido = None

    print(f"\nAbriendo el navegador para autorizar (esperando en el puerto {puerto})…")
    print("Si no se abre solo, entrá a este link:\n")
    print(url + "\n")
    webbrowser.open(url)

    # El navegador manda pedidos sueltos (favicon y demás) antes de la vuelta buena,
    # así que se atiende hasta que llegue el code o hasta que venza la espera.
    vence = time.monotonic() + ESPERA_SEG
    while servidor.recibido is None:
        restante = vence - time.monotonic()
        if restante <= 0:
            print("⌛ Se venció la espera. ¿Autorizaste en el navegador?")
            break
        servidor.timeout = restante
        servidor.handle_request()
    servidor.server_close()
    return servidor.recibido or {}


def _pedir_a_mano(url: str) -> dict:
    print("\n1) Abrí este link en el navegador (logueado con la cuenta del diario):\n")
    print(url)
    print("\n2) Autorizá. Te va a redirigir con algo como '?code=XXXX&state=...'.")
    print("   Copiá la URL COMPLETA de la barra de direcciones y pegala acá abajo.\n")
    pegado = input("URL de redirección (o solo el code): ").strip()
    if "code=" not in pegado:
        return {"code": pegado}
    q = urllib.parse.urlparse(pegado).query
    return {k: v[0] for k, v in urllib.parse.parse_qs(q).items()}


def main() -> None:
    load_config()
    ck = get("TIKTOK_CLIENT_KEY")
    cs = get("TIKTOK_CLIENT_SECRET")
    redirect = get("TIKTOK_REDIRECT_URI")
    if not (ck and cs and redirect):
        print("Faltan TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET / TIKTOK_REDIRECT_URI en el .env")
        sys.exit(1)

    state = secrets.token_urlsafe(8)
    url = AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "client_key": ck,
        "scope": SCOPES,
        "response_type": "code",
        "redirect_uri": redirect,
        "state": state,
    })

    es_local = urllib.parse.urlparse(redirect).hostname in ("localhost", "127.0.0.1")
    vuelta = _esperar_en_localhost(url, redirect) if es_local else _pedir_a_mano(url)

    if not vuelta or "code" not in vuelta:
        print(f"\n❌ No se recibió el código. Respuesta: {vuelta or 'nada'}")
        sys.exit(1)
    if vuelta.get("state") and vuelta["state"] != state:
        print("⚠️  El 'state' no coincide; seguí solo si confiás en el origen.")

    # TikTok a veces agrega un sufijo *? al code; lo limpiamos
    code = urllib.parse.unquote(vuelta["code"]).split("&")[0].rstrip("#/")

    r = requests.post(TOKEN_URL, data={
        "client_key": ck, "client_secret": cs,
        "code": code, "grant_type": "authorization_code",
        "redirect_uri": redirect,
    }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
    if not r.ok:
        print(f"\n❌ Error al canjear el código ({r.status_code}): {r.text[:400]}")
        sys.exit(1)
    save_initial_token(r.json())
    print("\n✅ Token guardado en .tiktok_token.json. Ya podés mandar reels a la bandeja de TikTok.")


if __name__ == "__main__":
    main()
