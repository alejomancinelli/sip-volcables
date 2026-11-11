"""
Tabla de eventos: las últimas N líneas de log, la más nueva arriba.

Buffer FIFO propio: la tabla se reconstruye desde él, así que cambiar de tema, de filtro o de
búsqueda no pierde historia. Una línea nueva no reconstruye nada: agrega su fila arriba y,
si el buffer estaba lleno, saca la de abajo. Reconstruir por línea eran cientos de items
por evento en el hilo de la GUI, justo cuando el log viene en ráfaga. Los colores por
nivel salen de `ui/theme.py`, porque el QSS no llega a los items de una QTableWidget.

Los niveles se agrupan en los cuatro de `LEVELS`: CRITICAL se filtra y se pinta como ERROR,
y uno que la tabla no conoce, como INFO. La línea conserva el nivel con el que llegó.

**En pausa la tabla no se mueve, pero el buffer sigue llenándose.** Es para leer una ráfaga
sin que cada línea nueva corra la que se está leyendo; al reanudar se reconstruye con todo lo
que llegó, y lo más viejo que se cayó del buffer mientras tanto ya no está.
"""

from collections import deque
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem,
)

from ui import theme
from ui.strings import tr

# Niveles que la tabla sabe filtrar y colorear, del más leve al más grave.
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
FILTER_ALL = "ALL"

_LEVEL_GROUPS = {"CRITICAL": "ERROR", "FATAL": "ERROR", "WARN": "WARNING"}
_DEFAULT_LEVEL = "INFO"
_ROW_HEIGHT_PX = 26
_MONO_POINT_SIZE = 10.0       # un punto más que el texto: Ubuntu Mono dibuja más chico
_TIMESTAMP_FORMAT = "%d/%m %H:%M:%S"


def get_level_group(level: str) -> str:
    """El nivel de `LEVELS` con el que se filtra y se pinta una línea de ese nivel."""
    level = level.upper()
    level = _LEVEL_GROUPS.get(level, level)
    return level if level in LEVELS else _DEFAULT_LEVEL


class EventLogTable(QTableWidget):
    """
    Tabla de eventos FIFO de hasta `max_rows` filas.

    Con `show_module=True` agrega la columna del módulo entre la hora y el nivel, que
    es lo que quiere la pestaña de logs; sin ella la tabla entra en un lugar angosto.
    """

    def __init__(self, parent=None, *, max_rows: int = 20, show_module: bool = False):
        super().__init__(0, 4 if show_module else 3, parent)
        self._entries: deque = deque(maxlen=max_rows)
        self._show_module = show_module
        self._filter = ""
        self._search = ""
        self._is_paused = False
        self._pending_count = 0
        self._counts = dict.fromkeys(LEVELS, 0)     # al día con cada línea: no se recorre
        self._level_colors = theme.get_log_level_colors(True)
        self._mono_font = QFont(theme.FONT_MONO)
        self._mono_font.setPointSizeF(_MONO_POINT_SIZE)

        self._build_headers()
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(_ROW_HEIGHT_PX)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setAlternatingRowColors(False)
        self.setShowGrid(False)
        self.setWordWrap(False)

    # ── API pública ──────────────────────────────────────────────────────────

    @property
    def max_rows(self) -> int:
        return self._entries.maxlen

    @property
    def total_count(self) -> int:
        """Las líneas guardadas, se vean o no."""
        return len(self._entries)

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    @property
    def pending_count(self) -> int:
        """Las líneas que llegaron en pausa y todavía no se muestran."""
        return self._pending_count

    def append(self, level: str, message: str, module: str = ""):
        """Agrega una entrada. `level` es el del logger; ver `get_level_group()`."""
        entry = (datetime.now().strftime(_TIMESTAMP_FORMAT), level.upper(), message, module)
        dropped = self._entries[0] if len(self._entries) == self._entries.maxlen else None
        self._entries.append(entry)
        self._counts[get_level_group(entry[1])] += 1
        if dropped is not None:
            self._counts[get_level_group(dropped[1])] -= 1
        if self._is_paused:
            self._pending_count += 1
            return
        if dropped is not None and self._is_shown(dropped) and self.rowCount() > 0:
            self.removeRow(self.rowCount() - 1)
        if self._is_shown(entry):
            self._insert_entry(0, entry)

    def set_filter(self, level: str):
        """Muestra sólo ese nivel. FILTER_ALL o vacío muestra todo."""
        self._filter = "" if not level or level == FILTER_ALL else get_level_group(level)
        self._rebuild()

    def set_search(self, text: str):
        """Muestra sólo las líneas cuyo mensaje o módulo contienen `text`, sin mayúsculas."""
        self._search = text.strip().casefold()
        self._rebuild()

    def set_paused(self, is_paused: bool):
        """Congela o reanuda la tabla. Al reanudar muestra lo que llegó en pausa."""
        if is_paused == self._is_paused:
            return
        self._is_paused = is_paused
        if not is_paused:
            self._rebuild()

    def get_counts(self) -> dict[str, int]:
        """Cuántas líneas guardadas hay de cada nivel de `LEVELS`, con o sin filtro."""
        return dict(self._counts)

    def clear_log(self):
        self._entries.clear()
        self._counts = dict.fromkeys(LEVELS, 0)
        self._pending_count = 0
        self.setRowCount(0)

    def set_dark(self, dark: bool):
        self._level_colors = theme.get_log_level_colors(dark)
        self._rebuild()

    # ── Internos ─────────────────────────────────────────────────────────────

    def _build_headers(self):
        headers = [tr("diag_log_time")]
        if self._show_module:
            headers.append(tr("diag_log_module"))
        headers += [tr("diag_log_level"), tr("diag_log_message")]
        self.setHorizontalHeaderLabels(headers)
        header = self.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for column in range(len(headers) - 1):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(len(headers) - 1, QHeaderView.Stretch)

    def _rebuild(self):
        self._pending_count = 0
        self.setUpdatesEnabled(False)
        self.setRowCount(0)
        for entry in reversed(self._entries):
            if self._is_shown(entry):
                self._insert_entry(self.rowCount(), entry)
        self.setUpdatesEnabled(True)

    def _is_shown(self, entry: tuple) -> bool:
        _, level, message, module = entry
        if self._filter and get_level_group(level) != self._filter:
            return False
        return (not self._search or self._search in message.casefold()
                or self._search in module.casefold())

    def _insert_entry(self, row: int, entry: tuple):
        timestamp, level, message, module = entry
        self.insertRow(row)
        foreground, background = self._level_colors[get_level_group(level)]
        # (texto, si va en monoespaciada): la hora y el nivel se leen en columna.
        columns = [(timestamp, True)]
        if self._show_module:
            columns.append((module, False))
        columns += [(level, True), (message, False)]
        for column, (text, is_mono) in enumerate(columns):
            item = QTableWidgetItem(text)
            item.setForeground(foreground)
            item.setBackground(background)
            item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            if is_mono:
                item.setFont(self._mono_font)
            self.setItem(row, column, item)
        # Un mensaje largo se corta en la columna; entero, en el tooltip.
        self.item(row, len(columns) - 1).setToolTip(message)
