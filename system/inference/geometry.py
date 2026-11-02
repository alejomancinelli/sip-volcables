"""
Geometría sobre detecciones: una máscara contra una altura, y la distancia entre dos máscaras.

Módulo puro: sin Qt, sin ConfigManager y sin I/O. Maquinaria genérica: no sabe qué es una
calza ni una rueda, mide píxeles de las máscaras que recibe.

Las máscaras llegan recortadas a su bbox (ver `result.py`), así que acá se ubican en
coordenadas del frame completo antes de medir. Una detección sin máscara cuenta con su bbox
entero, que es lo que un detector sin segmentación sabe de la forma.
"""

import cv2
import numpy as np

from .result import Detection

Point = tuple[int, int]

_PAIR_CHUNK = 512           # puntos por tanda: acota la matriz de distancias en memoria
_MAX_DIRECT_PAIRS = 20_000  # por encima, `find_closest_points` muestrea antes de comparar


def get_mask_points(detection: Detection) -> np.ndarray:
    """Píxeles de la detección como un array (N, 2) de `(x, y)` en el frame completo."""
    mask, x1, y1 = _get_placed_mask(detection)
    ys, xs = np.nonzero(mask)
    return np.column_stack((xs + x1, ys + y1))


def count_px_above_y(detection: Detection, y_px: int) -> int:
    """Píxeles de la máscara por encima de esa fila del frame: con una `y` menor que `y_px`."""
    mask, _, y1 = _get_placed_mask(detection)
    return int(np.count_nonzero(mask[:max(0, int(y_px) - y1)]))


def find_closest_points(first: Detection,
                        second: Detection) -> tuple[float, Point, Point] | None:
    """
    La distancia más corta entre las dos máscaras, en px, y el punto de cada una que la da.

    Devuelve `(distance_px, point_first, point_second)`, con distancia 0 y el mismo punto en
    los dos lados si las máscaras se tocan, o None si alguna está vacía.

    Es exacta y se busca sólo sobre los contornos: entre dos formas que no se tocan, el par
    más cercano está siempre en el borde de cada una. Con contornos largos —una rueda tiene
    miles de puntos— no se comparan todos contra todos: una pasada con un punto cada `step`
    da una cota de la distancia, y sólo los puntos que quedan a menos de esa cota más
    `step · √2` del contorno muestreado del otro pueden ser parte del par. Dos puntos
    seguidos de un contorno son vecinos, así que ningún punto queda a más de `step · √2`
    de una muestra de su propio contorno: el par verdadero nunca se descarta.
    """
    overlap = _find_overlap(first, second)
    if overlap is not None:
        return 0.0, overlap, overlap
    first_contours = _get_contours(first)
    second_contours = _get_contours(second)
    if not first_contours or not second_contours:
        return None
    first_points = np.vstack(first_contours)
    second_points = np.vstack(second_contours)

    step = int(np.ceil(np.sqrt(len(first_points) * len(second_points) / _MAX_DIRECT_PAIRS)))
    if step > 1:
        first_samples = np.vstack([contour[::step] for contour in first_contours])
        second_samples = np.vstack([contour[::step] for contour in second_contours])
        bound_squared, _, _ = _find_closest_pair(first_samples, second_samples)
        radius_squared = (np.sqrt(max(0.0, bound_squared)) + step * np.sqrt(2.0)) ** 2
        first_points = first_points[
            _get_min_squared_distances(first_points, second_samples) <= radius_squared]
        second_points = second_points[
            _get_min_squared_distances(second_points, first_samples) <= radius_squared]

    best_squared, i, j = _find_closest_pair(first_points, second_points)
    point_first = first_points[i]
    point_second = second_points[j]
    return (float(np.sqrt(max(0.0, best_squared))),
            (int(point_first[0]), int(point_first[1])),
            (int(point_second[0]), int(point_second[1])))


def _get_squared_distances(points: np.ndarray, others: np.ndarray) -> np.ndarray:
    """
    Distancias al cuadrado de cada punto contra cada otro, (len(points), len(others)).

    |a - b|² = |a|² + |b|² - 2·a·b: un producto de matrices en vez de restar par por par.
    En float64 es exacto con coordenadas de píxel enteras.
    """
    points = points.astype(np.float64)
    others = others.astype(np.float64)
    return ((points ** 2).sum(axis=1)[:, None] + (others ** 2).sum(axis=1)[None, :]
            - 2.0 * points @ others.T)


def _get_min_squared_distances(points: np.ndarray, others: np.ndarray) -> np.ndarray:
    """La distancia al cuadrado de cada punto al más cercano de `others`."""
    return np.concatenate([
        _get_squared_distances(points[start:start + _PAIR_CHUNK], others).min(axis=1)
        for start in range(0, len(points), _PAIR_CHUNK)])


def _find_closest_pair(points: np.ndarray, others: np.ndarray) -> tuple[float, int, int]:
    """El par más cercano entre los dos conjuntos: `(distancia², índice en points, en others)`."""
    best_squared, best_i, best_j = None, 0, 0
    for start in range(0, len(points), _PAIR_CHUNK):
        squared = _get_squared_distances(points[start:start + _PAIR_CHUNK], others)
        i, j = np.unravel_index(int(np.argmin(squared)), squared.shape)
        if best_squared is None or squared[i, j] < best_squared:
            best_squared, best_i, best_j = float(squared[i, j]), start + int(i), int(j)
    return best_squared, best_i, best_j


def _get_placed_mask(detection: Detection) -> tuple[np.ndarray, int, int]:
    """
    La máscara de la detección y la esquina del frame donde va.

    El tamaño sale de la máscara y no del bbox: si no coinciden, lo que se mide es la forma
    que dio el modelo. Sin máscara, el bbox entero.
    """
    x1, y1, x2, y2 = (int(value) for value in detection.bbox_px)
    if detection.mask is None:
        return np.ones((max(0, y2 - y1), max(0, x2 - x1)), np.uint8), x1, y1
    return (detection.mask > 0).astype(np.uint8), x1, y1


def _find_overlap(first: Detection, second: Detection) -> Point | None:
    """Un píxel que las dos máscaras comparten, o None si no se tocan."""
    first_mask, ax, ay = _get_placed_mask(first)
    second_mask, bx, by = _get_placed_mask(second)
    x1, y1 = max(ax, bx), max(ay, by)
    x2 = min(ax + first_mask.shape[1], bx + second_mask.shape[1])
    y2 = min(ay + first_mask.shape[0], by + second_mask.shape[0])
    if x1 >= x2 or y1 >= y2:
        return None
    shared = (first_mask[y1 - ay:y2 - ay, x1 - ax:x2 - ax]
              & second_mask[y1 - by:y2 - by, x1 - bx:x2 - bx])
    ys, xs = np.nonzero(shared)
    if not len(xs):
        return None
    return int(xs[0] + x1), int(ys[0] + y1)


def _get_contours(detection: Detection) -> list[np.ndarray]:
    """
    Los contornos exteriores de la máscara, cada uno (N, 2) de `(x, y)` en el frame.

    Por separado y no juntos: el muestreo de `find_closest_points` arranca cada contorno en
    su primer punto, que es lo que garantiza que todo punto tenga una muestra cerca.
    """
    mask, x1, y1 = _get_placed_mask(detection)
    if not mask.size:
        return []
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    origin = np.array([x1, y1], np.int32)
    return [contour.reshape(-1, 2).astype(np.int32) + origin for contour in contours]
