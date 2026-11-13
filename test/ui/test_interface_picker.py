"""
Tests de los selectores de interfaz de red: qué ofrecen, y que un valor del config que el
equipo no tiene vuelva al archivo tal como estaba.

Las interfaces se pasan armadas, sin psutil. Construye widgets, así que usa un
`QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                              # noqa: E402

from tools.net_interfaces import NetInterface                           # noqa: E402
from ui.widgets.form import set_combo_data                              # noqa: E402
from ui.widgets.interface_picker import (                               # noqa: E402
    ALL_ADDRESSES, LOOPBACK_ADDRESS, InterfaceChecklist, build_address_combo,
)

_INTERFACES = [
    NetInterface("eth0", "192.168.0.10", True),
    NetInterface("eth1", "10.0.0.5", False),
    NetInterface("wlan0", "", False),
]


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


def _values(combo) -> list:
    return [combo.itemData(index) for index in range(combo.count())]


class TestAddressCombo:
    def test_it_offers_every_interface_with_an_address(self):
        combo = build_address_combo(ALL_ADDRESSES, _INTERFACES)
        assert _values(combo) == [ALL_ADDRESSES, "192.168.0.10", "10.0.0.5", LOOPBACK_ADDRESS]

    def test_the_current_address_is_selected(self):
        combo = build_address_combo("10.0.0.5", _INTERFACES)
        assert combo.currentData() == "10.0.0.5"

    def test_an_address_this_device_does_not_have_is_kept(self):
        combo = build_address_combo(ALL_ADDRESSES, _INTERFACES)
        set_combo_data(combo, "172.16.0.9")
        assert combo.currentData() == "172.16.0.9"


class TestChecklist:
    def test_an_empty_list_checks_every_interface_and_goes_back_empty(self):
        checklist = InterfaceChecklist(_INTERFACES)
        checklist.set_selected([])
        assert all(check.isChecked() for check in checklist._checks.values())
        assert checklist.get_selected() == []

    def test_an_untouched_list_goes_back_as_it_was(self):
        """Las dos marcadas no se guardan como «todas» si nadie tocó nada."""
        checklist = InterfaceChecklist(_INTERFACES)
        checklist.set_selected(["eth0", "eth1", "wlan0"])
        assert checklist.get_selected() == ["eth0", "eth1", "wlan0"]

    def test_unchecking_one_saves_the_others(self):
        checklist = InterfaceChecklist(_INTERFACES)
        checklist.set_selected([])
        checklist._checks["eth1"].setChecked(False)
        assert checklist.get_selected() == ["eth0", "wlan0"]

    def test_checking_them_all_saves_empty(self):
        checklist = InterfaceChecklist(_INTERFACES)
        checklist.set_selected(["eth0"])
        for check in checklist._checks.values():
            check.setChecked(True)
        assert checklist.get_selected() == []

    def test_a_name_this_device_does_not_have_is_kept_and_reported(self):
        checklist = InterfaceChecklist(_INTERFACES)
        checklist.set_selected(["eth0", "enp3s0"])
        checklist._checks["eth1"].setChecked(True)
        assert checklist.get_selected() == ["eth0", "eth1", "enp3s0"]
        assert "enp3s0" in checklist._note.text()
        assert not checklist._note.isHidden()

    def test_the_last_checked_interface_cannot_be_unchecked(self):
        checklist = InterfaceChecklist(_INTERFACES)
        checklist.set_selected(["eth0"])
        checklist._checks["eth0"].setChecked(False)
        assert checklist._checks["eth0"].isChecked()

    def test_without_interfaces_the_config_goes_back_as_it_was(self):
        checklist = InterfaceChecklist([])
        checklist.set_selected(["eth0"])
        assert checklist.get_selected() == ["eth0"]
        assert not checklist._note.isHidden()
