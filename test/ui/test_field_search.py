"""
Tests de la búsqueda de ajustes en una pestaña: qué campos y secciones encuentra, con qué
camino, qué deja afuera, y que llegar a uno elija las sub-pestañas que lo contienen.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget  # noqa: E402

from ui.widgets import field_search                                     # noqa: E402
from ui.widgets.form import build_slot_tabs                             # noqa: E402
from ui.widgets.settings_card import SectionStack, SettingsCard         # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


def _build_tab() -> tuple[QWidget, dict]:
    """Una pestaña como las de cámaras: una sección general y una sub-pestaña por cámara."""
    root = QWidget()
    card = SettingsCard("Cámaras")
    QVBoxLayout(root).addWidget(card)
    general = card.add_section("Óptica", "Cuándo el vidrio está sucio")
    widgets = {"interval": general.add_field("Intervalo", QLineEdit(), hint="En segundos")}
    tabs = build_slot_tabs()
    card.add_row(tabs)
    for name in ("Entrada", "Salida"):
        stack = SectionStack()
        section = stack.add_section("Adquisición")
        widgets[name] = section.add_field("Límite de FPS", QLineEdit())
        widgets[f"{name}_exposure"] = section.add_field("Exposición", QLineEdit())
        tabs.addTab(stack, name)
    widgets["tabs"] = tabs
    widgets["section"] = general
    return root, widgets


def _find(refs, title: str, path: tuple) -> field_search.SettingRef:
    return next(ref for ref in refs if ref.title == title and ref.path == path)


class TestCollect:
    def test_a_field_carries_its_section_and_hint(self):
        root, widgets = _build_tab()
        ref = _find(field_search.collect_settings(root), "Intervalo", ("Óptica",))
        assert ref.hint == "En segundos"
        assert ref.widget is widgets["interval"]
        assert ref.hit == field_search.HIT_FIELD

    def test_a_section_is_found_by_its_title(self):
        root, widgets = _build_tab()
        ref = _find(field_search.collect_settings(root), "Óptica", ())
        assert ref.widget is widgets["section"]
        assert ref.hit == field_search.HIT_SECTION

    def test_a_field_in_a_sub_tab_carries_the_sub_tab_name(self):
        """La sub-pestaña que no está a la vista también cuenta: se elige al llegar."""
        root, widgets = _build_tab()
        ref = _find(field_search.collect_settings(root), "Límite de FPS",
                    ("Salida", "Adquisición"))
        assert ref.widget is widgets["Salida"]

    def test_a_field_its_tab_hides_is_left_out(self):
        """Un campo que no aplica —otro driver— no se ofrece: llegar a él no mostraría nada."""
        root, widgets = _build_tab()
        widgets["Entrada_exposure"].parentWidget().hide()
        titles = [(ref.title, ref.path) for ref in field_search.collect_settings(root)]
        assert ("Exposición", ("Entrada", "Adquisición")) not in titles
        assert ("Exposición", ("Salida", "Adquisición")) in titles

    def test_a_hidden_sub_tab_is_left_out(self):
        root, widgets = _build_tab()
        widgets["tabs"].setTabVisible(1, False)
        paths = {ref.path[0] for ref in field_search.collect_settings(root) if ref.path}
        assert "Salida" not in paths


class TestReveal:
    def test_reaching_a_field_selects_its_sub_tab(self):
        root, widgets = _build_tab()
        field_search.reveal(widgets["Salida"])
        assert widgets["tabs"].currentIndex() == 1

    def test_reaching_a_field_marks_its_cell(self, qt_app):
        root, widgets = _build_tab()
        ref = _find(field_search.collect_settings(root), "Intervalo", ("Óptica",))
        field_search.reveal(ref.widget, highlight=ref.highlight, hit=ref.hit)
        qt_app.processEvents()
        assert ref.highlight.property("searchHit") == field_search.HIT_FIELD
