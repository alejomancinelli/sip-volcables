"""
Identidad visual de la interfaz — único dueño de los colores, las tipografías y los íconos.

Los valores salen del sistema de diseño de IEA (`Propuesta_UI_IEA_SI280ETH`, «Sistema de
diseño»), que a su vez toma la paleta de «Componentes Visuales IEA»: donde las dos
difieren, manda la web. El acento de la app es el celeste IEA.

Dos caminos, y la línea entre ellos es si Qt puede pintar el widget solo:

  - **Widgets estándar** (botones, tablas, inputs, pestañas): los pinta la hoja
    `ui/styles/app.qss`, que es **una sola plantilla para los dos temas**. Sus colores
    son tokens (`$card`, `$border`) y `load_stylesheet()` los reemplaza con los del tema
    pedido. Ningún widget se pinta con `setStyleSheet()` propio.
  - **Widgets que dibujan** (`QPainter`, series de QtCharts, items de tabla): el QSS
    no los alcanza, así que piden los colores acá con `get_palette()` y los aplican
    en su `set_dark()`.

Las claves de los tokens y de la paleta son estables: un widget nuevo usa las que ya
están o suma una, y los dos temas la definen. Una regla del QSS existe una sola vez, así
que un tema no puede quedar sin el estilo que tiene el otro.

Las tipografías viajan con la app (`ui/fonts/`) y se registran con `load_fonts()`: Exo
para títulos y valores medidos, Ubuntu para la interfaz y Ubuntu Mono para los datos
técnicos. Los íconos son de Tabler, los mismos de la guía visual, y se colorean al
cargarlos con `get_icon()`.

**El ícono de la aplicación es del fork y es opcional**, en `ui/icons/app/`: `icon.svg` —y
`icon_16.svg`, el dibujo simplificado para 16 y 24 px— es el ícono de la ventana y el que va
al lado del nombre en la placa de inicio. Lo leen `get_app_icon()` y `render_app_icon()`;
sin él la ventana lleva el ícono de IEA y la placa sólo el nombre. El `icon.ico` de la misma
carpeta es el del ejecutable, que declara `build/build.py`.

Los colores de las detecciones NO están acá: los tiene `system/inference/overlay.py`,
que es el que dibuja el resultado, y el frame anotado llega a la UI ya pintado.
"""

import os
from string import Template

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from system.logger import logger

_UI_DIR = os.path.dirname(os.path.abspath(__file__))
_STYLESHEET_PATH = os.path.join(_UI_DIR, "styles", "app.qss")
_FONTS_DIR = os.path.join(_UI_DIR, "fonts")
_ICONS_DIR = os.path.join(_UI_DIR, "icons", "tabler")
# La flecha de los combos y la tilde del checkbox, que el QSS pide por `url()`. Qt
# resuelve esa ruta contra el directorio de trabajo, así que viaja absoluta, y con `/`
# también en Windows.
_QSS_ICONS_DIR = os.path.join(_UI_DIR, "icons", "qss").replace("\\", "/")
_ICON_RENDER_PX = 64          # se rasteriza grande y Qt lo achica: nítido en cualquier tamaño
_APP_ICONS_DIR = os.path.join(_UI_DIR, "icons", "app")
_APP_ICON_FILENAME = "icon.svg"
_APP_ICON_SMALL_FILENAME = "icon_16.svg"
_APP_ICON_SIZES_PX = (16, 24, 32, 48, 64, 128, 256)
_APP_ICON_SMALL_MAX_PX = 24   # hasta acá, el dibujo simplificado: la órbita no se distingue

FONT_UI = "Ubuntu"
FONT_HEADING = "Exo"
FONT_MONO = "Ubuntu Mono"

# ── Tokens de la hoja de estilos ─────────────────────────────────────────────
# Cada clave es un rol, no un color: `$card` es el fondo de una tarjeta en cualquier
# widget. Los valores son texto de QSS, así que un token puede ser `rgba(...)`; los que
# se usan además fuera del QSS (íconos, paleta) van en hex.

_BRAND = "#28a7e0"            # celeste IEA: el acento de la app

# Iguales en los dos temas: la barra lateral es oscura siempre, y el acento no cambia.
# Los blancos translúcidos de la barra son los de la web: 3 % la tarjeta, 4 % el ítem
# bajo el mouse, 7 % el de la vista activa.
_TOKENS_COMMON = {
    "font_ui":                   FONT_UI,
    "font_heading":              FONT_HEADING,
    "font_mono":                 FONT_MONO,
    "qss_icons_dir":             _QSS_ICONS_DIR,
    "brand":                     _BRAND,
    "sidebar_foreground":        "#b4c4d8",
    "sidebar_muted":             "#5c6470",
    "sidebar_card":              "rgba(255,255,255,8)",
    "sidebar_hover":             "rgba(255,255,255,10)",
    "sidebar_active":            "rgba(255,255,255,18)",
    "sidebar_active_foreground": "#ffffff",
    "sidebar_divider":           "rgba(255,255,255,26)",
    # La barra de cambios sin guardar es oscura en los dos temas, como la de la web.
    "save_bar_border":           "rgba(255,255,255,26)",
    "save_bar_foreground":       "#ffffff",
    "save_bar_muted":            "rgba(255,255,255,180)",
    "save_bar_hover":            "rgba(255,255,255,26)",
    "save_bar_action_foreground": "#081322",
    "save_bar_action_hover":     "#48b4e5",
    # La placa de la página de inicio es «noche IEA» en los dos temas, como la barra lateral:
    # sus textos y su estado van sobre fondo oscuro siempre.
    "nameplate_background":      "#081322",
    "nameplate_foreground":      "#ffffff",
    "nameplate_muted":           "rgba(255,255,255,166)",
    "nameplate_faint":           "rgba(255,255,255,110)",
    # Opaca: el QSS no tiene el `backdrop-blur` de la web, y translúcida la órbita se veía
    # pasar por detrás del anillo. Es el 4 % de blanco de la web sobre «noche IEA».
    "nameplate_box":             "#111c2b",
    "nameplate_border":          "rgba(255,255,255,26)",
    "nameplate_ok":              "#2fbf84",
    "nameplate_warn":            "#f0a531",
    "nameplate_crit":            "#f0575c",
}

_TOKENS_LIGHT = {
    **_TOKENS_COMMON,
    "background":            "#f3f6f9",
    "foreground":            "#0a1a2c",
    "card":                  "#ffffff",
    "popover":               "#ffffff",
    "primary":               "#0e6ab0",
    "primary_hover":         "#0c5d9b",
    "primary_pressed":       "#0b5186",
    "primary_foreground":    "#ffffff",
    "secondary":             "#e9eff5",
    "secondary_hover":       "#dfe7ef",
    "secondary_foreground":  "#0a1a2c",
    "muted":                 "#edf2f6",
    "muted_foreground":      "#5a6b7e",
    "accent":                "#e8f0f7",
    "border":                "#dbe3eb",
    "input":                 "#cfd9e3",
    "brand_soft":            "rgba(40,167,224,36)",
    "ok":                    "#17905f",
    "ok_soft":               "rgba(23,144,95,33)",
    "crit":                  "#d93a3f",
    "crit_soft":             "rgba(217,58,63,33)",
    "warn":                  "#c97a06",
    "warn_soft":             "rgba(201,122,6,38)",
    "sidebar":               "#081322",
    "sidebar_border":        "rgba(255,255,255,18)",
    "save_bar_background":   "#081322",
    "scrollbar_handle":      "#cfd9e3",
    "footer_alert_foreground": "#ffffff",
}

_TOKENS_DARK = {
    **_TOKENS_COMMON,
    "background":            "#060e1a",
    "foreground":            "#e4edf6",
    "card":                  "#0b1727",
    "popover":               "#0e1b2d",
    "primary":               _BRAND,
    "primary_hover":         "#48b4e5",
    "primary_pressed":       "#228ebe",
    "primary_foreground":    "#04121f",
    "secondary":             "#13243a",
    "secondary_hover":       "#1a2e48",
    "secondary_foreground":  "#e4edf6",
    "muted":                 "#102035",
    "muted_foreground":      "#8597ae",
    "accent":                "#13253c",
    "border":                "rgba(150,180,215,28)",
    "input":                 "rgba(150,180,215,43)",
    "brand_soft":            "rgba(40,167,224,36)",
    "ok":                    "#2fbf84",
    "ok_soft":               "rgba(47,191,132,33)",
    "crit":                  "#f0575c",
    "crit_soft":             "rgba(240,87,92,33)",
    "warn":                  "#f0a531",
    "warn_soft":             "rgba(240,165,49,38)",
    "sidebar":               "#040a14",
    "sidebar_border":        "rgba(255,255,255,15)",
    "save_bar_background":   "#0e1b2d",
    "scrollbar_handle":      "#223145",
    "footer_alert_foreground": "#ffffff",
}

# ── Paleta de lo que se dibuja ───────────────────────────────────────────────
# Hex opaco y no `rgba`: lo lee `QColor`, y un borde translúcido de la web se resuelve
# acá contra el fondo de la tarjeta sobre la que cae. La opacidad de un trazo es de quien
# lo dibuja; acá va el color.

# La barra lateral, igual en los dos temas: el halo, la grilla de su textura y sus íconos.
_PALETTE_SIDEBAR = {
    "sidebar_glow":        _BRAND,
    "sidebar_grid":        "#ffffff",
    "sidebar_icon":        "#7f8794",
    "sidebar_icon_active": _BRAND,
    # La placa de la página de inicio: el halo, la grilla, las órbitas, la esfera del isotipo
    # —brillo, medio y borde, los tres pasos de su degradé en la web— y el anillo de cámaras.
    # Igual en los dos temas, como la barra.
    "nameplate_glow":      "#01366c",
    "nameplate_grid":      "#ffffff",
    "nameplate_orbit":     _BRAND,
    "nameplate_sphere_highlight": "#eafbff",
    "nameplate_sphere":    "#a4def5",
    "nameplate_text":      "#ffffff",
}

_PALETTE_DARK = {
    **_PALETTE_SIDEBAR,
    "panel_background":   "#0b1727",
    "chart_background":   "#0b1727",
    "chart_title":        "#e4edf6",
    "chart_grid":         "#1a283a",
    "chart_axis":         "#223145",
    "chart_labels":       "#8597ae",
    "video_background":   "#000000",
    "video_placeholder":  "#b4c4d8",
    "hint_text":          "#8597ae",
    "sparkline":          _BRAND,
    "roi_outline":        "#1e78ff",
    "polygon_outline":    "#ff8c1a",
    "footer_background":  "#0b1727",
    "footer_flash":       "#154968",
    "footer_alert":       "#d93a3f",
    "save_bar_shadow":    "#000000",
}

# Mismas claves que la oscura: si falta una, el widget que la pide se queda sin color y
# no hay forma de verlo sin abrir el archivo.
_PALETTE_LIGHT = {
    **_PALETTE_SIDEBAR,
    "panel_background":   "#ffffff",
    "chart_background":   "#ffffff",
    "chart_title":        "#0a1a2c",
    "chart_grid":         "#dbe3eb",
    "chart_axis":         "#cfd9e3",
    "chart_labels":       "#5a6b7e",
    "video_background":   "#000000",
    "video_placeholder":  "#b4c4d8",
    "hint_text":          "#5a6b7e",
    "sparkline":          _BRAND,
    "roi_outline":        "#1e78ff",
    "polygon_outline":    "#ff8c1a",
    "footer_background":  "#ffffff",
    "footer_flash":       "#d2f3ff",
    "footer_alert":       "#d93a3f",
    "save_bar_shadow":    "#081322",
}

# Color de la serie de cada gráfica de hardware, por rol. No cambian con el tema:
# son la identidad de la serie, como el color de una clase en el overlay.
SERIES_COLORS = {
    "cpu":      "#0d6efd",
    "gpu":      "#198754",
    "ram":      "#ffc107",
    "cpu_temp": "#dc3545",
    "gpu_temp": "#fd7e14",
    "power":    "#6f42c1",
}

# (texto, fondo) por nivel de log. Los items de una QTableWidget se pintan por
# código, así que el QSS no llega. El fondo de INFO es el de la tarjeta, y los de aviso
# y error son el ámbar y el rojo de estado a baja opacidad sobre ella. DEBUG va apagado:
# es detalle, no un aviso. Las claves son las de `log_table.LEVELS`.
_LOG_COLORS_DARK = {
    "DEBUG":   ("#8597ae", "#0b1727"),
    "INFO":    ("#e4edf6", "#0b1727"),
    "WARNING": ("#f0a531", "#2d2c28"),
    "ERROR":   ("#f0575c", "#291f2e"),
}
_LOG_COLORS_LIGHT = {
    "DEBUG":   ("#5a6b7e", "#ffffff"),
    "INFO":    ("#0a1a2c", "#ffffff"),
    "WARNING": ("#a16205", "#f7ebda"),
    "ERROR":   ("#b83136", "#fae5e6"),
}

_are_fonts_loaded = False


def get_tokens(dark: bool) -> dict:
    """Tokens de la hoja de estilos del tema, tal como se reemplazan en el QSS."""
    return dict(_TOKENS_DARK if dark else _TOKENS_LIGHT)


def get_palette(dark: bool) -> dict:
    """Colores del tema como strings hex, listos para `QColor(...)`."""
    return dict(_PALETTE_DARK if dark else _PALETTE_LIGHT)


def get_color(role: str, dark: bool) -> QColor:
    """`QColor` de un rol de la paleta. Un rol que no existe sale magenta y se avisa."""
    palette = _PALETTE_DARK if dark else _PALETTE_LIGHT
    hex_color = palette.get(role)
    if hex_color is None:
        logger.warning(f"[UI] Color '{role}' no está en la paleta del tema.")
        return QColor("#ff00ff")
    return QColor(hex_color)


def get_series_color(role: str) -> QColor:
    """`QColor` de la serie de una gráfica. Igual en los dos temas."""
    return QColor(SERIES_COLORS.get(role, "#0d6efd"))


def get_log_level_colors(dark: bool) -> dict[str, tuple[QColor, QColor]]:
    """(texto, fondo) por nivel de log, ya como `QColor`."""
    source = _LOG_COLORS_DARK if dark else _LOG_COLORS_LIGHT
    return {level: (QColor(fg), QColor(bg)) for level, (fg, bg) in source.items()}


def render_stylesheet(template: str, dark: bool) -> str:
    """
    La plantilla del QSS con los tokens del tema reemplazados.

    La plantilla ve también los roles de la paleta (`$video_background`): un color que
    pinta un widget estándar y uno que se dibuja por código salen del mismo valor.

    Un token que la plantilla nombra y el tema no define se deja escrito tal cual y se
    avisa: Qt descarta esa regla y el widget queda con el estilo por defecto, que se ve,
    en vez de perder la hoja entera.
    """
    tokens = {**get_palette(dark), **get_tokens(dark)}
    try:
        return Template(template).substitute(tokens)
    except (KeyError, ValueError) as error:
        logger.error(f"[UI] La hoja de estilos nombra un token que el tema no define: {error}")
        return Template(template).safe_substitute(tokens)


def load_stylesheet(dark: bool) -> str:
    """
    Hoja de estilos del tema. Devuelve '' si no se puede leer, y lo avisa.

    Encoding explícito: el .qss lleva acentos en los comentarios y el default en
    Windows es cp1252, que los decodifica mal.
    """
    try:
        with open(_STYLESHEET_PATH, encoding="utf-8") as stylesheet_file:
            template = stylesheet_file.read()
    except OSError as error:
        logger.error(f"[UI] No se pudo leer la hoja de estilos {_STYLESHEET_PATH}: {error}")
        return ""
    return render_stylesheet(template, dark)


def load_fonts():
    """
    Registra las tipografías de `ui/fonts/` en la aplicación. Se llama una vez, con el
    `QApplication` ya creado, antes de aplicar la hoja de estilos.

    Viajan con la app porque un equipo de planta no tiene Exo instalada y puede no tener
    internet. Una que no carga se avisa y Qt cae a la tipografía del sistema: la interfaz
    se ve distinta, pero abre.
    """
    global _are_fonts_loaded
    if _are_fonts_loaded:
        return
    _are_fonts_loaded = True
    try:
        filenames = sorted(name for name in os.listdir(_FONTS_DIR) if name.endswith(".ttf"))
    except OSError as error:
        logger.warning(f"[UI] No se encontraron las tipografías en {_FONTS_DIR}: {error}")
        return
    for filename in filenames:
        if QFontDatabase.addApplicationFont(os.path.join(_FONTS_DIR, filename)) < 0:
            logger.warning(f"[UI] No se pudo cargar la tipografía {filename}.")


def get_icon(name: str, color: str, checked_color: str = "") -> QIcon:
    """
    Ícono de Tabler (`ui/icons/tabler/<name>.svg`) pintado de `color`.

    Con `checked_color`, el estado «checked» de un botón lleva ese otro color: es lo que
    distingue la vista activa en la navegación sin que el botón cambie de ícono a mano.
    Un ícono que no existe devuelve un `QIcon` vacío y se avisa.
    """
    path = os.path.join(_ICONS_DIR, f"{name}.svg")
    try:
        with open(path, encoding="utf-8") as svg_file:
            svg = svg_file.read()
    except OSError as error:
        logger.warning(f"[UI] No se encontró el ícono '{name}': {error}")
        return QIcon()
    icon = QIcon()
    icon.addPixmap(_render_svg(svg, color), QIcon.Normal, QIcon.Off)
    if checked_color:
        icon.addPixmap(_render_svg(svg, checked_color), QIcon.Normal, QIcon.On)
    return icon


def get_app_icon() -> QIcon:
    """
    El ícono de la aplicación en todos sus tamaños, o un `QIcon` vacío si el fork no lo
    declara. Se rasteriza acá y no se le pasa el SVG a Qt: así no depende del plugin de
    íconos SVG, que el ejecutable compilado puede no llevar.
    """
    icon = QIcon()
    for size_px in _APP_ICON_SIZES_PX:
        pixmap = render_app_icon(size_px)
        if pixmap is not None:
            icon.addPixmap(pixmap)
    return icon


def render_app_icon(size_px: int, device_pixel_ratio: float = 1.0) -> QPixmap | None:
    """El ícono de la aplicación a ese tamaño en pantalla, o None si el fork no lo declara."""
    small = os.path.join(_APP_ICONS_DIR, _APP_ICON_SMALL_FILENAME)
    path = (small if size_px <= _APP_ICON_SMALL_MAX_PX and os.path.isfile(small)
            else os.path.join(_APP_ICONS_DIR, _APP_ICON_FILENAME))
    if not os.path.isfile(path):
        return None
    renderer = QSvgRenderer(path)
    if not renderer.isValid():
        logger.warning(f"[UI] El ícono de la aplicación no se pudo leer: {path}")
        return None
    pixels = max(1, round(size_px * device_pixel_ratio))
    pixmap = QPixmap(pixels, pixels)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    renderer.render(painter, QRectF(0, 0, pixels, pixels))
    painter.end()
    pixmap.setDevicePixelRatio(device_pixel_ratio)
    return pixmap


def _render_svg(svg: str, color: str) -> QPixmap:
    # Los íconos de Tabler trazan con `currentColor`, que QtSvg pinta de negro.
    renderer = QSvgRenderer(QByteArray(svg.replace("currentColor", color).encode("utf-8")))
    pixmap = QPixmap(_ICON_RENDER_PX, _ICON_RENDER_PX)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter, QRectF(0, 0, _ICON_RENDER_PX, _ICON_RENDER_PX))
    painter.end()
    return pixmap
