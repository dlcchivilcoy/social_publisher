# Cómo usar el Publicador Automático

## En pocas palabras
Todos los días a las **8:00 AM**, el sistema revisa la carpeta y publica las notas
nuevas en **Wix, Facebook, Instagram y X**. Vos solo tenés que dejar la edición
en la carpeta antes de esa hora.

---

## El día a día (lo único que tenés que hacer)

1. Copiá la **carpeta de la edición** (ej. `diario para 29 de mayo`) dentro de:
   ```
   C:\Users\Diario\Desktop\NOTAS AUTOMATICAS
   ```

2. Listo. A las 8:00 AM se publica solo.

---

## Cómo tiene que estar armada la edición

```
NOTAS AUTOMATICAS\
└── diario para 29 de mayo\          ← carpeta de la edición
    ├── la pagina 2\
    │   ├── Florencia Salinardi.docx  ← la nota (Word)
    │   └── salinardi.png             ← su foto
    ├── la pagina 3\
    │   ├── camion1.docx
    │   ├── camion.png
    │   ├── hurto esclarecido.docx
    │   └── hurto.jpg
    └── ...
```

### Reglas
- **Solo se publican las páginas:** 2, 3, 5, 7, 8, 9. El resto se ignora.
- Cada nota es un archivo **`.docx`** (Word) con su **foto** al lado.
- La foto y la nota se emparejan por **nombre parecido** (no hace falta que sean
  idénticos: "Florencia Salinardi.docx" encuentra "salinardi.png").
- Dentro del Word, el texto debe tener este orden:
  - **Línea 1:** volanta o categoría (ej: `RUTA 5`)
  - **Línea 2:** el titular (ej: `Triple choque de camiones en el km 138`)
  - **Línea 3 en adelante:** el cuerpo de la nota
- Una nota ya publicada **no se vuelve a publicar** (queda registrada).

---

## Comandos útiles (PowerShell)

Abrí PowerShell y entrá a la carpeta del programa:
```powershell
cd C:\Users\Diario\social_publisher
```

### Ver qué se publicaría, SIN publicar (recomendado antes de cada edición)
```powershell
.\venv\Scripts\python.exe main.py --dry-run
```
Te muestra cada nota, su título y con qué foto quedó emparejada.

### Publicar AHORA mismo (sin esperar a las 8)
```powershell
.\venv\Scripts\python.exe main.py --run-now
```
⚠️ Esto publica de verdad y en público en las 4 redes.

### Revisar una edición puntual (otra carpeta)
```powershell
.\venv\Scripts\python.exe main.py --dry-run --folder "C:\ruta\a\la\edicion"
```

---

## ¿Cómo sé si funcionó?
Revisá el archivo de registro:
```
C:\Users\Diario\social_publisher\logs\publisher.log
```
Ahí figura, por cada nota, si salió OK o falló en cada red.

---

## Cambiar la configuración

Editá el archivo `.env` (con el Bloc de notas) para cambiar:
- `ALLOWED_PAGES=2,3,5,7,8,9` → qué páginas publicar
- `SCHEDULE_HOUR=8` → la hora (formato 24hs)
- `POSTS_FOLDER=...` → la carpeta que se vigila

> Si cambiás la hora acá, también hay que actualizar la tarea programada de Windows.
> Avisame y lo hago.

---

## La tarea automática de Windows
- Nombre: **"Publicador Diario LC"**
- Corre todos los días a las **08:00**.
- Para verla: abrí el **Programador de tareas** de Windows y buscala por ese nombre.
- Importante: la PC tiene que estar **encendida** a esa hora (con tu usuario iniciado).

---

## Si algo falla
- Las notas que fallan **no se pierden**: quedan sin marcar y se reintentan en la
  próxima corrida.
- Los tokens de Facebook/Instagram son permanentes, pero si alguna vez Meta los
  revoca, hay que regenerarlos. El registro (`publisher.log`) lo va a avisar con
  un error de "Credential error".
