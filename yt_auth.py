"""Autorización de YouTube por ÚNICA VEZ (local).

Abre el navegador, te logueás con la cuenta dlc.chivilcoy@gmail.com, ELEGÍS el canal y
autorizás. Guarda el token en un archivo gitignored (se refresca solo de ahí en más).

  python yt_auth.py            → token de RADIO DEL CENTRO (.yt_token.json), para el SEO.
  python yt_auth.py diario     → token de DIARIO LA CAMPAÑA (.yt_token_diario.json), para
                                 los Shorts del desgrabador. ⚠️ En la pantalla de Google,
                                 al elegir la cuenta, SELECCIONÁ el canal «Diario La Campaña»
                                 (NO «Radio del Centro»).

Antes de correr esto:
  1. Google Cloud Console → habilitá "YouTube Data API v3".
  2. Creá credenciales OAuth 2.0 tipo "Desktop app" → descargá el JSON como
     client_secret.json en la carpeta del proyecto (o seteá YT_OAUTH_CLIENT en .env).
"""
import sys

from platforms.youtube_api import SCOPES, TOKEN_FILE, _client_secret_path, _shorts_token_path
from utils.config import load_config


def main() -> None:
    load_config()
    diario = len(sys.argv) > 1 and sys.argv[1].lower() in ("diario", "shorts", "campana", "campaña")
    token_file = _shorts_token_path() if diario else TOKEN_FILE
    canal = "DIARIO LA CAMPAÑA" if diario else "RADIO DEL CENTRO"

    cs = _client_secret_path()
    if not cs.exists():
        print(f"❌ Falta el archivo de credenciales OAuth: {cs}\n"
              "Descargalo de Google Cloud Console (OAuth 2.0, tipo 'Desktop app') y guardalo ahí,\n"
              "o seteá YT_OAUTH_CLIENT=<ruta> en el .env.")
        sys.exit(1)

    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(cs), SCOPES)
    print(f"\nSe va a abrir el navegador. Logueate con dlc.chivilcoy@gmail.com y, cuando Google te "
          f"pida elegir, SELECCIONÁ el canal «{canal}» y autorizá.\n")
    creds = flow.run_local_server(port=0)
    token_file.write_text(creds.to_json(), encoding="utf-8")
    siguiente = ("python main.py --publish-video --file <video>" if diario
                 else "python main.py --yt-seo")
    print(f"\n[OK] Token de «{canal}» guardado en {token_file.name}. Ya podés correr: {siguiente}")


if __name__ == "__main__":
    main()
