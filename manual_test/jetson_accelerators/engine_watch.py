"""
Qué motores se encendieron mientras corría un bloque, por dos fuentes.

Lo comparten `probe_report.py` y `stage_bench.py`: las dos necesitan saber si una prueba
encendió el motor que dice usar, y una muestra suelta al final no alcanza —el motor ya se
apagó—. Un hilo muestrea a ritmo fijo y guarda lo más alto que vio de cada motor:

  - `tegrastats`: frecuencia y, cuando la publica, carga. **Sólo como root**: un usuario
    común ve la GPU y nada más.
  - sysfs, sin root: el estado de runtime PM de cada motor ('active' / 'suspended') y su
    frecuencia en devfreq, si el kernel los publica. Es la fuente candidata para la app,
    que corre sin sudo, y esta prueba es la que dice si alcanza.
"""

import threading

from tools.jetson import board
from tools.jetson.tegrastats import TegrastatsReader

# Siempre encendidos (memoria y GPU): no dicen qué motor usó la prueba.
_ALWAYS_ON = ("emc", "gr3d")
_GPU_DEVFREQ_PREFIX = "17000000."


class EngineWatch:
    """
    `with EngineWatch(reader, interval_s, devices): ...` deja, para el bloque:

      peaks           {motor: {"mhz", "load_pct"}} lo más alto según `tegrastats`
      active_devices  motores que sysfs vio en 'active' en alguna muestra
      devfreq_mhz     {nodo: (MHz al entrar, MHz máximo)} de devfreq

    `devices` sale de `board.find_engine_devices()`, buscado una vez afuera.
    """

    def __init__(self, reader: TegrastatsReader, poll_interval_s: float,
                 devices: dict[str, str] | None = None):
        self._reader = reader
        self._poll_interval_s = poll_interval_s
        self._devices = devices or {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.peaks: dict = {}
        self.active_devices: set[str] = set()
        self.devfreq_mhz: dict[str, tuple[int, int]] = {}

    def __enter__(self) -> "EngineWatch":
        self._stop.clear()
        self._sample()
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._stop.set()
        self._thread.join()
        self._sample()

    def has_engine(self, name: str) -> bool:
        """Si `tegrastats` publicó ese motor en alguna muestra, encendido o no."""
        return name in self.peaks

    def _poll(self):
        while not self._stop.wait(self._poll_interval_s):
            self._sample()

    def _sample(self):
        for name, engine in self._reader.get_engines().items():
            peak = self.peaks.setdefault(name, {"mhz": 0, "load_pct": None})
            peak["mhz"] = max(peak["mhz"], engine["mhz"])
            if engine["load_pct"] is not None:
                peak["load_pct"] = max(peak["load_pct"] or 0, engine["load_pct"])

        for device, status in board.read_runtime_states(self._devices).items():
            if status == "active":
                self.active_devices.add(device)

        for node, clock in board.read_devfreq().items():
            first_mhz, peak_mhz = self.devfreq_mhz.get(node, (clock["cur_mhz"], 0))
            self.devfreq_mhz[node] = (first_mhz, max(peak_mhz, clock["cur_mhz"]))


def is_active(peak: dict) -> bool:
    return peak["mhz"] > 0 or bool(peak["load_pct"])


def format_tegrastats(watch: EngineWatch) -> str:
    """Los motores que `tegrastats` vio encendidos: 'nvdec 704MHz, vic 25%@729MHz'."""
    published = [name for name in watch.peaks if name not in _ALWAYS_ON]
    if not published:
        return "tegrastats: sin motores (¿sin root?)"
    parts = []
    for name in sorted(published):
        peak = watch.peaks[name]
        if not is_active(peak):
            continue
        load = f"{peak['load_pct']}%@" if peak["load_pct"] is not None else ""
        parts.append(f"{name} {load}{peak['mhz']}MHz")
    return "tegrastats: " + (", ".join(parts) or "ninguno encendido")


def format_sysfs(watch: EngineWatch) -> str:
    """Lo que vio sysfs sin root: motores en 'active' y nodos de devfreq que subieron."""
    parts = [f"{device} active" for device in sorted(watch.active_devices)]
    parts += [f"{node} {first}->{peak}MHz"
              for node, (first, peak) in sorted(watch.devfreq_mhz.items())
              if peak > first and not node.startswith(_GPU_DEVFREQ_PREFIX)]
    return "sysfs: " + (", ".join(parts) or "nada")
