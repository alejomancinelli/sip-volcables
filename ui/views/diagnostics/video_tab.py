"""
Pestaña de video: estado de los dos servidores y las URLs que hay que abrir.

Una tarjeta con la forma de las de ajustes (`ui/widgets/settings_card.py`) y una sección
por servidor: a la izquierda qué es, a la derecha su estado y sus clientes, y debajo sus
URLs en una lista, una por fila, seleccionables para copiarlas.

**Las URLs no se arman acá.** La UI no sabe la forma de una ruta de stream ni qué
interfaces tiene el equipo: las recibe hechas con `set_stream_urls()` desde el
cableado, que las pide a cada servidor. Si la UI las compusiera, el formato de la ruta
quedaría definido en dos lugares y un cambio en el servidor dejaría a la pantalla
mostrando direcciones que no existen.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from system.config_manager import ConfigManager

from ui import service_status
from ui.strings import tr
from ui.views.diagnostics.abstract_tab import AbstractDiagnosticsTab
from ui.widgets.settings_card import SettingsCard, SettingsList
from ui.widgets.status_chip import CHIP_INFO, CHIP_OK, StatusChip

# Servicio -> claves del título y la descripción de su sección.
_SECTION_KEYS = {
    service_status.SERVICE_VIDEO_HTTP: ("diag_streams_http", "diag_streams_http_description"),
    service_status.SERVICE_VIDEO_RTSP: ("diag_streams_rtsp", "diag_streams_rtsp_description"),
}
_CHIP_SPACING_PX = 8
_LIST_GAP_PX = 12            # entre los chips y la lista de URLs


class VideoTab(AbstractDiagnosticsTab):
    """Estado, clientes y URLs de los servidores de video."""

    TITLE_KEY = "tab_video_streams"
    IS_SCROLLABLE = True      # la lista de URLs crece con las cámaras

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        self._status_chips: dict[str, StatusChip] = {}
        self._client_chips: dict[str, StatusChip] = {}
        self._url_layouts: dict[str, QVBoxLayout] = {}
        self._urls: dict[str, list] = {}

        card = SettingsCard(tr("diag_streams_card"))
        for service, (title_key, description_key) in _SECTION_KEYS.items():
            section = card.add_section(tr(title_key), tr(description_key), columns=1)
            section.add_row(self._build_server(service))

        layout = QVBoxLayout(self)
        # Sin margen propio: la tarjeta arranca en el borde del encabezado de la página.
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(card)
        layout.addStretch()

    # ── API pública ──────────────────────────────────────────────────────────

    def set_service_status(self, service: str, status: str):
        """Actualiza el chip de estado de un servidor. Ignora un servicio que no sea de video."""
        chip = self._status_chips.get(service)
        if chip is None:
            return
        text, chip_state = service_status.describe(service, status)
        chip.set_state(chip_state, text)

    def set_client_count(self, service: str, count: int):
        """Clientes conectados a ese servidor: en verde con uno o más, en gris con ninguno."""
        chip = self._client_chips.get(service)
        if chip is None:
            return
        chip.set_state(CHIP_OK if count > 0 else CHIP_INFO,
                       f"{tr('diag_clients')}: {count}")

    def set_stream_urls(self, service: str, urls: list):
        """URLs de los streams de ese servidor, ya armadas por quien las publica."""
        if service not in self._url_layouts:
            return
        self._urls[service] = list(urls)
        self._rebuild_urls(service)

    # ── Internos ─────────────────────────────────────────────────────────────

    def _build_server(self, service: str) -> QWidget:
        """Los chips del servidor y, debajo, el lugar de sus URLs."""
        status_chip = StatusChip()
        self._status_chips[service] = status_chip
        self.set_service_status(service, "")

        client_chip = StatusChip()
        self._client_chips[service] = client_chip
        self.set_client_count(service, 0)

        chips = QHBoxLayout()
        chips.setSpacing(_CHIP_SPACING_PX)
        chips.addWidget(status_chip)
        chips.addWidget(client_chip)
        chips.addStretch()

        server = QWidget()
        layout = QVBoxLayout(server)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_LIST_GAP_PX)
        layout.addLayout(chips)
        urls = QVBoxLayout()
        urls.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(urls)
        self._url_layouts[service] = urls
        self._urls[service] = []
        self._rebuild_urls(service)
        return server

    def _rebuild_urls(self, service: str):
        layout = self._url_layouts[service]
        while layout.count():
            item = layout.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

        urls = self._urls.get(service, [])
        if not urls:
            empty = QLabel(tr("diag_streams_empty"))
            empty.setObjectName("settingsFieldHint")
            layout.addWidget(empty)
            return

        rows = SettingsList()
        for url in urls:
            label = QLabel(str(url))
            label.setObjectName("streamUrl")
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            rows.add_row(label)
        layout.addWidget(rows)
