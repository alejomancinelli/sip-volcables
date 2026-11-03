"""
Inferencia fuera de la app: los pipelines del config, corridos sobre imágenes sueltas.

Maquinaria genérica: reutilizable entre proyectos. Arma cada pipeline habilitado igual que
la app —la misma clase por slot, de `create_pipeline()`, el mismo preprocessor y los mismos
hooks del proyecto— y corre el ciclo del motor con `InferenceThread.process()`, sin arrancar el hilo. El resultado de
una imagen es el que saldría en vivo si esa imagen llegara de la cámara: no hay un segundo
camino de inferencia que pueda divergir del primero.

    runner = OfflineRunner(config)
    runner.load()
    for result in runner.run_image(frame_bgr, "camera_1"):   # uno por pipeline
        ...
    runner.unload()

Qué no hace, a propósito:
  - **No consulta la licencia.** Es una herramienta de desarrollo y corre desde fuentes,
    donde el estado es `unlicensed_build` igual.
  - **No lee ni escribe archivos.** De dónde salen las imágenes y dónde queda el resultado
    es de quien lo llama; así esto se prueba sin disco.
  - **No respeta `interval_s`** ni la cola de la cámara: cada imagen que se le pasa se
    infiere.

`apply_preprocessor=False` es para las imágenes que ya son frame de referencia —las del
dataset, que se guardan corregidas y ajustadas—: pasarles el preprocessor otra vez les
corregiría el lente y les multiplicaría el brillo dos veces.

**Una cámara con vistas** —un pipeline tiene un ROI en una cámara que decodifica video— recibe la
imagen como la entrega el video: nativa y sin rotar. Se le hace lo que en vivo hace el
decodificador por CPU (`decoder.render()`): se reduce al tamaño de `decode`, se le cortan
las vistas del nativo y se rota, así el pipeline ve la misma vista que en la línea. Una
cámara sin vistas recibe la imagen tal cual, como siempre.
"""

import numpy as np

from system.config_manager import ConfigManager
from system.logger import logger
from tools.camera.abstract_driver import FrameView, rotate_frame
from tools.camera.decoder import BACKEND_CPU, DecodeSettings, read_decode_settings, render

from .engine import InferenceThread
from .hooks import build_hooks
from .abstract_pipeline import AbstractPipeline
from .pipeline_factory import create_pipeline
from .preprocessing import build_camera_views, build_preprocessor
from .result import InferenceResult
from .roi_migration import migrate_roi_config


class OfflineRunner:
    """
    Los pipelines habilitados del config, listos para correr imágenes de a una.

    Se usa desde un solo hilo: los pipelines no son reentrantes. Cargar y descargar los
    modelos es explícito porque es lo caro, y quien corre una carpeta lo paga una vez.
    """

    def __init__(self, config: ConfigManager, *, apply_preprocessor: bool = True):
        # En memoria y sin reescribir el config de quien lo usa: se mide como mediría la
        # línea con ese mismo archivo, que la migra al arrancar.
        migrate_roi_config(config, save=False)
        preprocessor = build_preprocessor(config) if apply_preprocessor else None
        hooks = build_hooks(config)

        self._pipelines: list[AbstractPipeline] = []
        self._engines: list[InferenceThread] = []
        for pipeline_slot in (config.get("inference.pipelines", {}) or {}):
            if not config.get(f"inference.pipelines.{pipeline_slot}.enabled", True):
                logger.info(f"[Offline] {pipeline_slot} deshabilitado: no se corre.")
                continue
            pipeline = create_pipeline(config, pipeline_slot)
            # Sin `annotate_gate`: acá el anotado se dibuja siempre, porque es lo que se
            # viene a mirar.
            self._engines.append(InferenceThread(
                config, pipeline,
                preprocessor=preprocessor,
                classifier=hooks.classifier,
                analyzer=hooks.analyzer,
                annotator=hooks.annotator,
            ))
            self._pipelines.append(pipeline)

        self._config = config
        self._camera_views = build_camera_views(
            config, {pipeline.pipeline_slot: pipeline for pipeline in self._pipelines})
        self._decode_by_camera: dict[str, DecodeSettings] = {}

    @property
    def pipeline_slots(self) -> tuple[str, ...]:
        return tuple(engine.pipeline_slot for engine in self._engines)

    def load(self):
        """Carga los modelos de todos los pipelines. Una etapa que no carga queda en error."""
        for pipeline in self._pipelines:
            pipeline.load()

    def unload(self):
        for pipeline in self._pipelines:
            pipeline.unload()

    def get_status(self) -> dict:
        """`get_status()` del motor de cada pipeline, por slot: modelos cargados y errores."""
        return {engine.pipeline_slot: engine.get_status() for engine in self._engines}

    def run_image(self, frame_bgr: np.ndarray, camera_slot: str) -> list[InferenceResult]:
        """
        Corre la imagen por cada pipeline que recibe esa cámara, en el orden del config.

        Qué pipelines la reciben lo dice `inference.pipelines.<slot>.cameras`, igual que en
        vivo; una lista vacía las recibe a todas. Una cámara que no entra a ninguno
        devuelve lista vacía. En una cámara con vistas la imagen es la del video nativo
        (ver el docstring del módulo).
        """
        frame_bgr, views = self._render(frame_bgr, camera_slot)
        return [engine.process(frame_bgr, camera_slot, views) for engine in self._engines
                if engine.has_camera(camera_slot)]

    def _render(self, image_bgr: np.ndarray,
                camera_slot: str) -> tuple[np.ndarray, dict[str, FrameView]]:
        """(frame, vistas) de esa imagen, como los entregaría la cámara; sin vistas, tal cual."""
        camera_views = self._camera_views.get(camera_slot)
        if not camera_views:
            return image_bgr, {}
        settings = self._decode_by_camera.get(camera_slot)
        if settings is None:
            # Siempre por CPU: es lo que hay en una corrida sin video, y las dos vías
            # entregan lo mismo.
            config_cam = dict(self._config.get(f"cameras.{camera_slot}", {}) or {})
            config_cam["decode"] = {**(config_cam.get("decode") or {}),
                                    "backend": BACKEND_CPU, "views": camera_views}
            settings = read_decode_settings(config_cam, camera_slot)
            self._decode_by_camera[camera_slot] = settings
        frame_bgr, views = render(image_bgr, settings)
        return rotate_frame(frame_bgr, settings.rotation), {
            name: FrameView(rotate_frame(view.image_bgr, settings.rotation), view.rect_px)
            for name, view in views.items()}
