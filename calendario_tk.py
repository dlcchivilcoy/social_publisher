"""Un calendario chiquito para elegir fechas, en tkinter puro.

No usa `tkcalendar` a propósito: esa librería habría que ponerla en
`requirements.txt`, y entonces la nube la instalaría en cada corrida para nada
(allá no hay ventanas). Con el módulo `calendar` de la biblioteca estándar
alcanza y sobra.

Uso:

    import calendario_tk
    calendario_tk.elegir(parent, var_fecha, junto_a=boton)

`var_fecha` es un `StringVar` con la fecha en dd/mm/aaaa. Si ya tiene algo, el
calendario abre en ese mes; si no, en el de hoy.
"""
from __future__ import annotations

import calendar
import tkinter as tk
from datetime import date
from tkinter import ttk

NARANJA = "#e2620c"
GRIS = "#f0f0f0"

MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio",
         "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre")
DIAS = ("L", "M", "M", "J", "V", "S", "D")      # la semana arranca el lunes


def _leer(texto: str) -> date | None:
    """dd/mm/aaaa -> date. Devuelve None si no se entiende."""
    texto = (texto or "").strip()
    if not texto:
        return None
    for sep in ("/", "-"):
        partes = texto.split(sep)
        if len(partes) == 3:
            try:
                d, m, a = (int(p) for p in partes)
            except ValueError:
                continue
            if a < 100:
                a += 2000
            try:
                return date(a, m, d)
            except ValueError:
                return None
    return None


class _Calendario(tk.Toplevel):
    def __init__(self, parent, inicial: date, junto_a=None):
        super().__init__(parent)
        self.elegida: date | None = None
        self._mes = inicial.month
        self._anio = inicial.year
        self._hoy = date.today()

        self.title("Elegí la fecha")
        self.resizable(False, False)
        self.transient(parent)

        # Encabezado: mes anterior / mes y año / mes siguiente
        cab = ttk.Frame(self)
        cab.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Button(cab, text="◀", width=3, command=self._mes_anterior).pack(side="left")
        self._titulo = ttk.Label(cab, anchor="center", font=("Segoe UI", 10, "bold"))
        self._titulo.pack(side="left", fill="x", expand=True)
        ttk.Button(cab, text="▶", width=3, command=self._mes_siguiente).pack(side="left")

        self._grilla = ttk.Frame(self)
        self._grilla.pack(padx=8, pady=(0, 4))

        pie = ttk.Frame(self)
        pie.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(pie, text="Hoy", command=self._ir_a_hoy).pack(side="left")
        ttk.Button(pie, text="Borrar", command=self._borrar).pack(side="left", padx=6)
        ttk.Button(pie, text="Cancelar", command=self.destroy).pack(side="right")

        self._pintar()
        self._ubicar(junto_a or parent)

        self.bind("<Escape>", lambda _e: self.destroy())
        self.grab_set()
        self.focus_set()

    # ── navegación ──
    def _mes_anterior(self):
        self._mes -= 1
        if self._mes < 1:
            self._mes, self._anio = 12, self._anio - 1
        self._pintar()

    def _mes_siguiente(self):
        self._mes += 1
        if self._mes > 12:
            self._mes, self._anio = 1, self._anio + 1
        self._pintar()

    def _ir_a_hoy(self):
        self._elegir(self._hoy)

    def _borrar(self):
        self.elegida = ""          # cadena vacía = "dejá el campo en blanco"
        self.destroy()

    def _elegir(self, d: date):
        self.elegida = d
        self.destroy()

    # ── dibujo ──
    def _pintar(self):
        for w in self._grilla.winfo_children():
            w.destroy()
        self._titulo.config(text=f"{MESES[self._mes - 1]} {self._anio}")

        for col, nombre in enumerate(DIAS):
            ttk.Label(self._grilla, text=nombre, width=4, anchor="center",
                      foreground="#777").grid(row=0, column=col, pady=(0, 2))

        cal = calendar.Calendar(firstweekday=0)     # 0 = lunes
        for fila, semana in enumerate(cal.monthdayscalendar(self._anio, self._mes), start=1):
            for col, dia in enumerate(semana):
                if dia == 0:
                    continue
                d = date(self._anio, self._mes, dia)
                es_hoy = (d == self._hoy)
                b = tk.Button(
                    self._grilla, text=str(dia), width=3, relief="flat",
                    cursor="hand2",
                    bg=NARANJA if es_hoy else GRIS,
                    fg="white" if es_hoy else "black",
                    font=("Segoe UI", 9, "bold" if es_hoy else "normal"),
                    command=lambda dd=d: self._elegir(dd),
                )
                b.grid(row=fila, column=col, padx=1, pady=1)

    def _ubicar(self, ancla):
        """Abre la ventanita pegada al botón que la llamó, sin salirse de la pantalla."""
        self.update_idletasks()
        try:
            x = ancla.winfo_rootx()
            y = ancla.winfo_rooty() + ancla.winfo_height() + 2
        except tk.TclError:
            x = y = 200
        an, al = self.winfo_width(), self.winfo_height()
        x = max(0, min(x, self.winfo_screenwidth() - an - 10))
        y = max(0, min(y, self.winfo_screenheight() - al - 40))
        self.geometry(f"+{x}+{y}")


def elegir(parent, var, junto_a=None) -> None:
    """Abre el calendario y, si se elige un día, lo escribe en `var` (dd/mm/aaaa)."""
    inicial = _leer(var.get()) or date.today()
    pop = _Calendario(parent, inicial, junto_a)
    parent.wait_window(pop)
    if pop.elegida == "":
        var.set("")
    elif pop.elegida:
        var.set(pop.elegida.strftime("%d/%m/%Y"))
