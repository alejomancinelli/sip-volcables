"""
Tests de la pestaña de cámaras: una sub-pestaña por cámara con su conexión, su adquisición y su
óptica, y la detección de lente sucio debajo.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton                 # noqa: E402

from ui.navigation import get_page                                      # noqa: E402
from ui.strings import tr                                               # noqa: E402
from ui.views.config.cameras_tab import CamerasTab                      # noqa: E402

_PREFIX = "cameras.camera_1.lens_health.roi"
_ROI = {"enabled": True, "x_px": 10, "y_px": 20, "width_px": 30, "height_px": 40}


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


class _MockConfig:
    """ConfigManager mínimo con `get` punteado sobre un árbol y `set` registrado."""

    def __init__(self, roi: dict | None = None, **camera_1):
        lens_health = {"reference": {"variance": 0.0}}
        if roi is not None:
            lens_health["roi"] = roi
        self.tree = {"lens_health": {}, "cameras": {
            "camera_1": {"name": "Uno", "brand": "rtsp", "model": "generic",
                         "lens_health": lens_health, **camera_1},
            "camera_2": {"name": "Dos", "brand": "basler",
                         "lens_health": {"reference": {"variance": 0.0}}}}}
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


def _tab(roi: dict | None = None, **camera_1) -> tuple[CamerasTab, _MockConfig]:
    config = _MockConfig(roi, **camera_1)
    return CamerasTab(config), config


def _form(tab: CamerasTab, camera_slot: str = "camera_1"):
    return tab._forms[camera_slot]


class TestTabs:
    def test_there_is_one_tab_per_camera_and_optics_goes_last(self):
        tab, _ = _tab()
        assert [tab._tabs.tabText(index) for index in range(tab._tabs.count())] \
            == ["Uno", "Dos", tr("cam_optics_tab")]

    def test_the_optics_tab_is_marked_for_its_tint(self):
        tab, _ = _tab()
        assert tab._tabs.property("hasGeneralTab") is True

    def test_the_general_settings_are_not_in_a_camera_tab(self):
        tab, _ = _tab()
        last_page = tab._tabs.widget(tab._tabs.count() - 1)
        assert last_page.isAncestorOf(tab._interval_spin)
        assert not _form(tab).isAncestorOf(tab._interval_spin)

    def test_each_camera_tab_has_its_own_region_and_reference(self):
        tab, _ = _tab()
        assert _form(tab).isAncestorOf(_form(tab)._roi_check)
        assert _form(tab, "camera_2").isAncestorOf(_form(tab, "camera_2")._variance_spin)

    def test_the_cameras_page_has_no_separate_optics_tab(self):
        assert get_page("cameras").tabs == (CamerasTab,)


class TestEnabled:
    def test_a_disabled_camera_cannot_be_edited(self):
        tab, _ = _tab(enabled=False)
        form = _form(tab)
        assert not form._name_edit.isEnabled()
        assert not form._fps_spin.isEnabled()
        assert not form._variance_spin.isEnabled()
        assert not form._roi_check.isEnabled()

    def test_enabling_it_turns_the_fields_back_on(self):
        tab, _ = _tab(enabled=False)
        form = _form(tab)
        form._enabled_check.setChecked(True)
        assert form._name_edit.isEnabled()
        assert form._roi_check.isEnabled()

    def test_enabling_it_leaves_an_unchecked_region_off(self):
        tab, _ = _tab(enabled=False)
        form = _form(tab)
        form._enabled_check.setChecked(True)
        assert not form._roi_spins["x_px"].isEnabled()

    def test_a_checked_region_stays_off_in_a_disabled_camera(self):
        tab, _ = _tab(_ROI, enabled=False)
        assert not _form(tab)._roi_spins["x_px"].isEnabled()

    def test_the_camera_switch_does_not_touch_the_other_camera(self):
        tab, _ = _tab(enabled=False)
        assert _form(tab, "camera_2")._name_edit.isEnabled()


class TestSensorSettings:
    def test_an_rtsp_camera_hides_exposure_and_gain(self):
        tab, _ = _tab()
        form = _form(tab)
        assert not form._exposure_spin.isVisibleTo(form)
        assert not form._gain_spin.isVisibleTo(form)
        assert form._fps_spin.isVisibleTo(form)

    def test_a_gige_camera_shows_them(self):
        tab, _ = _tab()
        form = _form(tab, "camera_2")
        assert form._exposure_spin.isVisibleTo(form)

    def test_an_rtsp_camera_does_not_write_them(self):
        tab, config = _tab()
        tab.save()
        assert "cameras.camera_1.acquisition.exposure_time_us" not in config.written
        assert "cameras.camera_1.acquisition.fps_limit" in config.written

    def test_a_gige_camera_writes_them(self):
        tab, config = _tab()
        tab.save()
        assert "cameras.camera_2.acquisition.exposure_time_us" in config.written
        assert "cameras.camera_2.acquisition.gain" in config.written

    def test_choosing_a_gige_brand_shows_them(self):
        tab, _ = _tab()
        form = _form(tab)
        index = form._brand_combo.findData("basler")
        form._brand_combo.setCurrentIndex(index)
        assert form._exposure_spin.isVisibleTo(form)

    def test_a_mock_camera_hides_the_address(self):
        tab, _ = _tab(brand="mock")
        form = _form(tab)
        assert not form._address_edit.isVisibleTo(form)



class TestDecode:
    """El backend se edita; el tamaño se muestra y nunca se escribe."""

    def test_an_rtsp_camera_shows_the_backend_and_the_size(self):
        tab, _ = _tab()
        form = _form(tab)
        assert form._decode_combo.isVisibleTo(form)
        assert form._decode_size_field.isVisibleTo(form)

    def test_a_gige_camera_hides_them(self):
        tab, _ = _tab()
        form = _form(tab, "camera_2")
        assert not form._decode_combo.isVisibleTo(form)
        assert not form._decode_size_field.isVisibleTo(form)

    def test_the_backend_round_trips(self):
        tab, config = _tab(decode={"backend": "nvdec"})
        assert _form(tab)._decode_combo.currentData() == "nvdec"
        tab.save()
        assert config.written["cameras.camera_1.decode.backend"] == "nvdec"

    def test_without_a_decode_section_it_is_the_decoder_default(self):
        tab, config = _tab()
        tab.save()
        assert config.written["cameras.camera_1.decode.backend"] == "cpu"

    def test_an_unknown_backend_goes_back_as_it_was(self):
        tab, config = _tab(decode={"backend": "vaapi"})
        tab.save()
        assert config.written["cameras.camera_1.decode.backend"] == "vaapi"

    def test_the_size_is_shown_and_never_written(self):
        tab, config = _tab(decode={"backend": "auto", "width_px": 640, "height_px": 384})
        assert _form(tab)._decode_size_field.text() == "640 × 384"
        assert _form(tab)._decode_size_field.isReadOnly()
        tab.save()
        assert not any(key.startswith("cameras.camera_1.decode.") and
                       key != "cameras.camera_1.decode.backend" for key in config.written)

    def test_a_size_in_zero_is_the_videos(self):
        tab, _ = _tab(decode={"width_px": 0, "height_px": 0})
        assert _form(tab)._decode_size_field.text() == tr("cam_decode_native")

    def test_a_gige_camera_does_not_write_the_backend(self):
        tab, config = _tab()
        tab.save()
        assert "cameras.camera_2.decode.backend" not in config.written

    def test_a_disabled_camera_cannot_change_it(self):
        tab, _ = _tab(enabled=False)
        assert not _form(tab)._decode_combo.isEnabled()

class TestOptionalSections:
    def test_without_image_adjust_in_the_config_there_is_no_field(self):
        tab, _ = _tab()
        form = _form(tab)
        assert not hasattr(form, "_brightness_spin")

    def test_without_image_adjust_save_does_not_create_it(self):
        tab, config = _tab()
        tab.save()
        assert not [key for key in config.written if ".image_adjust." in key]

    def test_with_image_adjust_it_loads_and_saves(self):
        tab, config = _tab(image_adjust={"brightness_factor": 2.5, "clahe_clip": 0.0})
        assert _form(tab)._brightness_spin.value() == pytest.approx(2.5)
        tab.save()
        assert config.written["cameras.camera_1.image_adjust.brightness_factor"] \
            == pytest.approx(2.5)

    def test_without_calibration_in_the_config_there_is_no_field(self):
        tab, _ = _tab()
        form = _form(tab)
        assert not hasattr(form, "_calibration_field")

    def test_with_calibration_it_shows_whether_it_is_set(self):
        tab, _ = _tab(calibration={"camera_matrix": [[1, 0, 0]], "dist_coeffs": []})
        assert _form(tab)._calibration_field.text() == tr("cam_calibration_set")

    def test_save_never_writes_the_calibration(self):
        tab, config = _tab(calibration={"camera_matrix": [], "dist_coeffs": []})
        tab.save()
        assert not [key for key in config.written if key.startswith("cameras.camera_1.calibration")]


class TestDetection:
    def test_its_fields_depend_on_its_checkbox(self):
        tab, config = _tab()
        config.tree["lens_health"] = {"enabled": False}
        tab.load()
        assert not tab._interval_spin.isEnabled()
        tab._lens_enabled_check.setChecked(True)
        assert tab._interval_spin.isEnabled()

    def test_a_window_in_loose_seconds_shows_the_nearest_hour(self):
        tab, config = _tab()
        config.tree["lens_health"] = {"window_s": 9900}
        tab.load()
        assert tab._window_spin.value() == 3

    def test_the_window_is_shown_in_hours_and_saved_in_seconds(self):
        tab, config = _tab()
        config.tree["lens_health"] = {"window_s": 7200}
        tab.load()
        assert tab._window_spin.value() == pytest.approx(2.0)
        tab.save()
        assert config.written["lens_health.window_s"] == 7200


class TestRegion:
    def test_it_loads_from_the_lens_section_of_the_camera(self):
        tab, _ = _tab(_ROI)
        form = _form(tab)
        assert form._roi_check.isChecked()
        assert [form._roi_spins[key].value()
                for key in ("x_px", "y_px", "width_px", "height_px")] == [10, 20, 30, 40]

    def test_a_camera_without_a_region_starts_off_and_empty(self):
        tab, _ = _tab()
        form = _form(tab)
        assert not form._roi_check.isChecked()
        assert all(spin.value() == 0 for spin in form._roi_spins.values())

    def test_each_camera_has_its_own(self):
        tab, _ = _tab(_ROI)
        assert not _form(tab, "camera_2")._roi_check.isChecked()

    def test_save_writes_enabled_and_the_geometry_under_the_lens_section(self):
        tab, config = _tab(_ROI)
        _form(tab)._roi_spins["x_px"].setValue(77)
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
        form = _form(tab)
        assert not form._roi_spins["x_px"].isEnabled()
        form._roi_check.setChecked(True)
        assert form._roi_spins["x_px"].isEnabled()

    def test_the_button_asks_for_the_dialog_with_the_lens_key(self):
        tab, _ = _tab(_ROI)
        requests = []
        tab.open_roi_requested.connect(lambda *args: requests.append(args))
        buttons = [button for button in _form(tab).findChildren(QPushButton)
                   if button.text() == tr("lens_draw_region")]
        buttons[0].click()
        assert requests == [("camera_1", _PREFIX, "lens_roi_title")]

    def test_reload_brings_the_geometry_the_dialog_wrote(self):
        tab, config = _tab(_ROI)
        config.tree["cameras"]["camera_1"]["lens_health"]["roi"] = {**_ROI, "x_px": 7}
        tab.reload_roi("camera_1")
        assert _form(tab)._roi_spins["x_px"].value() == 7

    def test_reload_does_not_touch_the_checkbox(self):
        """El diálogo sólo escribe geometría: lo que el operador tildó y no guardó se queda."""
        tab, config = _tab(_ROI)
        _form(tab)._roi_check.setChecked(False)
        config.tree["cameras"]["camera_1"]["lens_health"]["roi"] = {**_ROI, "x_px": 7}
        tab.reload_roi("camera_1")
        assert not _form(tab)._roi_check.isChecked()

    def test_reload_of_an_unknown_camera_does_nothing(self):
        tab, _ = _tab(_ROI)
        tab.reload_roi("camera_9")

    def test_the_reference_is_still_the_only_thing_save_writes_about_it(self):
        tab, config = _tab(_ROI)
        tab.save()
        reference_keys = [key for key in config.written if ".reference." in key]
        assert reference_keys == ["cameras.camera_1.lens_health.reference.variance",
                                  "cameras.camera_2.lens_health.reference.variance"]

    def test_the_calibrate_button_asks_for_that_camera(self):
        tab, _ = _tab()
        requests = []
        tab.calibrate_requested.connect(requests.append)
        _form(tab, "camera_2")._calibrate_button.click()
        assert requests == ["camera_2"]

    def test_refresh_reference_shows_what_the_calibration_wrote(self):
        tab, config = _tab()
        config.tree["cameras"]["camera_1"]["lens_health"]["reference"] = {
            "variance": 123.0, "calibrated_at": "2026-11-06", "sample_count": 30}
        tab.refresh_reference("camera_1")
        assert _form(tab)._variance_spin.value() == pytest.approx(123.0)
        assert "2026-11-06" in _form(tab)._state_field.text()


class TestCalibrating:
    def test_pressing_calibrate_says_it_is_calibrating(self):
        tab, config = _tab()
        config.tree["lens_health"] = {"calibration_samples": 30, "interval_s": 10}
        _form(tab)._calibrate_button.click()
        assert _form(tab)._state_field.text() == tr("lens_calibrating").format(
            samples=30, interval=10, minutes=5)

    def test_discarding_the_screen_does_not_hide_it(self):
        """Descartar no frena la calibración: el estado no puede volver a «Sin calibrar»."""
        tab, _ = _tab()
        _form(tab)._calibrate_button.click()
        tab.load()
        assert _form(tab)._state_field.text().startswith(tr("lens_calibrating")[:10])

    def test_the_finished_calibration_shows_the_new_reference(self):
        tab, config = _tab()
        _form(tab)._calibrate_button.click()
        config.tree["cameras"]["camera_1"]["lens_health"]["reference"] = {
            "variance": 400.0, "calibrated_at": "2026-11-06T10:00:00", "sample_count": 30}
        tab.refresh_reference("camera_1")
        assert "2026-11-06T10:00:00" in _form(tab)._state_field.text()

    def test_only_the_pressed_camera_is_calibrating(self):
        tab, _ = _tab()
        _form(tab)._calibrate_button.click()
        assert _form(tab, "camera_2")._state_field.text() == tr("lens_uncalibrated")
