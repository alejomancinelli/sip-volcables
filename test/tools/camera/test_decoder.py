"""Tests del decodificador de los drivers de video: qué backend se elige con qué config,
que los dos entreguen el mismo tamaño, cómo se arman los pipelines de la Jetson y qué pasa
sin GStreamer.

NVDEC no se ejercita acá: necesita la Jetson. Se prueban las piezas puras del camino —los
pipelines, las credenciales, la conversión de BGRx— y el camino por CPU con un doble de
`cv2.VideoCapture`. Lo de hardware está en `manual_test/jetson_accelerators/`."""

import logging

import numpy as np
import pytest

from tools.camera import decoder
from tools.camera.decoder import (BACKEND_CPU, BACKEND_NVDEC, DecodeSettings,
                                  bgrx_to_bgr, build_file_launch, build_stream_launch,
                                  read_decode_settings, repeat_parameter_sets,
                                  split_credentials)


def _logged(caplog: pytest.LogCaptureFixture, text: str) -> int:
    return sum(1 for record in caplog.records if text in record.getMessage())


@pytest.fixture
def nvdec_available(monkeypatch):
    monkeypatch.setattr(decoder, "_nvdec_reason", "")


@pytest.fixture
def nvdec_missing(monkeypatch):
    monkeypatch.setattr(decoder, "_nvdec_reason", "faltan los elementos nvv4l2decoder")


class _FakeVideoCapture:
    """`cv2.VideoCapture` que entrega siempre el mismo frame y guarda cómo se lo abrió."""

    def __init__(self, *args, is_opened: bool = True, fps: float = 10.0):
        self.args = args
        self._is_opened = is_opened
        self._fps = fps
        self.frame = np.full((1080, 1920, 3), 90, np.uint8)
        self.properties = {}
        self.is_released = False

    def isOpened(self) -> bool:  # noqa: N802 — firma de OpenCV
        return self._is_opened

    def grab(self) -> bool:
        return True

    def retrieve(self) -> tuple:
        return True, self.frame

    def read(self) -> tuple:
        return True, self.frame

    def set(self, prop: int, value: float) -> bool:
        self.properties[prop] = value
        return True

    def get(self, _prop: int) -> float:
        return self._fps

    def release(self):
        self.is_released = True


@pytest.fixture
def fake_cv2(monkeypatch):
    opened = []

    def install(**kwargs):
        def open_capture(*args):
            opened.append(_FakeVideoCapture(*args, **kwargs))
            return opened[-1]

        monkeypatch.setattr(decoder.cv2, "VideoCapture", open_capture)
        return opened

    return install


class TestReadDecodeSettings:
    def test_without_a_section_it_is_the_cpu_at_native_size(self):
        assert read_decode_settings({}) == DecodeSettings(BACKEND_CPU, 0, 0)

    def test_the_section_is_read(self, nvdec_available):
        settings = read_decode_settings(
            {"decode": {"backend": "nvdec", "width_px": 640, "height_px": 360}})
        assert settings == DecodeSettings(BACKEND_NVDEC, 640, 360)
        assert settings.size_px == (640, 360)

    def test_auto_picks_nvdec_where_there_is_one(self, nvdec_available):
        assert read_decode_settings({"decode": {"backend": "auto"}}).backend == BACKEND_NVDEC

    def test_auto_picks_the_cpu_where_there_is_none(self, nvdec_missing):
        assert read_decode_settings({"decode": {"backend": "auto"}}).backend == BACKEND_CPU

    def test_nvdec_where_there_is_none_stays_nvdec_and_says_so(self, nvdec_missing, caplog):
        """Una planta que cuenta con el VIC no puede pasar a gastar CPU sin que se note."""
        with caplog.at_level(logging.ERROR):
            settings = read_decode_settings({"decode": {"backend": "nvdec"}}, "camera_1")
        assert settings.backend == BACKEND_NVDEC
        assert _logged(caplog, "no va a abrir")

    def test_an_unknown_backend_falls_back_to_the_cpu_and_warns(self, caplog):
        with caplog.at_level(logging.WARNING):
            settings = read_decode_settings({"decode": {"backend": "vaapi"}})
        assert settings.backend == BACKEND_CPU
        assert _logged(caplog, "'vaapi' desconocido")

    def test_one_side_alone_leaves_the_native_size(self, caplog):
        with caplog.at_level(logging.WARNING):
            settings = read_decode_settings({"decode": {"width_px": 640}})
        assert settings.size_px is None
        assert _logged(caplog, "juntos")

    def test_negative_sizes_are_native(self):
        assert read_decode_settings(
            {"decode": {"width_px": -1, "height_px": -1}}).size_px is None


class TestCpuPath:
    def test_a_stream_opens_with_ffmpeg_and_the_timeouts(self, fake_cv2):
        opened = fake_cv2()
        decoder.open_stream("rtsp://cam/stream", DecodeSettings(BACKEND_CPU, 0, 0),
                            open_timeout_ms=5000, read_timeout_ms=4000)
        url, api_preference, params = opened[0].args
        assert url == "rtsp://cam/stream"
        assert api_preference == decoder.cv2.CAP_FFMPEG
        assert params == [decoder.cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000,
                          decoder.cv2.CAP_PROP_READ_TIMEOUT_MSEC, 4000]
        assert opened[0].properties[decoder.cv2.CAP_PROP_BUFFERSIZE] == 1

    def test_a_stream_that_does_not_open_is_none_and_released(self, fake_cv2):
        opened = fake_cv2(is_opened=False)
        assert decoder.open_stream("rtsp://cam", DecodeSettings(BACKEND_CPU, 0, 0),
                                   open_timeout_ms=1, read_timeout_ms=1) is None
        assert opened[0].is_released

    def test_the_native_size_is_the_frame_as_it_came(self, fake_cv2):
        opened = fake_cv2()
        capture = decoder.open_file("clip.mp4", DecodeSettings(BACKEND_CPU, 0, 0))
        is_read, frame = capture.read()
        assert is_read
        assert frame is opened[0].frame

    @pytest.mark.parametrize("method", ["read", "retrieve"])
    def test_a_size_resizes_on_the_cpu_like_the_vic_would(self, fake_cv2, method):
        """Cambiar de backend no puede cambiar el frame que ve la app."""
        fake_cv2()
        capture = decoder.open_file("clip.mp4", DecodeSettings(BACKEND_CPU, 640, 360))
        is_read, frame = getattr(capture, method)()
        assert is_read
        assert frame.shape == (360, 640, 3)

    def test_seek_and_fps_go_to_opencv(self, fake_cv2):
        opened = fake_cv2(fps=12.5)
        capture = decoder.open_file("clip.mp4", DecodeSettings(BACKEND_CPU, 0, 0))
        assert capture.get_fps() == 12.5
        assert capture.seek(40)
        assert opened[0].properties[decoder.cv2.CAP_PROP_POS_FRAMES] == 40


class TestNvdecPipelines:
    def test_the_stream_goes_through_nvdec_and_the_vic_into_bgrx(self):
        launch = build_stream_launch(DecodeSettings(BACKEND_NVDEC, 640, 360), 5000)
        assert launch.index("rtspsrc") < launch.index("nvv4l2decoder") \
            < launch.index("nvvidconv") < launch.index("appsink")
        assert "video/x-raw,format=BGRx,width=640,height=360" in launch

    def test_the_vic_scales_bilinear(self):
        """El default de nvvidconv es vecino más cercano, que pierde los objetos chicos."""
        launch = build_stream_launch(DecodeSettings(BACKEND_NVDEC, 640, 360), 5000)
        assert f"interpolation-method={decoder._NVVIDCONV_BILINEAR}" in launch

    def test_the_stream_keeps_only_the_newest_frame_over_tcp(self):
        launch = build_stream_launch(DecodeSettings(BACKEND_NVDEC, 0, 0), 5000)
        assert "max-buffers=1 drop=true" in launch
        assert "protocols=tcp" in launch
        assert "tcp-timeout=5000000" in launch

    def test_at_native_size_no_size_is_asked(self):
        assert "width=" not in build_stream_launch(DecodeSettings(BACKEND_NVDEC, 0, 0), 5000)

    def test_a_file_drops_nothing_and_keeps_the_reorder_buffer(self):
        """El driver elige el frame por reloj; una grabación puede traer B-frames."""
        launch = build_file_launch(DecodeSettings(BACKEND_NVDEC, 640, 360))
        assert launch.startswith("filesrc name=source ")
        assert "drop=false" in launch
        assert "disable-dpb" not in launch

    def test_credentials_leave_the_url_unescaped(self):
        assert split_credentials("rtsp://svc%40plant:p%3Ass@10.0.0.5:554/ch1") == (
            "rtsp://10.0.0.5:554/ch1", "svc@plant", "p:ss")

    def test_a_url_without_credentials_stays_as_it_is(self):
        assert split_credentials("rtsp://10.0.0.5/ch1") == ("rtsp://10.0.0.5/ch1", "", "")


class _FakeParser:
    """Un elemento de GStreamer con las propiedades que se le declaren."""

    def __init__(self, properties: dict):
        self.properties = dict(properties)

    def find_property(self, name: str):
        return name if name in self.properties else None

    def set_property(self, name: str, value):
        self.properties[name] = value


class TestRepeatParameterSets:
    def test_a_parser_repeats_the_parameter_sets_on_every_keyframe(self):
        """Sin SPS/PPS en el stream NVDEC descarta todos los frames y la cámara no abre."""
        parser = _FakeParser({"config-interval": 0})
        repeat_parameter_sets(parser)
        assert parser.properties["config-interval"] == -1

    def test_an_element_without_the_property_is_left_alone(self):
        queue = _FakeParser({"max-size-buffers": 200})
        repeat_parameter_sets(queue)
        assert queue.properties == {"max-size-buffers": 200}


class TestBgrxToBgr:
    def test_the_padding_byte_is_dropped(self):
        bgrx = np.zeros((2, 3, 4), np.uint8)
        bgrx[..., 0], bgrx[..., 1], bgrx[..., 2], bgrx[..., 3] = 10, 20, 30, 255
        frame = bgrx_to_bgr(bgrx.tobytes(), 3, 2)
        assert frame.shape == (2, 3, 3)
        assert frame[0, 0].tolist() == [10, 20, 30]

    def test_padding_at_the_end_of_each_row_is_skipped(self):
        """nvvidconv puede alinear las filas: una fila mide más que ancho por cuatro."""
        width_px, height_px, row_bytes = 3, 2, 16
        data = bytearray(row_bytes * height_px)
        for row in range(height_px):
            for col in range(width_px):
                offset = row * row_bytes + col * 4
                data[offset:offset + 4] = bytes([row, col, 7, 255])
        frame = bgrx_to_bgr(bytes(data), width_px, height_px)
        assert frame[1, 2].tolist() == [1, 2, 7]

    def test_the_frame_is_its_own_array(self):
        """El contrato del driver: nunca una vista sobre un buffer que se recicle."""
        frame = bgrx_to_bgr(np.zeros((2, 3, 4), np.uint8).tobytes(), 3, 2)
        assert frame.flags.owndata
        assert frame.flags.writeable


class TestNvdecWithoutGstreamer:
    def test_a_stream_does_not_open(self, nvdec_missing):
        assert decoder.open_stream("rtsp://cam", DecodeSettings(BACKEND_NVDEC, 0, 0),
                                   open_timeout_ms=1, read_timeout_ms=1) is None

    def test_a_file_does_not_open(self, nvdec_missing):
        assert decoder.open_file("clip.mp4", DecodeSettings(BACKEND_NVDEC, 0, 0)) is None

    def test_the_reason_names_what_is_missing(self, monkeypatch):
        monkeypatch.setattr(decoder, "_nvdec_reason", None)
        monkeypatch.setattr(decoder, "_GSTREAMER_AVAILABLE", False)
        monkeypatch.setattr(decoder, "_IMPORT_ERROR", "No module named 'gi'")
        assert "gi" in decoder.get_nvdec_unavailable_reason()
