"""
Tests de la pestaña de telemetría: que la sección `telemetry:` vaya y vuelva del config sin
deformarse, y que cada destino apagado apague sus propios campos.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                              # noqa: E402

from ui.views.config.telemetry_tab import TelemetryTab                  # noqa: E402

_VALUES = {
    "telemetry.influxdb.enabled": True,
    "telemetry.influxdb.url": "http://10.0.0.2:8086",
    "telemetry.influxdb.org": "iea",
    "telemetry.influxdb.bucket": "planta",
    "telemetry.mqtt.enabled": False,
    "telemetry.mqtt.host": "10.0.0.3",
    "telemetry.mqtt.port": 8883,
    "telemetry.mqtt.tls": True,
    "telemetry.mqtt.user": "equipo",
    "telemetry.mqtt.topic_base": "planta/linea_1",
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


def _tab(**overrides) -> tuple[TelemetryTab, _MockConfig]:
    config = _MockConfig(**overrides)
    return TelemetryTab(config), config


class TestRoundTrip:
    def test_every_value_goes_back_unchanged(self):
        tab, config = _tab()
        tab.save()
        assert config.written == _VALUES


class TestEnabled:
    def test_a_disabled_destination_disables_its_fields(self):
        tab, _ = _tab()
        assert not tab._mqtt_host.isEnabled()
        assert not tab._mqtt_tls.isEnabled()
        assert tab._influx_url.isEnabled()

    def test_enabling_a_destination_enables_its_fields(self):
        tab, _ = _tab()
        tab._mqtt_enabled.setChecked(True)
        assert tab._mqtt_tls.isEnabled()
        assert tab._mqtt_topic_base.isEnabled()
