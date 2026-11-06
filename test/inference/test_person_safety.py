"""
Tests del veredicto de personas: quién cuenta en la zona, y que el analyzer filtre por par
(pipeline, cámara) y dé 1 sólo sin nadie confirmado.
"""

import pytest

from system.inference.person_safety import (
    SAFETY_METRIC, build_analyzer, count_in_zone, parse_zones,
)
from system.inference.result import Detection, InferenceResult

_PIPELINE = "pipeline_2"
_CAMERA = "camera_3"
_OTHER_CAMERA = "camera_4"
_ZONE = [[100, 0], [199, 0], [199, 119], [100, 119]]


def _person(x1: int, y1: int, x2: int, y2: int) -> Detection:
    return Detection(class_index=0, class_name="person", confidence_pct=90.0,
                     bbox_px=(x1, y1, x2, y2))


def _result(*detections: Detection, timestamp_s: float = 100.0, camera_slot: str = _CAMERA,
            pipeline_slot: str = _PIPELINE) -> InferenceResult:
    return InferenceResult(camera_slot=camera_slot, pipeline_slot=pipeline_slot,
                           detections=list(detections), timestamp_s=timestamp_s)


def _analyzer(**overrides):
    settings = {"person_pipelines": {_PIPELINE}, "confirm_hits": 2, "confirm_window": 3,
                "release_s": 1.0}
    settings.update(overrides)
    return build_analyzer(**settings)


class TestZone:
    def test_only_the_feet_inside_the_zone_count(self):
        zone = parse_zones({_CAMERA: _ZONE})[_CAMERA]
        # La primera cruza la zona, pero los pies quedan afuera.
        assert count_in_zone([_person(40, 20, 120, 90), _person(110, 20, 150, 90)], zone) == 1

    def test_without_a_zone_anyone_in_the_frame_counts(self):
        assert count_in_zone([_person(0, 0, 10, 10), _person(20, 0, 30, 10)], None) == 2
        assert count_in_zone([], None) == 0

    def test_a_malformed_zone_stops_the_build(self):
        with pytest.raises(ValueError, match=_CAMERA):
            parse_zones({_CAMERA: [[0, 0], [10, 10]]})

    @pytest.mark.parametrize("points", [None, []])
    def test_a_cleared_zone_is_no_zone(self, points):
        """Es lo que deja el editor al borrarla: la cámara cuenta todo el frame."""
        assert parse_zones({_CAMERA: points}) == {}


class TestAnalyzer:
    def test_nobody_is_ok(self):
        assert _analyzer()(_result())[SAFETY_METRIC] == 1

    def test_a_single_frame_with_a_person_is_still_ok(self):
        metrics = _analyzer()(_result(_person(0, 0, 10, 10)))
        assert metrics[SAFETY_METRIC] == 1
        assert metrics["in_zone_count"] == 1

    def test_a_confirmed_person_is_zero(self):
        analyze = _analyzer()
        analyze(_result(_person(0, 0, 10, 10), timestamp_s=100.0))
        metrics = analyze(_result(_person(0, 0, 10, 10), timestamp_s=100.1))
        assert metrics[SAFETY_METRIC] == 0

    def test_it_stays_zero_until_the_release_time_passes(self):
        analyze = _analyzer(release_s=1.0)
        for timestamp_s in (100.0, 100.1):
            analyze(_result(_person(0, 0, 10, 10), timestamp_s=timestamp_s))
        held = analyze(_result(timestamp_s=100.9))
        released = analyze(_result(timestamp_s=101.2))
        assert held[SAFETY_METRIC] == 0 and held["in_zone_count"] == 0
        assert released[SAFETY_METRIC] == 1

    def test_each_camera_has_its_own_filter(self):
        analyze = _analyzer()
        analyze(_result(_person(0, 0, 10, 10), timestamp_s=100.0))
        metrics = analyze(_result(_person(0, 0, 10, 10), timestamp_s=100.1,
                                  camera_slot=_OTHER_CAMERA))
        assert metrics[SAFETY_METRIC] == 1

    def test_the_counts_split_the_frame_from_the_zone(self):
        analyze = _analyzer(zone_by_camera=parse_zones({_CAMERA: _ZONE}))
        metrics = analyze(_result(_person(40, 20, 60, 90), _person(110, 20, 150, 90)))
        assert metrics["in_frame_count"] == 2 and metrics["in_zone_count"] == 1

    def test_a_person_outside_the_zone_does_not_count(self):
        analyze = _analyzer(zone_by_camera=parse_zones({_CAMERA: _ZONE}))
        for timestamp_s in (100.0, 100.1, 100.2):
            metrics = analyze(_result(_person(40, 20, 60, 90), timestamp_s=timestamp_s))
        assert metrics[SAFETY_METRIC] == 1

    def test_another_pipeline_gets_no_person_metrics(self):
        assert _analyzer()(_result(pipeline_slot="pipeline_1")) == {}

    def test_an_impossible_filter_stops_the_build(self):
        with pytest.raises(ValueError):
            _analyzer(confirm_hits=4, confirm_window=3)
