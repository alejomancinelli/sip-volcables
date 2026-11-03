"""
Lo que dice la planta en un instante: las entradas del GPIO y las variables del PLC.

Maquinaria genérica: reutilizable entre proyectos. Módulo puro: sin Qt, sin I/O y sin
imports del proyecto, así que se prueba con dicts.

Es el contrato entre tres lados que no se conocen: el cableado arma la foto, las
compuertas de frames la leen por frame en los hilos de captura, y el fork la compara en
`_on_signals_changed()` con las funciones de flanco de este módulo. `plc` es además la foto
que viaja con cada frame a `InferenceResult.signals`.
"""

import dataclasses
from collections.abc import Callable, Mapping
from types import MappingProxyType


@dataclasses.dataclass(frozen=True)
class FrameSignals:
    """
    Lo que dice la planta en este instante, para las compuertas de frames.

    Inmutable y entero a propósito: se arma uno nuevo cada vez que cambia una fuente y se
    reemplaza de una vez, así que un hilo de captura que lo lee ve el viejo o el nuevo,
    nunca la mitad de cada uno, sin lock. Una fuente nueva es un campo más, y las
    compuertas que no lo usan no cambian.

        gpio_inputs : canal (desde 1, como en el borne) -> 1, 0 o `READ_ERROR`
        plc         : nombre de la fila `plc` -> valor decodificado (bool con `is_bool`,
                      float con escala). Están todas desde el arranque, en su valor de
                      reposo: el 0 decodificado, que es lo que tiene el registro hasta que
                      el PLC escribe.
    """

    gpio_inputs: Mapping[int, int] = dataclasses.field(
        default_factory=lambda: MappingProxyType({}))
    plc: Mapping[str, object] = dataclasses.field(
        default_factory=lambda: MappingProxyType({}))


# `gate(camera_slot, signals) -> bool`: False corta los frames de esa cámara a ese pipeline.
FrameGate = Callable[[str, FrameSignals], bool]


# ── Flancos ─────────────────────────────────────────────────────────────────────
#
# Comparan la foto de antes con la de ahora, así un fork que reacciona a un evento —un
# disparo del PLC, un sensor— no escribe su propia detección.

def gpio_rose(previous: FrameSignals, current: FrameSignals, channel: int) -> bool:
    """
    Flanco de subida en una entrada (canal desde 1, como en el borne): de 0 a 1.

    Sólo de 0 a 1: la primera lectura del arranque no es un flanco, y un `READ_ERROR` en el
    medio tampoco —no se sabe qué pasó en la línea mientras no se la pudo leer—.
    """
    return previous.gpio_inputs.get(channel) == 0 and current.gpio_inputs.get(channel) == 1


def gpio_fell(previous: FrameSignals, current: FrameSignals, channel: int) -> bool:
    """Flanco de bajada en una entrada: de 1 a 0, con el mismo criterio que `gpio_rose`."""
    return previous.gpio_inputs.get(channel) == 1 and current.gpio_inputs.get(channel) == 0


def plc_rose(previous: FrameSignals, current: FrameSignals, name: str) -> bool:
    """Una variable del PLC que pasa de 0 o False a otra cosa: un ON/OFF que se prende."""
    return not previous.plc.get(name) and bool(current.plc.get(name))


def plc_fell(previous: FrameSignals, current: FrameSignals, name: str) -> bool:
    """Una variable del PLC que vuelve a 0 o False."""
    return bool(previous.plc.get(name)) and not current.plc.get(name)


def plc_changed(previous: FrameSignals, current: FrameSignals, name: str) -> bool:
    """
    Una variable del PLC que cambió de valor, en cualquier sentido: es cómo se lee un
    contador de disparo, que el PLC incrementa cada vez que quiere un evento.
    """
    return previous.plc.get(name) != current.plc.get(name)
