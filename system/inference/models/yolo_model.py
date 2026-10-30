"""
Modelos YOLO de ultralytics exportados a TensorRT: detección, segmentación y
clasificación, corridos sin ultralytics.

Maquinaria genérica: no conoce ninguna planta. Se registran en la fábrica como `yolo_det`,
`yolo_seg` y `yolo_cls`. Ultralytics se usa para entrenar y para exportar el `.engine`
(`manual_test/model_export/`); en el equipo corre el engine con `tensorrt_runtime.py` y el
pre y post proceso de acá, en numpy y OpenCV. Así el modelo carga desde bytes —y los pesos
pueden ir cifrados— y no paga por pasada el andamiaje en Python de ultralytics, que en el
benchmark de volcables leer los resultados costaba más de la mitad que la inferencia.

Lo que reproduce de ultralytics, porque el modelo se entrenó viéndolo así:
  - **Detección y segmentación**: letterbox al tamaño del engine —reducir conservando la
    relación de aspecto, centrar y rellenar con gris 114—, RGB y 0–1. NMS por clase.
  - **Clasificación**: reducir el lado corto al del engine y recortar el centro, que es lo
    que hace ultralytics al clasificar. Lo que queda fuera del centro no se mira: si
    importa, el ROI de la cámara lo resuelve.

**El tamaño de entrada sale del engine.** Uno exportado a 384x640 recibe 384x640, y con un
frame de 640x360 el letterbox casi no tiene que reducir. Elegir entre uno cuadrado y uno
rectangular es elegir qué archivo apunta `path`. Conviene que el engine tenga la relación
de aspecto de lo que mira —el frame, o el ROI si la cámara lo usa—: con uno cuadrado sobre
un frame 16:9 casi la mitad de la entrada es relleno.

Extras propios en `params` de la sección del modelo:
    iou_threshold   : solapamiento desde el que dos cajas de la misma clase son una; 0.7
    max_detections  : tope de detecciones por frame; 300

`previous` no se usa: estos modelos miran el frame que reciben, que el motor ya recortó
al ROI.
"""

import math
from abc import abstractmethod
from typing import NamedTuple

import cv2
import numpy as np

from system.logger import logger

from ..result import Detection
from .abstract_model import (STATUS_UNLOADED, TASK_CLASSIFICATION, TASK_DETECTION,
                             TASK_SEGMENTATION, AbstractModel, InputSpec)
from .tensorrt_runtime import TensorRtRunner, split_ultralytics_engine

_LETTERBOX_FILL = 114
_DEFAULT_IOU_THRESHOLD = 0.7
_DEFAULT_MAX_DETECTIONS = 300
_MASK_THRESHOLD = 0.5
# Separa las cajas de clases distintas antes del NMS, para que no se supriman entre ellas:
# el mismo recurso que usa ultralytics. Más grande que cualquier lado de un frame.
_CLASS_OFFSET_PX = 7680
# Una salida que suma esto con tolerancia ya son probabilidades; si no, son logits.
_PROBABILITY_SUM_TOLERANCE = 0.01


class Letterbox(NamedTuple):
    """Cómo se llevó un frame a la entrada del engine, para volver las cajas al frame."""

    scale: float
    pad_x: int          # relleno a la izquierda, px de la entrada
    pad_y: int          # relleno arriba
    resized_w: int
    resized_h: int


def compute_letterbox(frame_w: int, frame_h: int, input_w: int, input_h: int) -> Letterbox:
    """La misma cuenta que el LetterBox de ultralytics, redondeos incluidos."""
    scale = min(input_h / frame_h, input_w / frame_w)
    resized_w, resized_h = int(round(frame_w * scale)), int(round(frame_h * scale))
    pad_x = int(round((input_w - resized_w) / 2 - 0.1))
    pad_y = int(round((input_h - resized_h) / 2 - 0.1))
    return Letterbox(scale, pad_x, pad_y, resized_w, resized_h)


def _to_bgr(frame: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR) if frame.ndim == 2 else frame


def _to_nchw(image_bgr: np.ndarray) -> np.ndarray:
    """BGR uint8 HWC → RGB float32 0–1 NCHW, que es lo que espera un YOLO."""
    rgb = image_bgr[:, :, ::-1].transpose(2, 0, 1)
    return np.ascontiguousarray(rgb[None], dtype=np.float32) / 255.0


def prepare_letterboxed(frame: np.ndarray, input_hw: tuple[int, int]
                        ) -> tuple[np.ndarray, Letterbox]:
    """El frame como entrada de un detector o segmentador, y cómo se lo llevó ahí."""
    image = _to_bgr(frame)
    input_h, input_w = input_hw
    frame_h, frame_w = image.shape[:2]
    box = compute_letterbox(frame_w, frame_h, input_w, input_h)
    if (box.resized_w, box.resized_h) != (frame_w, frame_h):
        image = cv2.resize(image, (box.resized_w, box.resized_h), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((input_h, input_w, 3), _LETTERBOX_FILL, np.uint8)
    canvas[box.pad_y:box.pad_y + box.resized_h, box.pad_x:box.pad_x + box.resized_w] = image
    return _to_nchw(canvas), box


def prepare_center_crop(frame: np.ndarray, input_hw: tuple[int, int]) -> np.ndarray:
    """
    El frame como entrada de un clasificador: cubrir la entrada conservando la relación de
    aspecto y recortar el centro. INTER_AREA al reducir, que se acerca al antialias con
    que reduce ultralytics.
    """
    image = _to_bgr(frame)
    input_h, input_w = input_hw
    frame_h, frame_w = image.shape[:2]
    scale = max(input_h / frame_h, input_w / frame_w)
    resized_w = max(input_w, int(math.ceil(frame_w * scale)))
    resized_h = max(input_h, int(math.ceil(frame_h * scale)))
    interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_LINEAR
    image = cv2.resize(image, (resized_w, resized_h), interpolation=interpolation)
    top, left = (resized_h - input_h) // 2, (resized_w - input_w) // 2
    return _to_nchw(image[top:top + input_h, left:left + input_w])


class _Candidates(NamedTuple):
    """Detecciones que pasaron el umbral y el NMS, todavía en px de la entrada."""

    boxes_xyxy: np.ndarray       # (K, 4) float
    scores: np.ndarray           # (K,) 0–1
    class_ids: np.ndarray        # (K,) int
    mask_coefs: np.ndarray       # (K, mask_dim); (K, 0) sin máscaras


def decode_candidates(raw: np.ndarray, *, mask_dim: int, min_score: float,
                      iou_threshold: float, max_detections: int) -> _Candidates:
    """
    La salida de un detector o segmentador YOLO, filtrada por confianza y por NMS.

    Entiende las dos formas que exporta ultralytics:
      - (1, 4 + clases + mask_dim, N): una columna por ancla, caja centro-ancho-alto y una
        confianza por clase (YOLOv8, YOLO11). Lleva NMS.
      - (1, K, 6 + mask_dim): una fila por detección ya filtrada, caja de esquinas,
        confianza y clase (los exportados sin NMS externo, como YOLO26). No lo lleva.
    Se distinguen por cuál eje es más largo: hay muchas más anclas que canales.
    """
    pred = raw[0]
    if pred.shape[0] > pred.shape[1]:
        rows = pred[pred[:, 4] >= min_score]
        rows = rows[np.argsort(-rows[:, 4])][:max_detections]
        return _Candidates(rows[:, :4].astype(np.float32), rows[:, 4],
                           rows[:, 5].astype(int), rows[:, 6:6 + mask_dim])

    pred = pred.T
    class_end = pred.shape[1] - mask_dim
    class_scores = pred[:, 4:class_end]
    class_ids = class_scores.argmax(axis=1)
    scores = class_scores[np.arange(len(pred)), class_ids]
    keep = scores >= min_score
    pred, scores, class_ids = pred[keep], scores[keep], class_ids[keep]

    centers, sizes = pred[:, :2], pred[:, 2:4]
    boxes_xyxy = np.concatenate([centers - sizes / 2, centers + sizes / 2], axis=1)
    offsets = (class_ids * _CLASS_OFFSET_PX)[:, None].astype(np.float32)
    nms_boxes = np.concatenate([boxes_xyxy[:, :2] + offsets, sizes], axis=1)
    kept = cv2.dnn.NMSBoxes(nms_boxes.tolist(), scores.tolist(), min_score, iou_threshold)
    kept = np.asarray(kept, dtype=int).reshape(-1)
    kept = kept[np.argsort(-scores[kept])][:max_detections]
    return _Candidates(boxes_xyxy[kept].astype(np.float32), scores[kept], class_ids[kept],
                       pred[kept, class_end:])


def to_frame_box(box_xyxy: np.ndarray, letterbox: Letterbox,
                 frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
    """Una caja de la entrada del engine, en px enteros del frame, dentro del frame."""
    x1, y1, x2, y2 = box_xyxy
    xs = np.clip((np.array([x1, x2]) - letterbox.pad_x) / letterbox.scale, 0, frame_w)
    ys = np.clip((np.array([y1, y2]) - letterbox.pad_y) / letterbox.scale, 0, frame_h)
    return int(round(xs[0])), int(round(ys[0])), int(round(xs[1])), int(round(ys[1]))


def build_mask(coefs: np.ndarray, protos: np.ndarray, frame_box: tuple[int, int, int, int],
               letterbox: Letterbox, input_hw: tuple[int, int]) -> np.ndarray:
    """
    Máscara uint8 0/1 del tamaño de `frame_box`: la combinación de los prototipos con los
    coeficientes de la detección, llevada de la entrada del engine al bbox en el frame.
    """
    mask_dim, proto_h, proto_w = protos.shape
    logits = coefs @ protos.reshape(mask_dim, -1)
    mask = (1.0 / (1.0 + np.exp(-logits))).reshape(proto_h, proto_w).astype(np.float32)
    input_h, input_w = input_hw
    mask = cv2.resize(mask, (input_w, input_h), interpolation=cv2.INTER_LINEAR)

    x1, y1, x2, y2 = frame_box
    box_w, box_h = max(1, x2 - x1), max(1, y2 - y1)
    ix1 = int(round(x1 * letterbox.scale + letterbox.pad_x))
    iy1 = int(round(y1 * letterbox.scale + letterbox.pad_y))
    ix2 = max(ix1 + 1, int(round(x2 * letterbox.scale + letterbox.pad_x)))
    iy2 = max(iy1 + 1, int(round(y2 * letterbox.scale + letterbox.pad_y)))
    region = mask[iy1:iy2, ix1:ix2]
    if region.size == 0:
        return np.zeros((box_h, box_w), np.uint8)
    region = cv2.resize(region, (box_w, box_h), interpolation=cv2.INTER_LINEAR)
    return (region > _MASK_THRESHOLD).astype(np.uint8)


def to_probabilities(raw: np.ndarray) -> np.ndarray:
    """Las probabilidades por clase de un clasificador; aplica softmax si vinieron logits."""
    values = raw.reshape(-1).astype(np.float64)
    if values.min() >= 0.0 and abs(values.sum() - 1.0) <= _PROBABILITY_SUM_TOLERANCE:
        return values
    exponentials = np.exp(values - values.max())
    return exponentials / exponentials.sum()


# ── Modelos ──────────────────────────────────────────────────────────────────

class _YoloModel(AbstractModel):
    """
    Lo común a las tres tareas: cargar el engine, llevar el frame a su entrada y armar las
    detecciones. Cada subclase declara su `task` y decodifica su salida.
    """

    def __init__(self, config_manager, model_slot: str):
        super().__init__(config_manager, model_slot)
        self._runner: TensorRtRunner | None = None
        self._engine_names: dict[int, str] = {}

    @property
    def class_names(self) -> tuple[str, ...]:
        """Los de los pesos protegidos o del config; si no declaran, los del engine."""
        declared = super().class_names
        if declared or not self._engine_names:
            return declared
        return tuple(self._engine_names[i] for i in sorted(self._engine_names))

    def load(self):
        if self.is_loaded:
            return
        data = self._read_weights(self._get_model_path())
        if not data:
            return
        metadata, engine_bytes = split_ultralytics_engine(data)
        if not self._verify_task(str(metadata.get("task", ""))):
            return

        runner = TensorRtRunner()
        reason = runner.load(engine_bytes) or self._check_outputs(runner)
        if reason:
            runner.close()
            self._set_error(reason)
            logger.error(f"[Inference] Modelo '{self.model_slot}': {reason}")
            return

        self._engine_names = _read_engine_names(metadata)
        self._runner = runner
        self._set_loaded()
        input_h, input_w = runner.input_hw
        logger.info(f"[Inference] Modelo '{self.model_slot}': {self.task} TensorRT, entrada "
                    f"{input_w}x{input_h}.")

    def peek_input(self) -> InputSpec | None:
        """
        El tamaño de entrada del engine, leído de la metadata que ultralytics le pone al
        exportarlo —`imgsz`—, sin deserializarlo ni tocar la GPU. Un engine pelado, sin esa
        metadata, no lo dice: None.
        """
        peeked = self._peek_weights(self._get_model_path())
        if peeked is None:
            return None
        _, weights = peeked
        size = split_ultralytics_engine(weights)[0].get("imgsz")
        if isinstance(size, int):
            size = [size, size]
        try:
            height_px, width_px = (int(side) for side in size)
        except (TypeError, ValueError):
            return None
        if height_px <= 0 or width_px <= 0:
            return None
        return InputSpec(height_px, width_px, self.task == TASK_CLASSIFICATION)

    def unload(self):
        if self._runner is not None:
            self._runner.close()
            self._runner = None
        self._status = STATUS_UNLOADED
        self._error = None

    def predict(self, frame_bgr: np.ndarray,
                previous: list[Detection] | None = None) -> list[Detection]:
        if not self.is_loaded or frame_bgr is None or frame_bgr.size == 0:
            return []
        return self._infer(frame_bgr)

    @abstractmethod
    def _infer(self, frame: np.ndarray) -> list[Detection]:
        """Las detecciones de un frame no vacío, con el engine cargado."""

    @abstractmethod
    def _check_outputs(self, runner: TensorRtRunner) -> str:
        """'' si las salidas del engine tienen la forma que esta tarea sabe decodificar."""

    def _get_class_name(self, class_index: int) -> str:
        names = self.class_names
        if 0 <= class_index < len(names):
            return names[class_index]
        return f"class_{class_index}"

    def _read_nms_params(self) -> tuple[float, int]:
        params = self._get_params()
        iou_threshold = float(params.get("iou_threshold", _DEFAULT_IOU_THRESHOLD))
        max_detections = int(params.get("max_detections", _DEFAULT_MAX_DETECTIONS))
        return iou_threshold, max(1, max_detections)


def _read_engine_names(metadata: dict) -> dict[int, str]:
    """`names` de la metadata de ultralytics ({"0": "person"}) con claves enteras."""
    names = metadata.get("names") or {}
    if isinstance(names, list):
        return dict(enumerate(str(name) for name in names))
    result = {}
    for key, name in names.items():
        try:
            result[int(key)] = str(name)
        except (TypeError, ValueError):
            continue
    return result


class YoloDetectionModel(_YoloModel):
    """Cajas por instancia."""

    task = TASK_DETECTION

    def _check_outputs(self, runner: TensorRtRunner) -> str:
        shapes = runner.output_shapes
        if len(shapes) != 1 or len(shapes[0]) != 3:
            return f"un detector tiene una salida de 3 dimensiones; este engine da {shapes}"
        return ""

    def _infer(self, frame: np.ndarray) -> list[Detection]:
        input_nchw, letterbox = prepare_letterboxed(frame, self._runner.input_hw)
        (raw,) = self._runner.run(input_nchw).values()
        iou_threshold, max_detections = self._read_nms_params()
        candidates = decode_candidates(raw, mask_dim=0,
                                       min_score=self.min_confidence_pct / 100.0,
                                       iou_threshold=iou_threshold,
                                       max_detections=max_detections)
        frame_h, frame_w = frame.shape[:2]
        return [
            Detection(int(class_id), self._get_class_name(int(class_id)),
                      float(score) * 100.0,
                      to_frame_box(box, letterbox, frame_w, frame_h))
            for box, score, class_id in zip(candidates.boxes_xyxy, candidates.scores,
                                            candidates.class_ids, strict=True)
        ]


class YoloSegmentationModel(_YoloModel):
    """Cajas y máscara por instancia. El área y el centroide salen de la máscara."""

    task = TASK_SEGMENTATION

    def _check_outputs(self, runner: TensorRtRunner) -> str:
        shapes = sorted(runner.output_shapes, key=len)
        if len(shapes) != 2 or len(shapes[0]) != 3 or len(shapes[1]) != 4:
            return (f"un segmentador tiene una salida de 3 dimensiones y los prototipos de "
                    f"4; este engine da {shapes}")
        return ""

    def _infer(self, frame: np.ndarray) -> list[Detection]:
        input_hw = self._runner.input_hw
        input_nchw, letterbox = prepare_letterboxed(frame, input_hw)
        outputs = sorted(self._runner.run(input_nchw).values(), key=lambda array: array.ndim)
        raw, protos = outputs[0], outputs[1][0]
        iou_threshold, max_detections = self._read_nms_params()
        candidates = decode_candidates(raw, mask_dim=protos.shape[0],
                                       min_score=self.min_confidence_pct / 100.0,
                                       iou_threshold=iou_threshold,
                                       max_detections=max_detections)
        frame_h, frame_w = frame.shape[:2]
        detections = []
        for box, score, class_id, coefs in zip(candidates.boxes_xyxy, candidates.scores,
                                               candidates.class_ids, candidates.mask_coefs,
                                               strict=True):
            frame_box = to_frame_box(box, letterbox, frame_w, frame_h)
            mask = build_mask(coefs, protos, frame_box, letterbox, input_hw)
            detections.append(Detection(
                int(class_id), self._get_class_name(int(class_id)), float(score) * 100.0,
                frame_box, area_px=int(mask.sum()),
                centroid_px=_mask_centroid(mask, frame_box), mask=mask))
        return detections


def _mask_centroid(mask: np.ndarray,
                   frame_box: tuple[int, int, int, int]) -> tuple[int, int] | None:
    """Centroide de la máscara en px del frame; None si quedó vacía (lo pone el bbox)."""
    moments = cv2.moments(mask, binaryImage=True)
    if moments["m00"] == 0:
        return None
    x1, y1 = frame_box[:2]
    return (x1 + int(round(moments["m10"] / moments["m00"])),
            y1 + int(round(moments["m01"] / moments["m00"])))


class YoloClassificationModel(_YoloModel):
    """
    Un veredicto de la imagen entera: una sola detección del tamaño del frame con la clase
    más probable, o ninguna si no llega al umbral. El pipeline la vuelve label.
    """

    task = TASK_CLASSIFICATION

    def _check_outputs(self, runner: TensorRtRunner) -> str:
        shapes = runner.output_shapes
        if len(shapes) != 1 or len(shapes[0]) != 2:
            return f"un clasificador tiene una salida (1, clases); este engine da {shapes}"
        return ""

    def _infer(self, frame: np.ndarray) -> list[Detection]:
        (raw,) = self._runner.run(prepare_center_crop(frame, self._runner.input_hw)).values()
        probabilities = to_probabilities(raw)
        class_id = int(probabilities.argmax())
        confidence_pct = float(probabilities[class_id]) * 100.0
        if confidence_pct < self.min_confidence_pct:
            return []
        frame_h, frame_w = frame.shape[:2]
        return [Detection(class_id, self._get_class_name(class_id), confidence_pct,
                          (0, 0, frame_w, frame_h))]
