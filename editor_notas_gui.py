"""Editor de notas de la web — app de escritorio (Diario La Campaña).

Ventana simple (Tkinter) para:
  • EDITAR una nota: pegás el link de la nota de la web, se carga su título/cuerpo/portada,
    los cambiás y publica los cambios (podés también cambiar la foto de portada).
  • BORRAR una nota: la saca de la web (papelera).
  • CREAR una nota nueva: elegís un Word (.docx o .txt) + fotos y la publica (mismo formato
    que «notas para web»; la 1ª foto es la portada).

Reutiliza el motor del publicador (platforms/wix.py) y su .env — NO usa claves nuevas.
Se abre con doble clic en «Editor de notas.bat». (V1 sin video.)
"""
import re
import threading
import webbrowser
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from utils.config import load_config
from platforms import wix
import avisos_web

NARANJA = "#e2620c"
ROJO = "#b00020"


def parrafos_desde_texto(raw: str) -> list:
    """Texto del cuadro → lista de párrafos (bloques separados por una línea en blanco;
    los saltos sueltos dentro de un bloque se unen con espacio para que quede prolijo)."""
    salida = []
    for bloque in re.split(r"\n\s*\n", raw or ""):
        limpio = " ".join(l.strip() for l in bloque.splitlines() if l.strip())
        if limpio:
            salida.append(limpio)
    return salida


class EditorNotas:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.nota = None            # dict de la nota cargada (para editar/borrar)
        self.nueva_portada = None   # ruta de foto nueva elegida (editar)
        self.doc_crear = None       # ruta del Word/txt (crear)
        self.fotos_crear = []       # rutas de fotos (crear)
        self._thumb_ref = None      # referencia viva de la miniatura (que no la limpie el GC)
        self.avisos_data = []       # lista de publicidades cargadas
        self.aviso_file = None      # archivo elegido para una publicidad nueva
        self._aviso_thumb_ref = None
        self.aviso_edit_idx = None      # índice de la publi seleccionada (para editar/borrar)
        self.aviso_edit_reemplazo = None  # archivo nuevo elegido para reemplazar (editar)

        root.title("Editor de notas — Diario La Campaña")
        root.geometry("760x720")
        root.minsize(680, 640)

        # Barra de estado (se crea ANTES de las pestañas: algunas la usan al construirse).
        self.status = tk.Label(root, text="Listo.", anchor="w", fg="#555",
                               font=("Segoe UI", 9), padx=12, pady=6)
        self.status.pack(fill="x", side="bottom")

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True, padx=10, pady=10)
        self.tab_editar = ttk.Frame(nb)
        self.tab_crear = ttk.Frame(nb)
        self.tab_avisos = ttk.Frame(nb)
        nb.add(self.tab_editar, text="  Editar o borrar una nota  ")
        nb.add(self.tab_crear, text="  Crear nota nueva  ")
        nb.add(self.tab_avisos, text="  Publicidades  ")

        self._build_editar(self.tab_editar)
        self._build_crear(self.tab_crear)
        self._build_avisos(self.tab_avisos)

    # ── helpers de UI ─────────────────────────────────────────────────────────
    def set_status(self, texto, color="#555"):
        self.status.config(text=texto, fg=color)

    def run_bg(self, fn, on_ok, busy="Procesando…"):
        """Corre `fn()` en segundo plano (para no congelar la ventana) y llama `on_ok(res)`
        en el hilo de la UI. Los errores se muestran en un cartel."""
        self.set_status(busy, NARANJA)
        self.root.config(cursor="watch")

        def worker():
            try:
                res = fn()
                self.root.after(0, lambda: self._done(lambda: on_ok(res)))
            except Exception as e:
                msg = str(e)
                self.root.after(0, lambda: self._done(lambda: self._error(msg)))

        threading.Thread(target=worker, daemon=True).start()

    def _done(self, cb):
        self.root.config(cursor="")
        cb()

    def _error(self, msg):
        self.set_status("Ups: " + msg, ROJO)
        messagebox.showerror("Error", msg)

    def _miniatura(self, source, es_url):
        try:
            from PIL import Image, ImageTk
            if es_url:
                import io
                import requests
                datos = requests.get(source, timeout=30).content
                img = Image.open(io.BytesIO(datos))
            else:
                img = Image.open(source)
            img.thumbnail((240, 240))
            return ImageTk.PhotoImage(img)
        except Exception:
            return None

    # ── pestaña EDITAR / BORRAR ───────────────────────────────────────────────
    def _build_editar(self, f):
        top = ttk.Frame(f)
        top.pack(fill="x", padx=12, pady=(12, 6))
        ttk.Label(top, text="Link de la nota:").pack(side="left")
        self.link_var = tk.StringVar()
        self.link_entry = ttk.Entry(top, textvariable=self.link_var)
        self.link_entry.pack(side="left", fill="x", expand=True, padx=8)
        self.btn_cargar = ttk.Button(top, text="Cargar", command=self.on_cargar)
        self.btn_cargar.pack(side="left")

        # Zona editable (oculta hasta cargar una nota)
        self.edit_frame = ttk.Frame(f)

        ttk.Label(self.edit_frame, text="Título:").pack(anchor="w", padx=12, pady=(8, 0))
        self.titulo_var = tk.StringVar()
        ttk.Entry(self.edit_frame, textvariable=self.titulo_var).pack(
            fill="x", padx=12, pady=(2, 8))

        ttk.Label(self.edit_frame,
                  text="Cuerpo (un párrafo por bloque; separá los párrafos con una línea en blanco):"
                  ).pack(anchor="w", padx=12)
        cuerpo_box = ttk.Frame(self.edit_frame)
        cuerpo_box.pack(fill="both", expand=True, padx=12, pady=(2, 8))
        self.cuerpo_text = tk.Text(cuerpo_box, wrap="word", height=14, font=("Segoe UI", 10),
                                   undo=True)
        sb = ttk.Scrollbar(cuerpo_box, command=self.cuerpo_text.yview)
        self.cuerpo_text.config(yscrollcommand=sb.set)
        self.cuerpo_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        portada = ttk.Frame(self.edit_frame)
        portada.pack(fill="x", padx=12, pady=(0, 8))
        self.thumb_label = tk.Label(portada, text="(sin portada)", width=28, height=8,
                                    bg="#f0f0f0", relief="groove")
        self.thumb_label.pack(side="left")
        pd = ttk.Frame(portada)
        pd.pack(side="left", fill="x", expand=True, padx=12)
        ttk.Label(pd, text="Foto de portada").pack(anchor="w")
        ttk.Button(pd, text="Cambiar foto de portada…", command=self.on_cambiar_portada
                   ).pack(anchor="w", pady=4)
        self.portada_nueva_label = ttk.Label(pd, text="", foreground=NARANJA)
        self.portada_nueva_label.pack(anchor="w")

        botones = ttk.Frame(self.edit_frame)
        botones.pack(fill="x", padx=12, pady=(0, 12))
        self.btn_guardar = tk.Button(botones, text="💾  Guardar cambios y publicar",
                                     command=self.on_guardar, bg=NARANJA, fg="white",
                                     font=("Segoe UI", 10, "bold"), relief="flat",
                                     padx=14, pady=8, cursor="hand2")
        self.btn_guardar.pack(side="left")
        self.btn_abrir = ttk.Button(botones, text="Ver en la web", command=self.on_abrir_web)
        self.btn_abrir.pack(side="left", padx=8)
        self.btn_borrar = tk.Button(botones, text="🗑  Borrar nota", command=self.on_borrar,
                                    bg=ROJO, fg="white", font=("Segoe UI", 10, "bold"),
                                    relief="flat", padx=14, pady=8, cursor="hand2")
        self.btn_borrar.pack(side="right")

    def on_cargar(self):
        link = self.link_var.get().strip()
        if not link:
            messagebox.showinfo("Falta el link", "Pegá el link de la nota que querés editar.")
            return
        self.run_bg(lambda: wix.cargar_nota(link), self._pintar_nota, busy="Buscando la nota…")

    def _pintar_nota(self, nota):
        self.nota = nota
        self.nueva_portada = None
        self.portada_nueva_label.config(text="")
        self.titulo_var.set(nota["title"])
        self.cuerpo_text.delete("1.0", "end")
        self.cuerpo_text.insert("1.0", "\n\n".join(nota["paragraphs"]))
        self.edit_frame.pack(fill="both", expand=True)
        self.set_status(f"Nota cargada: {nota['url']}", "#227a22")
        # Miniatura de la portada (en segundo plano; si falla, queda el texto).
        cover = nota.get("cover_url")
        if cover:
            self.run_bg(lambda: self._miniatura(cover, True), self._set_thumb,
                        busy="Cargando la nota…")

    def _set_thumb(self, img):
        if img:
            self._thumb_ref = img
            self.thumb_label.config(image=img, text="", width=240, height=180)
        else:
            self.thumb_label.config(image="", text="(portada actual)")
        self.set_status(f"Nota cargada: {self.nota['url']}", "#227a22")

    def on_cambiar_portada(self):
        ruta = filedialog.askopenfilename(
            title="Elegí la nueva foto de portada",
            filetypes=[("Imágenes", "*.jpg *.jpeg *.png *.webp"), ("Todos", "*.*")])
        if not ruta:
            return
        self.nueva_portada = ruta
        self.portada_nueva_label.config(text="Nueva portada: " + Path(ruta).name)
        img = self._miniatura(ruta, False)
        if img:
            self._thumb_ref = img
            self.thumb_label.config(image=img, text="", width=240, height=180)

    def on_guardar(self):
        if not self.nota:
            return
        titulo = self.titulo_var.get().strip()
        parrafos = parrafos_desde_texto(self.cuerpo_text.get("1.0", "end"))
        if not titulo:
            messagebox.showinfo("Falta el título", "La nota necesita un título.")
            return
        if not parrafos:
            messagebox.showinfo("Falta el cuerpo", "La nota necesita al menos un párrafo.")
            return
        if not messagebox.askyesno("Confirmar", "¿Publico los cambios en la web?"):
            return
        pid = self.nota["id"]
        portada = self.nueva_portada
        self.run_bg(lambda: wix.editar_nota(pid, titulo, parrafos, nueva_portada_path=portada),
                    self._guardado_ok, busy="Publicando los cambios…")

    def _guardado_ok(self, res):
        url = (res or {}).get("url") or self.nota.get("url", "")
        self.nota["url"] = url
        self.nueva_portada = None
        self.portada_nueva_label.config(text="")
        self.set_status("✅ Cambios publicados: " + url, "#227a22")
        messagebox.showinfo("Listo", "Los cambios ya están publicados en la web.")

    def on_borrar(self):
        if not self.nota:
            return
        if not messagebox.askyesno(
                "Borrar nota",
                "¿Seguro que querés BORRAR esta nota de la web?\n\n"
                f"{self.nota.get('url', '')}\n\nVa a la papelera y deja de verse."):
            return
        pid = self.nota["id"]
        self.run_bg(lambda: wix.borrar_post(pid), self._borrado_ok, busy="Borrando la nota…")

    def _borrado_ok(self, _res):
        self.set_status("🗑 Nota borrada de la web.", ROJO)
        messagebox.showinfo("Borrada", "La nota se borró de la web.")
        self.edit_frame.pack_forget()
        self.nota = None
        self.link_var.set("")

    def on_abrir_web(self):
        if self.nota and self.nota.get("url"):
            webbrowser.open(self.nota["url"])

    # ── pestaña CREAR ─────────────────────────────────────────────────────────
    def _build_crear(self, f):
        ttk.Label(f, text="Texto de la nota (Word .docx o .txt):").pack(
            anchor="w", padx=12, pady=(14, 0))
        docf = ttk.Frame(f)
        docf.pack(fill="x", padx=12, pady=(2, 6))
        self.doc_var = tk.StringVar()
        ttk.Entry(docf, textvariable=self.doc_var, state="readonly").pack(
            side="left", fill="x", expand=True)
        ttk.Button(docf, text="Elegir…", command=self.on_elegir_doc).pack(side="left", padx=8)
        self.doc_preview = ttk.Label(f, text="", foreground="#227a22", wraplength=700,
                                     justify="left")
        self.doc_preview.pack(anchor="w", padx=12)

        ttk.Label(f, text="Fotos (la 1ª de la lista es la PORTADA):").pack(
            anchor="w", padx=12, pady=(10, 0))
        fotf = ttk.Frame(f)
        fotf.pack(fill="both", expand=True, padx=12, pady=(2, 6))
        self.fotos_list = tk.Listbox(fotf, height=8)
        self.fotos_list.pack(side="left", fill="both", expand=True)
        fbtn = ttk.Frame(fotf)
        fbtn.pack(side="left", fill="y", padx=8)
        ttk.Button(fbtn, text="Agregar fotos…", command=self.on_agregar_fotos).pack(fill="x", pady=2)
        ttk.Button(fbtn, text="Quitar", command=self.on_quitar_foto).pack(fill="x", pady=2)
        ttk.Button(fbtn, text="▲ Subir", command=lambda: self.on_mover_foto(-1)).pack(fill="x", pady=2)
        ttk.Button(fbtn, text="▼ Bajar", command=lambda: self.on_mover_foto(1)).pack(fill="x", pady=2)

        self.btn_crear = tk.Button(f, text="🚀  Publicar nota nueva", command=self.on_crear,
                                   bg=NARANJA, fg="white", font=("Segoe UI", 10, "bold"),
                                   relief="flat", padx=14, pady=8, cursor="hand2")
        self.btn_crear.pack(anchor="w", padx=12, pady=(4, 14))

    def on_elegir_doc(self):
        ruta = filedialog.askopenfilename(
            title="Elegí el Word o .txt de la nota",
            filetypes=[("Word / texto", "*.docx *.txt"), ("Todos", "*.*")])
        if not ruta:
            return
        self.doc_crear = ruta
        self.doc_var.set(ruta)
        try:
            from notas_web import _parse_docx
            volanta, titular, cuerpo = _parse_docx(Path(ruta))
            if not titular:
                self.doc_preview.config(text="⚠ No pude detectar el título en ese archivo.",
                                        foreground=ROJO)
            else:
                vt = f"{volanta} — {titular}" if volanta else titular
                self.doc_preview.config(text=f"Título detectado: {vt}  ·  {len(cuerpo)} párrafo/s",
                                        foreground="#227a22")
        except Exception as e:
            self.doc_preview.config(text="⚠ No pude leer el archivo: " + str(e), foreground=ROJO)

    def on_agregar_fotos(self):
        rutas = filedialog.askopenfilenames(
            title="Elegí las fotos",
            filetypes=[("Imágenes", "*.jpg *.jpeg *.png *.webp"), ("Todos", "*.*")])
        for r in rutas:
            if r not in self.fotos_crear:
                self.fotos_crear.append(r)
                self.fotos_list.insert("end", Path(r).name)

    def on_quitar_foto(self):
        sel = list(self.fotos_list.curselection())
        for i in reversed(sel):
            self.fotos_list.delete(i)
            del self.fotos_crear[i]

    def on_mover_foto(self, delta):
        sel = self.fotos_list.curselection()
        if not sel:
            return
        i = sel[0]
        j = i + delta
        if j < 0 or j >= len(self.fotos_crear):
            return
        self.fotos_crear[i], self.fotos_crear[j] = self.fotos_crear[j], self.fotos_crear[i]
        self.fotos_list.delete(0, "end")
        for r in self.fotos_crear:
            self.fotos_list.insert("end", Path(r).name)
        self.fotos_list.selection_set(j)

    def on_crear(self):
        if not self.doc_crear:
            messagebox.showinfo("Falta el texto", "Elegí el Word o .txt de la nota.")
            return
        if not self.fotos_crear:
            messagebox.showinfo("Faltan fotos", "Agregá al menos una foto (la 1ª es la portada).")
            return
        if not messagebox.askyesno("Confirmar", "¿Publico la nota nueva en la web?"):
            return
        doc = Path(self.doc_crear)
        fotos = list(self.fotos_crear)
        self.run_bg(lambda: self._publicar_nueva(doc, fotos), self._creada_ok,
                    busy="Publicando la nota nueva…")

    def _publicar_nueva(self, doc: Path, fotos: list) -> dict:
        from notas_web import _parse_docx
        volanta, titular, cuerpo = _parse_docx(doc)
        if not titular:
            raise ValueError("El archivo no tiene un título legible.")
        title = f"{volanta} — {titular}" if volanta else titular
        body = titular + ("\n\n" + "\n\n".join(cuerpo) if cuerpo else "")
        desc = cuerpo[0] if cuerpo else titular
        info = wix.crear_borrador_galeria(title, body, fotos, video_urls=[], page=0,
                                          description=desc)
        return wix.publicar_borrador(info["draft_id"])

    def _creada_ok(self, res):
        url = (res or {}).get("url", "")
        self.set_status("✅ Nota publicada: " + url, "#227a22")
        if messagebox.askyesno("Publicada", "La nota nueva ya está en la web.\n\n¿La abro?"):
            if url:
                webbrowser.open(url)
        # limpiar para la próxima
        self.doc_crear = None
        self.doc_var.set("")
        self.doc_preview.config(text="")
        self.fotos_crear = []
        self.fotos_list.delete(0, "end")

    # ── pestaña PUBLICIDADES (avisos del pie de la web) ───────────────────────
    def _build_avisos(self, f):
        top = ttk.Frame(f)
        top.pack(fill="x", padx=12, pady=(12, 4))
        ttk.Label(top, text="Publicidades del pie de la web («Nuestros anunciantes»)",
                  font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Button(top, text="↻ Actualizar", command=self.on_avisos_refrescar).pack(side="right")

        cuerpo = ttk.Frame(f)
        cuerpo.pack(fill="both", expand=True, padx=12, pady=4)

        # Izquierda: lista de publicidades
        izq = ttk.Frame(cuerpo)
        izq.pack(side="left", fill="both", expand=True)
        ttk.Label(izq, text="Publicidades actuales:").pack(anchor="w")
        lb_box = ttk.Frame(izq)
        lb_box.pack(fill="both", expand=True, pady=(2, 0))
        self.avisos_list = tk.Listbox(lb_box, height=10)
        self.avisos_list.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(lb_box, command=self.avisos_list.yview)
        self.avisos_list.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.avisos_list.bind("<<ListboxSelect>>", self.on_aviso_seleccion)

        # Derecha: EDITAR la publicidad seleccionada (nombre, link, reemplazar archivo, borrar)
        der = ttk.LabelFrame(cuerpo, text="  Editar la publicidad seleccionada  ", width=310)
        der.pack(side="left", fill="y", padx=(12, 0))
        der.pack_propagate(False)
        self.aviso_thumb = tk.Label(der, text="(elegí una de la lista)", width=26, height=8,
                                    bg="#f0f0f0", relief="groove")
        self.aviso_thumb.pack(pady=(8, 4))

        er_n = ttk.Frame(der)
        er_n.pack(fill="x", padx=10, pady=2)
        ttk.Label(er_n, text="Nombre:", width=7).pack(side="left")
        self.aviso_edit_nombre_var = tk.StringVar()
        self.aviso_edit_nombre_entry = ttk.Entry(er_n, textvariable=self.aviso_edit_nombre_var,
                                                  state="disabled")
        self.aviso_edit_nombre_entry.pack(side="left", fill="x", expand=True)
        er_l = ttk.Frame(der)
        er_l.pack(fill="x", padx=10, pady=2)
        ttk.Label(er_l, text="Link:", width=7).pack(side="left")
        self.aviso_edit_link_var = tk.StringVar()
        self.aviso_edit_link_entry = ttk.Entry(er_l, textvariable=self.aviso_edit_link_var,
                                               state="disabled")
        self.aviso_edit_link_entry.pack(side="left", fill="x", expand=True)

        self.btn_aviso_reemplazar = ttk.Button(der, text="Reemplazar archivo…",
                                                command=self.on_aviso_edit_reemplazar,
                                                state="disabled")
        self.btn_aviso_reemplazar.pack(anchor="w", padx=10, pady=(4, 0))
        self.aviso_reemplazo_lbl = ttk.Label(der, text="", foreground=NARANJA,
                                             wraplength=270, justify="left")
        self.aviso_reemplazo_lbl.pack(anchor="w", padx=10)

        er_btn = ttk.Frame(der)
        er_btn.pack(fill="x", padx=10, pady=(8, 8))
        self.btn_aviso_guardar = tk.Button(er_btn, text="💾  Guardar cambios",
                                           command=self.on_aviso_guardar, bg=NARANJA, fg="white",
                                           font=("Segoe UI", 9, "bold"), relief="flat",
                                           padx=10, pady=6, cursor="hand2", state="disabled")
        self.btn_aviso_guardar.pack(side="left")
        self.btn_aviso_borrar = tk.Button(er_btn, text="🗑  Borrar", command=self.on_aviso_borrar,
                                          bg=ROJO, fg="white", font=("Segoe UI", 9, "bold"),
                                          relief="flat", padx=10, pady=6, cursor="hand2",
                                          state="disabled")
        self.btn_aviso_borrar.pack(side="right")

        ttk.Separator(f, orient="horizontal").pack(fill="x", padx=12, pady=(6, 4))

        # Alta de una publicidad nueva
        alta = ttk.LabelFrame(f, text="  Agregar una publicidad nueva  ")
        alta.pack(fill="x", padx=12, pady=(0, 8))
        r1 = ttk.Frame(alta)
        r1.pack(fill="x", padx=10, pady=(8, 2))
        ttk.Label(r1, text="Imagen o video:", width=15).pack(side="left")
        self.aviso_file_var = tk.StringVar()
        ttk.Entry(r1, textvariable=self.aviso_file_var, state="readonly").pack(
            side="left", fill="x", expand=True)
        ttk.Button(r1, text="Elegir…", command=self.on_aviso_elegir_archivo).pack(side="left", padx=6)
        r2 = ttk.Frame(alta)
        r2.pack(fill="x", padx=10, pady=2)
        ttk.Label(r2, text="Nombre:", width=15).pack(side="left")
        self.aviso_nombre_var = tk.StringVar()
        ttk.Entry(r2, textvariable=self.aviso_nombre_var).pack(side="left", fill="x", expand=True)
        r3 = ttk.Frame(alta)
        r3.pack(fill="x", padx=10, pady=2)
        ttk.Label(r3, text="Link (opcional):", width=15).pack(side="left")
        self.aviso_link_var = tk.StringVar()
        ttk.Entry(r3, textvariable=self.aviso_link_var).pack(side="left", fill="x", expand=True)
        ttk.Label(alta, foreground="#777", wraplength=680, justify="left",
                  text="El link es a dónde va el visitante al tocar el aviso "
                       "(Instagram, Facebook, la web del comercio, o «tel:2346…»). Se puede dejar vacío.\n"
                       "La publicidad nueva se muestra PRIMERA (arriba a la izquierda del bloque)."
                  ).pack(anchor="w", padx=10, pady=(2, 0))
        self.btn_aviso_agregar = tk.Button(alta, text="➕  Agregar y publicar",
                                           command=self.on_aviso_agregar, bg=NARANJA, fg="white",
                                           font=("Segoe UI", 10, "bold"), relief="flat",
                                           padx=14, pady=8, cursor="hand2")
        self.btn_aviso_agregar.pack(anchor="w", padx=10, pady=(6, 10))

        # Pie: carpeta de la web
        pie = ttk.Frame(f)
        pie.pack(fill="x", padx=12, pady=(0, 8))
        self.aviso_dir_lbl = ttk.Label(pie, text="", foreground="#777")
        self.aviso_dir_lbl.pack(side="left")
        ttk.Button(pie, text="Cambiar carpeta…", command=self.on_aviso_cambiar_carpeta).pack(side="right")

        self.on_avisos_refrescar()

    def on_avisos_refrescar(self):
        self.run_bg(avisos_web.cargar_avisos, self._pintar_avisos, busy="Leyendo publicidades…")

    def _pintar_avisos(self, data):
        self.avisos_data = data
        self.avisos_list.delete(0, "end")
        for a in data:
            etiqueta = a.get("nombre", "(sin nombre)")
            if a.get("_es_video"):
                etiqueta += "  🎬"
            if a.get("link"):
                etiqueta += "  🔗"
            self.avisos_list.insert("end", etiqueta)
        self._reset_aviso_edit()
        try:
            self.aviso_dir_lbl.config(text="Carpeta de la web: " + str(avisos_web.web_dir()))
        except Exception:
            self.aviso_dir_lbl.config(
                text="⚠ No encontré la carpeta de la web — usá «Cambiar carpeta…».")
        self.set_status(f"{len(data)} publicidad/es en la web.", "#227a22")

    def _reset_aviso_edit(self):
        """Deja el formulario de edición vacío y deshabilitado (nada seleccionado)."""
        self.aviso_edit_idx = None
        self.aviso_edit_reemplazo = None
        self.aviso_edit_nombre_var.set("")
        self.aviso_edit_link_var.set("")
        self.aviso_reemplazo_lbl.config(text="")
        self.aviso_thumb.config(image="", text="(elegí una de la lista)", width=26, height=8)
        self._aviso_thumb_ref = None
        for w in (self.aviso_edit_nombre_entry, self.aviso_edit_link_entry):
            w.config(state="disabled")
        for b in (self.btn_aviso_reemplazar, self.btn_aviso_guardar, self.btn_aviso_borrar):
            b.config(state="disabled")

    def on_aviso_seleccion(self, _evt=None):
        sel = self.avisos_list.curselection()
        if not sel:
            return
        idx = sel[0]
        a = self.avisos_data[idx]
        self.aviso_edit_idx = idx
        self.aviso_edit_reemplazo = None
        self.aviso_reemplazo_lbl.config(text="")
        # Prellenar y habilitar el formulario
        self.aviso_edit_nombre_entry.config(state="normal")
        self.aviso_edit_link_entry.config(state="normal")
        self.aviso_edit_nombre_var.set(a.get("nombre", ""))
        self.aviso_edit_link_var.set(a.get("link", ""))
        self.btn_aviso_reemplazar.config(state="normal")
        self.btn_aviso_guardar.config(state="normal")
        self.btn_aviso_borrar.config(state="normal")
        # Vista previa (imagen o cuadro del video)
        self.aviso_thumb.config(image="", text="⏳", width=26, height=8)
        self._aviso_thumb_ref = None
        if a.get("_es_video"):
            self.run_bg(lambda: self._thumb_video(a.get("_archivo")),
                        self._set_aviso_thumb, busy="Extrayendo un cuadro del video…")
        else:
            self.run_bg(lambda: self._miniatura(a.get("_archivo"), False),
                        self._set_aviso_thumb, busy="Cargando imagen…")

    def on_aviso_edit_reemplazar(self):
        if self.aviso_edit_idx is None:
            return
        ruta = filedialog.askopenfilename(
            title="Elegí el archivo nuevo (reemplaza el actual)",
            filetypes=[("Imágenes y video", "*.jpg *.jpeg *.png *.webp *.gif *.mp4 *.webm"),
                       ("Imágenes", "*.jpg *.jpeg *.png *.webp *.gif"),
                       ("Video", "*.mp4 *.webm"),
                       ("Todos", "*.*")])
        if not ruta:
            return
        self.aviso_edit_reemplazo = ruta
        self.aviso_reemplazo_lbl.config(text="Archivo nuevo: " + Path(ruta).name)
        # Vista previa del reemplazo
        self.aviso_thumb.config(image="", text="⏳", width=26, height=8)
        self._aviso_thumb_ref = None
        if Path(ruta).suffix.lower() in (".mp4", ".webm", ".mov"):
            self.run_bg(lambda: self._thumb_video(ruta), self._set_aviso_thumb,
                        busy="Extrayendo un cuadro del video…")
        else:
            self.run_bg(lambda: self._miniatura(ruta, False), self._set_aviso_thumb,
                        busy="Cargando imagen…")

    def on_aviso_guardar(self):
        if self.aviso_edit_idx is None:
            return
        idx = self.aviso_edit_idx
        actual = self.avisos_data[idx]
        nombre_orig = actual.get("nombre", "")
        nombre = self.aviso_edit_nombre_var.get().strip()
        link = self.aviso_edit_link_var.get().strip()
        reemplazo = self.aviso_edit_reemplazo
        if not nombre:
            messagebox.showinfo("Falta el nombre", "La publicidad necesita un nombre.")
            return
        # ¿Cambió algo?
        if (not reemplazo and nombre == nombre_orig and link == (actual.get("link", "") or "")):
            messagebox.showinfo("Sin cambios", "No cambiaste nada para guardar.")
            return
        cambios = []
        if nombre != nombre_orig:
            cambios.append("• el nombre")
        if link != (actual.get("link", "") or ""):
            cambios.append("• el link")
        if reemplazo:
            cambios.append("• el archivo (reemplazo)")
        if not messagebox.askyesno(
                "Guardar cambios",
                f"¿Guardo estos cambios en «{nombre_orig}» y los publico?\n\n"
                + "\n".join(cambios) + "\n\nAparece en la web en 1-2 minutos."):
            return
        self.run_bg(
            lambda: avisos_web.editar_aviso(idx, nombre_orig, nombre, link,
                                            nuevo_archivo=reemplazo),
            self._aviso_editado_ok, busy="Guardando los cambios…")

    def _aviso_editado_ok(self, nueva):
        self.set_status("✅ Publicidad actualizada: " + nueva.get("nombre", ""), "#227a22")
        messagebox.showinfo(
            "Listo",
            "Los cambios se guardaron.\n\nEn 1-2 minutos se ven en la web "
            "(Vercel está publicando).")
        self.on_avisos_refrescar()

    def _thumb_video(self, video_path):
        """Saca un cuadro del video con ffmpeg y lo convierte en miniatura (o None)."""
        png = avisos_web.miniatura_video(video_path)
        if not png:
            return None
        return self._miniatura(str(png), False)

    def _set_aviso_thumb(self, img):
        if img:
            self._aviso_thumb_ref = img
            self.aviso_thumb.config(image=img, text="", width=240, height=180)
        else:
            self.aviso_thumb.config(image="", text="🎬 (sin vista previa)",
                                    width=26, height=9)
            self._aviso_thumb_ref = None

    def on_aviso_elegir_archivo(self):
        ruta = filedialog.askopenfilename(
            title="Elegí la imagen o el video de la publicidad",
            filetypes=[("Imágenes y video", "*.jpg *.jpeg *.png *.webp *.gif *.mp4 *.webm"),
                       ("Imágenes", "*.jpg *.jpeg *.png *.webp *.gif"),
                       ("Video", "*.mp4 *.webm"),
                       ("Todos", "*.*")])
        if not ruta:
            return
        self.aviso_file = ruta
        self.aviso_file_var.set(ruta)
        if not self.aviso_nombre_var.get().strip():
            sugerido = Path(ruta).stem.replace("-", " ").replace("_", " ").strip().title()
            self.aviso_nombre_var.set(sugerido)

    def on_aviso_agregar(self):
        archivo = self.aviso_file
        nombre = self.aviso_nombre_var.get().strip()
        link = self.aviso_link_var.get().strip()
        if not archivo:
            messagebox.showinfo("Falta el archivo", "Elegí la imagen o el video de la publicidad.")
            return
        if not nombre:
            messagebox.showinfo("Falta el nombre", "Escribí el nombre del anunciante.")
            return
        if not messagebox.askyesno(
                "Publicar publicidad",
                f"¿Agrego «{nombre}» a las publicidades de la web y lo publico?\n\n"
                "Se sube al sitio; aparece en la web en 1-2 minutos."):
            return
        self.run_bg(lambda: avisos_web.agregar_aviso(nombre, archivo, link),
                    self._aviso_agregado_ok, busy="Publicando la publicidad…")

    def _aviso_agregado_ok(self, entry):
        self.set_status("✅ Publicidad agregada: " + entry.get("nombre", ""), "#227a22")
        self.aviso_file = None
        self.aviso_file_var.set("")
        self.aviso_nombre_var.set("")
        self.aviso_link_var.set("")
        messagebox.showinfo(
            "Listo",
            "La publicidad se subió.\n\nEn 1-2 minutos aparece en la web "
            "(Vercel la está publicando).")
        self.on_avisos_refrescar()

    def on_aviso_borrar(self):
        idx = self.aviso_edit_idx
        if idx is None:
            return
        a = self.avisos_data[idx]
        nombre = a.get("nombre", "")
        if not messagebox.askyesno(
                "Borrar publicidad",
                f"¿Seguro que querés BORRAR la publicidad «{nombre}» de la web?\n\n"
                "Se saca del sitio; en 1-2 minutos deja de verse."):
            return
        self.run_bg(lambda: avisos_web.borrar_aviso(idx, nombre_esperado=nombre),
                    self._aviso_borrado_ok, busy="Borrando la publicidad…")

    def _aviso_borrado_ok(self, quitado):
        self.set_status("🗑 Publicidad borrada: " + quitado.get("nombre", ""), ROJO)
        messagebox.showinfo(
            "Borrada",
            "La publicidad se sacó.\n\nEn 1-2 minutos deja de verse en la web.")
        self.on_avisos_refrescar()

    def on_aviso_cambiar_carpeta(self):
        ruta = filedialog.askdirectory(
            title="Elegí la carpeta del proyecto de la web (diario_web)")
        if not ruta:
            return
        try:
            avisos_web.set_web_dir(ruta)
        except Exception as e:
            messagebox.showerror("Carpeta inválida", str(e))
            return
        self.on_avisos_refrescar()


def main():
    load_config()
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass
    EditorNotas(root)
    root.mainloop()


if __name__ == "__main__":
    main()
