"""
Diálogo de nivel: marcar con el mouse una altura de referencia sobre el frame de una cámara.

Es el hermano del diálogo de ROI para lo que no es un rectángulo: un límite, un nivel, una
altura contra la que se mide algo. La altura es una fila del frame y se usa como una línea
horizontal de borde a borde, así que se guarda un solo número. Trabaja sobre el frame en
vivo —quien lo abre le conecta la señal de frames del panel— y la fila que guarda es del
frame de cámara, en píxeles: la misma que lee el motor.

Guarda la fila como un entero en la clave que se le dé, y persiste el archivo al aceptar.
**La clave es un parámetro**: quien pide el nivel es la pestaña dueña de esa sección, y este
diálogo no sabe qué se mide contra él.
"""

import cv2
import numpy as np
from PySide6.QtCore import QPointF, Qt, Slot
from PySide6.QtGui import QImage, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSpinBox,
    QVBoxLayout,
)

from system.config_manager import ConfigManager

from ui import theme
from ui.strings import tr
from ui.widgets.form import add_hint_row

_MIN_CANVAS_WIDTH_PX = 640
_MIN_CANVAS_HEIGHT_PX = 400
_MAX_FRAME_SIDE_PX = 8192
_LEVEL_PEN_WIDTH_PX = 3
_DIALOG_WIDTH_PX = 760
_DIALOG_HEIGHT_PX = 560


class LevelDialog(QDialog):
    """
    Diálogo para marcar una altura de referencia de una cámara.

    Se abre con `exec()`. Guardar escribe la fila en `config_key` y persiste; cancelar no
    deja nada. Los frames entran por `update_frame()`, que es el slot que hay que conectar
    a la señal de frames de esa cámara. `title_key` es la clave de idiomas del título, para
    que la ventana diga qué altura se está marcando.
    """

    def __init__(self, config_manager: ConfigManager, camera_slot: str, config_key: str,
                 parent=None, *, title_key: str = "level_title"):
        super().__init__(parent)
        self._config = config_manager
        self._config_key = config_key
        self._last_frame: np.ndarray | None = None

        camera_name = str(self._config.get(f"cameras.{camera_slot}.name", "") or camera_slot)
        self.setWindowTitle(f"{tr(title_key)} — {camera_name}")
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        self.resize(_DIALOG_WIDTH_PX, _DIALOG_HEIGHT_PX)

        self._canvas = _LevelCanvas()
        self._canvas.level_changed = self._on_level_drawn

        layout = QVBoxLayout(self)
        layout.addWidget(self._canvas, stretch=1)
        layout.addWidget(self._build_coords_box())
        layout.addLayout(self._build_button_row())

        current = self._config.get(self._config_key)
        self._y_spin.setValue(int(current) if isinstance(current, int) else 0)
        self._canvas.set_y_px(self._y_spin.value())

    # ── API pública ──────────────────────────────────────────────────────────

    @Slot(object)
    def update_frame(self, frame_bgr: np.ndarray | None):
        """Slot para la señal de frames de la cámara. Ignora un frame vacío."""
        if frame_bgr is None or frame_bgr.size == 0:
            return
        self._last_frame = frame_bgr
        self._canvas.set_frame(frame_bgr)

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_coords_box(self) -> QGroupBox:
        box = QGroupBox(tr("level_box_coords"))
        form = QFormLayout(box)
        self._y_spin = QSpinBox()
        self._y_spin.setRange(0, _MAX_FRAME_SIDE_PX)
        self._y_spin.valueChanged.connect(self._on_spin_changed)
        form.addRow(QLabel(tr("level_y")), self._y_spin)
        add_hint_row(form, tr("level_hint"))
        return box

    def _build_button_row(self) -> QHBoxLayout:
        cancel_button = QPushButton(tr("dialog_cancel"))
        cancel_button.setObjectName("cancelButton")
        cancel_button.clicked.connect(self.reject)

        save_button = QPushButton(tr("level_save"))
        save_button.setObjectName("saveButton")
        save_button.clicked.connect(self._on_save_clicked)

        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(cancel_button)
        row.addWidget(save_button)
        return row

    # ── Internos ─────────────────────────────────────────────────────────────

    def _on_level_drawn(self, y_px: int):
        self._y_spin.blockSignals(True)
        self._y_spin.setValue(y_px)
        self._y_spin.blockSignals(False)

    def _on_spin_changed(self):
        """Un valor tipeado a mano también mueve la línea."""
        self._canvas.set_y_px(self._y_spin.value())
        if self._last_frame is not None:
            self._canvas.set_frame(self._last_frame)

    def _on_save_clicked(self):
        self._config.set(self._config_key, self._y_spin.value())
        self._config.save()
        self.accept()


class _LevelCanvas(QLabel):
    """
    Lienzo del frame con la altura marcable con el mouse: un click la ubica y arrastrar la
    mueve, con la línea siguiendo al puntero.

    Guarda la altura en filas del frame y la reproyecta en cada frame recibido, así sobrevive
    a que la ventana cambie de tamaño. `level_changed` es un callback `(y_px)` que el diálogo
    asigna.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("videoFeed")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(_MIN_CANVAS_WIDTH_PX, _MIN_CANVAS_HEIGHT_PX)

        self.level_changed = None
        self._y_px = 0
        self._is_dragging = False
        self._last_frame: np.ndarray | None = None
        self._scale_y = 1.0
        self._offset_x_px = 0
        self._offset_y_px = 0
        self._image_width_px = 0
        self._frame_height_px = 0

    def set_y_px(self, y_px: int):
        """Fija la altura en filas del frame; se dibuja con el próximo frame."""
        self._y_px = int(y_px)

    def set_frame(self, frame_bgr: np.ndarray):
        """Escala el frame al lienzo, calcula la conversión y dibuja la línea."""
        if frame_bgr.ndim == 2:
            frame_bgr = cv2.cvtColor(frame_bgr, cv2.COLOR_GRAY2BGR)
        frame_bgr = np.ascontiguousarray(frame_bgr)
        self._last_frame = frame_bgr
        frame_height_px, frame_width_px = frame_bgr.shape[:2]

        image = QImage(frame_bgr.data, frame_width_px, frame_height_px,
                       3 * frame_width_px, QImage.Format_BGR888)
        scaled = QPixmap.fromImage(image).scaled(
            self.size(), Qt.KeepAspectRatio, Qt.FastTransformation
        )
        if scaled.width() <= 0 or scaled.height() <= 0:
            return

        self._frame_height_px = frame_height_px
        self._scale_y = frame_height_px / scaled.height()
        self._offset_x_px = (self.width() - scaled.width()) // 2
        self._offset_y_px = (self.height() - scaled.height()) // 2
        self._image_width_px = scaled.width()

        canvas = QPixmap(self.size())
        canvas.fill(Qt.black)
        painter = QPainter(canvas)
        painter.drawPixmap(self._offset_x_px, self._offset_y_px, scaled)
        y_widget = self._y_px / self._scale_y + self._offset_y_px
        painter.setPen(QPen(theme.get_color("roi_outline", True), _LEVEL_PEN_WIDTH_PX))
        painter.drawLine(QPointF(self._offset_x_px, y_widget),
                         QPointF(self._offset_x_px + self._image_width_px, y_widget))
        painter.end()
        self.setPixmap(canvas)

    # ── Mouse ────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() != Qt.LeftButton:
            return
        self._is_dragging = True
        self._move_to(event.position().y())

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._is_dragging:
            self._move_to(event.position().y())

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._is_dragging = False

    # ── Internos ─────────────────────────────────────────────────────────────

    def _move_to(self, y_widget: float):
        """Lleva la altura a la fila del frame bajo el puntero, la redibuja y la publica."""
        if not self._frame_height_px:
            return
        y_px = int((y_widget - self._offset_y_px) * self._scale_y)
        self._y_px = max(0, min(y_px, self._frame_height_px - 1))
        if self._last_frame is not None:
            self.set_frame(self._last_frame)
        if self.level_changed is not None:
            self.level_changed(self._y_px)
