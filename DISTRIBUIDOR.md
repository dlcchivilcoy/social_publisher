# Distribuidor central a X y TikTok (vía Buffer)

Agrega X y TikTok de **@diarioyradio** a los cuatro sistemas que ya publican, sin tocar
cómo publican en Instagram, Facebook, YouTube ni la web. Ninguno de los sistemas publica en
X o TikTok directamente: dejan su contenido en un **buzón**, y el distribuidor decide qué
sale, en qué red y a qué hora, respetando los límites. La publicación la hace **Buffer**
(plan Free), que tiene la autorización oficial de TikTok para publicar directo.

```
 Bot (social_publisher) ─┐  state/buzon/*.json (mismo repo)
 Entrevistas (PC radio) ─┤  state/buzon/*.json (por la API de GitHub) + MP4 en Release buzon-distribuidor
 Policiales (Actions)  ──┤  estado/buzon_distribuidor.json del repo policiales-bsas + MP4 en su Release
 Notas de la web  ───────┘  API del blog de Wix (lo publicado en 24 h)
                 │
                 ▼
   distribuidor (GitHub Actions, cada 20 min, workflow distribuidor.yml)
   cola propia: state/distribuidor.json · panel: state/distribuidor_panel.md
                 │  API GraphQL de Buffer (hasta 10 publicaciones por solicitud)
                 ▼
          Buffer → X @diarioyradio · TikTok @diarioyradio
```

## Matriz (máximos por día, hora AR — nunca se rellena)

| Origen | X | TikTok |
|---|---:|---:|
| Recortes de entrevistas | 10 | 0 |
| Reels del bot | 10 | 10 |
| Reels policiales | 12 | 12 |
| Notas web | 15 | 0 |
| **Topes totales en 24 h** | **47** | **15** |

Cada cupo se cambia con una variable del `.env` (secret `ENV_FILE`), sin tocar código:
`CUPO_<ORIGEN>_<RED>` (p. ej. `CUPO_POLICIALES_TIKTOK=8`), `TOPE_X_24H`, `TOPE_TIKTOK_24H`.
Todo lo configurable está en `distribuidor/config.py`.

## Límites reales (verificados el 11/10/2026)

- **X**: Buffer admite 50/24 h a una cuenta sin verificar (`subscriptionType: None`). Tope propio 47.
- **TikTok**: Buffer dice 25/24 h, pero la documentación de TikTok dice que la publicación
  directa tiene un tope «típicamente ~15 por día por cuenta», compartido por todas las apps, y
  no se puede consultar antes de chocarlo. **Tope propio 15** → los 22 de la matriz no entran
  siempre: el bot (contenido propio) va primero, después policiales por potencial viral. Si
  TikTok contesta `spam_risk_too_many_posts`, el error queda en el panel y se baja el tope.
- **Buffer Free**: 10 programadas **por canal** (probado: con 10 en X el 11.º dio
  `LimitReachedError` y el de TikTok entró). El distribuidor usa 8 y deja 2 para lo manual.
- **API de Buffer**: 100 / 15 min, 250 / 24 h, 3.000 / 30 días. Varias publicaciones en una
  sola solicitud cuentan como una (probado con 10). Un día simulado con 35 contenidos usó
  20 solicitudes; el máximo teórico (69 publicaciones) queda en ~50-60 por día.

## Reglas

- **Idempotencia**: una fila por `id|red`. Reenviar el mismo contenido no hace nada. Antes de
  llamar a Buffer se guarda «enviando»: si la corrida muere, la siguiente busca la publicación
  en Buffer por canal + horario exacto (cada una tiene su segundo propio) y no la reenvía.
- **Prioridad**: urgente primero (toma el primer hueco libre y, si Buffer está lleno, corre a
  la cola la NO urgente más lejana); después contenido propio > entrevistas > web > policiales,
  más la prioridad de cada pieza, menos la antigüedad.
- **Separación**: X 15 min, TikTok 40 min. Reel y nota de la misma noticia en X: 3 h y textos
  distintos. Franja: X 07:00–24:00, TikTok 08:00–23:30.
- **Etapa 6 (17/10/2026, PDF «Prompt integral»)**:
  - Origen nuevo **«nacionales»**: los videos de nacionales y de sus categorías (deportes,
    espectáculos, cine y series, tecnología) llegan por el buzón de policiales-bsas. **Solo TikTok**
    (a X ya van como notas de la web), cupo 4 dentro del MISMO tope de TikTok: reemplaza parte de lo
    policial y le da al intercalado otra temática. Hashtag de su sección (`#deportes`…).
  - **Radio de 100 km**: un evento policial con `radio_ok: false` o `distancia_km > 100` se rechaza
    al entrar (ni X ni TikTok), aunque policiales ya los filtra.
  - **La red pide esperar**: cuando TikTok contesta «too many posts via OpenAPI in the last 24
    hours» (pasó 6 veces del 12 al 16/10, con 13 y 15 publicaciones en las 24 h previas), el canal
    queda **en pausa 24 h** (`est["pausas"]`, mail de aviso) y su tope pasa a **lo que aguantó menos
    una** durante 7 días. Con los cortes históricos quedó en 12. Se ve en el panel.
- **Métricas (etapa 7, 17/10/2026)**: una vez por día se leen las métricas que Buffer tenga de lo
  enviado en 3 días (1 solicitud, `buffer_api.metricas_enviados`) y se pegan a cada publicación; el
  panel muestra la mediana por red y origen. Si el plan no las da, queda «no disponible» y no se
  insiste hasta el día siguiente. El panel editorial completo vive en policiales-bsas
  (`estado/panel_editorial.md`).
- **Intercalado (17/10/2026, PDF «Prompt integral»)**: en cada canal nunca dos policiales
  seguidos; entre uno y otro, al menos `MIN_NON_POLICE_POSTS_BETWEEN_POLICE` (1) de otra temática.
  Policial = origen «policiales» o un título con un hecho policial (`distribuidor/tema.py`: el bot
  también publica choques y robos). Cuenta lo nuestro programado o enviado y lo AJENO que Buffer
  muestra al sincronizar (`est["ajenos"]`, no se toca). La cola se ordena alternando (otra temática,
  policial, otra…); lo urgente tampoco se pega. Sin otra temática, el policial espera en la cola con
  el motivo «intercalado» (vence como siempre a las 18 h). Al desprogramar por un urgente, nunca se
  saca la publicación que separa dos policiales. `INTERCALADO=0` lo apaga.
- **Vencimiento**: policiales 18 h (sus videos se borran a las 24 h), bot y entrevistas 48 h,
  notas 24 h. Lo vencido queda informado en el panel, nunca se pierde en silencio.
- **Entrevistas**: el pipeline sube todo en PRIVADO hasta que el editor aprueba. El recorte
  sale en X **recién cuando su Short de YouTube es público** (si el largo también lo es, el
  link va a la entrevista completa). Si en 7 días no se aprueba, no sale.
- **Notas web**: solo X, con la foto destacada y el link `/n/slug` (se verifica que abra en
  nuestro sitio). Sin «Región» (esas son policiales y ya van como reel). Sin hashtags.
- **Textos**: se reutiliza lo que generó cada sistema; no se inventa nada. X ≤ 280 con el peso
  real de X (link 23, emoji 2). TikTok: título + bajada, sin links, `#chivilcoy
  #diariolacampaña #radiodelcentro` + hasta 2 del tema (máx. 5).
- **Videos**: se verifica que el MP4 se baje sin login; X no admite más de 140 s sin suscripción.

## Interruptores (todos en el `.env` / secret `ENV_FILE`)

| Variable | Qué hace | Default |
|---|---|---|
| `DISTRIBUIDOR_ACTIVO` | general: apagado junta la cola pero no toca Buffer | `0` |
| `DISTRIBUIDOR_X`, `DISTRIBUIDOR_TIKTOK` | cada red | `1` |
| `DISTRIBUIDOR_ORIGEN_<ORIGEN>` | cada origen (ENTREVISTAS, BOT, POLICIALES, WEB) | `1` |
| `TIKTOK_VIA_BUFFER` | el bot deja de mandar borradores propios a TikTok (evita el duplicado) | `0` |
| `DISTRIBUIDOR_BOT` | el bot deja el reel en el buzón | `1` |
| `WEB_X_CON_FOTO` | la nota en X lleva la foto | `1` |

En policiales: variable `DISTRIBUIDOR_POLICIALES=0`. En el pipeline: `distribuidor.enabled: false`.

**Revertir todo**: `DISTRIBUIDOR_ACTIVO=0` (y `TIKTOK_VIA_BUFFER=0`). La generación de
contenido y las demás redes no dependen del distribuidor en nada.

## Operación

- Panel: [`state/distribuidor_panel.md`](state/distribuidor_panel.md) (hoy por origen y red,
  cola, errores, cuota, canales) y el resumen de cada corrida en Actions.
- Avisos por mail (máx. uno cada 12 h por tipo): canal desconectado, clave rechazada, cuota baja.
- A mano: `venv\Scripts\python.exe -m distribuidor.run --simular` (no toca Buffer ni guarda).
- Pruebas: `venv\Scripts\python.exe -m distribuidor.test_distribuidor` (Buffer falso).
- Disparo: job de cron-job.org cada 20 min → `workflow_dispatch` de `distribuidor.yml`.

## Recuperación

- **Canal desconectado** (mail): publish.buffer.com → Settings → Channels → Reconnect. Si
  Buffer le da un id nuevo: `BUFFER_CANAL_X` / `BUFFER_CANAL_TIKTOK` en el `.env`.
- **Clave rechazada**: crear otra en publish.buffer.com/settings/api y doble clic en
  `Cargar clave Buffer.bat` (la prueba, la guarda en `.env`, `ENV_FILE` y policiales-bsas).
- **`state/distribuidor.json` dañado**: el distribuidor se frena a propósito (no republica
  nada). Restaurar la versión anterior desde el historial de git.
- **Algo salió dos veces**: no debería (ver Idempotencia); borrarlo en Buffer y avisar.

## Costos

Buffer Free: USD 0 (2 de 3 canales). GitHub Actions: repo público, minutos gratis. Sin
servicios nuevos: los videos usan los Releases de GitHub que ya se usaban para Instagram
(Buffer los lee aunque redirijan; probado). Si algún día hace falta más (más de 10 programadas
por canal o más cuota), el plan Essentials de Buffer sube la cuota a 7.500/mes.

## TikTok en tandas (19/10/2026)

Pedido del editor: equilibrar TikTok y subir lo más viral. TikTok ya no gotea cada 40 min: se programa solo en
**tandas a las 10, 18 y 21** (`TIKTOK_TANDAS`, hora AR). En la primera corrida del distribuidor dentro de cada
tanda (ventana de 50 min) entra el tope de 24 h dividido por la cantidad de tandas (15 / 3 = **5**):

- **Las más virales** de lo pendiente (la «prioridad» del buzón = potencial viral de la pieza; a igual
  viralidad, la más nueva; lo urgente primero).
- **Equilibrio:** no más de `TIKTOK_TANDA_MAX_ORIGEN` (2) del mismo origen (policiales, nacionales, bot…) si
  hay de otros.
- Una cada `TIKTOK_TANDA_SEPARACION_MIN` (20) minutos, nunca dos policiales seguidos.
- Lo que no entra sigue en la cola para la tanda siguiente (o vence: policiales y nacionales a las 18 h).

`TIKTOK_TANDAS=0` vuelve al goteo de antes (cupos por origen y 40 min entre posteos). X no cambia.

## X sincronizado y sin repetidos (19/10/2026)

Pedido del editor: una estructura para X sin posteos repetidos, con todo lo que sale (notas web, recortes de la
radio, bot, policiales, nacionales/generales), sincronizada con las otras redes. `distribuidor/x_sincro.py`:

1. **Una noticia, un posteo.** Cada publicación de X lleva su «noticia» (slug de la nota o, sin nota, la huella
   del título). Si ya hay en X algo de la misma noticia en 48 h (mismo slug o título parecido ≥ 0,6), lo nuevo no
   entra. Un **reel reemplaza a la nota** de la misma noticia que todavía no llegó a Buffer (en cada corrida se
   encolan primero los videos).
2. **Al mismo tiempo que las otras redes.** Cada pieza se programa a la hora en que salió en IG/FB/YouTube (o lo
   antes posible), con `X_SEPARACION_SINCRO_MIN` (5) entre posteos de X; las notas web cada `X_WEB_RITMO_MIN` (15),
   el ritmo del carrusel del diario. Nada se programa a más de `X_HORIZONTE_SINCRO_MIN` (90).
3. **No se publica tarde:** un reel vence a las `X_VENTANA_REEL_H` (2) y una nota a las `X_VENTANA_NOTA_H` (4).
4. Cupos de X por día: web 15, nacionales 12, bot 10, recortes 8, policiales 6; tope total 47 en 24 h.

Para que llegue a tiempo, policiales y nacionales suben su buzón al repo **apenas sale cada pieza**
(policiales-bsas, `reels/distribuidor.subir_al_repo`). `X_SINCRO=0` vuelve al goteo de antes.
