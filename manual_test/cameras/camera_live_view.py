"""
Visor en vivo para probar los drivers de cámara contra hardware real.

Uso — hay que invocarlo con el intérprete del venv, no con el `.py` a secas:

    Windows:  .venv\\Scripts\\python.exe manual_test\\cameras\\camera_live_view.py [nombre]
    Linux:    .venv/bin/python manual_test/cameras/camera_live_view.py [nombre]

Ejecutar `.\\manual_test\\cameras\\camera_live_view.py` deja que Windows elija el
intérprete por asociación de archivo, que puede no tener consola (no se ve nada) y no
es el del venv, así que los SDK de cámara no están. `py` tampoco sirve: toma el Python
del sistema, sin pypylon ni stapipy.

Lee el config.yaml que tiene al lado (no el de la app) y arma el driver con la misma
fábrica que usa el sistema, así se prueba el camino real y no una instancia a mano.
Sin argumento toma la primera cámara del archivo.

Las credenciales de una cámara RTSP no están en ese archivo: salen del `.env` de la raíz
del repo, que este script carga por su cuenta porque no pasa por `main.py`.

Teclas:
    q / ESC   salir (también sirve cerrar la ventana)
    e         habilitar / deshabilitar la captura

El frame se muestra tal como lo devuelve el driver: la ventana se encoge para que
entre en pantalla, pero la imagen no se toca.

Sin ventana —el OpenCV del venv de la Jetson es el headless, y una Jetson de planta suele
no tener pantalla— corre igual: imprime el estado con el tamaño del frame y guarda un frame
cada `_SNAPSHOT_INTERVAL_S` en `output/`, que no se versiona. Termina con Ctrl+C.
"""

import sys
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import yaml

# La raíz del repo es el primer ancestro que contiene `tools/`, no un número fijo de
# niveles: así el script sobrevive a que lo muevan de carpeta.
_REPO_ROOT = next(
    (p for p in Path(__file__).resolve().parents if (p / "tools").is_dir()),
    None,
)
if _REPO_ROOT is None:
    raise SystemExit("No se encontró la raíz del repo: ningún directorio padre tiene tools/.")
sys.path.insert(0, str(_REPO_ROOT))

from system.env import load_env_file  # noqa: E402
from tools.camera.abstract_driver import AbstractCameraDriver  # noqa: E402
from tools.camera.camera_factory import create_camera  # noqa: E402

_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"
_OUTPUT_DIR = Path(__file__).resolve().parent / "output"

_WINDOW_ID = "camera_live_view"
_DISPLAY_MAX_WIDTH_PX = 1280   # ancho máximo de la ventana, no del frame
_FRAME_TIMEOUT_MS = 1000
_STATUS_INTERVAL_S = 1.0       # cada cuánto se imprime el estado en consola
_RECONNECT_AFTER_MISSES = 30   # frames nulos seguidos antes de reintentar connect()
_QUIT_KEYS = (ord("q"), 27)    # q y ESC
_TOGGLE_CAPTURE_KEY = ord("e")
_SNAPSHOT_INTERVAL_S = 5.0     # sin ventana, cada cuánto se guarda un frame en output/


def _load_cameras() -> dict:
    """Devuelve la sección `cameras` de la config de prueba."""
    config = yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    return config.get("cameras") or {}


def _resize_window(frame: np.ndarray):
    """Ajusta la ventana al frame, encogiéndola si es más ancho que la pantalla."""
    height_px, width_px = frame.shape[:2]
    scale = min(1.0, _DISPLAY_MAX_WIDTH_PX / width_px)
    cv2.resizeWindow(_WINDOW_ID, int(width_px * scale), int(height_px * scale))


def _print_status(driver: AbstractCameraDriver, frame_shape: tuple | None):
    status = driver.get_status()
    size = f"{frame_shape[1]}x{frame_shape[0]}" if frame_shape else "-"
    print(
        f"  conectada {status.get('connected')} | "
        f"captura {'ON ' if status.get('capture_enabled') else 'OFF'} | "
        f"fps {status.get('fps_estimated', 0.0):5.1f} | "
        f"frame {size} | "
        f"temperatura {status.get('temperature', 0.0):5.1f} °C"
    )


def _count_miss(driver: AbstractCameraDriver, misses: int) -> int:
    """Suma un frame que no llegó; pasado el límite reintenta connect(). Devuelve la cuenta."""
    # Sin captura no llegan frames por diseño: no es una falla que reconectar.
    if not driver.is_capture_enabled:
        return misses
    misses += 1
    if misses >= _RECONNECT_AFTER_MISSES:
        print(f"Sin frames desde hace {misses} intentos: reintentando connect()...")
        driver.connect()
        return 0
    return misses


def _has_gui() -> bool:
    """
    Si este OpenCV puede abrir ventanas. El del venv de la Jetson es el headless, que no
    puede, y una Jetson de planta suele no tener pantalla.
    """
    try:
        cv2.namedWindow(_WINDOW_ID, cv2.WINDOW_NORMAL)
    except cv2.error:
        return False
    return True


def _run_headless(driver: AbstractCameraDriver, name: str):
    """
    Sin ventana: el estado por consola y un frame cada `_SNAPSHOT_INTERVAL_S` en output/,
    para mirar después qué entregó el driver. Termina con Ctrl+C.
    """
    _OUTPUT_DIR.mkdir(exist_ok=True)
    print(f"OpenCV sin interfaz gráfica: no hay ventana. Estado cada "
          f"{_STATUS_INTERVAL_S:g} s y un frame cada {_SNAPSHOT_INTERVAL_S:g} s en "
          f"{_OUTPUT_DIR}. Ctrl+C para terminar.")
    misses = 0
    frame_shape = None
    last_status_s = last_snapshot_s = time.time()

    while True:
        frame = driver.get_frame(timeout_ms=_FRAME_TIMEOUT_MS)
        now_s = time.time()
        if frame is None:
            misses = _count_miss(driver, misses)
        else:
            misses = 0
            frame_shape = frame.shape
            if now_s - last_snapshot_s >= _SNAPSHOT_INTERVAL_S:
                path = _OUTPUT_DIR / f"{name}_{datetime.now():%H%M%S}.jpg"
                cv2.imwrite(str(path), frame)
                print(f"  frame guardado: {path.name}")
                last_snapshot_s = now_s

        if now_s - last_status_s >= _STATUS_INTERVAL_S:
            _print_status(driver, frame_shape)
            last_status_s = now_s


def _show_live_feed(driver: AbstractCameraDriver, name: str):
    """
    Muestra el feed hasta que el usuario cierre. Reintenta connect() si el driver cae.
    La ventana ya la abrió `_has_gui()`.
    """
    cv2.setWindowTitle(_WINDOW_ID, f"{name} - {type(driver).__name__}")

    is_window_sized = False
    misses = 0
    frame_shape = None
    last_status_s = time.time()

    while True:
        frame = driver.get_frame(timeout_ms=_FRAME_TIMEOUT_MS)

        if frame is None:
            misses = _count_miss(driver, misses)
        else:
            misses = 0
            frame_shape = frame.shape
            if not is_window_sized:
                _resize_window(frame)
                is_window_sized = True
            cv2.imshow(_WINDOW_ID, frame)

        if time.time() - last_status_s >= _STATUS_INTERVAL_S:
            _print_status(driver, frame_shape)
            last_status_s = time.time()

        key = cv2.waitKey(1) & 0xFF
        if key in _QUIT_KEYS:
            break
        if key == _TOGGLE_CAPTURE_KEY:
            driver.set_capture_enabled(not driver.is_capture_enabled)
            print(f"Captura -> {'habilitada' if driver.is_capture_enabled else 'deshabilitada'}")

        # La X de la ventana también termina el script.
        if cv2.getWindowProperty(_WINDOW_ID, cv2.WND_PROP_VISIBLE) < 1:
            break


def main() -> int:
    # Antes de armar el driver: la cámara RTSP resuelve sus credenciales al construirse, y
    # este script no pasa por `main.py`, que es donde la app carga el archivo.
    load_env_file()

    cameras = _load_cameras()
    if not cameras:
        print(f"{_CONFIG_PATH} no tiene ninguna cámara en 'cameras'.")
        return 2

    name = sys.argv[1] if len(sys.argv) > 1 else next(iter(cameras))
    if name not in cameras:
        print(f"'{name}' no está en {_CONFIG_PATH}. Disponibles: {', '.join(cameras)}.")
        return 2

    driver = create_camera(cameras[name])
    print(f"Cámara '{name}' -> {type(driver).__name__}")
    if driver.is_synthetic:
        print("Driver sintético: los frames no vienen de una cámara real.")

    if not driver.connect():
        status = driver.get_status()
        if driver.is_config_error:
            print(f"Config inválida: {status.get('error')}")
        else:
            print(f"No se pudo conectar. Estado: {status}")
        return 1

    if not driver.is_capture_enabled:
        print("Captura deshabilitada por config (`enabled: false`): 'e' la habilita.")

    has_gui = _has_gui()
    try:
        if has_gui:
            _show_live_feed(driver, name)
        else:
            _run_headless(driver, name)
    except KeyboardInterrupt:
        print()
    finally:
        driver.disconnect()
        if has_gui:
            cv2.destroyAllWindows()

    return 0


if __name__ == "__main__":
    sys.exit(main())
