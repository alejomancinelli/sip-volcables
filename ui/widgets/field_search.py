"""
Los ajustes de una pestaña, para buscarlos, y el camino de la pantalla hasta uno.

`collect_settings(root)` lista las secciones (`SettingsSection`) y los campos (`FieldGrid`)
que la pestaña muestra, cada uno con su camino: las sub-pestañas que lo contienen —una por
cámara, una por pipeline— y la sección. Lee lo que ya está armado, así que una pestaña nueva,
o un campo nuevo en una que ya existe, aparece en la búsqueda sin declarar nada aparte; y lo
que la pestaña esconde —un campo que no aplica al driver elegido— no aparece.

`reveal(widget)` elige cada pestaña que contiene al widget, lo trae a la vista dentro de su
scroll, le da el foco y, si se le pasa qué resaltar, pinta su rótulo unos segundos —con la
propiedad `searchHit`, que pinta el QSS—, para que el ojo lo encuentre después del salto.

Módulo de presentación: no conoce ninguna pestaña ni clave del config.
"""

from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QScrollArea, QStackedWidget, QTabWidget, QWidget
from shiboken6 import isValid

from ui.widgets.settings_card import FieldGrid, SettingsSection

HIT_FIELD = "field"         # se pinta el rótulo del campo
HIT_SECTION = "section"     # se pinta el título de la sección

_HIT_PROPERTY = "searchHit"
_HIT_DURATION_MS = 2500
_SCROLL_MARGIN_PX = 48      # aire arriba y abajo del widget al traerlo a la vista


@dataclass(frozen=True)
class SettingRef:
    """Un ajuste encontrado: qué dice, dónde está y qué se resalta al llegar."""

    title: str
    hint: str               # la aclaración del campo o la descripción de la sección
    path: tuple[str, ...]   # sub-pestañas y sección, de afuera hacia adentro
    widget: QWidget         # lo que recibe el foco: el campo, o la sección entera
    highlight: QWidget      # lo que lleva `searchHit`: la celda del campo, o la sección
    hit: str                # HIT_FIELD o HIT_SECTION


def collect_settings(root: QWidget) -> list[SettingRef]:
    """Las secciones y los campos a la vista dentro de `root`, cada sección antes que sus campos."""
    refs = []
    for section in root.findChildren(SettingsSection):
        if _is_shown(section, root):
            refs.append(SettingRef(section.title, section.description,
                                   _get_path(section, root), section, section, HIT_SECTION))
    for grid in root.findChildren(FieldGrid):
        for field in grid.get_fields():
            if _is_shown(field.cell, root):
                refs.append(SettingRef(field.label, field.hint, _get_path(field.cell, root),
                                       field.widget, field.cell, HIT_FIELD))
    return refs


def reveal(widget: QWidget, *, highlight: QWidget | None = None, hit: str = ""):
    """
    Elige las pestañas que contienen a `widget` y, en la vuelta siguiente del loop —con la
    página ya acomodada—, lo trae a la vista, le da el foco y resalta `highlight`.
    """
    _select_tabs(widget)
    QTimer.singleShot(0, lambda: _scroll_and_flash(widget, highlight, hit))


# ── Internos ─────────────────────────────────────────────────────────────────

def _get_tab_page(widget: QWidget) -> tuple[QTabWidget, QWidget] | None:
    """(pestañas, página) si `widget` es la página de un QTabWidget; si no, None."""
    stack = widget.parentWidget()
    if isinstance(stack, QStackedWidget) and isinstance(stack.parentWidget(), QTabWidget):
        return stack.parentWidget(), widget
    return None


def _is_shown(widget: QWidget, root: QWidget) -> bool:
    """
    Si `widget` se vería con su pestaña elegida. Una página de pestañas está escondida
    mientras no es la actual, y eso no cuenta; una pestaña que su dueño oculta sí.
    """
    current = widget
    while current is not None and current is not root:
        tab_page = _get_tab_page(current)
        if tab_page is not None:
            tabs, page = tab_page
            if not tabs.isTabVisible(tabs.indexOf(page)):
                return False
        elif current.isHidden():
            return False
        current = current.parentWidget()
    return True


def _get_path(widget: QWidget, root: QWidget) -> tuple[str, ...]:
    """Los rótulos de las sub-pestañas y, si lo hay, el título de la sección que lo contiene."""
    path = []
    current = widget.parentWidget()
    while current is not None and current is not root:
        tab_page = _get_tab_page(current)
        if tab_page is not None:
            tabs, page = tab_page
            path.append(tabs.tabText(tabs.indexOf(page)))
        elif isinstance(current, SettingsSection):
            path.append(current.title)
        current = current.parentWidget()
    return tuple(reversed(path))


def _select_tabs(widget: QWidget):
    current = widget
    while current is not None:
        tab_page = _get_tab_page(current)
        if tab_page is not None:
            tabs, page = tab_page
            tabs.setCurrentWidget(page)
        current = current.parentWidget()


def _scroll_and_flash(widget: QWidget, highlight: QWidget | None, hit: str):
    if not isValid(widget):
        return
    target = highlight or widget
    # De adentro hacia afuera: primero el scroll de la sub-pestaña, después el de la página.
    current = target.parentWidget()
    while current is not None:
        if isinstance(current, QScrollArea):
            current.ensureWidgetVisible(target, 0, _SCROLL_MARGIN_PX)
        current = current.parentWidget()
    if widget.isEnabled() and widget.focusPolicy() != Qt.NoFocus:
        widget.setFocus(Qt.OtherFocusReason)
    if highlight is not None and hit:
        _set_hit(highlight, hit)
        QTimer.singleShot(_HIT_DURATION_MS,
                          lambda: isValid(highlight) and _set_hit(highlight, ""))


def _set_hit(widget: QWidget, hit: str):
    """Deja la marca y le pide al QSS que repinte los rótulos de adentro, que son los que cambian."""
    widget.setProperty(_HIT_PROPERTY, hit)
    for label in (widget, *widget.findChildren(QLabel)):
        label.style().unpolish(label)
        label.style().polish(label)
