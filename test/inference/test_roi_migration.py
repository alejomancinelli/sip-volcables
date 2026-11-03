"""
Tests de la migración del config: el ROI de cámara pasa a los pipelines y a la óptica, y
`crop_to_roi` se va. Lo que se prueba es que un equipo con el config viejo mida lo mismo
que medía, y que no se reescriba el archivo si no había nada que migrar.
"""

import copy

import pytest

from system.inference.roi_migration import migrate_roi_config

_GEOMETRY = {"x_px": 10, "y_px": 20, "width_px": 30, "height_px": 40}
_CAMERA_ROI = {"enabled": True, **_GEOMETRY}


class _MockConfig:
    """ConfigManager mínimo con árbol anidado: `get`/`set`/`delete` punteados y `save` contado."""

    def __init__(self, tree: dict):
        self.tree = copy.deepcopy(tree)
        self.saves = 0

    def get(self, key: str, default: object = None) -> object:
        node = self.tree
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return copy.deepcopy(node)

    def set(self, key: str, value: object) -> bool:
        *parents, last = key.split(".")
        node = self.tree
        for part in parents:
            if not isinstance(node.get(part), dict):
                node[part] = {}
            node = node[part]
        node[last] = copy.deepcopy(value)
        return True

    def delete(self, key: str) -> bool:
        *parents, last = key.split(".")
        node = self.tree
        for part in parents:
            node = node.get(part) if isinstance(node, dict) else None
        if not isinstance(node, dict) or last not in node:
            return False
        del node[last]
        return True

    def save(self) -> bool:
        self.saves += 1
        return True


def _tree(camera: dict | None = None, pipelines: dict | None = None,
          overlay: dict | None = None) -> dict:
    return {
        "cameras": {"camera_1": {"name": "Uno", "roi": copy.deepcopy(_CAMERA_ROI),
                                 "lens_health": {"reference": {"variance": 0.0}},
                                 **(camera or {})},
                    "camera_2": {"name": "Dos"}},
        "inference": {
            "overlay": overlay or {},
            "pipelines": pipelines if pipelines is not None else {
                "pipeline_1": {"cameras": ["camera_1"]},
                "pipeline_2": {"cameras": ["camera_2"]},
            }},
    }


def _roi(config: _MockConfig, pipeline_slot: str = "pipeline_1", camera_slot: str = "camera_1"):
    return config.tree["inference"]["pipelines"][pipeline_slot].get("roi", {}).get(camera_slot)


class TestPipelines:
    def test_the_camera_roi_becomes_the_roi_of_the_pipeline_that_looks_at_it(self):
        config = _MockConfig(_tree())
        migrate_roi_config(config)
        assert _roi(config) == _CAMERA_ROI

    def test_a_pipeline_of_another_camera_is_left_alone(self):
        config = _MockConfig(_tree())
        migrate_roi_config(config)
        assert "roi" not in config.tree["inference"]["pipelines"]["pipeline_2"]

    def test_a_pipeline_without_a_camera_list_looks_at_all_of_them(self):
        config = _MockConfig(_tree(pipelines={"pipeline_1": {"cameras": []}}))
        migrate_roi_config(config)
        assert _roi(config) == _CAMERA_ROI

    def test_a_disabled_pipeline_gets_it_too(self):
        """Apagado hoy, prendido mañana: no puede volver a andar con otro recorte."""
        config = _MockConfig(_tree(pipelines={
            "pipeline_1": {"enabled": False, "cameras": ["camera_1"]}}))
        migrate_roi_config(config)
        assert _roi(config) == _CAMERA_ROI

    def test_a_pipeline_with_its_own_roi_keeps_it(self):
        own = {"enabled": True, "x_px": 1, "y_px": 2, "width_px": 3, "height_px": 4}
        config = _MockConfig(_tree(pipelines={
            "pipeline_1": {"cameras": ["camera_1"], "roi": {"camera_1": own}}}))
        migrate_roi_config(config)
        assert _roi(config) == own

    def test_a_pipeline_that_chose_the_whole_frame_keeps_choosing_it(self):
        config = _MockConfig(_tree(pipelines={
            "pipeline_1": {"cameras": ["camera_1"], "roi": {"camera_1": {"enabled": False}}}}))
        migrate_roi_config(config)
        assert _roi(config) == {"enabled": False}

    def test_a_null_own_roi_is_not_a_decision_and_gets_the_camera_one(self):
        config = _MockConfig(_tree(pipelines={
            "pipeline_1": {"cameras": ["camera_1"], "roi": {"camera_1": None}}}))
        migrate_roi_config(config)
        assert _roi(config) == _CAMERA_ROI

    def test_a_disabled_camera_roi_copies_nothing(self):
        config = _MockConfig(_tree(camera={"roi": {"enabled": False, **_GEOMETRY}}))
        migrate_roi_config(config)
        assert _roi(config) is None

    def test_the_copy_has_only_the_geometry_and_enabled(self):
        config = _MockConfig(_tree(camera={"roi": {**_CAMERA_ROI, "junk": 1}}))
        migrate_roi_config(config)
        assert set(_roi(config)) == {"enabled", *_GEOMETRY}


class TestLensHealth:
    def _lens_roi(self, config: _MockConfig, camera_slot: str = "camera_1"):
        return config.tree["cameras"][camera_slot]["lens_health"].get("roi")

    def test_the_camera_roi_becomes_the_measurement_region_of_the_optics(self):
        config = _MockConfig(_tree())
        migrate_roi_config(config)
        assert self._lens_roi(config) == _CAMERA_ROI

    def test_a_region_already_set_is_kept(self):
        own = {"enabled": True, "x_px": 5, "y_px": 5, "width_px": 9, "height_px": 9}
        config = _MockConfig(_tree(camera={"lens_health": {"roi": own}}))
        migrate_roi_config(config)
        assert self._lens_roi(config) == own

    def test_a_disabled_region_does_not_stop_the_copy(self):
        config = _MockConfig(_tree(camera={"lens_health": {"roi": {"enabled": False}}}))
        migrate_roi_config(config)
        assert self._lens_roi(config) == _CAMERA_ROI

    def test_the_reference_is_untouched(self):
        config = _MockConfig(_tree())
        migrate_roi_config(config)
        assert config.tree["cameras"]["camera_1"]["lens_health"]["reference"] == {
            "variance": 0.0}

    def test_a_camera_without_a_lens_section_gets_one(self):
        tree = _tree()
        del tree["cameras"]["camera_1"]["lens_health"]
        config = _MockConfig(tree)
        migrate_roi_config(config)
        assert self._lens_roi(config) == _CAMERA_ROI


class TestOldKeys:
    @pytest.mark.parametrize("roi", [_CAMERA_ROI, {"enabled": False}, None])
    def test_the_camera_roi_is_deleted_whatever_it_was(self, roi):
        config = _MockConfig(_tree(camera={"roi": roi}))
        migrate_roi_config(config)
        assert "roi" not in config.tree["cameras"]["camera_1"]

    def test_crop_to_roi_is_deleted(self):
        config = _MockConfig(_tree(overlay={"crop_to_roi": False, "draw_roi": True}))
        migrate_roi_config(config)
        assert config.tree["inference"]["overlay"] == {"draw_roi": True}

    def test_crop_to_roi_turned_on_is_warned(self, caplog):
        config = _MockConfig(_tree(overlay={"crop_to_roi": True}))
        migrate_roi_config(config)
        assert "crop_to_roi" in caplog.text

    def test_crop_to_roi_turned_off_does_not_warn(self, caplog):
        config = _MockConfig(_tree(overlay={"crop_to_roi": False}))
        migrate_roi_config(config)
        assert "crop_to_roi" not in caplog.text

    def test_each_copy_is_in_the_log(self, caplog):
        migrate_roi_config(_MockConfig(_tree()))
        assert "pipeline_1" in caplog.text and "óptica" in caplog.text


class TestWhenItRuns:
    def test_it_returns_how_many_keys_it_touched(self):
        assert migrate_roi_config(_MockConfig(_tree())) > 0

    def test_it_is_idempotent(self):
        config = _MockConfig(_tree())
        migrate_roi_config(config)
        before = copy.deepcopy(config.tree)
        assert migrate_roi_config(config) == 0
        assert config.tree == before

    def test_it_saves_once_when_something_changed(self):
        config = _MockConfig(_tree())
        migrate_roi_config(config)
        assert config.saves == 1

    def test_it_does_not_rewrite_the_file_when_there_is_nothing_to_migrate(self):
        tree = _tree()
        del tree["cameras"]["camera_1"]["roi"]
        config = _MockConfig(tree)
        assert migrate_roi_config(config) == 0
        assert config.saves == 0

    def test_without_save_it_changes_memory_only(self):
        config = _MockConfig(_tree())
        migrate_roi_config(config, save=False)
        assert config.saves == 0
        assert _roi(config) == _CAMERA_ROI

    def test_a_config_without_cameras_or_pipelines_is_fine(self):
        assert migrate_roi_config(_MockConfig({})) == 0

    def test_a_malformed_camera_entry_is_skipped(self):
        config = _MockConfig({"cameras": {"camera_1": None, "camera_2": "x"}})
        assert migrate_roi_config(config) == 0


def _with_pipeline_roi(**roi) -> _MockConfig:
    """Un config con un ROI prendido en `pipeline_1`, con esas claves de más, y sin ROI de cámara."""
    tree = _tree(pipelines={
        "pipeline_1": {"cameras": ["camera_1"], "roi": {"camera_1": {
            "enabled": True, **_GEOMETRY, **roi}}}})
    del tree["cameras"]["camera_1"]["roi"]
    return _MockConfig(tree)


class TestFullResolution:
    """El corte del video nativo ya no se elige: la clave se borra del ROI de cada pipeline."""

    def _tree_with(self, **roi) -> _MockConfig:
        return _with_pipeline_roi(**roi)

    def test_the_key_is_deleted_when_it_was_on(self):
        config = self._tree_with(full_resolution=True)
        migrate_roi_config(config)
        assert "full_resolution" not in _roi(config)

    def test_the_key_is_deleted_when_it_was_off_and_the_log_says_it_no_longer_counts(self, caplog):
        config = self._tree_with(full_resolution=False)
        migrate_roi_config(config)
        assert "full_resolution" not in _roi(config)
        assert "full_resolution: false" in caplog.text

    def test_a_key_that_was_on_does_not_warn(self, caplog):
        migrate_roi_config(self._tree_with(full_resolution=True))
        assert "full_resolution: false" not in caplog.text

    def test_the_rest_of_the_roi_stays(self):
        config = self._tree_with(full_resolution=True)
        migrate_roi_config(config)
        assert _roi(config) == {"enabled": True, **_GEOMETRY}

    def test_it_counts_as_a_change_and_saves(self):
        config = self._tree_with(full_resolution=True)
        assert migrate_roi_config(config) > 0 and config.saves == 1

    def test_a_roi_without_the_key_is_left_alone(self):
        config = self._tree_with()
        before = copy.deepcopy(config.tree)
        migrate_roi_config(config)
        assert config.tree == before and config.saves == 0

    def test_a_null_or_odd_roi_is_skipped(self):
        config = _MockConfig(_tree(camera={"roi": None}, pipelines={
            "pipeline_1": {"roi": {"camera_1": None, "camera_2": "x"}}}))
        migrate_roi_config(config)

    def test_it_is_idempotent(self):
        config = self._tree_with(full_resolution=True)
        migrate_roi_config(config)
        assert migrate_roi_config(config) == 0


class TestOutputShortSide:
    """La vista sale siempre a la entrada del modelo: el lado corto se borra del ROI."""

    def _tree_with(self, **roi) -> _MockConfig:
        return _with_pipeline_roi(**roi)

    def test_the_key_is_deleted(self):
        config = self._tree_with(output_short_side_px=0)
        migrate_roi_config(config)
        assert _roi(config) == {"enabled": True, **_GEOMETRY}

    def test_a_side_that_was_set_warns_it_no_longer_counts(self, caplog):
        migrate_roi_config(self._tree_with(output_short_side_px=224))
        assert "output_short_side_px: 224" in caplog.text

    def test_a_zero_side_does_not_warn(self, caplog):
        migrate_roi_config(self._tree_with(output_short_side_px=0))
        assert "output_short_side_px" not in caplog.text

    def test_it_counts_as_a_change_and_saves(self):
        config = self._tree_with(output_short_side_px=0)
        assert migrate_roi_config(config) > 0 and config.saves == 1
