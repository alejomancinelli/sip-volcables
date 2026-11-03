"""
Tests del armado del config de un equipo: qué se saca, qué cámaras ve cada pipeline, qué
se avisa y qué se rechaza.

Cada test arma su config base en memoria, con la forma del proyecto: calzas en camera_1/2,
grano y personas en camera_3/4, y una camera_5 que ningún pipeline declara. `setup/` no es
un paquete importable, así que el módulo se carga por ruta.
"""

import importlib.util
import os
import sys

import pytest
import yaml

_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), os.pardir,
    "setup", "device-config", "device_config.py")


@pytest.fixture
def device_config(monkeypatch):
    spec = importlib.util.spec_from_file_location("device_config", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # Los dataclass buscan su módulo en sys.modules mientras se arman.
    monkeypatch.setitem(sys.modules, "device_config", module)
    spec.loader.exec_module(module)
    return module


def _base() -> dict:
    return {
        "system": {"device_id": "DEVICE_ID"},
        "process": {
            "stopper_check": {"raised_y_px": {"camera_1": 725, "camera_2": 685}},
            "max_load_y_px": {"camera_3": 100},
        },
        "inference": {
            "pipelines": {
                "pipeline_1": {"type": "grain_flow", "cameras": ["camera_3", "camera_4"],
                               "roi": {"camera_3": {"x_px": 1}, "camera_4": {"x_px": 2}}},
                "pipeline_2": {"type": "person_safety", "cameras": ["camera_3", "camera_4"]},
                "pipeline_3": {"type": "stopper_check", "cameras": ["camera_1", "camera_2"]},
            },
            "models": {"grain_cls": {}, "person_det": {}, "stopper_seg": {}},
        },
        "image_collector": {"cameras": [], "pipelines": ["pipeline_1"]},
        "cameras": {slot: {"name": slot.upper()} for slot in
                    ("camera_1", "camera_2", "camera_3", "camera_4", "camera_5")},
    }


def _pipelines(device) -> dict:
    return {slot: section.get("cameras") for slot, section
            in device.config["inference"]["pipelines"].items()}


class TestCameras:
    def test_cameras_left_out_disappear_from_every_place_that_names_them(self, device_config):
        device = device_config.build_device_config(_base(), cameras=["camera_3"])
        config = device.config
        assert list(config["cameras"]) == ["camera_3"]
        assert config["inference"]["pipelines"]["pipeline_1"]["cameras"] == ["camera_3"]
        assert list(config["inference"]["pipelines"]["pipeline_1"]["roi"]) == ["camera_3"]
        assert config["process"]["stopper_check"]["raised_y_px"] == {}

    def test_a_camera_no_pipeline_sees_is_reported(self, device_config):
        device = device_config.build_device_config(_base(), cameras=["camera_3", "camera_5"])
        assert device.idle_cameras == ["camera_5"]

    def test_an_unknown_camera_is_rejected(self, device_config):
        with pytest.raises(ValueError, match="camera_9"):
            device_config.build_device_config(_base(), cameras=["camera_9"])

    def test_an_empty_camera_selection_is_rejected(self, device_config):
        with pytest.raises(ValueError, match="empty"):
            device_config.build_device_config(_base(), cameras=[])


class TestPipelines:
    def test_without_a_map_each_pipeline_keeps_its_declared_cameras(self, device_config):
        device = device_config.build_device_config(_base(), cameras=["camera_3", "camera_4"])
        assert _pipelines(device) == {"pipeline_1": ["camera_3", "camera_4"],
                                      "pipeline_2": ["camera_3", "camera_4"]}
        assert device.dropped_pipelines == ["pipeline_3"]

    def test_a_pipeline_can_skip_one_of_its_cameras(self, device_config):
        device = device_config.build_device_config(
            _base(), cameras=["camera_3", "camera_4"],
            pipelines={"pipeline_1": ["camera_4"], "pipeline_2": None})
        assert _pipelines(device)["pipeline_1"] == ["camera_4"]
        assert list(device.config["inference"]["pipelines"]["pipeline_1"]["roi"]) == [
            "camera_4"]

    def test_a_pipeline_can_run_on_a_camera_it_does_not_declare(self, device_config):
        device = device_config.build_device_config(
            _base(), cameras=["camera_3", "camera_5"],
            pipelines={"pipeline_1": None, "pipeline_2": ["camera_5"]})
        assert _pipelines(device) == {"pipeline_1": ["camera_3"], "pipeline_2": ["camera_5"]}

    def test_a_pipeline_left_out_is_removed_from_every_list_that_names_it(self, device_config):
        device = device_config.build_device_config(_base(), pipelines={"pipeline_2": None})
        assert list(device.config["inference"]["pipelines"]) == ["pipeline_2"]
        assert device.config["image_collector"]["pipelines"] == []
        assert device.emptied_lists == ["image_collector.pipelines"]

    def test_a_pipeline_given_no_cameras_is_removed(self, device_config):
        device = device_config.build_device_config(
            _base(), pipelines={"pipeline_1": [], "pipeline_2": None})
        assert list(device.config["inference"]["pipelines"]) == ["pipeline_2"]

    def test_a_pipeline_without_declared_cameras_keeps_seeing_them_all(self, device_config):
        base = _base()
        base["inference"]["pipelines"]["pipeline_2"]["cameras"] = []
        device = device_config.build_device_config(base, cameras=["camera_1"])
        assert _pipelines(device)["pipeline_2"] == []
        assert device.idle_cameras == []

    def test_a_camera_the_device_does_not_have_is_rejected(self, device_config):
        with pytest.raises(ValueError, match="camera_4"):
            device_config.build_device_config(
                _base(), cameras=["camera_3"], pipelines={"pipeline_2": ["camera_4"]})

    def test_a_selected_pipeline_without_its_cameras_is_rejected(self, device_config):
        with pytest.raises(ValueError, match="pipeline_3"):
            device_config.build_device_config(
                _base(), cameras=["camera_3"], pipelines={"pipeline_3": None})

    def test_an_unknown_pipeline_is_rejected(self, device_config):
        with pytest.raises(ValueError, match="pipeline_9"):
            device_config.build_device_config(_base(), pipelines={"pipeline_9": None})


class TestKept:
    def test_models_are_kept(self, device_config):
        device = device_config.build_device_config(_base(), cameras=["camera_3"])
        assert list(device.config["inference"]["models"]) == [
            "grain_cls", "person_det", "stopper_seg"]

    def test_a_list_that_was_already_empty_is_not_reported(self, device_config):
        device = device_config.build_device_config(_base(), cameras=["camera_3"])
        assert "image_collector.cameras" not in device.emptied_lists

    def test_the_base_is_not_modified(self, device_config):
        base = _base()
        device_config.build_device_config(
            base, cameras=["camera_3"], pipelines={"pipeline_1": ["camera_3"]})
        assert base == _base()


class TestOverrides:
    def test_an_existing_value_is_replaced_and_parsed_as_yaml(self, device_config):
        config = _base()
        device_config.apply_overrides(config, [
            "system.device_id=VOLC-02", "process.max_load_y_px.camera_3=250"])
        assert config["system"]["device_id"] == "VOLC-02"
        assert config["process"]["max_load_y_px"]["camera_3"] == 250

    def test_a_key_that_does_not_exist_is_rejected(self, device_config):
        with pytest.raises(ValueError, match="system.devic_id"):
            device_config.apply_overrides(_base(), ["system.devic_id=X"])

    def test_an_assignment_without_equals_is_rejected(self, device_config):
        with pytest.raises(ValueError, match="key=value"):
            device_config.apply_overrides(_base(), ["system.device_id"])


def _scripted(answers: list[str]):
    """Un `ask` que devuelve las respuestas en orden; quedarse sin ellas es un error."""
    pending = iter(answers)
    return lambda prompt: next(pending)


class TestWizard:
    def test_each_pipeline_is_asked_over_the_cameras_of_the_device(self, device_config,
                                                                   tmp_path):
        output_path = str(tmp_path / "device.yaml")
        # Cámaras 3, 4 y 5 -> en el equipo son 1, 2 y 3. Grano en la 4; personas sólo en
        # la 5, que no declara; calzas no corre.
        ask = _scripted(["3 4 5", "2", "3", "0", "VOLC-02", output_path, ""])

        device, path = device_config.run_wizard(_base(), ask)

        assert path == output_path
        assert _pipelines(device) == {"pipeline_1": ["camera_4"], "pipeline_2": ["camera_5"]}
        assert device.config["system"]["device_id"] == "VOLC-02"

    def test_enter_takes_the_declared_cameras_of_each_pipeline(self, device_config, tmp_path):
        ask = _scripted(["3 4", "", "", "", "", str(tmp_path / "device.yaml"), ""])

        device, _ = device_config.run_wizard(_base(), ask)

        assert _pipelines(device) == {"pipeline_1": ["camera_3", "camera_4"],
                                      "pipeline_2": ["camera_3", "camera_4"]}
        assert device.config["system"]["device_id"] == "DEVICE_ID"

    def test_the_default_output_is_named_after_the_device(self, device_config, tmp_path,
                                                          monkeypatch):
        monkeypatch.setattr(device_config, "DEFAULT_OUTPUT_DIR", str(tmp_path))
        ask = _scripted(["", "", "", "", "VOLC-02", "", ""])

        _, path = device_config.run_wizard(_base(), ask)

        assert path == str(tmp_path / "VOLC-02" / "config.yaml")

    def test_an_invalid_answer_is_asked_again(self, device_config, tmp_path):
        # 0 no vale para las cámaras: un equipo tiene al menos una.
        ask = _scripted(["9", "0", "3", "", "", "", "", str(tmp_path / "device.yaml"), ""])

        device, _ = device_config.run_wizard(_base(), ask)

        assert list(device.config["cameras"]) == ["camera_3"]

    def test_declining_returns_nothing(self, device_config, tmp_path):
        ask = _scripted(["", "", "", "", "", str(tmp_path / "device.yaml"), "n"])
        assert device_config.run_wizard(_base(), ask) is None

    def test_an_existing_file_is_kept_unless_confirmed(self, device_config, tmp_path):
        output_path = tmp_path / "device.yaml"
        output_path.write_text("kept", encoding="utf-8")
        ask = _scripted(["", "", "", "", "", str(output_path), ""])
        assert device_config.run_wizard(_base(), ask) is None


class TestMain:
    def _write_base(self, tmp_path) -> str:
        base_path = tmp_path / "base.yaml"
        base_path.write_text(yaml.safe_dump(_base()), encoding="utf-8")
        return str(base_path)

    def test_without_arguments_it_guides_and_writes(self, device_config, tmp_path):
        output_path = tmp_path / "out" / "device.yaml"
        ask = _scripted(["1 2", "", "", "", "VOLC-03", str(output_path), "y"])

        exit_code = device_config.main(["--base", self._write_base(tmp_path)], ask)

        assert exit_code == device_config.EXIT_OK
        written = yaml.safe_load(output_path.read_text(encoding="utf-8"))
        assert list(written["inference"]["pipelines"]) == ["pipeline_3"]

    def test_a_cancel_writes_nothing(self, device_config, tmp_path):
        base_path = self._write_base(tmp_path)

        def interrupt(prompt: str) -> str:
            raise KeyboardInterrupt

        exit_code = device_config.main(["--base", base_path], interrupt)

        assert exit_code == device_config.EXIT_CANCELLED
        assert [str(path) for path in tmp_path.iterdir()] == [base_path]

    def test_the_arguments_choose_the_cameras_of_each_pipeline(self, device_config, tmp_path):
        output_path = tmp_path / "device.yaml"

        exit_code = device_config.main([
            "--base", self._write_base(tmp_path), "--cameras", "camera_3", "camera_4",
            "--pipeline", "pipeline_1", "--pipeline", "pipeline_2=camera_4",
            "--set", "system.device_id=VOLC-02", "--output", str(output_path)])

        assert exit_code == device_config.EXIT_OK
        written = yaml.safe_load(output_path.read_text(encoding="utf-8"))
        assert written["inference"]["pipelines"]["pipeline_1"]["cameras"] == [
            "camera_3", "camera_4"]
        assert written["inference"]["pipelines"]["pipeline_2"]["cameras"] == ["camera_4"]
        assert written["system"]["device_id"] == "VOLC-02"

    def test_an_existing_output_is_not_overwritten_without_force(self, device_config, tmp_path):
        output_path = tmp_path / "device.yaml"
        output_path.write_text("kept", encoding="utf-8")

        exit_code = device_config.main(
            ["--base", self._write_base(tmp_path), "--output", str(output_path)])

        assert exit_code == device_config.EXIT_INVALID
        assert output_path.read_text(encoding="utf-8") == "kept"
