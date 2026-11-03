"""
Servidor Modbus esclavo: TCP y RTU sobre un mismo datastore en RAM.

Expone los holding registers del mapa. El PLC lee todo con FC03 y la app escribe desde
adentro con `update_register` / `update_block`. **El PLC escribe —FC06 y FC16— sólo en las
direcciones de `write_ranges`**, y sólo si hay un `on_write` que lo reciba; sin él, todo es
de sólo lectura. Cualquier otra función se rechaza con 0x02 en vez de contestar un valor que
parezca una medición.

Una escritura se valida **entera antes de tocar un registro**: 0x02 si alguna dirección no
es escribible, 0x03 si algún valor cae fuera de su rango. Después se escribe y se avisa a
`on_write` bajo el mismo lock que `update_block()`: la app se entera por evento, y entre la
escritura y el aviso no se mete nada.

Los dos transportes comparten el contexto, así que el PLC ve los mismos valores por el
cable que sea, y son independientes: se habilitan los dos, uno o ninguno desde `modbus` en
el config.yaml. Cada uno reporta su `status` con el vocabulario `STATUS_*` de este módulo,
que es lo que espera la palabra de comunicaciones.

`run()` toma el hilo que lo llama y arma ahí su loop de asyncio; `stop()` lo desarma desde
afuera. La forma de uso es un QThread cuyo `run()` llame a este.

El mapa no se conoce acá: el cableado le pasa sólo hasta qué dirección llega (`max_addr`) y
el rango crudo de cada dirección que escribe el PLC (`write_ranges`). Qué significa cada
dirección es de `system/modbus/registers.py`.

    server = SharedModbusServer(ConfigManager())
    server.update_block(SCHEMA.encode_batch({"cpu_usage_pct": 37}))
    server.run()                              # bloquea hasta stop()
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
from collections.abc import Callable, Mapping

from pymodbus.constants import ExcCodes
from pymodbus.datastore import ModbusServerContext
from pymodbus.datastore.simulator import ModbusSimulatorContext
from pymodbus.server import StartAsyncSerialServer, StartAsyncTcpServer

from system.config_manager import ConfigManager
from system.logger import logger
from system.modbus.schema import to_plc_address

# pymodbus 3.x emite sus advertencias de obsolescencia por su propio logger, no por
# los warnings de Python. Se suprimen acá, en la fuente; migrar a SimData/SimDevice
# al actualizar a la v4, donde ModbusSimulatorContext ya no existe.
logging.getLogger("pymodbus").setLevel(logging.ERROR)

STATUS_DISABLED = "disabled"    # apagado en el config; no es una falla
STATUS_STARTING = "starting"    # levantando, todavía no acepta consultas
STATUS_ACTIVE = "active"        # sirviendo
STATUS_ERROR = "error"          # quiso levantar y no pudo

# Tamaño del mapa expuesto cuando el config no dice otra cosa: registros 1-100
# (40001-40100). Si el mapa llega más lejos, el datastore llega hasta su última fila y
# no más allá: una lectura que se pase del final se rechaza en vez de contestar un 0 que
# parezca medición.
DEFAULT_REGISTER_COUNT = 100

DEFAULT_SLAVE_ID = 1

# Las funciones servidas: FC03 sobre todo el mapa; FC06 y FC16 sólo donde escribe el PLC.
_FC_READ_HOLDING_REGISTERS = 3
_FC_WRITE_SINGLE_REGISTER = 6
_FC_WRITE_MULTIPLE_REGISTERS = 16
_WRITE_FUNC_CODES = (_FC_WRITE_SINGLE_REGISTER, _FC_WRITE_MULTIPLE_REGISTERS)

# Lo que el cableado le pasa al servidor para que el PLC escriba.
WriteRanges = Mapping[int, tuple[int, int]]    # {addr base-1: (mínimo, máximo) crudos}
OnWrite = Callable[[dict[int, int]], None]     # {addr base-1: crudo} de una petición

_SLAVE_ID_MIN = 1
_SLAVE_ID_MAX = 247

# Margen entre el bind y el "está andando": si en ese tiempo no falló, levantó.
_STARTUP_GRACE_S = 2.0

# Cada cuánto se repite el aviso de una escritura rechazada con la misma dirección y motivo.
_REJECTION_LOG_INTERVAL_S = 60.0


def _build_simulator_config(register_count: int) -> dict:
    """Config del datastore de pymodbus: sólo holding registers, todos uint16 en 0."""
    return {
        "setup": {
            "di size": 0,
            "co size": 0,
            "ir size": 0,
            "hr size": register_count,
            "shared blocks": False,
            "type exception": False,
            "defaults": {
                "value": {"bits": 0, "uint16": 0, "uint32": 0, "float32": 0, "string": " "},
                "action": {"bits": None, "uint16": None, "uint32": None,
                           "float32": None, "string": None},
            },
        },
        "invalid": [],
        "write": [],
        "bits": [],
        "uint16": [[0, register_count - 1]],
        "uint32": [],
        "float32": [],
        "string": [],
        "repeat": [],
    }


class _RejectedWriteLog:
    """
    El aviso de una escritura del PLC rechazada, uno por dirección y motivo cada
    `interval_s`.

    El PLC ya se entera por la excepción; esto es para el equipo, que si no, no deja rastro
    de que un programa manda un valor que no corresponde. Un PLC que lo reintenta en cada
    ciclo llenaría el log, así que los repetidos se cuentan y salen en el aviso siguiente.

    Lo llama sólo el hilo del servidor —los dos transportes comparten su loop—, así que no
    lleva lock.
    """

    def __init__(self, interval_s: float, clock: Callable[[], float] = time.monotonic):
        self._interval_s = interval_s
        self._clock = clock
        self._last_logged: dict[tuple[int, int], float] = {}
        self._silenced: dict[tuple[int, int], int] = {}

    def report(self, func_code: int, first_addr: int, count: int,
               addr: int, raw: int, code: ExcCodes, reason: str):
        """`addr` y `raw` son los del registro que hizo rechazar la petición entera."""
        key = (addr, int(code))
        now = self._clock()
        last = self._last_logged.get(key)
        if last is not None and now - last < self._interval_s:
            self._silenced[key] = self._silenced.get(key, 0) + 1
            return
        self._last_logged[key] = now
        silenced = self._silenced.pop(key, 0)

        span = to_plc_address(first_addr)
        if count > 1:
            span += f"-{to_plc_address(first_addr + count - 1)}"
        message = (f"[Modbus] Escritura del PLC rechazada: FC{func_code:02d} en {span}, "
                   f"{to_plc_address(addr)} = {raw} {reason} (0x{int(code):02X}); "
                   f"no se escribió ningún registro.")
        if silenced:
            message += f" Otras {silenced} iguales desde el aviso anterior."
        logger.warning(message)


class _HoldingRegisters(ModbusSimulatorContext):
    """
    Contexto que sirve FC03 dentro del mapa, y FC06 / FC16 sólo sobre `write_ranges`.

    Rechaza las demás funciones con 0x02. También arregla el rango: `validate()` de
    pymodbus chequea sólo la dirección de ARRANQUE (y con `>` en vez de `>=`), así que una
    lectura que empieza adentro y se pasa del final revienta con IndexError → 0x04 más un
    traceback por cada consulta.

    **La lectura del PLC toma el mismo lock que la escritura de la app.** pymodbus
    recorre los registros sin lock desde el hilo del servidor, así que una consulta que
    cae en medio de `update_block()` leería medio bloque nuevo y medio viejo.

    **La escritura del PLC se valida entera antes de tocar nada.** La de la base valida
    registro por registro mientras escribe, así que un FC16 que se mete en una fila no
    escribible dejaría escrita la primera mitad.

    `write_ranges` va en direcciones base-1, como el mapa. `on_write` corre en el hilo del
    servidor con el lock tomado: tiene que volver enseguida —encolar, no procesar—.
    """

    def __init__(self, config: dict, write_lock: threading.Lock,
                 write_ranges: WriteRanges, on_write: OnWrite | None):
        super().__init__(config, None)
        self._write_lock = write_lock
        # Aceptar escrituras que nadie aplica sería peor que rechazarlas.
        self._write_ranges = dict(write_ranges) if on_write is not None else {}
        self._on_write = on_write
        self._rejections = _RejectedWriteLog(_REJECTION_LOG_INTERVAL_S)

    async def async_OLD_setValues(self, func_code, address, values):
        """
        FC06 / FC16 del PLC: valida la petición entera, escribe y avisa bajo el lock.

        Es el método al que `ModbusServerContext.async_setValues` delega en pymodbus 3.x.
        Devolver un `ExcCodes` es lo que pymodbus convierte en la respuesta de excepción.
        """
        writes = {address + 1 + offset: raw for offset, raw in enumerate(values)}
        rejection = self._rejection(func_code, writes)
        if rejection is not None:
            addr, code, reason = rejection
            self._rejections.report(func_code, address + 1, len(values), addr,
                                    writes.get(addr, 0), code, reason)
            return code

        start = self.fc_offset[func_code] + address
        with self._write_lock:
            for offset, raw in enumerate(values):
                self.registers[start + offset].value = raw
            self._on_write(writes)
        return None

    def _rejection(self, func_code: int, writes: dict[int, int]
                   ) -> tuple[int, ExcCodes, str] | None:
        """
        Por qué se rechaza la petición entera —(registro culpable, excepción, motivo)—, o
        None si se acepta. Una dirección no escribible gana sobre un valor fuera de rango.
        """
        first_addr = min(writes, default=0)
        if func_code not in _WRITE_FUNC_CODES:
            return first_addr, ExcCodes.ILLEGAL_ADDRESS, f"la función FC{func_code:02d} no se sirve"
        for addr in writes:
            if not self._is_writable(addr):
                return addr, ExcCodes.ILLEGAL_ADDRESS, "no es escribible"
        for addr, raw in writes.items():
            low, high = self._write_ranges[addr]
            if not low <= raw <= high:
                return addr, ExcCodes.ILLEGAL_VALUE, "fuera de rango"
        return None

    def _is_writable(self, addr: int) -> bool:
        """La única regla de qué dirección base-1 escribe el PLC."""
        return addr in self._write_ranges and addr <= self.register_count

    async def async_OLD_getValues(self, func_code, address, count=1):
        """
        FC03 bajo el lock de escritura: un bloque se lee entero de antes o entero de después.

        Es el método al que `ModbusServerContext.async_getValues` delega en pymodbus 3.x, y
        reemplaza la lectura de la base, que además corre las `action` de cada celda; este
        datastore no declara ninguna. Una consulta inválida sigue en la base, que es la que
        arma la excepción. Si una versión de pymodbus renombra el método, falla el test de
        la lectura bajo lock.
        """
        if not self.validate(func_code, address, count):
            return await super().async_OLD_getValues(func_code, address, count)
        start = self.fc_offset[func_code] + address
        with self._write_lock:
            return [cell.value for cell in self.registers[start:start + count]]

    def validate(self, func_code, address, count=1):
        if func_code not in (_FC_READ_HOLDING_REGISTERS, *_WRITE_FUNC_CODES):
            return False
        real_address = self.fc_offset[func_code] + address
        if real_address < 0 or count < 1:
            return False
        if real_address + count > self.register_count:
            return False
        if func_code in _WRITE_FUNC_CODES:
            # También la relectura del eco: después de un FC06, pymodbus arma la respuesta
            # con `getValues(fc=6)`. Si esto aceptara sólo FC03, el registro quedaría
            # escrito y el PLC recibiría 0x02 por una escritura que sí se aplicó.
            return all(self._is_writable(address + 1 + offset) for offset in range(count))
        return super().validate(func_code, address, count)


class SharedModbusServer:
    """
    Servidor Modbus TCP + RTU compartiendo un datastore en RAM.

    Los métodos de escritura y lectura de registros son thread-safe y se pueden
    llamar desde cualquier hilo, esté el servidor corriendo o no; un `update_block()`
    es además atómico frente a las lecturas del PLC. `run()` bloquea
    el hilo que lo llama; todo lo demás se llama desde afuera.

    `blocked_reason` lo pone el cableado cuando hay algo por lo que este servidor no
    puede servir aunque el config lo pida —hoy, un mapa de registros que no se pudo
    leer—. Con eso puesto los dos transportes quedan en `STATUS_ERROR` y ninguno toma su
    puerto. Que no arranque es mejor que servir un datastore vacío: un PLC leyendo ceros
    no puede distinguir «no hay medición» de «falta el mapa», y sin conexión ni latido esa
    ambigüedad desaparece. Este módulo no sabe qué es un mapa de registros y no tiene por
    qué saberlo: recibe el motivo ya escrito.

    Lo mismo con lo que sale del mapa:

    - `max_addr` es la dirección más alta; el datastore llega por lo menos hasta ahí,
      aunque `modbus.register_count` diga menos. Así una fila nueva no obliga a acordarse
      de subir otra clave, y un mapa chico no expone registros que nadie escribe.
    - `write_ranges` son las direcciones que escribe el PLC (base-1), cada una con su rango
      crudo, como lo arma `RegisterSchema.write_ranges()`.
    - `on_write` recibe `{addr: crudo}` por cada petición aceptada, en el hilo del
      servidor y con el lock tomado: encola y vuelve. Sin él no se acepta ninguna
      escritura. Las escrituras de la app (`update_block`) no lo llaman nunca, así que lo
      que llega ahí es siempre del PLC.
    """

    def __init__(self, config_manager: ConfigManager, blocked_reason: str = "", *,
                 max_addr: int = 0, write_ranges: WriteRanges | None = None,
                 on_write: OnWrite | None = None):
        self._config = config_manager
        self._blocked_reason = blocked_reason
        self._lock = threading.Lock()
        self._tcp_status = STATUS_DISABLED
        self._rtu_status = STATUS_DISABLED
        self._loop: asyncio.AbstractEventLoop | None = None

        self._register_count = max(self._read_register_count(), max_addr)
        self._simulator = _HoldingRegisters(
            _build_simulator_config(self._register_count), self._lock,
            write_ranges or {}, on_write,
        )
        self._hr_offset = self._simulator.fc_offset[_FC_READ_HOLDING_REGISTERS]

        # Un solo esclavo registrado: el servidor contesta ÚNICAMENTE a su id. Con
        # `single=True` respondía a los 247, que en un RS-485 compartido pisa a los
        # demás dispositivos del bus.
        self._slave_id = self._read_slave_id()
        self._context = ModbusServerContext(devices={self._slave_id: self._simulator})

    # ── Estado ───────────────────────────────────────────────────────────────

    @property
    def tcp_status(self) -> str:
        return self._tcp_status

    @property
    def rtu_status(self) -> str:
        return self._rtu_status

    @property
    def slave_id(self) -> int:
        return self._slave_id

    @property
    def register_count(self) -> int:
        return self._register_count

    # ── Acceso a los registros (thread-safe) ─────────────────────────────────

    def update_register(self, address: int, value: int):
        """
        Escribe un holding register, con direccionamiento base-1: el registro 1 es
        el primero, el que el PLC lee como 40001.

        Una dirección fuera del mapa se descarta con un warning: es un error del
        mapa, no del PLC, y no vale la pena tirar abajo el ciclo que la escribió.
        """
        self.update_block({address: value})

    def get_register(self, address: int) -> int:
        """Lee un holding register (base-1). Devuelve 0 fuera del mapa."""
        index = self._index_of(address)
        if index is None:
            return 0
        with self._lock:
            return self._simulator.registers[index].value

    def update_block(self, items: dict[int, int]):
        """
        Escritura en batch: {addr: valor crudo}, tal como lo devuelve
        `RegisterSchema.encode_batch()`.

        **Atómica para el PLC**: el bloque entero se escribe bajo un solo lock, el mismo
        que toma su lectura, así que una consulta ve el bloque de antes o el de después y
        nunca la mitad. Importa en los valores de dos palabras: leído a medias, el reloj de
        `clock_epoch_s_high` / `_low` salta 18 h cuando la palabra baja da la vuelta, y ese
        salto es justo lo que se le pide al PLC que enclave.

        Una dirección fuera del mapa se descarta con un warning y no frena a las demás.
        """
        indexed = []
        for address, value in items.items():
            index = self._index_of(address)
            if index is None:
                logger.warning(
                    f"[Modbus] Dirección fuera del mapa: {address} "
                    f"(el mapa expone 1-{self._register_count})."
                )
                continue
            indexed.append((index, int(value) & 0xFFFF))
        with self._lock:
            for index, value in indexed:
                self._simulator.registers[index].value = value

    def _index_of(self, address: int) -> int | None:
        """Índice en el datastore de una dirección base-1, o None si está fuera."""
        pdu = address - 1
        if not (0 <= pdu < self._register_count):
            return None
        return self._hr_offset + pdu

    # ── Lectura de configuración ─────────────────────────────────────────────

    def _read_slave_id(self) -> int:
        """Lee `modbus.slave_id` y lo valida contra el rango de Modbus."""
        raw = self._config.get("modbus.slave_id", DEFAULT_SLAVE_ID)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            logger.error(f"[Modbus] slave_id inválido ({raw!r}). Usando {DEFAULT_SLAVE_ID}.")
            return DEFAULT_SLAVE_ID
        if not (_SLAVE_ID_MIN <= value <= _SLAVE_ID_MAX):
            logger.error(
                f"[Modbus] slave_id {value} fuera de rango "
                f"[{_SLAVE_ID_MIN}-{_SLAVE_ID_MAX}]. Usando {DEFAULT_SLAVE_ID}."
            )
            return DEFAULT_SLAVE_ID
        return value

    def _read_register_count(self) -> int:
        """Lee `modbus.register_count`: cuántos registros expone el datastore como mínimo."""
        raw = self._config.get("modbus.register_count", DEFAULT_REGISTER_COUNT)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = 0
        if value < 1:
            logger.error(
                f"[Modbus] register_count inválido ({raw!r}). "
                f"Usando {DEFAULT_REGISTER_COUNT}."
            )
            return DEFAULT_REGISTER_COUNT
        return value

    # ── Servidores async ─────────────────────────────────────────────────────

    def _blocked(self, transport: str, status_attribute: str) -> bool:
        """
        True si hay un motivo para no servir. Deja el transporte en error y lo loguea.

        Se pregunta antes que por el `enabled` del config: el motivo no es una decisión
        de la instalación sino algo que no funciona, y decir «deshabilitado» cuando lo
        que pasa es que falta el mapa manda a mirar el config equivocado.
        """
        if not self._blocked_reason:
            return False
        setattr(self, status_attribute, STATUS_ERROR)
        logger.error(f"[Modbus] {transport} no arranca: {self._blocked_reason}")
        return True

    async def run_tcp_server(self):
        """Levanta el servidor TCP y no vuelve hasta que se lo cancela."""
        if self._blocked("TCP", "_tcp_status"):
            return
        tcp_config = self._config.get("modbus.tcp", {})
        if not tcp_config.get("enabled", True):
            self._tcp_status = STATUS_DISABLED
            logger.info("[Modbus] TCP deshabilitado en config.yaml")
            return

        self._tcp_status = STATUS_STARTING
        host = tcp_config.get("host", "0.0.0.0")
        port = tcp_config.get("port", 502)
        logger.info(
            f"[Modbus] TCP arrancando en {host}:{port} "
            f"({self._register_count} registros, unit id {self._slave_id})"
        )

        grace = asyncio.ensure_future(self._mark_active("_tcp_status"))
        try:
            # ignore_missing_devices=False: una consulta a otro unit id recibe la
            # excepción 0x0B en vez de un timeout opaco. Es punto a punto, no hay bus
            # que pisar, así que conviene avisarle al integrador que el id está mal.
            await StartAsyncTcpServer(
                context=self._context, address=(host, port),
                ignore_missing_devices=False, broadcast_enable=False,
            )
        except asyncio.CancelledError:
            grace.cancel()
            raise
        except PermissionError:
            grace.cancel()
            self._tcp_status = STATUS_ERROR
            self._log_bind_denied(port)
        except (OSError, RuntimeError) as e:
            grace.cancel()
            self._tcp_status = STATUS_ERROR
            logger.error(f"[Modbus] Error del servidor TCP: {e}")

    async def run_rtu_server(self):
        """Levanta el servidor RTU y no vuelve hasta que se lo cancela."""
        if self._blocked("RTU", "_rtu_status"):
            return
        rtu_config = self._config.get("modbus.rtu", {})
        if not rtu_config.get("enabled", False):
            self._rtu_status = STATUS_DISABLED
            logger.info("[Modbus] RTU deshabilitado en config.yaml")
            return

        self._rtu_status = STATUS_STARTING
        port = rtu_config.get("port", "/dev/ttyS0")
        baudrate = rtu_config.get("baudrate", 115200)
        logger.info(
            f"[Modbus] RTU arrancando en {port} @ {baudrate} bps "
            f"(unit id {self._slave_id})"
        )

        grace = asyncio.ensure_future(self._mark_active("_rtu_status"))
        try:
            # ignore_missing_devices=True: en un bus RS-485 compartido, contestar una
            # consulta dirigida a otro esclavo pisaría su respuesta y rompería la
            # comunicación de los dos. El maestro ve el timeout del esclavo ausente.
            await StartAsyncSerialServer(
                context=self._context,
                port=port,
                baudrate=baudrate,
                parity=rtu_config.get("parity", "N"),
                stopbits=rtu_config.get("stop_bits", 1),
                bytesize=8,
                ignore_missing_devices=True,
                broadcast_enable=False,
            )
        except asyncio.CancelledError:
            grace.cancel()
            raise
        except Exception as e:
            grace.cancel()
            self._rtu_status = STATUS_ERROR
            logger.error(f"[Modbus] Falla del RTU en {port}: {e}. Continúa sólo el TCP.")

    async def _mark_active(self, status_attr: str):
        """Pasa un transporte a `active` si sobrevivió el arranque sin fallar."""
        await asyncio.sleep(_STARTUP_GRACE_S)
        if getattr(self, status_attr) == STATUS_STARTING:
            setattr(self, status_attr, STATUS_ACTIVE)

    def _log_bind_denied(self, port: int):
        if sys.platform == "win32":
            # En Windows no hay puertos privilegiados: un WSAEACCES (10013) es el
            # puerto ya tomado en exclusiva, el firewall, o un rango reservado por
            # Hyper-V/WSL2 (netsh interface ipv4 show excludedportrange protocol=tcp).
            logger.error(
                f"[Modbus] Sin permiso para el bind en el puerto {port}. Puede estar "
                f"ocupado, bloqueado por el firewall o dentro de un rango reservado "
                f"por Hyper-V/WSL2."
            )
        else:
            logger.error(
                f"[Modbus] Sin permiso para el bind en el puerto {port}. "
                f"Ejecutar con sudo o usar un puerto > 1024."
            )

    # ── Ciclo de vida ────────────────────────────────────────────────────────

    def run(self):
        """
        Toma el hilo actual, arma su propio loop de asyncio y corre TCP + RTU.

        Bloquea hasta que `stop()` lo desarme desde otro hilo. Un transporte que no
        levanta no tumba al otro: cada uno deja el motivo en su `status`.
        """
        if sys.platform == "win32":
            # En Windows el default es ProactorEventLoop, que no implementa
            # add_reader/add_writer — justo lo que pyserial-asyncio necesita para el
            # puerto COM, así que el servidor RTU es imposible sobre Proactor. El loop
            # se crea en el hilo que llama, por eso la política se fija acá y no en el
            # arranque de la app. El TCP funciona igual con las dos.
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop

        tasks = [
            loop.create_task(self.run_tcp_server()),
            loop.create_task(self.run_rtu_server()),
        ]

        try:
            loop.run_until_complete(asyncio.gather(*tasks))
        except asyncio.CancelledError:
            logger.info("[Modbus] Tareas canceladas durante el cierre.")
        except RuntimeError as e:
            logger.info(f"[Modbus] Loop detenido durante el cierre: {e}")
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            self._cancel_pending(loop)
            # Ya no hay nada sirviendo, así que ningún transporte puede quedar en
            # `active`; pero un `error` se conserva, porque es el diagnóstico de por
            # qué esta corrida no sirvió y se perdería para siempre. Las dos cosas
            # apagan igual el bit de la palabra de comunicaciones.
            self._tcp_status = self._settled_status(self._tcp_status)
            self._rtu_status = self._settled_status(self._rtu_status)
            self._loop = None
            logger.info("[Modbus] Servidor cerrado correctamente.")
            loop.close()

    @staticmethod
    def _settled_status(status: str) -> str:
        return status if status == STATUS_ERROR else STATUS_DISABLED

    def stop(self):
        """
        Pide el cierre desde otro hilo; `run()` vuelve poco después.

        Sin servidor corriendo no hace nada, así que se puede llamar dos veces.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        loop.call_soon_threadsafe(self._cancel_all_tasks, loop)

    @staticmethod
    def _cancel_all_tasks(loop: asyncio.AbstractEventLoop):
        """Cancela todo lo que quede vivo en el loop, desde el propio loop.

        Sólo cancelar tareas; NO llamar a `loop.stop()` — eso deja el
        `run_until_complete` con un "Event loop stopped before Future completed".
        """
        for task in asyncio.all_tasks(loop):
            task.cancel()

    @staticmethod
    def _cancel_pending(loop: asyncio.AbstractEventLoop):
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
