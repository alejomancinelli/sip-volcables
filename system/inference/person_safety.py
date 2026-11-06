"""
¿Hay alguien en la zona de exclusión de cada cámara? El veredicto con el que el PLC bloquea
el arranque de la descarga.

Módulo puro: sin Qt, sin ConfigManager y sin I/O.

**Específico del proyecto — no cross-portar tal cual.** Lo genérico —confirmar y liberar
una presencia en el tiempo— es de `presence.py`; acá va qué cuenta como presencia en esta
planta y cómo sale.

Una persona cuenta si el punto medio del borde inferior de su caja —los pies— cae en el
polígono de su cámara: con la perspectiva, es donde está parada y no dónde la tapa el torso.
Una cámara sin zona cuenta a cualquier persona del frame.

El veredicto de un frame suelto no sale: pasa por un `PresenceFilter` por cámara. El
analyzer (`build_analyzer()`) deja tres métricas, sin la cámara en el nombre —en la
telemetría la cámara es un tag, y qué registro le toca a cada una lo decide el mapa—:

  - `in_frame_count` e `in_zone_count`: las personas del frame y las que tienen los pies en
    la zona, crudas. Son para mirar —en la telemetría se comparan con el filtrado para
    ajustar el filtro—, no para decidir.
  - `safety_ok`: el filtrado, **1 nadie en la zona y 0 cualquier otra cosa**: alguien
    confirmado o todavía sin liberar. El 0 es también lo que tiene que leer quien lo consuma
    sin veredicto —el equipo arrancando, el pipeline caído, frames que no se pueden juzgar—,
    así que el estado seguro y la falla son el mismo número y una falla nunca se lee como
    «no hay nadie». Que vuelva a 0 cuando el analyzer deja de llamarse es de quien publica
    (`max_age_s` en el mapa de registros), porque sin resultados nuevos acá no corre nada.

**Guarda estado**, a diferencia del analyzer del template: un filtro por par (pipeline,
cámara). El analyzer es uno solo y lo comparten todos los motores, pero cada par lo actualiza
un solo hilo —el de su pipeline— y tiene su propia entrada del dict.
"""

from collections.abc import Callable

import cv2
import numpy as np

from .presence import PresenceFilter
from .result import Detection, InferenceResult

SAFETY_METRIC = "safety_ok"

DEFAULT_CONFIRM_HITS = 4
DEFAULT_CONFIRM_WINDOW = 6
DEFAULT_RELEASE_S = 5.0


def parse_zones(points_by_camera: dict[str, list]) -> dict[str, np.ndarray]:
    """
    {cámara: polígono} listo para `cv2`, desde los puntos `[x, y]` del config.

    Una cámara en null o con la lista vacía no tiene zona: es lo que deja el editor al
    borrarla. Levanta ValueError con la cámara si un polígono tiene puntos pero menos de
    tres: una zona mal escrita no puede pasar a contar todo el frame sin que nadie se entere.
    """
    zone_by_camera = {}
    for camera_slot, points in (points_by_camera or {}).items():
        if not points:
            continue
        zone = np.asarray(points, dtype=np.int32)
        if zone.ndim != 2 or zone.shape[0] < 3 or zone.shape[1] != 2:
            raise ValueError(f"La zona de exclusión de {camera_slot} no es un polígono de "
                             f"al menos tres puntos [x, y]: {points}")
        zone_by_camera[camera_slot] = zone.reshape(-1, 1, 2)
    return zone_by_camera


def count_in_zone(detections: list[Detection], zone: np.ndarray | None) -> int:
    """Cuántas detecciones tienen los pies —el centro del borde inferior— en la zona."""
    if zone is None:
        return len(detections)
    count = 0
    for detection in detections:
        x1, _, x2, y2 = detection.bbox_px
        feet_px = (float(x1 + x2) / 2, float(y2))
        if cv2.pointPolygonTest(zone, feet_px, False) >= 0:
            count += 1
    return count


def build_analyzer(*, person_pipelines: set[str],
                   zone_by_camera: dict[str, np.ndarray] | None = None,
                   confirm_hits: int = DEFAULT_CONFIRM_HITS,
                   confirm_window: int = DEFAULT_CONFIRM_WINDOW,
                   release_s: float = DEFAULT_RELEASE_S
                   ) -> Callable[[InferenceResult], dict]:
    """
    Las métricas de personas de un resultado confiable; {} si no es de `person_pipelines`.

    Levanta ValueError al armarse con un filtro imposible, no en el primer frame.
    """
    zone_by_camera = dict(zone_by_camera or {})
    filters: dict[tuple[str, str], PresenceFilter] = {}

    def build_filter() -> PresenceFilter:
        return PresenceFilter(confirm_hits=confirm_hits, confirm_window=confirm_window,
                              release_s=release_s)

    build_filter()

    def analyze(result: InferenceResult) -> dict:
        if result.pipeline_slot not in person_pipelines:
            return {}
        camera_slot = result.camera_slot
        key = (result.pipeline_slot, camera_slot)
        presence = filters.get(key)
        if presence is None:
            presence = filters[key] = build_filter()
        in_zone_count = count_in_zone(result.detections, zone_by_camera.get(camera_slot))
        is_present = presence.update(result.timestamp_s, in_zone_count > 0)
        return {"in_frame_count": len(result.detections),
                "in_zone_count": in_zone_count,
                SAFETY_METRIC: 0 if is_present else 1}

    analyze.__name__ = (f"person_safety(pipelines={sorted(person_pipelines)}, "
                        f"confirm={confirm_hits}/{confirm_window}, release_s={release_s})")
    return analyze
