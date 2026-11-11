"""
Tests de la paleta de búsqueda: la regla con la que filtra y ordena, y cómo se mueve y elige
con el teclado.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt                                           # noqa: E402
from PySide6.QtTest import QTest                                        # noqa: E402
from PySide6.QtWidgets import QApplication                              # noqa: E402

from ui.widgets.command_palette import (                                # noqa: E402
    CommandPalette, PaletteEntry, filter_entries, group_entries,
)


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


_PAGES = [PaletteEntry("Ir a", "Cámaras"), PaletteEntry("Ir a", "Modbus")]
_SETTINGS = [
    PaletteEntry("Ajustes", "Límite de FPS", "Cámaras › Entrada › Adquisición"),
    PaletteEntry("Ajustes", "Exposición", "Cámaras › Entrada › Adquisición",
                 keywords="En microsegundos"),
    PaletteEntry("Ajustes", "Puerto serie", "Modbus › Configuración"),
    PaletteEntry("Ajustes", "Puerto", "Modbus › Configuración"),
]


def _titles(entries) -> list[str]:
    return [entry.title for entry in entries]


class TestFilter:
    def test_case_and_accents_are_ignored(self):
        assert _titles(filter_entries(_SETTINGS, "EXPOSICION")) == ["Exposición"]

    def test_every_word_has_to_appear(self):
        assert _titles(filter_entries(_SETTINGS, "puerto modbus")) == ["Puerto serie", "Puerto"]
        assert filter_entries(_SETTINGS, "puerto camaras") == []

    def test_the_path_and_the_hint_are_searched_too(self):
        assert _titles(filter_entries(_SETTINGS, "microsegundos")) == ["Exposición"]
        assert "Límite de FPS" in _titles(filter_entries(_SETTINGS, "entrada"))

    def test_a_title_match_goes_before_a_path_match(self):
        entries = _PAGES + _SETTINGS
        assert _titles(filter_entries(entries, "camaras"))[0] == "Cámaras"

    def test_a_title_that_starts_with_the_query_goes_first(self):
        entries = [PaletteEntry("Ajustes", "Frames por segundo"), PaletteEntry("Ajustes", "FPS")]
        assert _titles(filter_entries(entries, "fps"))[0] == "FPS"

    def test_an_empty_query_finds_nothing(self):
        assert filter_entries(_SETTINGS, "   ") == []

    def test_groups_keep_the_order_of_their_best_entry(self):
        entries = filter_entries(_PAGES + _SETTINGS, "modbus")
        assert [group for group, _ in group_entries(entries)] == ["Ir a", "Ajustes"]


class TestPalette:
    def _palette(self) -> CommandPalette:
        palette = CommandPalette()
        palette.set_entries(_PAGES + _SETTINGS, _PAGES)
        return palette

    def test_with_an_empty_field_it_shows_the_idle_entries(self):
        assert self._palette().shown_entries == _PAGES

    def test_typing_filters_the_list(self):
        palette = self._palette()
        palette.set_query("puerto")
        assert _titles(palette.shown_entries) == ["Puerto serie", "Puerto"]

    def test_enter_chooses_the_first_entry_and_not_the_group_label(self):
        palette = self._palette()
        chosen = []
        palette.entry_chosen.connect(chosen.append)
        palette.set_query("puerto")
        QTest.keyClick(palette._input, Qt.Key_Return)
        assert _titles(chosen) == ["Puerto serie"]

    def test_down_skips_to_the_next_entry(self):
        palette = self._palette()
        chosen = []
        palette.entry_chosen.connect(chosen.append)
        palette.set_query("modbus")
        QTest.keyClick(palette._input, Qt.Key_Down)
        palette.choose_current()
        # «Modbus» es la primera; abajo, saltando el rótulo «Ajustes», la primera de ese grupo.
        assert _titles(chosen) == ["Puerto serie"]

    def test_without_matches_it_says_so(self):
        palette = self._palette()
        palette.set_query("zzz")
        assert palette.shown_entries == []
        assert not palette._empty.isHidden()
