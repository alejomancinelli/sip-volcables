import time
from abc import ABC, abstractmethod
from collections import deque
from typing import NamedTuple

import cv2
import numpy as np

# Valores que acepta la clave `rotation` de cada cámara en config.yaml.
ROTATE_90_CW = "90cw"
ROTATE_90_CCW = "90ccw"
ROTATE_180 = "180"
_ROTATE_MAP = {
    ROTATE_90_CW:  cv2.ROTATE_90_CLOCKWISE,
    ROTATE_90_CCW: cv2.ROTATE_90_COUNTERCLOCKWISE,
    ROTATE_180:    cv2.ROTATE_180,
}

_FPS_WINDOW = 30         # frames sobre los que se promedia fps_estimated
_FPS_STALE_PERIODS = 3   # períodos sin frame tras los que el fps medido pasa a 0
_FPS_STALE_MIN_S = 1.0   # piso de esa espera, para que un tirón no lo haga parpadear


class FrameView(NamedTuple):
    """
    Un rectángulo del frame entregado aparte, a más resolución que el frame: el ROI de un
    pipeline cortado del video nativo antes de reducirlo (ver `decoder.py`).

    `rect_px` es (x, y, ancho, alto) en el espacio del frame que entrega el driver —ya
    reducido y ya rotado—, y es el rectángulo que la imagen cubre de verdad: puede ser un
    píxel más ancho que el pedido, porque el decodificador corta en coordenadas pares.
    La imagen tiene la orientación del frame y su propio tamaño.
    """

    image_bgr: np.ndarray
    rect_px: tuple[int, int, int, int]


class AbstractCameraDriver(ABC):
    """
    Contrato de todo driver de cámara: ciclo de vida, entrega de frames
    always-fresh y estado de salud para telemetría.

    Las implementaciones devuelven cada frame por `_deliver()`, que aplica la
    rotación configurada y marca la llegada para medir el fps real. Ningún driver
    conoce el mapa de rotaciones de OpenCV ni repite el cálculo de fps.

    Un driver que decodifica puede entregar, con cada frame, **vistas**: rectángulos del
    video cortados a resolución nativa (`FrameView`). Las pasa a `_deliver()` junto con el
    frame, que las rota igual que a él, y `get_views()` las devuelve. El que no las tiene
    no hace nada: `get_views()` devuelve un dict vacío.
    """

    # Marcadores de la implementación concreta: el consumidor los lee sin saber
    # qué driver le tocó. Cada driver al que apliquen los pisa en True.
    is_synthetic = False
    is_config_error = False

    def __init__(self, config_cam: dict | None = None):
        # Sesión abierta con el hardware. Lo sostiene cada implementación; quien
        # captura lo lee para saber si tiene que llamar a connect().
        self.is_connected = False

        config = config_cam or {}
        self._rotation = config.get("rotation")
        # `enabled: false` deja la cámara sin capturar sin borrarla del config.
        self._capture_enabled = bool(config.get("enabled", True))
        self._frame_stamps: deque = deque(maxlen=_FPS_WINDOW)
        self._delivered_views: dict[str, FrameView] = {}

    @abstractmethod
    def connect(self) -> bool:
        """Intenta inicializar la conexión por red o bus con la cámara."""

    @abstractmethod
    def disconnect(self):
        """Libera los recursos crudos del host."""

    # Vacío a propósito y no abstracto: un driver cuyo connect() vuelve enseguida no lo
    # redefine.
    def interrupt(self):  # noqa: B027
        """
        Pide, desde otro hilo, que un `connect()` en curso vuelva cuanto antes.

        Existe porque `connect()` puede esperar segundos —una GigE que no aparece— y el
        hilo de captura no ve su propia interrupción mientras espera: el cierre lo daba
        por colgado y Qt abortaba con el hilo vivo. No libera nada, eso sigue siendo
        `disconnect()` del hilo dueño, y es terminal: un driver interrumpido no vuelve a
        conectar. Sin redefinir no hace nada, que alcanza para un `connect()` que vuelve
        enseguida.
        """

    @abstractmethod
    def get_frame(self, timeout_ms: int = 500) -> np.ndarray | None:
        """
        Devuelve el último frame validado, tipo always-fresh, o None.

        **El array es propio y no se vuelve a tocar**: nunca una vista sobre un buffer del
        SDK que se recicle, ni uno que el driver reescriba en el próximo frame. Es lo que
        deja a la captura repartir el mismo array a la UI, al video y a la inferencia sin
        copiarlo, y por eso quien lo recibe tampoco lo modifica in-place.

        Con la captura deshabilitada devuelve None sin tocar el hardware.
        """

    @abstractmethod
    def get_status(self) -> dict:
        """
        Devuelve la salud de la cámara con claves estables:

            connected       : bool — hay sesión abierta con el hardware
            capture_enabled : bool — se están pidiendo frames
            temperature     : °C, 0.0 cuando no hay lectura
            fps_estimated   : fps medido sobre los frames entregados; 0 si dejaron de llegar

        Un driver que no puede operar agrega `error` con el motivo. `connected` y
        `capture_enabled` son independientes: una cámara conectada y deshabilitada
        no es lo mismo que una cámara caída, y el consumidor tiene que poder
        distinguirlas.
        """

    def get_views(self) -> dict[str, FrameView]:
        """
        Las vistas del último frame que devolvió `get_frame()`, por nombre; vacío si no hay.

        Se llama desde el mismo hilo que `get_frame()`, justo después: las vistas son de ese
        frame y de ningún otro. Un frame que llegó sin una de sus vistas —el decodificador
        no la pudo aparear— la tiene ausente, nunca reemplazada por una vieja.
        """
        return self._delivered_views

    # ── Captura habilitada ───────────────────────────────────────────────────

    @property
    def is_capture_enabled(self) -> bool:
        return self._capture_enabled

    def set_capture_enabled(self, enabled: bool):
        """
        Habilita o corta la captura sin cerrar la conexión con la cámara.

        Deshabilitada, `get_frame()` devuelve None y el status lo informa. Los
        drivers que pueden además cortar el grab en el hardware lo hacen en
        `_apply_capture_enabled()`, que es donde se libera ancho de banda del bus.
        """
        if enabled == self._capture_enabled:
            return
        self._capture_enabled = enabled
        self._apply_capture_enabled()

    # Vacío a propósito y no abstracto: el hook es opcional para cada driver.
    def _apply_capture_enabled(self):  # noqa: B027
        """
        Hook para cortar o retomar la captura en el hardware.

        La base ya garantiza que no salgan frames; esto es solo la optimización de
        no moverlos por el bus. Un driver que no pueda hacerlo deja el hook vacío.
        """

    # ── Entrega de frames ────────────────────────────────────────────────────

    def _deliver(self, frame: np.ndarray,
                 views: dict[str, FrameView] | None = None) -> np.ndarray:
        """
        Marca el frame para el cálculo de fps y le aplica la rotación configurada, a él y a
        sus vistas. Las vistas traen el rectángulo ya en el espacio del frame rotado: lo
        único que falta girar es la imagen.
        """
        self._frame_stamps.append(time.monotonic())
        self._delivered_views = {
            name: FrameView(self._rotate_frame(view.image_bgr), view.rect_px)
            for name, view in (views or {}).items()
        }
        return self._rotate_frame(frame)

    def _measured_fps(self) -> float:
        """
        fps real medido sobre las marcas de los últimos frames entregados.

        **Vuelve a 0 cuando los frames dejan de llegar**: pasados unos períodos sin frame
        —con un piso de un segundo— la ventana ya no describe a la cámara, y seguir
        informando los 15 fps de antes hacía que una cámara trabada se leyera sana. Las
        marcas son de reloj monotónico: un ajuste de NTP al arrancar, en un equipo sin RTC,
        no puede inventar ni borrar frames.
        """
        if len(self._frame_stamps) < 2:
            return 0.0
        span_s = self._frame_stamps[-1] - self._frame_stamps[0]
        if span_s <= 0:
            return 0.0
        period_s = span_s / (len(self._frame_stamps) - 1)
        stale_s = max(_FPS_STALE_PERIODS * period_s, _FPS_STALE_MIN_S)
        if time.monotonic() - self._frame_stamps[-1] > stale_s:
            return 0.0
        return round(1.0 / period_s, 1)

    def _rotate_frame(self, frame: np.ndarray) -> np.ndarray:
        """Aplica la rotación configurada; sin `rotation` válida devuelve el frame tal cual."""
        return rotate_frame(frame, self._rotation)


def rotate_frame(frame: np.ndarray, rotation: str | None) -> np.ndarray:
    """
    Gira el frame según el valor de `rotation` del config; sin uno válido lo devuelve tal
    cual. Es la misma rotación que aplica todo driver, para quien arma frames sin uno.
    """
    rotate_code = _ROTATE_MAP.get(rotation)
    if rotate_code is None:
        return frame
    return cv2.rotate(frame, rotate_code)
