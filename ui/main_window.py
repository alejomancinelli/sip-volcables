"""
Ventana principal: barra lateral con navegación, barra superior, las páginas y la barra
de estado.

Es la puerta de la UI para el cableado: `main.py` construye esta ventana y le habla
sólo a ella —`set_service_status()`, `log_event()`, los slots de actualización—, sin
saber qué página dibuja cada cosa. Así mover una pestaña de página no cambia el cableado.

Qué páginas hay y qué pestañas muestra cada una lo dice `ui/navigation.py`. Las pestañas
las crean `ConfigTabs` —que las guarda todas juntas— y `DiagnosticsTabs` —que les reparte
lo que llega—, y esta ventana arma cada página con las que le tocan.

La forma es la del sistema de diseño de IEA: la marca vive en la barra lateral —oscura
en los dos temas—, la barra superior dice dónde se está y el área de trabajo queda
limpia para los datos. Las tipografías y los íconos los registra y los pinta
`ui/theme.py`.

La barra superior lleva además la búsqueda de la web —el campo y `Ctrl+K`—, que abre la
paleta (`ui/widgets/command_palette.py`) con las páginas, sus pestañas, cada campo y cada
sección de las pestañas de configuración, y el cambio de tema. Elegir un ajuste lleva a su
página, elige su pestaña y su sub-pestaña y lo deja a la vista con el foco; los campos los
lee `ui/widgets/field_search.py` de lo que cada pestaña ya armó, así que ninguna declara
nada para aparecer.

Tres cosas que no son obvias:

  - **Las pestañas de configuración se arman la primera vez que se abre una página que
    tenga alguna**, o la búsqueda, que tiene que poder encontrar sus campos. Son nueve pestañas con cientos de campos y el arranque de la
    aplicación no las necesita. Se arman todas juntas porque se guardan todas juntas. Las
    de diagnóstico, en cambio, existen desde el arranque: reciben datos desde el primer
    segundo, aunque su página no se haya abierto.
  - **La página de entradas y salidas aparece sólo si hay controlador.** `set_gpio()` es
    lo que la habilita: un equipo que no lo inyecta no tiene esa entrada en la barra, en
    vez de tener una que no comanda nada.
  - **Sin licencia válida, el footer lo dice todo el tiempo y lo dice una sola vez al
    abrir.** `update_license()` pinta toda la barra de rojo mientras el estado lo
    amerite —el mismo criterio que el bit que va al PLC—, y no un chip: en una barra de
    28px un chip se pierde, y esto no es algo que se pueda pasar por alto. En el primer
    `showEvent` se suma un cartel modal si el problema ya estaba ahí al arrancar. El
    cartel es de una sola vez a propósito: molestar en cada recheck horario sería peor
    que el problema que avisa.

La ventana no toca hardware ni protocolos: reparte lo que recibe y persiste config a
través de `ConfigTabs`.
"""

import os
from dataclasses import dataclass

from PySide6.QtCore import QSize, QTime, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton, QStackedWidget,
    QVBoxLayout, QWidget,
)

from system.config_manager import ConfigManager
from system.version import APP_VERSION

from ui import navigation, theme
from ui.navigation import PAGE_GPIO, PAGE_HOME, PAGE_MONITOR
from ui.strings import tr
from ui.views.config.abstract_tab import AbstractConfigTab
from ui.views.config_tabs import ConfigTabs
from ui.views.diagnostics.abstract_tab import AbstractDiagnosticsTab
from ui.views.diagnostics_tabs import DiagnosticsTabs
from ui.views.gpio_view import GpioView
from ui.views.home_view import HomeView
from ui.views.monitor_view import MonitorView
from ui.views.page_view import PageView
from ui.widgets import field_search
from ui.widgets.command_palette import CommandPalette, PaletteEntry
from ui.widgets.form import WheelGuard
from ui.widgets.sidebar import Sidebar

_TOP_BAR_HEIGHT_PX = 56
_TOP_BAR_MARGIN_PX = 24       # el mismo aire que las vistas dejan contra la barra lateral
_BREADCRUMB_SPACING_PX = 8
_TOP_BAR_ICON_PX = 18
_TOP_BAR_BUTTON_PX = 32
_SEARCH_WIDTH_PX = 256
_SEARCH_ICON_PX = 15
_SEARCH_SHORTCUT = "Ctrl+K"
_SEARCH_SHORTCUT_TEXT = "Ctrl K"
_SETTING_ICON = "adjustments-horizontal"
_PATH_SEPARATOR = " › "
_FOOTER_HEIGHT_PX = 28
_FOOTER_SPACING_PX = 18       # aire entre los datos del lado derecho de la barra de estado
_WINDOW_WIDTH_PX = 1920
_WINDOW_HEIGHT_PX = 1080
_CLOCK_INTERVAL_MS = 1000

_ICONS_DIRNAME = "icons"
_WINDOW_ICON_FILENAME = "iea_100x100.ico"
_BREADCRUMB_SEPARATOR = "›"

# Flash del footer: se pinta del color de aviso y se apaga hasta el fondo del tema, para
# que un mensaje nuevo se note sin tener que leerlo. Es el único estilo animado de la UI
# y por eso el único que se aplica inline: el QSS no interpola colores.
_FLASH_STEPS = 55
_FLASH_INTERVAL_MS = 50

# Propiedad Qt que marca la barra de estado sin licencia válida; la pinta el QSS.
_ALERT_PROPERTY = "alert"

_ACTION_TOGGLE_THEME = "toggle_theme"


@dataclass(frozen=True)
class _SearchTarget:
    """Adónde lleva una entrada de la búsqueda: una página y, adentro, un widget; o una acción."""

    page_key: str = ""
    widget: QWidget | None = None       # una pestaña, un campo o una sección
    highlight: QWidget | None = None    # lo que se resalta al llegar
    hit: str = ""
    action: str = ""


class MainWindow(QMainWindow):
    """Ventana principal. Ver el contrato en el docstring del módulo."""

    config_saved = Signal()      # se persistió el config.yaml; reenvía el de ConfigTabs
    # Slot de la cámara cuya óptica hay que calibrar. Lo atiende `main.py`, que es
    # quien tiene los frames: la UI no mide.
    lens_calibration_requested = Signal(str)
    # Rutas que el operador eligió en la pestaña de licencia; las reenvía DiagnosticsTabs.
    # Quien abre los archivos es el cableado, no la ventana.
    license_export_requested = Signal(str)
    license_install_requested = Signal(str)

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(parent)
        self._config = config_manager
        self._result_source = None
        self._config_tabs: ConfigTabs | None = None
        self._pages: dict[str, QWidget] = {}
        self._is_dark = True
        self._flash_step = 0
        self._license_status: dict = {}
        self._license_invalid = False
        # El cartel de arranque es de una sola vez: sin este flag, minimizar y restaurar
        # la ventana —que también dispara `showEvent`— lo mostraría de nuevo.
        self._did_show_license_warning = False

        self.setWindowTitle(str(self._config.get("system.app_name", "")))
        self._apply_window_icon()
        self.resize(_WINDOW_WIDTH_PX, _WINDOW_HEIGHT_PX)
        theme.load_fonts()
        # Sobre toda la aplicación, así alcanza también a los diálogos: ver `WheelGuard`.
        self._wheel_guard = WheelGuard(self)
        QApplication.instance().installEventFilter(self._wheel_guard)

        self._diagnostics = DiagnosticsTabs(
            self._config, navigation.get_tab_classes(AbstractDiagnosticsTab), self)
        self._diagnostics.license_export_requested.connect(self.license_export_requested)
        self._diagnostics.license_install_requested.connect(self.license_install_requested)

        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self._build_sidebar())

        column = QVBoxLayout()
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        root_layout.addLayout(column, stretch=1)
        column.addWidget(self._build_top_bar())

        self._stack = QStackedWidget()
        column.addWidget(self._stack, stretch=1)

        self._home = HomeView(self._config)
        self._home.monitor_requested.connect(lambda: self._navigate(PAGE_MONITOR))
        self._add_page(PAGE_HOME, self._home)
        self.monitor_view = MonitorView(self._config)
        self._add_page(PAGE_MONITOR, self.monitor_view)
        self._sidebar.set_item_visible(PAGE_GPIO, False)

        column.addWidget(self._build_footer())

        self._palette = CommandPalette(self)
        self._palette.entry_chosen.connect(self._on_search_chosen)
        QShortcut(QKeySequence(_SEARCH_SHORTCUT), self, activated=self._open_search)

        self._navigate(PAGE_HOME)
        self.apply_theme()
        self.set_status_message(tr("status_started"))

    # ── API pública ──────────────────────────────────────────────────────────

    def set_result_source(self, result_source):
        """
        Inyecta `result_source(cámara, pipeline) -> InferenceResult | None`: el último
        resultado de ese par, que lleva el cableado. Lo usa el diálogo de la altura para
        medir sobre un frame inferido cuando se pausa; sin esto se abre sin medidas.
        """
        self._result_source = result_source

    def set_gpio(self, gpio_controller: object, gpio_thread: object = None):
        """
        Inyecta el controlador de GPIO y, si lo hay, el hilo que informa las entradas.

        Sin esto la página de entradas y salidas no aparece: un equipo sin entradas ni
        salidas digitales no muestra una página que no comanda nada. Ver
        `ui/views/gpio_view.py`.
        """
        old_page = self._pages.pop(PAGE_GPIO, None)
        if old_page is not None:
            if self._stack.currentWidget() is old_page:
                self._navigate(PAGE_MONITOR)
            self._stack.removeWidget(old_page)
            old_page.deleteLater()
        self._sidebar.set_item_visible(PAGE_GPIO, gpio_controller is not None)
        if gpio_controller is None:
            return
        view = GpioView(gpio_controller)
        if gpio_thread is not None:
            gpio_thread.inputs_updated.connect(view.update_inputs)
        self._add_page(PAGE_GPIO, PageView(**self._get_header(PAGE_GPIO), content=view))

    def reload_config(self):
        """
        Recarga las pestañas de configuración desde el config, si ya están construidas.

        Para cuando el `config.yaml` cambió por afuera de la aplicación: sin esto las
        pestañas siguen mostrando lo que leyeron al abrirse y lo escribirían de vuelta.
        """
        if self._config_tabs is not None:
            self._config_tabs.reload()

    def set_service_status(self, service: str, status: str):
        """Estado de un servicio, a la página de inicio y a las pestañas que lo muestran."""
        self._home.set_service_status(service, status)
        self._diagnostics.set_service_status(service, status)

    def set_client_count(self, service: str, count: int):
        self._diagnostics.set_client_count(service, count)

    def set_stream_urls(self, service: str, urls: list):
        """URLs de los streams de un servidor de video, ya armadas por quien las publica."""
        self._diagnostics.set_stream_urls(service, urls)

    def log_event(self, level: str, message: str, module: str = ""):
        """Una línea de log a la pestaña de logs."""
        self._diagnostics.log_event(level, message, module)

    def update_hardware_metrics(self, metrics: dict):
        """Slot para el dict de `SystemMonitor.get_metrics()`."""
        self._diagnostics.update_hardware_metrics(metrics)

    def update_inference_times(self, camera_slot: str, pipeline_slot: str,
                               cycle_time_ms: float, inference_time_ms: float):
        """Los tiempos de un resultado de inferencia, para la página de inicio."""
        self._home.update_inference_times(camera_slot, pipeline_slot, cycle_time_ms,
                                          inference_time_ms)

    def update_heartbeat(self, count: int):
        """El contador del registro `heartbeat`, tal como sale al PLC."""
        self._home.update_heartbeat(count)

    def update_verdicts(self, verdicts: dict):
        """{(cámara, pipeline): {veredicto: 1, 0 o None}}, para la página de inicio."""
        self._home.update_verdicts(verdicts)

    def update_camera_status(self, status: dict, camera_slot: str):
        """Slot con la firma de `CaptureThread.status_updated`: (status, slot)."""
        self._home.update_camera_status(status, camera_slot)
        self._diagnostics.update_camera_status(status, camera_slot)

    def update_modbus_values(self, values: dict):
        """Slot para {dirección: valor} de los registros que se están publicando."""
        self._diagnostics.update_modbus_values(values)

    def update_modbus_stats(self, stats: dict):
        """Los contadores de cada transporte Modbus, de `SharedModbusServer.get_stats()`."""
        self._diagnostics.update_modbus_stats(stats)

    def update_license(self, status: dict):
        """
        Estado de la licencia, tal como sale de `LicenseManager.get_status()`.

        Además de la pestaña, pinta o despinta de rojo la barra de estado. Se guarda el
        último status recibido porque puede llegar antes de que la ventana se muestre —el
        cableado lo publica en el arranque, antes de `show()`—, y el cartel modal necesita
        ese valor recién en el primer `showEvent`.
        """
        self._license_status = status
        self._home.update_license(status)
        self._diagnostics.update_license(status)
        self._update_license_alert(status)

    def show_license_result(self, is_ok: bool, message: str):
        """Resultado de instalar una licencia, para que el operador lo vea donde apretó."""
        self._diagnostics.show_license_result(is_ok, message)

    def set_status_message(self, message: str = ""):
        """Cambia el texto del footer y lo hace destellar para que el cambio se note."""
        self._footer.setText(message or tr("status_ready"))
        self._flash_step = 0
        self._flash_timer.start(_FLASH_INTERVAL_MS)

    def showEvent(self, event):
        """
        El cartel de licencia se muestra acá, y no cuando llega el status, para que
        aparezca sobre una ventana ya visible en vez de flotar solo sobre el escritorio.

        Sólo la primera vez: no hay recheck que vuelva a disparar esto durante la misma
        corrida, y minimizar/restaurar la ventana no cuenta como una nueva apertura.
        """
        super().showEvent(event)
        if self._did_show_license_warning:
            return
        self._did_show_license_warning = True
        if self._license_status.get("should_report_invalid"):
            QTimer.singleShot(0, lambda: self._warn_no_license(self._license_status))

    def apply_theme(self, dark: bool | None = None):
        """
        Aplica el tema a la ventana y lo baja a los widgets que se pintan por código.

        Con `dark=None` lo lee del config, que es lo que hace el arranque y lo que hace
        el guardado de la configuración.
        """
        if dark is None:
            dark = bool(self._config.get("ui.dark_mode", True))
        self._is_dark = dark
        self.setStyleSheet(theme.load_stylesheet(dark))
        self._home.apply_theme(dark)
        self._diagnostics.apply_theme(dark)
        # El ícono dice a qué tema se pasa, como en la web: el sol en el oscuro.
        self._theme_button.setIcon(theme.get_icon(
            "sun" if dark else "moon", theme.get_palette(dark)["hint_text"]))
        self._theme_button.setToolTip(tr("theme_to_light" if dark else "theme_to_dark"))
        self._search_icon.setPixmap(theme.get_icon(
            "search", theme.get_palette(dark)["hint_text"]).pixmap(_SEARCH_ICON_PX,
                                                                   _SEARCH_ICON_PX))
        self._palette.apply_theme(dark)

    # ── Barra lateral ────────────────────────────────────────────────────────

    def _build_sidebar(self) -> Sidebar:
        """
        La barra con la navegación agrupada. Lleva el nombre de la aplicación y, debajo, el
        cliente; sin `project.client` declarado, sólo el nombre.
        """
        groups = tuple(
            (tr(group_key), tuple((page.key, tr(page.title_key), page.icon) for page in pages))
            for group_key, pages in navigation.NAVIGATION)
        self._sidebar = Sidebar(
            groups,
            title=str(self._config.get("system.app_name", "") or ""),
            subtitle=str(self._config.get("project.client", "") or "").strip(),
            footer=tr("sidebar_company"),
        )
        self._sidebar.navigation_requested.connect(self._navigate)
        return self._sidebar

    # ── Barra superior ───────────────────────────────────────────────────────

    def _build_top_bar(self) -> QWidget:
        """
        La miga de pan —aplicación › página— y, a la derecha, el cambio de tema. Es lo único
        de la configuración que se aplica sin reiniciar, así que va acá, a mano desde
        cualquier página, y no en la pestaña Sistema. El idioma no: queda fijo por corrida.
        """
        bar = QWidget()
        bar.setObjectName("appTopBar")
        bar.setAttribute(Qt.WA_StyledBackground, True)
        bar.setFixedHeight(_TOP_BAR_HEIGHT_PX)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(_TOP_BAR_MARGIN_PX, 0, _TOP_BAR_MARGIN_PX, 0)
        layout.setSpacing(_BREADCRUMB_SPACING_PX)

        root = QLabel(str(self._config.get("system.app_name", "") or ""))
        root.setObjectName("breadcrumbRoot")
        layout.addWidget(root)
        separator = QLabel(_BREADCRUMB_SEPARATOR)
        separator.setObjectName("breadcrumbSeparator")
        layout.addWidget(separator)
        self._breadcrumb_current = QLabel()
        self._breadcrumb_current.setObjectName("breadcrumbCurrent")
        layout.addWidget(self._breadcrumb_current)
        layout.addStretch()
        layout.addWidget(self._build_search_button())

        self._theme_button = QPushButton()
        self._theme_button.setObjectName("topBarIconButton")
        self._theme_button.setCursor(Qt.PointingHandCursor)
        self._theme_button.setFixedSize(_TOP_BAR_BUTTON_PX, _TOP_BAR_BUTTON_PX)
        self._theme_button.setIconSize(QSize(_TOP_BAR_ICON_PX, _TOP_BAR_ICON_PX))
        self._theme_button.clicked.connect(self._toggle_theme)
        layout.addWidget(self._theme_button)
        return bar

    def _build_search_button(self) -> QPushButton:
        """El campo de la web: parece un buscador, y abre la paleta. Dice su atajo a la derecha."""
        button = QPushButton()
        button.setObjectName("topBarSearch")
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedSize(_SEARCH_WIDTH_PX, _TOP_BAR_BUTTON_PX)
        button.clicked.connect(self._open_search)
        row = QHBoxLayout(button)
        row.setContentsMargins(10, 0, 8, 0)
        row.setSpacing(8)
        self._search_icon = QLabel()
        self._search_icon.setFixedSize(_SEARCH_ICON_PX, _SEARCH_ICON_PX)
        text = QLabel(tr("search_button"))
        text.setObjectName("topBarSearchText")
        shortcut = QLabel(_SEARCH_SHORTCUT_TEXT)
        shortcut.setObjectName("kbd")
        for label in (self._search_icon, text, shortcut):
            label.setAttribute(Qt.WA_TransparentForMouseEvents)
        row.addWidget(self._search_icon)
        row.addWidget(text)
        row.addStretch()
        row.addWidget(shortcut)
        return button

    def _toggle_theme(self):
        """
        Pasa al otro tema y lo guarda en `ui.dark_mode`, para que el próximo arranque abra
        igual. Se persiste solo esa clave: lo que esté editado y sin guardar en las pestañas
        de configuración vive en sus campos, no en el config, así que no se escribe con esto.
        """
        dark = not self._is_dark
        self.apply_theme(dark)
        self._config.set("ui.dark_mode", dark)
        if not self._config.save():
            self.set_status_message(tr("status_theme_not_saved"))

    def _build_footer(self) -> QWidget:
        """
        Barra de estado: el mensaje a la izquierda, la versión y el reloj a la derecha.

        La versión no cambia nunca en la corrida, así que se arma una vez. El mensaje sí,
        y es el que hace destellar la barra. Sin licencia válida es la barra entera la
        que se pinta de rojo —ver `_update_license_alert()`—, no un widget aparte: en
        28px de alto un chip se pierde y esto no es algo que se pueda pasar por alto.
        """
        self._footer_bar = QWidget()
        self._footer_bar.setObjectName("appFooter")
        self._footer_bar.setAttribute(Qt.WA_StyledBackground, True)
        self._footer_bar.setFixedHeight(_FOOTER_HEIGHT_PX)
        row = QHBoxLayout(self._footer_bar)
        # Alineados con la barra superior: el mensaje bajo la miga de pan.
        row.setContentsMargins(_TOP_BAR_MARGIN_PX, 0, _TOP_BAR_MARGIN_PX, 0)
        row.setSpacing(_FOOTER_SPACING_PX)

        self._footer = QLabel()
        self._footer.setObjectName("footerMessage")
        row.addWidget(self._footer)
        row.addStretch()

        version_label = QLabel(self._build_version_text())
        version_label.setObjectName("footerVersion")
        row.addWidget(version_label)

        self._clock_label = QLabel()
        self._clock_label.setObjectName("footerClock")
        row.addWidget(self._clock_label)
        clock_timer = QTimer(self)
        clock_timer.timeout.connect(self._on_clock_tick)
        clock_timer.start(_CLOCK_INTERVAL_MS)
        self._on_clock_tick()

        self._flash_timer = QTimer(self)
        self._flash_timer.timeout.connect(self._on_flash_tick)
        return self._footer_bar

    def _update_license_alert(self, status: dict):
        """
        Pinta la barra entera de rojo mientras la licencia no valga, con el motivo en
        el tooltip. El mismo criterio que el bit que va al PLC.
        """
        self._license_invalid = bool(status.get("should_report_invalid"))
        self._footer_bar.setToolTip(str(status.get("reason") or "") if self._license_invalid
                                    else "")
        if not self._flash_timer.isActive():
            self._apply_footer_background()

    def _apply_footer_background(self):
        """
        Fondo persistente de la barra: rojo sin licencia válida, si no el del tema.

        El color del texto no va inline: la propiedad `alert` lo deja en manos del QSS, que
        lo pasa a blanco sobre el rojo.
        """
        self._footer_bar.setProperty(_ALERT_PROPERTY, self._license_invalid)
        for widget in (self._footer_bar, *self._footer_bar.findChildren(QLabel)):
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        if self._license_invalid:
            color = theme.get_palette(self._is_dark)["footer_alert"]
            self._footer_bar.setStyleSheet(f"QWidget#appFooter {{ background-color: {color}; }}")
        else:
            self._footer_bar.setStyleSheet("")

    def _warn_no_license(self, status: dict):
        """Cartel modal de una sola vez. El motivo es el mismo que ya lee la pestaña."""
        reason = str(status.get("reason") or "")
        QMessageBox.warning(
            self, tr("lic_startup_warning_title"),
            tr("lic_startup_warning_body").format(reason=reason),
        )

    def _build_version_text(self) -> str:
        """
        Id del proyecto y versión del programa.

        Sin `project.project_id` declarado muestra sólo la versión: un separador colgando
        de la nada se lee como un error de la aplicación.
        """
        project_id = str(self._config.get("project.project_id", "") or "").strip()
        if not project_id:
            return tr("footer_version_only").format(version=APP_VERSION)
        return tr("footer_version").format(project_id=project_id, version=APP_VERSION)

    def _on_flash_tick(self):
        """
        Un paso de la interpolación del color de aviso al fondo de la barra.

        El destino no es siempre el fondo del tema: sin licencia válida, la barra tiene
        que volver al rojo de alerta y no al gris de siempre, o un mensaje nuevo —incluso
        uno trivial, como guardar la configuración— apagaría la alerta durante unos
        segundos. El selector es explícito y no un `background-color` suelto: una hoja
        sin selector se propaga a los hijos, y el mensaje y la versión quedarían con su
        propio recuadro de color en vez de sobre la barra.
        """
        if self._flash_step >= _FLASH_STEPS:
            self._flash_timer.stop()
            self._apply_footer_background()
            return
        palette = theme.get_palette(self._is_dark)
        target_key = "footer_alert" if self._license_invalid else "footer_background"
        progress = self._flash_step / (_FLASH_STEPS - 1)
        color = _blend(QColor(palette["footer_flash"]),
                       QColor(palette[target_key]), progress)
        self._footer_bar.setStyleSheet(
            f"QWidget#appFooter {{ background-color: {color.name()}; }}"
        )
        self._flash_step += 1

    # ── Búsqueda ─────────────────────────────────────────────────────────────

    def _open_search(self):
        """
        Abre la paleta con lo que hay ahora. Se rearma en cada apertura: lo que se ve de cada
        pestaña cambia con lo que se edita —un campo que aparece con otro driver—, y recorrer
        unos cientos de campos no se nota.
        """
        self._ensure_config_tabs()
        pages, tabs, settings = self._build_search_entries()
        actions = [PaletteEntry(
            tr("search_group_actions"),
            tr("theme_to_light" if self._is_dark else "theme_to_dark"),
            icon="sun" if self._is_dark else "moon",
            target=_SearchTarget(action=_ACTION_TOGGLE_THEME))]
        # Con el campo vacío, las páginas y las acciones: las pestañas, que repetirían el
        # nombre de su página, aparecen al escribir.
        self._palette.set_entries(pages + tabs + settings + actions, pages + actions)
        self._palette.open_over(self)

    def _build_search_entries(self) -> tuple[list[PaletteEntry], list[PaletteEntry],
                                             list[PaletteEntry]]:
        """(páginas, pestañas, ajustes), en el orden de la barra lateral."""
        pages, tab_entries, settings = [], [], []
        group_pages = tr("search_group_pages")
        group_settings = tr("search_group_settings")
        for entry in navigation.get_pages():
            if entry.key == PAGE_GPIO and PAGE_GPIO not in self._pages:
                continue
            title = tr(entry.title_key)
            pages.append(PaletteEntry(
                group_pages, title,
                tr(entry.description_key) if entry.description_key else "", entry.icon,
                target=_SearchTarget(entry.key)))
            tabs = [self._get_tab(tab_class) for tab_class in entry.tabs]
            # Una página con una sola pestaña no muestra la barra: su rótulo no es un camino.
            has_tab_bar = len(tabs) > 1
            for tab in tabs:
                tab_title = tr(tab.TITLE_KEY)
                if has_tab_bar:
                    tab_entries.append(PaletteEntry(group_pages, tab_title, title, entry.icon,
                                                    target=_SearchTarget(entry.key, tab)))
                if not isinstance(tab, AbstractConfigTab):
                    continue
                prefix = (title, tab_title) if has_tab_bar else (title,)
                for ref in field_search.collect_settings(tab):
                    settings.append(PaletteEntry(
                        group_settings, ref.title, _PATH_SEPARATOR.join(prefix + ref.path),
                        _SETTING_ICON, keywords=ref.hint,
                        target=_SearchTarget(entry.key, ref.widget, ref.highlight, ref.hit)))
        return pages, tab_entries, settings

    def _get_tab(self, tab_class: type) -> QWidget:
        owner = (self._config_tabs if issubclass(tab_class, AbstractConfigTab)
                 else self._diagnostics)
        return owner.get_tab(tab_class)

    def _on_search_chosen(self, entry: PaletteEntry):
        target: _SearchTarget = entry.target
        if target.action == _ACTION_TOGGLE_THEME:
            self._toggle_theme()
            return
        self._navigate(target.page_key)
        if target.widget is not None:
            field_search.reveal(target.widget, highlight=target.highlight, hit=target.hit)

    # ── Navegación ───────────────────────────────────────────────────────────

    def _navigate(self, key: str):
        """
        Muestra la página, armándola si es la primera vez. Una página que no se puede armar
        —la de entradas y salidas sin controlador— deja la actual como está.
        """
        page = self._pages.get(key) or self._build_page(key)
        if page is None:
            return
        self._stack.setCurrentWidget(page)
        self._sidebar.set_current(key)
        self._breadcrumb_current.setText(self._sidebar.get_label(key))

    def _add_page(self, key: str, page: QWidget):
        self._pages[key] = page
        self._stack.addWidget(page)

    def _build_page(self, key: str) -> QWidget | None:
        """
        Arma una página con pestañas con las que le asigna `ui/navigation.py`.

        Las pestañas ya existen —de configuración en `ConfigTabs`, de diagnóstico en
        `DiagnosticsTabs`—: la página sólo las muestra. Lleva «Guardar y aplicar» si alguna
        edita el config.
        """
        entry = navigation.get_page(key)
        if entry is None or not entry.tabs:
            return None
        has_config = any(issubclass(tab_class, AbstractConfigTab) for tab_class in entry.tabs)
        if has_config:
            self._ensure_config_tabs()
        tabs = [self._get_tab(tab_class) for tab_class in entry.tabs]
        page = PageView(**self._get_header(key), tabs=tabs, can_save=has_config)
        if has_config:
            page.save_requested.connect(lambda: self._config_tabs.save(self))
            page.discard_requested.connect(lambda: self._config_tabs.reload())
            self._config_tabs.pending_changed.connect(page.set_pending)
            page.set_pending(self._config_tabs.get_pending_titles())
        self._add_page(key, page)
        return page

    def _get_header(self, key: str) -> dict:
        """El título, el rótulo —el grupo de la barra lateral— y la descripción de la página."""
        entry = navigation.get_page(key)
        group_key = navigation.get_group_key(key)
        return {
            "title": tr(entry.title_key),
            "eyebrow": tr(group_key) if group_key else "",
            "description": tr(entry.description_key) if entry.description_key else "",
        }

    def _ensure_config_tabs(self):
        """Arma las pestañas de configuración la primera vez que una página las pide."""
        if self._config_tabs is not None:
            return
        self._config_tabs = ConfigTabs(
            self._config, navigation.get_tab_classes(AbstractConfigTab), self)
        self._config_tabs.config_saved.connect(self._on_config_saved)
        self._config_tabs.open_roi_requested.connect(self._on_open_roi_requested)
        self._config_tabs.open_regions_requested.connect(self._on_open_regions_requested)
        self._config_tabs.open_level_requested.connect(self._on_open_level_requested)
        self._config_tabs.open_polygon_requested.connect(self._on_open_polygon_requested)
        self._config_tabs.calibrate_lens_requested.connect(self._on_calibrate_lens_requested)

    # ── Slots ────────────────────────────────────────────────────────────────

    def _on_clock_tick(self):
        self._clock_label.setText(QTime.currentTime().toString("HH:mm:ss"))

    def _on_config_saved(self):
        """
        El tema es lo único que se aplica sin reiniciar.

        El resto —cámaras, servidores, modelos, el idioma— se lee al arrancar y el
        propio panel avisa que hay que reiniciar. Reenvía la señal para que el cableado
        haga lo suyo.

        **El idioma no se cambia en caliente.** Cada widget resuelve su texto con `tr()`
        cuando se construye, así que cambiarlo acá sólo alcanza a lo que se arme después
        —un diálogo, las pestañas si todavía no se abrieron— y deja la pantalla partida en
        dos idiomas. Peor: un combo cuyas opciones quedaron en el idioma anterior ya no
        se puede leer por texto, que es lo que devolvía la etiqueta traducida al
        `config.yaml`.
        """
        self.apply_theme()
        self.set_status_message(tr("status_config_saved"))
        self.config_saved.emit()

    def _on_open_roi_requested(self, camera_slot: str, config_prefix: str,
                               title_key: str):
        """
        Abre el diálogo de ROI alimentado con el frame en vivo de esa cámara.

        **Qué rectángulo se dibuja lo dice quien lo pide.** La pestaña manda bajo qué clave
        guardarlo y cómo titular la ventana, así que esta ventana no conoce ninguna sección
        del config: sirve igual a la región de óptica de una cámara que al rectángulo que un
        proyecto mida aparte, sin enterarse de cuál es cuál.
        """
        from ui.dialogs.roi_dialog import RoiDialog

        dialog = RoiDialog(self._config, camera_slot, self,
                           config_prefix=config_prefix, title_key=title_key)
        self._exec_on_live_frame(dialog, camera_slot)
        if self._config_tabs is not None:
            self._config_tabs.refresh_roi_fields(camera_slot)

    def _on_open_regions_requested(self, camera_slot: str, pipeline_slot: str):
        """
        Abre el diálogo de regiones de esa cámara, con el pipeline pedido elegido.

        El diálogo escribe en el config las regiones que el operador tocó, así que esta
        ventana sólo le pasa el frame en vivo y, al cerrarse, le dice a la pestaña que
        vuelva a leer sus filas.
        """
        from ui.dialogs.pipeline_regions_dialog import PipelineRegionsDialog

        dialog = PipelineRegionsDialog(self._config, camera_slot, self,
                                       initial_pipeline=pipeline_slot)
        self._exec_on_live_frame(dialog, camera_slot)
        if self._config_tabs is not None:
            self._config_tabs.refresh_region_fields()

    def _on_open_level_requested(self, camera_slot: str, config_key: str, title_key: str):
        """
        Abre el diálogo de nivel alimentado con el frame en vivo de esa cámara.

        Igual que el del ROI: dónde se guarda la altura y cómo se titula la ventana lo dice la
        pestaña que la pide, así que esta ventana no conoce qué se mide contra ella. Lo que se
        muestra en pausa también lo arma esa pestaña, si sabe hacerlo.
        """
        from ui.dialogs.level_dialog import LevelDialog

        inspector = None
        if self._config_tabs is not None:
            inspector = self._config_tabs.build_level_inspector(camera_slot,
                                                                self._result_source)
        dialog = LevelDialog(self._config, camera_slot, config_key, self, title_key=title_key,
                             inspector=inspector)
        self._exec_on_live_frame(dialog, camera_slot)
        if self._config_tabs is not None:
            self._config_tabs.refresh_level_fields(camera_slot)

    def _on_open_polygon_requested(self, camera_slot: str, config_key: str, title_key: str):
        """
        Abre el diálogo de zona alimentado con el frame en vivo de esa cámara.

        Igual que el de nivel: dónde se guarda la zona y cómo se titula la ventana lo dice la
        pestaña que la pide, así que esta ventana no conoce qué se mide con ella.
        """
        from ui.dialogs.polygon_dialog import PolygonDialog

        dialog = PolygonDialog(self._config, camera_slot, config_key, self, title_key=title_key)
        self._exec_on_live_frame(dialog, camera_slot)
        if self._config_tabs is not None:
            self._config_tabs.refresh_polygon_fields(camera_slot)

    def _exec_on_live_frame(self, dialog, camera_slot: str):
        """
        Corre un diálogo de dibujo conectado al frame en vivo de la cámara, y lo desconecta.

        El frame sale del panel que la vista de monitor tenga para ese slot: si el widget del
        centro no expone uno, el diálogo se abre igual y se dibuja sobre negro, que es mejor
        que no abrirse.
        """
        panel = self._get_camera_panel(camera_slot)
        if panel is not None:
            panel.frame_updated.connect(dialog.update_frame)
            dialog.update_frame(panel.get_last_frame())
        dialog.exec()
        if panel is not None:
            panel.frame_updated.disconnect(dialog.update_frame)

    def _on_calibrate_lens_requested(self, camera_slot: str):
        """
        Avisa que la calibración arrancó y deja que `main.py` junte las muestras.

        El cartel dice cuánto va a tardar porque son varios minutos —N muestras, una
        por intervalo— y sin eso el botón parece no haber hecho nada.
        """
        samples = int(self._config.get("lens_health.calibration_samples", 30) or 30)
        interval_s = int(self._config.get("lens_health.interval_s", 10) or 10)
        self.lens_calibration_requested.emit(camera_slot)
        QMessageBox.information(
            self, tr("lens_calibrate_started"),
            tr("lens_calibrate_body").format(
                samples=samples,
                camera=str(self._config.get(f"cameras.{camera_slot}.name", "")
                           or camera_slot),
                interval=interval_s),
        )

    def refresh_lens_reference(self, camera_slot: str):
        """La calibración terminó y escribió el config: que la pantalla lo muestre."""
        if self._config_tabs is not None:
            self._config_tabs.refresh_lens_reference(camera_slot)

    # ── Internos ─────────────────────────────────────────────────────────────

    def _get_camera_panel(self, camera_slot: str):
        """
        Panel de esa cámara en el contenido del monitor, o None.

        Lo busca por tipo y no por una referencia guardada porque el área central la
        arma el fork: la ventana no sabe qué widget puso ni cómo lo organizó.
        """
        from ui.widgets.camera_panel import CameraPanel

        for panel in self.monitor_view.findChildren(CameraPanel):
            if panel.camera_slot == camera_slot:
                return panel
        return None

    def _apply_window_icon(self):
        icon_path = self._get_icon_path(_WINDOW_ICON_FILENAME)
        if os.path.isfile(icon_path):
            self.setWindowIcon(QIcon(icon_path))

    def _get_icon_path(self, filename: str) -> str:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            _ICONS_DIRNAME, filename)


def _blend(start: QColor, end: QColor, progress: float) -> QColor:
    """Color intermedio entre dos, con `progress` de 0.0 (start) a 1.0 (end)."""
    return QColor(
        int(start.red() * (1 - progress) + end.red() * progress),
        int(start.green() * (1 - progress) + end.green() * progress),
        int(start.blue() * (1 - progress) + end.blue() * progress),
    )
