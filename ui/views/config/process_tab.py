"""
Pestaña de proceso: la sección `process:` del config, con lo que se calibra en esta planta.

**Específico del proyecto.** Es la única pestaña cuya forma cambia entre forks, porque
`process:` es la única sección cuya forma cambia. Acá van las calzas: una sub-pestaña por
cada cámara que mira un pipeline `stopper_check`, como las de modelos y pipelines de la
pestaña de inferencia, con la altura de calza levantada —que se
tipea o se marca sobre el frame en vivo—, los píxeles mínimos sobre ella, la distancia máxima
entre la rueda y la calza y de qué lado va la rueda. Qué hace cada valor está en la sección `process.stopper_check` del config.

**Las sub-pestañas salen de los pipelines, no de una lista propia.** Qué cámaras miran calzas lo
dice `inference.pipelines`, y la regla para leerlo —habilitados, de ese `type`, `cameras: []`
= todas— es la misma con la que `hooks.py` arma el annotator: `read_pipelines()`. Una cámara
nueva en el pipeline aparece acá sola al reiniciar.

La altura marcada sobre la imagen la escribe y la persiste su diálogo, como el ROI, y al
cerrarse el ConfigView pide `reload_level()` para que el campo la muestre. Una altura o una
distancia «sin calibrar» se guarda como null, que es lo que el annotator lee como ese
veredicto sin dar.

Al pausar el video del diálogo de la altura, la pestaña le da al diálogo el último frame que
el pipeline de calzas infirió en esa cámara y le dice qué daría la altura marcada: los
píxeles de la calza sobre ella y la distancia a la rueda, con los valores que tiene la
pestaña en ese momento —guardados o no—. Lo mide con las mismas funciones del annotator
(`pick_stopper_and_wheels`, `find_closest_wheel`), así que el número del diálogo es el que el
equipo mide. Es el último inferido y no el último en vivo porque es el único que trae
detecciones: con la compuerta de la plataforma cerrada puede ser viejo, y por eso dice la hora.

Dos cosas que **no** van acá: la validación de lo que tiene que frenar el arranque —es de
quien lee la sección, `hooks.py`— y qué cámaras usa cada proceso, que es config.
"""

from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from system.config_manager import ConfigManager
from system.inference.annotations import (
    DEFAULT_MIN_RAISED_PX, WHEEL_SIDE_ANY, WHEEL_SIDE_LEFT, WHEEL_SIDE_RIGHT,
    find_closest_wheel, pick_stopper_and_wheels,
)
from system.inference.geometry import count_px_above_y
from system.inference.hooks import (
    STOPPER_CLASS, STOPPER_PIPELINE_TYPE, WHEEL_CLASS, read_pipelines,
)
from system.inference.result import InferenceResult

from ui.dialogs.level_dialog import LevelSnapshot
from ui.strings import tr
from ui.views.config.abstract_tab import AbstractConfigTab
from ui.widgets.form import (
    add_form_row, add_hint_row, build_form_page, build_group_box, build_slot_tabs,
    build_spin_box, build_value_combo_box, set_combo_data,
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
    """Sección `process:` del config: una sub-pestaña de calza por cámara de `stopper_check`."""

    TITLE_KEY = "tab_process"

    # (slot de cámara, clave del config donde va la altura, clave de idioma del título).
    open_level_requested = Signal(str, str, str)

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        self._stoppers: dict[str, _StopperFields] = {}
        self._stopper_tabs = None

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        _, stopper_cameras = read_pipelines(self._config, STOPPER_PIPELINE_TYPE)
        if stopper_cameras:
            box, form = build_group_box(tr("process_stopper_box"))
            self._stopper_tabs = build_slot_tabs()
            for camera_slot in stopper_cameras:
                fields = _StopperFields(self._config, camera_slot)
                fields.open_level_requested.connect(self.open_level_requested)
                self._stoppers[camera_slot] = fields
                self._stopper_tabs.addTab(fields, fields.camera_name)
            form.addRow(self._stopper_tabs)
            layout.addWidget(box)
        else:
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

    def build_level_inspector(
            self, camera_slot: str,
            result_source: Callable[[str, str], InferenceResult | None],
    ) -> Callable[[], LevelSnapshot | None] | None:
        """
        El inspector del diálogo de la altura de esa cámara: en pausa, el último resultado
        válido de calzas y qué daría la altura sobre él. None si la cámara no mira calzas.

        `result_source(cámara, pipeline)` es el último resultado de ese par, que lleva el
        cableado.
        """
        fields = self._stoppers.get(camera_slot)
        if fields is None:
            return None
        stopper_pipelines, _ = read_pipelines(self._config, STOPPER_PIPELINE_TYPE)

        def inspect() -> LevelSnapshot | None:
            results = [result for result in (result_source(camera_slot, pipeline_slot)
                                             for pipeline_slot in sorted(stopper_pipelines))
                       if result is not None and result.is_valid
                       and result.source_bgr is not None]
            if not results:
                return None
            result = max(results, key=lambda candidate: candidate.timestamp_s)
            return LevelSnapshot(result.source_bgr,
                                 lambda y_px: fields.describe(result, y_px))

        return inspect


class _StopperFields(QWidget):
    """La sub-pestaña de calza de una cámara: la altura, los píxeles sobre ella y la rueda."""

    open_level_requested = Signal(str, str, str)

    def __init__(self, config_manager: ConfigManager, camera_slot: str, parent=None):
        super().__init__(parent)
        self._config = config_manager
        self._camera_slot = camera_slot
        self.camera_name = str(self._config.get(f"cameras.{camera_slot}.name", "")
                               or camera_slot)

        page, form = build_form_page()
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
        layout.addWidget(page)

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

    def describe(self, result: InferenceResult, y_px: int) -> list[str]:
        """
        Qué daría esa altura sobre el resultado, con los valores que muestra la pestaña: los
        píxeles de la calza sobre la línea contra el mínimo, y la distancia a la rueda contra
        el máximo.
        """
        time_text = datetime.fromtimestamp(result.timestamp_s).strftime("%H:%M:%S")
        lines = [tr("stopper_inspect_frame").format(time=time_text)]
        stopper, wheels = pick_stopper_and_wheels(
            result.detections, stopper_class=STOPPER_CLASS, wheel_class=WHEEL_CLASS,
            wheel_side=self._wheel_side_combo.currentData() or WHEEL_SIDE_ANY)
        if stopper is None:
            lines.append(tr("stopper_inspect_no_stopper"))
            return lines

        px_above = count_px_above_y(stopper, y_px)
        min_raised = self._min_raised_spin.value()
        lines.append(tr("stopper_inspect_px").format(
            px=px_above, min=min_raised, verdict=_verdict(px_above >= min_raised)))

        closest = find_closest_wheel(stopper, wheels)
        if closest is None:
            lines.append(tr("stopper_inspect_no_wheel"))
            return lines
        gap_px = round(closest[0])
        max_gap = self._max_gap_spin.value()
        if max_gap == _UNCALIBRATED:
            lines.append(tr("stopper_inspect_gap_uncalibrated").format(gap=gap_px))
        else:
            lines.append(tr("stopper_inspect_gap").format(
                gap=gap_px, max=max_gap, verdict=_verdict(closest[0] <= max_gap)))
        return lines

    def _read_camera_value(self, key: str) -> object:
        values = self._config.get(f"{_STOPPER_SECTION}.{key}", {}) or {}
        return values.get(self._camera_slot)


def _verdict(is_ok: bool) -> str:
    return tr("stopper_inspect_ok") if is_ok else tr("stopper_inspect_nok")
