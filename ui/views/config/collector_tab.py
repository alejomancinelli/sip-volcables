"""
Pestaña de dataset: la sección `image_collector:`.

Dos tarjetas con la forma de las de ajustes (`ui/widgets/settings_card.py`): «Recolección»
—cuándo se guarda, qué lleva cada guardado y de qué cámaras y pipelines— y «Almacenamiento» —los topes de
espacio y los duplicados—.

`enabled` se prende y se apaga en caliente, pero el modo no: cambiarlo exige reiniciar
la aplicación, y la aclaración del campo lo dice donde se lo elige.

«A pedido» se ofrece sólo si algún pipeline tiene ciclo de medición: en ese modo guarda
quien orquesta el ciclo, una vez por medición, y sin ciclo no guarda nunca. La regla de
qué es tener ciclo es `has_measurement_cycle()` del scheduler, y no se re-deduce acá. Si
el config ya dice `on_demand` sin ciclo, la opción se muestra igual con un aviso debajo:
ocultarla y guardar cambiaría el modo en silencio. Se evalúa en `load()`, así que un
`frames_per_cycle` cambiado en la pestaña de inferencia lo mueve recién al guardar, que
es cuando el panel recarga.

Con la recolección apagada no se edita nada más: los campos se atenúan como en las otras
pestañas, porque un valor que se puede escribir mientras el subsistema está apagado
promete algo que no va a pasar.

Los dos intervalos tienen una condición más: sólo hacen algo en modo `interval`, así que
están habilitados únicamente cuando la recolección está prendida **y** el modo es ese. El
mínimo no puede pasar al máximo: cada guardado cae al azar entre los dos.

Las cámaras que se guardan son una casilla por slot de `cameras:`, con su nombre, y los
pipelines, una por slot de `inference.pipelines`. Las dos listas siguen la misma regla: todas
marcadas guarda `[]`, que también incluye a los que se agreguen después; si no, la lista de
los marcados. Un slot de la lista que ya no está en el config vuelve al archivo tal cual. El
último marcado no se puede desmarcar: la lista vacía significa todos, y no guardar ninguno es
apagar la recolección. El filtro de pipelines decide de qué resultados se guarda la imagen
—la región que vio el pipeline, si tiene ROI propio, o el frame entero— y lo aplica el
recolector (`should_collect()`).
"""

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QCheckBox, QLabel, QVBoxLayout, QWidget

from system.camera.capture_scheduler import has_measurement_cycle
from system.config_manager import ConfigManager

from ui.strings import tr
from ui.views.config.abstract_tab import AbstractConfigTab
from ui.widgets.form import (
    build_check_grid, build_spin_box, build_value_combo_box, set_combo_data,
    set_field_enabled, wire_enable_toggle,
)
from ui.widgets.settings_card import SettingsCard

# Clave de idioma -> valor de `image_collector.mode`. El valor viaja en el item del
# combo y no en el texto, que cambia con el idioma.
_MODES = {
    "col_mode_on_demand": "on_demand",
    "col_mode_interval":  "interval",
}
_MODE_ON_DEMAND = "on_demand"
_MODE_INTERVAL = "interval"
_CAMERAS = "cameras"             # las dos listas de slots de `image_collector:`
_PIPELINES = "pipelines"

_MAX_INTERVAL_S = 86400
_MAX_IMAGES = 1000000
_MAX_FREE_SPACE_GB = 1000
_MAX_HISTORY_LEN = 50
_MAX_HAMMING = 64

_CARD_SPACING_PX = 24            # entre tarjetas, como la web
_CHECK_COLUMNS = 4               # casillas por fila: cámaras y archivos


class CollectorTab(AbstractConfigTab):
    """Sección `image_collector:` del config. Ver el contrato en `abstract_tab.py`."""

    TITLE_KEY = "tab_collector"

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        layout = QVBoxLayout(self)
        # Sin margen propio: las tarjetas arrancan en el borde del encabezado de la página.
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_CARD_SPACING_PX)
        layout.addWidget(self._build_collection_card())
        layout.addWidget(self._build_storage_card())
        layout.addStretch()
        self._wire_enabled()
        self.load()

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_collection_card(self) -> QWidget:
        card = SettingsCard(tr("col_box_general"))
        self._has_cycle = True

        timing = card.add_section(tr("col_collection_section"),
                                  tr("col_collection_description"), columns=3)
        self._enabled = timing.add_row(QCheckBox(tr("col_enabled")))
        self._mode_combo = timing.add_field(
            tr("col_mode"),
            build_value_combo_box({tr(key): value for key, value in _MODES.items()},
                                  _MODE_ON_DEMAND),
            hint=tr("col_mode_hint"))
        self._interval_min = timing.add_field(
            tr("col_interval_min"), build_spin_box(1, _MAX_INTERVAL_S, 60),
            hint=tr("unit_seconds"))
        self._interval_max = timing.add_field(
            tr("col_interval_max"), build_spin_box(1, _MAX_INTERVAL_S, 600),
            hint=tr("col_interval_hint"))
        # Cada guardado cae al azar entre los dos: el mínimo no puede pasar al máximo.
        self._interval_max.valueChanged.connect(self._interval_min.setMaximum)
        self._no_cycle_warning = QLabel(tr("col_mode_no_cycle"))
        self._no_cycle_warning.setObjectName("warningBanner")
        self._no_cycle_warning.setWordWrap(True)
        timing.add_row(self._no_cycle_warning)

        content = card.add_section(tr("col_content_section"), tr("col_content_description"),
                                   columns=1)
        self._save_original = QCheckBox(tr("col_save_original"))
        self._save_annotated = QCheckBox(tr("col_save_annotated"))
        self._save_json = QCheckBox(tr("col_save_json"))
        content.add_row(build_check_grid(
            [self._save_original, self._save_annotated, self._save_json], _CHECK_COLUMNS))

        # Slots de cada lista del config que no tienen casilla: vuelven al archivo como estaban.
        self._unlisted: dict[str, list[str]] = {_CAMERAS: [], _PIPELINES: []}
        self._camera_checks = self._add_slot_section(
            card, self._config.get("cameras", {}), tr("col_box_cameras"),
            tr("col_cameras_note"), tr("col_cameras_hint"))
        self._pipeline_checks: dict[str, QCheckBox] = {}
        pipelines = self._config.get("inference.pipelines", {}) or {}
        if pipelines:
            self._pipeline_checks = self._add_slot_section(
                card, pipelines, tr("col_box_pipelines"), tr("col_pipelines_note"),
                tr("col_pipelines_hint"))
        return card

    def _add_slot_section(self, card: SettingsCard, sections: object, title: str,
                          description: str, hint_text: str) -> dict[str, QCheckBox]:
        """Una sección con una casilla por slot de `sections`, rotulada con su `name`."""
        section = card.add_section(title, description, columns=1)
        checks: dict[str, QCheckBox] = {}
        for slot, slot_section in (sections or {}).items():
            name = slot_section.get("name") if isinstance(slot_section, dict) else None
            check = QCheckBox(str(name or "").strip() or str(slot))
            check.setToolTip(str(slot))
            check.setChecked(True)
            check.toggled.connect(
                lambda checked, checks=checks, check=check: _keep_one_checked(checks, check))
            checks[str(slot)] = check
        section.add_row(build_check_grid(list(checks.values()), _CHECK_COLUMNS))
        hint = QLabel(hint_text)
        hint.setObjectName("settingsFieldHint")
        section.add_row(hint)
        return checks

    def _build_storage_card(self) -> QWidget:
        card = SettingsCard(tr("col_card_storage"))
        space = card.add_section(tr("col_space_section"), tr("col_space_description"))
        self._max_images = space.add_field(
            tr("col_max_images"), build_spin_box(1, _MAX_IMAGES, 10000),
            hint=tr("col_max_images_hint"))
        self._min_free_space = space.add_field(
            tr("col_min_free_space"), build_spin_box(0, _MAX_FREE_SPACE_GB, 5),
            hint=tr("col_min_free_space_hint"))

        dedup = card.add_section(tr("col_box_dedup"), tr("col_dedup_description"))
        self._dedup_enabled = dedup.add_row(QCheckBox(tr("col_dedup_enabled")))
        self._dedup_enabled.setChecked(True)
        self._history_len = dedup.add_field(
            tr("col_history_len"), build_spin_box(1, _MAX_HISTORY_LEN, 5),
            hint=tr("col_history_len_hint"))
        self._hamming = dedup.add_field(
            tr("col_hamming"), build_spin_box(0, _MAX_HAMMING, 5), hint=tr("col_hamming_hint"))
        wire_enable_toggle(self._dedup_enabled, [self._history_len, self._hamming])
        return card

    def _wire_enabled(self):
        """
        Ata todo el bloque al checkbox de recolección activa.

        Se llama después de armar las dos tarjetas porque la cadena llega hasta los campos
        de dedup: `wire_enable_toggle` re-emite el `toggled` de un checkbox dependiente,
        así que apagar la recolección apaga dedup y, en cascada, sus dos campos.
        """
        wire_enable_toggle(self._enabled, [
            self._mode_combo, self._interval_min, self._interval_max,
            self._max_images, self._min_free_space,
            self._save_original, self._save_annotated, self._save_json,
            *self._camera_checks.values(), *self._pipeline_checks.values(),
            self._dedup_enabled,
        ])
        # Los intervalos dependen además del modo, y eso se resuelve después del toggle,
        # que es el que los deja habilitados sin mirar el modo.
        self._enabled.toggled.connect(self._refresh_interval_fields)
        self._mode_combo.currentIndexChanged.connect(self._refresh_interval_fields)
        self._mode_combo.currentIndexChanged.connect(self._refresh_no_cycle_warning)

    # ── Contrato de la pestaña ───────────────────────────────────────────────

    def load(self):
        self._enabled.setChecked(bool(self._config.get("image_collector.enabled", False)))
        self._load_mode()
        # El máximo primero: es el techo del mínimo.
        self._interval_max.setValue(int(self._config.get("image_collector.interval_max_s", 600)))
        self._interval_min.setValue(int(self._config.get("image_collector.interval_min_s", 60)))
        self._max_images.setValue(int(self._config.get("image_collector.max_images", 10000)))
        self._min_free_space.setValue(
            int(self._config.get("image_collector.min_free_space_gb", 5))
        )
        self._save_original.setChecked(
            bool(self._config.get("image_collector.save_original", True))
        )
        self._save_annotated.setChecked(
            bool(self._config.get("image_collector.save_annotated", False))
        )
        self._save_json.setChecked(
            bool(self._config.get("image_collector.save_inference_json", True))
        )
        self._load_slots(_CAMERAS, self._camera_checks)
        self._load_slots(_PIPELINES, self._pipeline_checks)
        self._dedup_enabled.setChecked(
            bool(self._config.get("image_collector.dedup.enabled", True))
        )
        self._history_len.setValue(int(self._config.get("image_collector.dedup.history_len", 5)))
        self._hamming.setValue(
            int(self._config.get("image_collector.dedup.hamming_threshold", 5))
        )
        # `toggled` no se emite si el valor no cambió, así que la cascada se fuerza acá:
        # `load()` corre también al descartar y el estado tiene que quedar consistente.
        self._enabled.toggled.emit(self._enabled.isChecked())
        self._refresh_interval_fields()

    def save(self):
        self._config.set("image_collector.enabled", self._enabled.isChecked())
        self._config.set("image_collector.mode", self._mode_combo.currentData())
        self._config.set("image_collector.interval_min_s", self._interval_min.value())
        self._config.set("image_collector.interval_max_s", self._interval_max.value())
        self._config.set("image_collector.max_images", self._max_images.value())
        self._config.set("image_collector.min_free_space_gb", self._min_free_space.value())
        self._config.set("image_collector.save_original", self._save_original.isChecked())
        self._config.set("image_collector.save_annotated", self._save_annotated.isChecked())
        self._config.set("image_collector.save_inference_json", self._save_json.isChecked())
        self._config.set("image_collector.cameras",
                         self._get_selected(_CAMERAS, self._camera_checks))
        # Sin pipelines en el config no hay casillas: la lista no se mostró y no se toca.
        if self._pipeline_checks:
            self._config.set("image_collector.pipelines",
                             self._get_selected(_PIPELINES, self._pipeline_checks))
        self._config.set("image_collector.dedup.enabled", self._dedup_enabled.isChecked())
        self._config.set("image_collector.dedup.history_len", self._history_len.value())
        self._config.set("image_collector.dedup.hamming_threshold", self._hamming.value())

    # ── Internos ─────────────────────────────────────────────────────────────

    def _refresh_interval_fields(self):
        """Los intervalos: sólo con la recolección prendida y el modo por intervalo."""
        is_usable = (self._enabled.isChecked()
                     and self._mode_combo.currentData() == _MODE_INTERVAL)
        for spin in (self._interval_min, self._interval_max):
            set_field_enabled(spin, is_usable)

    def _load_mode(self):
        """Arma las opciones del modo según haya ciclo, y elige la del config."""
        mode = self._config.get("image_collector.mode", _MODE_ON_DEMAND)
        self._has_cycle = has_measurement_cycle(self._config)
        with QSignalBlocker(self._mode_combo):
            self._mode_combo.clear()
            for key, value in _MODES.items():
                if value == _MODE_ON_DEMAND and not self._has_cycle and mode != value:
                    continue
                self._mode_combo.addItem(tr(key), value)
            set_combo_data(self._mode_combo, mode)
        self._refresh_no_cycle_warning()

    def _refresh_no_cycle_warning(self):
        """El aviso, sólo con «A pedido» elegido y ningún pipeline con ciclo."""
        is_useless = not self._has_cycle and self._mode_combo.currentData() == _MODE_ON_DEMAND
        self._no_cycle_warning.setVisible(is_useless)

    def _load_slots(self, key: str, checks: dict[str, QCheckBox]):
        """Marca los slots de `image_collector.<key>`; vacía —o que no es lista— los marca todos."""
        slots = self._config.get(f"image_collector.{key}", []) or []
        selected = [str(slot) for slot in slots] if isinstance(slots, (list, tuple)) else []
        self._unlisted[key] = [slot for slot in selected if slot not in checks]
        for slot, check in checks.items():
            with QSignalBlocker(check):
                check.setChecked(not selected or slot in selected)

    def _get_selected(self, key: str, checks: dict[str, QCheckBox]) -> list[str]:
        """Lo que va a `image_collector.<key>`: [] con todos marcados, si no la lista."""
        checked = [slot for slot, check in checks.items() if check.isChecked()]
        if len(checked) == len(checks):
            return []
        return checked + self._unlisted[key]


def _keep_one_checked(checks: dict[str, QCheckBox], check: QCheckBox):
    """Vuelve a marcar el último slot desmarcado: ninguno se guardaría como todos."""
    if any(other.isChecked() for other in checks.values()):
        return
    with QSignalBlocker(check):
        check.setChecked(True)
