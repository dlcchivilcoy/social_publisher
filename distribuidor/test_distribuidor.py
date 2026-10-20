"""Pruebas del distribuidor contra un Buffer FALSO (no toca la red ni publica nada).

    venv\\Scripts\\python.exe -m distribuidor.test_distribuidor
"""
from __future__ import annotations

import os
import sys
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("DISTRIBUIDOR_ACTIVO", "1")
# El intercalado de policiales (17/10/2026) tiene su prueba propia (prueba_intercalado); las demás
# son de cupos, topes y urgentes con lotes casi todos policiales, y se escribieron sin él.
os.environ["INTERCALADO"] = "0"
# TikTok en tandas (19/10/2026) tiene su prueba propia (prueba_tandas_tiktok); las demás son del goteo.
os.environ["TIKTOK_TANDAS"] = "0"
# X sincronizado y sin repetidos (19/10/2026) tiene su prueba propia (prueba_x_sincro); las demás son del
# goteo de antes, con sus cupos de entonces.
os.environ["X_SINCRO"] = "0"
os.environ.setdefault("CUPO_POLICIALES_X", "12")
os.environ.setdefault("CUPO_NACIONALES_X", "0")
os.environ.setdefault("CUPO_ENTREVISTAS_X", "10")

from distribuidor import buffer_api, config, estado, fuentes, planificador, run, textos, x_sincro  # noqa: E402

OK, MAL = [], []


def chequear(nombre, cond, detalle=""):
    (OK if cond else MAL).append(nombre)
    print(("  ok   " if cond else "  MAL  ") + nombre + ("" if cond else f"  → {detalle}"))


# ------------------------------------------------------------------ dobles de prueba
class BufferFalso:
    def __init__(self):
        self.posts = {}          # id → post
        self.n = 0
        self.solicitudes = 0
        self.fallar_con = None   # BufferError a tirar en la próxima mutación
        self.tope_canal = 10

    def crear_lote(self, pubs, borrar=None):
        self.solicitudes += 1
        if self.fallar_con:
            e, self.fallar_con = self.fallar_con, None
            raise e
        out = {"_borrados": []}
        for pid in borrar or []:
            if self.posts.pop(pid, None):
                out["_borrados"].append(pid)
        for p in pubs:
            canal = config.canal(p["plataforma"])
            prog = [x for x in self.posts.values() if x["channelId"] == canal and x["status"] == "scheduled"]
            if len(prog) >= self.tope_canal:
                out[p["clave"]] = {"ok": False, "id": "", "tope": True,
                                   "error": "Scheduled posts limit reached."}
                continue
            self.n += 1
            pid = f"post{self.n}"
            self.posts[pid] = {"id": pid, "channelId": canal, "status": "scheduled",
                               "dueAt": p["dueAt"], "text": p["texto"], "externalLink": "",
                               "sentAt": None, "error": None}
            out[p["clave"]] = {"ok": True, "id": pid, "error": "", "tope": False}
        return out

    def estado_general(self, desde, hasta):
        self.solicitudes += 1
        return {"canales": [{"id": config.canal(r), "service": config.SERVICIO[r], "name": "diarioyradio",
                             "isDisconnected": False, "isLocked": False, "isQueuePaused": False}
                            for r in config.PLATAFORMAS],
                "limites": [], "posts": list(self.posts.values())}

    def publicar_vencidos(self):
        ahora = estado.ahora()
        for p in self.posts.values():
            if p["status"] == "scheduled" and estado.de_iso(p["dueAt"]) <= ahora:
                p.update(status="sent", sentAt=estado.iso(ahora), externalLink=f"https://x.com/i/{p['id']}")


def preparar(eventos_fn):
    from distribuidor import resumen
    resumen._pedir_gemini = lambda texto, titulo, maximo: ""     # nunca la red en las pruebas
    tmp = Path(tempfile.mkdtemp())
    estado.ARCHIVO = tmp / "distribuidor.json"
    estado.PANEL = tmp / "panel.md"
    estado.BUZON = tmp / "buzon"
    from distribuidor import x_sincro as _xs
    _xs.PLAN_IG = tmp / "carrusel_plan.json"          # nunca el plan real del repo
    # Las reglas del 20/10/2026 (entre semana solo lo propio) tienen su prueba (prueba_semana_finde).
    config.SOLO_FINDE, config.X_WEB_SOLO_DIARIO, config.BOT_PRIMERO = set(), False, False
    falso = BufferFalso()
    buffer_api.crear_lote = falso.crear_lote
    buffer_api.estado_general = falso.estado_general
    fuentes.todas = eventos_fn
    fuentes.video_verificado = lambda ev: (True, "", {"mb": 5, "bytes": 5_000_000})
    fuentes.nota_verificada = lambda url: (True, "")
    run.avisar = lambda *a, **k: None
    return falso


def reloj(t: datetime):
    estado.ahora = lambda: t


def ev(i, origen="policiales", urgente=False, prioridad=5, horas=0.0, **kw):
    base = {"id": f"{origen}:{i}", "origen": origen, "tipo": "nota" if origen == "web" else "video",
            "titulo": f"Título de prueba número {i} para {origen}",
            "descripcion": "Primera oración de la bajada. Segunda oración con más datos del hecho.",
            "video_url": f"https://github.com/x/y/releases/download/t/{i}.mp4",
            "nota_url": f"https://www.diariolacampaña.com.ar/n/nota-{i}",
            "prioridad": prioridad, "urgente": urgente, "localidad": "Bragado",
            "generado": estado.iso(estado.ahora() - timedelta(hours=horas))}
    if origen == "web":
        base.pop("video_url")
    base.update(kw)
    return base


AR_10 = datetime(2026, 10, 12, 13, 0, tzinfo=timezone.utc)   # 10:00 en Argentina


# ------------------------------------------------------------------ pruebas
def prueba_textos():
    print("textos: solo título + bajada completa + link, nunca «…»")
    from distribuidor import resumen
    pedidos = []
    respuesta = {"txt": "Un auto y una camioneta chocaron en el kilómetro 120 de la Ruta 7."}
    resumen._roto, resumen._inicio = False, None
    resumen._pedir_gemini = lambda texto, titulo, maximo: (pedidos.append(maximo) or respuesta["txt"])
    larga = ("Un auto y una camioneta chocaron esta mañana en el kilómetro 120 de la Ruta 7. " * 6
             + "\n\n📲 Seguí leyendo en www.diariolacampaña.com.ar #policiales #ruta7")
    e = {"origen": "policiales", "tipo": "video", "localidad": "San Andrés de Giles",
         "titulo": "Choque en la ruta 7: dos heridos", "nota_url": "https://www.diariolacampaña.com.ar/n/x",
         "descripcion": larga}
    tx = textos.texto_x(e)
    partes = tx.split("\n\n")
    chequear("X ≤ 280 con el peso real", textos.largo_x(tx) <= 280, f"{textos.largo_x(tx)}: {tx}")
    chequear("X = título / bajada / link (3 bloques)", len(partes) == 3 and partes[0] == e["titulo"]
             and partes[2] == e["nota_url"], tx)
    chequear("X: bajada larga → resumen de Gemini", partes[1] == respuesta["txt"] and pedidos, tx)
    chequear("X sin «…», sin hashtags, sin etiquetas", "…" not in tx and "#" not in tx
             and "Nota completa" not in tx and "📲" not in tx, tx)

    # Gemini inventa un número que no está en el texto → se descarta y van oraciones enteras.
    respuesta["txt"] = "Chocaron en el kilómetro 999 y hubo 5 heridos."
    tx2 = textos.texto_x(e)
    baj2 = tx2.split("\n\n")[1]
    chequear("resumen con un dato inventado: descartado", "999" not in tx2, tx2)
    chequear("…y queda en oraciones ENTERAS del original", baj2.endswith(".") and "Un auto" in baj2
             and "…" not in baj2, tx2)

    # Sin Gemini (caído): oraciones enteras; si ni la primera entra, sin bajada.
    resumen._roto = True
    una = {"origen": "web", "tipo": "nota", "titulo": "T" * 60, "nota_url": "https://a.b/n/c",
           "descripcion": "Palabra " * 60 + "final."}
    tx3 = textos.texto_x(una)
    chequear("sin oración que entre: título + link solos", tx3 == "T" * 60 + "\n\nhttps://a.b/n/c", tx3)
    corta = dict(una, descripcion="Primera oración completa. " + "Segunda muy larga " * 30 + "fin.")
    tx4 = textos.texto_x(corta)
    chequear("Gemini caído: primera oración entera", tx4.split("\n\n")[1] == "Primera oración completa.", tx4)
    resumen._roto = False

    w = {"origen": "web", "tipo": "nota", "titulo": "El Concejo aprobó el presupuesto",
         "descripcion": "Fue por mayoría.", "nota_url": "https://www.diariolacampaña.com.ar/n/p"}
    chequear("nota web corta: tal cual", textos.texto_x(w) ==
             "El Concejo aprobó el presupuesto\n\nFue por mayoría.\n\nhttps://www.diariolacampaña.com.ar/n/p",
             textos.texto_x(w))
    sin_punto = dict(w, descripcion="La sesión duró cuatro horas y hubo debate")
    respuesta["txt"] = "La sesión duró cuatro horas y hubo debate."
    chequear("bajada sin punto final (cortada en origen) → se completa",
             textos.texto_x(sin_punto).split("\n\n")[1].endswith("."), textos.texto_x(sin_punto))
    en = {"origen": "entrevistas", "tipo": "video", "titulo": "Juan Pérez, director del hospital",
          "descripcion": "Habló de la guardia.", "youtube_url": "https://youtu.be/abc",
          "nota_url": "https://otra"}
    chequear("entrevista: link a la entrevista completa", textos.texto_x(en).endswith("https://youtu.be/abc"))

    respuesta["txt"] = "Un auto y una camioneta chocaron en el kilómetro 120 de la Ruta 7."
    tt = textos.texto_tiktok(e)
    bloques = tt.split("\n\n")
    tags = [w for w in tt.split() if w.startswith("#")]
    chequear("TikTok = título / bajada / link / hashtags", len(bloques) == 4 and bloques[0] == e["titulo"]
             and bloques[2] == e["nota_url"] and bloques[3].startswith("#"), tt)
    chequear("TikTok: bajada ≤ 450 y entera", len(bloques[1]) <= 450 and bloques[1].endswith("."), bloques[1])
    chequear("TikTok máx. 5 hashtags, con los 3 fijos", len(tags) <= 5 and
             all(h in tags for h in config.TIKTOK_HASHTAGS_FIJOS), tags)
    chequear("TikTok ignora la descripción vieja con hashtags",
             "Seguí leyendo" not in tt and "#ruta7" not in tt, tt)
    chequear("emoji pesa 2 en X", textos.largo_x("🎥") == 2)
    chequear("link pesa 23 en X", textos.largo_x("https://example.com/" + "a" * 80) == 23)
    frase = "A las 21.40 llegó la S.A. de Juan. Después se fue."
    chequear("«21.40» y «S.A.» no cortan oraciones", len(resumen.oraciones(frase)) == 2,
             resumen.oraciones(frase))


def prueba_migracion_textos():
    print("textos viejos: se rehacen (cola) y se reprograman (Buffer)")
    reloj(AR_10)
    falso = preparar(lambda: [ev(i, "bot") for i in range(3)])
    run.pasada()
    est = estado.cargar()
    # Simula lo de antes del 17/10: textos con «…» y sin versión.
    for p in est["publicaciones"].values():
        p["texto"] = "viejo cortado…"
        p.pop("texto_v", None)
    limite = estado.ahora() + timedelta(minutes=10)     # las que salen ya, no se tocan
    programadas = [p for p in est["publicaciones"].values() if p["estado"] == "programado"
                   and estado.de_iso(p["dueAt"]) > limite]
    cerca = {p["clave"] for p in est["publicaciones"].values() if p["estado"] == "programado"
             and estado.de_iso(p["dueAt"]) <= limite}
    estado.guardar(est)
    n_antes = len(falso.posts)
    run.pasada()
    est = estado.cargar()
    chequear("ningún texto viejo queda programado o en cola",
             all("…" not in p["texto"] for p in est["publicaciones"].values()
                 if p["estado"] in ("pendiente", "programado") and p["clave"] not in cerca),
             [p["texto"] for p in est["publicaciones"].values()][:3])
    chequear("se borraron de Buffer las programadas viejas",
             all(p["buffer_id"] not in falso.posts for p in programadas), list(falso.posts))
    chequear("y se volvieron a programar con texto nuevo", len(falso.posts) == n_antes,
             [x["text"] for x in falso.posts.values()])


def prueba_idempotencia_y_cupos():
    print("idempotencia, cupos y topes")
    reloj(AR_10)
    lote = [ev(i) for i in range(30)] + [ev(i, "web") for i in range(5)]
    falso = preparar(lambda: lote)
    run.pasada()
    est = estado.cargar()
    pol_x = [p for p in est["publicaciones"].values() if p["origen"] == "policiales" and p["plataforma"] == "x"]
    chequear("una fila por contenido y red", len(est["publicaciones"]) == 30 * 2 + 5, len(est["publicaciones"]))
    chequear("notas web solo a X", all(p["plataforma"] == "x" for p in est["publicaciones"].values()
                                       if p["origen"] == "web"))
    prog = [p for p in est["publicaciones"].values() if p["estado"] == "programado"]
    por_red = {r: sum(1 for p in prog if p["plataforma"] == r) for r in config.PLATAFORMAS}
    chequear("nunca más de 8 programadas por canal", max(por_red.values()) <= config.max_pendientes("x"), por_red)
    chequear("lotes de 10 por solicitud", falso.solicitudes <= 2, falso.solicitudes)
    n_antes = len(falso.posts)
    run.pasada()     # mismo lote otra vez: nada nuevo
    chequear("reenviar el mismo lote no duplica", len(falso.posts) == n_antes, (n_antes, len(falso.posts)))

    # Avanzar el día publicando: nunca pasa los cupos de la matriz ni los topes.
    t = AR_10
    for _ in range(14 * 3):              # 14 horas, de a 20 minutos
        t += timedelta(minutes=20)
        reloj(t)
        falso.publicar_vencidos()
        run.pasada()
    est = estado.cargar()
    hechas = [p for p in est["publicaciones"].values() if p["estado"] in ("programado", "enviado")]
    cuenta = {}
    for p in hechas:
        cuenta[(p["origen"], p["plataforma"])] = cuenta.get((p["origen"], p["plataforma"]), 0) + 1
    chequear("policiales X ≤ 12", cuenta.get(("policiales", "x"), 0) <= 12, cuenta)
    chequear("policiales TikTok ≤ 12", cuenta.get(("policiales", "tiktok"), 0) <= 12, cuenta)
    chequear("se llegó al cupo de policiales X (había 30)", cuenta.get(("policiales", "x"), 0) == 12, cuenta)
    chequear("web X ≤ 15 y salieron las 5", cuenta.get(("web", "x"), 0) == 5, cuenta)
    dues = sorted(estado.de_iso(p["dueAt"]) for p in hechas if p["plataforma"] == "x")
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(dues, dues[1:])]
    chequear("X separadas ≥ 15 min (menos 1 de redondeo)", min(gaps) >= 14, min(gaps) if gaps else None)
    dues_t = sorted(estado.de_iso(p["dueAt"]) for p in hechas if p["plataforma"] == "tiktok")
    gaps_t = [(b - a).total_seconds() / 60 for a, b in zip(dues_t, dues_t[1:])]
    chequear("TikTok separadas ≥ 40 min", not gaps_t or min(gaps_t) >= 39, min(gaps_t) if gaps_t else None)
    horas = {d.astimezone(planificador.AR).hour for d in dues + dues_t}
    chequear("nada antes de las 7 (hora AR)", min(horas) >= 7, sorted(horas))
    enviados_txt = [p["text"] for p in falso.posts.values()]
    chequear("ningún texto repetido en Buffer", len(enviados_txt) == len(set(enviados_txt)))
    chequear("solicitudes en un día ≤ 60", falso.solicitudes <= 60, falso.solicitudes)
    print(f"       (solicitudes simuladas en 14 h: {falso.solicitudes}; cupos: {cuenta})")
    sobrantes = [p for p in est["publicaciones"].values() if p["origen"] == "policiales"
                 and p["plataforma"] == "x" and p["estado"] == "pendiente"]
    chequear("lo que no entró queda con motivo", all(p.get("espera") for p in sobrantes),
             [p.get("espera") for p in sobrantes][:3])


def prueba_tope_tiktok_15():
    print("tope total de TikTok (15) con bot + policiales")
    reloj(AR_10)
    lote = [ev(i, "bot") for i in range(12)] + [ev(i) for i in range(14)]
    falso = preparar(lambda: lote)
    t = AR_10
    for _ in range(16 * 3):
        run.pasada()
        t += timedelta(minutes=20)
        reloj(t)
        falso.publicar_vencidos()
    est = estado.cargar()
    tt = [p for p in est["publicaciones"].values() if p["plataforma"] == "tiktok"
          and p["estado"] in ("programado", "enviado")]
    bot = sum(1 for p in tt if p["origen"] == "bot")
    chequear("TikTok ≤ 15 en 24 h", len(tt) <= 15, len(tt))
    chequear("bot (propio) antes que policiales: sus 10", bot == 10, bot)


def prueba_urgente():
    print("urgente adelanta y hace lugar")
    reloj(AR_10)
    lote = [ev(i, prioridad=5) for i in range(20)]
    falso = preparar(lambda: lote)
    run.pasada()
    lote.append(ev("U", urgente=True, prioridad=9))
    reloj(AR_10 + timedelta(minutes=1))       # las 8 programadas siguen en Buffer: está lleno
    run.pasada()
    est = estado.cargar()
    u = est["publicaciones"]["policiales:U|x"]
    chequear("urgente programada", u["estado"] == "programado", u)
    chequear("urgente sale en < 15 min", estado.de_iso(u["dueAt"]) - estado.ahora() < timedelta(minutes=15),
             u["dueAt"])
    corridas = [p for p in est["publicaciones"].values() if p.get("espera") == "corrida por una urgente"]
    chequear("se desprogramó una no urgente (vuelve a la cola)", len(corridas) >= 1 and
             all(p["estado"] == "pendiente" for p in corridas), corridas)


def prueba_corte_y_recuperacion():
    print("corrida cortada a mitad: no duplica")
    reloj(AR_10)
    lote = [ev(i, "bot") for i in range(3)]
    falso = preparar(lambda: lote)
    falso.fallar_con = buffer_api.BufferError("timeout leyendo la respuesta", pasajero=True)
    # Buffer SÍ recibió el lote pero la respuesta se perdió: lo simulamos creándolo igual.
    original = BufferFalso.crear_lote

    def recibe_y_falla(pubs, borrar=None):
        falso.fallar_con = None
        original(falso, pubs, borrar)
        raise buffer_api.BufferError("se cortó la conexión", pasajero=True)
    buffer_api.crear_lote = recibe_y_falla
    run.pasada()
    est = estado.cargar()
    chequear("quedan «enviando»", all(p["estado"] == "enviando" for p in est["publicaciones"].values()
                                       if p["estado"] not in ("descartado",)),
             {k: p["estado"] for k, p in est["publicaciones"].items()})
    n = len(falso.posts)
    buffer_api.crear_lote = falso.crear_lote
    reloj(AR_10 + timedelta(minutes=20))
    run.pasada()
    est = estado.cargar()
    chequear("se recuperaron sin reenviar", len(falso.posts) == n, (n, len(falso.posts)))
    chequear("quedaron programadas con su id", all(p["estado"] == "programado" and p["buffer_id"]
                                                   for p in est["publicaciones"].values()),
             {k: (p["estado"], p["buffer_id"]) for k, p in est["publicaciones"].items()})


def prueba_vencimiento_y_reglas():
    print("vencimiento y reglas por red")
    reloj(AR_10)
    lote = [ev("viejo", horas=30),                                   # policiales vence a las 18 h
            ev("largo", "bot", duracion_seg=200),                    # X admite 140 s
            ev("ok", "bot", duracion_seg=45),
            {"id": "x:1", "origen": "otro", "titulo": "a"},          # origen inválido
            {"id": "bot:sinurl", "origen": "bot", "titulo": "b"}]    # video sin URL
    preparar(lambda: lote)
    run.pasada()
    est = estado.cargar()
    p = est["publicaciones"]
    chequear("lo que llega vencido no entra", "policiales:viejo|x" not in p)
    chequear("video > 140 s descartado para X", p["bot:largo|x"]["estado"] == "descartado", p["bot:largo|x"])
    chequear("…pero sí va a TikTok", p["bot:largo|tiktok"]["estado"] in ("programado", "pendiente"))
    chequear("inválidos rechazados", "x:1|x" not in p and "bot:sinurl|x" not in p)
    reloj(AR_10 + timedelta(hours=60))
    run.vencer(est)
    chequear("pendiente vencida queda informada", all(q["estado"] != "pendiente" for q in p.values()))


def prueba_misma_nota():
    print("reel y nota de la misma noticia en X: separados 3 h")
    reloj(AR_10)
    url = "https://www.diariolacampaña.com.ar/n/misma"
    lote = [ev("r", "bot", nota_url=url), ev("n", "web", nota_url=url)]
    preparar(lambda: lote)
    t = AR_10
    for _ in range(12):
        run.pasada()
        t += timedelta(minutes=20)
        reloj(t)
    est = estado.cargar()
    a, b = est["publicaciones"]["bot:r|x"], est["publicaciones"]["web:n|x"]
    if a.get("dueAt") and b.get("dueAt"):
        dif = abs(estado.de_iso(a["dueAt"]) - estado.de_iso(b["dueAt"]))
        chequear("separadas ≥ 3 h", dif >= timedelta(minutes=179), dif)
    else:
        chequear("separadas ≥ 3 h (la segunda espera)", bool(a.get("dueAt")) != bool(b.get("dueAt"))
                 or not b.get("dueAt"), (a.get("dueAt"), b.get("dueAt")))
    chequear("textos distintos", a.get("texto") != b.get("texto"))


def prueba_entrevistas_esperan_youtube():
    print("entrevistas: salen recién cuando el Short es público")
    reloj(AR_10)
    privacidad = {"S1": "private", "L1": "private"}
    def lote():
        return [ev("1", "entrevistas", youtube_url="https://youtube.com/shorts/S1",
                   espera_youtube_publico={"short": "S1", "largo": "L1"}, plataformas=["x"])]
    preparar(lote)
    fuentes.privacidad_youtube = lambda ids: {i: privacidad.get(i, "") for i in ids}
    run.pasada()
    est = estado.cargar()
    chequear("privado: no se encola", "entrevistas:1|x" not in est["publicaciones"])
    chequear("privado: no se marca como visto", "entrevistas:1" not in est["vistos"])
    privacidad.update(S1="public", L1="public")
    reloj(AR_10 + timedelta(minutes=20))
    run.pasada()
    est = estado.cargar()
    p = est["publicaciones"].get("entrevistas:1|x") or {}
    chequear("público: se encola y programa", p.get("estado") == "programado", p)
    chequear("enlaza la entrevista completa (largo público)", "https://youtu.be/L1" in (p.get("texto") or ""),
             p.get("texto"))
    chequear("no va a TikTok", "entrevistas:1|tiktok" not in est["publicaciones"])


def prueba_video_cambiado():
    print("el video se pisó entre que se encoló y se manda: no sale")
    reloj(AR_10)
    tam = {"v": 5_000_000}
    falso = preparar(lambda: [ev("pis", "bot")])
    fuentes.video_verificado = lambda e: (True, "", {"mb": 5, "bytes": tam["v"]})
    est = estado.cargar()
    run.encolar(est, fuentes.todas())
    tam["v"] = 7_123_456                     # otro reel con el mismo nombre
    run.enviar(est)
    p = est["publicaciones"]["bot:pis|x"]
    chequear("descartada con motivo", p["estado"] == "descartado" and "cambi" in p["error"], p)
    chequear("no llegó a Buffer", not falso.posts)


def prueba_franja():
    print("franja horaria")
    t = datetime(2026, 10, 12, 5, 0, tzinfo=timezone.utc)        # 02:00 AR
    r = planificador.en_franja(t, "x")
    chequear("02:00 AR → 07:00 AR", r.astimezone(planificador.AR).hour == 7, r)
    t2 = datetime(2026, 10, 13, 3, 15, tzinfo=timezone.utc)      # 00:15 AR
    chequear("00:15 AR ya no vale en X (hasta 24:00)", planificador.en_franja(t2, "x") != t2)
    t3 = datetime(2026, 10, 13, 2, 50, tzinfo=timezone.utc)      # 23:50 AR
    chequear("23:50 AR vale en X", planificador.en_franja(t3, "x") == t3)
    chequear("00:15 AR no vale en TikTok", planificador.en_franja(t2, "tiktok") != t2)


def prueba_intercalado():
    print("intercalado: nunca dos policiales seguidos en el mismo canal (17/10/2026)")
    os.environ["INTERCALADO"] = "1"
    try:
        t0 = AR_10
        L = [(t0, False), (t0 + timedelta(minutes=20), True)]
        chequear("pegado: después de un policial, sí", planificador.pegado(L, t0 + timedelta(minutes=40), 1))
        chequear("pegado: antes de uno de otra temática, no",
                 not planificador.pegado([(t0, False)], t0 + timedelta(minutes=5), 1))
        chequear("pegado: en medio, si el de después es policial, también",
                 planificador.pegado(L, t0 + timedelta(minutes=10), 1))
        chequear("tema: el bot con un choque en el título es policial",
                 planificador.tema.es_policial({"origen": "bot", "titulo": "Choque en la Ruta 5: dos heridos"}))
        chequear("tema: el bot con la Fiesta del Inmigrante no",
                 not planificador.tema.es_policial({"origen": "bot", "titulo": "Se viene la Fiesta del Inmigrante"}))
        cola = [{"origen": "web", "titulo": f"Nota {i}"} for i in range(3)] +                [{"origen": "policiales", "titulo": f"Pol {i}"} for i in range(3)]
        orden = [("P" if planificador.tema.es_policial(p) else "o") for p in planificador.intercalar_cola(cola, [], 1)]
        chequear("la cola se intercala: P o P o P o", orden == list("PoPoPo"), orden)
        orden = [("P" if planificador.tema.es_policial(p) else "o")
                 for p in planificador.intercalar_cola(cola, [(t0, True)], 1)]
        chequear("...y si lo último del canal fue policial, arranca con otra temática", orden[0] == "o", orden)

        reloj(AR_10)
        lote = ([ev(i) for i in range(6)] + [ev(i, "web") for i in range(3)]
                + [ev(i, "bot", titulo=f"Inauguraron la muestra número {i} en el museo") for i in range(3)]
                + [ev("choque", "bot", titulo="Choque en la Ruta 5: dos heridos")])
        falso = preparar(lambda: lote)
        t = AR_10
        for _ in range(16 * 3):
            run.pasada()
            t += timedelta(minutes=20)
            reloj(t)
            falso.publicar_vencidos()
        est = estado.cargar()
        for red in ("x", "tiktok"):
            linea = sorted((estado.de_iso(p["dueAt"]), planificador.tema.es_policial(p))
                           for p in est["publicaciones"].values()
                           if p["plataforma"] == red and p["estado"] in ("programado", "enviado"))
            seguidos = sum(1 for a, b in zip(linea, linea[1:]) if a[1] and b[1])
            chequear(f"{config.NOMBRE[red]}: ningún par de policiales seguidos", seguidos == 0,
                     [("P" if p else "o") for _, p in linea])
        linea_x = sorted((estado.de_iso(p["dueAt"]), planificador.tema.es_policial(p))
                         for p in est["publicaciones"].values()
                         if p["plataforma"] == "x" and p["estado"] in ("programado", "enviado"))
        n_pol = sum(1 for _, p in linea_x if p)
        chequear("X: salen tantos policiales como huecos hay (6 de otra temática → hasta 7 policiales)",
                 5 <= n_pol <= 7, [("P" if p else "o") for _, p in linea_x])
        esperan = [p for p in est["publicaciones"].values() if p["estado"] == "pendiente"
                   and (p.get("espera") or "").startswith("intercalado")]
        chequear("lo que no tuvo hueco espera con el motivo «intercalado»", len(esperan) >= 1,
                 [p.get("espera") for p in est["publicaciones"].values() if p["estado"] == "pendiente"][:3])

        # Lo AJENO del canal (cargado a mano en Buffer) también cuenta.
        reloj(AR_10)
        lote2 = [ev("a", "policiales")]
        falso = preparar(lambda: lote2)
        est = estado.cargar()
        est["ajenos"] = {"x": [{"dueAt": estado.iso(AR_10 + timedelta(minutes=10)), "policial": True,
                                "texto": "Robo en un comercio"}], "tiktok": []}
        estado.guardar(est)
        run.pasada()
        est = estado.cargar()
        px = est["publicaciones"]["policiales:a|x"]
        chequear("un policial cargado a mano en Buffer tampoco queda pegado a uno nuestro",
                 px["estado"] == "pendiente" and px.get("espera", "").startswith("intercalado"), px.get("espera"))
    finally:
        os.environ["INTERCALADO"] = "0"


def prueba_etapa6():
    print("etapa 6: nacionales a TikTok, radio de policiales y pausa ante el tope de TikTok (17/10/2026)")
    reloj(AR_10)
    nac = ev("n1", "bot", titulo="Argentinos Juniors ganó y alcanzó a Boca en la Tabla Anual")
    nac.update(id="nacionales:n1", origen="nacionales", plataformas=["tiktok"], categoria="Deportes",
               hashtags=["TorneoClausura"])
    lejos = ev("lejos", localidad="Pergamino", distancia_km=122.9, radio_ok=False)
    cerca = ev("cerca", localidad="Bragado", distancia_km=49.3, radio_ok=True)
    falso = preparar(lambda: [nac, lejos, cerca])
    run.pasada()
    est = estado.cargar()
    pubs = est["publicaciones"]
    chequear("nacionales: solo TikTok (a X ya va como nota web)",
             "nacionales:n1|tiktok" in pubs and "nacionales:n1|x" not in pubs, list(pubs))
    chequear("nacionales: hashtag de su categoría en TikTok",
             "#deportes" in pubs["nacionales:n1|tiktok"].get("texto", ""), pubs["nacionales:n1|tiktok"].get("texto"))
    chequear("policial fuera del radio de 100 km: no entra ni a X ni a TikTok",
             not any(k.startswith("policiales:lejos") for k in pubs))
    chequear("policial dentro del radio: entra", "policiales:cerca|x" in pubs)

    # TikTok contesta «too many posts»: pausa 24 h y tope medido.
    for i in range(14):
        k = f"bot:h{i}|tiktok"
        pubs[k] = {"clave": k, "id": f"bot:h{i}", "plataforma": "tiktok", "origen": "bot", "tipo": "video",
                   "estado": "enviado", "dueAt": estado.iso(AR_10 - timedelta(hours=20) + timedelta(minutes=40 * i)),
                   "titulo": "x", "generado": estado.iso(AR_10)}
    pausa = run.registrar_pausa(est, "tiktok", AR_10, simular=True)
    chequear("tope de TikTok: pausa 24 h desde el corte", pausa["hasta"] == estado.iso(AR_10 + timedelta(hours=24)))
    chequear("...y el tope medido es lo que aguantó menos una (14 → 13)", pausa["tope_medido"] == 13, pausa)
    chequear("es_tope_de_la_red reconoce el mensaje real de TikTok",
             run.es_tope_de_la_red("This TikTok creator has made too many posts via OpenAPI in the last 24 hours"))
    estado.guardar(est)
    reloj(AR_10 + timedelta(hours=1))
    nac2 = dict(nac, id="nacionales:n2", titulo="Netflix estrena la serie de Mafalda en noviembre",
                generado=estado.iso(AR_10 + timedelta(hours=1)))
    falso_eventos = [nac, lejos, cerca, nac2]
    fuentes.todas = lambda: falso_eventos
    run.pasada()
    est = estado.cargar()
    t = est["publicaciones"]["nacionales:n2|tiktok"]
    chequear("en pausa no se programa nada en TikTok (y el motivo queda a la vista)",
             t["estado"] == "pendiente" and t.get("espera", "").startswith("en pausa"), t.get("espera"))
    chequear("...X sigue igual", est["publicaciones"]["policiales:cerca|x"]["estado"] in ("programado", "enviado"))
    est["pausas"]["tiktok"]["hasta"] = estado.iso(AR_10)          # ya pasó la pausa
    est["pausas"]["tiktok"]["tope_medido"] = 14
    estado.guardar(est)
    _a, _d, espera = planificador.planificar(est, "tiktok", AR_10 + timedelta(hours=2))
    chequear("pasada la pausa, rige el tope medido (14 en 24 h, no 15)",
             any("tope de 14" in m for m in espera.values()) or len(_a) <= 1, espera)


def prueba_metricas_buffer():
    print("métricas de Buffer una vez por día (17/10/2026)")
    reloj(AR_10)
    falso = preparar(lambda: [ev("m1", "bot")])
    llamadas = []
    buffer_api.metricas_enviados = lambda d, h: llamadas.append(1) or {"post1": {"views": 120, "likes": 4}}
    run.pasada()
    falso.publicar_vencidos()
    est = estado.cargar()
    est.pop("metricas_buffer", None)
    estado.guardar(est)
    reloj(AR_10 + timedelta(hours=2))
    run.pasada()
    est = estado.cargar()
    con = [p for p in est["publicaciones"].values() if p.get("metricas")]
    chequear("las métricas de Buffer se pegan a su publicación", len(con) == 1 and con[0]["metricas"]["views"] == 120,
             est.get("metricas_buffer"))
    n = len(llamadas)
    reloj(AR_10 + timedelta(hours=3))
    run.pasada()
    chequear("...y se piden una sola vez por día", len(llamadas) == n, len(llamadas))
    chequear("...el panel las muestra", any("mediana vistas/impresiones 120" in l for l in run.metricas_panel(estado.cargar())))
    def falla(d, h):
        raise RuntimeError("field not available on this plan")
    buffer_api.metricas_enviados = falla
    est = estado.cargar()
    est["metricas_buffer"]["visto"] = estado.iso(AR_10 - timedelta(days=2))
    estado.guardar(est)
    run.pasada()
    chequear("si el plan no las da, queda «no disponible» y la pasada sigue",
             estado.cargar()["metricas_buffer"].get("disponible") is False)


def prueba_tandas_tiktok():
    """TikTok en TANDAS (19/10/2026): a las 10, 18 y 21 entra lo más viral (15 / 3 = 5), con equilibrio
    entre orígenes y nunca dos policiales seguidos; fuera de tanda no se programa nada."""
    print("\n— TikTok en tandas (10, 18 y 21) con lo más viral")
    os.environ["TIKTOK_TANDAS"] = "10,18,21"
    os.environ["INTERCALADO"] = "1"
    try:
        def pub(i, origen, viral, horas=1):
            return {"clave": f"{origen}{i}|tiktok", "id": f"{origen}{i}", "plataforma": "tiktok",
                    "estado": "pendiente", "origen": origen, "prioridad": viral, "titulo": f"Pieza {origen} {i}",
                    "generado": estado.iso(AR_10 - timedelta(hours=horas))}
        est = estado.vacio()
        lote = ([pub(i, "policiales", v) for i, v in enumerate((10, 9, 9, 8, 3))]
                + [pub(i, "nacionales", v) for i, v in enumerate((9, 7, 2))]
                + [pub(i, "bot", v) for i, v in enumerate((8, 4))])
        est["publicaciones"] = {x["clave"]: x for x in lote}
        a, d, espera = planificador.planificar(est, "tiktok", AR_10 + timedelta(minutes=5))
        elegidas = [k for k, _due in a]
        origenes = [est["publicaciones"][k]["origen"] for k in elegidas]
        chequear("tanda de las 10: entran 5 (tope 15 / 3 tandas)", len(elegidas) == 5, elegidas)
        chequear("equilibrio: no más de 2 del mismo origen si hay de otros",
                 max(origenes.count(o) for o in set(origenes)) <= 2, origenes)
        chequear("lo más viral adentro: el policial de 10 y el nacional de 9",
                 "policiales0|tiktok" in elegidas and "nacionales0|tiktok" in elegidas, elegidas)
        chequear("lo menos viral afuera (el policial de 3 y el nacional de 2)",
                 "policiales4|tiktok" not in elegidas and "nacionales2|tiktok" not in elegidas, elegidas)
        pol = [est["publicaciones"][k]["origen"] == "policiales" for k in elegidas]
        chequear("nunca dos policiales seguidos", not any(x and y for x, y in zip(pol, pol[1:])), origenes)
        horas = [estado.de_iso(due) for _k, due in a]
        chequear("una cada 20 minutos desde el arranque de la tanda",
                 all(19 <= (b - x).total_seconds() / 60 <= 21 for x, b in zip(horas, horas[1:])), horas)
        a2, _d, _e = planificador.planificar(est, "tiktok", AR_10 + timedelta(minutes=25))
        chequear("la misma tanda no se arma dos veces (la corrida siguiente, 20 min después)", a2 == [])
        a3, _d, e3 = planificador.planificar(est, "tiktok", AR_10 + timedelta(hours=3))
        chequear("fuera de tanda (13:00) no se programa nada y lo que queda espera la próxima",
                 a3 == [] and all("tandas" in m for m in e3.values()))
        chequear("las tandas son 10, 18 y 21",
                 planificador.tanda_actual("tiktok", AR_10 + timedelta(hours=8, minutes=10)).endswith("18")
                 and planificador.tanda_actual("tiktok", AR_10 + timedelta(hours=11, minutes=30)).endswith(" 21")
                 and planificador.tanda_actual("x", AR_10) == "")
    finally:
        os.environ["TIKTOK_TANDAS"] = "0"
        os.environ["INTERCALADO"] = "0"


def prueba_semana_finde():
    """Entre semana, X y TikTok llevan solo el diario de la mañana, el bot (primero) y los recortes; nacionales
    y policiales, el finde (20/10/2026)."""
    print("\n— Entre semana solo lo propio; lo demás, el finde —")
    preparar(lambda: [])
    config.SOLO_FINDE, config.X_WEB_SOLO_DIARIO, config.BOT_PRIMERO = {"nacionales"}, True, True
    try:
        reloj(AR_10)                                                     # lunes 10:00
        est = estado.vacio()
        run.encolar(est, [ev(1, "nacionales", plataformas=["x", "tiktok"], titulo="Tormenta en Córdoba deja evacuados"),
                          ev(2, "policiales", titulo="Robaron una moto en Bragado"),
                          dict(ev(3, "bot"), plataformas=["x", "tiktok"], titulo="Inauguraron la plaza de Villa Moquehuá"),
                          dict(ev(4, "entrevistas"), plataformas=["x"], titulo="Britos habló de las cloacas en la radio"),
                          dict(ev(5, "web"), titulo="Agro: cultivos de cobertura en soja", generado=estado.iso(AR_10 - timedelta(hours=20)))])
        n = run.depurar(est)
        px = est["publicaciones"]
        quedan = sorted(k for k, p in px.items() if p["estado"] == "pendiente")
        chequear("lunes: nacionales y notas web viejas salen de la cola; quedan el bot, los recortes y los policiales",
                 quedan == ["bot:3|tiktok", "bot:3|x", "entrevistas:4|x", "policiales:2|tiktok", "policiales:2|x"]
                 and n >= 2, (quedan, n))
        chequear("…con su motivo", "finde" in px["nacionales:1|tiktok"]["error"] and "mañana" in px["web:5|x"]["error"])
        a, _d, _e = planificador.planificar(est, "x", AR_10)
        chequear("X: el bot y el recorte se programan primero (prioridad)", {"bot:3|x", "entrevistas:4|x"} <= {k for k, _t in a}
                 and [k for k, _t in a][:2] in (["bot:3|x", "entrevistas:4|x"], ["entrevistas:4|x", "bot:3|x"]), a)
        sabado = AR_10 + timedelta(days=5)
        reloj(sabado)
        est2 = estado.vacio()
        run.encolar(est2, [ev(6, "nacionales", plataformas=["x", "tiktok"], titulo="Gran final del torneo de vóley"),
                           ev(7, "policiales", titulo="Choque en la ruta 5 cerca de Alberti")])
        chequear("sábado: nacionales sí van", run.depurar(est2) == 0
                 and all(p["estado"] == "pendiente" for p in est2["publicaciones"].values()))
        cola = [dict(clave=f"n{i}", origen="nacionales", prioridad=9, generado=estado.iso(sabado)) for i in range(4)]
        cola += [dict(clave=f"b{i}", origen="bot", prioridad=5, generado=estado.iso(sabado)) for i in range(3)]
        elegidas = [p["clave"] for p in planificador.elegir_tanda(cola, 4, 2, sabado)]
        chequear("TikTok: en la tanda el bot va primero, sin el tope de 2 por origen", elegidas[:3] == ["b0", "b1", "b2"], elegidas)
    finally:
        config.SOLO_FINDE, config.X_WEB_SOLO_DIARIO, config.BOT_PRIMERO = set(), False, False


def prueba_x_sincro():
    """X SINCRONIZADO Y SIN REPETIDOS (19/10/2026): una noticia, un posteo (el reel gana a la nota); sale
    cuando salió en las otras redes, con 5 min entre posteos de X y las notas web cada 15."""
    print("\n— X sincronizado y sin repetidos")
    os.environ["X_SINCRO"] = "1"
    os.environ["INTERCALADO"] = "1"
    for k in ("CUPO_POLICIALES_X", "CUPO_NACIONALES_X", "CUPO_ENTREVISTAS_X"):
        os.environ.pop(k, None)
    try:
        preparar(lambda: [])
        reloj(AR_10)
        est = estado.vacio()
        url = "https://www.diariolacampaña.com.ar/n/vacunacion-antigripal-en-chivilcoy-abc123"
        nota = dict(ev(1, "web"), nota_url=url, titulo="Vacunación antigripal en el hospital de Chivilcoy")
        reel = dict(ev(1, "bot"), nota_url=url, titulo="Vacunación antigripal en el hospital de Chivilcoy",
                    plataformas=["x", "tiktok"])
        run.encolar(est, [nota, reel])
        px = est["publicaciones"]
        chequear("misma noticia (nota + reel del bot) → en X sale una sola, el REEL",
                 px["bot:1|x"]["estado"] == "pendiente" and px["web:1|x"]["estado"] == "descartado"
                 and "ya sale en X" in px["web:1|x"]["error"], {k: v["estado"] for k, v in px.items()})
        chequear("…y el reel igual va a TikTok", px["bot:1|tiktok"]["estado"] == "pendiente")

        url2 = "https://www.diariolacampaña.com.ar/n/el-gobierno-anuncio-cambios-en-el-rigi-def456"
        run.encolar(est, [dict(ev(2, "web"), nota_url=url2, titulo="El Gobierno anunció cambios en el RIGI")])
        run.encolar(est, [dict(ev(2, "nacionales"), nota_url=url2, titulo="El Gobierno anunció cambios en el RIGI",
                               plataformas=["x", "tiktok"])])
        chequear("la nota que esperaba en la cola cede su lugar al reel de nacionales de la misma noticia",
                 px["web:2|x"]["estado"] == "descartado" and "reemplazada" in px["web:2|x"]["error"]
                 and px["nacionales:2|x"]["estado"] == "pendiente")

        run.encolar(est, [dict(ev(3, "policiales"), nota_url="", titulo="Choque en la Ruta 5: dos heridos cerca de Chivilcoy"),
                          dict(ev(4, "entrevistas"), nota_url="", titulo="Choque en la Ruta 5: dos heridos cerca de Chivilcoy hoy")])
        chequear("sin nota, un título casi igual también cuenta como la misma noticia",
                 px["policiales:3|x"]["estado"] == "pendiente" and px["entrevistas:4|x"]["estado"] == "descartado")
        chequear("un reel en X vence a las 2 h de salir en las otras redes (no se publica tarde)",
                 estado.de_iso(px["bot:1|x"]["vence"]) <= AR_10 + timedelta(hours=2, minutes=1))

        # Planificación: tres notas del diario a la vez y dos reels.
        titulos = ((5, "Inauguraron la nueva plaza del barrio Villa Moquehuá"),
                   (6, "El Concejo Deliberante aprobó el presupuesto municipal 2027"),
                   (7, "Gimnasia ganó el clásico y es puntero del torneo local"))
        for i, t in titulos:
            run.encolar(est, [dict(ev(i, "web"), titulo=t)])
        # Sin el plan del carrusel, una nota de la carga de la mañana espera su hora de Instagram.
        _a0, _d0, espera0 = planificador.planificar(est, "x", AR_10)
        chequear("X: sin el plan del carrusel, las notas de la mañana esperan su hora de Instagram (20/10/2026)",
                 "Instagram" in espera0.get("web:5|x", ""), espera0)
        # El plan: el carrusel saca en Instagram una nota cada 15 min desde las 10:10.
        import carrusel_notas as _cn
        plan = _cn.plan_para_x([t for _i, t in titulos], AR_10 + timedelta(minutes=10), 900)
        x_sincro.PLAN_IG.write_text(json.dumps(plan), encoding="utf-8")
        a, _d, espera = planificador.planificar(est, "x", AR_10)
        horas = {k: estado.de_iso(due) for k, due in a}
        orden = sorted(horas.values())
        chequear("X: todo lo pendiente se programa ya (sincronizado con las otras redes)",
                 orden and (orden[0] - AR_10).total_seconds() / 60 <= 5, [(k, h.isoformat()) for k, h in horas.items()])
        chequear("X: nunca dos posteos a menos de 5 minutos",
                 all((b - x).total_seconds() >= 5 * 60 - 60 for x, b in zip(orden, orden[1:])), orden)
        webs = sorted(h for k, h in horas.items() if k.startswith("web:"))
        chequear("X: cada nota del diario sale a la MISMA hora que en Instagram (el plan del carrusel)",
                 len(webs) == 3 and all(abs((h - (AR_10 + timedelta(minutes=10 + 15 * k))).total_seconds()) < 60
                                        for k, h in enumerate(webs)), webs)
        chequear("X: nada se programa más allá de 90 min", all(h <= AR_10 + timedelta(minutes=91) for h in orden))

        # Qué va y qué no (19/10/2026).
        import re as _re
        excl = _re.compile(config.WEB_TITULOS_EXCLUIDOS, _re.I)
        chequear("X: sepelios, farmacias, clima, tapa y el Archivo no van; una nota común sí",
                 all(excl.search(t) for t in ("Sepelios — lunes 19 de octubre", "Farmacias de turno",
                                             "Clima en Chivilcoy", "Tapa del diario", "Archivo Literario: 1948"))
                 and not excl.search("Gimnasia ganó el clásico y es puntero"))
        chequear("X: las notas nacionales de la web no van (las nacionales llegan como reel, 20/10/2026)",
                 config.X_SIN_NOTAS_NACIONALES)
        chequear("X: agro no va (su sección está excluida)",
                 "0b4eafce-3bb8-44df-96dd-43e53fa24a9c" in config.WEB_CATS_EXCLUIDAS)
        run.encolar(est, [dict(ev(8, "nacionales"), categoria_nacional="deportes",
                               titulo="Boca le ganó a River en la Bombonera", plataformas=["x", "tiktok"])])
        chequear("X: deportes, espectáculos, cine e IA de nacionales NO salen un lunes",
                 px["nacionales:8|x"]["estado"] == "descartado" and "sábado" in px["nacionales:8|x"]["error"]
                 and px["nacionales:8|tiktok"]["estado"] == "pendiente")
        sabado = AR_10 + timedelta(days=5)
        reloj(sabado)
        run.encolar(est, [dict(ev(9, "nacionales"), categoria_nacional="espectaculos",
                               titulo="Mirtha Legrand volvió a la televisión", plataformas=["x", "tiktok"])])
        chequear("X: …y el sábado sí", px["nacionales:9|x"]["estado"] == "pendiente")

        # Lugar para los virales: con el tope casi lleno, solo entra el viral.
        os.environ["TOPE_X_24H"], os.environ["X_RESERVA_VIRALES"] = "10", "3"
        est2 = estado.vacio()
        for i in range(7):
            est2["publicaciones"][f"ya{i}|x"] = {"clave": f"ya{i}|x", "id": f"ya{i}", "plataforma": "x",
                                                 "origen": "web", "estado": "enviado", "tipo": "nota",
                                                 "dueAt": estado.iso(sabado - timedelta(minutes=30 + 10 * i))}
        run.encolar(est2, [dict(ev(20, "nacionales"), categoria_nacional="virales",
                                titulo="El video del perro que aprendió a abrir la heladera", plataformas=["x"]),
                           dict(ev(21, "web"), titulo="El municipio repavimentó la avenida Soarez"),
                           dict(ev(22, "entrevistas"), titulo="Britos habló de las obras de cloacas en la radio",
                                plataformas=["x"])])
        a2, _d, e2 = planificador.planificar(est2, "x", sabado)
        claves = [k for k, _t in a2]
        chequear("X: con el tope casi lleno, el lugar que queda es para el reel viral… y el recorte de la radio",
                 sorted(claves) == ["entrevistas:22|x", "nacionales:20|x"] and "virales" in e2.get("web:21|x", ""),
                 (claves, e2))
        chequear("X: el recorte de la radio sale apenas sale en Instagram (antes que el resto)",
                 claves[0] == "entrevistas:22|x", claves)
        chequear("X: el viral queda marcado y sale primero", est2["publicaciones"]["nacionales:20|x"].get("viral") is True)
        os.environ.pop("TOPE_X_24H")
        os.environ.pop("X_RESERVA_VIRALES")
    finally:
        os.environ["X_SINCRO"] = "0"
        os.environ["INTERCALADO"] = "0"
        os.environ["CUPO_POLICIALES_X"], os.environ["CUPO_NACIONALES_X"], os.environ["CUPO_ENTREVISTAS_X"] = "12", "0", "10"


if __name__ == "__main__":
    for f in (prueba_textos, prueba_migracion_textos, prueba_idempotencia_y_cupos, prueba_tope_tiktok_15, prueba_urgente,
              prueba_corte_y_recuperacion, prueba_vencimiento_y_reglas, prueba_misma_nota,
              prueba_entrevistas_esperan_youtube, prueba_video_cambiado, prueba_franja, prueba_intercalado, prueba_etapa6, prueba_metricas_buffer, prueba_tandas_tiktok, prueba_x_sincro,
              prueba_semana_finde):
        f()
    print(f"\n{len(OK)}/{len(OK) + len(MAL)} ok")
    sys.exit(1 if MAL else 0)
