"""
Tests del preprocessor del motor: la cadena que arma el frame de referencia.

Los dos builders se reemplazan por dobles que dejan rastro: lo que se prueba es la
composición —qué entra, en qué orden y cuándo no hay nada—, no la corrección ni el ajuste
de brillo, que tienen sus propios tests. Un caso va sin dobles, para ver que la clave del
config llega de verdad al builder.
"""

import numpy as np

from system.inference import preprocessing
from system.inference.preprocessing import build_camera_views, build_preprocessor


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
    roi = {"enabled": True, "full_resolution": True, "x_px": 10, "y_px": 20,
           "width_px": 30, "height_px": 40, "output_short_side_px": 224}
    roi.update(overrides)
    return roi


class TestCameraViews:
    """Qué vistas corta cada cámara: los ROI con `full_resolution` de los que corren."""

    def test_a_full_resolution_roi_is_a_view_named_after_its_pipeline(self):
        config = _MockConfig(**{"inference.pipelines.pipeline_1": {"roi": {"camera_3": _roi()}}})
        assert build_camera_views(config, ["pipeline_1"]) == {"camera_3": {"pipeline_1": {
            "x_px": 10, "y_px": 20, "width_px": 30, "height_px": 40,
            "output_short_side_px": 224}}}

    def test_without_full_resolution_there_is_no_view(self):
        config = _MockConfig(**{"inference.pipelines.pipeline_1": {"roi": {
            "camera_3": _roi(full_resolution=False)}}})
        assert build_camera_views(config, ["pipeline_1"]) == {}

    def test_a_disabled_roi_has_no_view(self):
        config = _MockConfig(**{"inference.pipelines.pipeline_1": {"roi": {
            "camera_3": _roi(enabled=False)}}})
        assert build_camera_views(config, ["pipeline_1"]) == {}

    def test_a_pipeline_that_does_not_run_cuts_nothing(self):
        """Una vista que nadie lee es VIC y copias de más."""
        config = _MockConfig(**{"inference.pipelines.pipeline_1": {"roi": {"camera_3": _roi()}}})
        assert build_camera_views(config, ["pipeline_2"]) == {}

    def test_a_camera_outside_the_pipeline_cuts_nothing(self):
        config = _MockConfig(**{"inference.pipelines.pipeline_1": {
            "roi": {"camera_3": _roi()}, "cameras": ["camera_4"]}})
        assert build_camera_views(config, ["pipeline_1"]) == {}

    def test_two_pipelines_on_a_camera_are_two_views(self):
        config = _MockConfig(**{"inference.pipelines.pipeline_1": {"roi": {"camera_3": _roi()}},
                                "inference.pipelines.pipeline_2": {"roi": {"camera_3": _roi()}}})
        views = build_camera_views(config, ["pipeline_1", "pipeline_2"])
        assert sorted(views["camera_3"]) == ["pipeline_1", "pipeline_2"]

    def test_a_camera_with_an_image_adjustment_cuts_nothing(self, caplog):
        """La vista no pasa por el preprocessor: mediría otra imagen que el frame."""
        config = _MockConfig(**{
            "inference.pipelines.pipeline_1": {"roi": {"camera_3": _roi()}},
            "cameras.camera_3.image_adjust": {"brightness_factor": 2.0},
        })
        assert build_camera_views(config, ["pipeline_1"]) == {}
        assert "full_resolution" in caplog.text
