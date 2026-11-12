"""
Tests de la ventana principal: que las páginas se armen solas después de mostrarse, de a
una, y que abrir una ya armada no la vuelva a armar.

Construye la ventana entera, así que usa un `QApplication` sin pantalla —plataforma
`offscreen`— y un config de mentira que contesta los defaults.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent                                       # noqa: E402
from PySide6.QtWidgets import QApplication, QStyleFactory               # noqa: E402

from ui import navigation                                               # noqa: E402
from ui.main_window import MainWindow                                   # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


class _MockConfig:
    """ConfigManager mínimo: contesta el default de cada clave y no persiste nada."""

    revision = 0

    def __init__(self):
        self.written: dict = {}

    def get(self, key: str, default: object = None) -> object:
        return self.written.get(key, default)

    def set(self, key: str, value: object):
        self.written[key] = value

    def save(self) -> bool:
        return True


def _pages_with_tabs() -> set[str]:
    return {page.key for page in navigation.get_pages() if page.tabs}


@pytest.fixture
def window(qt_app):
    """La ventana instala su estilo en la aplicación: se devuelve el de antes al terminar."""
    original_style = qt_app.style().name()
    window = MainWindow(_MockConfig())
    yield window
    window.close()
    window.deleteLater()
    # Destruida de verdad antes de devolver el estilo: `deleteLater` sólo la encola, y si no
    # se iba a destruir en medio de otro test, con sus timers todavía armados.
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    qt_app.setStyle(QStyleFactory.create(original_style))


class TestPrewarm:
    def test_it_builds_one_page_per_step(self, window):
        before = set(window._pages)
        window._prewarm_next_page()
        assert len(set(window._pages) - before) == 1

    def test_in_the_end_every_page_with_tabs_is_built(self, window):
        for _ in range(len(_pages_with_tabs())):
            window._prewarm_next_page()
        assert _pages_with_tabs() <= set(window._pages)

    def test_opening_a_built_page_does_not_build_it_again(self, window):
        key = sorted(_pages_with_tabs())[0]
        window._navigate(key)
        page = window._pages[key]
        window._navigate(navigation.PAGE_HOME)
        window._navigate(key)
        assert window._pages[key] is page

    def test_it_starts_after_the_window_is_shown(self, window):
        assert not window._is_prewarm_scheduled
        window.show()
        assert window._is_prewarm_scheduled
