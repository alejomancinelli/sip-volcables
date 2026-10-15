"""
Carga de los motores de la Jetson, leída de `tegrastats`.

Maquinaria genérica: no conoce la app ni su config. `tegrastats` es la herramienta de
NVIDIA y el único lugar donde aparecen juntos el VIC, NVENC, NVDEC, NVJPG, los DLA y la
memoria (EMC): sysfs publica la carga de la GPU sin privilegios, pero el resto de los
motores sólo por debugfs, que pide root.

Cada motor sale como `{"mhz": int, "load_pct": int | None}`:
  - `mhz` es la frecuencia a la que corre; 0 si `tegrastats` lo informa apagado (`off`).
  - `load_pct` es la carga 0–100 cuando `tegrastats` la publica (`EMC_FREQ 3%@3199`), y
    None cuando sólo publica la frecuencia (`NVDEC 704`). Un motor encendido sin carga
    informada está trabajando; cuánto, no se sabe.

Sólo se reportan los motores de `ENGINE_NAMES`, con el nombre en minúscula y sin el
sufijo `_FREQ`: la línea trae además temperaturas, rails y el procesador de audio, que no
son de este módulo. Un motor que el módulo no tiene —el NVENC de un Orin Nano, el segundo
DLA de un NX de 8 GB— no aparece en la línea, así que tampoco aparece en el dict.

Fuera de una Jetson no hay `tegrastats`: el lector no arranca, lo dice en `error` y
devuelve {}. El llamador no pregunta si está disponible.
"""

import re
import shutil
import subprocess
import threading
import time
import weakref

from system.logger import logger

# Motores que se reportan, con el nombre que les da `tegrastats` ya en minúscula.
ENGINE_NAMES = ("gr3d", "emc", "vic", "nvenc", "nvdec", "nvjpg", "nvjpg1", "ofa",
                "nvdla0", "nvdla1", "pva0")

_BINARY = "tegrastats"
_DEFAULT_INTERVAL_MS = 1000
# Una lectura más vieja que esta cantidad de intervalos es de un proceso trabado.
_STALE_INTERVALS = 5
_STOP_TIMEOUT_S = 2.0

# `NOMBRE[_FREQ] valor`, con el valor en una de las formas que usa tegrastats:
#   off · 704 · 3% · 3%@3199 · 0%@[408,408]
# El lookahead exige un espacio o el fin de línea detrás del valor, así que
# `RAM 3527/7620MB` o `VDD_IN 5432mW/5432mW` no se confunden con un motor.
_ENTRY_RE = re.compile(
    r"\b([A-Z][A-Z0-9_]*?)(?:_FREQ)? "
    r"(off|\d+%@\[[\d,]+\]|\d+%@\d+|\d+%|\d+)(?=\s|$)"
)
_LOAD_FREQ_RE = re.compile(r"(\d+)%(?:@\[?([\d,]+)\]?)?")


def _parse_value(raw: str) -> dict:
    """Un valor de tegrastats como `{"mhz", "load_pct"}`."""
    if raw == "off":
        return {"mhz": 0, "load_pct": 0}
    if raw.isdigit():
        return {"mhz": int(raw), "load_pct": None}
    match = _LOAD_FREQ_RE.fullmatch(raw)
    load_pct = max(0, min(100, int(match.group(1))))
    # Con frecuencia por GPC (`[408,408]`) se informa la más alta: es la del motor.
    freqs_mhz = [int(f) for f in (match.group(2) or "").split(",") if f]
    return {"mhz": max(freqs_mhz, default=0), "load_pct": load_pct}


def parse_line(line: str) -> dict:
    """
    Motores de una línea de `tegrastats`: `{nombre: {"mhz", "load_pct"}}`.

    Sólo los de `ENGINE_NAMES` que la línea trae. {} si no trae ninguno: una línea vacía
    o de otra cosa no es un error, es una línea sin motores.
    """
    engines = {}
    for match in _ENTRY_RE.finditer(line):
        name = match.group(1).lower()
        if name in ENGINE_NAMES:
            engines[name] = _parse_value(match.group(2))
    return engines


def _terminate(process: subprocess.Popen):
    """Corta el proceso de `tegrastats` si sigue vivo. Silenciosa."""
    if process.poll() is None:
        try:
            process.terminate()
            process.wait(timeout=_STOP_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError):
            process.kill()


class TegrastatsReader:
    """
    Un proceso `tegrastats` de larga vida y la última línea que publicó, ya traducida.

    Un proceso por lectura costaría un fork cada vez y la primera línea tarda un
    intervalo entero en salir, así que se lanza una vez y un hilo lee su salida. Se
    puede consultar desde cualquier hilo: lo compartido va bajo Lock.

    Si el proceso muere, `get_engines()` devuelve {} y `error` dice por qué; no se
    relanza solo, porque un `tegrastats` que no arranca no va a arrancar al segundo
    intento. El proceso se corta con `stop()` o, si nadie lo llama, cuando el lector
    se libera o termina el intérprete.
    """

    def __init__(self, interval_ms: int = _DEFAULT_INTERVAL_MS):
        self._interval_ms = max(1, int(interval_ms))
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self._engines: dict = {}
        self._last_line = ""
        self._last_line_s: float | None = None
        self._error = ""

    # ── API pública ──────────────────────────────────────────────────────────

    @property
    def error(self) -> str:
        """Por qué no hay lecturas: '' mientras el proceso corre o todavía no arrancó."""
        with self._lock:
            return self._error

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def start(self) -> bool:
        """
        Lanza `tegrastats`. True si quedó corriendo.

        Idempotente: con el proceso ya vivo no lanza otro. Sin `tegrastats` en el PATH
        devuelve False y deja el motivo en `error`, sin propagar.
        """
        if self.is_running:
            return True
        path = shutil.which(_BINARY)
        if path is None:
            self._set_error(f"{_BINARY} no está en el PATH: este equipo no es una Jetson")
            return False
        try:
            # stdin a DEVNULL por lo mismo que las sondas del monitor de sistema: sin
            # consola, el stdin heredado es un handle inválido.
            process = subprocess.Popen(
                [path, "--interval", str(self._interval_ms)],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, text=True, bufsize=1,
            )
        except OSError as e:
            self._set_error(f"no se pudo lanzar {_BINARY}: {e}")
            return False

        self._process = process
        self._set_error("")
        weakref.finalize(self, _terminate, process)
        self._thread = threading.Thread(target=self._read_lines, args=(process,),
                                        name="tegrastats", daemon=True)
        self._thread.start()
        return True

    def stop(self):
        """Corta el proceso y espera al hilo lector. Idempotente."""
        process, self._process = self._process, None
        if process is not None:
            _terminate(process)
        if self._thread is not None:
            self._thread.join(timeout=_STOP_TIMEOUT_S)
            self._thread = None

    def get_engines(self) -> dict:
        """
        Motores de la última línea: `{nombre: {"mhz", "load_pct"}}`.

        {} si todavía no salió ninguna, si el proceso murió o si la última es más vieja
        que `_STALE_INTERVALS` intervalos: una carga congelada se leería como real.
        """
        with self._lock:
            if self._last_line_s is None:
                return {}
            age_s = time.monotonic() - self._last_line_s
            if age_s > _STALE_INTERVALS * self._interval_ms / 1000.0:
                return {}
            return {name: dict(engine) for name, engine in self._engines.items()}

    def get_last_line(self) -> str:
        """La última línea cruda, para diagnóstico: qué publica este equipo y qué no."""
        with self._lock:
            return self._last_line

    # ── Internos ─────────────────────────────────────────────────────────────

    def _read_lines(self, process: subprocess.Popen):
        """Cuerpo del hilo lector: una línea por intervalo hasta que el proceso termina."""
        for line in process.stdout:
            engines = parse_line(line)
            with self._lock:
                self._engines = engines
                self._last_line = line.strip()
                self._last_line_s = time.monotonic()

        return_code = process.wait()
        if self._process is process:
            # Murió solo: nadie llamó a stop(). Lo más común es que necesite root para
            # algún nodo y salga con error en vez de omitir el campo.
            self._set_error(f"{_BINARY} terminó con código {return_code}")
            logger.warning(f"[Tegrastats] {self.error}; los motores quedan sin lectura.")
        with self._lock:
            self._last_line_s = None

    def _set_error(self, message: str):
        with self._lock:
            self._error = message
