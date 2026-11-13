"""
Página de inicio: presenta el equipo en una pantalla y resume si lo que tiene está andando.

    ┌─────────────────────────────────────────────────────────────┬───────────┐
    │ ● EQUIPO EN MARCHA                                           │   ( 3 )   │
    │ Nombre de la aplicación                                      │   de 4    │
    │ Visión artificial en línea: …                                │ Cámaras … │
    │ Equipo      Versión      En servicio                         │           │
    └─────────────────────────────────────────────────────────────┴───────────┘
    ┌ Resultados ───────────────────────────────────────────────────────┐
    │ Pipeline               Cámara      Veredictos                     │
    │ Presencia              Cinta       [Presencia: OK]                │
    │ Nivel de llenado       Báscula     [Nivel: OK] [Tapa: NOK]        │
    └───────────────────────────────────────────────────────────────────┘
    ┌ Cámaras ─────────────────────────────────────────── Ver monitor ↗ ┐
    │ Cámara           Dirección                      Estado       FPS  │
    │ Cinta            rtsp://192.168.1.10:554/cam_1  Conectada    9.8  │
    └───────────────────────────────────────────────────────────────────┘
    ┌ Inferencia ─────────────────────────┐  ┌ Servicios ──────────────────┐
    │ Pipeline   Cámara   Ciclo  Inf.  Último│  │ Modbus TCP               OK │
    │ Nivel de…  Báscula  101 ms 38 ms ahora │  │ …  Licencia · Latido al PLC │
    └─────────────────────────────────────┘  └─────────────────────────────┘

Es la página «Inicio» del sistema de diseño (`Propuesta_UI_IEA_SI280ETH`): una placa con la
identidad del equipo, los veredictos que salen al PLC, lo que el equipo mira y cómo lo está
procesando, y los canales de salida con la licencia y el latido. Es breve a propósito: el
detalle —el uso del hardware incluido— está en la página de cada subsistema, y ésta sólo
dice qué hay y si anda. Sin la IP del equipo: tiene más de una interfaz y la que importa
depende de quién pregunta.

Sólo muestra, como las pestañas de diagnóstico: recibe por sus slots el estado de los
servicios, de las cámaras y de la licencia, el latido, los veredictos y los tiempos de cada
resultado de inferencia, y del config lee sólo lo que no cambia en la corrida: qué
cámaras hay, su dirección y qué pipelines las miran. El texto y el color de cada estado los
decide `ui/service_status.py`, el mismo que usan el panel de cada cámara y las pestañas, así
que el resumen no puede decir algo distinto que el detalle.

**Los tiempos de inferencia se guardan al llegar y se pintan una vez por segundo.** Llega un
resultado por frame y por par (pipeline, cámara) —decenas por segundo—, y repintar la tabla
en cada uno sería trabajo que nadie alcanza a leer. El mismo tick avanza la edad del último
resultado, que es lo que delata un par que dejó de dar resultados: su ciclo queda quieto,
su edad no.

**Un veredicto es una métrica `<algo>_ok` del analyzer** (`analysis.is_verdict_key()`): 1 es
OK y 0 NOK. Llegan una vez por segundo desde el cableado, ya vencidos con el mismo criterio
que el registro del PLC, así que uno sin resultado confiable reciente dice «Sin dato» y no
queda en verde. Su rótulo es `verdict_<nombre>` de `strings.py`, o el nombre si no está. Un
NOK es del proceso y no del equipo: no toca el estado general.

**Las columnas no bailan.** Los FPS y los tiempos van en monoespaciada y con ancho fijo —dos
dígitos los FPS, cuatro los ms—; la píldora de una cámara mide lo que su estado más largo, y
las de resultados miden todas lo mismo, el del texto más largo de cualquiera. Un valor que
cambia no corre la columna de al lado.

El estado general del rótulo es lo más grave de lo que hay abajo: rojo sin licencia válida
—el mismo `should_report_invalid` que pinta la barra de estado—, ámbar si una cámara
habilitada o un servicio están en error, verde si no. Un servicio apagado a propósito y una
cámara que todavía arranca no son un aviso.

La dirección de una cámara RTSP es la URL entera, menos el usuario y la clave si alguien los
escribió en ella: las credenciales salen del entorno y no se muestran.
"""

import os
import time
from urllib.parse import urlsplit, urlunsplit

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal, Slot
from PySide6.QtGui import (
    QColor, QFont, QImage, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSizePolicy, QStyle,
    QStyleOption, QVBoxLayout, QWidget,
)

from system.config_manager import ConfigManager
from system.inference.regions import list_camera_regions
from system.version import APP_VERSION

from ui import service_status, theme
from ui.strings import has_text, tr
from ui.widgets.form import PAGE_CONTENT_WIDTH_PX
from ui.widgets.status_chip import CHIP_ERROR, CHIP_OK, CHIP_WARNING, StatusChip

_EMPTY_VALUE = "—"
_MARGINS_PX = (32, 28, 32, 20)    # los de una página con pestañas: el mismo borde
_SECTION_GAP_PX = 24
_CARD_MARGINS_PX = (18, 16, 18, 16)
_SERVICE_ROW_GAP_PX = 14
_REFRESH_INTERVAL_MS = 1000       # tiempos de inferencia, su edad y el tiempo en servicio

_NAMEPLATE_MARGINS_PX = (32, 28, 28, 28)
_DOT_PX = 6
_APP_TILE_PX = 52             # el ícono de la aplicación al lado del nombre
_APP_TILE_GAP_PX = 16
_EYEBROW_SPACING_PCT = 116.0
_SPEC_COLUMN_GAP_PX = 36
_RING_PX = 104
_RING_STROKE_PX = 5
_RING_TRACK_ALPHA = 22

# Textura de la placa, la de la web: un halo azul desde la esquina de arriba a la
# izquierda, una grilla de plano que se desvanece hacia la izquierda y las órbitas con la
# esfera del isotipo a la derecha (`Orbits` de `brand.tsx`).
_GLOW_RADIUS_PCT = 0.66
_GLOW_ALPHA = 0.65
_GRID_STEP_PX = 24
_GRID_ALPHA = 12
_GRID_WIDTH_PCT = 0.7
_ORBIT_WIDTH_PCT = 0.7            # contra la derecha, como `w-[70%]` en la web
_ORBIT_VIEWBOX = (600.0, 260.0)   # el SVG de la web, que se escala como `slice`, centrado
_ORBIT_OPACITY = 0.9
# (puntos, ancho, [(posición, opacidad)]) de cada órbita, en coordenadas del SVG: el inicio y
# después de a tres los de cada curva. La primera es la del acento; las otras dos, blancas y
# apenas visibles.
_ORBITS = (
    (((-20, 228), (120, 120), (330, 52), (470, 58), (560, 62), (600, 96), (560, 150)),
     2.2, ((0.0, 0.0), (0.55, 0.9), (1.0, 0.15))),
    (((40, 260), (170, 150), (380, 92), (520, 110), (600, 120), (620, 160), (590, 200)),
     1.0, ((0.0, 0.0), (0.6, 0.14), (1.0, 0.0))),
    (((-40, 180), (90, 60), (300, 6), (450, 14)),
     1.0, ((0.0, 0.0), (0.6, 0.14), (1.0, 0.0))),
)
_SPHERE_CENTER = (372.0, 66.0)
_SPHERE_RADIUS = 13.0
_SPHERE_LIGHT = (0.34, 0.3, 0.75)   # centro y radio del brillo, en fracciones de la esfera
_HALO_RADIUS = 26.0
_HALO_ALPHA = 0.12

_URL_DRIVERS = ("rtsp",)          # su dirección es una URL y se muestra entera
_FPS_WIDTH_CHARS = 4              # «99.9»: dos dígitos, el punto y uno
_MS_WIDTH_CHARS = 7               # «9999 ms»: un ciclo a pedido pasa del segundo
_CAMERA_STATE_KEYS = ("camera_starting", "camera_connected", "camera_disconnected",
                      "camera_disabled", "camera_misconfigured")
_VERDICT_STATE_KEYS = ("verdict_ok", "verdict_nok", "verdict_no_data")


def format_camera_address(driver: str, address: object) -> str:
    """
    Cómo se muestra la dirección de una cámara: la URL entera de una RTSP —sin usuario ni
    clave—, el nombre del archivo de un video y la IP o el número de serie de una GigE tal
    cual.
    """
    text = str(address or "").strip()
    if not text:
        return _EMPTY_VALUE
    if "://" in text:
        parts = urlsplit(text)
        if driver in _URL_DRIVERS or parts.hostname:
            # Las credenciales van antes de la última `@` del netloc; lo demás queda igual.
            return urlunsplit(parts._replace(netloc=parts.netloc.rpartition("@")[2]))
        return os.path.basename(parts.path.rstrip("/")) or _EMPTY_VALUE
    if "/" in text or "\\" in text:
        return os.path.basename(text.replace("\\", "/").rstrip("/")) or text
    return text


def format_uptime(seconds: float) -> str:
    """Tiempo en servicio en la unidad que se lee: minutos, horas o días."""
    total_minutes = int(max(0.0, seconds) // 60)
    days, rest = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(rest, 60)
    if days:
        return tr("home_uptime_days").format(days=days, hours=hours)
    if hours:
        return tr("home_uptime_hours").format(hours=hours, minutes=minutes)
    return tr("home_uptime_minutes").format(minutes=minutes)


def format_age(seconds: float) -> str:
    """Hace cuánto llegó el último resultado, a la resolución del refresco: un segundo."""
    whole_seconds = int(max(0.0, seconds))
    if whole_seconds < 1:
        return tr("home_age_now")
    if whole_seconds < 60:
        return tr("home_age_seconds").format(seconds=whole_seconds)
    if whole_seconds < 3600:
        return tr("home_age_minutes").format(minutes=whole_seconds // 60)
    return tr("home_age_hours").format(hours=whole_seconds // 3600)


def format_ms(milliseconds: float) -> str:
    """Un tiempo en ms, sin decimales; 0 es «no hay» —el primer frame no tiene ciclo—."""
    return f"{milliseconds:.0f} ms" if milliseconds > 0 else _EMPTY_VALUE


def get_overall_state(camera_states: list[str], service_states: list[str],
                      is_license_invalid: bool) -> str:
    """El estado del rótulo de la placa: lo más grave de lo que muestra la página."""
    if is_license_invalid:
        return CHIP_ERROR
    if CHIP_ERROR in camera_states or CHIP_ERROR in service_states:
        return CHIP_WARNING
    return CHIP_OK


class HomeView(QWidget):
    """Página de inicio. Ver el contrato en el docstring del módulo."""

    monitor_requested = Signal()      # el operador apretó «Ver monitor»

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(parent)
        self._config = config_manager
        self._started_s = time.monotonic()
        self._service_pills: dict[str, StatusChip] = {}
        self._service_states: dict[str, str] = {}
        self._camera_rows: dict[str, _CameraRow] = {}
        self._timing_rows: dict[tuple[str, str], _TimingRow] = {}
        self._verdict_rows: dict[tuple[str, str], _VerdictRow] = {}
        self._is_license_invalid = False
        self._are_widths_fixed = False

        pipelines = self._config.get("inference.pipelines", {}) or {}
        self._pipeline_count = sum(1 for section in pipelines.values()
                                   if isinstance(section, dict) and section.get("enabled", True))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        page = QWidget()
        scroll.setWidget(page)
        row = QHBoxLayout(page)
        row.setContentsMargins(*_MARGINS_PX)
        column = QWidget()
        column.setMaximumWidth(PAGE_CONTENT_WIDTH_PX)
        column.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        row.addStretch(0)
        row.addWidget(column, 1)
        row.addStretch(0)

        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_SECTION_GAP_PX)
        self._nameplate = _Nameplate(self._config, self._pipeline_count)
        layout.addWidget(self._nameplate)
        cameras = self._config.get("cameras", {}) or {}
        pairs = _list_pairs(pipelines, cameras)
        layout.addWidget(self._build_results_card(pairs, cameras))
        layout.addWidget(self._build_cameras_card(cameras))

        # Las dos del mismo ancho y del mismo alto: la fila las estira a las dos.
        bottom = QHBoxLayout()
        bottom.setSpacing(_SECTION_GAP_PX)
        bottom.addWidget(self._build_inference_card(pairs, cameras), stretch=1)
        bottom.addWidget(self._build_services_card(), stretch=1)
        layout.addLayout(bottom)
        layout.addStretch()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start(_REFRESH_INTERVAL_MS)
        self.refresh()
        self._refresh_overall()
        self.apply_theme(True)

    # ── API pública ──────────────────────────────────────────────────────────

    @property
    def overall_state(self) -> str:
        """El estado del rótulo de la placa, uno de los CHIP_*."""
        return self._nameplate.state

    def set_service_status(self, service: str, status: str):
        """Estado de un canal de salida. Un servicio que no está en la lista se ignora."""
        pill = self._service_pills.get(service)
        if pill is None:
            return
        text, chip_state = service_status.describe_status(status)
        pill.set_state(chip_state, text)
        self._service_states[service] = chip_state
        self._refresh_overall()

    @Slot(object, str)
    def update_camera_status(self, status: dict, camera_slot: str):
        """Slot con la firma de `CaptureThread.status_updated`: (status, slot)."""
        row = self._camera_rows.get(camera_slot)
        if row is None:
            return
        row.update_status(status)
        self._refresh_overall()

    def update_inference_times(self, camera_slot: str, pipeline_slot: str,
                               cycle_time_ms: float, inference_time_ms: float):
        """
        Los tiempos del último resultado de un par, los de `InferenceResult`. Se guardan y se
        pintan en el próximo `refresh()`. Un par que la tabla no tiene se ignora.
        """
        row = self._timing_rows.get((pipeline_slot, camera_slot))
        if row is not None:
            row.store(cycle_time_ms, inference_time_ms, time.monotonic())

    def update_heartbeat(self, count: int):
        """El contador que el equipo publica en el registro `heartbeat` para el PLC."""
        self._heartbeat.setText(str(count))

    def update_verdicts(self, verdicts: dict):
        """
        {(cámara, pipeline): {veredicto: 1, 0 o None}}, como lo arma el cableado una vez por
        segundo; None es «venció». Un par que la tabla no tiene se ignora.
        """
        has_new_pills = False
        for (camera_slot, pipeline_slot), values in verdicts.items():
            row = self._verdict_rows.get((pipeline_slot, camera_slot))
            if row is not None:
                has_new_pills |= row.update(values)
        if has_new_pills and self._are_widths_fixed:
            self._fix_verdict_widths()

    @Slot(object)
    def update_license(self, status: dict):
        """Slot para el dict de `LicenseManager.get_status()`."""
        text, chip_state = service_status.describe_license(status)
        self._license_pill.set_state(chip_state, text)
        self._is_license_invalid = bool(status.get("should_report_invalid"))
        self._refresh_overall()

    def refresh(self):
        """
        Pinta lo que depende del reloj: los tiempos guardados, la edad de cada resultado y el
        tiempo en servicio. Lo llama su timer una vez por segundo.
        """
        now_s = time.monotonic()
        for row in self._timing_rows.values():
            row.paint(now_s)
        self._nameplate.set_uptime(format_uptime(now_s - self._started_s))

    def apply_theme(self, dark: bool):
        """El ícono se pinta al cargarlo: el QSS no lo alcanza."""
        self._monitor_button.setIcon(
            theme.get_icon("arrow-up-right", theme.get_palette(dark)["hint_text"]))

    def showEvent(self, event):
        """
        Fija el ancho de las píldoras la primera vez que se ve: recién ahí tienen la
        tipografía y el relleno del QSS con los que se miden.
        """
        super().showEvent(event)
        if self._are_widths_fixed:
            return
        self._are_widths_fixed = True
        for row in self._camera_rows.values():
            row.fix_widths()
        self._fix_verdict_widths()

    # ── Armado ───────────────────────────────────────────────────────────────

    def _build_cameras_card(self, cameras: dict) -> QWidget:
        """Todas las cámaras en una tarjeta: una fila por cámara."""
        card, layout = _new_card_with_layout()
        header = QHBoxLayout()
        header.addWidget(_new_label(tr("home_cameras"), "homeCardTitle"))
        header.addStretch()
        self._monitor_button = QPushButton(tr("home_open_monitor"))
        self._monitor_button.setObjectName("ghostButton")
        self._monitor_button.setCursor(Qt.PointingHandCursor)
        # El ícono va después del texto, como la flecha de un enlace.
        self._monitor_button.setLayoutDirection(Qt.RightToLeft)
        self._monitor_button.clicked.connect(self.monitor_requested)
        header.addWidget(self._monitor_button)
        layout.addLayout(header)

        if not cameras:
            layout.addWidget(_new_label(tr("camera_no_cameras"), "hintLabel"))
            return card
        table = _Table((tr("home_camera"), tr("home_camera_address"),
                        tr("home_camera_state"), tr("home_camera_fps")),
                       stretches=(2, 4, 0, 0))
        for camera_slot, camera in cameras.items():
            row = _CameraRow(camera_slot, camera or {})
            self._camera_rows[camera_slot] = row
            table.add_row(row.cells)
        layout.addLayout(table)
        return card

    def _build_results_card(self, pairs: list, cameras: dict) -> QWidget:
        """Los veredictos de cada par, con el mismo orden y agrupado que la inferencia."""
        card, layout = _new_card_with_layout()
        layout.addWidget(_new_label(tr("home_results"), "homeCardTitle"))
        if not pairs:
            layout.addWidget(_new_label(tr("home_no_pipelines"), "hintLabel"))
            return card
        table = _Table((tr("home_inference_pipeline"), tr("home_camera"),
                        tr("home_results_verdicts")),
                       stretches=(2, 2, 5))
        previous_slot = ""
        for name, pipeline_slot, camera_slot in pairs:
            camera_name = str((cameras.get(camera_slot) or {}).get("name") or camera_slot)
            row = _VerdictRow(name if pipeline_slot != previous_slot else "", camera_name)
            self._verdict_rows[(pipeline_slot, camera_slot)] = row
            table.add_row(row.cells, divider=pipeline_slot != previous_slot)
            previous_slot = pipeline_slot
        layout.addLayout(table)
        return card

    def _build_inference_card(self, pairs: list, cameras: dict) -> QWidget:
        """
        Una fila por par (pipeline, cámara) habilitado, agrupadas por pipeline en el orden
        del config. El nombre del pipeline va sólo en la primera fila de su grupo.
        """
        card, layout = _new_card_with_layout()
        layout.addWidget(_new_label(tr("home_inference"), "homeCardTitle"))
        if not pairs:
            layout.addWidget(_new_label(tr("home_no_pipelines"), "hintLabel"))
            return card
        table = _Table((tr("home_inference_pipeline"), tr("home_camera"),
                        tr("home_inference_cycle"), tr("home_inference_time"),
                        tr("home_inference_age")),
                       stretches=(3, 2, 0, 0, 0))
        previous_slot = ""
        for name, pipeline_slot, camera_slot in pairs:
            camera_name = str((cameras.get(camera_slot) or {}).get("name") or camera_slot)
            row = _TimingRow(name if pipeline_slot != previous_slot else "", camera_name)
            self._timing_rows[(pipeline_slot, camera_slot)] = row
            table.add_row(row.cells, divider=pipeline_slot != previous_slot)
            previous_slot = pipeline_slot
        layout.addLayout(table)
        layout.addStretch()
        return card

    def _build_services_card(self) -> QWidget:
        """Los canales de salida, la licencia y el latido."""
        services, services_layout = _new_card_with_layout()
        # Filas sin filete entre ellas: un poco más de aire las separa a la vista.
        services_layout.setSpacing(_SERVICE_ROW_GAP_PX)
        services_layout.addWidget(_new_label(tr("home_services"), "homeCardTitle"))
        # Los seis canales, en el orden en que los declara `com_status`: el mismo en el que
        # el PLC los lee.
        for service in service_status.SERVICES:
            pill = StatusChip(pill=True)
            text, chip_state = service_status.describe_status("")
            pill.set_state(chip_state, text)
            self._service_pills[service] = pill
            services_layout.addLayout(_new_row(service_status.get_label(service), pill))
        services_layout.addWidget(_new_divider(horizontal=True))
        self._license_pill = StatusChip(pill=True)
        text, chip_state = service_status.describe_license({})
        self._license_pill.set_state(chip_state, text)
        services_layout.addLayout(_new_row(tr("home_license"), self._license_pill))
        # El contador que el PLC vigila: si deja de moverse acá, tampoco se mueve allá.
        self._heartbeat = _new_label(_EMPTY_VALUE, "homeFieldValue")
        services_layout.addLayout(_new_row(tr("home_heartbeat"), self._heartbeat))
        services_layout.addStretch()
        return services

    # ── Internos ─────────────────────────────────────────────────────────────

    def _fix_verdict_widths(self):
        """
        Todas las píldoras de resultados del mismo ancho, el del texto más largo que puede
        llevar cualquiera: la tabla se lee como una grilla y un OK que pasa a NOK no se mueve.
        Se rehace cuando aparece un veredicto nuevo, que puede traer un rótulo más largo.
        """
        samples = [sample for row in self._verdict_rows.values() for sample in row.get_samples()]
        width = max((pill.measure(texts) for pill, texts in samples), default=0)
        for pill, _ in samples:
            pill.fix_width([], width)

    def _refresh_overall(self):
        rows = [row for row in self._camera_rows.values() if row.is_enabled]
        self._nameplate.set_cameras_online(sum(row.state == CHIP_OK for row in rows), len(rows))
        self._nameplate.set_state(get_overall_state([row.state for row in rows],
                                                    list(self._service_states.values()),
                                                    self._is_license_invalid))


class _Nameplate(QWidget):
    """
    La placa del equipo: estado general, nombre, una línea de qué hace, los datos de
    identidad y el anillo de cámaras en línea. Oscura en los dos temas, con la textura de la
    web dibujada encima del fondo del QSS.

    Si el fork declara el ícono de la aplicación (`ui/icons/app/`, ver `ui/theme.py`), va al
    lado del nombre.
    """

    def __init__(self, config: ConfigManager, pipeline_count: int, parent=None):
        super().__init__(parent)
        self.setObjectName("homeNameplate")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.state = CHIP_OK
        self._grid_cache: QPixmap | None = None
        palette = theme.get_palette(True)
        self._glow_color = QColor(palette["nameplate_glow"])
        self._grid_color = QColor(palette["nameplate_grid"])
        self._orbit_color = QColor(palette["nameplate_orbit"])
        self._sphere_highlight = QColor(palette["nameplate_sphere_highlight"])
        self._sphere_color = QColor(palette["nameplate_sphere"])

        row = QHBoxLayout(self)
        row.setContentsMargins(*_NAMEPLATE_MARGINS_PX)
        row.setSpacing(24)
        identity = QVBoxLayout()
        identity.setSpacing(0)
        row.addLayout(identity, stretch=1)

        eyebrow_row = QHBoxLayout()
        eyebrow_row.setSpacing(8)
        self._dot = QWidget()
        self._dot.setObjectName("homeLiveDot")
        self._dot.setAttribute(Qt.WA_StyledBackground, True)
        self._dot.setFixedSize(_DOT_PX, _DOT_PX)
        eyebrow_row.addWidget(self._dot, alignment=Qt.AlignVCenter)
        self._eyebrow = _new_label("", "homeNameplateEyebrow")
        font = self._eyebrow.font()
        font.setLetterSpacing(QFont.PercentageSpacing, _EYEBROW_SPACING_PCT)
        self._eyebrow.setFont(font)
        eyebrow_row.addWidget(self._eyebrow)
        eyebrow_row.addStretch()
        identity.addLayout(eyebrow_row)
        identity.addSpacing(12)

        title_row = QHBoxLayout()
        title_row.setSpacing(_APP_TILE_GAP_PX)
        app_icon = theme.render_app_icon(_APP_TILE_PX, self.devicePixelRatioF())
        if app_icon is not None:
            tile = QLabel()
            tile.setObjectName("homeAppIcon")
            tile.setPixmap(app_icon)
            tile.setFixedSize(_APP_TILE_PX, _APP_TILE_PX)
            title_row.addWidget(tile, alignment=Qt.AlignVCenter)
        title_row.addWidget(_new_label(str(config.get("system.app_name", "") or ""),
                                       "homeNameplateTitle"), alignment=Qt.AlignVCenter)
        title_row.addStretch()
        identity.addLayout(title_row)
        identity.addSpacing(10)
        # La línea de la instalación, si la declara; si no, la genérica del template.
        tagline = _new_label(str(config.get("system.app_description", "") or "").strip()
                             or tr("home_tagline"), "homeNameplateTagline")
        tagline.setWordWrap(True)
        identity.addWidget(tagline)
        identity.addSpacing(24)

        specs = QGridLayout()
        specs.setHorizontalSpacing(_SPEC_COLUMN_GAP_PX)
        specs.setVerticalSpacing(4)
        self._uptime = _new_label(_EMPTY_VALUE, "homeSpecValue")
        values = (
            ("home_spec_device", _new_label(str(config.get("system.device_id", "") or "")
                                            or _EMPTY_VALUE, "homeSpecValue")),
            ("home_spec_version", _new_label(APP_VERSION, "homeSpecValue")),
            ("home_spec_uptime", self._uptime),
        )
        for column, (label_key, value) in enumerate(values):
            specs.addWidget(_new_label(tr(label_key), "homeSpecLabel"), 0, column)
            specs.addWidget(value, 1, column)
        specs.setColumnStretch(len(values), 1)
        identity.addLayout(specs)

        ring_box = QWidget()
        ring_box.setObjectName("homeRingBox")
        ring_box.setAttribute(Qt.WA_StyledBackground, True)
        ring_layout = QVBoxLayout(ring_box)
        ring_layout.setContentsMargins(24, 18, 24, 18)
        ring_layout.setSpacing(4)
        self._ring = _CameraRing(palette)
        ring_layout.addWidget(self._ring, alignment=Qt.AlignHCenter)
        ring_layout.addSpacing(6)
        ring_layout.addWidget(_new_label(tr("home_ring_caption"), "homeRingCaption"),
                              alignment=Qt.AlignHCenter)
        ring_layout.addWidget(
            _new_label(tr("home_ring_detail").format(count=pipeline_count), "homeRingDetail"),
            alignment=Qt.AlignHCenter)
        row.addWidget(ring_box, alignment=Qt.AlignVCenter)

    def set_state(self, state: str):
        self.state = state
        self._eyebrow.setText(tr(f"home_state_{state}").upper())
        self._dot.setProperty("state", state)
        self._dot.style().unpolish(self._dot)
        self._dot.style().polish(self._dot)

    def set_cameras_online(self, online: int, total: int):
        self._ring.set_value(online, total)

    def set_uptime(self, text: str):
        self._uptime.setText(text)

    def paintEvent(self, event):
        painter = QPainter(self)
        option = QStyleOption()
        option.initFrom(self)
        self.style().drawPrimitive(QStyle.PE_Widget, option, painter, self)
        painter.setRenderHint(QPainter.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()), 12, 12)
        painter.setClipPath(clip)
        self._paint_glow(painter)
        grid = self._get_grid()
        painter.drawPixmap(self.width() - grid.width(), 0, grid)
        self._paint_orbit(painter)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._grid_cache = None

    def _paint_glow(self, painter: QPainter):
        gradient = QRadialGradient(QPointF(0, 0), self.width() * _GLOW_RADIUS_PCT)
        inner = QColor(self._glow_color)
        inner.setAlphaF(_GLOW_ALPHA)
        outer = QColor(self._glow_color)
        outer.setAlphaF(0.0)
        gradient.setColorAt(0.0, inner)
        gradient.setColorAt(1.0, outer)
        painter.fillRect(self.rect(), gradient)

    def _get_grid(self) -> QPixmap:
        """La grilla de la mitad derecha, desvanecida hacia la izquierda. Una por tamaño."""
        if self._grid_cache is not None:
            return self._grid_cache
        layer = QImage(max(1, int(self.width() * _GRID_WIDTH_PCT)), max(1, self.height()),
                       QImage.Format_ARGB32_Premultiplied)
        layer.fill(Qt.transparent)
        painter = QPainter(layer)
        line = QColor(self._grid_color)
        line.setAlpha(_GRID_ALPHA)
        # Desde el borde derecho, para que la grilla calce con el de la placa.
        for x in range(layer.width() - 1, -1, -_GRID_STEP_PX):
            painter.fillRect(x, 0, 1, layer.height(), line)
        for y in range(0, layer.height(), _GRID_STEP_PX):
            painter.fillRect(0, y, layer.width(), 1, line)
        fade = QLinearGradient(0, 0, layer.width(), 0)
        fade.setColorAt(0.0, QColor(0, 0, 0, 0))
        fade.setColorAt(1.0, QColor(0, 0, 0, 255))
        painter.setCompositionMode(QPainter.CompositionMode_DestinationIn)
        painter.fillRect(layer.rect(), fade)
        painter.end()
        self._grid_cache = QPixmap.fromImage(layer)
        return self._grid_cache

    def _paint_orbit(self, painter: QPainter):
        """
        Las órbitas y la esfera de `Orbits` de la web: el mismo SVG en el 70 % derecho de la
        placa, escalado para cubrirlo y centrado. Los degradés van en coordenadas del SVG,
        como allá, así que se estiran con él.
        """
        view_width, view_height = _ORBIT_VIEWBOX
        area = QRectF(self.width() * (1 - _ORBIT_WIDTH_PCT), 0,
                      self.width() * _ORBIT_WIDTH_PCT, self.height())
        scale = max(area.width() / view_width, area.height() / view_height)
        painter.save()
        painter.setClipRect(area, Qt.IntersectClip)
        painter.setOpacity(_ORBIT_OPACITY)
        painter.translate(area.x() + (area.width() - view_width * scale) / 2,
                          (area.height() - view_height * scale) / 2)
        painter.scale(scale, scale)
        painter.setBrush(Qt.NoBrush)
        for index, (points, width, stops) in enumerate(_ORBITS):
            path = QPainterPath(QPointF(*points[0]))
            for start in range(1, len(points), 3):
                path.cubicTo(*(QPointF(*point) for point in points[start:start + 3]))
            bounds = path.boundingRect()
            gradient = QLinearGradient(bounds.left(), 0, bounds.right(), 0)
            base = self._orbit_color if index == 0 else self._grid_color
            for position, alpha in stops:
                color = QColor(base)
                color.setAlphaF(alpha)
                gradient.setColorAt(position, color)
            painter.setPen(QPen(gradient, width))
            painter.drawPath(path)

        center = QPointF(*_SPHERE_CENTER)
        painter.setPen(Qt.NoPen)
        halo = QColor(self._orbit_color)
        halo.setAlphaF(_HALO_ALPHA)
        painter.setBrush(halo)
        painter.drawEllipse(center, _HALO_RADIUS, _HALO_RADIUS)
        # El brillo arriba a la izquierda: el degradé radial de la web, con su centro y su
        # radio relativos a la caja de la esfera.
        light_x, light_y, light_radius = _SPHERE_LIGHT
        diameter = 2 * _SPHERE_RADIUS
        light = QPointF(center.x() - _SPHERE_RADIUS + light_x * diameter,
                        center.y() - _SPHERE_RADIUS + light_y * diameter)
        shading = QRadialGradient(light, light_radius * diameter)
        shading.setColorAt(0.0, self._sphere_highlight)
        shading.setColorAt(0.2, self._sphere_color)
        shading.setColorAt(0.55, self._orbit_color)
        shading.setColorAt(1.0, self._glow_color)
        painter.setBrush(shading)
        painter.drawEllipse(center, _SPHERE_RADIUS, _SPHERE_RADIUS)
        painter.restore()


class _CameraRing(QWidget):
    """Anillo de cámaras en línea: el arco es la fracción, y el número va en el centro."""

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self.setFixedSize(_RING_PX, _RING_PX)
        self._online = 0
        self._total = 0
        self._arc_color = QColor(palette["nameplate_orbit"])
        self._text_color = QColor(palette["nameplate_text"])

    def set_value(self, online: int, total: int):
        self._online, self._total = online, total
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        inset = _RING_STROKE_PX / 2 + 1
        rect = QRectF(self.rect()).adjusted(inset, inset, -inset, -inset)
        track = QColor(self._text_color)
        track.setAlpha(_RING_TRACK_ALPHA)
        painter.setPen(QPen(track, _RING_STROKE_PX))
        painter.drawEllipse(rect)
        if self._total and self._online:
            pen = QPen(self._arc_color, _RING_STROKE_PX)
            pen.setCapStyle(Qt.RoundCap)
            painter.setPen(pen)
            # En dieciseisavos de grado, desde las doce y en sentido horario.
            painter.drawArc(rect, 90 * 16, -round(360 * 16 * self._online / self._total))

        painter.setPen(self._text_color)
        value_font = QFont(theme.FONT_HEADING)
        value_font.setPointSizeF(20)
        value_font.setWeight(QFont.DemiBold)
        painter.setFont(value_font)
        painter.drawText(QRectF(0, 0, self.width(), self.height() * 0.58),
                         Qt.AlignHCenter | Qt.AlignBottom, str(self._online))
        faint = QColor(self._text_color)
        faint.setAlphaF(0.45)
        painter.setPen(faint)
        detail_font = QFont(theme.FONT_MONO)
        detail_font.setPointSizeF(8.5)
        painter.setFont(detail_font)
        painter.drawText(QRectF(0, self.height() * 0.6, self.width(), self.height() * 0.2),
                         Qt.AlignHCenter | Qt.AlignTop,
                         tr("home_ring_total").format(total=self._total))


class _Table(QGridLayout):
    """
    Una tabla liviana dentro de una tarjeta: una fila de rótulos y filas de celdas, con un
    filete entre filas. `stretches` reparte el ancho que sobra: 0 es «lo que pida».
    """

    def __init__(self, headers: tuple[str, ...], *, stretches: tuple[int, ...]):
        super().__init__()
        self.setHorizontalSpacing(20)
        self.setVerticalSpacing(11)
        self._columns = len(headers)
        for column, (text, stretch) in enumerate(zip(headers, stretches, strict=True)):
            self.addWidget(_new_label(text, "homeFieldLabel"), 0, column)
            self.setColumnStretch(column, stretch)
        self._next_row = 1

    def add_row(self, cells: tuple[QWidget, ...], *, divider: bool = True):
        if divider:
            self.addWidget(_new_divider(horizontal=True), self._next_row, 0, 1, self._columns)
            self._next_row += 1
        for column, cell in enumerate(cells):
            self.addWidget(cell, self._next_row, column, Qt.AlignLeft | Qt.AlignVCenter)
        self._next_row += 1


class _CameraRow:
    """
    Una cámara en la tabla: nombre con slot y driver, dirección, estado y FPS. Una cámara
    apagada en el config queda como «Deshabilitada» y no cuenta en el anillo ni en el estado
    general: está así porque alguien lo pidió.
    """

    def __init__(self, camera_slot: str, camera: dict):
        self.is_enabled = bool(camera.get("enabled", True))
        self.state = CHIP_WARNING

        names = QWidget()
        names_layout = QVBoxLayout(names)
        names_layout.setContentsMargins(0, 0, 0, 0)
        names_layout.setSpacing(1)
        names_layout.addWidget(_new_label(str(camera.get("name") or camera_slot),
                                          "homeRowLabel"))
        driver = str(camera.get("driver") or "").strip()
        names_layout.addWidget(_new_label(f"{camera_slot} · {driver}" if driver else camera_slot,
                                          "homeCardDetail"))

        self.address = _new_label(format_camera_address(driver, camera.get("address")),
                                  "homeFieldValue")
        self.address.setToolTip(self.address.text())
        self.address.setTextInteractionFlags(Qt.TextSelectableByMouse)
        # Una URL larga no empuja la tabla más allá de la tarjeta: se corta, y entera está
        # en el tooltip.
        self.address.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.address.setMinimumWidth(1)

        self._pill = StatusChip(pill=True)
        self._pill.set_state(CHIP_WARNING, tr("camera_starting" if self.is_enabled
                                              else "camera_disabled"))
        self._fps = _new_fixed_label(_EMPTY_VALUE.rjust(_FPS_WIDTH_CHARS))
        self.cells = (names, self.address, self._pill, self._fps)

    @property
    def pill_text(self) -> str:
        return self._pill.text()

    @property
    def fps_text(self) -> str:
        return self._fps.text().strip()

    def fix_widths(self):
        self._pill.fix_width([tr(key) for key in _CAMERA_STATE_KEYS])

    def update_status(self, status: dict):
        """Claves de `AbstractCameraDriver.get_status()`."""
        text, self.state = service_status.describe_camera(status)
        self._pill.set_state(self.state, text)
        self._pill.setToolTip(str(status.get("error") or ""))
        self._fps.setText(f"{status.get('fps_estimated', 0.0):.1f}".rjust(_FPS_WIDTH_CHARS))


class _TimingRow:
    """Un par (pipeline, cámara): los tiempos guardados del último resultado y su edad."""

    def __init__(self, pipeline_name: str, camera_name: str):
        self._cycle_ms = 0.0
        self._inference_ms = 0.0
        self._received_s: float | None = None
        self._cycle = _new_fixed_label(_EMPTY_VALUE.rjust(_MS_WIDTH_CHARS))
        self._inference = _new_fixed_label(_EMPTY_VALUE.rjust(_MS_WIDTH_CHARS))
        self._age = _new_label(tr("home_no_result"), "homeRowDetail")
        self.cells = (_new_label(pipeline_name, "homeRowLabel"),
                      _new_label(camera_name, "homeRowLabel"),
                      self._cycle, self._inference, self._age)

    @property
    def texts(self) -> tuple[str, str, str]:
        """(ciclo, inferencia, edad) tal como se ven, sin el relleno."""
        return self._cycle.text().strip(), self._inference.text().strip(), self._age.text()

    def store(self, cycle_time_ms: float, inference_time_ms: float, received_s: float):
        self._cycle_ms = cycle_time_ms
        self._inference_ms = inference_time_ms
        self._received_s = received_s

    def paint(self, now_s: float):
        if self._received_s is None:
            return
        self._cycle.setText(format_ms(self._cycle_ms).rjust(_MS_WIDTH_CHARS))
        self._inference.setText(format_ms(self._inference_ms).rjust(_MS_WIDTH_CHARS))
        self._age.setText(format_age(now_s - self._received_s))


class _VerdictRow:
    """
    Un par (pipeline, cámara) con una píldora por veredicto. Las píldoras se arman con el
    primer resultado: qué veredictos da un pipeline lo dice su analyzer, no el config.
    """

    def __init__(self, pipeline_name: str, camera_name: str):
        self._pills: dict[str, StatusChip] = {}
        verdicts = QWidget()
        self._layout = QHBoxLayout(verdicts)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)
        self._waiting = _new_label(tr("home_results_waiting"), "homeRowDetail")
        self._layout.addWidget(self._waiting)
        self._layout.addStretch()
        self.cells = (_new_label(pipeline_name, "homeRowLabel"),
                      _new_label(camera_name, "homeRowLabel"), verdicts)

    @property
    def pill_texts(self) -> list[str]:
        return [pill.text() for pill in self._pills.values()]

    def update(self, values: dict) -> bool:
        """Pinta cada veredicto. Devuelve si apareció uno nuevo, que pide medir los anchos."""
        has_new_pills = False
        for name, value in values.items():
            pill = self._pills.get(name)
            if pill is None:
                pill = StatusChip(pill=True)
                self._pills[name] = pill
                self._layout.insertWidget(self._layout.count() - 1, pill)
                self._waiting.hide()
                has_new_pills = True
            if value is None:
                pill.set_state(CHIP_WARNING, _verdict_text(name, "verdict_no_data"))
            elif value == 1:
                pill.set_state(CHIP_OK, _verdict_text(name, "verdict_ok"))
            else:
                pill.set_state(CHIP_ERROR, _verdict_text(name, "verdict_nok"))
        return has_new_pills

    def get_samples(self) -> list[tuple[StatusChip, list[str]]]:
        """Cada píldora con los textos que puede llevar: OK, NOK y sin dato."""
        return [(pill, [_verdict_text(name, key) for key in _VERDICT_STATE_KEYS])
                for name, pill in self._pills.items()]


# ── Piezas sueltas ──────────────────────────────────────────────────────────────

def _list_pairs(pipelines: dict, cameras: dict) -> list[tuple[str, str, str]]:
    """(nombre, pipeline, cámara) de cada par habilitado, agrupados por pipeline en su orden."""
    regions = sorted(((region.index, region.name, region.pipeline_slot, camera_slot)
                      for camera_slot in cameras
                      for region in list_camera_regions(pipelines, camera_slot)),
                     key=lambda pair: pair[0])
    return [(name, pipeline_slot, camera_slot) for _, name, pipeline_slot, camera_slot in regions]


def _verdict_text(name: str, state_key: str) -> str:
    """«Nivel: OK». El rótulo es `verdict_<nombre>` o, sin texto en la tabla, el nombre."""
    label = tr(f"verdict_{name}") if has_text(f"verdict_{name}") else name
    return f"{label}: {tr(state_key)}"


def _new_fixed_label(text: str) -> QLabel:
    """
    Un número de ancho fijo: monoespaciada y rellenado a la izquierda por quien lo escribe,
    así que pasar de 10 a 9.8 no corre la columna de al lado. Texto plano, porque con texto
    enriquecido Qt se come los espacios del relleno.
    """
    label = _new_label(text, "homeFieldValue")
    label.setTextFormat(Qt.PlainText)
    return label


def _new_label(text: str, object_name: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    return label


def _new_card() -> QWidget:
    card = QWidget()
    card.setObjectName("homeCard")
    card.setAttribute(Qt.WA_StyledBackground, True)
    return card


def _new_card_with_layout() -> tuple[QWidget, QVBoxLayout]:
    card = _new_card()
    layout = QVBoxLayout(card)
    layout.setContentsMargins(*_CARD_MARGINS_PX)
    layout.setSpacing(10)
    return card, layout


def _new_divider(*, horizontal: bool = False) -> QWidget:
    divider = QWidget()
    divider.setObjectName("homeDivider")
    divider.setAttribute(Qt.WA_StyledBackground, True)
    if horizontal:
        divider.setFixedHeight(1)
    else:
        divider.setFixedWidth(1)
    return divider


def _new_row(text: str, value: QWidget) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(_new_label(text, "homeRowLabel"))
    row.addStretch()
    row.addWidget(value)
    return row
