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
import calendario_tk

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
        ttk.Button(top, text="🧹 Limpiar vencidas",
                   command=self.on_avisos_limpiar).pack(side="right", padx=(0, 6))
        ttk.Button(top, text="🕒 Agendadas en redes",
                   command=self.on_avisos_programadas).pack(side="right", padx=(0, 6))

        # OJO CON EL ORDEN: el pie y el formulario de alta se empaquetan ANTES que
        # la lista y contra el fondo (side="bottom"). El packer de Tk reparte en el
        # orden en que se le pide, asi que lo que va primero se asegura su lugar y
        # lo que se achica cuando la ventana queda corta es la LISTA, nunca los
        # botones. Al sumar los campos de programacion, los botones de editar y
        # borrar habian quedado directamente sin dibujar.
        pie = ttk.Frame(f)
        pie.pack(side="bottom", fill="x", padx=12, pady=(0, 8))
        self.aviso_dir_lbl = ttk.Label(pie, text="", foreground="#777")
        self.aviso_dir_lbl.pack(side="left")
        ttk.Button(pie, text="Cambiar carpeta…",
                   command=self.on_aviso_cambiar_carpeta).pack(side="right")

        alta = ttk.LabelFrame(f, text="  Agregar una publicidad nueva  ")
        alta.pack(side="bottom", fill="x", padx=12, pady=(0, 8))

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

        r4 = ttk.Frame(alta)
        r4.pack(fill="x", padx=10, pady=(4, 2))
        ttk.Label(r4, text="Sale el:", width=15).pack(side="left")
        self.aviso_desde_f = tk.StringVar()
        ttk.Entry(r4, textvariable=self.aviso_desde_f, width=12).pack(side="left")
        b_desde = ttk.Button(r4, text="📅", width=3)
        b_desde.config(command=lambda: calendario_tk.elegir(self.root, self.aviso_desde_f, b_desde))
        b_desde.pack(side="left", padx=(2, 0))
        ttk.Label(r4, text=" a las ").pack(side="left")
        self.aviso_desde_h = tk.StringVar(value="08:00")
        ttk.Entry(r4, textvariable=self.aviso_desde_h, width=7).pack(side="left")
        ttk.Label(r4, text="      Termina el:").pack(side="left")
        self.aviso_hasta_f = tk.StringVar()
        ttk.Entry(r4, textvariable=self.aviso_hasta_f, width=12).pack(side="left", padx=(6, 0))
        b_hasta = ttk.Button(r4, text="📅", width=3)
        b_hasta.config(command=lambda: calendario_tk.elegir(self.root, self.aviso_hasta_f, b_hasta))
        b_hasta.pack(side="left", padx=(2, 0))
        ttk.Label(r4, text=" a las ").pack(side="left")
        self.aviso_hasta_h = tk.StringVar(value="23:59")
        ttk.Entry(r4, textvariable=self.aviso_hasta_h, width=7).pack(side="left")

        r5 = ttk.Frame(alta)
        r5.pack(fill="x", padx=10, pady=2)
        ttk.Label(r5, text="¿Dónde va?:", width=15).pack(side="left")
        self.aviso_web_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(r5, text="Web (anunciantes)", variable=self.aviso_web_var).pack(side="left")
        # La forma NO se pregunta: sale de medir la pieza (ver _aviso_medido). La
        # variable queda igual porque es lo que se manda al guardar, y arranca en
        # "ancha" por si el archivo no se puede medir.
        self.aviso_forma_var = tk.StringVar(value="ancha")
        self.aviso_fb_var = tk.BooleanVar(value=False)
        self.aviso_ig_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(r5, text="Facebook", variable=self.aviso_fb_var).pack(side="left", padx=(12, 0))
        ttk.Checkbutton(r5, text="Instagram", variable=self.aviso_ig_var).pack(side="left", padx=(8, 0))

        self.aviso_medida_lbl = ttk.Label(alta, text="", foreground=NARANJA)
        self.aviso_medida_lbl.pack(anchor="w", padx=10, pady=(2, 0))

        r6 = ttk.Frame(alta)
        r6.pack(fill="x", padx=10, pady=2)
        ttk.Label(r6, text="Texto del posteo:", width=15).pack(side="left")
        self.aviso_texto_var = tk.StringVar()
        ttk.Entry(r6, textvariable=self.aviso_texto_var).pack(side="left", fill="x", expand=True)

        ttk.Label(alta, foreground="#777", wraplength=700, justify="left",
                  text="Fechas con el botón 📅 o a mano en dd/mm/aaaa. «Sale el» vacío = entra ya; "
                       "«Termina el» vacío = no termina nunca.\n"
                       "El tamaño de la pieza lo mide el programa solo y de ahí sale en qué hueco "
                       "de la web encaja y cómo sale en las redes.\n"
                       "La fecha de fin es un FRENO: deja de salir y el aviso se va de la web. "
                       "Lo ya posteado en Facebook e Instagram no se borra."
                  ).pack(anchor="w", padx=10, pady=(2, 0))

        self.btn_aviso_agregar = tk.Button(alta, text="➕  Agregar y publicar",
                                           command=self.on_aviso_agregar, bg=NARANJA, fg="white",
                                           font=("Segoe UI", 10, "bold"), relief="flat",
                                           padx=14, pady=8, cursor="hand2")
        self.btn_aviso_agregar.pack(anchor="w", padx=10, pady=(6, 10))

        ttk.Separator(f, orient="horizontal").pack(side="bottom", fill="x", padx=12, pady=(6, 4))

        # ── la lista, con lo que sobre de alto ──
        cuerpo = ttk.Frame(f)
        cuerpo.pack(fill="both", expand=True, padx=12, pady=4)

        izq = ttk.Frame(cuerpo)
        izq.pack(side="left", fill="both", expand=True)
        ttk.Label(izq, text="Publicidades actuales (doble clic para editarla):").pack(anchor="w")
        lb_box = ttk.Frame(izq)
        lb_box.pack(fill="both", expand=True, pady=(2, 0))
        self.avisos_list = tk.Listbox(lb_box, height=6)
        self.avisos_list.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(lb_box, command=self.avisos_list.yview)
        self.avisos_list.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.avisos_list.bind("<<ListboxSelect>>", self.on_aviso_seleccion)
        self.avisos_list.bind("<Double-Button-1>", lambda _e: self.on_aviso_editar())

        acciones = ttk.Frame(izq)
        acciones.pack(fill="x", pady=(6, 0))
        self.btn_aviso_editar = tk.Button(acciones, text="✏️  Editar / cambiar la imagen",
                                          command=self.on_aviso_editar, bg=NARANJA, fg="white",
                                          font=("Segoe UI", 9, "bold"), relief="flat",
                                          padx=10, pady=6, cursor="hand2", state="disabled")
        self.btn_aviso_editar.pack(side="left")
        self.btn_aviso_borrar = tk.Button(acciones, text="🗑  Borrar", command=self.on_aviso_borrar,
                                          bg=ROJO, fg="white", font=("Segoe UI", 9, "bold"),
                                          relief="flat", padx=10, pady=6, cursor="hand2",
                                          state="disabled")
        self.btn_aviso_borrar.pack(side="left", padx=(8, 0))

        der = ttk.Frame(cuerpo, width=250)
        der.pack(side="left", fill="y", padx=(12, 0))
        der.pack_propagate(False)
        self.aviso_thumb = tk.Label(der, text="(elegí una de la lista)", width=24, height=7,
                                    bg="#f0f0f0", relief="groove")
        self.aviso_thumb.pack(pady=(18, 4))

        self.on_avisos_refrescar()

    def on_avisos_programadas(self):
        """Ventana con los posteos agendados en Facebook e Instagram.

        Existe porque una campaña que va SOLO a redes no aparece en la lista de
        anunciantes (no está en el JSON de la web), y sin esto no habría forma de
        verla ni de cancelarla.
        """
        self.run_bg(avisos_web.programadas, self._ventana_programadas,
                    busy="Leyendo los posteos agendados…")

    def _ventana_programadas(self, trabajos):
        win = tk.Toplevel(self.root)
        win.title("Posteos agendados en redes")
        win.geometry("620x360")
        win.transient(self.root)

        ttk.Label(win, text="Posteos agendados en Facebook e Instagram",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=12, pady=(12, 2))
        ttk.Label(win, foreground="#777", wraplength=580, justify="left",
                  text="Cancelar saca el posteo de la cola: si todavía no salió, no sale. "
                       "Lo que YA se publicó no se puede borrar desde acá — eso se saca "
                       "a mano desde la red."
                  ).pack(anchor="w", padx=12, pady=(0, 6))

        caja = ttk.Frame(win)
        caja.pack(fill="both", expand=True, padx=12)
        lista = tk.Listbox(caja, height=12)
        lista.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(caja, command=lista.yview)
        lista.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")

        ESTADOS = {"pendiente": "agendado", "publicado": "ya salió",
                   "bajado": "terminado", "vencido": "venció sin salir", "error": "falló"}
        for t_ in trabajos:
            cuando = (t_.get("cuando") or "")
            redes = ", ".join({"facebook": "Facebook",
                               "instagram": "Instagram"}.get(r, r) for r in t_.get("destinos", []))
            linea = (cuando[8:10] + "/" + cuando[5:7] + " " + cuando[11:16] + "   " +
                     t_.get("nombre", "(sin nombre)") + "   ·   " + redes + "   ·   " +
                     ESTADOS.get(t_.get("estado"), t_.get("estado", "")))
            if not t_.get("_en_web"):
                linea += "   (solo redes)"
            lista.insert("end", linea)
        if not trabajos:
            lista.insert("end", "  No hay ningún posteo agendado.")

        pie = ttk.Frame(win)
        pie.pack(fill="x", padx=12, pady=10)

        def cancelar():
            sel = lista.curselection()
            if not sel or not trabajos:
                return
            t_ = trabajos[sel[0]]
            if t_.get("estado") != "pendiente":
                messagebox.showinfo(
                    "Ya no está agendado",
                    "Ese posteo está como «" + ESTADOS.get(t_.get("estado"), "") +
                    "», así que no hay nada que cancelar. Si ya salió y lo querés sacar, "
                    "hay que hacerlo desde Facebook o Instagram.", parent=win)
                return
            solo_redes = not t_.get("_en_web")
            pregunta = ("¿Cancelo el posteo agendado de «" + t_.get("nombre", "") + "»?"
                        + chr(10) + chr(10) + "No va a salir en las redes.")
            if solo_redes:
                pregunta += (chr(10) + chr(10) + "Como esta campaña no está cargada en la "
                             "web, también borro su archivo del sistema.")
            if not messagebox.askyesno("Cancelar el posteo", pregunta, parent=win):
                return
            try:
                avisos_web.cancelar_programada(t_["id"], borrar_archivo=solo_redes)
            except Exception as e:                                   # noqa: BLE001
                messagebox.showerror("No se pudo", str(e), parent=win)
                return
            win.destroy()
            self.set_status("🗑 Posteo agendado cancelado: " + t_.get("nombre", ""), ROJO)
            self.on_avisos_refrescar()

        tk.Button(pie, text="🗑  Cancelar el seleccionado", command=cancelar,
                  bg=ROJO, fg="white", font=("Segoe UI", 9, "bold"), relief="flat",
                  padx=10, pady=6, cursor="hand2").pack(side="left")
        ttk.Button(pie, text="Cerrar", command=win.destroy).pack(side="right")

    def on_avisos_limpiar(self):
        """Saca de la web las campañas que ya terminaron, con su archivo.

        El freno lo hace la fecha sola; esto es la limpieza de después. No toca
        NADA de Facebook ni de Instagram.
        """
        self.run_bg(avisos_web.vencidas, self._avisos_limpiar_confirmar,
                    busy="Buscando campañas vencidas…")

    def _avisos_limpiar_confirmar(self, nombres):
        if not nombres:
            messagebox.showinfo("Nada que limpiar",
                                "No hay ninguna campaña vencida. "
                                "(Las que no tienen fecha de fin no vencen nunca.)")
            return
        detalle = chr(10).join("   • " + n for n in nombres)
        if not messagebox.askyesno(
                "Limpiar campañas vencidas",
                "Estas campañas ya terminaron. Las saco de la web y borro su archivo:"
                + chr(10) + chr(10) + detalle + chr(10) + chr(10)
                + "Lo que se posteó en Facebook e Instagram NO se toca."):
            return
        self.run_bg(avisos_web.limpiar_vencidas, self._avisos_limpiadas,
                    busy="Limpiando las vencidas…")

    def _avisos_limpiadas(self, nombres):
        self.set_status("✅ Limpieza: " + str(len(nombres)) + " campaña(s)", "#227a22")
        messagebox.showinfo("Listo", "Saqué " + str(len(nombres)) +
                            " campaña(s) vencida(s) de la web.")
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
            estado = a.get("_estado")
            if estado == "programada":
                etiqueta += "   🕒 sale el " + (a.get("desde") or "")[8:10] + "/" + \
                            (a.get("desde") or "")[5:7]
            elif estado == "vencida":
                etiqueta += "   ⏸ vencida"
            elif a.get("hasta"):
                etiqueta += "   hasta el " + a["hasta"][8:10] + "/" + a["hasta"][5:7]
            self.avisos_list.insert("end", etiqueta)
        self._reset_aviso_edit()
        try:
            self.aviso_dir_lbl.config(text="Carpeta de la web: " + str(avisos_web.web_dir()))
        except Exception:
            self.aviso_dir_lbl.config(
                text="⚠ No encontré la carpeta de la web — usá «Cambiar carpeta…».")
        self.set_status(f"{len(data)} publicidad/es en la web.", "#227a22")

    def _reset_aviso_edit(self):
        """Nada seleccionado: botones apagados y vista previa en blanco."""
        self.aviso_edit_idx = None
        self.aviso_edit_reemplazo = None
        self.aviso_thumb.config(image="", text="(elegí una de la lista)", width=24, height=7)
        self._aviso_thumb_ref = None
        for b in (self.btn_aviso_editar, self.btn_aviso_borrar):
            b.config(state="disabled")

    def on_aviso_seleccion(self, _evt=None):
        sel = self.avisos_list.curselection()
        if not sel:
            return
        idx = sel[0]
        a = self.avisos_data[idx]
        self.aviso_edit_idx = idx
        self.btn_aviso_editar.config(state="normal")
        self.btn_aviso_borrar.config(state="normal")
        self.aviso_thumb.config(image="", text="⏳", width=24, height=7)
        self._aviso_thumb_ref = None
        if a.get("_es_video"):
            self.run_bg(lambda: self._thumb_video(a.get("_archivo")),
                        self._set_aviso_thumb, busy="Extrayendo un cuadro del video…")
        else:
            self.run_bg(lambda: self._miniatura(a.get("_archivo"), False),
                        self._set_aviso_thumb, busy="Cargando imagen…")

    @staticmethod
    def _iso_a_campos(iso):
        """«2026-09-20T08:00:00-03:00» -> («20/09/2026», «08:00»). Vacío -> («», «»)."""
        iso = (iso or "").strip()
        if len(iso) < 16:
            return "", ""
        return iso[8:10] + "/" + iso[5:7] + "/" + iso[0:4], iso[11:16]

    def on_aviso_editar(self):
        """Ventana para cambiar una publicidad ya cargada.

        Tiene los MISMOS campos que el alta —archivo, nombre, link, forma y las dos
        fechas con calendario— porque cambiar un aviso y cargarlo son la misma
        tarea vista de dos lados. Antes esto vivía apretado en un panel al costado
        y, al crecer el formulario de abajo, sus botones dejaron de dibujarse.
        """
        idx = self.aviso_edit_idx
        if idx is None:
            messagebox.showinfo("Elegí una", "Primero seleccioná una publicidad de la lista.")
            return
        actual = self.avisos_data[idx]
        nombre_orig = actual.get("nombre", "")

        win = tk.Toplevel(self.root)
        win.title("Editar: " + nombre_orig)
        win.geometry("560x420")
        win.transient(self.root)
        win.grab_set()

        estado = {"reemplazo": None}

        ttk.Label(win, text="Editar «" + nombre_orig + "»",
                  font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=14, pady=(14, 8))

        lbl_medida = ttk.Label(win, text="", foreground=NARANJA)

        f1 = ttk.Frame(win)
        f1.pack(fill="x", padx=14, pady=3)
        ttk.Label(f1, text="Imagen o video:", width=16).pack(side="left")
        v_archivo = tk.StringVar(value="(el que ya tiene)")
        ttk.Entry(f1, textvariable=v_archivo, state="readonly").pack(
            side="left", fill="x", expand=True)

        def reemplazar():
            ruta = filedialog.askopenfilename(
                parent=win, title="Elegí la imagen o el video nuevo",
                filetypes=[("Imágenes y video", "*.jpg *.jpeg *.png *.webp *.gif *.mp4 *.webm"),
                           ("Imágenes", "*.jpg *.jpeg *.png *.webp *.gif"),
                           ("Video", "*.mp4 *.webm"),
                           ("Todos", "*.*")])
            if not ruta:
                return
            estado["reemplazo"] = ruta
            v_archivo.set(Path(ruta).name)
            lbl_medida.config(text="Midiendo la pieza…")
            self.run_bg(lambda: avisos_web.medir(ruta), mostrar_medida,
                        busy="Midiendo la pieza…")

        ttk.Button(f1, text="Cambiar…", command=reemplazar).pack(side="left", padx=6)

        f2 = ttk.Frame(win)
        f2.pack(fill="x", padx=14, pady=3)
        ttk.Label(f2, text="Nombre:", width=16).pack(side="left")
        v_nombre = tk.StringVar(value=nombre_orig)
        ttk.Entry(f2, textvariable=v_nombre).pack(side="left", fill="x", expand=True)

        f3 = ttk.Frame(win)
        f3.pack(fill="x", padx=14, pady=3)
        ttk.Label(f3, text="Link:", width=16).pack(side="left")
        v_link = tk.StringVar(value=actual.get("link", "") or "")
        ttk.Entry(f3, textvariable=v_link).pack(side="left", fill="x", expand=True)

        # La forma no se pregunta: se mide la pieza, igual que en el alta. La
        # variable existe porque es lo que se guarda; arranca con la que ya tenía
        # el aviso, por si el archivo no se pudiera medir.
        v_forma = tk.StringVar(value=actual.get("forma", "ancha") or "ancha")
        lbl_medida.pack(anchor="w", padx=14, pady=(6, 0))

        df, dh = self._iso_a_campos(actual.get("desde"))
        hf, hh = self._iso_a_campos(actual.get("hasta"))
        v_df, v_dh = tk.StringVar(value=df), tk.StringVar(value=dh or "08:00")
        v_hf, v_hh = tk.StringVar(value=hf), tk.StringVar(value=hh or "23:59")

        f5 = ttk.Frame(win)
        f5.pack(fill="x", padx=14, pady=(8, 3))
        ttk.Label(f5, text="Sale el:", width=16).pack(side="left")
        ttk.Entry(f5, textvariable=v_df, width=12).pack(side="left")
        bd = ttk.Button(f5, text="📅", width=3)
        bd.config(command=lambda: calendario_tk.elegir(win, v_df, bd))
        bd.pack(side="left", padx=(2, 0))
        ttk.Label(f5, text=" a las ").pack(side="left")
        ttk.Entry(f5, textvariable=v_dh, width=7).pack(side="left")

        f6 = ttk.Frame(win)
        f6.pack(fill="x", padx=14, pady=3)
        ttk.Label(f6, text="Termina el:", width=16).pack(side="left")
        ttk.Entry(f6, textvariable=v_hf, width=12).pack(side="left")
        bh = ttk.Button(f6, text="📅", width=3)
        bh.config(command=lambda: calendario_tk.elegir(win, v_hf, bh))
        bh.pack(side="left", padx=(2, 0))
        ttk.Label(f6, text=" a las ").pack(side="left")
        ttk.Entry(f6, textvariable=v_hh, width=7).pack(side="left")

        ttk.Label(win, foreground="#777", wraplength=520, justify="left",
                  text="Dejá las fechas vacías para que el aviso quede fijo, sin fecha de "
                       "entrada ni de salida. Cambiar la imagen sube la nueva, borra la "
                       "vieja y deja el aviso en el mismo lugar de la lista.\n"
                       "La forma se calcula sola con el tamaño de la pieza; acá se puede "
                       "corregir si alguna vez no da con el hueco que querés."
                  ).pack(anchor="w", padx=14, pady=(10, 0))

        pie = ttk.Frame(win)
        pie.pack(fill="x", padx=14, pady=14)

        def mostrar_medida(m):
            """Pinta lo detectado. Chequea que la ventana siga abierta: la medición
            va en segundo plano y el usuario puede haberla cerrado antes."""
            if not win.winfo_exists():
                return
            if not m or not m.get("ancho"):
                lbl_medida.config(text="No pude medir la pieza; queda con la forma «" +
                                       v_forma.get() + "».")
                return
            v_forma.set(m["forma"])
            lbl_medida.config(
                text="Detectado: " + str(m["ancho"]) + "×" + str(m["alto"]) + " (" +
                     m["orientacion"] + ")  →  forma «" + m["forma"] + "»  ·  en redes: " +
                     avisos_web.como_sale(m))

        archivo_actual = actual.get("_archivo") or ""
        if archivo_actual:
            lbl_medida.config(text="Midiendo la pieza…")
            self.run_bg(lambda: avisos_web.medir(archivo_actual), mostrar_medida,
                        busy="Midiendo la pieza…")

        def guardar():
            nombre = v_nombre.get().strip()
            if not nombre:
                messagebox.showinfo("Falta el nombre",
                                    "La publicidad necesita un nombre.", parent=win)
                return
            try:
                desde = self._fecha_iso(v_df, v_dh, "Sale el")
                hasta = self._fecha_iso(v_hf, v_hh, "Termina el")
            except ValueError as e:
                messagebox.showerror("Fecha mal escrita", str(e), parent=win)
                return
            if desde and hasta and hasta <= desde:
                messagebox.showerror("Fechas al revés",
                                     "Termina antes de empezar.", parent=win)
                return
            win.destroy()
            self.run_bg(
                lambda: avisos_web.editar_aviso(
                    idx, nombre_orig, nombre, v_link.get().strip(),
                    nuevo_archivo=estado["reemplazo"], forma=v_forma.get(),
                    desde=desde, hasta=hasta, tocar_fechas=True),
                self._aviso_editado_ok, busy="Guardando los cambios…")

        def borrar():
            win.destroy()
            self.on_aviso_borrar()

        tk.Button(pie, text="💾  Guardar", command=guardar, bg=NARANJA, fg="white",
                  font=("Segoe UI", 9, "bold"), relief="flat", padx=12, pady=6,
                  cursor="hand2").pack(side="left")
        tk.Button(pie, text="🗑  Borrar", command=borrar, bg=ROJO, fg="white",
                  font=("Segoe UI", 9, "bold"), relief="flat", padx=12, pady=6,
                  cursor="hand2").pack(side="left", padx=(8, 0))
        ttk.Button(pie, text="Cancelar", command=win.destroy).pack(side="right")
        win.bind("<Escape>", lambda _e: win.destroy())

    def _aviso_editado_ok(self, nueva):
        self.set_status("✅ Publicidad actualizada: " + nueva.get("nombre", ""), "#227a22")
        achicado = nueva.get("_optimizado") or ""
        messagebox.showinfo(
            "Listo",
            "Los cambios se guardaron.\n\nEn 1-2 minutos se ven en la web "
            "(Vercel está publicando)."
            + (f"\n\nEl archivo se comprimió antes de subirlo: {achicado}." if achicado else ""))
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
        # Medir la pieza y acomodar la forma sola. Se puede cambiar a mano después:
        # el combo queda habilitado, esto solo pone el valor que corresponde.
        self.aviso_medida_lbl.config(text="Midiendo la pieza…")
        self.run_bg(lambda: avisos_web.medir(ruta), self._aviso_medido,
                    busy="Midiendo la pieza…")

    def _aviso_medido(self, m):
        if not m or not m.get("ancho"):
            self.aviso_medida_lbl.config(
                text="No pude medir la pieza; dejo la forma en «" +
                     self.aviso_forma_var.get() + "». Elegila a mano si no es esa.")
            return
        self.aviso_forma_var.set(m["forma"])
        self.aviso_medida_lbl.config(
            text="Detectado: " + str(m["ancho"]) + "×" + str(m["alto"]) + " (" +
                 m["orientacion"] + ")  →  forma «" + m["forma"] + "»  ·  en redes: " +
                 avisos_web.como_sale(m))

    def _fecha_iso(self, var_fecha, var_hora, etiqueta):
        """dd/mm/aaaa + hh:mm  ->  ISO con el huso de Argentina. Vacío da None.

        El huso va escrito a propósito: la nube corre en UTC y sin él una campaña
        que arranca a las 8 de la mañana arrancaría a las 5.
        """
        from datetime import datetime
        f = (var_fecha.get() or "").strip()
        h = (var_hora.get() or "").strip() or "00:00"
        if not f:
            return None
        for formato in ("%d/%m/%Y %H:%M", "%d/%m/%y %H:%M",
                        "%d-%m-%Y %H:%M", "%Y-%m-%d %H:%M"):
            try:
                d = datetime.strptime(f + " " + h, formato)
            except ValueError:
                continue
            return d.strftime("%Y-%m-%dT%H:%M:00-03:00")
        raise ValueError("No entiendo la fecha de «" + etiqueta + "»: «" + f + " " + h +
                         "». Va en dd/mm/aaaa y hh:mm.")

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
        try:
            desde = self._fecha_iso(self.aviso_desde_f, self.aviso_desde_h, "Sale el")
            hasta = self._fecha_iso(self.aviso_hasta_f, self.aviso_hasta_h, "Termina el")
        except ValueError as e:
            messagebox.showerror("Fecha mal escrita", str(e))
            return
        if desde and hasta and hasta <= desde:
            messagebox.showerror("Fechas al revés",
                                 "La campaña termina antes de empezar. Revisá las fechas.")
            return

        en_web = bool(self.aviso_web_var.get())
        redes = []
        if self.aviso_fb_var.get():
            redes.append("facebook")
        if self.aviso_ig_var.get():
            redes.append("instagram")
        if not en_web and not redes:
            messagebox.showinfo("Falta el destino",
                                "Elegí al menos uno: la web, Facebook o Instagram.")
            return
        texto = self.aviso_texto_var.get().strip()
        forma = self.aviso_forma_var.get() or "ancha"
        es_video = str(archivo).lower().endswith((".mp4", ".webm", ".mov"))

        cuando = ("ya" if not desde else "el " + self.aviso_desde_f.get().strip() +
                  " a las " + self.aviso_desde_h.get().strip())
        lineas = ["Anunciante: " + nombre, "Arranca: " + cuando]
        if hasta:
            lineas.append("Termina: el " + self.aviso_hasta_f.get().strip() +
                          " a las " + self.aviso_hasta_h.get().strip())
        else:
            lineas.append("Termina: no tiene fecha de fin")
        lineas.append("")
        lineas.append("Web: " + ("sí, forma " + forma if en_web else "no"))
        if redes:
            como = "reel + historia" if es_video else "publicación + historia"
            lineas.append("Redes: " + " y ".join(
                {"facebook": "Facebook", "instagram": "Instagram"}[r] for r in redes) +
                " (" + como + ")")
        else:
            lineas.append("Redes: no")

        if redes and not texto:
            if not messagebox.askyesno("Sin texto",
                                       "El posteo de las redes va a salir sin ningún texto. "
                                       "¿Lo dejo así?"):
                return
        if not messagebox.askyesno("Confirmar la campaña", chr(10).join(lineas)):
            return

        self.run_bg(
            lambda: avisos_web.programar(nombre, archivo, forma=forma, link=link,
                                         desde=desde, hasta=hasta, en_web=en_web,
                                         redes=redes, texto=texto),
            self._aviso_agregado_ok, busy="Cargando la campaña…")

    def _aviso_agregado_ok(self, res):
        # `programar()` devuelve {"aviso", "url", "programado"}; el alta simple, la entrada.
        entry = res.get("aviso", res) if isinstance(res, dict) else res
        trabajo = res.get("programado") if isinstance(res, dict) else None
        self.set_status("✅ Publicidad agregada: " + entry.get("nombre", ""), "#227a22")
        self.aviso_file = None
        self.aviso_file_var.set("")
        self.aviso_nombre_var.set("")
        self.aviso_link_var.set("")
        achicado = (res.get("_optimizado") if isinstance(res, dict) else "") or \
                   entry.get("_optimizado") or ""
        partes = []
        en_web = res.get("en_web", True) if isinstance(res, dict) else True
        if en_web:
            if entry.get("desde"):
                partes.append("En la web entra el " + entry["desde"][8:10] + "/" +
                              entry["desde"][5:7] + " a las " + entry["desde"][11:16] + ".")
            else:
                partes.append("Ya está en la web (tarda 1-2 minutos en verse).")
            if entry.get("hasta"):
                partes.append("Sale sola el " + entry["hasta"][8:10] + "/" +
                              entry["hasta"][5:7] + " a las " + entry["hasta"][11:16] + ".")
        else:
            partes.append("No va a la web: el archivo se subió solo para que las "
                          "redes lo puedan tomar.")
        if trabajo:
            redes = " y ".join({"facebook": "Facebook",
                                "instagram": "Instagram"}[r] for r in trabajo["destinos"])
            partes.append("El posteo en " + redes + " queda agendado para el " +
                          trabajo["cuando"][8:10] + "/" + trabajo["cuando"][5:7] +
                          " a las " + trabajo["cuando"][11:16] +
                          ". Lo hace la nube, así que sale aunque la PC esté apagada.")
        if achicado:
            partes.append("Se comprimió antes de subirla: " + achicado + ".")
        messagebox.showinfo("Listo", chr(10).join(partes))
        self.on_avisos_refrescar()

    def on_aviso_borrar(self):
        idx = self.aviso_edit_idx
        if idx is None:
            return
        a = self.avisos_data[idx]
        nombre = a.get("nombre", "")
        aviso = ("¿Seguro que querés BORRAR la publicidad «" + nombre + "»?" + chr(10) + chr(10)
                 + "Se saca de la web ya mismo (en 1-2 minutos deja de verse) y se "
                 "cancela cualquier posteo que tuviera agendado en las redes." + chr(10) + chr(10)
                 + "Lo que YA se haya publicado en Facebook o Instagram no se toca.")
        if not messagebox.askyesno("Borrar publicidad", aviso):
            return
        self.run_bg(lambda: avisos_web.borrar_aviso(idx, nombre_esperado=nombre),
                    self._aviso_borrado_ok, busy="Borrando la publicidad…")

    def _aviso_borrado_ok(self, quitado):
        self.set_status("🗑 Publicidad borrada: " + quitado.get("nombre", ""), ROJO)
        partes = ["La publicidad se sacó. En 1-2 minutos deja de verse en la web."]
        cancelados = int(quitado.get("_cancelados") or 0)
        if cancelados:
            partes.append("También cancelé " + str(cancelados) +
                          " posteo(s) que tenía agendado(s) en las redes.")
        messagebox.showinfo("Borrada", chr(10) + chr(10).join(partes))
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
