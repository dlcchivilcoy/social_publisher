"""A qué corrida, categoría y formato pertenece cada posteo.

Primero se busca el id en los registros de policiales-bsas (repo público: policiales, nacionales y el
archivo). Lo que no está ahí lo publicó este repo o una persona: se reconoce por el texto. La categoría
sale del registro cuando la hay; si no, de palabras clave del texto. Sin IA.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

TZ_AR = timezone(timedelta(hours=-3))
RAW = "https://raw.githubusercontent.com/dlcchivilcoy/policiales-bsas/main/estado/{}"
DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")

CATEGORIAS = {      # nombre que se muestra
    "policiales": "Policiales", "politica": "Política", "economia": "Economía", "sociedad": "Sociedad",
    "deportes": "Deportes", "espectaculos": "Espectáculos", "cine_series": "Cine y series",
    "tecnologia_ia": "Tecnología / IA", "internacionales": "Mundo", "virales": "Virales",
    "cultura": "Cultura y eventos", "educacion": "Educación", "salud": "Salud", "clima": "Clima",
    "agro": "Agro", "transito": "Tránsito", "historia": "Historia / efemérides", "servicios": "Servicios",
    "pregunta": "Pregunta del día", "general": "General",
}

# Palabras clave por categoría para lo que no trae categoría de un registro. Cada una se busca al
# principio de una palabra (así «detenid» toma detenido/detenida y «liga» no toma «obliga»); con «$» al
# final tiene que ser la palabra entera; «_» une las de dos palabras.
_CLAVES = [
    ("policiales", "detenid aprehendid allanamient robo$ robaron hurto delincuent policia comisaria imputad "
                   "secuestr narco femicid homicid asesin disparo armas$ operativo_policial estafa causa_penal "
                   "fiscalia"),
    ("transito", "choque choco vuelco accidente transito colision despiste motociclista"),
    ("clima", "lluvia tormenta alerta_amarilla alerta_naranja temperatura pronostico smn$ granizo"),
    ("deportes", "futbol basquet partido torneo campeon medalla rally atleta liga$ gol$ goles club$ maraton "
                 "bochas voley tenis juegos_bonaerenses automovilismo hockey natacion"),
    ("agro", "soja maiz trigo cosecha siembra productor ganader hacienda granos rural"),
    ("politica", "intendente concejal concejo diputad senad milei kicillof gobierno elecciones candidat "
                 "britos ministro presidente legislatura oficialismo oposicion"),
    ("economia", "precio inflacion dolar salario jubilacion anses aumento tarifa economi comercio pymes "
                 "impuesto"),
    ("salud", "hospital salud medic vacuna enfermedad dengue paciente sanatorio"),
    ("educacion", "escuela alumnos docente universidad estudiantes educacion jardin$ colegio"),
    ("cultura", "festival teatro musica banda_de show$ muestra artist feria fiesta folklor espectaculo cine$ "
                "libro concierto"),
    ("historia", "efemeride un_dia_como_hoy fundacion_de historia_de_chivilcoy"),
    ("servicios", "farmacias_de_turno sepelios corte_de_luz corte_de_agua"),
]
_PATRONES = [(cat, [re.compile(r"\b" + re.escape(k.rstrip("$").replace("_", " ")) + (r"\b" if k.endswith("$") else ""))
                    for k in claves.split()]) for cat, claves in _CLAVES]


def _plano(t: str) -> str:
    t = unicodedata.normalize("NFKD", (t or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def categoria_por_texto(texto: str) -> str:
    t = _plano(texto)
    mejor, puntos = "general", 0
    for cat, patrones in _PATRONES:
        n = sum(1 for pat in patrones if pat.search(t))
        if n > puntos:
            mejor, puntos = cat, n
    return mejor


# --------------------------------------------------------------------------- registros
def cargar_registros(carpeta: str | None = None, get=None) -> dict:
    """{nombre: dict} de los registros de policiales-bsas. `carpeta` = leerlos del disco (pruebas/local)."""
    carpeta = carpeta or os.getenv("METRICAS_REGISTROS_DIR", "")
    out = {}
    for nombre in ("publicados.json", "nacionales.json", "archivo.json"):
        try:
            if carpeta:
                out[nombre] = json.loads((Path(carpeta) / nombre).read_text(encoding="utf-8"))
            else:
                r = (get or requests.get)(RAW.format(nombre), timeout=60)
                out[nombre] = r.json() if r.status_code == 200 else {}
        except Exception:  # noqa: BLE001 — sin registro se etiqueta por el texto
            out[nombre] = {}
    return out


def indice(registros: dict) -> dict:
    """{id de Meta: etiqueta} con lo que publicó policiales-bsas."""
    idx = {}

    def poner(ids, etiqueta):
        for i in ids:
            if i:
                i = str(i)
                idx[i] = etiqueta
                if "_" in i:                    # posteo de FB «página_posteo»: también por la parte final
                    idx[i.split("_", 1)[1]] = etiqueta

    for _url, e in (registros.get("publicados.json") or {}).items():
        if not isinstance(e, dict):
            continue
        et = {"origen": "Policiales", "categoria": "policiales", "titulo": e.get("titular") or "",
              "km": e.get("distancia_km"), "localidad": e.get("lugar_hecho") or e.get("localidad") or ""}
        poner([(e.get("instagram") or {}).get("id"), (e.get("facebook") or {}).get("id")], et)
    nac = registros.get("nacionales.json") or {}
    for _url, e in (nac.get("piezas") or {}).items():
        if not isinstance(e, dict):
            continue
        cat = e.get("categoria") or e.get("subgeneral") or "general"
        et = {"origen": "Nacionales", "categoria": cat, "titulo": e.get("titular") or "",
              "impacto": e.get("impacto")}
        poner([(e.get("instagram") or {}).get("id"), (e.get("facebook") or {}).get("id")], et)
    for dia, c in (nac.get("carruseles") or {}).items():
        if isinstance(c, dict):
            poner([c.get("id"), c.get("facebook_id"), (c.get("facebook") or {}).get("id")],
                  # Desde el 24/10/2026 el resumen es un reel a las 23 (policiales-bsas de9f4d2).
                  {"origen": ("Nacionales · resumen de las 23 (reel)" if c.get("formato") == "reel"
                              else "Nacionales · carrusel de las 22"), "categoria": "general",
                   "titulo": f"Las {c.get('diapositivas') or 10} noticias del día ({dia})"})
    pubs = (registros.get("archivo.json") or {}).get("publicaciones") or []
    for fila in (pubs.values() if isinstance(pubs, dict) else pubs):
        e = fila[-1] if isinstance(fila, (list, tuple)) and fila else fila
        if isinstance(e, dict):
            sub = e.get("subtipo") or "archivo"
            poner([e.get("id"), (e.get("facebook") or {}).get("id") if isinstance(e.get("facebook"), dict) else None],
                  {"origen": "Pregunta del día" if sub == "pregunta" else "Archivo / efemérides",
                   "categoria": "pregunta" if sub == "pregunta" else "historia", "titulo": e.get("titular") or ""})
    return idx


# --------------------------------------------------------------------------- por el texto
def origen_por_texto(texto: str, formato: str) -> tuple:
    """(origen, categoría o None) de lo que no está en los registros."""
    t = texto or ""
    p = _plano(t)
    primera = t.strip().split("\n", 1)[0]
    if "📰" in t[:200]:
        return "Notas del diario (carrusel de la mañana)", None
    if p.startswith("🌾 el campo hoy") or "el campo hoy" in p[:40]:
        return "Agro · datos del día (colab)", "agro"
    if "farmacias de turno" in p[:200]:
        return "Servicios (tapa, farmacias, clima, sepelios)", "servicios"
    if "sepelios" in p[:120] or "tapa de hoy" in p[:120] or "edicion de hoy" in p[:120]:
        return "Servicios (tapa, farmacias, clima, sepelios)", "servicios"
    if "youtube.com" in p or "youtu.be" in p:
        return "Videos de YouTube en Facebook", None
    if "clasificados" in p[:300]:
        return "Grupo de clasificados", "servicios"
    if formato in ("reel", "video"):
        # Entrevistas y corresponsales (desgrabador): título corto arriba y el texto abajo. Notas de la
        # redacción en video (notas web, foto-nota): arrancan directo con el primer párrafo de la nota.
        if len(primera) <= 110 and "\n\n" in t[:200]:
            return "Videos propios (entrevistas y corresponsales)", None
        return "Notas locales en reel (redacción)", None
    return "Otros", None


# --------------------------------------------------------------------------- etiqueta final
def etiquetar(red: str, mid: str, cuando_utc: datetime, formato: str, texto: str, idx: dict) -> dict:
    ar = cuando_utc.astimezone(TZ_AR)
    et = dict(idx.get(str(mid)) or {})
    if not et and "_" in str(mid):
        et = dict(idx.get(str(mid).split("_", 1)[1]) or {})
    if not et:
        origen, cat = origen_por_texto(texto, formato)
        et = {"origen": origen, "categoria": cat}
    if not et.get("categoria") or et["categoria"] == "general":
        cat_txt = categoria_por_texto(texto)
        if cat_txt != "general" or not et.get("categoria"):
            et["categoria"] = cat_txt
    if formato == "historia":
        et["origen"] = "Historias"
    et["titulo"] = (et.get("titulo") or _titulo(texto))[:90]
    et.update({"red": red, "formato": formato, "hora": ar.hour, "dia": DIAS[ar.weekday()],
               "fecha": ar.date().isoformat(), "finde": ar.weekday() >= 5})
    return et


def _titulo(texto: str) -> str:
    linea = (texto or "").strip().split("\n", 1)[0]
    linea = re.sub(r"^[^\wÁÉÍÓÚÑáéíóúñ¿¡\"“]+", "", linea)
    return linea.replace("📰", "").strip()


def corrida(et: dict) -> str:
    """La «corrida»: el sistema que lo publicó y la hora en que salió."""
    return f"{et.get('origen') or 'Otros'} · {et.get('hora', 0):02d} h"


def nombre_categoria(c: str) -> str:
    return CATEGORIAS.get(c or "general", (c or "General").replace("_", " ").capitalize())


FORMATOS = {"reel": "Reel", "carrusel": "Carrusel", "foto": "Foto", "video": "Video", "link": "Link",
            "historia": "Historia", "texto": "Texto"}


def formato_ig(media_type: str, producto: str) -> str:
    if producto == "STORY":
        return "historia"
    if producto == "REELS":
        return "reel"
    return {"CAROUSEL_ALBUM": "carrusel", "IMAGE": "foto", "VIDEO": "video"}.get(media_type or "", "foto")


def formato_fb(status_type: str, media_type: str, tipo: str) -> str:
    mt, tp, st = (media_type or "").lower(), (tipo or "").lower(), (status_type or "").lower()
    if "album" in mt or "album" in tp:
        return "carrusel"
    if "reel" in tp or "reel" in mt:
        return "reel"
    if "video" in mt or "video" in tp:
        return "video"
    if "photo" in mt or "photo" in tp:
        return "foto"
    if "share" in tp or "link" in mt or st == "shared_story":
        return "link"
    return "texto" if not mt else "foto"
