"""
Una página de la barra lateral: su encabezado y sus pestañas —de configuración o de
diagnóstico— o, si no tiene pestañas, un solo contenido.

Todo va en una columna centrada del ancho de la web (`PAGE_CONTENT_WIDTH_PX`): el encabezado,
la barra de pestañas y las tarjetas arrancan en el mismo borde, y en una pantalla ancha lo que
sobra queda a los costados en vez de separar el título del contenido.

Sólo muestra. Las pestañas las crean y las alimentan `ConfigTabs` y `DiagnosticsTabs`, y esta
vista las recibe ya hechas y las envuelve en la tarjeta del tema según lo que cada una declara
(`IS_CENTERED`, `IS_SCROLLABLE`). Una página con una sola pestaña esconde la barra: un rótulo
que repite el título no agrega nada.

Si alguna de sus pestañas edita el config, la página lleva la barra de cambios sin guardar
(`ui/widgets/save_bar.py`), que aparece sólo cuando hay algo editado —`set_pending()`, con
los títulos de las pestañas que cambiaron— y publica «Descartar» y «Guardar y aplicar» por
señal. Guardar es de `ConfigTabs`, que guarda todas las pestañas de configuración juntas,
estén en esta página o en otra: por eso la barra nombra también las de otras páginas.
Mientras está a la vista, la página deja libre abajo su alto, para que no tape el final del
contenido.
"""

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QSizePolicy, QTabWidget, QVBoxLayout, QWidget

from ui.strings import tr
from ui.widgets.form import PAGE_CONTENT_WIDTH_PX, wrap_in_card
from ui.widgets.page_header import PageHeader
from ui.widgets.save_bar import SaveBar

_MARGINS_PX = (32, 28, 32, 20)    # izquierda, arriba, derecha, abajo: el aire de la página
_HEADER_GAP_PX = 24               # entre el encabezado y lo que sigue
_SPACING_PX = 8


class PageView(QWidget):
    """Página con encabezado y pestañas, o con un contenido. Ver el docstring del módulo."""

    save_requested = Signal()       # el operador apretó «Guardar y aplicar»
    discard_requested = Signal()    # el operador apretó «Descartar»

    def __init__(self, title: str, *, eyebrow: str = "", description: str = "",
                 tabs: list[QWidget] | None = None, content: QWidget | None = None,
                 can_save: bool = False, parent=None):
        super().__init__(parent)
        self._save_bar: SaveBar | None = None
        self._outer = QHBoxLayout(self)
        outer = self._outer
        outer.setContentsMargins(*_MARGINS_PX)
        outer.setSpacing(0)

        column = QWidget()
        column.setMaximumWidth(PAGE_CONTENT_WIDTH_PX)
        column.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_SPACING_PX)
        # La columna tiene el stretch: crece hasta su tope y lo que sobra va a los costados.
        outer.addStretch(0)
        outer.addWidget(column, 1)
        outer.addStretch(0)

        layout.addWidget(PageHeader(title, eyebrow=eyebrow, description=description))
        layout.addSpacing(_HEADER_GAP_PX - _SPACING_PX)

        if tabs:
            tab_widget = QTabWidget()
            tab_widget.setTabBarAutoHide(True)
            for tab in tabs:
                tab_widget.addTab(
                    wrap_in_card(tab, center=tab.IS_CENTERED, scroll=tab.IS_SCROLLABLE),
                    tr(tab.TITLE_KEY))
            layout.addWidget(tab_widget, stretch=1)
        elif content is not None:
            layout.addWidget(content, stretch=1)

        if can_save:
            self._save_bar = SaveBar(self)
            self._save_bar.save_requested.connect(self.save_requested)
            self._save_bar.discard_requested.connect(self.discard_requested)

    @property
    def is_save_bar_visible(self) -> bool:
        return self._save_bar is not None and self._save_bar.isVisible()

    def set_pending(self, titles: list[str]):
        """
        Muestra la barra de guardar con las pestañas que cambiaron, o la esconde sin cambios.
        Una página que no edita el config la ignora.
        """
        if self._save_bar is None:
            return
        left, top, right, bottom = _MARGINS_PX
        if titles:
            self._save_bar.set_message(tr("save_bar_message").format(tabs=", ".join(titles)))
            self._outer.setContentsMargins(left, top, right,
                                           max(bottom, self._save_bar.get_reserved_height()))
            self._save_bar.show_animated()
        else:
            self._save_bar.hide()
            self._outer.setContentsMargins(left, top, right, bottom)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.is_save_bar_visible:
            self._save_bar.place()
