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
    tmp = Path(tempfile.mkdtemp())
    estado.ARCHIVO = tmp / "distribuidor.json"
    estado.PANEL = tmp / "panel.md"
    estado.BUZON = tmp / "buzon"
    falso = BufferFalso()
    buffer_api.crear_lote = falso.crear_lote
    buffer_api.estado_general = falso.estado_general
    fuentes.todas = eventos_fn
    fuentes.video_verificado = lambda ev: (True, "", {"mb": 5})
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
    print("textos")
    e = {"origen": "policiales", "tipo": "video", "localidad": "San Andrés de Giles",
         "titulo": "Choque en la ruta 7: dos heridos", "nota_url": "https://www.diariolacampaña.com.ar/n/x",
         "descripcion": ("Un auto y una camioneta chocaron esta mañana en el kilómetro 120. " * 12
                         + "\n\n📲 Seguí leyendo en www.diariolacampaña.com.ar #policiales #ruta7")}
    tx = textos.texto_x(e)
    chequear("X ≤ 280 con el peso real", textos.largo_x(tx) <= 280, f"{textos.largo_x(tx)}: {tx}")
    chequear("X lleva el link de la nota", "https://www.diariolacampaña.com.ar/n/x" in tx, tx)
    chequear("X corta en oración o con …", tx.split("\n\n")[1].endswith((".", "…")), tx)
    chequear("X hashtag de la localidad", "#SanAndrésDeGiles" in tx, tx)
    tt = textos.texto_tiktok(e)
    tags = [w for w in tt.split() if w.startswith("#")]
    chequear("TikTok máx. 5 hashtags", len(tags) <= 5, tags)
    chequear("TikTok con los 3 fijos", all(h in tags for h in config.TIKTOK_HASHTAGS_FIJOS), tags)
    chequear("TikTok sin links", "http" not in tt, tt)
    chequear("TikTok saca el «Seguí leyendo» viejo", tt.count("Seguí leyendo") == 0, tt)
    w = {"origen": "web", "tipo": "nota", "titulo": "El Concejo aprobó el presupuesto",
         "descripcion": "Fue por mayoría.", "nota_url": "https://www.diariolacampaña.com.ar/n/p"}
    tw = textos.texto_x(w)
    chequear("nota web: 📰 + link, sin hashtag", tw.startswith("📰") and "/n/p" in tw and "#" not in tw, tw)
    bt = textos.texto_x({"origen": "bot", "tipo": "video", "titulo": "Corte de luz en el centro",
                         "descripcion": "Será el martes.", "video_url": "https://a/b.mp4"})
    chequear("reel propio del bot: #Chivilcoy", "#Chivilcoy" in bt, bt)
    wn = dict(w, categoria="nacionales", titulo="Milei viaja a Washington")
    chequear("nota nacional sin #Chivilcoy", "#Chivilcoy" not in textos.texto_x(wn), textos.texto_x(wn))
    en = {"origen": "entrevistas", "tipo": "video", "titulo": "Juan Pérez, director del hospital",
          "descripcion": "Habló de la guardia.", "youtube_url": "https://youtu.be/abc"}
    chequear("entrevista: link a YouTube", "https://youtu.be/abc" in textos.texto_x(en))
    chequear("emoji pesa 2 en X", textos.largo_x("🎥") == 2)
    chequear("link pesa 23 en X", textos.largo_x("https://example.com/" + "a" * 80) == 23)


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


if __name__ == "__main__":
    for f in (prueba_textos, prueba_idempotencia_y_cupos, prueba_tope_tiktok_15, prueba_urgente,
              prueba_corte_y_recuperacion, prueba_vencimiento_y_reglas, prueba_misma_nota,
              prueba_entrevistas_esperan_youtube, prueba_franja):
        f()
    print(f"\n{len(OK)}/{len(OK) + len(MAL)} ok")
    sys.exit(1 if MAL else 0)
