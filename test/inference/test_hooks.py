"""
Tests de los hooks del proyecto: qué le entrega el template al motor desde `process:`.

Un fork reescribe `build_hooks()` y con él estos tests. Lo que se prueba acá es el
template: no filtra, cuenta con el analyzer genérico y dibuja el límite de carga sólo si
alguna cámara lo declara.
"""

import numpy as np

from system.inference.hooks import EngineHooks, build_hooks
from system.inference.metrics import compute_metrics
from system.inference.result import InferenceResult


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

    def test_the_load_limit_is_not_drawn_on_other_cameras(self):
        hooks = build_hooks(_MockConfig(**{"process.max_load_y_px": {"camera_1": 30}}))
        frame = np.zeros((60, 80, 3), np.uint8)
        hooks.annotator(frame, InferenceResult(camera_slot="camera_2"))
        assert not frame.any()
