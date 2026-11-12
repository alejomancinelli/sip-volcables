"""
Pestaña de sistema: las secciones `project:`, `system:`, `ui:` y `system_monitor:`.

Una tarjeta con cuatro secciones, como las demás pestañas de configuración: la identidad del
equipo, el registro y las carpetas, el idioma, y qué mide el monitor de hardware.

`project:`, el nombre de la aplicación y su descripción se muestran de sólo lectura:
identifican la instalación y se escriben una vez, al armar el fork. Cambiarlos desde la UI de planta
rompería la correspondencia entre el equipo y sus datos históricos —y el nombre, además,
es el que aparece en el título de la ventana y en la barra lateral, que se arman al iniciar—.

`system.device_id` también es de sólo lectura, por la misma razón: es una identidad que se
fija en la puesta en marcha y no una preferencia de planta. Es el `client_id` de MQTT —dos
equipos con el mismo id se echan del broker— y el tag `device` de cada punto de InfluxDB,
así que cambiarlo parte la serie en dos. La licencia no lo compara: la ata la huella.

**El tema no está acá.** Es lo único que la interfaz aplica sin reiniciar, así que va en la
barra superior, al alcance desde cualquier página, y lo guarda la ventana. El idioma sí: queda
fijo por corrida y toma efecto al reiniciar, que es lo que dice la aclaración del campo.
"""

from PySide6.QtWidgets import QVBoxLayout, QWidget

from system.config_manager import ConfigManager

from ui.strings import LANGUAGES, tr
from ui.views.config.abstract_tab import AbstractConfigTab
from ui.widgets.form import (
    build_combo_box, build_line_edit, build_readonly_line_edit, build_value_combo_box,
    set_combo_data, set_combo_value,
)
from ui.widgets.interface_picker import InterfaceChecklist
from ui.widgets.settings_card import SettingsCard

_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
_DEFAULT_LANGUAGE = "es"
_COLUMNS = 2


class SystemTab(AbstractConfigTab):
    """Secciones `project:`, `system:`, `ui:` y `system_monitor:` del config."""

    TITLE_KEY = "tab_system"

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        layout = QVBoxLayout(self)
        # Sin margen propio: la tarjeta arranca en el borde del encabezado de la página.
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._build_card())
        layout.addStretch()
        self.load()

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_card(self) -> QWidget:
        card = SettingsCard(tr("sys_card"))

        identity = card.add_section(tr("sys_box_project"), tr("sys_project_description"),
                                    _COLUMNS)
        self._client = identity.add_field(tr("sys_client"), build_readonly_line_edit(""))
        self._project_id = identity.add_field(tr("sys_project_id"),
                                              build_readonly_line_edit(""))
        self._app_name = identity.add_field(tr("sys_app_name"), build_readonly_line_edit(""))
        self._device_id = identity.add_field(tr("sys_device_id"), build_readonly_line_edit(""),
                                             hint=tr("sys_device_id_note"))
        # A todo el ancho: es una frase, la que va debajo del nombre en la página de inicio.
        self._app_description = identity.add_field(
            tr("sys_app_description"), build_readonly_line_edit(""),
            hint=tr("sys_app_description_hint"), span=2)

        general = card.add_section(tr("sys_box_general"), tr("sys_general_description"),
                                   _COLUMNS)
        self._log_level = general.add_field(
            tr("sys_log_level"), build_combo_box(list(_LOG_LEVELS), "INFO"),
            hint=tr("sys_log_level_hint"))
        # Las rutas son largas: cada una a todo el ancho, debajo del nivel.
        self._logs_path = general.add_field(tr("sys_logs_path"), build_line_edit(""), span=2)
        self._dataset_path = general.add_field(tr("sys_dataset_path"), build_line_edit(""),
                                               span=2)

        interface = card.add_section(tr("sys_box_interface"), tr("sys_interface_description"),
                                     _COLUMNS)
        self._language = interface.add_field(
            tr("sys_language"),
            build_value_combo_box({tr(f"language_{code}"): code for code in LANGUAGES},
                                  _DEFAULT_LANGUAGE),
            hint=tr("sys_interface_note"))

        monitor = card.add_section(tr("sys_box_monitor"), tr("sys_monitor_description"),
                                   _COLUMNS)
        self._disk_path = monitor.add_field(tr("sys_disk_path"), build_line_edit("", "."),
                                            hint=tr("sys_disk_path_hint"))
        # A todo el ancho: cada casilla lleva la interfaz con su dirección.
        self._net_interfaces = monitor.add_field(tr("sys_net_interfaces"), InterfaceChecklist(),
                                                 hint=tr("sys_net_interfaces_hint"), span=2)
        return card

    # ── Contrato de la pestaña ───────────────────────────────────────────────

    def load(self):
        self._client.setText(str(self._config.get("project.client", "") or ""))
        self._project_id.setText(str(self._config.get("project.project_id", "") or ""))

        self._app_name.setText(str(self._config.get("system.app_name", "") or ""))
        self._app_description.setText(
            str(self._config.get("system.app_description", "") or ""))
        self._device_id.setText(str(self._config.get("system.device_id", "") or ""))
        set_combo_value(self._log_level, self._config.get("system.log_level", "INFO"))
        self._logs_path.setText(str(self._config.get("system.paths.logs", "") or ""))
        self._dataset_path.setText(str(self._config.get("system.paths.dataset", "") or ""))

        set_combo_data(self._language, self._config.get("ui.language", _DEFAULT_LANGUAGE))

        self._disk_path.setText(str(self._config.get("system_monitor.disk_path", ".") or "."))
        self._net_interfaces.set_selected(self._config.get("system_monitor.net_interfaces", []))

    def save(self):
        # `app_name`, `app_description` y `device_id` no se guardan: son de sólo lectura. El `device_id` es
        # la identidad del equipo en MQTT y en InfluxDB, y se cambia en el config.yaml.
        # `ui.dark_mode` tampoco: lo guarda la barra superior al cambiar el tema.
        self._config.set("system.log_level", self._log_level.currentText())
        self._config.set("system.paths.logs", self._logs_path.text())
        self._config.set("system.paths.dataset", self._dataset_path.text())

        self._config.set("ui.language", self._language.currentData())

        self._config.set("system_monitor.disk_path", self._disk_path.text())
        self._config.set("system_monitor.net_interfaces", self._net_interfaces.get_selected())
