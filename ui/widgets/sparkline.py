"""
Línea chica de tendencia, sin ejes: los últimos N valores de una serie, como la de la web.

Dice si algo sube, baja o se cortó, no cuánto: el número va al lado, en grande. Por eso no
tiene ejes ni grilla, y la escala vertical es la de los valores que muestra —de su mínimo a
su máximo—. Una serie plana se dibuja a media altura.

Con `low` y `high` la escala es fija: un porcentaje que va de 20 a 21 se ve casi plano, que
es lo que es, en vez de ocupar todo el alto como si se hubiera disparado. Un valor fuera del
rango se dibuja contra el borde.

Se pinta con QPainter, así que el QSS no la alcanza: el color sale de la paleta del tema y
cambia con `apply_theme()`. Debajo de la línea va el mismo color, que se desvanece hacia
abajo.
"""

from collections import deque

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ui import theme

_LINE_WIDTH_PX = 1.6
_FILL_TOP_ALPHA = 56          # de 255, la opacidad del relleno pegado a la línea
_PADDING_PX = 3               # arriba y abajo: la línea no se corta contra el borde
_HEIGHT_PX = 48


class Sparkline(QWidget):
    """Tendencia de los últimos `max_points` valores. Se le empujan con `push()`."""

    def __init__(self, max_points: int = 60, parent=None, *, low: float | None = None,
                 high: float | None = None, height_px: int = _HEIGHT_PX):
        super().__init__(parent)
        self._values: deque = deque(maxlen=max_points)
        # La escala fija vale sólo con los dos extremos y en orden; si no, la de los valores.
        self._fixed_range = ((float(low), float(high))
                             if low is not None and high is not None and high > low else None)
        self._color = theme.get_color("sparkline", True)
        self.setFixedHeight(height_px)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def push(self, value: float):
        self._values.append(float(value))
        self.update()

    def clear(self):
        self._values.clear()
        self.update()

    def apply_theme(self, dark: bool):
        self._color = theme.get_color("sparkline", dark)
        self.update()

    def paintEvent(self, event):
        if len(self._values) < 2:
            return
        width = self.width()
        height = self.height() - 2 * _PADDING_PX
        low, high = self._fixed_range or (min(self._values), max(self._values))
        span = high - low
        step = width / (self._values.maxlen - 1)
        # Los puntos se alinean a la derecha: la serie entra por ahí mientras se llena.
        offset = width - step * (len(self._values) - 1)

        line = QPainterPath()
        for index, value in enumerate(self._values):
            ratio = min(1.0, max(0.0, (value - low) / span)) if span else 0.5
            point = QPointF(offset + index * step, _PADDING_PX + height * (1 - ratio))
            if index == 0:
                line.moveTo(point)
            else:
                line.lineTo(point)

        area = QPainterPath(line)
        area.lineTo(width, self.height())
        area.lineTo(offset, self.height())
        area.closeSubpath()
        gradient = QLinearGradient(0, 0, 0, self.height())
        top = QColor(self._color)
        top.setAlpha(_FILL_TOP_ALPHA)
        bottom = QColor(self._color)
        bottom.setAlpha(0)
        gradient.setColorAt(0, top)
        gradient.setColorAt(1, bottom)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.fillPath(area, gradient)
        pen = QPen(self._color, _LINE_WIDTH_PX)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.drawPath(line)
