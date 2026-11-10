"""
Pestaña de estadísticas de Modbus: cuánto y cómo le contesta el equipo al PLC, por
transporte.

Dos tarjetas, TCP y RTU, como las de la web: arriba qué transporte es, dónde escucha y su
estado; después las consultas por minuto, en número y en tendencia, y debajo los contadores
—contestadas, lecturas, escrituras, excepciones, quién está conectado y cuándo llegó la
última—. Es lo que mira quien integra cuando el PLC «no ve» al equipo: sin consultas el
problema está del lado del PLC o del cable; con excepciones, en lo que pide.

Consume el dict de `SharedModbusServer.get_stats()` tal como sale, con sus claves estables,
por `update_stats()`. Nada se cuenta acá: qué es una consulta, y por qué no hay contador de
CRC ni de timeouts, lo dice el servidor.

**«Reiniciar» no toca el servidor.** Pone en cero lo que se ve —desde ahora— y vacía la
tendencia: es lo que se hace al cambiar algo en el PLC para ver si lo nuevo anda, y para eso
alcanza con restar. Los contadores del servidor siguen desde el arranque. Las consultas por
minuto no se reinician: ya son una ventana.

Dónde escucha cada transporte sale del config y no se actualiza en vivo: cambiarlo exige
reiniciar la aplicación, como todo lo del Modbus.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from system.config_manager import ConfigManager
from system.modbus.server import TRANSPORT_RTU, TRANSPORT_TCP

from ui import service_status
from ui.strings import tr
from ui.views.diagnostics.abstract_tab import AbstractDiagnosticsTab
from ui.widgets.sparkline import Sparkline
from ui.widgets.status_chip import StatusChip

# Los contadores que se reinician, y los que no: la ventana y el estado de ahora.
_CUMULATIVE_KEYS = ("requests", "reads", "writes", "exceptions")
_EMPTY_VALUE = "—"
_SPARKLINE_POINTS = 60        # un punto por segundo: el último minuto
_CARD_SPACING_PX = 24
_CARD_MARGINS_PX = (20, 18, 20, 0)
_CELL_MARGINS_PX = (20, 12, 20, 12)
_SECONDS_PER_MINUTE = 60

# Transporte -> (servicio de su chip, clave de su título).
_TRANSPORTS = {
    TRANSPORT_TCP: (service_status.SERVICE_MODBUS_TCP, "mb_box_tcp"),
    TRANSPORT_RTU: (service_status.SERVICE_MODBUS_RTU, "mb_box_rtu"),
}


class ModbusStatsTab(AbstractDiagnosticsTab):
    """Contadores de los dos transportes Modbus. Ver el docstring del módulo."""

    TITLE_KEY = "tab_modbus_stats"
    IS_SCROLLABLE = True      # dos tarjetas de alto fijo; en una pantalla baja no entran

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        self._cards = {
            TRANSPORT_TCP: _TransportCard(TRANSPORT_TCP, self._describe_tcp()),
            TRANSPORT_RTU: _TransportCard(TRANSPORT_RTU, self._describe_rtu()),
        }
        row = QHBoxLayout()
        row.setSpacing(_CARD_SPACING_PX)
        for card in self._cards.values():
            row.addWidget(card, stretch=1)

        layout = QVBoxLayout(self)
        # Sin margen propio: las tarjetas arrancan en el borde del encabezado de la página.
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(row)
        layout.addStretch()

    # ── API pública ──────────────────────────────────────────────────────────

    def update_stats(self, stats: dict):
        """Slot para el dict de `SharedModbusServer.get_stats()`. Un transporte que falta, sin datos."""
        for transport, card in self._cards.items():
            card.update_stats(stats.get(transport) or {})

    def set_service_status(self, service: str, status: str):
        """El chip de estado de la tarjeta de ese transporte. Ignora otro servicio."""
        for card in self._cards.values():
            card.set_service_status(service, status)

    def apply_theme(self, dark: bool):
        super().apply_theme(dark)
        for card in self._cards.values():
            card.apply_theme(dark)

    # ── Internos ─────────────────────────────────────────────────────────────

    def _describe_tcp(self) -> str:
        """«0.0.0.0:502 · esclavo 1»: dónde lo busca el PLC."""
        host = self._config.get("modbus.tcp.host", "0.0.0.0")
        port = self._config.get("modbus.tcp.port", 502)
        return f"{host}:{port} · {self._describe_slave()}"

    def _describe_rtu(self) -> str:
        """«/dev/ttyTHS1 · 115200 bps · 8N1 · esclavo 1», como lo escribe un manual."""
        port = self._config.get("modbus.rtu.port", "") or _EMPTY_VALUE
        baudrate = self._config.get("modbus.rtu.baudrate", 115200)
        parity = self._config.get("modbus.rtu.parity", "N")
        stop_bits = self._config.get("modbus.rtu.stop_bits", 1)
        return f"{port} · {baudrate} bps · 8{parity}{stop_bits} · {self._describe_slave()}"

    def _describe_slave(self) -> str:
        return tr("diag_stats_slave").format(slave_id=self._config.get("modbus.slave_id", 1))


class _TransportCard(QWidget):
    """La tarjeta de un transporte: encabezado, tendencia y contadores."""

    def __init__(self, transport: str, location: str, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._transport = transport
        self._service, title_key = _TRANSPORTS[transport]
        self._baseline = dict.fromkeys(_CUMULATIVE_KEYS, 0)
        self._last: dict = {}

        self._chip = StatusChip()
        self.set_service_status(self._service, "")
        self._rate = QLabel(_EMPTY_VALUE)
        self._rate.setObjectName("statsRate")
        self._sparkline = Sparkline(_SPARKLINE_POINTS)

        top = QWidget()
        top_layout = QVBoxLayout(top)
        top_layout.setContentsMargins(*_CARD_MARGINS_PX)
        top_layout.setSpacing(12)
        top_layout.addLayout(self._build_header(tr(title_key), location))
        rate_row = QHBoxLayout()
        rate_label = QLabel(tr("diag_stats_rate"))
        rate_label.setObjectName("hintLabel")
        rate_row.addWidget(rate_label, alignment=Qt.AlignBottom)
        rate_row.addStretch()
        rate_row.addWidget(self._rate)
        top_layout.addLayout(rate_row)
        top_layout.addWidget(self._sparkline)

        self._values: dict[str, QLabel] = {}
        cells = QGridLayout()
        cells.setContentsMargins(0, 12, 0, 0)
        cells.setSpacing(0)
        connections_key = ("diag_stats_clients" if transport == TRANSPORT_TCP
                           else "diag_stats_port")
        for index, (key, label_key) in enumerate((
                ("requests", "diag_stats_requests"),
                ("reads", "diag_stats_reads"),
                ("writes", "diag_stats_writes"),
                ("exceptions", "diag_stats_exceptions"),
                ("connections", connections_key),
                ("last_request_age_s", "diag_stats_last_request"))):
            cells.addWidget(self._build_cell(key, tr(label_key), index % 2),
                            index // 2, index % 2)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addWidget(top)
        layout.addLayout(cells)
        self.update_stats({})

    def update_stats(self, stats: dict):
        self._last = dict(stats)
        # Un servidor que volvió a arrancar trae los contadores en cero: la referencia
        # vieja los dejaría negativos.
        if any(int(stats.get(key, 0)) < self._baseline[key] for key in _CUMULATIVE_KEYS):
            self._baseline = dict.fromkeys(_CUMULATIVE_KEYS, 0)

        rate = stats.get("requests_per_min")
        self._rate.setText(_EMPTY_VALUE if rate is None else str(int(rate)))
        if rate is not None:
            self._sparkline.push(rate)
        for key in _CUMULATIVE_KEYS:
            value = stats.get(key)
            self._values[key].setText(
                _EMPTY_VALUE if value is None else str(int(value) - self._baseline[key]))

        exceptions = int(stats.get("exceptions", 0)) - self._baseline["exceptions"]
        code = int(stats.get("last_exception_code", 0))
        if exceptions > 0 and code:
            self._values["exceptions"].setText(
                tr("diag_stats_exceptions_last").format(count=exceptions, code=code))
        self._set_warn(self._values["exceptions"], exceptions > 0)

        connections = stats.get("connections")
        if connections is None:
            self._values["connections"].setText(_EMPTY_VALUE)
        elif self._transport == TRANSPORT_TCP:
            self._values["connections"].setText(str(int(connections)))
        else:
            self._values["connections"].setText(
                tr("diag_stats_port_open" if connections else "diag_stats_port_closed"))
        self._values["last_request_age_s"].setText(_format_age(stats.get("last_request_age_s")))

    def set_service_status(self, service: str, status: str):
        if service != self._service:
            return
        # Sólo el estado: el nombre del transporte ya es el título de la tarjeta.
        text, chip_state = service_status.describe_status(status)
        self._chip.set_state(chip_state, text)

    def apply_theme(self, dark: bool):
        self._sparkline.apply_theme(dark)

    def _reset(self):
        """Pone en cero lo que se ve, desde ahora. El servidor sigue contando."""
        self._baseline = {key: int(self._last.get(key, 0)) for key in _CUMULATIVE_KEYS}
        self._sparkline.clear()
        self.update_stats(self._last)

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

        reset = QPushButton(tr("diag_stats_reset"))
        reset.setObjectName("ghostButton")
        reset.clicked.connect(self._reset)

        header = QHBoxLayout()
        header.setSpacing(8)
        header.addLayout(titles, stretch=1)
        header.addWidget(self._chip, alignment=Qt.AlignTop)
        header.addWidget(reset, alignment=Qt.AlignTop)
        return header

    def _build_cell(self, key: str, label: str, column: int) -> QWidget:
        cell = QWidget()
        cell.setObjectName("statsCell")
        cell.setProperty("column", column)
        cell.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(cell)
        layout.setContentsMargins(*_CELL_MARGINS_PX)
        layout.setSpacing(2)
        caption = QLabel(label)
        caption.setObjectName("hintLabel")
        value = QLabel(_EMPTY_VALUE)
        value.setObjectName("statsValue")
        layout.addWidget(caption)
        layout.addWidget(value)
        self._values[key] = value
        return cell

    @staticmethod
    def _set_warn(label: QLabel, is_warn: bool):
        if label.property("warn") == is_warn:
            return
        label.setProperty("warn", is_warn)
        label.style().unpolish(label)
        label.style().polish(label)


def _format_age(age_s: object) -> str:
    """«hace 0.4 s», «hace 3 min», o un guion si todavía no llegó ninguna."""
    if age_s is None:
        return _EMPTY_VALUE
    age_s = float(age_s)
    if age_s < _SECONDS_PER_MINUTE:
        return tr("diag_stats_age_s").format(age=f"{age_s:.1f}")
    return tr("diag_stats_age_min").format(age=int(age_s // _SECONDS_PER_MINUTE))
