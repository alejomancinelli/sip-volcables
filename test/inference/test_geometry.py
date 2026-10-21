"""
Tests de la geometría sobre detecciones: una máscara contra una línea y la distancia más
corta entre dos máscaras.

Módulo puro: las detecciones se arman a mano, con la máscara recortada a su bbox como las
entrega el modelo.
"""

import cv2
import numpy as np
import pytest

from system.inference.geometry import (
    count_px_above_y, find_closest_points, get_mask_points,
)
from system.inference.result import Detection

_SHAPE = (240, 320)


def _from_full_mask(mask: np.ndarray) -> Detection:
    """Una detección con la máscara recortada a su bbox, como la devuelve el modelo."""
    ys, xs = np.nonzero(mask)
    x1, y1, x2, y2 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
    return Detection(class_index=0, class_name="x", confidence_pct=90.0,
                     bbox_px=(x1, y1, x2, y2), mask=mask[y1:y2, x1:x2].astype(np.uint8))


def _rectangle(x1: int, y1: int, x2: int, y2: int) -> Detection:
    """Un rectángulo lleno de (x1, y1) a (x2, y2), los dos incluidos."""
    mask = np.zeros(_SHAPE, np.uint8)
    mask[y1:y2 + 1, x1:x2 + 1] = 1
    return _from_full_mask(mask)


def _brute_force_distance_px(first: np.ndarray, second: np.ndarray) -> float:
    """La distancia más corta píxel a píxel, con la transformada exacta de OpenCV."""
    distance = cv2.distanceTransform(np.where(first > 0, 0, 255).astype(np.uint8),
                                     cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    return float(distance[second > 0].min())


class TestMaskPoints:
    def test_the_points_are_placed_in_the_full_frame(self):
        points = get_mask_points(_rectangle(10, 20, 11, 20))
        assert sorted(map(tuple, points.tolist())) == [(10, 20), (11, 20)]

    def test_a_detection_without_mask_counts_its_whole_bbox(self):
        detection = Detection(class_index=0, class_name="x", confidence_pct=90.0,
                              bbox_px=(5, 5, 8, 7))
        assert len(get_mask_points(detection)) == 3 * 2


class TestAboveY:
    def test_it_counts_the_pixels_with_a_smaller_y(self):
        # Filas 90 a 109 a lo ancho de 10 columnas; la altura en y = 100.
        assert count_px_above_y(_rectangle(50, 90, 59, 109), 100) == 10 * 10

    def test_a_shape_below_the_height_has_none_above(self):
        assert count_px_above_y(_rectangle(50, 150, 59, 160), 100) == 0

    def test_a_shape_above_the_height_counts_whole(self):
        assert count_px_above_y(_rectangle(50, 10, 59, 19), 100) == 100

    def test_only_the_mask_counts_not_its_bbox(self):
        """Una máscara con huecos cuenta sus píxeles, no el rectángulo que la encierra."""
        mask = np.zeros((10, 10), np.uint8)
        mask[:, :3] = 1
        detection = Detection(class_index=0, class_name="x", confidence_pct=90.0,
                              bbox_px=(0, 0, 10, 10), mask=mask)
        assert count_px_above_y(detection, 5) == 5 * 3


class TestClosestPoints:
    def test_two_separate_shapes_give_their_gap(self):
        distance_px, point_first, point_second = find_closest_points(
            _rectangle(10, 10, 49, 49), _rectangle(80, 20, 99, 30))
        assert distance_px == pytest.approx(80 - 49)
        assert point_first[0] == 49 and point_second[0] == 80
        assert point_first[1] == point_second[1]

    def test_a_diagonal_gap_is_euclidean(self):
        distance_px, _, _ = find_closest_points(_rectangle(0, 0, 9, 9),
                                                _rectangle(12, 13, 20, 20))
        assert distance_px == pytest.approx(np.hypot(3, 4))

    def test_touching_shapes_give_zero_and_a_shared_point(self):
        first, second = _rectangle(10, 10, 49, 49), _rectangle(40, 40, 80, 80)
        distance_px, point_first, point_second = find_closest_points(first, second)
        assert distance_px == 0.0 and point_first == point_second
        x_px, y_px = point_first
        assert 40 <= x_px <= 49 and 40 <= y_px <= 49

    def test_an_empty_mask_gives_none(self):
        empty = Detection(class_index=0, class_name="x", confidence_pct=90.0,
                          bbox_px=(0, 0, 5, 5), mask=np.zeros((5, 5), np.uint8))
        assert find_closest_points(empty, _rectangle(10, 10, 20, 20)) is None

    def test_long_contours_are_still_exact(self):
        """Con contornos largos se muestrea antes de comparar, y no se pierde el par."""
        rng = np.random.default_rng()
        for _ in range(10):
            first = np.zeros(_SHAPE, np.uint8)
            second = np.zeros(_SHAPE, np.uint8)
            cv2.ellipse(first, (int(rng.integers(60, 260)), int(rng.integers(60, 180))),
                        (int(rng.integers(20, 60)), int(rng.integers(20, 60))),
                        int(rng.integers(0, 180)), 0, 360, 1, -1)
            corners = rng.integers(0, [_SHAPE[1], _SHAPE[0]], size=(5, 2)).astype(np.int32)
            cv2.fillPoly(second, [corners], 1)
            if not second.any():
                continue
            distance_px, _, _ = find_closest_points(_from_full_mask(first),
                                                    _from_full_mask(second))
            # La referencia de OpenCV sale en float32: la tolerancia es la de ese tipo.
            assert distance_px == pytest.approx(_brute_force_distance_px(first, second),
                                                abs=1e-3)
