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
cerrarse, `ConfigTabs` pide `reload_level()` para que el campo la muestre. Una altura o una
distancia «sin calibrar» se guarda como null, que es lo que el annotator lee como ese
veredicto sin dar.

Al pausar el video del diálogo de la altura, la pestaña le da al diálogo el último frame que
el pipeline de calzas infirió en esa cámara y le dice qué daría la altura marcada: los
píxeles de la calza sobre ella y la distancia a la rueda, con los valores que tiene la
pestaña en ese momento —guardados o no—. Lo mide con las mismas funciones del annotator
de `stopper_check.py` (`pick_stopper_and_wheels`, `find_closest_wheel`), así que el número del diálogo es el que el
equipo mide. Es el último inferido y no el último en vivo porque es el único que trae
detecciones: con la compuerta de la plataforma cerrada puede ser viejo, y por eso dice la hora.

Con algún pipeline `person_safety` va además un grupo de personas. El filtro es uno solo para
todas las cámaras —cuántos frames confirman a una persona en la zona y cuántos segundos sin
verla la liberan—: es una política de seguridad y no una calibración del montaje. Qué hace
cada valor está en `system/inference/presence.py`. La zona de exclusión sí es de cada
montaje: una sub-pestaña por cámara de personas dice si tiene zona y abre el diálogo que la
dibuja sobre el frame en vivo (`ui/dialogs/polygon_dialog.py`). Esa zona la escribe y la
persiste su diálogo, como la altura, y al cerrarse, `ConfigTabs` pide `reload_polygon()`.

La pantalla tiene la forma de las tarjetas de ajustes (`ui/widgets/settings_card.py`): una
tarjeta por proceso, secciones con un título y una descripción de una línea, y los campos en
columnas con el rótulo arriba. Qué hace cada valor en detalle está en los módulos que los
usan, no en la pantalla.

Dos cosas que **no** van acá: la validación de lo que tiene que frenar el arranque —es de
quien lee la sección, `hooks.py`— y qué cámaras usa cada proceso, que es config.
"""

from collections.abc import Callable
from datetime import datetime

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QLabel, QPushButton, QVBoxLayout, QWidget

from system.config_manager import ConfigManager
from system.inference.geometry import count_px_above_y
from system.inference.hooks import (
    PERSON_PIPELINE_TYPE, PERSON_SECTION, STOPPER_CLASS, STOPPER_PIPELINE_TYPE, WHEEL_CLASS,
    read_pipelines,
)
from system.inference.person_safety import (
    DEFAULT_CONFIRM_HITS, DEFAULT_CONFIRM_WINDOW, DEFAULT_RELEASE_S,
)
from system.inference.result import InferenceResult
from system.inference.stopper_check import (
    DEFAULT_MIN_RAISED_PX, WHEEL_SIDE_ANY, WHEEL_SIDE_LEFT, WHEEL_SIDE_RIGHT,
    find_closest_wheel, pick_stopper_and_wheels,
)

from ui.dialogs.level_dialog import LevelSnapshot
from ui.strings import tr
from ui.views.config.abstract_tab import AbstractConfigTab
from ui.widgets.form import (
    build_inline_row, build_slot_tabs, build_spin_box,
    build_value_combo_box, set_combo_data,
)
from ui.widgets.settings_card import FieldGrid, SettingsCard

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
_MAX_CONFIRM_FRAMES = 60
_MAX_RELEASE_S = 120          # s, en enteros: una fracción de segundo no cambia lo que lee el PLC
_CARD_SPACING_PX = 24        # entre tarjetas, como la web
_TAB_PAGE_TOP_PX = 14         # aire entre la barra de cámaras y los campos de su página


class ProcessTab(AbstractConfigTab):
    """
    Sección `process:` del config: una sub-pestaña de calza por cámara de `stopper_check` y
    el filtro de personas, si hay un pipeline `person_safety`.
    """

    TITLE_KEY = "tab_process"

    # (slot de cámara, clave del config donde va la altura, clave de idioma del título).
    open_level_requested = Signal(str, str, str)
    # (slot de cámara, clave del config donde va la zona, clave de idioma del título).
    open_polygon_requested = Signal(str, str, str)

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        self._stoppers: dict[str, _StopperFields] = {}
        self._stopper_tabs = None
        self._person = None

        layout = QVBoxLayout(self)
        # Sin margen propio: las tarjetas arrancan en el borde del encabezado de la página.
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_CARD_SPACING_PX)
        _, person_cameras = read_pipelines(self._config, PERSON_PIPELINE_TYPE)
        if person_cameras:
            self._person = _PersonFields(self._config, person_cameras)
            self._person.open_polygon_requested.connect(self.open_polygon_requested)
            layout.addWidget(self._person)
        _, stopper_cameras = read_pipelines(self._config, STOPPER_PIPELINE_TYPE)
        if stopper_cameras:
            card = SettingsCard(tr("process_stopper_box"))
            section = card.add_section(tr("process_stopper_section"),
                                       tr("process_stopper_description"), columns=1)
            self._stopper_tabs = build_slot_tabs()
            for camera_slot in stopper_cameras:
                fields = _StopperFields(self._config, camera_slot)
                fields.open_level_requested.connect(self.open_level_requested)
                self._stoppers[camera_slot] = fields
                self._stopper_tabs.addTab(fields, fields.camera_name)
            section.add_row(self._stopper_tabs)
            layout.addWidget(card)
        if self._person is None and not stopper_cameras:
            note = QLabel(tr("process_empty_note"))
            note.setObjectName("hintLabel")
            note.setWordWrap(True)
            layout.addWidget(note)
        layout.addStretch()

    # ── Contrato de la pestaña ───────────────────────────────────────────────

    def load(self):
        if self._person is not None:
            self._person.load()
        for fields in self._stoppers.values():
            fields.load()

    def save(self):
        if self._person is not None:
            self._person.save()
        for fields in self._stoppers.values():
            fields.save()

    def reload_polygon(self, camera_slot: str):
        """Recarga la zona de una cámara cuando su diálogo terminó de escribirla."""
        if self._person is not None:
            self._person.load_zone(camera_slot)

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


class _PersonFields(QWidget):
    """
    La tarjeta de personas: el filtro —cuántos frames confirman y cuántos segundos liberan—
    y una sub-pestaña por cámara con su zona de exclusión, como las de calza.
    """

    open_polygon_requested = Signal(str, str, str)

    def __init__(self, config_manager: ConfigManager, camera_slots: list[str], parent=None):
        super().__init__(parent)
        self._config = config_manager

        card = SettingsCard(tr("process_person_box"))
        confirm = card.add_section(tr("process_person_filter"),
                                   tr("process_person_filter_description"), columns=3)
        self._confirm_hits_spin = confirm.add_field(
            tr("process_person_confirm_hits"),
            build_spin_box(1, _MAX_CONFIRM_FRAMES, DEFAULT_CONFIRM_HITS))
        self._confirm_window_spin = confirm.add_field(
            tr("process_person_confirm_window"),
            build_spin_box(1, _MAX_CONFIRM_FRAMES, DEFAULT_CONFIRM_WINDOW))
        # No se pueden pedir más aciertos que frames en la ventana: el filtro no arrancaría.
        self._confirm_window_spin.valueChanged.connect(self._confirm_hits_spin.setMaximum)
        self._confirm_hits_spin.setMaximum(self._confirm_window_spin.value())
        self._release_spin = confirm.add_field(
            tr("process_person_release"),
            build_spin_box(0, _MAX_RELEASE_S, round(DEFAULT_RELEASE_S)),
            hint=tr("process_person_release_hint"))

        zones = card.add_section(tr("process_person_zone"),
                                 tr("process_person_zone_description"), columns=1)
        self._zone_tabs = build_slot_tabs()
        self._zone_labels: dict[str, QLabel] = {}
        for camera_slot in camera_slots:
            label = QLabel()
            button = QPushButton(tr("process_draw_zone"))
            button.clicked.connect(
                lambda _=False, slot=camera_slot: self.open_polygon_requested.emit(
                    slot, _get_zone_key(slot), "process_person_zone"))
            self._zone_labels[camera_slot] = label
            page = QWidget()
            grid = FieldGrid(columns=2)
            grid.add_field(tr("process_person_zone_state"), build_inline_row(label, button))
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(0, _TAB_PAGE_TOP_PX, 0, 0)
            page_layout.addWidget(grid)
            camera_name = str(self._config.get(f"cameras.{camera_slot}.name", "")
                              or camera_slot)
            self._zone_tabs.addTab(page, camera_name)
        zones.add_row(self._zone_tabs)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(card)

    def load(self):
        # La ventana primero: es el techo de los aciertos.
        self._confirm_window_spin.setValue(int(self._config.get(
            f"{PERSON_SECTION}.confirm_window", DEFAULT_CONFIRM_WINDOW)))
        self._confirm_hits_spin.setValue(int(self._config.get(
            f"{PERSON_SECTION}.confirm_hits", DEFAULT_CONFIRM_HITS)))
        # Un valor con decimales escrito a mano en el config se muestra redondeado.
        self._release_spin.setValue(round(float(self._config.get(
            f"{PERSON_SECTION}.release_s", DEFAULT_RELEASE_S))))
        for camera_slot in self._zone_labels:
            self.load_zone(camera_slot)

    def load_zone(self, camera_slot: str):
        """Muestra si la cámara tiene zona; la escribe su diálogo, no `save()`."""
        label = self._zone_labels.get(camera_slot)
        if label is None:
            return
        zones = self._config.get(f"{PERSON_SECTION}.exclusion_zone_px", {}) or {}
        points = zones.get(camera_slot) or []
        label.setText(tr("polygon_vertices").format(count=len(points)) if points
                      else tr("process_person_no_zone"))

    def save(self):
        self._config.set(f"{PERSON_SECTION}.confirm_hits", self._confirm_hits_spin.value())
        self._config.set(f"{PERSON_SECTION}.confirm_window", self._confirm_window_spin.value())
        self._config.set(f"{PERSON_SECTION}.release_s", self._release_spin.value())


def _get_zone_key(camera_slot: str) -> str:
    return f"{PERSON_SECTION}.exclusion_zone_px.{camera_slot}"


class _StopperFields(QWidget):
    """La sub-pestaña de calza de una cámara: la altura, los píxeles sobre ella y la rueda."""

    open_level_requested = Signal(str, str, str)

    def __init__(self, config_manager: ConfigManager, camera_slot: str, parent=None):
        super().__init__(parent)
        self._config = config_manager
        self._camera_slot = camera_slot
        self.camera_name = str(self._config.get(f"cameras.{camera_slot}.name", "")
                               or camera_slot)

        grid = FieldGrid(columns=2)
        self._raised_y_spin = build_spin_box(_UNCALIBRATED, _MAX_FRAME_SIDE_PX, _UNCALIBRATED)
        self._raised_y_spin.setSpecialValueText(tr("process_uncalibrated"))
        level_button = QPushButton(tr("process_draw_level"))
        level_button.clicked.connect(
            lambda: self.open_level_requested.emit(
                self._camera_slot, f"{_STOPPER_SECTION}.raised_y_px.{self._camera_slot}",
                "process_stopper_level"))
        grid.add_field(tr("process_stopper_level"),
                       build_inline_row(self._raised_y_spin, level_button))
        self._min_raised_spin = grid.add_field(
            tr("process_stopper_min_raised"),
            build_spin_box(1, _MAX_RAISED_PX, DEFAULT_MIN_RAISED_PX),
            hint=tr("process_stopper_min_raised_hint"))
        self._max_gap_spin = grid.add_field(
            tr("process_stopper_max_gap"),
            build_spin_box(_UNCALIBRATED, _MAX_FRAME_SIDE_PX, _UNCALIBRATED))
        self._max_gap_spin.setSpecialValueText(tr("process_uncalibrated"))
        self._wheel_side_combo = grid.add_field(
            tr("process_wheel_side"),
            build_value_combo_box({tr(label_key): side for label_key, side in _WHEEL_SIDES},
                                  WHEEL_SIDE_ANY),
            hint=tr("process_wheel_side_hint"))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, _TAB_PAGE_TOP_PX, 0, 0)
        layout.addWidget(grid)

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
