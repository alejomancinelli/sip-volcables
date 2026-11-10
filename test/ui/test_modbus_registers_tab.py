"""
Tests de la pestaña de registros: el valor de una palabra de estado en hexadecimal, sus bits
al elegirla y el CSV que copia.

Usa el mapa del repo, como la pantalla. Construye widgets, así que usa un `QApplication` sin
pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel                      # noqa: E402

from system.formats import com_status                                   # noqa: E402
from system.modbus.export_map import build_csv                          # noqa: E402
from system.modbus.registers import SCHEMA                              # noqa: E402
from ui.views.diagnostics.modbus_tab import ModbusTab                   # noqa: E402

_WORD_ADDRESS = SCHEMA.addr("com_status_bitfield")
_PLAIN_ADDRESS = SCHEMA.addr("heartbeat")


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


def _tab() -> ModbusTab:
    return ModbusTab(config_manager=None)


def _value_text(tab: ModbusTab, address: int) -> str:
    return tab._table.item(tab._row_by_address[address], 2).text()


def _bit_texts(tab: ModbusTab) -> list[str]:
    return [label.text() for label in tab._bits_grid_host.findChildren(QLabel)
            if label.objectName() == "bitDescription"]


class TestValues:
    def test_a_status_word_is_shown_in_hex(self):
        tab = _tab()
        tab.update_values({_WORD_ADDRESS: 0x31})
        assert _value_text(tab, _WORD_ADDRESS) == "0x0031"

    def test_a_plain_register_is_shown_in_decimal(self):
        tab = _tab()
        tab.update_values({_PLAIN_ADDRESS: 1234})
        assert _value_text(tab, _PLAIN_ADDRESS) == "1234"

    def test_only_a_status_word_can_be_selected(self):
        tab = _tab()
        tab._table.selectRow(tab._row_by_address[_PLAIN_ADDRESS])
        assert tab._selected_address is None


class TestBits:
    def test_selecting_a_word_lists_its_bits(self):
        tab = _tab()
        tab.update_values({_WORD_ADDRESS: 1 << com_status.BIT_MODBUS_TCP})
        tab._table.selectRow(tab._row_by_address[_WORD_ADDRESS])
        assert _bit_texts(tab) == [com_status.DESCRIPTIONS[bit]
                                   for bit in sorted(com_status.DESCRIPTIONS)]

    def test_the_panel_follows_a_new_value(self):
        tab = _tab()
        tab.update_values({_WORD_ADDRESS: 0})
        tab._table.selectRow(tab._row_by_address[_WORD_ADDRESS])
        tab.update_values({_WORD_ADDRESS: 0x0010})
        assert tab._bits_value.text().startswith("0x0010")

    def test_without_a_selection_it_asks_for_one(self):
        tab = _tab()
        assert tab._selected_address is None
        assert tab._bits_grid_host.isHidden()


class TestCopyCsv:
    def test_it_copies_the_integrator_csv(self):
        tab = _tab()
        tab._copy_button.click()
        assert QApplication.clipboard().text() == build_csv()
