"""
Migración del config: el ROI de cámara y `crop_to_roi` ya no existen.

Maquinaria genérica: reutilizable entre proyectos. Un config de antes de que el recorte
fuera de cada pipeline trae `cameras.<slot>.roi`, que el motor aplicaba a todo pipeline sin
ROI propio, y a veces `inference.overlay.crop_to_roi`. Ninguno de los dos lo lee ya nadie, y
dejarlos sin más cambiaría lo que mide el equipo sin ningún error: el pipeline que heredaba
el recorte pasaría a inferir el frame completo. Por eso se **migra y no se borra**:

  - un `cameras.<slot>.roi` prendido se copia como ROI propio a cada pipeline que mira esa
    cámara y no declara el suyo —uno que declara el suyo, aunque sea apagado, ya decidió—;
  - se copia también a `cameras.<slot>.lens_health.roi`, que es dónde mide ahora la óptica
    —ahí el rectángulo es otra pregunta: dónde hay textura nítida para medir el vidrio—, salvo
    que esa cámara ya tenga el suyo;
  - la clave vieja se borra, prendida o no, y un `crop_to_roi` también: queda un aviso en el
    log si estaba prendido, porque el monitor ahora siempre muestra el frame completo;
  - `full_resolution` de un ROI de pipeline se borra: el corte del video nativo ya no se
    elige, lo decide la cámara. Uno en `false` ya no se respeta, y se avisa;
  - `output_short_side_px` también: la vista sale siempre a la entrada del modelo. Uno mayor
    que 0 ya no se respeta, y se avisa.

`migrate_roi_config()` es idempotente: sin claves viejas no hace nada, así que corre en cada
arranque. Con `save=True` persiste el archivo —que deja su copia `.bak`—; con `save=False`
sólo cambia lo que hay en memoria, que es lo que quiere la corrida de imágenes sueltas: no
debería reescribir el config de quien la usa, pero sí medir como mediría la línea.
"""

from system.config_manager import ConfigManager
from system.logger import logger

from .regions import select_pipeline_roi

_GEOMETRY_KEYS = ("x_px", "y_px", "width_px", "height_px")


def migrate_roi_config(config: ConfigManager, *, save: bool = True) -> int:
    """
    Aplica la migración y devuelve cuántas claves tocó. 0 es que no había nada que migrar.

    Sólo escribe el archivo si cambió algo.
    """
    changes = 0
    cameras = config.get("cameras", {}) or {}
    pipelines = config.get("inference.pipelines", {}) or {}
    for camera_slot, camera in cameras.items():
        if not isinstance(camera, dict) or "roi" not in camera:
            continue
        roi = camera["roi"]
        if isinstance(roi, dict) and roi.get("enabled", False):
            geometry = {key: int(roi.get(key, 0) or 0) for key in _GEOMETRY_KEYS}
            changes += _copy_to_pipelines(config, pipelines, camera_slot, geometry)
            changes += _copy_to_lens_health(config, camera, camera_slot, geometry)
        config.delete(f"cameras.{camera_slot}.roi")
        changes += 1

    changes += _drop_view_keys(config, pipelines)

    if "crop_to_roi" in (config.get("inference.overlay", {}) or {}):
        if config.get("inference.overlay.crop_to_roi", False):
            logger.warning(
                "[Config] inference.overlay.crop_to_roi ya no existe: el monitor muestra "
                "siempre el frame completo, con el ROI de cada pipeline dibujado encima. "
                "Se quita del config.")
        config.delete("inference.overlay.crop_to_roi")
        changes += 1

    if changes and save:
        config.save()
    return changes


def _drop_view_keys(config: ConfigManager, pipelines: dict) -> int:
    """
    Borra `full_resolution` y `output_short_side_px` de los ROI de los pipelines, avisando del
    que pedía algo que ya no se respeta.
    """
    drops = 0
    for pipeline_slot, section in pipelines.items():
        rois = section.get("roi") if isinstance(section, dict) else None
        for camera_slot, roi in (rois.items() if isinstance(rois, dict) else ()):
            if not isinstance(roi, dict):
                continue
            key = f"inference.pipelines.{pipeline_slot}.roi.{camera_slot}"
            is_enabled = roi.get("enabled", False)
            if "full_resolution" in roi:
                if not roi["full_resolution"] and is_enabled:
                    logger.warning(
                        f"[Config] {pipeline_slot} en {camera_slot} tenía `full_resolution: "
                        f"false`: ya no se elige, la región se corta del video nativo si la "
                        f"cámara decodifica video. Se quita la clave.")
                config.delete(f"{key}.full_resolution")
                drops += 1
            if "output_short_side_px" in roi:
                if int(roi["output_short_side_px"] or 0) > 0 and is_enabled:
                    logger.warning(
                        f"[Config] {pipeline_slot} en {camera_slot} tenía `output_short_side_px: "
                        f"{roi['output_short_side_px']}`: ya no se elige, la vista sale a la "
                        f"entrada del modelo. Se quita la clave.")
                config.delete(f"{key}.output_short_side_px")
                drops += 1
    return drops


def _copy_to_pipelines(config: ConfigManager, pipelines: dict, camera_slot: str,
                       geometry: dict) -> int:
    """Copia el recorte de la cámara a cada pipeline que la mira y no declara su ROI."""
    copies = 0
    for pipeline_slot, section in pipelines.items():
        if not isinstance(section, dict):
            continue
        cameras = section.get("cameras") or []
        if cameras and camera_slot not in cameras:
            continue
        if select_pipeline_roi(section, camera_slot) is not None:
            continue
        config.set(f"inference.pipelines.{pipeline_slot}.roi.{camera_slot}",
                   {"enabled": True, **geometry})
        logger.warning(
            f"[Config] El ROI de {camera_slot} pasó a ser el de {pipeline_slot} en esa "
            f"cámara: {geometry}. El ROI de cámara ya no existe.")
        copies += 1
    return copies


def _copy_to_lens_health(config: ConfigManager, camera: dict, camera_slot: str,
                         geometry: dict) -> int:
    """Le da a la óptica el recorte de la cámara, si esa cámara no tiene el suyo."""
    lens_health = camera.get("lens_health")
    own = lens_health.get("roi") if isinstance(lens_health, dict) else None
    if isinstance(own, dict) and own.get("enabled", False):
        return 0
    config.set(f"cameras.{camera_slot}.lens_health.roi", {"enabled": True, **geometry})
    logger.warning(
        f"[Config] El ROI de {camera_slot} pasó a ser la región de medición de su óptica.")
    return 1
