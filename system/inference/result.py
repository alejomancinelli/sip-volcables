"""
Resultado de una inferencia: el objeto que cruza del motor a la UI, al dataset, a la
telemetría y al PLC.

Módulo puro: sin Qt, sin ConfigManager y sin I/O. Sólo define la forma de lo que
produce una inferencia, para que ningún consumidor dependa del framework que la
calculó ni del pipeline que la corrió.

Maquinaria genérica: reutilizable entre proyectos. Lo único específico de cada fork es
el contenido de `metrics` —lo que devuelve el analyzer— y los nombres de clase, que
salen del config.

Cinco puntos del contrato que no se ven en los campos:
  - **Todas las coordenadas son del frame de cámara**, en píxeles y con el origen
    arriba a la izquierda, incluso cuando la inferencia corrió sobre un ROI: quien
    recorta vuelve a sumar el origen con `offset_detections` antes de entregar —y, si
    la región llegó a otra escala, la devuelve primero con `scale_detections`—. Así
    una detección se dibuja sobre el frame crudo sin saber si hubo recorte.
  - **`mask` va recortada al bbox**, no al tamaño del frame: con cientos de instancias,
    una máscara por detección del tamaño de la imagen no entra en memoria.
  - **`metrics` es un dict plano** de valores numéricos o booleanos. Es lo que viaja al
    panel del frame anotado, al JSON del dataset, a los fields de la telemetría y
    —escalado por el esquema— a los registros del PLC; un dict anidado no entra en
    ninguno de los cuatro.
  - **`labels` son los veredictos de frame**, texto y no números: lo que una etapa de
    clasificación dice de la imagen entera y no de una instancia («cinta llena», «foco
    fuera de rango»). Van aparte de `detections` porque no tienen forma ni posición, y
    aparte de `metrics` porque son texto: quien necesite publicarlos al PLC los traduce
    a un número o a un bit en el analyzer, que es el que sabe a qué equivalen.
  - **`roi_state` es el veredicto del recuadro del ROI**: con qué estado se pinta la región
    que mira el pipeline —`ROI_STATE_OK`, `ROI_STATE_NOK` o `ROI_STATE_WARNING`—, o
    `ROI_STATE_NONE` si no dice nada. Lo declara el pipeline junto a sus `labels`, porque
    sólo el código del proyecto sabe qué veredicto equivale a «bien» o «mal». Un resultado
    con `is_valid` en False no se pinta aunque traiga un estado: sus números no son una
    medición, y un recuadro verde sobre algo que no se midió es peor que ninguno.
  - **Los frames viajan por referencia.** `source_bgr` es el frame que entró,
    `region_bgr` la imagen sobre la que corrió el pipeline —el recorte del ROI, o la
    vista que el decodificador cortó a resolución nativa— y `annotated_bgr` una copia
    con las anotaciones dibujadas: quien recibe el resultado no los modifica.

`is_valid` es la única señal de confianza: en False el resultado se emite igual —el
frame anotado y la iluminación siguen sirviendo— pero sus números no son una medición
del proceso y no se publican como tal. `invalid_reason` dice por qué, con el
vocabulario REASON_* de este módulo.
"""

import time
from dataclasses import dataclass, field

import cv2
import numpy as np

# Vocabulario de `invalid_reason`, para que la UI y los tests no repitan literales.
REASON_NO_MODEL = "no_model"                # ninguna etapa del pipeline tiene modelo cargado
REASON_DARK_FRAME = "dark_frame"            # iluminación por debajo del mínimo de la cámara
REASON_LOW_CONFIDENCE = "low_confidence"    # confianza media por debajo del mínimo del pipeline
REASON_FEW_DETECTIONS = "few_detections"    # menos detecciones que el mínimo del pipeline
REASON_ERROR = "error"                      # excepción en alguna etapa
REASON_NO_VIEW = "no_view"                  # el pipeline pide la vista nativa y no llegó

# Vocabulario de `roi_state`. Es el único lugar donde están los textos: el pipeline los
# declara, el overlay los pinta y el JSON del dataset los guarda.
ROI_STATE_NONE = ""
ROI_STATE_OK = "ok"
ROI_STATE_NOK = "nok"
ROI_STATE_WARNING = "warning"
ROI_STATES = (ROI_STATE_OK, ROI_STATE_NOK, ROI_STATE_WARNING)


@dataclass
class Detection:
    """
    Una instancia detectada, en coordenadas del frame de cámara.

    `area_px` y `centroid_px` se completan desde el bbox cuando no vienen dados, así
    que ninguna implementación de modelo repite esa cuenta. Un modelo con máscara sí
    los pasa: el área de la máscara es la superficie real y el centroide del bbox no
    es el de la forma.
    """

    class_index: int
    class_name: str
    confidence_pct: float                    # 0–100
    bbox_px: tuple[int, int, int, int]       # (x1, y1, x2, y2)
    area_px: int = 0                         # de la máscara, o del bbox si no hay
    centroid_px: tuple[int, int] | None = None
    mask: np.ndarray | None = None           # uint8 0/1, del tamaño del bbox

    def __post_init__(self):
        x1, y1, x2, y2 = self.bbox_px
        if self.area_px <= 0:
            self.area_px = max(0, x2 - x1) * max(0, y2 - y1)
        if self.centroid_px is None:
            self.centroid_px = ((x1 + x2) // 2, (y1 + y2) // 2)

    def to_dict(self) -> dict:
        """Forma serializable. La máscara no va: se informa si estaba."""
        return {
            "class_index": self.class_index,
            "class_name": self.class_name,
            "confidence_pct": round(float(self.confidence_pct), 1),
            "bbox_px": list(self.bbox_px),
            "area_px": int(self.area_px),
            "centroid_px": None if self.centroid_px is None else list(self.centroid_px),
            "has_mask": self.mask is not None,
        }


@dataclass
class InferenceResult:
    """
    Todo lo que salió de inferir un frame de una cámara.

    Un resultado por frame procesado. El promedio en el tiempo no vive acá: se calcula
    sobre varios resultados con las funciones de `analysis`, donde lo necesite quien
    publique.
    """

    camera_slot: str
    pipeline_slot: str = ""
    timestamp_s: float = field(default_factory=time.time)
    detections: list[Detection] = field(default_factory=list)
    labels: dict = field(default_factory=dict)           # veredictos de frame: {nombre: valor}
    roi_state: str = ROI_STATE_NONE          # cómo se pinta el recuadro del ROI: ROI_STATE_*
    metrics: dict = field(default_factory=dict)
    confidence_pct: float = 0.0              # media de las detecciones que sobrevivieron
    inference_time_ms: float = 0.0           # frame completo: recorte, etapas y filtros
    cycle_time_ms: float = 0.0               # desde el arranque del frame anterior de la cámara; 0 = no hay
    stage_times_ms: dict = field(default_factory=dict)   # {model_slot: ms}
    illumination_pct: int = 0                # brillo medio del ROI, 0–100
    roi_px: tuple[int, int, int, int] | None = None      # None = se infirió el frame entero
    source_bgr: np.ndarray | None = None
    region_bgr: np.ndarray | None = None     # lo que vio el pipeline; None = no llegó a verlo
    annotated_bgr: np.ndarray | None = None
    is_valid: bool = True
    invalid_reason: str = ""
    is_synthetic: bool = False               # detecciones de un modelo sintético, no una medición

    @property
    def detection_count(self) -> int:
        return len(self.detections)

    def to_dict(self) -> dict:
        """
        Forma serializable, con claves estables.

        Es lo que guarda el recolector de dataset al lado del frame anotado y lo que
        leen las condiciones de guardado, así que `confidence_pct` y los nombres de
        `metrics` son los que ven esos filtros. Los frames no van.
        """
        return {
            "camera_slot": self.camera_slot,
            "pipeline_slot": self.pipeline_slot,
            "timestamp_s": round(float(self.timestamp_s), 3),
            "is_valid": self.is_valid,
            "invalid_reason": self.invalid_reason,
            "is_synthetic": self.is_synthetic,
            "illumination_pct": int(self.illumination_pct),
            "confidence_pct": round(float(self.confidence_pct), 1),
            "detection_count": self.detection_count,
            "inference_time_ms": round(float(self.inference_time_ms), 1),
            "cycle_time_ms": round(float(self.cycle_time_ms), 1),
            "stage_times_ms": dict(self.stage_times_ms),
            "roi_px": None if self.roi_px is None else list(self.roi_px),
            "labels": dict(self.labels),
            "roi_state": self.roi_state,
            "metrics": dict(self.metrics),
            "detections": [detection.to_dict() for detection in self.detections],
        }


def offset_detections(detections: list[Detection], x_px: int, y_px: int) -> list[Detection]:
    """
    Corre las detecciones al espacio del frame de cámara. Modifica las que recibe.

    Lo llama quien recortó un ROI, con el origen del recorte: el modelo trabaja en el
    espacio del frame que le dieron y no tiene por qué saber de dónde salió. Con
    (0, 0) no hay nada que correr.
    """
    if not x_px and not y_px:
        return detections
    for detection in detections:
        x1, y1, x2, y2 = detection.bbox_px
        detection.bbox_px = (x1 + x_px, y1 + y_px, x2 + x_px, y2 + y_px)
        if detection.centroid_px is not None:
            cx, cy = detection.centroid_px
            detection.centroid_px = (cx + x_px, cy + y_px)
    return detections


def scale_detections(detections: list[Detection], scale_x: float,
                     scale_y: float) -> list[Detection]:
    """
    Lleva las detecciones de una imagen a otra de distinta escala. Modifica las que recibe.

    Lo llama quien le dio al pipeline la región a otra resolución que el frame —la vista
    que el decodificador corta a tamaño nativo— con el factor de esa imagen al frame, y
    después corre el origen con `offset_detections`. Las máscaras se reescalan al bbox
    nuevo, así que el área de una detección con máscara se vuelve a contar. Con (1, 1)
    no hay nada que hacer.
    """
    if scale_x == 1.0 and scale_y == 1.0:
        return detections
    for detection in detections:
        x1, y1, x2, y2 = detection.bbox_px
        x1, x2 = round(x1 * scale_x), round(x2 * scale_x)
        y1, y2 = round(y1 * scale_y), round(y2 * scale_y)
        detection.bbox_px = (x1, y1, x2, y2)
        if detection.centroid_px is not None:
            cx, cy = detection.centroid_px
            detection.centroid_px = (round(cx * scale_x), round(cy * scale_y))
        if detection.mask is not None:
            # Vecino más cercano: la máscara es 0/1 y un bilineal le inventaría valores.
            detection.mask = cv2.resize(detection.mask, (max(1, x2 - x1), max(1, y2 - y1)),
                                        interpolation=cv2.INTER_NEAREST)
            detection.area_px = int(np.count_nonzero(detection.mask))
        else:
            detection.area_px = max(0, x2 - x1) * max(0, y2 - y1)
    return detections
