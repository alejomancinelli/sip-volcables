"""
Tests del preprocessor del motor: la cadena que arma el frame de referencia.

Los dos builders se reemplazan por dobles que dejan rastro: lo que se prueba es la
composición —qué entra, en qué orden y cuándo no hay nada—, no la corrección ni el ajuste
de brillo, que tienen sus propios tests. Un caso va sin dobles, para ver que la clave del
config llega de verdad al builder.
"""

import numpy as np
import pytest

from system.inference import preprocessing
from system.inference.models.abstract_model import InputSpec
from system.inference.preprocessing import (
    build_camera_views, build_preprocessor, can_cut_native, list_view_cameras, loses_native_cut,
)


class _MockConfig:
    """ConfigManager mínimo: `get` con clave punteada, sin archivo ni Lock."""

    def __init__(self, **overrides):
        self._values = dict(overrides)

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


def _with_doubles(monkeypatch, *, with_undistort: bool, with_adjust: bool) -> list:
    calls: list = []

    def undistort(frame, camera_slot):
        calls.append(("undistort", camera_slot))
        return frame + 1

    def adjust(frame, camera_slot):
        calls.append(("adjust", camera_slot))
        return frame * 2

    monkeypatch.setattr(preprocessing, "build_undistorter",
                        lambda calibration: undistort if with_undistort else None)
    monkeypatch.setattr(preprocessing, "build_image_adjust",
                        lambda adjust_by_camera: adjust if with_adjust else None)
    return calls


_ONE_CAMERA = _MockConfig(cameras={"camera_1": {}})


class TestChain:
    def test_with_both_the_geometry_goes_first(self, monkeypatch):
        """El contraste local ecualiza por celdas: tiene que correr sobre el frame corregido."""
        calls = _with_doubles(monkeypatch, with_undistort=True, with_adjust=True)
        frame = build_preprocessor(_ONE_CAMERA)(np.zeros((2, 2), np.int32), "camera_1")
        assert calls == [("undistort", "camera_1"), ("adjust", "camera_1")]
        assert (frame == 2).all()

    def test_with_both_the_name_says_what_runs(self, monkeypatch):
        _with_doubles(monkeypatch, with_undistort=True, with_adjust=True)
        assert build_preprocessor(_ONE_CAMERA).__name__ == "undistort + adjust"

    def test_with_only_the_lens_it_is_the_undistorter_itself(self, monkeypatch):
        """Con uno solo no se envuelve: el motor no paga una llamada de más por frame."""
        _with_doubles(monkeypatch, with_undistort=True, with_adjust=False)
        assert build_preprocessor(_ONE_CAMERA).__name__ == "undistort"

    def test_with_only_the_adjustment_it_is_the_adjustment_itself(self, monkeypatch):
        _with_doubles(monkeypatch, with_undistort=False, with_adjust=True)
        assert build_preprocessor(_ONE_CAMERA).__name__ == "adjust"

    def test_with_neither_there_is_no_preprocessor(self, monkeypatch):
        """Sin nada declarado el motor mide el frame de la cámara y no gasta la pasada."""
        _with_doubles(monkeypatch, with_undistort=False, with_adjust=False)
        assert build_preprocessor(_ONE_CAMERA) is None


class TestConfig:
    def test_the_adjustment_is_read_from_each_camera(self):
        """Sin dobles: la clave del config llega al builder y ajusta sólo esa cámara."""
        config = _MockConfig(**{
            "cameras": {"camera_1": {}, "camera_2": {}},
            "cameras.camera_1.image_adjust": {"brightness_factor": 2.0, "clahe_clip": 0.0},
        })
        preprocessor = build_preprocessor(config)
        frame = np.full((4, 4, 3), 60, np.uint8)
        assert preprocessor(frame, "camera_1").mean() > 60
        assert preprocessor(frame, "camera_2") is frame

    def test_without_cameras_there_is_no_preprocessor(self):
        assert build_preprocessor(_MockConfig()) is None


def _roi(**overrides) -> dict:
    roi = {"enabled": True, "x_px": 10, "y_px": 20, "width_px": 30, "height_px": 40}
    roi.update(overrides)
    return roi


class _FakePipeline:
    """Un pipeline que sólo sabe de qué tamaño quiere la región, que es lo que se le pregunta."""

    def __init__(self, spec: InputSpec | None = None):
        self._spec = spec

    def peek_view_input(self) -> InputSpec | None:
        return self._spec


_RTSP = {"driver": "rtsp"}


def _views_config(*, camera: dict | None = None, **pipelines) -> _MockConfig:
    """Un config con esos pipelines y la cámara `camera_3`, por defecto de video."""
    values = {f"inference.pipelines.{slot}": section for slot, section in pipelines.items()}
    camera = _RTSP if camera is None else camera
    values["cameras.camera_3"] = camera
    # El doble resuelve claves punteadas planas: lo de adentro de la cámara va también suelto.
    values.update({f"cameras.camera_3.{key}": value for key, value in camera.items()})
    return _MockConfig(**values)


def _own(**overrides) -> dict:
    return {"roi": {"camera_3": _roi(**overrides)}}


class TestCameraViews:
    """Qué vistas corta cada cámara: el ROI de cada pipeline que corre, donde hay decodificador."""

    def test_a_roi_on_a_video_camera_is_a_view_named_after_its_pipeline(self):
        config = _views_config(pipeline_1=_own())
        views = build_camera_views(config, {"pipeline_1": _FakePipeline()})
        assert views == {"camera_3": {"pipeline_1": {
            "x_px": 10, "y_px": 20, "width_px": 30, "height_px": 40}}}

    def test_no_key_has_to_ask_for_it(self):
        """Antes había que pedirla con `full_resolution`: ahora la decide la cámara."""
        config = _views_config(pipeline_1=_own())
        assert "camera_3" in build_camera_views(config, {"pipeline_1": _FakePipeline()})

    def test_a_disabled_roi_has_no_view(self):
        config = _views_config(pipeline_1=_own(enabled=False))
        assert build_camera_views(config, {"pipeline_1": _FakePipeline()}) == {}

    def test_a_pipeline_that_does_not_run_cuts_nothing(self):
        """Una vista que nadie lee es VIC y copias de más."""
        config = _views_config(pipeline_1=_own())
        assert build_camera_views(config, {"pipeline_2": _FakePipeline()}) == {}

    def test_a_camera_outside_the_pipeline_cuts_nothing(self):
        config = _views_config(pipeline_1={**_own(), "cameras": ["camera_4"]})
        assert build_camera_views(config, {"pipeline_1": _FakePipeline()}) == {}

    def test_two_pipelines_on_a_camera_are_two_views(self):
        config = _views_config(pipeline_1=_own(), pipeline_2=_own())
        views = build_camera_views(config, {"pipeline_1": _FakePipeline(),
                                            "pipeline_2": _FakePipeline()})
        assert sorted(views["camera_3"]) == ["pipeline_1", "pipeline_2"]

    @pytest.mark.parametrize("camera", [{"driver": "basler_gige"}, {"driver": "st_gige"},
                                        {"driver": "mock"}, {}])
    def test_a_camera_that_does_not_decode_video_cuts_no_view(self, camera):
        """Un GigE entrega el frame del sensor: su recorte ya es nativo."""
        config = _views_config(camera=camera, pipeline_1=_own())
        assert build_camera_views(config, {"pipeline_1": _FakePipeline()}) == {}

    def test_a_video_file_camera_cuts_views_too(self):
        config = _views_config(camera={"driver": "video_file"}, pipeline_1=_own())
        assert "camera_3" in build_camera_views(config, {"pipeline_1": _FakePipeline()})

    def test_a_camera_with_an_image_adjustment_cuts_nothing_and_says_why(self, caplog):
        """La vista no pasa por el preprocessor: la región se corta del frame de referencia."""
        config = _views_config(camera={**_RTSP, "image_adjust": {"brightness_factor": 2.0}},
                               pipeline_1=_own())
        assert build_camera_views(config, {"pipeline_1": _FakePipeline()}) == {}
        assert "frame de referencia" in caplog.text

    def test_a_gige_camera_with_an_adjustment_does_not_warn(self, caplog):
        config = _views_config(camera={"driver": "basler_gige",
                                       "image_adjust": {"brightness_factor": 2.0}},
                               pipeline_1=_own())
        build_camera_views(config, {"pipeline_1": _FakePipeline()})
        assert "frame de referencia" not in caplog.text


class TestViewSize:
    """A qué tamaño sale la vista: el que el modelo que la lee usaría, salvo que el ROI diga otro."""

    def _view(self, spec, **roi) -> dict:
        config = _views_config(pipeline_1=_own(**roi))
        return build_camera_views(config, {"pipeline_1": _FakePipeline(spec)})[
            "camera_3"]["pipeline_1"]

    def test_the_size_is_the_input_of_the_model(self):
        view = self._view(InputSpec(224, 224, True))
        assert view["output_box_px"] == [224, 224] and view["output_covers"] is True

    def test_a_detector_fits_and_a_classifier_covers(self):
        assert self._view(InputSpec(384, 640, False))["output_covers"] is False
        assert self._view(InputSpec(224, 224, True))["output_covers"] is True

    def test_a_rectangular_input_is_width_by_height(self):
        assert self._view(InputSpec(384, 640, False))["output_box_px"] == [640, 384]

    def test_a_short_side_in_the_roi_does_not_override_the_model(self):
        view = self._view(InputSpec(224, 224, True), output_short_side_px=300)
        assert view["output_box_px"] == [224, 224] and "output_short_side_px" not in view

    def test_a_model_that_does_not_say_leaves_the_native_size(self):
        view = self._view(None)
        assert "output_box_px" not in view and "output_short_side_px" not in view

    def test_the_model_is_asked_once_per_pipeline(self):
        calls = []

        class _Counting(_FakePipeline):
            def peek_view_input(self):
                calls.append(1)
                return None

        config = _views_config(pipeline_1={"roi": {"camera_3": _roi(), "camera_4": _roi()}})
        config._values["cameras.camera_4"] = _RTSP
        build_camera_views(config, {"pipeline_1": _Counting()})
        assert len(calls) == 1

    def test_a_pipeline_without_views_is_not_asked(self):
        calls = []

        class _Counting(_FakePipeline):
            def peek_view_input(self):
                calls.append(1)

        config = _views_config(camera={"driver": "basler_gige"}, pipeline_1=_own())
        build_camera_views(config, {"pipeline_1": _Counting()})
        assert calls == []


class TestNativeDecisions:
    def test_can_cut_native_needs_a_video_driver_and_no_preprocessor(self):
        assert can_cut_native(_views_config(), "camera_3")
        assert not can_cut_native(_views_config(camera={"driver": "basler_gige"}), "camera_3")
        assert not can_cut_native(
            _views_config(camera={**_RTSP, "image_adjust": {"brightness_factor": 2.0}}),
            "camera_3")

    def test_loses_native_cut_is_a_video_camera_with_a_preprocessor(self):
        adjusted = {**_RTSP, "image_adjust": {"brightness_factor": 2.0}}
        assert loses_native_cut(_views_config(camera=adjusted), "camera_3")
        assert not loses_native_cut(_views_config(), "camera_3")
        assert not loses_native_cut(
            _views_config(camera={"driver": "basler_gige",
                                  "image_adjust": {"brightness_factor": 2.0}}), "camera_3")

    def test_the_cameras_of_a_pipeline_are_the_same_the_engine_expects(self):
        config = _views_config(pipeline_1={"roi": {"camera_3": _roi(), "camera_4": _roi()}})
        config._values["cameras.camera_4"] = {"driver": "basler_gige"}
        assert list_view_cameras(config, "pipeline_1") == ["camera_3"]
