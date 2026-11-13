"""
Tests del diálogo de ROI: que la pausa congele el frame sobre el que se dibuja.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                              # noqa: E402

from ui.dialogs.roi_dialog import RoiDialog                             # noqa: E402
from ui.widgets.pause_button import PauseButton                         # noqa: E402

_PREFIX = "cameras.camera_1.lens_health.roi"


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


class _MockConfig:
    """ConfigManager mínimo con `get`, `set` y `save`, sin archivo ni Lock."""

    def __init__(self, **values):
        self._values = dict(values)
        self.written: dict = {}

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)

    def set(self, key: str, value: object):
        self.written[key] = value

    def save(self) -> bool:
        return True


def _frame(value: int) -> np.ndarray:
    return np.full((240, 320, 3), value, np.uint8)


class TestPause:
    def test_a_paused_dialog_keeps_the_frame_it_had(self):
        dialog = RoiDialog(_MockConfig(), "camera_1", config_prefix=_PREFIX)
        dialog.update_frame(_frame(10))
        dialog.findChild(PauseButton).click()
        dialog.update_frame(_frame(200))
        assert dialog._last_frame[0, 0, 0] == 10

    def test_resuming_takes_the_next_frame(self):
        dialog = RoiDialog(_MockConfig(), "camera_1", config_prefix=_PREFIX)
        dialog.update_frame(_frame(10))
        button = dialog.findChild(PauseButton)
        button.click()
        button.click()
        dialog.update_frame(_frame(200))
        assert dialog._last_frame[0, 0, 0] == 200


class TestEditing:
    def test_a_rectangle_edited_on_the_image_fills_the_fields(self):
        dialog = RoiDialog(_MockConfig(), "camera_1", config_prefix=_PREFIX)
        dialog._canvas.region_edited.emit(10, 20, 30, 40)
        assert [dialog._spins[key].value() for key in ("x_px", "y_px", "width_px",
                                                       "height_px")] == [10, 20, 30, 40]

    def test_a_typed_value_moves_the_rectangle_on_the_image(self):
        roi = {"x_px": 10, "y_px": 20, "width_px": 30, "height_px": 40}
        dialog = RoiDialog(_MockConfig(**{_PREFIX: roi}), "camera_1", config_prefix=_PREFIX)
        dialog._spins["x_px"].setValue(50)
        assert dialog._canvas._regions[0].rect_px == (50, 20, 30, 40)

    def test_without_width_there_is_no_rectangle_to_drag(self):
        """Sin recuadro, arrastrar sobre la imagen dibuja uno nuevo en vez de mover uno vacío."""
        dialog = RoiDialog(_MockConfig(), "camera_1", config_prefix=_PREFIX)
        assert dialog._canvas._regions[0].rect_px is None
