"""
Prueba manual: qué aceleradores tiene esta Jetson y si cada uno anda de verdad.

Uso — con el intérprete del venv, en la Jetson y sin sudo (la app tampoco lo tiene):

    .venv/bin/python manual_test/jetson_accelerators/probe_report.py

Lee la sección `probe` del config.yaml que tiene al lado. Imprime, en este orden:

  1. El equipo: módulo, L4T, modo de potencia, si los relojes están fijados
     (`jetson_clocks`) y los contadores de sobrecorriente.
  2. Las librerías de Python del venv: si OpenCV trae GStreamer o CUDA, VPI y cuántos DLA
     ve TensorRT.
  3. Los elementos de GStreamer instalados.
  4. Qué publica `tegrastats` corriendo como este usuario: la línea cruda y los motores
     que se entendieron de ella.
  5. Un pipeline corto por motor (VIC, NVENC, NVDEC, NVJPG) con `tegrastats` mirando: si
     terminó bien, cuánto tardó y **qué motor se encendió mientras corría**.

Qué mirar:
    - El punto 4. Si la línea cruda no trae `NVENC`, `NVDEC`, `NVJPG`, `VIC` o `EMC_FREQ`,
      este usuario no los ve sin root y la app tampoco los va a ver: comparar con
      `sudo tegrastats --interval 1000` y pasar las dos líneas. De eso depende de dónde
      lee la carga de los motores la telemetría.
    - El punto 5. Un pipeline que pasa pero no enciende su motor está cayendo a software
      sin avisar. El caso esperable es NVJPG en un Orin Nano, que no tiene codificador
      JPEG; en el Orin NX los cuatro tienen que verse.
    - Que los relojes digan fijados después de `sudo jetson_clocks`, y que el modo de
      potencia sea el de producción (20W en el NX de volcables).
    - Que los contadores de sobrecorriente no suban entre dos corridas.

Pasarle la salida completa a quien sigue con la fase 0: las rutas y las formas de la línea
que no coinciden con este equipo se corrigen en `tools/jetson/`.
"""

import platform
import sys
import time
from pathlib import Path

# La raíz del repo es el primer ancestro que contiene `system/`, no un número fijo de
# niveles: así el script sobrevive a que lo muevan de carpeta.
_REPO_ROOT = next(
    (p for p in Path(__file__).resolve().parents if (p / "system").is_dir()),
    None,
)
if _REPO_ROOT is None:
    raise SystemExit("No se encontró la raíz del repo: ningún directorio padre tiene system/.")
sys.path.insert(0, str(_REPO_ROOT))

from engine_watch import EngineWatch, format_peaks, is_active  # noqa: E402
from system.config_manager import ConfigManager                # noqa: E402
from tools.jetson import board, capabilities                   # noqa: E402
from tools.jetson.tegrastats import TegrastatsReader           # noqa: E402

_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"
_FIRST_LINE_TIMEOUT_S = 5.0

# Qué motor tiene que encenderse en cada prueba, con el nombre que le da tegrastats. El
# NX y el AGX tienen dos instancias de NVJPG: cualquiera de las dos vale.
_EXPECTED_ENGINES = {
    "vic": ("vic",),
    "nvenc_h264": ("nvenc",),
    "nvenc_h265": ("nvenc",),
    "nvdec_h264": ("nvdec",),
    "nvjpg_encode": ("nvjpg", "nvjpg1"),
    "nvjpg_decode": ("nvjpg", "nvjpg1"),
}


def _print_board():
    print("== Equipo ==")
    print(f"  módulo            {board.read_module() or '(no es una Jetson)'}")
    print(f"  L4T               {board.read_l4t_release() or '-'}")
    print(f"  Python            {platform.python_version()} ({sys.executable})")
    mode = board.read_power_mode()
    print(f"  modo de potencia  {mode.get('name') or '?'} (id {mode.get('id', '-')})"
          if mode else "  modo de potencia  - (sin nvpmodel)")

    clocks = board.read_clocks()
    pinned = board.are_clocks_pinned(clocks)
    print(f"  relojes fijados   {'-' if pinned is None else ('sí' if pinned else 'no')}")
    for section in ("cpu", "gpu"):
        for name, clock in clocks[section].items():
            print(f"    {section} {name:16s} {clock['cur_mhz']:5d} MHz "
                  f"(mín {clock['min_mhz']}, máx {clock['max_mhz']})")

    counts = board.read_overcurrent_counts()
    print("  sobrecorriente    "
          + (", ".join(f"{name}={count}" for name, count in counts.items()) or "-"))


def _print_python_libs():
    print("\n== Librerías del venv ==")
    libs = capabilities.probe_python_libs()
    opencv = libs["opencv"]
    print(f"  OpenCV    {opencv['version'] or opencv['error']}  "
          f"GStreamer: {opencv['gstreamer'] or '?'}  CUDA: {opencv['cuda'] or '?'}")
    vpi = libs["vpi"]
    print(f"  VPI       {vpi['version'] + ' ' + str(vpi['backends']) if vpi['version'] else vpi['error']}")
    tensorrt = libs["tensorrt"]
    print(f"  TensorRT  {tensorrt['version'] + ', DLA: ' + str(tensorrt['dla_cores']) if tensorrt['version'] else tensorrt['error']}")


def _print_elements():
    print("\n== Elementos de GStreamer ==")
    elements = capabilities.find_elements()
    for name, found in elements.items():
        print(f"  {name:16s} {'presente' if found else 'ausente'}")


def _start_tegrastats(interval_ms: int) -> TegrastatsReader:
    print("\n== tegrastats como este usuario ==")
    reader = TegrastatsReader(interval_ms=interval_ms)
    if not reader.start():
        print(f"  no arrancó: {reader.error}")
        return reader
    deadline_s = time.monotonic() + _FIRST_LINE_TIMEOUT_S
    while not reader.get_last_line() and time.monotonic() < deadline_s:
        time.sleep(0.05)
    print(f"  línea cruda: {reader.get_last_line() or '(ninguna en ' + str(_FIRST_LINE_TIMEOUT_S) + ' s)'}")
    print(f"  motores:     {reader.get_engines() or '(ninguno)'}")
    if reader.error:
        print(f"  error:       {reader.error}")
    return reader


def _verdict(check: str, watch: EngineWatch, error: str) -> str:
    """'motor visto', 'NO SE ENCENDIÓ' o 'sin lecturas', para la columna final."""
    if error:
        return "-"
    if not watch.has_readings:
        return "sin lecturas"
    seen = any(is_active(watch.peaks[name])
               for name in _EXPECTED_ENGINES[check] if name in watch.peaks)
    return "motor visto" if seen else "NO SE ENCENDIÓ"


def _run_checks(config: ConfigManager, reader: TegrastatsReader, interval_s: float):
    width_px = int(config.get("probe.width_px", 1920))
    height_px = int(config.get("probe.height_px", 1080))
    frame_count = int(config.get("probe.frame_count", 120))
    print(f"\n== Un pipeline por motor ({frame_count} frames de {width_px}x{height_px}) ==")

    for check, launch in capabilities.build_pipeline_checks(
            width_px, height_px, frame_count).items():
        start_s = time.perf_counter()
        with EngineWatch(reader, interval_s) as watch:
            error = capabilities.check_pipeline(launch)
        elapsed_s = time.perf_counter() - start_s
        status = "PASA" if not error else "FALLA"
        print(f"  {check:13s} {status:5s} {elapsed_s:6.2f} s  "
              f"{_verdict(check, watch, error):15s} {format_peaks(watch)}")
        if error:
            print(f"      {error}")


def main() -> int:
    if not _CONFIG_PATH.exists():
        print(f"Falta {_CONFIG_PATH}.")
        return 2
    config = ConfigManager(str(_CONFIG_PATH))
    interval_ms = int(config.get("tegrastats_interval_ms", 100))

    _print_board()
    _print_python_libs()
    _print_elements()
    reader = _start_tegrastats(interval_ms)
    try:
        _run_checks(config, reader, interval_ms / 1000.0)
    finally:
        reader.stop()

    counts = board.read_overcurrent_counts()
    if counts:
        print("\n  sobrecorriente al terminar: "
              + ", ".join(f"{name}={count}" for name, count in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
