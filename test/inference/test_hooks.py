"""
Tests de los hooks del proyecto: qué le entrega el template al motor desde `process:`.

Un fork reescribe `build_hooks()` y con él estos tests. Lo que se prueba acá es el
template: no filtra, cuenta con el analyzer genérico y dibuja el límite de carga sólo si
alguna cámara lo declara.
"""

import numpy as np
import pytest

from system.inference.hooks import EngineHooks, build_hooks, read_pipelines
from system.inference.metrics import compute_metrics
from system.inference.person_safety import SAFETY_METRIC
from system.inference.result import Detection, InferenceResult


class _MockConfig:
    """ConfigManager mínimo: `get` con clave punteada, sin archivo ni Lock."""

    def __init__(self, **overrides):
        self._values = dict(overrides)

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


class TestTemplateHooks:
    def test_it_returns_the_hooks_object(self):
        assert isinstance(build_hooks(_MockConfig()), EngineHooks)

    def test_the_template_does_not_filter_detections(self):
        assert build_hooks(_MockConfig()).classifier is None

    def test_the_analyzer_is_the_generic_count(self):
        assert build_hooks(_MockConfig()).analyzer is compute_metrics

    def test_without_a_load_limit_there_is_no_annotator(self):
        """Sin nada que dibujar el motor no gasta una llamada por frame."""
        assert build_hooks(_MockConfig()).annotator is None

    def test_a_declared_load_limit_is_drawn_on_its_camera(self):
        hooks = build_hooks(_MockConfig(**{"process.max_load_y_px": {"camera_1": 30}}))
        frame = np.zeros((60, 80, 3), np.uint8)
        hooks.annotator(frame, InferenceResult(camera_slot="camera_1"))
        assert frame[30].any()

    def test_a_declared_grain_flow_draws_the_verdict_panel(self):
        hooks = build_hooks(_MockConfig(**{"process.grain_flow": {}}))
        frame = np.zeros((60, 80, 3), np.uint8)
        hooks.annotator(frame, InferenceResult(camera_slot="camera_1",
                                               labels={"grain_flow": "flow"}))
        assert frame.any()

    def test_without_the_engine_timestamp_the_project_draws_it(self):
        hooks = build_hooks(_MockConfig(**{"inference.overlay.draw_timestamp": False}))
        frame = np.zeros((60, 80, 3), np.uint8) + 128
        hooks.annotator(frame, InferenceResult(camera_slot="camera_1"))
        assert np.all(frame[-1, -1] == 0)

    def test_with_the_engine_timestamp_the_project_does_not_draw_it(self):
        """Una sola clave decide quién la dibuja: nunca salen las dos."""
        assert build_hooks(_MockConfig(**{"inference.overlay.draw_timestamp": True})).annotator is None

    def test_the_load_limit_is_not_drawn_on_other_cameras(self):
        hooks = build_hooks(_MockConfig(**{"process.max_load_y_px": {"camera_1": 30}}))
        frame = np.zeros((60, 80, 3), np.uint8)
        hooks.annotator(frame, InferenceResult(camera_slot="camera_2"))
        assert not frame.any()


_STOPPER_PIPELINES = {"pipeline_3": {"type": "stopper_check", "cameras": ["camera_1"]}}


def _stopper_result() -> InferenceResult:
    """Calza levantada sobre y = 30 y pegada a la rueda: OK en los dos si está calibrado."""
    stopper = Detection(class_index=0, class_name="stopper", confidence_pct=90.0,
                        bbox_px=(60, 20, 70, 50), mask=np.ones((30, 10), np.uint8))
    wheel = Detection(class_index=1, class_name="wheel", confidence_pct=90.0,
                      bbox_px=(30, 20, 58, 55), mask=np.ones((35, 28), np.uint8))
    return InferenceResult(camera_slot="camera_1", pipeline_slot="pipeline_3",
                           detections=[stopper, wheel])


def _stopper_config(**overrides) -> _MockConfig:
    values = {
        "inference.pipelines": _STOPPER_PIPELINES,
        "process.stopper_check.raised_y_px": {"camera_1": 30},
        "process.stopper_check.min_raised_px": {"camera_1": 20},
        "process.stopper_check.max_gap_px": {"camera_1": 5},
    }
    values.update(overrides)
    return _MockConfig(**values)


def _drawn_by(hooks: EngineHooks) -> np.ndarray:
    frame = np.full((60, 80, 3), 128, np.uint8)
    hooks.annotator(frame, _stopper_result())
    return frame


class TestStopperHooks:
    """
    El annotator de calzas no lleva borde de estado —lo decide el PLC—, así que estos tests
    no leen un veredicto: comparan el dibujo con el de otro config, y un valor que se lee es
    uno que lo cambia.
    """

    def test_a_stopper_pipeline_draws_the_stopper_status(self):
        assert np.any(_drawn_by(build_hooks(_stopper_config())) != 128)

    def test_without_a_stopper_pipeline_there_is_no_annotator(self):
        assert build_hooks(_stopper_config(**{"inference.pipelines": {}})).annotator is None

    def test_the_process_values_are_the_ones_measured(self):
        hooks = build_hooks(_stopper_config(**{"process.stopper_check.max_gap_px":
                                               {"camera_1": 1}}))
        assert not np.array_equal(_drawn_by(hooks), _drawn_by(build_hooks(_stopper_config())))

    def test_the_wheel_side_is_read(self):
        """La rueda está a la izquierda de la calza: pedirla a la derecha cambia el contacto."""
        hooks = build_hooks(_stopper_config(**{"process.stopper_check.wheel_side":
                                               {"camera_1": "right"}}))
        assert not np.array_equal(_drawn_by(hooks), _drawn_by(build_hooks(_stopper_config())))

    def test_a_null_distance_is_uncalibrated(self):
        """La pestaña guarda «sin calibrar» como null: se dibuja igual que sin la clave."""
        null = build_hooks(_stopper_config(**{"process.stopper_check.max_gap_px":
                                              {"camera_1": None}}))
        missing = build_hooks(_stopper_config(**{"process.stopper_check.max_gap_px": {}}))
        assert np.array_equal(_drawn_by(null), _drawn_by(missing))
        assert not np.array_equal(_drawn_by(null), _drawn_by(build_hooks(_stopper_config())))


_PERSON_PIPELINES = {"pipeline_2": {"type": "person_safety", "cameras": ["camera_3"]}}


def _person_hooks(**overrides) -> EngineHooks:
    values = {"cameras": {"camera_3": {}},
              "inference.pipelines": _PERSON_PIPELINES,
              "process.person_safety.confirm_hits": 1,
              "process.person_safety.confirm_window": 1}
    values.update(overrides)
    return build_hooks(_MockConfig(**values))


def _person_result(*detections: Detection) -> InferenceResult:
    return InferenceResult(camera_slot="camera_3", pipeline_slot="pipeline_2",
                           detections=list(detections))


_FEET_AT_100_80 = Detection(class_index=0, class_name="person", confidence_pct=90.0,
                            bbox_px=(90, 20, 110, 80))


class TestPersonHooks:
    def test_a_person_pipeline_adds_the_filtered_verdict(self):
        metrics = _person_hooks().analyzer(_person_result(_FEET_AT_100_80))
        assert metrics[SAFETY_METRIC] == 0
        assert metrics["detection_count"] == 1

    def test_the_filter_settings_are_read(self):
        hooks = _person_hooks(**{"process.person_safety.confirm_hits": 2,
                                 "process.person_safety.confirm_window": 2})
        metrics = hooks.analyzer(_person_result(_FEET_AT_100_80))
        assert metrics[SAFETY_METRIC] == 1

    def test_the_exclusion_zone_is_read(self):
        zone = {"camera_3": [[0, 0], [50, 0], [50, 50], [0, 50]]}
        hooks = _person_hooks(**{"process.person_safety.exclusion_zone_px": zone})
        metrics = hooks.analyzer(_person_result(_FEET_AT_100_80))
        assert metrics[SAFETY_METRIC] == 1

    def test_a_malformed_zone_stops_the_build(self):
        with pytest.raises(ValueError, match="camera_3"):
            _person_hooks(**{"process.person_safety.exclusion_zone_px":
                             {"camera_3": [[0, 0]]}})

    def test_an_impossible_filter_stops_the_build(self):
        with pytest.raises(ValueError):
            _person_hooks(**{"process.person_safety.confirm_hits": 3})


class TestStopperSignals:
    def test_a_stopper_pipeline_adds_its_signals(self):
        hooks = build_hooks(_MockConfig(**{
            "inference.pipelines": _STOPPER_PIPELINES,
            "process.stopper_check.raised_y_px": {"camera_1": 30},
            "process.stopper_check.min_raised_px": {"camera_1": 1},
            "process.stopper_check.max_gap_px": {"camera_1": 5},
        }))
        metrics = hooks.analyzer(_stopper_result())
        assert metrics["raised_ok"] == 1 and metrics["contact_ok"] == 1

    def test_uncalibrated_signals_are_zero(self):
        hooks = build_hooks(_MockConfig(**{"inference.pipelines": _STOPPER_PIPELINES}))
        metrics = hooks.analyzer(_stopper_result())
        assert metrics["raised_ok"] == 0 and metrics["contact_ok"] == 0


class TestReadPipelines:
    def _config(self, pipelines: dict) -> _MockConfig:
        return _MockConfig(**{"cameras": {"camera_1": {}, "camera_2": {}, "camera_3": {}},
                              "inference.pipelines": pipelines})

    def test_it_keeps_the_enabled_pipelines_of_that_type(self):
        config = self._config({
            "pipeline_1": {"type": "stopper_check", "cameras": ["camera_2"]},
            "pipeline_2": {"type": "person_safety", "cameras": ["camera_1"]},
            "pipeline_3": {"type": "stopper_check", "enabled": False, "cameras": ["camera_3"]},
        })
        assert read_pipelines(config, "stopper_check") == ({"pipeline_1"}, ["camera_2"])

    def test_the_cameras_come_in_the_config_order(self):
        config = self._config({
            "pipeline_1": {"type": "stopper_check", "cameras": ["camera_3", "camera_1"]},
        })
        assert read_pipelines(config, "stopper_check")[1] == ["camera_1", "camera_3"]

    def test_no_cameras_means_all_of_them(self):
        config = self._config({"pipeline_1": {"type": "stopper_check", "cameras": []}})
        assert read_pipelines(config, "stopper_check")[1] == ["camera_1", "camera_2", "camera_3"]
