"""
Los calibradores de esta instalación, y cómo se lanza uno.

Un calibrador es una herramienta de puesta en marcha que **comparte el ejecutable de la
app**: no es un programa aparte, así que llega compilado a una planta donde no hay Python, y
recibe el config de la instalación ya resuelto en vez de elegir el suyo. La jerarquía de la
carpeta y las reglas que sigue cada uno están en `calibration/README.md`.

**`TOOLS` es lo único que edita un fork**: una fila por calibrador. De esa tabla salen el
subcomando de `main.py`, el lanzador y las carpetas que arma `make_release.py`, y `build.py`
compila el paquete entero; agregar un calibrador es escribir su carpeta y sumar una línea
acá, sin tocar la maquinaria.

Módulo puro: sin Qt, sin ConfigManager y sin importar ningún calibrador. `make_release.py`
lo lee para armar el entregable, y un calibrador puede traer matplotlib: leer la tabla no
puede costar lo que cuesta correr la herramienta. Por eso `module` es un nombre y no la
función, y el import recién se hace en `run_tool()`.
"""

import importlib
import os
import re
import sys
from dataclasses import dataclass

# El nombre es el subcomando y la carpeta: una palabra que no hay que entrecomillar en
# ningún shell, y un identificador válido para que la carpeta se importe como paquete.
_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

# Lo que no puede ir en el nombre de un archivo de Windows: el lanzador se llama así.
_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*]')

# Código de salida de un uso equivocado, el mismo que usa argparse.
USAGE_ERROR = 2


@dataclass(frozen=True)
class CalibrationTool:
    """
    Un calibrador: cómo se pide, dónde está y qué deja armado el entregable.

    `name` es el subcomando (`main.py --calibrate <name>`) y la carpeta: `calibration/<name>/`
    en el repo, `installation/calibration/<name>/` en el entregable. `module` es el módulo
    que expone `run(config, argv) -> int`, y vive en esa carpeta. `launcher` es el nombre
    del lanzador en el entregable, sin extensión; `description`, la línea que lo explica.

    `folders` son las carpetas de entrada y salida que el entregable crea vacías, como
    `(ruta relativa a la del calibrador, nota)`. La nota queda en un `LEEME.txt` adentro de
    cada una, porque quien las llena es el que calibra y lo que lee es eso: una carpeta que
    no existe, o que nadie sabe qué espera, es un error a mitad de una puesta en marcha.
    """

    name: str
    module: str
    launcher: str
    description: str
    folders: tuple = ()


# ── Lo que cambia en cada fork ──────────────────────────────────────────────
#
# Una fila por calibrador. Vacía en el template. Ejemplo de un fork que ajusta los
# umbrales de malla contra zarandeos pesados a mano:
#
#     TOOLS = (
#         CalibrationTool(
#             name="mesh",
#             module="calibration.mesh.mesh_calibrator",
#             launcher="Calibrar mallas",
#             description="Ajusta los umbrales de malla contra los zarandeos pesados.",
#             folders=(
#                 ("samples/sieve", "Los CSV del zarandeo: timestamp;class;grams.\n"),
#                 ("samples/measurements", "Los JSON del dataset de esas horas.\n"),
#                 ("out", "Lo que deja cada corrida, con el instante en el nombre.\n"),
#             ),
#         ),
#     )
TOOLS: tuple = ()


def find_tool(name: str) -> CalibrationTool | None:
    """El calibrador con ese nombre, o None si no está registrado."""
    return next((tool for tool in TOOLS if tool.name == name), None)


def run_tool(config: object, argv: list) -> int:
    """
    Corre el calibrador que nombra `argv[0]` con el resto de la línea; devuelve su código.

    `config` es el `ConfigManager` de la instalación y pasa tal cual. Un nombre que falta o
    que no está registrado no levanta: lista los que hay y devuelve `USAGE_ERROR`, porque
    lo va a leer alguien en la consola de un equipo de planta y no en un traceback.
    """
    name = argv[0] if argv else ""
    tool = find_tool(name)
    if tool is None:
        print(_describe_unknown(name), file=sys.stderr)
        return USAGE_ERROR
    return importlib.import_module(tool.module).run(config, list(argv[1:]))


def check_tools(tools: tuple) -> list:
    """
    Los problemas de una tabla de calibradores, uno por línea; vacía si está bien.

    No mira si el módulo existe ni lo importa: eso lo prueba la suite del fork, que sí
    tiene sus dependencias. Esto es lo que se puede afirmar leyendo la tabla, y lo que,
    si está mal, deja un entregable con un lanzador roto o dos que se pisan.
    """
    problems = []
    names = [tool.name for tool in tools]
    launchers = [tool.launcher.casefold() for tool in tools]
    for tool in tools:
        if not _NAME_PATTERN.match(tool.name):
            problems.append(f"«{tool.name}»: el nombre va en minúscula y snake_case, "
                            f"sin espacios")
        if names.count(tool.name) > 1:
            problems.append(f"«{tool.name}»: hay dos calibradores con ese nombre")
        if not tool.module.startswith(f"calibration.{tool.name}."):
            problems.append(f"«{tool.name}»: `{tool.module}` no está en "
                            f"calibration/{tool.name}/")
        if not tool.launcher.strip() or _INVALID_FILENAME_CHARS.search(tool.launcher):
            problems.append(f"«{tool.name}»: «{tool.launcher}» no sirve como nombre de "
                            f"archivo")
        elif launchers.count(tool.launcher.casefold()) > 1:
            problems.append(f"«{tool.name}»: otro calibrador ya usa el lanzador "
                            f"«{tool.launcher}»")
        for relative, _ in tool.folders:
            if not _is_inside(relative):
                problems.append(f"«{tool.name}»: la carpeta «{relative}» sale de "
                                f"calibration/{tool.name}/")
    return list(dict.fromkeys(problems))


def _is_inside(relative: str) -> bool:
    """Si una ruta relativa queda adentro de la carpeta contra la que se resuelve."""
    if not relative or os.path.isabs(relative) or relative.startswith(("/", "\\")):
        return False
    normalized = os.path.normpath(relative)
    return normalized != os.curdir and not normalized.startswith(os.pardir)


def _describe_unknown(name: str) -> str:
    """El mensaje para un calibrador que no está: cuál se pidió y cuáles hay."""
    asked = f"No hay un calibrador «{name}»." if name else "Falta el nombre del calibrador."
    if not TOOLS:
        return f"{asked} Esta instalación no tiene ninguno registrado."
    available = "\n".join(f"  {tool.name:<16}{tool.description}" for tool in TOOLS)
    return f"{asked} Los que hay:\n{available}\n\nUso: --calibrate <nombre> [opciones]"
