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

from . import annotations, person_safety, stopper_check
from .metrics import build_analyzer
from .pipeline import FLOW_CLASS
from .result import Detection, InferenceResult

Classifier = Callable[[list[Detection], str], list[Detection]]
Analyzer = Callable[[InferenceResult], dict]

GRAIN_PIPELINE_TYPE = "grain_flow"
PERSON_PIPELINE_TYPE = "person_safety"
PERSON_SECTION = "process.person_safety"
STOPPER_PIPELINE_TYPE = "stopper_check"

# Las clases de `stopper_seg`.
STOPPER_CLASS = "stopper"
WHEEL_CLASS = "wheel"


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

    No filtra ni reetiqueta y cuenta con el analyzer genérico.

    Qué pipelines hay de cada proceso y qué cámaras miran sale de `inference.pipelines` (ver
    `read_pipelines()`), no de una clave propia de `process:`. Con algún pipeline del grano se
    dibuja el estado de la descarga: el panel OK / NOK del grano y de las personas. Con alguno
    de personas, el analyzer suma el veredicto filtrado de personas
    (`person_safety.py`) con la zona de exclusión y el filtro de `process.person_safety`; la
    misma zona es la que dibuja el annotator, así que la pantalla y el PLC no pueden mirar
    zonas distintas. Con algún pipeline `stopper_check` se arma la regla de las calzas
    (`stopper_check.py`) con los valores de `process.stopper_check`, y la misma regla da las
    señales del analyzer y el panel OK / NOK de la calza levantada y del contacto con la
    rueda. Los rótulos de los dos paneles salen en el idioma de
    `ui.language`.

    Con `inference.overlay.draw_timestamp` en false la hora la dibuja el proyecto, del
    tamaño del frame y última, encima de todo: es una sola clave para que nunca salgan las
    dos ni ninguna sin quererlo.
    """
    grain_pipelines, _ = read_pipelines(config, GRAIN_PIPELINE_TYPE)
    language = str(config.get("ui.language", "") or "")

    person_pipelines, person_cameras = read_pipelines(config, PERSON_PIPELINE_TYPE)
    zone_by_camera = person_safety.parse_zones(
        config.get(f"{PERSON_SECTION}.exclusion_zone_px", {}) or {})
    extras = []
    if person_pipelines:
        extras.append(person_safety.build_analyzer(
            person_pipelines=person_pipelines,
            zone_by_camera=zone_by_camera,
            confirm_hits=int(config.get(f"{PERSON_SECTION}.confirm_hits",
                                        person_safety.DEFAULT_CONFIRM_HITS)),
            confirm_window=int(config.get(f"{PERSON_SECTION}.confirm_window",
                                          person_safety.DEFAULT_CONFIRM_WINDOW)),
            release_s=float(config.get(f"{PERSON_SECTION}.release_s",
                                       person_safety.DEFAULT_RELEASE_S)),
        ))

    selected = []
    if grain_pipelines:
        selected.append(annotations.unload_status_annotator(
            flow_class=FLOW_CLASS,
            person_cameras=set(person_cameras),
            zone_by_camera=zone_by_camera,
            language=language,
        ))
    stopper_pipelines, _ = read_pipelines(config, STOPPER_PIPELINE_TYPE)
    if stopper_pipelines:
        section = "process.stopper_check"
        rule = stopper_check.StopperRule(
            stopper_class=STOPPER_CLASS,
            wheel_class=WHEEL_CLASS,
            raised_y_px_by_camera=_read_camera_map(config, f"{section}.raised_y_px"),
            min_raised_px_by_camera=_read_camera_map(config, f"{section}.min_raised_px"),
            max_gap_px_by_camera=_read_camera_map(config, f"{section}.max_gap_px"),
            wheel_side_by_camera=_read_camera_map(config, f"{section}.wheel_side"),
        )
        extras.append(stopper_check.build_analyzer(rule, stopper_pipelines=stopper_pipelines))
        selected.append(annotations.stopper_status_annotator(
            rule, stopper_pipelines=stopper_pipelines, language=language))
    if not config.get("inference.overlay.draw_timestamp", True):
        selected.append(annotations.timestamp_annotator())
    annotator = annotations.chain(*selected) if selected else None

    return EngineHooks(analyzer=build_analyzer(*extras), annotator=annotator)


def read_pipelines(config: ConfigManager, pipeline_type: str) -> tuple[set[str], list[str]]:
    """
    Los slots habilitados de ese `type` y las cámaras que miran entre todos.

    Las cámaras salen en el orden de `cameras:` del config, sin repetir; un pipeline con
    `cameras: []` las mira todas. Lo usa también la pestaña de proceso, para armar un grupo
    por cámara sin repetir esta regla.
    """
    all_cameras = list((config.get("cameras", {}) or {}).keys())
    pipelines: set[str] = set()
    watched: set[str] = set()
    for pipeline_slot, section in (config.get("inference.pipelines", {}) or {}).items():
        if not isinstance(section, dict) or not section.get("enabled", True):
            continue
        if section.get("type") != pipeline_type:
            continue
        pipelines.add(pipeline_slot)
        watched |= set(section.get("cameras") or all_cameras)
    ordered = [camera_slot for camera_slot in all_cameras if camera_slot in watched]
    return pipelines, ordered + sorted(watched - set(ordered))


def _read_camera_map(config: ConfigManager, key: str) -> dict:
    """Un mapa por cámara del config, sin las cámaras en null: null es «sin calibrar»."""
    values = config.get(key, {}) or {}
    return {camera_slot: value for camera_slot, value in values.items() if value is not None}
