"""
Contrato de una pestaña de configuración.

Una pestaña es dueña de **una sección** de `config.yaml`: arma sus campos en el
constructor, los llena desde el config en `load()` y los escribe en `save()`. Nadie
más toca esas claves, así que agregar una sección al config es agregar un archivo acá
y una línea en la página que la muestra, en `ui/navigation.py`.

Tres reglas del contrato:

  - **`save()` no persiste.** Deja los valores en el ConfigManager con `set()` y
    nada más; el `save()` del archivo lo llama `ConfigTabs` una sola vez, cuando
    todas las pestañas escribieron. Así un error en la pestaña cinco no deja el
    config.yaml a medio escribir.
  - **`load()` tiene que poder llamarse muchas veces.** Es lo que hace el botón de
    descartar, y también el arranque: la misma función, sin estado acumulado.
  - **`TITLE_KEY` es una clave de la tabla de idiomas**, no un texto. El título se
    resuelve al construir la pestaña, para que salga en el idioma activo.

La pestaña no se preocupa por el alto: la página la envuelve en un área con scroll
(`IS_SCROLLABLE`), así que puede tener los campos que necesite. Sin eso, una pestaña más
alta que la pantalla estira el QStackedWidget, le corta los botones de abajo y deforma
también a las otras vistas, que comparten el alto del stack.

No es una clase abstracta de `abc`: un QWidget con metaclase ABCMeta pelea con la de
Qt. Los métodos levantan NotImplementedError, que para una jerarquía de tres archivos
alcanza y no arrastra el problema.
"""

from PySide6.QtWidgets import QWidget

from system.config_manager import ConfigManager


class AbstractConfigTab(QWidget):
    """Pestaña dueña de una sección del config. Ver el contrato en el docstring del módulo."""

    TITLE_KEY = ""       # clave de ui/strings.py con el título de la pestaña
    IS_CENTERED = True   # los grupos de campos van en la tarjeta centrada
    IS_SCROLLABLE = True

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(parent)
        self._config = config_manager

    def load(self):
        """Lleva los valores del config a los campos. Descarta lo que hubiera editado."""
        raise NotImplementedError

    def save(self):
        """Lleva los valores de los campos al ConfigManager. No persiste el archivo."""
        raise NotImplementedError
