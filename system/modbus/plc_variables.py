"""
Las variables que escribe el PLC: de las escrituras crudas del servidor a valores por nombre.

Maquinaria genérica: reutilizable entre proyectos. Sin Qt: el ritmo lo pone quien cablea,
que llama a `drain()` cada `DRAIN_INTERVAL_MS`.

Las escrituras entran **por evento**: el servidor llama a `on_write()` desde su hilo, bajo
el lock con que escribe, y acá se encolan. Comparar registros cada tanto perdería dos
escrituras que caen en el mismo período. `drain()` las aplica en orden de llegada y devuelve
las variables nuevas, enteras, sólo si alguna cambió: lo escrito dentro de un drenado llega
como un solo cambio, con el valor final, y reescribir el mismo valor —el PLC escribe sus
variables de forma cíclica— no es un cambio.

Para la maquinaria todas las filas `plc` son variables. Un disparo es una variable que el
fork lee como evento —un contador que cambia—; acá no se distingue.

Cada cambio deja una línea en el log, a lo sumo una por variable cada `_LOG_PERIOD_S`, y la
siguiente dice cuántos cambios se callaron: una analógica que el PLC reescribe en cada ciclo
cambia en casi todos los drenados, y sin techo rotaría el log en horas.

    variables = PlcVariables(SCHEMA)
    server = SharedModbusServer(config, write_ranges=SCHEMA.write_ranges(),
                                on_write=variables.on_write)
    plc = variables.rest_values()
    ...
    plc = variables.drain(plc) or plc       # cada DRAIN_INTERVAL_MS
"""

import queue
import time
from collections.abc import Callable, Mapping
from types import MappingProxyType

from system.logger import logger
from system.modbus.schema import Reg, RegisterSchema

DRAIN_INTERVAL_MS = 100     # cada cuánto se aplican; docs/plc_commands.md se lo promete al PLC
_LOG_PERIOD_S = 10.0        # techo de las líneas de log, por variable


class PlcVariables:
    """
    Las filas `plc` de un mapa, la cola de escrituras y el log de sus cambios.

    `on_write()` es thread-safe; `drain()` se llama siempre desde el mismo hilo.
    """

    def __init__(self, schema: RegisterSchema, clock: Callable[[], float] = time.monotonic):
        self._schema = schema
        self._by_addr: dict[int, Reg] = {reg.addr: reg for reg in schema.plc_regs()}
        self._writes: queue.SimpleQueue[dict[int, int]] = queue.SimpleQueue()
        self._clock = clock
        self._logged_at: dict[str, float] = {}   # nombre -> última línea de log
        self._unlogged: dict[str, int] = {}      # nombre -> cambios callados desde esa

    @property
    def addrs(self) -> frozenset[int]:
        """Las direcciones que escribe el PLC. Vacío: no hay nada que drenar."""
        return frozenset(self._by_addr)

    def rest_values(self) -> Mapping[str, object]:
        """Cada variable en su reposo: el 0 decodificado, lo que tiene el registro al arrancar."""
        return MappingProxyType({reg.name: self._schema.decode(reg.name, 0)
                                 for reg in self._by_addr.values()})

    def on_write(self, writes: dict[int, int]):
        """Para el servidor: `{addr base-1: crudo}` de una petición ya validada."""
        self._writes.put(writes)

    def drain(self, current: Mapping[str, object]) -> Mapping[str, object] | None:
        """
        Aplica sobre `current` lo escrito desde el drenado anterior, en orden de llegada.

        Devuelve las variables nuevas —una copia: `current` no se modifica— o None si ninguna
        cambió. Una dirección que no es una fila `plc` se ignora: el servidor sólo acepta
        esas, pero quien lo cablea podría pasarle otro rango.
        """
        if self._writes.empty():     # el caso de casi todos los drenados
            return None
        plc = dict(current)
        changed = False
        while True:
            try:
                writes = self._writes.get_nowait()
            except queue.Empty:
                break
            for addr, raw in writes.items():
                reg = self._by_addr.get(addr)
                if reg is None:
                    continue
                value = self._schema.decode(reg.name, raw)
                if plc.get(reg.name) == value:
                    continue
                self._log_change(reg, plc.get(reg.name), value)
                plc[reg.name] = value
                changed = True
        return MappingProxyType(plc) if changed else None

    def _log_change(self, reg: Reg, old: object, new: object):
        """Una línea por cambio, con techo por variable; la siguiente dice cuántos calló."""
        now_s = self._clock()
        last_s = self._logged_at.get(reg.name)
        if last_s is not None and now_s - last_s < _LOG_PERIOD_S:
            self._unlogged[reg.name] = self._unlogged.get(reg.name, 0) + 1
            return
        self._logged_at[reg.name] = now_s
        unlogged = self._unlogged.pop(reg.name, 0)
        unit = f" {reg.unit}" if reg.unit else ""
        note = f" (+{unlogged} cambios sin loguear)" if unlogged else ""
        logger.info(f"[Modbus] PLC: {reg.name}: {old} → {new}{unit}{note}")
