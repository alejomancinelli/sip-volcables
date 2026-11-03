"""
Prueba manual: el servidor Modbus del template solo, con filas que escribe el PLC.

Uso — con el intérprete del venv, y el PLC en otra terminal (`plc_client.py`) o un poller:

    Windows:  .venv\\Scripts\\python.exe manual_test\\modbus_write\\writable_server.py
    Linux:    .venv/bin/python manual_test/modbus_write/writable_server.py

Levanta el `SharedModbusServer` de verdad sobre el `register_map.yaml` de al lado, cargado
con `load_registers()`, sin cámaras ni inferencia: es la prueba del protocolo, rápida y sin
ventana. La app entera, con compuertas y mediciones a pedido, es `app_with_plc.py`; los dos
toman el mismo puerto, así que se corre uno por vez.

    - **Umbrales** (`producer: config`): el tick los publica desde el `config.yaml` de al
      lado con `SCHEMA.mirror_batch()`, para que el PLC o un HMI los lean. El PLC no los
      escribe: una escritura ahí da 0x02.
    - **Variables** (`producer: plc`): se drenan con el `PlcVariables` de la app, así que
      cada cambio deja la misma línea `[Modbus] PLC: nombre: viejo → nuevo`. El contador `measure_seq` es una variable más: acá no dispara nada.

Qué mirar en esta terminal mientras escribe el PLC:
    - Una línea `[Modbus] PLC` por cada escritura que cambia un valor, a lo sumo una cada
      10 s por variable —la siguiente dice cuántas calló—; reescribir el mismo, ninguna.
    - Un `WARNING [Modbus] Escritura del PLC rechazada` por cada rechazo, con la dirección,
      el valor y el motivo, y ninguna línea `[Modbus] PLC` para ese registro: si aparece una, el
      servidor aplicó algo que tenía que rechazar con 0x02 o 0x03. El mismo rechazo se
      repite a lo sumo una vez por minuto, con cuántos se callaron en el medio.
    - El caso del unit id equivocado deja un `requested device id does not exist: 2`
      seguido de las últimas tramas en hexa: es el log de error de pymodbus, que les
      agrega el volcado. Es el mismo que da el servidor de la app, no una falla.
"""

from __future__ import annotations

import threading
import time

from write_map import CONFIG_PATH, MEASURE_COUNTER, load_schema

from system.config_manager import ConfigManager
from system.modbus.plc_variables import DRAIN_INTERVAL_MS, PlcVariables
from system.modbus.schema import RegisterSchema, to_plc_address, write_range
from system.modbus.server import STATUS_ACTIVE, STATUS_ERROR, SharedModbusServer

_DRAIN_INTERVAL_S = DRAIN_INTERVAL_MS / 1000    # el QTimer del cableado
_TICK_INTERVAL_S = 1.0          # el ciclo de registros de la app
_STARTUP_TIMEOUT_S = 5.0        # el servidor se declara activo a los 2 s del bind


class _Equipment:
    """
    El lado de la app: drena las escrituras del PLC con el `PlcVariables` de la app y
    escribe lo suyo.

    Un solo dueño por fila: las filas `plc` las escribe sólo el PLC, y el tick nunca las
    toca —`SCHEMA.encode()` se negaría—; los umbrales los escribe sólo la app.
    """

    def __init__(self, config: ConfigManager, schema: RegisterSchema):
        self._config = config
        self._schema = schema
        self.variables = PlcVariables(schema)
        self.server: SharedModbusServer | None = None
        self._heartbeat = 0
        # Cada variable en su reposo, como `FrameSignals.plc` en la app.
        self._plc = self.variables.rest_values()

    def publish_thresholds(self):
        """Antes de levantar el servidor: el PLC nunca lee un 0 que no es el valor."""
        self.server.update_block(self._schema.mirror_batch(self._config))

    def drain(self):
        """Lo que el PLC escribió desde el último drenado; cada cambio lo loguea `drain()`."""
        self._plc = self.variables.drain(self._plc) or self._plc

    def tick(self):
        """Latido y umbrales publicados; nunca una fila del PLC."""
        self._heartbeat = (self._heartbeat + 1) & 0xFFFF
        block = self._schema.mirror_batch(self._config)
        block.update(self._schema.encode_batch({"heartbeat": self._heartbeat}))
        self.server.update_block(block)


def _print_map(schema: RegisterSchema):
    """Lo que escribe el PLC, con su rango, y los umbrales que sólo lee."""
    print("Filas que escribe el PLC:")
    for reg in sorted(schema.plc_regs(), key=lambda r: r.addr):
        low, high = write_range(reg)
        bounds = f"{low:g}-{high:g}{f' {reg.unit}' if reg.unit else ''}"
        if reg.scale != 1:
            bounds += f" (x{reg.scale})"
        kind = "contador" if reg.name == MEASURE_COUNTER else "variable"
        print(f"  {to_plc_address(reg.addr)}  {reg.name:38s} {bounds:22s} {kind}")
    print("Umbrales que publica el equipo (sólo lectura):")
    for reg in schema.config_regs():
        scale = f" (x{reg.scale})" if reg.scale != 1 else ""
        print(f"  {to_plc_address(reg.addr)}  {reg.name:38s} ← {reg.config_key}{scale}")


def _wait_until_serving(server: SharedModbusServer) -> bool:
    """Hasta que el TCP queda activo o en error; arranca en `disabled` hasta que `run()` lo toma."""
    deadline = time.monotonic() + _STARTUP_TIMEOUT_S
    while time.monotonic() < deadline and server.tcp_status not in (STATUS_ACTIVE, STATUS_ERROR):
        time.sleep(_DRAIN_INTERVAL_S)
    return server.tcp_status == STATUS_ACTIVE


def main() -> int:
    schema, _ = load_schema()
    config = ConfigManager(str(CONFIG_PATH))
    equipment = _Equipment(config, schema)
    server = SharedModbusServer(
        config, max_addr=schema.max_addr(), write_ranges=schema.write_ranges(),
        on_write=equipment.variables.on_write,
    )
    equipment.server = server
    equipment.publish_thresholds()

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    if not _wait_until_serving(server):
        print(f"El servidor TCP no levantó (status: {server.tcp_status}). Ver el log de arriba.")
        return 1

    _print_map(schema)
    print(f"Sirviendo en {config.get('modbus.tcp.host')}:{config.get('modbus.tcp.port')}, "
          f"unit id {server.slave_id}, registros 1-{server.register_count}. "
          f"Ctrl+C para cortar.\n")

    next_tick = time.monotonic()
    try:
        while thread.is_alive():
            equipment.drain()
            if time.monotonic() >= next_tick:
                equipment.tick()
                next_tick += _TICK_INTERVAL_S
            time.sleep(_DRAIN_INTERVAL_S)
    except KeyboardInterrupt:
        print("\nCortando...")
    finally:
        server.stop()
        thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
