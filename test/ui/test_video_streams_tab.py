"""
Tests de la pestaña de streams: qué muestra de cada servidor de video, que la lista de URLs
se rearme sólo cuando cambia y que copiar deje la URL entera en el portapapeles.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication                               # noqa: E402
from PySide6.QtWidgets import QApplication                              # noqa: E402

from ui import service_status                                           # noqa: E402
from ui.views.diagnostics.video_tab import VideoTab                     # noqa: E402

_HTTP = service_status.SERVICE_VIDEO_HTTP
_RTSP = service_status.SERVICE_VIDEO_RTSP
_URLS = ["http://127.0.0.1:8091/entrada/raw", "http://127.0.0.1:8091/entrada/annotated"]


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


class _MockConfig:
    """ConfigManager mínimo con `get` punteado, sin archivo ni Lock."""

    def __init__(self, values: dict | None = None):
        self._values = values or {}

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


def _card(tab: VideoTab, service: str):
    return tab._cards[service]


class TestHeader:
    def test_http_says_where_it_listens_and_what_it_sends(self):
        tab = VideoTab(_MockConfig({"video.http.host": "10.0.0.5", "video.http.port": 8099,
                                    "video.http.jpeg_quality": 70,
                                    "video.http.frame_width_px": 1280}))
        assert tab._describe_http() == "10.0.0.5:8099 · JPEG 70 · 1280 px"

    def test_rtsp_without_bitrate_leaves_it_to_the_encoder(self):
        tab = VideoTab(_MockConfig({"video.rtsp.port": 8555, "video.rtsp.codec": "h265_hw",
                                    "video.rtsp.fps": 10, "video.rtsp.frame_width_px": 1920,
                                    "video.rtsp.bitrate_kbps": 0}))
        assert "kbps" not in tab._describe_rtsp()
        assert "h265_hw · 10 fps · 1920 px" in tab._describe_rtsp()

    def test_a_status_for_another_service_is_ignored(self):
        tab = VideoTab(_MockConfig())
        tab.set_service_status(service_status.SERVICE_MQTT, "error")
        tab.set_client_count(service_status.SERVICE_MQTT, 3)
        tab.set_stream_urls(service_status.SERVICE_MQTT, _URLS)
        assert _card(tab, _HTTP)._rows == []


class TestUrls:
    def test_every_url_gets_a_row_and_is_counted(self):
        tab = VideoTab(_MockConfig())
        tab.set_stream_urls(_HTTP, _URLS)
        card = _card(tab, _HTTP)
        assert [row.url for row in card._rows] == _URLS
        assert card._stream_count.text() == "2"
        assert card._empty.isHidden()

    def test_without_urls_the_empty_line_is_shown(self):
        tab = VideoTab(_MockConfig())
        tab.set_stream_urls(_HTTP, _URLS)
        tab.set_stream_urls(_HTTP, [])
        card = _card(tab, _HTTP)
        assert card._rows == []
        assert not card._empty.isHidden()

    def test_the_same_urls_do_not_rebuild_the_rows(self):
        """El cableado las publica cada segundo: rearmar perdería el botón bajo el mouse."""
        tab = VideoTab(_MockConfig())
        tab.set_stream_urls(_HTTP, _URLS)
        rows = list(_card(tab, _HTTP)._rows)
        tab.set_stream_urls(_HTTP, list(_URLS))
        assert _card(tab, _HTTP)._rows == rows

    def test_only_http_rows_can_be_opened_in_the_browser(self):
        tab = VideoTab(_MockConfig())
        tab.set_stream_urls(_HTTP, _URLS)
        tab.set_stream_urls(_RTSP, ["rtsp://10.0.0.5:8554/entrada/raw"])
        assert _card(tab, _HTTP)._rows[0]._open_button is not None
        assert _card(tab, _RTSP)._rows[0]._open_button is None

    def test_copy_puts_the_whole_url_in_the_clipboard(self):
        """La etiqueta puede estar recortada con «…»: lo que se copia es la URL entera."""
        tab = VideoTab(_MockConfig())
        tab.set_stream_urls(_HTTP, _URLS)
        _card(tab, _HTTP)._rows[1]._copy_button.click()
        assert QGuiApplication.clipboard().text() == _URLS[1]


class TestClients:
    def test_the_client_count_is_shown_per_server(self):
        tab = VideoTab(_MockConfig())
        tab.set_client_count(_HTTP, 2)
        assert _card(tab, _HTTP)._clients.text() == "2"
        assert _card(tab, _RTSP)._clients.text() == "0"
