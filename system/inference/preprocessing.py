"""
El `preprocessor` del motor: sobre qué imagen se mide.

Maquinaria genérica: reutilizable entre proyectos. Arma, desde la sección de cada cámara,
la corrección de lente (`tools/image/undistort.py`) y el ajuste de imagen
(`tools/image/enhance.py`), y los encadena en una sola función con la firma que pide el
motor: `(frame, camera_slot) -> frame`.

Lo que devuelve esa función es el **frame de referencia** del ciclo: el que ve el modelo,
el que queda en `source_bgr` —y con él en el dataset—, el espacio del ROI y el lienzo del
overlay. Lo usan la app y la corrida de imágenes sueltas, y por eso vive acá y no en
`main.py`: dos lugares que arman el preprocessor son dos lugares que pueden discrepar.

Acá se decide también qué **vistas** corta cada cámara (`build_camera_views()`): el ROI de
cada pipeline que corre, cortado del video nativo en vez del frame reducido. Es la otra mitad
de la misma pregunta —qué imagen ve el modelo—, y lo decide la cámara y no un tilde del ROI:
un driver que decodifica video (`rtsp`, `video_file`) corta siempre la vista, y uno de cámara
GigE entrega el frame del sensor, así que su recorte ya es nativo. La excepción es la cámara
con corrección de lente o ajuste de imagen: la vista sale del decodificador y no pasa por el
preprocessor, así que mediría otra imagen que el frame de referencia. En esa cámara **todos**
los ROI se cortan del frame de referencia —se avisa al arrancar—: la decisión es de la
cámara y del config, no de cada frame, para que un pipeline no mida a veces una imagen y a
veces otra.

El tamaño al que sale cada vista es el que el modelo que la lee usaría igual
(`AbstractPipeline.peek_view_input()`): así el VIC entrega la región ya a su entrada y el
modelo no la reescala. No hay clave que lo pise: un tamaño elegido a mano es uno que puede
quedar distinto del modelo que se instale después. Un modelo que no informa su entrada recibe
la vista a tamaño nativo.
"""

from collections.abc import Callable, Mapping

import numpy as np

from system.config_manager import ConfigManager
from system.logger import logger
from tools.camera.camera_factory import can_cut_views
from tools.image.enhance import build_image_adjust
from tools.image.undistort import build_undistorter

from .abstract_pipeline import AbstractPipeline
from .regions import select_pipeline_roi

Preprocessor = Callable[[np.ndarray, str], np.ndarray]

# Lo que una vista copia del ROI del pipeline: la geometría.
_VIEW_KEYS = ("x_px", "y_px", "width_px", "height_px")


def build_preprocessor(config: ConfigManager) -> Preprocessor | None:
    """
    Corrector de lente y ajuste de imagen, si alguna cámara declara alguno de los dos.

    Sin nada declarado devuelve None y el motor no gasta la pasada.

    **Primero la geometría y después el brillo.** El factor de brillo es por píxel y el
    orden no le cambia nada, pero el contraste local ecualiza por celdas: corrido antes de
    corregir el lente, las celdas quedarían deformadas sobre el frame que mide el modelo.
    """
    camera_slots = tuple(config.get("cameras", {}) or {})
    undistorter = build_undistorter({
        camera_slot: config.get(f"cameras.{camera_slot}.calibration", {}) or {}
        for camera_slot in camera_slots
    })
    if undistorter is not None:
        logger.info("[Inference] Corrección de lente activa: el frame de referencia va corregido.")
    image_adjust = build_image_adjust({
        camera_slot: config.get(f"cameras.{camera_slot}.image_adjust", {}) or {}
        for camera_slot in camera_slots
    })
    if image_adjust is not None:
        logger.info(f"[Inference] Ajuste de imagen activo — {image_adjust.__name__}: el modelo "
                    f"y el dataset reciben el frame ajustado.")
    return _chain_preprocessors(undistorter, image_adjust)


def can_cut_native(config: ConfigManager, camera_slot: str) -> bool:
    """
    True si el ROI de un pipeline en esa cámara se corta del video nativo.

    Hace falta un driver que decodifique video y una cámara sin corrección de lente ni ajuste
    de imagen. Lo demás —GigE, o una cámara con preprocessor— recorta el frame que ya hay.
    """
    return (can_cut_views(config.get(f"cameras.{camera_slot}", {}) or {})
            and not is_preprocessed(config, camera_slot))


def loses_native_cut(config: ConfigManager, camera_slot: str) -> bool:
    """True si la cámara decodifica video pero su preprocessor impide cortar del nativo."""
    return (can_cut_views(config.get(f"cameras.{camera_slot}", {}) or {})
            and is_preprocessed(config, camera_slot))


def list_view_cameras(config: ConfigManager, pipeline_slot: str) -> list[str]:
    """
    Las cámaras en las que el ROI de ese pipeline se corta del video nativo.

    Es una función del config, y la usan los dos lados que tienen que coincidir: quien pide
    las vistas al decodificador y el motor que las espera. Si cada uno lo decidiera por su
    cuenta, un pipeline podría esperar una vista que nadie cortó.
    """
    section = config.get(f"inference.pipelines.{pipeline_slot}", {}) or {}
    if not isinstance(section, dict):
        return []
    cameras = section.get("cameras") or []
    return [camera_slot for camera_slot in (section.get("roi") or {})
            if (not cameras or camera_slot in cameras)
            and (select_pipeline_roi(section, camera_slot) or {}).get("enabled", False)
            and can_cut_native(config, camera_slot)]


def build_camera_views(config: ConfigManager,
                       pipelines: Mapping[str, AbstractPipeline]) -> dict[str, dict[str, dict]]:
    """
    {cámara: {pipeline: vista}} para `decode.views`: el ROI de cada pipeline, en cada cámara
    donde se corta del nativo (`list_view_cameras()`). La vista lleva el nombre del
    pipeline, que es como el motor la busca.

    Sólo los pipelines de `pipelines` —los que corren—: cortar la vista de uno que no
    arrancó sería VIC y copias que nadie lee. El tamaño de salida sale de la entrada del
    modelo que lee la región.
    """
    views: dict[str, dict[str, dict]] = {}
    for pipeline_slot, pipeline in pipelines.items():
        section = config.get(f"inference.pipelines.{pipeline_slot}", {}) or {}
        _warn_if_not_native(config, pipeline_slot, section)
        camera_slots = list_view_cameras(config, pipeline_slot)
        if not camera_slots:
            continue
        spec = pipeline.peek_view_input()
        for camera_slot in camera_slots:
            roi = select_pipeline_roi(section, camera_slot)
            view: dict = {key: roi.get(key, 0) for key in _VIEW_KEYS}
            if spec is not None:
                view["output_box_px"] = [spec.width_px, spec.height_px]
                view["output_covers"] = spec.crops_center
            views.setdefault(camera_slot, {})[pipeline_slot] = view
            logger.info(
                f"[Inference] {pipeline_slot} en {camera_slot}: la región se corta del video "
                f"nativo, " + (f"a la entrada del modelo ({spec.width_px}x{spec.height_px})."
                               if spec is not None else
                               "a tamaño nativo: el modelo no informa su entrada."))
    return views


def _warn_if_not_native(config: ConfigManager, pipeline_slot: str, section: object):
    """Avisa de la cámara con driver de video donde el preprocessor impide el corte nativo."""
    if not isinstance(section, dict):
        return
    cameras = section.get("cameras") or []
    for camera_slot in (section.get("roi") or {}):
        if cameras and camera_slot not in cameras:
            continue
        roi = select_pipeline_roi(section, camera_slot) or {}
        if roi.get("enabled", False) and loses_native_cut(config, camera_slot):
            logger.warning(
                f"[Inference] {pipeline_slot} en {camera_slot}: la cámara tiene corrección de "
                f"lente o ajuste de imagen, así que la región se corta del frame de "
                f"referencia y no del video nativo: la vista no pasa por ese preprocessor.")


def is_preprocessed(config: ConfigManager, camera_slot: str) -> bool:
    """True si el preprocessor le cambia algo al frame de esa cámara."""
    calibration = config.get(f"cameras.{camera_slot}.calibration", {}) or {}
    image_adjust = config.get(f"cameras.{camera_slot}.image_adjust", {}) or {}
    return (build_undistorter({camera_slot: calibration}) is not None
            or build_image_adjust({camera_slot: image_adjust}) is not None)


def _chain_preprocessors(*preprocessors: Preprocessor | None) -> Preprocessor | None:
    """
    Junta varios preprocessors en uno, en orden. El motor recibe una sola función.

    Los que vienen en None se descartan: sin ninguno devuelve None, y con uno solo lo
    devuelve tal cual, así el motor no paga una llamada de más por frame.
    """
    selected = [preprocessor for preprocessor in preprocessors if preprocessor is not None]
    if not selected:
        return None
    if len(selected) == 1:
        return selected[0]

    def preprocess(frame_bgr: np.ndarray, camera_slot: str) -> np.ndarray:
        for preprocessor in selected:
            frame_bgr = preprocessor(frame_bgr, camera_slot)
        return frame_bgr

    preprocess.__name__ = " + ".join(getattr(p, "__name__", repr(p)) for p in selected)
    return preprocess
