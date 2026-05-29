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


def cmd_run_now(folder: Path) -> None:
    from publisher import run_publish_cycle
    run_publish_cycle(folder)


def cmd_start_scheduler(folder: Path, hour: int, minute: int) -> None:
    from scheduler import start
    start(folder, hour=hour, minute=minute)


def main() -> None:
    load_config()

    parser = argparse.ArgumentParser(
        description="Social Media Auto-Publisher — publica pares foto+texto en Wix, Facebook, Instagram y X."
    )
    parser.add_argument(
        "--run-now",
        action="store_true",
        help="Ejecutar una publicación inmediata y salir (útil para testing).",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Verificar que todas las variables de .env estén configuradas y salir.",
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
        default=int(get("SCHEDULE_HOUR") or 10),
        help="Hora de publicación diaria (0-23, por defecto 10).",
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

    if args.run_now:
        logger.info(f"Modo --run-now. Carpeta: {folder}")
        cmd_run_now(folder)
    else:
        cmd_start_scheduler(folder, args.hour, args.minute)


if __name__ == "__main__":
    main()
