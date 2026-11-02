"""
El mapa de la prueba de escritura, cargado con la maquinaria del template.

No es una prueba: es lo que `writable_server.py`, `plc_client.py` y `app_with_plc.py`
necesitan igual. El mapa se lee con `load_registers()` —así que si no carga, el esquema está
roto— y se devuelve con su `RegisterSchema`, que es el que codifica, decodifica y valida.
"""

from __future__ import annotations

import sys
from pathlib import Path

# La raíz del repo es el primer ancestro que contiene `system/`, como en manual_test/modbus/.
_REPO_ROOT = next(
    (p for p in Path(__file__).resolve().parents if (p / "system").is_dir()),
    None,
)
if _REPO_ROOT is None:
    raise SystemExit("No se encontró la raíz del repo: ningún directorio padre tiene system/.")
sys.path.insert(0, str(_REPO_ROOT))

from system.modbus.schema import Reg, RegisterSchema, load_registers  # noqa: E402

MAP_PATH = Path(__file__).resolve().parent / "register_map.yaml"
CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

# El contador que la prueba lee como evento: cada número nuevo pide una medición. Para la
# maquinaria es una variable más; el 0 es «nunca disparó».
MEASURE_COUNTER = "measure_seq"


def load_schema() -> tuple[RegisterSchema, dict[str, Reg]]:
    """El esquema del mapa de al lado y sus filas por nombre."""
    registers = load_registers(str(MAP_PATH))
    return RegisterSchema(registers), {reg.name: reg for reg in registers}
