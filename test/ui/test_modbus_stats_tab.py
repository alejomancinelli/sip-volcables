"""
Tests de la pestaña de estadísticas de Modbus: qué muestra de cada transporte y qué hace
«Reiniciar».

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                              # noqa: E402

from system.modbus.server import TRANSPORT_RTU, TRANSPORT_TCP           # noqa: E402
from ui.views.diagnostics.modbus_stats_tab import ModbusStatsTab        # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


class _MockConfig:
    """ConfigManager mínimo con `get` punteado, sin archivo ni Lock."""

    def get(self, key: str, default: object = None) -> object:
        return default


def _stats(**overrides) -> dict:
    stats = {"connections": 1, "requests": 100, "reads": 90, "writes": 6, "exceptions": 4,
             "last_exception_code": 2, "requests_per_min": 60, "last_request_age_s": 0.5}
    stats.update(overrides)
    return stats


def _shown(tab: ModbusStatsTab, transport: str, key: str) -> str:
    return tab._cards[transport]._values[key].text()


class TestValues:
    def test_the_counters_are_shown_as_they_come(self):
        tab = ModbusStatsTab(_MockConfig())
        tab.update_stats({TRANSPORT_TCP: _stats()})
        assert _shown(tab, TRANSPORT_TCP, "requests") == "100"
        assert _shown(tab, TRANSPORT_TCP, "connections") == "1"
        assert tab._cards[TRANSPORT_TCP]._rate.text() == "60"

    def test_the_exceptions_name_the_last_code(self):
        tab = ModbusStatsTab(_MockConfig())
        tab.update_stats({TRANSPORT_TCP: _stats()})
        assert "0x02" in _shown(tab, TRANSPORT_TCP, "exceptions")

    def test_the_serial_port_is_open_or_closed_and_not_a_count(self):
        tab = ModbusStatsTab(_MockConfig())
        tab.update_stats({TRANSPORT_RTU: _stats(connections=0)})
        assert not _shown(tab, TRANSPORT_RTU, "connections").isdigit()

    def test_a_missing_transport_shows_no_data(self):
        tab = ModbusStatsTab(_MockConfig())
        tab.update_stats({})
        assert _shown(tab, TRANSPORT_TCP, "requests") == "—"


class TestReset:
    def test_it_counts_from_zero_after_a_reset(self):
        tab = ModbusStatsTab(_MockConfig())
        tab.update_stats({TRANSPORT_TCP: _stats()})
        tab._cards[TRANSPORT_TCP]._reset()
        tab.update_stats({TRANSPORT_TCP: _stats(requests=105, reads=95)})
        assert _shown(tab, TRANSPORT_TCP, "requests") == "5"
        assert _shown(tab, TRANSPORT_TCP, "reads") == "5"

    def test_the_other_transport_is_not_reset(self):
        tab = ModbusStatsTab(_MockConfig())
        tab.update_stats({TRANSPORT_TCP: _stats(), TRANSPORT_RTU: _stats()})
        tab._cards[TRANSPORT_TCP]._reset()
        assert _shown(tab, TRANSPORT_RTU, "requests") == "100"

    def test_a_restarted_server_drops_the_old_reference(self):
        """Con los contadores del servidor de vuelta en cero, restar daría negativos."""
        tab = ModbusStatsTab(_MockConfig())
        tab.update_stats({TRANSPORT_TCP: _stats()})
        tab._cards[TRANSPORT_TCP]._reset()
        tab.update_stats({TRANSPORT_TCP: _stats(requests=3, reads=3, writes=0, exceptions=0)})
        assert _shown(tab, TRANSPORT_TCP, "requests") == "3"
