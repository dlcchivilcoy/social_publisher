"""Una pasada del distribuidor (la dispara cron-job.org cada 20 minutos).

    python -m distribuidor.run              # pasada normal (publica si DISTRIBUIDOR_ACTIVO=1)
    python -m distribuidor.run --simular    # todo menos tocar Buffer; no guarda el estado
    python -m distribuidor.run --panel      # solo reescribe el panel con lo que hay

Orden de una pasada:
  1. Junta lo nuevo de las cuatro fuentes y lo encola (sin duplicar: id|red).
  2. Vence lo que esperó demasiado.
  3. Si hace falta (hay programadas cuyo horario ya pasó, o una corrida quedó a medias),
     UNA consulta a Buffer: estado de las publicaciones, canales y límites diarios.
  4. Planifica cada red y manda lo que entra, de a 10 por solicitud.
  5. Avisa por mail lo que necesita a una persona y reescribe el panel.

La cuota de la API (250/día, 3.000/mes en el plan Free) se cuida así: una pasada sin nada
nuevo y sin nada que confirmar NO consulta a Buffer.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta

from utils.config import load_config

load_config()

from utils.logger import get_logger  # noqa: E402

from . import buffer_api, config, estado, fuentes, planificador, tema, textos, x_sincro  # noqa: E402

logger = get_logger("distribuidor")


# ----------------------------------------------------------------------------- avisos
def avisar(est: dict, tipo: str, asunto: str, cuerpo: str, cada_horas: int = 12,
           simular: bool = False) -> None:
    """Mail a la redacción, como mucho uno por tipo cada `cada_horas`."""
    ultimo = estado.de_iso(est["avisos"].get(tipo))
    if ultimo and estado.ahora() - ultimo < timedelta(hours=cada_horas):
        return
    logger.warning(f"AVISO [{tipo}] {asunto}")
    if simular:
        return
    try:
        from notificar_corrida import _enviar
        if _enviar(f"[Distribuidor X/TikTok] {asunto}", cuerpo + "\n\nPanel: "
                   "https://github.com/dlcchivilcoy/social_publisher/blob/main/state/distribuidor_panel.md"):
            est["avisos"][tipo] = estado.iso(estado.ahora())
    except Exception as e:  # noqa: BLE001
        logger.warning(f"no pude mandar el aviso por mail: {e}")


# ----------------------------------------------------------------------------- 1. entrada
DIAS_ESPERA_YOUTUBE = 7


def esperan_youtube(est: dict, eventos: list) -> list:
    """Las entrevistas salen en X recién cuando su Short de YouTube es PÚBLICO (lo aprobó el
    editor). Hasta entonces quedan en el buzón; se consultan todas juntas (1 pedido)."""
    ahora = estado.ahora()
    pendientes = [e for e in eventos if isinstance(e, dict) and e.get("espera_youtube_publico")
                  and str(e.get("id")) not in est["vistos"]]
    if not pendientes:
        return eventos
    ids = []
    for e in pendientes:
        w = e["espera_youtube_publico"]
        ids += [w.get("short"), w.get("largo")]
    priv = fuentes.privacidad_youtube(ids)
    salen, quedan = [], 0
    for e in pendientes:
        w = e.pop("espera_youtube_publico")
        if priv.get(w.get("short")) == "public":
            e["generado"] = estado.iso(ahora)          # cuenta desde que se aprobó
            if w.get("largo") and priv.get(w["largo"]) == "public":
                e["youtube_url"] = f"https://youtu.be/{w['largo']}"
            salen.append(e)
            estado.anotar(est, "entrevista_aprobada", clave=e["id"])
        elif (ahora - (estado.de_iso(e.get("generado")) or ahora)) > timedelta(days=DIAS_ESPERA_YOUTUBE):
            est["vistos"][e["id"]] = estado.iso(ahora)
            estado.anotar(est, "entrevista_no_aprobada", clave=e["id"],
                          motivo=f"el Short siguió sin ser público {DIAS_ESPERA_YOUTUBE} días")
        else:
            quedan += 1
    if quedan:
        logger.info(f"entrevistas esperando que su Short sea público: {quedan}")
    en_espera = {id(e) for e in pendientes}
    otros = [e for e in eventos if id(e) not in en_espera]
    return otros + salen


def encolar(est: dict, eventos: list) -> dict:
    cuenta = {"nuevos": 0, "repetidos": 0, "rechazados": 0, "reintentar": 0}
    ahora = estado.ahora()
    eventos = esperan_youtube(est, eventos)
    # X sin repetidos (19/10/2026): los REELS primero, así el video de una noticia gana a su nota.
    eventos = sorted(eventos, key=lambda e: 1 if (e.get("tipo") or ("nota" if e.get("origen") == "web" else "video"))
                     == "nota" else 0)
    for crudo in eventos:
        ev, motivo = fuentes.normalizar(crudo)
        if not ev:
            cuenta["rechazados"] += 1
            logger.warning(f"evento rechazado ({motivo}): {str(crudo)[:160]}")
            continue
        if ev["id"] in est["vistos"]:
            cuenta["repetidos"] += 1
            continue
        generado = estado.de_iso(ev["generado"]) or ahora
        vence = generado + timedelta(hours=config.VENCE_HORAS[ev["origen"]])
        if vence < ahora:
            est["vistos"][ev["id"]] = estado.iso(ahora)
            continue           # llegó viejo: no tiene sentido publicarlo
        redes = [p for p in ev["plataformas"] if config.puede_ir(ev["origen"], p)]
        if not redes:
            continue           # no va a ninguna red (cupo 0 o apagada): ni se anota
        # Verificaciones del medio (una vez por contenido).
        problema = ""
        if ev["tipo"] == "video":
            ok, problema, info = fuentes.video_verificado(ev)
            ev.update({f"video_{k}": v for k, v in info.items()})
        else:
            ok, problema = fuentes.nota_verificada(ev["nota_url"])
            if not ok and (ahora - generado) < timedelta(hours=3):
                # Una nota recién publicada puede tardar en abrir (caché de la web).
                cuenta["reintentar"] += 1
                continue
        est["contenidos"][ev["id"]] = {k: v for k, v in ev.items() if not k.startswith("_")}
        est["vistos"][ev["id"]] = estado.iso(ahora)
        cuenta["nuevos"] += 1
        # X: una noticia, un posteo (x_sincro). La nota que ya estaba en la cola cede su lugar al reel.
        noticia, repetida_x = ("", "")
        if "x" in redes and x_sincro.activo():
            noticia, repetida_x = x_sincro.filtrar(est, ev, ahora)
        for red in redes:
            k = estado.clave(ev["id"], red)
            if k in est["publicaciones"]:
                continue
            pub = {"clave": k, "id": ev["id"], "plataforma": red, "origen": ev["origen"],
                   "tipo": ev["tipo"], "titulo": ev.get("titulo", "")[:200],
                   "generado": ev["generado"], "creado": estado.iso(ahora),
                   "actualizado": estado.iso(ahora), "vence": estado.iso(vence),
                   "prioridad": ev["prioridad"], "urgente": ev["urgente"],
                   "nota_url": ev.get("nota_url", ""), "video_url": ev.get("video_url", ""),
                   "video_bytes": ev.get("video_bytes", 0),
                   "estado": "pendiente", "intentos": 0, "buffer_id": "", "dueAt": "",
                   "error": "", "link": ""}
            if red == "x" and x_sincro.activo():
                pub["noticia"] = noticia
                # Sincronizado: fuera de su ventana ya no sale (no se publica tarde).
                pub["vence"] = estado.iso(min(vence, generado + timedelta(hours=x_sincro.ventana_h(ev["tipo"]))))
            apto, motivo = (ok, problema) if not ok else fuentes.apto_para(ev, red)
            if apto and red == "x" and repetida_x:
                apto, motivo = False, repetida_x
            if not apto:
                pub.update(estado="descartado", error=motivo)
            else:
                pub["texto"] = textos.texto_x(ev) if red == "x" else textos.texto_tiktok(ev)
                pub["texto_v"] = textos.VERSION
            est["publicaciones"][k] = pub
            estado.anotar(est, "recibido", clave=k, origen=ev["origen"],
                          estado=pub["estado"], motivo=pub["error"])
    return cuenta


# ----------------------------------------------------------------------------- 1b. textos
def migrar_textos(est: dict, simular: bool = False) -> dict:
    """Rehace con el formato vigente (textos.VERSION) lo que todavía no salió.

    - En la cola propia: se reescribe el texto y listo.
    - Ya programado en Buffer (y falta más de 10 min): se BORRA de Buffer (una solicitud para
      todos) y vuelve a la cola con el texto nuevo; solo lo que Buffer confirmó que borró.
    """
    ahora = estado.ahora()
    cuenta = {"cola": 0, "buffer": 0}
    borrar = []
    for p in est["publicaciones"].values():
        if p.get("texto_v") == textos.VERSION or p["estado"] not in ("pendiente", "programado"):
            continue
        ev = est["contenidos"].get(p["id"])
        if not ev:
            continue
        nuevo = textos.texto_x(ev) if p["plataforma"] == "x" else textos.texto_tiktok(ev)
        if p["estado"] == "pendiente":
            p.update(texto=nuevo, texto_v=textos.VERSION)
            cuenta["cola"] += 1
        elif p.get("buffer_id") and (estado.de_iso(p.get("dueAt")) or ahora) > ahora + timedelta(minutes=10):
            p["_texto_nuevo"] = nuevo
            borrar.append(p)
    if borrar and not simular and config.activo():
        try:
            res = buffer_api.crear_lote([], [p["buffer_id"] for p in borrar])
        except buffer_api.BufferError as e:
            logger.warning(f"no pude rehacer los programados con texto viejo: {e}")
            res = {"_borrados": []}
        for p in borrar:
            nuevo = p.pop("_texto_nuevo")
            if p["buffer_id"] in res.get("_borrados", []):
                p.update(estado="pendiente", buffer_id="", dueAt="", texto=nuevo,
                         texto_v=textos.VERSION, espera="texto rehecho (formato nuevo)")
                estado.anotar(est, "texto_rehecho", clave=p["clave"])
                cuenta["buffer"] += 1
    else:
        for p in borrar:
            p.pop("_texto_nuevo", None)
    if cuenta["cola"] or cuenta["buffer"]:
        logger.info(f"textos rehechos con el formato nuevo: {cuenta}")
    return cuenta


# ----------------------------------------------------------------------------- 2. vencer
def vencer(est: dict) -> int:
    ahora, n = estado.ahora(), 0
    for p in est["publicaciones"].values():
        if p["estado"] == "pendiente" and (estado.de_iso(p.get("vence")) or ahora) < ahora:
            p.update(estado="vencido", actualizado=estado.iso(ahora),
                     error=p.get("espera") or "no hubo lugar antes de su vencimiento")
            estado.anotar(est, "vencido", clave=p["clave"], motivo=p["error"])
            n += 1
    return n


# ----------------------------------------------------------------------------- 3. sincronizar
def cuota_alcanza(est: dict, para: str) -> bool:
    """¿Queda cuota? `para` = "consulta" (prescindible) o "publicar" (lo importante)."""
    q, visto = est.get("cuota") or {}, estado.de_iso(est.get("cuota_visto"))
    if not q or not visto:
        return True
    ahora = estado.ahora()
    for ventana, reserva in (("24h", config.RESERVA_24H), ("30d", config.RESERVA_30D)):
        v = q.get(ventana)
        if not v or ahora > visto + timedelta(seconds=v["segundos"]):
            continue      # esa ventana ya se renovó
        minimo = reserva if para == "consulta" else 2
        if v["restantes"] <= minimo:
            return False
    return True


def hace_falta_sincronizar(est: dict) -> bool:
    ahora = estado.ahora()
    pubs = est["publicaciones"].values()
    if any(p["estado"] == "enviando" for p in pubs):
        return True
    vencidas = [p for p in pubs if p["estado"] == "programado"
                and (estado.de_iso(p.get("dueAt")) or ahora) < ahora - timedelta(minutes=5)]
    if not vencidas:
        return False
    ultima = estado.de_iso(est.get("ultima_sync"))
    return not ultima or ahora - ultima >= timedelta(minutes=config.SINCRONIZAR_CADA_MIN)


def sincronizar(est: dict, simular: bool = False) -> None:
    ahora = estado.ahora()
    pubs = [p for p in est["publicaciones"].values() if p["estado"] in ("programado", "enviando")]
    fechas = [estado.de_iso(p["dueAt"]) for p in pubs if p.get("dueAt")] or [ahora]
    desde = min(fechas) - timedelta(hours=1)
    hasta = max(max(fechas), ahora) + timedelta(days=1)
    datos = buffer_api.estado_general(estado.iso(desde), estado.iso(hasta))
    est["ultima_sync"] = estado.iso(ahora)

    por_canal = {config.canal(p): p for p in config.PLATAFORMAS}
    for c in datos["canales"]:
        red = por_canal.get(c["id"])
        if not red:
            continue
        caido = bool(c.get("isDisconnected") or c.get("isLocked"))
        est["canales"][red] = {"desconectado": caido, "pausado": bool(c.get("isQueuePaused")),
                               "visto": estado.iso(ahora)}
        if caido:
            avisar(est, f"desconectado_{red}", f"{config.NOMBRE[red]} se desconectó de Buffer",
                   f"El canal {config.NOMBRE[red]} @diarioyradio aparece desconectado en Buffer.\n"
                   "Hay que reconectarlo: publish.buffer.com → Settings → Channels → Reconnect.\n"
                   "Mientras tanto no se programa nada en esa red (la cola espera).",
                   simular=simular)
    for lim in datos["limites"]:
        red = por_canal.get(lim["channelId"])
        if red:
            est["canales"].setdefault(red, {})["limite_buffer"] = lim

    por_id = {p["id"]: p for p in datos["posts"]}
    completo = len(datos["posts"]) < 100
    # Lo AJENO del canal (cargado a mano en Buffer u otra herramienta): no se toca, pero cuenta
    # para el intercalado de policiales (planificador.linea_del_canal, 17/10/2026).
    nuestros = {p.get("buffer_id") for p in est["publicaciones"].values() if p.get("buffer_id")}
    ajenos = {red: [] for red in config.PLATAFORMAS}
    for b in datos["posts"]:
        red = por_canal.get(b.get("channelId"))
        if red and b.get("id") not in nuestros and b.get("status") in ("scheduled", "sending", "sent"):
            ajenos[red].append({"dueAt": b.get("sentAt") or b.get("dueAt") or "",
                                "policial": tema.es_policial({"texto": b.get("text") or ""}),
                                "texto": (b.get("text") or "")[:120]})
    est["ajenos"] = ajenos
    for p in pubs:
        canal = config.canal(p["plataforma"])
        if p["estado"] == "enviando":
            # Corrida anterior cortada: ¿Buffer la recibió? Se busca por canal + dueAt exacto.
            hallada = next((b for b in datos["posts"] if b["channelId"] == canal
                            and (b.get("dueAt") or "")[:19] == p["dueAt"][:19]), None)
            if hallada:
                p.update(estado="programado", buffer_id=hallada["id"])
                estado.anotar(est, "recuperado", clave=p["clave"], buffer_id=hallada["id"])
            else:
                p.update(estado="pendiente", dueAt="")
                estado.anotar(est, "reencolado", clave=p["clave"])
                continue
        b = por_id.get(p.get("buffer_id"))
        if not b:
            if completo and (estado.de_iso(p["dueAt"]) or ahora) > desde:
                p.update(estado="descartado", error="la borraron a mano en Buffer",
                         actualizado=estado.iso(ahora))
                estado.anotar(est, "borrado_en_buffer", clave=p["clave"])
            continue
        if b["status"] == "sent":
            p.update(estado="enviado", link=b.get("externalLink") or "",
                     enviado=b.get("sentAt") or "", actualizado=estado.iso(ahora))
            estado.anotar(est, "enviado", clave=p["clave"], link=p["link"])
        elif b["status"] == "error":
            err = (b.get("error") or {})
            p.update(estado="error", error=err.get("message") or "Buffer no pudo publicarla",
                     error_crudo=(err.get("rawError") or "")[:300], actualizado=estado.iso(ahora))
            estado.anotar(est, "error_publicacion", clave=p["clave"], motivo=p["error"])
            if es_tope_de_la_red(p["error"] + " " + p["error_crudo"]):
                registrar_pausa(est, p["plataforma"], estado.de_iso(p.get("dueAt")) or ahora, simular)


# ------------------------------------------------------------ 3b. la red pide esperar (17/10/2026)
# TikTok cortó 6 veces del 12 al 16/10 con «too many posts via OpenAPI in the last 24 hours» con
# 13 y con 15 publicaciones en las 24 h previas: su tope real está por debajo del nuestro. El PDF
# «Prompt integral» pide no insistir contra una restricción: se pausa el canal hasta que se cumpla
# el plazo y su tope baja a lo que de verdad aguantó (durante DIAS_TOPE_MEDIDO).
DIAS_TOPE_MEDIDO = 7
_TOPE_RED = ("too many posts", "wait 24 hours", "spam_risk_too_many_posts", "large number of posts")


def es_tope_de_la_red(texto: str) -> bool:
    t = (texto or "").lower()
    return any(x in t for x in _TOPE_RED)


def registrar_pausa(est: dict, red: str, cuando: datetime, simular: bool = False) -> dict:
    """Pausa `red` 24 h desde `cuando` y mide su tope: las que salieron en las 24 h previas, menos una."""
    salieron = sum(1 for p in est["publicaciones"].values()
                   if p["plataforma"] == red and p["estado"] == "enviado"
                   and cuando - timedelta(hours=24) < (estado.de_iso(p.get("dueAt")) or cuando) < cuando)
    pausas = est.setdefault("pausas", {})
    previa = pausas.get(red) or {}
    hasta = max(cuando + timedelta(hours=24), estado.de_iso(previa.get("hasta")) or cuando)
    medido = max(1, salieron - 1)
    if previa.get("tope_medido") and (estado.de_iso(previa.get("medido")) or cuando) > cuando - timedelta(days=DIAS_TOPE_MEDIDO):
        medido = min(medido, int(previa["tope_medido"]))
    pausas[red] = {"hasta": estado.iso(hasta), "tope_medido": medido, "medido": estado.iso(cuando),
                   "motivo": f"{config.NOMBRE[red]} pidió esperar 24 h (salieron {salieron} en las 24 h previas)"}
    estado.anotar(est, "pausa_red", red=red, hasta=pausas[red]["hasta"], tope=medido)
    avisar(est, f"pausa_{red}", f"{config.NOMBRE[red]} pidió esperar 24 horas",
           f"{pausas[red]['motivo']}.\n"
           f"El distribuidor no programa nada en {config.NOMBRE[red]} hasta "
           f"{hasta.astimezone(planificador.AR):%d/%m %H:%M} (hora AR) y baja su tope a {medido} por 24 h "
           f"durante {DIAS_TOPE_MEDIDO} días. No hace falta hacer nada.", simular=simular)
    return pausas[red]


def leer_metricas(est: dict, cada_horas: int = 24) -> None:
    """Métricas de Buffer de lo enviado en los últimos 3 días, una vez por día (1 solicitud). Panel
    de la etapa 7 (17/10/2026). Si el plan no las da, queda dicho y no se reintenta hasta mañana."""
    ahora = estado.ahora()
    visto = estado.de_iso((est.get("metricas_buffer") or {}).get("visto"))
    if visto and ahora - visto < timedelta(hours=cada_horas):
        return
    info = {"visto": estado.iso(ahora)}
    try:
        datos = buffer_api.metricas_enviados(estado.iso(ahora - timedelta(days=3)), estado.iso(ahora))
    except Exception as e:                                       # noqa: BLE001 — nunca frena
        info.update(disponible=False, motivo=f"Buffer no las dio: {str(e)[:160]}")
    else:
        por_id = {p.get("buffer_id"): p for p in est["publicaciones"].values() if p.get("buffer_id")}
        con = 0
        for bid, m in datos.items():
            if m and bid in por_id:
                por_id[bid]["metricas"] = m
                con += 1
        info.update(disponible=con > 0, con_metricas=con, enviados=len(datos),
                    motivo="" if con else "Buffer devolvió los posts sin métricas (¿plan Free?)")
    est["metricas_buffer"] = info


def metricas_panel(est: dict) -> list:
    """Por red y origen: cuántos enviados tienen métricas y la mediana de vistas/impresiones y likes."""
    import statistics
    info = est.get("metricas_buffer") or {}
    if not info:
        return ["- todavía no se leyeron (una vez por día)"]
    if not info.get("disponible"):
        return [f"- no disponibles: {info.get('motivo') or 'sin datos'} (visto {info.get('visto', '—')[:16]})"]
    filas = []
    for red in config.PLATAFORMAS:
        for origen in config.ORIGENES:
            ms = [p["metricas"] for p in est["publicaciones"].values()
                  if p["plataforma"] == red and p["origen"] == origen and p.get("metricas")]
            if not ms:
                continue
            def med(*claves):
                v = [m.get(c) for m in ms for c in claves[:1] if isinstance(m.get(c), (int, float))]
                if not v:
                    v = [m.get(c) for m in ms for c in claves[1:] if isinstance(m.get(c), (int, float))]
                return f"{statistics.median(v):g}" if v else "s/d"
            filas.append(f"- {config.NOMBRE[red]} · {config.NOMBRE_ORIGEN[origen]}: {len(ms)} con métricas · "
                         f"mediana vistas/impresiones {med('views', 'impressions')} · likes {med('likes', 'reactions')}"
                         + (" ⚠️muestra chica" if len(ms) < 5 else ""))
    return filas or ["- ninguna publicación con métricas todavía"]


def medir_topes_historicos(est: dict) -> None:
    """Una sola vez (17/10/2026): los cortes de TikTok que ya estaban en el estado fijan su tope
    medido, sin mandar mails (ya pasaron)."""
    if "pausas" in est:
        return
    est["pausas"] = {}
    errores = sorted((p for p in est["publicaciones"].values() if p["estado"] == "error"
                      and es_tope_de_la_red(f"{p.get('error')} {p.get('error_crudo')}")),
                     key=lambda p: p.get("dueAt") or "")
    for p in errores:
        cuando = estado.de_iso(p.get("dueAt"))
        if cuando:
            registrar_pausa(est, p["plataforma"], cuando, simular=True)


def pausada(est: dict, red: str, ahora: datetime) -> str:
    p = (est.get("pausas") or {}).get(red) or {}
    hasta = estado.de_iso(p.get("hasta"))
    return p.get("motivo", "") if hasta and hasta > ahora else ""


# ----------------------------------------------------------------------------- 4. enviar
def enviar(est: dict, simular: bool = False) -> dict:
    ahora = estado.ahora()
    asignadas, borrar, resumen = [], [], {}
    for red in config.PLATAFORMAS:
        if not config.plataforma_activa(red):
            continue
        if (est["canales"].get(red) or {}).get("desconectado"):
            resumen[red] = "canal desconectado: no se programa"
            continue
        motivo_pausa = pausada(est, red, ahora)
        if motivo_pausa:
            resumen[red] = f"en pausa: {motivo_pausa}"
            for p in est["publicaciones"].values():
                if p["plataforma"] == red and p["estado"] == "pendiente":
                    p["espera"] = f"en pausa: {motivo_pausa}"
            continue
        a, d, espera = planificador.planificar(est, red, ahora)
        for k, motivo in espera.items():
            est["publicaciones"][k]["espera"] = motivo
        asignadas += a
        borrar += d
        resumen[red] = f"{len(a)} a programar, {len(d)} a correr, {len(espera)} esperando"
    if not asignadas and not borrar:
        return resumen

    pubs = est["publicaciones"]
    # El video tiene que seguir siendo EL MISMO que se encoló (mismo tamaño exacto): si el
    # archivo se pisó o se borró, no se manda (X saldría con el video de otra nota).
    if not simular:
        sanas = []
        for k, due in asignadas:
            p = pubs[k]
            if p["tipo"] == "video" and p.get("video_bytes"):
                ok, motivo, info = fuentes.video_verificado(p)
                if not ok or info.get("bytes") != p["video_bytes"]:
                    p.update(estado="descartado", actualizado=estado.iso(ahora),
                             error=motivo or "el video cambió desde que se encoló")
                    estado.anotar(est, "video_cambiado", clave=k, motivo=p["error"])
                    continue
            sanas.append((k, due))
        asignadas = sanas
    for k, due in asignadas:
        p = pubs[k]
        p["dueAt"] = due
        logger.info(f"→ {config.NOMBRE[p['plataforma']]} {due} [{p['origen']}] "
                    f"{(p.get('texto') or '')[:90]!r}")
    if simular:
        for k, due in asignadas:
            pubs[k]["estado"] = "simulado"
        return resumen

    # Desprogramar (urgentes que necesitan lugar) va en la PRIMERA solicitud.
    ids_borrar = [pubs[k]["buffer_id"] for k in borrar]
    # Marca de «enviando» guardada ANTES de llamar: si la corrida muere a mitad,
    # la próxima busca en Buffer por canal + dueAt y no la manda dos veces.
    for k, _ in asignadas:
        pubs[k]["estado"] = "enviando"
    estado.guardar(est)

    lotes = [asignadas[i:i + buffer_api.LOTE_MAX] for i in range(0, len(asignadas), buffer_api.LOTE_MAX)] or [[]]
    trabadas = set()
    for n, lote in enumerate(lotes):
        lote = [(k, d) for k, d in lote if pubs[k]["plataforma"] not in trabadas]
        datos = [{**pubs[k], "clave": k, "dueAt": d,
                  "video_url": pubs[k].get("video_url") or "",
                  "imagen_url": (est["contenidos"].get(pubs[k]["id"]) or {}).get("imagen_url", "")
                  if pubs[k]["tipo"] == "nota" and config.WEB_X_CON_FOTO else ""}
                 for k, d in lote]
        try:
            res = buffer_api.crear_lote(datos, ids_borrar if n == 0 else None)
        except buffer_api.BufferError as e:
            # Error de la solicitud entera: NO se sabe si Buffer recibió algo → quedan en
            # «enviando» y la próxima pasada lo averigua por canal + dueAt.
            logger.error(f"Buffer: {e}")
            if not e.pasajero:
                for k, _ in lote:
                    pubs[k].update(estado="pendiente", dueAt="", error=str(e))
            avisar(est, "api", "Buffer rechazó una solicitud", str(e), simular=simular)
            break
        if n == 0:
            for k in borrar:
                if pubs[k]["buffer_id"] in res.get("_borrados", []):
                    pubs[k].update(estado="pendiente", buffer_id="", dueAt="",
                                   espera="corrida por una urgente")
                    estado.anotar(est, "desprogramado", clave=k)
        for k, _ in lote:
            r, p = res.get(k) or {}, pubs[k]
            p["actualizado"] = estado.iso(ahora)
            if r.get("ok"):
                p.update(estado="programado", buffer_id=r["id"], error="", espera="")
                estado.anotar(est, "programado", clave=k, dueAt=p["dueAt"], buffer_id=r["id"])
            elif r.get("tope"):
                p.update(estado="pendiente", dueAt="", espera=f"Buffer: {r.get('error')}")
                trabadas.add(p["plataforma"])
            elif r.get("pasajero") and p["intentos"] + 1 < config.MAX_INTENTOS:
                p.update(estado="pendiente", dueAt="", intentos=p["intentos"] + 1,
                         error=r.get("error", ""))
            else:
                p.update(estado="error", error=r.get("error") or "rechazada por Buffer")
                estado.anotar(est, "error_programar", clave=k, motivo=p["error"])
        estado.guardar(est)
    return resumen


# ----------------------------------------------------------------------------- 5. panel
def _hoy_ar() -> str:
    return estado.ahora().astimezone(planificador.AR).strftime("%Y-%m-%d")


def panel(est: dict) -> str:
    hoy = _hoy_ar()
    filas = []
    tot = {}
    for p in est["publicaciones"].values():
        ref = p.get("dueAt") or p.get("creado")
        dt = estado.de_iso(ref)
        if not dt or dt.astimezone(planificador.AR).strftime("%Y-%m-%d") != hoy:
            continue
        k = (p["origen"], p["plataforma"])
        tot.setdefault(k, {}).setdefault(p["estado"], 0)
        tot[k][p["estado"]] += 1
    cols = ["enviado", "programado", "pendiente", "error", "vencido", "descartado"]
    filas.append("| Origen | Red | Cupo | " + " | ".join(cols) + " |")
    filas.append("|---|---|---:|" + "---:|" * len(cols))
    for o in config.ORIGENES:
        for r in config.PLATAFORMAS:
            c = config.cupo(o, r)
            if not c and not tot.get((o, r)):
                continue
            t = tot.get((o, r), {})
            filas.append(f"| {config.NOMBRE_ORIGEN[o]} | {config.NOMBRE[r]} | {c} | "
                         + " | ".join(str(t.get(x, 0)) for x in cols) + " |")
    cola = sorted((p for p in est["publicaciones"].values() if p["estado"] == "pendiente"),
                  key=lambda p: -planificador.puntaje(p, estado.ahora()))
    errores = [p for p in est["publicaciones"].values() if p["estado"] == "error"][-15:]
    q = est.get("cuota") or {}
    canales = est.get("canales") or {}
    md = [f"# Distribuidor X / TikTok — panel",
          f"Actualizado: {estado.ahora().astimezone(planificador.AR):%d/%m/%Y %H:%M} (hora AR) · "
          f"{'ACTIVO' if config.activo() else 'APAGADO (solo junta la cola)'}", "",
          f"## Hoy ({hoy})", *filas, "",
          "## Canales",
          *[f"- {config.NOMBRE[r]}: " + ("⚠️ DESCONECTADO" if (canales.get(r) or {}).get("desconectado")
                                         else "conectado")
            + (f" · Buffer: {canales[r]['limite_buffer']['sent']} enviadas + "
               f"{canales[r]['limite_buffer']['scheduled']} programadas de "
               f"{canales[r]['limite_buffer']['limit']} por día"
               if (canales.get(r) or {}).get("limite_buffer") else "")
            + (f" · ⏸ EN PAUSA hasta {estado.de_iso(est['pausas'][r]['hasta']).astimezone(planificador.AR):%d/%m %H:%M}"
               if pausada(est, r, estado.ahora()) else "")
            + (f" · tope medido {est['pausas'][r]['tope_medido']} por 24 h"
               if ((est.get("pausas") or {}).get(r) or {}).get("tope_medido") else "")
            for r in config.PLATAFORMAS],
          "", "## Métricas (Buffer, lo enviado en 3 días)",
          *metricas_panel(est),
          "", "## Cuota de la API de Buffer",
          (" · ".join(f"{v}: quedan {q[v]['restantes']} de {q[v]['tope']}" for v in ("15m", "24h", "30d") if v in q)
           or "sin datos todavía") + f" (visto {est.get('cuota_visto', '—')})", "",
          f"## Cola propia ({len(cola)} esperando)",
          *[f"- {config.NOMBRE[p['plataforma']]} · {config.NOMBRE_ORIGEN[p['origen']]} · "
            f"{'🔴 URGENTE · ' if p.get('urgente') else ''}{p.get('titulo', '')[:80]} — "
            f"{p.get('espera') or 'en turno'}" for p in cola[:30]], "",
          "## Errores recientes",
          *([f"- {config.NOMBRE[p['plataforma']]} · {p.get('titulo', '')[:70]} — {p['error']}"
             for p in errores] or ["- ninguno"]), "",
          "## Últimos movimientos",
          *[f"- {r['cuando'][:16]} {r['evento']} {r.get('clave', '')[:60]} {r.get('motivo', '') or ''}"
            for r in est["registro"][-25:]]]
    return "\n".join(md) + "\n"


# ----------------------------------------------------------------------------- pasada
def pasada(simular: bool = False) -> int:
    est = estado.cargar()
    eventos = fuentes.todas()
    cuenta = encolar(est, eventos)
    migrar_textos(est, simular)
    medir_topes_historicos(est)
    if config.activo() and not simular and cuota_alcanza(est, "consulta"):
        leer_metricas(est)
    venc = vencer(est)
    logger.info(f"entrada: {cuenta} · vencidos: {venc}")

    if not config.activo() and not simular:
        logger.info("DISTRIBUIDOR_ACTIVO no está prendido: se junta la cola, no se toca Buffer.")
    else:
        try:
            if hace_falta_sincronizar(est) and cuota_alcanza(est, "consulta"):
                if not simular:
                    sincronizar(est, simular)
            if cuota_alcanza(est, "publicar"):
                resumen = enviar(est, simular)
                logger.info(f"programación: {resumen}")
            else:
                avisar(est, "cuota", "Cuota de la API de Buffer casi agotada",
                       f"Quedan pocas solicitudes ({buffer_api.resumen_cuota()}). "
                       "Se frena la programación hasta que se renueve la ventana.",
                       simular=simular)
        except buffer_api.BufferError as e:
            logger.error(f"Buffer: {e}")
            avisar(est, "api", "Problema con la API de Buffer", str(e), simular=simular)
        if buffer_api.cuota:
            est["cuota"], est["cuota_visto"] = dict(buffer_api.cuota), estado.iso(estado.ahora())
            q30 = buffer_api.cuota.get("30d", {})
            if q30 and q30.get("restantes", 9999) < config.RESERVA_30D:
                avisar(est, "cuota30", "Cuota mensual de Buffer baja",
                       f"Quedan {q30['restantes']} solicitudes en la ventana de 30 días.",
                       cada_horas=48, simular=simular)
        logger.info(f"solicitudes a Buffer en esta pasada: {buffer_api.solicitudes} · "
                    f"{buffer_api.resumen_cuota()}")

    estado.podar(est)
    fuentes.limpiar_buzon(est)
    if not simular:
        fuentes.limpiar_release(est)
    texto = panel(est)
    if simular:
        print(texto)
        print(json.dumps({k: {x: p.get(x) for x in ("estado", "dueAt", "texto", "error", "espera")}
                          for k, p in est["publicaciones"].items()
                          if p["estado"] in ("simulado", "pendiente", "descartado")},
                         ensure_ascii=False, indent=1))
        return 0
    estado.guardar(est)
    estado.PANEL.write_text(texto, encoding="utf-8")
    resumen_gh = os.environ.get("GITHUB_STEP_SUMMARY")
    if resumen_gh:
        with open(resumen_gh, "a", encoding="utf-8") as fh:
            fh.write(texto)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--simular", action="store_true", help="no toca Buffer ni guarda el estado")
    ap.add_argument("--panel", action="store_true", help="solo reescribe el panel")
    a = ap.parse_args()
    if a.panel:
        est = estado.cargar()
        estado.PANEL.write_text(panel(est), encoding="utf-8")
        print(panel(est))
        return 0
    return pasada(simular=a.simular)


if __name__ == "__main__":
    sys.exit(main())
