"""
Pestaña de video: la sección `video:` — HTTP, RTSP y el ajuste de visualización.

Dos tarjetas con la forma de las de ajustes (`ui/widgets/settings_card.py`): «Servidores»,
con una sección por servidor, y «Visualización», con el gamma y el contraste local.

Los dos servidores son independientes y así se editan: cada uno con su checkbox de
habilitado, que apaga y atenúa sus propios campos. Se aplican al reiniciar. Por dónde
escucha cada uno se elige entre las interfaces que tiene el equipo
(`ui/widgets/interface_picker.py`): el HTTP en una dirección, el RTSP en una o varias
interfaces, porque levanta un servidor por cada una.

El ajuste de visualización está acá y no en la pestaña de inferencia a propósito: la
gamma y el contraste local no mueven un píxel de lugar, así que van sólo al camino de
visualización y el modelo y el dataset siguen viendo el frame crudo. La descripción de la
sección lo dice en pantalla, que es donde hace falta. Es lo único de esta pestaña que se
aplica al guardar, sin reiniciar: se ajusta mirando la imagen.
"""

from PySide6.QtWidgets import QCheckBox, QVBoxLayout, QWidget

from system.config_manager import ConfigManager

from ui.strings import tr
from ui.views.config.abstract_tab import AbstractConfigTab
from ui.widgets.form import (
    build_double_spin_box, build_spin_box, build_value_combo_box, set_combo_data,
    wire_enable_toggle,
)
from ui.widgets.interface_picker import ALL_ADDRESSES, InterfaceChecklist, build_address_combo
from ui.widgets.settings_card import SettingsCard

# Texto que ve el operador -> valor de `video.rtsp.codec`. Los `_hw` usan el encoder
# de la Jetson y no existen en un x86; el texto lo dice para que no se elijan a ciegas.
# El valor viaja en el item del combo y no se reconstruye desde el texto.
_CODECS = {
    "H.264 software":            "h264_sw",
    "H.264 hardware (Jetson)":   "h264_hw",
    "H.265 software":            "h265_sw",
    "H.265 hardware (Jetson)":   "h265_hw",
    "MJPEG":                     "mjpeg",
}
_DEFAULT_CODEC = "h264_sw"

_MAX_PORT = 65535
_MAX_FRAME_WIDTH_PX = 8192
_MAX_BITRATE_KBPS = 100_000
_MAX_FPS = 240
_MAX_CLAHE_CLIP = 8.0
_MAX_GAMMA = 4.0

_CARD_SPACING_PX = 24        # entre tarjetas, como la web
_COLUMNS = 3


class VideoTab(AbstractConfigTab):
    """Sección `video:` del config. Ver el contrato en `abstract_tab.py`."""

    TITLE_KEY = "tab_video"

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        layout = QVBoxLayout(self)
        # Sin margen propio: las tarjetas arrancan en el borde del encabezado de la página.
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_CARD_SPACING_PX)
        layout.addWidget(self._build_servers_card())
        layout.addWidget(self._build_display_card())
        layout.addStretch()
        self.load()

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_servers_card(self) -> QWidget:
        card = SettingsCard(tr("video_card_servers"))

        http = card.add_section(tr("video_box_http"), tr("video_http_description"), _COLUMNS)
        self._http_enabled = http.add_row(QCheckBox(tr("video_http_enabled")))
        self._http_host = http.add_field(
            tr("net_listen_on"), build_address_combo(), hint=tr("net_listen_hint"))
        self._http_port = http.add_field(tr("field_port"), build_spin_box(1, _MAX_PORT, 8091))
        self._http_quality = http.add_field(
            tr("video_jpeg_quality"), build_spin_box(1, 100, 80),
            hint=tr("video_jpeg_quality_hint"))
        self._http_width = http.add_field(
            tr("video_frame_width"), build_spin_box(0, _MAX_FRAME_WIDTH_PX, 960),
            hint=tr("video_frame_width_hint"))
        self._http_log = http.add_row(QCheckBox(tr("video_log_connections")))
        wire_enable_toggle(self._http_enabled, [
            self._http_host, self._http_port, self._http_quality, self._http_width,
            self._http_log,
        ])

        rtsp = card.add_section(tr("video_box_rtsp"), tr("video_rtsp_description"), _COLUMNS)
        self._rtsp_enabled = rtsp.add_row(QCheckBox(tr("video_rtsp_enabled")))
        self._rtsp_port = rtsp.add_field(tr("field_port"), build_spin_box(1, _MAX_PORT, 8554))
        self._rtsp_codec = rtsp.add_field(
            tr("video_codec"), build_value_combo_box(_CODECS, _DEFAULT_CODEC),
            hint=tr("video_codec_hint"))
        self._rtsp_fps = rtsp.add_field(
            tr("field_fps"), build_spin_box(1, _MAX_FPS, 15), hint=tr("unit_fps"))
        self._rtsp_width = rtsp.add_field(
            tr("video_frame_width"), build_spin_box(0, _MAX_FRAME_WIDTH_PX, 0),
            hint=tr("video_frame_width_hint"))
        self._rtsp_bitrate = rtsp.add_field(
            tr("video_bitrate"), build_spin_box(0, _MAX_BITRATE_KBPS, 4000),
            hint=tr("video_bitrate_hint"))
        self._rtsp_interfaces = rtsp.add_field(
            tr("video_net_interfaces"), InterfaceChecklist(), hint=tr("net_interfaces_hint"),
            span=_COLUMNS)
        self._rtsp_log = rtsp.add_row(QCheckBox(tr("video_log_connections")))
        wire_enable_toggle(self._rtsp_enabled, [
            self._rtsp_port, self._rtsp_codec, self._rtsp_fps, self._rtsp_width,
            self._rtsp_bitrate, self._rtsp_interfaces, self._rtsp_log,
        ])
        return card

    def _build_display_card(self) -> QWidget:
        card = SettingsCard(tr("video_card_display"))
        display = card.add_section(tr("video_box_display"), tr("video_display_description"),
                                   _COLUMNS)
        self._gamma = display.add_field(
            tr("video_gamma"),
            build_double_spin_box(0.1, _MAX_GAMMA, 1.0, decimals=2, step=0.05),
            hint=tr("video_gamma_hint"))
        self._clahe_clip = display.add_field(
            tr("video_clahe_clip"),
            build_double_spin_box(0.0, _MAX_CLAHE_CLIP, 0.0, decimals=1, step=0.5),
            hint=tr("video_clahe_hint"))
        return card

    # ── Contrato de la pestaña ───────────────────────────────────────────────

    def load(self):
        self._http_enabled.setChecked(bool(self._config.get("video.http.enabled", True)))
        set_combo_data(self._http_host, str(self._config.get("video.http.host", ALL_ADDRESSES)))
        self._http_port.setValue(int(self._config.get("video.http.port", 8091)))
        self._http_quality.setValue(int(self._config.get("video.http.jpeg_quality", 80)))
        self._http_width.setValue(int(self._config.get("video.http.frame_width_px", 960)))
        self._http_log.setChecked(bool(self._config.get("video.http.log_connections", True)))

        self._rtsp_enabled.setChecked(bool(self._config.get("video.rtsp.enabled", False)))
        self._rtsp_port.setValue(int(self._config.get("video.rtsp.port", 8554)))
        set_combo_data(self._rtsp_codec, self._config.get("video.rtsp.codec", _DEFAULT_CODEC))
        self._rtsp_fps.setValue(int(self._config.get("video.rtsp.fps", 15)))
        self._rtsp_width.setValue(int(self._config.get("video.rtsp.frame_width_px", 0)))
        self._rtsp_bitrate.setValue(int(self._config.get("video.rtsp.bitrate_kbps", 0) or 0))
        self._rtsp_interfaces.set_selected(self._config.get("video.rtsp.net_interfaces", []))
        self._rtsp_log.setChecked(bool(self._config.get("video.rtsp.log_connections", True)))

        self._gamma.setValue(float(self._config.get("video.display.gamma", 1.0)))
        self._clahe_clip.setValue(float(self._config.get("video.display.clahe_clip", 0.0)))

    def save(self):
        self._config.set("video.http.enabled", self._http_enabled.isChecked())
        self._config.set("video.http.host", self._http_host.currentData())
        self._config.set("video.http.port", self._http_port.value())
        self._config.set("video.http.jpeg_quality", self._http_quality.value())
        self._config.set("video.http.frame_width_px", self._http_width.value())
        self._config.set("video.http.log_connections", self._http_log.isChecked())

        self._config.set("video.rtsp.enabled", self._rtsp_enabled.isChecked())
        self._config.set("video.rtsp.port", self._rtsp_port.value())
        self._config.set("video.rtsp.codec", self._rtsp_codec.currentData())
        self._config.set("video.rtsp.fps", self._rtsp_fps.value())
        self._config.set("video.rtsp.frame_width_px", self._rtsp_width.value())
        self._config.set("video.rtsp.bitrate_kbps", self._rtsp_bitrate.value())
        self._config.set("video.rtsp.net_interfaces", self._rtsp_interfaces.get_selected())
        self._config.set("video.rtsp.log_connections", self._rtsp_log.isChecked())

        self._config.set("video.display.gamma", self._gamma.value())
        self._config.set("video.display.clahe_clip", self._clahe_clip.value())
