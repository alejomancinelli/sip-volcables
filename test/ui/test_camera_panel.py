"""
Tests del panel de cámara: lo que cuesta pintar un frame en el hilo de la GUI.

Construye widgets, así que necesita un `QApplication` sin pantalla —plataforma
`offscreen`—, igual que `test_process_tab.py`.
"""

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                              # noqa: E402

from system.inference import overlay                                    # noqa: E402
from system.video.abstract_video_server import MODE_ANNOTATED, MODE_RAW  # noqa: E402
from ui.widgets.camera_panel import CameraPanel                         # noqa: E402

_SLOT = "camera_1"
_FRAME_SHAPE = (1536, 2048, 3)      # un sensor de 3 MP


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


class _MockConfig:
    def get(self, key: str, default: object = None) -> object:
        return {f"cameras.{_SLOT}.name": "Entrada"}.get(key, default)


def _frame() -> np.ndarray:
    return np.full(_FRAME_SHAPE, 90, np.uint8)


def _shown_panel() -> CameraPanel:
    panel = CameraPanel(_SLOT, _MockConfig())
    panel.resize(660, 520)
    panel.show()
    QApplication.processEvents()
    return panel


class TestPainting:
    def test_a_hidden_panel_keeps_the_frame_without_painting_it(self):
        """Nadie lo ve: la conversión de 3 MP por frame era gasto puro en la GUI."""
        panel = CameraPanel(_SLOT, _MockConfig())
        panel.update_frame(_frame())
        assert panel._video_label.pixmap().isNull()

    def test_it_paints_the_kept_frame_when_shown(self):
        panel = CameraPanel(_SLOT, _MockConfig())
        panel.update_frame(_frame())
        panel.resize(660, 520)
        panel.show()
        QApplication.processEvents()
        assert not panel._video_label.pixmap().isNull()

    def test_the_pixmap_fits_the_label_and_keeps_the_aspect(self):
        panel = _shown_panel()
        panel.update_frame(_frame())
        pixmap = panel._video_label.pixmap()
        label = panel._video_label.size()
        assert pixmap.width() <= label.width() and pixmap.height() <= label.height()
        assert abs(pixmap.width() / pixmap.height() - 2048 / 1536) < 0.02

    def test_the_frame_is_shrunk_before_it_reaches_qt(self, monkeypatch):
        """Qt recibe una imagen del tamaño del label, no los 3 MP del sensor."""
        import ui.widgets.camera_panel as camera_panel
        sizes = []

        class _SpyImage(camera_panel.QImage):
            def __init__(self, data, width, height, *args):
                sizes.append((width, height))
                super().__init__(data, width, height, *args)

        panel = _shown_panel()
        monkeypatch.setattr(camera_panel, "QImage", _SpyImage)
        panel.update_frame(_frame())
        label = panel._video_label.size()
        assert sizes and all(w <= label.width() and h <= label.height() for w, h in sizes)


class _RegionsConfig:
    """Un config con pipelines que miran la cámara, y una `revision` que el test mueve."""

    def __init__(self, pipelines: dict, camera_roi: dict | None = None):
        self.revision = 0
        self.values = {
            f"cameras.{_SLOT}.name": "Entrada",
            "inference.pipelines": pipelines,
            f"cameras.{_SLOT}.roi": camera_roi or {"enabled": False},
        }

    def get(self, key: str, default: object = None) -> object:
        return self.values.get(key, default)


_OWN_ROI = {"enabled": True, "x_px": 200, "y_px": 150, "width_px": 800, "height_px": 600}


def _blue_pixels(panel: CameraPanel, color_bgr: tuple) -> int:
    """Cuántos píxeles del pixmap tienen exactamente el color de ese pipeline."""
    image = panel._video_label.pixmap().toImage()
    blue, green, red = color_bgr
    target = (red << 16) | (green << 8) | blue
    return sum(1 for y in range(image.height()) for x in range(image.width())
               if image.pixel(x, y) & 0xFFFFFF == target)


class TestRegions:
    """El panel dibuja las regiones de los pipelines sólo en crudo: en anotado las dibuja el overlay."""

    _BLUE = overlay.PIPELINE_COLORS_BGR[0]
    _CYAN = overlay.PIPELINE_COLORS_BGR[1]

    def _panel(self, pipelines: dict, camera_roi: dict | None = None):
        config = _RegionsConfig(pipelines, camera_roi)
        panel = CameraPanel(_SLOT, config)
        panel.resize(660, 520)
        panel.show()
        QApplication.processEvents()
        panel.update_frame(_frame())
        return panel, config

    def test_in_raw_mode_it_draws_the_region_in_the_pipeline_color(self):
        panel, _ = self._panel({"pipeline_1": {"roi": {_SLOT: _OWN_ROI}}})
        assert _blue_pixels(panel, self._BLUE) > 0

    def test_in_annotated_mode_it_draws_nothing(self):
        panel, _ = self._panel({"pipeline_1": {"roi": {_SLOT: _OWN_ROI}}})
        panel.set_mode(MODE_ANNOTATED)
        assert _blue_pixels(panel, self._BLUE) == 0

    def test_going_back_to_raw_draws_them_again(self):
        panel, _ = self._panel({"pipeline_1": {"roi": {_SLOT: _OWN_ROI}}})
        panel.set_mode(MODE_ANNOTATED)
        panel.set_mode(MODE_RAW)
        assert _blue_pixels(panel, self._BLUE) > 0

    def test_a_pipeline_that_sees_the_whole_frame_draws_nothing(self):
        panel, _ = self._panel({"pipeline_1": {"roi": {_SLOT: {"enabled": False}}}})
        assert _blue_pixels(panel, self._BLUE) == 0

    def test_a_pipeline_that_is_off_draws_nothing(self):
        panel, _ = self._panel(
            {"pipeline_1": {"enabled": False, "roi": {_SLOT: _OWN_ROI}}})
        assert _blue_pixels(panel, self._BLUE) == 0

    def test_a_pipeline_of_another_camera_draws_nothing(self):
        panel, _ = self._panel(
            {"pipeline_1": {"cameras": ["camera_2"], "roi": {_SLOT: _OWN_ROI}}})
        assert _blue_pixels(panel, self._BLUE) == 0

    def test_each_pipeline_has_its_own_color(self):
        panel, _ = self._panel({"pipeline_1": {"roi": {_SLOT: _OWN_ROI}},
                                "pipeline_2": {"roi": {_SLOT: {**_OWN_ROI, "x_px": 900}}}})
        assert _blue_pixels(panel, self._BLUE) > 0
        assert _blue_pixels(panel, self._CYAN) > 0

    def test_a_change_in_the_config_is_picked_up(self):
        panel, config = self._panel({"pipeline_1": {"roi": {_SLOT: {"enabled": False}}}})
        config.values["inference.pipelines"] = {"pipeline_1": {"roi": {_SLOT: _OWN_ROI}}}
        config.revision += 1
        panel.update_frame(_frame())
        assert _blue_pixels(panel, self._BLUE) > 0

    def test_without_a_revision_the_config_is_read_once(self):
        """Un doble de test sin `revision` no puede hacer que se relea el config por frame."""
        panel = CameraPanel(_SLOT, _MockConfig())
        panel.resize(660, 520)
        panel.show()
        QApplication.processEvents()
        panel.update_frame(_frame())
        panel.update_frame(_frame())
        assert panel._read_regions() == []


class TestNoCameraRoi:
    def test_a_leftover_camera_roi_is_not_drawn(self):
        """La cámara no tiene región: el recuadro azul de antes no vuelve."""
        config = _RegionsConfig({"pipeline_1": {}}, camera_roi=_OWN_ROI)
        panel = CameraPanel(_SLOT, config)
        panel.resize(660, 520)
        panel.show()
        QApplication.processEvents()
        panel.update_frame(_frame())
        assert _blue_pixels(panel, overlay.PIPELINE_COLORS_BGR[0]) == 0
