"""Tests de los annotators del proyecto: el límite de carga por cámara y la composición
de varios en uno.

Módulo puro: los annotators reciben la geometría ya resuelta, así que acá no hay config
ni frames de cámara.
"""

import numpy as np
import pytest

from system.inference.annotations import (
    chain, labels_panel_annotator, max_load_line_annotator, unload_status_annotator,
)
from system.inference.result import Detection, InferenceResult

_SLOT = "camera_1"
_OTHER_SLOT = "camera_2"


def _frame() -> np.ndarray:
    return np.full((120, 200, 3), 128, np.uint8)


def _result(camera_slot: str = _SLOT) -> InferenceResult:
    return InferenceResult(camera_slot=camera_slot)


def _painted_rows(frame_bgr: np.ndarray) -> set[int]:
    """
    Filas que dejaron de ser el fondo, para saber a qué altura se dibujó.

    El antialiasing de OpenCV reparte una línea de 2 px sobre unas cinco filas, así que
    los tests afirman sobre la fila pedida y sobre el entorno que puede teñir, no sobre
    un conteo exacto de píxeles.
    """
    return {int(row) for row in np.flatnonzero(np.any(frame_bgr != 128, axis=(1, 2)))}


def _near(y_px: int) -> set[int]:
    """Filas que puede teñir una línea de 2 px con antialiasing a la altura `y_px`."""
    return set(range(y_px - 2, y_px + 3))


class TestMaxLoadLine:
    def test_it_draws_at_the_configured_height(self):
        frame = _frame()
        max_load_line_annotator({_SLOT: 60}, label="")(frame, _result())
        rows = _painted_rows(frame)
        assert 60 in rows and rows <= _near(60)

    def test_it_spans_the_whole_width(self):
        frame = _frame()
        max_load_line_annotator({_SLOT: 60}, label="")(frame, _result())
        assert np.all(frame[60] != 128)

    def test_each_camera_gets_its_own_height(self):
        annotator = max_load_line_annotator({_SLOT: 30, _OTHER_SLOT: 90}, label="")
        first, second = _frame(), _frame()
        annotator(first, _result(_SLOT))
        annotator(second, _result(_OTHER_SLOT))
        assert _painted_rows(first) <= _near(30)
        assert _painted_rows(second) <= _near(90)

    def test_a_camera_without_a_height_gets_no_line(self):
        frame = _frame()
        max_load_line_annotator({_SLOT: 60})(frame, _result(_OTHER_SLOT))
        assert _painted_rows(frame) == set()

    @pytest.mark.parametrize("y_px", [-1, 120, 500])
    def test_a_height_outside_the_frame_draws_nothing(self, y_px):
        frame = _frame()
        max_load_line_annotator({_SLOT: y_px})(frame, _result())
        assert _painted_rows(frame) == set()

    def test_it_draws_with_no_detections(self):
        """Es la referencia contra la que se mira el material: no puede aparecer y desaparecer."""
        frame = _frame()
        max_load_line_annotator({_SLOT: 60}, label="")(frame, _result())
        assert _painted_rows(frame) != set()

    def test_an_empty_frame_is_ignored(self):
        max_load_line_annotator({_SLOT: 60})(np.zeros((0, 0, 3), np.uint8), _result())

    def test_the_label_is_drawn_over_the_line(self):
        plain, labelled = _frame(), _frame()
        max_load_line_annotator({_SLOT: 60}, label="")(plain, _result())
        max_load_line_annotator({_SLOT: 60}, label="MAX LOAD")(labelled, _result())
        assert len(_painted_rows(labelled)) > len(_painted_rows(plain))

    def test_it_names_itself_with_its_geometry(self):
        """El aviso del motor tiene que decir cuál de los annotators falló."""
        assert "60" in max_load_line_annotator({_SLOT: 60}).__name__


class TestChain:
    def test_it_runs_every_annotator(self):
        calls = []
        first = lambda frame_bgr, result: calls.append("first")
        second = lambda frame_bgr, result: calls.append("second")
        chain(first, second)(_frame(), _result())
        assert calls == ["first", "second"]

    def test_the_last_one_draws_on_top(self):
        frame = _frame()
        chain(lambda frame_bgr, result: frame_bgr[0, 0].fill(1),
              lambda frame_bgr, result: frame_bgr[0, 0].fill(2))(frame, _result())
        assert tuple(frame[0, 0]) == (2, 2, 2)

    def test_every_annotator_leaves_its_mark(self):
        frame = _frame()
        chain(max_load_line_annotator({_SLOT: 30}, label=""),
              max_load_line_annotator({_SLOT: 90}, label=""))(frame, _result())
        rows = _painted_rows(frame)
        assert 30 in rows and 90 in rows
        assert rows <= _near(30) | _near(90)

    def test_an_empty_chain_draws_nothing(self):
        frame = _frame()
        chain()(frame, _result())
        assert _painted_rows(frame) == set()

    def test_none_entries_are_skipped(self):
        """Deja armar la cadena con annotators opcionales sin filtrar en el llamador."""
        calls = []
        chain(None, lambda frame_bgr, result: calls.append(1))(_frame(), _result())
        assert calls == [1]

    def test_it_names_the_annotators_it_carries(self):
        name = chain(max_load_line_annotator({_SLOT: 60})).__name__
        assert "chain(" in name and "max_load_line" in name


class TestLabelsPanel:
    def test_a_result_with_labels_gets_a_panel(self):
        frame = _frame()
        result = InferenceResult(camera_slot=_SLOT, labels={"grain_flow": "flow"})
        labels_panel_annotator()(frame, result)
        assert _painted_rows(frame)

    def test_a_result_without_labels_draws_nothing(self):
        """En una cámara con varios pipelines el panel lo pone sólo el que tiene veredicto."""
        frame = _frame()
        labels_panel_annotator()(frame, _result())
        assert _painted_rows(frame) == set()



_GRAIN_PIPELINE = "pipeline_1"
_PERSON_PIPELINE = "pipeline_2"
_GREEN = (40, 200, 40)
_RED = (40, 40, 230)
_GRAY = (170, 170, 170)


def _status(*, person_cameras: set[str] | None = None, zones: dict | None = None):
    return unload_status_annotator(flow_class="flow", person_pipelines={_PERSON_PIPELINE},
                                   person_cameras=person_cameras if person_cameras is not None
                                   else {_SLOT},
                                   exclusion_zone_px_by_camera=zones)


def _grain(verdict: str, *, timestamp_s: float = 100.0,
           is_valid: bool = True) -> InferenceResult:
    return InferenceResult(camera_slot=_SLOT, pipeline_slot=_GRAIN_PIPELINE,
                           labels={"grain_flow": verdict}, timestamp_s=timestamp_s,
                           is_valid=is_valid)


def _person_box(x1: int, y1: int, x2: int, y2: int) -> Detection:
    return Detection(class_index=0, class_name="person", confidence_pct=90.0,
                     bbox_px=(x1, y1, x2, y2))


def _persons(*boxes: Detection, timestamp_s: float = 100.0,
             is_valid: bool = True) -> InferenceResult:
    return InferenceResult(camera_slot=_SLOT, pipeline_slot=_PERSON_PIPELINE,
                           detections=list(boxes), timestamp_s=timestamp_s,
                           is_valid=is_valid)


def _border(frame_bgr: np.ndarray) -> tuple[int, int, int]:
    return tuple(int(value) for value in frame_bgr[60, 0])


def _panel_height_px(frame_bgr: np.ndarray) -> int:
    """Filas negras del panel, medidas sobre una columna que el texto no toca."""
    return int(np.count_nonzero(np.all(frame_bgr[:, 12] == 0, axis=1)))


class TestUnloadStatus:
    def test_grain_and_no_person_is_green(self):
        annotator, frame = _status(), _frame()
        annotator(_frame(), _persons())
        annotator(frame, _grain("flow"))
        assert _border(frame) == _GREEN

    @pytest.mark.parametrize("verdict", ["no_flow", "none"])
    def test_no_grain_is_red(self, verdict):
        annotator, frame = _status(), _frame()
        annotator(_frame(), _persons())
        annotator(frame, _grain(verdict))
        assert _border(frame) == _RED

    def test_a_person_in_the_frame_is_red_even_with_grain(self):
        annotator, frame = _status(), _frame()
        annotator(_frame(), _grain("flow"))
        annotator(frame, _persons(_person_box(50, 20, 70, 90)))
        assert _border(frame) == _RED

    def test_a_person_and_no_grain_is_red(self):
        annotator, frame = _status(), _frame()
        annotator(_frame(), _grain("no_flow"))
        annotator(frame, _persons(_person_box(50, 20, 70, 90)))
        assert _border(frame) == _RED

    def test_the_verdict_is_the_same_whichever_result_draws(self):
        """El anotado lo puede poner cualquiera de los dos pipelines de la cámara."""
        annotator = _status()
        from_grain, from_persons = _frame(), _frame()
        annotator(from_persons, _persons(_person_box(50, 20, 70, 90)))
        annotator(from_grain, _grain("flow"))
        annotator(from_persons, _persons(_person_box(50, 20, 70, 90), timestamp_s=100.1))
        assert _border(from_grain) == _border(from_persons) == _RED

    def test_only_the_feet_inside_the_zone_count(self):
        zone = [[100, 0], [199, 0], [199, 119], [100, 119]]
        annotator = _status(zones={_SLOT: zone})
        annotator(_frame(), _grain("flow"))
        outside, inside = _frame(), _frame()
        # La caja cruza la zona, pero los pies quedan afuera.
        annotator(outside, _persons(_person_box(40, 20, 120, 90)))
        assert _border(outside) == _GREEN
        annotator(inside, _persons(_person_box(110, 20, 150, 90), timestamp_s=100.1))
        assert _border(inside) == _RED

    def test_the_zone_is_drawn(self):
        zone = [[100, 30], [180, 30], [180, 100], [100, 100]]
        annotator, frame = _status(zones={_SLOT: zone}), _frame()
        annotator(frame, _persons())
        assert np.all(frame[65, 100] != 128)

    def test_a_malformed_zone_stops_the_build(self):
        with pytest.raises(ValueError, match=_SLOT):
            _status(zones={_SLOT: [[0, 0], [10, 10]]})

    def test_without_a_person_verdict_it_is_red(self):
        """Un pipeline de personas caído no puede dar verde."""
        annotator, frame = _status(), _frame()
        annotator(frame, _grain("flow"))
        assert _border(frame) == _RED

    def test_a_stale_person_verdict_does_not_count(self):
        annotator, frame = _status(), _frame()
        annotator(_frame(), _persons(timestamp_s=100.0))
        annotator(frame, _grain("flow", timestamp_s=105.0))
        assert _border(frame) == _RED

    def test_a_camera_without_person_detection_follows_the_grain(self):
        annotator, frame = _status(person_cameras=set()), _frame()
        annotator(frame, _grain("flow"))
        assert _border(frame) == _GREEN

    def test_without_a_grain_verdict_there_is_no_border(self):
        frame = _frame()
        _status()(frame, _persons())
        assert _painted_rows(frame) == set()

    def test_an_invalid_result_updates_nothing(self):
        annotator, frame = _status(), _frame()
        annotator(_frame(), _persons())
        annotator(_frame(), _grain("flow"))
        annotator(frame, _grain("no_flow", timestamp_s=100.1, is_valid=False))
        assert _border(frame) == _GREEN

    def test_the_border_goes_around_the_whole_frame(self):
        annotator, frame = _status(person_cameras=set()), _frame()
        annotator(frame, _grain("flow"))
        for row, column in [(0, 100), (119, 100), (60, 0), (60, 199)]:
            assert np.all(frame[row, column] != 128)
        assert np.all(frame[60, 100] == 128)

    def test_the_panel_is_drawn_on_top_of_the_border(self):
        annotator, frame = _status(person_cameras=set()), _frame()
        annotator(frame, _grain("flow"))
        assert np.all(frame[11, 12] == 0)

    def test_the_panel_adds_the_person_line_where_persons_are_detected(self):
        with_persons, without_persons = _frame(), _frame()
        _status()(with_persons, _grain("flow"))
        _status(person_cameras=set())(without_persons, _grain("flow"))
        assert _panel_height_px(with_persons) > _panel_height_px(without_persons)

    def test_an_unknown_person_verdict_is_gray_in_the_panel(self):
        """En un frame grande: el trazo es grueso y su centro sale sin antialiasing."""
        annotator, frame = _status(), np.full((720, 1280, 3), 128, np.uint8)
        annotator(frame, _grain("flow"))
        assert np.any(np.all(frame == _GRAY, axis=2))

    def test_the_text_grows_with_the_frame(self):
        """La cámara puede llegar reducida por el decodificador: el estado se lee igual."""
        small = np.full((360, 640, 3), 128, np.uint8)
        large = np.full((1440, 2560, 3), 128, np.uint8)
        _status(person_cameras=set())(small, _grain("flow"))
        _status(person_cameras=set())(large, _grain("flow"))
        assert _panel_height_px(large) > 2 * _panel_height_px(small)

    def test_an_empty_frame_is_ignored(self):
        _status()(np.zeros((0, 0, 3), np.uint8), _grain("flow"))

    def test_it_names_itself_with_its_settings(self):
        assert "flow" in _status().__name__
