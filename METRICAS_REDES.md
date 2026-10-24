# Informe semanal de métricas — @diarioyradio (Instagram y Facebook)

Llega por mail **los miércoles a las 8:00**. Cubre la semana de lunes a domingo anterior y la compara con la previa.
**No usa IA ni gasta tokens:** son cuentas con reglas fijas en Python, corriendo en GitHub Actions
(`.github/workflows/metricas.yml`). Código en `metricas_redes/`.

## Cómo funciona

| Cuándo | Qué hace |
|---|---|
| Todos los días 00:15 | **Foto:** seguidores de cada cuenta; números del día de la cuenta (si hay permiso); cada posteo con 48 h o más que todavía no se midió; las historias vivas. |
| Miércoles 08:00 | Foto + **informe** por mail (HTML) con la planilla `posteos_AAAA-MM-DD.csv` adjunta. |

Todo queda en `state/metricas_redes.json` (un posteo por línea; se guardan 120 días).

**Misma edad para todos:** cada posteo se mide una sola vez, la primera pasada en que tiene 48 h. Así el
informe compara posteos de la misma edad. Por eso el informe sale el miércoles: el domingo a la noche ya cumplió 48 h.

## Qué etiqueta lleva cada posteo

- **Corrida (origen):** por el id, desde los registros de policiales-bsas (Policiales, Nacionales, carrusel
  de las 22, Pregunta del día, Archivo). Lo demás, por el texto: Notas del diario (carrusel de la mañana, «📰»),
  Videos propios (entrevistas y corresponsales: título corto arriba), Notas locales en reel (redacción),
  Agro colab («EL CAMPO HOY»), Servicios (farmacias, sepelios, tapa), Videos de YouTube en Facebook, Otros.
- **Categoría:** la del registro cuando la hay; si no, palabras clave del texto (`etiquetas._CLAVES`).
- **Formato:** reel, carrusel, foto, video, link o historia, según Meta.
- **Hora y día** en hora de Argentina.

## Qué dice el informe y cómo lo calcula

1. **La cuenta:** seguidores, ganados netos, nuevos, bajas, espectadores, visualizaciones, interacciones y visitas al perfil, contra la semana anterior.
2. **Qué subió, qué bajó y por qué:** cada total se parte en
   - *volumen* (se publicó más o menos) y *rendimiento* (cada posteo rindió más o menos), y
   - cuánto sumó o restó cada corrida, con su cantidad de posteos y su mediana.
3. **Seguidores: por qué llegan y por qué se van.**
   - Llegan: los posteos que más seguidores trajeron y qué tipo trae más por posteo.
   - Se van: Meta **no** dice quién se fue ni por qué posteo. El informe cruza los días con más bajas con lo
     que se publicó esos días, y estima con 5 semanas cuántas bajas se asocian a cada posteo de cada corrida
     (una regresión). Es una pista, no una prueba. Las señales directas también entran: «ocultar» y
     «dejar de seguir» desde un posteo en Facebook, y quién se va de una historia en Instagram.
4. **Qué tipo de posteo genera más:** para cada métrica (me gusta, comentarios, reposts, compartidas,
   visualizaciones, nuevos seguidores, espectadores, guardados), el tipo (categoría · formato) y la corrida
   que más y menos genera. Va también lo negativo. Usa las últimas 4 semanas y solo tipos con 3 posteos o más.
5. **¿El formato es el correcto?:** por corrida y por categoría, el rendimiento de cada formato y el veredicto.
   Recomienda cambiar solo con 15 % de diferencia o más.
6. **Mejor horario por corrida:** a qué horas sale ahora, qué hora rinde mejor y qué formato. En Instagram
   suma las horas en que más seguidores están conectados.
7. **Los 5 mejores y los 5 peores posteos** de la semana, y las historias.

**Rendimiento** = cada número del posteo dividido por la mediana de su red en el último mes, con pesos:
vistas 1, espectadores 1, me gusta 1, comentarios 2, guardados 2, compartidas 3, reposts 3, nuevos seguidores 4.
1,0 = un posteo típico. Pesos y umbrales en `analisis.py` (`PESOS`, `MIN_N`, `VENTAJA`).

## Permisos (completos desde el 24/10/2026)

El token de la página del diario (app «diario», tipo Page, no vence) tiene desde el 24/10/2026 los 9 permisos:
los 6 de publicar + `instagram_manage_insights`, `read_insights` y `pages_read_user_content`. Está en
`FACEBOOK_PAGE_ACCESS_TOKEN` e `INSTAGRAM_ACCESS_TOKEN` de los `.env` de social_publisher y policiales-bsas
y en sus secrets. Si algún día se renueva sin esos tres, el informe sigue andando con lo básico y lo avisa
en «Lo que falta medir».

**Lo que Meta da y no da (probado el 24/10/2026):**
- Instagram, posteos: vistas, espectadores, me gusta, comentarios, compartidas, guardados, reposts; reels
  también tiempo medio de reproducción. **Seguidores y visitas al perfil por posteo: solo en fotos y
  carruseles, NO en reels** (Meta rechaza `follows` y `profile_visits` para REELS).
- Instagram, historias: vistas, espectadores, respuestas, compartidas, seguidores, visitas al perfil, salidas.
- Instagram, cuenta por día: espectadores, vistas, interacciones, nuevos y **bajas**; horas de conexión.
- Facebook, posteos: vistas, clics, me gusta, comentarios, compartidas. Reels: vistas, seguidores que trajo
  y tiempo medio. **Ya no existen** el alcance por posteo (`post_impressions_unique`) ni «ocultar / dejar de
  seguir» por posteo (`post_negative_feedback_*`): Meta los retiró.
- Facebook, página por día: nuevos, **bajas**, vistas, espectadores, interacciones, visitas.

La primera foto con permisos completa 5 semanas: vuelve a medir con estadísticas los posteos que ya estaban
medidos sin ellas (marca `con_insights`) y lee las fotos de Facebook desde 35 días atrás (`completo` en el estado).

## Comandos

```
venv\Scripts\python.exe -m metricas_redes.run --foto                       # la foto (guarda)
venv\Scripts\python.exe -m metricas_redes.run --informe --sin-foto --simular  # el informe en logs\, sin mandar
venv\Scripts\python.exe -m metricas_redes.test_metricas                    # pruebas con un Meta falso
```

A mano en la nube: Actions → «Métricas de redes» → Run workflow (`--informe` lo manda por mail).
El destino del mail es `METRICAS_EMAIL` (si no está, `VIDEOS_NOTIFY_EMAIL` y si no, `MAIL_FROM`).

## Gotchas

- **Los links de paginación de Facebook traen el token adentro:** nunca loguear una respuesta cruda.
  Se pagina con el cursor `after`.
- **Las métricas de Meta cambian solas.** Si Meta rechaza una, se prueban de a una, la rechazada se anota en
  `invalidas` del estado y se vuelve a probar a los 14 días.
- **«Please reduce the amount of data»** al listar reels de Facebook: se pide la misma página más chica.
- **La primera foto mira 5 semanas para atrás**, para que el primer informe ya tenga con qué comparar.
  Esos posteos viejos se midieron con más de 48 h (figura en `edad_h`).
