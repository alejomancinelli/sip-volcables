"""
Tests de la barra lateral: la navegación que publica, la vista activa y el riel contraído.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton         # noqa: E402

from ui.strings import tr                                               # noqa: E402
from ui.widgets.sidebar import Sidebar                                  # noqa: E402

_GROUPS = (
    ("Operación", (("monitor", "Monitor", "layout-dashboard"),)),
    ("Equipo", (("system", "Sistema", "cpu"),
                ("logs", "Logs", "list-details"))),
)


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


def _build(subtitle: str = "Cliente") -> Sidebar:
    return Sidebar(_GROUPS, title="App", subtitle=subtitle, footer="IEA")


def _settle(sidebar: Sidebar):
    """
    Deja la barra con su geometría final. Esconder y mostrar encadena pasadas de layout, y
    un solo `processEvents()` puede leerla a mitad de camino.
    """
    for _ in range(3):
        sidebar.layout().activate()
        QApplication.processEvents()


def _nav_buttons(sidebar: Sidebar) -> list[QPushButton]:
    return [button for button in sidebar.findChildren(QPushButton)
            if button.objectName() == "navButton"]


class TestNavigation:
    def test_a_click_publishes_the_key_of_its_item(self):
        sidebar = _build()
        received = []
        sidebar.navigation_requested.connect(received.append)
        _nav_buttons(sidebar)[2].click()
        assert received == ["logs"]

    def test_set_current_checks_only_that_item(self):
        sidebar = _build()
        sidebar.set_current("system")
        assert [button.isChecked() for button in _nav_buttons(sidebar)] == [False, True, False]

    def test_an_unknown_key_leaves_the_current_item_alone(self):
        sidebar = _build()
        sidebar.set_current("system")
        sidebar.set_current("no_such_page")
        assert _nav_buttons(sidebar)[1].isChecked()

    def test_the_label_is_the_text_shown_in_the_bar(self):
        assert _build().get_label("logs") == "Logs"

    def test_an_item_can_be_hidden_and_shown_again(self):
        """Para una página que existe según el equipo, como la de entradas y salidas."""
        sidebar = _build()
        sidebar.show()
        sidebar.set_item_visible("logs", False)
        assert not _nav_buttons(sidebar)[2].isVisible()
        sidebar.set_item_visible("logs", True)
        assert _nav_buttons(sidebar)[2].isVisible()


class TestCollapse:
    """Contraída, el texto de cada ítem pasa al tooltip; expandida, vuelve."""

    def test_it_starts_expanded_with_the_texts_visible(self):
        sidebar = _build()
        assert not sidebar.is_collapsed
        assert [button.text() for button in _nav_buttons(sidebar)] == [
            "Monitor", "Sistema", "Logs"]

    def test_collapsing_moves_each_text_to_its_tooltip(self):
        sidebar = _build()
        sidebar.set_collapsed(True)
        buttons = _nav_buttons(sidebar)
        assert all(button.text() == "" for button in buttons)
        assert [button.toolTip() for button in buttons] == [
            "Monitor", "Sistema", "Logs"]

    def test_collapsing_narrows_the_bar(self):
        sidebar = _build()
        expanded_width = sidebar.width()
        sidebar.set_collapsed(True)
        assert sidebar.width() < expanded_width

    def test_expanding_again_restores_the_texts_and_drops_the_tooltips(self):
        sidebar = _build()
        sidebar.set_collapsed(True)
        sidebar.set_collapsed(False)
        buttons = _nav_buttons(sidebar)
        assert [button.text() for button in buttons] == [
            "Monitor", "Sistema", "Logs"]
        assert all(button.toolTip() == "" for button in buttons)

    def test_the_toggle_button_collapses_and_says_how_to_undo_it(self):
        sidebar = _build()
        toggle = sidebar.findChild(QPushButton, "navCollapseButton")
        toggle.click()
        assert sidebar.is_collapsed
        assert toggle.toolTip() == tr("nav_expand")

    def test_the_view_stays_selected_across_a_collapse(self):
        sidebar = _build()
        sidebar.set_current("logs")
        sidebar.set_collapsed(True)
        assert _nav_buttons(sidebar)[2].isChecked()

    def test_collapsing_keeps_every_icon_at_its_height(self):
        """La tarjeta y los rótulos se esconden pero dejan su lugar: el ojo no busca de nuevo."""
        sidebar = _build()
        sidebar.resize(248, 700)
        sidebar.show()
        _settle(sidebar)
        expanded = [button.geometry().center().y() for button in _nav_buttons(sidebar)]
        sidebar.set_collapsed(True)
        _settle(sidebar)
        collapsed = [button.geometry().center().y() for button in _nav_buttons(sidebar)]
        assert collapsed == expanded

    def test_expanding_again_keeps_them_there_too(self):
        sidebar = _build()
        sidebar.resize(248, 700)
        sidebar.show()
        _settle(sidebar)
        expanded = [button.geometry().center().y() for button in _nav_buttons(sidebar)]
        sidebar.set_collapsed(True)
        _settle(sidebar)
        sidebar.set_collapsed(False)
        _settle(sidebar)
        assert [button.geometry().center().y() for button in _nav_buttons(sidebar)] == expanded


class TestDeviceCard:
    def test_without_a_subtitle_the_card_has_only_the_name(self):
        """Una segunda línea vacía se leería como un dato que falta."""
        sidebar = _build(subtitle="")
        assert sidebar.findChild(QLabel, "sidebarDeviceDetail") is None
        assert sidebar.findChild(QLabel, "sidebarDeviceName").text() == "App"


class TestPainting:
    def test_it_paints_expanded_and_collapsed(self):
        """La textura y el filete se dibujan a mano: que no rompan al cambiar de ancho."""
        sidebar = _build()
        sidebar.resize(248, 600)
        sidebar.set_current("system")
        assert not sidebar.grab().isNull()
        sidebar.set_collapsed(True)
        assert not sidebar.grab().isNull()
