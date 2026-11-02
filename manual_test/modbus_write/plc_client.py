"""
Prueba manual: el PLC escribe. Corre cada caso del plan y dice cuál pasó.

Uso — con `writable_server.py` o con `app_with_plc.py` corriendo en otra terminal:

    Windows:  .venv\\Scripts\\python.exe manual_test\\modbus_write\\plc_client.py [--app]
    Linux:    .venv/bin/python manual_test/modbus_write/plc_client.py [--host H] [--port P]

Hace de PLC con el cliente de pymodbus: lee los umbrales que publica el equipo y escribe
variables y el contador de disparo, y en cada caso confirma lo que el PLC vería —el eco, la
excepción, el registro releído—. Termina con 0 si pasaron todos y con 1 si alguno no. Al
final deja las variables en 0, que es lo de reposo; el contador no, porque volver a 0 no es
un disparo.

Con `--app`, contra `app_with_plc.py`, además comprueba que un número nuevo en el contador
pida una medición: aparece un JSON nuevo en el dataset con ese número en `signals`.

Las direcciones salen del `register_map.yaml` de al lado por nombre, y los valores se
codifican con su `RegisterSchema`: ninguna dirección ni escala se escribe acá.

Qué mirar, además del resumen:
    - En la terminal del servidor, una línea por escritura aplicada y ninguna por las que
      el cliente espera rechazadas.
    - `variable_survives_tick`: si falla, el tick del equipo escribió una fila que es sólo
      del PLC —dos escritores sobre el mismo registro, P5—.
    - `fc16_never_writes_half`: si falla, el servidor escribió la primera mitad de un FC16
      que se metía en una fila no escribible (P2).
    - `variable_write_echoes`: si da excepción 2 pero el registro releído tiene el valor,
      el eco del FC06 se está validando como lectura (P15).
    - `threshold_is_read_only`: el PLC lee los umbrales del config pero no los escribe.
    - Con un poller (QModMaster, Modbus Poll) se puede repetir cualquier caso a mano: el
      PLC numera 40101 lo que pymodbus pide como `address=100`.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable
from pathlib import Path

import yaml
from write_map import CONFIG_PATH, MEASURE_COUNTER, load_schema

from pymodbus.client import ModbusTcpClient
from pymodbus.constants import ExcCodes

from system.modbus.schema import Reg, RegisterSchema, to_plc_address, to_uint16

_MEASURE_TIMEOUT_S = 15.0       # techo para ver el JSON de una medición pedida
_TICK_TIMEOUT_S = 3.0           # techo para ver pasar un tick de 1 s
_POLL_INTERVAL_S = 0.05

_WRONG_UNIT_ID = 2

# Donde guarda el dataset `app_with_plc.py`: `system.paths.dataset` del config de al lado.
DEFAULT_DATASET_DIR = Path(__file__).resolve().parent / "data" / "dataset"


class _Plc:
    """El PLC: lee y escribe por nombre de fila, en direcciones de PDU por debajo."""

    def __init__(self, client: ModbusTcpClient, schema: RegisterSchema,
                 rows: dict[str, Reg], unit_id: int, dataset_dir: Path = DEFAULT_DATASET_DIR):
        self._client = client
        self.schema = schema
        self.rows = rows
        self._unit_id = unit_id
        self.dataset_dir = dataset_dir    # dónde guarda la app lo que mide

    def read(self, name: str, count: int = 1) -> list[int]:
        response = self._client.read_holding_registers(
            self.rows[name].addr - 1, count=count, device_id=self._unit_id
        )
        if response.isError():
            raise RuntimeError(f"FC03 de {name} dio {response}")
        return list(response.registers)

    def write(self, name: str, raw: int, unit_id: int | None = None):
        """FC06. Devuelve la respuesta tal cual: la excepción también es un resultado."""
        return self.write_at(self.rows[name].addr, raw, unit_id)

    def write_at(self, addr: int, raw: int, unit_id: int | None = None):
        """FC06 sobre una dirección base-1, la declare o no una fila."""
        return self._client.write_register(addr - 1, raw, device_id=unit_id or self._unit_id)

    def write_many(self, name: str, raws: list[int]):
        """FC16 desde la fila `name` hacia adelante, crucen o no otras filas."""
        return self._client.write_registers(self.rows[name].addr - 1, raws,
                                            device_id=self._unit_id)

    def encode(self, name: str, value: float) -> int:
        """
        El crudo que escribe el PLC para un valor físico, con la escala de la fila.

        `SCHEMA.encode()` se niega con una fila `plc` —del lado del equipo nadie las
        escribe—, así que el PLC escala por su cuenta, como lo hará el de verdad.
        """
        reg = self.rows[name]
        return (1 if value else 0) if reg.is_bool else to_uint16(value, reg.scale)

    def raw_max(self, name: str) -> int:
        """El crudo más alto que acepta una fila escribible."""
        return self.schema.write_ranges()[self.rows[name].addr][1]

    def another_value(self, name: str, step: float) -> float:
        """Un valor físico distinto del actual y dentro del rango de la fila."""
        current = self.schema.decode(name, self.read(name)[0])
        candidate = current + step
        return candidate if self.encode(name, candidate) <= self.raw_max(name) \
            else current - step

    def wait_ticks(self, ticks: int) -> bool:
        """Espera a que el latido cambie `ticks` veces; False si no cambió a tiempo."""
        for _ in range(ticks):
            start = self.read("heartbeat")[0]
            if not _wait_until(lambda s=start: self.read("heartbeat")[0] != s, _TICK_TIMEOUT_S):
                return False
        return True


def _wait_until(condition: Callable[[], bool], timeout_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(_POLL_INTERVAL_S)
    return condition()


def _exception_of(response) -> int | None:
    """El código de excepción Modbus, o None si la escritura se aceptó."""
    return response.exception_code if response.isError() else None


def _dig(config: dict, key_path: str) -> object:
    """El valor de una clave punteada en el config leído con yaml."""
    value: object = config
    for part in key_path.split("."):
        value = value[part]
    return value


def _next_seq(seq: int) -> int:
    """El PLC cuenta 1-65535 y vuelve a 1: el 0 es «nunca disparó»."""
    return seq % 0xFFFF + 1


# ── Casos ────────────────────────────────────────────────────────────────────
# Cada uno devuelve (pasó, detalle). Los nombres van al resumen.

def thresholds_are_published(plc: _Plc) -> tuple[bool, str]:
    """Lo que el HMI mostraría: cada umbral del config, escalado, en su registro."""
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = yaml.safe_load(f)
    seen = []
    for reg in plc.schema.config_regs():
        expected = plc.schema.encode(reg.name, _dig(config, reg.config_key))
        actual = plc.read(reg.name)[0]
        seen.append(f"{to_plc_address(reg.addr)}={actual}")
        if actual != expected:
            return False, f"{reg.name}: publicado {actual}, el config dice {expected}"
    return True, ", ".join(seen)


def threshold_is_read_only(plc: _Plc) -> tuple[bool, str]:
    name = "max_load_y_px_camera_1"
    before = plc.read(name)[0]
    code = _exception_of(plc.write(name, before + 1))
    after = plc.read(name)[0]
    return code == ExcCodes.ILLEGAL_ADDRESS and before == after, \
        f"excepción {code}, registro {before} → {after}"


def variable_write_echoes(plc: _Plc) -> tuple[bool, str]:
    """Si da excepción 2 pero el releído tiene el valor, el eco se valida como lectura (P15)."""
    name = "product_code"
    raw = plc.encode(name, plc.another_value(name, 10))
    response = plc.write(name, raw)
    reread = plc.read(name)[0]
    if response.isError():
        return False, f"FC06 de {raw} dio excepción {response.exception_code}, releído {reread}"
    echoed, = response.registers
    return echoed == reread == raw, f"eco {echoed}, releído {reread}"


def variable_survives_tick(plc: _Plc) -> tuple[bool, str]:
    """Si falla, el tick del equipo escribió una fila que es sólo del PLC (P5)."""
    name = "product_code"
    raw = plc.encode(name, plc.another_value(name, 7))
    if plc.write(name, raw).isError():
        return False, "el FC06 se rechazó"
    if not plc.wait_ticks(2):
        return False, "el latido no se movió: ¿está corriendo el servidor?"
    reread = plc.read(name)[0]
    return reread == raw, f"escrito {raw}, releído {reread} dos ticks después"


def scaled_variable_keeps_decimals(plc: _Plc) -> tuple[bool, str]:
    name = "belt_speed_mps"
    value = round(plc.another_value(name, 0.25), 2)
    raw = plc.encode(name, value)
    if plc.write(name, raw).isError():
        return False, f"FC06 de {value} rechazado"
    reread = plc.read(name)[0]
    return reread == raw, f"{value} {plc.rows[name].unit} → crudo {raw}, releído {reread}"


def out_of_range_is_illegal_value(plc: _Plc) -> tuple[bool, str]:
    name = "belt_speed_mps"
    before = plc.read(name)[0]
    code = _exception_of(plc.write(name, plc.raw_max(name) + 1))
    after = plc.read(name)[0]
    return code == ExcCodes.ILLEGAL_VALUE and before == after, \
        f"excepción {code}, registro {before} → {after}"


def bool_out_of_range_is_illegal_value(plc: _Plc) -> tuple[bool, str]:
    code = _exception_of(plc.write("event_active", 2))
    return code == ExcCodes.ILLEGAL_VALUE, f"excepción {code}"


def read_only_row_is_illegal_address(plc: _Plc) -> tuple[bool, str]:
    code = _exception_of(plc.write("heartbeat", 1))
    return code == ExcCodes.ILLEGAL_ADDRESS, f"excepción {code}"


def undeclared_register_is_illegal_address(plc: _Plc) -> tuple[bool, str]:
    # El hueco que sigue al contador: lo pide por la fila de al lado, sin literal.
    counter = plc.rows[MEASURE_COUNTER]
    declared = {reg.addr for reg in plc.rows.values()}
    if counter.addr + 1 in declared:
        return False, "el mapa ya no tiene un hueco después del contador"
    code = _exception_of(plc.write_at(counter.addr + 1, 1))
    return code == ExcCodes.ILLEGAL_ADDRESS, \
        f"{to_plc_address(counter.addr + 1)}: excepción {code}"


def fc16_writes_variables(plc: _Plc) -> tuple[bool, str]:
    values = [1, 42]        # event_active, product_code
    if plc.write_many("event_active", values).isError():
        return False, "FC16 rechazado"
    reread = plc.read("event_active", count=2)
    return reread == values, f"escrito {values}, releído {reread}"


def fc16_never_writes_half(plc: _Plc) -> tuple[bool, str]:
    before = plc.read("event_active", count=2)
    # Tres registros desde event_active: el tercero cae en el hueco de después de
    # product_code, así que la petición entera tiene que rechazarse.
    attempt = [1 - before[0], before[1] + 1, 7]
    code = _exception_of(plc.write_many("event_active", attempt))
    after = plc.read("event_active", count=2)
    return code == ExcCodes.ILLEGAL_ADDRESS and after == before, \
        f"excepción {code}, {before} → {after}"


def wrong_unit_id_is_rejected(plc: _Plc) -> tuple[bool, str]:
    code = _exception_of(plc.write("event_active", 1, unit_id=_WRONG_UNIT_ID))
    return code == ExcCodes.GATEWAY_NO_RESPONSE, f"unit id {_WRONG_UNIT_ID}: excepción {code}"


def counter_takes_the_next_number(plc: _Plc) -> tuple[bool, str]:
    """Para la maquinaria el contador es una variable más: se escribe y se relee."""
    seq = _next_seq(plc.read(MEASURE_COUNTER)[0])
    response = plc.write(MEASURE_COUNTER, seq)
    reread = plc.read(MEASURE_COUNTER)[0]
    return not response.isError() and reread == seq, f"escrito {seq}, releído {reread}"


def counter_requests_a_measurement(plc: _Plc) -> tuple[bool, str]:
    """
    Sólo con `--app`: un número nuevo pide una ronda, y la ronda se guarda en el dataset
    —`image_collector.mode: on_demand`— con el número en `signals`, que es lo que la
    empareja con la referencia del PLC.
    """
    seq = _next_seq(plc.read(MEASURE_COUNTER)[0])
    before = set(plc.dataset_dir.rglob("*.json"))
    if plc.write(MEASURE_COUNTER, seq).isError():
        return False, "el FC06 del contador se rechazó"
    found: list[Path] = []

    def measured() -> bool:
        for path in set(plc.dataset_dir.rglob("*.json")) - before:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved.get("inference", {}).get("signals", {}).get(MEASURE_COUNTER) == seq:
                found.append(path)
                return True
        return False

    if not _wait_until(measured, _MEASURE_TIMEOUT_S):
        return False, f"ningún JSON nuevo con {MEASURE_COUNTER} = {seq} en {plc.dataset_dir}"
    return True, f"disparo {seq} → {found[0].name}"


_CASES = (
    thresholds_are_published,
    threshold_is_read_only,
    variable_write_echoes,
    variable_survives_tick,
    scaled_variable_keeps_decimals,
    out_of_range_is_illegal_value,
    bool_out_of_range_is_illegal_value,
    read_only_row_is_illegal_address,
    undeclared_register_is_illegal_address,
    fc16_writes_variables,
    fc16_never_writes_half,
    wrong_unit_id_is_rejected,
    counter_takes_the_next_number,
)

# Los que necesitan la app entera: sin cámaras ni inferencia no hay medición que guardar.
_APP_CASES = (counter_requests_a_measurement,)


def _rest_variables(plc: _Plc):
    """Deja las variables en 0, lo de reposo, salvo el contador: volver a 0 no es un disparo."""
    for reg in plc.schema.plc_regs():
        if reg.name != MEASURE_COUNTER:
            plc.write(reg.name, 0)


def main() -> int:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        modbus = yaml.safe_load(f)["modbus"]
    parser = argparse.ArgumentParser(description="El PLC escribe: umbrales, variables y disparos.")
    parser.add_argument("--host", default=modbus["tcp"]["host"])
    parser.add_argument("--port", type=int, default=modbus["tcp"]["port"])
    parser.add_argument("--unit", type=int, default=modbus.get("slave_id", 1))
    parser.add_argument("--app", action="store_true",
                        help="contra app_with_plc.py: también que el contador pida una medición")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_DIR,
                        help="dónde guarda la app el dataset, para el caso de --app")
    args = parser.parse_args()
    cases = list(_CASES) + (list(_APP_CASES) if args.app else [])

    schema, rows = load_schema()

    client = ModbusTcpClient(args.host, port=args.port, timeout=2)
    if not client.connect():
        print(f"No hay nadie en {args.host}:{args.port}. ¿Está corriendo writable_server.py?")
        return 1

    plc = _Plc(client, schema, rows, args.unit, args.dataset)
    failures = 0
    try:
        for case in cases:
            try:
                passed, detail = case(plc)
            except Exception as e:    # un caso que revienta es un caso que falla, no la corrida
                passed, detail = False, f"{type(e).__name__}: {e}"
            failures += not passed
            print(f"  {'OK   ' if passed else 'FALLA'}  {case.__name__:40s} {detail}", flush=True)
        _rest_variables(plc)
    finally:
        client.close()

    print(f"\n{len(cases) - failures}/{len(cases)} casos pasaron.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
