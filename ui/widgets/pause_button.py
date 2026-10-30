"""
Botón de pausa del video de un diálogo de dibujo: congela el frame en vivo para marcar sobre
una imagen quieta.

Es un botón con estado: apretado, el diálogo descarta los frames que le llegan y sigue
trabajando —lienzo, vista previa, avisos— sobre el último que mostró. Lo que se guarda no
cambia: las coordenadas son del frame de la cámara, esté quieto o no.
"""

from PySide6.QtWidgets import QPushButton

from ui.strings import tr


class PauseButton(QPushButton):
    """Botón que alterna entre pausar y reanudar el video; `is_paused` dice en cuál está."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.toggled.connect(self._on_toggled)
        self._on_toggled(False)

    @property
    def is_paused(self) -> bool:
        return self.isChecked()

    def _on_toggled(self, is_checked: bool):
        self.setText(tr("dialog_resume") if is_checked else tr("dialog_pause"))
