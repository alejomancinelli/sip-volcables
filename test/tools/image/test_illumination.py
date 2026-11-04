"""Tests del brillo medio: la escala 0–100 y los casos sin imagen."""

import numpy as np
import pytest

from tools.image.illumination import compute_illumination_pct


class TestIllumination:
    @pytest.mark.parametrize("level, expected_pct", [(0, 0), (255, 100), (128, 50)])
    def test_a_flat_image_reads_its_level_on_a_0_to_100_scale(self, level, expected_pct):
        image = np.full((10, 10, 3), level, np.uint8)
        assert compute_illumination_pct(image) == expected_pct

    def test_a_grayscale_image_reads_the_same_as_its_color_copy(self):
        gray = np.full((10, 10), 200, np.uint8)
        color = np.dstack([gray] * 3)
        assert compute_illumination_pct(gray) == compute_illumination_pct(color)

    @pytest.mark.parametrize("image", [None, np.zeros((0, 0, 3), np.uint8)])
    def test_no_image_reads_zero(self, image):
        assert compute_illumination_pct(image) == 0

    def test_a_strided_view_reads_like_the_whole_image(self):
        """El brillo de la cámara se mide salteando píxeles: tiene que dar el mismo número."""
        image = np.zeros((40, 40, 3), np.uint8)
        image[:, :20] = 200
        image[:, 20:] = 100
        assert compute_illumination_pct(image[::4, ::4]) == compute_illumination_pct(image)
