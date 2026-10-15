"""Tests de la sonda de aceleradores: los pipelines de prueba, cómo se juzga el resultado
de cada uno y qué pasa sin GStreamer.

GStreamer es un doble con lo justo que usa el módulo —`parse_launch`, el bus y los
cambios de estado—, así que se prueba en Windows, donde `gi` no existe."""

import pytest

from tools.jetson import capabilities
from tools.jetson.capabilities import (build_pipeline_checks, check_pipeline,
                                       find_elements, parse_build_information,
                                       probe_python_libs)


class _FakeGLib:
    class Error(Exception):
        pass


class _GstError:
    def __init__(self, message: str):
        self.message = message


class _Message:
    def __init__(self, message_type: int, text: str = ""):
        self.type = message_type
        self._text = text

    def parse_error(self) -> tuple:
        return _GstError(self._text), "debug"


class _Bus:
    def __init__(self, gst: "_FakeGst"):
        self._gst = gst

    def timed_pop_filtered(self, timeout_ns: int, _types: int) -> _Message | None:
        self._gst.timeouts_ns.append(timeout_ns)
        return self._gst.message

    def pop_filtered(self, _types: int) -> _Message | None:
        return self._gst.message


class _Pipeline:
    def __init__(self, gst: "_FakeGst"):
        self._gst = gst

    def set_state(self, state: str) -> str:
        self._gst.states.append(state)
        return self._gst.play_return if state == _FakeGst.State.PLAYING else "success"

    def get_bus(self) -> _Bus:
        return _Bus(self._gst)


class _FakeGst:
    """El `Gst` de PyGObject, reducido a lo que usa la sonda."""

    class State:
        PLAYING = "playing"
        NULL = "null"

    class StateChangeReturn:
        FAILURE = "failure"

    class MessageType:
        ERROR = 1
        EOS = 2

    def __init__(self, *, message: _Message | None = None, launch_error: str = "",
                 play_return: str = "async", installed: tuple[str, ...] = ()):
        self.message = message
        self.launch_error = launch_error
        self.play_return = play_return
        self.installed = installed
        self.states = []
        self.timeouts_ns = []
        self.launches = []
        self.ElementFactory = self

    def init(self, _argv: object):
        pass

    def find(self, name: str) -> object | None:
        return object() if name in self.installed else None

    def parse_launch(self, launch: str) -> _Pipeline:
        self.launches.append(launch)
        if self.launch_error:
            raise _FakeGLib.Error(self.launch_error)
        return _Pipeline(self)


@pytest.fixture
def gst(monkeypatch):
    """Instala un `_FakeGst` en el módulo; el test elige qué contesta el bus."""
    def install(**kwargs) -> _FakeGst:
        fake = _FakeGst(**kwargs)
        monkeypatch.setattr(capabilities, "_GSTREAMER_AVAILABLE", True)
        monkeypatch.setattr(capabilities, "Gst", fake, raising=False)
        monkeypatch.setattr(capabilities, "GLib", _FakeGLib, raising=False)
        return fake

    return install


@pytest.fixture
def no_gstreamer(monkeypatch):
    monkeypatch.setattr(capabilities, "_GSTREAMER_AVAILABLE", False)
    monkeypatch.setattr(capabilities, "_IMPORT_ERROR", "No module named 'gi'")


class TestPipelineChecks:
    def test_there_is_one_check_per_engine(self):
        assert set(build_pipeline_checks()) == {
            "vic", "nvenc_h264", "nvenc_h265", "nvdec_h264", "nvjpg_encode", "nvjpg_decode"}

    @pytest.mark.parametrize("engine, element", [
        ("vic", "nvvidconv"),
        ("nvenc_h264", "nvv4l2h264enc"),
        ("nvenc_h265", "nvv4l2h265enc"),
        ("nvdec_h264", "nvv4l2decoder"),
        ("nvjpg_encode", "nvjpegenc"),
        ("nvjpg_decode", "nvjpegdec"),
    ])
    def test_each_check_goes_through_its_engine(self, engine: str, element: str):
        assert f"! {element}" in build_pipeline_checks()[engine]

    def test_size_and_frame_count_are_the_ones_asked(self):
        launch = build_pipeline_checks(640, 360, 90)["nvenc_h264"]
        assert "num-buffers=90" in launch
        assert "width=640,height=360" in launch

    def test_the_vic_halves_to_even_sides(self):
        """NV12 no acepta un lado impar: 1282 / 2 = 641 tiene que salir 640."""
        assert "width=640,height=360" in build_pipeline_checks(1282, 722)["vic"]


class TestCheckPipeline:
    def test_reaching_eos_is_a_pass(self, gst):
        gst(message=_Message(_FakeGst.MessageType.EOS))
        assert check_pipeline("videotestsrc ! fakesink") == ""

    def test_an_error_on_the_bus_is_its_message(self, gst):
        gst(message=_Message(_FakeGst.MessageType.ERROR, "Could not open device"))
        assert check_pipeline("x") == "Could not open device"

    def test_no_message_before_the_timeout_says_so(self, gst):
        fake = gst(message=None)
        assert "sin EOS" in check_pipeline("x", timeout_s=1.5)
        assert fake.timeouts_ns == [1_500_000_000]

    def test_a_missing_element_is_the_parse_error(self, gst):
        gst(launch_error="no element \"nvjpegenc\"")
        assert "nvjpegenc" in check_pipeline("videotestsrc ! nvjpegenc ! fakesink")

    def test_a_failed_start_reports_the_error_it_left(self, gst):
        gst(play_return=_FakeGst.StateChangeReturn.FAILURE,
            message=_Message(_FakeGst.MessageType.ERROR, "NvMMLite open failed"))
        assert check_pipeline("x") == "NvMMLite open failed"

    def test_the_pipeline_is_always_torn_down(self, gst):
        fake = gst(message=_Message(_FakeGst.MessageType.ERROR, "boom"))
        check_pipeline("x")
        assert fake.states[-1] == _FakeGst.State.NULL


class TestWithoutGstreamer:
    def test_no_element_is_found(self, no_gstreamer):
        assert find_elements(("nvvidconv", "appsink")) == {"nvvidconv": False, "appsink": False}

    def test_a_check_returns_the_import_reason(self, no_gstreamer):
        assert "gi" in check_pipeline("videotestsrc ! fakesink")


def test_find_elements_asks_the_registry(gst):
    gst(installed=("nvvidconv",))
    assert find_elements(("nvvidconv", "nvjpegenc")) == {"nvvidconv": True, "nvjpegenc": False}


class TestPythonLibs:
    def test_opencv_build_flags_are_read_from_its_build_information(self):
        build_info = (
            "  Video I/O:\n"
            "    FFMPEG:                      YES\n"
            "    GStreamer:                   NO\n"
            "  NVIDIA CUDA:                   NO\n"
        )
        assert parse_build_information(build_info) == {"gstreamer": "NO", "cuda": "NO"}

    def test_a_flag_that_is_not_listed_is_empty(self):
        assert parse_build_information("") == {"gstreamer": "", "cuda": ""}

    def test_every_library_answers_even_when_missing(self):
        """En la PC de desarrollo VPI y TensorRT no están: su motivo va en `error`."""
        libs = probe_python_libs()
        assert set(libs) == {"opencv", "vpi", "tensorrt"}
        assert libs["opencv"]["version"]
        for name in ("vpi", "tensorrt"):
            assert bool(libs[name]["version"]) != bool(libs[name]["error"])
