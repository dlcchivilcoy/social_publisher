"""Pruebas del distribuidor contra un Buffer FALSO (no toca la red ni publica nada).

    venv\\Scripts\\python.exe -m distribuidor.test_distribuidor
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("DISTRIBUIDOR_ACTIVO", "1")
# El intercalado de policiales (17/10/2026) tiene su prueba propia (prueba_intercalado); las demás
# son de cupos, topes y urgentes con lotes casi todos policiales, y se escribieron sin él.
os.environ["INTERCALADO"] = "0"

from distribuidor import buffer_api, config, estado, fuentes, planificador, run, textos  # noqa: E402

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


if __name__ == "__main__":
    for f in (prueba_textos, prueba_migracion_textos, prueba_idempotencia_y_cupos, prueba_tope_tiktok_15, prueba_urgente,
              prueba_corte_y_recuperacion, prueba_vencimiento_y_reglas, prueba_misma_nota,
              prueba_entrevistas_esperan_youtube, prueba_video_cambiado, prueba_franja, prueba_intercalado, prueba_etapa6, prueba_metricas_buffer):
        f()
    print(f"\n{len(OK)}/{len(OK) + len(MAL)} ok")
    sys.exit(1 if MAL else 0)
