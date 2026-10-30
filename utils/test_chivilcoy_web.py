"""Pruebas de utils/chivilcoy_web.py (30/10/2026). Sin red: solo reglas."""
import os
import sys

from utils.config import load_config

load_config()
os.environ.pop("CHIVILCOY_WEB", None)
os.environ["SECCION_IA"] = "0"

from utils import chivilcoy_web as C  # noqa: E402

fallas, total = [], 0


def chequear(nombre, cond, detalle=""):
    global total
    total += 1
    print(("OK  " if cond else "MAL ") + nombre + (f"  → {detalle}" if not cond and detalle else ""))
    if not cond:
        fallas.append(nombre)


# 1. El bot: la volanta «Chivilcoy · Gremiales» la web la separa; el titular tiene que decirlo.
t, b = C.ajustar("Chivilcoy · Gremiales — Amarilla Gas cierra el fraccionamiento y desvincula a ocho empleados",
                 "La decisión fue comunicada durante una audiencia en el Ministerio de Trabajo.\nLa empresa pagará.")
chequear("bot: el titular arranca con «Chivilcoy: »",
         t == "Chivilcoy · Gremiales — Chivilcoy: Amarilla Gas cierra el fraccionamiento y desvincula a ocho empleados", t)
chequear("bot: la primera línea lleva la data", b.startswith("CHIVILCOY.– La decisión fue comunicada"), b[:60])
chequear("bot: el resto del cuerpo, igual", b.endswith("\nLa empresa pagará."))

# 2. La mañana: título sin volanta y cuerpo que ya nombra la ciudad en la primera línea.
t, b = C.ajustar("Comenzó la repavimentación de la avenida Villarino",
                 "La Municipalidad de Chivilcoy inició los trabajos en la avenida.\n\nSe extenderán dos meses.")
chequear("mañana: titular con «Chivilcoy: »", t == "Chivilcoy: Comenzó la repavimentación de la avenida Villarino", t)
chequear("mañana: si la primera línea ya dice Chivilcoy, no se toca", b.startswith("La Municipalidad de Chivilcoy"))

# 3. Lo que ya dice Chivilcoy en el titular, no se repite.
t, b = C.ajustar("Policiales — Robaron una moto en el centro de Chivilcoy", "Ocurrió anoche en calle Pellegrini.")
chequear("titular que ya lo dice: queda igual", t == "Policiales — Robaron una moto en el centro de Chivilcoy", t)
chequear("…pero la primera línea igual lleva la data", b.startswith("CHIVILCOY.– Ocurrió anoche"), b)

# 4. Lo que NO es de acá no se toca: otra ciudad, nacionales, opinión.
for titulo, cuerpo, que in (
        ("Junín: detuvieron a un hombre por robo", "La policía de Junín lo encontró en su casa.", "otra ciudad"),
        ("El Gobierno nacional anunció un aumento de las jubilaciones",
         "La ANSES informó que el haber mínimo sube en noviembre. Lo dijo el ministro de Economía.", "nacional"),
        ("Opinión — La ciudad que queremos", "Por Luis María Mariano.", "opinión")):
    t, b = C.ajustar(titulo, cuerpo)
    chequear(f"no se toca: {que}", (t, b) == (titulo, cuerpo), t)

# 4b. Casos reales del 30/10/2026: sin prueba de que es de acá, o con otra ciudad en el título, no se toca.
for titulo, cuerpo, que in (
        ("El día que la URSS hizo estallar la bomba atómica más potente de la historia",
         "Durante las conmemoraciones por el aniversario, el gobierno ruso recordó la prueba.", "nacional del papel"),
        ("BRAGADO · Policiales — Detuvieron a un hombre acusado de vender estupefacientes",
         "Intervino personal de la DDI con asiento en Chivilcoy.", "otra ciudad en la volanta")):
    t, b = C.ajustar(titulo, cuerpo)
    chequear(f"no se toca: {que}", (t, b) == (titulo, cuerpo), t)

# 4c. Volanta LARGA (> 25): la web muestra la primera parte como título (h1); ahí va «Chivilcoy: ».
t, b = C.ajustar("Acuerdo en el Ministerio de Trabajo — Amarilla Gas cierra el sector de envasado en Chivilcoy",
                 "La empresa comunicó la decisión a los trabajadores de la planta de Chivilcoy.")
chequear("volanta larga: el «Chivilcoy: » va en lo que la web muestra como título",
         t == "Chivilcoy: Acuerdo en el Ministerio de Trabajo — Amarilla Gas cierra el sector de envasado en Chivilcoy",
         t)
chequear("volanta larga: deshacer vuelve al original", C.deshacer(t, b) == (
    "Acuerdo en el Ministerio de Trabajo — Amarilla Gas cierra el sector de envasado en Chivilcoy",
    "La empresa comunicó la decisión a los trabajadores de la planta de Chivilcoy."))
chequear("localidad del partido cuenta como de acá",
         C.ajustar("Inauguraron la sala de primeros auxilios", "La obra en Moquehuá demandó un año.")[0]
         == "Chivilcoy: Inauguraron la sala de primeros auxilios")

# 5. La foto de corresponsal repite el titular arriba del cuerpo: va igual que el título.
t, b = C.ajustar("Chivilcoy · Sociedad — Vecinos reclaman por un basural en el barrio Ceci",
                 "Vecinos reclaman por un basural en el barrio Ceci\n\nLos residuos se acumulan hace semanas.")
chequear("corresponsal-foto: el titular repetido también cambia y la data va al primer párrafo de texto",
         b.startswith("Chivilcoy: Vecinos reclaman") and "\n\nCHIVILCOY.– Los residuos" in b, b[:120])

# 6. Las redes no heredan las marcas: deshacer devuelve el original.
for titulo, cuerpo in (("Chivilcoy · Gremiales — Amarilla Gas cierra", "La decisión fue comunicada.\n\nSigue."),
                       ("Chivilcoy · Sociedad — Vecinos reclaman", "Vecinos reclaman\n\nLos residuos se acumulan.")):
    t, b = C.ajustar(titulo, cuerpo)
    chequear(f"deshacer vuelve al original: «{titulo[:30]}»", C.deshacer(t, b) == (titulo, cuerpo), C.deshacer(t, b))

# 7. Interruptor y sección forzada.
os.environ["CHIVILCOY_WEB"] = "0"
chequear("CHIVILCOY_WEB=0 lo apaga", C.ajustar("Comenzó la obra", "La Municipalidad inició.") ==
         ("Comenzó la obra", "La Municipalidad inició."))
os.environ.pop("CHIVILCOY_WEB")
chequear("sección «inicio» (sepelios): no se toca",
         C.ajustar("Sepelios", "Falleció…", seccion="inicio") == ("Sepelios", "Falleció…"))

print(f"\n{total - len(fallas)}/{total} bien")
sys.exit(1 if fallas else 0)
