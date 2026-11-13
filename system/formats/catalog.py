"""
Las palabras de estado por nombre, para decodificarlas bit por bit.

Cada módulo de `system/formats/` es el dueño de su palabra y de lo que significa cada bit;
esto sólo los junta bajo un nombre, el mismo que declara una fila del mapa de registros en
su campo `bitfield`. Así la tabla de registros de la interfaz y la documentación del
integrador muestran qué dice cada bit sin correr un solo bit afuera de su dueño.

`list_bits()` devuelve **todos** los bits con significado —prendidos o no—, porque lo que
mira quien integra es la palabra entera: un bit de falla apagado también es un dato. Un bit
prendido que no tiene significado sale igual, sin descripción: o la palabra la armó otra
versión, o alguien escribió un número que no es una palabra de estado.

Módulo puro: sin Qt, sin Modbus, sin ConfigManager.
"""

from __future__ import annotations

from typing import NamedTuple

from system.formats import (
    camera_health, com_status, gpio_status, pipeline_status, system_status,
)

WORD_BITS = 16

# Nombre de la palabra -> {bit: qué significa prendido}.
WORDS: dict[str, dict[int, str]] = {
    "system_status":   system_status.DESCRIPTIONS,
    "com_status":      com_status.DESCRIPTIONS,
    "camera_health":   camera_health.DESCRIPTIONS,
    "pipeline_status": pipeline_status.DESCRIPTIONS,
    "gpio_inputs":     gpio_status.INPUT_BITS,
    "gpio_outputs":    gpio_status.OUTPUT_BITS,
}


class Bit(NamedTuple):
    index: int           # 0 es el menos significativo
    description: str     # '' en un bit prendido que la palabra no define
    is_set: bool


def list_bits(word_name: str, word: int) -> list[Bit] | None:
    """
    Los bits de `word` según la palabra `word_name`, del 0 al 15; None si no hay una
    palabra con ese nombre.
    """
    descriptions = WORDS.get(word_name)
    if descriptions is None:
        return None
    bits = []
    for index in range(WORD_BITS):
        is_set = bool(int(word) >> index & 1)
        description = descriptions.get(index, "")
        if description or is_set:
            bits.append(Bit(index, description, is_set))
    return bits
