"""
Pestaña de Modbus: la sección `modbus:` — transporte y datastore, no el mapa.

Una tarjeta con la forma de las de ajustes (`ui/widgets/settings_card.py`) y tres
secciones: cómo identifica el PLC al equipo, el servidor TCP y la línea RTU. Cada
transporte tiene su checkbox de habilitado, que apaga y atenúa sus propios campos: son
independientes, como en el servidor. Por dónde escucha el TCP se elige entre las
interfaces que tiene el equipo (`ui/widgets/interface_picker.py`).

El mapa de registros no se edita desde acá y la descripción de la sección lo dice: vive en
`system/modbus/register_map.yaml`, que es fuente única, y la tabla del integrador se
genera con `python -m system.modbus.export_map`. Un mapa editable desde la UI serían
dos verdades sobre la misma dirección.

`register_count` se muestra pero no se edita: cuántos registros expone el datastore lo
decide el tamaño del mapa, no una preferencia de planta. Se muestra lo que expone el
servidor —`register_count` o la última fila del mapa, la que sea mayor— y en su aclaración
hasta qué registro llega el mapa cargado.

Debajo de la línea RTU va la trama que resulta —«115200 bps · 8N1 · esclavo 1»—, que es
como la escribe el manual del PLC y lo que quien integra compara de un vistazo.
"""

from PySide6.QtWidgets import QCheckBox, QLabel, QVBoxLayout, QWidget

from system.config_manager import ConfigManager
from system.modbus.registers import SCHEMA
from system.modbus.schema import to_plc_address

from ui.strings import tr
from ui.views.config.abstract_tab import AbstractConfigTab
from ui.widgets.form import (
    build_combo_box, build_line_edit, build_readonly_line_edit, build_spin_box,
    build_value_combo_box, set_combo_data, set_combo_value, wire_enable_toggle,
)
from ui.widgets.interface_picker import ALL_ADDRESSES, build_address_combo
from ui.widgets.settings_card import SettingsCard

# Clave de idioma -> valor de `modbus.rtu.parity`. El valor viaja en el item del combo y
# no en el texto, que cambia con el idioma.
_PARITIES = {
    "mb_parity_none": "N",
    "mb_parity_even": "E",
    "mb_parity_odd":  "O",
}
_STOP_BITS = ("1", "2")
_DATA_BITS = 8               # pymodbus no deja elegirlos: la trama los muestra fijos

_MAX_PORT = 65535
_MAX_SLAVE_ID = 247
_MIN_BAUDRATE = 1200
_MAX_BAUDRATE = 921600

_CARD_SPACING_PX = 24        # entre tarjetas, como la web


class ModbusTab(AbstractConfigTab):
    """Sección `modbus:` del config. Ver el contrato en `abstract_tab.py`."""

    TITLE_KEY = "tab_modbus_config"

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        layout = QVBoxLayout(self)
        # Sin margen propio: la tarjeta arranca en el borde del encabezado de la página.
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_CARD_SPACING_PX)
        layout.addWidget(self._build_server_card())
        layout.addStretch()
        self.load()

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_server_card(self) -> QWidget:
        card = SettingsCard(tr("mb_card_server"))

        identity = card.add_section(tr("mb_identity_section"), tr("mb_identity_description"))
        self._slave_id = identity.add_field(
            tr("mb_slave_id"), build_spin_box(1, _MAX_SLAVE_ID, 1), hint=tr("mb_slave_id_hint"))
        # La aclaración sale del mapa, que se carga al importar y no cambia con el config.
        max_addr = SCHEMA.max_addr()
        self._register_count = identity.add_field(
            tr("mb_register_count"), build_readonly_line_edit(""),
            hint=tr("mb_map_extent").format(max_addr=max_addr,
                                            plc_addr=to_plc_address(max_addr)))

        tcp = card.add_section(tr("mb_box_tcp"), tr("mb_tcp_description"))
        self._tcp_enabled = tcp.add_row(QCheckBox(tr("mb_tcp_enabled")))
        self._tcp_host = tcp.add_field(
            tr("net_listen_on"), build_address_combo(), hint=tr("net_listen_hint"))
        self._tcp_port = tcp.add_field(
            tr("field_port"), build_spin_box(1, _MAX_PORT, 502), hint=tr("mb_port_hint"))
        wire_enable_toggle(self._tcp_enabled, [self._tcp_host, self._tcp_port])

        rtu = card.add_section(tr("mb_box_rtu"), tr("mb_rtu_description"))
        self._rtu_enabled = rtu.add_row(QCheckBox(tr("mb_rtu_enabled")))
        self._rtu_port = rtu.add_field(
            tr("mb_serial_port"), build_line_edit("", "COM1 / /dev/ttyS0"))
        self._rtu_baudrate = rtu.add_field(
            tr("mb_baudrate"), build_spin_box(_MIN_BAUDRATE, _MAX_BAUDRATE, 115200),
            hint=tr("mb_baudrate_hint"))
        self._rtu_parity = rtu.add_field(
            tr("mb_parity"),
            build_value_combo_box({tr(key): value for key, value in _PARITIES.items()}, "N"))
        self._rtu_stop_bits = rtu.add_field(
            tr("mb_stop_bits"), build_combo_box(list(_STOP_BITS), "1"))
        self._rtu_frame = QLabel()
        self._rtu_frame.setObjectName("settingsSummary")
        rtu.add_row(self._rtu_frame)
        wire_enable_toggle(self._rtu_enabled, [
            self._rtu_port, self._rtu_baudrate, self._rtu_parity, self._rtu_stop_bits,
            self._rtu_frame,
        ])

        self._slave_id.valueChanged.connect(self._refresh_rtu_frame)
        self._rtu_baudrate.valueChanged.connect(self._refresh_rtu_frame)
        self._rtu_parity.currentIndexChanged.connect(self._refresh_rtu_frame)
        self._rtu_stop_bits.currentIndexChanged.connect(self._refresh_rtu_frame)
        return card

    def _refresh_register_count(self):
        """Muestra los registros que expone el servidor."""
        # Como el servidor: el datastore llega hasta la última fila del mapa.
        count = max(int(self._config.get("modbus.register_count", 100)), SCHEMA.max_addr())
        # Con las dos numeraciones: adentro del repo la dirección es base-1, pero quien
        # mira esta pantalla suele tener el PLC al lado, donde el registro 1 es el 40001.
        self._register_count.setText(
            f"{count}   ({to_plc_address(1)}–{to_plc_address(count)})")

    def _refresh_rtu_frame(self):
        """La trama RTU como la escribe un manual: velocidad, 8N1 y la dirección."""
        self._rtu_frame.setText(tr("mb_frame").format(
            baudrate=self._rtu_baudrate.value(), data_bits=_DATA_BITS,
            parity=self._rtu_parity.currentData(), stop_bits=self._rtu_stop_bits.currentText(),
            slave_id=self._slave_id.value()))

    # ── Contrato de la pestaña ───────────────────────────────────────────────

    def load(self):
        self._slave_id.setValue(int(self._config.get("modbus.slave_id", 1)))
        self._refresh_register_count()

        self._tcp_enabled.setChecked(bool(self._config.get("modbus.tcp.enabled", True)))
        set_combo_data(self._tcp_host, str(self._config.get("modbus.tcp.host", ALL_ADDRESSES)))
        self._tcp_port.setValue(int(self._config.get("modbus.tcp.port", 502)))

        self._rtu_enabled.setChecked(bool(self._config.get("modbus.rtu.enabled", False)))
        self._rtu_port.setText(str(self._config.get("modbus.rtu.port", "") or ""))
        self._rtu_baudrate.setValue(int(self._config.get("modbus.rtu.baudrate", 115200)))
        set_combo_data(self._rtu_parity, self._config.get("modbus.rtu.parity", "N"))
        set_combo_value(self._rtu_stop_bits, self._config.get("modbus.rtu.stop_bits", 1))
        self._refresh_rtu_frame()

    def save(self):
        self._config.set("modbus.slave_id", self._slave_id.value())
        # `register_count` no se guarda: es de sólo lectura y su dueño es el mapa.

        self._config.set("modbus.tcp.enabled", self._tcp_enabled.isChecked())
        self._config.set("modbus.tcp.host", self._tcp_host.currentData())
        self._config.set("modbus.tcp.port", self._tcp_port.value())

        self._config.set("modbus.rtu.enabled", self._rtu_enabled.isChecked())
        self._config.set("modbus.rtu.port", self._rtu_port.text())
        self._config.set("modbus.rtu.baudrate", self._rtu_baudrate.value())
        self._config.set("modbus.rtu.parity", self._rtu_parity.currentData())
        self._config.set("modbus.rtu.stop_bits", int(self._rtu_stop_bits.currentText()))
