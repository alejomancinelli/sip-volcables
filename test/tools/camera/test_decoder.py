"""Tests del decodificador de los drivers de video: qué backend se elige con qué config,
que los dos entreguen el mismo tamaño, cómo se arman los pipelines de la Jetson y qué pasa
sin GStreamer.

NVDEC no se ejercita acá: necesita la Jetson. Se prueban las piezas puras del camino —los
pipelines, las credenciales, la conversión de BGRx— y el camino por CPU con un doble de
`cv2.VideoCapture`. Lo de hardware está en `manual_test/jetson_accelerators/`."""

import logging
import types

import numpy as np
import pytest

from tools.camera import decoder
from tools.camera.decoder import (BACKEND_CPU, BACKEND_NVDEC, DecodeSettings, ViewSettings,
                                  bgrx_to_bgr, build_file_launch, build_stream_launch,
                                  compute_view_size, pair_by_pts, plan_view, plan_views,
                                  read_decode_settings, render, repeat_parameter_sets,
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


# ── Vistas ───────────────────────────────────────────────────────────────────

_ROI = (210, 40, 180, 220)       # el ROI de prueba de los hallazgos, en 640x360


def _view(rect_px: tuple = _ROI, output_short_side_px: int = 0) -> ViewSettings:
    return ViewSettings("pipeline_1", rect_px, output_short_side_px)


def _plan(rect_px: tuple = _ROI, *, native_size: tuple = (1920, 1080),
          output_size: tuple = (640, 360), rotation: str | None = None,
          output_short_side_px: int = 0):
    return plan_view(_view(rect_px, output_short_side_px), native_size=native_size,
                     output_size=output_size, rotation=rotation)


class TestPlanView:
    """
    Dónde se corta una vista. Los números son los que se verificaron contra el VIC en la
    Jetson (`decoder-views-jetson-findings.md`, preguntas 2, 9 y 10).
    """

    def test_the_roi_is_scaled_to_the_native_video(self):
        crop = _plan()
        assert (crop.left, crop.top, crop.right, crop.bottom) == (630, 120, 1170, 780)

    def test_each_camera_has_its_own_scale(self):
        """cam_4 es de 2560x1440: el mismo ROI en 640x360 es otro recorte nativo."""
        crop = _plan(native_size=(2560, 1440))
        assert (crop.left, crop.top, crop.right, crop.bottom) == (840, 160, 1560, 1040)

    def test_the_corners_are_widened_to_even_coordinates(self):
        """Con un lado impar el VIC corre el recorte un píxel sin avisar."""
        crop = _plan((211, 41, 181, 221))
        assert (crop.left, crop.top, crop.right, crop.bottom) == (632, 122, 1176, 786)

    def test_the_covered_rectangle_is_in_the_reference_frame(self):
        assert _plan((211, 41, 181, 221)).rect_px == (211, 41, 181, 221)

    @pytest.mark.parametrize(("rotation", "expected"), [
        ("90cw", (450, 330, 1200, 780)),
        ("90ccw", (720, 300, 1470, 750)),
    ])
    def test_a_quarter_turn_is_undone_before_cutting(self, rotation, expected):
        """El ROI se dibuja sobre el frame rotado; el VIC corta el video sin rotar."""
        crop = _plan((100, 150, 150, 250), rotation=rotation)
        assert (crop.left, crop.top, crop.right, crop.bottom) == expected

    def test_a_half_turn_is_undone_before_cutting(self):
        crop = _plan(rotation="180")
        assert (crop.left, crop.top, crop.right, crop.bottom) == (750, 300, 1290, 960)

    @pytest.mark.parametrize(("rotation", "rect_px"), [
        ("90cw", (100, 150, 150, 250)),     # frame rotado de 360x640
        ("90ccw", (100, 150, 150, 250)),
        ("180", (100, 50, 150, 250)),       # sigue siendo de 640x360
    ])
    def test_the_covered_rectangle_comes_back_rotated(self, rotation, rect_px):
        assert _plan(rect_px, rotation=rotation).rect_px == rect_px

    def test_a_crop_under_sixteen_pixels_is_rejected(self):
        """Por debajo de 15 px por lado el VIC tira el pipeline entero, frame incluido."""
        assert _plan((0, 0, 4, 100)) is None

    def test_sixteen_pixels_are_enough(self):
        crop = _plan((0, 0, 6, 6))
        assert (crop.right - crop.left, crop.bottom - crop.top) == (18, 18)

    def test_a_zero_side_reaches_the_border(self):
        """Como el ROI del motor: un ancho o alto en 0 llega hasta el borde."""
        crop = _plan((600, 0, 0, 0))
        assert (crop.left, crop.top, crop.right, crop.bottom) == (1800, 0, 1920, 1080)

    def test_a_roi_past_the_border_is_clamped(self):
        crop = _plan((600, 300, 100, 100))
        assert (crop.right, crop.bottom) == (1920, 1080)

    def test_at_native_size_the_crop_is_the_roi(self):
        crop = _plan(output_size=(1920, 1080), native_size=(1920, 1080))
        assert (crop.left, crop.top, crop.right, crop.bottom) == (210, 40, 390, 260)

    def test_the_output_keeps_the_crop_by_default(self):
        assert _plan().output_size == (540, 660)

    def test_the_output_short_side_keeps_the_shape(self):
        """Medido: 224x274, y el center crop del clasificador sólo recorta."""
        assert _plan(output_short_side_px=224).output_size == (224, 274)


class TestComputeViewSize:
    def test_zero_keeps_the_native_crop(self):
        assert compute_view_size(540, 660, 0) == (540, 660)

    def test_the_short_side_can_be_the_height(self):
        assert compute_view_size(660, 540, 224) == (274, 224)


class TestViewSizeFromABox:
    """Sin un lado corto, la vista sale a lo que el modelo que la lee haría igual con ella."""

    def test_a_classifier_covers_its_input_and_the_center_crop_is_all_it_does_after(self):
        """Un recorte de 654x660 hacia una entrada de 224: el lado corto en 224 y el otro, redondeado
        para arriba, como lo reduce el clasificador."""
        assert compute_view_size(654, 660, 0, (224, 224), True) == (224, 227)

    def test_a_square_crop_covers_a_square_input_exactly(self):
        assert compute_view_size(540, 540, 0, (224, 224), True) == (224, 224)

    def test_a_detector_fits_inside_its_input_keeping_the_shape(self):
        """Un recorte 1495x1270 hacia 384x640: manda el lado que antes se llena."""
        assert compute_view_size(1495, 1270, 0, (640, 384), False) == (452, 384)

    def test_a_wide_crop_is_limited_by_the_width_of_the_input(self):
        assert compute_view_size(1920, 540, 0, (640, 384), False) == (640, 180)

    def test_it_never_enlarges_a_crop_that_is_smaller_than_the_input(self):
        """Lo que falta lo pone el modelo: agrandar en el VIC sólo gasta ancho de banda."""
        assert compute_view_size(100, 80, 0, (640, 384), False) == (100, 80)
        assert compute_view_size(100, 80, 0, (224, 224), True) == (100, 80)

    def test_an_explicit_short_side_wins_over_the_box(self):
        assert compute_view_size(660, 540, 300, (224, 224), True) == (367, 300)

    def test_without_a_box_or_a_short_side_it_is_the_native_crop(self):
        assert compute_view_size(540, 660, 0, (0, 0), False) == (540, 660)

    def test_a_box_with_a_zero_side_is_no_box(self):
        assert compute_view_size(540, 660, 0, (224, 0), True) == (540, 660)

    def test_the_view_is_planned_with_the_box(self):
        view = ViewSettings("pipeline_1", _ROI, 0, (224, 224), True)
        crop = plan_view(view, native_size=(1920, 1080), output_size=(640, 360), rotation=None)
        assert crop.output_size == (224, 274)

    def test_the_box_is_read_from_the_decode_section(self):
        settings = read_decode_settings({"decode": {"backend": "cpu", "views": {
            "pipeline_1": {"x_px": 1, "y_px": 2, "width_px": 30, "height_px": 40,
                           "output_box_px": [224, 224], "output_covers": True}}}}, "camera_1")
        assert settings.views == (ViewSettings("pipeline_1", (1, 2, 30, 40), 0, (224, 224), True),)

    def test_a_malformed_box_is_ignored(self):
        settings = read_decode_settings({"decode": {"backend": "cpu", "views": {
            "pipeline_1": {"x_px": 1, "y_px": 2, "width_px": 30, "height_px": 40,
                           "output_box_px": "big"}}}}, "camera_1")
        assert settings.views[0].output_box_px == (0, 0)


class TestPlanViews:
    def test_rejected_views_are_named(self):
        settings = DecodeSettings(BACKEND_CPU, 640, 360, (
            _view(), ViewSettings("tiny", (0, 0, 2, 2), 0)))
        plan, rejected = plan_views(settings, (1920, 1080))
        assert list(plan) == ["pipeline_1"] and rejected == ["tiny"]

    def test_without_a_size_the_video_is_the_reference(self):
        settings = DecodeSettings(BACKEND_CPU, 0, 0, (_view((10, 10, 20, 20)),))
        plan, _ = plan_views(settings, (1920, 1080))
        assert (plan["pipeline_1"].left, plan["pipeline_1"].right) == (10, 30)


class TestReadViews:
    def test_the_views_are_read_from_the_decode_section(self):
        settings = read_decode_settings({"decode": {"views": {"pipeline_1": {
            "x_px": 1, "y_px": 2, "width_px": 30, "height_px": 40,
            "output_short_side_px": 224}}}})
        assert settings.views == (ViewSettings("pipeline_1", (1, 2, 30, 40), 224),)

    def test_the_rotation_comes_from_the_camera(self):
        assert read_decode_settings({"rotation": "90cw"}).rotation == "90cw"

    def test_views_that_are_not_a_map_are_dropped_with_a_warning(self, caplog):
        assert read_decode_settings({"decode": {"views": ["pipeline_1"]}}).views == ()
        assert _logged(caplog, "decode.views") == 1

    def test_without_views_the_settings_are_the_same_as_before(self):
        assert read_decode_settings({}) == DecodeSettings(BACKEND_CPU, 0, 0)


def _puller(*samples):
    """Un appsink de mentira: entrega los samples en orden y después None."""
    queue = list(samples)
    return lambda: queue.pop(0) if queue else None


def _identity(pts: int) -> int:
    return pts


class TestPairByPts:
    """Frame y vistas salen de ramas distintas: se aparean por PTS, del lado que va atrás."""

    def test_a_view_with_the_same_pts_is_paired(self):
        assert pair_by_pts(5, _puller(), {"v": _puller(5)}, _identity) == (5, {"v": 5})

    def test_a_view_behind_is_pulled_again(self):
        assert pair_by_pts(5, _puller(), {"v": _puller(4, 5)}, _identity) == (5, {"v": 5})

    def test_a_view_ahead_moves_the_frame_forward(self):
        """La vista ya es del frame siguiente: el que va atrás es el frame."""
        assert pair_by_pts(5, _puller(6), {"v": _puller(6)}, _identity) == (6, {"v": 6})

    def test_a_view_that_never_arrives_is_missing_not_old(self):
        assert pair_by_pts(5, _puller(), {"v": _puller(4)}, _identity) == (5, {})

    def test_a_view_that_always_lags_gives_up(self):
        lagging = iter(range(1000))
        _, views = pair_by_pts(10_000, _puller(), {"v": lambda: next(lagging)}, _identity)
        assert views == {}

    def test_two_views_are_paired_to_the_same_frame(self):
        main, views = pair_by_pts(5, _puller(6), {"a": _puller(6), "b": _puller(5, 6)},
                                  _identity)
        assert (main, views) == (6, {"a": 6, "b": 6})


class TestViewPipelines:
    _SETTINGS = DecodeSettings(BACKEND_NVDEC, 640, 360, (_view(),))

    def test_the_decoder_feeds_a_tee(self):
        launch = build_stream_launch(self._SETTINGS, 5000)
        assert "nvv4l2decoder name=dec disable-dpb=true ! tee name=t" in launch

    def test_the_frame_branch_is_the_same_as_without_views(self):
        launch = build_stream_launch(self._SETTINGS, 5000)
        assert "video/x-raw,format=BGRx,width=640,height=360 ! appsink name=sink" in launch

    def test_a_view_is_reduced_with_five_taps(self):
        """Lo más parecido a INTER_AREA; el bilineal le agrega aliasing a una reducción x3."""
        launch = build_stream_launch(self._SETTINGS, 5000)
        assert "nvvidconv name=view_conv_0 interpolation-method=2" in launch

    def test_a_live_view_keeps_only_its_newest_frame(self):
        """Con dos buffers la vista queda un frame atrás del frame principal."""
        launch = build_stream_launch(self._SETTINGS, 5000)
        assert "appsink name=view_sink_0 max-buffers=1 drop=true" in launch
        assert launch.count("queue leaky=downstream max-size-buffers=1") == 2

    def test_a_file_view_drops_nothing(self):
        launch = build_file_launch(self._SETTINGS)
        assert "appsink name=view_sink_0 max-buffers=2 drop=false" in launch
        assert "leaky" not in launch

    def test_the_crop_is_not_in_the_launch(self):
        """Lo fija el probe cuando se conoce el tamaño nativo."""
        assert "left=" not in build_stream_launch(self._SETTINGS, 5000)


def _gradient_frame() -> np.ndarray:
    """Frame nativo de 1920x1080 donde el canal azul dice la columna: x // 8."""
    frame = np.zeros((1080, 1920, 3), np.uint8)
    frame[:, :, 0] = (np.arange(1920) // 8).astype(np.uint8)
    return frame


def _read_with_views(fake_cv2, *views: ViewSettings):
    opened = fake_cv2()
    capture = decoder.open_file("clip.mp4", DecodeSettings(BACKEND_CPU, 640, 360, views))
    opened[0].frame = _gradient_frame()
    is_read, frame = capture.read()
    assert is_read
    return capture, frame


class TestCpuViews:
    def test_the_view_is_cut_from_the_native_frame(self, fake_cv2):
        capture, _ = _read_with_views(fake_cv2, _view())
        view = capture.retrieve_views()["pipeline_1"]
        assert view.image_bgr.shape == (660, 540, 3)
        assert int(view.image_bgr[0, 0, 0]) == 630 // 8

    def test_the_frame_is_still_reduced(self, fake_cv2):
        _, frame = _read_with_views(fake_cv2, _view())
        assert frame.shape == (360, 640, 3)

    def test_the_view_comes_out_at_its_short_side(self, fake_cv2):
        capture, _ = _read_with_views(fake_cv2, _view(output_short_side_px=224))
        assert capture.retrieve_views()["pipeline_1"].image_bgr.shape == (274, 224, 3)

    def test_the_view_owns_its_array(self, fake_cv2):
        """Un recorte de numpy retendría el frame nativo entero mientras la vista viva."""
        capture, _ = _read_with_views(fake_cv2, _view())
        assert capture.retrieve_views()["pipeline_1"].image_bgr.flags.owndata

    def test_the_names_are_those_being_cut(self, fake_cv2):
        capture, _ = _read_with_views(fake_cv2, _view(), ViewSettings("tiny", (0, 0, 2, 2), 0))
        assert capture.get_view_names() == ("pipeline_1",)

    def test_a_rejected_view_is_logged_once(self, fake_cv2, caplog):
        capture, _ = _read_with_views(fake_cv2, ViewSettings("tiny", (0, 0, 2, 2), 0))
        capture.read()
        assert _logged(caplog, "tiny") == 1

    def test_without_views_there_are_none(self, fake_cv2):
        capture, _ = _read_with_views(fake_cv2)
        assert capture.retrieve_views() == {}


class TestRender:
    def test_it_is_the_cpu_path_in_one_call(self):
        frame, views = render(_gradient_frame(),
                              DecodeSettings(BACKEND_CPU, 640, 360, (_view(),)))
        assert frame.shape == (360, 640, 3)
        assert views["pipeline_1"].rect_px == _ROI

    def test_without_a_size_the_frame_is_the_same_array(self):
        native = _gradient_frame()
        frame, _ = render(native, DecodeSettings(BACKEND_CPU, 0, 0))
        assert frame is native


class _FakeSample:
    """Un sample de GStreamer: sólo el PTS de su buffer."""

    def __init__(self, pts: int):
        self._pts = pts

    def get_buffer(self):
        return types.SimpleNamespace(pts=self._pts)


class _FakeSink:
    """Un appsink que entrega samples con esos PTS, en orden, y después nada."""

    def __init__(self, *pts: int):
        self._samples = [_FakeSample(value) for value in pts]
        self.pulls = 0

    def emit(self, _signal: str, _timeout_ns: int):
        self.pulls += 1
        return self._samples.pop(0) if self._samples else None


def _branch(name: str, sink: _FakeSink, *, is_cut: bool = True):
    branch = decoder._ViewBranch(ViewSettings(name, (0, 0, 10, 10), 0), None, None, sink)
    branch.crop = decoder.ViewCrop(0, 0, 16, 16, (16, 16), (0, 0, 10, 10)) if is_cut else None
    return branch


def _gst_capture(main_sink: _FakeSink, *branches, is_live: bool = True):
    return decoder._GstCapture(None, main_sink, 1000, branches=branches,
                               native=decoder._NativeSize(), is_live=is_live)


class TestGstCaptureViews:
    """La parte del camino de la Jetson que no necesita GStreamer: qué se pide y cuándo."""

    def test_a_live_view_is_paired_by_pts(self):
        capture = _gst_capture(_FakeSink(5), _branch("v", _FakeSink(4, 5)))
        assert capture.grab()
        assert capture._view_samples["v"].get_buffer().pts == 5

    def test_a_file_takes_one_sample_from_every_branch(self):
        """Un archivo no descarta: una rama que nadie lee frena el tee entero."""
        cut, rejected = _FakeSink(1), _FakeSink(1)
        capture = _gst_capture(_FakeSink(1), _branch("cut", cut),
                               _branch("rejected", rejected, is_cut=False), is_live=False)
        assert capture.grab()
        assert (cut.pulls, rejected.pulls) == (1, 1)

    def test_the_names_leave_out_the_rejected_views(self):
        capture = _gst_capture(_FakeSink(), _branch("cut", _FakeSink()),
                               _branch("rejected", _FakeSink(), is_cut=False))
        assert capture.get_view_names() == ("cut",)

    def test_a_video_that_changed_size_is_reopened(self):
        """Cambiar el tamaño del corte en marcha da frames negros: se rearma el pipeline."""
        capture = _gst_capture(_FakeSink(5), _branch("v", _FakeSink(5)))
        capture._native.has_changed = True
        assert capture.grab() is False
