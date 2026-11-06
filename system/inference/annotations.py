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

from . import overlay, person_safety
from .pipeline import GRAIN_FLOW_LABEL
from .result import InferenceResult
from .stopper_check import Gap, StopperRule

Annotator = Callable[[np.ndarray, InferenceResult], None]

_MAX_LOAD_BGR = (60, 60, 230)   # rojo: es un límite, no una referencia neutra

# Rótulos del panel de estado por idioma (`ui.language`). Se dibujan con OpenCV, así que van
# en ASCII y en mayúsculas: las fuentes Hershey no tienen tildes ni eñes. No salen de
# ui/strings.py porque este módulo no conoce la UI.
_STATUS_LABELS = {
    "es": {"grain": "GRANO", "person": "PERSONA", "stopper": "CALZA", "contact": "CONTACTO"},
    "en": {"grain": "GRAIN", "person": "PERSON", "stopper": "STOPPER", "contact": "CONTACT"},
    "pt": {"grain": "GRAO", "person": "PESSOA", "stopper": "CALCO", "contact": "CONTATO"},
}
_FALLBACK_LANGUAGE = "es"

_STATUS_OK_BGR = (40, 200, 40)
_STATUS_NOK_BGR = (40, 40, 230)
_STATUS_UNKNOWN_BGR = (170, 170, 170)
# Texto del estado, en fracción del lado menor del frame: la cámara puede llegar a tamaño
# completo o reducida por el decodificador, y tiene que leerse igual en las dos.
_STATUS_TEXT_FRACTION = 0.05    # alto del texto del panel
_TIMESTAMP_TEXT_FRACTION = 0.04  # `draw_timestamp` lo dibuja un punto más chico que esto
_GAP_TEXT_FRACTION = 0.03
# Trama de la zona de exclusión: un tinte del color de su estado y rayas diagonales, para que
# se lea como un área y no como un recuadro más. El paso va en fracción del lado menor.
_ZONE_TINT_ALPHA = 0.18
_ZONE_HATCH_FRACTION = 0.025
_ZONE_HATCH_MIN_PX = 8
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
                          _MAX_LOAD_BGR, thickness=_get_stroke_px(frame_bgr), label=label)

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


def unload_status_annotator(*, flow_class: str, person_cameras: set[str],
                            zone_by_camera: dict[str, np.ndarray] | None = None,
                            language: str = _FALLBACK_LANGUAGE) -> Annotator:
    """
    Dibuja el estado de la descarga de cada cámara: la zona de exclusión y un panel arriba a
    la izquierda con el veredicto del grano y, si la cámara tiene detección de personas, el
    de personas.

    - **Grano:** OK si el label `grain_flow` es `flow_class`. Sin un veredicto vigente —con
      la plataforma inclinada el grano se corta— el renglón no se dibuja.
    - **Personas:** el veredicto filtrado que dejó el analyzer (`person_safety`), el mismo que
      se publica al PLC: OK sin nadie en la zona, NOK con alguien. La zona se pinta del mismo
      color, con un tinte y rayas diagonales. Sin un veredicto vigente —un pipeline de personas caído— sale `--` en gris: lo
      que no se midió no se afirma.

    Sin borde de estado: si se puede arrancar lo decide el PLC, y un borde verde en la
    pantalla afirmaría una decisión que no es del equipo. Una cámara sin ningún renglón no
    dibuja panel.

    **Guarda estado.** Grano y personas son dos pipelines y cada resultado trae sólo su
    veredicto, así que el annotator —uno solo, compartido por todos los motores— recuerda el
    último de cada cámara y dibuja los dos con cualquiera de los dos resultados. Un resultado
    no confiable no actualiza nada. Lo llaman los hilos de inferencia y el de la GUI: cada
    escritura reemplaza una entrada entera del dict, que es atómico bajo el GIL.
    """
    zone_by_camera = dict(zone_by_camera or {})
    labels = _get_status_labels(language)
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
        verdict = result.labels.get(GRAIN_FLOW_LABEL)
        safety_ok = result.metrics.get(person_safety.SAFETY_METRIC)
        if result.is_valid and verdict is not None:
            remember(camera_slot, "grain", result.timestamp_s, verdict == flow_class)
        elif result.is_valid and safety_ok is not None:
            remember(camera_slot, "person", result.timestamp_s, bool(safety_ok))

        grain = latest.get((camera_slot, "grain"))
        person = latest.get((camera_slot, "person"))
        newest_s = max(entry[0] for entry in (grain, person, (result.timestamp_s, True))
                       if entry is not None)
        is_grain_fresh = grain is not None and newest_s - grain[0] <= _VERDICT_MAX_AGE_S
        is_person_fresh = person is not None and newest_s - person[0] <= _VERDICT_MAX_AGE_S
        is_person_ok = person[1] if is_person_fresh else None

        zone = zone_by_camera.get(camera_slot)
        if zone is not None:
            _draw_zone(frame_bgr, zone, _get_state_bgr(is_person_ok))

        rows = []
        if is_grain_fresh:
            rows.append((labels["grain"], grain[1]))
        if camera_slot in person_cameras:
            rows.append((labels["person"], is_person_ok))
        if rows:
            _draw_status(frame_bgr, rows)

    draw.__name__ = (f"unload_status({flow_class}, persons={sorted(person_cameras)}, "
                     f"zones={sorted(zone_by_camera)})")
    return draw


def stopper_status_annotator(rule: StopperRule, *, stopper_pipelines: set[str],
                             language: str = _FALLBACK_LANGUAGE) -> Annotator:
    """
    Dibuja el estado de las calzas de cada cámara: la altura de calza levantada como una
    línea horizontal, la distancia más corta entre la rueda y la calza, y un panel con los
    dos veredictos. Sin borde de estado: si la calza está bien puesta lo decide el PLC, y
    un borde verde en la pantalla afirmaría una decisión que no es del equipo.

    Qué es OK lo decide `rule` (`stopper_check.py`), la misma regla que da las señales del
    analyzer. La distancia se dibuja sólo con la calza levantada, como el segmento entre los
    dos puntos que la dan y con su valor en px. Un veredicto sin calibrar sale `--` en gris.
    Un resultado de otro pipeline —se reconoce por `stopper_pipelines`— o no confiable no
    dibuja nada.
    """
    labels = _get_status_labels(language)

    def draw(frame_bgr: np.ndarray, result: InferenceResult):
        if (result.pipeline_slot not in stopper_pipelines or not result.is_valid
                or frame_bgr is None or frame_bgr.size == 0):
            return
        verdict = rule.judge(result.detections, result.camera_slot)
        raised_y_px = rule.raised_y_px_by_camera.get(result.camera_slot)
        if raised_y_px is not None:
            _draw_level(frame_bgr, raised_y_px, _get_state_bgr(verdict.is_raised))
        if verdict.closest is not None:
            _draw_gap(frame_bgr, verdict.closest, _get_state_bgr(verdict.is_touching))
        _draw_status(frame_bgr, [(labels["stopper"], verdict.is_raised),
                                 (labels["contact"], verdict.is_touching)])

    draw.__name__ = (f"stopper_status(pipelines={sorted(stopper_pipelines)}, "
                     f"levels={rule.raised_y_px_by_camera}, "
                     f"gaps={sorted(rule.max_gap_px_by_camera)}, "
                     f"sides={rule.wheel_side_by_camera})")
    return draw


def timestamp_annotator() -> Annotator:
    """
    Dibuja la fecha y hora del resultado abajo a la derecha, del tamaño del frame.

    Es la hora del motor con otra escala: la del motor es fija, y en un frame de 2560 px
    casi no se lee. Va con `inference.overlay.draw_timestamp` en false —si no, salen las
    dos— y última en la cadena, así queda encima de todo.
    """
    def draw(frame_bgr: np.ndarray, result: InferenceResult):
        if frame_bgr is None or frame_bgr.size == 0:
            return
        unit_height_px = cv2.getTextSize("0", cv2.FONT_HERSHEY_SIMPLEX, 1.0, 1)[0][1]
        font_scale = min(frame_bgr.shape[:2]) * _TIMESTAMP_TEXT_FRACTION / unit_height_px
        overlay.draw_timestamp(frame_bgr, result.timestamp_s,
                               options=overlay.OverlayOptions(font_scale=font_scale))

    draw.__name__ = "timestamp"
    return draw


def _get_status_labels(language: str) -> dict[str, str]:
    return _STATUS_LABELS.get(language, _STATUS_LABELS[_FALLBACK_LANGUAGE])


def _get_state_bgr(state: bool | None) -> tuple[int, int, int]:
    """Verde, rojo, o gris para un veredicto que no se pudo dar."""
    if state is None:
        return _STATUS_UNKNOWN_BGR
    return _STATUS_OK_BGR if state else _STATUS_NOK_BGR


def _get_stroke_px(frame_bgr: np.ndarray) -> int:
    """
    Grosor de una referencia dibujada —zona, línea, distancia—: el automático de las cajas,
    así las dos se ven con el mismo trazo en cualquier tamaño de frame.
    """
    return overlay.get_line_thickness(frame_bgr)


def _get_text_scale(frame_bgr: np.ndarray, height_fraction: float) -> float:
    """Escala de OpenCV para que el texto mida esa fracción del lado menor del frame."""
    unit_height_px = cv2.getTextSize("NOK", cv2.FONT_HERSHEY_SIMPLEX, 1.0, 1)[0][1]
    return min(frame_bgr.shape[:2]) * height_fraction / unit_height_px


def _draw_status(frame_bgr: np.ndarray, rows: list[tuple[str, bool | None]]):
    """
    Dibuja in-place el panel de estado pegado a la esquina superior izquierda, con un
    renglón por veredicto. Uno en None —sin dar— sale `--` en gris.
    """
    lines = [(label, "--" if state is None else "OK" if state else "NOK")
             for label, state in rows]
    colors = tuple(_get_state_bgr(state) for _, state in rows)
    font_scale = _get_text_scale(frame_bgr, _STATUS_TEXT_FRACTION)
    overlay.draw_text_panel(frame_bgr, lines, line_colors_bgr=colors,
                            options=overlay.OverlayOptions(font_scale=font_scale),
                            margin_px=0)


def _draw_level(frame_bgr: np.ndarray, y_px: int, color_bgr: tuple[int, int, int]):
    """Una altura como línea horizontal de borde a borde: se mide en todo el ancho."""
    cv2.line(frame_bgr, (0, int(y_px)), (frame_bgr.shape[1] - 1, int(y_px)), color_bgr,
             _get_stroke_px(frame_bgr), cv2.LINE_AA)


def _draw_gap(frame_bgr: np.ndarray, closest: Gap, color_bgr: tuple[int, int, int]):
    """El segmento más corto entre las dos máscaras, con su largo en px al lado."""
    distance_px, start_px, end_px = closest
    stroke_px = _get_stroke_px(frame_bgr)
    cv2.line(frame_bgr, start_px, end_px, color_bgr, stroke_px, cv2.LINE_AA)
    for point_px in (start_px, end_px):
        cv2.circle(frame_bgr, point_px, stroke_px * 2, color_bgr, cv2.FILLED, cv2.LINE_AA)

    text = f"{distance_px:.0f} px"
    font_scale = _get_text_scale(frame_bgr, _GAP_TEXT_FRACTION)
    thickness = overlay.text_thickness(font_scale)
    anchor_px = (max(start_px[0], end_px[0]) + stroke_px * 4,
                 (start_px[1] + end_px[1]) // 2)
    # Contorno negro abajo y el color encima: se lee sobre la calza clara y sobre el piso.
    cv2.putText(frame_bgr, text, anchor_px, cv2.FONT_HERSHEY_SIMPLEX, font_scale,
                (0, 0, 0), thickness + 2 * stroke_px, cv2.LINE_AA)
    cv2.putText(frame_bgr, text, anchor_px, cv2.FONT_HERSHEY_SIMPLEX, font_scale,
                color_bgr, thickness, cv2.LINE_AA)


def _draw_zone(frame_bgr: np.ndarray, zone: np.ndarray, color_bgr: tuple[int, int, int]):
    """
    La zona como un área tramada: un tinte del color, rayas diagonales y el borde.

    Se trabaja sólo sobre el rectángulo que la contiene, recortado al frame: una zona que se
    dibujó sobre un frame más grande no rompe nada, se ve hasta donde llega.
    """
    height_px, width_px = frame_bgr.shape[:2]
    x, y, w, h = cv2.boundingRect(zone)
    x1, y1 = max(0, x), max(0, y)
    x2, y2 = min(width_px, x + w), min(height_px, y + h)
    if x2 > x1 and y2 > y1:
        area = frame_bgr[y1:y2, x1:x2]
        inside = np.zeros(area.shape[:2], np.uint8)
        cv2.fillPoly(inside, [zone - np.array([x1, y1], np.int32)], 255)
        hatch = np.zeros_like(inside)
        step_px = max(_ZONE_HATCH_MIN_PX,
                      int(round(min(height_px, width_px) * _ZONE_HATCH_FRACTION)))
        rows_px, columns_px = hatch.shape
        for start_px in range(-rows_px, columns_px, step_px):
            cv2.line(hatch, (start_px, rows_px), (start_px + rows_px, 0), 255,
                     max(1, _get_stroke_px(frame_bgr) // 2))
        color = np.array(color_bgr, np.float32)
        is_inside = inside > 0
        area[is_inside] = (area[is_inside] * (1 - _ZONE_TINT_ALPHA)
                           + color * _ZONE_TINT_ALPHA).astype(np.uint8)
        is_line = is_inside & (hatch > 0)
        area[is_line] = color.astype(np.uint8)
    cv2.polylines(frame_bgr, [zone], True, color_bgr, _get_stroke_px(frame_bgr), cv2.LINE_AA)
