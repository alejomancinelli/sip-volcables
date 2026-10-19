"""Tests de los annotators del proyecto: el límite de carga por cámara y la composición
de varios en uno.

Módulo puro: los annotators reciben la geometría ya resuelta, así que acá no hay config
ni frames de cámara.
"""

import numpy as np
import pytest

from system.inference.annotations import (
    chain, labels_panel_annotator, max_load_line_annotator, stopper_status_annotator,
    timestamp_annotator, unload_status_annotator,
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


def _timestamp_height_px(frame_bgr: np.ndarray) -> int:
    """Filas negras del fondo de la hora, sobre la última columna del frame."""
    return int(np.count_nonzero(np.all(frame_bgr[:, -1] == 0, axis=1)))


class TestTimestamp:
    def test_it_draws_in_the_bottom_right_corner(self):
        frame = _frame()
        timestamp_annotator()(frame, _result())
        assert np.all(frame[-1, -1] == 0)
        assert np.all(frame[0, 0] == 128)

    def test_it_grows_with_the_frame(self):
        small = np.full((360, 640, 3), 128, np.uint8)
        large = np.full((1440, 2560, 3), 128, np.uint8)
        timestamp_annotator()(small, _result())
        timestamp_annotator()(large, _result())
        assert _timestamp_height_px(large) > 2 * _timestamp_height_px(small)

    def test_it_is_taller_than_the_status_border(self):
        """Encima del borde y más alto que él: si no, queda escondida adentro."""
        frame = np.full((1440, 2560, 3), 128, np.uint8)
        chain(_status(person_cameras=set()), timestamp_annotator())(frame, _grain("flow"))
        border_px = int(np.count_nonzero(np.any(frame[:, 1280] != 128, axis=1))) // 2
        assert _timestamp_height_px(frame) > border_px

    def test_an_empty_frame_is_ignored(self):
        timestamp_annotator()(np.zeros((0, 0, 3), np.uint8), _result())


_STOPPER_PIPELINE = "pipeline_3"
_RAISED_Y = 120


def _stopper_frame() -> np.ndarray:
    return np.full((240, 320, 3), 128, np.uint8)


def _box(class_name: str, x1: int, y1: int, x2: int, y2: int) -> Detection:
    """Una detección rectangular con su máscara llena, de (x1, y1) a (x2, y2) incluidos."""
    return Detection(class_index=0, class_name=class_name, confidence_pct=90.0,
                     bbox_px=(x1, y1, x2 + 1, y2 + 1),
                     mask=np.ones((y2 - y1 + 1, x2 - x1 + 1), np.uint8))


def _raised_stopper() -> Detection:
    """20 x 61 px, con sus 20 filas de arriba —400 px— por encima de la línea."""
    return _box("stopper", 200, 100, 219, 160)


def _wheel() -> Detection:
    """A 11 px de la calza: su borde derecho en x = 189."""
    return _box("wheel", 120, 90, 189, 200)


def _stoppers(*detections: Detection, is_valid: bool = True,
              pipeline_slot: str = _STOPPER_PIPELINE) -> InferenceResult:
    return InferenceResult(camera_slot=_SLOT, pipeline_slot=pipeline_slot,
                           detections=list(detections), is_valid=is_valid)


def _stopper_status(*, levels: dict | None = None, min_raised: dict | None = None,
                    max_gaps: dict | None = None, sides: dict | None = None,
                    language: str = "es"):
    return stopper_status_annotator(
        stopper_pipelines={_STOPPER_PIPELINE}, stopper_class="stopper", wheel_class="wheel",
        raised_y_px_by_camera={_SLOT: _RAISED_Y} if levels is None else levels,
        min_raised_px_by_camera=min_raised,
        max_gap_px_by_camera={_SLOT: 20} if max_gaps is None else max_gaps,
        wheel_side_by_camera=sides,
        language=language)


def _stopper_border(frame_bgr: np.ndarray) -> tuple[int, int, int]:
    return tuple(int(value) for value in frame_bgr[180, 0])


class TestStopperStatus:
    def test_a_raised_stopper_against_the_wheel_is_green(self):
        frame = _stopper_frame()
        _stopper_status()(frame, _stoppers(_raised_stopper(), _wheel()))
        assert _stopper_border(frame) == _GREEN

    def test_a_stopper_below_the_line_is_red(self):
        frame = _stopper_frame()
        _stopper_status()(frame, _stoppers(_box("stopper", 200, 130, 219, 160), _wheel()))
        assert _stopper_border(frame) == _RED

    def test_too_few_pixels_above_the_line_is_red(self):
        frame = _stopper_frame()
        _stopper_status(min_raised={_SLOT: 401})(frame, _stoppers(_raised_stopper(), _wheel()))
        assert _stopper_border(frame) == _RED

    def test_a_wheel_farther_than_the_maximum_is_red(self):
        frame = _stopper_frame()
        _stopper_status(max_gaps={_SLOT: 10})(frame, _stoppers(_raised_stopper(), _wheel()))
        assert _stopper_border(frame) == _RED

    def test_without_a_stopper_it_is_red(self):
        frame = _stopper_frame()
        _stopper_status()(frame, _stoppers(_wheel()))
        assert _stopper_border(frame) == _RED

    def test_without_a_wheel_it_is_red(self):
        frame = _stopper_frame()
        _stopper_status()(frame, _stoppers(_raised_stopper()))
        assert _stopper_border(frame) == _RED

    @pytest.mark.parametrize("missing", ["levels", "max_gaps"])
    def test_an_uncalibrated_camera_is_red(self, missing):
        """Lo que nadie calibró no se afirma, aunque la calza y la rueda estén bien."""
        frame = _stopper_frame()
        _stopper_status(**{missing: {}})(frame, _stoppers(_raised_stopper(), _wheel()))
        assert _stopper_border(frame) == _RED

    def test_an_uncalibrated_verdict_is_gray_in_the_panel(self):
        """En un frame grande: el trazo es grueso y su centro sale sin antialiasing."""
        frame = np.full((720, 1280, 3), 128, np.uint8)
        _stopper_status(levels={})(frame, _stoppers())
        assert np.any(np.all(frame == _GRAY, axis=2))

    def test_the_gap_is_drawn_between_the_two_masks(self):
        """Entre la rueda (x = 189) y la calza (x = 200): sólo la rueda cambia esa franja."""
        with_wheel, without_wheel = _stopper_frame(), _stopper_frame()
        _stopper_status()(with_wheel, _stoppers(_raised_stopper(), _wheel()))
        _stopper_status()(without_wheel, _stoppers(_raised_stopper()))
        assert not np.array_equal(with_wheel[95:166, 191:198],
                                  without_wheel[95:166, 191:198])

    def test_before_the_stopper_is_raised_the_wheel_changes_nothing(self):
        """La distancia sólo importa con la calza en posición: antes ni se mide ni se dibuja."""
        lowered = _box("stopper", 200, 130, 219, 160)
        with_wheel, without_wheel = _stopper_frame(), _stopper_frame()
        _stopper_status()(with_wheel, _stoppers(lowered, _wheel()))
        _stopper_status()(without_wheel, _stoppers(lowered))
        assert np.array_equal(with_wheel, without_wheel)

    def test_before_the_stopper_is_raised_the_contact_is_gray(self):
        """En un frame grande: el trazo es grueso y su centro sale sin antialiasing."""
        frame = np.full((720, 1280, 3), 128, np.uint8)
        _stopper_status()(frame, _stoppers(_box("stopper", 200, 130, 219, 160), _wheel()))
        assert np.any(np.all(frame == _GRAY, axis=2))

    def test_without_a_calibrated_height_the_contact_is_not_judged(self):
        """Sin altura no se sabe si está levantada, así que tampoco se juzga el contacto."""
        with_wheel, without_wheel = _stopper_frame(), _stopper_frame()
        _stopper_status(levels={})(with_wheel, _stoppers(_raised_stopper(), _wheel()))
        _stopper_status(levels={})(without_wheel, _stoppers(_raised_stopper()))
        assert np.array_equal(with_wheel, without_wheel)

    def test_the_height_is_drawn_across_the_whole_width(self):
        frame = _stopper_frame()
        _stopper_status()(frame, _stoppers())
        assert np.all(frame[_RAISED_Y, 40] != 128) and np.all(frame[_RAISED_Y, 280] != 128)

    def test_another_pipeline_draws_nothing(self):
        """El annotator es uno para todos los motores: el grano no lleva el borde de calzas."""
        frame = _stopper_frame()
        _stopper_status()(frame, _stoppers(_raised_stopper(), _wheel(),
                                           pipeline_slot="pipeline_1"))
        assert _painted_rows(frame) == set()

    def test_an_invalid_result_draws_nothing(self):
        frame = _stopper_frame()
        _stopper_status()(frame, _stoppers(_raised_stopper(), _wheel(), is_valid=False))
        assert _painted_rows(frame) == set()

    @pytest.mark.parametrize("y_px", [-1, 12.5, "120", True, [[0, 120], [319, 120]]])
    def test_a_height_that_is_not_a_row_stops_the_build(self, y_px):
        with pytest.raises(ValueError, match=_SLOT):
            _stopper_status(levels={_SLOT: y_px})

    @pytest.mark.parametrize("side", ["left", "any"])
    def test_a_wheel_on_the_configured_side_counts(self, side):
        frame = _stopper_frame()
        _stopper_status(sides={_SLOT: side})(frame, _stoppers(_raised_stopper(), _wheel()))
        assert _stopper_border(frame) == _GREEN

    def test_a_wheel_only_on_the_other_side_is_red(self):
        """La rueda está a la izquierda de la calza: con `right` la calza está mal puesta."""
        frame = _stopper_frame()
        _stopper_status(sides={_SLOT: "right"})(frame, _stoppers(_raised_stopper(), _wheel()))
        assert _stopper_border(frame) == _RED

    def test_the_side_picks_the_wheel_among_two(self):
        """Dos ejes a la vista: la rueda pegada del lado equivocado no cuenta."""
        far_right = _box("wheel", 260, 90, 319, 200)       # a 40 px, a la derecha
        touching_left = _box("wheel", 150, 90, 199, 200)   # pegada, a la izquierda
        frame = _stopper_frame()
        _stopper_status(sides={_SLOT: "right"})(
            frame, _stoppers(_raised_stopper(), touching_left, far_right))
        assert _stopper_border(frame) == _RED

    def test_an_unknown_side_stops_the_build(self):
        with pytest.raises(ValueError, match=_SLOT):
            _stopper_status(sides={_SLOT: "up"})

    def test_the_labels_follow_the_language(self):
        spanish, english, unknown = _stopper_frame(), _stopper_frame(), _stopper_frame()
        result = _stoppers(_raised_stopper(), _wheel())
        _stopper_status(language="es")(spanish, result)
        _stopper_status(language="en")(english, result)
        _stopper_status(language="xx")(unknown, result)
        assert not np.array_equal(spanish, english)
        assert np.array_equal(spanish, unknown)
