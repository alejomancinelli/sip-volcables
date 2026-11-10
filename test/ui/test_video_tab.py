"""
Tests de la pestaña de video: que la sección `video:` vaya y vuelva del config sin
deformarse, y que cada servidor apagado apague sus propios campos.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                              # noqa: E402

from ui.views.config.video_tab import VideoTab                          # noqa: E402

_VALUES = {
    "video.http.enabled": True,
    "video.http.host": "0.0.0.0",
    "video.http.port": 8099,
    "video.http.jpeg_quality": 70,
    "video.http.frame_width_px": 1280,
    "video.http.log_connections": False,
    "video.rtsp.enabled": False,
    "video.rtsp.port": 8555,
    "video.rtsp.codec": "h265_hw",
    "video.rtsp.fps": 10,
    "video.rtsp.frame_width_px": 1920,
    "video.rtsp.bitrate_kbps": 6000,
    "video.rtsp.net_interfaces": ["eth0", "wlan0"],
    "video.rtsp.log_connections": True,
    "video.display.gamma": 0.8,
    "video.display.clahe_clip": 2.0,
}


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


class _MockConfig:
    """ConfigManager mínimo con `get` punteado y `set`, sin archivo ni Lock."""

    def __init__(self, **overrides):
        self._values = dict(_VALUES)
        self._values.update(overrides)
        self.written: dict = {}

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)

    def set(self, key: str, value: object):
        self.written[key] = value


def _tab(**overrides) -> tuple[VideoTab, _MockConfig]:
    config = _MockConfig(**overrides)
    return VideoTab(config), config


class TestRoundTrip:
    def test_every_value_goes_back_unchanged(self):
        tab, config = _tab()
        tab.save()
        assert config.written == pytest.approx(_VALUES)

    def test_an_unknown_codec_goes_back_as_it_was(self):
        tab, config = _tab(**{"video.rtsp.codec": "av1_hw"})
        tab.save()
        assert config.written["video.rtsp.codec"] == "av1_hw"


class TestEnabled:
    def test_a_disabled_server_disables_its_fields(self):
        tab, _ = _tab()
        assert not tab._rtsp_port.isEnabled()
        assert not tab._rtsp_log.isEnabled()
        assert tab._http_port.isEnabled()

    def test_the_display_adjustment_does_not_depend_on_any_server(self):
        tab, _ = _tab(**{"video.http.enabled": False})
        assert tab._gamma.isEnabled()
        assert tab._clahe_clip.isEnabled()
