"""
Pestaña de proceso: la sección `process:` del config, con lo que se calibra en esta planta.

**Específico del proyecto.** Es la única pestaña cuya forma cambia entre forks, porque
`process:` es la única sección cuya forma cambia. Acá van las calzas: un grupo por cada
cámara que mira un pipeline `stopper_check`, con la altura de calza levantada —que se
tipea o se marca sobre el frame en vivo—, los píxeles mínimos sobre ella, la distancia máxima
entre la rueda y la calza y de qué lado va la rueda. Qué hace cada valor está en la sección `process.stopper_check` del config.

**Los grupos salen de los pipelines, no de una lista propia.** Qué cámaras miran calzas lo
dice `inference.pipelines`, y la regla para leerlo —habilitados, de ese `type`, `cameras: []`
= todas— es la misma con la que `hooks.py` arma el annotator: `read_pipelines()`. Una cámara
nueva en el pipeline aparece acá sola al reiniciar.

La altura marcada sobre la imagen la escribe y la persiste su diálogo, como el ROI, y al
cerrarse el ConfigView pide `reload_level()` para que el campo la muestre. Una altura o una
distancia «sin calibrar» se guarda como null, que es lo que el annotator lee como ese
veredicto sin dar.

Dos cosas que **no** van acá: la validación de lo que tiene que frenar el arranque —es de
quien lee la sección, `hooks.py`— y qué cámaras usa cada proceso, que es config.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from system.config_manager import ConfigManager
from system.inference.annotations import (
    DEFAULT_MIN_RAISED_PX, WHEEL_SIDE_ANY, WHEEL_SIDE_LEFT, WHEEL_SIDE_RIGHT,
)
from system.inference.hooks import STOPPER_PIPELINE_TYPE, read_pipelines

from ui.strings import tr
from ui.views.config.abstract_tab import AbstractConfigTab
from ui.widgets.form import (
    add_form_row, add_hint_row, build_group_box, build_spin_box, build_value_combo_box,
    set_combo_data,
)

_STOPPER_SECTION = "process.stopper_check"
# Clave de idioma -> valor de `wheel_side` en el config; el valor viaja en el item del combo.
_WHEEL_SIDES = (
    ("wheel_side_any", WHEEL_SIDE_ANY),
    ("wheel_side_left", WHEEL_SIDE_LEFT),
    ("wheel_side_right", WHEEL_SIDE_RIGHT),
)
_MAX_FRAME_SIDE_PX = 8192
_MAX_RAISED_PX = 10_000_000
_UNCALIBRATED = -1           # valor del spin que se muestra «sin calibrar» y se guarda null


class ProcessTab(AbstractConfigTab):
    """Sección `process:` del config: un grupo de calza por cámara de `stopper_check`."""

    TITLE_KEY = "tab_process"

    # (slot de cámara, clave del config donde va la altura, clave de idioma del título).
    open_level_requested = Signal(str, str, str)

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        self._stoppers: dict[str, _StopperFields] = {}

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        _, stopper_cameras = read_pipelines(self._config, STOPPER_PIPELINE_TYPE)
        for camera_slot in stopper_cameras:
            fields = _StopperFields(self._config, camera_slot)
            fields.open_level_requested.connect(self.open_level_requested)
            self._stoppers[camera_slot] = fields
            layout.addWidget(fields)
        if not self._stoppers:
            note = QLabel(tr("process_empty_note"))
            note.setObjectName("hintLabel")
            note.setWordWrap(True)
            layout.addWidget(note)
        layout.addStretch()

    # ── Contrato de la pestaña ───────────────────────────────────────────────

    def load(self):
        for fields in self._stoppers.values():
            fields.load()

    def save(self):
        for fields in self._stoppers.values():
            fields.save()

    def reload_level(self, camera_slot: str):
        """Recarga la altura de una cámara cuando su diálogo terminó de escribirla."""
        fields = self._stoppers.get(camera_slot)
        if fields is not None:
            fields.load_level()


class _StopperFields(QWidget):
    """El grupo de calza de una cámara: la altura, los píxeles sobre ella y la rueda."""

    open_level_requested = Signal(str, str, str)

    def __init__(self, config_manager: ConfigManager, camera_slot: str, parent=None):
        super().__init__(parent)
        self._config = config_manager
        self._camera_slot = camera_slot
        camera_name = str(self._config.get(f"cameras.{camera_slot}.name", "") or camera_slot)

        box, form = build_group_box(f"{tr('process_stopper_box')} — {camera_name}")
        self._raised_y_spin = add_form_row(
            form, tr("process_stopper_level"),
            build_spin_box(_UNCALIBRATED, _MAX_FRAME_SIDE_PX, _UNCALIBRATED))
        self._raised_y_spin.setSpecialValueText(tr("process_uncalibrated"))
        level_button = QPushButton(tr("process_draw_level"))
        level_button.clicked.connect(
            lambda: self.open_level_requested.emit(
                self._camera_slot, f"{_STOPPER_SECTION}.raised_y_px.{self._camera_slot}",
                "process_stopper_level"))
        form.addRow("", level_button)
        self._min_raised_spin = add_form_row(
            form, tr("process_stopper_min_raised"),
            build_spin_box(1, _MAX_RAISED_PX, DEFAULT_MIN_RAISED_PX))
        self._max_gap_spin = add_form_row(
            form, tr("process_stopper_max_gap"),
            build_spin_box(_UNCALIBRATED, _MAX_FRAME_SIDE_PX, _UNCALIBRATED))
        self._max_gap_spin.setSpecialValueText(tr("process_uncalibrated"))
        self._wheel_side_combo = add_form_row(
            form, tr("process_wheel_side"),
            build_value_combo_box({tr(label_key): side for label_key, side in _WHEEL_SIDES},
                                  WHEEL_SIDE_ANY))
        add_hint_row(form, tr("process_stopper_hint"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(box)

    def load(self):
        self.load_level()
        min_raised = self._read_camera_value("min_raised_px")
        self._min_raised_spin.setValue(
            int(min_raised) if min_raised is not None else DEFAULT_MIN_RAISED_PX)
        max_gap = self._read_camera_value("max_gap_px")
        self._max_gap_spin.setValue(int(max_gap) if max_gap is not None else _UNCALIBRATED)
        set_combo_data(self._wheel_side_combo,
                       self._read_camera_value("wheel_side") or WHEEL_SIDE_ANY)

    def load_level(self):
        raised_y = self._read_camera_value("raised_y_px")
        self._raised_y_spin.setValue(int(raised_y) if raised_y is not None else _UNCALIBRATED)

    def save(self):
        raised_y = self._raised_y_spin.value()
        self._config.set(f"{_STOPPER_SECTION}.raised_y_px.{self._camera_slot}",
                         None if raised_y == _UNCALIBRATED else raised_y)
        self._config.set(f"{_STOPPER_SECTION}.min_raised_px.{self._camera_slot}",
                         self._min_raised_spin.value())
        max_gap = self._max_gap_spin.value()
        self._config.set(f"{_STOPPER_SECTION}.max_gap_px.{self._camera_slot}",
                         None if max_gap == _UNCALIBRATED else max_gap)
        self._config.set(f"{_STOPPER_SECTION}.wheel_side.{self._camera_slot}",
                         self._wheel_side_combo.currentData())

    def _read_camera_value(self, key: str) -> object:
        values = self._config.get(f"{_STOPPER_SECTION}.{key}", {}) or {}
        return values.get(self._camera_slot)
