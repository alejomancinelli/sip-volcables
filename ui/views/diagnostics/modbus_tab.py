"""
Pestaña de Modbus: la tabla de holding registers que publica el equipo y los que escribe
el PLC.

Una tarjeta, como la del mapa Modbus de la web: arriba el estado de los dos transportes, qué
es la tabla y «Copiar CSV»; en el medio la tabla a todo el ancho, sin borde propio —el borde
es el de la tarjeta—, y abajo los bits de la palabra de estado elegida. La dirección y el
valor van en monoespaciada y el valor alineado a la derecha, para que una columna de números
se lea de un vistazo; el de una palabra de estado, en hexadecimal.

Las filas salen de `SCHEMA.descriptions()`, que es el mapa único cargado desde
`register_map.yaml`: agregar o cambiar un registro allá se refleja acá solo, y no hay
una sola dirección escrita en este archivo. Los valores los empuja quien llena los
registros, con `update_values()`.

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
    QAbstractItemView, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from system.config_manager import ConfigManager
from system.formats import catalog
from system.modbus.export_map import build_csv
from system.modbus.registers import SCHEMA
from system.modbus.schema import to_plc_address

from ui import service_status, theme
from ui.strings import tr
from ui.views.diagnostics.abstract_tab import AbstractDiagnosticsTab
from ui.widgets.status_chip import StatusChip

_ROW_HEIGHT_PX = 28
_COLUMN_REGISTER = 0
_COLUMN_VALUE = 2
_EMPTY_VALUE = "---"
_MONO_POINT_SIZE = 10.0
_HEADER_MARGINS_PX = (20, 14, 20, 14)   # izquierda, arriba, derecha, abajo: la franja de arriba
_HEADER_SPACING_PX = 8
_BITS_MARGINS_PX = (20, 14, 20, 16)     # el panel de bits, abajo
_BITS_COLUMNS = 2
_BIT_ROW_SPACING_PX = 6
_BIT_COLUMN_SPACING_PX = 24
_COPIED_FEEDBACK_MS = 1500              # lo que dura «Copiado» en el botón


class ModbusTab(AbstractDiagnosticsTab):
    """Estado de los dos transportes, la tabla de registros y los bits de una palabra."""

    TITLE_KEY = "tab_modbus_registers"

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        self._row_by_address: dict[int, int] = {}
        self._address_by_row: dict[int, int] = {}
        self._values: dict[int, int] = {}
        self._words: dict[int, str] = SCHEMA.bitfields()
        self._descriptions = SCHEMA.descriptions()
        self._selected_address: int | None = None
        # A un punto más que el texto de la tabla: Ubuntu Mono dibuja más chico que Ubuntu.
        self._mono_font = QFont(theme.FONT_MONO)
        self._mono_font.setPointSizeF(_MONO_POINT_SIZE)

        self._chips = {
            service_status.SERVICE_MODBUS_TCP: StatusChip(),
            service_status.SERVICE_MODBUS_RTU: StatusChip(),
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
            is_selected_changed |= (address == self._selected_address
                                    and self._values.get(address) != int(value))
            self._values[address] = int(value)
            item = self._table.item(row, _COLUMN_VALUE)
            if item is not None:
                item.setText(self._format_value(address, int(value)))
        if is_selected_changed:
            self._refresh_bits()

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_header(self) -> QWidget:
        header = QWidget()
        row = QHBoxLayout(header)
        row.setContentsMargins(*_HEADER_MARGINS_PX)
        row.setSpacing(_HEADER_SPACING_PX)
        for chip in self._chips.values():
            row.addWidget(chip)
        row.addStretch()
        note = QLabel(tr("diag_modbus_table_note"))
        note.setObjectName("hintLabel")
        row.addWidget(note)
        self._copy_button = QPushButton(tr("diag_modbus_copy_csv"))
        self._copy_button.clicked.connect(self._copy_csv)
        row.addWidget(self._copy_button)
        return header

    def _build_table(self) -> QTableWidget:
        self._table = QTableWidget(0, 3)
        self._table.setObjectName("cardTable")
        self._table.setHorizontalHeaderLabels([
            tr("diag_modbus_register"), tr("diag_modbus_desc"), tr("diag_modbus_value"),
        ])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._table.horizontalHeaderItem(_COLUMN_VALUE).setTextAlignment(
            Qt.AlignRight | Qt.AlignVCenter)
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(_ROW_HEIGHT_PX)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._table.setShowGrid(False)

        for address, description in sorted(self._descriptions.items()):
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._row_by_address[address] = row
            self._address_by_row[row] = address
            for column, text in enumerate(
                    [to_plc_address(address), description, _EMPTY_VALUE]):
                item = QTableWidgetItem(text)
                # Sólo una palabra de estado se elige: es la única fila que tiene algo
                # más que mostrar.
                flags = Qt.ItemIsEnabled
                if address in self._words:
                    flags |= Qt.ItemIsSelectable
                item.setFlags(flags)
                if column in (_COLUMN_REGISTER, _COLUMN_VALUE):
                    item.setFont(self._mono_font)
                if column == _COLUMN_VALUE:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self._table.setItem(row, column, item)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        return self._table

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
        """Una palabra de estado en hexadecimal: sus bits se leen mejor así que en decimal."""
        return f"0x{value:04X}" if address in self._words else str(value)

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
        self._bits_title.setText(
            f"{to_plc_address(address)} · {self._descriptions.get(address, '')}")
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


def _build_divider() -> QWidget:
    divider = QWidget()
    divider.setObjectName("settingsDivider")
    divider.setAttribute(Qt.WA_StyledBackground, True)
    divider.setFixedHeight(1)
    return divider
