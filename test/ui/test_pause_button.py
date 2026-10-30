"""
Tests del botón de pausa de los diálogos de dibujo: su estado y el texto que lo dice.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                              # noqa: E402

from ui.strings import tr                                               # noqa: E402
from ui.widgets.pause_button import PauseButton                         # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


class TestPauseButton:
    def test_it_starts_playing_and_offers_to_pause(self):
        button = PauseButton()
        assert not button.is_paused and button.text() == tr("dialog_pause")

    def test_a_click_pauses_and_offers_to_resume(self):
        button = PauseButton()
        button.click()
        assert button.is_paused and button.text() == tr("dialog_resume")

    def test_a_second_click_resumes(self):
        button = PauseButton()
        button.click()
        button.click()
        assert not button.is_paused and button.text() == tr("dialog_pause")
