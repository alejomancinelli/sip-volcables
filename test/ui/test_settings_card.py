"""
Tests de la tarjeta de ajustes: cómo se reparten los campos en columnas y dónde van los
filetes entre secciones.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QGridLayout, QLabel, QLineEdit, QWidget  # noqa: E402

from ui.widgets.settings_card import (                                             # noqa: E402
    FieldGrid, SectionStack, SettingsCard, SettingsList,
)


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


def _cell_of(grid: FieldGrid, widget: QWidget) -> tuple[int, int, int, int]:
    """(fila, columna, filas, columnas) de la celda que contiene al widget."""
    layout = grid.findChild(QGridLayout)
    for index in range(layout.count()):
        item = layout.itemAt(index).widget()
        if item is widget or item.isAncestorOf(widget):
            return layout.getItemPosition(index)
    raise AssertionError("el widget no está en la grilla")


class TestFieldGrid:
    def test_fields_fill_a_row_before_going_down(self):
        grid = FieldGrid(columns=2)
        fields = [grid.add_field(f"campo {i}", QLineEdit()) for i in range(3)]
        assert [_cell_of(grid, field)[:2] for field in fields] == [(0, 0), (0, 1), (1, 0)]

    def test_each_field_carries_its_label_above(self):
        grid = FieldGrid(columns=2)
        field = grid.add_field("Rótulo", QLineEdit())
        labels = [label.text() for label in field.parentWidget().findChildren(QLabel)]
        assert labels == ["Rótulo"]

    def test_a_hint_goes_under_the_field(self):
        grid = FieldGrid(columns=2)
        field = grid.add_field("Rótulo", QLineEdit(), hint="En milisegundos")
        hint = field.parentWidget().findChild(QLabel, "settingsFieldHint")
        assert hint is not None and hint.text() == "En milisegundos"

    def test_a_field_that_does_not_fit_starts_a_new_row(self):
        grid = FieldGrid(columns=2)
        grid.add_field("uno", QLineEdit())
        wide = grid.add_field("ancho", QLineEdit(), span=2)
        assert _cell_of(grid, wide) == (1, 0, 1, 2)

    def test_the_fields_are_listed_in_the_order_they_were_added(self):
        grid = FieldGrid(columns=2)
        first = grid.add_field("Puerto", QLineEdit(), hint="502 es el estándar")
        second = grid.add_field("Host", QLineEdit())
        fields = grid.get_fields()
        assert [(field.label, field.hint, field.widget) for field in fields] == [
            ("Puerto", "502 es el estándar", first), ("Host", "", second)]
        assert fields[0].cell.isAncestorOf(first)

    def test_a_full_width_row_takes_a_line_of_its_own(self):
        grid = FieldGrid(columns=3)
        grid.add_field("uno", QLineEdit())
        row = grid.add_row(QWidget())
        after = grid.add_field("dos", QLineEdit())
        assert _cell_of(grid, row) == (1, 0, 1, 3)
        assert _cell_of(grid, after)[:2] == (2, 0)


class TestSettingsCard:
    def test_the_eyebrow_is_in_capitals(self):
        card = SettingsCard("Personas")
        assert card.findChild(QLabel, "settingsEyebrow").text() == "PERSONAS"

    def test_without_an_eyebrow_there_is_no_label(self):
        assert SettingsCard().findChild(QLabel, "settingsEyebrow") is None

    def test_a_divider_goes_only_between_sections(self):
        card = SettingsCard()
        for title in ("uno", "dos", "tres"):
            card.add_section(title)
        assert len(card.findChildren(QWidget, "settingsDivider")) == 2

    def test_a_section_shows_its_title_and_description(self):
        card = SettingsCard()
        section = card.add_section("Confirmación", "Cuándo se confirma.")
        assert section.findChild(QLabel, "settingsSectionTitle").text() == "Confirmación"
        assert section.findChild(QLabel, "settingsSectionDescription").text() == \
            "Cuándo se confirma."

    def test_a_full_width_row_goes_without_a_divider(self):
        card = SettingsCard("Pipelines")
        row = QWidget()
        assert card.add_row(row) is row
        assert card.findChildren(QWidget, "settingsDivider") == []


class TestSectionStack:
    def test_a_divider_goes_only_between_sections(self):
        stack = SectionStack()
        for title in ("uno", "dos", "tres"):
            stack.add_section(title)
        assert len(stack.findChildren(QWidget, "settingsDivider")) == 2

    def test_it_has_no_card_of_its_own(self):
        """El borde lo pone la tarjeta que la contiene: una tarjeta adentro de otra no va."""
        assert SectionStack().objectName() != "settingsCard"


class TestSettingsList:
    def test_a_divider_goes_only_between_rows(self):
        rows = SettingsList()
        for _ in range(3):
            rows.add_row(QWidget())
        assert len(rows.findChildren(QWidget, "settingsDivider")) == 2

    def test_add_row_returns_the_row(self):
        row = QWidget()
        assert SettingsList().add_row(row) is row
