"""
Tests del diálogo de regiones: qué pestañas arma, qué escribe al aceptar y qué avisa.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import copy
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                              # noqa: E402

from ui.dialogs.pipeline_regions_dialog import PipelineRegionsDialog   # noqa: E402
from ui.widgets.pause_button import PauseButton                         # noqa: E402

_CAMERA = "camera_1"
_FRAME_SIZE = (320, 240)        # (ancho, alto)


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


class _MockConfig:
    """ConfigManager mínimo con árbol anidado, `get`/`set` punteados y `save` contado."""

    def __init__(self, tree: dict):
        self.tree = copy.deepcopy(tree)
        self.saves = 0

    def get(self, key: str, default: object = None) -> object:
        node = self.tree
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, key: str, value: object):
        *parents, last = key.split(".")
        node = self.tree
        for part in parents:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[last] = value

    def save(self):
        self.saves += 1


def _tree(**changes) -> dict:
    tree = {
        "cameras": {_CAMERA: {"name": "Entrada", "roi": {"enabled": False}},
                    "camera_2": {"name": "Salida", "roi": {"enabled": False}}},
        "inference": {"pipelines": {
            "pipeline_1": {"cameras": [_CAMERA], "roi": {_CAMERA: {
                "enabled": True, "x_px": 10, "y_px": 20, "width_px": 100, "height_px": 50,
                "frame_width_px": 320, "frame_height_px": 240}}},
            "pipeline_2": {"cameras": [_CAMERA, "camera_2"]},
            "pipeline_3": {"enabled": False, "cameras": [_CAMERA]},
            "pipeline_4": {"cameras": ["camera_2"]},
        }},
    }
    for key, value in changes.items():
        node = tree
        *parents, last = key.split(".")
        for part in parents:
            node = node[part]
        node[last] = value
    return tree


def _frame(width_px: int = _FRAME_SIZE[0], height_px: int = _FRAME_SIZE[1]) -> np.ndarray:
    return np.full((height_px, width_px, 3), 90, np.uint8)


def _dialog(tree: dict | None = None, *, pipeline: str = "", with_frame: bool = True):
    config = _MockConfig(tree or _tree())
    dialog = PipelineRegionsDialog(config, _CAMERA, initial_pipeline=pipeline)
    if with_frame:
        dialog.update_frame(_frame())
    return dialog, config


def _page(dialog: PipelineRegionsDialog, pipeline_slot: str):
    return next(page for page in dialog._pages if page.region.pipeline_slot == pipeline_slot)


def _titles(dialog: PipelineRegionsDialog) -> list[str]:
    return [dialog._tabs.tabText(index) for index in range(dialog._tabs.count())]


def _select_mode(page, mode: str):
    index = page.mode.findData(mode)
    assert index >= 0, f"el modo {mode} no se ofrece"
    page.mode.setCurrentIndex(index)


class TestTabs:
    def test_one_tab_per_pipeline_that_looks_at_the_camera_and_disabled_ones_too(self):
        dialog, _ = _dialog()
        titles = [title.removeprefix("⚠ ") for title in _titles(dialog)]
        assert titles == ["pipeline_1", "pipeline_2", "pipeline_3"]

    def test_it_opens_on_the_requested_pipeline(self):
        dialog, _ = _dialog(pipeline="pipeline_2")
        assert dialog._get_current_page().region.pipeline_slot == "pipeline_2"

    def test_the_window_names_the_camera(self):
        dialog, _ = _dialog()
        assert "Entrada" in dialog.windowTitle()

    def test_a_camera_nobody_looks_at_has_nothing_to_save(self):
        tree = _tree()
        tree["inference"]["pipelines"] = {"pipeline_4": {"cameras": ["camera_2"]}}
        dialog, _ = _dialog(tree)
        assert dialog._pages == []
        assert not dialog._save_button.isEnabled()
        assert not dialog._empty_label.isHidden()

    def test_each_tab_starts_in_the_mode_the_config_says(self):
        dialog, _ = _dialog()
        assert _page(dialog, "pipeline_1").mode.currentData() == "own"
        assert _page(dialog, "pipeline_2").mode.currentData() == "full"

    def test_a_disabled_own_roi_starts_as_the_whole_frame(self):
        tree = _tree()
        tree["inference"]["pipelines"]["pipeline_1"]["roi"][_CAMERA]["enabled"] = False
        dialog, _ = _dialog(tree)
        assert _page(dialog, "pipeline_1").mode.currentData() == "full"

    def test_only_an_own_region_has_editable_fields(self):
        dialog, _ = _dialog()
        page = _page(dialog, "pipeline_1")
        assert page.spins["x_px"].isEnabled()
        _select_mode(page, "full")
        assert not page.spins["x_px"].isEnabled()

    def test_there_is_no_checkbox_for_the_native_cut(self):
        """Ya no se elige: lo decide la cámara."""
        dialog, _ = _dialog()
        assert not hasattr(_page(dialog, "pipeline_1"), "full_resolution")

    def test_there_is_no_field_for_the_view_size(self):
        """La vista sale siempre a la entrada del modelo."""
        dialog, _ = _dialog(_tree(**{"cameras.camera_1.driver": "rtsp"}))
        assert not hasattr(_page(dialog, "pipeline_1"), "output_side")

    def test_the_geometry_loads_from_the_config(self):
        dialog, _ = _dialog()
        page = _page(dialog, "pipeline_1")
        assert [page.spins[key].value() for key in ("x_px", "y_px", "width_px", "height_px")] \
            == [10, 20, 100, 50]


class TestCanvasRegions:
    def _regions(self, dialog):
        return {region.name: region for region in dialog._canvas._regions}

    def test_every_pipeline_is_drawn_and_only_the_current_one_is_active(self):
        dialog, _ = _dialog()
        regions = self._regions(dialog)
        assert set(regions) == {"pipeline_1", "pipeline_2", "pipeline_3"}
        assert [name for name, region in regions.items() if region.is_active] == ["pipeline_1"]

    def test_the_color_is_the_one_the_overlay_gives_that_pipeline(self):
        from system.inference import overlay
        dialog, _ = _dialog()
        regions = self._regions(dialog)
        assert regions["pipeline_1"].color_bgr == overlay.get_pipeline_style(0).color_bgr
        assert regions["pipeline_2"].color_bgr == overlay.get_pipeline_style(1).color_bgr

    def test_changing_the_tab_changes_which_one_is_editable(self):
        dialog, _ = _dialog()
        dialog._tabs.setCurrentIndex(1)
        regions = self._regions(dialog)
        assert regions["pipeline_2"].is_active and not regions["pipeline_1"].is_active

    def test_a_pipeline_that_sees_the_whole_frame_has_no_rectangle(self):
        dialog, _ = _dialog()
        assert self._regions(dialog)["pipeline_2"].rect_px is None

    def test_a_zero_size_own_region_is_drawn_up_to_the_border(self):
        dialog, _ = _dialog()
        page = _page(dialog, "pipeline_1")
        for spin in page.spins.values():
            spin.setValue(0)
        assert self._regions(dialog)["pipeline_1"].rect_px == (0, 0, *_FRAME_SIZE)


class TestSave:
    def _written(self, config, pipeline_slot: str = "pipeline_1") -> dict:
        return config.tree["inference"]["pipelines"][pipeline_slot]["roi"][_CAMERA]

    def test_an_untouched_dialog_writes_no_region(self):
        dialog, config = _dialog()
        before = copy.deepcopy(config.tree)
        dialog._on_save_clicked()
        assert config.tree == before
        assert config.saves == 1

    def test_a_typed_value_is_written_with_the_frame_it_was_drawn_on(self):
        dialog, config = _dialog()
        _page(dialog, "pipeline_1").spins["x_px"].setValue(33)
        dialog._on_save_clicked()
        roi = self._written(config)
        assert roi["x_px"] == 33 and roi["enabled"] is True
        assert (roi["frame_width_px"], roi["frame_height_px"]) == _FRAME_SIZE

    def test_the_drawn_size_is_the_live_frame_not_the_stored_one(self):
        tree = _tree()
        tree["inference"]["pipelines"]["pipeline_1"]["roi"][_CAMERA].update(
            frame_width_px=640, frame_height_px=480)
        dialog, config = _dialog(tree)
        _page(dialog, "pipeline_1").spins["x_px"].setValue(5)
        dialog._on_save_clicked()
        roi = self._written(config)
        assert (roi["frame_width_px"], roi["frame_height_px"]) == _FRAME_SIZE

    def test_an_edit_made_with_the_mouse_counts_as_a_touch(self):
        dialog, config = _dialog()
        dialog._canvas.region_edited.emit(40, 30, 60, 50)
        dialog._on_save_clicked()
        roi = self._written(config)
        assert (roi["x_px"], roi["y_px"], roi["width_px"], roi["height_px"]) == (40, 30, 60, 50)

    def test_the_other_tabs_are_left_alone(self):
        dialog, config = _dialog()
        _page(dialog, "pipeline_1").spins["x_px"].setValue(33)
        dialog._on_save_clicked()
        assert "roi" not in config.tree["inference"]["pipelines"]["pipeline_2"]

    def test_the_whole_frame_is_written_as_disabled(self):
        dialog, config = _dialog()
        _select_mode(_page(dialog, "pipeline_1"), "full")
        dialog._on_save_clicked()
        assert self._written(config)["enabled"] is False

    def test_a_new_own_region_creates_its_section(self):
        dialog, config = _dialog()
        page = _page(dialog, "pipeline_2")
        _select_mode(page, "own")
        page.spins["width_px"].setValue(80)
        dialog._on_save_clicked()
        roi = self._written(config, "pipeline_2")
        assert roi["enabled"] is True and roi["width_px"] == 80
        assert "full_resolution" not in roi and "output_short_side_px" not in roi

    def test_cancel_writes_nothing(self):
        dialog, config = _dialog()
        before = copy.deepcopy(config.tree)
        _page(dialog, "pipeline_1").spins["x_px"].setValue(33)
        dialog.reject()
        assert config.tree == before and config.saves == 0

    def test_reset_zeroes_the_current_region(self):
        dialog, _ = _dialog()
        dialog._on_reset_clicked()
        page = _page(dialog, "pipeline_1")
        assert all(spin.value() == 0 for spin in page.spins.values())
        assert page.is_touched


class TestProblems:
    def _banner(self, dialog) -> str:
        return dialog._problems_label.text() if not dialog._problems_label.isHidden() else ""

    def test_a_good_region_has_no_banner(self):
        dialog, _ = _dialog()
        assert self._banner(dialog) == ""

    def test_a_region_past_the_frame_is_flagged_on_its_tab_and_in_the_banner(self):
        dialog, _ = _dialog()
        _page(dialog, "pipeline_1").spins["x_px"].setValue(300)
        assert "pipeline_1" in _titles(dialog)[0] and _titles(dialog)[0].startswith("⚠")
        assert self._banner(dialog)

    def test_the_banner_is_of_the_current_tab_and_the_others_get_a_mark(self):
        dialog, _ = _dialog()
        _page(dialog, "pipeline_1").spins["x_px"].setValue(300)
        dialog._tabs.setCurrentIndex(1)
        assert self._banner(dialog) == ""
        assert _titles(dialog)[0].startswith("⚠")

    def test_native_video_on_a_preprocessed_camera_is_flagged(self):
        tree = _tree(**{"cameras.camera_1.driver": "rtsp",
                        "cameras.camera_1.image_adjust": {"brightness_factor": 2.0}})
        dialog, _ = _dialog(tree)
        assert self._banner(dialog)

    def test_the_same_camera_that_is_gige_has_nothing_to_flag(self):
        tree = _tree(**{"cameras.camera_1.driver": "basler_gige",
                        "cameras.camera_1.image_adjust": {"brightness_factor": 2.0}})
        dialog, _ = _dialog(tree)
        assert self._banner(dialog) == ""

    def test_a_region_too_small_for_the_decoder_is_flagged_on_a_video_camera(self):
        dialog, _ = _dialog(_tree(**{"cameras.camera_1.driver": "rtsp"}))
        _page(dialog, "pipeline_1").spins["width_px"].setValue(12)
        assert self._banner(dialog)

    def test_the_same_small_region_is_fine_on_a_gige_camera(self):
        dialog, _ = _dialog(_tree(**{"cameras.camera_1.driver": "basler_gige"}))
        _page(dialog, "pipeline_1").spins["width_px"].setValue(12)
        assert self._banner(dialog) == ""

    def test_a_frame_of_another_size_than_the_drawn_one_is_flagged(self):
        dialog, _ = _dialog(with_frame=False)
        dialog.update_frame(_frame(640, 480))
        assert "320x240" in self._banner(dialog) and "640x480" in self._banner(dialog)

    def test_redrawing_the_region_on_the_new_frame_clears_the_flag(self):
        dialog, _ = _dialog(with_frame=False)
        dialog.update_frame(_frame(640, 480))
        _page(dialog, "pipeline_1").spins["x_px"].setValue(12)
        assert self._banner(dialog) == ""

    def test_without_a_frame_only_what_does_not_need_it_is_judged(self):
        dialog, _ = _dialog(with_frame=False)
        _page(dialog, "pipeline_1").spins["x_px"].setValue(9999)
        assert self._banner(dialog) == ""


class TestPreviewAndOverlap:
    def _note(self, dialog, pipeline_slot: str = "pipeline_1") -> str:
        return _page(dialog, pipeline_slot).preview_note.text()

    def test_the_preview_is_the_crop_the_model_gets(self):
        dialog, _ = _dialog()
        assert "100×50" in self._note(dialog)
        assert not _page(dialog, "pipeline_1").preview.pixmap().isNull()

    def test_without_a_region_it_is_the_whole_frame(self):
        dialog, _ = _dialog()
        _select_mode(_page(dialog, "pipeline_1"), "full")
        assert "320×240" in self._note(dialog)

    def test_the_native_cut_is_flagged_as_approximate(self):
        dialog, _ = _dialog(_tree(**{"cameras.camera_1.driver": "rtsp"}))
        assert "100×50" in self._note(dialog)
        assert "\n" in self._note(dialog)       # la segunda línea es la aclaración

    def test_without_a_frame_it_waits(self):
        dialog, _ = _dialog(with_frame=False)
        assert _page(dialog, "pipeline_1").preview.text()

    def test_a_new_frame_refreshes_the_preview_without_redrawing_everything(self):
        dialog, _ = _dialog()
        dialog.update_frame(_frame())
        assert "100×50" in self._note(dialog)

    def test_the_overlap_names_the_other_pipeline_and_the_share(self):
        dialog, _ = _dialog()
        page = _page(dialog, "pipeline_3")
        _select_mode(page, "own")
        for key, value in (("x_px", 10), ("y_px", 20), ("width_px", 50), ("height_px", 50)):
            page.spins[key].setValue(value)
        dialog._tabs.setCurrentIndex(2)
        dialog._refresh()
        assert "pipeline_1" in page.overlap.text() and "100 %" in page.overlap.text()

    def test_no_overlap_says_nothing(self):
        dialog, _ = _dialog()
        assert _page(dialog, "pipeline_1").overlap.text() == ""


class TestWithoutCameraRoi:
    """La cámara no tiene región: un pipeline sin sección empieza viendo el frame completo."""

    _LEGACY = {"enabled": True, "x_px": 5, "y_px": 5, "width_px": 40, "height_px": 30}

    def test_only_the_two_modes_are_offered(self):
        dialog, _ = _dialog(_tree(**{"cameras.camera_1.roi": self._LEGACY}))
        modes = [_page(dialog, "pipeline_2").mode.itemData(index)
                 for index in range(_page(dialog, "pipeline_2").mode.count())]
        assert modes == ["own", "full"]

    def test_a_pipeline_without_a_section_starts_as_the_whole_frame(self):
        dialog, _ = _dialog()
        assert _page(dialog, "pipeline_2").mode.currentData() == "full"

    def test_a_leftover_camera_roi_is_not_drawn(self):
        dialog, _ = _dialog(_tree(**{"cameras.camera_1.roi": self._LEGACY}))
        regions = {region.name: region for region in dialog._canvas._regions}
        assert regions["pipeline_2"].rect_px is None

    def test_a_pipeline_without_a_section_writes_nothing_if_untouched(self):
        dialog, config = _dialog()
        dialog._on_save_clicked()
        assert "roi" not in config.tree["inference"]["pipelines"]["pipeline_2"]

    def test_choosing_a_region_for_it_creates_its_section(self):
        dialog, config = _dialog()
        page = _page(dialog, "pipeline_2")
        _select_mode(page, "own")
        page.spins["width_px"].setValue(50)
        dialog._on_save_clicked()
        roi = config.tree["inference"]["pipelines"]["pipeline_2"]["roi"][_CAMERA]
        assert roi["enabled"] is True and roi["width_px"] == 50


class TestNames:
    """El diálogo muestra los nombres del operador; el slot sigue siendo la identidad."""

    def _named(self):
        tree = _tree()
        tree["inference"]["pipelines"]["pipeline_1"]["name"] = "Flujo de grano"
        tree["inference"]["pipelines"]["pipeline_2"]["name"] = "Personas"
        return _dialog(tree)

    def test_the_tabs_carry_the_pipeline_names(self):
        dialog, _ = self._named()
        titles = [title.removeprefix("⚠ ") for title in _titles(dialog)]
        assert titles == ["Flujo de grano", "Personas", "pipeline_3"]

    def test_a_pipeline_without_a_name_shows_its_slot(self):
        dialog, _ = self._named()
        assert _titles(dialog)[2] == "pipeline_3"

    def test_the_rectangles_carry_the_pipeline_names(self):
        dialog, _ = self._named()
        assert {region.name for region in dialog._canvas._regions} == {
            "Flujo de grano", "Personas", "pipeline_3"}

    def test_the_window_carries_the_camera_name(self):
        dialog, _ = self._named()
        assert "Entrada" in dialog.windowTitle() and _CAMERA not in dialog.windowTitle()

    def test_the_overlap_names_the_other_pipeline_by_its_name(self):
        dialog, _ = self._named()
        page = _page(dialog, "pipeline_2")
        _select_mode(page, "own")
        for key, value in (("x_px", 10), ("y_px", 20), ("width_px", 50), ("height_px", 50)):
            page.spins[key].setValue(value)
        dialog._tabs.setCurrentIndex(1)
        dialog._refresh()
        assert "Flujo de grano" in page.overlap.text()

    def test_the_tab_for_a_pipeline_is_still_chosen_by_its_slot(self):
        tree = _tree()
        tree["inference"]["pipelines"]["pipeline_2"]["name"] = "Personas"
        dialog, _ = _dialog(tree, pipeline="pipeline_2")
        assert dialog._get_current_page().region.pipeline_slot == "pipeline_2"

    def test_saving_still_writes_under_the_slot(self):
        tree = _tree()
        tree["inference"]["pipelines"]["pipeline_1"]["name"] = "Flujo de grano"
        dialog, config = _dialog(tree)
        _page(dialog, "pipeline_1").spins["x_px"].setValue(33)
        dialog._on_save_clicked()
        assert config.tree["inference"]["pipelines"]["pipeline_1"]["roi"][_CAMERA]["x_px"] == 33


class TestPause:
    """Con el video en pausa se dibuja sobre el frame quieto: los que llegan se descartan."""

    def test_a_paused_dialog_keeps_the_frame_it_had(self):
        dialog, _ = _dialog()
        dialog.findChild(PauseButton).click()
        dialog.update_frame(_frame(640, 480))
        assert dialog._get_frame_size_px() == _FRAME_SIZE

    def test_resuming_takes_the_next_frame(self):
        dialog, _ = _dialog()
        button = dialog.findChild(PauseButton)
        button.click()
        button.click()
        dialog.update_frame(_frame(640, 480))
        assert dialog._get_frame_size_px() == (640, 480)

    def test_the_region_is_still_editable_while_paused(self):
        dialog, _ = _dialog()
        dialog.findChild(PauseButton).click()
        _page(dialog, "pipeline_1").spins["width_px"].setValue(60)
        assert "60×50" in _page(dialog, "pipeline_1").preview_note.text()
