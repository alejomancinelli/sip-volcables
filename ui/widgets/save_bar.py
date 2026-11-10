"""
Barra de cambios sin guardar, como la de la web: una píldora oscura que aparece abajo, al
centro, sólo cuando hay algo editado, con «Descartar» y «Guardar y aplicar».

    ┌───────────────────────────────────────────────────────────┐
    │ (!) Cambios sin guardar en Proceso   Descartar  [Guardar] │
    └───────────────────────────────────────────────────────────┘

No vive en un layout: flota sobre la página que la tiene, que la ubica con `place()` cada
vez que cambia de tamaño. Escondida no ocupa lugar, y los botones no están a la vista cuando
no hay nada que guardar —un «Guardar» siempre visible invita a apretarlo por las dudas, y
sobrescribe el config con lo que haya—.

Es oscura en los dos temas, como la barra lateral: tiene que verse encima de cualquier
contenido. La sombra es lo único que se dibuja por código; el resto lo pinta el QSS.

Módulo de presentación: no sabe qué cambió ni cómo se guarda. Recibe el texto con
`set_message()` y publica lo que el operador apretó.
"""

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QGraphicsDropShadowEffect, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget,
)

from ui import theme
from ui.strings import tr

_MAX_WIDTH_PX = 576           # el `max-w-xl` de la web
_SIDE_GAP_PX = 20             # aire mínimo contra los costados de la página
_BOTTOM_GAP_PX = 20           # aire contra el borde de abajo
_ICON_PX = 18
_SHADOW_BLUR_PX = 32
_SHADOW_OFFSET_PX = 12
_SHADOW_ALPHA = 110           # de 255
_SLIDE_PX = 16                # cuánto sube al aparecer
_SLIDE_MS = 220
_ICON = "alert-circle"


class SaveBar(QWidget):
    """Barra flotante de cambios sin guardar. Ver el contrato en el docstring del módulo."""

    save_requested = Signal()       # el operador apretó «Guardar y aplicar»
    discard_requested = Signal()    # el operador apretó «Descartar»

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("saveBar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        row = QHBoxLayout(self)
        row.setContentsMargins(16, 8, 8, 8)
        row.setSpacing(12)

        icon = QLabel()
        icon.setPixmap(theme.get_icon(_ICON, theme.get_tokens(True)["brand"])
                       .pixmap(QSize(_ICON_PX, _ICON_PX)))
        row.addWidget(icon)

        self._message = QLabel()
        self._message.setObjectName("saveBarMessage")
        self._message.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        row.addWidget(self._message, stretch=1)

        discard_button = QPushButton(tr("config_discard"))
        discard_button.setObjectName("saveBarDiscard")
        discard_button.clicked.connect(self.discard_requested)
        row.addWidget(discard_button)

        save_button = QPushButton(tr("config_save"))
        save_button.setObjectName("saveBarSave")
        save_button.clicked.connect(self.save_requested)
        row.addWidget(save_button)

        shadow = QGraphicsDropShadowEffect(self)
        shadow_color = QColor(theme.get_palette(True)["save_bar_shadow"])
        shadow_color.setAlpha(_SHADOW_ALPHA)
        shadow.setColor(shadow_color)
        shadow.setBlurRadius(_SHADOW_BLUR_PX)
        shadow.setOffset(0, _SHADOW_OFFSET_PX)
        self.setGraphicsEffect(shadow)

        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setDuration(_SLIDE_MS)
        self._slide.setEasingCurve(QEasingCurve.OutCubic)
        self.hide()

    def set_message(self, text: str):
        self._message.setText(text)
        self._message.setToolTip(text)

    def show_animated(self):
        """Aparece subiendo desde abajo. Si ya estaba a la vista, sólo se reubica."""
        if self.isVisible():
            self.place()
            return
        target = self._get_target_pos()
        self.move(target + QPoint(0, _SLIDE_PX))
        self.show()
        self.raise_()
        self._slide.setStartValue(target + QPoint(0, _SLIDE_PX))
        self._slide.setEndValue(target)
        self._slide.start()

    def place(self):
        """Ubica la barra abajo y al centro de la página; la llama la página al cambiar de tamaño."""
        self._slide.stop()
        self.move(self._get_target_pos())
        self.raise_()

    def get_reserved_height(self) -> int:
        """El alto que la página deja libre abajo para que la barra no tape su contenido."""
        return self.sizeHint().height() + 2 * _BOTTOM_GAP_PX

    def _get_target_pos(self) -> QPoint:
        page = self.parentWidget()
        width = min(_MAX_WIDTH_PX, max(0, page.width() - 2 * _SIDE_GAP_PX))
        self.setFixedWidth(width)
        height = self.sizeHint().height()
        self.setFixedHeight(height)
        return QPoint((page.width() - width) // 2, page.height() - height - _BOTTOM_GAP_PX)
