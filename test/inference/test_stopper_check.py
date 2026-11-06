"""
Tests de la regla de las calzas como señales: 1 OK y 0 cualquier otra cosa. El dibujo de la
misma regla se prueba en test_annotations.py.
"""

import numpy as np
import pytest

from system.inference.result import Detection, InferenceResult
from system.inference.stopper_check import StopperRule, build_analyzer

_PIPELINE = "pipeline_3"
_CAMERA = "camera_1"


def _stopper() -> Detection:
    """Máscara de las filas 20 a 49: 100 px por encima de la fila 30."""
    return Detection(class_index=0, class_name="stopper", confidence_pct=90.0,
                     bbox_px=(60, 20, 70, 50), mask=np.ones((30, 10), np.uint8))


def _wheel() -> Detection:
    """A la izquierda de la calza, a unos 3 px."""
    return Detection(class_index=1, class_name="wheel", confidence_pct=90.0,
                     bbox_px=(30, 20, 58, 55), mask=np.ones((35, 28), np.uint8))


def _analyze(*detections: Detection, pipeline_slot: str = _PIPELINE, **calibration) -> dict:
    settings = {"raised_y_px_by_camera": {_CAMERA: 30}, "min_raised_px_by_camera": {_CAMERA: 1},
                "max_gap_px_by_camera": {_CAMERA: 5}}
    settings.update(calibration)
    rule = StopperRule(stopper_class="stopper", wheel_class="wheel", **settings)
    analyze = build_analyzer(rule, stopper_pipelines={_PIPELINE})
    return analyze(InferenceResult(camera_slot=_CAMERA, pipeline_slot=pipeline_slot,
                                   detections=list(detections)))


class TestSignals:
    def test_raised_and_touching_is_one_in_both(self):
        assert _analyze(_stopper(), _wheel()) == {"raised_ok": 1, "contact_ok": 1}

    def test_without_a_stopper_both_are_zero(self):
        assert _analyze(_wheel()) == {"raised_ok": 0, "contact_ok": 0}

    def test_a_raised_stopper_far_from_the_wheel_has_no_contact(self):
        assert _analyze(_stopper(), _wheel(),
                        max_gap_px_by_camera={_CAMERA: 1}) == {"raised_ok": 1, "contact_ok": 0}

    def test_an_uncalibrated_height_is_zero_in_both(self):
        """Lo que nadie calibró no se da por bueno."""
        assert _analyze(_stopper(), _wheel(),
                        raised_y_px_by_camera={}) == {"raised_ok": 0, "contact_ok": 0}

    def test_an_uncalibrated_gap_is_zero(self):
        assert _analyze(_stopper(), _wheel(),
                        max_gap_px_by_camera={}) == {"raised_ok": 1, "contact_ok": 0}

    def test_a_wheel_on_the_wrong_side_is_no_contact(self):
        assert _analyze(_stopper(), _wheel(), wheel_side_by_camera={_CAMERA: "right"}) == \
            {"raised_ok": 1, "contact_ok": 0}

    def test_another_pipeline_gets_no_stopper_signals(self):
        assert _analyze(_stopper(), _wheel(), pipeline_slot="pipeline_1") == {}


class TestRule:
    @pytest.mark.parametrize("y_px", [-1, 1.5, True])
    def test_a_height_that_is_not_a_frame_row_stops_the_build(self, y_px):
        with pytest.raises(ValueError, match=_CAMERA):
            StopperRule(stopper_class="stopper", wheel_class="wheel",
                        raised_y_px_by_camera={_CAMERA: y_px})

    def test_an_unknown_wheel_side_stops_the_build(self):
        with pytest.raises(ValueError, match=_CAMERA):
            StopperRule(stopper_class="stopper", wheel_class="wheel",
                        wheel_side_by_camera={_CAMERA: "up"})
