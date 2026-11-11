"""
Pestaña de Modbus: la tabla de holding registers que publica el equipo y los que escribe
el PLC.

    ┌──────────────────────────────────────────────────────────────────────────┐
    │ ⓘ Mapa de registros para integrar el equipo…  [TCP: Activo] [RTU] [Copiar CSV]
    │ [Todos · 21│Lectura · 20│Escribe el PLC · 1│Palabras de estado · 6] [🔍 Buscar…]
    ├──────────────────────────────────────────────────────────────────────────┤
    │ Registro  Tipo            Descripción               Unidad  Crudo   Valor │
    │ 40052     [Lectura]       Temperatura de la cámara 1 °C ×10   415  41.5 °C │
    │                           camera_1_temperature_c                          │
    ├──────────────────────────────────────────────────────────────────────────┤
    │ 40001 · Estado del sistema               0x0035 · 0000 0000 0011 0101    │
    │ ● 0 Modelo de inferencia no cargado     ○ 3 …                            │
    └──────────────────────────────────────────────────────────────────────────┘

Una tarjeta, como el mapa Modbus de la web: arriba qué es la tabla, el estado de los dos
transportes y «Copiar CSV», y debajo el filtro por tipo y la búsqueda; en el medio la tabla
a todo el ancho, sin borde propio —el borde es el de la tarjeta—, y abajo los bits de la
palabra de estado elegida.

Cada fila dice lo mismo que la tabla del integrador (`docs/modbus_map.md`): la dirección con
la que el PLC la pide, si la lee o la escribe, qué es —con el nombre del registro debajo,
que es el contrato con el integrador—, su unidad y su escala, y el valor dos veces: **crudo**,
el entero que lee el PLC, y **convertido** a la unidad física con `SCHEMA.decode()`, que es el
dueño de la escala. Una palabra de estado se muestra en hexadecimal, que es como se leen sus
bits. Las direcciones y los números van en monoespaciada, alineados a la derecha.

Las filas salen de `SCHEMA.registers`, el mapa único cargado desde `register_map.yaml`:
agregar o cambiar un registro allá se refleja acá solo, y no hay una sola dirección escrita
en este archivo. Los valores los empuja quien llena los registros, con `update_values()`.

**Los bits no se corren acá.** Una fila con `bitfield` en el mapa es una palabra de estado, y
qué dice cada bit lo contesta `system/formats/catalog.py`, que lo toma del módulo dueño de
esa palabra. Las descripciones de los bits están en castellano, como en el log: son de esos
módulos y no de la tabla de idiomas. Una palabra que el catálogo no conoce muestra sus bits
prendidos sin descripción.

«Copiar CSV» copia `export_map.build_csv()`, el mismo texto que `docs/modbus_map.csv`: lo que
el integrador pega en su planilla es el documento, no otra versión de él.
"""

from PySide6.QtCore import Qt, QTimer, Slot
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from system.config_manager import ConfigManager
from system.formats import catalog
from system.modbus.export_map import build_csv
from system.modbus.registers import SCHEMA
from system.modbus.schema import PRODUCER_PLC, Reg, to_plc_address

from ui import service_status, theme
from ui.strings import tr
from ui.views.diagnostics.abstract_tab import AbstractDiagnosticsTab
from ui.widgets.status_chip import CHIP_INFO, CHIP_NEUTRAL, StatusChip

_ROW_HEIGHT_PX = 44                     # dos líneas: la descripción y el nombre del registro
_COLUMN_REGISTER = 0
_COLUMN_TYPE = 1
_COLUMN_DESCRIPTION = 2
_COLUMN_UNIT = 3
_COLUMN_RAW = 4
_COLUMN_VALUE = 5
_COLUMN_COUNT = 6
_EMPTY_VALUE = "—"
_MONO_POINT_SIZE = 10.0
_HEADER_MARGINS_PX = (20, 14, 20, 14)   # izquierda, arriba, derecha, abajo: la franja de arriba
_HEADER_SPACING_PX = 8
_HEADER_ROW_GAP_PX = 12
_SEARCH_WIDTH_PX = 260
# El relleno de `QTableWidget#cardTable::item` en el QSS. A una celda con widget se le
# descuenta, pero la columna no lo suma al medirse: la de tipo se mide a mano.
_ITEM_PADDING_PX = 9
# El de una celda con widget: con 3 px más que el relleno, el texto arranca donde el de su
# encabezado.
_CELL_MARGINS_PX = (3, 0, 3, 0)
_BITS_MARGINS_PX = (20, 14, 20, 16)     # el panel de bits, abajo
_BITS_COLUMNS = 2
_BIT_ROW_SPACING_PX = 6
_BIT_COLUMN_SPACING_PX = 24
_COPIED_FEEDBACK_MS = 1500              # lo que dura «Copiado» en el botón
_FILTER_PROPERTY = "selected"           # lo pinta el QSS: el filtro activo se ve distinto

# Los filtros, en el orden del control segmentado: clave -> rótulo.
_FILTER_ALL = "all"
_FILTER_READ = "read"
_FILTER_WRITE = "write"
_FILTER_WORDS = "words"
_FILTER_LABEL_KEYS = {
    _FILTER_ALL: "diag_modbus_filter_all",
    _FILTER_READ: "diag_modbus_type_read",
    _FILTER_WRITE: "diag_modbus_type_write",
    _FILTER_WORDS: "diag_modbus_filter_words",
}


class ModbusTab(AbstractDiagnosticsTab):
    """Estado de los dos transportes, la tabla de registros y los bits de una palabra."""

    TITLE_KEY = "tab_modbus_registers"

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        self._registers: dict[int, Reg] = {reg.addr: reg for reg in SCHEMA.registers}
        self._row_by_address: dict[int, int] = {}
        self._address_by_row: dict[int, int] = {}
        self._values: dict[int, int] = {}
        self._words: dict[int, str] = SCHEMA.bitfields()
        self._selected_address: int | None = None
        self._filter = _FILTER_ALL
        self._filter_buttons: dict[str, QPushButton] = {}
        self._type_pills: list[StatusChip] = []
        self._are_widths_fixed = False
        # A un punto más que el texto de la tabla: Ubuntu Mono dibuja más chico que Ubuntu.
        self._mono_font = QFont(theme.FONT_MONO)
        self._mono_font.setPointSizeF(_MONO_POINT_SIZE)

        self._chips = {
            service_status.SERVICE_MODBUS_TCP: StatusChip(pill=True),
            service_status.SERVICE_MODBUS_RTU: StatusChip(pill=True),
        }
        for service in self._chips:
            self.set_service_status(service, "")

        card = QWidget()
        card.setObjectName("settingsCard")
        card.setAttribute(Qt.WA_StyledBackground, True)
        card_layout = QVBoxLayout(card)
        # Un píxel para que la tabla quede adentro del borde redondeado de la tarjeta.
        card_layout.setContentsMargins(1, 1, 1, 1)
        card_layout.setSpacing(0)
        card_layout.addWidget(self._build_header())
        card_layout.addWidget(_build_divider())
        card_layout.addWidget(self._build_table(), stretch=1)
        card_layout.addWidget(_build_divider())
        card_layout.addWidget(self._build_bits_panel())

        layout = QVBoxLayout(self)
        # Sin margen propio: la tarjeta arranca en el borde del encabezado de la página.
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(card)
        self._apply_filter()
        self._refresh_bits()

    # ── API pública ──────────────────────────────────────────────────────────

    def set_service_status(self, service: str, status: str):
        """Actualiza el chip de uno de los dos transportes. Ignora otro servicio."""
        chip = self._chips.get(service)
        if chip is None:
            return
        text, chip_state = service_status.describe(service, status)
        chip.set_state(chip_state, text)

    @Slot(object)
    def update_values(self, values: dict):
        """Slot para {dirección: valor}. Las direcciones que no están en el mapa se ignoran."""
        is_selected_changed = False
        for address, value in values.items():
            address = int(address)
            row = self._row_by_address.get(address)
            if row is None:
                continue
            value = int(value)
            is_selected_changed |= (address == self._selected_address
                                    and self._values.get(address) != value)
            self._values[address] = value
            self._table.item(row, _COLUMN_RAW).setText(str(value))
            self._table.item(row, _COLUMN_VALUE).setText(self._format_value(address, value))
        if is_selected_changed:
            self._refresh_bits()

    def apply_theme(self, dark: bool):
        """Los íconos se pintan al cargarlos y los items apagados, por código: el QSS no llega."""
        super().apply_theme(dark)
        palette = theme.get_palette(dark)
        self._info_icon.setPixmap(
            theme.get_icon("info-circle", theme.get_tokens(dark)["brand"]).pixmap(16, 16))
        self._copy_button.setIcon(theme.get_icon("copy", palette["hint_text"]))
        self._search_action.setIcon(theme.get_icon("search", palette["hint_text"]))
        muted = theme.get_color("hint_text", dark)
        for row in range(self._table.rowCount()):
            for column in (_COLUMN_UNIT, _COLUMN_RAW):
                self._table.item(row, column).setForeground(muted)

    def showEvent(self, event):
        """
        Fija el ancho de las píldoras de tipo la primera vez que se ve: recién ahí tienen la
        tipografía y el relleno del QSS con los que se miden. Todas iguales: la columna se lee
        como una grilla.
        """
        super().showEvent(event)
        if self._are_widths_fixed:
            return
        self._are_widths_fixed = True
        texts = [tr("diag_modbus_type_read"), tr("diag_modbus_type_write")]
        for pill in self._type_pills:
            pill.fix_width(texts)
        if self._type_pills:
            header = self._table.horizontalHeader()
            header.setSectionResizeMode(_COLUMN_TYPE, QHeaderView.Fixed)
            header.resizeSection(_COLUMN_TYPE, self._type_pills[0].width() + 2 * (
                _ITEM_PADDING_PX + _CELL_MARGINS_PX[0]))

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_header(self) -> QWidget:
        header = QWidget()
        layout = QVBoxLayout(header)
        layout.setContentsMargins(*_HEADER_MARGINS_PX)
        layout.setSpacing(_HEADER_ROW_GAP_PX)

        top = QHBoxLayout()
        top.setSpacing(_HEADER_SPACING_PX)
        self._info_icon = QLabel()
        top.addWidget(self._info_icon)
        note = QLabel(tr("diag_modbus_table_note"))
        note.setObjectName("hintLabel")
        top.addWidget(note)
        top.addStretch()
        for chip in self._chips.values():
            top.addWidget(chip)
        self._copy_button = QPushButton(tr("diag_modbus_copy_csv"))
        self._copy_button.clicked.connect(self._copy_csv)
        top.addWidget(self._copy_button)
        layout.addLayout(top)

        tools = QHBoxLayout()
        tools.setSpacing(_HEADER_SPACING_PX)
        tools.addWidget(self._build_filter())
        tools.addStretch()
        self._search = QLineEdit()
        self._search.setPlaceholderText(tr("diag_modbus_search"))
        self._search.setClearButtonEnabled(True)
        self._search.setFixedWidth(_SEARCH_WIDTH_PX)
        self._search_action = self._search.addAction(theme.get_icon(
            "search", theme.get_palette(True)["hint_text"]), QLineEdit.LeadingPosition)
        self._search.textChanged.connect(lambda _: self._apply_filter())
        tools.addWidget(self._search)
        layout.addLayout(tools)
        return header

    def _build_filter(self) -> QWidget:
        """El control segmentado, con cuántas filas hay de cada tipo."""
        counts = {_FILTER_ALL: len(self._registers)}
        for key in (_FILTER_READ, _FILTER_WRITE, _FILTER_WORDS):
            counts[key] = sum(_matches_filter(reg, key) for reg in self._registers.values())
        segmented = QWidget()
        segmented.setObjectName("segmentedControl")
        segmented.setAttribute(Qt.WA_StyledBackground, True)
        row = QHBoxLayout(segmented)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(2)
        for key, label_key in _FILTER_LABEL_KEYS.items():
            button = QPushButton(tr("diag_filter_count").format(label=tr(label_key),
                                                                count=counts[key]))
            button.setObjectName("segmentButton")
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _, chosen=key: self._set_filter(chosen))
            self._filter_buttons[key] = button
            row.addWidget(button)
        return segmented

    def _build_table(self) -> QTableWidget:
        self._table = QTableWidget(0, _COLUMN_COUNT)
        self._table.setObjectName("cardTable")
        self._table.setHorizontalHeaderLabels([
            tr("diag_modbus_register"), tr("diag_modbus_type"), tr("diag_modbus_desc"),
            tr("diag_modbus_unit"), tr("diag_modbus_raw"), tr("diag_modbus_value"),
        ])
        header = self._table.horizontalHeader()
        for column in range(_COLUMN_COUNT):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(_COLUMN_DESCRIPTION, QHeaderView.Stretch)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for column in (_COLUMN_RAW, _COLUMN_VALUE):
            self._table.horizontalHeaderItem(column).setTextAlignment(
                Qt.AlignRight | Qt.AlignVCenter)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_ROW_HEIGHT_PX)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setShowGrid(False)
        self._table.setWordWrap(False)

        for address in sorted(self._registers):
            self._add_row(self._registers[address])
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        return self._table

    def _add_row(self, reg: Reg):
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._row_by_address[reg.addr] = row
        self._address_by_row[row] = reg.addr
        texts = {
            _COLUMN_REGISTER: to_plc_address(reg.addr),
            _COLUMN_TYPE: "",
            _COLUMN_DESCRIPTION: "",
            _COLUMN_UNIT: _format_unit(reg),
            _COLUMN_RAW: _EMPTY_VALUE,
            _COLUMN_VALUE: _EMPTY_VALUE,
        }
        for column, text in texts.items():
            item = QTableWidgetItem(text)
            # Sólo una palabra de estado se elige: es la única fila que tiene algo más que
            # mostrar.
            flags = Qt.ItemIsEnabled
            if reg.addr in self._words:
                flags |= Qt.ItemIsSelectable
            item.setFlags(flags)
            if column in (_COLUMN_REGISTER, _COLUMN_UNIT, _COLUMN_RAW, _COLUMN_VALUE):
                item.setFont(self._mono_font)
            if column in (_COLUMN_RAW, _COLUMN_VALUE):
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self._table.setItem(row, column, item)
        pill = _build_type_pill(reg)
        self._type_pills.append(pill)
        self._table.setCellWidget(row, _COLUMN_TYPE, _wrap_cell(pill))
        self._table.setCellWidget(row, _COLUMN_DESCRIPTION, _build_description_cell(reg))

    def _build_bits_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(*_BITS_MARGINS_PX)
        layout.setSpacing(10)
        title_row = QHBoxLayout()
        self._bits_title = QLabel()
        self._bits_title.setObjectName("bitWordTitle")
        self._bits_value = QLabel()
        self._bits_value.setObjectName("bitWordValue")
        self._bits_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        title_row.addWidget(self._bits_title)
        title_row.addStretch()
        title_row.addWidget(self._bits_value)
        layout.addLayout(title_row)
        self._bits_grid_host = QWidget()
        self._bits_grid = QGridLayout(self._bits_grid_host)
        self._bits_grid.setContentsMargins(0, 0, 0, 0)
        self._bits_grid.setHorizontalSpacing(_BIT_COLUMN_SPACING_PX)
        self._bits_grid.setVerticalSpacing(_BIT_ROW_SPACING_PX)
        layout.addWidget(self._bits_grid_host)
        return panel

    # ── Internos ─────────────────────────────────────────────────────────────

    def _format_value(self, address: int, value: int) -> str:
        """
        El valor en su unidad física, convertido por el esquema. Una palabra de estado, en
        hexadecimal: sus bits se leen mejor así que en decimal.
        """
        if address in self._words:
            return f"0x{value:04X}"
        reg = self._registers[address]
        physical = SCHEMA.decode(reg.name, value)
        if isinstance(physical, bool):
            return tr("diag_modbus_true" if physical else "diag_modbus_false")
        text = f"{physical:.{_decimals(reg.scale)}f}" if reg.scale != 1 else str(physical)
        return f"{text} {reg.unit}".strip()

    def _set_filter(self, key: str):
        self._filter = key
        self._apply_filter()

    def _apply_filter(self):
        """Esconde las filas que no son del tipo elegido o no tienen el texto buscado."""
        for key, button in self._filter_buttons.items():
            _set_property(button, _FILTER_PROPERTY, key == self._filter)
        query = self._search.text().strip().casefold()
        for address, row in self._row_by_address.items():
            reg = self._registers[address]
            is_shown = _matches_filter(reg, self._filter) and (
                not query or any(query in text.casefold() for text in (
                    to_plc_address(address), str(address), reg.name, reg.desc)))
            self._table.setRowHidden(row, not is_shown)

    def _on_selection_changed(self):
        rows = {index.row() for index in self._table.selectedIndexes()}
        self._selected_address = (self._address_by_row.get(rows.pop()) if rows else None)
        self._refresh_bits()

    def _refresh_bits(self):
        """Arma el panel de bits de la palabra elegida, o la invitación a elegir una."""
        while self._bits_grid.count():
            item = self._bits_grid.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()

        address = self._selected_address
        if address is None:
            self._bits_title.setText(tr("diag_modbus_bits_prompt"))
            self._bits_title.setProperty("muted", True)
            self._bits_value.clear()
            self._bits_grid_host.hide()
            self._repolish(self._bits_title)
            return

        self._bits_title.setProperty("muted", False)
        self._repolish(self._bits_title)
        self._bits_title.setText(f"{to_plc_address(address)} · {self._registers[address].desc}")
        value = self._values.get(address)
        if value is None:
            self._bits_value.setText(_EMPTY_VALUE)
            self._bits_grid_host.hide()
            return
        binary = " ".join(f"{value:016b}"[index:index + 4] for index in range(0, 16, 4))
        self._bits_value.setText(f"0x{value:04X} · {binary}")

        bits = catalog.list_bits(self._words[address], value)
        if bits is None:
            # Una palabra que el catálogo no conoce: sus bits prendidos, sin descripción.
            bits = [catalog.Bit(index, "", True)
                    for index in range(catalog.WORD_BITS) if value >> index & 1]
        rows_per_column = (len(bits) + _BITS_COLUMNS - 1) // _BITS_COLUMNS
        for position, bit in enumerate(bits):
            self._bits_grid.addWidget(_build_bit(bit), position % max(1, rows_per_column),
                                      position // max(1, rows_per_column))
        for column in range(_BITS_COLUMNS):
            self._bits_grid.setColumnStretch(column, 1)
        self._bits_grid_host.setVisible(bool(bits))

    def _copy_csv(self):
        QGuiApplication.clipboard().setText(build_csv())
        self._copy_button.setText(tr("diag_modbus_copied"))
        QTimer.singleShot(_COPIED_FEEDBACK_MS,
                          lambda: self._copy_button.setText(tr("diag_modbus_copy_csv")))

    @staticmethod
    def _repolish(widget: QWidget):
        """Vuelve a aplicar el QSS después de cambiar una propiedad que lo selecciona."""
        widget.style().unpolish(widget)
        widget.style().polish(widget)


def _build_bit(bit: catalog.Bit) -> QWidget:
    """Una fila del panel: el punto, el número de bit y lo que significa prendido."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    dot = QLabel()
    dot.setObjectName("bitDot")
    dot.setProperty("on", bit.is_set)
    index = QLabel(f"{bit.index:>2}")
    index.setObjectName("bitIndex")
    description = QLabel(bit.description or tr("diag_modbus_bit_undefined"))
    description.setObjectName("bitDescription")
    description.setProperty("on", bit.is_set)
    layout.addWidget(dot, alignment=Qt.AlignVCenter)
    layout.addWidget(index)
    layout.addWidget(description, stretch=1)
    return row


def _matches_filter(reg: Reg, key: str) -> bool:
    if key == _FILTER_READ:
        return reg.producer != PRODUCER_PLC
    if key == _FILTER_WRITE:
        return reg.producer == PRODUCER_PLC
    if key == _FILTER_WORDS:
        return bool(reg.bitfield)
    return True


def _format_unit(reg: Reg) -> str:
    """«°C ×10»: la unidad y, si la hay, la escala con la que viaja; un guion sin ninguna."""
    parts = [reg.unit] if reg.unit else []
    if reg.scale != 1:
        parts.append(f"×{reg.scale}")
    return " ".join(parts) or _EMPTY_VALUE


def _decimals(scale: int) -> int:
    """Los decimales que da una escala: ×10 uno, ×100 dos. Otra escala, dos."""
    text = str(scale)
    return len(text) - 1 if text.startswith("1") and set(text[1:]) <= {"0"} else 2


def _build_type_pill(reg: Reg) -> StatusChip:
    """La píldora del tipo: lo que el PLC escribe en el color de la marca, lo que lee apagado."""
    pill = StatusChip(pill=True)
    if reg.producer == PRODUCER_PLC:
        pill.set_state(CHIP_INFO, tr("diag_modbus_type_write"))
    else:
        pill.set_state(CHIP_NEUTRAL, tr("diag_modbus_type_read"))
    return pill


def _build_description_cell(reg: Reg) -> QWidget:
    """Qué es y, debajo, el nombre del registro: el que usa el integrador y el CSV."""
    description = QLabel(reg.desc)
    description.setObjectName("tableCell")
    description.setToolTip(reg.desc)
    name = QLabel(reg.name)
    name.setObjectName("tableDetail")
    cell = QWidget()
    layout = QVBoxLayout(cell)
    layout.setContentsMargins(*_CELL_MARGINS_PX)
    layout.setSpacing(1)
    layout.addStretch()
    layout.addWidget(description)
    layout.addWidget(name)
    layout.addStretch()
    # Los clics los recibe la tabla: la fila se elige igual tocando la celda con widget.
    cell.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    return cell


def _wrap_cell(widget: QWidget) -> QWidget:
    cell = QWidget()
    layout = QHBoxLayout(cell)
    layout.setContentsMargins(*_CELL_MARGINS_PX)
    layout.addWidget(widget, alignment=Qt.AlignVCenter)
    layout.addStretch()
    cell.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    return cell


def _set_property(widget: QWidget, name: str, value: bool):
    """Deja la propiedad que pinta el QSS y le pide que la vuelva a leer."""
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def _build_divider() -> QWidget:
    divider = QWidget()
    divider.setObjectName("settingsDivider")
    divider.setAttribute(Qt.WA_StyledBackground, True)
    divider.setFixedHeight(1)
    return divider
