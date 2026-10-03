"""Pronóstico del día en Chivilcoy para la HISTORIA de clima (08:00, pedido 2026-10-03).

Misma fuente que la página /clima de la web (diario_web/src/lib/clima.js): MET Norway,
api.met.no. Gratis, permite uso comercial y pide identificarse con un User-Agent propio
y citar la fuente (CC BY 4.0) — la historia lo dice al pie.
"""
from datetime import datetime, timedelta, timezone

import requests

from utils.logger import get_logger

logger = get_logger("clima")

URL = "https://api.met.no/weatherapi/locationforecast/2.0/complete?lat=-34.90&lon=-60.02"
UA = "DiarioLaCampana/1.0 https://www.xn--diariolacampaa-2nb.com.ar/clima"
AR = timezone(timedelta(hours=-3))  # Argentina no tiene horario de verano desde 2009

# Horas que se muestran en la fila de la historia (hora de Chivilcoy)
HORAS = (9, 12, 15, 18, 21)

# Código de MET (sin _day/_night) → (texto, tipo de ícono)
ESTADOS = {
    "clearsky": ("Despejado", "sol"),
    "fair": ("Mayormente despejado", "sol_nube"),
    "partlycloudy": ("Parcialmente nublado", "sol_nube"),
    "cloudy": ("Nublado", "nube"),
    "fog": ("Niebla", "niebla"),
    "lightrainshowers": ("Chaparrones débiles", "chaparron"),
    "rainshowers": ("Chaparrones", "chaparron"),
    "heavyrainshowers": ("Chaparrones fuertes", "chaparron"),
    "lightrain": ("Lluvia débil", "lluvia"),
    "rain": ("Lluvia", "lluvia"),
    "heavyrain": ("Lluvia fuerte", "lluvia"),
    "lightsleet": ("Aguanieve débil", "nieve"),
    "sleet": ("Aguanieve", "nieve"),
    "heavysleet": ("Aguanieve fuerte", "nieve"),
    "lightsnow": ("Nevadas débiles", "nieve"),
    "snow": ("Nieve", "nieve"),
    "heavysnow": ("Nieve intensa", "nieve"),
}

PUNTOS = ["norte", "noreste", "este", "sureste", "sur", "suroeste", "oeste", "noroeste"]


def estado(codigo: str | None) -> dict:
    """'partlycloudy_night' → {'texto': 'Parcialmente nublado', 'icono': 'luna_nube'}."""
    codigo = codigo or ""
    base = codigo.rsplit("_", 1)[0] if codigo.endswith(("_day", "_night", "_polartwilight")) else codigo
    noche = codigo.endswith("_night")
    if "thunder" in base:
        return {"texto": "Tormentas", "icono": "tormenta"}
    texto, icono = ESTADOS.get(base) or ESTADOS.get(base.replace("showers", "")) or ("Variable", "sol_nube")
    if noche:
        icono = {"sol": "luna", "sol_nube": "luna_nube"}.get(icono, icono)
    return {"texto": texto, "icono": icono}


def _kmh(ms) -> int:
    return round((ms or 0) * 3.6)


def _rumbo(grados) -> str:
    return PUNTOS[round((grados or 0) % 360 / 45) % 8]


def _local(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(AR)


def _simbolo(data: dict) -> str | None:
    return ((data.get("next_1_hours") or {}).get("summary", {}).get("symbol_code")
            or (data.get("next_6_hours") or {}).get("summary", {}).get("symbol_code"))


def pronostico_hoy() -> dict:
    """Resumen del día de HOY en Chivilcoy. Lanza si MET no responde.

    {fecha, ahora: {temp, sensacion, humedad, viento, rumbo, estado},
     max, min, lluvia (mm), viento_max (km/h), estado (del día),
     horas: [{hora, temp, estado}]}"""
    r = requests.get(URL, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    ts = r.json()["properties"]["timeseries"]
    hoy = datetime.now(AR).date()

    det0 = ts[0]["data"]["instant"]["details"]
    ahora = {
        "temp": round(det0["air_temperature"]),
        "sensacion": round(det0.get("apparent_air_temperature", det0["air_temperature"])),
        "humedad": round(det0.get("relative_humidity", 0)),
        "viento": _kmh(det0.get("wind_speed")),
        "rumbo": _rumbo(det0.get("wind_from_direction")),
        "estado": estado(_simbolo(ts[0]["data"])),
    }

    temps, lluvia, viento_max = [], 0.0, 0
    codigo_dia, hora_codigo = None, 99
    horas = {}
    for i, t in enumerate(ts):
        loc = _local(t["time"])
        if loc.date() != hoy:
            continue
        data = t["data"]
        det = data["instant"]["details"]
        temps.append(det["air_temperature"])
        viento_max = max(viento_max, _kmh(det.get("wind_speed")))
        # La serie viene de a 1 hora los primeros días y de a 6 después: la lluvia se suma
        # con el bloque que corresponde a cada paso, para no contar dos veces la misma.
        sig = _local(ts[i + 1]["time"]) if i + 1 < len(ts) else loc
        paso = (sig - loc).total_seconds() / 3600
        bloque = data.get("next_6_hours") if paso >= 6 else data.get("next_1_hours")
        lluvia += ((bloque or {}).get("details") or {}).get("precipitation_amount", 0) or 0
        # Máx/mín de los bloques de 6 h que terminan dentro del día (los puntuales solos
        # dan una máxima baja si el pico cae entre dos horas de la serie).
        b6 = (data.get("next_6_hours") or {}).get("details") or {}
        if loc.hour <= 18 and b6.get("air_temperature_max") is not None:
            temps += [b6["air_temperature_max"], b6["air_temperature_min"]]
        # Ícono del día: el bloque de 6 h que arranca más cerca de las 9 (igual que la web)
        c6 = (data.get("next_6_hours") or {}).get("summary", {}).get("symbol_code")
        if c6 and abs(loc.hour - 9) < abs(hora_codigo - 9):
            codigo_dia, hora_codigo = c6, loc.hour
        if loc.hour in HORAS and loc.hour not in horas:
            horas[loc.hour] = {"hora": loc.hour, "temp": round(det["air_temperature"]),
                               "estado": estado(_simbolo(data))}

    if not temps:
        raise ValueError("MET Norway no trajo datos para hoy")
    return {
        "fecha": hoy,
        "ahora": ahora,
        "max": round(max(temps)),
        "min": round(min(temps)),
        "lluvia": round(lluvia, 1),
        "viento_max": viento_max,
        "estado": estado(codigo_dia or _simbolo(ts[0]["data"])),
        "horas": [horas[h] for h in HORAS if h in horas],
    }
