"""Autorización de TikTok por ÚNICA VEZ (local).

Genera el link para autorizar la app con tu cuenta de TikTok, toma el código que
queda en la URL de redirección y lo canjea por el token, que se guarda en
`.tiktok_token.json`. Después de esto, el reel se puede mandar a la bandeja sin
volver a autorizar (el token se refresca solo).

Uso:
  1. Cargá en el .env (local): TIKTOK_CLIENT_KEY, TIKTOK_CLIENT_SECRET, TIKTOK_REDIRECT_URI
  2. python tiktok_auth.py
  3. Abrí el link, autorizá con la cuenta del diario, y pegá acá la URL a la que te redirigió.
"""
import secrets
import sys
import urllib.parse

import requests

from platforms.tiktok import SCOPES, TOKEN_URL, save_initial_token
from utils.config import get, load_config

AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"


def main() -> None:
    load_config()
    ck = get("TIKTOK_CLIENT_KEY")
    cs = get("TIKTOK_CLIENT_SECRET")
    redirect = get("TIKTOK_REDIRECT_URI")
    if not (ck and cs and redirect):
        print("Faltan TIKTOK_CLIENT_KEY / TIKTOK_CLIENT_SECRET / TIKTOK_REDIRECT_URI en el .env")
        sys.exit(1)

    state = secrets.token_urlsafe(8)
    params = {
        "client_key": ck,
        "scope": SCOPES,
        "response_type": "code",
        "redirect_uri": redirect,
        "state": state,
    }
    url = AUTHORIZE_URL + "?" + urllib.parse.urlencode(params)
    print("\n1) Abrí este link en el navegador (logueado con la cuenta de TikTok del diario):\n")
    print(url)
    print("\n2) Autorizá. Te va a redirigir a tu web con algo como '?code=XXXX&state=...'.")
    print("   Copiá la URL COMPLETA de la barra de direcciones y pegala acá abajo.\n")

    redirected = input("URL de redirección (o solo el code): ").strip()
    code = redirected
    if "code=" in redirected:
        q = urllib.parse.urlparse(redirected).query
        params_back = urllib.parse.parse_qs(q)
        code = params_back.get("code", [""])[0]
        got_state = params_back.get("state", [""])[0]
        if got_state and got_state != state:
            print("⚠️  El 'state' no coincide; seguí solo si confiás en la URL pegada.")
    # TikTok a veces agrega un sufijo *? al code; lo limpiamos
    code = urllib.parse.unquote(code).split("&")[0].rstrip("#/")

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
