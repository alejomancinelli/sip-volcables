"""
Tests de la pestaña de inferencia: qué secciones edita y que `load()`/`save()` no deformen
lo que hay en el config.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt                                           # noqa: E402
from PySide6.QtWidgets import (                                         # noqa: E402
    QApplication, QCheckBox, QLabel, QPushButton, QTabWidget,
)

from system.inference import overlay                                    # noqa: E402
from system.inference.pipeline_factory import (                         # noqa: E402
    DEFAULT_PIPELINE, REGISTERED_PIPELINES,
)
from ui.strings import tr                                               # noqa: E402
from ui.views.config import inference_tab                               # noqa: E402
from ui.views.config.inference_tab import InferenceTab, _PipelineSwatch  # noqa: E402
from ui.widgets.form import build_slot_tabs                             # noqa: E402

_MODEL = "segmenter"
_PIPELINE = "pipeline_1"


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


class _MockConfig:
    """ConfigManager mínimo con `get` punteado y `set`, sin archivo ni Lock."""

    def __init__(self, **overrides):
        self._values = {
            "inference.models": {_MODEL: {}},
            f"inference.models.{_MODEL}.type": "mock",
            "inference.pipelines": {_PIPELINE: {}},
            f"inference.pipelines.{_PIPELINE}.cameras": ["camera_1"],
            f"inference.pipelines.{_PIPELINE}.frames_per_cycle": 3,
            "inference.overlay.font_scale": 1.6,
        }
        self._values.update(overrides)
        self.written: dict = {}

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)

    def set(self, key: str, value: object):
        self.written[key] = value


def _tab(**overrides) -> tuple[InferenceTab, _MockConfig]:
    config = _MockConfig(**overrides)
    return InferenceTab(config), config


class TestOverlay:
    def test_save_does_not_touch_the_overlay(self):
        """El anotado se edita sólo en el config.yaml: la pestaña no escribe ninguna clave."""
        tab, config = _tab()
        tab.save()
        assert not [key for key in config.written if key.startswith("inference.overlay")]


class TestClassNames:
    def test_save_does_not_write_them(self):
        """Se escriben en el config.yaml: un orden cambiado invierte los porcentajes."""
        tab, config = _tab(**{f"inference.models.{_MODEL}.class_names": ["ok", "nok"]})
        tab.save()
        assert f"inference.models.{_MODEL}.class_names" not in config.written

    def test_save_does_not_add_them_when_the_config_leaves_them_out(self):
        tab, config = _tab()
        tab.save()
        assert not [key for key in config.written if key.endswith(".class_names")]


class TestModelFields:
    def test_save_does_not_write_max_side(self):
        """Ningún modelo del template lo aplica: la pestaña no lo muestra ni lo escribe."""
        tab, config = _tab(**{f"inference.models.{_MODEL}.max_side_px": 640})
        tab.save()
        assert f"inference.models.{_MODEL}.max_side_px" not in config.written

    def test_a_browsed_file_inside_the_data_dir_is_saved_relative(self, monkeypatch, tmp_path):
        chosen = tmp_path / "models" / "best.engine"
        monkeypatch.setattr(inference_tab, "DATA_DIR", str(tmp_path))
        monkeypatch.setattr(inference_tab.QFileDialog, "getOpenFileName",
                            lambda *args: (str(chosen), ""))
        tab, config = _tab()
        _click(tab, tr("inf_model_browse"))
        tab.save()
        assert config.written[f"inference.models.{_MODEL}.path"] == "models/best.engine"

    def test_cancelling_the_browser_keeps_the_path(self, monkeypatch):
        monkeypatch.setattr(inference_tab.QFileDialog, "getOpenFileName",
                            lambda *args: ("", ""))
        tab, config = _tab(**{f"inference.models.{_MODEL}.path": "models/old.engine"})
        _click(tab, tr("inf_model_browse"))
        tab.save()
        assert config.written[f"inference.models.{_MODEL}.path"] == "models/old.engine"


class TestConfigPath:
    def test_a_path_inside_the_base_is_relative_with_forward_slashes(self, tmp_path):
        path = tmp_path / "models" / "seg" / "best.engine"
        assert inference_tab.to_config_path(str(path), str(tmp_path)) == "models/seg/best.engine"

    def test_a_path_outside_the_base_stays_absolute(self, tmp_path):
        base = tmp_path / "installation"
        outside = tmp_path / "elsewhere" / "best.engine"
        result = inference_tab.to_config_path(str(outside), str(base))
        assert result == str(outside).replace(os.sep, "/")


def _click(tab: InferenceTab, text: str):
    """Aprieta el primer botón de la pestaña con ese texto."""
    next(button for button in tab.findChildren(QPushButton) if button.text() == text).click()


def _camera_checks(tab: InferenceTab) -> dict[str, QCheckBox]:
    """Las casillas de cámara del pipeline, por slot (va en el tooltip)."""
    checklist = tab._pipeline_forms[_PIPELINE]["cameras"]
    return {check.toolTip(): check for check in checklist.findChildren(QCheckBox)}


class TestCameraChecklist:
    _CAMERAS = {"cameras": {"camera_1": {"name": "Cinta"}, "camera_2": {}}}

    def test_there_is_a_box_per_camera_with_its_name(self):
        tab, _ = _tab(**self._CAMERAS)
        assert [check.text() for check in _camera_checks(tab).values()] == \
            ["Cinta", "camera_2"]

    def test_the_pipeline_cameras_are_the_checked_ones(self):
        tab, _ = _tab(**self._CAMERAS)
        assert {slot: check.isChecked() for slot, check in _camera_checks(tab).items()} == \
            {"camera_1": True, "camera_2": False}

    def test_save_writes_the_checked_cameras(self):
        tab, config = _tab(**self._CAMERAS)
        _camera_checks(tab)["camera_2"].setChecked(True)
        tab.save()
        assert config.written[f"inference.pipelines.{_PIPELINE}.cameras"] == \
            ["camera_1", "camera_2"]

    def test_an_all_cameras_list_goes_back_empty(self):
        """Vacía es «todas»: guardarla explícita dejaría afuera a una cámara que se agregue."""
        tab, config = _tab(**self._CAMERAS,
                           **{f"inference.pipelines.{_PIPELINE}.cameras": []})
        assert all(check.isChecked() for check in _camera_checks(tab).values())
        tab.save()
        assert config.written[f"inference.pipelines.{_PIPELINE}.cameras"] == []

    def test_an_explicit_full_list_stays_explicit(self):
        tab, config = _tab(**self._CAMERAS, **{
            f"inference.pipelines.{_PIPELINE}.cameras": ["camera_1", "camera_2"]})
        tab.save()
        assert config.written[f"inference.pipelines.{_PIPELINE}.cameras"] == \
            ["camera_1", "camera_2"]

    def test_the_last_checked_camera_cannot_be_unchecked(self):
        """Sin ninguna, la lista quedaría vacía, y vacía es «todas»."""
        tab, _ = _tab(**self._CAMERAS)
        checks = _camera_checks(tab)
        assert not checks["camera_1"].isEnabled()
        checks["camera_2"].setChecked(True)
        assert checks["camera_1"].isEnabled() and checks["camera_2"].isEnabled()


class TestCycleFields:
    """Se prende sólo lo que rige con el número de imágenes por medición."""

    def _enabled(self, tab: InferenceTab) -> dict[str, bool]:
        fields = tab._pipeline_forms[_PIPELINE]
        return {key: fields[key].isEnabled()
                for key in ("interval_s", "cycle_interval_s", "capture_timeout_s",
                            "idle_cameras_between_cycles")}

    def test_with_one_frame_only_the_engine_interval_counts(self):
        tab, _ = _tab(**{f"inference.pipelines.{_PIPELINE}.frames_per_cycle": 1})
        assert self._enabled(tab) == {"interval_s": True, "cycle_interval_s": False,
                                      "capture_timeout_s": False,
                                      "idle_cameras_between_cycles": False}

    def test_with_several_frames_the_round_settings_count_and_the_interval_does_not(self):
        tab, _ = _tab()
        assert self._enabled(tab) == {"interval_s": False, "cycle_interval_s": True,
                                      "capture_timeout_s": True,
                                      "idle_cameras_between_cycles": True}

    def test_changing_the_frames_switches_them_at_once(self):
        tab, _ = _tab()
        tab._pipeline_forms[_PIPELINE]["frames_per_cycle"].setValue(1)
        assert self._enabled(tab)["interval_s"]
        assert not self._enabled(tab)["capture_timeout_s"]

    def test_on_request_one_frame_is_a_round_without_a_wait(self):
        tab, _ = _tab(**{f"inference.pipelines.{_PIPELINE}.frames_per_cycle": 1,
                         f"inference.pipelines.{_PIPELINE}.cycle_trigger": "request"})
        assert self._enabled(tab) == {"interval_s": False, "cycle_interval_s": False,
                                      "capture_timeout_s": True,
                                      "idle_cameras_between_cycles": True}

    def test_a_disabled_pipeline_has_them_all_off(self):
        tab, _ = _tab(**{f"inference.pipelines.{_PIPELINE}.enabled": False})
        assert not any(self._enabled(tab).values())

    def test_enabling_the_pipeline_again_keeps_the_ones_that_do_not_apply_off(self):
        tab, _ = _tab(**{f"inference.pipelines.{_PIPELINE}.enabled": False})
        tab._pipeline_forms[_PIPELINE]["enabled"].setChecked(True)
        assert not self._enabled(tab)["interval_s"]
        assert self._enabled(tab)["capture_timeout_s"]

    def test_a_field_that_does_not_apply_keeps_its_value(self):
        """Volver a varias imágenes no puede encontrar el tiempo máximo en 0, «sin límite»."""
        tab, config = _tab(**{f"inference.pipelines.{_PIPELINE}.frames_per_cycle": 1,
                              f"inference.pipelines.{_PIPELINE}.capture_timeout_s": 45})
        tab.save()
        assert config.written[f"inference.pipelines.{_PIPELINE}.capture_timeout_s"] == 45


class TestWholeTimes:
    def test_the_interval_is_edited_in_whole_milliseconds(self):
        tab, config = _tab(**{f"inference.pipelines.{_PIPELINE}.interval_s": 0.5})
        assert tab._pipeline_forms[_PIPELINE]["interval_s"].value() == 500
        tab.save()
        assert config.written[f"inference.pipelines.{_PIPELINE}.interval_s"] == \
            pytest.approx(0.5)

    def test_seconds_are_saved_as_integers(self):
        tab, config = _tab(**{f"inference.pipelines.{_PIPELINE}.cycle_interval_s": 60.0,
                              f"inference.pipelines.{_PIPELINE}.capture_timeout_s": 30.0})
        tab.save()
        written = [config.written[f"inference.pipelines.{_PIPELINE}.{key}"]
                   for key in ("cycle_interval_s", "capture_timeout_s")]
        assert written == [60, 30] and all(isinstance(value, int) for value in written)

    def test_a_fraction_of_a_second_does_not_become_zero(self):
        """En la espera y el tiempo máximo, 0 es otro modo: sin espera, sin límite."""
        tab, config = _tab(**{f"inference.pipelines.{_PIPELINE}.capture_timeout_s": 0.4})
        tab.save()
        assert config.written[f"inference.pipelines.{_PIPELINE}.capture_timeout_s"] == 1


class TestRoundTrip:
    def test_the_model_and_the_pipeline_go_back_as_they_were(self):
        tab, config = _tab()
        tab.save()
        assert config.written[f"inference.models.{_MODEL}.type"] == "mock"
        assert config.written[f"inference.pipelines.{_PIPELINE}.cameras"] == ["camera_1"]
        assert config.written[f"inference.pipelines.{_PIPELINE}.frames_per_cycle"] == 3


class TestPipelineType:
    def test_without_type_it_shows_and_saves_the_default(self):
        tab, config = _tab()
        tab.save()
        assert config.written[f"inference.pipelines.{_PIPELINE}.type"] == DEFAULT_PIPELINE

    def test_a_registered_type_goes_back_as_it_was(self):
        tab, config = _tab(**{f"inference.pipelines.{_PIPELINE}.type": DEFAULT_PIPELINE})
        tab.save()
        assert config.written[f"inference.pipelines.{_PIPELINE}.type"] == DEFAULT_PIPELINE

    def test_an_unregistered_type_is_not_lost_on_save(self):
        """Abrir la pestaña y guardar no puede cambiar qué clase corre el slot."""
        tab, config = _tab(**{f"inference.pipelines.{_PIPELINE}.type": "person"})
        tab.save()
        assert config.written[f"inference.pipelines.{_PIPELINE}.type"] == "person"

    def test_the_combo_offers_the_registered_types(self):
        tab, _ = _tab()
        combo = tab._pipeline_forms[_PIPELINE]["type"]
        options = [combo.itemText(i) for i in range(combo.count())]
        assert options == list(REGISTERED_PIPELINES)


def _tab_labels(tabs: QTabWidget) -> list[str]:
    return [tabs.tabText(i) for i in range(tabs.count())]


def _many_slots(count: int) -> dict:
    """Un config con `count` modelos y `count` pipelines."""
    return {
        "inference.models": {f"model_{i}": {} for i in range(1, count + 1)},
        "inference.pipelines": {f"pipeline_{i}": {} for i in range(1, count + 1)},
    }


class TestSections:
    def test_models_pipelines_and_regions_have_a_bar_each_in_that_order(self):
        tab, _ = _tab()
        models, pipelines, _regions = tab.findChildren(QTabWidget)
        assert _tab_labels(models) == [_MODEL]
        assert _tab_labels(pipelines) == [_PIPELINE]

    def test_one_sub_tab_per_slot(self):
        tab, _ = _tab(**_many_slots(3))
        models, pipelines, _regions = tab.findChildren(QTabWidget)
        assert _tab_labels(models) == ["model_1", "model_2", "model_3"]
        assert _tab_labels(pipelines) == ["pipeline_1", "pipeline_2", "pipeline_3"]

    def test_every_slot_round_trips_through_its_sub_tab(self):
        overrides = _many_slots(10)
        overrides.update({f"inference.pipelines.pipeline_{i}.frames_per_cycle": i
                          for i in range(1, 11)})
        tab, config = _tab(**overrides)
        tab.save()
        assert [config.written[f"inference.pipelines.pipeline_{i}.frames_per_cycle"]
                for i in range(1, 11)] == list(range(1, 11))


class TestSlotTabs:
    def test_a_long_bar_scrolls_instead_of_eliding(self):
        """Diez rótulos no entran en la tarjeta: flechas, y ningún slot cortado a la mitad."""
        tabs = build_slot_tabs()
        assert tabs.usesScrollButtons()
        assert tabs.elideMode() == Qt.ElideNone

    def test_ten_pipelines_keep_their_full_labels(self):
        tab, _ = _tab(**_many_slots(10))
        _, pipelines, _ = tab.findChildren(QTabWidget)
        assert _tab_labels(pipelines) == [f"pipeline_{i}" for i in range(1, 11)]
        assert pipelines.usesScrollButtons()


class TestPipelinePage:
    """La sub-pestaña de un pipeline lleva su nombre, y su slot queda a la vista debajo."""

    def _pipeline_bar(self, tab: InferenceTab) -> QTabWidget:
        _, pipelines, _ = tab.findChildren(QTabWidget)
        return pipelines

    def test_a_named_pipeline_shows_its_name_on_its_sub_tab(self):
        tab, _ = _tab(**{f"inference.pipelines.{_PIPELINE}": {"name": "Nivel de llenado"}})
        assert _tab_labels(self._pipeline_bar(tab)) == ["Nivel de llenado"]

    def test_typing_a_name_renames_its_sub_tab(self):
        tab, _ = _tab()
        tab._pipeline_forms[_PIPELINE]["name"].setText("Presencia")
        assert _tab_labels(self._pipeline_bar(tab)) == ["Presencia"]

    def test_the_slot_is_shown_under_the_name(self):
        tab, _ = _tab(**{f"inference.pipelines.{_PIPELINE}": {"name": "Nivel de llenado"}})
        hints = [label.text() for label in self._pipeline_bar(tab).widget(0).findChildren(
            QLabel, "settingsFieldHint")]
        assert tr("inf_pipeline_slot_hint").format(slot=_PIPELINE) in hints

    def test_a_disabled_pipeline_has_its_fields_off(self):
        tab, _ = _tab(**{f"inference.pipelines.{_PIPELINE}.enabled": False})
        fields = tab._pipeline_forms[_PIPELINE]
        assert fields["enabled"].isEnabled()
        assert not fields["name"].isEnabled()
        assert not fields["frames_per_cycle"].isEnabled()

    def test_a_disabled_pipeline_still_saves_its_values(self):
        tab, config = _tab(**{f"inference.pipelines.{_PIPELINE}.enabled": False})
        tab.save()
        assert config.written[f"inference.pipelines.{_PIPELINE}.enabled"] is False
        assert config.written[f"inference.pipelines.{_PIPELINE}.frames_per_cycle"] == 3


def _with_roi(roi: dict) -> dict:
    """El ROI propio de `camera_1`, donde el config lo tiene: en la sección del pipeline."""
    return {f"inference.pipelines.{_PIPELINE}": {"roi": {"camera_1": roi}}}


class TestRegionRows:
    """Una fila por (pipeline, cámara): dice qué región usa y abre el diálogo que la edita."""

    _OWN = {"enabled": True, "x_px": 10, "y_px": 20, "width_px": 30, "height_px": 40}

    def _texts(self, tab: InferenceTab, camera_slot: str = "camera_1") -> tuple[str, str]:
        summary, problems = tab._region_rows[(_PIPELINE, camera_slot)]
        return summary.text(), "" if problems.isHidden() else problems.text()

    def test_there_is_a_row_per_camera_of_the_pipeline(self):
        tab, _ = _tab(**{f"inference.pipelines.{_PIPELINE}.cameras": ["camera_1", "camera_2"]})
        assert sorted(tab._region_rows) == [(_PIPELINE, "camera_1"), (_PIPELINE, "camera_2")]

    def test_without_a_list_every_declared_camera_has_a_row(self):
        tab, _ = _tab(**{f"inference.pipelines.{_PIPELINE}.cameras": [],
                         "cameras": {"camera_1": {}, "camera_2": {}}})
        assert len(tab._region_rows) == 2

    def test_an_own_region_is_one_line(self):
        tab, _ = _tab(**_with_roi(self._OWN))
        assert self._texts(tab)[0] == "10,20 · 30×40"

    def test_on_a_video_camera_the_line_says_the_cut_is_native_and_the_size_comes_from_the_model(self):
        tab, _ = _tab(**_with_roi(self._OWN), **{"cameras.camera_1": {"driver": "rtsp"}})
        assert self._texts(tab)[0] == (
            f"10,20 · 30×40 · {tr('inf_region_native')} · {tr('inf_region_view_auto')}")

    def test_on_a_gige_camera_the_line_does_not_talk_about_a_native_cut(self):
        tab, _ = _tab(**_with_roi(self._OWN), **{"cameras.camera_1": {"driver": "basler_gige"}})
        assert self._texts(tab)[0] == "10,20 · 30×40"

    def test_a_disabled_one_is_the_whole_frame(self):
        tab, _ = _tab(**_with_roi({"enabled": False}))
        assert self._texts(tab)[0] == tr("inf_roi_mode_full")

    def test_without_one_and_without_a_camera_roi_it_is_the_whole_frame(self):
        tab, _ = _tab()
        assert self._texts(tab)[0] == tr("inf_roi_mode_full")

    def test_a_video_camera_with_an_adjustment_says_the_region_is_not_native(self):
        tab, _ = _tab(**_with_roi(self._OWN),
                      **{"cameras.camera_1": {"driver": "rtsp",
                                             "image_adjust": {"brightness_factor": 2.0}}})
        assert self._texts(tab)[1] == tr("regions_problem_native_preprocessed")

    def test_a_gige_camera_with_an_adjustment_has_nothing_to_flag(self):
        tab, _ = _tab(**_with_roi(self._OWN),
                      **{"cameras.camera_1": {"driver": "basler_gige",
                                             "image_adjust": {"brightness_factor": 2.0}}})
        assert self._texts(tab)[1] == ""

    def test_a_region_too_small_to_cut_from_the_native_is_flagged(self):
        tab, _ = _tab(**_with_roi({**self._OWN, "width_px": 12}),
                      **{"cameras.camera_1": {"driver": "rtsp"}})
        assert self._texts(tab)[1] == tr("regions_problem_too_small")

    def test_a_good_region_has_no_flag(self):
        tab, _ = _tab(**_with_roi(self._OWN))
        assert self._texts(tab)[1] == ""

    def test_the_button_asks_for_the_dialog_of_that_camera_with_that_pipeline(self):
        tab, _ = _tab()
        requests = []
        tab.open_regions_requested.connect(lambda *args: requests.append(args))
        buttons = [button for button in tab.findChildren(QPushButton)
                   if button.text() == tr("inf_regions_edit")]
        buttons[0].click()
        assert requests == [("camera_1", _PIPELINE)]

    def test_reloading_brings_what_the_dialog_wrote(self):
        tab, config = _tab(**_with_roi({"enabled": False}))
        config._values.update(_with_roi(self._OWN))
        tab.reload_regions()
        assert self._texts(tab)[0] == "10,20 · 30×40"

    def test_a_config_without_one_saves_nothing_about_it(self):
        """Abrir la pestaña y guardar no agrega una clave que el config no tenía."""
        tab, config = _tab()
        tab.save()
        assert not [key for key in config.written if ".roi" in key]

    def test_save_never_writes_a_region(self):
        """Las regiones las persiste el diálogo; guardar la pestaña no las pisa con lo que había."""
        tab, config = _tab(**_with_roi(self._OWN))
        tab.save()
        assert not [key for key in config.written if ".roi" in key]


class TestWithoutCameraRoi:
    def test_a_leftover_camera_roi_is_not_the_pipeline_region(self):
        tab, _ = _tab(**{"cameras.camera_1.roi": {"enabled": True, "x_px": 1, "width_px": 5}})
        summary, _ = tab._region_rows[(_PIPELINE, "camera_1")]
        assert summary.text() == tr("inf_roi_mode_full")


class TestRegionsSection:
    """Las regiones tienen su propia sección, con una sub-pestaña por cámara y los nombres del operador."""

    def _bars(self, tab: InferenceTab):
        models, pipelines, regions = tab.findChildren(QTabWidget)
        return regions

    def test_the_section_has_one_sub_tab_per_camera(self):
        tab, _ = _tab(**{f"inference.pipelines.{_PIPELINE}.cameras": ["camera_1", "camera_2"]})
        assert _tab_labels(self._bars(tab)) == ["camera_1", "camera_2"]

    def test_the_sub_tab_is_labelled_with_the_camera_name(self):
        tab, _ = _tab(**{"cameras": {"camera_1": {"name": "Calza derecha"}}})
        assert _tab_labels(self._bars(tab)) == ["Calza derecha"]

    def test_a_camera_without_a_name_shows_its_slot(self):
        tab, _ = _tab(**{"cameras": {"camera_1": {"name": ""}}})
        assert _tab_labels(self._bars(tab)) == ["camera_1"]

    def test_a_declared_camera_no_pipeline_names_is_listed_too(self):
        tab, _ = _tab(**{"cameras": {"camera_1": {}, "camera_7": {"name": "Sobrante"}}})
        assert "Sobrante" in _tab_labels(self._bars(tab))

    def test_a_camera_nobody_looks_at_says_so(self):
        tab, _ = _tab(**{"cameras": {"camera_1": {}, "camera_2": {}},
                         f"inference.pipelines.{_PIPELINE}.cameras": ["camera_1"]})
        assert not [key for key in tab._region_rows if key[1] == "camera_2"]

    def test_the_rows_are_not_inside_the_pipeline_pages(self):
        """Antes colgaban de cada pipeline; ahora son una sección aparte."""
        tab, _ = _tab()
        _, pipelines, _ = tab.findChildren(QTabWidget)
        page = pipelines.widget(0)
        edit_buttons = [button for button in page.findChildren(QPushButton)
                        if button.text() == tr("inf_regions_edit")]
        assert edit_buttons == []

    def test_a_row_is_titled_with_the_pipeline_name(self):
        tab, _ = _tab(**{f"inference.pipelines.{_PIPELINE}": {"name": "Nivel de llenado"}})
        assert tab._region_names[(_PIPELINE, "camera_1")].text() == "Nivel de llenado"

    def test_a_pipeline_without_a_name_shows_its_slot(self):
        tab, _ = _tab()
        assert tab._region_names[(_PIPELINE, "camera_1")].text() == _PIPELINE

    def test_typing_a_name_renames_the_rows_at_once(self):
        tab, _ = _tab()
        tab._pipeline_forms[_PIPELINE]["name"].setText("Presencia")
        assert tab._region_names[(_PIPELINE, "camera_1")].text() == "Presencia"

    def test_the_name_is_loaded_into_the_pipeline_page(self):
        tab, _ = _tab(**{f"inference.pipelines.{_PIPELINE}": {"name": "Nivel de llenado"}})
        assert tab._pipeline_forms[_PIPELINE]["name"].text() == "Nivel de llenado"

    def test_save_writes_the_name(self):
        tab, config = _tab()
        tab._pipeline_forms[_PIPELINE]["name"].setText("  Presencia ")
        tab.save()
        assert config.written[f"inference.pipelines.{_PIPELINE}.name"] == "Presencia"

    def test_save_does_not_add_an_empty_name_key(self):
        tab, config = _tab()
        tab.save()
        assert f"inference.pipelines.{_PIPELINE}.name" not in config.written

    def test_each_row_carries_the_color_of_its_pipeline(self):
        """El mismo color del recuadro en el diálogo y en el monitor: el del overlay."""
        tab, _ = _tab(**{"inference.pipelines": {_PIPELINE: {}, "pipeline_2": {}}})
        swatches = tab.findChildren(_PipelineSwatch)
        expected = [overlay.get_pipeline_style(index).color_bgr for index in (0, 1)]
        assert [swatch._color.getRgb()[2::-1] for swatch in swatches] == expected

    def test_a_name_that_was_cleared_is_cleared_in_the_file(self):
        tab, config = _tab(**{f"inference.pipelines.{_PIPELINE}": {"name": "Viejo"}})
        tab._pipeline_forms[_PIPELINE]["name"].setText("")
        tab.save()
        assert config.written[f"inference.pipelines.{_PIPELINE}.name"] == ""
