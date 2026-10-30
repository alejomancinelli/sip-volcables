"""
Qué región ve cada pipeline en cada cámara: resolverla, recortarla y juzgarla.

Módulo puro: sin Qt, sin ConfigManager, sin hilos y sin I/O. Recibe las secciones del
config ya leídas y devuelve datos. Maquinaria genérica: reutilizable entre proyectos.

Es el único lugar donde se decide **qué rectángulo rige** para un par (pipeline, cámara),
y por eso lo leen el motor —que mide sobre él—, el overlay y el panel —que lo dibujan— y
el diálogo de regiones —que lo edita—: un recuadro dibujado y uno medido que salieran de
dos reglas distintas serían dos cosas distintas con el mismo color.

El rectángulo que rige es el propio del pipeline (`inference.pipelines.<slot>.roi.<cámara>`)
si lo declara y está prendido; un pipeline sin sección, con la sección en null o con
`enabled: false` ve el frame completo. La cámara no tiene ROI propio: lo que se recorta en
el origen es de la interfaz de la cámara (ver `docs/jetson.md`), no de la app.

Un ROI propio puede traer `frame_width_px` y `frame_height_px`: el tamaño del frame sobre
el que se dibujó. No cambian el recorte; sólo permiten avisar que el frame que llega ya no
es ese —un recorte de cámara que se perdió, un `decode` distinto— y que el rectángulo
quedó corrido sin ningún error.
"""

from typing import NamedTuple

# Los problemas que `find_region_problems()` puede devolver. Son claves estables: quien los
# muestra los traduce, y el motor sólo decide cuáles le importan.
PROBLEM_OUTSIDE_FRAME = "outside_frame"
PROBLEM_NATIVE_PREPROCESSED = "native_preprocessed"
PROBLEM_TOO_SMALL = "too_small"
PROBLEM_FRAME_SIZE_CHANGED = "frame_size_changed"

# Lado mínimo, en píxeles del frame, de una región que se corta del video nativo: por debajo
# de 15 px el VIC tira el pipeline entero, y el decodificador la rechaza.
MIN_NATIVE_SIDE_PX = 16

# Claves del ROI propio donde queda el tamaño del frame sobre el que se dibujó.
FRAME_WIDTH_KEY = "frame_width_px"
FRAME_HEIGHT_KEY = "frame_height_px"


class PipelineRegion(NamedTuple):
    """
    La región de un pipeline en una cámara.

    `index` es la posición del pipeline en `inference.pipelines` y no cambia aunque otro
    esté apagado: de ella sale su color. `roi` es su sección del ROI, vacía si no declara
    ninguna. `is_enabled` es el `enabled` del pipeline. `name` es cómo lo llama el operador
    (`inference.pipelines.<slot>.name`), o el slot si no le puso nombre: es lo que se muestra,
    y el slot sigue siendo la identidad.
    """

    pipeline_slot: str
    index: int
    is_enabled: bool
    roi: dict
    name: str = ""


def select_pipeline_roi(pipeline_section: object, camera_slot: str) -> dict | None:
    """
    El ROI propio de un pipeline en esa cámara, desde la sección entera del pipeline, o
    None si no declara uno —y el pipeline ve el frame completo—.

    Se lee de la sección y no de la clave `…roi.<cámara>`, que falta en el caso normal: el
    config avisa cada clave ausente, y un pipeline sin ROI propio no es una advertencia.
    """
    if not isinstance(pipeline_section, dict):
        return None
    rois = pipeline_section.get("roi")
    roi = rois.get(camera_slot) if isinstance(rois, dict) else None
    return roi if isinstance(roi, dict) else None


def resolve_pipeline_roi(pipeline_section: object, camera_slot: str) -> dict:
    """El ROI de ese pipeline en esa cámara, o {} —el frame completo— si no declara uno."""
    return select_pipeline_roi(pipeline_section, camera_slot) or {}


def list_camera_regions(pipelines: object, camera_slot: str, *,
                        include_disabled: bool = False) -> list[PipelineRegion]:
    """
    Las regiones de todos los pipelines que miran esa cámara, en el orden del config.

    Un pipeline sin `cameras` las mira a todas, igual que en el motor. Con
    `include_disabled` entran también los apagados: el diálogo que edita las regiones tiene
    que poder editar el de un pipeline que hoy no corre.
    """
    if not isinstance(pipelines, dict):
        return []
    regions = []
    for index, (pipeline_slot, section) in enumerate(pipelines.items()):
        if not isinstance(section, dict):
            continue
        is_enabled = bool(section.get("enabled", True))
        if not is_enabled and not include_disabled:
            continue
        cameras = section.get("cameras") or []
        if cameras and camera_slot not in cameras:
            continue
        regions.append(PipelineRegion(str(pipeline_slot), index, is_enabled,
                                      resolve_pipeline_roi(section, camera_slot),
                                      get_pipeline_name(section, str(pipeline_slot))))
    return regions


def get_pipeline_name(pipeline_section: object, pipeline_slot: str) -> str:
    """
    Cómo llama el operador al pipeline (`inference.pipelines.<slot>.name`), o el slot si no
    le puso nombre. Es lo que se muestra; la identidad sigue siendo el slot.
    """
    name = pipeline_section.get("name") if isinstance(pipeline_section, dict) else None
    return str(name or "").strip() or pipeline_slot


def clip_roi_px(roi: dict, frame_width_px: int,
                frame_height_px: int) -> tuple[int, int, int, int] | None:
    """
    El ROI acotado al frame como (x, y, ancho, alto), o None si está apagado o no queda nada.

    Un ancho o alto en 0 llega hasta el borde: es lo que permite recortar sólo de un lado
    sin tener que escribir el tamaño del sensor en el config.
    """
    if not roi.get("enabled", False):
        return None
    x_px = max(0, min(frame_width_px - 1, int(roi.get("x_px", 0) or 0)))
    y_px = max(0, min(frame_height_px - 1, int(roi.get("y_px", 0) or 0)))
    width_px = int(roi.get("width_px", 0) or 0) or (frame_width_px - x_px)
    height_px = int(roi.get("height_px", 0) or 0) or (frame_height_px - y_px)
    width_px = min(width_px, frame_width_px - x_px)
    height_px = min(height_px, frame_height_px - y_px)
    if width_px <= 0 or height_px <= 0:
        return None
    return (x_px, y_px, width_px, height_px)


def find_region_problems(roi: dict, *, frame_size_px: tuple[int, int] | None,
                         is_native_lost: bool = False,
                         is_native_cut: bool = False) -> list[str]:
    """
    Qué le pasa a este ROI, como lista de `PROBLEM_*` (vacía si está bien o está apagado).

    `frame_size_px` es (ancho, alto) del frame de referencia, o None si no se conoce: sin
    él sólo se juzga lo que no depende del frame. `is_native_lost` dice que la cámara
    decodifica video pero tiene corrección de lente o ajuste de imagen, así que la región
    no se corta del nativo; `is_native_cut` dice que sí se corta, y entonces el decodificador
    le exige un mínimo de lado.
    """
    if not roi.get("enabled", False):
        return []
    problems = []
    if is_native_lost:
        problems.append(PROBLEM_NATIVE_PREPROCESSED)
    sides = [int(roi.get(key, 0) or 0) for key in ("width_px", "height_px")]
    if is_native_cut and any(0 < side < MIN_NATIVE_SIDE_PX for side in sides):
        problems.append(PROBLEM_TOO_SMALL)
    if frame_size_px is None:
        return problems

    frame_width_px, frame_height_px = frame_size_px
    x_px = int(roi.get("x_px", 0) or 0)
    y_px = int(roi.get("y_px", 0) or 0)
    width_px = int(roi.get("width_px", 0) or 0)
    height_px = int(roi.get("height_px", 0) or 0)
    if (x_px >= frame_width_px or y_px >= frame_height_px
            or x_px + width_px > frame_width_px or y_px + height_px > frame_height_px):
        problems.append(PROBLEM_OUTSIDE_FRAME)

    drawn_width_px = int(roi.get(FRAME_WIDTH_KEY, 0) or 0)
    drawn_height_px = int(roi.get(FRAME_HEIGHT_KEY, 0) or 0)
    if (drawn_width_px > 0 and drawn_height_px > 0
            and (drawn_width_px, drawn_height_px) != (frame_width_px, frame_height_px)):
        problems.append(PROBLEM_FRAME_SIZE_CHANGED)
    return problems


def get_overlap_pct(rect_px: tuple[int, int, int, int],
                    other_px: tuple[int, int, int, int]) -> float:
    """Qué porcentaje del área de `rect_px` cubre `other_px`, 0–100."""
    x1, y1, width_px, height_px = rect_px
    other_x1, other_y1, other_width_px, other_height_px = other_px
    if width_px <= 0 or height_px <= 0:
        return 0.0
    overlap_width_px = min(x1 + width_px, other_x1 + other_width_px) - max(x1, other_x1)
    overlap_height_px = min(y1 + height_px, other_y1 + other_height_px) - max(y1, other_y1)
    if overlap_width_px <= 0 or overlap_height_px <= 0:
        return 0.0
    return 100.0 * overlap_width_px * overlap_height_px / (width_px * height_px)


def get_pipeline_index(pipelines: object, pipeline_slot: str) -> int:
    """
    Posición de un pipeline en `inference.pipelines`, o 0 si no está.

    Es el orden que fija el color de cada uno, así que no depende de cuáles estén
    prendidos: apagar uno no le cambia el color a los demás.
    """
    slots = [str(slot) for slot in pipelines] if isinstance(pipelines, dict) else []
    return slots.index(pipeline_slot) if pipeline_slot in slots else 0
