"""Helper de scraping: pedidos HTTP con User-Agent de navegador real.

Varias webs (p. ej. dechivilcoy) bloquean el User-Agent por defecto y devuelven 403,
por eso usamos uno de navegador. Sin dependencias nuevas (requests ya está)."""
import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
_HEADERS = {"User-Agent": UA, "Accept-Language": "es-AR,es;q=0.9"}


def fetch_text(url: str, timeout: int = 25) -> str:
    r = requests.get(url, headers=_HEADERS, timeout=timeout)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or r.encoding
    return r.text


def fetch_bytes(url: str, timeout: int = 30) -> bytes:
    r = requests.get(url, headers=_HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.content
