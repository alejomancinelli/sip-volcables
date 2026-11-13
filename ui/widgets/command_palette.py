"""
La paleta de búsqueda de la barra superior: un campo y, debajo, lo que coincide, agrupado.

    ┌───────────────────────────────────────────────────────┐
    │ 🔍 exposic                                            │
    ├───────────────────────────────────────────────────────┤
    │ AJUSTES                                               │
    │ ⚙ Exposición    Cámaras › Entrada › Adquisición       │
    │ ⚙ Exposición    Cámaras › Salida › Adquisición        │
    ├───────────────────────────────────────────────────────┤
    │ ↑↓ para moverse · Enter para abrir · Esc para cerrar  │
    └───────────────────────────────────────────────────────┘

Es la de la web (el «Ctrl K» de la barra superior): se abre sobre la ventana, se busca
escribiendo, se elige con el teclado o el mouse y se cierra sola al elegir o al hacer clic
afuera. Con el campo vacío muestra las entradas «de siempre» —las páginas y las acciones—, y
al escribir busca en todas.

No sabe qué es cada entrada: recibe `PaletteEntry` ya armadas y devuelve la elegida por
`entry_chosen`. Qué hace al elegirla —ir a una página, llegar a un campo— lo decide quien la
abre. `filter_entries()` es la regla de la búsqueda y no depende de Qt.

**La búsqueda ignora mayúsculas y tildes**: en una planta se escribe «exposicion» y en otro
idioma la tilde ni existe. Cada palabra tiene que aparecer en el título, el camino o la
aclaración, y arriba va lo que empieza como se escribió.
"""

import unicodedata
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QSizePolicy, QVBoxLayout,
    QWidget,
)

from ui import theme
from ui.strings import tr

_WIDTH_PX = 620
_LIST_MAX_HEIGHT_PX = 420
_TOP_OFFSET_PCT = 0.12      # a qué altura de la ventana se abre: arriba, como en la web
_ROW_HEIGHT_PX = 36
_GROUP_HEIGHT_PX = 30
_ICON_PX = 16
_MAX_RESULTS = 60
_ROLE_ENTRY = Qt.UserRole

# Puntaje de una coincidencia: menos es mejor.
_SCORE_PREFIX = 0           # el título empieza como lo escrito
_SCORE_WORD = 1             # una palabra del título empieza con la primera palabra escrita
_SCORE_TITLE = 2            # todas las palabras están en el título
_SCORE_ELSEWHERE = 3        # alguna sólo en el camino o la aclaración


@dataclass(frozen=True)
class PaletteEntry:
    """Una entrada de la paleta. `target` vuelve tal cual por `entry_chosen`: la paleta no lo lee."""

    group: str              # rótulo del grupo: «Ir a», «Ajustes», «Acciones»
    title: str
    detail: str = ""        # el camino, en gris al lado del título
    icon: str = ""          # ícono de Tabler
    keywords: str = ""      # se busca también acá, sin mostrarse: la aclaración de un campo
    target: object = None


def normalize(text: str) -> str:
    """En minúsculas y sin tildes: «Exposición» y «exposicion» son lo mismo."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def filter_entries(entries: list[PaletteEntry], query: str,
                   limit: int = _MAX_RESULTS) -> list[PaletteEntry]:
    """
    Las entradas en las que aparece cada palabra de `query`, las mejores primero y, a igual
    puntaje, en el orden en que llegaron. Sin palabras, ninguna.
    """
    words = normalize(query).split()
    if not words:
        return []
    phrase = " ".join(words)
    scored = []
    for index, entry in enumerate(entries):
        title = normalize(entry.title)
        haystack = " ".join((title, normalize(entry.detail), normalize(entry.keywords)))
        if not all(word in haystack for word in words):
            continue
        if title.startswith(phrase):
            score = _SCORE_PREFIX
        elif all(word in title for word in words):
            is_word_start = any(part.startswith(words[0]) for part in title.split())
            score = _SCORE_WORD if is_word_start else _SCORE_TITLE
        else:
            score = _SCORE_ELSEWHERE
        scored.append((score, index, entry))
    scored.sort(key=lambda item: item[:2])
    return [entry for _, _, entry in scored[:limit]]


def group_entries(entries: list[PaletteEntry]) -> list[tuple[str, list[PaletteEntry]]]:
    """Agrupa conservando el orden: cada grupo aparece donde está su mejor entrada."""
    groups: dict[str, list[PaletteEntry]] = {}
    for entry in entries:
        groups.setdefault(entry.group, []).append(entry)
    return list(groups.items())


class CommandPalette(QFrame):
    """La paleta de búsqueda. Ver el docstring del módulo."""

    entry_chosen = Signal(object)    # la PaletteEntry elegida; la paleta ya se cerró

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setObjectName("commandPalette")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFixedWidth(_WIDTH_PX)
        self._entries: list[PaletteEntry] = []
        self._idle_entries: list[PaletteEntry] = []
        self._is_dark = True

        self._input = QLineEdit()
        self._input.setObjectName("commandInput")
        self._input.setPlaceholderText(tr("search_placeholder"))
        self._search_action = self._input.addAction(
            theme.get_icon("search", theme.get_palette(True)["hint_text"]),
            QLineEdit.LeadingPosition)
        self._input.textChanged.connect(self._refresh)
        self._input.installEventFilter(self)

        self._list = QListWidget()
        self._list.setObjectName("commandList")
        self._list.setMouseTracking(True)
        self._list.setVerticalScrollMode(QListWidget.ScrollPerPixel)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list.setFocusPolicy(Qt.NoFocus)
        self._list.itemEntered.connect(self._on_item_entered)
        self._list.itemClicked.connect(self._choose)

        self._empty = QLabel(tr("search_empty"))
        self._empty.setObjectName("commandEmpty")
        self._empty.setAlignment(Qt.AlignCenter)

        footer = QLabel(tr("search_footer"))
        footer.setObjectName("commandFooter")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addWidget(self._input)
        layout.addWidget(_build_divider())
        layout.addWidget(self._list)
        layout.addWidget(self._empty)
        layout.addWidget(_build_divider())
        layout.addWidget(footer)

    # ── API pública ──────────────────────────────────────────────────────────

    def set_entries(self, entries: list[PaletteEntry], idle_entries: list[PaletteEntry]):
        """Dónde se busca, y lo que se muestra con el campo vacío."""
        self._entries = list(entries)
        self._idle_entries = list(idle_entries)
        self._refresh()

    def open_over(self, window: QWidget):
        """Se abre centrada arriba de `window`, con el campo vacío y el foco en él."""
        self._input.clear()
        self._refresh()
        top_left = window.mapToGlobal(window.rect().topLeft())
        x = top_left.x() + (window.width() - self.width()) // 2
        y = top_left.y() + int(window.height() * _TOP_OFFSET_PCT)
        self.move(x, y)
        self.show()
        self.raise_()
        self._input.setFocus(Qt.PopupFocusReason)

    def apply_theme(self, dark: bool):
        self._is_dark = dark
        muted = theme.get_palette(dark)["hint_text"]
        self._search_action.setIcon(theme.get_icon("search", muted))
        self._refresh()

    @property
    def shown_entries(self) -> list[PaletteEntry]:
        """Las entradas que se ven ahora, en orden."""
        return [self._list.item(row).data(_ROLE_ENTRY) for row in range(self._list.count())
                if self._list.item(row).data(_ROLE_ENTRY) is not None]

    def set_query(self, text: str):
        """Escribe en el campo, como si lo hubiera tipeado el operador."""
        self._input.setText(text)

    def choose_current(self):
        """Elige la entrada marcada, como Enter."""
        item = self._list.currentItem()
        if item is not None:
            self._choose(item)

    # ── Qt ───────────────────────────────────────────────────────────────────

    def eventFilter(self, watched, event) -> bool:
        if watched is self._input and event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Down, Qt.Key_Up):
                self._move(1 if key == Qt.Key_Down else -1)
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self.choose_current()
                return True
            if key == Qt.Key_Escape:
                self.close()
                return True
        return super().eventFilter(watched, event)

    # ── Internos ─────────────────────────────────────────────────────────────

    def _refresh(self):
        query = self._input.text()
        entries = filter_entries(self._entries, query) if query.strip() else self._idle_entries
        self._list.clear()
        muted = theme.get_palette(self._is_dark)["hint_text"]
        height = 0
        for group, members in group_entries(entries):
            header = QListWidgetItem()
            header.setFlags(Qt.NoItemFlags)
            header.setSizeHint(QSize(0, _GROUP_HEIGHT_PX))
            self._list.addItem(header)
            label = QLabel(group.upper())
            label.setObjectName("commandGroup")
            self._list.setItemWidget(header, label)
            height += _GROUP_HEIGHT_PX
            for entry in members:
                item = QListWidgetItem()
                item.setData(_ROLE_ENTRY, entry)
                item.setSizeHint(QSize(0, _ROW_HEIGHT_PX))
                self._list.addItem(item)
                self._list.setItemWidget(item, _build_row(entry, muted))
                height += _ROW_HEIGHT_PX
        self._list.setVisible(bool(entries))
        self._empty.setVisible(not entries)
        self._list.setFixedHeight(min(height + 8, _LIST_MAX_HEIGHT_PX))
        self._move(1, from_row=-1)
        self.adjustSize()

    def _move(self, step: int, *, from_row: int | None = None):
        """Marca la entrada siguiente o anterior, salteando los rótulos de grupo."""
        row = self._list.currentRow() if from_row is None else from_row
        count = self._list.count()
        row += step
        while 0 <= row < count:
            if self._list.item(row).data(_ROLE_ENTRY) is not None:
                self._list.setCurrentRow(row)
                return
            row += step

    def _on_item_entered(self, item: QListWidgetItem):
        if item.data(_ROLE_ENTRY) is not None:
            self._list.setCurrentItem(item)

    def _choose(self, item: QListWidgetItem):
        entry = item.data(_ROLE_ENTRY)
        if entry is None:
            return
        self.close()
        self.entry_chosen.emit(entry)


def _build_row(entry: PaletteEntry, icon_color: str) -> QWidget:
    """Ícono, título y, en gris, el camino. No recibe el mouse: lo atiende la lista."""
    row = QWidget()
    row.setAttribute(Qt.WA_TransparentForMouseEvents)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(12, 0, 12, 0)
    layout.setSpacing(10)
    icon = QLabel()
    icon.setFixedSize(_ICON_PX, _ICON_PX)
    if entry.icon:
        icon.setPixmap(theme.get_icon(entry.icon, icon_color).pixmap(_ICON_PX, _ICON_PX))
    layout.addWidget(icon)
    title = QLabel(entry.title)
    title.setObjectName("commandTitle")
    layout.addWidget(title)
    detail = QLabel(entry.detail)
    detail.setObjectName("commandDetail")
    # Ignored: un camino largo se corta en el borde en vez de ensanchar la paleta.
    detail.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    layout.addWidget(detail, stretch=1)
    return row


def _build_divider() -> QWidget:
    divider = QWidget()
    divider.setObjectName("settingsDivider")
    divider.setAttribute(Qt.WA_StyledBackground, True)
    divider.setFixedHeight(1)
    return divider
