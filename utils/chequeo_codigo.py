# -*- coding: utf-8 -*-
"""Busca variables que se usan por un camino donde nunca se asignaron.

Por qué existe: dos veces en cuatro días se publicó código que reventaba con
`UnboundLocalError` / `NameError` en producción, por el camino BUENO (el que no falla), y
las dos veces se enteró el usuario por un mail de «Run failed» con la nota sin salir:

  · 2026-09-18 — `ctx` en `run_publish_video`: la variable no existía en esa función.
  · 2026-09-21 — `desc` en `_corresponsal_foto_etapa1`: estaba asignada SOLO adentro del
    `except`, así que existía únicamente cuando Gemini fallaba.

`pyflakes`, que es lo que hay instalado, no ve ninguno de los dos: el primero porque el
nombre existe en otra función del módulo, y el segundo porque no mira el flujo. Este
chequeo mira las dos cosas, es puro AST (no importa ni ejecuta nada, así que no puede tener
efectos) y tarda menos de un segundo sobre todo el repo.

Uso:  python -m utils.chequeo_codigo [archivo.py ...]      (sin argumentos: todo el repo)
Devuelve 1 si encontró algo. Pensado para correr ANTES de publicar, en la nube.
"""
import ast
import builtins
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
SALTEAR = {"venv", "__pycache__", ".git", "node_modules"}


class _Funcion(ast.NodeVisitor):
    """Recorre UNA función y anota dónde se asigna y dónde se lee cada nombre local."""

    def __init__(self):
        self.asignados: set = set()          # nombres asignados en cualquier lado
        self.solo_en_except: dict = {}        # nombre → el handler donde se asigna
        self.fuera_de_except: set = set()     # nombres asignados fuera de todo `except`
        self.leidos: list = []                # (nombre, línea, dentro_de_except)
        self._en_except = 0
        self.globales: set = set()

    # No entramos a funciones anidadas (tienen su propio ámbito), pero SÍ anotamos su
    # nombre: para la función de afuera, un `def` de adentro es una asignación más.
    def visit_FunctionDef(self, nodo):
        self.asignados.add(nodo.name)
        self.fuera_de_except.add(nodo.name)

    visit_AsyncFunctionDef = visit_FunctionDef
    visit_ClassDef = visit_FunctionDef

    def visit_Lambda(self, nodo):
        pass

    # Un `import` adentro de la función también define un nombre. En este repo se usan a
    # montones para no pagar el import al arrancar (`from utils import gemini`).
    def visit_Import(self, nodo):
        for a in nodo.names:
            nombre = (a.asname or a.name).split(".")[0]
            self.asignados.add(nombre)
            (self.solo_en_except.setdefault(nombre, nodo.lineno) if self._en_except
             else self.fuera_de_except.add(nombre))

    visit_ImportFrom = visit_Import

    def visit_Global(self, nodo):
        self.globales.update(nodo.names)

    visit_Nonlocal = visit_Global

    def visit_ExceptHandler(self, nodo):
        self._en_except += 1
        # El `as e` del except se borra al salir del handler: es un caso aparte y conocido.
        if nodo.name:
            self.asignados.add(nodo.name)
            self.solo_en_except.setdefault(nodo.name, nodo.lineno)
        for hijo in nodo.body:
            self.visit(hijo)
        self._en_except -= 1

    def visit_Name(self, nodo):
        if isinstance(nodo.ctx, ast.Store):
            self.asignados.add(nodo.id)
            if self._en_except:
                self.solo_en_except.setdefault(nodo.id, nodo.lineno)
            else:
                self.fuera_de_except.add(nodo.id)
        elif isinstance(nodo.ctx, ast.Load):
            self.leidos.append((nodo.id, nodo.lineno, bool(self._en_except)))
        self.generic_visit(nodo)


def _parametros(fn) -> set:
    a = fn.args
    nombres = {p.arg for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)}
    if a.vararg:
        nombres.add(a.vararg.arg)
    if a.kwarg:
        nombres.add(a.kwarg.arg)
    return nombres


def _nombres_del_modulo(arbol) -> set:
    """Todo lo que existe a nivel de módulo: importaciones, constantes, funciones, clases."""
    fuera = set()
    for nodo in arbol.body:
        if isinstance(nodo, (ast.Import, ast.ImportFrom)):
            for a in nodo.names:
                fuera.add((a.asname or a.name).split(".")[0])
        elif isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            fuera.add(nodo.name)
        elif isinstance(nodo, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            objetivos = nodo.targets if isinstance(nodo, ast.Assign) else [nodo.target]
            for t in objetivos:
                for sub in ast.walk(t):
                    if isinstance(sub, ast.Name):
                        fuera.add(sub.id)
        elif isinstance(nodo, (ast.Try, ast.If, ast.For, ast.While, ast.With)):
            # Importaciones y constantes dentro de un try/if a nivel de módulo.
            for sub in ast.walk(nodo):
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    for a in sub.names:
                        fuera.add((a.asname or a.name).split(".")[0])
                elif isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                    fuera.add(sub.id)
    return fuera


# Lo que siempre existe: los `builtins` y los dunder que Python le pone a todo módulo.
_SIEMPRE = set(dir(builtins)) | {
    "__file__", "__name__", "__doc__", "__package__", "__spec__", "__loader__",
    "__builtins__", "__path__", "__debug__", "__class__", "__annotations__", "__dict__",
}


def _revisar_funcion(fn, de_afuera: set, hallazgos: list) -> None:
    """Revisa UNA función y después, recursivamente, las que tenga adentro.

    `de_afuera` es todo lo que la función puede ver sin asignarlo: el módulo, los
    parámetros y las variables de las funciones que la contienen (las clausuras). Sin eso,
    cada función anidada parecía usar variables inexistentes."""
    v = _Funcion()
    for hijo in fn.body:
        v.visit(hijo)
    parametros = _parametros(fn)
    conocidos = de_afuera | parametros | v.asignados | v.globales
    for nombre, linea, dentro in v.leidos:
        if nombre not in conocidos:
            hallazgos.append((linea, fn.name, nombre,
                              "no existe en ningún lado: va a reventar siempre"))
        elif (not dentro and nombre in v.solo_en_except
              and nombre not in v.fuera_de_except and nombre not in parametros
              and nombre not in de_afuera and linea > v.solo_en_except[nombre]):
            hallazgos.append((linea, fn.name, nombre,
                              "solo se asigna adentro de un «except»: por el camino "
                              "bueno no existe"))
    # Las de adentro ven todo lo de ésta.
    adentro = conocidos
    for nodo in ast.walk(fn):
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)) and nodo is not fn:
            if _padre_directo(fn, nodo):
                _revisar_funcion(nodo, adentro, hallazgos)


def _padre_directo(fn, candidata) -> bool:
    """¿`candidata` está definida DIRECTAMENTE en `fn` y no dentro de otra función suya?"""
    for nodo in ast.walk(fn):
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)) and nodo not in (fn, candidata):
            if any(sub is candidata for sub in ast.walk(nodo)):
                return False
    return True


def revisar(ruta: Path) -> list:
    """Devuelve una lista de `(línea, función, nombre, motivo)`."""
    try:
        arbol = ast.parse(ruta.read_text(encoding="utf-8"), filename=str(ruta))
    except SyntaxError as e:
        return [(e.lineno or 0, "(el archivo)", "", f"no compila: {e.msg}")]

    del_modulo = _nombres_del_modulo(arbol) | _SIEMPRE
    hallazgos: list = []
    for nodo in arbol.body:
        for fn in ([nodo] if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef))
                   else [h for h in ast.walk(nodo)
                         if isinstance(h, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and _padre_directo(nodo, h)]):
            _revisar_funcion(fn, del_modulo, hallazgos)
    return sorted(set(hallazgos))


def archivos() -> list:
    return sorted(p for p in RAIZ.rglob("*.py")
                  if not any(parte in SALTEAR for parte in p.parts))


def main(argv=None) -> int:
    rutas = [Path(a) for a in (argv or [])] or archivos()
    total = 0
    for ruta in rutas:
        for linea, funcion, nombre, motivo in revisar(ruta):
            total += 1
            print(f"{ruta.relative_to(RAIZ) if RAIZ in ruta.parents else ruta}:{linea} "
                  f"· {funcion}() · «{nombre}» {motivo}")
    print(f"\n{len(rutas)} archivo(s) revisado(s): "
          + ("TODO EN ORDEN." if not total else
             f"{total} variable(s) que pueden reventar en producción."))
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
