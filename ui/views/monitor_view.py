"""
Vista de operador: un área central **vacía**, que llena el fork.

El área central es lo más específico de cada instalación —qué se mira mientras la
línea trabaja— y por eso el template no la llena: el fork le pasa su widget con
`set_content()` y esta vista no lo conoce. Para el caso normal ya está armado
`ui/widgets/camera_grid.py`, que muestra las cámaras configuradas y no hace falta
escribir.

La vista es toda del contenido: no lleva barras propias, para que el video tenga todo el
espacio. El log está en su página y el estado de los canales de salida, en las pestañas
de cada uno.

Vista de sólo lectura: no edita config y no comanda nada.
"""

from PySide6.QtWidgets import QVBoxLayout, QWidget

from system.config_manager import ConfigManager

_MARGINS_PX = (16, 12, 16, 12)    # izquierda, arriba, derecha, abajo


class MonitorView(QWidget):
    """Vista de operador. El área central la llena el fork con `set_content()`."""

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(parent)
        self._config = config_manager
        self._content: QWidget | None = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(*_MARGINS_PX)

    def set_content(self, widget: QWidget):
        """
        Pone el widget del proyecto en el área central, en lugar del que hubiera.

        Es el punto de extensión de esta vista: la grilla de cámaras, los paneles de
        proceso del fork, o los dos dentro de un layout propio.
        """
        if self._content is not None:
            self._layout.removeWidget(self._content)
            self._content.deleteLater()
        self._content = widget
        self._layout.addWidget(widget)
