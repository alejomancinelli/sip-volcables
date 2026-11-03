"""
Tests de los hooks del proyecto: qué le entrega el template al motor desde `process:`.

Un fork reescribe `build_hooks()` y con él estos tests. Lo que se prueba acá es el
template: no filtra, cuenta con el analyzer genérico y dibuja el límite de carga sólo si
alguna cámara lo declara.
"""

import numpy as np

from system.inference.hooks import EngineHooks, build_hooks, read_pipelines
from system.inference.metrics import compute_metrics
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
    """Calza levantada sobre y = 30 y pegada a la rueda: verde si todo está calibrado."""
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


def _border_of(hooks: EngineHooks) -> tuple[int, int, int]:
    frame = np.full((60, 80, 3), 128, np.uint8)
    hooks.annotator(frame, _stopper_result())
    return tuple(int(value) for value in frame[40, 0])


class TestStopperHooks:
    def test_a_stopper_pipeline_draws_the_stopper_status(self):
        assert _border_of(build_hooks(_stopper_config())) == (40, 200, 40)

    def test_without_a_stopper_pipeline_there_is_no_annotator(self):
        assert build_hooks(_stopper_config(**{"inference.pipelines": {}})).annotator is None

    def test_the_process_values_are_the_ones_measured(self):
        hooks = build_hooks(_stopper_config(**{"process.stopper_check.max_gap_px":
                                               {"camera_1": 1}}))
        assert _border_of(hooks) == (40, 40, 230)

    def test_the_wheel_side_is_read(self):
        """La rueda está a la izquierda de la calza: pedirla a la derecha da rojo."""
        hooks = build_hooks(_stopper_config(**{"process.stopper_check.wheel_side":
                                               {"camera_1": "right"}}))
        assert _border_of(hooks) == (40, 40, 230)

    def test_a_null_distance_is_uncalibrated(self):
        """La pestaña guarda «sin calibrar» como null: no puede dar verde."""
        hooks = build_hooks(_stopper_config(**{"process.stopper_check.max_gap_px":
                                               {"camera_1": None}}))
        assert _border_of(hooks) == (40, 40, 230)


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
