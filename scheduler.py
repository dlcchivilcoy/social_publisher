from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler

from publisher import run_publish_cycle
from utils.logger import get_logger

logger = get_logger("scheduler")


def start(posts_folder: Path, allowed_pages: set[int], hour: int = 8, minute: int = 0) -> None:
    scheduler = BlockingScheduler(timezone="America/Argentina/Buenos_Aires")
    scheduler.add_job(
        run_publish_cycle,
        trigger="cron",
        hour=hour,
        minute=minute,
        args=[posts_folder, allowed_pages],
        coalesce=True,
        replace_existing=True,
        id="daily_publish",
    )
    logger.info(f"Scheduler iniciado — publicación diaria a las {hour:02d}:{minute:02d} (Buenos Aires)")
    logger.info(f"Carpeta de posts: {posts_folder}")
    logger.info(f"Páginas permitidas: {sorted(allowed_pages)}")
    logger.info("Presioná Ctrl+C para detener.")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler detenido.")
