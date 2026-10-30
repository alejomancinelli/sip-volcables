"""Tests del hilo de inferencia: fan-in de varias cámaras, always-fresh por cámara,
recorte del ROI, compuertas de validez, analyzer inyectado y parada limpia.

El pipeline es un doble programable: lo que se verifica acá es el ciclo del motor, no un
modelo. Sin GPU, sin cámara y sin event loop de Qt.
"""

import threading
import time

import numpy as np
import pytest
from PySide6.QtCore import Qt

from system.inference import overlay
from system.inference.abstract_pipeline import AbstractPipeline
from system.inference.engine import InferenceThread
from system.inference.result import (REASON_DARK_FRAME, REASON_ERROR, REASON_FEW_DETECTIONS,
                                     REASON_LOW_CONFIDENCE, REASON_NO_MODEL, REASON_NO_VIEW,
                                     Detection, InferenceResult)
from tools.camera.abstract_driver import FrameView

_PIPELINE = "pipeline_1"
_SLOT = "camera_1"
_OTHER_SLOT = "camera_2"
_WAIT_TIMEOUT_MS = 3000
_WAIT_TIMEOUT_S = 3.0


class _MockConfig:
    """ConfigManager mínimo: `get` con clave punteada, sin archivo ni Lock."""

    def __init__(self, **overrides):
        self._values = {
            f"inference.pipelines.{_PIPELINE}.enabled": True,
            f"inference.pipelines.{_PIPELINE}.cameras": [],
        }
        self._values.update(overrides)

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


class _FakePipeline(AbstractPipeline):
    """
    Pipeline programable: cada test decide qué detecta, cuánto tarda y qué rompe.

    Devuelve detecciones nuevas en cada corrida, porque el motor las corre in-place al
    espacio del frame de cámara y reusar los objetos acumularía el desplazamiento.
    """

    model_slots = ("model_1",)

    def __init__(self, specs: list[tuple] | None = None, *, loaded: bool = True,
                 synthetic: bool = False, raises: bool = False, delay_s: float = 0.0,
                 labels: dict | None = None):
        super().__init__(_MockConfig(), _PIPELINE)
        self._specs = list(specs or [])
        self._labels_to_set = dict(labels or {})
        self._loaded = loaded
        self._synthetic = synthetic
        self._raises = raises
        self._delay_s = delay_s

        self.frames: list[np.ndarray] = []
        self.load_calls = 0
        self.unload_calls = 0

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    @property
    def is_synthetic(self) -> bool:
        return self._synthetic

    def load(self):
        self.load_calls += 1

    def unload(self):
        self.unload_calls += 1

    def _run(self, frame_bgr: np.ndarray, camera_slot: str) -> list[Detection]:
        self.frames.append(frame_bgr)
        for name, value in self._labels_to_set.items():
            self._set_label(name, value)
        if self._raises:
            raise RuntimeError("etapa caída")
        if self._delay_s:
            time.sleep(self._delay_s)
        return [Detection(class_index=index, class_name=f"class_{index}",
                          confidence_pct=confidence_pct, bbox_px=bbox_px)
                for index, confidence_pct, bbox_px in self._specs]


class _Collector:
    """Junta los resultados que emite el hilo de inferencia."""

    def __init__(self):
        self._lock = threading.Lock()
        self.results = []

    def on_result(self, result):
        with self._lock:
            self.results.append(result)

    def count(self) -> int:
        with self._lock:
            return len(self.results)

    def camera_slots(self) -> list[str]:
        with self._lock:
            return sorted(result.camera_slot for result in self.results)

    def last(self):
        with self._lock:
            return self.results[-1]


def _frame(width_px: int = 320, height_px: int = 240, level: int = 128) -> np.ndarray:
    return np.full((height_px, width_px, 3), level, np.uint8)


def _engine(pipeline: _FakePipeline, config: _MockConfig | None = None,
            **kwargs) -> InferenceThread:
    return InferenceThread(config or _MockConfig(), pipeline, **kwargs)


def _wait_until(condition, timeout_s: float = _WAIT_TIMEOUT_S) -> bool:
    deadline_s = time.monotonic() + timeout_s
    while time.monotonic() < deadline_s:
        if condition():
            return True
        time.sleep(0.01)
    return False


def _run_frames(engine: InferenceThread, frames: list[tuple], expected: int = 1) -> _Collector:
    """
    Corre el hilo, pushea (frame, slot) y espera `expected` resultados.

    Conecta la señal en directo: en la app la entrega el event loop de Qt, que en los
    tests no corre.
    """
    collector = _Collector()
    engine.result_ready.connect(collector.on_result, Qt.ConnectionType.DirectConnection)
    engine.start()
    try:
        for frame, camera_slot in frames:
            engine.push_frame(frame, camera_slot)
        assert _wait_until(lambda: collector.count() >= expected), \
            f"se esperaban {expected} resultados y llegaron {collector.count()}"
    finally:
        engine.requestInterruption()
        assert engine.wait(_WAIT_TIMEOUT_MS)
    return collector


def _process_one(engine: InferenceThread, frame: np.ndarray, camera_slot: str = _SLOT):
    return _run_frames(engine, [(frame, camera_slot)]).last()


# ── Construcción y estado ────────────────────────────────────────────────────

class TestConstruction:
    def test_the_slot_comes_from_the_pipeline(self):
        assert _engine(_FakePipeline()).pipeline_slot == _PIPELINE

    def test_cameras_are_read_from_its_own_section(self):
        config = _MockConfig(**{f"inference.pipelines.{_PIPELINE}.cameras": [_SLOT, _OTHER_SLOT]})
        assert _engine(_FakePipeline(), config).camera_slots == (_SLOT, _OTHER_SLOT)

    def test_it_has_the_cameras_it_declares(self):
        config = _MockConfig(**{f"inference.pipelines.{_PIPELINE}.cameras": [_SLOT]})
        engine = _engine(_FakePipeline(), config)
        assert engine.has_camera(_SLOT) is True
        assert engine.has_camera(_OTHER_SLOT) is False

    def test_without_a_list_it_has_every_camera(self):
        assert _engine(_FakePipeline()).has_camera(_OTHER_SLOT) is True

    def test_asking_for_a_foreign_camera_does_not_warn(self, caplog):
        """Es la pregunta de quien reparte frames: el aviso es de `push_frame`."""
        config = _MockConfig(**{f"inference.pipelines.{_PIPELINE}.cameras": [_SLOT]})
        with caplog.at_level("WARNING"):
            _engine(_FakePipeline(), config).has_camera(_OTHER_SLOT)
        assert _OTHER_SLOT not in caplog.text

    def test_status_is_available_before_starting(self):
        status = _engine(_FakePipeline()).get_status()
        assert status["pipeline_slot"] == _PIPELINE
        assert status["running"] is False
        assert status["processed"] == 0

    def test_status_reports_the_models_of_the_pipeline(self):
        status = _engine(_FakePipeline()).get_status()
        assert "models" in status and status["loaded"] is True


# ── Cola de frames ───────────────────────────────────────────────────────────

class TestQueue:
    def test_an_empty_frame_is_ignored(self):
        engine = _engine(_FakePipeline())
        engine.push_frame(None, _SLOT)
        engine.push_frame(np.zeros((0, 0, 3), np.uint8), _SLOT)
        assert engine.get_status()["queued"] == 0

    def test_a_camera_outside_the_pipeline_is_rejected(self):
        config = _MockConfig(**{f"inference.pipelines.{_PIPELINE}.cameras": [_SLOT]})
        engine = _engine(_FakePipeline(), config)
        engine.push_frame(_frame(), _OTHER_SLOT)
        assert engine.get_status()["queued"] == 0

    def test_without_a_declared_list_every_camera_is_accepted(self):
        engine = _engine(_FakePipeline())
        engine.push_frame(_frame(), _OTHER_SLOT)
        assert engine.get_status()["queued"] == 1

    def test_a_disabled_pipeline_takes_no_frames(self):
        config = _MockConfig(**{f"inference.pipelines.{_PIPELINE}.enabled": False})
        engine = _engine(_FakePipeline(), config)
        engine.push_frame(_frame(), _SLOT)
        assert engine.get_status()["queued"] == 0

    def test_the_latest_frame_of_a_camera_wins(self):
        """Always-fresh: el frame sin consumir se descarta y se cuenta."""
        engine = _engine(_FakePipeline())
        engine.push_frame(_frame(level=10), _SLOT)
        engine.push_frame(_frame(level=200), _SLOT)
        status = engine.get_status()
        assert (status["queued"], status["dropped"]) == (1, 1)

    def test_frames_of_different_cameras_coexist(self):
        engine = _engine(_FakePipeline())
        engine.push_frame(_frame(), _SLOT)
        engine.push_frame(_frame(), _OTHER_SLOT)
        status = engine.get_status()
        assert (status["queued"], status["dropped"]) == (2, 0)

    def test_the_frame_is_kept_without_a_copy(self):
        """El driver entrega un array propio: copiarlo es memoria que se tira por frame."""
        pipeline = _FakePipeline()
        engine = _engine(pipeline)
        frame = _frame(level=10)
        engine.push_frame(frame, _SLOT)
        _run_frames(engine, [])
        assert np.shares_memory(pipeline.frames[0], frame)

    def test_the_turn_rotates_between_cameras(self):
        """Con el modelo más lento que la captura, una cámara no puede tapar a la otra."""
        engine = _engine(_FakePipeline())
        engine.push_frame(_frame(), _SLOT)
        engine.push_frame(_frame(), _OTHER_SLOT)
        taken = [engine._take_next()[0], engine._take_next()[0]]
        assert sorted(taken) == [_SLOT, _OTHER_SLOT]
        assert engine._take_next() is None

    def test_the_interval_drops_frames_that_arrive_early(self):
        config = _MockConfig(**{f"inference.pipelines.{_PIPELINE}.interval_s": 60})
        engine = _engine(_FakePipeline(), config)
        engine.push_frame(_frame(), _SLOT)
        engine.push_frame(_frame(), _SLOT)
        status = engine.get_status()
        assert (status["queued"], status["dropped"]) == (1, 0)

    def test_a_frame_waiting_its_turn_is_refreshed_within_the_interval(self):
        """Con otra cámara infiriendo, el modelo tiene que ver el frame más nuevo."""
        config = _MockConfig(**{f"inference.pipelines.{_PIPELINE}.interval_s": 60})
        engine = _engine(_FakePipeline(), config)
        engine.push_frame(_frame(level=10), _SLOT)
        engine.push_frame(_frame(level=200), _SLOT)
        status = engine.get_status()
        assert (status["queued"], status["dropped"]) == (1, 0)
        _, (frame, _) = engine._take_next()
        assert frame[0, 0, 0] == 200

    def test_a_consumed_frame_is_not_replaced_within_the_interval(self):
        config = _MockConfig(**{f"inference.pipelines.{_PIPELINE}.interval_s": 60})
        engine = _engine(_FakePipeline(), config)
        engine.push_frame(_frame(), _SLOT)
        engine._take_next()
        engine.push_frame(_frame(), _SLOT)
        assert engine.get_status()["queued"] == 0

    def test_ignore_interval_forces_the_frame(self):
        config = _MockConfig(**{f"inference.pipelines.{_PIPELINE}.interval_s": 60})
        engine = _engine(_FakePipeline(), config)
        engine.push_frame(_frame(), _SLOT)
        engine._take_next()
        engine.push_frame(_frame(), _SLOT, ignore_interval=True)
        assert engine.get_status()["queued"] == 1


# ── ROI e iluminación ────────────────────────────────────────────────────────

class _CountingConfig(_MockConfig):
    """`_MockConfig` con `revision`, como el real, que además cuenta los `get`."""

    def __init__(self, **overrides):
        super().__init__(**overrides)
        self.revision = 0
        self.reads = 0

    def get(self, key: str, default: object = None) -> object:
        self.reads += 1
        return super().get(key, default)

    def set(self, key: str, value: object):
        self._values[key] = value
        self.revision += 1


class TestConfigCache:
    """
    El config se lee una vez, no por frame: cada `get` es un lock global y una copia.

    Un cambio desde la pantalla mueve `revision`, y con eso se relee en el frame siguiente.
    """

    def test_frames_after_the_first_do_not_read_the_config(self):
        config = _CountingConfig()
        engine = _engine(_FakePipeline(), config)
        _process_one(engine, _frame())
        reads_after_first = config.reads
        for _ in range(5):
            _process_one(engine, _frame())
        assert config.reads == reads_after_first

    def test_a_change_takes_effect_on_the_next_frame(self):
        config = _CountingConfig()
        pipeline = _FakePipeline()
        engine = _engine(pipeline, config)
        _process_one(engine, _frame())
        config.set(f"inference.pipelines.{_PIPELINE}", {"roi": {_SLOT: {
            "enabled": True, "x_px": 20, "y_px": 10, "width_px": 100, "height_px": 50}}})
        _process_one(engine, _frame())
        assert pipeline.frames[-1].shape[:2] == (50, 100)


class TestRoiAndIllumination:
    def test_the_pipeline_receives_the_cropped_roi(self):
        config = _MockConfig(**{f"inference.pipelines.{_PIPELINE}": {"roi": {_SLOT: {"enabled": True, "x_px": 20, "y_px": 10, "width_px": 100, "height_px": 50}}}})
        pipeline = _FakePipeline()
        result = _process_one(_engine(pipeline, config), _frame())
        assert pipeline.frames[0].shape[:2] == (50, 100)
        assert result.roi_px == (20, 10, 100, 50)

    def test_detections_come_back_in_camera_coordinates(self):
        """El modelo trabaja en el espacio del recorte; el consumidor ve el frame entero."""
        config = _MockConfig(**{f"inference.pipelines.{_PIPELINE}": {"roi": {_SLOT: {"enabled": True, "x_px": 20, "y_px": 10, "width_px": 100, "height_px": 50}}}})
        pipeline = _FakePipeline([(0, 90.0, (5, 5, 15, 15))])
        result = _process_one(_engine(pipeline, config), _frame())
        assert result.detections[0].bbox_px == (25, 15, 35, 25)
        assert result.detections[0].centroid_px == (30, 20)

    def test_a_zero_size_roi_reaches_the_border(self):
        config = _MockConfig(**{f"inference.pipelines.{_PIPELINE}": {"roi": {_SLOT: {"enabled": True, "x_px": 120, "y_px": 40, "width_px": 0, "height_px": 0}}}})
        pipeline = _FakePipeline()
        _process_one(_engine(pipeline, config), _frame())
        assert pipeline.frames[0].shape[:2] == (200, 200)

    def test_a_disabled_roi_infers_the_whole_frame(self):
        config = _MockConfig(**_pipeline_roi(enabled=False))
        pipeline = _FakePipeline()
        result = _process_one(_engine(pipeline, config), _frame())
        assert pipeline.frames[0].shape[:2] == (240, 320)
        assert result.roi_px is None

    def test_a_roi_outside_the_frame_falls_back_to_the_whole_frame(self):
        config = _MockConfig(**{f"inference.pipelines.{_PIPELINE}": {"roi": {_SLOT: {"enabled": True, "x_px": 319, "y_px": 239, "width_px": 100, "height_px": 100}}}})
        pipeline = _FakePipeline()
        result = _process_one(_engine(pipeline, config), _frame())
        assert result.roi_px == (319, 239, 1, 1)
        assert pipeline.frames[0].shape[:2] == (1, 1)

    def test_illumination_is_measured_on_the_analyzed_region(self):
        """Es el brillo del ROI, no el del frame: es la zona que la luz ilumina."""
        config = _MockConfig(**{f"inference.pipelines.{_PIPELINE}": {"roi": {_SLOT: {"enabled": True, "x_px": 0, "y_px": 0, "width_px": 40, "height_px": 40}}}})
        frame = _frame(level=0)
        frame[0:40, 0:40] = 255
        assert _process_one(_engine(_FakePipeline(), config), frame).illumination_pct == 100

    def test_a_dark_frame_never_reaches_the_model(self):
        """Una imagen negra no vale una pasada del modelo, y su resultado no mide nada."""
        config = _MockConfig(**{f"cameras.{_SLOT}.illumination_min": 50})
        pipeline = _FakePipeline([(0, 90.0, (0, 0, 10, 10))])
        result = _process_one(_engine(pipeline, config), _frame(level=5))
        assert pipeline.frames == []
        assert (result.is_valid, result.invalid_reason) == (False, REASON_DARK_FRAME)
        assert result.detections == []


def _pipeline_roi(**overrides) -> dict:
    """La clave del ROI propio del pipeline para `_SLOT`, lista para `_MockConfig`."""
    roi = {"enabled": True, "x_px": 40, "y_px": 20, "width_px": 100, "height_px": 50}
    roi.update(overrides)
    return {f"inference.pipelines.{_PIPELINE}": {"roi": {_SLOT: roi}}}


class TestPipelineRoi:
    """El ROI de un pipeline es sólo de ese pipeline y de esa cámara."""

    def test_the_pipeline_crops_to_it(self):
        config = _MockConfig(**_pipeline_roi())
        pipeline = _FakePipeline()
        result = _process_one(_engine(pipeline, config), _frame())
        assert pipeline.frames[0].shape[:2] == (50, 100)
        assert result.roi_px == (40, 20, 100, 50)

    def test_disabled_it_gives_the_whole_frame(self):
        """Un `enabled: false` es ver el frame entero, aunque el resto de la sección esté escrita."""
        config = _MockConfig(**_pipeline_roi(enabled=False))
        pipeline = _FakePipeline()
        result = _process_one(_engine(pipeline, config), _frame())
        assert pipeline.frames[0].shape[:2] == (240, 320)
        assert result.roi_px is None

    def test_another_camera_keeps_its_own_roi(self):
        config = _MockConfig(**_pipeline_roi())
        result = _process_one(_engine(_FakePipeline(), config), _frame(), _OTHER_SLOT)
        assert result.roi_px is None

    def test_illumination_is_measured_on_its_region(self):
        config = _MockConfig(**_pipeline_roi(x_px=0, y_px=0, width_px=40, height_px=40))
        frame = _frame(level=0)
        frame[0:40, 0:40] = 255
        assert _process_one(_engine(_FakePipeline(), config), frame).illumination_pct == 100

    def test_the_result_carries_the_region_it_ran_on(self):
        config = _MockConfig(**_pipeline_roi())
        pipeline = _FakePipeline()
        result = _process_one(_engine(pipeline, config), _frame())
        assert result.region_bgr is pipeline.frames[0]


def _view(level: int = 200, *, width_px: int = 300, height_px: int = 150,
          rect_px: tuple = (40, 20, 100, 50)) -> FrameView:
    """Una vista a 3x del ROI de `_pipeline_roi()`, como la cortaría el decodificador."""
    return FrameView(np.full((height_px, width_px, 3), level, np.uint8), rect_px)


def _process_with_views(engine: InferenceThread, frame: np.ndarray, views: dict,
                        camera_slot: str = _SLOT) -> InferenceResult:
    collector = _Collector()
    engine.result_ready.connect(collector.on_result, Qt.ConnectionType.DirectConnection)
    engine.start()
    try:
        engine.push_frame(frame, camera_slot, views)
        assert _wait_until(lambda: collector.count() >= 1)
    finally:
        engine.requestInterruption()
        assert engine.wait(_WAIT_TIMEOUT_MS)
    return collector.last()


def _native_roi() -> dict:
    """El ROI propio del pipeline en una cámara `rtsp`: ahí la región se corta del nativo."""
    return {**_pipeline_roi(), f"cameras.{_SLOT}": {"driver": "rtsp"}}


class TestViews:
    """En una cámara que decodifica video, la región es la vista que el decodificador cortó del nativo."""

    def test_the_pipeline_receives_the_view(self):
        config = _MockConfig(**_native_roi())
        pipeline = _FakePipeline()
        view = _view()
        _process_with_views(_engine(pipeline, config), _frame(), {_PIPELINE: view})
        assert pipeline.frames[0] is view.image_bgr

    def test_the_roi_is_the_rectangle_the_view_covers(self):
        config = _MockConfig(**_native_roi())
        view = _view(rect_px=(40, 20, 101, 51))
        result = _process_with_views(_engine(_FakePipeline(), config), _frame(),
                                     {_PIPELINE: view})
        assert result.roi_px == (40, 20, 101, 51)

    def test_detections_come_back_scaled_to_the_reference_frame(self):
        """La vista está a 3x: una caja de la vista vuelve a un tercio, corrida al ROI."""
        config = _MockConfig(**_native_roi())
        pipeline = _FakePipeline([(0, 90.0, (30, 15, 60, 45))])
        result = _process_with_views(_engine(pipeline, config), _frame(), {_PIPELINE: _view()})
        assert result.detections[0].bbox_px == (50, 25, 60, 35)

    def test_illumination_is_measured_on_the_view(self):
        config = _MockConfig(**_native_roi())
        result = _process_with_views(_engine(_FakePipeline(), config), _frame(level=0),
                                     {_PIPELINE: _view(level=255)})
        assert result.illumination_pct == 100

    def test_the_result_carries_the_view_as_its_region(self):
        config = _MockConfig(**_native_roi())
        view = _view()
        result = _process_with_views(_engine(_FakePipeline(), config), _frame(),
                                     {_PIPELINE: view})
        assert result.region_bgr is view.image_bgr

    def test_the_source_frame_is_still_the_reference_frame(self):
        config = _MockConfig(**_native_roi())
        frame = _frame()
        result = _process_with_views(_engine(_FakePipeline(), config), frame,
                                     {_PIPELINE: _view()})
        assert result.source_bgr is frame

    def test_without_its_view_the_frame_is_not_inferred(self):
        """Inferir el recorte reducido daría números de otra imagen que la pedida."""
        config = _MockConfig(**_native_roi())
        pipeline = _FakePipeline([(0, 90.0, (0, 0, 10, 10))])
        result = _process_with_views(_engine(pipeline, config), _frame(), {})
        assert pipeline.frames == []
        assert (result.is_valid, result.invalid_reason) == (False, REASON_NO_VIEW)

    def test_without_its_view_the_roi_is_still_drawn(self):
        config = _MockConfig(**_native_roi())
        result = _process_with_views(_engine(_FakePipeline(), config), _frame(), {})
        assert result.roi_px == (40, 20, 100, 50)
        assert result.region_bgr is None

    def test_the_view_of_another_pipeline_is_ignored(self):
        config = _MockConfig(**_native_roi())
        result = _process_with_views(_engine(_FakePipeline(), config), _frame(),
                                     {"pipeline_9": _view()})
        assert result.invalid_reason == REASON_NO_VIEW

    def test_a_camera_that_does_not_cut_views_ignores_one_that_arrives(self):
        config = _MockConfig(**_pipeline_roi())
        pipeline = _FakePipeline()
        _process_with_views(_engine(pipeline, config), _frame(), {_PIPELINE: _view()})
        assert pipeline.frames[0].shape[:2] == (50, 100)

    def test_a_view_of_another_rectangle_is_measured_and_warned(self, caplog):
        """El corte lo fija el decodificador al abrir: hasta reiniciar manda la vista."""
        config = _MockConfig(**_native_roi())
        view = _view(rect_px=(0, 0, 100, 50))
        result = _process_with_views(_engine(_FakePipeline(), config), _frame(),
                                     {_PIPELINE: view})
        assert result.roi_px == (0, 0, 100, 50)
        assert "reiniciar" in caplog.text

    def test_process_takes_the_views_too(self):
        config = _MockConfig(**_native_roi())
        pipeline = _FakePipeline()
        view = _view()
        _engine(pipeline, config).process(_frame(), _SLOT, {_PIPELINE: view})
        assert pipeline.frames[0] is view.image_bgr


class TestPreprocessor:
    """El frame de referencia es lo que devuelve el preprocessor: el modelo y el overlay
    miden y dibujan sobre la misma imagen, así que no pueden divergir."""

    def test_it_receives_the_frame_and_the_camera_slot(self):
        seen = []

        def preprocessor(frame_bgr, camera_slot):
            seen.append((frame_bgr.shape, camera_slot))
            return frame_bgr

        _process_one(_engine(_FakePipeline(), preprocessor=preprocessor), _frame())
        assert seen == [((240, 320, 3), _SLOT)]

    def test_the_model_sees_what_it_returned(self):
        pipeline = _FakePipeline()
        engine = _engine(pipeline, preprocessor=lambda frame_bgr, camera_slot: _frame(level=7))
        _process_one(engine, _frame(level=200))
        assert int(pipeline.frames[0][0, 0, 0]) == 7

    def test_the_result_carries_it_as_the_source_frame(self):
        """Es el lienzo del overlay: si acá quedara el crudo, las cajas caerían corridas."""
        engine = _engine(_FakePipeline(),
                         preprocessor=lambda frame_bgr, camera_slot: _frame(level=7))
        result = _process_one(engine, _frame(level=200))
        assert int(result.source_bgr[0, 0, 0]) == 7

    def test_the_roi_is_read_on_the_preprocessed_frame(self):
        """Corregir el lente puede recortar: el ROI se acota contra la imagen corregida."""
        config = _MockConfig(**{f"inference.pipelines.{_PIPELINE}": {"roi": {_SLOT: {"enabled": True, "x_px": 0, "y_px": 0, "width_px": 200, "height_px": 200}}}})
        pipeline = _FakePipeline()
        engine = _engine(pipeline, config,
                         preprocessor=lambda frame_bgr, camera_slot: _frame(120, 100))
        result = _process_one(engine, _frame())
        assert result.roi_px == (0, 0, 120, 100)
        assert pipeline.frames[0].shape[:2] == (100, 120)

    def test_illumination_is_measured_on_the_preprocessed_frame(self):
        engine = _engine(_FakePipeline(),
                         preprocessor=lambda frame_bgr, camera_slot: _frame(level=255))
        assert _process_one(engine, _frame(level=0)).illumination_pct == 100

    def test_a_failing_preprocessor_falls_back_to_the_camera_frame(self):
        def broken_preprocessor(frame_bgr, camera_slot):
            raise ValueError("calibracion mal cargada")

        pipeline = _FakePipeline()
        engine = _engine(pipeline, preprocessor=broken_preprocessor)
        result = _process_one(engine, _frame(level=200))
        assert int(pipeline.frames[0][0, 0, 0]) == 200
        assert result.is_valid is True

    @pytest.mark.parametrize("returned", [None, np.zeros((0, 0, 3), np.uint8)])
    def test_an_empty_result_falls_back_to_the_camera_frame(self, returned):
        pipeline = _FakePipeline()
        engine = _engine(pipeline, preprocessor=lambda frame_bgr, camera_slot: returned)
        _process_one(engine, _frame(level=200))
        assert int(pipeline.frames[0][0, 0, 0]) == 200

    def test_without_a_preprocessor_the_camera_frame_is_measured(self):
        pipeline = _FakePipeline()
        _process_one(_engine(pipeline), _frame(level=200))
        assert int(pipeline.frames[0][0, 0, 0]) == 200


# ── Validez del resultado ────────────────────────────────────────────────────

class TestClassifier:
    def test_without_a_classifier_the_detections_pass_through(self):
        result = _process_one(_engine(_FakePipeline([(0, 90.0, (0, 0, 5, 5))])), _frame())
        assert result.detection_count == 1

    def test_it_receives_the_camera_slot(self):
        """Es la identidad que el modelo no tiene: uno solo sirve a todas las cámaras."""
        seen = []

        def classifier(detections, camera_slot):
            seen.append(camera_slot)
            return detections

        _process_one(_engine(_FakePipeline(), classifier=classifier), _frame(), _OTHER_SLOT)
        assert seen == [_OTHER_SLOT]

    def test_it_receives_the_detections_in_reference_frame_space(self):
        """Está calibrado en ese espacio: recibirlas en el del ROI las movería."""
        config = _MockConfig(**{f"inference.pipelines.{_PIPELINE}": {"roi": {_SLOT: {"enabled": True, "x_px": 20, "y_px": 10, "width_px": 60, "height_px": 40}}}})
        seen = []
        engine = _engine(_FakePipeline([(0, 90.0, (0, 0, 5, 5))]), config,
                         classifier=lambda detections, slot: seen.append(
                             detections[0].bbox_px) or detections)
        _process_one(engine, _frame())
        assert seen == [(20, 10, 25, 15)]

    def test_what_it_drops_does_not_reach_the_result(self):
        pipeline = _FakePipeline([(0, 90.0, (0, 0, 5, 5)), (1, 90.0, (6, 6, 10, 10))])
        engine = _engine(pipeline, classifier=lambda detections, slot: detections[:1])
        assert _process_one(engine, _frame()).detection_count == 1

    def test_what_it_drops_does_not_drag_the_confidence(self):
        """Filtrar después del promedio dejaría la confianza de detecciones descartadas."""
        pipeline = _FakePipeline([(0, 90.0, (0, 0, 5, 5)), (1, 10.0, (6, 6, 10, 10))])
        engine = _engine(pipeline, classifier=lambda detections, slot: detections[:1])
        assert _process_one(engine, _frame()).confidence_pct == 90.0

    def test_what_it_drops_does_not_count_for_min_detections(self):
        config = _MockConfig(**{f"inference.pipelines.{_PIPELINE}.min_detections": 2})
        pipeline = _FakePipeline([(0, 90.0, (0, 0, 5, 5)), (1, 90.0, (6, 6, 10, 10))])
        engine = _engine(pipeline, config, classifier=lambda detections, slot: detections[:1])
        result = _process_one(engine, _frame())
        assert (result.is_valid, result.invalid_reason) == (False, REASON_FEW_DETECTIONS)

    def test_the_class_it_stamps_travels_in_the_result(self):
        """Es lo que después colorea el overlay y cuenta `analysis.count_by_class`."""
        def classifier(detections, camera_slot):
            for detection in detections:
                detection.class_index = 3
                detection.class_name = "coarse"
            return detections

        engine = _engine(_FakePipeline([(0, 90.0, (0, 0, 5, 5))]), classifier=classifier)
        detection = _process_one(engine, _frame()).detections[0]
        assert (detection.class_index, detection.class_name) == (3, "coarse")

    def test_dropping_everything_leaves_no_detections(self):
        engine = _engine(_FakePipeline([(0, 90.0, (0, 0, 5, 5))]),
                         classifier=lambda detections, slot: None)
        assert _process_one(engine, _frame()).detections == []

    def test_a_failing_classifier_does_not_break_the_result(self):
        def broken_classifier(detections, camera_slot):
            raise ValueError("escala sin configurar")

        engine = _engine(_FakePipeline([(0, 90.0, (0, 0, 5, 5))]),
                         classifier=broken_classifier)
        result = _process_one(engine, _frame())
        assert (result.is_valid, result.detection_count) == (True, 1)


class TestValidity:
    def test_a_full_result_is_valid(self):
        result = _process_one(_engine(_FakePipeline([(0, 90.0, (0, 0, 10, 10))])), _frame())
        assert (result.is_valid, result.invalid_reason) == (True, "")

    def test_without_a_loaded_model_nothing_is_measured(self):
        pipeline = _FakePipeline([(0, 90.0, (0, 0, 10, 10))], loaded=False)
        result = _process_one(_engine(pipeline), _frame())
        assert pipeline.frames == []
        assert (result.is_valid, result.invalid_reason) == (False, REASON_NO_MODEL)

    def test_a_failing_stage_invalidates_the_result_without_killing_the_thread(self):
        result = _process_one(_engine(_FakePipeline(raises=True)), _frame())
        assert (result.is_valid, result.invalid_reason) == (False, REASON_ERROR)

    def test_too_few_detections_invalidate_the_result(self):
        config = _MockConfig(**{f"inference.pipelines.{_PIPELINE}.min_detections": 2})
        result = _process_one(_engine(_FakePipeline([(0, 90.0, (0, 0, 5, 5))]), config), _frame())
        assert (result.is_valid, result.invalid_reason) == (False, REASON_FEW_DETECTIONS)

    def test_low_average_confidence_invalidates_the_result(self):
        config = _MockConfig(
            **{f"inference.pipelines.{_PIPELINE}.min_result_confidence_pct": 80})
        pipeline = _FakePipeline([(0, 90.0, (0, 0, 5, 5)), (1, 50.0, (6, 6, 10, 10))])
        result = _process_one(_engine(pipeline, config), _frame())
        assert result.confidence_pct == 70.0
        assert (result.is_valid, result.invalid_reason) == (False, REASON_LOW_CONFIDENCE)

    def test_an_invalid_result_keeps_its_detections(self):
        """El anotado las muestra igual y el recolector de dataset puede filtrar por ellas."""
        config = _MockConfig(
            **{f"inference.pipelines.{_PIPELINE}.min_result_confidence_pct": 99})
        result = _process_one(_engine(_FakePipeline([(0, 90.0, (0, 0, 5, 5))]), config), _frame())
        assert result.detection_count == 1

    def test_confidence_is_the_average_of_the_detections(self):
        pipeline = _FakePipeline([(0, 90.0, (0, 0, 5, 5)), (1, 70.0, (6, 6, 10, 10))])
        assert _process_one(_engine(pipeline), _frame()).confidence_pct == 80.0

    def test_the_synthetic_marker_travels_in_the_result(self):
        result = _process_one(_engine(_FakePipeline(synthetic=True)), _frame())
        assert result.is_synthetic is True

    def test_invalid_results_are_counted(self):
        engine = _engine(_FakePipeline(raises=True))
        _run_frames(engine, [(_frame(), _SLOT)])
        status = engine.get_status()
        assert (status["processed"], status["invalid"]) == (1, 1)


# ── Analyzer inyectado ───────────────────────────────────────────────────────

class TestLabels:
    def test_the_verdict_of_the_pipeline_reaches_the_result(self):
        pipeline = _FakePipeline([(0, 90.0, (0, 0, 5, 5))], labels={"belt": "full"})
        assert _process_one(_engine(pipeline), _frame()).labels == {"belt": "full"}

    def test_without_labels_the_result_has_none(self):
        assert _process_one(_engine(_FakePipeline()), _frame()).labels == {}

    def test_a_frame_that_never_ran_has_no_labels(self):
        """Sin modelo no hay veredicto: no se arrastra el del frame anterior."""
        pipeline = _FakePipeline(labels={"belt": "full"}, loaded=False)
        assert _process_one(_engine(pipeline), _frame()).labels == {}

    def test_the_analyzer_can_read_the_labels(self):
        """Es como un veredicto de texto termina siendo un bit para el PLC."""
        pipeline = _FakePipeline([(0, 90.0, (0, 0, 5, 5))], labels={"belt": "full"})
        engine = _engine(pipeline,
                         analyzer=lambda result: {"belt_full": result.labels.get("belt") == "full"})
        assert _process_one(engine, _frame()).metrics == {"belt_full": True}


class TestAnalyzer:
    def test_the_analyzer_fills_the_metrics(self):
        engine = _engine(_FakePipeline([(0, 90.0, (0, 0, 5, 5))]),
                         analyzer=lambda result: {"count": result.detection_count})
        assert _process_one(engine, _frame()).metrics == {"count": 1}

    def test_without_an_analyzer_the_metrics_stay_empty(self):
        assert _process_one(_engine(_FakePipeline()), _frame()).metrics == {}

    def test_an_invalid_result_is_not_analyzed(self):
        """Una métrica calculada sobre un frame que no midió nada es peor que ninguna."""
        calls = []
        engine = _engine(_FakePipeline(raises=True),
                         analyzer=lambda result: calls.append(result) or {"count": 1})
        result = _process_one(engine, _frame())
        assert (calls, result.metrics) == ([], {})

    def test_a_failing_analyzer_does_not_break_the_result(self):
        def broken_analyzer(result):
            raise ValueError("cuenta mal escrita")

        engine = _engine(_FakePipeline([(0, 90.0, (0, 0, 5, 5))]), analyzer=broken_analyzer)
        result = _process_one(engine, _frame())
        assert (result.is_valid, result.metrics) == (True, {})

    def test_the_analyzer_sees_the_inference_time(self):
        """Un proyecto que publica el tiempo al PLC publicaría un cero medido."""
        pipeline = _FakePipeline([(0, 90.0, (0, 0, 5, 5))], delay_s=0.02)
        engine = _engine(pipeline,
                         analyzer=lambda result: {"elapsed_ms": result.inference_time_ms})
        result = _process_one(engine, _frame())
        assert result.metrics["elapsed_ms"] >= 20.0


# ── Frame anotado ────────────────────────────────────────────────────────────

class TestAnnotation:
    def test_the_result_carries_an_annotated_frame(self):
        result = _process_one(_engine(_FakePipeline([(0, 90.0, (10, 10, 40, 40))])), _frame())
        assert result.annotated_bgr is not None
        assert result.annotated_bgr.shape == (240, 320, 3)

    def test_the_camera_frame_is_never_touched(self):
        frame = _frame(level=128)
        result = _process_one(_engine(_FakePipeline([(0, 90.0, (10, 10, 40, 40))])), frame)
        assert np.array_equal(result.source_bgr, np.full((240, 320, 3), 128, np.uint8))

    def test_the_config_can_turn_the_overlay_off(self):
        config = _MockConfig(**{"inference.overlay.enabled": False})
        assert _process_one(_engine(_FakePipeline(), config), _frame()).annotated_bgr is None

    def test_nothing_is_drawn_when_nobody_is_watching(self):
        """Dibujar cuesta más que codificar: sin clientes no se anota."""
        engine = _engine(_FakePipeline(), annotate_gate=lambda camera_slot: False)
        assert _process_one(engine, _frame()).annotated_bgr is None

    def test_the_cycle_frame_is_drawn_even_when_nobody_is_watching(self):
        """
        `annotate()` saltea el gate a propósito.

        Es un dibujo por medición, no por frame, así que el costo que el gate cuida no
        aplica. Y como la pregunta se contesta en el momento del dibujo, respetarlo dejaba
        la medición sin imagen durante todo el ciclo por haber tenido la UI en crudo justo
        en ese instante — y sin nada guardado para el que se conectara después.
        """
        engine = _engine(_FakePipeline([(0, 90.0, (10, 10, 40, 40))]),
                         annotate_gate=lambda camera_slot: False)
        result = _process_one(engine, _frame())
        assert result.annotated_bgr is None          # el camino por frame sí lo respeta
        assert engine.annotate(result) is not None   # la medición, no

    def test_the_cycle_frame_still_obeys_the_config(self):
        """El gate es una optimización; apagar el overlay es una decisión."""
        config = _MockConfig(**{"inference.overlay.enabled": False})
        engine = _engine(_FakePipeline(), config)
        assert engine.annotate(_process_one(engine, _frame())) is None

    def test_the_annotator_draws_over_the_standard_overlay(self):
        seen = []

        def annotator(frame_bgr, result):
            seen.append((frame_bgr.shape, result.camera_slot))
            frame_bgr[0, 0] = (0, 0, 255)

        result = _process_one(_engine(_FakePipeline(), annotator=annotator), _frame())
        assert seen == [((240, 320, 3), _SLOT)]
        assert tuple(result.annotated_bgr[0, 0]) == (0, 0, 255)

    def test_the_timestamp_is_drawn_over_the_annotator(self):
        """Va abajo a la derecha, con fondo opaco: una referencia del proyecto no lo tapa."""
        def paint_everything(frame_bgr, result):
            frame_bgr[:] = (0, 0, 255)

        result = _process_one(_engine(_FakePipeline(), annotator=paint_everything), _frame())
        assert tuple(int(c) for c in result.annotated_bgr[-1, -1]) == (0, 0, 0)

    def test_without_a_timestamp_the_annotator_has_the_last_word(self):
        def paint_everything(frame_bgr, result):
            frame_bgr[:] = (0, 0, 255)

        config = _MockConfig(**{"inference.overlay.draw_timestamp": False})
        result = _process_one(_engine(_FakePipeline(), config, annotator=paint_everything),
                              _frame())
        assert tuple(int(c) for c in result.annotated_bgr[-1, -1]) == (0, 0, 255)

    def test_the_annotator_does_not_run_when_nothing_is_drawn(self):
        """Sin frame anotado no hay dónde dibujar, y el gate ya dijo que nadie mira."""
        calls = []
        engine = _engine(_FakePipeline(), annotator=lambda frame_bgr, result: calls.append(1),
                         annotate_gate=lambda camera_slot: False)
        assert _process_one(engine, _frame()).annotated_bgr is None
        assert calls == []

    def test_a_failing_annotator_keeps_the_standard_frame(self):
        def broken_annotator(frame_bgr, result):
            raise ValueError("linea mal configurada")

        result = _process_one(_engine(_FakePipeline(), annotator=broken_annotator), _frame())
        assert result.annotated_bgr is not None

    def test_the_annotator_also_sees_invalid_results(self):
        """La referencia que dibuja sirve igual —o más— cuando la medición no vale."""
        seen = []
        engine = _engine(_FakePipeline(raises=True),
                         annotator=lambda frame_bgr, result: seen.append(result.is_valid))
        _process_one(engine, _frame())
        assert seen == [False]

    def test_the_gate_receives_the_camera_slot(self):
        seen = []
        engine = _engine(_FakePipeline(),
                         annotate_gate=lambda camera_slot: seen.append(camera_slot))
        _process_one(engine, _frame())
        assert seen == [_SLOT]


def _other_result(roi_px: tuple | None = None) -> InferenceResult:
    """Resultado de otro pipeline, con una caja, para dibujarlo encima de un lienzo."""
    return InferenceResult(
        camera_slot=_SLOT, pipeline_slot="pipeline_2", roi_px=roi_px,
        detections=[Detection(class_index=0, class_name="person", confidence_pct=90.0,
                              bbox_px=(100, 100, 160, 180))])


class TestDrawOnto:
    """El overlay de un pipeline encima del anotado de otro: lo que arma el combinado."""

    def test_it_draws_the_boxes_on_the_canvas(self):
        canvas = _frame()
        _engine(_FakePipeline()).draw_onto(canvas, _other_result())
        assert not np.array_equal(canvas[100, 100:160], _frame()[100, 100:160])

    def test_it_draws_the_roi(self):
        canvas = _frame()
        _engine(_FakePipeline()).draw_onto(canvas, _other_result(roi_px=(5, 5, 50, 50)))
        assert not np.array_equal(canvas[5, 5:55], _frame()[5, 5:55])

    def test_it_runs_the_annotator(self):
        seen = []

        def annotator(frame_bgr, result):
            seen.append(result.pipeline_slot)
            frame_bgr[0, 0] = (0, 0, 255)

        canvas = _frame()
        _engine(_FakePipeline(), annotator=annotator).draw_onto(canvas, _other_result())
        assert seen == ["pipeline_2"]
        assert tuple(canvas[0, 0]) == (0, 0, 255)

    def test_it_draws_no_timestamp(self):
        """La hora es del frame: la pone una vez quien lo arma, al final."""
        canvas = _frame()
        _engine(_FakePipeline()).draw_onto(canvas, _other_result())
        assert tuple(canvas[-1, -1]) == (128, 128, 128)

    def test_it_does_not_adjust_the_canvas(self):
        """El lienzo ya viene ajustado para mirar: otra pasada lo aclararía dos veces."""
        canvas = _frame()
        engine = _engine(_FakePipeline(), canvas_adjust=lambda frame: np.full_like(frame, 255))
        engine.draw_onto(canvas, _other_result())
        assert tuple(canvas[0, 0]) == (128, 128, 128)

    def test_with_the_overlay_off_it_draws_nothing(self):
        config = _MockConfig(**{"inference.overlay.enabled": False})
        canvas = _frame()
        _engine(_FakePipeline(), config).draw_onto(canvas, _other_result())
        assert np.array_equal(canvas, _frame())

    def test_the_timestamp_is_drawn_on_request(self):
        canvas = _frame()
        _engine(_FakePipeline()).draw_timestamp(canvas, _other_result())
        assert tuple(int(c) for c in canvas[-1, -1]) == (0, 0, 0)

    def test_the_timestamp_obeys_the_config(self):
        config = _MockConfig(**{"inference.overlay.draw_timestamp": False})
        canvas = _frame()
        _engine(_FakePipeline(), config).draw_timestamp(canvas, _other_result())
        assert np.array_equal(canvas, _frame())


# ── Ciclo de vida ────────────────────────────────────────────────────────────

class TestLifecycle:
    def test_the_thread_loads_and_unloads_the_pipeline(self):
        pipeline = _FakePipeline()
        engine = _engine(pipeline)
        engine.start()
        assert _wait_until(lambda: pipeline.load_calls == 1)
        engine.requestInterruption()
        assert engine.wait(_WAIT_TIMEOUT_MS)
        assert (pipeline.load_calls, pipeline.unload_calls) == (1, 1)

    def test_it_stops_with_frames_still_queued(self):
        engine = _engine(_FakePipeline(delay_s=0.05))
        engine.start()
        for level in range(10):
            engine.push_frame(_frame(level=level * 20), f"camera_{level}")
        engine.requestInterruption()
        assert engine.wait(_WAIT_TIMEOUT_MS)

    def test_it_serves_every_camera_of_the_pipeline(self):
        """Fan-in: un solo hilo y una sola copia de los pesos para las dos cámaras."""
        engine = _engine(_FakePipeline([(0, 90.0, (0, 0, 5, 5))]))
        collector = _run_frames(engine, [(_frame(), _SLOT), (_frame(), _OTHER_SLOT)], expected=2)
        assert collector.camera_slots() == [_SLOT, _OTHER_SLOT]

    def test_the_result_names_its_pipeline_and_camera(self):
        result = _process_one(_engine(_FakePipeline()), _frame())
        assert (result.camera_slot, result.pipeline_slot) == (_SLOT, _PIPELINE)

    def test_the_inference_time_is_measured(self):
        result = _process_one(_engine(_FakePipeline(delay_s=0.02)), _frame())
        assert result.inference_time_ms >= 20


# ── Período por cámara ───────────────────────────────────────────────────────

class TestCycleTime:
    """`cycle_time_ms`: del arranque de un frame al arranque del siguiente de la misma cámara."""

    def test_the_first_frame_of_a_camera_has_no_cycle_time(self):
        assert _engine(_FakePipeline()).process(_frame(), _SLOT).cycle_time_ms == 0.0

    def test_the_cycle_counts_what_the_inference_time_leaves_out(self):
        """El analyzer queda fuera de `inference_time_ms` y dentro del período."""
        engine = _engine(_FakePipeline(), analyzer=lambda result: time.sleep(0.05) or {})
        engine.process(_frame(), _SLOT)
        second = engine.process(_frame(), _SLOT)
        assert second.cycle_time_ms >= 50
        assert second.cycle_time_ms > second.inference_time_ms

    def test_each_camera_has_its_own_cycle(self):
        engine = _engine(_FakePipeline())
        engine.process(_frame(), _SLOT)
        other = engine.process(_frame(), _OTHER_SLOT)
        again = engine.process(_frame(), _SLOT)
        assert other.cycle_time_ms == 0.0
        assert again.cycle_time_ms > 0.0


# ── Corrida sincrónica ───────────────────────────────────────────────────────

class TestSynchronousProcess:
    """`process()` corre el ciclo del hilo sobre un frame suelto, sin arrancarlo."""

    def test_it_returns_the_result_without_starting_the_thread(self):
        engine = _engine(_FakePipeline([(0, 90.0, (0, 0, 10, 10))]))
        result = engine.process(_frame(), _SLOT)
        assert result.is_valid and result.detection_count == 1
        assert engine.isRunning() is False

    def test_it_runs_the_same_cycle_as_the_thread(self):
        """ROI, analyzer y overlay incluidos: el resultado es el que saldría en vivo."""
        # Sin hora ni panel: dibujan el instante y el tiempo de inferencia, que cambian
        # entre dos corridas aunque el ciclo sea el mismo.
        config = _MockConfig(**{
            f"cameras.{_SLOT}.roi": {
                "enabled": True, "x_px": 100, "y_px": 50, "width_px": 80, "height_px": 60},
            "inference.overlay.draw_timestamp": False,
            "inference.overlay.draw_summary": False,
        })
        live = _process_one(_engine(_FakePipeline([(0, 90.0, (10, 10, 20, 20))]), config,
                                    analyzer=lambda result: {"count": result.detection_count}),
                            _frame())
        offline = _engine(_FakePipeline([(0, 90.0, (10, 10, 20, 20))]), config,
                          analyzer=lambda result: {"count": result.detection_count}
                          ).process(_frame(), _SLOT)

        assert offline.roi_px == live.roi_px
        assert offline.detections[0].bbox_px == live.detections[0].bbox_px
        assert offline.metrics == live.metrics == {"count": 1}
        np.testing.assert_array_equal(offline.annotated_bgr, live.annotated_bgr)

    def test_it_does_not_emit_the_result(self):
        engine = _engine(_FakePipeline())
        collector = _Collector()
        engine.result_ready.connect(collector.on_result, Qt.ConnectionType.DirectConnection)
        engine.process(_frame(), _SLOT)
        assert collector.count() == 0

    def test_it_does_not_add_to_the_counters_of_the_thread(self):
        engine = _engine(_FakePipeline(raises=True))
        engine.process(_frame(), _SLOT)
        status = engine.get_status()
        assert (status["processed"], status["invalid"]) == (0, 0)
        assert status["last_inference_time_ms"] == 0.0

    def test_it_does_not_load_the_pipeline(self):
        """Cargar es de quien construyó el pipeline; sin cargar sale como en vivo."""
        pipeline = _FakePipeline(loaded=False)
        result = _engine(pipeline).process(_frame(), _SLOT)
        assert pipeline.load_calls == 0
        assert result.invalid_reason == REASON_NO_MODEL

    def test_it_refuses_to_run_while_the_thread_is_running(self):
        pipeline = _FakePipeline()
        engine = _engine(pipeline)
        engine.start()
        try:
            assert _wait_until(lambda: pipeline.load_calls == 1)
            with pytest.raises(RuntimeError):
                engine.process(_frame(), _SLOT)
        finally:
            engine.requestInterruption()
            assert engine.wait(_WAIT_TIMEOUT_MS)


class _RecordingConfig(_MockConfig):
    """Anota cada clave que se le pide."""

    def __init__(self, **overrides):
        super().__init__(**overrides)
        self.asked: list[str] = []

    def get(self, key: str, default: object = None) -> object:
        self.asked.append(key)
        return super().get(key, default)


class TestMissingKeys:
    def test_a_pipeline_without_its_own_roi_never_asks_for_it(self):
        """El config avisa cada clave ausente: el caso normal no puede llenar el log."""
        config = _RecordingConfig()
        _process_one(_engine(_FakePipeline(), config), _frame())
        assert not [key for key in config.asked if key.startswith("inference.pipelines.")
                    and ".roi" in key]


class TestRegionDrawing:
    """Cada pipeline dibuja su ROI con su color y su nombre, y avisa del frame que cambió."""

    _BLUE = overlay.PIPELINE_COLORS_BGR[0]
    _CYAN = overlay.PIPELINE_COLORS_BGR[1]

    def _has_color_near(self, frame: np.ndarray, color: tuple, x_px: int, y_px: int) -> bool:
        window = frame[y_px - 3:y_px + 4, x_px]
        return bool(np.any(np.all(window == color, axis=1)))

    def test_the_annotated_frame_has_the_roi_in_the_pipeline_color(self):
        config = _MockConfig(**_pipeline_roi())
        result = _process_one(_engine(_FakePipeline(), config), _frame())
        assert self._has_color_near(result.annotated_bgr, self._BLUE, 100, 70)

    def test_the_color_comes_from_the_place_in_the_config_even_if_another_is_off(self):
        pipelines = {"pipeline_0": {"enabled": False}, _PIPELINE: {}}
        config = _MockConfig(**{"inference.pipelines": pipelines}, **_pipeline_roi())
        result = _process_one(_engine(_FakePipeline(), config), _frame())
        assert self._has_color_near(result.annotated_bgr, self._CYAN, 100, 70)
        assert not self._has_color_near(result.annotated_bgr, self._BLUE, 100, 70)

    def _drawn_texts(self, monkeypatch, config) -> list[str]:
        texts = []
        real_put_text = overlay.cv2.putText
        monkeypatch.setattr(overlay.cv2, "putText",
                            lambda frame, text, *args: texts.append(text)
                            or real_put_text(frame, text, *args))
        _process_one(_engine(_FakePipeline(), config), _frame())
        return texts

    def test_the_roi_carries_the_name_the_operator_gave_the_pipeline(self, monkeypatch):
        section = _pipeline_roi()[f"inference.pipelines.{_PIPELINE}"]
        config = _MockConfig(**{f"inference.pipelines.{_PIPELINE}":
                                {**section, "name": "Flujo de grano"}})
        assert "Flujo de grano" in self._drawn_texts(monkeypatch, config)

    def test_without_a_name_the_roi_carries_the_slot(self, monkeypatch):
        assert _PIPELINE in self._drawn_texts(monkeypatch, _MockConfig(**_pipeline_roi()))

    def test_draw_onto_uses_the_same_style(self):
        config = _MockConfig(**_pipeline_roi())
        engine = _engine(_FakePipeline(), config)
        result = _process_one(engine, _frame())
        canvas = _frame()
        engine.draw_onto(canvas, result)
        assert self._has_color_near(canvas, self._BLUE, 100, 70)

    def test_a_frame_of_another_size_than_the_drawn_one_is_warned(self, caplog):
        config = _MockConfig(**_pipeline_roi(frame_width_px=640, frame_height_px=480))
        result = _process_one(_engine(_FakePipeline(), config), _frame())
        assert result.roi_px == (40, 20, 100, 50)       # se mide igual: es un aviso
        assert "640x480" in caplog.text and "320x240" in caplog.text

    def test_the_same_size_does_not_warn(self, caplog):
        config = _MockConfig(**_pipeline_roi(frame_width_px=320, frame_height_px=240))
        _process_one(_engine(_FakePipeline(), config), _frame())
        assert "se dibujó sobre un frame" not in caplog.text

    def test_a_region_without_a_drawn_size_does_not_warn(self, caplog):
        _process_one(_engine(_FakePipeline(), _MockConfig(**_pipeline_roi())), _frame())
        assert "se dibujó sobre un frame" not in caplog.text


class _StatePipeline(_FakePipeline):
    """Un pipeline que declara el estado del recuadro de cada corrida."""

    def __init__(self, state: str, **kwargs):
        super().__init__(**kwargs)
        self._state_to_set = state

    def _run(self, frame_bgr, camera_slot):
        self._set_roi_state(self._state_to_set)
        return super()._run(frame_bgr, camera_slot)


class TestRoiState:
    _GREEN = overlay.ROI_STATE_COLORS_BGR["ok"]
    _RED = overlay.ROI_STATE_COLORS_BGR["nok"]
    _GREY = overlay._INVALID_ROI_BGR

    def _has_color_near(self, frame: np.ndarray, color: tuple) -> bool:
        window = frame[67:74, 100]
        return bool(np.any(np.all(window == color, axis=1)))

    def test_the_result_carries_what_the_pipeline_declared(self):
        config = _MockConfig(**_pipeline_roi())
        result = _process_one(_engine(_StatePipeline("nok"), config), _frame())
        assert result.roi_state == "nok"

    def test_the_annotated_frame_paints_the_border_with_it(self):
        config = _MockConfig(**_pipeline_roi())
        result = _process_one(_engine(_StatePipeline("ok"), config), _frame())
        assert self._has_color_near(result.annotated_bgr, self._GREEN)

    def test_a_pipeline_that_says_nothing_leaves_the_result_without_state(self):
        config = _MockConfig(**_pipeline_roi())
        assert _process_one(_engine(_FakePipeline(), config), _frame()).roi_state == ""

    def test_an_invalid_result_is_grey_whatever_the_pipeline_said(self):
        config = _MockConfig(**_pipeline_roi(), **{f"cameras.{_SLOT}.illumination_min": 99})
        result = _process_one(_engine(_StatePipeline("ok"), config), _frame(level=10))
        assert not result.is_valid
        assert not self._has_color_near(result.annotated_bgr, self._GREEN)
        assert self._has_color_near(result.annotated_bgr, self._GREY)

    def test_draw_onto_paints_the_same(self):
        config = _MockConfig(**_pipeline_roi())
        engine = _engine(_StatePipeline("nok"), config)
        result = _process_one(engine, _frame())
        canvas = _frame()
        engine.draw_onto(canvas, result)
        assert self._has_color_near(canvas, self._RED)

    def test_the_state_goes_to_the_dataset_json(self):
        config = _MockConfig(**_pipeline_roi())
        result = _process_one(_engine(_StatePipeline("warning"), config), _frame())
        assert result.to_dict()["roi_state"] == "warning"


class TestNoCameraRoi:
    """`cameras.<slot>.roi` ya no lo lee nadie: sin región propia el pipeline ve todo el frame."""

    _LEGACY = {"enabled": True, "x_px": 20, "y_px": 10, "width_px": 100, "height_px": 50}

    def test_a_leftover_camera_roi_does_not_crop(self):
        config = _MockConfig(**{f"cameras.{_SLOT}.roi": self._LEGACY})
        pipeline = _FakePipeline()
        result = _process_one(_engine(pipeline, config), _frame())
        assert result.roi_px is None
        assert pipeline.frames[0].shape[:2] == (240, 320)

    def test_the_own_roi_still_crops(self):
        config = _MockConfig(**{f"cameras.{_SLOT}.roi": self._LEGACY}, **_pipeline_roi())
        result = _process_one(_engine(_FakePipeline(), config), _frame())
        assert result.roi_px == (40, 20, 100, 50)

    def test_the_overlay_has_no_crop_option_anymore(self):
        assert not hasattr(overlay.OverlayOptions(), "crop_to_roi")

    def test_a_leftover_crop_to_roi_does_not_crop_the_annotated_frame(self):
        config = _MockConfig(**{"inference.overlay.crop_to_roi": True}, **_pipeline_roi())
        result = _process_one(_engine(_FakePipeline(), config), _frame())
        assert result.annotated_bgr.shape[:2] == (240, 320)

    def test_draw_onto_draws_with_a_leftover_crop_to_roi(self):
        config = _MockConfig(**{"inference.overlay.crop_to_roi": True}, **_pipeline_roi())
        engine = _engine(_FakePipeline(), config)
        result = _process_one(engine, _frame())
        canvas = _frame()
        engine.draw_onto(canvas, result)
        assert not np.array_equal(canvas, _frame())


class TestWhereTheRegionIsNative:
    """La región se corta del nativo sólo donde hay un decodificador que lo haga, y se decide por config."""

    def _run(self, camera: dict | None, **roi) -> tuple:
        flat = {f"cameras.{_SLOT}.{key}": value for key, value in (camera or {}).items()}
        config = _MockConfig(**_pipeline_roi(**roi), **flat,
                             **({f"cameras.{_SLOT}": camera} if camera is not None else {}))
        pipeline = _FakePipeline()
        result = _process_with_views(_engine(pipeline, config), _frame(), {})
        return result, pipeline

    def test_a_gige_camera_crops_the_frame_it_already_has(self):
        """Su frame ya es el del sensor: no espera ninguna vista y no sale `no_view`."""
        result, pipeline = self._run({"driver": "basler_gige"})
        assert result.is_valid and result.roi_px == (40, 20, 100, 50)
        assert pipeline.frames[0].shape[:2] == (50, 100)

    def test_a_camera_without_a_driver_crops_the_frame(self):
        result, _ = self._run(None)
        assert result.is_valid

    def test_a_video_camera_without_its_view_is_not_inferred(self):
        result, pipeline = self._run({"driver": "rtsp"})
        assert result.invalid_reason == REASON_NO_VIEW and pipeline.frames == []

    def test_a_video_file_camera_expects_the_view_too(self):
        result, _ = self._run({"driver": "video_file"})
        assert result.invalid_reason == REASON_NO_VIEW

    def test_a_video_camera_with_an_image_adjustment_crops_the_frame(self):
        """La vista no pasa por el preprocessor: se corta del frame de referencia, siempre."""
        result, pipeline = self._run(
            {"driver": "rtsp", "image_adjust": {"brightness_factor": 2.0}})
        assert result.is_valid and pipeline.frames[0].shape[:2] == (50, 100)

    def test_a_whole_frame_pipeline_expects_no_view(self):
        result, _ = self._run({"driver": "rtsp"}, enabled=False)
        assert result.is_valid and result.roi_px is None

    def test_the_decision_does_not_depend_on_a_full_resolution_key(self):
        """Un `full_resolution: false` que quedó en un config viejo no apaga el corte nativo."""
        result, _ = self._run({"driver": "rtsp"}, full_resolution=False)
        assert result.invalid_reason == REASON_NO_VIEW
