"""
Pestaña de licencia: qué licencia tiene el equipo, y los dos botones de la renovación.

**No valida nada.** Recibe el dict de `LicenseManager.get_status()` por `update_license()`
y lo muestra; quién decide si una licencia vale es el subsistema, no la pantalla. El color
del chip de cada estado sale de `ui/service_status.py`, el mismo que usa la página de
inicio.

Los dos botones **no leen ni escriben archivos**: abren un diálogo para elegir una ruta
—que es presentación, no I/O— y emiten la ruta por una señal. El que abre el archivo,
verifica la firma y lo copia a su lugar es el cableado, que para eso tiene el manager. Es
la misma división que con las URLs de los streams: la pantalla muestra, el dueño del dato
hace.

El flujo del cliente, que es el que estos dos botones tienen que hacer simple:

    Exportar solicitud  ->  el archivo viaja por mail  ->  vuelve un .lic  ->  Instalar

Se importa el vocabulario de estados de `system/license/manager.py` en vez de repetirlo:
es una tabla con dueño único y sin Qt, el mismo caso que los módulos de bits de
`system/formats/`, y copiar los siete nombres acá sería tener dos listas que se van a
desincronizar el día que se agregue un estado.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from system.config_manager import ConfigManager
from system.license import manager
from system.license.request import REQUEST_FILENAME

from ui import service_status
from ui.strings import tr
from ui.views.diagnostics.abstract_tab import AbstractDiagnosticsTab
from ui.widgets.settings_card import SettingsCard, SettingsSection
from ui.widgets.status_chip import CHIP_ERROR, CHIP_OK, CHIP_WARNING, StatusChip

_EMPTY_VALUE = "—"
_COLUMNS = 3
_ERROR_PROPERTY = "error"         # lo pinta el QSS: el resultado de una instalación que falló


class LicenseTab(AbstractDiagnosticsTab):
    """Estado de la licencia del equipo y el flujo de renovación."""

    TITLE_KEY = "tab_license"
    IS_SCROLLABLE = True      # una tarjeta de cinco secciones; en una pantalla baja no entra

    export_requested = Signal(str)    # ruta elegida para escribir la solicitud
    install_requested = Signal(str)   # ruta del .lic que el operador quiere instalar

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        self._values: dict = {}

        self._state_chip = StatusChip(pill=True)
        self._expiry_chip = StatusChip(pill=True)
        self._reason = QLabel()
        self._reason.setObjectName("settingsFieldHint")
        self._reason.setWordWrap(True)
        self._result = QLabel()
        self._result.setObjectName("licenseResult")
        self._result.setWordWrap(True)

        card = SettingsCard(tr("lic_card"))
        self._build_state_section(card)
        self._build_license_section(card)
        self._build_machine_section(card)
        self._build_entitlements_section(card)
        self._build_renewal_section(card)

        layout = QVBoxLayout(self)
        # Sin margen propio: la tarjeta arranca en el borde del encabezado de la página.
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(card)
        layout.addStretch()

        self._show_result("")
        self.update_license({})

    # ── API pública ──────────────────────────────────────────────────────────

    def update_license(self, status: dict):
        """
        Muestra el dict de `LicenseManager.get_status()`.

        Con un dict vacío queda en «sin datos», que es lo que se ve hasta que el cableado
        publica el primer estado.
        """
        self._values["license_id"].setText(str(status.get("license_id") or _EMPTY_VALUE))
        self._values["client"].setText(str(status.get("client") or _EMPTY_VALUE))
        self._values["project_id"].setText(str(status.get("project_id") or _EMPTY_VALUE))
        self._values["issued_at"].setText(_format_date(status.get("issued_at")))
        self._values["expires_at"].setText(
            tr("lic_perpetual") if status.get("is_perpetual")
            else _format_date(status.get("expires_at"))
        )
        self._values["fingerprint"].setText(_format_fingerprint(status))
        self._values["sources"].setText(
            ", ".join(status.get("fingerprint_sources") or ()) or _EMPTY_VALUE)
        self._values["build"].setText(
            tr("lic_build_compiled") if status.get("is_compiled") else tr("lic_build_sources"))
        self._values["cameras"].setText(_format_cameras(status.get("max_cameras")))
        self._values["features"].setText(", ".join(status.get("features") or ()) or _EMPTY_VALUE)

        text, chip_state = service_status.describe_license(status)
        self._state_chip.set_state(chip_state, text)
        self._update_expiry_chip(status)

        reason = str(status.get("reason") or "")
        self._reason.setText(reason)
        self._reason.setVisible(bool(reason))

    def show_install_result(self, is_ok: bool, message: str):
        """Resultado de la última instalación, tal como lo devolvió el manager."""
        self._show_result(message, is_error=not is_ok)

    # ── Internos ─────────────────────────────────────────────────────────────

    def _build_state_section(self, card: SettingsCard):
        section = card.add_section(tr("lic_box_state"), tr("lic_state_description"), _COLUMNS)
        chips = QWidget()
        row = QHBoxLayout(chips)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self._state_chip)
        row.addWidget(self._expiry_chip)
        row.addStretch()
        section.add_row(chips)
        section.add_row(self._reason)

    def _build_license_section(self, card: SettingsCard):
        section = card.add_section(tr("lic_box_license"), tr("lic_license_description"),
                                   _COLUMNS)
        self._add_value(section, "license_id", "lic_id")
        self._add_value(section, "client", "lic_client")
        self._add_value(section, "project_id", "lic_project")
        self._add_value(section, "issued_at", "lic_issued")
        self._add_value(section, "expires_at", "lic_expires")

    def _build_machine_section(self, card: SettingsCard):
        section = card.add_section(tr("lic_box_machine"), tr("lic_machine_description"),
                                   _COLUMNS)
        self._add_value(section, "fingerprint", "lic_fingerprint")
        self._add_value(section, "sources", "lic_sources", span=2)
        self._add_value(section, "build", "lic_build")

    def _build_entitlements_section(self, card: SettingsCard):
        section = card.add_section(tr("lic_box_entitlements"),
                                   tr("lic_entitlements_description"), _COLUMNS)
        self._add_value(section, "cameras", "lic_max_cameras")
        self._add_value(section, "features", "lic_features", span=2)

    def _build_renewal_section(self, card: SettingsCard):
        section = card.add_section(tr("lic_box_renewal"), tr("lic_renewal_hint"), _COLUMNS)
        export_button = QPushButton(tr("lic_export_button"))
        export_button.setObjectName("primaryButton")
        export_button.clicked.connect(self._on_export_clicked)
        install_button = QPushButton(tr("lic_install_button"))
        install_button.clicked.connect(self._on_install_clicked)

        buttons = QWidget()
        row = QHBoxLayout(buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(export_button)
        row.addWidget(install_button)
        row.addStretch()
        section.add_row(buttons)
        section.add_row(self._result)

    def _add_value(self, section: SettingsSection, key: str, label_key: str, *, span: int = 1):
        value = QLabel(_EMPTY_VALUE)
        value.setObjectName("tableValue")
        value.setWordWrap(True)
        value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._values[key] = value
        section.add_field(tr(label_key), value, span=span)

    def _update_expiry_chip(self, status: dict):
        days = status.get("days_remaining")
        if days is None:
            self._expiry_chip.setVisible(False)
            return

        self._expiry_chip.setVisible(True)
        if days <= 0:
            self._expiry_chip.set_state(CHIP_ERROR, tr("lic_expired"))
            return

        text = tr("lic_days_left_one") if days == 1 else tr("lic_days_left").format(days=days)
        state = CHIP_WARNING if days <= manager.EXPIRY_WARNING_DAYS else CHIP_OK
        self._expiry_chip.set_state(state, text)

    def _show_result(self, message: str, *, is_error: bool = False):
        self._result.setText(message)
        self._result.setVisible(bool(message))
        if self._result.property(_ERROR_PROPERTY) != is_error:
            self._result.setProperty(_ERROR_PROPERTY, is_error)
            self._result.style().unpolish(self._result)
            self._result.style().polish(self._result)

    def _on_export_clicked(self):
        # El resultado se limpia al empezar la acción siguiente y no al llegar un estado
        # nuevo: así el mensaje no depende de en qué orden el cableado llame a los dos
        # métodos públicos, que es una trampa que se paga una sola vez y tarde.
        self._show_result("")
        path, _ = QFileDialog.getSaveFileName(
            self, tr("lic_export_title"), REQUEST_FILENAME, tr("lic_request_filter"))
        if path:
            self.export_requested.emit(path)

    def _on_install_clicked(self):
        self._show_result("")
        path, _ = QFileDialog.getOpenFileName(
            self, tr("lic_install_title"), "", tr("lic_license_filter"))
        if path:
            self.install_requested.emit(path)


def _format_date(value: object) -> str:
    """Recorta el instante ISO al día: la hora exacta de emisión no le sirve a nadie acá."""
    text = str(value or "")
    return text[:10] if len(text) >= 10 else _EMPTY_VALUE


def _format_cameras(max_cameras: object) -> str:
    return tr("lic_cameras_unlimited") if max_cameras is None else str(max_cameras)


def _format_fingerprint(status: dict) -> str:
    required = status.get("fingerprint_required")
    if not required:
        return _EMPTY_VALUE
    return tr("lic_fingerprint_value").format(
        matches=status.get("fingerprint_matches", 0), required=required)
