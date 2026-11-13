"""
Tests de la pestaña de proceso: una sub-pestaña de calza por cámara de `stopper_check`, y que
`load()`/`save()` lleven sus valores al config sin tocar la línea, que es de su diálogo.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton                 # noqa: E402

from system.inference.person_safety import (                            # noqa: E402
    DEFAULT_CONFIRM_HITS, DEFAULT_CONFIRM_WINDOW, DEFAULT_RELEASE_S,
)
from system.inference.stopper_check import DEFAULT_MIN_RAISED_PX       # noqa: E402
from system.inference.result import Detection, InferenceResult         # noqa: E402
from ui.strings import tr                                               # noqa: E402
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


class TestCameraTabs:
    def test_one_tab_per_stopper_camera(self):
        tab, _ = _tab()
        assert list(tab._stoppers) == ["camera_1", "camera_2"]
        assert tab._stopper_tabs.count() == 2

    def test_each_tab_is_named_after_its_camera(self):
        """Como las de la pestaña de inferencia: el nombre del operador, y el slot si no tiene."""
        tab, _ = _tab(**{"cameras.camera_1.name": "Calza derecha"})
        labels = [tab._stopper_tabs.tabText(i) for i in range(tab._stopper_tabs.count())]
        assert labels == ["Calza derecha", "camera_2"]

    def test_without_stopper_pipelines_there_are_no_tabs(self):
        tab, config = _tab(**{"inference.pipelines": {}})
        tab.save()
        assert tab._stoppers == {} and tab._stopper_tabs is None and config.written == {}


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


_PERSON_SECTION = "process.person_safety"
_WITH_PERSONS = {"inference.pipelines": {
    "pipeline_2": {"type": "person_safety", "cameras": ["camera_3"]},
    "pipeline_3": {"type": "stopper_check", "cameras": ["camera_1"]},
}}


class TestPersonFilter:
    def test_without_person_pipelines_there_is_no_group(self):
        tab, _ = _tab()
        assert tab._person is None

    def test_the_values_go_back_as_they_were(self):
        tab, config = _tab(**_WITH_PERSONS, **{f"{_PERSON_SECTION}.confirm_hits": 3,
                                               f"{_PERSON_SECTION}.confirm_window": 8,
                                               f"{_PERSON_SECTION}.release_s": 7})
        tab.save()
        assert config.written[f"{_PERSON_SECTION}.confirm_hits"] == 3
        assert config.written[f"{_PERSON_SECTION}.confirm_window"] == 8
        assert config.written[f"{_PERSON_SECTION}.release_s"] == 7

    def test_a_release_with_decimals_is_shown_rounded(self):
        """El campo es de segundos enteros: un valor con decimales escrito a mano se redondea."""
        tab, _ = _tab(**_WITH_PERSONS, **{f"{_PERSON_SECTION}.release_s": 7.5})
        assert tab._person._release_spin.value() == 8

    def test_missing_values_show_the_defaults(self):
        tab, config = _tab(**_WITH_PERSONS)
        tab.save()
        assert config.written[f"{_PERSON_SECTION}.confirm_hits"] == DEFAULT_CONFIRM_HITS
        assert config.written[f"{_PERSON_SECTION}.confirm_window"] == DEFAULT_CONFIRM_WINDOW
        assert config.written[f"{_PERSON_SECTION}.release_s"] == pytest.approx(DEFAULT_RELEASE_S)

    def test_the_hits_cannot_exceed_the_window(self):
        """Más aciertos que frames en la ventana y el filtro no arrancaría."""
        tab, config = _tab(**_WITH_PERSONS)
        tab._person._confirm_window_spin.setValue(3)
        tab._person._confirm_hits_spin.setValue(5)
        tab.save()
        assert config.written[f"{_PERSON_SECTION}.confirm_hits"] == 3

    def test_the_stopper_tabs_are_still_there(self):
        tab, _ = _tab(**_WITH_PERSONS)
        assert list(tab._stoppers) == ["camera_1"]


_ZONE_KEY = f"{_PERSON_SECTION}.exclusion_zone_px"


class TestPersonZone:
    def test_one_zone_tab_per_person_camera(self):
        tab, _ = _tab(**_WITH_PERSONS)
        assert list(tab._person._zone_labels) == ["camera_3"]

    def test_a_camera_without_zone_says_the_whole_frame_counts(self):
        tab, _ = _tab(**_WITH_PERSONS)
        assert tab._person._zone_labels["camera_3"].text() == tr("process_person_no_zone")

    def test_a_drawn_zone_shows_its_vertices(self):
        tab, _ = _tab(**_WITH_PERSONS, **{_ZONE_KEY: {"camera_3": [[0, 0], [9, 0], [9, 9]]}})
        assert tab._person._zone_labels["camera_3"].text() == \
            tr("polygon_vertices").format(count=3)

    def test_the_draw_button_asks_for_its_camera_key(self):
        tab, _ = _tab(**_WITH_PERSONS)
        requests = []
        tab.open_polygon_requested.connect(lambda *args: requests.append(args))
        buttons = [button for button in tab._person.findChildren(QPushButton)
                   if button.text() == tr("process_draw_zone")]
        buttons[0].click()
        assert requests == [("camera_3", f"{_ZONE_KEY}.camera_3", "process_person_zone")]

    def test_reload_polygon_shows_what_the_dialog_wrote(self):
        tab, config = _tab(**_WITH_PERSONS)
        config.set(_ZONE_KEY, {"camera_3": [[0, 0], [9, 0], [9, 9], [0, 9]]})
        tab.reload_polygon("camera_3")
        assert tab._person._zone_labels["camera_3"].text() == \
            tr("polygon_vertices").format(count=4)

    def test_save_does_not_write_the_zone(self):
        """La zona la escribe su diálogo: guardar la pestaña no la pisa."""
        tab, config = _tab(**_WITH_PERSONS)
        tab.save()
        assert not any(key.startswith(_ZONE_KEY) for key in config.written)


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


def _box(class_name: str, x1: int, y1: int, x2: int, y2: int) -> Detection:
    """Una detección rectangular con su máscara llena, de (x1, y1) a (x2, y2) incluidos."""
    return Detection(class_index=0, class_name=class_name, confidence_pct=90.0,
                     bbox_px=(x1, y1, x2 + 1, y2 + 1),
                     mask=np.ones((y2 - y1 + 1, x2 - x1 + 1), np.uint8))


# La calza tiene 20 px de ancho y arranca en y = 100: con la línea en 120 quedan 400 px sobre
# ella. La rueda termina en x = 189, a 11 px de la calza, a su izquierda.
_STOPPER = _box("stopper", 200, 100, 219, 160)
_WHEEL = _box("wheel", 120, 90, 189, 200)
_FRAME = np.zeros((240, 320, 3), np.uint8)


def _result(*detections: Detection, timestamp_s: float = 100.0,
            is_valid: bool = True) -> InferenceResult:
    return InferenceResult(camera_slot="camera_1", pipeline_slot="pipeline_3",
                           detections=list(detections), source_bgr=_FRAME,
                           timestamp_s=timestamp_s, is_valid=is_valid)


def _inspect_tab(**overrides) -> ProcessTab:
    values = {f"{_SECTION}.min_raised_px": {"camera_1": 300},
              f"{_SECTION}.max_gap_px": {"camera_1": 20}}
    values.update(overrides)
    tab, _ = _tab(**values)
    return tab


class TestLevelInspector:
    """
    En pausa, el diálogo de la altura mide sobre el último frame inferido: los píxeles de la
    calza sobre la línea y la distancia a la rueda, con las reglas del annotator.
    """

    def test_it_measures_the_last_valid_result_of_the_camera(self):
        tab = _inspect_tab()
        inspect = tab.build_level_inspector("camera_1",
                                            lambda camera, pipeline: _result(_STOPPER, _WHEEL))
        snapshot = inspect()
        assert snapshot.frame_bgr is _FRAME
        assert snapshot.describe(120)[1:] == [
            tr("stopper_inspect_px").format(px=400, min=300, verdict=tr("stopper_inspect_ok")),
            tr("stopper_inspect_gap").format(gap=11, max=20, verdict=tr("stopper_inspect_ok")),
        ]

    def test_the_pixels_follow_the_line(self):
        tab = _inspect_tab()
        snapshot = tab.build_level_inspector(
            "camera_1", lambda camera, pipeline: _result(_STOPPER, _WHEEL))()
        assert snapshot.describe(110)[1] == tr("stopper_inspect_px").format(
            px=200, min=300, verdict=tr("stopper_inspect_nok"))

    def test_it_judges_with_the_values_on_screen_even_unsaved(self):
        tab = _inspect_tab()
        tab._stoppers["camera_1"]._max_gap_spin.setValue(5)
        snapshot = tab.build_level_inspector(
            "camera_1", lambda camera, pipeline: _result(_STOPPER, _WHEEL))()
        assert snapshot.describe(120)[2] == tr("stopper_inspect_gap").format(
            gap=11, max=5, verdict=tr("stopper_inspect_nok"))

    def test_an_uncalibrated_gap_is_shown_without_a_verdict(self):
        tab = _inspect_tab(**{f"{_SECTION}.max_gap_px": {}})
        snapshot = tab.build_level_inspector(
            "camera_1", lambda camera, pipeline: _result(_STOPPER, _WHEEL))()
        assert snapshot.describe(120)[2] == tr("stopper_inspect_gap_uncalibrated").format(gap=11)

    def test_a_wheel_on_the_other_side_does_not_count(self):
        tab = _inspect_tab(**{f"{_SECTION}.wheel_side": {"camera_1": "right"}})
        snapshot = tab.build_level_inspector(
            "camera_1", lambda camera, pipeline: _result(_STOPPER, _WHEEL))()
        assert snapshot.describe(120)[2] == tr("stopper_inspect_no_wheel")

    def test_without_a_stopper_it_says_so(self):
        tab = _inspect_tab()
        snapshot = tab.build_level_inspector(
            "camera_1", lambda camera, pipeline: _result(_WHEEL))()
        assert snapshot.describe(120)[1:] == [tr("stopper_inspect_no_stopper")]

    @pytest.mark.parametrize("result", [None, _result(_STOPPER, _WHEEL, is_valid=False)])
    def test_without_a_valid_result_there_is_nothing_to_show(self, result):
        tab = _inspect_tab()
        assert tab.build_level_inspector("camera_1", lambda camera, pipeline: result)() is None

    def test_a_camera_without_stoppers_has_no_inspector(self):
        tab = _inspect_tab()
        assert tab.build_level_inspector("camera_3", lambda camera, pipeline: None) is None
