"""
Panel de una cámara: el feed en vivo, sus chips de telemetría y el recuadro del ROI.

Un panel por slot de `cameras:`, en una tarjeta: arriba una sola fila con el nombre de la
cámara y sus chips —chicos, para que la fila no le robe alto al video— y debajo el video,
que se lleva todo el resto. Recibe frames y estado por sus slots
—`update_frame()` y `update_status()`—, que es lo que un CaptureThread emite, y no
sabe de dónde salieron: la grilla o la vista de monitor los conecta.

Las regiones de los pipelines se dibujan acá, en modo crudo, porque son una referencia de la
configuración y no del resultado. En modo anotado no: el overlay ya las dibujó en el frame
—con el mismo color y el mismo nombre— y redibujarlas las duplicaría.
"""

import cv2
import numpy as np
from PySide6.QtCore import QEvent, QRect, Qt, Signal, Slot
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from system.config_manager import ConfigManager
from system.inference import overlay
from system.inference.regions import PipelineRegion, clip_roi_px, list_camera_regions
from system.video.abstract_video_server import MODE_RAW

from ui import service_status
from ui.strings import tr
from ui.widgets.region_canvas import paint_region_box
from ui.widgets.status_chip import CHIP_INFO, StatusChip

_MIN_VIDEO_WIDTH_PX = 320
_MIN_VIDEO_HEIGHT_PX = 200
_CARD_MARGIN_PX = 8
_HEADER_SPACING_PX = 6        # entre el nombre y los chips, y entre chip y chip
# Ancho mínimo del panel, fijo a propósito: ver `__init__`.
_MIN_PANEL_WIDTH_PX = 360
_REGION_PEN_WIDTH_PX = 2


class CameraPanel(QWidget):
    """
    Feed de una cámara con su telemetría.

    El título es el `name` de la cámara en el config —`name`—; el slot es la identidad y
    no se muestra.

    **Lo que se muestra y lo que se recorta no son el mismo frame.** `update_frame()`
    pinta lo que el cableado mande según el modo —crudo o anotado—, y `update_raw_frame()`
    guarda el de cámara, que es el único con el que tiene sentido definir un ROI: el
    anotado puede venir con máscaras encima y, si el overlay recorta al ROI, ni siquiera
    tiene el tamaño del sensor. `frame_updated` publica el crudo por eso mismo.
    """

    frame_updated = Signal(object)   # np.ndarray BGR crudo — uno por frame de cámara

    def __init__(self, camera_slot: str, config_manager: ConfigManager, parent=None):
        super().__init__(parent)
        self.setObjectName("cameraPanel")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._config = config_manager
        self._camera_slot = camera_slot
        self._name = str(self._config.get(f"cameras.{camera_slot}.name", camera_slot))
        self._last_frame: np.ndarray | None = None
        self._last_raw_frame: np.ndarray | None = None
        self._is_dark = True
        self._mode = MODE_RAW
        self._regions: list[PipelineRegion] = []
        self._regions_revision: object = object()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(_CARD_MARGIN_PX, _CARD_MARGIN_PX,
                                  _CARD_MARGIN_PX, _CARD_MARGIN_PX)
        layout.setSpacing(_CARD_MARGIN_PX)

        self._video_label = QLabel(tr("camera_no_signal"))
        self._video_label.setObjectName("videoFeed")
        self._video_label.setMinimumSize(_MIN_VIDEO_WIDTH_PX, _MIN_VIDEO_HEIGHT_PX)
        self._video_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self._video_label.setAlignment(Qt.AlignCenter)
        self._video_label.installEventFilter(self)

        self._chip_state = StatusChip(tr("camera_starting"), compact=True)
        self._chip_fps = StatusChip("0.0 FPS", compact=True)
        self._chip_temperature = StatusChip("0.0 °C", compact=True)
        self._chip_illumination = StatusChip("--- %", compact=True)
        for chip in (self._chip_fps, self._chip_temperature, self._chip_illumination):
            chip.set_state(CHIP_INFO)

        # El nombre cede ancho antes que los chips: si no entra se recorta, y los chips
        # —que cambian en caliente— nunca empujan la fila más allá de la tarjeta.
        title = QLabel(self._name)
        title.setObjectName("cameraTitle")
        title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        header = QHBoxLayout()
        header.setSpacing(_HEADER_SPACING_PX)
        header.addWidget(title, stretch=1)
        for chip in (self._chip_state, self._chip_fps,
                     self._chip_temperature, self._chip_illumination):
            header.addWidget(chip)

        layout.addLayout(header)
        layout.addWidget(self._video_label, stretch=1)

        # El ancho que el panel pide es fijo y no sale de su contenido. Sin esto lo fija el
        # texto más largo que tenga adentro —el nombre de la cámara en el título, o el chip
        # de estado, que pasa de «Conectada» a «Mal configurada» en caliente— y dos paneles
        # con el mismo tamaño de imagen terminan con anchos distintos en la grilla: la
        # cámara con problemas se lleva 70 px de la que anda. Se nota cuando el espacio
        # aprieta, que es justo con la barra lateral expandida. Si el texto no entra, se
        # recorta; el estado también se lee por el color del chip.
        #
        # `Ignored` es la mitad que hace falta para que sean iguales de verdad: el mínimo
        # de arriba empareja el piso, pero la grilla reparte el espacio sobrante mirando
        # también el tamaño preferido, que el título vuelve a inflar. Ignorándolo, lo único
        # que decide el ancho es el stretch de la columna, que es 1 para todas.
        self.setMinimumWidth(_MIN_PANEL_WIDTH_PX)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)

    # ── API pública ──────────────────────────────────────────────────────────

    @property
    def camera_slot(self) -> str:
        return self._camera_slot

    @property
    def name(self) -> str:
        """El nombre de la cámara en el config, el que se ve arriba del video."""
        return self._name

    def get_last_frame(self) -> np.ndarray | None:
        """
        Último frame CRUDO de la cámara, o None si todavía no llegó ninguno.

        Crudo y no el que se está mostrando: es el que usa el diálogo de ROI, y un ROI
        definido sobre un frame anotado o recortado sale con coordenadas que no son las
        del sensor.
        """
        return self._last_raw_frame

    @Slot(object)
    def update_raw_frame(self, frame_bgr: np.ndarray | None):
        """Guarda el frame de cámara y lo publica. No dibuja: de eso se ocupa
        `update_frame()`, que puede estar mostrando el anotado."""
        if frame_bgr is None or frame_bgr.size == 0:
            return
        self._last_raw_frame = _to_bgr(frame_bgr)
        self.frame_updated.emit(self._last_raw_frame)

    def set_mode(self, mode: str):
        """El modo de video que se está mostrando. Las regiones se dibujan sólo en crudo."""
        self._mode = mode
        self._repaint()

    def apply_theme(self, dark: bool):
        self._is_dark = dark

    @Slot(object)
    def update_frame(self, frame_bgr: np.ndarray | None):
        """
        Slot de `CaptureThread.frame_ready` (o del frame anotado del motor).

        Con el panel oculto —otra vista al frente, u otra cámara enfocada— sólo guarda el
        frame: pintar lo que nadie ve costaba una conversión de 3 MP por frame en el hilo
        de la GUI. Se pinta al volver a mostrarse.
        """
        if frame_bgr is None or frame_bgr.size == 0:
            return
        self._last_frame = _to_bgr(frame_bgr)
        if self._video_label.isVisible():
            self._repaint()

    def clear_frame(self, message: str = ""):
        """
        Deja el panel sin imagen, con un texto en el lugar del video.

        La hace falta porque el panel retiene el último pixmap que le dieron: mostrando el
        anotado antes de la primera medición quedaría el último frame crudo, que parece la
        inferencia y no lo es.
        """
        self._last_frame = None
        self._video_label.clear()
        self._video_label.setText(message or tr("camera_no_signal"))

    def eventFilter(self, watched, event):
        """
        Reescala la imagen cuando cambia el tamaño del video, y la pinta al mostrarse.

        El pixmap se escala al tamaño que tiene el label en el momento de pintarlo, así
        que sin esto una imagen quieta se queda del tamaño viejo. En crudo no se nota
        —llegan cinco frames por segundo y cada uno se pinta al tamaño nuevo— pero el
        anotado es uno por ciclo: plegar la barra lateral o enfocar una cámara dejaba la
        imagen chica hasta la medición siguiente. Al mostrarse pasa lo mismo: mientras
        estuvo oculto guardó el frame sin pintarlo.
        """
        if watched is self._video_label and (
                event.type() == QEvent.Show
                or (event.type() == QEvent.Resize and event.size() != event.oldSize())):
            self._repaint()
        return super().eventFilter(watched, event)

    def _repaint(self):
        """
        Pinta `_last_frame` al tamaño que tiene ahora el label.

        Se achica con OpenCV **antes** de pasarlo a Qt: convertir el frame entero a pixmap
        para después escalarlo eran 9 MB por frame en el hilo de la GUI, y el label mide
        una fracción de eso.
        """
        frame_bgr = self._last_frame
        if frame_bgr is None:
            return
        height, width = frame_bgr.shape[:2]
        label_size = self._video_label.size()
        scale = min(label_size.width() / width, label_size.height() / height)
        target_px = (max(1, int(width * scale)), max(1, int(height * scale)))
        shown = cv2.resize(frame_bgr, target_px,
                           interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
        image = QImage(shown.data, target_px[0], target_px[1], 3 * target_px[0],
                       QImage.Format_BGR888)
        pixmap = QPixmap.fromImage(image)
        self._draw_regions(pixmap, width, height)
        self._video_label.setPixmap(pixmap)

    @Slot(object)
    def update_status(self, status: dict):
        """
        Slot de `CaptureThread.status_updated`. Claves de `AbstractCameraDriver.get_status()`.

        Qué estado es cada caso lo decide `service_status.describe_camera()`. Una cámara mal
        configurada o desconectada se queda sin video: el último frame haría creer que
        sigue andando.
        """
        text, chip_state = service_status.describe_camera(status)
        self._chip_state.set_state(chip_state, text)
        self._chip_state.setToolTip(str(status.get("error") or ""))
        if status.get("error") or not status.get("connected"):
            self._show_placeholder()
        self._chip_fps.setText(f"{status.get('fps_estimated', 0.0):.1f} FPS")
        self._chip_temperature.setText(f"{status.get('temperature', 0.0):.1f} °C")

    def set_illumination_pct(self, illumination_pct: int):
        """Brillo medio del ROI que midió el motor de inferencia, 0–100."""
        self._chip_illumination.setText(f"{int(illumination_pct)} %")

    # ── Internos ─────────────────────────────────────────────────────────────

    def _show_placeholder(self):
        # Se olvida el frame además de borrarlo: si queda guardado, el repintado por
        # resize lo vuelve a dibujar y la cámara caída muestra su última imagen buena cada
        # vez que se pliega la barra lateral.
        self._last_frame = None
        self._video_label.clear()
        self._video_label.setText(tr("camera_no_signal"))

    def _read_regions(self) -> list[PipelineRegion]:
        """Las regiones de los pipelines prendidos que miran esta cámara; se leen al cambiar el config."""
        revision = getattr(self._config, "revision", None)
        if revision != self._regions_revision:
            self._regions_revision = revision
            self._regions = list_camera_regions(
                self._config.get("inference.pipelines", {}) or {}, self._camera_slot)
        return self._regions

    def _draw_regions(self, pixmap: QPixmap, frame_width_px: int, frame_height_px: int):
        if self._mode != MODE_RAW:
            return
        scale = pixmap.width() / frame_width_px
        painter = None
        for region in self._read_regions():
            rect_px = clip_roi_px(region.roi, frame_width_px, frame_height_px)
            if rect_px is None:
                continue
            if painter is None:
                painter = QPainter(pixmap)
            style = overlay.get_pipeline_style(region.index)
            x_px, y_px, width_px, height_px = rect_px
            paint_region_box(
                painter,
                QRect(int(x_px * scale), int(y_px * scale),
                      int(width_px * scale), int(height_px * scale)),
                region.name, style.color_bgr, style.is_dashed, _REGION_PEN_WIDTH_PX)
        if painter is not None:
            painter.end()


def _to_bgr(frame: np.ndarray) -> np.ndarray:
    """Lleva un frame gris o de un canal a BGR contiguo, que es lo que espera QImage."""
    if frame.ndim == 2:
        frame = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
    elif frame.ndim == 3 and frame.shape[2] == 1:
        frame = cv2.cvtColor(frame[:, :, 0], cv2.COLOR_GRAY2BGR)
    return np.ascontiguousarray(frame)
