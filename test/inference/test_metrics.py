"""Tests de las métricas del proyecto: las genéricas, la señal del grano y cómo se les
suman las de un extra."""

import pytest

from system.inference.metrics import build_analyzer, compute_metrics
from system.inference.result import Detection, InferenceResult


def _result() -> InferenceResult:
    detection = Detection(class_index=0, class_name="person", confidence_pct=90.0,
                          bbox_px=(0, 0, 10, 10))
    return InferenceResult(camera_slot="camera_1", detections=[detection])


class TestBuildAnalyzer:
    def test_without_extras_it_is_the_generic_count(self):
        assert build_analyzer() is compute_metrics

    def test_the_extras_are_added_to_the_generic_metrics(self):
        analyze = build_analyzer(lambda result: {"extra_flag": 1})
        assert analyze(_result()) == {**compute_metrics(_result()), "extra_flag": 1}

    def test_an_extra_overrides_a_generic_key(self):
        analyze = build_analyzer(lambda result: {"detection_count": 99})
        assert analyze(_result())["detection_count"] == 99


class TestGrainFlow:
    def test_flow_is_one(self):
        result = InferenceResult(camera_slot="camera_3", labels={"grain_flow": "flow"})
        assert compute_metrics(result)["flow_ok"] == 1

    @pytest.mark.parametrize("verdict", ["no_flow", "none"])
    def test_anything_else_is_zero(self, verdict):
        result = InferenceResult(camera_slot="camera_3", labels={"grain_flow": verdict})
        assert compute_metrics(result)["flow_ok"] == 0

    def test_a_result_without_the_verdict_has_no_signal(self):
        assert "flow_ok" not in compute_metrics(_result())
