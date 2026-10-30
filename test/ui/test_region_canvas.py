"""
Tests del lienzo de regiones: la geometría del arrastre, que son funciones puras, y la
conversión entre la pantalla y el frame.

El lienzo es un widget, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt                                   # noqa: E402
from PySide6.QtTest import QTest                                        # noqa: E402
from PySide6.QtWidgets import QApplication                              # noqa: E402

from ui.widgets import region_canvas                                    # noqa: E402
from ui.widgets.region_canvas import (                                  # noqa: E402
    CanvasRegion, RegionCanvas, build_rect_from_points, drag_rect, get_handle_at,
)

_FRAME = (200, 100)     # (ancho, alto)


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


class TestGetHandleAt:
    _RECT = (50, 40, 100, 50)

    @pytest.mark.parametrize("point, expected", [
        ((100, 65), "move"),
        ((100, 40), "n"),
        ((100, 90), "s"),
        ((50, 65), "w"),
        ((150, 65), "e"),
        ((50, 40), "nw"),
        ((150, 40), "ne"),
        ((50, 90), "sw"),
        ((150, 90), "se"),
        ((10, 10), ""),
        ((100, 200), ""),
    ])
    def test_which_part_is_under_the_mouse(self, point, expected):
        assert get_handle_at(self._RECT, point, scale=1.0) == expected

    def test_the_margin_is_measured_on_screen(self):
        """A media escala el borde se agarra desde el doble de píxeles de frame."""
        assert get_handle_at(self._RECT, (100, 52), scale=1.0) == "move"
        assert get_handle_at(self._RECT, (100, 52), scale=0.5) == "n"

    def test_just_outside_the_margin_is_outside(self):
        assert get_handle_at(self._RECT, (50 - 7, 65), scale=1.0) == ""


class TestDragRect:
    _RECT = (50, 40, 100, 50)

    def test_moving_keeps_the_size(self):
        assert drag_rect(self._RECT, "move", 10, -5, _FRAME) == (60, 35, 100, 50)

    def test_moving_stops_at_the_frame(self):
        assert drag_rect(self._RECT, "move", 500, 500, _FRAME) == (100, 50, 100, 50)
        assert drag_rect(self._RECT, "move", -500, -500, _FRAME) == (0, 0, 100, 50)

    def test_the_right_edge_grows_the_width(self):
        assert drag_rect(self._RECT, "e", 20, 0, _FRAME) == (50, 40, 120, 50)

    def test_the_left_edge_moves_the_origin_and_keeps_the_right_side(self):
        assert drag_rect(self._RECT, "w", -20, 0, _FRAME) == (30, 40, 120, 50)

    def test_a_corner_moves_two_edges(self):
        assert drag_rect(self._RECT, "nw", 10, 10, _FRAME) == (60, 50, 90, 40)

    def test_an_edge_cannot_cross_the_opposite_one(self):
        x_px, _, width_px, _ = drag_rect(self._RECT, "e", -500, 0, _FRAME)
        assert width_px == 10 and x_px == 50
        x_px, _, width_px, _ = drag_rect(self._RECT, "w", 500, 0, _FRAME)
        assert width_px == 10 and x_px + width_px == 150

    def test_resizing_stops_at_the_frame(self):
        assert drag_rect(self._RECT, "se", 500, 500, _FRAME) == (50, 40, 150, 60)


class TestBuildRectFromPoints:
    def test_any_diagonal_gives_the_same_rectangle(self):
        assert build_rect_from_points((20, 10), (70, 60), _FRAME) == (20, 10, 50, 50)
        assert build_rect_from_points((70, 60), (20, 10), _FRAME) == (20, 10, 50, 50)

    def test_it_is_cut_at_the_frame(self):
        assert build_rect_from_points((-30, -30), (500, 500), _FRAME) == (0, 0, 200, 100)

    def test_a_click_is_not_a_region(self):
        assert build_rect_from_points((20, 10), (23, 12), _FRAME) is None


def _canvas() -> RegionCanvas:
    canvas = RegionCanvas()
    canvas.resize(640, 400)
    return canvas


def _frame() -> np.ndarray:
    return np.full((_FRAME[1], _FRAME[0], 3), 90, np.uint8)


def _active(rect_px, *, is_editable: bool = True) -> CanvasRegion:
    return CanvasRegion("pipeline_1", (230, 130, 40), rect_px, is_active=True,
                        is_editable=is_editable)


class TestCanvas:
    # 200x100 en 640x400: escala 3.2, con 40 px de aire arriba y abajo.
    _SCALE = 3.2
    _TOP_PX = 40

    def _widget_point(self, x_px: float, y_px: float) -> QPoint:
        return QPoint(round(x_px * self._SCALE), round(y_px * self._SCALE) + self._TOP_PX)

    def _drag(self, canvas: RegionCanvas, start, end):
        QTest.mousePress(canvas, Qt.LeftButton, Qt.NoModifier, start)
        QTest.mouseMove(canvas, end)
        QTest.mouseRelease(canvas, Qt.LeftButton, Qt.NoModifier, end)

    def test_it_knows_the_frame_size_once_it_has_one(self):
        canvas = _canvas()
        assert canvas.frame_size_px == (0, 0)
        canvas.set_frame(_frame())
        assert canvas.frame_size_px == _FRAME

    def test_it_paints_a_pixmap_the_size_of_the_widget(self):
        canvas = _canvas()
        canvas.set_frame(_frame())
        canvas.set_regions([_active((10, 10, 50, 50))])
        assert canvas.pixmap().size() == canvas.size()

    def test_a_grey_frame_is_accepted(self):
        canvas = _canvas()
        canvas.set_frame(np.full((100, 200), 90, np.uint8))
        assert canvas.frame_size_px == _FRAME

    def test_dragging_inside_moves_the_active_region(self):
        canvas = _canvas()
        canvas.set_frame(_frame())
        canvas.set_regions([_active((50, 20, 100, 50))])
        edits = []
        canvas.region_edited.connect(lambda *rect: edits.append(rect))
        self._drag(canvas, self._widget_point(100, 45), self._widget_point(110, 45))
        assert edits[-1] == (60, 20, 100, 50)

    def test_dragging_an_edge_resizes_it(self):
        canvas = _canvas()
        canvas.set_frame(_frame())
        canvas.set_regions([_active((50, 20, 100, 50))])
        edits = []
        canvas.region_edited.connect(lambda *rect: edits.append(rect))
        self._drag(canvas, self._widget_point(150, 45), self._widget_point(170, 45))
        assert edits[-1] == (50, 20, 120, 50)

    def test_dragging_outside_draws_a_new_one(self):
        canvas = _canvas()
        canvas.set_frame(_frame())
        canvas.set_regions([_active(None)])
        edits = []
        canvas.region_edited.connect(lambda *rect: edits.append(rect))
        self._drag(canvas, self._widget_point(20, 10), self._widget_point(80, 60))
        assert edits[-1] == (20, 10, 60, 50)

    def test_a_region_that_is_not_editable_ignores_the_mouse(self):
        canvas = _canvas()
        canvas.set_frame(_frame())
        canvas.set_regions([_active((50, 20, 100, 50), is_editable=False)])
        edits = []
        canvas.region_edited.connect(lambda *rect: edits.append(rect))
        self._drag(canvas, self._widget_point(100, 45), self._widget_point(110, 45))
        assert edits == []

    def test_a_reference_region_never_moves(self):
        canvas = _canvas()
        canvas.set_frame(_frame())
        other = CanvasRegion("pipeline_2", (225, 215, 40), (50, 20, 100, 50))
        canvas.set_regions([other, _active((0, 0, 20, 20))])
        edits = []
        canvas.region_edited.connect(lambda *rect: edits.append(rect))
        self._drag(canvas, self._widget_point(100, 45), self._widget_point(110, 45))
        assert all(rect[2:] != (100, 50) for rect in edits)

    def test_without_a_frame_the_mouse_does_nothing(self):
        canvas = _canvas()
        canvas.set_regions([_active((50, 20, 100, 50))])
        edits = []
        canvas.region_edited.connect(lambda *rect: edits.append(rect))
        self._drag(canvas, QPoint(100, 100), QPoint(120, 100))
        assert edits == []

    def test_a_drag_that_ends_leaves_the_next_move_alone(self):
        canvas = _canvas()
        canvas.set_frame(_frame())
        canvas.set_regions([_active((50, 20, 100, 50))])
        edits = []
        canvas.region_edited.connect(lambda *rect: edits.append(rect))
        self._drag(canvas, self._widget_point(100, 45), self._widget_point(110, 45))
        count = len(edits)
        QTest.mouseMove(canvas, self._widget_point(130, 45))
        assert len(edits) == count


def test_the_painter_helper_is_shared_with_the_camera_panel():
    """El panel de cámara dibuja con la misma función: una región se ve igual en los dos."""
    from ui.widgets import camera_panel
    assert camera_panel.paint_region_box is region_canvas.paint_region_box
