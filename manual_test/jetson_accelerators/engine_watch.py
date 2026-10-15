"""
Picos de carga de cada motor mientras corre un bloque, leídos de `tegrastats`.

Lo comparten `probe_report.py` y `stage_bench.py`: las dos necesitan saber si una prueba
encendió el motor que dice usar, y una muestra suelta al final no alcanza —el motor ya se
apagó—. Un hilo consulta el lector a su mismo ritmo y guarda el máximo de cada motor.
"""

import threading

from tools.jetson.tegrastats import TegrastatsReader


class EngineWatch:
    """
    `with EngineWatch(reader): ...` deja en `peaks` el máximo de cada motor en el bloque:
    `{nombre: {"mhz": int, "load_pct": int | None}}`.

    Con el lector sin lecturas, `peaks` queda vacío y `has_readings` en False: no se
    puede afirmar que un motor no se usó si nunca se lo pudo ver.
    """

    def __init__(self, reader: TegrastatsReader, poll_interval_s: float):
        self._reader = reader
        self._poll_interval_s = poll_interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.peaks: dict = {}
        self.has_readings = False

    def __enter__(self) -> "EngineWatch":
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        self._stop.set()
        self._thread.join()
        self._sample()

    def _poll(self):
        while not self._stop.wait(self._poll_interval_s):
            self._sample()

    def _sample(self):
        engines = self._reader.get_engines()
        if not engines:
            return
        self.has_readings = True
        for name, engine in engines.items():
            peak = self.peaks.setdefault(name, {"mhz": 0, "load_pct": None})
            peak["mhz"] = max(peak["mhz"], engine["mhz"])
            if engine["load_pct"] is not None:
                peak["load_pct"] = max(peak["load_pct"] or 0, engine["load_pct"])


def is_active(peak: dict) -> bool:
    return peak["mhz"] > 0 or bool(peak["load_pct"])


def format_peaks(watch: EngineWatch, ignored: tuple[str, ...] = ("emc", "gr3d")) -> str:
    """
    Los motores que se encendieron, en una línea: 'nvdec 704MHz, vic 25%@729MHz'.

    `ignored` deja afuera los que están siempre encendidos (memoria y GPU), que no dicen
    nada de qué motor usó la prueba.
    """
    if not watch.has_readings:
        return "sin lecturas de tegrastats"
    parts = []
    for name, peak in sorted(watch.peaks.items()):
        if name in ignored or not is_active(peak):
            continue
        load = f"{peak['load_pct']}%@" if peak["load_pct"] is not None else ""
        parts.append(f"{name} {load}{peak['mhz']}MHz")
    return ", ".join(parts) or "ningún motor"
