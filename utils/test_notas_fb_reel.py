# -*- coding: utf-8 -*-
"""Notas de la mañana en Facebook (25/11/2026): de 6 a 11:40, una cada 20 min, y en REEL salvo policiales y clima.

    python utils/test_notas_fb_reel.py
"""
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

import carrusel_notas as CN  # noqa: E402

fallas, total = [], 0


def chequear(nombre, condicion):
    global total
    total += 1
    print(("OK " if condicion else "MAL") + " " + nombre)
    if not condicion:
        fallas.append(nombre)


A_LAS_6 = datetime(2026, 11, 26, 6, 0, tzinfo=CN.TZ_AR)

# --- el reparto ------------------------------------------------------------------------------------------
chequear("15 notas desde las 6: una cada 20 min", CN._fb_intervalo_para(15, A_LAS_6) == 20 * 60)
_n = 22
_paso = CN._fb_intervalo_para(_n, A_LAS_6)
chequear("22 notas: se achica el paso y la última sale antes de las 11:40",
         _paso < 20 * 60 and A_LAS_6.timestamp() + _paso * (_n - 1) <= A_LAS_6.replace(hour=11, minute=40).timestamp())

# --- qué va en reel ---------------------------------------------------------------------------------------
chequear("una nota de cultura va en reel",
         CN._fb_va_en_reel("Teatro", "Se presenta la obra en el teatro municipal", ["Habrá música y una muestra."]))
chequear("un policial sigue en foto (en Facebook la foto le rinde el triple)",
         not CN._fb_va_en_reel("", "Detuvieron a un hombre tras un robo en el centro", ["La policía lo aprehendió."]))
chequear("el clima sigue en foto",
         not CN._fb_va_en_reel("", "Alerta amarilla por tormenta y granizo", ["El pronóstico del SMN."]))
os.environ["NOTAS_FB_REEL"] = "0"
chequear("NOTAS_FB_REEL=0: todo en foto", not CN._fb_va_en_reel("Teatro", "Obra en el teatro", ["Música."]))
os.environ.pop("NOTAS_FB_REEL")

# --- el lugar en la página (30 reels por API cada 24 h) -----------------------------------------------------
chequear("con 10 reels en 24 h hay lugar", CN._fb_hay_lugar_reel(lambda: 10))
chequear("con 27 no (deja lugar a los videos propios)", not CN._fb_hay_lugar_reel(lambda: 27))
chequear("si no se puede contar, foto", not CN._fb_hay_lugar_reel(lambda: None))

# --- la corrida, con todo de mentira ----------------------------------------------------------------------
NOTAS = {
    "n1": ("Teatro", "Se presenta la obra en el teatro municipal", ["Habrá música y una muestra de artistas."]),
    "n2": ("", "Detuvieron a un hombre tras un robo en el centro", ["La policía lo aprehendió."]),
    "n3": ("Escuelas", "Los alumnos de la escuela 8 hicieron una feria", ["Docentes y estudiantes."]),
}
hechos, ledger, dormido = [], set(), []
CN._load_fb_ledger = lambda f: set(ledger)
CN._save_fb_ledger = lambda f, k: ledger.update(k)
CN._docx_sin_nota = lambda f, n: []
CN._avisar_notas_no_posteadas = lambda *a: None
CN._parse_nota = lambda docx: NOTAS[Path(docx).name]
CN.wix.url_de_nota = lambda t: "https://diario/" + CN.wix._slugify(t)
CN._prepare_image = lambda img: img
CN._fb_intervalo_para = lambda n, ahora=None: 1200
CN._armar_reel_fb = lambda note, v, t, c, p, k: Path(f"{note['key']}.mp4")
CN._fb_hay_lugar_reel = lambda contar=None: True
CN.time.sleep = lambda s: dormido.append(s)
falla_reel = {"n3"}


def reel_falso(caption, mp4):
    if mp4.stem in falla_reel:
        raise RuntimeError("procesamiento")
    hechos.append(("reel", mp4.stem, caption))


CN._publicar_reel_fb = reel_falso
CN.facebook.publish = lambda caption, img: hechos.append(("foto", img, caption))
notas = [{"key": k, "docx": Path(k), "image": k, "title": k} for k in NOTAS]
CN._postear_notas_facebook(Path("."), notas, dry_run=False)
chequear("cultura sale en reel con titular y link arriba",
         hechos[0][:2] == ("reel", "n1") and hechos[0][2].startswith("📰 Teatro — Se presenta")
         and "https://diario/" in hechos[0][2].split("\n")[1])
chequear("el policial sale en foto", hechos[1][:2] == ("foto", "n2"))
chequear("si el reel falla, la nota sale en foto igual", hechos[2][:2] == ("foto", "n3"))
chequear("las tres quedan anotadas (no se repiten)", ledger == {"n1", "n2", "n3"})
chequear("entre nota y nota espera su turno", len(dormido) == 2)

print(f"\n--- {total - len(fallas)}/{total} correctos ---")
sys.exit(1 if fallas else 0)
