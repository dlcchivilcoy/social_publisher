"""Pruebas de la colaboración con @diarioyradioagro en el bot (utils/colab_agro.py, 27/10/2026). Sin red:
se simulan las llamadas a Instagram."""
import os
import sys

from utils.config import load_config

load_config()
os.environ["INSTAGRAM_USER_ID"] = "17841405971293744"
os.environ["INSTAGRAM_ACCESS_TOKEN"] = "x"
os.environ.pop("AGRO_INSTAGRAM_USER_ID", None)
os.environ.pop("AGRO_INSTAGRAM_ACCESS_TOKEN", None)
os.environ.pop("AGRO_FACEBOOK_PAGE_ACCESS_TOKEN", None)
os.environ.pop("COLAB_AGRO", None)
os.environ["COLAB_AGRO_TEXTO"] = "1"     # 24/11/2026: la colab por palabras se prueba prendida (de fábrica, apagada)

from platforms import instagram as IG  # noqa: E402
from utils import colab_agro as CA  # noqa: E402

# 25/11/2026: de fábrica no hay ninguna colab automática (ni la nota 6); lo que sigue prueba con COLAB_AGRO=1.
_sin = (not CA.activo() and CA.colaborador_para("Cosecha de trigo", "17841405971293744", siempre=True) == "")
os.environ["COLAB_AGRO"] = "1"

fallas, total = [], 0
_pendiente = [("25/11: de fábrica, sin colab (ni la nota 6)", _sin)]


def chequear(nombre, cond):
    global total
    total += 1
    print(("OK  " if cond else "MAL ") + nombre)
    if not cond:
        fallas.append(nombre)


for _n, _c in _pendiente:
    chequear(_n, _c)


contenedores = []


def crear(uid, tok, data, intentos=3):
    contenedores.append(dict(data))
    if data.get("collaborators") and os.environ.get("_RECHAZAR_COLAB"):
        raise RuntimeError("Instagram (crear contenedor): 400 collaborators")
    return f"C{len(contenedores)}"


IG._crear_contenedor = crear
IG._contenedor_video_listo = lambda uid, tok, data, url, que, intentos=4: crear(uid, tok, data)
IG._wait_container_ready = lambda *a, **k: None
IG._publicar_contenedor = lambda *a, **k: "M1"
IG._location = lambda: ""

r = IG.publish_reel("https://v/x.mp4", "Récord de cosecha de soja en Chivilcoy\n\nLos productores celebran.")
chequear("reel del campo → invita a @diarioyradioagro",
         contenedores[-1].get("collaborators") == '["diarioyradioagro"]' and r["colab"] == "sin credenciales")
r = IG.publish_reel("https://v/x.mp4", "Choque en la ruta 5")
chequear("reel que no es del campo → sin colab", "collaborators" not in contenedores[-1] and r["colab"] == "")
r = IG.publish_reel("https://v/x.mp4", "Publicidad: semillas de soja y maíz", colab=False)
chequear("colab=False (publicidades, tapa…) → nunca colab", "collaborators" not in contenedores[-1])
os.environ["_RECHAZAR_COLAB"] = "1"
contenedores.clear()
r = IG.publish_reel("https://v/x.mp4", "Senasa y la campaña de vacunación del rodeo")
chequear("si Instagram rechaza la colab, el reel sale igual sin ella",
         r["id"] == "M1" and len(contenedores) == 2 and "collaborators" not in contenedores[1])
os.environ.pop("_RECHAZAR_COLAB")
os.environ["INSTAGRAM_USER_ID"] = "999"
chequear("otra cuenta (la radio) → sin colab", CA.colaborador_para("Cosecha de trigo") == "")
os.environ["INSTAGRAM_USER_ID"] = "17841405971293744"
chequear("las palabras ambiguas solas no alcanzan",
         not CA.es_del_campo("La productora del recital y el rendimiento del equipo"))

# 24/11/2026: de fábrica, por palabras NO (solo la nota 6).
os.environ.pop("COLAB_AGRO_TEXTO", None)
chequear("de fábrica, un texto del campo NO va en colab", CA.colaborador_para("Récord de cosecha de soja") == "")
chequear("de fábrica, la nota 6 SÍ", CA.colaborador_para("Un médico de la ciudad", siempre=True) == "diarioyradioagro")
# 23/11/2026: la nota 6 del Drive SIEMPRE en colab con agro.
from pathlib import Path as _P  # noqa: E402
os.environ.pop("COLAB_AGRO_NOTAS", None)
chequear("número de nota «6-nota ornella mm.docx» = 6", CA.numero_de_nota("6-nota ornella mm.docx") == 6)
chequear("número de nota con ruta y cero adelante", CA.numero_de_nota(_P("x/06 nota.docx")) == 6)
chequear("«16-nota» no es la 6", CA.numero_de_nota("16-nota golf.docx") == 16)
chequear("sin número = 0", CA.numero_de_nota("nota suelta.docx") == 0)
chequear("la nota 6 → colab siempre", CA.colab_de_nota({"docx": _P("6-nota ornella mm.docx")}) == "siempre")
chequear("la nota 9 → decide el texto", CA.colab_de_nota({"docx": _P("9-nota rolandelli h.docx")}) is True)
chequear("la 16 no cuenta como 6", CA.colab_de_nota({"docx": _P("16-nota x.docx")}) is True)
chequear("siempre=True invita aunque no sea del campo",
         CA.colaborador_para("Ornella Martínez Marcelo", siempre=True) == "diarioyradioagro")
os.environ["COLAB_AGRO_NOTAS"] = "6,9"
chequear("COLAB_AGRO_NOTAS=6,9 suma la 9", CA.colab_de_nota({"docx": _P("9-nota.docx")}) == "siempre")
os.environ["COLAB_AGRO_NOTAS"] = "0"
chequear("COLAB_AGRO_NOTAS=0 = ninguna", CA.colab_de_nota({"docx": _P("6-nota.docx")}) is True)
os.environ.pop("COLAB_AGRO_NOTAS")
contenedores.clear()
IG.publish_reel("https://v/x.mp4", "Ornella Martínez Marcelo: las funciones de la temporada", colab="siempre")
chequear("reel de la nota 6 (no es del campo) → invita a agro",
         contenedores[-1].get("collaborators") == '["diarioyradioagro"]')
os.environ["INSTAGRAM_USER_ID"] = "999"
chequear("otra cuenta nunca invita, ni con siempre", CA.colaborador_para("x", siempre=True) == "")
os.environ["INSTAGRAM_USER_ID"] = "17841405971293744"

print(f"\n{total - len(fallas)}/{total} bien")
sys.exit(1 if fallas else 0)
