"""
Tarjeta de ajustes con la forma del sistema de diseño: secciones con el título a la izquierda y
los campos a la derecha, en columnas.

    ┌──────────────────────────────────────────────────────────────────┐
    │ PRESENCIA                                                        │
    │ Confirmación          Frames para confirmar   En los últimos     │
    │ Cuándo se confirma…   [ 4            ]        [ 6            ]   │
    │ ──────────────────────────────────────────────────────────────── │
    │ Zona de exclusión     Cinta                   Báscula            │
    │ Por cámara…           Sin zona  [Dibujar]     5 vértices [Dib…]  │
    └──────────────────────────────────────────────────────────────────┘

Cada campo lleva su rótulo arriba y, si hace falta, una aclaración corta abajo: el rótulo
pegado al campo se lee junto con él, y varios campos por fila aprovechan el ancho que una
fila por campo dejaba vacío. Lo que explica la sección va en su descripción, de una o dos
líneas; un párrafo largo en la pantalla no lo lee nadie y su lugar es el docstring o
`docs/`.

`FieldGrid` es la grilla de campos sola, para armar los campos dentro de otro contenedor
—la página de una sub-pestaña por cámara— con la misma forma. `SectionStack` son las
secciones solas, para cuando cada slot tiene varias: la tarjeta lleva arriba la barra de
sub-pestañas (`SettingsCard.add_row()`) y cada página es una pila de secciones. `SettingsList`
es la lista de la web —filas dentro de un borde fino, con un filete entre una y otra— para
varios ítems del mismo tipo dentro de una sección.

Cada grilla sabe qué campos tiene (`FieldGrid.get_fields()`) y cada sección su título y su
descripción: es lo que lee la búsqueda de la barra superior (`ui/widgets/field_search.py`),
sin que ninguna pestaña declare nada aparte.

Módulo de presentación: no lee ni escribe el config. Los campos entran ya construidos —con
los constructores de `ui/widgets/form.py`— y quien los carga y los guarda es la pestaña.
"""

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

# Las medidas son las de la web: 24 px a los costados de la tarjeta, 28 arriba, y cada
# sección con 28 px a cada lado del filete que la separa de la anterior.
_CARD_SIDE_PX = 24
_CARD_TOP_PX = 28
_SECTION_PADDING_PX = 28
_EYEBROW_GAP_PX = 16          # entre el rótulo de la tarjeta y su primera sección
_STACK_TOP_PX = 24            # entre la barra de sub-pestañas y la primera sección de su página
_LIST_ROW_PADDING_PX = (16, 12, 16, 12)   # izquierda, arriba, derecha, abajo de cada fila
_ASIDE_WIDTH_PX = 260         # la columna del título y la descripción
_ASIDE_GAP_PX = 40
_COLUMN_GAP_PX = 16
_ROW_GAP_PX = 24              # entre filas de campos: el rótulo no se pega a la aclaración de arriba
_LABEL_GAP_PX = 6             # entre el rótulo, el campo y su aclaración
_FIELD_MIN_HEIGHT_PX = 30
_EYEBROW_SPACING_PCT = 116.0  # el rótulo de la tarjeta va espaciado, como en la web


@dataclass(frozen=True)
class Field:
    """Un campo de una grilla: su rótulo, su aclaración, el campo y la celda que los junta."""

    label: str
    hint: str
    widget: QWidget
    cell: QWidget


class FieldGrid(QWidget):
    """
    Campos en `columns` columnas, cada uno con su rótulo arriba. Se llenan de izquierda a
    derecha y bajan a la fila siguiente al completar una.
    """

    def __init__(self, columns: int = 2, parent=None):
        super().__init__(parent)
        self._columns = max(1, columns)
        self._row = 0
        self._column = 0
        self._cells: dict[QWidget, QWidget] = {}     # campo -> su celda, con rótulo y aclaración
        self._fields: list[Field] = []
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(_COLUMN_GAP_PX)
        self._grid.setVerticalSpacing(_ROW_GAP_PX)
        for column in range(self._columns):
            self._grid.setColumnStretch(column, 1)

    def add_field(self, label: str, widget: QWidget, *, hint: str = "",
                  span: int = 1) -> QWidget:
        """Agrega un campo con su rótulo y, si se da, su aclaración. Devuelve el campo."""
        span = min(max(1, span), self._columns)
        if self._column + span > self._columns:
            self._next_row()
        cell = QWidget()
        cell.setObjectName("settingsFieldCell")
        layout = QVBoxLayout(cell)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_LABEL_GAP_PX)
        caption = QLabel(label)
        caption.setObjectName("settingsFieldLabel")
        layout.addWidget(caption)
        widget.setMinimumHeight(_FIELD_MIN_HEIGHT_PX)
        layout.addWidget(widget)
        if hint:
            note = QLabel(hint)
            note.setObjectName("settingsFieldHint")
            note.setWordWrap(True)
            layout.addWidget(note)
        layout.addStretch()
        self._grid.addWidget(cell, self._row, self._column, 1, span)
        self._cells[widget] = cell
        self._fields.append(Field(label, hint, widget, cell))
        self._column += span
        if self._column >= self._columns:
            self._next_row()
        return widget

    def get_fields(self) -> list[Field]:
        """Los campos agregados con `add_field()`, en orden."""
        return list(self._fields)

    def add_row(self, widget: QWidget) -> QWidget:
        """Agrega un widget a todo el ancho, en una fila propia: una barra de sub-pestañas."""
        if self._column:
            self._next_row()
        self._grid.addWidget(widget, self._row, 0, 1, self._columns)
        self._next_row()
        return widget

    def set_field_visible(self, widget: QWidget, visible: bool):
        """
        Muestra u oculta un campo con su rótulo y su aclaración. Una fila con todos sus campos
        ocultos no ocupa lugar; uno oculto en una fila con otros deja su columna vacía.
        """
        cell = self._cells.get(widget)
        if cell is not None:
            cell.setVisible(visible)

    def _next_row(self):
        self._row += 1
        self._column = 0


class SettingsSection(QWidget):
    """Una sección: título y descripción a la izquierda, sus campos a la derecha."""

    def __init__(self, title: str, description: str = "", columns: int = 2, *,
                 top_padding_px: int = _SECTION_PADDING_PX, parent=None):
        super().__init__(parent)
        self.title = title
        self.description = description
        row = QHBoxLayout(self)
        row.setContentsMargins(0, top_padding_px, 0, _SECTION_PADDING_PX)
        row.setSpacing(_ASIDE_GAP_PX)

        aside = QWidget()
        aside.setFixedWidth(_ASIDE_WIDTH_PX)
        aside_layout = QVBoxLayout(aside)
        aside_layout.setContentsMargins(0, 0, 0, 0)
        aside_layout.setSpacing(6)
        title_label = QLabel(title)
        title_label.setObjectName("settingsSectionTitle")
        title_label.setWordWrap(True)
        aside_layout.addWidget(title_label)
        if description:
            description_label = QLabel(description)
            description_label.setObjectName("settingsSectionDescription")
            description_label.setWordWrap(True)
            aside_layout.addWidget(description_label)
        aside_layout.addStretch()
        row.addWidget(aside, alignment=Qt.AlignTop)

        self._fields = FieldGrid(columns)
        self._fields.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        # Arriba y a su alto: si la sección queda más alta que sus campos —una descripción
        # larga—, el sobrante va abajo y no se reparte entre las filas.
        row.addWidget(self._fields, stretch=1, alignment=Qt.AlignTop)

    def add_field(self, label: str, widget: QWidget, *, hint: str = "",
                  span: int = 1) -> QWidget:
        """Agrega un campo a la sección. Ver `FieldGrid.add_field()`."""
        return self._fields.add_field(label, widget, hint=hint, span=span)

    def add_row(self, widget: QWidget) -> QWidget:
        """Agrega un widget a todo el ancho de los campos. Ver `FieldGrid.add_row()`."""
        return self._fields.add_row(widget)

    def set_field_visible(self, widget: QWidget, visible: bool):
        """Muestra u oculta un campo. Ver `FieldGrid.set_field_visible()`."""
        self._fields.set_field_visible(widget, visible)


class SettingsCard(QWidget):
    """
    Tarjeta con secciones separadas por un filete. `eyebrow` es el rótulo chico, en
    mayúsculas, que dice de qué es la tarjeta; sin él, la tarjeta arranca en la primera
    sección.
    """

    def __init__(self, eyebrow: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("settingsCard")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._layout = QVBoxLayout(self)
        # Abajo el aire lo pone la última sección, que lleva el suyo debajo.
        self._layout.setContentsMargins(_CARD_SIDE_PX, _CARD_TOP_PX, _CARD_SIDE_PX, 0)
        self._layout.setSpacing(0)
        self._has_content = False
        self._eyebrow: QLabel | None = None
        if eyebrow:
            self._eyebrow = QLabel()
            self._eyebrow.setObjectName("settingsEyebrow")
            # El QSS no tiene `letter-spacing`; lo que no fija la hoja lo conserva el font.
            font = self._eyebrow.font()
            font.setLetterSpacing(QFont.PercentageSpacing, _EYEBROW_SPACING_PCT)
            self._eyebrow.setFont(font)
            self._eyebrow.setText(eyebrow.upper())
            self._layout.addWidget(self._eyebrow)

    def add_section(self, title: str, description: str = "",
                    columns: int = 2) -> SettingsSection:
        """Agrega una sección al pie de la tarjeta, con un filete arriba si no es lo primero."""
        if self._has_content:
            self._layout.addWidget(_build_divider())
            top_padding_px = _SECTION_PADDING_PX
        else:
            # La primera arranca en el borde de la tarjeta, o debajo de su rótulo.
            top_padding_px = _EYEBROW_GAP_PX if self._eyebrow is not None else 0
        self._has_content = True
        section = SettingsSection(title, description, columns, top_padding_px=top_padding_px)
        self._layout.addWidget(section)
        return section

    def add_row(self, widget: QWidget) -> QWidget:
        """
        Agrega un widget a todo el ancho de la tarjeta, sin filete: la barra de sub-pestañas de
        una tarjeta cuyos slots tienen cada uno sus secciones, con un `SectionStack` por página.
        """
        if not self._has_content and self._eyebrow is not None:
            self._layout.addSpacing(_EYEBROW_GAP_PX)
        self._has_content = True
        self._layout.addWidget(widget)
        return widget


class SectionStack(QWidget):
    """
    Secciones separadas por un filete, sin la tarjeta: la página de una sub-pestaña que va
    adentro de una `SettingsCard`. La tarjeta pone el borde y el aire de los costados.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)
        self._has_sections = False

    def add_section(self, title: str, description: str = "",
                    columns: int = 2) -> SettingsSection:
        """Agrega una sección al pie, con un filete arriba si no es la primera."""
        if self._has_sections:
            self._layout.addWidget(_build_divider())
        top_padding_px = _SECTION_PADDING_PX if self._has_sections else _STACK_TOP_PX
        self._has_sections = True
        section = SettingsSection(title, description, columns, top_padding_px=top_padding_px)
        self._layout.addWidget(section)
        return section


class SettingsList(QWidget):
    """
    Filas dentro de un borde fino, con un filete entre una y otra. Cada fila la arma quien la
    usa; la lista le pone el aire de adentro.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("settingsList")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._layout = QVBoxLayout(self)
        # Un píxel para que las filas y los filetes queden adentro del borde.
        self._layout.setContentsMargins(1, 1, 1, 1)
        self._layout.setSpacing(0)
        self._row_count = 0

    def add_row(self, widget: QWidget) -> QWidget:
        """Agrega una fila al pie, con un filete arriba si no es la primera. Devuelve la fila."""
        if self._row_count:
            self._layout.addWidget(_build_divider())
        widget.setContentsMargins(*_LIST_ROW_PADDING_PX)
        self._layout.addWidget(widget)
        self._row_count += 1
        return widget


def _build_divider() -> QWidget:
    divider = QWidget()
    divider.setObjectName("settingsDivider")
    divider.setAttribute(Qt.WA_StyledBackground, True)
    divider.setFixedHeight(1)
    return divider
