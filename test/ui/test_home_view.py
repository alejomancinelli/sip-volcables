"""
Tests de la página de inicio: cómo se muestran la dirección de una cámara, los tiempos y su
edad, el estado general de la placa y que cada tabla resuma lo que le llega.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton        # noqa: E402

import ui.views.home_view as home_view                                 # noqa: E402
from system.license import manager                                     # noqa: E402
from ui.strings import tr                                              # noqa: E402
from ui.views.home_view import (                                       # noqa: E402
    HomeView, format_age, format_camera_address, format_ms, format_uptime, get_overall_state,
)
from ui.widgets.status_chip import CHIP_ERROR, CHIP_OK, CHIP_WARNING   # noqa: E402

_CONNECTED = {"connected": True, "capture_enabled": True, "fps_estimated": 9.84}
_DISCONNECTED = {"connected": False, "capture_enabled": True, "fps_estimated": 0.0}


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


class _MockConfig:
    """Dos cámaras —una apagada— y tres pipelines: uno por cámara y uno apagado."""

    def __init__(self, **overrides):
        self._values = {
            "system.app_name": "Volcables",
            "system.device_id": "JETSON-01",
            "cameras": {
                "camera_1": {"name": "Acoplado", "driver": "rtsp",
                             "address": "rtsp://10.8.51.1:8554/cam_1"},
                "camera_2": {"name": "Chasis", "driver": "basler", "address": "10.8.51.20",
                             "enabled": False},
            },
            "inference.pipelines": {
                "pipeline_1": {"name": "Flujo de grano", "cameras": ["camera_1"]},
                "pipeline_2": {"cameras": ["camera_2"]},
                "pipeline_3": {"name": "Apagado", "enabled": False},
            },
        }
        self._values.update(overrides)

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


class _Clock:
    """Reloj monótono controlado: la edad de un resultado se mide, no se espera."""

    def __init__(self, now_s: float = 1_000.0):
        self.now_s = now_s

    def advance(self, seconds: float):
        self.now_s += seconds

    def __call__(self) -> float:
        return self.now_s


def _texts(widget, object_name: str) -> list[str]:
    """Los textos como se leen, sin el relleno de las columnas de ancho fijo."""
    return [label.text().strip() for label in widget.findChildren(QLabel, object_name)]


def _raw_texts(widget, object_name: str) -> list[str]:
    return [label.text() for label in widget.findChildren(QLabel, object_name)]


class TestCameraAddress:
    def test_an_rtsp_camera_shows_the_whole_url(self):
        assert format_camera_address("rtsp", "rtsp://10.8.51.1:8554/cam_1") == \
            "rtsp://10.8.51.1:8554/cam_1"

    def test_credentials_written_in_the_url_are_left_out(self):
        """Las credenciales salen del entorno; si alguien las escribió igual, no se ven."""
        assert format_camera_address("rtsp", "rtsp://admin:secreta@10.8.51.1:8554/cam_1") == \
            "rtsp://10.8.51.1:8554/cam_1"

    def test_an_ip_or_serial_is_shown_as_is(self):
        assert format_camera_address("basler", "10.8.51.20") == "10.8.51.20"
        assert format_camera_address("st", "24-05044") == "24-05044"

    def test_a_video_file_shows_only_its_name(self):
        assert format_camera_address("video_file", "recordings/linea/turno_noche.mp4") == \
            "turno_noche.mp4"
        assert format_camera_address("video_file", r"C:\videos\turno.mp4") == "turno.mp4"

    def test_without_an_address_there_is_a_dash(self):
        assert format_camera_address("mock", None) == "—"
        assert format_camera_address("rtsp", "  ") == "—"


class TestFormats:
    def test_under_an_hour_the_uptime_counts_minutes(self):
        assert format_uptime(59 * 60 + 59) == tr("home_uptime_minutes").format(minutes=59)

    def test_under_a_day_the_uptime_counts_hours_and_minutes(self):
        assert format_uptime(2 * 3600 + 5 * 60) == \
            tr("home_uptime_hours").format(hours=2, minutes=5)

    def test_from_a_day_on_the_uptime_counts_days_and_hours(self):
        assert format_uptime(3 * 86400 + 4 * 3600 + 59 * 60) == \
            tr("home_uptime_days").format(days=3, hours=4)

    def test_the_age_goes_from_now_to_seconds_minutes_and_hours(self):
        assert format_age(0.4) == tr("home_age_now")
        assert format_age(42.9) == tr("home_age_seconds").format(seconds=42)
        assert format_age(150) == tr("home_age_minutes").format(minutes=2)
        assert format_age(7300) == tr("home_age_hours").format(hours=2)

    def test_a_time_of_zero_means_there_is_none(self):
        """El primer frame de una cámara no tiene frame anterior: no tiene ciclo."""
        assert format_ms(0.0) == "—"
        assert format_ms(101.6) == "102 ms"


class TestOverallState:
    def test_everything_running_is_ok(self):
        assert get_overall_state([CHIP_OK], [CHIP_OK], False) == CHIP_OK

    def test_a_camera_or_service_in_error_is_a_warning(self):
        assert get_overall_state([CHIP_ERROR], [CHIP_OK], False) == CHIP_WARNING
        assert get_overall_state([CHIP_OK], [CHIP_ERROR], False) == CHIP_WARNING

    def test_something_starting_or_off_on_purpose_is_not_a_warning(self):
        assert get_overall_state([CHIP_WARNING], [CHIP_WARNING], False) == CHIP_OK

    def test_without_a_valid_licence_it_is_red_whatever_the_rest(self):
        assert get_overall_state([CHIP_OK], [CHIP_OK], True) == CHIP_ERROR


class TestNameplate:
    def test_it_shows_the_app_and_the_device(self):
        view = HomeView(_MockConfig())
        assert "Volcables" in _texts(view, "homeNameplateTitle")
        assert "JETSON-01" in _texts(view, "homeSpecValue")

    def test_the_ip_of_the_device_is_not_shown(self):
        """Tiene más de una interfaz: la que importa depende de quién pregunta."""
        assert tr("home_spec_device") in _texts(HomeView(_MockConfig()), "homeSpecLabel")
        assert len(_texts(HomeView(_MockConfig()), "homeSpecLabel")) == 3

    def test_the_licence_decides_red_with_the_same_flag_as_the_footer(self):
        view = HomeView(_MockConfig())
        view.update_license({"state": manager.STATE_ABSENT, "should_report_invalid": True})
        assert view.overall_state == CHIP_ERROR
        assert tr("lic_state_absent") in _texts(view, "statusPill")


class TestCameras:
    def test_every_camera_is_a_row_of_the_same_card(self):
        view = HomeView(_MockConfig())
        names = _texts(view, "homeRowLabel")
        assert "Acoplado" in names and "Chasis" in names
        assert "rtsp://10.8.51.1:8554/cam_1" in _texts(view, "homeFieldValue")

    def test_a_row_follows_the_camera_status(self):
        view = HomeView(_MockConfig())
        view.update_camera_status(_CONNECTED, "camera_1")
        assert tr("camera_connected") in _texts(view, "statusPill")
        assert "9.8" in _texts(view, "homeFieldValue")

    def test_the_fps_keep_the_width_of_two_digits(self):
        """Pasar de 12.5 a 9.8 no corre la columna del estado."""
        view = HomeView(_MockConfig())
        view.update_camera_status({**_CONNECTED, "fps_estimated": 12.5}, "camera_1")
        wide = [text for text in _raw_texts(view, "homeFieldValue") if "12.5" in text]
        view.update_camera_status({**_CONNECTED, "fps_estimated": 9.8}, "camera_1")
        narrow = [text for text in _raw_texts(view, "homeFieldValue") if "9.8" in text]
        assert len(wide[0]) == len(narrow[0])

    def test_a_disabled_camera_says_so_and_does_not_raise_a_warning(self):
        view = HomeView(_MockConfig())
        assert tr("camera_disabled") in _texts(view, "statusPill")
        view.update_camera_status(_CONNECTED, "camera_1")
        assert view.overall_state == CHIP_OK

    def test_a_disconnected_camera_raises_a_warning(self):
        view = HomeView(_MockConfig())
        view.update_camera_status(_DISCONNECTED, "camera_1")
        assert view.overall_state == CHIP_WARNING

    def test_without_cameras_it_says_there_are_none(self):
        view = HomeView(_MockConfig(cameras={}))
        assert tr("camera_no_cameras") in _texts(view, "hintLabel")

    def test_the_monitor_button_asks_for_the_monitor(self):
        view = HomeView(_MockConfig())
        requests = []
        view.monitor_requested.connect(lambda: requests.append(True))
        view.findChild(QPushButton, "ghostButton").click()
        assert requests == [True]


class TestInferenceTimes:
    def test_there_is_a_row_per_enabled_pipeline_and_camera(self):
        """Sin `name`, el slot; un pipeline apagado no tiene fila."""
        names = _texts(HomeView(_MockConfig()), "homeRowLabel")
        assert "Flujo de grano" in names and "pipeline_2" in names
        assert "Apagado" not in names

    def test_a_pair_without_results_says_so(self):
        assert tr("home_no_result") in _texts(HomeView(_MockConfig()), "homeRowDetail")

    def test_the_times_are_painted_on_the_next_refresh_and_not_before(self, monkeypatch):
        """Llega uno por frame: repintar en cada uno sería trabajo que nadie lee."""
        monkeypatch.setattr(home_view.time, "monotonic", _Clock())
        view = HomeView(_MockConfig())
        view.update_inference_times("camera_1", "pipeline_1", 101.6, 38.2)
        assert "102 ms" not in _texts(view, "homeFieldValue")
        view.refresh()
        values = _texts(view, "homeFieldValue")
        assert "102 ms" in values and "38 ms" in values
        assert tr("home_age_now") in _texts(view, "homeRowDetail")

    def test_the_age_keeps_growing_while_no_result_arrives(self, monkeypatch):
        """Un par que se cayó deja quieto su ciclo, pero no su edad."""
        clock = _Clock()
        monkeypatch.setattr(home_view.time, "monotonic", clock)
        view = HomeView(_MockConfig())
        view.update_inference_times("camera_1", "pipeline_1", 100.0, 30.0)
        clock.advance(7.2)
        view.refresh()
        assert tr("home_age_seconds").format(seconds=7) in _texts(view, "homeRowDetail")

    def test_a_pair_that_is_not_in_the_table_is_ignored(self):
        view = HomeView(_MockConfig())
        view.update_inference_times("camera_2", "pipeline_1", 100.0, 30.0)
        view.refresh()
        assert "100 ms" not in _texts(view, "homeFieldValue")


class TestResults:
    def test_a_pair_waits_for_its_first_verdict(self):
        assert tr("home_results_waiting") in _texts(HomeView(_MockConfig()), "homeRowDetail")

    def test_each_verdict_is_a_pill_with_its_label_and_state(self):
        view = HomeView(_MockConfig())
        view.update_verdicts({("camera_1", "pipeline_1"): {"flow_ok": 1, "safety_ok": 0}})
        pills = _texts(view, "statusPill")
        assert f"{tr('verdict_flow_ok')}: {tr('verdict_ok')}" in pills
        assert f"{tr('verdict_safety_ok')}: {tr('verdict_nok')}" in pills

    def test_an_expired_verdict_says_there_is_no_data(self):
        """Venció en el cableado: en pantalla no queda el último OK."""
        view = HomeView(_MockConfig())
        view.update_verdicts({("camera_1", "pipeline_1"): {"flow_ok": 1}})
        view.update_verdicts({("camera_1", "pipeline_1"): {"flow_ok": None}})
        pills = _texts(view, "statusPill")
        assert f"{tr('verdict_flow_ok')}: {tr('verdict_no_data')}" in pills
        assert f"{tr('verdict_flow_ok')}: {tr('verdict_ok')}" not in pills

    def test_a_verdict_without_a_label_shows_its_name(self):
        view = HomeView(_MockConfig())
        view.update_verdicts({("camera_1", "pipeline_1"): {"belt_ok": 1}})
        assert f"belt_ok: {tr('verdict_ok')}" in _texts(view, "statusPill")

    def test_a_nok_does_not_touch_the_state_of_the_device(self):
        """NOK es del proceso, no una falla del equipo."""
        view = HomeView(_MockConfig())
        view.update_verdicts({("camera_1", "pipeline_1"): {"flow_ok": 0}})
        assert view.overall_state == CHIP_OK

    def test_a_pair_that_is_not_in_the_table_is_ignored(self):
        view = HomeView(_MockConfig())
        view.update_verdicts({("camera_2", "pipeline_1"): {"flow_ok": 1}})
        assert f"{tr('verdict_flow_ok')}: {tr('verdict_ok')}" not in _texts(view, "statusPill")


class TestServices:
    def test_a_service_in_error_raises_a_warning(self):
        view = HomeView(_MockConfig())
        view.set_service_status("modbus_tcp", "error")
        assert view.overall_state == CHIP_WARNING
        view.set_service_status("modbus_tcp", "active")
        assert view.overall_state == CHIP_OK

    def test_an_unknown_service_or_camera_is_ignored(self):
        view = HomeView(_MockConfig())
        view.set_service_status("ftp", "error")
        view.update_camera_status(_DISCONNECTED, "camera_9")
        assert view.overall_state == CHIP_OK

    def test_the_heartbeat_shows_the_counter_sent_to_the_plc(self):
        view = HomeView(_MockConfig())
        view.update_heartbeat(1234)
        assert "1234" in _texts(view, "homeFieldValue")
