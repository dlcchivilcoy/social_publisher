"""Lectura de la API de Meta (Graph) para las métricas: cuenta, posteos e historias.

Dos reglas de esta capa:
- NUNCA se loguea una respuesta cruda: los links de paginación de Facebook traen el token adentro.
- Las métricas de Meta cambian solas (se retiran, se renombran, no valen para un tipo de posteo). Cada
  pedido de insights prueba la lista entera; si Meta rechaza una métrica, se prueban de a una, se
  anotan las que no valen (`invalidas`, se reintentan a los 14 días) y se sigue con las demás.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import requests

GRAPH = "https://graph.facebook.com/v26.0"
TZ_AR = timezone(timedelta(hours=-3))
DIAS_REPROBAR = 14

# Permisos que hacen falta para cada parte (los demás datos salen con los permisos de publicar).
PERMISO_IG = "instagram_manage_insights"
PERMISO_FB = "read_insights"
PERMISO_FB_POSTEOS = "pages_read_user_content"


class SinPermiso(Exception):
    pass


class ErrorMeta(Exception):
    def __init__(self, codigo, mensaje):
        super().__init__(f"({codigo}) {mensaje}")
        self.codigo, self.mensaje = codigo, mensaje


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


class Meta:
    def __init__(self, token: str, invalidas: dict | None = None, get=None, pausa: float = 0.2):
        self.token = token
        # {clave: {métrica: cuándo se rechazó}}: es parte del estado (state/metricas_redes.json).
        self.invalidas = invalidas if invalidas is not None else {}
        self._get_http = get or self._http
        self.pausa = pausa
        self.llamadas = 0

    # ------------------------------------------------------------------ http
    def _http(self, url: str, params: dict) -> dict:
        for intento in range(3):
            try:
                r = requests.get(url, params=params, timeout=60)
                d = r.json()
            except (requests.RequestException, ValueError) as e:
                if intento == 2:
                    raise ErrorMeta("red", str(e)[:200]) from None
                time.sleep(3 * (intento + 1))
                continue
            err = d.get("error") if isinstance(d, dict) else None
            if err and (err.get("is_transient") or err.get("code") in (1, 2, 4, 17, 32, 613)) and intento < 2:
                time.sleep(5 * (intento + 1))
                continue
            return d
        return {}

    def get(self, ruta: str, **params) -> dict:
        params["access_token"] = self.token
        self.llamadas += 1
        if self.pausa:
            time.sleep(self.pausa)
        d = self._get_http(f"{GRAPH}/{ruta}", params) or {}
        err = d.get("error")
        if err:
            raise ErrorMeta(err.get("code"), (err.get("message") or "")[:300])
        return d

    def paginas(self, ruta: str, hasta: datetime, campo_fecha: str, **params):
        """Recorre una lista de más nuevo a más viejo hasta `hasta` (sin seguir los links con el token:
        se pagina con el cursor `after`)."""
        params.setdefault("limit", 50)
        for _ in range(60):
            try:
                d = self.get(ruta, **params)
            except ErrorMeta as e:
                # «Please reduce the amount of data you're asking for»: la misma página, más chica.
                if e.codigo == 1 and params["limit"] > 5:
                    params["limit"] = max(5, params["limit"] // 2)
                    continue
                raise
            filas = d.get("data") or []
            for f in filas:
                t = _fecha(f.get(campo_fecha))
                if t and t < hasta:
                    return
                yield f
            despues = ((d.get("paging") or {}).get("cursors") or {}).get("after")
            if not filas or not despues or not (d.get("paging") or {}).get("next"):
                return
            params["after"] = despues

    # ------------------------------------------------------------------ permisos
    def permisos(self) -> set:
        d = self.get("debug_token", input_token=self.token)
        return set((d.get("data") or {}).get("scopes") or [])

    # ------------------------------------------------------------------ insights adaptativos
    def _vigentes(self, clave: str, candidatas: list) -> list:
        malas = self.invalidas.get(clave) or {}
        limite = _iso(datetime.now(timezone.utc) - timedelta(days=DIAS_REPROBAR))
        return [m for m in candidatas if not (m in malas and malas[m] >= limite)]

    def insights(self, objeto: str, candidatas: list, clave: str, ruta: str = "insights", **params) -> dict:
        """{métrica: valor} de lo que Meta acepte. `clave` agrupa el tipo de objeto (ig_REELS, fb_page…)."""
        return self._adaptivo(objeto, candidatas, clave, ruta, params, _leer_insights)

    def insights_serie(self, objeto: str, candidatas: list, clave: str, **params) -> dict:
        """{métrica: {fecha: valor}} de una consulta con period=day."""
        return self._adaptivo(objeto, candidatas, clave, "insights", params, serie)

    def _adaptivo(self, objeto, candidatas, clave, ruta, params, lector) -> dict:
        metricas = self._vigentes(clave, candidatas)
        if not metricas:
            return {}
        try:
            return lector(self.get(f"{objeto}/{ruta}", metric=",".join(metricas), **params))
        except ErrorMeta as e:
            if _es_permiso(e):
                raise SinPermiso(str(e)) from None
            if e.codigo != 100 or len(metricas) == 1:
                if e.codigo == 100:
                    self._marcar(clave, metricas[0])
                    return {}
                raise
        out = {}
        for m in metricas:                      # alguna no vale: de a una
            try:
                out.update(lector(self.get(f"{objeto}/{ruta}", metric=m, **params)))
            except ErrorMeta as e:
                if _es_permiso(e):
                    raise SinPermiso(str(e)) from None
                if e.codigo == 100:
                    self._marcar(clave, m)
                else:
                    raise
        return out

    def _marcar(self, clave: str, metrica: str):
        self.invalidas.setdefault(clave, {})[metrica] = _iso(datetime.now(timezone.utc))


def _es_permiso(e: ErrorMeta) -> bool:
    return e.codigo in (10, 200, 190) or "permission" in (e.mensaje or "").lower()


def _fecha(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00").replace("+0000", "+00:00"))
    except ValueError:
        return None


def _leer_insights(d: dict) -> dict:
    """Aplana la respuesta de insights: total_value (con o sin desglose) o el último valor de la serie."""
    out = {}
    for it in d.get("data") or []:
        nombre = it.get("name")
        tv = it.get("total_value")
        if isinstance(tv, dict):
            desglose = {}
            for b in tv.get("breakdowns") or []:
                for r in b.get("results") or []:
                    desglose["|".join(r.get("dimension_values") or [])] = r.get("value")
            out[nombre] = desglose if desglose else tv.get("value")
            continue
        vals = it.get("values") or []
        if vals:
            out[nombre] = vals[-1].get("value")
    return out


def serie(d: dict) -> dict:
    """{métrica: {fecha AR: valor}} de una respuesta con period=day (fecha = día que cierra end_time)."""
    out = {}
    for it in d.get("data") or []:
        for v in it.get("values") or []:
            t = _fecha(v.get("end_time"))
            if not t:
                continue
            dia = (t - timedelta(hours=12)).astimezone(TZ_AR).date().isoformat()
            out.setdefault(it.get("name"), {})[dia] = v.get("value")
    return out
