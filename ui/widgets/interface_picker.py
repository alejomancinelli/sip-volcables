"""
Elegir por qué interfaz de red escucha un servidor, sin tipear un nombre ni una dirección.

Dos formas, según lo que sabe hacer el servidor:

  - `build_address_combo()` — **una dirección**: la de Modbus TCP o la del video HTTP, que
    escuchan en un solo socket. Ofrece todas las interfaces (`0.0.0.0`), cada interfaz con
    su IPv4, y sólo el propio equipo (`127.0.0.1`). El valor que va al config es la
    dirección, en el item del combo.
  - `InterfaceChecklist` — **varias interfaces, por nombre**: la del RTSP, que levanta un
    servidor por interfaz, o las que mide el monitor de hardware. Una casilla por interfaz.

Las interfaces salen de `tools/net_interfaces.py` al construir el widget: es lo que el
equipo tiene ahora. Un valor del config que el equipo no tiene —un config copiado de otro
equipo, un cable que todavía no está— **no se pierde**: la dirección queda como opción del
combo con su forma cruda, y el nombre queda en la lista y se avisa debajo de las casillas.
Perderlo sería cambiar el config con sólo abrir la pantalla y guardar otra cosa.

Módulo de presentación: no lee ni escribe el config. Los valores entran y salen por la
pestaña que lo usa.
"""

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QCheckBox, QComboBox, QLabel, QVBoxLayout, QWidget

from tools.net_interfaces import NetInterface, list_interfaces
from ui.strings import tr
from ui.widgets.form import build_check_grid, build_value_combo_box

ALL_ADDRESSES = "0.0.0.0"
LOOPBACK_ADDRESS = "127.0.0.1"

_CHECK_COLUMNS = 3
_SPACING_PX = 8


def build_address_combo(current: str = ALL_ADDRESSES,
                        interfaces: list[NetInterface] | None = None) -> QComboBox:
    """
    Combo de la dirección en la que escucha un servidor. El valor se lee con
    `currentData()` y se carga con `set_combo_data()`, como cualquier combo de valor.

    Una interfaz sin IPv4 no se ofrece: no hay dirección en la que escuchar.
    """
    interfaces = list_interfaces() if interfaces is None else interfaces
    options = {f"{tr('net_all_interfaces')} ({ALL_ADDRESSES})": ALL_ADDRESSES}
    for interface in interfaces:
        if interface.ipv4:
            options[_describe(interface)] = interface.ipv4
    options[f"{tr('net_loopback')} ({LOOPBACK_ADDRESS})"] = LOOPBACK_ADDRESS
    return build_value_combo_box(options, current)


class InterfaceChecklist(QWidget):
    """
    Una casilla por interfaz del equipo. Ver el docstring del módulo.

    La lista vacía del config quiere decir «todas», también las que aparezcan después: así
    se carga —todas marcadas— y así se guarda cuando el operador las marca todas. Por eso la
    última marcada no se puede desmarcar: ninguna se guardaría como todas.

    Lo que no se tocó vuelve al archivo tal como estaba: un `[eth0, eth1]` en un equipo que
    tiene esas dos se ve con las dos marcadas, y guardar otra pestaña no lo convierte en
    `[]`.
    """

    def __init__(self, interfaces: list[NetInterface] | None = None, parent=None):
        super().__init__(parent)
        interfaces = list_interfaces() if interfaces is None else interfaces
        self._checks: dict[str, QCheckBox] = {}
        for interface in interfaces:
            check = QCheckBox(_describe(interface))
            check.setChecked(True)
            check.toggled.connect(lambda checked, check=check: self._keep_one_checked(check))
            self._checks[interface.name] = check
        self._loaded: list[str] = []
        self._loaded_state: dict[str, bool] = {}
        self._unknown: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_SPACING_PX)
        if self._checks:
            layout.addWidget(build_check_grid(list(self._checks.values()), _CHECK_COLUMNS))
        self._note = QLabel()
        self._note.setObjectName("settingsFieldHint")
        self._note.setWordWrap(True)
        layout.addWidget(self._note)
        self._refresh_note()

    def set_selected(self, names: object):
        """Marca las interfaces de la lista; vacía —o que no es lista— las marca todas."""
        selected = [str(name) for name in names] if isinstance(names, (list, tuple)) else []
        self._loaded = selected
        self._unknown = [name for name in selected if name not in self._checks]
        for name, check in self._checks.items():
            with QSignalBlocker(check):
                check.setChecked(not selected or name in selected)
        self._loaded_state = self._read_state()
        self._refresh_note()

    def get_selected(self) -> list[str]:
        """
        Lo que va al config: la lista cargada si nada cambió; si no, [] con todas marcadas o
        las marcadas más las del config que el equipo no tiene.
        """
        state = self._read_state()
        if state == self._loaded_state:
            return list(self._loaded)
        checked = [name for name, is_checked in state.items() if is_checked]
        if len(checked) == len(self._checks) and not self._unknown:
            return []
        return checked + self._unknown

    def _read_state(self) -> dict[str, bool]:
        return {name: check.isChecked() for name, check in self._checks.items()}

    def _keep_one_checked(self, check: QCheckBox):
        """Vuelve a marcar la última desmarcada: ninguna se guardaría como todas."""
        if any(other.isChecked() for other in self._checks.values()):
            return
        with QSignalBlocker(check):
            check.setChecked(True)

    def _refresh_note(self):
        if not self._checks:
            text = tr("net_none_found")
        elif self._unknown:
            text = tr("net_unknown_interfaces").format(names=", ".join(self._unknown))
        else:
            text = ""
        self._note.setText(text)
        self._note.setVisible(bool(text))


def _describe(interface: NetInterface) -> str:
    """«eth0 — 192.168.1.10», y si no tiene enlace, dicho al final."""
    text = f"{interface.name} — {interface.ipv4}" if interface.ipv4 else interface.name
    if not interface.is_up:
        text += f" ({tr('net_no_link')})"
    return text
