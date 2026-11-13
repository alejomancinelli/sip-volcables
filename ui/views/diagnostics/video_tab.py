"""
Pestaña de streams: estado de los dos servidores de video y las URLs que hay que abrir.

Dos tarjetas, HTTP y RTSP, con la forma de las de estadísticas de Modbus: arriba qué
servidor es, cómo está configurado y su estado; después los clientes conectados y los
streams publicados; y debajo sus URLs, una por fila, con un botón para copiarla —y, en
HTTP, otro para abrirla en el navegador—.

    ┌──────────────────────────────────────────┐
    │ HTTP (MJPEG)                    [Activo] │
    │ 0.0.0.0:8099 · JPEG 70 · 1280 px         │
    │ Para mirar en el navegador: …            │
    ├────────────────────┬─────────────────────┤
    │ Clientes conectados│ Streams publicados  │
    │ 2                  │ 6                   │
    ├────────────────────┴─────────────────────┤
    │ http://127.0.0.1:8099/camara_1/raw  ⧉ ↗  │
    │ …                                        │
    └──────────────────────────────────────────┘

**Las URLs no se arman acá.** La UI no sabe la forma de una ruta de stream ni qué
interfaces tiene el equipo: las recibe hechas con `set_stream_urls()` desde el
cableado, que las pide a cada servidor. Si la UI las compusiera, el formato de la ruta
quedaría definido en dos lugares y un cambio en el servidor dejaría a la pantalla
mostrando direcciones que no existen. Lo que sí sale del config es la línea de cómo está
configurado cada servidor, como en Modbus: cambiarlo exige reiniciar, así que no se
actualiza en vivo.

El cableado publica las URLs una vez por segundo: la lista se rearma sólo cuando cambian,
para no perder el botón bajo el mouse ni parpadear.
"""

from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)

from system.config_manager import ConfigManager

from ui import service_status, theme
from ui.strings import tr
from ui.views.diagnostics.abstract_tab import AbstractDiagnosticsTab
from ui.widgets.status_chip import StatusChip

_EMPTY_VALUE = "—"
_CARD_SPACING_PX = 24
_CARD_MARGINS_PX = (20, 18, 20, 16)
_CELL_MARGINS_PX = (20, 12, 20, 12)
_ROW_MARGINS_PX = (20, 6, 12, 6)
_ICON_SIZE_PX = 16
_COPIED_FEEDBACK_MS = 1500


class VideoTab(AbstractDiagnosticsTab):
    """Estado, clientes y URLs de los servidores de video. Ver el docstring del módulo."""

    TITLE_KEY = "tab_video_streams"
    IS_SCROLLABLE = True      # la lista de URLs crece con las cámaras

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        self._cards = {
            service_status.SERVICE_VIDEO_HTTP: _ServerCard(
                tr("diag_streams_http"), self._describe_http(),
                tr("diag_streams_http_description"), can_open=True),
            service_status.SERVICE_VIDEO_RTSP: _ServerCard(
                tr("diag_streams_rtsp"), self._describe_rtsp(),
                tr("diag_streams_rtsp_description"), can_open=False),
        }
        row = QHBoxLayout()
        row.setSpacing(_CARD_SPACING_PX)
        for card in self._cards.values():
            # Arriba: con URLs de un lado y ninguna del otro, cada tarjeta tiene su alto.
            row.addWidget(card, stretch=1, alignment=Qt.AlignTop)

        layout = QVBoxLayout(self)
        # Sin margen propio: las tarjetas arrancan en el borde del encabezado de la página.
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(row)
        layout.addStretch()

    # ── API pública ──────────────────────────────────────────────────────────

    def set_service_status(self, service: str, status: str):
        """Actualiza la píldora de estado de un servidor. Ignora un servicio que no sea de video."""
        card = self._cards.get(service)
        if card is not None:
            card.set_status(status)

    def set_client_count(self, service: str, count: int):
        """Clientes conectados a ese servidor, sumando todos sus streams."""
        card = self._cards.get(service)
        if card is not None:
            card.set_client_count(count)

    def set_stream_urls(self, service: str, urls: list):
        """URLs de los streams de ese servidor, ya armadas por quien las publica."""
        card = self._cards.get(service)
        if card is not None:
            card.set_urls(urls)

    def apply_theme(self, dark: bool):
        super().apply_theme(dark)
        for card in self._cards.values():
            card.apply_theme(dark)

    # ── Internos ─────────────────────────────────────────────────────────────

    def _describe_http(self) -> str:
        """«0.0.0.0:8099 · JPEG 70 · 1280 px»: dónde escucha y qué manda."""
        host = self._config.get("video.http.host", "0.0.0.0") or "0.0.0.0"
        port = self._config.get("video.http.port", 8091)
        quality = self._config.get("video.http.jpeg_quality", 80)
        return " · ".join((f"{host}:{port}", f"JPEG {quality}",
                           self._describe_width("video.http.frame_width_px")))

    def _describe_rtsp(self) -> str:
        """«puerto 8555 · h265_hw · 10 fps · 1920 px · 6000 kbps». Sin bitrate, el del encoder."""
        parts = [tr("diag_streams_port").format(port=self._config.get("video.rtsp.port", 8554)),
                 str(self._config.get("video.rtsp.codec", "") or _EMPTY_VALUE),
                 f"{self._config.get('video.rtsp.fps', 0)} fps",
                 self._describe_width("video.rtsp.frame_width_px")]
        bitrate_kbps = int(self._config.get("video.rtsp.bitrate_kbps", 0) or 0)
        if bitrate_kbps > 0:
            parts.append(f"{bitrate_kbps} kbps")
        return " · ".join(parts)

    def _describe_width(self, key: str) -> str:
        width_px = int(self._config.get(key, 0) or 0)
        return f"{width_px} px" if width_px > 0 else tr("diag_streams_native_width")


class _ServerCard(QWidget):
    """La tarjeta de un servidor: encabezado, contadores y la lista de URLs."""

    def __init__(self, title: str, location: str, description: str, *, can_open: bool,
                 parent=None):
        super().__init__(parent)
        self.setObjectName("settingsCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._can_open = can_open
        self._is_dark = True
        self._urls: list[str] = []
        self._rows: list[_UrlRow] = []

        self._pill = StatusChip(pill=True)
        self.set_status("")

        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(*_CARD_MARGINS_PX)
        top_layout.setSpacing(10)
        top_layout.addLayout(self._build_header(title, location))
        description_label = QLabel(description)
        description_label.setObjectName("hintLabel")
        description_label.setWordWrap(True)
        top_layout.addWidget(description_label)

        cells = QGridLayout()
        cells.setContentsMargins(0, 0, 0, 0)
        cells.setSpacing(0)
        self._clients = self._add_cell(cells, tr("diag_streams_clients"), 0)
        self._stream_count = self._add_cell(cells, tr("diag_streams_count"), 1)

        self._list = QVBoxLayout()
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(0)
        self._empty = QLabel(tr("diag_streams_empty"))
        self._empty.setObjectName("streamEmpty")
        self._empty.setWordWrap(True)
        self._list.addWidget(self._empty)

        layout = QVBoxLayout(self)
        # Un píxel para que las filas queden adentro del borde redondeado de la tarjeta.
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addWidget(top)
        layout.addLayout(cells)
        layout.addLayout(self._list)
        self.set_client_count(0)
        self._refresh_count()

    # ── API ──────────────────────────────────────────────────────────────────

    def set_status(self, status: str):
        # Sólo el estado: el nombre del servidor ya es el título de la tarjeta.
        text, chip_state = service_status.describe_status(status)
        self._pill.set_state(chip_state, text)

    def set_client_count(self, count: int):
        self._clients.setText(str(int(count)))

    def set_urls(self, urls: list):
        urls = [str(url) for url in urls]
        if urls == self._urls:
            return
        self._urls = urls
        for row in self._rows:
            # Se esconde ya: `deleteLater` la borra recién al volver al loop de eventos.
            row.hide()
            row.deleteLater()
        self._rows = [_UrlRow(url, self._can_open, self._is_dark) for url in urls]
        for row in self._rows:
            self._list.addWidget(row)
        self._empty.setVisible(not urls)
        self._refresh_count()

    def apply_theme(self, dark: bool):
        self._is_dark = dark
        for row in self._rows:
            row.apply_theme(dark)

    # ── Internos ─────────────────────────────────────────────────────────────

    def _build_header(self, title: str, location: str) -> QHBoxLayout:
        titles = QVBoxLayout()
        titles.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("settingsSectionTitle")
        location_label = QLabel(location)
        location_label.setObjectName("statsLocation")
        location_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        titles.addWidget(title_label)
        titles.addWidget(location_label)

        header = QHBoxLayout()
        header.setSpacing(8)
        header.addLayout(titles, stretch=1)
        header.addWidget(self._pill, alignment=Qt.AlignTop)
        return header

    @staticmethod
    def _add_cell(grid: QGridLayout, caption: str, column: int) -> QLabel:
        cell = QWidget()
        cell.setObjectName("statsCell")
        cell.setProperty("column", column)
        cell.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(cell)
        layout.setContentsMargins(*_CELL_MARGINS_PX)
        layout.setSpacing(2)
        caption_label = QLabel(caption)
        caption_label.setObjectName("hintLabel")
        value = QLabel(_EMPTY_VALUE)
        value.setObjectName("statsValue")
        layout.addWidget(caption_label)
        layout.addWidget(value)
        grid.addWidget(cell, 0, column)
        return value

    def _refresh_count(self):
        self._stream_count.setText(str(len(self._urls)))


class _UrlRow(QWidget):
    """Una URL, recortada al medio si no entra, con sus botones de copiar y abrir."""

    def __init__(self, url: str, can_open: bool, dark: bool, parent=None):
        super().__init__(parent)
        self.setObjectName("streamRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.url = url
        self._is_dark = dark

        self._label = _ElidedLabel(url)
        self._label.setObjectName("streamUrl")
        self._label.setToolTip(url)

        self._copy_button = _build_icon_button(tr("diag_streams_copy"))
        self._copy_button.clicked.connect(self._copy)
        self._open_button = None
        if can_open:
            self._open_button = _build_icon_button(tr("diag_streams_open"))
            self._open_button.clicked.connect(
                lambda: QDesktopServices.openUrl(QUrl(self.url)))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(*_ROW_MARGINS_PX)
        layout.setSpacing(2)
        layout.addWidget(self._label, stretch=1)
        layout.addWidget(self._copy_button)
        if self._open_button is not None:
            layout.addWidget(self._open_button)
        self._is_copied = False
        self.apply_theme(dark)

    def apply_theme(self, dark: bool):
        self._is_dark = dark
        self._refresh_icons()

    def _copy(self):
        QGuiApplication.clipboard().setText(self.url)
        self._is_copied = True
        self._refresh_icons()
        QTimer.singleShot(_COPIED_FEEDBACK_MS, self._end_copied)

    def _end_copied(self):
        self._is_copied = False
        self._refresh_icons()

    def _refresh_icons(self):
        muted = theme.get_palette(self._is_dark)["hint_text"]
        self._copy_button.setIcon(theme.get_icon("check" if self._is_copied else "copy", muted))
        if self._open_button is not None:
            self._open_button.setIcon(theme.get_icon("arrow-up-right", muted))


class _ElidedLabel(QLabel):
    """Etiqueta de una línea que recorta el texto al medio con «…» en vez de ensanchar la tarjeta."""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._full_text = text
        # Ignored: el ancho lo pone la tarjeta y no el largo de la URL.
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.setText(self.fontMetrics().elidedText(self._full_text, Qt.ElideMiddle,
                                                   self.width()))


def _build_icon_button(tooltip: str) -> QPushButton:
    button = QPushButton()
    button.setObjectName("ghostButton")
    button.setToolTip(tooltip)
    button.setCursor(Qt.PointingHandCursor)
    button.setIconSize(QSize(_ICON_SIZE_PX, _ICON_SIZE_PX))
    return button
