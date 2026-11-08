import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Claves que algunas librerías meten en el texto de sus errores (2026-11-08: un corte de red de Gemini
# dejó la URL con `?key=…` entera en el log, y los logs de la nube de un repo público los ve cualquiera).
_SECRETO = re.compile(r"(?i)\b((?:api_?)?key|access_token|token|client_secret|password)=([^&\s\"'<>)]{6,})")


class _SinSecretos(logging.Formatter):
    def format(self, record):
        return _SECRETO.sub(lambda m: f"{m.group(1)}=***", super().format(record))

_LOG_FILE = Path(__file__).parent.parent / "logs" / "publisher.log"
_LOG_FILE.parent.mkdir(exist_ok=True)

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    fmt = _SinSecretos(
        "%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = RotatingFileHandler(_LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    ch.setLevel(logging.INFO)

    logger.addHandler(fh)
    logger.addHandler(ch)
    return logger
