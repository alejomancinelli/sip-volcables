"""
Tests del diálogo de polígono: los vértices que se agregan, mueven y borran con el mouse, y
qué guarda y dónde.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt                                   # noqa: E402
from PySide6.QtTest import QTest                                        # noqa: E402
from PySide6.QtWidgets import QApplication                              # noqa: E402

from ui.dialogs.polygon_dialog import PolygonDialog                     # noqa: E402
from ui.strings import tr                                               # noqa: E402
from ui.widgets.pause_button import PauseButton                         # noqa: E402

_KEY = "process.person_safety.exclusion_zone_px.camera_3"
_SQUARE = [[100, 100], [300, 100], [300, 300], [100, 300]]


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
        self.saved = False

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)

    def set(self, key: str, value: object):
        self.written[key] = value

    def save(self) -> bool:
        self.saved = True
        return True


def _dialog(**values) -> tuple[PolygonDialog, _MockConfig]:
    """El diálogo con un frame de 640x400 en un lienzo del mismo tamaño: escala 1, sin bordes."""
    config = _MockConfig(**values)
    dialog = PolygonDialog(config, "camera_3", _KEY)
    dialog._canvas.resize(640, 400)
    dialog.update_frame(np.full((400, 640, 3), 90, np.uint8))
    return dialog, config


def _click(dialog: PolygonDialog, x: int, y: int, button=Qt.LeftButton):
    QTest.mouseClick(dialog._canvas, button, Qt.NoModifier, QPoint(x, y))


class TestEditing:
    def test_it_starts_with_the_saved_zone(self):
        dialog, _ = _dialog(**{_KEY: _SQUARE})
        assert dialog._canvas.points == [tuple(point) for point in _SQUARE]

    def test_a_click_adds_a_vertex_at_the_end(self):
        dialog, _ = _dialog()
        _click(dialog, 50, 60)
        _click(dialog, 200, 60)
        assert dialog._canvas.points == [(50, 60), (200, 60)]

    def test_a_click_on_an_edge_inserts_the_vertex_there(self):
        dialog, _ = _dialog(**{_KEY: _SQUARE})
        _click(dialog, 200, 102)
        assert dialog._canvas.points[1] == (200, 102)
        assert len(dialog._canvas.points) == 5

    def test_dragging_a_vertex_moves_it(self):
        dialog, _ = _dialog(**{_KEY: _SQUARE})
        canvas = dialog._canvas
        QTest.mousePress(canvas, Qt.LeftButton, Qt.NoModifier, QPoint(300, 300))
        QTest.mouseMove(canvas, QPoint(350, 320))
        QTest.mouseRelease(canvas, Qt.LeftButton, Qt.NoModifier, QPoint(350, 320))
        assert canvas.points[2] == (350, 320)
        assert len(canvas.points) == 4

    def test_a_right_click_deletes_a_vertex(self):
        dialog, _ = _dialog(**{_KEY: _SQUARE})
        _click(dialog, 300, 300, Qt.RightButton)
        assert dialog._canvas.points == [(100, 100), (300, 100), (100, 300)]

    def test_clear_leaves_no_vertices(self):
        dialog, _ = _dialog(**{_KEY: _SQUARE})
        dialog._on_clear_clicked()
        assert dialog._canvas.points == []

    def test_a_vertex_outside_the_frame_is_clamped_to_it(self):
        dialog, _ = _dialog()
        dialog._canvas.resize(640, 500)
        dialog.update_frame(np.full((400, 640, 3), 90, np.uint8))
        _click(dialog, 320, 2)
        assert dialog._canvas.points == [(320, 0)]

    def test_without_a_frame_the_mouse_does_nothing(self):
        dialog = PolygonDialog(_MockConfig(), "camera_3", _KEY)
        dialog._canvas.resize(640, 400)
        _click(dialog, 50, 60)
        assert dialog._canvas.points == []


class TestSave:
    def test_it_saves_the_vertices_under_its_key_and_persists(self):
        dialog, config = _dialog(**{_KEY: _SQUARE})
        dialog._on_save_clicked()
        assert config.written == {_KEY: _SQUARE}
        assert config.saved

    def test_a_cleared_zone_is_saved_as_null(self):
        dialog, config = _dialog(**{_KEY: _SQUARE})
        dialog._on_clear_clicked()
        dialog._on_save_clicked()
        assert config.written == {_KEY: None}

    def test_one_or_two_vertices_cannot_be_saved(self):
        dialog, _ = _dialog()
        _click(dialog, 50, 60)
        _click(dialog, 200, 60)
        assert not dialog._save_button.isEnabled()
        _click(dialog, 200, 200)
        assert dialog._save_button.isEnabled()

    def test_the_count_says_how_many_vertices_there_are(self):
        dialog, _ = _dialog(**{_KEY: _SQUARE})
        assert dialog._count_label.text() == tr("polygon_vertices").format(count=4)

    def test_a_malformed_saved_zone_starts_empty(self):
        dialog, _ = _dialog(**{_KEY: [[1, 2, 3]]})
        assert dialog._canvas.points == []


class TestPause:
    def test_a_paused_dialog_keeps_the_frame_it_had(self):
        dialog, _ = _dialog()
        dialog.findChild(PauseButton).click()
        dialog.update_frame(np.full((400, 640, 3), 200, np.uint8))
        assert int(dialog._canvas._last_frame[0, 0, 0]) == 90
