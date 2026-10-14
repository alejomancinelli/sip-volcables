"""
Lo del proyecto que entra al motor de inferencia: qué detecciones cuentan, qué significan
y qué se dibuja además del resultado.

**Específico del proyecto — no cross-portar tal cual.** Es uno de los archivos que se
reescriben en cada fork, junto con `pipeline.py` y `metrics.py`. Lo que se reescribe es el
cuerpo de `build_hooks()`; `EngineHooks` es la forma en que sale y no se toca.

Es acá, y no en `main.py`, porque lo usan dos: la app y la corrida de imágenes sueltas. Las
dos tienen que armar el motor con los mismos hooks, o una imagen suelta pasaría con un
analyzer que no es el que corre en la línea.

**`process:` se lee una sola vez, arriba de `build_hooks()`**, y los mismos números van a
cada hook. Las factories de `annotations.py` y de `metrics.py` no leen el config a
propósito: reciben números. Leerlo una vez es lo que garantiza que el annotator que dibuja
un límite y el analyzer que mide contra ese límite usen el mismo valor, y que el operador y
el PLC no puedan estar mirando cosas distintas. Así cada factory se testea con tres números
y sin archivo.
"""

from collections.abc import Callable
from dataclasses import dataclass

from system.config_manager import ConfigManager

from . import annotations
from .metrics import compute_metrics
from .result import Detection, InferenceResult

Classifier = Callable[[list[Detection], str], list[Detection]]
Analyzer = Callable[[InferenceResult], dict]


@dataclass(frozen=True)
class EngineHooks:
    """
    Los tres puntos de extensión del motor que son del proyecto. None = el motor no lo usa.

        classifier : qué detecciones cuentan y con qué clase. Recibe
                     `(detections, camera_slot)` y devuelve las que quedan, ya en
                     coordenadas del frame completo; lo que descarte no cuenta para la
                     confianza ni para `min_detections`, y la clase que deje en
                     `class_index` es la que colorea el overlay. Es donde va lo que depende
                     de la cámara y el modelo no puede saber: una escala de píxel, un filtro
                     de tamaño, una clase que sale de la medida.
        analyzer   : qué significan las detecciones; ver `metrics.py`.
        annotator  : qué se dibuja encima del anotado estándar; ver `annotations.py`.
    """

    classifier: Classifier | None = None
    analyzer: Analyzer | None = None
    annotator: annotations.Annotator | None = None


def build_hooks(config: ConfigManager) -> EngineHooks:
    """
    Arma los hooks del proyecto con los valores de `process:`.

    El template no filtra ni reetiqueta, cuenta con el analyzer genérico, y dibuja el
    límite de carga sólo si alguna cámara lo declara.
    """
    max_load_y_px = config.get("process.max_load_y_px", {}) or {}

    annotator = None
    if max_load_y_px:
        annotator = annotations.chain(annotations.max_load_line_annotator(max_load_y_px))

    return EngineHooks(analyzer=compute_metrics, annotator=annotator)
