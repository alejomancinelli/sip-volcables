"""
Pestaña de inferencia: `inference.models` e `inference.pipelines`.

Tres tarjetas, una debajo de la otra —«Modelos», «Pipelines» y «Regiones (ROI)»—, cada una con
su barra de sub-pestañas arriba y, en cada página, sus secciones con la forma de las tarjetas de
ajustes (`ui/widgets/settings_card.py`). Los slots salen del config y son fijos por proyecto,
igual que las cámaras. Los pipelines van debajo de los modelos porque los nombran, así que se
leen en ese orden, y las barras nunca se mezclan: un slot de modelo y uno de pipeline en la
misma fila de pestañas no se distinguen por el rótulo. Con más slots de los que entran a lo
ancho, la barra muestra flechas en vez de cortar rótulos (`build_slot_tabs()`).

Los pesos de un modelo se tipean o se eligen con «Examinar…», que abre el selector de archivos
en la carpeta de los pesos actuales. La ruta elegida se guarda relativa a `DATA_DIR` si está
adentro —que es contra lo que el modelo resuelve una ruta relativa—, así la instalación se
puede mover de carpeta. La pestaña no abre el archivo: sólo escribe la ruta.

Un pipeline tiene tres secciones: qué corre y sobre qué cámaras, cuándo su resultado es una
medición y el ciclo de medición. Destildado «Pipeline habilitado», sus campos quedan apagados:
no se edita un pipeline que no va a arrancar. Las cámaras son una casilla por cámara, y la
última marcada no se puede desmarcar: en el config una lista vacía es «todas», así que
desmarcarlas todas lo pondría a mirar todo. Una lista que ya era «todas» y se guarda con todas
marcadas vuelve vacía, como estaba.

**Del ciclo se muestra encendido sólo lo que rige** con el número de imágenes que tiene el campo,
con la misma regla que el scheduler (`is_cycle()`): sin ciclo, el intervalo mínimo del motor; con
ciclo, la espera entre rondas, el tiempo máximo por imagen y el apagado de la captura —el motor
recibe esos frames con `ignore_interval` y su intervalo no cuenta—. La espera tampoco rige a
pedido (`cycle_trigger: request`, que se edita en el archivo). Lo apagado conserva su valor y
se guarda tal cual: volver a varias imágenes no tiene que encontrar un tiempo máximo en 0, que
es «sin límite» y deja una ronda colgada para siempre si un frame no vuelve.

Los tiempos son enteros: segundos, salvo el intervalo mínimo, que va en milisegundos porque un
pipeline puede necesitar inferir más de una vez por segundo. Un valor con decimales escrito a
mano se muestra redondeado, pero nunca a 0: ahí 0 es otro modo —cada frame, sin espera, sin
límite— y no un número.

**El orden de las etapas no está acá y no puede estarlo**: se programa en
`system/inference/pipeline.py`, porque encadenar dos modelos es lógica del proceso. De
esta pestaña sale de dónde se carga cada modelo y qué cámaras entran a cada pipeline.

Los tipos de modelo y de pipeline que ofrecen los combos salen de `REGISTERED_MODELS` y
`REGISTERED_PIPELINES`, así que un fork que registra el suyo en la fábrica lo ve acá sin
tocar la UI.

Cuatro cosas de la sección no están en esta pantalla, y no es un olvido:

  - **`device`** no existe más: el modelo resuelve solo si hay CUDA disponible. Elegirlo
    a mano desde la planta sólo servía para dejar la inferencia en CPU sin darse cuenta.
  - **`inference.overlay`**, el anotado del resultado, se edita sólo en el config.yaml,
    como su paleta `class_colors_bgr`. Varios de sus valores son de la instalación y no
    una preferencia del operador —el tamaño del texto se calibra para el frame de cada
    cámara—, y cambiarlos desde la planta no mide mejor: sólo cambia lo que se ve en el
    stream, en la UI y en el dataset a la vez.
  - **`class_names`** de cada modelo se escribe en el config.yaml al armar el fork, o lo
    traen los pesos, que le ganan. Cambiar el orden no da un error: invierte en silencio
    todos los porcentajes. Un fork que declara sus clases en el código no lo usa, y ahí
    escribirlo sólo podía hacer que el pipeline se negara a arrancar.
  - **`max_side_px`** lo lee el contrato del modelo, pero ningún modelo del template lo
    aplica: un `.engine` tiene la entrada fija. Un campo que no hace nada se lee como un
    ajuste que no tomó; el fork cuyo modelo lo use lo escribe en el config.yaml.

**Las regiones tienen su propia tarjeta, «Regiones (ROI)», y no se editan ahí.** Tiene una
sub-pestaña por cámara, rotulada con su nombre, y en cada una una lista con una fila por
pipeline que la mira: el color del pipeline —el mismo del recuadro en el diálogo y en el
monitor—, su nombre, qué región usa —la propia o el frame completo— y lo que se puede avisar
sin el frame. El botón «Editar regiones…» abre el diálogo de esa cámara con ese pipeline
elegido (`PipelineRegionsDialog`): ahí se dibujan todas juntas sobre el frame en vivo. Esta
pestaña no escribe ninguna clave de `inference.pipelines.<slot>.roi`: el diálogo persiste lo
suyo, y acá `reload_regions()` vuelve a leer las filas cuando se cierra. Las filas salen de
las cámaras guardadas en el config, no de las casillas sin guardar.

**El nombre de un pipeline** (`inference.pipelines.<slot>.name`) es opcional y es el que se ve
en su sub-pestaña, en esas filas y en el diálogo, y cambia mientras se tipea: el slot sigue
siendo la identidad —se ve debajo del nombre— y sin nombre se muestra él.
"""

import os
from collections.abc import Callable, Iterable

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QCheckBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QTabWidget,
    QVBoxLayout, QWidget,
)

from system.camera.capture_scheduler import (
    CYCLE_TRIGGER_INTERVAL, CYCLE_TRIGGER_REQUEST, is_cycle,
)
from system.config_manager import ConfigManager
from system.inference import overlay
from system.inference.models.model_factory import REGISTERED_MODELS
from system.inference.pipeline_factory import DEFAULT_PIPELINE, REGISTERED_PIPELINES
from system.inference.preprocessing import can_cut_native, loses_native_cut
from system.inference.regions import (
    PROBLEM_NATIVE_PREPROCESSED, PROBLEM_TOO_SMALL, find_region_problems, get_pipeline_index,
    resolve_pipeline_roi,
)
from system.paths import DATA_DIR, resolve

from ui.strings import tr
from ui.views.config.abstract_tab import AbstractConfigTab
from ui.widgets.form import (
    build_check_grid, build_combo_box, build_inline_row, build_line_edit, build_slot_tabs,
    build_spin_box,
    set_combo_value, set_field_enabled, wire_enable_toggle,
)
from ui.widgets.settings_card import SectionStack, SettingsCard, SettingsList

_MAX_DETECTIONS = 10000
_MAX_INTERVAL_MS = 3_600_000     # una hora entre inferencias
_INTERVAL_STEP_MS = 100
_MS_PER_S = 1000
_MAX_FRAMES_PER_CYCLE = 100      # N imágenes por medición; más que esto es otro problema
_MAX_CYCLE_INTERVAL_S = 86400    # una medición por día es el extremo razonable
_DEFAULT_CAPTURE_TIMEOUT_S = 30

_CARD_SPACING_PX = 24            # entre tarjetas, como la web
_SWATCH_SIDE_PX = 10
_SWATCH_RADIUS_PX = 2
_ROW_SPACING_PX = 12             # entre el texto de una fila de región y su botón
_ROW_LINE_SPACING_PX = 4         # entre el nombre, la región y el aviso
_CAMERA_COLUMNS = 4              # casillas de cámara por fila

# Lo que el ciclo prende o apaga según el número de imágenes; «Pipeline habilitado» no los toca.
_CYCLE_FIELDS = ("interval_s", "cycle_interval_s", "capture_timeout_s",
                 "idle_cameras_between_cycles")


class InferenceTab(AbstractConfigTab):
    """Sección `inference:` del config, menos el orden de las etapas y el anotado."""

    TITLE_KEY = "tab_inference"

    # (slot de cámara, slot del pipeline): el operador quiere editar las regiones de esa
    # cámara con ese pipeline elegido. Una vez por clic en «Editar regiones…».
    open_regions_requested = Signal(str, str)

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        self._model_forms: dict[str, dict] = {}
        self._pipeline_forms: dict[str, dict] = {}
        # `cycle_trigger` de cada pipeline: no se edita acá, pero decide qué del ciclo rige.
        self._cycle_triggers: dict[str, str] = {}
        # (pipeline, cámara) -> (qué región usa, avisos): las dos etiquetas de su fila.
        self._region_rows: dict[tuple[str, str], tuple[QLabel, QLabel]] = {}
        # (pipeline, cámara) -> la etiqueta de su fila con el nombre del pipeline.
        self._region_names: dict[tuple[str, str], QLabel] = {}

        layout = QVBoxLayout(self)
        # Sin margen propio: las tarjetas arrancan en el borde del encabezado de la página.
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_CARD_SPACING_PX)
        models_card, _ = self._build_slot_card(
            tr("inf_box_models"), self._build_model_page,
            self._config.get("inference.models", {}) or {})
        layout.addWidget(models_card)
        pipelines_card, self._pipeline_tabs = self._build_slot_card(
            tr("inf_box_pipelines"), self._build_pipeline_page,
            self._config.get("inference.pipelines", {}) or {})
        layout.addWidget(pipelines_card)
        layout.addWidget(self._build_regions_card())
        layout.addStretch()
        self.load()

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_slot_card(self, eyebrow: str, build_page: Callable[[str], QWidget],
                         slots: Iterable[str]) -> tuple[SettingsCard, QTabWidget]:
        """Una tarjeta con una sub-pestaña por slot; el rótulo de cada una es el slot."""
        card = SettingsCard(eyebrow)
        tabs = build_slot_tabs()
        for slot in slots:
            tabs.addTab(build_page(str(slot)), str(slot))
        card.add_row(tabs)
        return card, tabs

    def _build_model_page(self, model_slot: str) -> QWidget:
        page = SectionStack()
        section = page.add_section(tr("inf_model_section"), tr("inf_model_description"))
        path = build_line_edit("", "models/model.engine")
        browse_button = QPushButton(tr("inf_model_browse"))
        browse_button.clicked.connect(lambda: self._on_browse_clicked(path))
        fields = {"type": section.add_field(tr("inf_model_type"),
                                            build_combo_box(list(REGISTERED_MODELS), ""))}
        section.add_field(tr("inf_model_path"), build_inline_row(path, browse_button))
        fields["path"] = path
        fields["min_confidence_pct"] = section.add_field(
            tr("inf_min_confidence"), build_spin_box(0, 100, 50),
            hint=tr("inf_min_confidence_hint"))
        self._model_forms[model_slot] = fields
        return page

    def _build_pipeline_page(self, pipeline_slot: str) -> QWidget:
        page = SectionStack()
        identity = page.add_section(tr("inf_pipeline_section"), tr("inf_pipeline_description"))
        enabled = identity.add_row(QCheckBox(tr("inf_pipeline_enabled")))
        enabled.setChecked(True)
        fields = {
            "enabled": enabled,
            "name": identity.add_field(
                tr("inf_pipeline_name"), build_line_edit("", pipeline_slot),
                hint=tr("inf_pipeline_slot_hint").format(slot=pipeline_slot)),
            "type": identity.add_field(tr("inf_pipeline_type"),
                                       build_combo_box(list(REGISTERED_PIPELINES), "")),
            "cameras": identity.add_field(
                tr("inf_pipeline_cameras"),
                _CameraChecklist([(slot, self._get_camera_name(slot))
                                  for slot in self._list_region_cameras()]),
                hint=tr("inf_pipeline_cameras_hint"), span=2),
        }

        result = page.add_section(tr("inf_result_section"), tr("inf_result_description"),
                                  columns=3)
        fields["min_detections"] = result.add_field(
            tr("inf_min_detections"), build_spin_box(0, _MAX_DETECTIONS, 0))
        fields["min_result_confidence_pct"] = result.add_field(
            tr("inf_min_result_conf"), build_spin_box(0, 100, 0),
            hint=tr("inf_min_result_conf_hint"))

        cycle = page.add_section(tr("inf_cycle_section"), tr("inf_cycle_description"))
        fields["frames_per_cycle"] = cycle.add_field(
            tr("inf_frames_per_cycle"), build_spin_box(1, _MAX_FRAMES_PER_CYCLE, 1))
        interval = build_spin_box(0, _MAX_INTERVAL_MS, 0)
        interval.setSingleStep(_INTERVAL_STEP_MS)
        interval.setSpecialValueText(tr("inf_every_frame"))
        fields["interval_s"] = cycle.add_field(tr("inf_pipeline_interval"), interval,
                                               hint=tr("inf_pipeline_interval_hint"))
        cycle_interval = build_spin_box(0, _MAX_CYCLE_INTERVAL_S, 0)
        cycle_interval.setSpecialValueText(tr("inf_no_wait"))
        fields["cycle_interval_s"] = cycle.add_field(tr("inf_cycle_interval"), cycle_interval,
                                                     hint=tr("inf_cycle_interval_hint"))
        capture_timeout = build_spin_box(0, _MAX_CYCLE_INTERVAL_S, _DEFAULT_CAPTURE_TIMEOUT_S)
        capture_timeout.setSpecialValueText(tr("inf_no_limit"))
        fields["capture_timeout_s"] = cycle.add_field(tr("inf_capture_timeout"),
                                                      capture_timeout,
                                                      hint=tr("inf_capture_timeout_hint"))
        fields["idle_cameras_between_cycles"] = cycle.add_field(
            tr("inf_idle_cameras_label"), QCheckBox(tr("inf_idle_cameras")),
            hint=tr("inf_idle_cameras_hint"))

        self._pipeline_forms[pipeline_slot] = fields
        self._cycle_triggers[pipeline_slot] = CYCLE_TRIGGER_INTERVAL
        wire_enable_toggle(enabled, [field for key, field in fields.items()
                                     if key != "enabled" and key not in _CYCLE_FIELDS])
        enabled.toggled.connect(lambda _checked: self._refresh_cycle_fields(pipeline_slot))
        fields["frames_per_cycle"].valueChanged.connect(
            lambda _value: self._refresh_cycle_fields(pipeline_slot))
        fields["name"].textChanged.connect(lambda _text: self._refresh_pipeline_names())
        self._refresh_cycle_fields(pipeline_slot)
        return page

    def _build_regions_card(self) -> QWidget:
        """Una sub-pestaña por cámara y, en cada una, la región de sus pipelines."""
        card = SettingsCard(tr("inf_box_regions"))
        # Una cámara que miran dos pipelines tiene el doble de filas que una que mira uno.
        tabs = build_slot_tabs(fit_to_current=True)
        for camera_slot in self._list_region_cameras():
            tabs.addTab(self._build_camera_regions_page(camera_slot),
                        self._get_camera_name(camera_slot))
        card.add_row(tabs)
        return card

    def _list_region_cameras(self) -> list[str]:
        """Las cámaras declaradas más las que algún pipeline nombra, en ese orden."""
        cameras = [str(slot) for slot in (self._config.get("cameras", {}) or {})]
        for pipeline_slot in (self._config.get("inference.pipelines", {}) or {}):
            for slot in self._config.get(f"inference.pipelines.{pipeline_slot}.cameras", []) or []:
                if str(slot) not in cameras:
                    cameras.append(str(slot))
        return cameras

    def _list_camera_pipelines(self, camera_slot: str) -> list[str]:
        """Los pipelines que miran esa cámara; uno sin lista las mira a todas."""
        pipelines = []
        for pipeline_slot in (self._config.get("inference.pipelines", {}) or {}):
            cameras = self._config.get(f"inference.pipelines.{pipeline_slot}.cameras", []) or []
            if not cameras or camera_slot in cameras:
                pipelines.append(str(pipeline_slot))
        return pipelines

    def _build_camera_regions_page(self, camera_slot: str) -> QWidget:
        page = SectionStack()
        section = page.add_section(tr("inf_regions_section"), tr("inf_regions_description"),
                                   columns=1)
        pipelines = self._list_camera_pipelines(camera_slot)
        if not pipelines:
            note = QLabel(tr("regions_no_pipelines"))
            note.setObjectName("hintLabel")
            section.add_row(note)
            return page
        rows = SettingsList()
        for pipeline_slot in pipelines:
            rows.add_row(self._build_region_row(pipeline_slot, camera_slot))
        section.add_row(rows)
        return page

    def _build_region_row(self, pipeline_slot: str, camera_slot: str) -> QWidget:
        """La región de un pipeline en una cámara: su color, su nombre, qué usa y el botón."""
        name = QLabel(self._get_pipeline_name(pipeline_slot))
        name.setObjectName("settingsListTitle")
        summary = QLabel()
        summary.setObjectName("settingsListDetail")
        problems = QLabel()
        problems.setObjectName("warningBanner")
        problems.setWordWrap(True)
        edit_button = QPushButton(tr("inf_regions_edit"))
        edit_button.clicked.connect(
            lambda: self.open_regions_requested.emit(camera_slot, pipeline_slot))

        title_line = QHBoxLayout()
        title_line.setSpacing(8)
        title_line.addWidget(_PipelineSwatch(self._get_pipeline_color(pipeline_slot)))
        title_line.addWidget(name, stretch=1)
        texts = QVBoxLayout()
        texts.setSpacing(_ROW_LINE_SPACING_PX)
        texts.addLayout(title_line)
        texts.addWidget(summary)
        texts.addWidget(problems)

        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_ROW_SPACING_PX)
        layout.addLayout(texts, stretch=1)
        layout.addWidget(edit_button)
        self._region_rows[(pipeline_slot, camera_slot)] = (summary, problems)
        self._region_names[(pipeline_slot, camera_slot)] = name
        return row

    # ── Contrato de la pestaña ───────────────────────────────────────────────

    def load(self):
        for model_slot, fields in self._model_forms.items():
            prefix = f"inference.models.{model_slot}"
            set_combo_value(fields["type"], self._config.get(f"{prefix}.type", "mock"))
            fields["path"].setText(str(self._config.get(f"{prefix}.path", "") or ""))
            fields["min_confidence_pct"].setValue(
                int(self._config.get(f"{prefix}.min_confidence_pct", 50))
            )

        for pipeline_slot, fields in self._pipeline_forms.items():
            prefix = f"inference.pipelines.{pipeline_slot}"
            # Primero: decide qué del ciclo se prende mientras se cargan los campos.
            self._cycle_triggers[pipeline_slot] = str(
                self._config.get(f"{prefix}.cycle_trigger", CYCLE_TRIGGER_INTERVAL)
                or CYCLE_TRIGGER_INTERVAL)
            # De la sección y no de la clave: un pipeline sin nombre no es una clave ausente.
            section = self._config.get(prefix, {}) or {}
            fields["name"].setText(str(section.get("name") or "") if isinstance(section, dict) else "")
            fields["enabled"].setChecked(bool(self._config.get(f"{prefix}.enabled", True)))
            set_combo_value(fields["type"],
                            self._config.get(f"{prefix}.type") or DEFAULT_PIPELINE)
            fields["cameras"].set_cameras(
                [str(slot) for slot in self._config.get(f"{prefix}.cameras", []) or []])
            fields["interval_s"].setValue(round(
                float(self._config.get(f"{prefix}.interval_s", 0.0) or 0.0) * _MS_PER_S))
            fields["min_detections"].setValue(
                int(self._config.get(f"{prefix}.min_detections", 0))
            )
            fields["min_result_confidence_pct"].setValue(
                int(self._config.get(f"{prefix}.min_result_confidence_pct", 0))
            )
            fields["frames_per_cycle"].setValue(
                int(self._config.get(f"{prefix}.frames_per_cycle", 1)))
            fields["cycle_interval_s"].setValue(_to_whole_seconds(
                self._config.get(f"{prefix}.cycle_interval_s", 0)))
            fields["capture_timeout_s"].setValue(_to_whole_seconds(
                self._config.get(f"{prefix}.capture_timeout_s", _DEFAULT_CAPTURE_TIMEOUT_S)))
            fields["idle_cameras_between_cycles"].setChecked(
                bool(self._config.get(f"{prefix}.idle_cameras_between_cycles", False)))
            self._refresh_cycle_fields(pipeline_slot)

        self.reload_regions()

    def reload_regions(self):
        """Vuelve a leer, del config, qué región usa cada pipeline en cada cámara."""
        self._refresh_pipeline_names()
        for (pipeline_slot, camera_slot), (summary, problems) in self._region_rows.items():
            summary.setText(self._describe_region(pipeline_slot, camera_slot))
            messages = self._describe_problems(pipeline_slot, camera_slot)
            problems.setText("\n".join(messages))
            problems.setVisible(bool(messages))

    def save(self):
        for model_slot, fields in self._model_forms.items():
            prefix = f"inference.models.{model_slot}"
            self._config.set(f"{prefix}.type", fields["type"].currentText())
            self._config.set(f"{prefix}.path", fields["path"].text())
            self._config.set(f"{prefix}.min_confidence_pct",
                             fields["min_confidence_pct"].value())

        for pipeline_slot, fields in self._pipeline_forms.items():
            prefix = f"inference.pipelines.{pipeline_slot}"
            # Sin nombre no se agrega la clave, pero uno que se borró sí se borra del archivo.
            name = fields["name"].text().strip()
            if name or "name" in (self._config.get(prefix, {}) or {}):
                self._config.set(f"{prefix}.name", name)
            self._config.set(f"{prefix}.enabled", fields["enabled"].isChecked())
            self._config.set(f"{prefix}.type", fields["type"].currentText())
            self._config.set(f"{prefix}.cameras", fields["cameras"].get_cameras())
            self._config.set(f"{prefix}.interval_s", fields["interval_s"].value() / _MS_PER_S)
            self._config.set(f"{prefix}.min_detections", fields["min_detections"].value())
            self._config.set(f"{prefix}.min_result_confidence_pct",
                             fields["min_result_confidence_pct"].value())
            self._config.set(f"{prefix}.frames_per_cycle", fields["frames_per_cycle"].value())
            self._config.set(f"{prefix}.cycle_interval_s", fields["cycle_interval_s"].value())
            self._config.set(f"{prefix}.capture_timeout_s",
                             fields["capture_timeout_s"].value())
            self._config.set(f"{prefix}.idle_cameras_between_cycles",
                             fields["idle_cameras_between_cycles"].isChecked())

    # ── Internos ─────────────────────────────────────────────────────────────

    def _on_browse_clicked(self, path_edit: QLineEdit):
        """Abre el selector en la carpeta de los pesos actuales y deja la ruta elegida."""
        start_dir = (os.path.dirname(resolve(path_edit.text(), ""))
                     if path_edit.text().strip() else DATA_DIR)
        if not os.path.isdir(start_dir):
            start_dir = DATA_DIR
        chosen, _ = QFileDialog.getOpenFileName(
            self, tr("inf_model_browse_title"), start_dir, tr("inf_model_browse_filter"))
        if chosen:
            path_edit.setText(to_config_path(chosen, DATA_DIR))

    def _refresh_cycle_fields(self, pipeline_slot: str):
        """Prende sólo lo que rige con el número de imágenes que muestra el campo."""
        fields = self._pipeline_forms[pipeline_slot]
        trigger = self._cycle_triggers[pipeline_slot]
        is_on = fields["enabled"].isChecked()
        has_cycle = is_cycle(fields["frames_per_cycle"].value(), trigger)
        set_field_enabled(fields["interval_s"], is_on and not has_cycle)
        set_field_enabled(fields["cycle_interval_s"],
                          is_on and has_cycle and trigger != CYCLE_TRIGGER_REQUEST)
        set_field_enabled(fields["capture_timeout_s"], is_on and has_cycle)
        set_field_enabled(fields["idle_cameras_between_cycles"], is_on and has_cycle)

    def _get_camera_name(self, camera_slot: str) -> str:
        """El nombre de la cámara, o su slot si no tiene."""
        section = (self._config.get("cameras", {}) or {}).get(camera_slot)
        name = section.get("name") if isinstance(section, dict) else None
        return str(name or "").strip() or camera_slot

    def _get_pipeline_name(self, pipeline_slot: str) -> str:
        """Lo que dice el campo de nombre del pipeline —aunque no se haya guardado—, o su slot."""
        fields = self._pipeline_forms.get(pipeline_slot)
        if fields is not None:
            return fields["name"].text().strip() or pipeline_slot
        section = self._config.get(f"inference.pipelines.{pipeline_slot}", {}) or {}
        name = section.get("name") if isinstance(section, dict) else None
        return str(name or "").strip() or pipeline_slot

    def _get_pipeline_color(self, pipeline_slot: str) -> tuple[int, int, int]:
        """El color BGR del pipeline, por su lugar en `inference.pipelines`, como el overlay."""
        index = get_pipeline_index(self._config.get("inference.pipelines", {}) or {},
                                   pipeline_slot)
        return overlay.get_pipeline_style(index).color_bgr

    def _refresh_pipeline_names(self):
        """Pone el nombre de cada pipeline, mientras se tipea, en su sub-pestaña y en sus filas."""
        for index, pipeline_slot in enumerate(self._pipeline_forms):
            self._pipeline_tabs.setTabText(index, self._get_pipeline_name(pipeline_slot))
        for (pipeline_slot, _camera_slot), label in self._region_names.items():
            label.setText(self._get_pipeline_name(pipeline_slot))

    def _read_region(self, pipeline_slot: str, camera_slot: str) -> dict:
        """El ROI de ese pipeline en esa cámara, o {} si ve el frame completo."""
        return resolve_pipeline_roi(
            self._config.get(f"inference.pipelines.{pipeline_slot}", {}), camera_slot)

    def _describe_region(self, pipeline_slot: str, camera_slot: str) -> str:
        """Qué región usa el pipeline, en una línea: «256,95 · 218×220 · nativo · …»."""
        roi = self._read_region(pipeline_slot, camera_slot)
        if not roi.get("enabled", False):
            return tr("inf_roi_mode_full")
        parts = [f"{int(roi.get('x_px', 0) or 0)},{int(roi.get('y_px', 0) or 0)}",
                 f"{int(roi.get('width_px', 0) or 0)}×{int(roi.get('height_px', 0) or 0)}"]
        if can_cut_native(self._config, camera_slot):
            parts.append(tr("inf_region_native"))
            parts.append(tr("inf_region_view_auto"))
        return " · ".join(parts)

    def _describe_problems(self, pipeline_slot: str, camera_slot: str) -> list[str]:
        """Los avisos que se pueden dar sin el frame: lo demás lo juzga el diálogo."""
        roi = self._read_region(pipeline_slot, camera_slot)
        problems = find_region_problems(
            roi, frame_size_px=None,
            is_native_lost=loses_native_cut(self._config, camera_slot),
            is_native_cut=can_cut_native(self._config, camera_slot))
        messages = {PROBLEM_NATIVE_PREPROCESSED: tr("regions_problem_native_preprocessed"),
                    PROBLEM_TOO_SMALL: tr("regions_problem_too_small")}
        return [messages[problem] for problem in problems if problem in messages]


def to_config_path(path: str, base_dir: str) -> str:
    """
    La ruta como va al config: relativa a `base_dir` si está adentro —así la instalación se
    puede mover de carpeta— y absoluta si no. Con `/`, que vale en Windows y en Linux.
    """
    absolute = os.path.normpath(os.path.abspath(path))
    try:
        relative = os.path.relpath(absolute, os.path.normpath(os.path.abspath(base_dir)))
    except ValueError:      # otra unidad, en Windows
        relative = os.pardir
    if relative == os.pardir or relative.startswith(os.pardir + os.sep):
        return absolute.replace(os.sep, "/")
    return relative.replace(os.sep, "/")


def _to_whole_seconds(value: object) -> int:
    """Segundos enteros; un valor positivo nunca queda en 0, que en estos campos es otro modo."""
    seconds = float(value or 0)
    return max(1, round(seconds)) if seconds > 0 else 0


class _CameraChecklist(QWidget):
    """
    Una casilla por cámara. La última marcada no se puede desmarcar: en el config una lista
    vacía es «todas», y desmarcarlas todas pondría al pipeline a mirar todo.
    """

    def __init__(self, cameras: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self._checks: dict[str, QCheckBox] = {}
        # La lista del config estaba vacía: se guarda vacía mientras sigan todas marcadas.
        self._is_all_by_default = False
        for camera_slot, name in cameras:
            check = QCheckBox(name)
            check.setToolTip(camera_slot)
            check.toggled.connect(self._on_check_toggled)
            self._checks[camera_slot] = check
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(build_check_grid(list(self._checks.values()), _CAMERA_COLUMNS))

    def set_cameras(self, camera_slots: list[str]):
        """Marca las de la lista; una lista vacía es «todas», como la lee el motor."""
        self._is_all_by_default = not camera_slots
        for camera_slot, check in self._checks.items():
            check.setChecked(self._is_all_by_default or camera_slot in camera_slots)
        self._on_check_toggled()

    def get_cameras(self) -> list[str]:
        """
        Las marcadas, en el orden de las cámaras. Una lista que era «todas» y sigue con todas
        marcadas vuelve vacía, como estaba: así una cámara que se agregue después también entra.
        """
        checked = [camera_slot for camera_slot, check in self._checks.items()
                   if check.isChecked()]
        if self._is_all_by_default and len(checked) == len(self._checks):
            return []
        return checked

    def _on_check_toggled(self, _checked: bool = False):
        checked = [check for check in self._checks.values() if check.isChecked()]
        for check in self._checks.values():
            check.setEnabled(not (len(checked) == 1 and check.isChecked()))


class _PipelineSwatch(QWidget):
    """El cuadrado con el color del pipeline: el del recuadro en el diálogo y en el monitor."""

    def __init__(self, color_bgr: tuple[int, int, int], parent=None):
        super().__init__(parent)
        blue, green, red = color_bgr
        self._color = QColor(red, green, blue)
        self.setFixedSize(_SWATCH_SIDE_PX, _SWATCH_SIDE_PX)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._color)
        painter.drawRoundedRect(self.rect(), _SWATCH_RADIUS_PX, _SWATCH_RADIUS_PX)
