"""
Tests de la pestaña de óptica: la región donde se mide la nitidez de cada cámara.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton                 # noqa: E402

from ui.strings import tr                                               # noqa: E402
from ui.views.config.lens_health_tab import LensHealthTab               # noqa: E402

_PREFIX = "cameras.camera_1.lens_health.roi"
_ROI = {"enabled": True, "x_px": 10, "y_px": 20, "width_px": 30, "height_px": 40}


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


class _MockConfig:
    """ConfigManager mínimo con `get` punteado sobre un árbol y `set` registrado."""

    def __init__(self, roi: dict | None = None):
        lens_health = {"reference": {"variance": 0.0}}
        if roi is not None:
            lens_health["roi"] = roi
        self.tree = {"lens_health": {}, "cameras": {
            "camera_1": {"name": "Uno", "lens_health": lens_health},
            "camera_2": {"name": "Dos", "lens_health": {"reference": {"variance": 0.0}}}}}
        self.written: dict = {}

    def get(self, key: str, default: object = None) -> object:
        node = self.tree
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, key: str, value: object):
        self.written[key] = value


def _tab(roi: dict | None = None) -> tuple[LensHealthTab, _MockConfig]:
    config = _MockConfig(roi)
    return LensHealthTab(config), config


def _box(tab: LensHealthTab, camera_slot: str = "camera_1"):
    return tab._references[camera_slot]


class TestRegion:
    def test_it_loads_from_the_lens_section_of_the_camera(self):
        tab, _ = _tab(_ROI)
        box = _box(tab)
        assert box._roi_check.isChecked()
        assert [box._roi_spins[key].value() for key in ("x_px", "y_px", "width_px", "height_px")] \
            == [10, 20, 30, 40]

    def test_a_camera_without_a_region_starts_off_and_empty(self):
        tab, _ = _tab()
        box = _box(tab)
        assert not box._roi_check.isChecked()
        assert all(spin.value() == 0 for spin in box._roi_spins.values())

    def test_each_camera_has_its_own(self):
        tab, _ = _tab(_ROI)
        assert not _box(tab, "camera_2")._roi_check.isChecked()

    def test_save_writes_enabled_and_the_geometry_under_the_lens_section(self):
        tab, config = _tab(_ROI)
        _box(tab)._roi_spins["x_px"].setValue(77)
        tab.save()
        assert config.written[f"{_PREFIX}.enabled"] is True
        assert config.written[f"{_PREFIX}.x_px"] == 77
        assert config.written[f"{_PREFIX}.width_px"] == 30

    def test_save_writes_no_key_under_the_camera_roi_anymore(self):
        tab, config = _tab(_ROI)
        tab.save()
        assert not [key for key in config.written if key.startswith("cameras.camera_1.roi")]

    def test_the_fields_depend_on_the_checkbox(self):
        tab, _ = _tab()
        box = _box(tab)
        assert not box._roi_spins["x_px"].isEnabled()
        box._roi_check.setChecked(True)
        assert box._roi_spins["x_px"].isEnabled()

    def test_the_button_asks_for_the_dialog_with_the_lens_key(self):
        tab, _ = _tab(_ROI)
        requests = []
        tab.open_roi_requested.connect(lambda *args: requests.append(args))
        buttons = [button for button in _box(tab).findChildren(QPushButton)
                   if button.text() == tr("cam_roi_tool")]
        buttons[0].click()
        assert requests == [("camera_1", _PREFIX, "lens_roi_title")]

    def test_reload_brings_the_geometry_the_dialog_wrote(self):
        tab, config = _tab(_ROI)
        config.tree["cameras"]["camera_1"]["lens_health"]["roi"] = {**_ROI, "x_px": 7}
        tab.reload_roi("camera_1")
        assert _box(tab)._roi_spins["x_px"].value() == 7

    def test_reload_does_not_touch_the_checkbox(self):
        """El diálogo sólo escribe geometría: lo que el operador tildó y no guardó se queda."""
        tab, config = _tab(_ROI)
        _box(tab)._roi_check.setChecked(False)
        config.tree["cameras"]["camera_1"]["lens_health"]["roi"] = {**_ROI, "x_px": 7}
        tab.reload_roi("camera_1")
        assert not _box(tab)._roi_check.isChecked()

    def test_reload_of_an_unknown_camera_does_nothing(self):
        tab, _ = _tab(_ROI)
        tab.reload_roi("camera_9")

    def test_the_reference_is_still_the_only_thing_save_writes_about_it(self):
        tab, config = _tab(_ROI)
        tab.save()
        reference_keys = [key for key in config.written if ".reference." in key]
        assert reference_keys == ["cameras.camera_1.lens_health.reference.variance",
                                  "cameras.camera_2.lens_health.reference.variance"]
