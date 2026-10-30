"""Tests de la grilla de cámaras: lo que reparte a sus paneles al cambiar el modo."""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                              # noqa: E402

from system.video.abstract_video_server import MODE_ANNOTATED, MODE_RAW  # noqa: E402
from ui.widgets.camera_grid import CameraGrid                           # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


class _MockConfig:
    def get(self, key: str, default: object = None) -> object:
        values = {"cameras": {"camera_1": {"name": "Uno"}, "camera_2": {"name": "Dos"}}}
        return values.get(key, default)


class TestMode:
    def test_the_panels_start_in_raw(self):
        grid = CameraGrid(_MockConfig())
        assert all(grid.get_panel(slot)._mode == MODE_RAW for slot in grid.camera_slots)

    def test_changing_the_mode_tells_every_panel(self):
        """El panel dibuja las regiones sólo en crudo: tiene que saber cuándo lo está."""
        grid = CameraGrid(_MockConfig())
        grid.set_mode(MODE_ANNOTATED)
        assert all(grid.get_panel(slot)._mode == MODE_ANNOTATED for slot in grid.camera_slots)
        grid.set_mode(MODE_RAW)
        assert all(grid.get_panel(slot)._mode == MODE_RAW for slot in grid.camera_slots)

    def test_the_signal_still_goes_out(self):
        grid = CameraGrid(_MockConfig())
        modes = []
        grid.mode_changed.connect(modes.append)
        grid.set_mode(MODE_ANNOTATED)
        assert modes == [MODE_ANNOTATED]
