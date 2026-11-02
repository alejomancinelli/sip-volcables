"""
Qué Jetson es y cómo está configurada: módulo, L4T, modo de potencia, relojes y eventos
de sobrecorriente.

Maquinaria genérica: no conoce la app ni su config. Todo sale de archivos que un usuario
sin root puede leer —`/proc`, `/etc`, `/var/lib` y sysfs—, así que se puede consultar
desde la app sin sudo. La carga de los motores en % no está acá: `tegrastats` la publica
sólo como root. Lo que sí está es si cada motor está encendido (runtime PM) y a qué
frecuencia corre (devfreq), cuando el kernel lo publica.

Ninguna función propaga excepciones: fuera de una Jetson, o en un kernel que no publica
un nodo, devuelven '' / None / {} según el tipo.

Por qué importa cada dato al medir rendimiento:
  - El **modo de potencia** fija el techo de los relojes de CPU, GPU y de cada motor. Un
    equipo en 10W hace ver lento todo lo demás.
  - Los **relojes fijados** (`jetson_clocks`) dejan el mínimo igual al máximo. Sin
    fijarlos, el gobernador los baja con poca carga y el pre y post proceso de Python
    tardan más: dos corridas iguales pueden dar distinto por eso solo.
  - Los **eventos de sobrecorriente** son contadores desde el arranque. Uno que sube
    durante una prueba invalida la medición: el módulo recortó relojes para protegerse.
"""

import glob
import os
import re

_MODEL_PATH = "/proc/device-tree/model"
_TEGRA_RELEASE_PATH = "/etc/nv_tegra_release"
_NVPMODEL_STATUS_PATH = "/var/lib/nvpmodel/status"
_NVPMODEL_CONF_PATH = "/etc/nvpmodel.conf"
_CPUFREQ_POLICY_GLOB = "/sys/devices/system/cpu/cpufreq/policy*"
# El nombre del nodo cambia con la GPU y el kernel: `17000000.gpu` en JetPack 6,
# `17000000.ga10b` en JetPack 5. La dirección es la misma en todo Orin.
_GPU_DEVFREQ_GLOB = "/sys/class/devfreq/17000000.*"
_OVERCURRENT_GLOB = "/sys/class/hwmon/hwmon*/oc*_event_cnt"
_DEVFREQ_GLOB = "/sys/class/devfreq/*"
# Los motores cuelgan de /sys/devices/platform a distinta profundidad según el kernel
# (`bus@0/15340000.vic` en JetPack 6): se buscan hasta tres niveles abajo.
_RUNTIME_STATUS_GLOBS = tuple(
    f"/sys/devices/platform/{'*/' * depth}power/runtime_status" for depth in (1, 2, 3))
# El nodo de un motor es `<dirección>.<motor>` (`15340000.vic`, `15880000.nvdla0`). Buscar
# la palabra suelta confunde nodos como `device`, que contiene «vic».
_ENGINE_DEVICE_RE = re.compile(r"[0-9a-f]+\.(vic|nvenc|nvdec|nvjpg|ofa|nvdla\d*|pva\d*)")

_KHZ_PER_MHZ = 1_000
_HZ_PER_MHZ = 1_000_000

# "# R36 (release), REVISION: 4.0, GCID: ..." -> R36.4.0
_RELEASE_RE = re.compile(r"#\s*R(\d+)\s*\(release\),\s*REVISION:\s*([\d.]+)")
_PMODE_RE = re.compile(r"pmode:\s*(\d+)")
_POWER_MODEL_RE = re.compile(r"<\s*POWER_MODEL\s+ID=(\d+)\s+NAME=(\S+?)\s*>")


def _read_text(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def _read_int(path: str) -> int | None:
    text = _read_text(path)
    try:
        return int(text.strip()) if text is not None else None
    except ValueError:
        return None


def read_module() -> str:
    """Nombre del módulo según el device tree ('NVIDIA Jetson Orin NX ...'), o ''."""
    text = _read_text(_MODEL_PATH)
    return "" if text is None else text.replace("\0", "").strip()


def parse_l4t_release(text: str) -> str:
    """'R36.4.0' a partir de la primera línea de `/etc/nv_tegra_release`, o ''."""
    match = _RELEASE_RE.search(text)
    return f"R{match.group(1)}.{match.group(2)}" if match else ""


def read_l4t_release() -> str:
    """Versión de L4T de este equipo ('R36.4.0'), o '' fuera de una Jetson."""
    text = _read_text(_TEGRA_RELEASE_PATH)
    return "" if text is None else parse_l4t_release(text)


def parse_power_mode(status: str, conf: str) -> dict:
    """
    Modo de potencia a partir del estado de nvpmodel y de su tabla de modos.

    Devuelve `{"id": int, "name": str}`; el nombre queda '' si el id no está en la tabla.
    {} si el estado no trae un modo.
    """
    match = _PMODE_RE.search(status)
    if match is None:
        return {}
    mode_id = int(match.group(1))
    names = {int(i): name for i, name in _POWER_MODEL_RE.findall(conf)}
    return {"id": mode_id, "name": names.get(mode_id, "")}


def read_power_mode() -> dict:
    """Modo de potencia vigente, `{"id", "name"}`, o {} si no hay nvpmodel."""
    status = _read_text(_NVPMODEL_STATUS_PATH)
    if status is None:
        return {}
    return parse_power_mode(status, _read_text(_NVPMODEL_CONF_PATH) or "")


def _read_clock(directory: str, files: tuple[str, str, str], per_mhz: int) -> dict | None:
    """`{"cur_mhz", "min_mhz", "max_mhz"}` de un nodo de frecuencias, o None si falta uno."""
    values = [_read_int(os.path.join(directory, name)) for name in files]
    if any(value is None for value in values):
        return None
    cur_mhz, min_mhz, max_mhz = (value // per_mhz for value in values)
    return {"cur_mhz": cur_mhz, "min_mhz": min_mhz, "max_mhz": max_mhz}


def read_clocks() -> dict:
    """
    Relojes de CPU (por política de cpufreq) y de GPU, en MHz.

    `{"cpu": {"policy0": {"cur_mhz", "min_mhz", "max_mhz"}, ...},
      "gpu": {"17000000.gpu": {...}}}`. Una sección queda vacía si el kernel no la publica.
    """
    cpu = {}
    for directory in sorted(glob.glob(_CPUFREQ_POLICY_GLOB)):
        clock = _read_clock(directory, ("scaling_cur_freq", "scaling_min_freq",
                                        "scaling_max_freq"), _KHZ_PER_MHZ)
        if clock is not None:
            cpu[os.path.basename(directory)] = clock

    gpu = {}
    for directory in sorted(glob.glob(_GPU_DEVFREQ_GLOB)):
        clock = _read_clock(directory, ("cur_freq", "min_freq", "max_freq"), _HZ_PER_MHZ)
        if clock is not None:
            gpu[os.path.basename(directory)] = clock
    return {"cpu": cpu, "gpu": gpu}


def are_clocks_pinned(clocks: dict) -> bool | None:
    """
    True si todos los relojes tienen el mínimo igual al máximo, que es lo que deja
    `jetson_clocks`. None si `clocks` no trae ninguno.
    """
    every_clock = [*clocks.get("cpu", {}).values(), *clocks.get("gpu", {}).values()]
    if not every_clock:
        return None
    return all(clock["min_mhz"] == clock["max_mhz"] for clock in every_clock)


def read_devfreq() -> dict:
    """
    Todos los nodos de devfreq, `{nodo: {"cur_mhz", "min_mhz", "max_mhz"}}`.

    Además de la GPU, el kernel puede publicar acá los motores (`15340000.vic`...). Se
    leen sin root, que es lo que `tegrastats` no permite.
    """
    nodes = {}
    for directory in sorted(glob.glob(_DEVFREQ_GLOB)):
        clock = _read_clock(directory, ("cur_freq", "min_freq", "max_freq"), _HZ_PER_MHZ)
        if clock is not None:
            nodes[os.path.basename(directory)] = clock
    return nodes


def find_engine_devices() -> dict[str, str]:
    """
    Los motores con estado de runtime PM: `{dispositivo: ruta de runtime_status}`.

    Se buscan una vez y se leen muchas con `read_runtime_states`: recorrer
    /sys/devices/platform en cada muestra cuesta más que la muestra.
    """
    devices = {}
    for pattern in _RUNTIME_STATUS_GLOBS:
        for path in sorted(glob.glob(pattern)):
            device = os.path.basename(os.path.dirname(os.path.dirname(path)))
            if _ENGINE_DEVICE_RE.fullmatch(device):
                devices.setdefault(device, path)
    return devices


def read_runtime_states(devices: dict[str, str]) -> dict[str, str]:
    """
    Estado de runtime PM de cada motor: 'active' mientras está encendido, 'suspended'
    cuando el kernel lo apagó. Un nodo que no se puede leer no aparece.
    """
    states = {}
    for device, path in devices.items():
        status = _read_text(path)
        if status is not None:
            states[device] = status.strip()
    return states


def read_engines(devices: dict[str, str]) -> dict:
    """
    Estado de cada motor, sin root: `{motor: {"is_active": bool, "mhz": int}}`.

    `devices` sale de `find_engine_devices()`. El nombre es el del motor sin su dirección
    ('vic', 'nvenc'), así que no cambia entre módulos Orin; dos instancias del mismo llevan
    número desde la segunda, en el orden de sus direcciones, como en `tegrastats`
    ('nvjpg', 'nvjpg1').

    - `is_active` dice que se usó hace poco, no que trabaja ahora: el kernel demora unos
      segundos en suspenderlo después del último trabajo.
    - `mhz` es la frecuencia de devfreq mientras está activo, y 0 si no: NVJPG y OFA
      informan su máximo aun suspendidos.
    """
    states = read_runtime_states(devices)
    clocks = read_devfreq()
    engines = {}
    for device in sorted(devices):
        if device not in states:
            continue
        base_name = device.split(".", 1)[1]
        name, index = base_name, 1
        while name in engines:
            name, index = f"{base_name}{index}", index + 1
        is_active = states[device] == "active"
        mhz = clocks.get(device, {}).get("cur_mhz", 0) if is_active else 0
        engines[name] = {"is_active": is_active, "mhz": mhz}
    return engines


def read_overcurrent_counts() -> dict:
    """
    Contadores de eventos de sobrecorriente desde el arranque, `{"<hwmon>/ocN": int}`.

    El nombre lleva el del hwmon que lo publica, porque el número de hwmon cambia entre
    arranques y el nombre no. {} si el kernel no publica ninguno.
    """
    counts = {}
    for path in sorted(glob.glob(_OVERCURRENT_GLOB)):
        count = _read_int(path)
        if count is None:
            continue
        hwmon_dir = os.path.dirname(path)
        hwmon_name = (_read_text(os.path.join(hwmon_dir, "name")) or "").strip()
        counter = os.path.basename(path).removesuffix("_event_cnt")
        counts[f"{hwmon_name or os.path.basename(hwmon_dir)}/{counter}"] = count
    return counts
