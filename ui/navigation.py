"""
Las páginas de la aplicación: qué hay en la barra lateral, agrupado, y qué pestañas muestra
cada página.

Tabla de datos, no maquinaria: es el único lugar que se toca para agregar una página o
mover una pestaña de una a otra. Las páginas se ordenan por **subsistema** —cámaras,
Modbus, sistema— y no por tipo de pantalla, así que una página junta las pestañas que
editan una sección del config con las que muestran ese subsistema andando: quien revisa el
Modbus ve el mapa que configura y los registros que salen sin cambiar de lugar.

Una pestaña aparece en una sola página. Las de configuración las crea y las guarda juntas
`ConfigTabs` —un único «Guardar y aplicar» para todas, esté donde esté cada una— y las de
diagnóstico las crea y las alimenta `DiagnosticsTabs`; las páginas sólo las muestran.

Tres páginas no tienen pestañas y las arma la ventana: el inicio, que presenta el equipo y
es la que se ve al abrir; el monitor, cuyo centro pone el fork, y la de entradas y salidas,
que existe sólo si se cableó un controlador de GPIO.
"""

from dataclasses import dataclass

from ui.views.config.cameras_tab import CamerasTab
from ui.views.config.collector_tab import CollectorTab
from ui.views.config.inference_tab import InferenceTab
from ui.views.config.modbus_tab import ModbusTab
from ui.views.config.process_tab import ProcessTab
from ui.views.config.system_tab import SystemTab
from ui.views.config.telemetry_tab import TelemetryTab
from ui.views.config.video_tab import VideoTab
from ui.views.diagnostics.hardware_tab import HardwareTab
from ui.views.diagnostics.license_tab import LicenseTab
from ui.views.diagnostics.logs_tab import LogsTab
from ui.views.diagnostics.modbus_stats_tab import ModbusStatsTab
from ui.views.diagnostics.modbus_tab import ModbusTab as ModbusRegistersTab
from ui.views.diagnostics.video_tab import VideoTab as VideoStreamsTab

PAGE_HOME = "home"
PAGE_MONITOR = "monitor"
PAGE_GPIO = "gpio"


@dataclass(frozen=True)
class Page:
    """Una entrada de la barra lateral. `tabs` va en el orden en que se muestran."""

    key: str
    title_key: str            # clave de ui/strings.py, el mismo texto en la barra y la página
    icon: str                 # ícono de Tabler, en ui/icons/tabler/
    tabs: tuple = ()
    description_key: str = ""   # la línea bajo el título de la página


# (clave del rótulo del grupo, páginas). El orden es el de la barra lateral.
NAVIGATION: tuple[tuple[str, tuple[Page, ...]], ...] = (
    ("nav_group_operation", (
        Page(PAGE_HOME, "nav_home", "home"),
        Page(PAGE_MONITOR, "nav_monitor", "layout-dashboard"),
        Page("process", "nav_process", "gauge", (ProcessTab,),       # la llena cada fork
             "nav_process_description"),
    )),
    ("nav_group_vision", (
        Page("cameras", "nav_cameras", "device-cctv", (CamerasTab,),
             "nav_cameras_description"),
        Page("inference", "nav_inference", "brain", (InferenceTab, CollectorTab),
             "nav_inference_description"),
    )),
    ("nav_group_integration", (
        # El canal con el PLC tiene página propia: primero si anda, después qué sale y
        # al final cómo se configura.
        Page("modbus", "nav_modbus", "topology-star-3",
             (ModbusStatsTab, ModbusRegistersTab, ModbusTab), "nav_modbus_description"),
        Page("video_telemetry", "nav_video_telemetry", "broadcast",
             (VideoStreamsTab, VideoTab, TelemetryTab), "nav_video_telemetry_description"),
    )),
    ("nav_group_device", (
        # Como el Modbus: primero si anda —el hardware, las cámaras, la red y la licencia—
        # y al final cómo se configura.
        Page("system", "nav_system", "cpu", (HardwareTab, LicenseTab, SystemTab),
             "nav_system_description"),
        Page("logs", "nav_logs", "list-details", (LogsTab,), "nav_logs_description"),
        Page(PAGE_GPIO, "nav_gpio", "plug-connected", description_key="nav_gpio_description"),
    )),
)


def get_pages() -> tuple[Page, ...]:
    """Todas las páginas, en el orden de la barra lateral."""
    return tuple(page for _, pages in NAVIGATION for page in pages)


def get_page(key: str) -> Page | None:
    """La página de esa clave, o None si no está en la tabla."""
    return next((page for page in get_pages() if page.key == key), None)


def get_group_key(key: str) -> str:
    """La clave del rótulo del grupo de la página, o '' si no está en la tabla."""
    return next((group_key for group_key, pages in NAVIGATION
                 if any(page.key == key for page in pages)), "")


def get_tab_classes(base: type) -> tuple[type, ...]:
    """Las clases de pestaña que heredan de `base`, en el orden en que aparecen."""
    return tuple(tab_class for page in get_pages() for tab_class in page.tabs
                 if issubclass(tab_class, base))
