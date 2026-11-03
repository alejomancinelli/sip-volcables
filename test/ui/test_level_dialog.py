"""
Tests del diálogo de nivel: qué guarda, dónde, y que lo marcado con el mouse llegue al campo.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                              # noqa: E402

from ui.dialogs.level_dialog import LevelDialog, LevelSnapshot          # noqa: E402
from ui.strings import tr                                               # noqa: E402
from ui.widgets.pause_button import PauseButton                         # noqa: E402

_KEY = "process.stopper_check.raised_y_px.camera_1"


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


def _dialog(**values) -> tuple[LevelDialog, _MockConfig]:
    config = _MockConfig(**values)
    return LevelDialog(config, "camera_1", _KEY), config


class TestLevelDialog:
    def test_it_starts_at_the_configured_height(self):
        dialog, _ = _dialog(**{_KEY: 410})
        assert dialog._y_spin.value() == 410

    def test_without_a_height_it_starts_at_the_top(self):
        dialog, _ = _dialog()
        assert dialog._y_spin.value() == 0

    def test_a_marked_height_reaches_the_field(self):
        dialog, _ = _dialog()
        dialog._on_level_drawn(233)
        assert dialog._y_spin.value() == 233

    def test_it_saves_one_row_under_its_key_and_persists(self):
        dialog, config = _dialog(**{_KEY: 410})
        dialog._y_spin.setValue(395)
        dialog._on_save_clicked()
        assert config.written == {_KEY: 395}
        assert config.saved


class TestPause:
    def test_a_paused_dialog_keeps_the_frame_it_had(self):
        dialog, _ = _dialog()
        dialog.update_frame(np.full((240, 320, 3), 10, np.uint8))
        dialog.findChild(PauseButton).click()
        dialog.update_frame(np.full((240, 320, 3), 200, np.uint8))
        assert dialog._last_frame[0, 0, 0] == 10


class TestInspector:
    """
    En pausa, el diálogo muestra el frame que trae el inspector y sus renglones para la
    altura marcada. Lo que se mide no es del diálogo: el inspector lo arma.
    """

    def _inspected(self, snapshot) -> LevelDialog:
        return LevelDialog(_MockConfig(), "camera_1", _KEY, inspector=lambda: snapshot)

    @staticmethod
    def _snapshot():
        return LevelSnapshot(np.full((40, 60, 3), 7, np.uint8),
                             lambda y_px: [f"y={y_px}"])

    def test_pausing_shows_the_inspected_frame_and_its_lines(self):
        dialog = self._inspected(self._snapshot())
        dialog.update_frame(np.zeros((40, 60, 3), np.uint8))
        dialog._pause_button.setChecked(True)
        assert np.all(dialog._last_frame == 7)
        assert dialog._inspection_label.text() == "y=0"

    def test_moving_the_line_rebuilds_the_lines(self):
        dialog = self._inspected(self._snapshot())
        dialog._pause_button.setChecked(True)
        dialog._y_spin.setValue(25)
        assert dialog._inspection_label.text() == "y=25"

    def test_without_a_snapshot_it_says_there_is_nothing_to_measure(self):
        dialog = self._inspected(None)
        live = np.zeros((40, 60, 3), np.uint8)
        dialog.update_frame(live)
        dialog._pause_button.setChecked(True)
        assert dialog._last_frame is live
        assert dialog._inspection_label.text() == tr("level_no_snapshot")

    def test_resuming_hides_the_lines(self):
        dialog = self._inspected(self._snapshot())
        dialog._pause_button.setChecked(True)
        dialog._pause_button.setChecked(False)
        assert dialog._inspection_label.isHidden()

    def test_without_an_inspector_pausing_shows_no_lines(self):
        dialog, _ = _dialog()
        dialog._pause_button.setChecked(True)
        assert dialog._inspection_label.isHidden()
