"""Borrar TODO lo que el bot publicó de una nota: la web y cada red (botón del Editor de notas).

Pedido del editor (27/10/2026): «apretar un botón para borrar todo lo que cargó el bot: la nota en la
web, el reel en IG/FB/TikTok/X». Cada red deja lo que deja:

  • Web (Wix)            → se borra (papelera) y la web la deja de mostrar al instante.
  • Facebook             → se borran el reel y la historia (token de la página, pages_manage_posts).
  • Instagram            → se borran el reel y la historia por API (DELETE /{media}, desde dic-2025) SI el
                           token tiene el permiso `instagram_manage_contents`; si no, queda el link.
  • YouTube (Short)      → se borra (token force-ssl del canal Diario La Campaña).
  • X y TikTok (Buffer)  → lo que todavía NO salió se cancela en Buffer y se saca de la cola del
                           distribuidor; lo que ya salió: X se borra por API si hay crédito (si no, 402 y
                           queda el link); TikTok no tiene borrado por API → queda el link.

Dos pasos, para que nunca se borre nada sin querer: `buscar(link)` arma el PLAN (no toca nada) y
`ejecutar(plan)` lo hace. Lo que no se pudo borrar vuelve con su link para borrarlo a mano.

Prueba sin borrar nada:  venv\\Scripts\\python.exe borrar_todo.py <link de la nota>
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import unquote

import requests

from utils.config import get

REPO = "dlcchivilcoy/social_publisher"
RAW = f"https://raw.githubusercontent.com/{REPO}/main/state/"
# El borrado de medios de Instagram llegó en dic-2025: con la v19 de platforms/ no está.
GRAPH = "https://graph.facebook.com/v24.0"
PERFIL = {"x": "https://x.com/diarioyradio", "tiktok": "https://www.tiktok.com/@diarioyradio",
          "instagram": "https://www.instagram.com/diarioyradio/", "facebook": "https://www.facebook.com/"}
NOMBRE_RED = {"x": "X", "tiktok": "TikTok"}

# Qué hace cada ítem del plan.
BORRAR, CANCELAR, A_MANO = "borrar", "cancelar", "a mano"


def norm_slug(link_o_slug: str) -> str:
    """El slug comparable de una nota, venga como link largo, /n/slug o slug (con ñ o con %C3%B1)."""
    s = (link_o_slug or "").strip().split("#", 1)[0].split("?", 1)[0].rstrip("/")
    return unquote(s.rsplit("/", 1)[-1]).lower()


def _bajar_estado(nombre: str, defecto):
    """Un archivo de state/ tal como está en la nube (el repo local puede estar atrasado)."""
    try:
        r = requests.get(RAW + nombre, timeout=40)
        if r.ok:
            return json.loads(r.content.decode("utf-8-sig"))
    except (requests.RequestException, ValueError):
        pass
    return defecto


# ───────────────────────────────────────────────────────────────────────────── 1. el plan
def buscar(link: str, nota: dict | None = None, ledger: list | None = None,
           dist: dict | None = None) -> dict:
    """Junta todo lo publicado de esa nota. NO borra nada."""
    from platforms import wix
    slug = norm_slug(link)
    if not slug:
        raise ValueError("Pegá el link de la nota.")
    if nota is None:
        try:
            nota = wix.cargar_nota(link)
        except Exception:  # noqa: BLE001 — ya borrada de la web: igual se buscan las redes
            nota = {}
    ledger = _bajar_estado(".videos_contabilidad.json", []) if ledger is None else ledger
    dist = _bajar_estado("distribuidor.json", {}) if dist is None else dist

    filas = [f for f in ledger if f.get("post_url") and norm_slug(f["post_url"]) == slug]
    ig_puede = ig_puede_borrar()
    items = []
    wix_id = (nota or {}).get("id") or next((f.get("draft_id") for f in filas if f.get("draft_id")), "")
    if wix_id:
        items.append({"red": "Web", "que": "la nota", "tipo": "wix", "id": wix_id, "accion": BORRAR,
                      "link": (nota or {}).get("url", "")})
    for f in filas:
        for campo, red, que, tipo in (("ig_media_id", "Instagram", "el reel", "ig"),
                                      ("ig_historia_id", "Instagram", "la historia", "ig"),
                                      ("fb_video_id", "Facebook", "el reel", "fb"),
                                      ("fb_historia_id", "Facebook", "la historia", "fb"),
                                      ("yt_video_id", "YouTube", "el Short", "yt")):
            if f.get(campo) and not any(i.get("id") == str(f[campo]) for i in items):
                accion = BORRAR
                if tipo == "ig" and not ig_puede:
                    accion, que = A_MANO, f"{que} (al token le falta el permiso para borrar)"
                items.append({"red": red, "que": que, "tipo": tipo, "id": str(f[campo]), "accion": accion,
                              "link": f.get("yt_url", "") if tipo == "yt" else ""})

    ids = {f"bot:{f['file']}" for f in filas if f.get("file")}
    ids |= {f"web:{x}" for x in {wix_id, *(f.get("draft_id") for f in filas)} if x}
    for p in (dist.get("publicaciones") or {}).values():
        if p.get("id") not in ids and norm_slug(p.get("nota_url") or "") != slug:
            continue
        red, est = p.get("plataforma", ""), p.get("estado", "")
        base = {"red": NOMBRE_RED.get(red, red), "plataforma": red, "clave": p.get("clave", "")}
        if est in ("pendiente", "enviando"):
            items.append(dict(base, que="en la cola (todavía no salió)", tipo="cola", accion=CANCELAR))
        elif est == "programado" and p.get("buffer_id"):
            items.append(dict(base, que=f"programado en Buffer ({_hora(p.get('dueAt'))})", tipo="buffer",
                              id=p["buffer_id"], accion=CANCELAR))
        elif est == "enviado":
            items.append(dict(base, tipo="x" if red == "x" else "tiktok",
                              que="ya publicado (si la API de X no tiene crédito, queda el link)" if red == "x"
                              else "ya publicado",
                              id=p.get("buffer_id", ""), link=p.get("link", ""),
                              accion=BORRAR if red == "x" else A_MANO))
    return {"link": link, "slug": slug, "titulo": (nota or {}).get("title") or next(
                (f.get("titulo") for f in filas if f.get("titulo")), ""),
            "ids": sorted(ids), "items": items, "filas": len(filas)}


def ig_puede_borrar() -> bool:
    """¿El token de Instagram trae `instagram_manage_contents`? Sin ese permiso Meta no deja borrar."""
    tok = get("INSTAGRAM_ACCESS_TOKEN")
    try:
        r = requests.get(f"{GRAPH}/debug_token", params={"input_token": tok, "access_token": tok}, timeout=30)
        return "instagram_manage_contents" in (((r.json() or {}).get("data") or {}).get("scopes") or [])
    except (requests.RequestException, ValueError):
        return False


def _hora(iso: str) -> str:
    try:
        d = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
        from zoneinfo import ZoneInfo
        return d.astimezone(ZoneInfo("America/Argentina/Buenos_Aires")).strftime("%d/%m %H:%M")
    except (ValueError, ImportError):
        return iso or "?"


def texto_plan(plan: dict) -> str:
    """El plan en castellano, para el cartel de confirmación."""
    grupos = {BORRAR: [], CANCELAR: [], A_MANO: []}
    for i in plan["items"]:
        grupos[i["accion"]].append(f"   • {i['red']}: {i['que']}")
    partes = [f"«{plan['titulo'] or plan['slug']}»", ""]
    if grupos[BORRAR]:
        partes += ["Se BORRA:"] + grupos[BORRAR] + [""]
    if grupos[CANCELAR]:
        partes += ["Se CANCELA antes de que salga:"] + grupos[CANCELAR] + [""]
    if grupos[A_MANO]:
        partes += ["No se puede borrar por API (te dejo el link para hacerlo a mano):"] + grupos[A_MANO] + [""]
    if not plan["items"]:
        partes.append("No encontré nada publicado de esta nota.")
    return "\n".join(partes).rstrip()


# ───────────────────────────────────────────────────────────────────────────── 2. ejecutar
def ejecutar(plan: dict, log=print) -> list:
    """Borra/cancela cada ítem. Devuelve [{red, que, ok, detalle, link}] (ok False = a mano)."""
    salida = []
    for i in plan["items"]:
        try:
            ok, detalle, link = _HACER[i["tipo"]](i)
        except Exception as e:  # noqa: BLE001 — una red que falla no frena a las otras
            ok, detalle, link = False, f"{type(e).__name__}: {str(e)[:160]}", i.get("link", "")
        salida.append({"red": i["red"], "que": i["que"], "ok": ok, "detalle": detalle, "link": link})
        log(f"{'✅' if ok else '✋'} {i['red']} {i['que']}: {detalle}")
    if plan.get("ids"):
        ok, detalle = avisar_distribuidor(plan)
        salida.append({"red": "Distribuidor", "que": "X y TikTok de acá en adelante", "ok": ok,
                       "detalle": detalle, "link": ""})
        log(f"{'✅' if ok else '✋'} distribuidor: {detalle}")
    return salida


def _wix(i):
    from platforms import wix
    wix.borrar_post(i["id"])
    return True, "borrada de la web", ""


def _error_graph(r) -> str:
    try:
        e = (r.json() or {}).get("error") or {}
        return e.get("error_user_msg") or e.get("message") or r.text[:160]
    except ValueError:
        return r.text[:160]


def _ig(i):
    tok = get("INSTAGRAM_ACCESS_TOKEN")
    r = requests.delete(f"{GRAPH}/{i['id']}", params={"access_token": tok}, timeout=40)
    if r.ok and (r.json() or {}).get("success", True):
        return True, "borrado", ""
    msg = _error_graph(r)
    p = requests.get(f"{GRAPH}/{i['id']}", params={"fields": "permalink", "access_token": tok}, timeout=30)
    link = (p.json() or {}).get("permalink", "") if p.ok else ""
    if not p.ok and i["que"] == "la historia":
        return True, "ya no está (las historias duran 24 h)", ""
    if "permission" in msg.lower() or "permiso" in msg.lower() or r.status_code == 403:
        msg = "al token de Instagram le falta el permiso instagram_manage_contents"
    return False, msg, link or PERFIL["instagram"]


def _fb(i):
    tok = get("FACEBOOK_PAGE_ACCESS_TOKEN")
    r = requests.delete(f"{GRAPH}/{i['id']}", params={"access_token": tok}, timeout=40)
    if r.ok:
        return True, "borrado", ""
    msg = _error_graph(r)
    if "does not exist" in msg.lower() or "no existe" in msg.lower():
        return True, "ya no estaba", ""
    return False, msg, f"https://www.facebook.com/{i['id']}"


def _yt(i):
    from googleapiclient.errors import HttpError
    from platforms import youtube_api as Y
    ultimo = ""
    # Los Shorts del bot van al canal Diario La Campaña; por las dudas se prueba también el de la radio.
    for creds in (Y._shorts_credentials, Y._credentials):
        try:
            Y._service(creds()).videos().delete(id=i["id"]).execute()
            return True, "borrado", ""
        except HttpError as e:
            if e.resp.status == 404:
                ultimo = "no está en este canal"
                continue
            ultimo = f"YouTube {e.resp.status}"
        except Exception as e:  # noqa: BLE001 — falta un token en esta PC
            ultimo = str(e)[:120]
    return False, ultimo, i.get("link") or f"https://studio.youtube.com/video/{i['id']}/edit"


def _post_buffer(pid: str) -> dict:
    from distribuidor import buffer_api
    d = buffer_api.gql("query($i: PostId!){ post(input:{id:$i}){ id status externalLink } }", {"i": pid})
    return d.get("post") or {}


def _buffer(i):
    from distribuidor import buffer_api
    b = _post_buffer(i["id"])
    if not b:
        return True, "ya no estaba en Buffer", ""
    if b.get("status") in ("sent", "sending"):
        # Salió entre que se armó el plan y ahora: se trata como publicado.
        sigue = dict(i, link=b.get("externalLink") or "")
        return (_x(sigue) if i.get("plataforma") == "x" else _tiktok(sigue))
    res = buffer_api.crear_lote([], [i["id"]])
    if i["id"] in res.get("_borrados", []):
        return True, "cancelado en Buffer: no va a salir", ""
    return False, "Buffer no lo dejó cancelar", "https://publish.buffer.com/"


def _x(i):
    link = i.get("link") or ""
    if not link and i.get("id"):
        link = _post_buffer(i["id"]).get("externalLink") or ""
    m = re.search(r"/status/(\d+)", link)
    if not m:
        return False, "no tengo el link del posteo", PERFIL["x"]
    from requests_oauthlib import OAuth1
    auth = OAuth1(get("TWITTER_API_KEY"), get("TWITTER_API_SECRET"),
                  get("TWITTER_ACCESS_TOKEN"), get("TWITTER_ACCESS_TOKEN_SECRET"))
    r = requests.delete(f"https://api.x.com/2/tweets/{m.group(1)}", auth=auth, timeout=40)
    if r.ok and ((r.json() or {}).get("data") or {}).get("deleted"):
        return True, "borrado", ""
    if r.status_code == 404:
        return True, "ya no estaba", ""
    if r.status_code == 402:
        return False, "la API de X no tiene crédito para borrar", link
    return False, f"X {r.status_code}", link


def _tiktok(i):
    link = i.get("link") or (_post_buffer(i["id"]).get("externalLink") if i.get("id") else "") or ""
    return False, "TikTok no deja borrar por API", link or PERFIL["tiktok"]


def _cola(i):
    return True, "sale de la cola (lo hace el distribuidor en su próxima pasada)", ""


_HACER = {"wix": _wix, "ig": _ig, "fb": _fb, "yt": _yt, "buffer": _buffer, "x": _x, "tiktok": _tiktok,
          "cola": _cola}


# ─────────────────────────────────────────────────────── 3. que el distribuidor no la mande más
def _token_github() -> str:
    tok = (get("GITHUB_TOKEN") or get("GH_TOKEN") or "").strip()
    if tok:
        return tok
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=20)
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def evento_baja(plan: dict) -> dict:
    return {"id": f"baja:{plan['slug']}", "origen": "baja", "baja": True, "ids": plan["ids"],
            "slug": plan["slug"], "titulo": plan.get("titulo", ""),
            "generado": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")}


def avisar_distribuidor(plan: dict) -> tuple:
    """Deja una «baja» en state/buzon/ del repo (API de GitHub): en su próxima pasada el distribuidor
    saca de la cola y de Buffer todo lo de esa nota, y no la vuelve a aceptar."""
    tok = _token_github()
    if not tok:
        return False, "no pude avisarle (falta GITHUB_TOKEN o `gh auth login` en esta PC)"
    ruta = f"state/buzon/baja_{hashlib.sha1(plan['slug'].encode()).hexdigest()[:16]}.json"
    url = f"https://api.github.com/repos/{REPO}/contents/{ruta}"
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
    previo = requests.get(url, headers=h, timeout=30)
    cuerpo = {"message": f"Editor de notas: baja de {plan['slug'][:60]} [skip ci]",
              "content": base64.b64encode(json.dumps(evento_baja(plan), ensure_ascii=False,
                                                     indent=1).encode()).decode()}
    if previo.ok:
        cuerpo["sha"] = previo.json().get("sha")
    r = requests.put(url, headers=h, json=cuerpo, timeout=40)
    if r.ok:
        return True, "avisado: no va a mandar nada más de esta nota a X ni a TikTok"
    return False, f"no pude avisarle (GitHub {r.status_code})"


if __name__ == "__main__":
    # Solo muestra el plan: NO borra nada.
    if len(sys.argv) < 2:
        print("Uso: python borrar_todo.py <link de la nota>")
        sys.exit(1)
    from utils.config import load_config
    load_config()
    p = buscar(sys.argv[1])
    print(texto_plan(p))
    print(json.dumps(p, ensure_ascii=False, indent=1))
