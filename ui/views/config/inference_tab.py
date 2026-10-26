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

**El ROI de cada pipeline, por cámara.** Cada pipeline tiene un grupo por cámara que mira,
con tres opciones: la región de la cámara —sin clave propia, que es lo de siempre—, una
propia, o el frame completo. Las tres se guardan distinto porque el motor las distingue
así: sin `inference.pipelines.<slot>.roi.<cámara>` —se escribe en null—, con la sección y
`enabled: true`, o con la sección y `enabled: false`. El rectángulo se dibuja con el mismo
diálogo que el de la cámara, pidiéndolo con la clave de este pipeline, y con
`full_resolution` el decodificador lo corta del video nativo al reiniciar.
"""

from collections.abc import Callable, Iterable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from system.inference.models.model_factory import REGISTERED_MODELS
from system.inference.pipeline_factory import DEFAULT_PIPELINE, REGISTERED_PIPELINES
from system.inference.preprocessing import select_pipeline_roi

from system.config_manager import ConfigManager

from ui.strings import tr
from ui.views.config.abstract_tab import AbstractConfigTab
from ui.widgets.form import (
    add_check_row, add_form_row, add_hint_row, build_combo_box, build_double_spin_box,
    build_form_page, build_group_box, build_line_edit, build_slot_tabs, build_spin_box,
    build_value_combo_box, join_list, set_combo_data, set_combo_value, split_list,
)

_MAX_SIDE_PX = 8192
_MAX_DETECTIONS = 10000
_MAX_INTERVAL_S = 3600.0
_MAX_FRAMES_PER_CYCLE = 100      # N imágenes por medición; más que esto es otro problema
_MAX_CYCLE_INTERVAL_S = 86400.0  # una medición por día es el extremo razonable
_MAX_VIEW_SIDE_PX = 4096

# Las tres formas del ROI de un pipeline en una cámara. Son de la pantalla, no del config:
# el config las dice con la presencia de la sección y su `enabled`.
_ROI_MODE_CAMERA = "camera"
_ROI_MODE_OWN = "own"
_ROI_MODE_FULL = "full"

# Claves de la geometría del ROI, con la etiqueta que ya usa la pestaña de cámaras.
_ROI_FIELDS = (
    ("x_px", "cam_roi_x"),
    ("y_px", "cam_roi_y"),
    ("width_px", "cam_roi_width"),
    ("height_px", "cam_roi_height"),
)


class InferenceTab(AbstractConfigTab):
    """Sección `inference:` del config, menos el orden de las etapas y el anotado."""

    TITLE_KEY = "tab_inference"

    # (slot de cámara, clave del config donde va el rectángulo, clave de idioma del título),
    # igual que la pestaña de cámaras: la clave es la del ROI de un pipeline.
    open_roi_requested = Signal(str, str, str)

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        self._model_forms: dict[str, dict] = {}
        self._pipeline_forms: dict[str, dict] = {}
        self._roi_forms: dict[tuple[str, str], dict] = {}
        # Pipeline que abrió el diálogo de ROI: `reload_roi()` recibe sólo la cámara.
        self._roi_requester = ""

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(self._build_section(
            tr("inf_box_models"), self._build_model_page,
            self._config.get("inference.models", {}) or {}))
        layout.addWidget(self._build_section(
            tr("inf_box_pipelines"), self._build_pipeline_page,
            self._config.get("inference.pipelines", {}) or {}))
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

        # Las cámaras del pipeline al abrir la pestaña; sin lista, todas las declaradas.
        camera_slots = (self._config.get(f"inference.pipelines.{pipeline_slot}.cameras", [])
                        or list(self._config.get("cameras", {}) or {}))
        for camera_slot in camera_slots:
            form.addRow(self._build_roi_box(pipeline_slot, str(camera_slot)))
        return page

    def _build_roi_box(self, pipeline_slot: str, camera_slot: str) -> QWidget:
        """El ROI de un pipeline en una cámara: de la cámara, propio o el frame completo."""
        box, form = build_group_box(f"{tr('inf_roi_box')} {camera_slot}")
        mode = add_form_row(form, tr("inf_roi_mode"), build_value_combo_box({
            tr("inf_roi_mode_camera"): _ROI_MODE_CAMERA,
            tr("inf_roi_mode_own"): _ROI_MODE_OWN,
            tr("inf_roi_mode_full"): _ROI_MODE_FULL,
        }, _ROI_MODE_CAMERA))
        spins = {key: add_form_row(form, tr(label_key), build_spin_box(0, _MAX_SIDE_PX, 0))
                 for key, label_key in _ROI_FIELDS}
        full_resolution = add_check_row(form, tr("inf_roi_full_resolution"), False)
        output_side = add_form_row(form, tr("inf_roi_output_side"),
                                   build_spin_box(0, _MAX_VIEW_SIDE_PX, 0))
        draw_button = QPushButton(tr("cam_roi_tool"))
        draw_button.clicked.connect(lambda: self._on_draw_roi(pipeline_slot, camera_slot))
        form.addRow("", draw_button)
        add_hint_row(form, tr("inf_roi_note"))

        def apply_state():
            is_own = mode.currentData() == _ROI_MODE_OWN
            for widget in (*spins.values(), full_resolution, draw_button):
                widget.setEnabled(is_own)
            output_side.setEnabled(is_own and full_resolution.isChecked())

        mode.currentIndexChanged.connect(lambda _index: apply_state())
        full_resolution.toggled.connect(lambda _checked: apply_state())
        apply_state()

        self._roi_forms[(pipeline_slot, camera_slot)] = {
            "mode": mode, "spins": spins, "full_resolution": full_resolution,
            "output_short_side_px": output_side,
        }
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

        for (pipeline_slot, camera_slot), fields in self._roi_forms.items():
            roi = self._read_roi(pipeline_slot, camera_slot)
            if roi is None:
                set_combo_data(fields["mode"], _ROI_MODE_CAMERA)
                roi = {}
            else:
                set_combo_data(fields["mode"], _ROI_MODE_OWN if roi.get("enabled", False)
                               else _ROI_MODE_FULL)
            self._load_roi_geometry(fields, roi)
            fields["full_resolution"].setChecked(bool(roi.get("full_resolution", False)))
            fields["output_short_side_px"].setValue(int(roi.get("output_short_side_px", 0) or 0))

    def reload_roi(self, camera_slot: str):
        """Recarga la geometría que escribió el diálogo, en el pipeline que lo abrió."""
        fields = self._roi_forms.get((self._roi_requester, camera_slot))
        if fields is not None:
            self._load_roi_geometry(fields,
                                    self._read_roi(self._roi_requester, camera_slot) or {})

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

        for (pipeline_slot, camera_slot), fields in self._roi_forms.items():
            key = _build_roi_key(pipeline_slot, camera_slot)
            mode = fields["mode"].currentData()
            if mode == _ROI_MODE_CAMERA:
                # Sin sección rige el ROI de la cámara. Un config que nunca la tuvo no
                # gana una clave en null por abrir la pestaña y guardar.
                if self._read_roi(pipeline_slot, camera_slot) is not None:
                    self._config.set(key, None)
                continue
            self._config.set(f"{key}.enabled", mode == _ROI_MODE_OWN)
            for field_key, spin in fields["spins"].items():
                self._config.set(f"{key}.{field_key}", spin.value())
            self._config.set(f"{key}.full_resolution", fields["full_resolution"].isChecked())
            self._config.set(f"{key}.output_short_side_px",
                             fields["output_short_side_px"].value())

    # ── Internos ─────────────────────────────────────────────────────────────

    def _read_roi(self, pipeline_slot: str, camera_slot: str) -> dict | None:
        """El ROI propio de ese pipeline en esa cámara, o None si rige el de la cámara."""
        return select_pipeline_roi(
            self._config.get(f"inference.pipelines.{pipeline_slot}", {}), camera_slot)

    def _load_roi_geometry(self, fields: dict, roi: dict):
        for field_key, spin in fields["spins"].items():
            spin.setValue(int(roi.get(field_key, 0) or 0))

    def _on_draw_roi(self, pipeline_slot: str, camera_slot: str):
        """Pide el diálogo de ROI con la clave de este pipeline, y recuerda quién lo pidió."""
        self._roi_requester = pipeline_slot
        self.open_roi_requested.emit(camera_slot, _build_roi_key(pipeline_slot, camera_slot),
                                     "inf_roi_title")


def _build_roi_key(pipeline_slot: str, camera_slot: str) -> str:
    """Ruta del ROI de un pipeline en una cámara."""
    return f"inference.pipelines.{pipeline_slot}.roi.{camera_slot}"
