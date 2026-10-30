"""
Pestaña de inferencia: `inference.models` e `inference.pipelines`.

Dos secciones, una debajo de la otra: «Modelos» y «Pipelines», cada una con una
sub-pestaña por slot, como Cámaras. Los slots salen del config y son fijos por proyecto,
igual que las cámaras. Los pipelines van debajo porque nombran modelos, así que se leen en
ese orden, y las dos barras nunca se mezclan: un slot de modelo y uno de pipeline en la
misma fila de pestañas no se distinguen por el rótulo. Con más slots de los que entran a
lo ancho, la barra muestra flechas en vez de cortar rótulos (`build_slot_tabs()`).

**El orden de las etapas no está acá y no puede estarlo**: se programa en
`system/inference/pipeline.py`, porque encadenar dos modelos es lógica del proceso. De
esta pestaña sale de dónde se carga cada modelo y qué cámaras entran a cada pipeline.

Los tipos de modelo y de pipeline que ofrecen los combos salen de `REGISTERED_MODELS` y
`REGISTERED_PIPELINES`, así que un fork que registra el suyo en la fábrica lo ve acá sin
tocar la UI.

Tres cosas de la sección no están en esta pantalla, y no es un olvido:

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

Las claves del ciclo de medición —`frames_per_cycle` y las tres que dependen de él— sí se
muestran: no las lee el motor sino el scheduler, y la nota del grupo dice que con una
imagen por medición las otras tres no hacen nada.

**Las regiones tienen su propia sección, «Regiones (ROI)», y no se editan ahí.** Va debajo de
«Pipelines» con una sub-pestaña por cámara, rotulada con su nombre. Cada una tiene una fila por
pipeline que la mira, con el nombre del pipeline, qué región usa —la propia o el frame completo—
y lo que se puede avisar sin el frame. El botón «Editar regiones…» abre el diálogo de esa cámara
con ese pipeline elegido (`PipelineRegionsDialog`): ahí se dibujan todas juntas sobre el frame
en vivo. Esta pestaña no escribe ninguna clave de `inference.pipelines.<slot>.roi`: el diálogo
persiste lo suyo, y acá `reload_regions()` vuelve a leer las filas cuando se cierra.

**El nombre de un pipeline** (`inference.pipelines.<slot>.name`) es opcional y es el que se ve en
esas filas y en el diálogo: el slot sigue siendo la identidad, y sin nombre se muestra él.
"""

from collections.abc import Callable, Iterable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from system.config_manager import ConfigManager
from system.inference.models.model_factory import REGISTERED_MODELS
from system.inference.pipeline_factory import DEFAULT_PIPELINE, REGISTERED_PIPELINES
from system.inference.preprocessing import can_cut_native, loses_native_cut
from system.inference.regions import (
    PROBLEM_NATIVE_PREPROCESSED, PROBLEM_TOO_SMALL, find_region_problems,
    resolve_pipeline_roi,
)

from ui.strings import tr
from ui.views.config.abstract_tab import AbstractConfigTab
from ui.widgets.form import (
    add_check_row, add_form_row, add_hint_row, build_combo_box, build_double_spin_box,
    build_form_page, build_group_box, build_line_edit, build_slot_tabs, build_spin_box,
    join_list, set_combo_value, split_list,
)

_MAX_SIDE_PX = 8192
_MAX_DETECTIONS = 10000
_MAX_INTERVAL_S = 3600.0
_MAX_FRAMES_PER_CYCLE = 100      # N imágenes por medición; más que esto es otro problema
_MAX_CYCLE_INTERVAL_S = 86400.0  # una medición por día es el extremo razonable


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
        # (pipeline, cámara) -> (qué región usa, avisos): las dos etiquetas de su fila.
        self._region_rows: dict[tuple[str, str], tuple[QLabel, QLabel]] = {}
        # (pipeline, cámara) -> el grupo de su fila, que lleva el nombre del pipeline.
        self._region_names: dict[tuple[str, str], QGroupBox] = {}

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(self._build_section(
            tr("inf_box_models"), self._build_model_page,
            self._config.get("inference.models", {}) or {}))
        layout.addWidget(self._build_section(
            tr("inf_box_pipelines"), self._build_pipeline_page,
            self._config.get("inference.pipelines", {}) or {}))
        layout.addWidget(self._build_regions_section())
        layout.addStretch()
        self.load()

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_section(self, title: str, build_page: Callable[[str], QWidget],
                       slots: Iterable[str]) -> QWidget:
        """Un grupo con una sub-pestaña por slot; el rótulo de cada una es el slot."""
        box, form = build_group_box(title)
        tabs = build_slot_tabs()
        for slot in slots:
            tabs.addTab(build_page(slot), str(slot))
        form.addRow(tabs)
        return box

    def _build_model_page(self, model_slot: str) -> QWidget:
        page, form = build_form_page()
        fields = {
            "type": add_form_row(form, tr("inf_model_type"),
                                 build_combo_box(list(REGISTERED_MODELS), "")),
            "path": add_form_row(form, tr("inf_model_path"),
                                 build_line_edit("", "models/model.pt")),
            "min_confidence_pct": add_form_row(form, tr("inf_min_confidence"),
                                               build_spin_box(0, 100, 50)),
            "max_side_px": add_form_row(form, tr("inf_max_side"),
                                        build_spin_box(0, _MAX_SIDE_PX, 0)),
        }
        self._model_forms[model_slot] = fields
        return page

    def _build_pipeline_page(self, pipeline_slot: str) -> QWidget:
        page, form = build_form_page()
        fields = {
            "name": add_form_row(form, tr("inf_pipeline_name"),
                                 build_line_edit("", pipeline_slot)),
            "enabled": add_check_row(form, tr("field_enabled"), True),
            "type": add_form_row(form, tr("inf_pipeline_type"),
                                 build_combo_box(list(REGISTERED_PIPELINES), "")),
            "cameras": add_form_row(form, tr("inf_pipeline_cameras"),
                                    build_line_edit("", "camera_1, camera_2")),
            "interval_s": add_form_row(
                form, tr("inf_pipeline_interval"),
                build_double_spin_box(0.0, _MAX_INTERVAL_S, 0.0, decimals=2, step=0.5),
            ),
            "min_detections": add_form_row(form, tr("inf_min_detections"),
                                           build_spin_box(0, _MAX_DETECTIONS, 0)),
            "min_result_confidence_pct": add_form_row(form, tr("inf_min_result_conf"),
                                                      build_spin_box(0, 100, 0)),
            # El ciclo de medición: los lee el scheduler, no el motor. Con
            # `frames_per_cycle` en 1 los otros tres no hacen nada.
            "frames_per_cycle": add_form_row(form, tr("inf_frames_per_cycle"),
                                             build_spin_box(1, _MAX_FRAMES_PER_CYCLE, 1)),
            "cycle_interval_s": add_form_row(
                form, tr("inf_cycle_interval"),
                build_double_spin_box(0.0, _MAX_CYCLE_INTERVAL_S, 0.0, decimals=1, step=10.0),
            ),
            "capture_timeout_s": add_form_row(
                form, tr("inf_capture_timeout"),
                build_double_spin_box(0.0, _MAX_CYCLE_INTERVAL_S, 30.0, decimals=1, step=5.0),
            ),
            "idle_cameras_between_cycles": add_check_row(
                form, tr("inf_idle_cameras"), False),
        }
        add_hint_row(form, tr("inf_cycle_note"))
        self._pipeline_forms[pipeline_slot] = fields

        fields["name"].textChanged.connect(lambda _text: self._refresh_region_names())
        return page

    def _build_regions_section(self) -> QWidget:
        """Un grupo con una sub-pestaña por cámara y, en cada una, la región de sus pipelines."""
        box, form = build_group_box(tr("inf_box_regions"))
        tabs = build_slot_tabs()
        for camera_slot in self._list_region_cameras():
            tabs.addTab(self._build_camera_regions_page(camera_slot),
                        self._get_camera_name(camera_slot))
        form.addRow(tabs)
        return box

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
        page, form = build_form_page()
        pipelines = self._list_camera_pipelines(camera_slot)
        if not pipelines:
            add_hint_row(form, tr("regions_no_pipelines"))
        for pipeline_slot in pipelines:
            form.addRow(self._build_region_row(pipeline_slot, camera_slot))
        return page

    def _build_region_row(self, pipeline_slot: str, camera_slot: str) -> QWidget:
        """La región de un pipeline en una cámara: su nombre, qué usa y el botón que la edita."""
        box, form = build_group_box(self._get_pipeline_name(pipeline_slot))
        summary = QLabel()
        problems = QLabel()
        problems.setObjectName("warningBanner")
        problems.setWordWrap(True)
        edit_button = QPushButton(tr("inf_regions_edit"))
        edit_button.clicked.connect(
            lambda: self.open_regions_requested.emit(camera_slot, pipeline_slot))
        line = QWidget()
        row = QHBoxLayout(line)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(summary, stretch=1)
        row.addWidget(edit_button)
        form.addRow(line)
        form.addRow(problems)
        self._region_rows[(pipeline_slot, camera_slot)] = (summary, problems)
        self._region_names[(pipeline_slot, camera_slot)] = box
        return box

    # ── Contrato de la pestaña ───────────────────────────────────────────────

    def load(self):
        for model_slot, fields in self._model_forms.items():
            prefix = f"inference.models.{model_slot}"
            set_combo_value(fields["type"], self._config.get(f"{prefix}.type", "mock"))
            fields["path"].setText(str(self._config.get(f"{prefix}.path", "") or ""))
            fields["min_confidence_pct"].setValue(
                int(self._config.get(f"{prefix}.min_confidence_pct", 50))
            )
            fields["max_side_px"].setValue(int(self._config.get(f"{prefix}.max_side_px", 0)))

        for pipeline_slot, fields in self._pipeline_forms.items():
            prefix = f"inference.pipelines.{pipeline_slot}"
            # De la sección y no de la clave: un pipeline sin nombre no es una clave ausente.
            section = self._config.get(prefix, {}) or {}
            fields["name"].setText(str(section.get("name") or "") if isinstance(section, dict) else "")
            fields["enabled"].setChecked(bool(self._config.get(f"{prefix}.enabled", True)))
            set_combo_value(fields["type"],
                            self._config.get(f"{prefix}.type") or DEFAULT_PIPELINE)
            fields["cameras"].setText(join_list(self._config.get(f"{prefix}.cameras", [])))
            fields["interval_s"].setValue(float(self._config.get(f"{prefix}.interval_s", 0.0)))
            fields["min_detections"].setValue(
                int(self._config.get(f"{prefix}.min_detections", 0))
            )
            fields["min_result_confidence_pct"].setValue(
                int(self._config.get(f"{prefix}.min_result_confidence_pct", 0))
            )
            fields["frames_per_cycle"].setValue(
                int(self._config.get(f"{prefix}.frames_per_cycle", 1)))
            fields["cycle_interval_s"].setValue(
                float(self._config.get(f"{prefix}.cycle_interval_s", 0.0)))
            fields["capture_timeout_s"].setValue(
                float(self._config.get(f"{prefix}.capture_timeout_s", 30.0)))
            fields["idle_cameras_between_cycles"].setChecked(
                bool(self._config.get(f"{prefix}.idle_cameras_between_cycles", False)))

        self.reload_regions()

    def reload_regions(self):
        """Vuelve a leer, del config, qué región usa cada pipeline en cada cámara."""
        self._refresh_region_names()
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
            self._config.set(f"{prefix}.max_side_px", fields["max_side_px"].value())

        for pipeline_slot, fields in self._pipeline_forms.items():
            prefix = f"inference.pipelines.{pipeline_slot}"
            # Sin nombre no se agrega la clave, pero uno que se borró sí se borra del archivo.
            name = fields["name"].text().strip()
            if name or "name" in (self._config.get(prefix, {}) or {}):
                self._config.set(f"{prefix}.name", name)
            self._config.set(f"{prefix}.enabled", fields["enabled"].isChecked())
            self._config.set(f"{prefix}.type", fields["type"].currentText())
            self._config.set(f"{prefix}.cameras", split_list(fields["cameras"].text()))
            self._config.set(f"{prefix}.interval_s", fields["interval_s"].value())
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

    def _refresh_region_names(self):
        """Pone en cada fila el nombre que tiene el pipeline en su campo, mientras se tipea."""
        for (pipeline_slot, _camera_slot), box in self._region_names.items():
            box.setTitle(self._get_pipeline_name(pipeline_slot))

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
