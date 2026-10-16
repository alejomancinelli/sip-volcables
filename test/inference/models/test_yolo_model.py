"""Tests de los modelos YOLO sobre TensorRT: el letterbox y el recorte del clasificador, la
decodificación de las dos formas de salida, las máscaras, y el ciclo de vida de los tres
modelos.

TensorRT y la GPU no están: el runner es un doble que devuelve las salidas que arma cada
test, así que lo que se prueba es todo lo que este módulo decide alrededor de la pasada."""

import json

import numpy as np
import pytest

from system.inference.models import yolo_model
from system.inference.models.abstract_model import STATUS_ERROR, STATUS_UNLOADED
from system.inference.models.yolo_model import (YoloClassificationModel, YoloDetectionModel,
                                                YoloSegmentationModel, build_mask,
                                                compute_letterbox, decode_candidates,
                                                prepare_center_crop, prepare_letterboxed,
                                                to_frame_box, to_probabilities)

_SLOT = "person_det"


class _StubConfig:
    """ConfigManager mínimo: `get` con clave punteada, sin archivo ni Lock."""

    def __init__(self, section: dict):
        self._values = {f"inference.models.{_SLOT}": section}
        for key, value in section.items():
            self._values[f"inference.models.{_SLOT}.{key}"] = value

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


class _FakeRunner:
    """`TensorRtRunner` sin GPU: devuelve lo que el test le carga en `outputs`."""

    load_reason = ""
    input_hw = (384, 640)
    output_shapes: list = [(1, 6, 5040)]
    outputs: dict = {}

    def __init__(self):
        self.inputs = []
        self.is_closed = False

    def load(self, _engine_bytes: bytes) -> str:
        return self.load_reason

    def run(self, input_nchw: np.ndarray) -> dict:
        self.inputs.append(input_nchw)
        return dict(self.outputs)

    def close(self):
        self.is_closed = True


@pytest.fixture
def runner(monkeypatch):
    """Instala un `_FakeRunner` nuevo como runner de los modelos y lo devuelve como clase."""
    fake = type("_Runner", (_FakeRunner,), {"outputs": {}, "output_shapes": [(1, 6, 5040)]})
    monkeypatch.setattr(yolo_model, "TensorRtRunner", fake)
    return fake


def _engine_file(tmp_path, metadata: dict | None = None) -> str:
    """Un `.engine` con el encabezado de ultralytics y un engine de mentira."""
    path = tmp_path / "model.engine"
    data = b"ENGINE"
    if metadata is not None:
        meta = json.dumps(metadata).encode()
        data = len(meta).to_bytes(4, byteorder="little", signed=True) + meta + data
    path.write_bytes(data)
    return str(path)


def _model(model_class, tmp_path, *, metadata: dict | None = None, **section):
    section.setdefault("path", _engine_file(tmp_path, metadata))
    section.setdefault("min_confidence_pct", 50)
    return model_class(_StubConfig(section), _SLOT)


def _detector_output(*anchors: tuple, class_count: int = 2,
                     anchor_count: int = 64) -> np.ndarray:
    """
    Salida (1, 4 + clases, anclas) como la de YOLOv8/11. Cada ancla es
    (cx, cy, w, h, clase, confianza); el resto queda en cero. Hay más anclas que canales,
    como en un engine de verdad: es lo que distingue esta forma de la ya filtrada.
    """
    raw = np.zeros((1, 4 + class_count, anchor_count), np.float32)
    for i, (cx, cy, w, h, class_id, score) in enumerate(anchors):
        raw[0, :4, i] = (cx, cy, w, h)
        raw[0, 4 + class_id, i] = score
    return raw


# ── Llevar el frame a la entrada ─────────────────────────────────────────────

class TestLetterbox:
    def test_a_640x360_frame_fills_a_rectangular_engine_with_12px_bars(self):
        """Medido en volcables: con 384x640 casi no hay relleno, con 640x640 sobra la mitad."""
        box = compute_letterbox(640, 360, 640, 384)
        assert (box.scale, box.pad_x, box.pad_y, box.resized_w, box.resized_h) == \
            (1.0, 0, 12, 640, 360)

    def test_a_1080p_frame_into_a_square_engine(self):
        box = compute_letterbox(1920, 1080, 640, 640)
        assert box.scale == pytest.approx(1 / 3)
        assert (box.resized_w, box.resized_h, box.pad_x, box.pad_y) == (640, 360, 0, 140)

    def test_the_input_is_rgb_nchw_from_0_to_1_with_grey_bars(self):
        frame = np.zeros((360, 640, 3), np.uint8)
        frame[...] = (10, 20, 30)              # BGR
        input_nchw, box = prepare_letterboxed(frame, (384, 640))
        assert input_nchw.shape == (1, 3, 384, 640)
        assert input_nchw.dtype == np.float32
        assert input_nchw[0, :, 100, 100] == pytest.approx([30 / 255, 20 / 255, 10 / 255])
        assert input_nchw[0, 0, 0, 0] == pytest.approx(114 / 255)
        assert box.pad_y == 12

    def test_a_grey_frame_is_accepted(self):
        input_nchw, _ = prepare_letterboxed(np.full((360, 640), 50, np.uint8), (384, 640))
        assert input_nchw.shape == (1, 3, 384, 640)


class TestCenterCrop:
    def test_the_classifier_sees_the_centre_of_the_frame(self):
        """
        Como ultralytics: de un frame de 640x360 entra el cuadrado central de 360x360
        —x de 140 a 500— y lo de los costados no.
        """
        frame = np.zeros((360, 640, 3), np.uint8)
        frame[:, :140] = (0, 0, 255)           # rojo a la izquierda
        frame[:, 140:500] = (0, 255, 0)        # verde al centro
        frame[:, 500:] = (255, 0, 0)           # azul a la derecha
        input_nchw = prepare_center_crop(frame, (224, 224))
        assert input_nchw.shape == (1, 3, 224, 224)
        red, green, blue = (input_nchw[0, channel].mean() for channel in range(3))
        assert green > 0.9
        assert red < 0.1 and blue < 0.1

    def test_a_small_frame_is_scaled_up_to_cover(self):
        assert prepare_center_crop(np.zeros((100, 120, 3), np.uint8), (224, 224)).shape == \
            (1, 3, 224, 224)


# ── Decodificar la salida ────────────────────────────────────────────────────

class TestDecodeCandidates:
    def _decode(self, raw: np.ndarray, **kwargs):
        options = {"mask_dim": 0, "min_score": 0.5, "iou_threshold": 0.7,
                   "max_detections": 300}
        options.update(kwargs)
        return decode_candidates(raw, **options)

    def test_boxes_come_out_as_corners(self):
        result = self._decode(_detector_output((100, 50, 40, 20, 0, 0.9)))
        np.testing.assert_allclose(result.boxes_xyxy, [[80, 40, 120, 60]])
        assert result.class_ids.tolist() == [0]
        assert result.scores.tolist() == pytest.approx([0.9])

    def test_below_the_threshold_is_dropped(self):
        assert len(self._decode(_detector_output((100, 50, 40, 20, 0, 0.3))).scores) == 0

    def test_an_overlapping_box_of_the_same_class_is_suppressed(self):
        result = self._decode(_detector_output((100, 100, 50, 50, 0, 0.9),
                                               (102, 101, 50, 50, 0, 0.8)))
        assert result.scores.tolist() == pytest.approx([0.9])

    def test_an_overlapping_box_of_another_class_survives(self):
        """El NMS es por clase, como en ultralytics."""
        result = self._decode(_detector_output((100, 100, 50, 50, 0, 0.9),
                                               (102, 101, 50, 50, 1, 0.8)))
        assert sorted(result.class_ids.tolist()) == [0, 1]

    def test_the_best_go_first_and_the_cap_applies(self):
        result = self._decode(_detector_output((50, 50, 10, 10, 0, 0.6),
                                               (200, 200, 10, 10, 0, 0.95),
                                               (400, 300, 10, 10, 1, 0.8)),
                              max_detections=2)
        assert result.scores.tolist() == pytest.approx([0.95, 0.8])

    def test_mask_coefficients_travel_with_their_box(self):
        raw = _detector_output((100, 100, 50, 50, 0, 0.9), class_count=2)
        raw = np.concatenate([raw, np.zeros((1, 3, raw.shape[2]), np.float32)], axis=1)
        raw[0, 6:, 0] = (0.1, 0.2, 0.3)
        result = self._decode(raw, mask_dim=3)
        np.testing.assert_allclose(result.mask_coefs, [[0.1, 0.2, 0.3]])

    def test_an_end_to_end_export_is_already_filtered(self):
        """(1, K, 6): esquinas, confianza y clase, sin NMS que hacer."""
        raw = np.zeros((1, 300, 6), np.float32)
        raw[0, 0] = (10, 20, 110, 220, 0.4, 1)
        raw[0, 1] = (30, 40, 130, 240, 0.9, 0)
        result = self._decode(raw)
        np.testing.assert_allclose(result.boxes_xyxy, [[30, 40, 130, 240]])
        assert result.class_ids.tolist() == [0]


class TestFrameBox:
    def test_a_box_goes_back_through_the_letterbox(self):
        box = compute_letterbox(1920, 1080, 640, 384)       # escala 1/3, 12 px arriba
        assert to_frame_box(np.array([100, 52, 200, 112]), box, 1920, 1080) == \
            (300, 120, 600, 300)

    def test_a_box_over_the_bars_is_clipped_to_the_frame(self):
        box = compute_letterbox(640, 360, 640, 384)
        assert to_frame_box(np.array([-5, 0, 700, 384]), box, 640, 360) == (0, 0, 640, 360)


class TestBuildMask:
    def test_the_mask_has_the_size_of_the_box(self):
        protos = np.full((1, 96, 160), 5.0, np.float32)      # positivo en todo el frame
        box = compute_letterbox(640, 360, 640, 384)
        mask = build_mask(np.array([1.0]), protos, (100, 50, 180, 110), box, (384, 640))
        assert mask.shape == (60, 80)
        assert mask.dtype == np.uint8
        assert mask.min() == 1

    def test_only_where_the_prototype_is_on(self):
        protos = np.full((1, 96, 160), -5.0, np.float32)
        protos[0, :, :80] = 5.0                              # mitad izquierda de la entrada
        box = compute_letterbox(640, 360, 640, 384)
        mask = build_mask(np.array([1.0]), protos, (200, 100, 440, 200), box, (384, 640))
        assert mask[:, :100].min() == 1                     # x 200–300 cae a la izquierda
        assert mask[:, -100:].max() == 0                    # x 340–440 cae a la derecha


class TestProbabilities:
    def test_probabilities_are_left_as_they_are(self):
        np.testing.assert_allclose(to_probabilities(np.array([[0.2, 0.8]])), [0.2, 0.8])

    def test_logits_go_through_softmax(self):
        probabilities = to_probabilities(np.array([[1.0, 3.0, -2.0]]))
        assert probabilities.sum() == pytest.approx(1.0)
        assert probabilities.argmax() == 1


# ── Los modelos ──────────────────────────────────────────────────────────────

class TestLoad:
    def test_it_loads_and_reads_the_input_from_the_engine(self, runner, tmp_path):
        model = _model(YoloDetectionModel, tmp_path, metadata={"task": "detect"})
        model.load()
        assert model.is_loaded

    def test_a_task_mismatch_leaves_it_in_error(self, runner, tmp_path):
        """Un engine de segmentación cargado como detector no llega al primer frame."""
        model = _model(YoloDetectionModel, tmp_path, metadata={"task": "segment"})
        model.load()
        assert model.status == STATUS_ERROR
        assert "segmentation" in model.error

    def test_an_engine_that_does_not_open_says_why(self, runner, tmp_path):
        runner.load_reason = "TensorRT no pudo abrir el engine"
        model = _model(YoloDetectionModel, tmp_path)
        model.load()
        assert model.status == STATUS_ERROR
        assert "no pudo abrir" in model.error

    def test_outputs_of_another_task_are_rejected(self, runner, tmp_path):
        runner.output_shapes = [(1, 10)]
        model = _model(YoloDetectionModel, tmp_path)
        model.load()
        assert model.status == STATUS_ERROR
        assert "3 dimensiones" in model.error

    def test_the_engine_names_are_used_when_nobody_declares_them(self, runner, tmp_path):
        model = _model(YoloDetectionModel, tmp_path,
                       metadata={"task": "detect", "names": {"0": "person", "1": "truck"}})
        model.load()
        assert model.class_names == ("person", "truck")

    def test_the_config_names_win_over_the_engine(self, runner, tmp_path):
        model = _model(YoloDetectionModel, tmp_path, class_names=["persona", "camion"],
                       metadata={"names": {"0": "person", "1": "truck"}})
        model.load()
        assert model.class_names == ("persona", "camion")

    def test_unload_releases_the_engine_and_is_idempotent(self, runner, tmp_path):
        model = _model(YoloDetectionModel, tmp_path)
        model.load()
        engine = model._runner
        model.unload()
        model.unload()
        assert engine.is_closed
        assert model.status == STATUS_UNLOADED

    def test_before_load_there_are_no_detections(self, runner, tmp_path):
        assert _model(YoloDetectionModel, tmp_path).predict(
            np.zeros((360, 640, 3), np.uint8)) == []


class TestDetectionModel:
    def test_detections_come_in_frame_pixels_with_their_names(self, runner, tmp_path):
        runner.outputs = {"output0": _detector_output((320, 192, 64, 40, 1, 0.9),
                                                      (100, 100, 10, 10, 0, 0.3))}
        model = _model(YoloDetectionModel, tmp_path, class_names=["person", "truck"])
        model.load()
        (detection,) = model.predict(np.zeros((360, 640, 3), np.uint8))
        assert detection.class_name == "truck"
        assert detection.confidence_pct == pytest.approx(90.0)
        assert detection.bbox_px == (288, 160, 352, 200)    # 12 px de barra arriba

    def test_the_threshold_comes_from_the_config(self, runner, tmp_path):
        runner.outputs = {"output0": _detector_output((320, 192, 64, 40, 0, 0.3))}
        model = _model(YoloDetectionModel, tmp_path, min_confidence_pct=20)
        model.load()
        assert len(model.predict(np.zeros((360, 640, 3), np.uint8))) == 1

    def test_the_nms_extras_are_read_from_params(self, runner, tmp_path):
        runner.outputs = {"output0": _detector_output((100, 100, 50, 50, 0, 0.9),
                                                      (300, 200, 50, 50, 0, 0.8))}
        model = _model(YoloDetectionModel, tmp_path, params={"max_detections": 1})
        model.load()
        assert len(model.predict(np.zeros((360, 640, 3), np.uint8))) == 1


class TestSegmentationModel:
    def test_each_detection_carries_its_mask_area_and_centroid(self, runner, tmp_path):
        raw = _detector_output((320, 192, 64, 40, 0, 0.9), class_count=1)
        raw = np.concatenate([raw, np.zeros((1, 1, raw.shape[2]), np.float32)], axis=1)
        raw[0, 5, 0] = 1.0
        runner.output_shapes = [(1, 6, 64), (1, 1, 96, 160)]
        runner.outputs = {"output0": raw, "output1": np.full((1, 1, 96, 160), 5.0, np.float32)}
        model = _model(YoloSegmentationModel, tmp_path)
        model.load()
        (detection,) = model.predict(np.zeros((360, 640, 3), np.uint8))
        assert detection.mask.shape == (40, 64)
        assert detection.area_px == 40 * 64
        assert detection.centroid_px == (320, 180)

    def test_an_engine_without_prototypes_is_rejected(self, runner, tmp_path):
        runner.output_shapes = [(1, 38, 5040)]
        model = _model(YoloSegmentationModel, tmp_path)
        model.load()
        assert model.status == STATUS_ERROR


class TestClassificationModel:
    @pytest.fixture
    def classifier_runner(self, runner):
        runner.input_hw = (224, 224)
        runner.output_shapes = [(1, 3)]
        return runner

    def test_the_verdict_is_one_detection_over_the_whole_frame(self, classifier_runner,
                                                               tmp_path):
        classifier_runner.outputs = {"output0": np.array([[0.1, 0.85, 0.05]], np.float32)}
        model = _model(YoloClassificationModel, tmp_path, class_names=["idle", "falling", "x"])
        model.load()
        (detection,) = model.predict(np.zeros((360, 640, 3), np.uint8))
        assert detection.class_name == "falling"
        assert detection.confidence_pct == pytest.approx(85.0)
        assert detection.bbox_px == (0, 0, 640, 360)

    def test_below_the_threshold_there_is_no_verdict(self, classifier_runner, tmp_path):
        classifier_runner.outputs = {"output0": np.array([[0.4, 0.35, 0.25]], np.float32)}
        model = _model(YoloClassificationModel, tmp_path)
        model.load()
        assert model.predict(np.zeros((360, 640, 3), np.uint8)) == []

    def test_the_classifier_gets_the_engine_input_size(self, classifier_runner, tmp_path):
        classifier_runner.outputs = {"output0": np.array([[0.9, 0.05, 0.05]], np.float32)}
        model = _model(YoloClassificationModel, tmp_path)
        model.load()
        model.predict(np.zeros((360, 640, 3), np.uint8))
        assert model._runner.inputs[0].shape == (1, 3, 224, 224)
