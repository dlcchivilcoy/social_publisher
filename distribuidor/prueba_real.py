r"""Etapa 5: arma en Buffer, como BORRADORES, una publicación real de cada tipo y red.

Nada se publica: quedan en publish.buffer.com → Borradores para revisarlas. Usa los mismos
textos y el mismo cliente que el distribuidor. Contenido real del 11/10/2026.

    venv\Scripts\python.exe -m distribuidor.prueba_real
"""
from __future__ import annotations

import json

from utils.config import load_config

load_config()

from distribuidor import buffer_api, fuentes, textos  # noqa: E402

SENTRA_NOTA = "https://www.diariolacampaña.com.ar/n/chivilcoy-politica-la-empresa-sentra-respondio-a-las-acusaciones-del"
BOT = {"id": "prueba:bot-sentra", "origen": "bot", "tipo": "video",
       "titulo": "La empresa Sentra respondió a las acusaciones del concejal Echeverría",
       "descripcion": ("Con un comunicado enviado a medios periodísticos, la empresa SENTRA respondió "
                       "a las acusaciones del concejal Martín Echeverría y aclaró la situación a la comunidad."),
       "video_url": "https://github.com/dlcchivilcoy/social_publisher/releases/download/reel-latest/dist_prueba_sentra.mp4",
       "nota_url": SENTRA_NOTA, "youtube_url": "https://www.youtube.com/shorts/UsNvXM4SAD8",
       "localidad": "Chivilcoy"}
POL = {"id": "prueba:pol-camioneta", "origen": "policiales", "tipo": "video",
       "titulo": "Camioneta despistó en la Ruta Provincial 51 y terminó en los pastizales",
       "descripcion": ("Una camioneta protagonizó un despiste sobre la Ruta Provincial 51, entre General "
                       "Alvear y Saladillo. No hubo heridos de gravedad."),
       "video_url": ("https://github.com/dlcchivilcoy/policiales-bsas/releases/download/reels-policiales/"
                     "2026-10-11_2109_01_las-flores_camioneta-despist-en-la-ruta-p.mp4"),
       "nota_url": "https://www.diariolacampaña.com.ar/n/saladillo-una-camioneta-despisto-en-la-ruta-51-y-termino-entre-5c1649",
       "localidad": "Saladillo"}
WEB = {"id": "prueba:web-sentra", "origen": "web", "tipo": "nota",
       "titulo": "La empresa Sentra respondió a las acusaciones del concejal Echeverría",
       "descripcion": "La empresa difundió un comunicado en respuesta a los dichos del concejal Martín Echeverría.",
       "nota_url": SENTRA_NOTA}


def main() -> None:
    from platforms import wix
    WEB["imagen_url"] = (wix.cargar_nota(SENTRA_NOTA) or {}).get("cover_url", "")
    pubs = []
    for ev, red in ((BOT, "x"), (BOT, "tiktok"), (POL, "x"), (POL, "tiktok"), (WEB, "x")):
        if ev["tipo"] == "video":
            ok, motivo, info = fuentes.video_verificado(ev)
            print(f"video {ev['id']}: {'OK' if ok else motivo} {info}")
        else:
            ok, motivo = fuentes.nota_verificada(ev["nota_url"])
            print(f"nota {ev['id']}: {'OK' if ok else motivo}")
        texto = textos.texto_x(ev) if red == "x" else textos.texto_tiktok(ev)
        print(f"\n--- {red.upper()} · {ev['origen']} ({textos.largo_x(texto) if red == 'x' else len(texto)} c.)\n{texto}\n")
        pubs.append({"clave": f"{ev['id']}|{red}", "plataforma": red, "texto": texto, "borrador": True,
                     "video_url": ev.get("video_url", ""), "dueAt": "", "tipo": ev["tipo"],
                     "imagen_url": ev.get("imagen_url", "")})
    res = buffer_api.crear_lote(pubs)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    print(buffer_api.resumen_cuota())


if __name__ == "__main__":
    main()
