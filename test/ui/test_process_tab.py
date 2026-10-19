"""
Tests de la pestaña de proceso: un grupo de calza por cámara de `stopper_check`, y que
`load()`/`save()` lleven sus valores al config sin tocar la línea, que es de su diálogo.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton                 # noqa: E402

from system.inference.annotations import DEFAULT_MIN_RAISED_PX          # noqa: E402
from ui.views.config.process_tab import ProcessTab                      # noqa: E402

_SECTION = "process.stopper_check"


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


class _MockConfig:
    """ConfigManager mínimo con `get` punteado y `set`, sin archivo ni Lock."""

    def __init__(self, **overrides):
        self._values = {
            "cameras": {"camera_1": {}, "camera_2": {}, "camera_3": {}},
            "inference.pipelines": {
                "pipeline_1": {"type": "grain_flow", "cameras": ["camera_3"]},
                "pipeline_3": {"type": "stopper_check", "cameras": ["camera_1", "camera_2"]},
            },
        }
        self._values.update(overrides)
        self.written: dict = {}

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)

    def set(self, key: str, value: object):
        self.written[key] = value
        self._values[key] = value


def _tab(**overrides) -> tuple[ProcessTab, _MockConfig]:
    config = _MockConfig(**overrides)
    tab = ProcessTab(config)
    tab.load()
    return tab, config


class TestGroups:
    def test_one_group_per_stopper_camera(self):
        tab, _ = _tab()
        assert list(tab._stoppers) == ["camera_1", "camera_2"]

    def test_without_stopper_pipelines_there_are_no_groups(self):
        tab, config = _tab(**{"inference.pipelines": {}})
        tab.save()
        assert tab._stoppers == {} and config.written == {}


class TestRoundTrip:
    def test_the_values_go_back_as_they_were(self):
        tab, config = _tab(**{f"{_SECTION}.min_raised_px": {"camera_1": 80},
                              f"{_SECTION}.max_gap_px": {"camera_1": 35}})
        tab.save()
        assert config.written[f"{_SECTION}.min_raised_px.camera_1"] == 80
        assert config.written[f"{_SECTION}.max_gap_px.camera_1"] == 35

    def test_a_missing_minimum_shows_the_default(self):
        tab, config = _tab()
        tab.save()
        assert config.written[f"{_SECTION}.min_raised_px.camera_2"] == DEFAULT_MIN_RAISED_PX

    def test_an_uncalibrated_distance_is_saved_as_null(self):
        """Null es lo que el annotator lee como «sin calibrar»: un 0 diría «sólo pegada»."""
        tab, config = _tab()
        tab.save()
        assert config.written[f"{_SECTION}.max_gap_px.camera_2"] is None

    def test_the_wheel_side_goes_back_as_it_was(self):
        tab, config = _tab(**{f"{_SECTION}.wheel_side": {"camera_1": "left"}})
        tab.save()
        assert config.written[f"{_SECTION}.wheel_side.camera_1"] == "left"

    def test_a_missing_wheel_side_is_any(self):
        tab, config = _tab()
        tab.save()
        assert config.written[f"{_SECTION}.wheel_side.camera_2"] == "any"

    def test_the_height_goes_back_as_it_was(self):
        tab, config = _tab(**{f"{_SECTION}.raised_y_px": {"camera_1": 410}})
        tab.save()
        assert config.written[f"{_SECTION}.raised_y_px.camera_1"] == 410

    def test_an_uncalibrated_height_is_saved_as_null(self):
        tab, config = _tab()
        tab.save()
        assert config.written[f"{_SECTION}.raised_y_px.camera_2"] is None


class TestLevel:
    def test_reload_level_shows_what_the_dialog_wrote(self):
        tab, config = _tab()
        config.set(f"{_SECTION}.raised_y_px", {"camera_2": 77})
        tab.reload_level("camera_2")
        assert tab._stoppers["camera_2"]._raised_y_spin.value() == 77

    def test_the_mark_button_asks_for_its_camera_key(self):
        tab, _ = _tab()
        requests = []
        tab.open_level_requested.connect(lambda *args: requests.append(args))
        tab._stoppers["camera_2"].findChild(QPushButton).click()
        assert requests and requests[0][:2] == ("camera_2", f"{_SECTION}.raised_y_px.camera_2")
