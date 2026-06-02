import argparse
import sys
from pathlib import Path

from utils.config import get, load_config, validate_config
from utils.logger import get_logger

logger = get_logger("main")


def _default_folder() -> Path:
    raw = get("POSTS_FOLDER")
    if raw:
        return Path(raw)
    return Path.home() / "Desktop" / "NOTAS AUTOMATICAS"


def _allowed_pages() -> set[int]:
    raw = get("ALLOWED_PAGES") or "2,3,5,7,8,9"
    return {int(p.strip()) for p in raw.split(",") if p.strip().isdigit()}


def cmd_check_config() -> None:
    missing = validate_config()
    if missing:
        print("\n[!] Faltan las siguientes variables en .env:\n")
        for key in missing:
            print(f"    {key}")
        print("\nCopiá .env.example a .env y completá los valores.")
        sys.exit(1)
    else:
        print("\n[OK] Todas las variables de configuración están presentes.\n")


def main() -> None:
    load_config()

    parser = argparse.ArgumentParser(
        description="Social Media Auto-Publisher — publica notas (foto+Word) en Wix, Facebook, Instagram y X."
    )
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="Ejecutar una publicación inmediata y salir.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostrar qué notas y fotos se emparejarían, SIN publicar nada.",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Verificar que todas las variables de .env estén configuradas y salir.",
    )
    parser.add_argument(
        "--news-stories",
        action="store_true",
        help="Publicar HISTORIAS (stories) de las notas del diario de hoy en IG+FB.",
    )
    parser.add_argument(
        "--yt-live",
        action="store_true",
        help="Publicar la HISTORIA del vivo de YouTube (si el canal está en vivo).",
    )
    parser.add_argument(
        "--yt-notes",
        action="store_true",
        help="Publicar HISTORIAS de las notas de YouTube subidas hoy (excluye el programa completo).",
    )
    parser.add_argument(
        "--tapa",
        action="store_true",
        help="Publicar la TAPA del diario (imagen de la carpeta del PDF) en muro + historia de IG y FB.",
    )
    parser.add_argument(
        "--canal-story",
        action="store_true",
        help="Publicar la HISTORIA promo del Canal de WhatsApp (con QR) en IG+FB.",
    )
    parser.add_argument(
        "--repost",
        action="store_true",
        help="Repostear como historia la publicidad de comercios que mencionen a @dlcchivilcoy.",
    )
    parser.add_argument(
        "--mail",
        action="store_true",
        help="Enviar el PDF del diario por correo a los clientes con MAIL en la planilla.",
    )
    parser.add_argument(
        "--sepelios",
        action="store_true",
        help="Publicar los SEPELIOS de Chivilcoy del día (Wix+FB+IG, muro + historia).",
    )
    parser.add_argument(
        "--farmacias",
        action="store_true",
        help="Publicar las FARMACIAS de turno de hoy (Wix+FB+IG, muro + historia).",
    )
    parser.add_argument(
        "--folder",
        type=str,
        default=None,
        help="Ruta a la carpeta de posts (por defecto: valor de POSTS_FOLDER en .env).",
    )
    parser.add_argument(
        "--hour",
        type=int,
        default=int(get("SCHEDULE_HOUR") or 8),
        help="Hora de publicación diaria (0-23, por defecto 8).",
    )
    parser.add_argument(
        "--minute",
        type=int,
        default=int(get("SCHEDULE_MINUTE") or 0),
        help="Minuto de publicación diaria (0-59, por defecto 0).",
    )

    args = parser.parse_args()

    if args.check_config:
        cmd_check_config()
        return

    folder = Path(args.folder) if args.folder else _default_folder()
    pages = _allowed_pages()

    # --- Historias (stories) ---
    if args.news_stories:
        from stories import run_news_stories
        logger.info(f"Modo --news-stories (dry_run={args.dry_run}). Carpeta: {folder}")
        run_news_stories(folder, pages, dry_run=args.dry_run)
        return
    if args.yt_live:
        from stories import run_youtube_live_story
        logger.info(f"Modo --yt-live (dry_run={args.dry_run}).")
        run_youtube_live_story(dry_run=args.dry_run)
        return
    if args.yt_notes:
        from stories import run_youtube_notes_stories
        logger.info(f"Modo --yt-notes (dry_run={args.dry_run}).")
        run_youtube_notes_stories(dry_run=args.dry_run)
        return
    if args.tapa:
        from tapa import run_tapa
        logger.info(f"Modo --tapa (dry_run={args.dry_run}).")
        run_tapa(dry_run=args.dry_run)
        return
    if args.canal_story:
        from stories import run_canal_story
        logger.info(f"Modo --canal-story (dry_run={args.dry_run}).")
        run_canal_story(dry_run=args.dry_run)
        return
    if args.repost:
        from repost import run_repost
        logger.info(f"Modo --repost (dry_run={args.dry_run}).")
        run_repost(dry_run=args.dry_run)
        return
    if args.mail:
        from mailer import run_mail
        logger.info(f"Modo --mail (dry_run={args.dry_run}).")
        run_mail(dry_run=args.dry_run)
        return
    if args.sepelios:
        from sepelios import run_sepelios
        logger.info(f"Modo --sepelios (dry_run={args.dry_run}).")
        run_sepelios(dry_run=args.dry_run)
        return
    if args.farmacias:
        from farmacias import run_farmacias
        logger.info(f"Modo --farmacias (dry_run={args.dry_run}).")
        run_farmacias(dry_run=args.dry_run)
        return

    if args.dry_run:
        from publisher import run_publish_cycle
        logger.info(f"Modo --dry-run. Carpeta: {folder}")
        run_publish_cycle(folder, pages, dry_run=True)
    elif args.run_now:
        from publisher import run_publish_cycle
        logger.info(f"Modo --run-now. Carpeta: {folder}")
        run_publish_cycle(folder, pages, dry_run=False)
    else:
        from scheduler import start
        start(folder, pages, hour=args.hour, minute=args.minute)


if __name__ == "__main__":
    main()
