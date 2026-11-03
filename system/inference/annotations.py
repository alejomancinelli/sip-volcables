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

from . import geometry, overlay
from .result import Detection, InferenceResult

Annotator = Callable[[np.ndarray, InferenceResult], None]

DEFAULT_MIN_RAISED_PX = 50      # píxeles de la calza sobre su línea para darla por levantada
# De qué lado de la calza tiene que estar la rueda, en la imagen. `any` = cualquiera.
WHEEL_SIDE_ANY = "any"
WHEEL_SIDE_LEFT = "left"
WHEEL_SIDE_RIGHT = "right"
WHEEL_SIDES = (WHEEL_SIDE_ANY, WHEEL_SIDE_LEFT, WHEEL_SIDE_RIGHT)

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
_TIMESTAMP_TEXT_FRACTION = 0.04  # `draw_timestamp` lo dibuja un punto más chico que esto
_GAP_TEXT_FRACTION = 0.03
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
                            language: str = _FALLBACK_LANGUAGE) -> Annotator:
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
        is_person_ok = person[1] if is_person_fresh else None

        if zone is not None:
            cv2.polylines(frame_bgr, [zone], True, _get_state_bgr(is_person_ok),
                          _get_stroke_px(frame_bgr), cv2.LINE_AA)
        if not is_grain_fresh:
            return

        rows = [(labels["grain"], grain[1])]
        if camera_slot in person_cameras:
            rows.append((labels["person"], is_person_ok))
        _draw_status(frame_bgr, rows)

    draw.__name__ = (f"unload_status({flow_class}, persons={sorted(person_pipelines)}, "
                     f"zones={sorted(zone_by_camera)})")
    return draw


def stopper_status_annotator(*, stopper_pipelines: set[str], stopper_class: str,
                             wheel_class: str,
                             raised_y_px_by_camera: dict[str, int] | None = None,
                             min_raised_px_by_camera: dict[str, int] | None = None,
                             max_gap_px_by_camera: dict[str, float] | None = None,
                             wheel_side_by_camera: dict[str, str] | None = None,
                             language: str = _FALLBACK_LANGUAGE) -> Annotator:
    """
    Dibuja el estado de las calzas de cada cámara: un borde en todo el frame, la altura de
    calza levantada como una línea horizontal, la distancia más corta entre la rueda y la calza, y un panel con los
    dos veredictos.

    - **Calza:** OK si al menos `min_raised_px` píxeles de la máscara de la calza más
      confiable quedan por encima de `raised_y_px`, la fila de su cámara; sin calza
      detectada, NOK. La altura es horizontal y cubre todo el ancho.
    - **Contacto:** sólo se juzga con la calza levantada —antes la distancia no dice nada, y
      sale `--` sin dibujar el segmento—. OK si la distancia más corta entre la calza y la
      rueda más cercana no pasa de `max_gap_px`; sin rueda, NOK. La distancia se dibuja como el
      segmento entre los dos puntos que la dan, con su valor en px. Se mide entre máscaras
      y no entre bbox: con la calza en el piso, bajo la curva de la goma, los bbox se
      solapan aunque haya varios centímetros de aire.
    - **Lado de la rueda:** con `wheel_side` en `left` o `right` sólo cuenta una rueda cuyo
      centro esté de ese lado del centro de la calza. Es lo que elige la rueda correcta con
      dos ejes a la vista, y una rueda sólo del otro lado deja el contacto en NOK: la calza
      está puesta del lado equivocado. Sin valor, `any`.

    Una cámara sin altura o sin distancia máxima tiene ese veredicto sin calibrar: sale `--`
    en gris y el borde, rojo, porque lo que nadie calibró no se afirma. Verde sólo con los
    dos en OK. Un resultado de otro pipeline —se reconoce por `stopper_pipelines`— o no
    confiable no dibuja nada. Una cámara sin `min_raised_px` usa `DEFAULT_MIN_RAISED_PX`.
    """
    raised_y_px_by_camera = dict(raised_y_px_by_camera or {})
    for camera_slot, y_px in raised_y_px_by_camera.items():
        if isinstance(y_px, bool) or not isinstance(y_px, int) or y_px < 0:
            raise ValueError(f"La altura de calza de {camera_slot} tiene que ser una fila del "
                             f"frame, un entero desde 0: {y_px}")
    min_raised_px_by_camera = min_raised_px_by_camera or {}
    max_gap_px_by_camera = max_gap_px_by_camera or {}
    wheel_side_by_camera = wheel_side_by_camera or {}
    for camera_slot, side in wheel_side_by_camera.items():
        if side not in WHEEL_SIDES:
            raise ValueError(f"El lado de la rueda de {camera_slot} tiene que ser uno de "
                             f"{', '.join(WHEEL_SIDES)}: {side}")
    labels = _get_status_labels(language)

    def draw(frame_bgr: np.ndarray, result: InferenceResult):
        if (result.pipeline_slot not in stopper_pipelines or not result.is_valid
                or frame_bgr is None or frame_bgr.size == 0):
            return
        camera_slot = result.camera_slot
        stopper = max((d for d in result.detections if d.class_name == stopper_class),
                      key=lambda detection: detection.confidence_pct, default=None)
        wheels = [d for d in result.detections if d.class_name == wheel_class]
        side = wheel_side_by_camera.get(camera_slot, WHEEL_SIDE_ANY)
        if stopper is not None and side != WHEEL_SIDE_ANY:
            wheels = [wheel for wheel in wheels if _is_on_side(wheel, stopper, side)]

        raised_y_px = raised_y_px_by_camera.get(camera_slot)
        is_raised = None
        if raised_y_px is not None:
            min_raised_px = int(min_raised_px_by_camera.get(camera_slot, DEFAULT_MIN_RAISED_PX))
            is_raised = (stopper is not None
                         and geometry.count_px_above_y(stopper, raised_y_px) >= min_raised_px)

        # El contacto sólo se juzga con la calza levantada: antes, la distancia a la rueda no
        # dice nada, y ni se mide.
        closest = None
        is_touching = None
        max_gap_px = max_gap_px_by_camera.get(camera_slot)
        if is_raised:
            gaps = [geometry.find_closest_points(stopper, wheel) for wheel in wheels]
            closest = min((gap for gap in gaps if gap is not None),
                          key=lambda gap: gap[0], default=None)
            if max_gap_px is not None:
                is_touching = closest is not None and closest[0] <= float(max_gap_px)

        if raised_y_px is not None:
            _draw_level(frame_bgr, raised_y_px, _get_state_bgr(is_raised))
        if closest is not None:
            _draw_gap(frame_bgr, closest, _get_state_bgr(is_touching))
        _draw_status(frame_bgr, [(labels["stopper"], is_raised),
                                 (labels["contact"], is_touching)])

    draw.__name__ = (f"stopper_status(pipelines={sorted(stopper_pipelines)}, "
                     f"levels={raised_y_px_by_camera}, gaps={sorted(max_gap_px_by_camera)}, "
                     f"sides={wheel_side_by_camera})")
    return draw


def _is_on_side(wheel: Detection, stopper: Detection, side: str) -> bool:
    """True si el centro del bbox de la rueda queda de ese lado del de la calza."""
    wheel_x_px = (wheel.bbox_px[0] + wheel.bbox_px[2]) / 2
    stopper_x_px = (stopper.bbox_px[0] + stopper.bbox_px[2]) / 2
    return wheel_x_px < stopper_x_px if side == WHEEL_SIDE_LEFT else wheel_x_px > stopper_x_px


def timestamp_annotator() -> Annotator:
    """
    Dibuja la fecha y hora del resultado abajo a la derecha, del tamaño del frame.

    Es la hora del motor con otra escala: la del motor es fija, y en un frame de 2560 px
    queda escondida dentro del borde de estado. Va con `inference.overlay.draw_timestamp`
    en false —si no, salen las dos— y última en la cadena, así queda encima del borde.
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
    """Grosor de una referencia dibujada —zona, línea, distancia— según el tamaño del frame."""
    return max(2, int(round(min(frame_bgr.shape[:2]) * _ZONE_THICKNESS_FRACTION)))


def _get_text_scale(frame_bgr: np.ndarray, height_fraction: float) -> float:
    """Escala de OpenCV para que el texto mida esa fracción del lado menor del frame."""
    unit_height_px = cv2.getTextSize("NOK", cv2.FONT_HERSHEY_SIMPLEX, 1.0, 1)[0][1]
    return min(frame_bgr.shape[:2]) * height_fraction / unit_height_px


def _draw_status(frame_bgr: np.ndarray, rows: list[tuple[str, bool | None]]):
    """
    Dibuja in-place el borde de estado y, encima, el panel con un renglón por veredicto.

    El borde es verde sólo si todos los veredictos son OK; uno en None —sin dar— sale `--`
    en gris y deja el borde en rojo.
    """
    is_ok = all(state is True for _, state in rows)
    border_bgr = _STATUS_OK_BGR if is_ok else _STATUS_NOK_BGR
    side_px = min(frame_bgr.shape[:2])
    border_px = max(_STATUS_BORDER_MIN_PX, int(round(side_px * _STATUS_BORDER_FRACTION)))
    frame_bgr[:border_px] = border_bgr
    frame_bgr[-border_px:] = border_bgr
    frame_bgr[:, :border_px] = border_bgr
    frame_bgr[:, -border_px:] = border_bgr

    lines = [(label, "--" if state is None else "OK" if state else "NOK")
             for label, state in rows]
    colors = tuple(_get_state_bgr(state) for _, state in rows)
    font_scale = _get_text_scale(frame_bgr, _STATUS_TEXT_FRACTION)
    overlay.draw_text_panel(frame_bgr, lines, line_colors_bgr=colors,
                            options=overlay.OverlayOptions(font_scale=font_scale))


def _draw_level(frame_bgr: np.ndarray, y_px: int, color_bgr: tuple[int, int, int]):
    """Una altura como línea horizontal de borde a borde: se mide en todo el ancho."""
    cv2.line(frame_bgr, (0, int(y_px)), (frame_bgr.shape[1] - 1, int(y_px)), color_bgr,
             _get_stroke_px(frame_bgr), cv2.LINE_AA)


def _draw_gap(frame_bgr: np.ndarray, closest: tuple[float, geometry.Point, geometry.Point],
              color_bgr: tuple[int, int, int]):
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
