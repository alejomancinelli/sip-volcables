"""
Exporta el mapa de registros a los formatos que lee una persona.

El mapa se edita en `register_map.yaml` y se lee de ahí; esto genera las copias
para quien no abre el repo:

  - `docs/modbus_map.md`  — tabla para leer y para pegar en una puesta en marcha
  - `docs/modbus_map.csv` — la misma tabla para abrir en Excel y mandarla; es también
    lo que copia el botón «Copiar CSV» de la tabla de registros (`build_csv()`)

Las dos son **generadas**: se regeneran cuando cambia el mapa y no se editan a
mano, así que no hay dos versiones del mapa que puedan discrepar.

    .venv\\Scripts\\python.exe -m system.modbus.export_map
"""

from __future__ import annotations

import csv
import io
import os

from system.modbus.registers import MAP_PATH, SCHEMA
from system.modbus.schema import PRODUCER_PLC, Reg, to_plc_address, write_range
from system.paths import PROJECT_ROOT

DOCS_DIR = os.path.join(PROJECT_ROOT, "docs")
MARKDOWN_PATH = os.path.join(DOCS_DIR, "modbus_map.md")
CSV_PATH = os.path.join(DOCS_DIR, "modbus_map.csv")

_COLUMNS = ("Registro", "PLC", "Nombre", "Descripción", "Unidad", "Escala", "Acceso",
            "Rango", "Origen")

_ACCESS_READ = "lectura"
_ACCESS_WRITE = "escribe el PLC"

_GENERATED_NOTE = (
    "Generado por `python -m system.modbus.export_map` desde "
    "`system/modbus/register_map.yaml`. No editar a mano: los cambios se hacen en "
    "el YAML y se vuelve a generar."
)


def _range_text(reg: Reg) -> str:
    """
    Lo que el PLC puede escribir en la fila, en la unidad física; '-' si no la escribe.

    Va en la unidad física y no cruda, como `min_value` / `max_value` en el YAML: la escala
    tiene su propia columna, y el integrador la aplica igual que al leer.
    """
    if reg.producer != PRODUCER_PLC:
        return "-"
    low, high = write_range(reg)
    return f"{low:g}-{high:g}"


def _row(reg: Reg) -> tuple[str, ...]:
    """Una fila de la tabla, con todo ya como texto."""
    return (
        str(reg.addr),
        to_plc_address(reg.addr),        # el número con el que el PLC lo direcciona
        reg.name,
        reg.desc,
        reg.unit or "-",
        f"x{reg.scale}" if reg.scale != 1 else "-",
        _ACCESS_WRITE if reg.producer == PRODUCER_PLC else _ACCESS_READ,
        _range_text(reg),
        reg.producer,
    )


def build_markdown() -> str:
    """Arma el documento en Markdown, ordenado por dirección."""
    lines = [
        "# Mapa de registros Modbus",
        "",
        _GENERATED_NOTE,
        "",
        "Holding registers, direccionamiento base-1: el registro N es el que el PLC lee "
        "como 4000N. Todo se lee con FC03. Las filas con acceso «escribe el PLC» se "
        "escriben con FC06 o FC16, dentro de su rango: fuera de rango la respuesta es la "
        "excepción 0x03, y sobre cualquier otro registro la 0x02. Cómo y cuándo escribir "
        "está en `docs/plc_commands.md`.",
        "",
        "Los valores con escala viajan multiplicados por ella: un registro con "
        "escala x10 y valor 415 son 41,5 en su unidad. El rango está en esa misma unidad.",
        "",
        "| " + " | ".join(_COLUMNS) + " |",
        "|" + "|".join("---" for _ in _COLUMNS) + "|",
    ]
    for reg in sorted(SCHEMA.registers, key=lambda r: r.addr):
        lines.append("| " + " | ".join(_row(reg)) + " |")
    lines.append("")
    return "\n".join(lines)


def build_csv() -> str:
    """
    La tabla en CSV, separada por `;` —lo que Excel espera con la configuración regional de
    acá—. Es también lo que copia el botón de la tabla de registros, así que el
    portapapeles y el archivo no pueden discrepar.
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow(_COLUMNS)
    for reg in sorted(SCHEMA.registers, key=lambda r: r.addr):
        writer.writerow(_row(reg))
    return buffer.getvalue()


def write_exports() -> list[str]:
    """Escribe las dos copias y devuelve las rutas generadas."""
    os.makedirs(DOCS_DIR, exist_ok=True)

    with open(MARKDOWN_PATH, "w", encoding="utf-8") as f:
        f.write(build_markdown())

    # utf-8-sig: sin BOM, Excel abre las tildes como mojibake.
    with open(CSV_PATH, "w", encoding="utf-8-sig", newline="") as f:
        f.write(build_csv())

    return [MARKDOWN_PATH, CSV_PATH]


if __name__ == "__main__":
    print(f"Mapa: {MAP_PATH} ({len(SCHEMA.registers)} registros)")
    for path in write_exports():
        print(f"  -> {os.path.relpath(path, PROJECT_ROOT)}")
