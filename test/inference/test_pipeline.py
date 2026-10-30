"""Tests de la maquinaria del pipeline: carga de las etapas por slot, tiempos por etapa,
encadenado en cascada y el pipeline de una etapa del template.

Los modelos son dobles: acá se verifica el andamio de las etapas, no un framework.
"""

import numpy as np
import pytest

from system.inference.models.abstract_model import AbstractModel, InputSpec
from system.inference.abstract_pipeline import AbstractPipeline
from system.inference.pipeline import (
    GrainFlowPipeline, PersonPipeline, Pipeline, StopperPipeline,
)
from system.inference.result import (
    ROI_STATE_NOK, ROI_STATE_NONE, ROI_STATE_OK, Detection,
)

_PIPELINE = "pipeline_1"
_SLOT = "camera_1"


class _MockConfig:
    """ConfigManager mínimo: `get` con clave punteada, sin archivo ni Lock."""

    def __init__(self, **sections):
        self._values = {}
        for model_slot, section in sections.items():
            self._values[f"inference.models.{model_slot}"] = section
            for key, value in section.items():
                self._values[f"inference.models.{model_slot}.{key}"] = value

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


class _FakeModel(AbstractModel):
    """Modelo programable: registra qué frame y qué detecciones previas le llegaron."""

    def __init__(self, config_manager, model_slot: str, *, loads: bool = True):
        super().__init__(config_manager, model_slot)
        self._loads = loads
        self.calls: list[tuple] = []

    def load(self):
        if self._loads:
            self._set_loaded()
        else:
            self._set_error("pesos ausentes")

    def predict(self, frame_bgr, previous=None) -> list[Detection]:
        self.calls.append((frame_bgr.shape, list(previous or [])))
        return [Detection(class_index=0, class_name=self.model_slot,
                          confidence_pct=90.0, bbox_px=(0, 0, 10, 10))]

    def unload(self):
        self._status = "unloaded"


def _frame(width_px: int = 64, height_px: int = 48) -> np.ndarray:
    return np.full((height_px, width_px, 3), 128, np.uint8)


def _install_models(monkeypatch, models: dict):
    """Reemplaza la fábrica: la elección de la clase concreta se testea en su propio test."""
    import system.inference.abstract_pipeline as ap
    monkeypatch.setattr(ap, "create_model", lambda config, model_slot: models[model_slot])


class _LabellingPipeline(AbstractPipeline):
    """Deja un veredicto de frame y saltea la segunda etapa cuando no hace falta."""

    model_slots = ("classifier", "segmenter")

    def _run(self, frame_bgr, camera_slot):
        verdict = self._run_stage("classifier", frame_bgr)
        self._set_label("belt", "full" if verdict else "empty")
        if not verdict:
            return []
        return self._run_stage("segmenter", frame_bgr)


class _TwoStagePipeline(AbstractPipeline):
    """Cascada: la segunda etapa recibe lo que encontró la primera."""

    model_slots = ("detector", "classifier")

    def _run(self, frame_bgr, camera_slot):
        detections = self._run_stage("detector", frame_bgr)
        return self._run_stage("classifier", frame_bgr, detections)


class TestLoading:
    def test_it_loads_one_model_per_declared_slot(self, monkeypatch):
        config = _MockConfig(model_1={"type": "mock"})
        models = {"model_1": _FakeModel(config, "model_1")}
        _install_models(monkeypatch, models)
        pipeline = Pipeline(config, _PIPELINE)
        pipeline.load()
        assert pipeline.is_loaded is True
        assert set(pipeline.get_status()["models"]) == {"model_1"}

    def test_loading_twice_does_not_reload(self, monkeypatch):
        config = _MockConfig(model_1={"type": "mock"})
        created = []
        import system.inference.abstract_pipeline as ap
        monkeypatch.setattr(ap, "create_model", lambda config, model_slot:
                            created.append(model_slot) or _FakeModel(config, model_slot))
        pipeline = Pipeline(config, _PIPELINE)
        pipeline.load()
        pipeline.load()
        assert created == ["model_1"]

    def test_a_stage_that_could_not_load_leaves_the_pipeline_unloaded(self, monkeypatch):
        config = _MockConfig(model_1={"type": "mock"})
        _install_models(monkeypatch, {"model_1": _FakeModel(config, "model_1", loads=False)})
        pipeline = Pipeline(config, _PIPELINE)
        pipeline.load()
        assert pipeline.is_loaded is False
        assert pipeline.get_status()["models"]["model_1"]["error"] == "pesos ausentes"

    def test_a_pipeline_without_stages_is_never_loaded(self):
        class _EmptyPipeline(AbstractPipeline):
            def _run(self, frame_bgr, camera_slot):
                return []

        pipeline = _EmptyPipeline(_MockConfig(), _PIPELINE)
        pipeline.load()
        assert pipeline.is_loaded is False

    def test_unload_releases_every_stage(self, monkeypatch):
        config = _MockConfig(model_1={"type": "mock"})
        _install_models(monkeypatch, {"model_1": _FakeModel(config, "model_1")})
        pipeline = Pipeline(config, _PIPELINE)
        pipeline.load()
        pipeline.run(_frame(), _SLOT)
        pipeline.unload()
        assert pipeline.get_status()["models"] == {}
        assert (pipeline.stage_times_ms, pipeline.labels) == ({}, {})


class TestRunning:
    def test_the_template_pipeline_runs_its_single_stage(self, monkeypatch):
        config = _MockConfig(model_1={"type": "mock"})
        model = _FakeModel(config, "model_1")
        _install_models(monkeypatch, {"model_1": model})
        pipeline = Pipeline(config, _PIPELINE)
        pipeline.load()
        detections = pipeline.run(_frame(), _SLOT)
        assert [d.class_name for d in detections] == ["model_1"]
        assert len(model.calls) == 1

    def test_a_stage_without_a_model_returns_nothing(self, monkeypatch):
        config = _MockConfig(model_1={"type": "mock"})
        _install_models(monkeypatch, {"model_1": _FakeModel(config, "model_1", loads=False)})
        pipeline = Pipeline(config, _PIPELINE)
        pipeline.load()
        assert pipeline.run(_frame(), _SLOT) == []

    def test_each_stage_gets_its_own_time(self, monkeypatch):
        config = _MockConfig(detector={"type": "mock"}, classifier={"type": "mock"})
        _install_models(monkeypatch, {"detector": _FakeModel(config, "detector"),
                                      "classifier": _FakeModel(config, "classifier")})
        pipeline = _TwoStagePipeline(config, _PIPELINE)
        pipeline.load()
        pipeline.run(_frame(), _SLOT)
        assert set(pipeline.stage_times_ms) == {"detector", "classifier"}

    def test_the_times_are_those_of_the_last_run(self, monkeypatch):
        config = _MockConfig(detector={"type": "mock"}, classifier={"type": "mock"})
        _install_models(monkeypatch, {"detector": _FakeModel(config, "detector"),
                                      "classifier": _FakeModel(config, "classifier")})
        pipeline = _TwoStagePipeline(config, _PIPELINE)
        pipeline.load()
        pipeline.run(_frame(), _SLOT)
        pipeline._models.pop("classifier")
        pipeline.run(_frame(), _SLOT)
        assert set(pipeline.stage_times_ms) == {"detector"}

    def test_a_cascade_hands_the_previous_detections_over(self, monkeypatch):
        """Es lo que hace posible detectar y después clasificar cada detección."""
        config = _MockConfig(detector={"type": "mock"}, classifier={"type": "mock"})
        classifier = _FakeModel(config, "classifier")
        _install_models(monkeypatch, {"detector": _FakeModel(config, "detector"),
                                      "classifier": classifier})
        pipeline = _TwoStagePipeline(config, _PIPELINE)
        pipeline.load()
        pipeline.run(_frame(), _SLOT)
        _, previous = classifier.calls[0]
        assert [d.class_name for d in previous] == ["detector"]

    def test_the_first_stage_gets_no_previous_detections(self, monkeypatch):
        config = _MockConfig(model_1={"type": "mock"})
        model = _FakeModel(config, "model_1")
        _install_models(monkeypatch, {"model_1": model})
        pipeline = Pipeline(config, _PIPELINE)
        pipeline.load()
        pipeline.run(_frame(), _SLOT)
        assert model.calls[0][1] == []

    def test_a_failing_stage_propagates_to_the_caller(self, monkeypatch):
        """El motor es el que decide qué hacer con el error, no el pipeline."""
        config = _MockConfig(model_1={"type": "mock"})
        model = _FakeModel(config, "model_1")
        model.predict = lambda frame_bgr, previous=None: (_ for _ in ()).throw(RuntimeError("cuda"))
        _install_models(monkeypatch, {"model_1": model})
        pipeline = Pipeline(config, _PIPELINE)
        pipeline.load()
        with pytest.raises(RuntimeError):
            pipeline.run(_frame(), _SLOT)
        assert "model_1" in pipeline.stage_times_ms

    def test_a_stage_can_leave_a_frame_verdict(self, monkeypatch):
        config = _MockConfig(classifier={"type": "mock"}, segmenter={"type": "mock"})
        _install_models(monkeypatch, {"classifier": _FakeModel(config, "classifier"),
                                      "segmenter": _FakeModel(config, "segmenter")})
        pipeline = _LabellingPipeline(config, _PIPELINE)
        pipeline.load()
        pipeline.run(_frame(), _SLOT)
        assert pipeline.labels == {"belt": "full"}

    def test_a_skipped_stage_leaves_the_verdict_that_skipped_it(self, monkeypatch):
        """La etapa que no corrió no deja tiempo, pero se ve por qué no corrió."""
        config = _MockConfig(classifier={"type": "mock"}, segmenter={"type": "mock"})
        classifier = _FakeModel(config, "classifier")
        classifier.predict = lambda frame_bgr, previous=None: []
        _install_models(monkeypatch, {"classifier": classifier,
                                      "segmenter": _FakeModel(config, "segmenter")})
        pipeline = _LabellingPipeline(config, _PIPELINE)
        pipeline.load()
        assert pipeline.run(_frame(), _SLOT) == []
        assert pipeline.labels == {"belt": "empty"}
        assert set(pipeline.stage_times_ms) == {"classifier"}

    def test_a_verdict_does_not_survive_the_next_frame(self, monkeypatch):
        config = _MockConfig(classifier={"type": "mock"}, segmenter={"type": "mock"})
        classifier = _FakeModel(config, "classifier")
        _install_models(monkeypatch, {"classifier": classifier,
                                      "segmenter": _FakeModel(config, "segmenter")})
        pipeline = _LabellingPipeline(config, _PIPELINE)
        pipeline.load()
        pipeline.run(_frame(), _SLOT)
        classifier.predict = lambda frame_bgr, previous=None: []
        pipeline.run(_frame(), _SLOT)
        assert pipeline.labels == {"belt": "empty"}

    def test_the_labels_are_a_copy(self, monkeypatch):
        config = _MockConfig(classifier={"type": "mock"}, segmenter={"type": "mock"})
        _install_models(monkeypatch, {"classifier": _FakeModel(config, "classifier"),
                                      "segmenter": _FakeModel(config, "segmenter")})
        pipeline = _LabellingPipeline(config, _PIPELINE)
        pipeline.load()
        pipeline.run(_frame(), _SLOT)
        pipeline.labels["belt"] = "empty"
        assert pipeline.labels == {"belt": "full"}

    def test_a_synthetic_stage_marks_the_pipeline(self, monkeypatch):
        config = _MockConfig(model_1={"type": "mock"})
        model = _FakeModel(config, "model_1")
        model.is_synthetic = True
        _install_models(monkeypatch, {"model_1": model})
        pipeline = Pipeline(config, _PIPELINE)
        pipeline.load()
        assert pipeline.is_synthetic is True


class _ScriptedModel(_FakeModel):
    """Modelo que devuelve las detecciones que el test le dicta."""

    def __init__(self, config_manager, model_slot: str, detections: list[Detection]):
        super().__init__(config_manager, model_slot)
        self._detections = detections

    def predict(self, frame_bgr, previous=None) -> list[Detection]:
        return list(self._detections)


def _detection(class_name: str, confidence_pct: float) -> Detection:
    return Detection(class_index=0, class_name=class_name, confidence_pct=confidence_pct,
                     bbox_px=(0, 0, 10, 10))


class TestProjectPipelines:
    """Los pipelines de esta instalación: grano, personas y calzas."""

    def _loaded(self, monkeypatch, pipeline_class, model_slot: str,
                detections: list[Detection]):
        config = _MockConfig(**{model_slot: {"type": "mock"}})
        _install_models(monkeypatch,
                        {model_slot: _ScriptedModel(config, model_slot, detections)})
        pipeline = pipeline_class(config, _PIPELINE)
        pipeline.load()
        return pipeline

    def test_grain_flow_is_a_label_with_the_most_confident_class(self, monkeypatch):
        pipeline = self._loaded(monkeypatch, GrainFlowPipeline, "grain_cls",
                                [_detection("no_flow", 30.0), _detection("flow", 80.0)])
        assert pipeline.run(_frame(), _SLOT) == []
        assert pipeline.labels == {"grain_flow": "flow"}

    def test_grain_flow_without_detections_says_none(self, monkeypatch):
        pipeline = self._loaded(monkeypatch, GrainFlowPipeline, "grain_cls", [])
        pipeline.run(_frame(), _SLOT)
        assert pipeline.labels == {"grain_flow": "none"}

    def test_grain_flow_paints_the_roi_ok_when_grain_flows(self, monkeypatch):
        pipeline = self._loaded(monkeypatch, GrainFlowPipeline, "grain_cls",
                                [_detection("no_flow", 30.0), _detection("flow", 80.0)])
        pipeline.run(_frame(), _SLOT)
        assert pipeline.roi_state == ROI_STATE_OK

    def test_grain_flow_paints_the_roi_nok_when_it_does_not(self, monkeypatch):
        pipeline = self._loaded(monkeypatch, GrainFlowPipeline, "grain_cls",
                                [_detection("no_flow", 80.0), _detection("flow", 30.0)])
        pipeline.run(_frame(), _SLOT)
        assert pipeline.roi_state == ROI_STATE_NOK

    def test_grain_flow_without_a_verdict_leaves_the_roi_alone(self, monkeypatch):
        pipeline = self._loaded(monkeypatch, GrainFlowPipeline, "grain_cls", [])
        pipeline.run(_frame(), _SLOT)
        assert pipeline.roi_state == ROI_STATE_NONE

    def test_grain_flow_with_an_unknown_class_leaves_the_roi_alone(self, monkeypatch):
        pipeline = self._loaded(monkeypatch, GrainFlowPipeline, "grain_cls",
                                [_detection("dust", 90.0)])
        pipeline.run(_frame(), _SLOT)
        assert pipeline.roi_state == ROI_STATE_NONE

    @pytest.mark.parametrize("pipeline_class, model_slot", [
        (StopperPipeline, "stopper_seg"),
    ])
    def test_a_one_stage_pipeline_returns_what_its_model_found(self, monkeypatch,
                                                               pipeline_class, model_slot):
        found = [_detection("x", 90.0)]
        pipeline = self._loaded(monkeypatch, pipeline_class, model_slot, found)
        assert [d.class_name for d in pipeline.run(_frame(), _SLOT)] == ["x"]

    def test_the_person_pipeline_keeps_only_persons(self, monkeypatch):
        """Un YOLO genérico también detecta autos y camiones: esas cajas no se dibujan."""
        found = [_detection("person", 90.0), _detection("car", 90.0),
                 _detection("class_7", 90.0)]
        pipeline = self._loaded(monkeypatch, PersonPipeline, "person_det", found)
        assert [d.class_name for d in pipeline.run(_frame(), _SLOT)] == ["person"]


class _VerdictPipeline(AbstractPipeline):
    """Declara el estado del recuadro según lo que encontró."""

    model_slots = ("model_1",)

    def __init__(self, config, pipeline_slot, state=ROI_STATE_NOK):
        super().__init__(config, pipeline_slot)
        self._state = state

    def _run(self, frame_bgr, camera_slot):
        detections = self._run_stage("model_1", frame_bgr)
        self._set_roi_state(self._state if detections else ROI_STATE_NONE)
        return detections


def _verdict_pipeline(monkeypatch, state=ROI_STATE_NOK) -> _VerdictPipeline:
    config = _MockConfig(model_1={"type": "mock"})
    _install_models(monkeypatch, {"model_1": _FakeModel(config, "model_1")})
    pipeline = _VerdictPipeline(config, _PIPELINE, state)
    pipeline.load()
    return pipeline


class TestRoiState:
    def test_a_pipeline_that_says_nothing_leaves_the_roi_without_a_state(self, monkeypatch):
        config = _MockConfig(classifier={"type": "mock"}, segmenter={"type": "mock"})
        _install_models(monkeypatch, {"classifier": _FakeModel(config, "classifier"),
                                      "segmenter": _FakeModel(config, "segmenter")})
        pipeline = _LabellingPipeline(config, _PIPELINE)
        pipeline.load()
        pipeline.run(_frame(), _SLOT)
        assert pipeline.roi_state == ROI_STATE_NONE

    def test_the_pipeline_declares_it_next_to_its_labels(self, monkeypatch):
        pipeline = _verdict_pipeline(monkeypatch)
        pipeline.run(_frame(), _SLOT)
        assert pipeline.roi_state == ROI_STATE_NOK

    def test_a_state_does_not_survive_the_next_frame(self, monkeypatch):
        pipeline = _verdict_pipeline(monkeypatch)
        pipeline.run(_frame(), _SLOT)
        pipeline._models["model_1"].predict = lambda frame_bgr, previous=None: []
        pipeline.run(_frame(), _SLOT)
        assert pipeline.roi_state == ROI_STATE_NONE

    def test_a_value_outside_the_vocabulary_is_ignored(self, monkeypatch):
        pipeline = _verdict_pipeline(monkeypatch, state="purple")
        pipeline.run(_frame(), _SLOT)
        assert pipeline.roi_state == ROI_STATE_NONE

    def test_unloading_clears_it(self, monkeypatch):
        pipeline = _verdict_pipeline(monkeypatch)
        pipeline.run(_frame(), _SLOT)
        pipeline.unload()
        assert pipeline.roi_state == ROI_STATE_NONE


class TestTemplateDemo:
    """El pipeline del template pinta el recuadro por el veredicto sólo con un modelo sintético."""

    def _pipeline(self, monkeypatch, *, synthetic: bool, detects: bool = True) -> Pipeline:
        config = _MockConfig(model_1={"type": "mock"})
        model = _FakeModel(config, "model_1")
        model.is_synthetic = synthetic
        if not detects:
            model.predict = lambda frame_bgr, previous=None: []
        _install_models(monkeypatch, {"model_1": model})
        pipeline = Pipeline(config, _PIPELINE)
        pipeline.load()
        return pipeline

    def test_a_synthetic_model_with_detections_is_ok(self, monkeypatch):
        pipeline = self._pipeline(monkeypatch, synthetic=True)
        pipeline.run(_frame(), _SLOT)
        assert pipeline.roi_state == ROI_STATE_OK

    def test_a_synthetic_model_without_detections_is_nok(self, monkeypatch):
        pipeline = self._pipeline(monkeypatch, synthetic=True, detects=False)
        pipeline.run(_frame(), _SLOT)
        assert pipeline.roi_state == ROI_STATE_NOK

    def test_a_real_model_never_sets_a_state(self, monkeypatch):
        """Con uno real el estado lo decide la lógica del proceso, no esta demostración."""
        pipeline = self._pipeline(monkeypatch, synthetic=False)
        pipeline.run(_frame(), _SLOT)
        assert pipeline.roi_state == ROI_STATE_NONE


class _PeekingModel(AbstractModel):
    """Un modelo que sabe su entrada antes de cargarse."""

    def __init__(self, config_manager, model_slot: str, spec):
        super().__init__(config_manager, model_slot)
        self._spec = spec

    def load(self):
        raise AssertionError("preguntar por la entrada no carga el modelo")

    def predict(self, frame_bgr, previous=None):
        return []

    def unload(self):
        pass

    def peek_input(self):
        return self._spec


class TestViewInput:
    """El primer modelo del pipeline dice de qué tamaño quiere la región, sin cargarse."""

    def _pipeline(self, monkeypatch, pipeline_class, specs: dict):
        import system.inference.abstract_pipeline as ap
        config = _MockConfig()
        monkeypatch.setattr(ap, "create_model", lambda config, model_slot:
                            _PeekingModel(config, model_slot, specs.get(model_slot)))
        return pipeline_class(config, _PIPELINE)

    def test_it_is_the_input_of_the_first_stage(self, monkeypatch):
        spec = InputSpec(224, 224, True)
        pipeline = self._pipeline(monkeypatch, _TwoStagePipeline,
                                  {"detector": spec, "classifier": InputSpec(64, 64, True)})
        assert pipeline.view_model_slot == "detector"
        assert pipeline.peek_view_input() == spec

    def test_a_pipeline_can_name_another_stage(self, monkeypatch):
        class _Second(_TwoStagePipeline):
            @property
            def view_model_slot(self):
                return "classifier"

        spec = InputSpec(64, 64, True)
        pipeline = self._pipeline(monkeypatch, _Second, {"classifier": spec})
        assert pipeline.peek_view_input() == spec

    def test_a_model_that_does_not_say_gives_none(self, monkeypatch):
        assert self._pipeline(monkeypatch, _TwoStagePipeline, {}).peek_view_input() is None

    def test_it_does_not_load_any_stage(self, monkeypatch):
        pipeline = self._pipeline(monkeypatch, _TwoStagePipeline,
                                  {"detector": InputSpec(224, 224, True)})
        pipeline.peek_view_input()
        assert not pipeline.is_loaded

    def test_a_pipeline_without_stages_has_none(self):
        class _Empty(AbstractPipeline):
            def _run(self, frame_bgr, camera_slot):
                return []

        pipeline = _Empty(_MockConfig(), _PIPELINE)
        assert pipeline.view_model_slot == "" and pipeline.peek_view_input() is None

    def test_a_model_that_knows_nothing_says_none_by_default(self):
        class _Plain(AbstractModel):
            def load(self):
                pass

            def predict(self, frame_bgr, previous=None):
                return []

            def unload(self):
                pass

        assert _Plain(_MockConfig(), "model_1").peek_input() is None
