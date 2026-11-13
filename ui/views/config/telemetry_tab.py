"""
Pestaña de telemetría: la sección `telemetry:` — InfluxDB y MQTT.

Una tarjeta con la forma de las de ajustes (`ui/widgets/settings_card.py`) y una sección
por destino, cada una con su checkbox de habilitado, que apaga y atenúa sus campos. Se
aplican al reiniciar.

Las credenciales no están en el formulario porque no están en el config: el token de
InfluxDB sale de `INFLUXDB_TOKEN` y la password de MQTT de `MQTT_PASSWORD`. Una aclaración
al pie de cada sección lo dice, que es donde alguien va a buscar el campo que falta.

El muestreo de la telemetría no está en esta pantalla: es un punto por segundo, fijo en
`main.py`, porque no hay instalación que quiera otro. La estructura de las series está en
`docs/influxdb.md`, y cómo sale esa misma estructura por MQTT en `docs/mqtt.md`.
"""

from PySide6.QtWidgets import QCheckBox, QLabel, QVBoxLayout, QWidget

from system.config_manager import ConfigManager

from ui.strings import tr
from ui.views.config.abstract_tab import AbstractConfigTab
from ui.widgets.form import build_line_edit, build_spin_box, wire_enable_toggle
from ui.widgets.settings_card import SettingsCard, SettingsSection

_MAX_PORT = 65535

_CARD_SPACING_PX = 24        # entre tarjetas, como la web
_COLUMNS = 3


class TelemetryTab(AbstractConfigTab):
    """Sección `telemetry:` del config. Ver el contrato en `abstract_tab.py`."""

    TITLE_KEY = "tab_telemetry"

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        layout = QVBoxLayout(self)
        # Sin margen propio: la tarjeta arranca en el borde del encabezado de la página.
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_CARD_SPACING_PX)
        layout.addWidget(self._build_destinations_card())
        layout.addStretch()
        self.load()

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_destinations_card(self) -> QWidget:
        card = SettingsCard(tr("tel_card_destinations"))

        influx = card.add_section(tr("tel_box_influxdb"), tr("tel_influx_description"),
                                  _COLUMNS)
        self._influx_enabled = influx.add_row(QCheckBox(tr("tel_influx_enabled")))
        self._influx_url = influx.add_field(
            tr("field_url"), build_line_edit("", "http://localhost:8086"))
        self._influx_org = influx.add_field(tr("tel_org"), build_line_edit(""))
        self._influx_bucket = influx.add_field(tr("tel_bucket"), build_line_edit(""))
        _add_note(influx, tr("tel_influx_token_note"))
        wire_enable_toggle(self._influx_enabled, [
            self._influx_url, self._influx_org, self._influx_bucket,
        ])

        mqtt = card.add_section(tr("tel_box_mqtt"), tr("tel_mqtt_description"), _COLUMNS)
        self._mqtt_enabled = mqtt.add_row(QCheckBox(tr("tel_mqtt_enabled")))
        self._mqtt_host = mqtt.add_field(tr("field_host"), build_line_edit("", "192.168.0.20"))
        # 0 no es un puerto: es "el que corresponda según tls", y lo resuelve el backend.
        self._mqtt_port = mqtt.add_field(
            tr("field_port"), build_spin_box(0, _MAX_PORT, 0), hint=tr("tel_mqtt_port_hint"))
        # Al lado del puerto, porque con 0 lo elige: 1883 o 8883.
        self._mqtt_tls = mqtt.add_field(tr("tel_security"), QCheckBox(tr("tel_tls")))
        self._mqtt_user = mqtt.add_field(tr("field_user"), build_line_edit(""))
        self._mqtt_topic_base = mqtt.add_field(
            tr("tel_topic_base"), build_line_edit(""), hint=tr("tel_topic_base_hint"), span=2)
        _add_note(mqtt, tr("tel_mqtt_password_note"))
        wire_enable_toggle(self._mqtt_enabled, [
            self._mqtt_host, self._mqtt_port, self._mqtt_tls,
            self._mqtt_user, self._mqtt_topic_base,
        ])
        return card

    # ── Contrato de la pestaña ───────────────────────────────────────────────

    def load(self):
        self._influx_enabled.setChecked(
            bool(self._config.get("telemetry.influxdb.enabled", False))
        )
        self._influx_url.setText(str(self._config.get("telemetry.influxdb.url", "") or ""))
        self._influx_org.setText(str(self._config.get("telemetry.influxdb.org", "") or ""))
        self._influx_bucket.setText(str(self._config.get("telemetry.influxdb.bucket", "") or ""))

        self._mqtt_enabled.setChecked(bool(self._config.get("telemetry.mqtt.enabled", False)))
        self._mqtt_host.setText(str(self._config.get("telemetry.mqtt.host", "") or ""))
        self._mqtt_port.setValue(int(self._config.get("telemetry.mqtt.port", 0)))
        self._mqtt_tls.setChecked(bool(self._config.get("telemetry.mqtt.tls", False)))
        self._mqtt_user.setText(str(self._config.get("telemetry.mqtt.user", "") or ""))
        self._mqtt_topic_base.setText(str(self._config.get("telemetry.mqtt.topic_base", "") or ""))

    def save(self):
        self._config.set("telemetry.influxdb.enabled", self._influx_enabled.isChecked())
        self._config.set("telemetry.influxdb.url", self._influx_url.text())
        self._config.set("telemetry.influxdb.org", self._influx_org.text())
        self._config.set("telemetry.influxdb.bucket", self._influx_bucket.text())

        self._config.set("telemetry.mqtt.enabled", self._mqtt_enabled.isChecked())
        self._config.set("telemetry.mqtt.host", self._mqtt_host.text())
        self._config.set("telemetry.mqtt.port", self._mqtt_port.value())
        self._config.set("telemetry.mqtt.tls", self._mqtt_tls.isChecked())
        self._config.set("telemetry.mqtt.user", self._mqtt_user.text())
        self._config.set("telemetry.mqtt.topic_base", self._mqtt_topic_base.text())


def _add_note(section: SettingsSection, text: str):
    """Una aclaración a todo el ancho de los campos, al pie de la sección."""
    note = QLabel(text)
    note.setObjectName("settingsFieldHint")
    note.setWordWrap(True)
    section.add_row(note)
