"""
¿La calza está levantada y contra la rueda? La regla con la que se juzga cada cámara de calzas.

Módulo puro: sin Qt, sin ConfigManager y sin I/O.

**Específico del proyecto — no cross-portar tal cual.** Es una sola regla para tres lectores:
el analyzer, que deja las señales que se publican (`build_analyzer()`); el annotator, que la
dibuja; y la pestaña de proceso, que la muestra sobre un frame quieto al calibrar. Dos
lecturas del mismo resultado que eligieran otra calza u otra rueda mostrarían en pantalla
números que el equipo no mide.

- **Calza:** levantada si al menos `min_raised_px` píxeles de la máscara de la calza más
  confiable quedan por encima de `raised_y_px`, la fila de su cámara; sin calza detectada, no.
- **Contacto:** sólo se juzga con la calza levantada —antes la distancia no dice nada—. Toca
  si la distancia más corta entre la calza y la rueda más cercana no pasa de `max_gap_px`;
  sin rueda, no. Se mide entre máscaras y no entre bbox: con la calza en el piso, bajo la
  curva de la goma, los bbox se solapan aunque haya varios centímetros de aire.
- **Lado de la rueda:** con `wheel_side` en `left` o `right` sólo cuenta una rueda cuyo
  centro esté de ese lado del centro de la calza. Es lo que elige la rueda correcta con dos
  ejes a la vista, y una rueda sólo del otro lado deja el contacto en no: la calza está
  puesta del lado equivocado. Sin valor, `any`.

Una cámara sin altura o sin distancia máxima tiene ese veredicto sin calibrar —None—: lo que
nadie calibró no se afirma. Una sin `min_raised_px` usa `DEFAULT_MIN_RAISED_PX`.

Las señales del analyzer son 1 OK y 0 cualquier otra cosa —NOK, sin calibrar, el contacto
con la calza abajo—: el 0 es el estado seguro, y nunca da por bueno lo que no midió.
"""

from collections.abc import Callable
from typing import NamedTuple

from . import geometry
from .result import Detection, InferenceResult

DEFAULT_MIN_RAISED_PX = 50      # píxeles de la calza sobre su línea para darla por levantada
# De qué lado de la calza tiene que estar la rueda, en la imagen. `any` = cualquiera.
WHEEL_SIDE_ANY = "any"
WHEEL_SIDE_LEFT = "left"
WHEEL_SIDE_RIGHT = "right"
WHEEL_SIDES = (WHEEL_SIDE_ANY, WHEEL_SIDE_LEFT, WHEEL_SIDE_RIGHT)

Gap = tuple[float, geometry.Point, geometry.Point]


class StopperVerdict(NamedTuple):
    """Los dos veredictos de un frame —None sin calibrar— y la distancia que dio el contacto."""

    is_raised: bool | None
    is_touching: bool | None
    closest: Gap | None


class StopperRule:
    """
    La regla con la calibración de cada cámara, ya validada.

    Levanta ValueError al armarse con una altura que no es una fila del frame o un lado de la
    rueda fuera de `WHEEL_SIDES`: una calibración mal escrita frena el arranque y no pasa a
    juzgar con otra cosa.
    """

    def __init__(self, *, stopper_class: str, wheel_class: str,
                 raised_y_px_by_camera: dict[str, int] | None = None,
                 min_raised_px_by_camera: dict[str, int] | None = None,
                 max_gap_px_by_camera: dict[str, float] | None = None,
                 wheel_side_by_camera: dict[str, str] | None = None):
        self._stopper_class = stopper_class
        self._wheel_class = wheel_class
        self.raised_y_px_by_camera = dict(raised_y_px_by_camera or {})
        for camera_slot, y_px in self.raised_y_px_by_camera.items():
            if isinstance(y_px, bool) or not isinstance(y_px, int) or y_px < 0:
                raise ValueError(f"La altura de calza de {camera_slot} tiene que ser una fila "
                                 f"del frame, un entero desde 0: {y_px}")
        self._min_raised_px_by_camera = dict(min_raised_px_by_camera or {})
        self.max_gap_px_by_camera = dict(max_gap_px_by_camera or {})
        self.wheel_side_by_camera = dict(wheel_side_by_camera or {})
        for camera_slot, side in self.wheel_side_by_camera.items():
            if side not in WHEEL_SIDES:
                raise ValueError(f"El lado de la rueda de {camera_slot} tiene que ser uno de "
                                 f"{', '.join(WHEEL_SIDES)}: {side}")

    def judge(self, detections: list[Detection], camera_slot: str) -> StopperVerdict:
        """Los veredictos de un frame de esa cámara."""
        stopper, wheels = pick_stopper_and_wheels(
            detections, stopper_class=self._stopper_class, wheel_class=self._wheel_class,
            wheel_side=self.wheel_side_by_camera.get(camera_slot, WHEEL_SIDE_ANY))

        raised_y_px = self.raised_y_px_by_camera.get(camera_slot)
        is_raised = None
        if raised_y_px is not None:
            min_raised_px = int(self._min_raised_px_by_camera.get(camera_slot,
                                                                  DEFAULT_MIN_RAISED_PX))
            is_raised = (stopper is not None
                         and geometry.count_px_above_y(stopper, raised_y_px) >= min_raised_px)

        closest = None
        is_touching = None
        max_gap_px = self.max_gap_px_by_camera.get(camera_slot)
        if is_raised:
            closest = find_closest_wheel(stopper, wheels)
            if max_gap_px is not None:
                is_touching = closest is not None and closest[0] <= float(max_gap_px)
        return StopperVerdict(is_raised, is_touching, closest)


def build_analyzer(rule: StopperRule, *, stopper_pipelines: set[str]
                   ) -> Callable[[InferenceResult], dict]:
    """
    Las señales de calza de un resultado confiable: `raised_ok` y `contact_ok`, 1 OK y 0
    cualquier otra cosa. {} si el resultado no es de `stopper_pipelines`.
    """
    def analyze(result: InferenceResult) -> dict:
        if result.pipeline_slot not in stopper_pipelines:
            return {}
        verdict = rule.judge(result.detections, result.camera_slot)
        return {"raised_ok": int(verdict.is_raised is True),
                "contact_ok": int(verdict.is_touching is True)}

    analyze.__name__ = f"stopper_check(pipelines={sorted(stopper_pipelines)})"
    return analyze


def pick_stopper_and_wheels(detections: list[Detection], *, stopper_class: str,
                            wheel_class: str, wheel_side: str = WHEEL_SIDE_ANY
                            ) -> tuple[Detection | None, list[Detection]]:
    """
    La calza que se juzga —la más confiable— y las ruedas que cuentan contra ella: con
    `wheel_side` en `left` o `right`, sólo las de ese lado de la calza.
    """
    stopper = max((d for d in detections if d.class_name == stopper_class),
                  key=lambda detection: detection.confidence_pct, default=None)
    wheels = [d for d in detections if d.class_name == wheel_class]
    if stopper is not None and wheel_side != WHEEL_SIDE_ANY:
        wheels = [wheel for wheel in wheels if _is_on_side(wheel, stopper, wheel_side)]
    return stopper, wheels


def find_closest_wheel(stopper: Detection, wheels: list[Detection]) -> Gap | None:
    """La distancia más corta entre la calza y la rueda más cercana, con sus dos puntos."""
    gaps = [geometry.find_closest_points(stopper, wheel) for wheel in wheels]
    return min((gap for gap in gaps if gap is not None), key=lambda gap: gap[0], default=None)


def _is_on_side(wheel: Detection, stopper: Detection, side: str) -> bool:
    """True si el centro del bbox de la rueda queda de ese lado del de la calza."""
    wheel_x_px = (wheel.bbox_px[0] + wheel.bbox_px[2]) / 2
    stopper_x_px = (stopper.bbox_px[0] + stopper.bbox_px[2]) / 2
    return wheel_x_px < stopper_x_px if side == WHEEL_SIDE_LEFT else wheel_x_px > stopper_x_px
