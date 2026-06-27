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
        "--newsletter",
        action="store_true",
        help="Enviar el newsletter de la mañana (titulares + PDF) a los suscriptores de la web (Supabase).",
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
        "--tapa-farmacias",
        action="store_true",
        help="CARRUSEL Tapa+Farmacias en FB/IG (tapa 1°, farmacias 2°) + historia de la tapa.",
    )
    parser.add_argument(
        "--notes-web",
        action="store_true",
        help="SOLO carga las notas del día a la web (Wix), sin tocar FB/IG (corrida de las 7:00).",
    )
    parser.add_argument(
        "--notes-carousel",
        action="store_true",
        help="CARRUSEL con todas las notas del día en FB/IG + 1 historia 'Noticias de hoy' (cada nota va a Wix).",
    )
    parser.add_argument(
        "--reel",
        action="store_true",
        help="Publicar el REEL 'Las 5 más leídas del día' (video 9:16) en FB+IG (feed + historia).",
    )
    parser.add_argument(
        "--transcribe-video",
        action="store_true",
        help="DESGRABAR un video (Drive) a nota en Wix como BORRADOR + reel listo (etapa 1, con --file/--uploader).",
    )
    parser.add_argument(
        "--publish-video",
        action="store_true",
        help="PUBLICAR el borrador del video aprobado: web + reel a FB/IG (etapa 2, con --file).",
    )
    parser.add_argument(
        "--videos-report",
        action="store_true",
        help="Enviar por mail el Excel de contabilidad de videos por colaborador (mes anterior, o --mes YYYY-MM).",
    )
    parser.add_argument(
        "--yt-seo",
        action="store_true",
        help="Generar propuestas SEO (título, descripción, miniatura) de los últimos videos de YouTube. Revisión manual.",
    )
    parser.add_argument(
        "--yt-seo-apply",
        action="store_true",
        help="Aplicar en YouTube las propuestas marcadas con 'aplicar': true en youtube_seo/propuestas.json.",
    )
    parser.add_argument(
        "--yt-seo-auto",
        action="store_true",
        help="AUTOMÁTICO: a los videos subidos HOY les aplica solo título+descripción+miniatura nuevos (con registro anti-repetición).",
    )
    parser.add_argument(
        "--yt-desgrabar",
        action="store_true",
        help="DESGRABAR a texto + miniatura las notas de YouTube de hoy (Radio del Centro) y mandarlas por mail (local, Gemini, 14:30).",
    )
    parser.add_argument(
        "--notas-web",
        action="store_true",
        help="Publicar a la WEB (Wix) + reel a FB/IG las notas nuevas de la carpeta «notas para web» (Word + foto + video por subcarpeta).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=15,
        help="Cantidad de videos a procesar para --yt-seo (por defecto 15).",
    )
    parser.add_argument(
        "--file",
        type=str,
        default=None,
        help="Nombre del archivo de video (para --transcribe-video / --publish-video).",
    )
    parser.add_argument(
        "--uploader",
        type=str,
        default=None,
        help="Email del colaborador que subió el video (para --transcribe-video, lo pasa el Apps Script).",
    )
    parser.add_argument(
        "--mes",
        type=str,
        default=None,
        help="Mes YYYY-MM para --videos-report (por defecto, el mes anterior).",
    )
    parser.add_argument(
        "--folder",
        type=str,
        default=None,
        help="Ruta a la carpeta de posts (por defecto: valor de POSTS_FOLDER en .env).",
    )
    parser.add_argument(
        "--pages",
        type=str,
        default=None,
        help="Override de páginas SOLO para esta corrida (ej: 3,5,7). Si se omite, usa ALLOWED_PAGES.",
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
    if args.pages:  # override por corrida (ej: 7am=3,5,7 / 13hs=8,9)
        override = {int(p.strip()) for p in args.pages.split(",") if p.strip().isdigit()}
        if override:
            pages = override
            logger.info(f"Páginas override para esta corrida: {sorted(pages)}")

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
    if args.newsletter:
        from newsletter import run_newsletter
        logger.info(f"Modo --newsletter (dry_run={args.dry_run}).")
        run_newsletter(dry_run=args.dry_run)
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
    if args.tapa_farmacias:
        from carrusel_tapa_farmacias import run_tapa_farmacias
        logger.info(f"Modo --tapa-farmacias (dry_run={args.dry_run}).")
        run_tapa_farmacias(dry_run=args.dry_run)
        return
    if args.notes_web:
        from carrusel_notas import run_notes_web
        logger.info(f"Modo --notes-web (dry_run={args.dry_run}). Carpeta: {folder}")
        run_notes_web(folder, pages, dry_run=args.dry_run)
        return
    if args.notes_carousel:
        from carrusel_notas import run_notes_carousel
        logger.info(f"Modo --notes-carousel (dry_run={args.dry_run}). Carpeta: {folder}")
        run_notes_carousel(folder, pages, dry_run=args.dry_run)
        return
    if args.reel:
        from reel import run_reel
        logger.info(f"Modo --reel (dry_run={args.dry_run}).")
        run_reel(dry_run=args.dry_run)
        return
    if args.transcribe_video:
        from transcriber import run_transcribe_video
        logger.info(f"Modo --transcribe-video (dry_run={args.dry_run}). file={args.file}")
        run_transcribe_video(file=args.file or "", uploader=args.uploader or "", dry_run=args.dry_run)
        return
    if args.publish_video:
        from transcriber import run_publish_video
        logger.info(f"Modo --publish-video (dry_run={args.dry_run}). file={args.file}")
        run_publish_video(file=args.file or "", dry_run=args.dry_run)
        return
    if args.videos_report:
        from reporte import run_videos_report
        logger.info(f"Modo --videos-report (dry_run={args.dry_run}). mes={args.mes}")
        run_videos_report(mes=args.mes, dry_run=args.dry_run)
        return
    if args.yt_seo:
        from youtube_seo import run_generate
        logger.info(f"Modo --yt-seo (limit={args.limit}, dry_run={args.dry_run}).")
        run_generate(limit=args.limit, dry_run=args.dry_run)
        return
    if args.yt_seo_apply:
        from youtube_seo import run_apply
        logger.info(f"Modo --yt-seo-apply (dry_run={args.dry_run}).")
        run_apply(dry_run=args.dry_run)
        return
    if args.yt_seo_auto:
        from youtube_seo import run_auto
        logger.info(f"Modo --yt-seo-auto (dry_run={args.dry_run}).")
        run_auto(dry_run=args.dry_run)
        return
    if args.yt_desgrabar:
        from yt_desgrabador import run_yt_desgrabar
        logger.info(f"Modo --yt-desgrabar (dry_run={args.dry_run}).")
        run_yt_desgrabar(dry_run=args.dry_run)
        return
    if args.notas_web:
        from notas_web import run_notas_web
        logger.info(f"Modo --notas-web (dry_run={args.dry_run}).")
        run_notas_web(dry_run=args.dry_run)
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
