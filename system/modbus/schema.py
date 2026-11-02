"""
Esquema de registros Modbus — maquinaria genérica, reutilizable entre proyectos.

Define el tipo `Reg` (una fila del mapa) y `RegisterSchema`, que deriva de una
lista de `Reg` todo lo que el resto del sistema necesita: la codificación de cada
valor físico a su holding register, las descripciones para la UI y la
documentación, la lista de registros a releer del servidor, la copia de valores del
config que se publica al PLC, y qué puede escribir el PLC.

**El PLC escribe sólo las filas `plc`**, que no apuntan a ninguna clave del config. Cada
una tiene su rango (`write_range()`, de `min_value` / `max_value` en la unidad física), y el
servidor rechaza lo que caiga afuera con `write_ranges()`. **El config no lo
escribe nunca**: las filas `config` son la copia de un valor de `config.yaml` —un umbral del
proceso— que la app publica para que el PLC o un HMI lo muestren, y son de sólo lectura como
el resto del mapa. La configuración es de la interfaz y del archivo.

Este módulo no conoce ningún registro puntual: la lista concreta se carga de un
archivo YAML con `load_registers()` y vive en `system/modbus/register_map.yaml`.
Así la maquinaria se comparte entre forks mientras el mapa, que es lo que cambia
en cada instalación, queda en un archivo de datos que se edita sin tocar código.

**Este módulo es el dueño de las escalas.** Quien produce un valor entrega el
valor físico (%, °C, ms, fps) y pide `encode()`; nadie multiplica por 10 ni satura
a uint16 por su cuenta.

Módulo puro: sin Qt, sin pymodbus, sin ConfigManager.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields

import yaml

from system.paths import DATA_DIR

_MAP_FILENAME = "register_map.yaml"

# Rango de un holding register.
UINT16_MAX = 0xFFFF

# Quién escribe cada fila; determina cómo la trata el sistema.
PRODUCER_INFERENCE = "inference"   # lo escribe el productor de resultados de inferencia
PRODUCER_HEALTH = "health"         # lo escribe el ciclo de métricas de hardware y cámara
PRODUCER_CONFIG = "config"         # copia de un valor de config.yaml; la publica este esquema
PRODUCER_PLC = "plc"               # lo escribe sólo el PLC
PRODUCERS = (PRODUCER_INFERENCE, PRODUCER_HEALTH, PRODUCER_CONFIG, PRODUCER_PLC)

# Campos que sólo tienen sentido en una fila `config`: una fila `plc` no va al config.
_CONFIG_ONLY_FIELDS = ("config_key", "default")


def to_plc_address(addr: int) -> str:
    """
    La dirección como la nombra el PLC: el registro 1 es el 40001.

    El `4` adelante es la convención con la que se documentan los holding registers, y no
    algo que viaje por el cable —en el protocolo la petición lleva un offset de base 0, así
    que el registro 1 se pide como offset 0—. Son tres numeraciones para lo mismo, y por eso
    la conversión vive acá y no en cada pantalla: adentro del repo la dirección es siempre
    base-1, y esto es lo único que la traduce para mostrarla.
    """
    return f"4{int(addr):04d}"


def to_uint16(value: object, scale: int = 1) -> int:
    """Codifica un valor (o None) a un holding register, saturando en el rango."""
    if value is None:
        return 0
    return max(0, min(UINT16_MAX, int(round(float(value) * scale))))


@dataclass(frozen=True)
class Reg:
    """
    Una fila del mapa de registros Modbus.

    `producer` indica quién escribe el registro; los valores válidos son los
    `PRODUCER_*` de este módulo. Las filas `config` son las únicas que este
    esquema arma por su cuenta (`mirror_batch`), copiando su clave del config; las
    `plc` las escribe sólo el PLC, y `encode()` se niega a codificarlas; las demás
    las rellena su productor.

    `config_key` —obligatorio— y `default` sólo aplican a filas con `producer: config`.
    `min_value` y `max_value` sólo a las `plc`: el rango en la
    unidad física, ambos incluidos. Sin ellos el rango es el del registro entero; una
    fila `is_bool` acepta 0 y 1.
    """
    addr: int                          # dirección Modbus 1-based
    name: str                          # id corto y único: "cpu_usage_pct"
    desc: str                          # texto humano → UI y documentación
    producer: str                      # uno de PRODUCERS
    scale: int = 1                     # registro = round(valor*scale); valor = registro/scale
    is_bool: bool = False              # codifica/decodifica como 0/1
    unit: str = ""                     # unidad física del valor sin escalar: "%", "°C", "ms"
    # ── Sólo filas producer="config" ──
    config_key: str | None = None      # ruta punteada en config.yaml
    default: object = 0                # default para cfg.get al espejar
    # ── Sólo filas que escribe el PLC (producer="plc") ──
    min_value: float | None = None     # mínimo aceptado, en la unidad física
    max_value: float | None = None     # máximo aceptado, en la unidad física


# Campos que `load_registers` acepta en cada entrada del YAML.
_REG_FIELDS = frozenset(f.name for f in fields(Reg))
_REQUIRED_FIELDS = ("addr", "name", "desc", "producer")


def write_range(reg: Reg) -> tuple[float, float]:
    """
    Lo que el PLC puede escribir en una fila `plc`, en la unidad física: (mínimo, máximo),
    ambos incluidos.

    Es la regla única del rango: sin `min_value` es 0, sin `max_value` es el registro
    entero, y una fila `is_bool` acepta 0 y 1. La validación del servidor y la tabla del
    integrador salen de acá, así que no pueden discrepar.
    """
    if reg.is_bool:
        return 0, 1
    low = 0 if reg.min_value is None else reg.min_value
    high = UINT16_MAX / reg.scale if reg.max_value is None else reg.max_value
    return low, high


def _raw_range(reg: Reg) -> tuple[int, int]:
    """`write_range()` en crudo, ya escalado."""
    if reg.is_bool:
        return 0, 1
    low, high = write_range(reg)
    return to_uint16(low, reg.scale), to_uint16(high, reg.scale)


def _check_plc_fields(entry: dict) -> str | None:
    """
    Qué tiene de malo una entrada en lo que toca al PLC, o None si nada.

    Una fila escribible mal declarada no es un error del integrador que se descubre en la
    planta, es un mapa que no carga.
    """
    producer = entry["producer"]
    has_range = [key for key in ("min_value", "max_value") if key in entry]

    # En una fila que el PLC no escribe, el rango no lo controlaría nadie, y la tabla del
    # integrador diría que sí.
    if has_range and producer != PRODUCER_PLC:
        return f"{has_range} sólo van en filas que escribe el PLC (producer: plc)."
    if has_range and entry.get("is_bool"):
        return "una fila is_bool ya tiene su rango, 0 y 1: sobran min_value / max_value."

    scale = entry.get("scale", 1)
    for key in has_range:
        value = entry[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return f"{key} tiene que ser un número, no {value!r}."
        # Fuera de esto `to_uint16` satura, y el rango aceptado no sería el declarado.
        if not 0 <= value * scale <= UINT16_MAX:
            return f"{key} = {value} no entra en un registro con escala x{scale}."
    if len(has_range) == 2 and entry["min_value"] > entry["max_value"]:
        return f"min_value ({entry['min_value']}) es mayor que max_value ({entry['max_value']})."

    if producer == PRODUCER_PLC:
        config_fields = [key for key in _CONFIG_ONLY_FIELDS if key in entry]
        if config_fields:
            return f"una fila plc no va al config: sobra(n) {config_fields}."
    return None


def load_registers(map_path: str) -> list[Reg]:
    """
    Lee el mapa de registros de un YAML y lo devuelve como lista de `Reg`.

    El archivo es una lista de mapas, uno por registro, con las claves del
    dataclass; las que no están toman el default del campo. Un archivo vacío o sin
    entradas devuelve una lista vacía, que es un mapa válido: hay proyectos que
    todavía no publican nada.

    Levanta ValueError con el archivo y la fila si una entrada está incompleta —una
    fila `config` sin `config_key` incluida—, tiene una clave desconocida, un `producer`
    fuera del vocabulario, o una fila
    escribible por el PLC mal declarada: un rango que no entra en el registro, o una
    fila `plc` que apunta al config. Preferimos romper al arrancar antes que servirle
    al PLC un mapa a medias.
    """
    with open(map_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{map_path}: el mapa tiene que ser una lista de registros.")

    registers = []
    for index, entry in enumerate(raw):
        location = f"{map_path}, entrada {index + 1}"
        if not isinstance(entry, dict):
            raise ValueError(f"{location}: cada registro es un mapa de claves, no {type(entry).__name__}.")

        unknown = set(entry) - _REG_FIELDS
        if unknown:
            raise ValueError(f"{location}: clave(s) desconocida(s) {sorted(unknown)}.")

        missing = [key for key in _REQUIRED_FIELDS if entry.get(key) in (None, "")]
        if missing:
            raise ValueError(f"{location}: falta(n) {missing}.")

        if entry["producer"] not in PRODUCERS:
            raise ValueError(
                f"{location}: producer {entry['producer']!r} no es uno de {list(PRODUCERS)}."
            )
        if not isinstance(entry["addr"], int) or entry["addr"] < 1:
            raise ValueError(f"{location}: addr tiene que ser un entero >= 1 (base-1).")

        problem = _check_plc_fields(entry)
        if problem:
            raise ValueError(f"{location} ({entry['name']}): {problem}")
        # Sin clave no hay qué publicar, y nadie más escribe esa fila: el PLC leería 0.
        if entry["producer"] == PRODUCER_CONFIG and not entry.get("config_key"):
            raise ValueError(f"{location} ({entry['name']}): una fila config necesita config_key.")

        registers.append(Reg(**entry))

    return registers


class RegisterSchema:
    """
    Índices y operaciones derivadas de una lista de `Reg`.

    Valida al construirse: dos filas no pueden compartir dirección ni nombre. Es
    inmutable después, así que se instancia una vez a nivel de módulo y se comparte
    entre hilos sin lock.
    """

    def __init__(self, registers: list[Reg]):
        self.registers = list(registers)
        self._by_addr: dict[int, Reg] = {}
        self._by_name: dict[str, Reg] = {}
        for r in self.registers:
            if r.addr in self._by_addr:
                raise ValueError(f"Dirección Modbus duplicada: {r.addr}")
            if r.name in self._by_name:
                raise ValueError(f"Nombre de registro duplicado: {r.name!r}")
            self._by_addr[r.addr] = r
            self._by_name[r.name] = r
        self._raw_ranges = {r.addr: _raw_range(r) for r in self.plc_regs()}

    # ── Índices de sólo lectura ──────────────────────────────────────────────

    def descriptions(self) -> dict[int, str]:
        """{addr: desc} — fuente única para la tabla de registros de la UI y los docs."""
        return {r.addr: r.desc for r in self.registers}

    def addr(self, name: str) -> int:
        """Dirección de un registro por su nombre corto. Ninguna dirección se escribe
        como literal fuera de este paquete: se pide por nombre."""
        return self._by_name[name].addr

    def max_addr(self) -> int:
        """Dirección más alta del mapa; 0 si el mapa está vacío."""
        return max(self._by_addr, default=0)

    def config_regs(self) -> list[Reg]:
        """Filas que copian un valor del config para que el PLC lo lea."""
        return [r for r in self.registers if r.producer == PRODUCER_CONFIG]

    def plc_regs(self) -> list[Reg]:
        """Filas que escribe el PLC, y ninguna otra."""
        return [r for r in self.registers if r.producer == PRODUCER_PLC]

    def write_ranges(self) -> dict[int, tuple[int, int]]:
        """
        {addr: (mínimo, máximo) crudos} de las filas `plc`: lo que el servidor deja escribir.

        Crudo para que el servidor compare sin decodificar en su hilo.
        """
        return dict(self._raw_ranges)

    # ── Codificación escalar ─────────────────────────────────────────────────

    def encode(self, name: str, value: object) -> int:
        """
        Codifica un valor físico al holding register de `name`, con su escala.

        Levanta ValueError con una fila `plc`: tiene un solo dueño, y que la app la
        escribiera pisaría lo que mandó el PLC en el tick siguiente sin que nada lo avise.
        """
        return self._encode(self._app_reg(name), value)

    def encode_batch(self, values: dict[str, object]) -> dict[int, int]:
        """{nombre: valor físico} → {addr: valor crudo}, listo para `update_block`."""
        return {self._app_reg(name).addr: self._encode(self._app_reg(name), value)
                for name, value in values.items()}

    def decode(self, name: str, raw: int) -> object:
        """
        El valor físico de un registro crudo: lo inverso de `encode()`.

        Un `is_bool` vuelve como bool y una fila con escala como float. Sirve para todas
        las filas, también las `plc`: leerlas es justamente lo que hace la app con ellas.
        """
        return self._decode(self._by_name[name], raw)

    def _app_reg(self, name: str) -> Reg:
        reg = self._by_name[name]
        if reg.producer == PRODUCER_PLC:
            raise ValueError(f"{name} la escribe sólo el PLC: la app no la codifica.")
        return reg

    def _encode(self, reg: Reg, value: object) -> int:
        if reg.is_bool:
            return 1 if value else 0
        return to_uint16(value, reg.scale)

    def _decode(self, reg: Reg, raw: int) -> object:
        if reg.is_bool:
            return bool(raw)
        if reg.scale != 1:
            return raw / reg.scale
        return raw

    # ── Copia del config para el PLC (config.yaml → registros) ───────────────

    def mirror_batch(self, config_manager) -> dict[int, int]:
        """
        {addr: valor crudo} de las filas `config`, leído de config.yaml.

        Es el único sentido: el PLC o un HMI leen el umbral vigente, y lo cambia sólo la
        interfaz o el archivo.
        """
        return {r.addr: self._encode(r, config_manager.get(r.config_key, r.default))
                for r in self.config_regs()}


def default_map_path() -> str:
    """
    Dónde está el mapa: primero en la raíz de la instalación, si no al lado de este módulo.

    Son dos lugares porque el archivo cumple dos papeles. En el repo vive junto al esquema
    que lo valida, que es donde se lo edita y se lo lee. En un entregable compilado es un
    archivo de la instalación —lo acuerda el integrador y cambia en cada planta—, así que
    va al lado del ejecutable, en `DATA_DIR`, y no adentro de la distribución, que se
    reemplaza entera al actualizar.

    Gana `DATA_DIR`: si alguien puso un mapa ahí, es el de esa planta y no el de fábrica.
    """
    installed = os.path.join(DATA_DIR, _MAP_FILENAME)
    if os.path.isfile(installed):
        return installed
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), _MAP_FILENAME)
