"""
Tests de la pestaña de Modbus: que la sección `modbus:` vaya y vuelva del config sin
deformarse, que la paridad se guarde como letra aunque se muestre traducida, y que la trama
RTU siga a los campos.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                              # noqa: E402

from ui.views.config.modbus_tab import ModbusTab                        # noqa: E402

_VALUES = {
    "modbus.slave_id": 7,
    "modbus.tcp.enabled": False,
    "modbus.tcp.host": "192.168.0.5",
    "modbus.tcp.port": 5020,
    "modbus.rtu.enabled": True,
    "modbus.rtu.port": "/dev/ttyTHS1",
    "modbus.rtu.baudrate": 19200,
    "modbus.rtu.parity": "E",
    "modbus.rtu.stop_bits": 2,
}


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


class _MockConfig:
    """ConfigManager mínimo con `get` punteado y `set`, sin archivo ni Lock."""

    def __init__(self, **overrides):
        self._values = dict(_VALUES)
        self._values.update(overrides)
        self.written: dict = {}

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)

    def set(self, key: str, value: object):
        self.written[key] = value


def _tab(**overrides) -> tuple[ModbusTab, _MockConfig]:
    config = _MockConfig(**overrides)
    return ModbusTab(config), config


class TestRoundTrip:
    def test_every_value_goes_back_unchanged(self):
        tab, config = _tab()
        tab.save()
        assert config.written == _VALUES

    def test_register_count_is_not_saved(self):
        """Es de sólo lectura: su dueño es el mapa."""
        tab, config = _tab(**{"modbus.register_count": 500})
        tab.save()
        assert "modbus.register_count" not in config.written


class TestParity:
    def test_the_combo_shows_a_word_and_saves_the_letter(self):
        tab, config = _tab(**{"modbus.rtu.parity": "O"})
        assert tab._rtu_parity.currentText() != "O"
        tab.save()
        assert config.written["modbus.rtu.parity"] == "O"

    def test_an_unknown_parity_goes_back_as_it_was(self):
        tab, config = _tab(**{"modbus.rtu.parity": "M"})
        tab.save()
        assert config.written["modbus.rtu.parity"] == "M"


class TestFrame:
    def test_the_frame_reflects_the_loaded_line(self):
        tab, _ = _tab()
        text = tab._rtu_frame.text()
        assert "19200 bps" in text
        assert "8E2" in text
        assert text.endswith("7")

    def test_the_frame_follows_an_edit(self):
        tab, _ = _tab()
        tab._rtu_baudrate.setValue(9600)
        tab._slave_id.setValue(12)
        text = tab._rtu_frame.text()
        assert "9600 bps" in text
        assert text.endswith("12")


class TestEnabled:
    def test_a_disabled_transport_disables_its_fields(self):
        tab, _ = _tab()
        assert not tab._tcp_host.isEnabled()
        assert not tab._tcp_port.isEnabled()
        assert tab._rtu_port.isEnabled()

    def test_enabling_a_transport_enables_its_fields(self):
        tab, _ = _tab()
        tab._tcp_enabled.setChecked(True)
        assert tab._tcp_host.isEnabled()
        assert tab._tcp_port.isEnabled()
