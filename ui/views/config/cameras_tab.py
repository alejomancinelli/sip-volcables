"""
Pestaña de cámaras: una sub-pestaña por slot de `cameras:` con dos tarjetas —la de la cámara,
con su conexión y su adquisición, y la de su óptica, con la región y la referencia de vidrio
limpio— y, la última, la sub-pestaña «Óptica» con la detección de lente sucio, que es una sola
para todas las cámaras.

Los slots salen del config y son fijos por proyecto: la pestaña edita los que están y
no crea ni borra ninguno. Agregar una cámara es una edición del `config.yaml` y un
reinicio, porque la topología del sistema no es una preferencia de la UI.

**Es dueña de `cameras:` y de `lens_health:`.** La óptica de una cámara va en su
sub-pestaña porque su referencia y su región viven en `cameras.<slot>.lens_health`
—dependen del lente y del montaje— y calibrar es una tarea de puesta en marcha de esa
cámara: limpiar el vidrio, apretar el botón, esperar las muestras. Lo que vale para todas
—`lens_health:`— no va con ninguna: mostrarlo debajo de una cámara hacía creer que era de
ella. Va en una sub-pestaña propia, la última y con otro tinte, para que no se lea como una
cámara más (`add_general_tab()`). La referencia se puede editar a mano además de
calibrarse: un equipo gemelo arranca copiando la del primero en vez de esperar la ventana.
La región donde se mide la nitidez es otra pregunta que la de qué mira cada modelo —dónde
hay textura fija y nítida—, así que no comparte el rectángulo de ningún pipeline; sin
región se mide el frame entero.

El botón de calibrar no mide acá. Emite `calibrate_requested` y lo atiende `main.py`, que
es quien tiene los frames y el tick: la UI no abre cámaras (ver `docs/ui.md`).

**Lo que no aplica a la cámara no se muestra.** La exposición y la ganancia, sólo si el
catálogo dice que el driver las lleva al sensor (`sensor_settings`): una cámara IP o un
video las ignoran. La decodificación, sólo si el driver decodifica video (`decodes`): una
GigE entrega el frame ya armado. La dirección, salvo en una simulada. El ajuste de imagen y la corrección
de lente aparecen sólo si la cámara tiene su sección en el config: son para reproducir un
equipo y para un lente con distorsión, y un proyecto que no los usa los saca del archivo
en vez de mostrar campos que no hacen nada. Sin la sección, `save()` tampoco la escribe:
crearla al guardar la volvería a mostrar.

Con «Cámara habilitada» destildado, los campos de esa cámara quedan apagados: no se edita
una cámara que no va a abrir.

De la decodificación se edita quién decodifica (`decode.backend`) y el tamaño del frame se
muestra pero no se edita. El backend cambia quién trabaja y no la imagen que ve la app, así
que cambiarlo es seguro. El tamaño es el espacio de todo lo que el config mide en píxeles —las
regiones de los pipelines y las alturas o zonas que dibuja el proceso—: cambiarlo desde la
planta dejaría todo eso midiendo mal sin un error. Se cambia en el config.yaml, en la puesta
en marcha, y después se vuelve a dibujar.

Los intrínsecos del lente se muestran pero no se editan: una matriz 3x3 y hasta 14
coeficientes no son un formulario, y equivocar un dígito ahí mueve todas las coordenadas
del proyecto.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QCheckBox, QPushButton, QVBoxLayout, QWidget

from system.config_manager import ConfigManager
from tools.camera.camera_catalog import CAMERA_CATALOG
from tools.image.enhance import BRIGHTNESS_FACTOR_MAX, BRIGHTNESS_FACTOR_MIN, CLAHE_CLIP_MAX

from ui.strings import tr
from ui.views.config.abstract_tab import AbstractConfigTab
from ui.widgets.form import (
    add_general_tab, build_combo_box, build_double_spin_box, build_inline_row, build_line_edit,
    build_readonly_line_edit, build_slot_tabs, build_spin_box, build_value_combo_box,
    set_combo_data, wire_enable_toggle,
)
from ui.widgets.settings_card import SettingsCard

# Clave de idioma -> valor de `rotation` en el config. El valor es el que entiende el
# driver y viaja en el item del combo: no se reconstruye desde el texto, que cambia
# con el idioma. Una rotación que el repo no conoce entra como opción y vuelve al
# config tal cual; caer a «sin rotación» cambiaría, en silencio, cómo entra la imagen.
_ROTATIONS = {
    "rotation_none":  None,
    "rotation_90cw":  "90cw",
    "rotation_90ccw": "90ccw",
    "rotation_180":   "180",
}

# Clave de idioma -> valor de `decode.backend`: el vocabulario de `tools/camera/decoder.py`,
# que no se importa porque arrastra GStreamer. Un backend que el repo no conoce entra como
# opción y vuelve al config tal cual, como la rotación.
_DECODE_BACKENDS = {
    "decode_cpu":   "cpu",
    "decode_nvdec": "nvdec",
    "decode_auto":  "auto",
}
_DEFAULT_DECODE_BACKEND = "cpu"     # el del decodificador sin la clave

_MAX_EXPOSURE_US = 1000000
_MAX_GAIN = 48.0
_MAX_FPS = 240
_FACTOR_PREFIX = "x"          # el factor se lee como multiplicador: x0.1, x1.0, x4.6
_MOCK_DRIVER = "mock"         # el único sin dirección

_MAX_INTERVAL_S = 3600
_MAX_WINDOW_H = 168            # una semana; más que eso la ventana deja de reaccionar
_MAX_ANALYSIS_WIDTH_PX = 4096
_MAX_CALIBRATION_SAMPLES = 500
_MAX_FRAME_SIDE_PX = 8192      # tope de los spin de la región; cubre cualquier sensor del catálogo
_MAX_VARIANCE = 1e6            # el Laplaciano de una imagen con mucho detalle llega a miles
_SECONDS_PER_HOUR = 3600
_SECONDS_PER_MINUTE = 60

_CARD_SPACING_PX = 24          # entre tarjetas, como la web
_TAB_PAGE_TOP_PX = 16          # aire entre la barra de cámaras y la primera tarjeta


class CamerasTab(AbstractConfigTab):
    """Una sub-pestaña por cámara y una de óptica general. Ver el contrato en `abstract_tab.py`."""

    TITLE_KEY = "tab_cameras"

    calibrate_requested = Signal(str)     # slot de la cámara cuya óptica hay que calibrar
    # (slot de cámara, clave del config donde va el rectángulo, clave de idioma del título).
    # La pestaña manda la clave porque es la dueña de su sección: quien abre el diálogo no
    # tiene por qué saber dónde guarda sus cosas cada una.
    open_roi_requested = Signal(str, str, str)

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        self._forms: dict[str, _CameraForm] = {}

        layout = QVBoxLayout(self)
        # Sin margen propio: las tarjetas arrancan en el borde del encabezado de la página.
        layout.setContentsMargins(0, 0, 0, 0)

        # La óptica general es mucho más corta que una cámara.
        self._tabs = build_slot_tabs(in_card=False, fit_to_current=True)
        for camera_slot in (self._config.get("cameras", {}) or {}):
            form = _CameraForm(self._config, camera_slot)
            form.calibrate_requested.connect(self.calibrate_requested)
            form.open_roi_requested.connect(self.open_roi_requested)
            self._forms[camera_slot] = form
            self._tabs.addTab(form, form.camera_name)
        add_general_tab(self._tabs, self._build_optics_page(), tr("cam_optics_tab"))
        layout.addWidget(self._tabs)
        layout.addStretch()

        self.load()

    def _build_optics_page(self) -> QWidget:
        card = SettingsCard(tr("lens_general_eyebrow"))
        section = card.add_section(tr("lens_box_detection"), tr("lens_method_note"), columns=3)
        self._lens_enabled_check = section.add_row(QCheckBox(tr("lens_enabled")))
        self._interval_spin = section.add_field(
            tr("lens_interval"), build_spin_box(1, _MAX_INTERVAL_S, 10),
            hint=tr("unit_seconds"))
        # En horas y no en segundos: la ventana son horas y un campo de 28800 se tipea mal.
        self._window_spin = section.add_field(
            tr("lens_window"), build_spin_box(1, _MAX_WINDOW_H, 8), hint=tr("unit_hours"))
        self._threshold_spin = section.add_field(
            tr("lens_threshold"), build_spin_box(1, 100, 50), hint=tr("lens_threshold_hint"))
        self._analysis_width_spin = section.add_field(
            tr("lens_analysis_width"), build_spin_box(64, _MAX_ANALYSIS_WIDTH_PX, 960),
            hint=tr("lens_width_note"))
        self._samples_spin = section.add_field(
            tr("lens_calibration_samples"), build_spin_box(1, _MAX_CALIBRATION_SAMPLES, 30),
            hint=tr("lens_samples_hint"))
        wire_enable_toggle(self._lens_enabled_check, [
            self._interval_spin, self._window_spin, self._threshold_spin,
            self._analysis_width_spin, self._samples_spin,
        ])
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, _TAB_PAGE_TOP_PX, 0, 0)
        page_layout.addWidget(card)
        page_layout.addStretch()
        return page

    # ── Contrato de la pestaña ───────────────────────────────────────────────

    def load(self):
        for form in self._forms.values():
            form.load()
        self._lens_enabled_check.setChecked(bool(self._config.get("lens_health.enabled", True)))
        self._interval_spin.setValue(int(self._config.get("lens_health.interval_s", 10) or 10))
        window_s = float(self._config.get("lens_health.window_s", 28800) or 28800)
        # Una ventana escrita a mano en segundos sueltos se muestra en la hora más cercana.
        self._window_spin.setValue(max(1, round(window_s / _SECONDS_PER_HOUR)))
        self._threshold_spin.setValue(
            int(self._config.get("lens_health.threshold_pct", 50) or 50))
        self._analysis_width_spin.setValue(
            int(self._config.get("lens_health.analysis_width_px", 960) or 960))
        self._samples_spin.setValue(
            int(self._config.get("lens_health.calibration_samples", 30) or 30))

    def save(self):
        for form in self._forms.values():
            form.save()
        self._config.set("lens_health.enabled", self._lens_enabled_check.isChecked())
        self._config.set("lens_health.interval_s", self._interval_spin.value())
        self._config.set("lens_health.window_s", self._window_spin.value() * _SECONDS_PER_HOUR)
        self._config.set("lens_health.threshold_pct", self._threshold_spin.value())
        self._config.set("lens_health.analysis_width_px", self._analysis_width_spin.value())
        self._config.set("lens_health.calibration_samples", self._samples_spin.value())

    def reload_roi(self, camera_slot: str):
        """Recarga la geometría de la región de una cámara tras guardar el diálogo interactivo."""
        form = self._forms.get(camera_slot)
        if form is not None:
            form.load_roi()

    def refresh_reference(self, camera_slot: str):
        """Recarga la referencia de una cámara después de que la calibración la escribió."""
        form = self._forms.get(camera_slot)
        if form is not None:
            form.finish_calibration()


class _CameraForm(QWidget):
    """
    La sub-pestaña de una cámara: su tarjeta —conexión, adquisición y lo opcional si el config
    lo trae— y la de su óptica —región y referencia de vidrio limpio—.
    """

    calibrate_requested = Signal(str)
    open_roi_requested = Signal(str, str, str)

    def __init__(self, config_manager: ConfigManager, camera_slot: str, parent=None):
        super().__init__(parent)
        self._config = config_manager
        self._camera_slot = camera_slot
        self._prefix = f"cameras.{camera_slot}"
        self._reference_prefix = f"{self._prefix}.lens_health.reference"
        self._roi_prefix = f"{self._prefix}.lens_health.roi"
        # Entre el botón y la referencia nueva: lo que junta las muestras es `main.py`, y
        # avisa al terminar con `finish_calibration()`.
        self._is_calibrating = False
        # Se decide al abrir: lo que dice si la sección existe es el archivo, no la pantalla.
        # Se mira en la cámara y no por la ruta entera, que avisaría de una clave ausente.
        camera = self._config.get(self._prefix, {}) or {}
        self._has_image_adjust = isinstance(camera.get("image_adjust"), dict)
        self._has_calibration = isinstance(camera.get("calibration"), dict)

        card = SettingsCard(camera_slot)
        self._build_connection_section(card)
        self._build_acquisition_section(card)
        if self._has_image_adjust:
            self._build_image_adjust_section(card)
        if self._has_calibration:
            self._build_calibration_section(card)
        optics_card = SettingsCard(tr("lens_card_eyebrow"))
        self._build_region_section(optics_card)
        self._build_reference_section(optics_card)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, _TAB_PAGE_TOP_PX, 0, 0)
        layout.setSpacing(_CARD_SPACING_PX)
        layout.addWidget(card)
        layout.addWidget(optics_card)
        layout.addStretch()

        # La región cuelga de su casilla, y todo lo de la cámara —la casilla de la región
        # incluida, que al volver re-emite su estado— de «Cámara habilitada».
        wire_enable_toggle(self._roi_check, list(self._roi_spins.values())
                           + [self._roi_button])
        wire_enable_toggle(self._enabled_check, self._get_camera_fields())

    @property
    def camera_name(self) -> str:
        """Texto de la sub-pestaña: el `name` del config, o el slot si está vacío."""
        return str(self._config.get(f"{self._prefix}.name", "") or self._camera_slot)

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_connection_section(self, card: SettingsCard):
        section = card.add_section(tr("cam_box_connection"), tr("cam_connection_description"),
                                   columns=3)
        self._connection = section
        self._enabled_check = section.add_row(QCheckBox(tr("cam_enabled")))
        self._name_edit = section.add_field(tr("field_name"), build_line_edit(""))
        self._brand_combo = section.add_field(
            tr("cam_brand"),
            build_value_combo_box(
                {entry["label"]: key for key, entry in CAMERA_CATALOG.items()}, ""))
        self._model_combo = section.add_field(tr("cam_model"), build_combo_box([], ""))
        self._address_edit = section.add_field(
            tr("cam_address"), build_line_edit("", "192.168.0.10"), span=2)
        self._rotation_combo = section.add_field(
            tr("cam_rotation"),
            build_value_combo_box({tr(key): value for key, value in _ROTATIONS.items()},
                                  None))
        self._brand_combo.currentIndexChanged.connect(self._on_brand_changed)

    def _build_acquisition_section(self, card: SettingsCard):
        section = card.add_section(tr("cam_box_acquisition"), tr("cam_acquisition_description"),
                                   columns=2)
        self._acquisition = section
        self._fps_spin = section.add_field(tr("cam_fps_limit"), build_spin_box(1, _MAX_FPS, 15),
                                           hint=tr("unit_fps"))
        self._exposure_spin = section.add_field(
            tr("cam_exposure"), build_spin_box(1, _MAX_EXPOSURE_US, 25000),
            hint=tr("unit_microseconds"))
        self._gain_spin = section.add_field(
            tr("cam_gain"), build_double_spin_box(0.0, _MAX_GAIN, 1.0))
        self._decode_combo = section.add_field(
            tr("cam_decode_backend"),
            build_value_combo_box({tr(key): value for key, value in _DECODE_BACKENDS.items()},
                                  _DEFAULT_DECODE_BACKEND),
            hint=tr("cam_decode_backend_hint"))
        self._decode_size_field = section.add_field(
            tr("cam_decode_size"), build_readonly_line_edit(""), hint=tr("cam_decode_size_hint"))

    def _build_image_adjust_section(self, card: SettingsCard):
        section = card.add_section(tr("cam_box_image_adjust"), tr("cam_image_adjust_note"),
                                   columns=2)
        self._brightness_spin = section.add_field(
            tr("cam_brightness_factor"),
            build_double_spin_box(BRIGHTNESS_FACTOR_MIN, BRIGHTNESS_FACTOR_MAX, 1.0,
                                  decimals=1, step=0.1),
            hint=tr("cam_brightness_hint"))
        self._brightness_spin.setPrefix(_FACTOR_PREFIX)
        self._clahe_spin = section.add_field(
            tr("cam_clahe"),
            build_double_spin_box(0.0, CLAHE_CLIP_MAX, 0.0, decimals=1, step=0.5),
            hint=tr("cam_clahe_hint"))

    def _build_calibration_section(self, card: SettingsCard):
        section = card.add_section(tr("cam_box_calibration"), tr("cam_calibration_note"),
                                   columns=2)
        self._calibration_field = section.add_field(
            tr("lens_reference_state"), build_readonly_line_edit(""), span=2)

    def _build_reference_section(self, card: SettingsCard):
        section = card.add_section(tr("lens_box_reference"), tr("lens_calibrate_note"),
                                   columns=3)
        self._variance_spin = section.add_field(
            tr("lens_reference_variance"),
            build_double_spin_box(0.0, _MAX_VARIANCE, 0.0, decimals=2, step=1.0,
                                  special_value_text=tr("process_uncalibrated")),
            hint=tr("lens_variance_hint"))
        self._state_field = build_readonly_line_edit("")
        self._calibrate_button = QPushButton(tr("lens_calibrate"))
        self._calibrate_button.clicked.connect(self._on_calibrate_clicked)
        section.add_field(tr("lens_reference_state"),
                          build_inline_row(self._state_field, self._calibrate_button), span=2)

    def _build_region_section(self, card: SettingsCard):
        section = card.add_section(tr("lens_box_region"), tr("lens_region_note"), columns=4)
        self._roi_check = QCheckBox(tr("lens_region_enabled"))
        self._roi_button = QPushButton(tr("lens_draw_region"))
        self._roi_button.clicked.connect(
            lambda: self.open_roi_requested.emit(
                self._camera_slot, self._roi_prefix, "lens_roi_title"))
        section.add_row(build_inline_row(self._roi_check, self._roi_button))
        self._roi_spins = {}
        for key, label_key in (("x_px", "lens_region_x"), ("y_px", "lens_region_y"),
                               ("width_px", "lens_region_width"),
                               ("height_px", "lens_region_height")):
            self._roi_spins[key] = section.add_field(
                tr(label_key), build_spin_box(0, _MAX_FRAME_SIDE_PX, 0), hint=tr("unit_pixels"))

    def _get_camera_fields(self) -> list:
        """Lo que apaga «Cámara habilitada». La casilla de la región va última: ver `wire_enable_toggle`."""
        fields = [self._name_edit, self._brand_combo, self._model_combo, self._address_edit,
                  self._rotation_combo, self._fps_spin, self._exposure_spin, self._gain_spin,
                  self._decode_combo]
        if self._has_image_adjust:
            fields += [self._brightness_spin, self._clahe_spin]
        return fields + [self._variance_spin, self._calibrate_button, self._roi_check]

    # ── Contrato de la pestaña ───────────────────────────────────────────────

    def load(self):
        self._name_edit.setText(str(self._config.get(f"{self._prefix}.name", "") or ""))

        brand_key = str(self._config.get(f"{self._prefix}.brand", "") or "")
        self._brand_combo.blockSignals(True)
        set_combo_data(self._brand_combo, brand_key)
        self._brand_combo.blockSignals(False)
        self._reload_models(brand_key, str(self._config.get(f"{self._prefix}.model", "")))

        self._address_edit.setText(str(self._config.get(f"{self._prefix}.address", "") or ""))
        set_combo_data(self._rotation_combo, self._config.get(f"{self._prefix}.rotation", None))

        acquisition = self._config.get(f"{self._prefix}.acquisition", {}) or {}
        self._fps_spin.setValue(int(acquisition.get("fps_limit", 15)))
        self._exposure_spin.setValue(int(acquisition.get("exposure_time_us", 25000)))
        self._gain_spin.setValue(float(acquisition.get("gain", 1.0)))
        decode = self._config.get(f"{self._prefix}.decode", {}) or {}
        set_combo_data(self._decode_combo, decode.get("backend") or _DEFAULT_DECODE_BACKEND)
        self._decode_size_field.setText(_build_decode_size_text(decode))

        if self._has_image_adjust:
            image_adjust = self._config.get(f"{self._prefix}.image_adjust", {}) or {}
            self._brightness_spin.setValue(
                float(image_adjust.get("brightness_factor", 1.0) or 1.0))
            self._clahe_spin.setValue(float(image_adjust.get("clahe_clip", 0.0) or 0.0))
        if self._has_calibration:
            calibration = self._config.get(f"{self._prefix}.calibration", {}) or {}
            self._calibration_field.setText(
                tr("cam_calibration_set") if calibration.get("camera_matrix")
                else tr("cam_calibration_unset"))

        self.load_reference()
        # La casilla se lee acá y no en `load_roi()`: esto es la carga completa desde el
        # archivo; `load_roi()` es el callback del diálogo, que sólo escribe geometría y no
        # tiene que pisar lo que el operador acaba de tildar.
        self._roi_check.setChecked(bool(self._read_roi().get("enabled", False)))
        self.load_roi()

        # Última, y re-emitida aunque no cambie: la casilla de la región se acaba de cargar y
        # pudo prender sus campos en una cámara deshabilitada.
        self._enabled_check.setChecked(bool(self._config.get(f"{self._prefix}.enabled", True)))
        self._enabled_check.toggled.emit(self._enabled_check.isChecked())

    def load_reference(self):
        """
        Recarga la referencia de vidrio limpio; la escribe la calibración, no `save()`.

        Con una calibración en curso el estado sigue diciéndolo: descartar los cambios de la
        pantalla no la frena, y volver a «Sin calibrar» haría creer que se cortó.
        """
        reference = self._config.get(self._reference_prefix, {}) or {}
        self._variance_spin.setValue(float(reference.get("variance", 0.0) or 0.0))
        self._state_field.setText(self._build_calibrating_text() if self._is_calibrating
                                  else self._build_state_text(reference))
        self._state_field.setToolTip(self._state_field.text())

    def finish_calibration(self):
        """La calibración terminó y escribió la referencia: el estado pasa a mostrarla."""
        self._is_calibrating = False
        self.load_reference()

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
        brand_key = self._brand_combo.currentData()
        self._config.set(f"{self._prefix}.enabled", self._enabled_check.isChecked())
        self._config.set(f"{self._prefix}.name", self._name_edit.text())
        self._config.set(f"{self._prefix}.brand", brand_key)
        self._config.set(f"{self._prefix}.model", self._model_combo.currentText())
        # El driver no se elige a mano: sale del catálogo, que es el que sabe qué
        # implementación atiende a cada fabricante. Con una marca que no está en el
        # catálogo se deja el `driver` que ya tenía el config: escribir '' dejaría la
        # cámara sin driver por haber abierto el panel.
        driver = CAMERA_CATALOG.get(brand_key, {}).get("driver", "")
        if driver:
            self._config.set(f"{self._prefix}.driver", driver)
        self._config.set(f"{self._prefix}.address", self._address_edit.text())
        self._config.set(f"{self._prefix}.rotation", self._rotation_combo.currentData())

        self._config.set(f"{self._prefix}.acquisition.fps_limit", self._fps_spin.value())
        # Oculto no se escribe: el driver lo ignora, y lo que hubiera en el archivo se queda.
        if self._has_sensor_settings():
            self._config.set(f"{self._prefix}.acquisition.exposure_time_us",
                             self._exposure_spin.value())
            self._config.set(f"{self._prefix}.acquisition.gain", self._gain_spin.value())
        # Como la exposición: con un driver que no decodifica no se escribe ni se crea.
        if self._decodes():
            self._config.set(f"{self._prefix}.decode.backend", self._decode_combo.currentData())
        if self._has_image_adjust:
            self._config.set(f"{self._prefix}.image_adjust.brightness_factor",
                             self._brightness_spin.value())
            self._config.set(f"{self._prefix}.image_adjust.clahe_clip",
                             self._clahe_spin.value())

        # De la referencia, sólo la varianza: el resto —fecha, muestras, dispersión y
        # condiciones— lo escribe la calibración y describe una medición que pasó. Dejar
        # que la pantalla lo pisara daría una fecha que no corresponde a ese número.
        self._config.set(f"{self._reference_prefix}.variance", self._variance_spin.value())
        self._config.set(f"{self._roi_prefix}.enabled", self._roi_check.isChecked())
        for key, spin in self._roi_spins.items():
            self._config.set(f"{self._roi_prefix}.{key}", spin.value())

    # ── Internos ─────────────────────────────────────────────────────────────

    def _on_calibrate_clicked(self):
        """Pide la calibración y deja dicho en el estado que está corriendo y cuánto tarda."""
        self._is_calibrating = True
        self._state_field.setText(self._build_calibrating_text())
        self._state_field.setToolTip(self._state_field.text())
        self.calibrate_requested.emit(self._camera_slot)

    def _build_calibrating_text(self) -> str:
        """
        Cuántas muestras y cada cuánto, con lo guardado en el config: es lo que lee `main.py`
        al arrancar la calibración, no lo que muestre la pantalla sin guardar.
        """
        samples = int(self._config.get("lens_health.calibration_samples", 30) or 30)
        interval_s = int(self._config.get("lens_health.interval_s", 10) or 10)
        return tr("lens_calibrating").format(
            samples=samples, interval=interval_s,
            minutes=max(1, round(samples * interval_s / _SECONDS_PER_MINUTE)))

    def _on_brand_changed(self):
        self._reload_models(self._brand_combo.currentData(), "")

    def _reload_models(self, brand_key: str, current_model: str):
        """Rearma la lista de modelos del fabricante elegido y muestra los campos que aplican."""
        entry = CAMERA_CATALOG.get(brand_key, {})
        models = list(entry.get("models", []))
        # Un modelo que no está en el catálogo se agrega como opción en vez de perderse:
        # ver el porqué en `build_combo_box`.
        if current_model and current_model not in models:
            models.insert(0, current_model)
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        self._model_combo.addItems(models)
        if current_model:
            self._model_combo.setCurrentText(current_model)
        self._model_combo.blockSignals(False)

        self._connection.set_field_visible(self._address_edit,
                                           entry.get("driver") != _MOCK_DRIVER)
        is_sensor = self._has_sensor_settings()
        self._acquisition.set_field_visible(self._exposure_spin, is_sensor)
        self._acquisition.set_field_visible(self._gain_spin, is_sensor)
        is_decoding = self._decodes()
        self._acquisition.set_field_visible(self._decode_combo, is_decoding)
        self._acquisition.set_field_visible(self._decode_size_field, is_decoding)

    def _has_sensor_settings(self) -> bool:
        """Si el driver de la marca elegida le aplica al sensor la exposición y la ganancia."""
        return bool(CAMERA_CATALOG.get(self._brand_combo.currentData(), {})
                    .get("sensor_settings", False))

    def _decodes(self) -> bool:
        """Si el driver de la marca elegida decodifica video y lee la sección `decode`."""
        return bool(CAMERA_CATALOG.get(self._brand_combo.currentData(), {})
                    .get("decodes", False))

    def _read_roi(self) -> dict:
        """La región de esta cámara, leída de su sección de óptica."""
        section = self._config.get(f"{self._prefix}.lens_health", {}) or {}
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


def _build_decode_size_text(decode: dict) -> str:
    """El tamaño de `decode` como se lee: «640 × 384», o el del video con los dos en 0."""
    width_px = int(decode.get("width_px", 0) or 0)
    height_px = int(decode.get("height_px", 0) or 0)
    if not (width_px and height_px):
        return tr("cam_decode_native")
    return f"{width_px} × {height_px}"
