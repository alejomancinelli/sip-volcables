"""
Prueba manual: las vistas del decodificador contra cámaras de verdad, en la Jetson.

Uso — hay que invocarlo con el intérprete del venv, no con el `.py` a secas:

    .venv/bin/python manual_test/decoder_views/view_check.py snapshot
    .venv/bin/python manual_test/decoder_views/view_check.py pairing --duration 600
    .venv/bin/python manual_test/decoder_views/view_check.py reopen --cycles 20

    --config ruta     cámaras a probar; sin esto, el `config.yaml` de esta carpeta.
    --camera nombre   una sola de sus cámaras; sin esto, todas a la vez.

Las tres pruebas usan el driver RTSP de la app tal cual —el mismo decodificador, el mismo
apareo y la misma rotación—, así que lo que pasa acá es lo que pasa en la línea:

    snapshot   Un frame de cada cámara con el rectángulo dibujado, y su vista, en
               `output/<fecha>_<hora>/`. Mirar que la vista sea la zona del rectángulo, más
               nítida, y que no esté corrida ni estirada.
    pairing    Corre `--duration` segundos y cuenta, por cámara, frames, frames con todas
               sus vistas y vistas que faltaron (`view_misses`), además del CPU del proceso,
               la memoria y los descriptores abiertos. Medido en el desarrollo: 0 faltas en
               11 min con dos cámaras.
    reopen     Abre y cierra cada cámara `--cycles` veces y cuenta los descriptores abiertos
               después de cada vuelta. Tienen que quedar planos: un probe que retiene el
               pipeline deja dos por reapertura, y un enlace que se corta cada 30 s agota el
               `ulimit` en horas.

Los descriptores se cuentan en /proc/self/fd, que es de Linux: en otro sistema esa columna
sale vacía. Para la carga del VIC y de NVDEC hace falta root, en otra terminal:

    sudo tegrastats --interval 1000 | grep -oE "(VIC|NVDEC)[A-Z_]* [0-9%@\\[\\],]+"
"""

import argparse
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2
import psutil

# La raíz del repo es el primer ancestro que contiene `system/`, no un número fijo de
# niveles: así el script sobrevive a que lo muevan de carpeta.
_REPO_ROOT = next(
    (p for p in Path(__file__).resolve().parents if (p / "system").is_dir()),
    None,
)
if _REPO_ROOT is None:
    raise SystemExit("No se encontró la raíz del repo: ningún directorio padre tiene system/.")
sys.path.insert(0, str(_REPO_ROOT))

from system.config_manager import ConfigManager  # noqa: E402
from system.env import load_env_file  # noqa: E402
from tools.camera.camera_factory import create_camera  # noqa: E402

_HERE = Path(__file__).resolve().parent
_DEFAULT_CONFIG = _HERE / "config.yaml"
_OUTPUT_DIR = _HERE / "output"
_RUN_DIR_FORMAT = "%Y-%m-%d_%H-%M-%S"
_FD_DIR = "/proc/self/fd"

_FRAME_TIMEOUT_MS = 500
_SNAPSHOT_TIMEOUT_S = 20.0      # el primer frame de un RTSP tarda lo que el handshake
_REPORT_PERIOD_S = 30.0
_RECT_COLOR_BGR = (0, 255, 255)
_RECT_THICKNESS_PX = 1


def _parse_args(argv: list) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prueba las vistas del decodificador.")
    parser.add_argument("mode", choices=("snapshot", "pairing", "reopen"))
    parser.add_argument("--config", type=Path, default=_DEFAULT_CONFIG)
    parser.add_argument("--camera", default=None)
    parser.add_argument("--duration", type=float, default=600.0)
    parser.add_argument("--cycles", type=int, default=20)
    return parser.parse_args(argv)


def _count_fds() -> int | None:
    """Descriptores abiertos por este proceso, o None fuera de Linux."""
    return len(os.listdir(_FD_DIR)) if os.path.isdir(_FD_DIR) else None


def _connect(name: str, section: dict):
    driver = create_camera(section)
    if not driver.connect():
        driver.disconnect()
        raise SystemExit(f"{name}: no abrió. Revisar la dirección, el `.env` y el log.")
    return driver


# ── snapshot ─────────────────────────────────────────────────────────────────

def _run_snapshot(cameras: dict):
    run_dir = _OUTPUT_DIR / datetime.now().strftime(_RUN_DIR_FORMAT)
    run_dir.mkdir(parents=True, exist_ok=True)
    for name, section in cameras.items():
        driver = _connect(name, section)
        try:
            frame, views = _wait_for_views(driver, len(section["decode"].get("views", {})))
        finally:
            driver.disconnect()
        if frame is None:
            print(f"{name}: sin frame con todas sus vistas en {_SNAPSHOT_TIMEOUT_S:g} s.")
            continue
        marked = frame.copy()
        for view_name, view in views.items():
            x, y, width, height = view.rect_px
            cv2.rectangle(marked, (x, y), (x + width - 1, y + height - 1), _RECT_COLOR_BGR,
                          _RECT_THICKNESS_PX)
            cv2.imwrite(str(run_dir / f"{name}_{view_name}.png"), view.image_bgr)
            print(f"{name}/{view_name}: vista de {view.image_bgr.shape[1]}x"
                  f"{view.image_bgr.shape[0]}, cubre {view.rect_px} del frame de "
                  f"{frame.shape[1]}x{frame.shape[0]}.")
        cv2.imwrite(str(run_dir / f"{name}_frame.png"), marked)
    print(f"Imágenes en {run_dir}")


def _wait_for_views(driver, expected: int) -> tuple:
    """El primer frame que llega con todas sus vistas; (None, {}) si no llega a tiempo."""
    deadline_s = time.monotonic() + _SNAPSHOT_TIMEOUT_S
    while time.monotonic() < deadline_s:
        frame = driver.get_frame(timeout_ms=_FRAME_TIMEOUT_MS)
        views = driver.get_views()
        if frame is not None and len(views) == expected:
            return frame, views
    return None, {}


# ── pairing ──────────────────────────────────────────────────────────────────

class _Counts:
    def __init__(self):
        self.frames = 0
        self.complete = 0
        self.lock = threading.Lock()


def _consume(driver, expected: int, counts: _Counts, stop: threading.Event):
    while not stop.is_set():
        if driver.get_frame(timeout_ms=_FRAME_TIMEOUT_MS) is None:
            continue
        with counts.lock:
            counts.frames += 1
            counts.complete += len(driver.get_views()) == expected


def _run_pairing(cameras: dict, duration_s: float):
    process = psutil.Process()
    drivers = {name: _connect(name, section) for name, section in cameras.items()}
    counts = {name: _Counts() for name in drivers}
    stop = threading.Event()
    threads = [threading.Thread(target=_consume, daemon=True, args=(
        driver, len(cameras[name]["decode"].get("views", {})), counts[name], stop))
        for name, driver in drivers.items()]
    fds_start = _count_fds()
    process.cpu_percent()
    started_s = time.monotonic()
    for thread in threads:
        thread.start()
    try:
        while time.monotonic() - started_s < duration_s:
            time.sleep(min(_REPORT_PERIOD_S, duration_s - (time.monotonic() - started_s)))
            _report_pairing(drivers, counts, process, time.monotonic() - started_s)
    finally:
        stop.set()
        for thread in threads:
            thread.join()
        for driver in drivers.values():
            driver.disconnect()
    print(f"Descriptores: {fds_start} al empezar, {_count_fds()} al terminar.")


def _report_pairing(drivers: dict, counts: dict, process: psutil.Process, elapsed_s: float):
    print(f"── {elapsed_s:.0f} s — CPU del proceso {process.cpu_percent():.1f} % de un núcleo, "
          f"RSS {process.memory_info().rss / 1e6:.0f} MB, descriptores {_count_fds()}")
    for name, driver in drivers.items():
        status = driver.get_status()
        with counts[name].lock:
            frames, complete = counts[name].frames, counts[name].complete
        print(f"   {name}: {frames} frames ({frames / elapsed_s:.1f} fps), {complete} con "
              f"todas sus vistas, view_misses {status.get('view_misses', '-')}")


# ── reopen ───────────────────────────────────────────────────────────────────

def _run_reopen(cameras: dict, cycles: int):
    for name, section in cameras.items():
        expected = len(section["decode"].get("views", {}))
        fds = [_count_fds()]
        for cycle in range(1, cycles + 1):
            driver = _connect(name, section)
            frame, _ = _wait_for_views(driver, expected)
            driver.disconnect()
            fds.append(_count_fds())
            state = "con vistas" if frame is not None else "SIN frame con vistas"
            print(f"{name}: vuelta {cycle}/{cycles}, {state}, descriptores {fds[-1]}")
        if fds[0] is not None:
            print(f"{name}: {fds[0]} -> {fds[-1]} descriptores en {cycles} reaperturas "
                  f"({'planos' if fds[-1] <= fds[1] else 'CRECEN'} después de la primera).")


def main(argv: list) -> int:
    args = _parse_args(argv)
    load_env_file()
    cameras = ConfigManager(str(args.config)).get("cameras", {}) or {}
    if args.camera:
        if args.camera not in cameras:
            raise SystemExit(f"No hay una cámara '{args.camera}' en {args.config}.")
        cameras = {args.camera: cameras[args.camera]}
    if args.mode == "snapshot":
        _run_snapshot(cameras)
    elif args.mode == "pairing":
        _run_pairing(cameras, args.duration)
    else:
        _run_reopen(cameras, args.cycles)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
