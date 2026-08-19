"""Aviso por mail cuando una corrida de la NUBE (GitHub Actions) FALLA o se CANCELA.

Lo llama el workflow en el ultimo paso, con `if: failure() || cancelled()`. Es
self-contained (mismo patron de mail que el resto del publicador: MAIL_FROM /
MAIL_APP_PASSWORD / SMTP_HOST / destino VIDEOS_NOTIFY_EMAIL|FARMACIAS_NOTIFY_EMAIL).

Solo avisa para las corridas de PUBLICACION del diario (notas / historias /
tapa-farmacias / notas-web). Los desgrabadores de video, la radio, corresponsales,
yt-seo y yt-desgrabar ya mandan su PROPIO aviso y reintentan solos, asi que se
saltean aca para no duplicar mails.

Entradas por variables de entorno (las setea el workflow):
  CMD_ARGS   -> los args de main.py de esa corrida (ej: "--notes-web")
  OUTCOME    -> "cancelada" | "fallida" (si no viene, se deduce de WAS_CANCELLED)
  WAS_CANCELLED / WAS_FAILURE -> "true"/"false" (de cancelled()/failure())
  RUN_URL    -> link a la corrida en GitHub Actions
  NOTIFY_DRYRUN=1 -> imprime el mail en vez de enviarlo (para probar).
"""
import os
import ssl
import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from utils.config import get, load_config

# Tokens de los comandos de PUBLICACION que SI ameritan aviso si no salen.
PUBLICACION_TOKENS = (
    "notes-web", "notes-carousel", "run-now", "news-stories",
    "farmacias", "tapa", "muro", "yt-live", "yt-notes", "canal-story",
    "notas-web", "sepelios",
)


def _es_publicacion(args: str) -> bool:
    return any(tok in args for tok in PUBLICACION_TOKENS)


def _enviar(asunto: str, cuerpo: str) -> bool:
    remitente = get("MAIL_FROM")
    password = get("MAIL_APP_PASSWORD")
    destino = (get("VIDEOS_NOTIFY_EMAIL") or get("FARMACIAS_NOTIFY_EMAIL")
               or get("MAIL_FROM") or "").strip()
    if not remitente or not password or not destino:
        print("[notificar] sin credenciales de mail (MAIL_FROM/MAIL_APP_PASSWORD/destino); no se envia.")
        return False
    msg = EmailMessage()
    msg["From"] = formataddr((get("MAIL_FROM_NAME") or "Diario La Campaña", remitente))
    msg["To"] = destino
    msg["Subject"] = asunto
    msg.set_content(cuerpo)
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(get("SMTP_HOST") or "smtp.gmail.com",
                          int(get("SMTP_PORT") or 587), timeout=60) as s:
            s.starttls(context=ctx)
            s.login(remitente, password)
            s.send_message(msg)
        print(f"[notificar] aviso enviado a {destino}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[notificar] no se pudo enviar el aviso: {e}")
        return False


def main() -> None:
    load_config()  # carga el .env a os.environ (get() solo lee os.getenv)
    args = (os.environ.get("CMD_ARGS") or "").strip() or "(desconocido)"
    run_url = (os.environ.get("RUN_URL") or "").strip()

    outcome = (os.environ.get("OUTCOME") or "").strip()
    if not outcome:
        cancelada = (os.environ.get("WAS_CANCELLED") or "").strip().lower() == "true"
        outcome = "cancelada" if cancelada else "fallida"

    if not _es_publicacion(args):
        print(f"[notificar] la corrida ({args}) no es de publicacion del diario; "
              "no se avisa (esos flujos ya avisan solos). Fin.")
        return

    asunto = f"⚠️ Publicador: corrida {outcome} — {args}"
    cuerpo = (
        f"Una corrida de PUBLICACION en la nube quedo {outcome} y puede que ese "
        f"contenido NO haya salido.\n\n"
        f"Comando:  main.py {args}\n"
        f"Estado:   {outcome}\n"
        f"Detalle/logs:  {run_url or '(sin URL)'}\n\n"
        "Que revisar segun el comando:\n"
        "  • --notes-web / --notes-carousel / --run-now  -> notas en web / Facebook / carrusel IG\n"
        "  • --news-stories / --yt-live / --yt-notes / --canal-story  -> historias\n"
        "  • --tapa-farmacias / --farmacias / --muro-...  -> tapa y farmacias\n\n"
        "Si hizo falta, se puede republicar a mano (localmente) esa parte.\n\n"
        "— Publicador Diario La Campaña"
    )

    if (os.environ.get("NOTIFY_DRYRUN") or "").strip() == "1":
        print("=== [DRY-RUN] no se envia; asi saldria el mail ===")
        print("Para:", (get("VIDEOS_NOTIFY_EMAIL") or get("FARMACIAS_NOTIFY_EMAIL")
                         or get("MAIL_FROM") or "(sin destino)"))
        print("Asunto:", asunto)
        print(cuerpo)
        return

    _enviar(asunto, cuerpo)


if __name__ == "__main__":
    main()
