"""
Pestaña de logs: las últimas líneas de log en una tarjeta, con filtro, búsqueda y pausa.

    ┌──────────────────────────────────────────────────────────────────────────┐
    │ [Todos · 120│Depuración · 0│Información · 100│Avisos · 15│Errores · 5]   │
    │                                  [🔍 Buscar…        ] [Pausar] [Limpiar] │
    ├──────────────────────────────────────────────────────────────────────────┤
    │ Hora            Módulo     Nivel    Mensaje                              │
    │ …                                                                        │
    ├──────────────────────────────────────────────────────────────────────────┤
    │ 120 de 500 líneas · la más nueva arriba                                  │
    └──────────────────────────────────────────────────────────────────────────┘

La forma es la de Registros: la franja de controles arriba, la tabla a todo el ancho sin
borde propio y una línea al pie. El filtro es un control segmentado con cuántas líneas
guardadas hay de cada nivel —se ve que hubo errores sin tener que buscarlos—, y la búsqueda
mira el mensaje y el módulo. «Pausar» congela la tabla para leer una ráfaga; el pie dice
cuántas líneas llegaron mientras tanto.

No lee el archivo de log ni engancha un handler: recibe las líneas por `log_event()`.
Quien las publica es el cableado, que es el único que sabe qué handler del logger
alimenta a la GUI.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from system.config_manager import ConfigManager

from ui import theme
from ui.strings import tr
from ui.views.diagnostics.abstract_tab import AbstractDiagnosticsTab
from ui.widgets.log_table import FILTER_ALL, LEVELS, EventLogTable

_MAX_ROWS = 500
_HEADER_MARGINS_PX = (20, 14, 20, 14)   # izquierda, arriba, derecha, abajo
_FOOTER_MARGINS_PX = (20, 10, 20, 10)
_HEADER_SPACING_PX = 8
_SEARCH_WIDTH_PX = 260
_FILTER_PROPERTY = "selected"     # lo pinta el QSS: el filtro activo se ve distinto
_WARN_PROPERTY = "warn"

# Nivel -> clave de su rótulo en el filtro.
_LEVEL_LABEL_KEYS = {
    FILTER_ALL: "diag_log_all",
    "DEBUG": "diag_log_debug",
    "INFO": "diag_log_info",
    "WARNING": "diag_log_warning",
    "ERROR": "diag_log_error",
}


class LogsTab(AbstractDiagnosticsTab):
    """Tabla de logs con filtro, búsqueda y pausa. Ver el docstring del módulo."""

    TITLE_KEY = "tab_logs"
    IS_CENTERED = False

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        self._filter_buttons: dict[str, QPushButton] = {}
        self._active_filter = FILTER_ALL

        self._table = EventLogTable(max_rows=_MAX_ROWS, show_module=True)
        self._table.setObjectName("cardTable")

        card = QWidget()
        card.setObjectName("settingsCard")
        card.setAttribute(Qt.WA_StyledBackground, True)
        card_layout = QVBoxLayout(card)
        # Un píxel para que la tabla quede adentro del borde redondeado de la tarjeta.
        card_layout.setContentsMargins(1, 1, 1, 1)
        card_layout.setSpacing(0)
        card_layout.addWidget(self._build_header())
        card_layout.addWidget(_build_divider())
        card_layout.addWidget(self._table, stretch=1)
        card_layout.addWidget(_build_divider())
        card_layout.addWidget(self._build_footer())

        layout = QVBoxLayout(self)
        # Sin margen propio: la tarjeta arranca en el borde del encabezado de la página.
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(card)
        self._refresh_counts()

    # ── API pública ──────────────────────────────────────────────────────────

    def log_event(self, level: str, message: str, module: str = ""):
        self._table.append(level, message, module)
        self._refresh_counts()

    def apply_theme(self, dark: bool):
        super().apply_theme(dark)
        self._table.set_dark(dark)
        muted = theme.get_palette(dark)["hint_text"]
        self._search_action.setIcon(theme.get_icon("search", muted))
        self._refresh_pause_icon()

    # ── Armado ───────────────────────────────────────────────────────────────

    def _build_header(self) -> QWidget:
        header = QWidget()
        row = QHBoxLayout(header)
        row.setContentsMargins(*_HEADER_MARGINS_PX)
        row.setSpacing(_HEADER_SPACING_PX)
        row.addWidget(self._build_filter())
        row.addStretch()

        self._search = QLineEdit()
        self._search.setPlaceholderText(tr("diag_log_search"))
        self._search.setClearButtonEnabled(True)
        self._search.setFixedWidth(_SEARCH_WIDTH_PX)
        self._search_action = self._search.addAction(
            theme.get_icon("search", theme.get_palette(True)["hint_text"]),
            QLineEdit.LeadingPosition)
        self._search.textChanged.connect(self._on_search_changed)
        row.addWidget(self._search)

        self._pause_button = QPushButton(tr("diag_log_pause"))
        self._pause_button.setCheckable(True)
        self._pause_button.toggled.connect(self._on_pause_toggled)
        row.addWidget(self._pause_button)

        clear_button = QPushButton(tr("diag_log_clear"))
        # El rojo de lo que borra, con el alto de los botones de al lado.
        clear_button.setObjectName("resetButton")
        clear_button.clicked.connect(self._clear)
        row.addWidget(clear_button)
        return header

    def _build_filter(self) -> QWidget:
        """El control segmentado: el elegido es una tarjeta sobre el fondo apagado."""
        segmented = QWidget()
        segmented.setObjectName("segmentedControl")
        segmented.setAttribute(Qt.WA_StyledBackground, True)
        row = QHBoxLayout(segmented)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(2)
        for level in (FILTER_ALL, *LEVELS):
            button = QPushButton()
            button.setObjectName("segmentButton")
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _, chosen=level: self._set_filter(chosen))
            self._filter_buttons[level] = button
            row.addWidget(button)
        self._refresh_filter_buttons()
        return segmented

    def _build_footer(self) -> QWidget:
        footer = QWidget()
        row = QHBoxLayout(footer)
        row.setContentsMargins(*_FOOTER_MARGINS_PX)
        self._shown_label = QLabel()
        self._shown_label.setObjectName("hintLabel")
        self._paused_label = QLabel()
        self._paused_label.setObjectName("hintLabel")
        row.addWidget(self._shown_label)
        row.addStretch()
        row.addWidget(self._paused_label)
        return footer

    # ── Internos ─────────────────────────────────────────────────────────────

    def _set_filter(self, level: str):
        self._active_filter = level
        self._table.set_filter(level)
        self._refresh_filter_buttons()
        self._refresh_counts()

    def _on_search_changed(self, text: str):
        self._table.set_search(text)
        self._refresh_counts()

    def _on_pause_toggled(self, is_paused: bool):
        self._table.set_paused(is_paused)
        self._pause_button.setText(tr("diag_log_resume" if is_paused else "diag_log_pause"))
        self._refresh_pause_icon()
        self._refresh_counts()

    def _clear(self):
        self._table.clear_log()
        self._refresh_counts()

    def _refresh_pause_icon(self):
        name = "player-play" if self._table.is_paused else "player-pause"
        self._pause_button.setIcon(
            theme.get_icon(name, theme.get_palette(self._is_dark)["hint_text"]))

    def _refresh_counts(self):
        """Los contadores del filtro y el pie. Cuentan lo guardado, no lo que se ve."""
        counts = self._table.get_counts()
        counts[FILTER_ALL] = self._table.total_count
        for level, button in self._filter_buttons.items():
            button.setText(tr("diag_filter_count").format(
                label=tr(_LEVEL_LABEL_KEYS[level]), count=counts[level]))
        self._shown_label.setText(tr("diag_log_shown").format(
            shown=self._table.rowCount(), total=self._table.total_count,
            max_rows=self._table.max_rows))
        is_paused = self._table.is_paused
        self._paused_label.setVisible(is_paused)
        if is_paused:
            self._paused_label.setText(tr("diag_log_paused").format(
                count=self._table.pending_count))
        _set_property(self._paused_label, _WARN_PROPERTY, is_paused)

    def _refresh_filter_buttons(self):
        for level, button in self._filter_buttons.items():
            _set_property(button, _FILTER_PROPERTY, level == self._active_filter)


def _build_divider() -> QWidget:
    divider = QWidget()
    divider.setObjectName("settingsDivider")
    divider.setAttribute(Qt.WA_StyledBackground, True)
    divider.setFixedHeight(1)
    return divider


def _set_property(widget: QWidget, name: str, value: bool):
    """Deja la propiedad que pinta el QSS y le pide que la vuelva a leer."""
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)
