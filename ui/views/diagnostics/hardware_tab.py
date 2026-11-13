"""
Pestaña de hardware: el estado del equipo por secciones, cada una en su tarjeta.

    ┌ Hardware ───────────────────────────────────────────────────────────┐
    │ CPU        │ GPU        │ Memoria        │ Disco       │ Potencia   │
    │ 37 %       │ 12 %       │ 3.2 / 7.4 GB   │ 120 GB      │ 9.8 W      │
    │ ~~~~~~     │ ~~~~~~     │ ~~~~~~         │             │ ~~~~~~     │
    │ Temp. 52 °C│ Temp. 49 °C│ 43 % en uso    │ en .        │ Sin sobre… │
    └─────────────────────────────────────────────────────────────────────┘
    ┌ Temperaturas ───────────────────┐ ┌ Aceleradores ──────────────────┐
    │ ┌ CPU ────┐ ┌ GPU ────┐ ┌ TJ ───┐│ │ VIC   En uso      729 MHz      │
    │ │ 52.0 °C │ │ 49.0 °C │ │ 55 °C ││ │                                │
    │ │ ▬▬▬▬──  │ │ ▬▬▬───  │ │ ▬▬▬── ││ │                                │
    │ └─────────┘ └─────────┘ └───────┘│ │                                │
    └─────────────────────────────────┘ └────────────────────────────────┘
    ┌ Cámaras ────────────────────────────────────────────────────────────┐
    │ Cámara        Estado       FPS        Último minuto  Temp.  Vistas  │
    └─────────────────────────────────────────────────────────────────────┘
    ┌ Red ────────────────────────────────────────────────────────────────┐
    │ Interfaz   Dirección      Enlace       Recibe       Envía           │
    └─────────────────────────────────────────────────────────────────────┘

Consume el dict de `SystemMonitor.get_metrics()` tal como sale, con sus claves estables, y
el de `AbstractCameraDriver.get_status()` de cada cámara. Nada se mide acá: si hace falta
un número nuevo lo agrega su dueño. Lo único que se hace con los números es mostrarlos —el
porcentaje de memoria es `ram_used_mb` sobre `ram_total_mb`—, y un valor que el equipo no
expone, que el monitor y el driver informan como 0, se ve como «—» y no como un cero que
parece medido.

Las tendencias son `Sparkline`, como las de Estadísticas de Modbus: los porcentajes con la
escala fija de 0 a 100 —una CPU que va de 20 a 21 se ve casi plana, que es lo que es— y los
FPS de cada cámara contra su `acquisition.fps_limit`.

Cada zona térmica es una baldosa con su nombre corto —`cpu-thermal` se lee «CPU»; el
nombre entero queda en el tooltip—, el valor y una barra de 0 a 100 °C que pasa al ámbar
desde `_TEMPERATURE_WARN_C`: la zona caliente se ve sin leer los nueve números. El umbral es
de lectura y no un límite del equipo —la Jetson recién se frena cerca de los 99 °C—, así que
no sale al PLC ni a ningún lado.

Lo que depende del equipo se arma con la primera medición y no a mano: las zonas térmicas
salen de `temps_c`, los motores de `engines` —fuera de una Jetson viene vacío y su tarjeta
no se muestra— y las interfaces de `net_mbps`, que son las de `system_monitor.net_interfaces`.
La dirección y el enlace de cada interfaz se leen de `tools/net_interfaces.py`, lo mismo que
ofrecen los selectores de interfaz. Las cámaras salen de `cameras:` desde el arranque: una que
no informa queda en su fila, con su estado, en vez de no estar.
"""

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QProgressBar, QSizePolicy, QVBoxLayout, QWidget,
)

from system.config_manager import ConfigManager
from tools.net_interfaces import list_interfaces

from ui import service_status
from ui.strings import tr
from ui.views.diagnostics.abstract_tab import AbstractDiagnosticsTab
from ui.widgets.sparkline import Sparkline
from ui.widgets.status_chip import CHIP_INFO, CHIP_NEUTRAL, CHIP_OK, CHIP_WARNING, StatusChip

_EMPTY_VALUE = "—"
_TREND_POINTS = 120           # un punto por medición, una por segundo: los últimos dos minutos
_TILE_TREND_HEIGHT_PX = 36
_ROW_TREND_HEIGHT_PX = 22
_ROW_TREND_WIDTH_PX = 120
_CARD_SPACING_PX = 24
_CARD_MARGINS_PX = (20, 18, 20, 18)
_TILE_MARGINS_PX = (20, 14, 20, 16)
_TABLE_COLUMN_GAP_PX = 20
_TABLE_ROW_GAP_PX = 10
_TEMPERATURE_COLUMNS = 3
_TEMPERATURE_GAP_PX = 10
_TEMPERATURE_TILE_MARGINS_PX = (12, 10, 12, 12)
_TEMPERATURE_SCALE_C = 100    # la barra va de 0 a esto
_TEMPERATURE_WARN_C = 85      # desde acá, en ámbar: ver el docstring del módulo
_ZONE_SUFFIXES = ("-thermal", "_thermal", " thermal")
_MB_PER_GB = 1024.0
_PERCENT = 100.0

# Las cinco métricas de la fila de arriba, en orden. La clave es la del monitor.
_TILE_CPU = "cpu"
_TILE_GPU = "gpu"
_TILE_MEMORY = "memory"
_TILE_DISK = "disk"
_TILE_POWER = "power"

_CAMERA_STATE_KEYS = ("camera_starting", "camera_connected", "camera_disconnected",
                      "camera_disabled", "camera_misconfigured")


class HardwareTab(AbstractDiagnosticsTab):
    """El estado del equipo en vivo. Ver el docstring del módulo."""

    TITLE_KEY = "tab_hardware"
    IS_SCROLLABLE = True      # cinco tarjetas apiladas; en una pantalla baja no entran

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        self._are_widths_fixed = False
        self._tiles = {
            _TILE_CPU: _MetricTile(tr("hw_cpu"), trend_range=(0.0, _PERCENT)),
            _TILE_GPU: _MetricTile(tr("hw_gpu"), trend_range=(0.0, _PERCENT)),
            _TILE_MEMORY: _MetricTile(tr("hw_memory"), trend_range=(0.0, _PERCENT)),
            _TILE_DISK: _MetricTile(tr("hw_disk"), has_trend=False),
            _TILE_POWER: _MetricTile(tr("hw_power")),
        }
        self._temperature_cells: dict[str, QLabel] = {}
        self._temperature_bars: dict[str, QProgressBar] = {}
        self._engine_rows: dict[str, tuple[StatusChip, QLabel]] = {}
        self._camera_rows: dict[str, _CameraRow] = {}
        self._network_rows: dict[str, _NetworkRow] = {}

        temperatures_row = QHBoxLayout()
        temperatures_row.setSpacing(_CARD_SPACING_PX)
        temperatures_row.addWidget(self._build_temperatures_card(), stretch=1)
        temperatures_row.addWidget(self._build_engines_card(), stretch=1)

        layout = QVBoxLayout(self)
        # Sin margen propio: las tarjetas arrancan en el borde del encabezado de la página.
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_CARD_SPACING_PX)
        layout.addWidget(self._build_hardware_card())
        layout.addLayout(temperatures_row)
        layout.addWidget(self._build_cameras_card())
        layout.addWidget(self._build_network_card())
        layout.addStretch()

    # ── Slots de actualización ───────────────────────────────────────────────

    @Slot(object)
    def update_metrics(self, metrics: dict):
        """Slot para el dict de `SystemMonitor.get_metrics()`."""
        self._update_tiles(metrics)
        self._update_temperatures(metrics.get("temps_c") or {})
        self._update_engines(metrics.get("engines") or {})
        self._update_network(metrics.get("net_mbps") or {})

    @Slot(object, str)
    def update_camera_status(self, status: dict, camera_slot: str):
        """Slot con la firma de `CaptureThread.status_updated`: (status, slot)."""
        row = self._camera_rows.get(camera_slot)
        if row is not None:
            row.update_status(status)

    def apply_theme(self, dark: bool):
        super().apply_theme(dark)
        for tile in self._tiles.values():
            tile.apply_theme(dark)
        for row in self._camera_rows.values():
            row.apply_theme(dark)

    def showEvent(self, event):
        """
        Fija el ancho de las píldoras la primera vez que se ve: recién ahí tienen la
        tipografía y el relleno del QSS con los que se miden.
        """
        super().showEvent(event)
        if self._are_widths_fixed:
            return
        self._are_widths_fixed = True
        for row in self._camera_rows.values():
            row.fix_widths()
        for row in self._network_rows.values():
            row.fix_widths()
        for pill, _ in self._engine_rows.values():
            _fix_engine_width(pill)

    # ── Armado ───────────────────────────────────────────────────────────────

    def _build_hardware_card(self) -> QWidget:
        """Las cinco métricas en una fila de celdas, con un filete entre una y otra."""
        card = _new_card()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(_CARD_MARGINS_PX[0], _CARD_MARGINS_PX[1],
                                         _CARD_MARGINS_PX[2], 14)
        header_layout.addLayout(_build_card_header(tr("hw_card_hardware"),
                                                   tr("hw_card_hardware_description")))
        layout.addWidget(header)

        tiles = QHBoxLayout()
        tiles.setContentsMargins(0, 0, 0, 0)
        tiles.setSpacing(0)
        for index, tile in enumerate(self._tiles.values()):
            tile.setProperty("first", index == 0)
            tiles.addWidget(tile, stretch=1)
        layout.addLayout(tiles)
        return card

    def _build_temperatures_card(self) -> QWidget:
        card, layout = _new_card_with_layout()
        layout.addLayout(_build_card_header(tr("hw_card_temperatures"),
                                            tr("hw_card_temperatures_description")))
        self._temperatures_hint = _new_label(tr("hw_waiting"), "hintLabel")
        layout.addWidget(self._temperatures_hint)
        self._temperatures_grid = QGridLayout()
        self._temperatures_grid.setSpacing(_TEMPERATURE_GAP_PX)
        for column in range(_TEMPERATURE_COLUMNS):
            self._temperatures_grid.setColumnStretch(column, 1)
        layout.addLayout(self._temperatures_grid)
        layout.addStretch()
        return card

    def _build_engines_card(self) -> QWidget:
        """Se esconde hasta que el monitor informe algún motor: fuera de una Jetson no hay."""
        self._engines_card, layout = _new_card_with_layout()
        layout.addLayout(_build_card_header(tr("hw_card_engines"),
                                            tr("hw_card_engines_description")))
        self._engines_table = _Table((tr("hw_engine"), tr("hw_engine_state"),
                                      tr("hw_engine_clock")), stretches=(1, 0, 0))
        layout.addLayout(self._engines_table)
        layout.addStretch()
        self._engines_card.hide()
        return self._engines_card

    def _build_cameras_card(self) -> QWidget:
        card, layout = _new_card_with_layout()
        layout.addLayout(_build_card_header(tr("hw_card_cameras"),
                                            tr("hw_card_cameras_description")))
        cameras = self._config.get("cameras", {}) or {}
        if not cameras:
            layout.addWidget(_new_label(tr("camera_no_cameras"), "hintLabel"))
            return card
        table = _Table((tr("hw_camera"), tr("hw_camera_state"), tr("hw_camera_fps"),
                        tr("hw_camera_trend"), tr("hw_camera_temperature"),
                        tr("hw_camera_view_misses")),
                       stretches=(3, 0, 0, 2, 0, 0))
        for camera_slot, camera in cameras.items():
            row = _CameraRow(camera_slot, camera or {})
            self._camera_rows[camera_slot] = row
            table.add_row(row.cells)
        layout.addLayout(table)
        return card

    def _build_network_card(self) -> QWidget:
        card, layout = _new_card_with_layout()
        layout.addLayout(_build_card_header(tr("hw_card_network"),
                                            tr("hw_card_network_description")))
        self._network_hint = _new_label(tr("hw_waiting"), "hintLabel")
        layout.addWidget(self._network_hint)
        self._network_table = _Table((tr("hw_net_interface"), tr("hw_net_address"),
                                      tr("hw_net_link"), tr("hw_net_rx"), tr("hw_net_tx")),
                                     stretches=(2, 3, 0, 0, 0))
        layout.addLayout(self._network_table)
        return card

    # ── Actualización ────────────────────────────────────────────────────────

    def _update_tiles(self, metrics: dict):
        cpu = self._tiles[_TILE_CPU]
        cpu.set_value(str(int(metrics.get("cpu_usage_pct", 0))), "%")
        cpu.push(metrics.get("cpu_usage_pct", 0))
        cpu.set_foot(_format_temperature_foot(metrics.get("cpu_temp_c", 0)))

        gpu = self._tiles[_TILE_GPU]
        gpu.set_value(str(int(metrics.get("gpu_usage_pct", 0))), "%")
        gpu.push(metrics.get("gpu_usage_pct", 0))
        gpu.set_foot(_format_temperature_foot(metrics.get("gpu_temp_c", 0)))

        memory = self._tiles[_TILE_MEMORY]
        used_mb = float(metrics.get("ram_used_mb", 0))
        total_mb = float(metrics.get("ram_total_mb", 0))
        if total_mb > 0:
            used_pct = used_mb / total_mb * _PERCENT
            memory.set_value(f"{used_mb / _MB_PER_GB:.1f}",
                             tr("hw_memory_unit").format(total=f"{total_mb / _MB_PER_GB:.1f}"))
            memory.push(used_pct)
            memory.set_foot(tr("hw_memory_foot").format(pct=f"{used_pct:.0f}"))
        else:
            memory.set_value(_EMPTY_VALUE)
            memory.set_foot("")

        disk = self._tiles[_TILE_DISK]
        disk.set_value(f"{float(metrics.get('disk_free_gb', 0)):.0f}", tr("hw_disk_unit"))
        disk_path = str(self._config.get("system_monitor.disk_path", ".") or ".")
        disk.set_foot(tr("hw_disk_foot_app") if disk_path == "." else
                      tr("hw_disk_foot").format(path=disk_path))

        power = self._tiles[_TILE_POWER]
        power_w = float(metrics.get("power_w", 0.0))
        if power_w > 0:
            power.set_value(f"{power_w:.1f}", "W")
            power.push(power_w)
        else:
            power.set_value(_EMPTY_VALUE)
        overcurrent = int(metrics.get("overcurrent_count", 0))
        power.set_foot(tr("hw_overcurrent").format(count=overcurrent) if overcurrent
                       else tr("hw_overcurrent_none"), is_warn=overcurrent > 0)

    def _update_temperatures(self, temps_c: dict):
        """Una celda por zona, creada la primera vez que aparece, en orden alfabético."""
        self._temperatures_hint.setText(tr("hw_temperatures_empty"))
        self._temperatures_hint.setVisible(not temps_c)
        for zone in sorted(temps_c):
            cell = self._temperature_cells.get(zone)
            if cell is None:
                cell = self._add_temperature_cell(zone)
            celsius = float(temps_c[zone] or 0.0)
            cell.setText(_format_temperature(celsius))
            bar = self._temperature_bars[zone]
            bar.setValue(int(round(min(max(celsius, 0.0), _TEMPERATURE_SCALE_C))))
            is_hot = celsius >= _TEMPERATURE_WARN_C
            _set_property(cell, "warn", is_hot)
            _set_property(bar, "warn", is_hot)

    def _add_temperature_cell(self, zone: str) -> QLabel:
        """La baldosa de una zona: nombre corto, valor y barra. Devuelve la etiqueta del valor."""
        index = len(self._temperature_cells)
        tile = QWidget()
        tile.setObjectName("temperatureTile")
        tile.setAttribute(Qt.WA_StyledBackground, True)
        tile.setToolTip(zone)
        layout = QVBoxLayout(tile)
        layout.setContentsMargins(*_TEMPERATURE_TILE_MARGINS_PX)
        layout.setSpacing(4)
        layout.addWidget(_new_label(_short_zone_name(zone), "metricFoot"))
        value = _new_label(_EMPTY_VALUE, "temperatureValue")
        value.setTextFormat(Qt.PlainText)
        layout.addWidget(value)
        bar = QProgressBar()
        bar.setObjectName("temperatureBar")
        bar.setRange(0, _TEMPERATURE_SCALE_C)
        bar.setTextVisible(False)
        bar.setFixedHeight(4)
        layout.addWidget(bar)
        self._temperatures_grid.addWidget(tile, index // _TEMPERATURE_COLUMNS,
                                          index % _TEMPERATURE_COLUMNS)
        self._temperature_cells[zone] = value
        self._temperature_bars[zone] = bar
        return value

    def _update_engines(self, engines: dict):
        self._engines_card.setVisible(bool(engines))
        for name in sorted(engines):
            engine = engines[name] or {}
            row = self._engine_rows.get(name)
            if row is None:
                pill = StatusChip(pill=True)
                clock = _new_fixed_label(_EMPTY_VALUE)
                self._engines_table.add_row((_new_label(name, "tableCell"), pill, clock))
                row = self._engine_rows[name] = (pill, clock)
                if self._are_widths_fixed:
                    _fix_engine_width(pill)
            pill, clock = row
            if engine.get("is_active"):
                pill.set_state(CHIP_INFO, tr("hw_engine_active"))
            else:
                pill.set_state(CHIP_NEUTRAL, tr("hw_engine_idle"))
            mhz = int(engine.get("mhz", 0))
            clock.setText(f"{mhz} MHz" if mhz else _EMPTY_VALUE)

    def _update_network(self, net_mbps: dict):
        """
        Una fila por interfaz que informa el monitor, creada la primera vez que aparece:
        cuáles hay depende del equipo y de `system_monitor.net_interfaces`, y eso se sabe
        con la primera medición.
        """
        self._network_hint.setText(tr("hw_network_empty"))
        self._network_hint.setVisible(not net_mbps)
        interfaces = {interface.name: interface for interface in list_interfaces()}
        for name in sorted(net_mbps):
            row = self._network_rows.get(name)
            if row is None:
                row = self._network_rows[name] = _NetworkRow(name)
                self._network_table.add_row(row.cells)
                if self._are_widths_fixed:
                    row.fix_widths()
            row.update(net_mbps[name] or {}, interfaces.get(name))


class _MetricTile(QWidget):
    """
    Una métrica de la fila de arriba: el rótulo, el valor grande con su unidad, la tendencia
    y una línea al pie. Sin tendencia deja el mismo alto vacío, para que los pies de las
    cinco queden alineados.
    """

    def __init__(self, caption: str, *, has_trend: bool = True,
                 trend_range: tuple[float, float] | None = None, parent=None):
        super().__init__(parent)
        self.setObjectName("metricTile")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._value = _new_label(_EMPTY_VALUE, "metricValue")
        self._unit = _new_label("", "metricUnit")
        self._foot = _new_label("", "metricFoot")
        self._foot.setWordWrap(True)
        low, high = trend_range or (None, None)
        self._trend = Sparkline(_TREND_POINTS, low=low, high=high,
                                height_px=_TILE_TREND_HEIGHT_PX) if has_trend else None

        value_row = QHBoxLayout()
        value_row.setSpacing(4)
        value_row.addWidget(self._value, alignment=Qt.AlignBaseline)
        value_row.addWidget(self._unit, alignment=Qt.AlignBaseline)
        value_row.addStretch()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*_TILE_MARGINS_PX)
        layout.setSpacing(6)
        layout.addWidget(_new_label(caption, "hintLabel"))
        layout.addLayout(value_row)
        if self._trend is not None:
            layout.addWidget(self._trend)
        else:
            layout.addSpacing(_TILE_TREND_HEIGHT_PX)
        layout.addWidget(self._foot)
        layout.addStretch()

    @property
    def value_text(self) -> str:
        """El valor y la unidad, como se leen: «37 %»."""
        return f"{self._value.text()} {self._unit.text()}".strip()

    @property
    def foot_text(self) -> str:
        return self._foot.text()

    def set_value(self, value: str, unit: str = ""):
        self._value.setText(value)
        self._unit.setText(unit if value != _EMPTY_VALUE else "")

    def set_foot(self, text: str, *, is_warn: bool = False):
        self._foot.setText(text)
        _set_property(self._foot, "warn", is_warn)

    def push(self, value: float):
        if self._trend is not None:
            self._trend.push(float(value))

    def apply_theme(self, dark: bool):
        if self._trend is not None:
            self._trend.apply_theme(dark)


class _CameraRow:
    """
    Una cámara: nombre con slot y driver, estado, FPS medidos contra el límite configurado,
    su tendencia, la temperatura del sensor y las vistas que el decodificador no pudo
    aparear. Una cámara que no publica temperatura —por RTSP no hay sensor— muestra «—», y
    una que no decodifica video no tiene vistas que perder.
    """

    def __init__(self, camera_slot: str, camera: dict):
        is_enabled = bool(camera.get("enabled", True))
        acquisition = camera.get("acquisition") or {}
        self._fps_limit = float(acquisition.get("fps_limit") or 0)

        names = QWidget()
        names_layout = QVBoxLayout(names)
        names_layout.setContentsMargins(0, 0, 0, 0)
        names_layout.setSpacing(1)
        names_layout.addWidget(_new_label(str(camera.get("name") or camera_slot), "tableCell"))
        driver = str(camera.get("driver") or "").strip()
        names_layout.addWidget(_new_label(f"{camera_slot} · {driver}" if driver else camera_slot,
                                          "tableDetail"))

        self._pill = StatusChip(pill=True)
        self._pill.set_state(CHIP_WARNING, tr("camera_starting" if is_enabled
                                              else "camera_disabled"))

        fps = QWidget()
        fps_layout = QHBoxLayout(fps)
        fps_layout.setContentsMargins(0, 0, 0, 0)
        fps_layout.setSpacing(4)
        self._fps = _new_fixed_label(_EMPTY_VALUE)
        fps_layout.addWidget(self._fps)
        if self._fps_limit:
            fps_layout.addWidget(_new_label(f"/ {self._fps_limit:g}", "tableDetail"))

        self._trend = Sparkline(_TREND_POINTS, low=0.0, high=self._fps_limit or None,
                                height_px=_ROW_TREND_HEIGHT_PX)
        self._trend.setMinimumWidth(_ROW_TREND_WIDTH_PX)
        self._temperature = _new_fixed_label(_EMPTY_VALUE)
        self._view_misses = _new_fixed_label(_EMPTY_VALUE)
        self.cells = (names, self._pill, fps, self._trend, self._temperature, self._view_misses)

    @property
    def texts(self) -> tuple[str, str, str, str]:
        """(estado, FPS, temperatura, vistas perdidas) tal como se ven."""
        return (self._pill.text(), self._fps.text(), self._temperature.text(),
                self._view_misses.text())

    def update_status(self, status: dict):
        """Claves de `AbstractCameraDriver.get_status()`."""
        text, state = service_status.describe_camera(status)
        self._pill.set_state(state, text)
        self._pill.setToolTip(str(status.get("error") or ""))
        fps = float(status.get("fps_estimated", 0.0))
        self._fps.setText(f"{fps:.1f}")
        self._trend.push(fps)
        self._temperature.setText(_format_temperature(status.get("temperature", 0.0)))
        view_misses = status.get("view_misses")
        self._view_misses.setText(_EMPTY_VALUE if view_misses is None else str(int(view_misses)))
        _set_property(self._view_misses, "warn", bool(view_misses))

    def fix_widths(self):
        self._pill.fix_width([tr(key) for key in _CAMERA_STATE_KEYS])

    def apply_theme(self, dark: bool):
        self._trend.apply_theme(dark)


class _NetworkRow:
    """Una interfaz: su dirección IPv4, si tiene enlace y lo que recibe y envía en Mbps."""

    def __init__(self, name: str):
        self._address = _new_label(_EMPTY_VALUE, "tableValue")
        self._address.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._link = StatusChip(pill=True)
        self._link.set_state(CHIP_NEUTRAL, _EMPTY_VALUE)
        self._rx = _new_fixed_label(_EMPTY_VALUE)
        self._tx = _new_fixed_label(_EMPTY_VALUE)
        self.cells = (_new_label(name, "tableCell"), self._address, self._link,
                      self._rx, self._tx)

    @property
    def texts(self) -> tuple[str, str, str, str]:
        """(dirección, enlace, recibe, envía) tal como se ven."""
        return self._address.text(), self._link.text(), self._rx.text(), self._tx.text()

    def update(self, throughput: dict, interface: object):
        """`interface` es el `NetInterface` de esa interfaz, o None si ya no está."""
        if interface is None:
            self._address.setText(_EMPTY_VALUE)
            self._link.set_state(CHIP_NEUTRAL, _EMPTY_VALUE)
        else:
            self._address.setText(interface.ipv4 or _EMPTY_VALUE)
            if interface.is_up:
                self._link.set_state(CHIP_OK, tr("hw_net_up"))
            else:
                self._link.set_state(CHIP_NEUTRAL, tr("hw_net_down"))
        self._rx.setText(_format_mbps(throughput.get("rx_mbps", 0.0)))
        self._tx.setText(_format_mbps(throughput.get("tx_mbps", 0.0)))

    def fix_widths(self):
        self._link.fix_width([tr("hw_net_up"), tr("hw_net_down")])


class _Table(QGridLayout):
    """
    Una tabla liviana dentro de una tarjeta: una fila de rótulos y filas de celdas, con un
    filete entre filas. `stretches` reparte el ancho que sobra: 0 es «lo que pida».
    """

    def __init__(self, headers: tuple[str, ...], *, stretches: tuple[int, ...]):
        super().__init__()
        self.setHorizontalSpacing(_TABLE_COLUMN_GAP_PX)
        self.setVerticalSpacing(_TABLE_ROW_GAP_PX)
        self._columns = len(headers)
        for column, (text, stretch) in enumerate(zip(headers, stretches, strict=True)):
            self.addWidget(_new_label(text, "tableHeader"), 0, column)
            self.setColumnStretch(column, stretch)
        self._next_row = 1

    def add_row(self, cells: tuple[QWidget, ...]):
        self.addWidget(_new_divider(), self._next_row, 0, 1, self._columns)
        self._next_row += 1
        for column, cell in enumerate(cells):
            # Lo que se estira —una tendencia— llena su columna; el resto, a su ancho.
            if cell.sizePolicy().horizontalPolicy() == QSizePolicy.Expanding:
                self.addWidget(cell, self._next_row, column, Qt.AlignVCenter)
            else:
                self.addWidget(cell, self._next_row, column, Qt.AlignLeft | Qt.AlignVCenter)
        self._next_row += 1


# ── Piezas sueltas ──────────────────────────────────────────────────────────────

def _short_zone_name(zone: str) -> str:
    """«cpu-thermal» -> «CPU»: el sufijo lo repiten todas las zonas y no dice nada."""
    for suffix in _ZONE_SUFFIXES:
        if zone.lower().endswith(suffix):
            return zone[:-len(suffix)].upper()
    return zone


def _format_temperature(celsius: object) -> str:
    """«52.0 °C», o un guion si no hay lectura: 0 es lo que informa quien no tiene sensor."""
    value = float(celsius or 0)
    return f"{value:.1f} °C" if value else _EMPTY_VALUE


def _format_temperature_foot(celsius: object) -> str:
    value = float(celsius or 0)
    return tr("hw_temperature_foot").format(value=f"{value:.0f}") if value \
        else tr("hw_no_temperature")


def _fix_engine_width(pill: StatusChip):
    pill.fix_width([tr("hw_engine_active"), tr("hw_engine_idle")])


def _format_mbps(mbps: object) -> str:
    return f"{float(mbps or 0):.2f} Mbps"


def _build_card_header(title: str, description: str) -> QVBoxLayout:
    header = QVBoxLayout()
    header.setSpacing(2)
    header.addWidget(_new_label(title, "settingsSectionTitle"))
    description_label = _new_label(description, "settingsSectionDescription")
    description_label.setWordWrap(True)
    header.addWidget(description_label)
    header.addSpacing(10)
    return header


def _new_card() -> QWidget:
    card = QWidget()
    card.setObjectName("settingsCard")
    card.setAttribute(Qt.WA_StyledBackground, True)
    card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    return card


def _new_card_with_layout() -> tuple[QWidget, QVBoxLayout]:
    card = _new_card()
    layout = QVBoxLayout(card)
    layout.setContentsMargins(*_CARD_MARGINS_PX)
    layout.setSpacing(8)
    return card, layout


def _new_divider() -> QWidget:
    divider = QWidget()
    divider.setObjectName("settingsDivider")
    divider.setAttribute(Qt.WA_StyledBackground, True)
    divider.setFixedHeight(1)
    return divider


def _new_label(text: str, object_name: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    return label


def _new_fixed_label(text: str) -> QLabel:
    """Un número en monoespaciada: pasar de 10.0 a 9.8 no cambia su ancho."""
    label = _new_label(text, "tableValue")
    label.setTextFormat(Qt.PlainText)
    return label


def _set_property(label: QLabel, name: str, value: bool):
    """Deja la propiedad que pinta el QSS y le pide que la vuelva a leer."""
    if label.property(name) == value:
        return
    label.setProperty(name, value)
    label.style().unpolish(label)
    label.style().polish(label)
