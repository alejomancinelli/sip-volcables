"""
Dibujos propios de la instalación, encima del anotado estándar.

Módulo puro: sin Qt, sin ConfigManager y sin I/O. Cada factory devuelve un annotator —una
función que recibe el frame ya anotado y el resultado, y dibuja in-place— y `hooks.py` los
compone para el motor:

    engine = InferenceThread(config, pipeline, analyzer=compute_metrics,
                             annotator=chain(
                                 max_load_line_annotator({"camera_1": 430}),
                                 ...))

**Específico del proyecto — no cross-portar tal cual.** Lo que sale de la inferencia lo
dibuja `overlay`, que es genérico; acá van las referencias que sólo existen en esta
planta: un límite de carga, el borde de una zona de rechazo, un eje de calibración.
Separado de `overlay` porque ese archivo se copia al próximo proyecto sin cambios, y esto
no.

Las factories reciben la geometría en píxeles y no leen el config: quién lee la sección
`process` es `hooks.py`, igual que `main.py` con las condiciones de guardado del
recolector. El mismo valor alimenta al annotator que dibuja el límite y al analyzer que
calcula el porcentaje contra ese límite, así que se lee una vez y no pueden discrepar. Así un annotator se
testea con tres números y sin archivo, y la misma función sirve para una cámara o para
diez.

Cada annotator lleva su `__name__` armado con los valores que recibió, así el aviso del
motor dice cuál falló.
"""

from collections.abc import Callable

import cv2
import numpy as np

from . import overlay
from .result import InferenceResult

Annotator = Callable[[np.ndarray, InferenceResult], None]

_MAX_LOAD_BGR = (60, 60, 230)   # rojo: es un límite, no una referencia neutra

_GRAIN_FLOW_LABEL = "grain_flow"   # el label que deja GrainFlowPipeline
_STATUS_OK_BGR = (40, 200, 40)
_STATUS_NOK_BGR = (40, 40, 230)
_STATUS_UNKNOWN_BGR = (170, 170, 170)
# Borde y texto del estado, en fracción del lado menor del frame: la cámara puede llegar a
# tamaño completo o reducida por el decodificador, y tiene que leerse igual en las dos.
_STATUS_BORDER_FRACTION = 0.03
_STATUS_BORDER_MIN_PX = 12      # más que el margen del panel, para que siempre lo pise
_STATUS_TEXT_FRACTION = 0.05    # alto del texto del panel
_ZONE_THICKNESS_FRACTION = 0.004
# Un veredicto más viejo que esto, contado desde el más nuevo de su cámara, ya no cuenta:
# el pipeline que lo dio se cayó o dejó de recibir frames.
_VERDICT_MAX_AGE_S = 2.0


def chain(*annotators: Annotator) -> Annotator:
    """
    Junta varios annotators en uno. El motor recibe una sola función.

    Los corre en orden, así que el último dibuja encima. Uno que falla corta la cadena: el
    motor lo avisa y el frame conserva lo que se haya dibujado hasta ahí.
    """
    selected = [annotator for annotator in annotators if annotator is not None]

    def draw(frame_bgr: np.ndarray, result: InferenceResult):
        for annotator in selected:
            annotator(frame_bgr, result)

    draw.__name__ = f"chain({', '.join(getattr(a, '__name__', repr(a)) for a in selected)})"
    return draw


def max_load_line_annotator(y_px_by_camera: dict[str, int], *,
                            label: str = "MAX LOAD") -> Annotator:
    """
    Dibuja el límite de carga de la cinta: una línea horizontal a la altura configurada.

    La altura es por cámara —cada cinta tiene la suya y el montaje de la cámara la
    mueve—, y una cámara que no figura en el dict no lleva línea. Se dibuja siempre,
    haya o no detecciones: es la referencia contra la que el operador mira el material,
    y una línea que aparece y desaparece no sirve para eso.
    """
    def draw(frame_bgr: np.ndarray, result: InferenceResult):
        y_px = y_px_by_camera.get(result.camera_slot)
        if y_px is None or frame_bgr is None or frame_bgr.size == 0:
            return
        height_px, width_px = frame_bgr.shape[:2]
        if not 0 <= int(y_px) < height_px:
            return
        overlay.draw_line(frame_bgr, (0, int(y_px)), (width_px, int(y_px)),
                          _MAX_LOAD_BGR, thickness=2, label=label)

    draw.__name__ = f"max_load_line({y_px_by_camera})"
    return draw


def labels_panel_annotator() -> Annotator:
    """
    Dibuja los veredictos de frame del resultado —`labels`— en un panel arriba a la izquierda.

    Un veredicto no tiene caja, así que sin esto no se ve: es la forma de mostrar el
    `grain_flow` del clasificador. Un resultado sin labels no dibuja nada, así que en una
    cámara que miran varios pipelines el panel lo pone sólo el que tiene veredicto.
    """
    def draw(frame_bgr: np.ndarray, result: InferenceResult):
        if not result.labels or frame_bgr is None or frame_bgr.size == 0:
            return
        overlay.draw_text_panel(frame_bgr, list(result.labels.items()))

    draw.__name__ = "labels_panel"
    return draw


def unload_status_annotator(*, flow_class: str, person_pipelines: set[str],
                            person_cameras: set[str],
                            exclusion_zone_px_by_camera: dict[str, list] | None = None,
                            ) -> Annotator:
    """
    Dibuja el estado de la descarga de cada cámara: un borde en todo el frame, la zona de
    exclusión y un panel con el veredicto del grano y, si la cámara tiene detección de
    personas, el de personas.

    Verde sólo si cae grano —el label `grain_flow` es `flow_class`— y no hay ninguna persona
    en la zona de exclusión; rojo en cualquier otro caso. En una cámara de `person_cameras`
    sin un veredicto vigente de personas también es rojo, con el panel en `--`: un pipeline
    de personas caído no puede dar verde. Una persona cuenta si el punto medio del borde
    inferior de su caja —los pies— cae en la zona de su cámara; una cámara sin zona cuenta
    a cualquier persona del frame.

    **Guarda estado.** Grano y personas son dos pipelines y cada resultado trae sólo su
    veredicto, así que el annotator —uno solo, compartido por todos los motores— recuerda el
    último de cada cámara y dibuja el combinado con cualquiera de los dos resultados. El de
    personas se reconoce por su `pipeline_slot`, que tiene que estar en `person_pipelines`.
    Un resultado no confiable no actualiza nada. Sin un veredicto vigente del grano no se
    dibujan ni el borde ni el panel. Lo llaman los hilos de inferencia y el de la GUI: cada
    escritura reemplaza una entrada entera del dict, que es atómico bajo el GIL.
    """
    zone_by_camera = {}
    for camera_slot, points in (exclusion_zone_px_by_camera or {}).items():
        zone = np.asarray(points, dtype=np.int32)
        if zone.ndim != 2 or zone.shape[0] < 3 or zone.shape[1] != 2:
            raise ValueError(f"La zona de exclusión de {camera_slot} no es un polígono de "
                             f"al menos tres puntos [x, y]: {points}")
        zone_by_camera[camera_slot] = zone.reshape(-1, 1, 2)
    # (cámara, "grain" | "person") -> (timestamp_s, is_ok)
    latest: dict[tuple[str, str], tuple[float, bool]] = {}

    def remember(camera_slot: str, kind: str, timestamp_s: float, is_ok: bool):
        previous = latest.get((camera_slot, kind))
        if previous is None or timestamp_s >= previous[0]:
            latest[(camera_slot, kind)] = (timestamp_s, is_ok)

    def draw(frame_bgr: np.ndarray, result: InferenceResult):
        if frame_bgr is None or frame_bgr.size == 0:
            return
        camera_slot = result.camera_slot
        zone = zone_by_camera.get(camera_slot)
        verdict = result.labels.get(_GRAIN_FLOW_LABEL)
        if result.is_valid and verdict is not None:
            remember(camera_slot, "grain", result.timestamp_s, verdict == flow_class)
        elif result.is_valid and result.pipeline_slot in person_pipelines:
            remember(camera_slot, "person", result.timestamp_s,
                     not _has_person_in_zone(result.detections, zone))

        grain = latest.get((camera_slot, "grain"))
        person = latest.get((camera_slot, "person"))
        newest_s = max(entry[0] for entry in (grain, person, (result.timestamp_s, True))
                       if entry is not None)
        is_grain_fresh = grain is not None and newest_s - grain[0] <= _VERDICT_MAX_AGE_S
        is_person_fresh = person is not None and newest_s - person[0] <= _VERDICT_MAX_AGE_S
        expects_person = camera_slot in person_cameras
        person_bgr = (_STATUS_UNKNOWN_BGR if not is_person_fresh
                      else _STATUS_OK_BGR if person[1] else _STATUS_NOK_BGR)

        side_px = min(frame_bgr.shape[:2])
        if zone is not None:
            cv2.polylines(frame_bgr, [zone], True, person_bgr,
                          max(2, int(round(side_px * _ZONE_THICKNESS_FRACTION))), cv2.LINE_AA)
        if not is_grain_fresh:
            return

        is_grain_ok = grain[1]
        is_person_ok = is_person_fresh and person[1]
        is_ok = is_grain_ok and (is_person_ok or not expects_person)
        border_bgr = _STATUS_OK_BGR if is_ok else _STATUS_NOK_BGR
        border_px = max(_STATUS_BORDER_MIN_PX, int(round(side_px * _STATUS_BORDER_FRACTION)))
        frame_bgr[:border_px] = border_bgr
        frame_bgr[-border_px:] = border_bgr
        frame_bgr[:, :border_px] = border_bgr
        frame_bgr[:, -border_px:] = border_bgr

        lines = [("GRAIN", "OK" if is_grain_ok else "NOK")]
        colors = [_STATUS_OK_BGR if is_grain_ok else _STATUS_NOK_BGR]
        if expects_person:
            lines.append(("PERSON", "--" if not is_person_fresh
                          else "OK" if is_person_ok else "NOK"))
            colors.append(person_bgr)
        unit_height_px = cv2.getTextSize("NOK", cv2.FONT_HERSHEY_SIMPLEX, 1.0, 1)[0][1]
        font_scale = side_px * _STATUS_TEXT_FRACTION / unit_height_px
        overlay.draw_text_panel(frame_bgr, lines, line_colors_bgr=tuple(colors),
                                options=overlay.OverlayOptions(font_scale=font_scale))

    draw.__name__ = (f"unload_status({flow_class}, persons={sorted(person_pipelines)}, "
                     f"zones={sorted(zone_by_camera)})")
    return draw


def _has_person_in_zone(detections: list, zone: np.ndarray | None) -> bool:
    """True si los pies de alguna detección —el centro del borde inferior— caen en la zona."""
    if zone is None:
        return bool(detections)
    for detection in detections:
        x1, _, x2, y2 = detection.bbox_px
        feet_px = (float(x1 + x2) / 2, float(y2))
        if cv2.pointPolygonTest(zone, feet_px, False) >= 0:
            return True
    return False
