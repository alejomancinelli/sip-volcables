"""Tests de las regiones: qué rectángulo rige para un pipeline, cómo se acota y qué se le avisa."""

import pytest

from system.inference import regions
from system.inference.regions import (
    PROBLEM_FRAME_SIZE_CHANGED, PROBLEM_NATIVE_PREPROCESSED, PROBLEM_OUTSIDE_FRAME,
    PROBLEM_TOO_SMALL,
)

_CAMERA = "camera_1"
_OWN = {"enabled": True, "x_px": 10, "y_px": 20, "width_px": 30, "height_px": 40}


def _pipelines() -> dict:
    return {
        "pipeline_1": {"cameras": [_CAMERA], "roi": {_CAMERA: _OWN}},
        "pipeline_2": {"cameras": [_CAMERA, "camera_2"]},
        "pipeline_3": {"enabled": False, "cameras": [_CAMERA]},
        "pipeline_4": {"cameras": ["camera_2"]},
        "pipeline_5": {},
    }


class TestResolve:
    def test_a_null_own_roi_is_not_an_override(self):
        section = {"roi": {_CAMERA: None}}
        assert regions.select_pipeline_roi(section, _CAMERA) is None

    @pytest.mark.parametrize("section", [None, "x", [], {"roi": None}, {"roi": []}])
    def test_a_malformed_section_has_no_own_roi(self, section):
        assert regions.select_pipeline_roi(section, _CAMERA) is None



class TestPipelineIndex:
    def test_it_is_the_position_in_the_config(self):
        assert regions.get_pipeline_index(_pipelines(), "pipeline_3") == 2

    def test_an_unknown_pipeline_is_the_first(self):
        assert regions.get_pipeline_index(_pipelines(), "nope") == 0
        assert regions.get_pipeline_index(None, "pipeline_1") == 0


class TestClip:
    def test_a_disabled_roi_gives_nothing(self):
        assert regions.clip_roi_px({"enabled": False, "width_px": 10}, 100, 100) is None

    def test_a_zero_size_reaches_the_border(self):
        roi = {"enabled": True, "x_px": 30, "y_px": 20}
        assert regions.clip_roi_px(roi, 100, 80) == (30, 20, 70, 60)

    def test_it_is_cut_at_the_frame(self):
        roi = {"enabled": True, "x_px": 90, "y_px": 0, "width_px": 50, "height_px": 10}
        assert regions.clip_roi_px(roi, 100, 100) == (90, 0, 10, 10)

    def test_an_origin_outside_the_frame_is_pulled_to_the_last_pixel(self):
        roi = {"enabled": True, "x_px": 500, "y_px": 500, "width_px": 10, "height_px": 10}
        assert regions.clip_roi_px(roi, 100, 100) == (99, 99, 1, 1)


class TestProblems:
    _FRAME = (200, 100)

    def _roi(self, **overrides) -> dict:
        roi = {"enabled": True, "x_px": 10, "y_px": 10, "width_px": 50, "height_px": 50}
        roi.update(overrides)
        return roi

    def _find(self, roi: dict, *, frame=_FRAME, is_native_lost=False,
              is_native_cut=False) -> list:
        return regions.find_region_problems(roi, frame_size_px=frame,
                                            is_native_lost=is_native_lost,
                                            is_native_cut=is_native_cut)

    def test_a_good_region_has_none(self):
        assert self._find(self._roi()) == []

    def test_a_disabled_one_is_never_judged(self):
        roi = self._roi(enabled=False, x_px=9999, width_px=3)
        assert self._find(roi, is_native_lost=True, is_native_cut=True) == []

    def test_going_past_the_right_edge(self):
        assert self._find(self._roi(x_px=180)) == [PROBLEM_OUTSIDE_FRAME]

    def test_going_past_the_bottom_edge(self):
        assert self._find(self._roi(y_px=80)) == [PROBLEM_OUTSIDE_FRAME]

    def test_starting_outside_the_frame(self):
        assert PROBLEM_OUTSIDE_FRAME in self._find(self._roi(x_px=200, width_px=0))

    def test_a_zero_size_that_reaches_the_border_is_inside(self):
        assert self._find(self._roi(width_px=0, height_px=0)) == []

    def test_a_video_camera_with_a_preprocessor_cannot_cut_from_the_native(self):
        assert self._find(self._roi(), is_native_lost=True) == [PROBLEM_NATIVE_PREPROCESSED]
        assert self._find(self._roi(), is_native_lost=False) == []

    def test_a_native_cut_needs_at_least_16_px_per_side(self):
        for side in ("width_px", "height_px"):
            assert self._find(self._roi(**{side: 15}), is_native_cut=True) == [PROBLEM_TOO_SMALL]
            assert self._find(self._roi(**{side: 16}), is_native_cut=True) == []

    def test_a_small_region_is_fine_where_nothing_is_cut_from_the_native(self):
        assert self._find(self._roi(width_px=15), is_native_cut=False) == []

    def test_a_zero_side_is_not_a_small_one(self):
        """Cero es «hasta el borde», no un rectángulo de cero píxeles."""
        assert self._find(self._roi(width_px=0, height_px=0), is_native_cut=True) == []

    def test_without_the_frame_only_what_does_not_depend_on_it_is_judged(self):
        roi = self._roi(x_px=9999, width_px=3)
        assert self._find(roi, frame=None, is_native_lost=True, is_native_cut=True) == [
            PROBLEM_NATIVE_PREPROCESSED, PROBLEM_TOO_SMALL]

    def test_a_frame_of_another_size_than_the_drawn_one(self):
        roi = self._roi(frame_width_px=640, frame_height_px=360)
        assert self._find(roi) == [PROBLEM_FRAME_SIZE_CHANGED]

    def test_the_same_size_as_the_drawn_one_is_fine(self):
        roi = self._roi(frame_width_px=200, frame_height_px=100)
        assert self._find(roi) == []

    def test_a_region_without_a_drawn_size_is_not_judged_for_it(self):
        assert PROBLEM_FRAME_SIZE_CHANGED not in self._find(self._roi())


class TestOverlap:
    def test_no_contact(self):
        assert regions.get_overlap_pct((0, 0, 10, 10), (20, 20, 10, 10)) == 0.0

    def test_touching_edges_do_not_overlap(self):
        assert regions.get_overlap_pct((0, 0, 10, 10), (10, 0, 10, 10)) == 0.0

    def test_it_is_the_share_of_the_first_one(self):
        assert regions.get_overlap_pct((0, 0, 10, 10), (5, 0, 100, 100)) == pytest.approx(50.0)

    def test_it_is_not_symmetric(self):
        small, big = (0, 0, 10, 10), (0, 0, 100, 100)
        assert regions.get_overlap_pct(small, big) == pytest.approx(100.0)
        assert regions.get_overlap_pct(big, small) == pytest.approx(1.0)

    def test_an_empty_rectangle_overlaps_nothing(self):
        assert regions.get_overlap_pct((0, 0, 0, 10), (0, 0, 10, 10)) == 0.0


class TestWithoutCameraRoi:
    """La cámara no tiene ROI: un pipeline sin región propia ve el frame completo."""

    def test_a_pipeline_without_a_section_sees_the_whole_frame(self):
        assert regions.resolve_pipeline_roi({}, _CAMERA) == {}

    def test_the_own_roi_is_the_one_that_rules(self):
        assert regions.resolve_pipeline_roi(_pipelines()["pipeline_1"], _CAMERA) == _OWN

    def test_a_null_or_malformed_one_is_the_whole_frame(self):
        assert regions.resolve_pipeline_roi({"roi": {_CAMERA: None}}, _CAMERA) == {}
        assert regions.resolve_pipeline_roi(None, _CAMERA) == {}

    def test_the_list_has_the_enabled_pipelines_that_look_at_the_camera(self):
        listed = regions.list_camera_regions(_pipelines(), _CAMERA)
        assert [region.pipeline_slot for region in listed] == [
            "pipeline_1", "pipeline_2", "pipeline_5"]

    def test_disabled_ones_can_be_included(self):
        listed = regions.list_camera_regions(_pipelines(), _CAMERA, include_disabled=True)
        assert "pipeline_3" in [region.pipeline_slot for region in listed]

    def test_a_pipeline_without_cameras_looks_at_all_of_them(self):
        listed = regions.list_camera_regions(_pipelines(), "camera_9")
        assert [region.pipeline_slot for region in listed] == ["pipeline_5"]

    def test_the_index_is_the_position_in_the_config(self):
        listed = regions.list_camera_regions(_pipelines(), _CAMERA)
        assert [region.index for region in listed] == [0, 1, 4]

    def test_a_region_carries_its_own_roi_or_an_empty_one(self):
        listed = regions.list_camera_regions(_pipelines(), _CAMERA)
        assert [region.roi for region in listed[:2]] == [_OWN, {}]

    def test_it_survives_a_missing_section(self):
        assert regions.list_camera_regions(None, _CAMERA) == []


class TestRegionName:
    def test_it_is_the_name_the_operator_gave_the_pipeline(self):
        pipelines = {"pipeline_1": {"name": "Flujo de grano"}}
        assert regions.list_camera_regions(pipelines, _CAMERA)[0].name == "Flujo de grano"

    def test_without_one_it_is_the_slot(self):
        assert regions.list_camera_regions({"pipeline_1": {}}, _CAMERA)[0].name == "pipeline_1"

    @pytest.mark.parametrize("name", ["", "   ", None])
    def test_an_empty_name_is_the_slot(self, name):
        listed = regions.list_camera_regions({"pipeline_1": {"name": name}}, _CAMERA)
        assert listed[0].name == "pipeline_1"

    def test_the_name_is_read_from_the_pipeline_section(self):
        assert regions.get_pipeline_name({"name": " Grano "}, "pipeline_1") == "Grano"

    @pytest.mark.parametrize("section", [None, "x", {}])
    def test_a_section_without_a_name_gives_the_slot(self, section):
        assert regions.get_pipeline_name(section, "pipeline_1") == "pipeline_1"

    def test_the_slot_stays_as_the_identity(self):
        listed = regions.list_camera_regions({"pipeline_1": {"name": "X"}}, _CAMERA)
        assert listed[0].pipeline_slot == "pipeline_1"
