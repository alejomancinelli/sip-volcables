"""
Tests de la pestaña de hardware: qué muestra de cada métrica, de cada cámara y de cada
interfaz, y que lo que el equipo no expone se vea como «—» y no como un cero medido.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                              # noqa: E402

from tools.net_interfaces import NetInterface                           # noqa: E402
from ui.views.diagnostics import hardware_tab                           # noqa: E402
from ui.views.diagnostics.hardware_tab import HardwareTab               # noqa: E402

_EMPTY = "—"


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def fixed_interfaces(monkeypatch):
    """Las interfaces del equipo, fijas: la pestaña no lee las de la máquina que corre el test."""
    monkeypatch.setattr(hardware_tab, "list_interfaces", lambda: [
        NetInterface("eth0", "10.0.0.5", True),
        NetInterface("eth1", "", False),
    ])


class _MockConfig:
    """ConfigManager mínimo con `get` punteado, sin archivo ni Lock."""

    def __init__(self, values: dict | None = None):
        self._values = values or {}

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


def _metrics(**overrides) -> dict:
    metrics = {"cpu_usage_pct": 37, "gpu_usage_pct": 12, "cpu_temp_c": 52.4, "gpu_temp_c": 0,
               "ram_used_mb": 2048, "ram_total_mb": 8192, "disk_free_gb": 120.4,
               "power_w": 9.75, "net_mbps": {}, "temps_c": {}, "engines": {},
               "overcurrent_count": 0}
    metrics.update(overrides)
    return metrics


def _tile(tab: HardwareTab, key: str):
    return tab._tiles[key]


class TestTiles:
    def test_usage_is_shown_with_its_unit(self):
        tab = HardwareTab(_MockConfig())
        tab.update_metrics(_metrics())
        assert _tile(tab, "cpu").value_text == "37 %"
        assert _tile(tab, "power").value_text == "9.8 W"

    def test_memory_is_shown_in_gb_with_the_share_in_use(self):
        tab = HardwareTab(_MockConfig())
        tab.update_metrics(_metrics())
        assert _tile(tab, "memory").value_text == "2.0 / 8.0 GB"
        assert _tile(tab, "memory").foot_text.startswith("25 %")

    def test_a_missing_temperature_sensor_is_not_shown_as_zero(self):
        """El monitor informa 0 lo que el equipo no expone: no es una lectura."""
        tab = HardwareTab(_MockConfig())
        tab.update_metrics(_metrics())
        assert "52" in _tile(tab, "cpu").foot_text
        assert "0" not in _tile(tab, "gpu").foot_text

    def test_no_power_sensor_shows_a_dash(self):
        tab = HardwareTab(_MockConfig())
        tab.update_metrics(_metrics(power_w=0.0))
        assert _tile(tab, "power").value_text == _EMPTY

    def test_overcurrent_events_are_counted_in_the_power_foot(self):
        tab = HardwareTab(_MockConfig())
        tab.update_metrics(_metrics(overcurrent_count=3))
        assert "3" in _tile(tab, "power").foot_text


class TestThermalZonesAndEngines:
    def test_every_zone_gets_its_temperature(self):
        tab = HardwareTab(_MockConfig())
        tab.update_metrics(_metrics(temps_c={"cpu-thermal": 51.0, "gpu-thermal": 48.5}))
        assert tab._temperature_cells["cpu-thermal"].text() == "51.0 °C"
        assert tab._temperature_cells["gpu-thermal"].text() == "48.5 °C"

    def test_the_engines_card_is_hidden_without_engines(self):
        """Fuera de una Jetson `engines` viene vacío: una tarjeta vacía no dice nada."""
        tab = HardwareTab(_MockConfig())
        tab.update_metrics(_metrics())
        assert tab._engines_card.isHidden()

    def test_an_idle_engine_shows_no_clock(self):
        tab = HardwareTab(_MockConfig())
        tab.update_metrics(_metrics(engines={"VIC": {"is_active": True, "mhz": 729},
                                             "NVENC": {"is_active": False, "mhz": 0}}))
        assert not tab._engines_card.isHidden()
        assert tab._engine_rows["VIC"][1].text() == "729 MHz"
        assert tab._engine_rows["NVENC"][1].text() == _EMPTY


class TestCameras:
    def _tab(self) -> HardwareTab:
        return HardwareTab(_MockConfig({"cameras": {
            "camera_1": {"name": "Entrada", "driver": "basler",
                         "acquisition": {"fps_limit": 10}},
            "camera_2": {"name": "Salida", "driver": "rtsp"},
        }}))

    def test_every_configured_camera_has_a_row_before_it_reports(self):
        tab = self._tab()
        assert set(tab._camera_rows) == {"camera_1", "camera_2"}

    def test_fps_temperature_and_missed_views_are_shown(self):
        tab = self._tab()
        tab.update_camera_status({"connected": True, "capture_enabled": True,
                                  "temperature": 41.5, "fps_estimated": 9.84,
                                  "view_misses": 2}, "camera_1")
        _, fps, temperature, view_misses = tab._camera_rows["camera_1"].texts
        assert (fps, temperature, view_misses) == ("9.8", "41.5 °C", "2")

    def test_a_camera_without_sensor_or_views_shows_dashes(self):
        """Por RTSP la temperatura es 0.0 siempre, y sin vistas pedidas no hay `view_misses`."""
        tab = self._tab()
        tab.update_camera_status({"connected": True, "capture_enabled": True,
                                  "temperature": 0.0, "fps_estimated": 5.0}, "camera_2")
        _, _, temperature, view_misses = tab._camera_rows["camera_2"].texts
        assert (temperature, view_misses) == (_EMPTY, _EMPTY)

    def test_a_camera_that_is_not_configured_is_ignored(self):
        tab = self._tab()
        tab.update_camera_status({"fps_estimated": 5.0}, "camera_9")
        assert "camera_9" not in tab._camera_rows


class TestNetwork:
    def test_an_interface_shows_its_address_and_traffic(self):
        tab = HardwareTab(_MockConfig())
        tab.update_metrics(_metrics(net_mbps={"eth0": {"rx_mbps": 42.1, "tx_mbps": 3.2}}))
        address, _, received, sent = tab._network_rows["eth0"].texts
        assert (address, received, sent) == ("10.0.0.5", "42.10 Mbps", "3.20 Mbps")

    def test_an_interface_without_address_shows_a_dash(self):
        tab = HardwareTab(_MockConfig())
        tab.update_metrics(_metrics(net_mbps={"eth1": {"rx_mbps": 0.0, "tx_mbps": 0.0}}))
        assert tab._network_rows["eth1"].texts[0] == _EMPTY

    def test_a_row_is_created_once_per_interface(self):
        tab = HardwareTab(_MockConfig())
        for _ in range(3):
            tab.update_metrics(_metrics(net_mbps={"eth0": {"rx_mbps": 1.0, "tx_mbps": 1.0}}))
        assert list(tab._network_rows) == ["eth0"]
