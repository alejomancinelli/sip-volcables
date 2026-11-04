"""
Brillo medio de una imagen, 0–100.

Librería portable: sin Qt, sin ConfigManager, sin I/O y sin nada del repo.

Es la única cuenta de brillo del programa, y por eso la comparten el mínimo de iluminación
de cada pipeline, el brillo de la cámara que se publica y el que muestra el diálogo de
regiones: el número que el operador lee al fijar un umbral tiene que ser el mismo con el que
el motor lo compara.
"""

import cv2
import numpy as np


def compute_illumination_pct(image_bgr: np.ndarray | None) -> int:
    """Brillo medio de la imagen en escala de grises, 0–100; 0 si no hay imagen."""
    if image_bgr is None or image_bgr.size == 0:
        return 0
    gray = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2GRAY) if image_bgr.ndim == 3 else image_bgr
    return min(100, int(float(np.mean(gray)) * 100 / 255))
