"""
Tests del diálogo de nivel: qué guarda, dónde, y que lo marcado con el mouse llegue al campo.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                              # noqa: E402

from ui.dialogs.level_dialog import LevelDialog                         # noqa: E402

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
