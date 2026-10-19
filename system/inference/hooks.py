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

_FLOW_CLASS = "flow"   # la clase de `grain_cls` que dice que cae grano
_PERSON_PIPELINE_TYPE = "person_safety"


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

    No filtra ni reetiqueta y cuenta con el analyzer genérico. Dibuja el límite de carga
    sólo si alguna cámara lo declara, y el estado de la descarga —borde y panel OK / NOK del
    grano y de las personas— sólo si el config declara la sección `process.grain_flow`.

    Qué pipelines son de personas y qué cámaras miran sale de `inference.pipelines`: los
    habilitados de tipo `person_safety`, con `cameras: []` = todas las del config. La zona
    de exclusión sale de `process.person_safety.exclusion_zone_px`.

    Con `inference.overlay.draw_timestamp` en false la hora la dibuja el proyecto, del
    tamaño del frame y última, encima del borde: es una sola clave para que nunca salgan
    las dos ni ninguna sin quererlo.
    """
    max_load_y_px = config.get("process.max_load_y_px", {}) or {}
    has_grain_flow = isinstance(config.get("process.grain_flow"), dict)

    selected = []
    if has_grain_flow:
        person_pipelines, person_cameras = _read_person_pipelines(config)
        selected.append(annotations.unload_status_annotator(
            flow_class=_FLOW_CLASS,
            person_pipelines=person_pipelines,
            person_cameras=person_cameras,
            exclusion_zone_px_by_camera=config.get(
                "process.person_safety.exclusion_zone_px", {}) or {},
        ))
    if max_load_y_px:
        selected.append(annotations.max_load_line_annotator(max_load_y_px))
    if not config.get("inference.overlay.draw_timestamp", True):
        selected.append(annotations.timestamp_annotator())
    annotator = annotations.chain(*selected) if selected else None

    return EngineHooks(analyzer=compute_metrics, annotator=annotator)


def _read_person_pipelines(config: ConfigManager) -> tuple[set[str], set[str]]:
    """Los slots habilitados de tipo `person_safety` y las cámaras que miran entre todos."""
    all_cameras = set((config.get("cameras", {}) or {}).keys())
    pipelines: set[str] = set()
    cameras: set[str] = set()
    for pipeline_slot, section in (config.get("inference.pipelines", {}) or {}).items():
        if not isinstance(section, dict) or not section.get("enabled", True):
            continue
        if section.get("type") != _PERSON_PIPELINE_TYPE:
            continue
        pipelines.add(pipeline_slot)
        cameras |= set(section.get("cameras") or []) or all_cameras
    return pipelines, cameras
