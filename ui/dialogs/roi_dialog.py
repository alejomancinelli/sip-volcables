"""
Diálogo de ROI: dibujar con el mouse la región que se analiza de una cámara.

Trabaja sobre el frame en vivo de esa cámara: quien lo abre le conecta la señal de
frames del panel, así que el operador recorta sobre lo que la cámara está viendo y no
sobre una captura vieja.

El rectángulo se edita como en el diálogo de regiones de los pipelines, con el mismo lienzo
(`ui/widgets/region_canvas.py`): arrastrarlo desde adentro lo mueve, desde un borde o una
esquina lo estira, y desde afuera dibuja uno nuevo. Ajustar un recuadro casi bien puesto
no obliga a dibujarlo entero de nuevo.

Las coordenadas que se guardan son del frame de cámara, en píxeles: las mismas que lee
el motor de inferencia. El lienzo convierte entre el frame y la pantalla y no deja
salir coordenadas de widget.

Guarda las cuatro claves de geometría bajo el prefijo que se le dé —por defecto
`cameras.<slot>.roi`— y persiste el archivo al aceptar. No toca ninguna otra clave, y el
`enabled` del ROI queda como está: prender el recorte es una decisión aparte de dibujarlo.

**El prefijo es un parámetro** porque un proyecto puede tener más de un rectángulo por
cámara: el que se analiza y el que algún proceso mida aparte.
Los dos se dibujan igual y sobre el mismo frame en vivo, así que lo único que cambia es
dónde se guardan y cómo se titula la ventana. Sin esto, el segundo rectángulo se escribiría
a mano en el `config.yaml`, que es justo lo que este diálogo existe para evitar.

El video se puede pausar (`PauseButton`) para dibujar sobre un frame quieto.
"""

import numpy as np
from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QSpinBox,
    QVBoxLayout,
)

from system.config_manager import ConfigManager

from ui import theme
from ui.strings import tr
from ui.widgets.form import add_hint_row
from ui.widgets.pause_button import PauseButton
from ui.widgets.region_canvas import CanvasRegion, RegionCanvas

_MAX_FRAME_SIDE_PX = 8192
_DIALOG_WIDTH_PX = 760
_DIALOG_HEIGHT_PX = 620

# Claves del ROI en el config, en el orden en que se muestran, con su etiqueta.
_ROI_FIELDS = (
    ("x_px", "cam_roi_x"),
    ("y_px", "cam_roi_y"),
    ("width_px", "cam_roi_width"),
    ("height_px", "cam_roi_height"),
)


class RoiDialog(QDialog):
    """
    Diálogo de definición de ROI de una cámara.

    Se abre con `exec()`. Aceptar escribe el ROI en el config y lo persiste; cancelar
    no deja nada. Los frames entran por `update_frame()`, que es el slot que hay que
    conectar a la señal de frames de esa cámara.

    `config_prefix` dice bajo qué clave se guardan las cuatro coordenadas; vacío significa
    el ROI de análisis de esa cámara. `title_key` es la clave de idiomas del título, para
    que la ventana diga qué rectángulo se está dibujando.
    """

    def __init__(self, config_manager: ConfigManager, camera_slot: str, parent=None, *,
                 config_prefix: str = "", title_key: str = "roi_title"):
        super().__init__(parent)
        self._config = config_manager
        self._camera_slot = camera_slot
        self._prefix = config_prefix or f"cameras.{camera_slot}.roi"
        self._region_name = tr(title_key)
        self._last_frame: np.ndarray | None = None

        camera_name = str(self._config.get(f"cameras.{camera_slot}.name", "") or camera_slot)
        self.setWindowTitle(f"{self._region_name} — {camera_name}")
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        self.resize(_DIALOG_WIDTH_PX, _DIALOG_HEIGHT_PX)

        roi = self._config.get(self._prefix, {}) or {}
        self._canvas = RegionCanvas()
        self._canvas.region_edited.connect(self._on_roi_drawn)

        layout = QVBoxLayout(self)
        layout.addWidget(self._canvas, stretch=1)
        layout.addWidget(self._build_coords_box(roi))
        layout.addLayout(self._build_button_row())
        self._refresh_canvas()

    # ── API pública ──────────────────────────────────────────────────────────

    @Slot(object)
    def update_frame(self, frame_bgr: np.ndarray | None):
        """Slot para la señal de frames de la cámara. Ignora un frame vacío o en pausa."""
        if frame_bgr is None or frame_bgr.size == 0 or self._pause_button.is_paused:
            return
        self._last_frame = frame_bgr
        self._canvas.set_frame(frame_bgr)

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_coords_box(self, roi: dict) -> QGroupBox:
        box = QGroupBox(tr("roi_box_coords"))
        form = QFormLayout(box)
        self._spins: dict[str, QSpinBox] = {}
        for key, label_key in _ROI_FIELDS:
            spin = QSpinBox()
            spin.setRange(0, _MAX_FRAME_SIDE_PX)
            spin.setValue(int(roi.get(key, 0)))
            spin.valueChanged.connect(self._refresh_canvas)
            self._spins[key] = spin
            form.addRow(QLabel(tr(label_key)), spin)
        add_hint_row(form, tr("roi_hint"))
        return box

    def _build_button_row(self) -> QHBoxLayout:
        reset_button = QPushButton(tr("roi_reset"))
        reset_button.setObjectName("resetButton")
        reset_button.clicked.connect(self._on_reset_clicked)
        self._pause_button = PauseButton()

        cancel_button = QPushButton(tr("dialog_cancel"))
        cancel_button.setObjectName("cancelButton")
        cancel_button.clicked.connect(self.reject)

        save_button = QPushButton(tr("roi_save"))
        save_button.setObjectName("saveButton")
        save_button.clicked.connect(self._on_save_clicked)

        row = QHBoxLayout()
        row.addWidget(reset_button)
        row.addWidget(self._pause_button)
        row.addStretch()
        row.addWidget(cancel_button)
        row.addWidget(save_button)
        return row

    # ── Internos ─────────────────────────────────────────────────────────────

    def _on_roi_drawn(self, x_px: int, y_px: int, width_px: int, height_px: int):
        """El lienzo ya movió su recuadro: sólo falta que los campos lo digan."""
        self._set_spins(x_px, y_px, width_px, height_px)

    def _on_reset_clicked(self):
        self._set_spins(0, 0, 0, 0)
        self._refresh_canvas()

    def _on_save_clicked(self):
        for key, _ in _ROI_FIELDS:
            self._config.set(f"{self._prefix}.{key}", self._spins[key].value())
        self._config.save()
        self.accept()

    def _set_spins(self, *values: int):
        for (key, _), value in zip(_ROI_FIELDS, values, strict=True):
            spin = self._spins[key]
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def _refresh_canvas(self):
        """
        Lleva al lienzo el rectángulo de los campos, tipeado o recién cargado. Sin ancho o
        sin alto no hay recuadro, y arrastrar sobre la imagen dibuja uno nuevo.
        """
        x_px, y_px, width_px, height_px = (self._spins[key].value() for key, _ in _ROI_FIELDS)
        rect_px = (x_px, y_px, width_px, height_px) if width_px > 0 and height_px > 0 else None
        color = theme.get_color("roi_outline", True)
        self._canvas.set_regions([CanvasRegion(
            self._region_name, (color.blue(), color.green(), color.red()), rect_px,
            is_active=True, is_editable=True)])
