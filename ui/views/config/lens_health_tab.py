"""
Pestaña de óptica: los parámetros del detector de lente sucio y la referencia de cada
cámara.

Es dueña de la sección `lens_health:` y, además, de `cameras.<slot>.lens_health` — la
referencia de vidrio limpio, que vive en la cámara porque depende del lente y del
montaje. Las dos cosas van en la misma pantalla a propósito: calibrar es una sola tarea
de puesta en marcha —limpiar el vidrio, apretar el botón, esperar las muestras— y
repartirla entre dos pestañas obligaría a ir y volver para hacerla.

La referencia se puede editar a mano además de calibrarse: un equipo gemelo, con el mismo
lente y el mismo montaje, arranca copiando la del primero en vez de esperar la ventana.

**La región donde se mide la nitidez es de la cámara y se dibuja acá**
(`cameras.<slot>.lens_health.roi`): es otra pregunta que la de qué mira cada modelo —dónde hay
textura estática y nítida para medir el vidrio—, así que no comparte el rectángulo de ningún
pipeline. Sin región se mide el frame entero.

El botón de calibrar no mide acá. Emite `calibrate_requested` y lo atiende `main.py`, que
es quien tiene los frames y el tick: la UI no abre cámaras (ver `docs/ui.md`).
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from system.config_manager import ConfigManager

from ui.strings import tr
from ui.views.config.abstract_tab import AbstractConfigTab
from ui.widgets.form import (
    add_check_row, add_form_row, add_hint_row, build_double_spin_box, build_group_box,
    build_readonly_line_edit, build_spin_box, wire_enable_toggle,
)

_MAX_INTERVAL_S = 3600
_MAX_WINDOW_H = 168            # una semana; más que eso la ventana deja de reaccionar
_MAX_ANALYSIS_WIDTH_PX = 4096
_MAX_CALIBRATION_SAMPLES = 500
_MAX_FRAME_SIDE_PX = 8192      # tope de los spin de la región; cubre cualquier sensor del catálogo
_MAX_VARIANCE = 1e6            # el Laplaciano de una imagen con mucho detalle llega a miles
_SECONDS_PER_HOUR = 3600


class LensHealthTab(AbstractConfigTab):
    """Parámetros del detector y referencia por cámara. Ver el contrato en `abstract_tab.py`."""

    TITLE_KEY = "tab_lens_health"

    calibrate_requested = Signal(str)     # slot de la cámara que hay que calibrar
    # (slot de cámara, clave del config donde va el rectángulo, clave de idioma del título).
    # La pestaña manda la clave porque es la dueña de su sección: quien abre el diálogo no
    # tiene por qué saber dónde guarda sus cosas cada una.
    open_roi_requested = Signal(str, str, str)

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        self._references: dict[str, _ReferenceBox] = {}

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addWidget(self._build_detection_box())
        for camera_slot in (self._config.get("cameras", {}) or {}):
            box = _ReferenceBox(self._config, camera_slot)
            box.calibrate_requested.connect(self.calibrate_requested)
            box.open_roi_requested.connect(self.open_roi_requested)
            self._references[camera_slot] = box
            layout.addWidget(box)
        layout.addStretch()

        self.load()

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_detection_box(self) -> QWidget:
        box, form = build_group_box(tr("lens_box_detection"))
        self._enabled_check = add_check_row(form, tr("lens_enabled"), True)
        self._interval_spin = add_form_row(
            form, tr("lens_interval"), build_spin_box(1, _MAX_INTERVAL_S, 10)
        )
        # En horas y no en segundos: la ventana son horas y un campo de 28800 se tipea mal.
        self._window_spin = add_form_row(
            form, tr("lens_window"), build_double_spin_box(0.1, _MAX_WINDOW_H, 8.0,
                                                           decimals=1, step=0.5)
        )
        self._threshold_spin = add_form_row(
            form, tr("lens_threshold"), build_spin_box(1, 100, 50)
        )
        add_hint_row(form, tr("lens_method_note"))
        self._analysis_width_spin = add_form_row(
            form, tr("lens_analysis_width"),
            build_spin_box(64, _MAX_ANALYSIS_WIDTH_PX, 960)
        )
        add_hint_row(form, tr("lens_width_note"))
        self._samples_spin = add_form_row(
            form, tr("lens_calibration_samples"),
            build_spin_box(1, _MAX_CALIBRATION_SAMPLES, 30)
        )
        return box

    # ── Contrato de la pestaña ───────────────────────────────────────────────

    def load(self):
        self._enabled_check.setChecked(bool(self._config.get("lens_health.enabled", True)))
        self._interval_spin.setValue(int(self._config.get("lens_health.interval_s", 10) or 10))
        window_s = float(self._config.get("lens_health.window_s", 28800) or 28800)
        self._window_spin.setValue(window_s / _SECONDS_PER_HOUR)
        self._threshold_spin.setValue(
            int(self._config.get("lens_health.threshold_pct", 50) or 50))
        self._analysis_width_spin.setValue(
            int(self._config.get("lens_health.analysis_width_px", 960) or 960))
        self._samples_spin.setValue(
            int(self._config.get("lens_health.calibration_samples", 30) or 30))
        for box in self._references.values():
            box.load()

    def save(self):
        self._config.set("lens_health.enabled", self._enabled_check.isChecked())
        self._config.set("lens_health.interval_s", self._interval_spin.value())
        self._config.set("lens_health.window_s",
                         int(round(self._window_spin.value() * _SECONDS_PER_HOUR)))
        self._config.set("lens_health.threshold_pct", self._threshold_spin.value())
        self._config.set("lens_health.analysis_width_px", self._analysis_width_spin.value())
        self._config.set("lens_health.calibration_samples", self._samples_spin.value())
        for box in self._references.values():
            box.save()

    def reload_roi(self, camera_slot: str):
        """Recarga la geometría de la región de una cámara tras guardar el diálogo interactivo."""
        box = self._references.get(camera_slot)
        if box is not None:
            box.load_roi()

    def refresh_reference(self, camera_slot: str):
        """Recarga la referencia de una cámara después de que la calibración la escribió."""
        box = self._references.get(camera_slot)
        if box is not None:
            box.load()


class _ReferenceBox(QWidget):
    """
    Lo de óptica de una cámara: la referencia de vidrio limpio —el número, cuándo se sacó y
    el botón— y la región donde se mide.
    """

    calibrate_requested = Signal(str)
    open_roi_requested = Signal(str, str, str)

    def __init__(self, config_manager: ConfigManager, camera_slot: str, parent=None):
        super().__init__(parent)
        self._config = config_manager
        self._camera_slot = camera_slot
        self._prefix = f"cameras.{camera_slot}.lens_health.reference"
        self._roi_prefix = f"cameras.{camera_slot}.lens_health.roi"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        box, form = build_group_box(f"{tr('lens_box_reference')} — {self._camera_name}")
        self._variance_spin = add_form_row(
            form, tr("lens_reference_variance"),
            build_double_spin_box(0.0, _MAX_VARIANCE, 0.0, decimals=2, step=1.0,
                                  special_value_text=tr("lens_uncalibrated")),
        )
        self._state_field = add_form_row(
            form, tr("lens_reference_state"), build_readonly_line_edit("")
        )
        calibrate_button = QPushButton(tr("lens_calibrate"))
        calibrate_button.clicked.connect(
            lambda: self.calibrate_requested.emit(self._camera_slot)
        )
        form.addRow("", calibrate_button)
        add_hint_row(form, tr("lens_calibrate_note"))
        layout.addWidget(box)
        layout.addWidget(self._build_region_box())

    def _build_region_box(self) -> QWidget:
        box, form = build_group_box(f"{tr('lens_box_region')} — {self._camera_name}")
        self._roi_check = add_check_row(form, tr("cam_roi_enabled"), False)
        self._roi_spins = {}
        for key, label_key in (("x_px", "cam_roi_x"), ("y_px", "cam_roi_y"),
                               ("width_px", "cam_roi_width"), ("height_px", "cam_roi_height")):
            self._roi_spins[key] = add_form_row(
                form, tr(label_key), build_spin_box(0, _MAX_FRAME_SIDE_PX, 0)
            )
        roi_button = QPushButton(tr("cam_roi_tool"))
        roi_button.clicked.connect(
            lambda: self.open_roi_requested.emit(
                self._camera_slot, self._roi_prefix, "lens_roi_title")
        )
        form.addRow("", roi_button)
        add_hint_row(form, tr("lens_region_note"))
        wire_enable_toggle(self._roi_check, list(self._roi_spins.values()) + [roi_button])
        return box

    @property
    def _camera_name(self) -> str:
        return str(self._config.get(f"cameras.{self._camera_slot}.name", "")
                   or self._camera_slot)

    # ── Contrato de la pestaña ───────────────────────────────────────────────

    def load(self):
        reference = self._config.get(self._prefix, {}) or {}
        self._variance_spin.setValue(float(reference.get("variance", 0.0) or 0.0))
        self._state_field.setText(self._build_state_text(reference))
        # La casilla se lee acá y no en `load_roi()`: esto es la carga completa desde el
        # archivo; `load_roi()` es el callback del diálogo, que sólo escribe geometría y no
        # tiene que pisar lo que el operador acaba de tildar.
        self._roi_check.setChecked(bool(self._read_roi().get("enabled", False)))
        self.load_roi()

    def load_roi(self):
        """
        Recarga sólo la geometría de la región: es lo único que cambia el diálogo interactivo.

        **El `enabled` no se toca a propósito.** El diálogo escribe las cuatro coordenadas y
        nada más, así que releer la casilla desde el archivo pisaría lo que el operador acaba
        de marcar y todavía no guardó —y como los campos y el botón cuelgan de esa casilla, se
        apagarían solos justo después de dibujar—.
        """
        roi = self._read_roi()
        for key, spin in self._roi_spins.items():
            spin.setValue(int(roi.get(key, 0) or 0))

    def save(self):
        # Sólo la varianza: el resto de la referencia —fecha, muestras, dispersión y
        # condiciones— lo escribe la calibración y describe una medición que pasó. Dejar
        # que la pantalla lo pisara daría una fecha que no corresponde a ese número.
        self._config.set(f"{self._prefix}.variance", self._variance_spin.value())
        self._config.set(f"{self._roi_prefix}.enabled", self._roi_check.isChecked())
        for key, spin in self._roi_spins.items():
            self._config.set(f"{self._roi_prefix}.{key}", spin.value())

    # ── Internos ─────────────────────────────────────────────────────────────

    def _read_roi(self) -> dict:
        """La región de esta cámara, leída de su sección de óptica, que siempre está."""
        section = self._config.get(f"cameras.{self._camera_slot}.lens_health", {}) or {}
        roi = section.get("roi") if isinstance(section, dict) else None
        return roi if isinstance(roi, dict) else {}

    def _build_state_text(self, reference: dict) -> str:
        """Cuándo se calibró y con qué, o por qué esa cámara todavía no informa nada."""
        if not float(reference.get("variance", 0.0) or 0.0):
            return tr("lens_uncalibrated")
        return tr("lens_calibrated_on").format(
            date=str(reference.get("calibrated_at", "") or "—"),
            samples=int(reference.get("sample_count", 0) or 0),
            dispersion=f"{float(reference.get('dispersion_pct', 0.0) or 0.0):.1f}",
        )
