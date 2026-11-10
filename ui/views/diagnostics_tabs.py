"""
Las pestañas de diagnóstico: las crea y les reparte lo que llega.

No es una pantalla. Las páginas de `ui/navigation.py` muestran cada pestaña donde
corresponde; esto recibe métricas, estados y logs y los reenvía a la pestaña que los
muestra. Una pestaña que no conoce un servicio lo ignora, así que el cableado publica todo
en un solo lugar y no tiene que saber quién lo dibuja ni en qué página está.

Las pestañas existen desde el arranque, aunque su página todavía no se haya abierto: así
el log y las gráficas ya traen lo que pasó cuando el operador llega a mirarlas.
"""

from PySide6.QtCore import QObject, Signal, Slot

from system.config_manager import ConfigManager
from ui.views.diagnostics.abstract_tab import AbstractDiagnosticsTab
from ui.views.diagnostics.hardware_tab import HardwareTab
from ui.views.diagnostics.license_tab import LicenseTab
from ui.views.diagnostics.logs_tab import LogsTab
from ui.views.diagnostics.modbus_stats_tab import ModbusStatsTab
from ui.views.diagnostics.modbus_tab import ModbusTab
from ui.views.diagnostics.video_tab import VideoTab


class DiagnosticsTabs(QObject):
    """Dueño de las pestañas de diagnóstico. Reparte lo que le empuja el cableado."""

    # Las dos rutas que el operador eligió en un diálogo. Quien abre el archivo es el
    # cableado: la pantalla elige la ruta y avisa. Una por acción del usuario.
    license_export_requested = Signal(str)
    license_install_requested = Signal(str)

    def __init__(self, config_manager: ConfigManager, tab_classes: tuple[type, ...],
                 parent=None):
        super().__init__(parent)
        self._tabs: list[AbstractDiagnosticsTab] = [tab_class(config_manager)
                                                    for tab_class in tab_classes]
        license_tab = self.get_tab(LicenseTab)
        if license_tab is not None:
            license_tab.export_requested.connect(self.license_export_requested)
            license_tab.install_requested.connect(self.license_install_requested)

    # ── API pública ──────────────────────────────────────────────────────────

    def get_tab(self, tab_class: type) -> AbstractDiagnosticsTab | None:
        """La pestaña de esa clase, o None si ninguna página la muestra."""
        for tab in self._tabs:
            if isinstance(tab, tab_class):
                return tab
        return None

    def set_service_status(self, service: str, status: str):
        """Reparte el estado de un servicio a las pestañas que lo muestran."""
        for tab in self._tabs:
            setter = getattr(tab, "set_service_status", None)
            if setter is not None:
                setter(service, status)

    def set_client_count(self, service: str, count: int):
        self._call(VideoTab, "set_client_count", service, count)

    def set_stream_urls(self, service: str, urls: list):
        self._call(VideoTab, "set_stream_urls", service, urls)

    def log_event(self, level: str, message: str, module: str = ""):
        self._call(LogsTab, "log_event", level, message, module)

    def show_license_result(self, is_ok: bool, message: str):
        """Resultado de instalar una licencia, tal como lo devolvió el manager."""
        self._call(LicenseTab, "show_install_result", is_ok, message)

    def apply_theme(self, dark: bool):
        for tab in self._tabs:
            tab.apply_theme(dark)

    # ── Slots de actualización ───────────────────────────────────────────────

    @Slot(object)
    def update_hardware_metrics(self, metrics: dict):
        """Slot para el dict de `SystemMonitor.get_metrics()`."""
        self._call(HardwareTab, "update_metrics", metrics)

    @Slot(object, str)
    def update_camera_status(self, status: dict, camera_slot: str):
        """Slot con la firma de `CaptureThread.status_updated`: (status, slot)."""
        self._call(HardwareTab, "update_camera_status", status, camera_slot)

    @Slot(object)
    def update_modbus_values(self, values: dict):
        """Slot para {dirección: valor} de los registros que se están publicando."""
        self._call(ModbusTab, "update_values", values)

    @Slot(object)
    def update_modbus_stats(self, stats: dict):
        """Slot para el dict de `SharedModbusServer.get_stats()`."""
        self._call(ModbusStatsTab, "update_stats", stats)

    @Slot(object)
    def update_license(self, status: dict):
        """Slot para el dict de `LicenseManager.get_status()`."""
        self._call(LicenseTab, "update_license", status)

    # ── Internos ─────────────────────────────────────────────────────────────

    def _call(self, tab_class: type, method: str, *args):
        """
        Llama al método de la pestaña de esa clase. Si ninguna página la muestra, lo que
        le llega se descarta: un fork que saca una pestaña de la navegación no rompe el
        cableado, que sigue publicando igual.
        """
        tab = self.get_tab(tab_class)
        if tab is not None:
            getattr(tab, method)(*args)
