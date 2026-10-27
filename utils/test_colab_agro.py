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

from platforms import instagram as IG  # noqa: E402
from utils import colab_agro as CA  # noqa: E402

fallas, total = [], 0


def chequear(nombre, cond):
    global total
    total += 1
    print(("OK  " if cond else "MAL ") + nombre)
    if not cond:
        fallas.append(nombre)


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

print(f"\n{total - len(fallas)}/{total} bien")
sys.exit(1 if fallas else 0)
