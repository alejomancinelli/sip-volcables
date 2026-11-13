"""
Encabezado de una página, con la forma de la web: un rótulo chico con un filete celeste, el
título grande y una línea que dice de qué es la página.

    ▍ INTEGRACIÓN
    Video y telemetría
    Streams de video para mirar la línea y telemetría hacia InfluxDB y MQTT.

El rótulo es el grupo de la barra lateral al que pertenece la página, así que dice dónde se
está sin repetir la miga de pan. La descripción es una sola línea —dos como mucho—: lo que
hace falta para orientarse, no un manual.

Módulo de presentación: recibe los textos ya traducidos.
"""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

_EYEBROW_BAR_SIZE = QSize(3, 12)
_EYEBROW_GAP_PX = 8           # entre el filete y el rótulo
_EYEBROW_SPACING_PCT = 114.0  # 0.14 em entre letras, como la web
_TITLE_GAP_PX = 8             # entre el rótulo y el título
_DESCRIPTION_GAP_PX = 10      # entre el título y la descripción
_DESCRIPTION_MAX_WIDTH_PX = 576


class PageHeader(QWidget):
    """Rótulo, título y descripción de una página. El rótulo y la descripción son opcionales."""

    def __init__(self, title: str, *, eyebrow: str = "", description: str = "", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if eyebrow:
            row = QHBoxLayout()
            row.setSpacing(_EYEBROW_GAP_PX)
            bar = QWidget()
            bar.setObjectName("pageEyebrowBar")
            bar.setAttribute(Qt.WA_StyledBackground, True)
            bar.setFixedSize(_EYEBROW_BAR_SIZE)
            row.addWidget(bar, alignment=Qt.AlignVCenter)
            label = QLabel(eyebrow.upper())
            label.setObjectName("pageEyebrow")
            # El QSS no tiene `letter-spacing`; lo que no fija la hoja lo conserva el font.
            font = label.font()
            font.setLetterSpacing(QFont.PercentageSpacing, _EYEBROW_SPACING_PCT)
            label.setFont(font)
            row.addWidget(label)
            row.addStretch()
            layout.addLayout(row)
            layout.addSpacing(_TITLE_GAP_PX)

        title_label = QLabel(title)
        title_label.setObjectName("viewTitle")
        layout.addWidget(title_label)

        if description:
            layout.addSpacing(_DESCRIPTION_GAP_PX)
            description_label = QLabel(description)
            description_label.setObjectName("pageDescription")
            description_label.setWordWrap(True)
            description_label.setMaximumWidth(_DESCRIPTION_MAX_WIDTH_PX)
            # En una fila con un stretch y no suelta: el alto de un texto que se parte lo
            # calcula Qt para el ancho de la columna, sin mirar el tope de la etiqueta, y una
            # descripción de dos líneas se medía como de una y aplastaba el título.
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            # La etiqueta se lleva el ancho hasta su tope, y lo que sobra queda al costado.
            row.addWidget(description_label, stretch=1)
            row.addStretch()
            layout.addLayout(row)
