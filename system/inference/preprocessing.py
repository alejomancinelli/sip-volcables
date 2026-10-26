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

Acá se decide también qué **vistas** corta cada cámara (`build_camera_views()`): los ROI con
`full_resolution` de los pipelines que corren. Es la otra mitad de la misma pregunta —qué
imagen ve el modelo—, y tiene la misma restricción del otro lado: la vista sale del
decodificador y no pasa por el preprocessor, así que en una cámara con corrección de lente
o ajuste de imagen mediría otra imagen que el frame de referencia, y no se corta.
"""

from collections.abc import Callable, Iterable

import numpy as np

from system.config_manager import ConfigManager
from system.logger import logger
from tools.image.enhance import build_image_adjust
from tools.image.undistort import build_undistorter

Preprocessor = Callable[[np.ndarray, str], np.ndarray]

# Lo que una vista copia del ROI del pipeline: la geometría y su tamaño de salida.
_VIEW_KEYS = ("x_px", "y_px", "width_px", "height_px", "output_short_side_px")


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


def build_camera_views(config: ConfigManager,
                       pipeline_slots: Iterable[str]) -> dict[str, dict[str, dict]]:
    """
    {cámara: {pipeline: vista}} para `decode.views`: el ROI de cada pipeline que lo pide
    con `full_resolution`, en cada cámara que ese pipeline mira. La vista lleva el nombre
    del pipeline, que es como el motor la busca.

    Sólo los pipelines de `pipeline_slots` —los que corren—: cortar la vista de uno que no
    arrancó sería VIC y copias que nadie lee. Una cámara con corrección de lente o ajuste
    de imagen no corta ninguna, con un error en el log: su pipeline sale `no_view`.
    """
    views: dict[str, dict[str, dict]] = {}
    for pipeline_slot in pipeline_slots:
        section = f"inference.pipelines.{pipeline_slot}"
        rois = config.get(f"{section}.roi", {}) or {}
        cameras = config.get(f"{section}.cameras", []) or []
        if not isinstance(rois, dict):
            continue
        for camera_slot, roi in rois.items():
            if (not isinstance(roi, dict) or not roi.get("enabled", False)
                    or not roi.get("full_resolution", False)):
                continue
            if cameras and camera_slot not in cameras:
                continue
            if _is_preprocessed(config, camera_slot):
                logger.error(
                    f"[Inference] {pipeline_slot} pide `full_resolution` en {camera_slot}, que "
                    f"tiene corrección de lente o ajuste de imagen: la vista no pasa por ese "
                    f"preprocessor, así que no se corta y el pipeline no infiere esa cámara."
                )
                continue
            views.setdefault(camera_slot, {})[pipeline_slot] = {
                key: roi.get(key, 0) for key in _VIEW_KEYS}
    return views


def _is_preprocessed(config: ConfigManager, camera_slot: str) -> bool:
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
