"""
Barra lateral de la ventana: la marca, la tarjeta de la aplicación y la navegación.

Es oscura en los dos temas: es donde vive la marca, y el tema claro u oscuro es del área
de trabajo. Lleva la textura del sistema de diseño —un halo del acento arriba a la
izquierda y una grilla de plano que se desvanece hacia abajo— y marca la vista activa
con un filete del acento contra el borde. Las dos se dibujan acá y no en el QSS, que no
tiene degradés radiales ni máscaras.

Se contrae a un riel de íconos con el botón del pie. Contraída, el logo pasa a ser la
esfera, la tarjeta y el nombre de la empresa se esconden, el rótulo de cada grupo pasa a
ser un filete y cada ícono lleva su texto en el tooltip. **Los íconos no se mueven**: lo
que se esconde deja su lugar —la tarjeta y cada rótulo conservan el alto que tenían—,
para que el ojo encuentre cada ítem donde estaba. No se guarda entre corridas: arranca
expandida.

Widget de presentación: no lee el config ni conoce las vistas. Recibe los grupos ya
traducidos, avisa por `navigation_requested` qué ítem eligió el operador y la ventana le
dice cuál quedó activo con `set_current()`.
"""

import os

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor, QFont, QImage, QLinearGradient, QPainter, QPixmap, QRadialGradient,
)
from PySide6.QtWidgets import (
    QButtonGroup, QHBoxLayout, QLabel, QPushButton, QStyle, QStyleOption, QVBoxLayout,
    QWidget,
)

from ui import theme
from ui.strings import tr

# (clave, texto, ícono de Tabler) de un ítem, y (rótulo, ítems) de un grupo.
NavItem = tuple[str, str, str]
NavGroup = tuple[str, tuple[NavItem, ...]]

_EXPANDED_WIDTH_PX = 248
_COLLAPSED_WIDTH_PX = 64
_MARGIN_PX = 12               # aire de todo contra los bordes de la barra
_LOGO_BAND_HEIGHT_PX = 64
_LOGO_HEIGHT_PX = 30
_LOGO_INSET_PX = 20           # el logo entra más que el resto, como en la web
_SPHERE_PX = 14
_CARD_MARGIN_PX = 12
_CARD_BOTTOM_GAP_PX = 6
_GROUP_GAP_PX = 18            # aire sobre el rótulo de cada grupo
_GROUP_LABEL_SPACING_PCT = 116.0  # el rótulo va espaciado: 0.16 em entre letras
_DIVIDER_SIZE = QSize(24, 1)
_ICON_PX = 18
_NAV_BUTTON_HEIGHT_PX = 36    # fijo: con texto o sin él, el ítem mide lo mismo
_TOGGLE_PX = 32
_UNLIMITED_HEIGHT_PX = 16777215   # QWIDGETSIZE_MAX: el tope por defecto de Qt

# Textura: un halo de 288 px corrido 96 px afuera de la esquina, difuminado, y una
# grilla de 24 px en los primeros 224 px de alto.
_GLOW_CENTER_PX = 48
_GLOW_RADIUS_PX = 210
_GLOW_OPACITY = 0.16
_GRID_STEP_PX = 24
_GRID_HEIGHT_PX = 224
_GRID_ALPHA = 9               # de 255: la grilla se adivina, no se lee

# Filete de la vista activa, contra el borde izquierdo, con su resplandor.
_INDICATOR_WIDTH_PX = 3
_INDICATOR_HEIGHT_PX = 20
_INDICATOR_GLOW_PX = 10
_INDICATOR_GLOW_ALPHA = 110

_ICONS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "icons")
# Sin la bajada: «Ingeniería Electrónica Argentina» va al pie, en texto.
_LOGO_FILENAME = "logo-iea-blanco.png"
_SPHERE_FILENAME = "logo-iea-esfera.png"
_COLLAPSE_ICON = "layout-sidebar-left-collapse"
_EXPAND_ICON = "layout-sidebar-left-expand"

# Propiedad Qt que el QSS lee para centrar los íconos del riel contraído.
_COLLAPSED_PROPERTY = "collapsed"


class Sidebar(QWidget):
    """Barra lateral. Ver el contrato en el docstring del módulo."""

    navigation_requested = Signal(str)   # clave del ítem que eligió el operador

    def __init__(self, groups: tuple[NavGroup, ...], *, title: str, subtitle: str = "",
                 footer: str = "", parent=None):
        super().__init__(parent)
        self.setObjectName("appSidebar")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._is_collapsed = False
        self._buttons: dict[str, QPushButton] = {}
        self._labels: dict[str, str] = {}
        self._group_headers: list[QWidget] = []
        self._group_labels: list[QLabel] = []
        self._dividers: list[QWidget] = []
        self._grid_cache: QPixmap | None = None
        palette = theme.get_palette(True)
        self._glow_color = QColor(palette["sidebar_glow"])
        self._grid_color = QColor(palette["sidebar_grid"])
        self._indicator_color = QColor(palette["sidebar_icon_active"])
        self._icon_color = palette["sidebar_icon"]

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(_MARGIN_PX, 0, _MARGIN_PX, _MARGIN_PX)
        self._layout.setSpacing(4)
        self._layout.addWidget(self._build_logo_band())
        # La tarjeta va en un hueco propio: contraída se esconde y el hueco conserva su alto.
        self._card_slot = QWidget()
        card_layout = QVBoxLayout(self._card_slot)
        card_layout.setContentsMargins(0, 0, 0, 0)
        self._device_card = self._build_device_card(title, subtitle)
        card_layout.addWidget(self._device_card)
        self._layout.addWidget(self._card_slot)
        self._layout.addSpacing(_CARD_BOTTOM_GAP_PX)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        for group_title, items in groups:
            self._add_group(group_title, items, palette)

        self._layout.addStretch()
        self._layout.addWidget(self._build_footer(footer))
        self.set_collapsed(False)

    # ── API pública ──────────────────────────────────────────────────────────

    @property
    def is_collapsed(self) -> bool:
        return self._is_collapsed

    def set_current(self, key: str):
        """Marca el ítem de la vista activa. Una clave que no está se ignora."""
        button = self._buttons.get(key)
        if button is None:
            return
        button.setChecked(True)
        self.update()

    def get_label(self, key: str) -> str:
        """Texto del ítem, el mismo que se ve en la barra; '' si la clave no está."""
        return self._labels.get(key, "")

    def set_item_visible(self, key: str, visible: bool):
        """Muestra o esconde un ítem, para una página que existe según el equipo."""
        button = self._buttons.get(key)
        if button is not None:
            button.setVisible(visible)
            self.update()

    def set_collapsed(self, collapsed: bool):
        """Expande la barra o la contrae al riel de íconos, sin mover ningún ícono."""
        # Antes de esconder nada: el alto que se congela es el que tienen expandidos.
        for slot in (self._card_slot, *self._group_headers):
            _set_height_frozen(slot, collapsed)
        self._is_collapsed = collapsed
        self.setFixedWidth(_COLLAPSED_WIDTH_PX if collapsed else _EXPANDED_WIDTH_PX)
        self._logo.setVisible(not collapsed)
        self._sphere.setVisible(collapsed)
        self._logo_band_layout.setContentsMargins(
            0 if collapsed else _LOGO_INSET_PX - _MARGIN_PX, 0, 0, 0)
        self._device_card.setVisible(not collapsed)
        for label in self._group_labels:
            label.setVisible(not collapsed)
        for divider in self._dividers:
            divider.setVisible(collapsed)
        for key, button in self._buttons.items():
            label = self._labels[key]
            button.setText("" if collapsed else label)
            button.setToolTip(label if collapsed else "")
            button.setProperty(_COLLAPSED_PROPERTY, collapsed)
            button.style().unpolish(button)
            button.style().polish(button)
        self._footer_label.setVisible(not collapsed)
        self._toggle.setIcon(theme.get_icon(_EXPAND_ICON if collapsed else _COLLAPSE_ICON,
                                            self._icon_color))
        self._toggle.setToolTip(tr("nav_expand" if collapsed else "nav_collapse"))
        self.update()

    # ── Armado ───────────────────────────────────────────────────────────────

    def _build_logo_band(self) -> QWidget:
        band = QWidget()
        band.setFixedHeight(_LOGO_BAND_HEIGHT_PX)
        self._logo_band_layout = QHBoxLayout(band)
        self._logo_band_layout.setSpacing(0)

        self._logo = QLabel()
        self._logo.setObjectName("appLogo")
        logo = QPixmap(os.path.join(_ICONS_DIR, _LOGO_FILENAME))
        if not logo.isNull():
            self._logo.setPixmap(_scale_to_height(logo, _LOGO_HEIGHT_PX, self))
        self._logo_band_layout.addWidget(self._logo, alignment=Qt.AlignLeft | Qt.AlignVCenter)

        self._sphere = QLabel()
        sphere = QPixmap(os.path.join(_ICONS_DIR, _SPHERE_FILENAME))
        if not sphere.isNull():
            self._sphere.setPixmap(_scale_to_height(sphere, _SPHERE_PX, self))
        self._logo_band_layout.addWidget(self._sphere, alignment=Qt.AlignCenter)
        return band

    def _build_device_card(self, title: str, subtitle: str) -> QWidget:
        """
        El nombre de la aplicación y, debajo, el subtítulo.

        Sin subtítulo la tarjeta lleva sólo el nombre: una segunda línea vacía se lee como
        un dato que falta.
        """
        card = QWidget()
        card.setObjectName("sidebarDevice")
        card.setAttribute(Qt.WA_StyledBackground, True)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(_CARD_MARGIN_PX, _CARD_MARGIN_PX,
                                  _CARD_MARGIN_PX, _CARD_MARGIN_PX)
        layout.setSpacing(2)

        name = QLabel(title)
        name.setObjectName("sidebarDeviceName")
        name.setWordWrap(True)
        layout.addWidget(name)
        if subtitle:
            detail = QLabel(subtitle)
            detail.setObjectName("sidebarDeviceDetail")
            detail.setWordWrap(True)
            layout.addWidget(detail)
        return card

    def _add_group(self, group_title: str, items: tuple[NavItem, ...], palette: dict):
        self._layout.addSpacing(_GROUP_GAP_PX)
        # El rótulo y el filete que lo reemplaza contraída comparten un hueco del alto del
        # rótulo, para que los ítems de abajo no suban.
        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(0)
        self._group_headers.append(header)
        self._layout.addWidget(header)

        label = QLabel(group_title.upper())
        label.setObjectName("sidebarGroupLabel")
        # El QSS no tiene `letter-spacing`; lo que no fija la hoja lo conserva el font.
        font = label.font()
        font.setLetterSpacing(QFont.PercentageSpacing, _GROUP_LABEL_SPACING_PCT)
        label.setFont(font)
        self._group_labels.append(label)
        header_layout.addWidget(label)

        divider = QWidget()
        divider.setObjectName("sidebarDivider")
        divider.setAttribute(Qt.WA_StyledBackground, True)
        divider.setFixedSize(_DIVIDER_SIZE)
        self._dividers.append(divider)
        header_layout.addWidget(divider, alignment=Qt.AlignCenter)

        for key, text, icon_name in items:
            button = QPushButton(text)
            button.setObjectName("navButton")
            button.setCheckable(True)
            button.setFixedHeight(_NAV_BUTTON_HEIGHT_PX)
            button.setCursor(Qt.PointingHandCursor)
            button.setIcon(theme.get_icon(icon_name, palette["sidebar_icon"],
                                          palette["sidebar_icon_active"]))
            button.setIconSize(QSize(_ICON_PX, _ICON_PX))
            button.clicked.connect(lambda _, target=key: self.navigation_requested.emit(target))
            self._nav_group.addButton(button)
            self._buttons[key] = button
            self._labels[key] = text
            self._layout.addWidget(button)

    def _build_footer(self, footer: str) -> QWidget:
        bar = QWidget()
        bar.setObjectName("sidebarFooterBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        row = QHBoxLayout(bar)
        row.setContentsMargins(0, _MARGIN_PX, 0, 0)
        row.setSpacing(8)

        self._footer_label = QLabel(footer)
        self._footer_label.setObjectName("sidebarFooter")
        self._footer_label.setWordWrap(True)
        row.addWidget(self._footer_label, stretch=1)

        self._toggle = QPushButton()
        self._toggle.setObjectName("navCollapseButton")
        self._toggle.setFixedSize(_TOGGLE_PX, _TOGGLE_PX)
        self._toggle.setIconSize(QSize(_ICON_PX, _ICON_PX))
        self._toggle.setCursor(Qt.PointingHandCursor)
        self._toggle.clicked.connect(lambda: self.set_collapsed(not self._is_collapsed))
        row.addWidget(self._toggle, alignment=Qt.AlignRight | Qt.AlignVCenter)
        return bar

    # ── Pintado ──────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        painter = QPainter(self)
        # El fondo y el borde son del QSS; la textura va encima y los botones encima de ella.
        option = QStyleOption()
        option.initFrom(self)
        self.style().drawPrimitive(QStyle.PE_Widget, option, painter, self)
        painter.setRenderHint(QPainter.Antialiasing)
        self._paint_glow(painter)
        painter.drawPixmap(0, 0, self._get_grid())
        self._paint_indicator(painter)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._grid_cache = None

    def _paint_glow(self, painter: QPainter):
        center = QPointF(_GLOW_CENTER_PX, _GLOW_CENTER_PX)
        gradient = QRadialGradient(center, _GLOW_RADIUS_PX)
        inner = QColor(self._glow_color)
        inner.setAlphaF(_GLOW_OPACITY)
        outer = QColor(self._glow_color)
        outer.setAlphaF(0.0)
        gradient.setColorAt(0.0, inner)
        gradient.setColorAt(1.0, outer)
        painter.fillRect(self.rect(), gradient)

    def _get_grid(self) -> QPixmap:
        """
        La grilla, ya desvanecida hacia abajo. Se arma una vez por ancho: el desvanecido es
        una máscara sobre la capa entera, y rehacerlo en cada repintado sería trabajo tirado.
        """
        if self._grid_cache is not None:
            return self._grid_cache
        layer = QImage(max(1, self.width()), _GRID_HEIGHT_PX,
                       QImage.Format_ARGB32_Premultiplied)
        layer.fill(Qt.transparent)
        painter = QPainter(layer)
        line = QColor(self._grid_color)
        line.setAlpha(_GRID_ALPHA)
        for x in range(0, layer.width(), _GRID_STEP_PX):
            painter.fillRect(x, 0, 1, layer.height(), line)
        for y in range(0, layer.height(), _GRID_STEP_PX):
            painter.fillRect(0, y, layer.width(), 1, line)
        fade = QLinearGradient(0, 0, 0, layer.height())
        fade.setColorAt(0.0, QColor(0, 0, 0, 255))
        fade.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        painter.fillRect(layer.rect(), fade)
        painter.end()
        self._grid_cache = QPixmap.fromImage(layer)
        return self._grid_cache

    def _paint_indicator(self, painter: QPainter):
        button = self._nav_group.checkedButton()
        if button is None or not button.isVisible():
            return
        center_y = button.geometry().center().y()
        top = center_y - _INDICATOR_HEIGHT_PX / 2
        glow = QLinearGradient(0, 0, _INDICATOR_GLOW_PX, 0)
        start = QColor(self._indicator_color)
        start.setAlpha(_INDICATOR_GLOW_ALPHA)
        end = QColor(self._indicator_color)
        end.setAlpha(0)
        glow.setColorAt(0.0, start)
        glow.setColorAt(1.0, end)
        painter.fillRect(QRectF(0, top - 4, _INDICATOR_GLOW_PX, _INDICATOR_HEIGHT_PX + 8), glow)
        painter.setPen(Qt.NoPen)
        painter.setBrush(self._indicator_color)
        radius = _INDICATOR_WIDTH_PX / 2
        # Corrido a la izquierda para que sólo se vea redondeado el lado de adentro.
        painter.drawRoundedRect(QRectF(-_INDICATOR_WIDTH_PX, top, 2 * _INDICATOR_WIDTH_PX,
                                       _INDICATOR_HEIGHT_PX), radius, radius)


def _set_height_frozen(widget: QWidget, frozen: bool):
    """
    Congela el alto actual del widget o lo libera. Antes de mostrarse no tiene alto todavía,
    y se usa el que pide.
    """
    if frozen:
        widget.setFixedHeight(widget.height() if widget.isVisible() else
                              widget.sizeHint().height())
    else:
        widget.setMinimumHeight(0)
        widget.setMaximumHeight(_UNLIMITED_HEIGHT_PX)


def _scale_to_height(pixmap: QPixmap, height_px: int, widget: QWidget) -> QPixmap:
    """La imagen a esa altura lógica, nítida en una pantalla de alta densidad."""
    ratio = widget.devicePixelRatioF()
    scaled = pixmap.scaledToHeight(round(height_px * ratio), Qt.SmoothTransformation)
    scaled.setDevicePixelRatio(ratio)
    return scaled
