"""
Dibujado del resultado sobre el frame: cajas, máscaras, etiquetas, panel de información
y timestamp.

Módulo puro: sin Qt, sin ConfigManager y sin hilos. Recibe un resultado y devuelve una copia
anotada del frame. Lo único que lee de disco son las tipografías de la app, una vez.

Maquinaria genérica: reutilizable entre proyectos. Todo resultado de inferencia tiene un
frame anotado —es lo que mira el operador en la UI, lo que sale por el stream
`annotated` y lo que guarda el dataset—, y sale de acá una sola vez: el mismo array
viaja a los tres destinos en vez de que cada uno vuelva a dibujar lo mismo.

Por eso también lo puede importar `ui/`: es nivel 1 y no sabe que existe una GUI. Un
panel que quiera redibujar un resultado guardado llama a `annotate()` y obtiene
exactamente lo que vio el operador.

Acá va sólo lo que sirve en cualquier fork: lo que sale de la inferencia —cajas,
máscaras, etiquetas, el panel, el ROI, el timestamp— y las primitivas con las que se
dibuja cualquier referencia (`draw_line`, `draw_text_panel`, `draw_status_panel`). Los
dibujos propios de una instalación —un límite de carga, una zona de rechazo— se arman con
esas primitivas en `annotations.py` y entran por el `annotator` del motor, no acá: este
archivo se copia al próximo proyecto tal cual.

**La forma es la de la interfaz.** El texto va en Ubuntu —la tipografía de la app, de
`ui/fonts/`— con antialiasing; los paneles y las etiquetas son tarjetas redondeadas del azul
noche del tema, translúcidas, y un veredicto es una píldora de su color sobre su tono
suave, como en la página de inicio. Los colores de estado son los del tema (`ok`, `crit`,
`warn`), así que un NOK en el video y uno en la pantalla son el mismo rojo.

Tres restricciones que no se ven en las firmas:
  - **El texto se dibuja con Pillow.** Sin Pillow, o sin las tipografías, se cae a la
    fuente Hershey de OpenCV, que no tiene acentos ni eñes: el texto se pasa a ASCII
    —«Verificación» sale «Verificacion»— y no con signos de pregunta. La forma de las
    tarjetas no cambia.
  - **`annotate()` copia el frame** y los `draw_*` dibujan in-place sobre lo que reciben.
    El frame de la cámara no se toca nunca: lo comparten la captura, el stream crudo y
    el dataset.
  - **El tamaño del texto sigue siendo la escala de OpenCV** (`font_scale`), la del config:
    a escala 1 la Ubuntu mide lo que medía la Hershey, así que un `font_scale` calibrado
    para una cámara se sigue leyendo igual.
"""

import datetime
import functools
import os
import unicodedata
from dataclasses import dataclass, field
from typing import NamedTuple

import cv2
import numpy as np

from system import paths

from .result import (ROI_STATE_NOK, ROI_STATE_OK, ROI_STATE_WARNING, Detection,
                     InferenceResult)

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:             # sin Pillow, la fuente de OpenCV: ver el docstring
    ImageFont = None

# Paleta por índice de clase, en BGR. Se recorre en círculo, así que sirve para
# cualquier cantidad de clases; el config la puede reemplazar entera.
DEFAULT_CLASS_COLORS_BGR = (
    (246, 130, 59),    # azul      #3b82f6
    (22, 115, 249),    # naranja   #f97316
    (94, 197, 34),     # verde     #22c55e
    (8, 179, 234),     # amarillo  #eab308
    (247, 85, 168),    # violeta   #a855f7
    (68, 68, 239),     # rojo      #ef4444
)

# Un color por pipeline para su ROI, en BGR. **Sin verde, rojo ni amarillo**: son los
# colores del estado —un ROI que se pinta por su veredicto— y un pipeline que los usara de
# identidad se confundiría con un resultado. Con más pipelines que colores se repiten, y
# la segunda vuelta va punteada. El primero es el celeste de la marca.
PIPELINE_COLORS_BGR = (
    (224, 167, 40),    # celeste   #28a7e0
    (238, 211, 34),    # cian      #22d3ee
    (239, 70, 217),    # magenta   #d946ef
    (246, 92, 139),    # violeta   #8b5cf6
    (245, 245, 245),   # blanco
)

# Con qué se pinta el borde del ROI según el veredicto del pipeline (`roi_state`). Son los
# `ok`, `crit` y `warn` del tema oscuro de la UI, y los tres colores que la paleta de
# pipelines no usa, para que un estado nunca se confunda con una identidad. El texto va en
# la etiqueta además del color: no se puede perder la alarma en un stream en blanco y negro
# ni para un operador daltónico.
ROI_STATE_COLORS_BGR = {
    ROI_STATE_OK: (132, 191, 47),       # verde     #2fbf84
    ROI_STATE_NOK: (92, 87, 240),       # rojo      #f0575c
    ROI_STATE_WARNING: (49, 165, 240),  # ámbar     #f0a531
}
_ROI_STATE_TEXT = {ROI_STATE_OK: "OK", ROI_STATE_NOK: "NOK", ROI_STATE_WARNING: "WARN"}

# La superficie de los paneles y las etiquetas: la tarjeta del tema oscuro, translúcida. No
# es opaca del todo para que el frame se adivine detrás, y lo es lo suficiente para que el
# texto se lea sobre un frame claro, que es para lo que existe el fondo.
SURFACE_BGR = (39, 23, 11)            # #0b1727
SURFACE_OPACITY = 0.82
TEXT_BGR = (246, 237, 228)            # #e4edf6, el texto del tema oscuro
MUTED_BGR = (174, 151, 133)           # #8597ae, el texto apagado
_DARK_TEXT_BGR = (31, 18, 6)          # #06121f: texto sobre una etiqueta de color claro
_PILL_OPACITY = 0.22                  # el tono suave de una píldora: su color sobre la tarjeta
_INVALID_ROI_BGR = MUTED_BGR          # un resultado que no es una medición no se pinta de estado
_ROI_BGR = (180, 180, 180)
_PANEL_TEXT_BGR = TEXT_BGR

# Cómo se dibuja la máscara de una detección. Con pocas instancias el relleno se lee
# mejor; con cientos —granos, semillas, partículas— se convierte en una mancha y el
# contorno es lo único que deja ver dónde termina cada una.
MASK_STYLE_FILL = "fill"          # sólo el relleno translúcido
MASK_STYLE_OUTLINE = "outline"    # sólo el contorno
MASK_STYLE_BOTH = "both"          # contorno nítido y relleno translúcido
MASK_STYLES = (MASK_STYLE_FILL, MASK_STYLE_OUTLINE, MASK_STYLE_BOTH)

_MASK_ALPHA = 0.45        # peso del color de la máscara sobre el frame
_MARGIN_PX = 10           # aire entre el panel y el borde del frame
_INNER_PAD_PX = 8         # mínimo del relleno de un panel; crece con el texto
_LINE_PAD_PX = 6          # aire mínimo entre dos líneas del panel
_LINE_PAD_RATIO = 0.4     # ídem, en fracción del alto del texto: crece con la escala
_PANEL_PAD_RATIO = 0.6    # relleno del panel, en fracción del alto de las mayúsculas
_TAG_PAD_RATIO = 0.45     # relleno vertical de una etiqueta, ídem
_RADIUS_RATIO = 0.45      # radio de las esquinas, ídem
_COLUMN_GAP_RATIO = 1.6   # aire entre la etiqueta y el valor, ídem
_TIMESTAMP_RATIO = 0.8    # el timestamp va más chico que el panel: es referencia, no dato
_LABEL_SCALE = 0.55       # etiqueta de una referencia dibujada con draw_line()
_ROI_THICKNESS_PX = 2     # mínimo del ROI; crece con el trazo de las cajas
_DASH_LENGTH_PX = 10      # largo de un trazo y de su hueco en un ROI punteado
_CORNER_RATIO = 0.08      # largo de las escuadras del ROI, en fracción de su lado menor
_CORNER_MAX_PX = 40

# Grosor automático de las líneas (`thickness: 0`): una fracción del lado menor del frame,
# con topes. Da 4 px en 1080, así que una cámara reducida por el decodificador se ve en
# pantalla con casi el mismo trazo que una nativa. El mínimo es 2 y no 1: en 360 la cuenta da
# 1 px, que se pierde en una cámara chica mirada en la grilla. El máximo evita que en 4K las
# cajas tapen lo que encierran.
_AUTO_THICKNESS_FRACTION = 0.004
_AUTO_THICKNESS_MIN_PX = 2
_AUTO_THICKNESS_MAX_PX = 6

# Escala automática (`font_scale: 0`). El texto se calcula para que la línea más larga
# ocupe esta fracción del ancho del frame, con topes para que no quede ilegible en un
# recorte chico ni gigante en un frame completo.
_AUTO_TEXT_WIDTH_FRACTION = 0.95
_AUTO_FONT_MIN = 0.25
_AUTO_FONT_MAX = 1.00
# Escala hasta la que el trazo del texto es de un píxel. Es la escala por defecto, así
# que el texto de siempre se dibuja igual y sólo engorda cuando el texto crece.
_TEXT_THICKNESS_STEP = 0.5
# Línea nominal con la que la etiqueta de una detección calcula su escala automática. Se
# mide contra un largo fijo y no contra su propio texto: si no, «N6» se dibujaría del
# ancho del frame y «N12 87 %» mucho más chico, y son la misma etiqueta.
_AUTO_LABEL_LINE = "M" * 44

# Tipografías. A escala 1 de OpenCV las mayúsculas de la Hershey miden 22 px; la Ubuntu da
# esas mismas mayúsculas con un cuerpo de 32 px, así que `font_scale` sigue diciendo lo mismo.
_FONTS_DIR = os.path.join(paths.APP_DIR, "ui", "fonts")
_FONT_FILES = {
    "regular": "Ubuntu-R.ttf",
    "medium": "Ubuntu-M.ttf",
    "bold": "Ubuntu-B.ttf",
    "mono": "UbuntuMono-R.ttf",
}
_EM_PX_PER_SCALE = 32
_MIN_FONT_PX = 7
_FONT = cv2.FONT_HERSHEY_SIMPLEX      # la de respaldo


class PipelineStyle(NamedTuple):
    """Cómo se dibuja el ROI de un pipeline: su color y si va punteado."""

    color_bgr: tuple[int, int, int]
    is_dashed: bool


@dataclass
class OverlayOptions:
    """
    Qué se dibuja y con qué tamaño. El motor la arma desde `inference.overlay`.

    `class_colors_bgr` vacío usa DEFAULT_CLASS_COLORS_BGR.

    `mask_style` elige entre relleno translúcido, contorno o los dos; `mask_alpha` es
    cuánto tiñe el relleno, y con cientos de instancias conviene bajarlo.

    `roi_color_bgr`, `roi_is_dashed` y `roi_label` dicen cómo se dibuja el ROI de **este**
    pipeline: su color, si va punteado y el nombre que lleva en el borde. Los arma el motor
    desde el lugar del pipeline en el config; con los valores por defecto el ROI sale gris
    y sin nombre, que es lo que corresponde a quien dibuja un resultado suelto.
    `draw_roi_label` en False deja el recuadro sin su etiqueta —ni el nombre, ni el veredicto
    escrito, ni el motivo de un resultado inválido—: el estado queda dicho sólo por el color
    y el trazo, y eso es decisión de quien lo apaga.

    `font_scale` en 0 calcula el tamaño del texto a partir del ancho del frame. Es lo que
    hace falta cuando el lienzo cambia de tamaño —un recorte de 130 px y un frame de
    1920—: una escala fija que se lee bien en uno tapa al otro o desaparece.

    `thickness` en 0 hace lo mismo con el trazo de las cajas y del ROI: ver
    `get_line_thickness()`. Un valor positivo son píxeles fijos en cualquier frame.
    """

    draw_boxes: bool = True
    draw_masks: bool = True
    mask_style: str = MASK_STYLE_FILL
    mask_alpha: float = _MASK_ALPHA
    draw_labels: bool = True
    draw_roi: bool = True
    draw_roi_label: bool = True
    draw_timestamp: bool = True
    font_scale: float = 0.6   # 0 = automática, según el ancho del frame
    thickness: int = 0        # 0 = automático, según el lado menor del frame
    class_colors_bgr: tuple = field(default_factory=tuple)
    roi_color_bgr: tuple = _ROI_BGR
    roi_is_dashed: bool = False
    roi_label: str = ""


def get_class_color_bgr(class_index: int, colors_bgr: tuple = ()) -> tuple[int, int, int]:
    """Color de una clase por índice, recorriendo la paleta en círculo."""
    palette = tuple(colors_bgr) or DEFAULT_CLASS_COLORS_BGR
    color = palette[class_index % len(palette)]
    return (int(color[0]), int(color[1]), int(color[2]))


def get_pipeline_style(index: int) -> PipelineStyle:
    """
    Color y trazo del ROI del pipeline que está en esa posición de `inference.pipelines`.

    Recorre `PIPELINE_COLORS_BGR` en círculo y la segunda vuelta va punteada, así que dos
    pipelines nunca se ven iguales aunque haya más que colores.
    """
    laps, color_index = divmod(max(0, int(index)), len(PIPELINE_COLORS_BGR))
    return PipelineStyle(PIPELINE_COLORS_BGR[color_index], laps % 2 == 1)


def get_line_thickness(frame_bgr: np.ndarray, options: OverlayOptions | None = None) -> int:
    """
    Trazo de las cajas en este frame: el de `options.thickness`, o el automático si es 0.

    El automático es una fracción del lado menor del frame entre `_AUTO_THICKNESS_MIN_PX` y
    `_AUTO_THICKNESS_MAX_PX`, y es lo que también usan los annotators del proyecto para sus
    referencias: así una caja y una línea dibujadas sobre el mismo frame tienen el mismo trazo.
    """
    opts = options or OverlayOptions()
    if opts.thickness > 0:
        return int(opts.thickness)
    auto_px = int(round(min(frame_bgr.shape[:2]) * _AUTO_THICKNESS_FRACTION))
    return max(_AUTO_THICKNESS_MIN_PX, min(_AUTO_THICKNESS_MAX_PX, auto_px))


def annotate(result: InferenceResult, options: OverlayOptions | None = None, *,
             canvas_adjust=None) -> np.ndarray | None:
    """
    Devuelve una copia anotada de `result.source_bgr`, o None si no hay frame.

    Dibuja, en este orden: el recuadro del ROI con el nombre del pipeline, las máscaras, las
    cajas con su etiqueta, el panel de información y el timestamp. Un frame en escala de grises se convierte a
    BGR para que el color de cada clase se distinga.

    `canvas_adjust` es `(frame) -> frame` y se aplica al lienzo **antes** de dibujar. Es
    para que el anotado se vea como el stream crudo, que sale ajustado para una persona: un
    frame medido con poca luz es igual de ilegible con máscaras encima. Va antes y no
    después porque después le correría el color a las máscaras, a las referencias y al
    texto, que son justamente lo que el ajuste no tiene que tocar. Lo que se mide no cambia:
    `source_bgr` queda como estaba.
    """
    frame = result.source_bgr
    if frame is None or frame.size == 0:
        return None
    opts = options or OverlayOptions()

    annotated = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR) if frame.ndim == 2 else frame.copy()
    if canvas_adjust is not None:
        annotated = canvas_adjust(annotated)

    draw_result_roi(annotated, result, opts)
    draw_detections(annotated, result.detections, options=opts)
    if opts.draw_timestamp:
        draw_timestamp(annotated, result.timestamp_s, options=opts)
    return annotated


def draw_detections(frame_bgr: np.ndarray, detections: list[Detection], *,
                    options: OverlayOptions | None = None):
    """
    Dibuja in-place las máscaras, las cajas y las etiquetas, con el color de cada clase.

    Las etiquetas van al final, encima de todas las cajas: con detecciones encimadas, la caja
    de una no tacha el texto de otra.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return
    opts = options or OverlayOptions()
    height_px, width_px = frame_bgr.shape[:2]
    thickness = get_line_thickness(frame_bgr, opts)

    # Relleno por clase, acumulado y teñido una sola vez al final: ver `_MaskFill`.
    fill = _MaskFill((height_px, width_px))
    labels = []

    for detection in detections:
        color = get_class_color_bgr(detection.class_index, opts.class_colors_bgr)
        x1, y1, x2, y2 = (int(value) for value in detection.bbox_px)
        # Una detección que no toca el frame no se dibuja: acotarla contra el borde
        # dejaría una marca en la esquina que no corresponde a nada.
        if x2 <= 0 or y2 <= 0 or x1 >= width_px or y1 >= height_px:
            continue
        x1 = max(0, min(width_px - 1, x1))
        y1 = max(0, min(height_px - 1, y1))
        x2 = max(0, min(width_px, x2))
        y2 = max(0, min(height_px, y2))
        if x2 <= x1 or y2 <= y1:
            continue

        if opts.draw_masks and detection.mask is not None:
            if opts.mask_style in (MASK_STYLE_FILL, MASK_STYLE_BOTH):
                fill.add(color, detection.mask, (x1, y1, x2, y2))
            if opts.mask_style in (MASK_STYLE_OUTLINE, MASK_STYLE_BOTH):
                _outline_mask(frame_bgr, detection.mask, (x1, y1, x2, y2), color)
        if opts.draw_boxes:
            cv2.rectangle(frame_bgr, (x1, y1), (x2, y2), color, thickness)
        if opts.draw_labels:
            labels.append((f"{detection.class_name} {detection.confidence_pct:.0f}%",
                           (x1, y1), color))

    fill.blend_into(frame_bgr, opts.mask_alpha)
    for text, origin_px, color in labels:
        _draw_label(frame_bgr, text, origin_px, color, opts)


def draw_result_roi(frame_bgr: np.ndarray, result: InferenceResult, options: OverlayOptions):
    """
    Dibuja in-place el ROI de un resultado con el estado que le corresponde.

    Es lo que dibujan el anotado propio de cada pipeline y el que se superpone al de otro
    (`draw_onto`): los dos tienen que pintar igual. No dibuja con `draw_roi` apagado ni
    sobre un resultado sin ROI —el frame entero no lleva recuadro—.
    """
    if not options.draw_roi or result.roi_px is None:
        return
    draw_roi(frame_bgr, result.roi_px, options=options, state=result.roi_state,
             invalid_reason="" if result.is_valid else (result.invalid_reason or "invalid"))


def draw_roi(frame_bgr: np.ndarray, roi_px: tuple[int, int, int, int], *,
             options: OverlayOptions | None = None, state: str = "",
             invalid_reason: str = ""):
    """
    Marca in-place el recorte que se analizó, para que se vea qué quedó afuera.

    El color, el trazo y el nombre salen de las opciones: cada pipeline dibuja el suyo y
    en una cámara con varios se distinguen. El trazo es la mitad del de las cajas, con
    `_ROI_THICKNESS_PX` de mínimo, y las cuatro esquinas llevan una escuadra del doble: crece
    con el frame igual que ellas. Encima va el estado:
      - `state` (un `ROI_STATE_*`) pinta el borde del color del veredicto y lo escribe en
        una píldora de la etiqueta, con el mismo trazo en los tres estados;
      - `invalid_reason` no vacío gana sobre `state`: el resultado no es una medición, así
        que el borde va gris y punteado —sin escuadras— con el motivo, nunca de un color
        de estado.
    La etiqueta es una tarjeta con un cuadrado del color del pipeline, que conserva siempre,
    y su nombre; con `draw_roi_label` apagado no se dibuja.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return
    opts = options or OverlayOptions()
    color = opts.roi_color_bgr
    is_dashed = opts.roi_is_dashed
    thickness = max(_ROI_THICKNESS_PX, get_line_thickness(frame_bgr, opts) // 2)
    status = None                       # (texto, color) de la píldora
    if invalid_reason:
        color, is_dashed = _INVALID_ROI_BGR, True
        status = (invalid_reason, MUTED_BGR)
    elif state in ROI_STATE_COLORS_BGR:
        color, is_dashed = ROI_STATE_COLORS_BGR[state], False
        status = (_ROI_STATE_TEXT[state], color)

    x_px, y_px, width_px, height_px = (int(value) for value in roi_px)
    top_left = (x_px, y_px)
    bottom_right = (x_px + width_px, y_px + height_px)
    if is_dashed:
        _draw_dashed_rectangle(frame_bgr, top_left, bottom_right, color, thickness)
    else:
        cv2.rectangle(frame_bgr, top_left, bottom_right, color, thickness)
        _draw_corners(frame_bgr, top_left, bottom_right, color, thickness * 2)
    if opts.draw_roi_label and (opts.roi_label or status):
        _draw_roi_tag(frame_bgr, opts.roi_label, status, opts.roi_color_bgr,
                      top_left, thickness, opts)


def draw_line(frame_bgr: np.ndarray, start_px: tuple[int, int], end_px: tuple[int, int],
              color_bgr: tuple[int, int, int], *, thickness: int = 1, label: str = ""):
    """
    Dibuja in-place una línea y, si se le da, su etiqueta sobre el extremo izquierdo.

    Primitiva genérica para las referencias que no salen de la inferencia: un límite de
    carga, un eje de calibración, el borde de una zona. La geometría no se decide acá —es
    de cada instalación—: llega en píxeles ya resueltos. La etiqueta es una tarjeta con el
    texto del color de la línea, así se lee sobre cualquier fondo.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return
    cv2.line(frame_bgr, (int(start_px[0]), int(start_px[1])),
             (int(end_px[0]), int(end_px[1])), color_bgr, thickness, cv2.LINE_AA)
    if not label:
        return
    size_px = _font_px(_LABEL_SCALE)
    x_px = min(int(start_px[0]), int(end_px[0])) + _INNER_PAD_PX
    line_y_px = min(int(start_px[1]), int(end_px[1]))
    _, height_px = _measure_tag(label, size_px)
    top_px = line_y_px - height_px - thickness
    if top_px < 0:
        top_px = line_y_px + thickness
    _draw_tag(frame_bgr, label, (x_px, top_px), size_px, text_bgr=color_bgr)


def resolve_font_scale(frame_bgr: np.ndarray, lines: list[str],
                       options: OverlayOptions | None = None) -> float:
    """
    Escala de texto a usar: la del config, o una calculada si está en 0.

    La automática mide la línea más larga a escala 1 y la ajusta para que ocupe una
    fracción del ancho. Medir en vez de estimar es lo que hace que el panel entre igual
    con dos clases que con cinco, y con nombres de clase cortos o largos.
    """
    opts = options or OverlayOptions()
    if opts.font_scale > 0:
        return opts.font_scale
    if frame_bgr is None or frame_bgr.size == 0 or not lines:
        return _AUTO_FONT_MIN

    unit_px = _font_px(1.0)
    widest_px = max(_text_width(line, unit_px) for line in lines)
    # Con el relleno del panel, que crece con el texto: si se mide contra el ancho pelado,
    # el texto entra en la cuenta y el panel se corta contra el borde igual.
    padding_px = 2 * _PANEL_PAD_RATIO * _cap_height(unit_px)
    available_px = max(1, frame_bgr.shape[1] - _MARGIN_PX - 2)
    target_px = available_px * _AUTO_TEXT_WIDTH_FRACTION
    return max(_AUTO_FONT_MIN, min(_AUTO_FONT_MAX, target_px / max(1, widest_px + padding_px)))


def text_thickness(font_scale: float) -> int:
    """
    Grosor del trazo para ese tamaño de texto, con la fuente de OpenCV.

    El tamaño ya se adapta al ancho del frame; el grosor tiene que acompañarlo. Un trazo
    de un píxel en un texto grande se ve pálido y roto, que es justo lo que pasa en el
    frame completo después de haberlo ajustado en un recorte.
    """
    return max(1, int(round(font_scale / _TEXT_THICKNESS_STEP)))


def draw_text_panel(frame_bgr: np.ndarray, lines: list, *,
                    options: OverlayOptions | None = None,
                    text_bgr: tuple[int, int, int] = _PANEL_TEXT_BGR,
                    line_colors_bgr: tuple = (),
                    margin_px: int | None = None):
    """
    Dibuja in-place un panel de texto sobre una tarjeta, arriba a la izquierda.

    El fondo no es estética: sobre un frame claro el texto sin fondo no se lee, y el
    operador mira esto para decidir. Es translúcido apenas, con la opacidad de
    `SURFACE_OPACITY`.

    Cada línea es un texto o un par `(etiqueta, valor)`. Los pares se dibujan en dos
    columnas, con los valores alineados a la derecha: la fuente es proporcional, así que
    rellenar con espacios no alinea nada, y una columna de números desalineada se lee mal.

    `line_colors_bgr` pinta línea por línea, en el mismo orden que `lines`; lo que sobra,
    falta o venga en `None` cae en `text_bgr`. Es lo que deja que un panel que enumera
    clases use el color con el que están dibujadas: dos referencias del mismo dato que no
    coinciden de color obligan a leer el nombre para saber qué es cuál.

    `margin_px` es el aire entre el panel y el borde del frame; sin valor, `_MARGIN_PX`. En 0
    el panel va pegado a la esquina, y sin redondear: una esquina redonda contra el borde
    deja un hueco que parece un error.
    """
    if frame_bgr is None or frame_bgr.size == 0 or not lines:
        return
    opts = options or OverlayOptions()
    margin_px = _MARGIN_PX if margin_px is None else max(0, int(margin_px))
    rows = [(str(line[0]), str(line[1])) if isinstance(line, tuple) else (str(line), None)
            for line in lines]

    font_scale = resolve_font_scale(
        frame_bgr, [label if value is None else f"{label}  {value}" for label, value in rows],
        opts)
    size_px = _font_px(font_scale)
    cap_px = _cap_height(size_px)
    pad_px = max(_INNER_PAD_PX, int(round(cap_px * _PANEL_PAD_RATIO)))
    gap_px = int(round(cap_px * _COLUMN_GAP_RATIO))
    line_pad_px = max(_LINE_PAD_PX, int(round(cap_px * _LINE_PAD_RATIO)))

    pairs = [(label, value) for label, value in rows if value is not None]
    widths_px = [_text_width(label, size_px) for label, value in rows if value is None]
    columns_width_px = 0
    if pairs:
        columns_width_px = (max(_text_width(label, size_px) for label, _ in pairs) + gap_px
                            + max(_text_width(value, size_px) for _, value in pairs))
        widths_px.append(columns_width_px)
    panel_width_px = max(widths_px) + 2 * pad_px
    panel_height_px = len(rows) * cap_px + (len(rows) - 1) * line_pad_px + 2 * pad_px
    radius_px = 0 if margin_px == 0 else int(round(cap_px * _RADIUS_RATIO))
    _fill_rounded(frame_bgr, (margin_px, margin_px, panel_width_px, panel_height_px),
                  radius_px, SURFACE_BGR, SURFACE_OPACITY)

    value_right_px = margin_px + pad_px + columns_width_px
    for i, (label, value) in enumerate(rows):
        baseline_px = margin_px + pad_px + (i + 1) * cap_px + i * line_pad_px
        color = (line_colors_bgr[i] if i < len(line_colors_bgr) else None) or text_bgr
        _draw_text(frame_bgr, label, (margin_px + pad_px, baseline_px), size_px, color)
        if value is not None:
            _draw_text(frame_bgr, value,
                       (value_right_px - _text_width(value, size_px), baseline_px),
                       size_px, color)


def draw_status_panel(frame_bgr: np.ndarray, rows: list, *, font_scale: float,
                      margin_px: int | None = None):
    """
    Dibuja in-place un panel de veredictos arriba a la izquierda: un renglón por veredicto,
    con su rótulo y, a la derecha, una píldora con su texto y su color.

    `rows` son `(rótulo, texto, color_bgr)` —«GRANO», «OK», el verde—. Es la forma de la
    página de inicio: la píldora es el color sobre su tono suave, y todas miden lo que la
    más ancha, así que un OK que pasa a NOK no corre nada. El rótulo va en el color del
    texto y no en el gris apagado, que es el de un veredicto que no se pudo dar: un renglón
    tiene un solo color de estado.
    """
    if frame_bgr is None or frame_bgr.size == 0 or not rows:
        return
    margin_px = _MARGIN_PX if margin_px is None else max(0, int(margin_px))
    size_px = _font_px(font_scale)
    cap_px = _cap_height(size_px)
    pad_px = max(_INNER_PAD_PX, int(round(cap_px * _PANEL_PAD_RATIO)))
    pill_pad_x_px = int(round(cap_px * 0.7))
    pill_pad_y_px = int(round(cap_px * _TAG_PAD_RATIO))
    pill_height_px = cap_px + 2 * pill_pad_y_px
    pill_width_px = max(_text_width(text, size_px, "bold") for _, text, _ in rows) \
        + 2 * pill_pad_x_px
    label_width_px = max(_text_width(label, size_px, "medium") for label, _, _ in rows)
    gap_px = int(round(cap_px * _COLUMN_GAP_RATIO))
    row_gap_px = int(round(cap_px * 0.5))

    panel_width_px = label_width_px + gap_px + pill_width_px + 2 * pad_px
    panel_height_px = len(rows) * pill_height_px + (len(rows) - 1) * row_gap_px + 2 * pad_px
    radius_px = 0 if margin_px == 0 else int(round(cap_px * _RADIUS_RATIO))
    _fill_rounded(frame_bgr, (margin_px, margin_px, panel_width_px, panel_height_px),
                  radius_px, SURFACE_BGR, SURFACE_OPACITY)

    pill_x_px = margin_px + pad_px + label_width_px + gap_px
    for i, (label, text, color) in enumerate(rows):
        top_px = margin_px + pad_px + i * (pill_height_px + row_gap_px)
        baseline_px = top_px + pill_pad_y_px + cap_px
        _draw_text(frame_bgr, label, (margin_px + pad_px, baseline_px), size_px, TEXT_BGR,
                   "medium")
        _fill_rounded(frame_bgr, (pill_x_px, top_px, pill_width_px, pill_height_px),
                      pill_height_px // 2, color, _PILL_OPACITY)
        text_x_px = pill_x_px + (pill_width_px - _text_width(text, size_px, "bold")) // 2
        _draw_text(frame_bgr, text, (text_x_px, baseline_px), size_px, color, "bold")


def draw_tag(frame_bgr: np.ndarray, text: str, origin_px: tuple[int, int], *,
             font_scale: float, text_bgr: tuple[int, int, int] = TEXT_BGR,
             is_centered_y: bool = False):
    """
    Dibuja in-place una etiqueta suelta —una medida, el nombre de una referencia— en una
    tarjeta de una línea. `origin_px` es su esquina de arriba a la izquierda o, con
    `is_centered_y`, el medio de su borde izquierdo: lo que hace falta para ponerla al lado
    de un punto.
    """
    if frame_bgr is None or frame_bgr.size == 0 or not text:
        return
    size_px = _font_px(font_scale)
    x_px, y_px = origin_px
    if is_centered_y:
        y_px -= _measure_tag(text, size_px)[1] // 2
    _draw_tag(frame_bgr, text, (int(x_px), int(y_px)), size_px, text_bgr=text_bgr)


def draw_timestamp(frame_bgr: np.ndarray, timestamp_s: float, *,
                   options: OverlayOptions | None = None):
    """
    Dibuja in-place la fecha y hora del resultado abajo a la derecha, en una tarjeta chica.

    Con `font_scale` en 0 se adapta al ancho del frame igual que el panel, un punto más
    chico: en un recorte angosto un timestamp de tamaño fijo se come media imagen. Va en la
    monoespaciada, así los dígitos no bailan de un segundo al siguiente.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return
    text = datetime.datetime.fromtimestamp(timestamp_s).strftime("%Y-%m-%d %H:%M:%S")
    font_scale = resolve_font_scale(frame_bgr, [text], options) * _TIMESTAMP_RATIO
    size_px = _font_px(font_scale)
    width_px, height_px = _measure_tag(text, size_px, "mono")
    frame_height_px, frame_width_px = frame_bgr.shape[:2]
    _draw_tag(frame_bgr, text, (frame_width_px - width_px - _MARGIN_PX,
                                frame_height_px - height_px - _MARGIN_PX),
              size_px, weight="mono")


class _MaskFill:
    """
    Acumula las máscaras por color y tiñe el frame una sola vez al final.

    **El relleno se pinta por UNIÓN y no por instancia.** Teñir cada máscara por separado
    mezcla el color tantas veces como instancias se superpongan, así que una zona con
    muchas detecciones encimadas sale más saturada que una con pocas aunque las dos estén
    igual de cubiertas: el color deja de decir qué clase es y pasa a decir cuántos
    polígonos se pisaron ahí. Y lo que se ve deja de coincidir con lo que se mide, que
    cuenta cada píxel una sola vez.

    Un píxel que dos clases se disputan queda de la última que lo reclame, que es el mismo
    criterio con el que se cuenta.

    La unión es un mapa de dueños —un número de color por píxel— y no un lienzo del frame
    por color, y el teñido se hace sólo en el rectángulo que cubren las máscaras de cada
    color: una calza y una rueda que ocupan medio frame de 1920 x 1088 costaban 134 ms
    tiñendo el frame entero e indexando con booleanos, y así cuestan unos pocos.
    """

    def __init__(self, frame_shape: tuple[int, int]):
        self._frame_shape = frame_shape
        self._owner = None            # (alto, ancho) uint16; 0 = sin máscara, i = color i
        self._colors = []             # color por número de dueño - 1
        self._extents = []            # [x1, y1, x2, y2] que cubre cada color

    def add(self, color: tuple[int, int, int], mask: np.ndarray,
            bbox_px: tuple[int, int, int, int]):
        x1, y1, x2, y2 = bbox_px
        if mask.shape[:2] != (y2 - y1, x2 - x1):
            # La máscara es del bbox sin recortar: si el bbox se cortó contra el borde del
            # frame, no se la puede indexar contra la región.
            return
        selected = mask > 0
        if not selected.any():
            return
        if self._owner is None:
            self._owner = np.zeros(self._frame_shape, np.uint16)
        if color in self._colors:
            index = self._colors.index(color)
            extent = self._extents[index]
            self._extents[index] = [min(extent[0], x1), min(extent[1], y1),
                                    max(extent[2], x2), max(extent[3], y2)]
        else:
            self._colors.append(color)
            self._extents.append([x1, y1, x2, y2])
            index = len(self._colors) - 1
        np.copyto(self._owner[y1:y2, x1:x2], index + 1, where=selected)

    def blend_into(self, frame_bgr: np.ndarray, alpha: float = _MASK_ALPHA):
        """Tiñe in-place, de una pasada por color, los píxeles que quedaron de cada uno."""
        alpha = min(1.0, max(0.0, float(alpha)))
        for index, (color, (x1, y1, x2, y2)) in enumerate(zip(self._colors, self._extents,
                                                              strict=True)):
            owned = (self._owner[y1:y2, x1:x2] == index + 1).view(np.uint8)
            if not owned.any():
                continue          # otra clase le ganó todos los píxeles
            region = frame_bgr[y1:y2, x1:x2]
            # Un plano por canal y `merge`: llenar un array BGR con una tupla es diez veces
            # más lento, y `addWeighted` no acepta un color como escalar.
            tint = cv2.merge([np.full(owned.shape, channel, np.uint8) for channel in color])
            blended = cv2.addWeighted(region, 1.0 - alpha, tint, alpha, 0)
            # `copyTo` con máscara uint8 escribe en la vista del frame: indexar con booleanos
            # copia lo mismo cuarenta veces más lento.
            cv2.copyTo(blended, owned, region)


def _outline_mask(frame_bgr: np.ndarray, mask: np.ndarray,
                  bbox_px: tuple[int, int, int, int], color: tuple[int, int, int]):
    """
    Contornea in-place la forma de la máscara, sin teñir su interior.

    Con cientos de instancias el relleno se superpone hasta que no se distingue una de
    otra; el contorno deja ver el tamaño y la forma de cada una, que es lo que se está
    midiendo. El grosor es 1 fijo y no `thickness`: con instancias chicas una línea de
    dos píxeles tapa la instancia entera.
    """
    x1, y1, x2, y2 = bbox_px
    if mask.shape[:2] != (y2 - y1, x2 - x1):
        return
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    if contours:
        cv2.drawContours(frame_bgr, contours, -1, color, 1, offset=(x1, y1))


def _draw_dashed_rectangle(frame_bgr: np.ndarray, top_left_px: tuple[int, int],
                           bottom_right_px: tuple[int, int], color: tuple[int, int, int],
                           thickness: int = _ROI_THICKNESS_PX):
    """Rectángulo punteado in-place: trazos y huecos de `_DASH_LENGTH_PX`."""
    x1, y1 = top_left_px
    x2, y2 = bottom_right_px
    sides = (((x1, y1), (x2, y1)), ((x2, y1), (x2, y2)),
             ((x2, y2), (x1, y2)), ((x1, y2), (x1, y1)))
    for (start_x, start_y), (end_x, end_y) in sides:
        length_px = max(abs(end_x - start_x), abs(end_y - start_y))
        for offset_px in range(0, length_px, 2 * _DASH_LENGTH_PX):
            stop_px = min(offset_px + _DASH_LENGTH_PX, length_px)
            ratio_start = offset_px / length_px
            ratio_stop = stop_px / length_px
            cv2.line(frame_bgr,
                     (round(start_x + (end_x - start_x) * ratio_start),
                      round(start_y + (end_y - start_y) * ratio_start)),
                     (round(start_x + (end_x - start_x) * ratio_stop),
                      round(start_y + (end_y - start_y) * ratio_stop)),
                     color, thickness)


def _draw_corners(frame_bgr: np.ndarray, top_left_px: tuple[int, int],
                  bottom_right_px: tuple[int, int], color: tuple[int, int, int],
                  thickness: int):
    """Las cuatro escuadras del ROI, del doble de trazo: dicen dónde empieza la región de un vistazo."""
    x1, y1 = top_left_px
    x2, y2 = bottom_right_px
    length_px = min(_CORNER_MAX_PX, int(min(x2 - x1, y2 - y1) * _CORNER_RATIO))
    if length_px < 2:
        return
    for corner_x, corner_y, step_x, step_y in ((x1, y1, 1, 1), (x2, y1, -1, 1),
                                               (x1, y2, 1, -1), (x2, y2, -1, -1)):
        cv2.line(frame_bgr, (corner_x, corner_y), (corner_x + step_x * length_px, corner_y),
                 color, thickness)
        cv2.line(frame_bgr, (corner_x, corner_y), (corner_x, corner_y + step_y * length_px),
                 color, thickness)


def _to_drawable_text(text: str) -> str:
    """
    El texto sin tildes ni eñes, para la fuente Hershey de respaldo, que sólo dibuja ASCII:
    el nombre de un pipeline que pone el operador («Verificación de calza») saldría con «??».
    """
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _draw_label(frame_bgr: np.ndarray, text: str, origin_px: tuple[int, int],
                color: tuple[int, int, int], options: OverlayOptions):
    """
    La etiqueta de una detección: una tarjeta del color de la clase apoyada sobre la caja,
    con el texto oscuro o blanco según qué se lea mejor sobre ese color.
    """
    # Con la escala en automático la etiqueta también se adapta: leer `font_scale` crudo
    # la dibujaba en cero, o sea invisible.
    size_px = _font_px(resolve_font_scale(frame_bgr, [_AUTO_LABEL_LINE], options))
    x_px, y_px = origin_px
    _, height_px = _measure_tag(text, size_px, "medium")
    top_px = y_px - height_px
    # Contra el borde de arriba la etiqueta se dibuja dentro de la caja.
    if top_px < 0:
        top_px = y_px
    _draw_tag(frame_bgr, text, (x_px, top_px), size_px, text_bgr=_get_text_on(color),
              background_bgr=color, opacity=1.0, weight="medium")


def _draw_roi_tag(frame_bgr: np.ndarray, name: str, status: tuple | None,
                  accent_bgr: tuple[int, int, int], top_left_px: tuple[int, int],
                  stroke_px: int, options: OverlayOptions):
    """
    La etiqueta del ROI, arriba del recuadro: un cuadrado del color del pipeline, su nombre
    y, si lo hay, el veredicto en una píldora de su color —o el motivo de un resultado que no
    vale, en gris—. Sin lugar arriba, va adentro del recuadro.
    """
    size_px = _font_px(resolve_font_scale(frame_bgr, [_AUTO_LABEL_LINE], options))
    cap_px = _cap_height(size_px)
    pad_y_px = max(2, int(round(cap_px * _TAG_PAD_RATIO)))
    pad_x_px = max(3, int(round(cap_px * 0.6)))
    gap_px = max(3, int(round(cap_px * 0.5)))
    height_px = cap_px + 2 * pad_y_px
    swatch_px = max(4, int(round(cap_px * 0.8)))

    name_width_px = _text_width(name, size_px, "medium") if name else 0
    pill = None
    if status is not None:
        text, color = status
        pill_pad_x_px = max(2, int(round(cap_px * 0.45)))
        pill = (text, color, _text_width(text, size_px, "bold") + 2 * pill_pad_x_px,
                pill_pad_x_px)
    width_px = pad_x_px + swatch_px + pad_x_px
    if name:
        width_px += gap_px + name_width_px
    if pill is not None:
        width_px += gap_px + pill[2]

    x_px, y_px = top_left_px
    top_px = y_px - height_px - stroke_px
    if top_px < 0:
        top_px = y_px + stroke_px + 1
    _fill_rounded(frame_bgr, (x_px, top_px, width_px, height_px),
                  int(round(cap_px * _RADIUS_RATIO)), SURFACE_BGR, SURFACE_OPACITY)

    baseline_px = top_px + pad_y_px + cap_px
    swatch_top_px = top_px + (height_px - swatch_px) // 2
    cursor_px = x_px + pad_x_px
    _fill_rounded(frame_bgr, (cursor_px, swatch_top_px, swatch_px, swatch_px),
                  max(1, swatch_px // 4), accent_bgr, 1.0)
    cursor_px += swatch_px
    if name:
        cursor_px += gap_px
        _draw_text(frame_bgr, name, (cursor_px, baseline_px), size_px, TEXT_BGR, "medium")
        cursor_px += name_width_px
    if pill is not None:
        cursor_px += gap_px
        text, color, pill_width_px, pill_pad_x_px = pill
        pill_top_px = top_px + max(1, pad_y_px // 3)
        pill_height_px = height_px - 2 * max(1, pad_y_px // 3)
        _fill_rounded(frame_bgr, (cursor_px, pill_top_px, pill_width_px, pill_height_px),
                      pill_height_px // 2, color, _PILL_OPACITY)
        _draw_text(frame_bgr, text, (cursor_px + pill_pad_x_px, baseline_px), size_px, color,
                   "bold")


def _get_text_on(background_bgr: tuple[int, int, int]) -> tuple[int, int, int]:
    """El texto que se lee sobre ese color: oscuro sobre uno claro, blanco sobre uno oscuro."""
    blue, green, red = background_bgr
    luminance = 0.0722 * blue + 0.7152 * green + 0.2126 * red
    return _DARK_TEXT_BGR if luminance > 140 else (255, 255, 255)


# ── Texto y tarjetas ─────────────────────────────────────────────────────────

def _font_px(font_scale: float) -> int:
    """El cuerpo de la tipografía, en píxeles, que corresponde a esa escala de OpenCV."""
    return max(_MIN_FONT_PX, int(round(font_scale * _EM_PX_PER_SCALE)))


@functools.lru_cache(maxsize=128)
def _get_font(size_px: int, weight: str = "medium"):
    """La Ubuntu de ese peso y cuerpo, o None si no hay Pillow o no está el archivo."""
    if ImageFont is None:
        return None
    try:
        # El motor básico y no Raqm: el texto del overlay es latino y de una línea, y Raqm
        # tardaba tres veces más en cada uno.
        return ImageFont.truetype(os.path.join(_FONTS_DIR, _FONT_FILES[weight]), size_px,
                                  layout_engine=ImageFont.Layout.BASIC)
    except OSError:
        return None


def _hershey_scale(size_px: int) -> float:
    return size_px / _EM_PX_PER_SCALE


@functools.lru_cache(maxsize=128)
def _cap_height(size_px: int, weight: str = "medium") -> int:
    """El alto de las mayúsculas: con eso se centra el texto en una tarjeta."""
    font = _get_font(size_px, weight)
    if font is None:
        scale = _hershey_scale(size_px)
        return cv2.getTextSize("H", _FONT, scale, text_thickness(scale))[0][1]
    top, bottom = font.getbbox("H")[1::2]
    return max(1, int(bottom - top))


@functools.lru_cache(maxsize=2048)
def _text_width(text: str, size_px: int, weight: str = "medium") -> int:
    font = _get_font(size_px, weight)
    if font is None:
        scale = _hershey_scale(size_px)
        return cv2.getTextSize(_to_drawable_text(text), _FONT, scale,
                               text_thickness(scale))[0][0]
    return int(np.ceil(font.getlength(text)))


def _draw_text(frame_bgr: np.ndarray, text: str, origin_px: tuple[int, int], size_px: int,
               color: tuple[int, int, int], weight: str = "medium"):
    """
    Escribe in-place `text` con la base de la línea en `origin_px`. El texto se dibuja como
    máscara en un parche de su tamaño y se mezcla con el frame: los bordes quedan suaves y
    lo que está adentro de cada trazo es exactamente `color`.
    """
    if not text:
        return
    mask, (offset_x_px, offset_y_px) = _text_mask(text, size_px, weight)
    _blend_mask(frame_bgr, mask, (origin_px[0] + offset_x_px, origin_px[1] + offset_y_px),
                color)


@functools.lru_cache(maxsize=1024)
def _text_mask(text: str, size_px: int, weight: str) -> tuple[np.ndarray, tuple[int, int]]:
    """
    La máscara de `text` y dónde va su esquina respecto de la base de la línea.

    **Se guarda**: los textos del overlay se repiten frame a frame —el nombre del pipeline,
    «OK», la clase con su confianza— y rasterizar con FreeType cuesta casi un milisegundo por
    texto en la Jetson. Lo que cambia, como la hora, entra y sale del caché sin crecer más
    que su tope.
    """
    font = _get_font(size_px, weight)
    if font is None:
        scale = _hershey_scale(size_px)
        thickness = text_thickness(scale)
        drawable = _to_drawable_text(text)
        (width_px, height_px), descent_px = cv2.getTextSize(drawable, _FONT, scale, thickness)
        mask = np.zeros((height_px + descent_px + 2 * thickness, width_px + 2 * thickness),
                        np.uint8)
        cv2.putText(mask, drawable, (thickness, height_px + thickness), _FONT, scale, 255,
                    thickness, cv2.LINE_AA)
        offset = (-thickness, -height_px - thickness)
    else:
        ascent_px, descent_px = font.getmetrics()
        patch = Image.new("L", (_text_width(text, size_px, weight) + 2, ascent_px + descent_px))
        ImageDraw.Draw(patch).text((1, ascent_px), text, font=font, fill=255, anchor="ls")
        mask = np.array(patch)
        offset = (-1, -ascent_px)
    mask.setflags(write=False)        # compartida entre llamadas: nadie la modifica
    return mask, offset


def _measure_tag(text: str, size_px: int, weight: str = "medium") -> tuple[int, int]:
    """(ancho, alto) de la tarjeta que `_draw_tag` arma para ese texto."""
    cap_px = _cap_height(size_px, weight)
    pad_y_px = max(2, int(round(cap_px * _TAG_PAD_RATIO)))
    pad_x_px = max(3, int(round(cap_px * 0.6)))
    return _text_width(text, size_px, weight) + 2 * pad_x_px, cap_px + 2 * pad_y_px


def _draw_tag(frame_bgr: np.ndarray, text: str, top_left_px: tuple[int, int], size_px: int, *,
              text_bgr: tuple[int, int, int] = TEXT_BGR,
              background_bgr: tuple[int, int, int] = SURFACE_BGR,
              opacity: float = SURFACE_OPACITY, weight: str = "medium"):
    """Una tarjeta redondeada de una línea con su texto, desde su esquina de arriba a la izquierda."""
    width_px, height_px = _measure_tag(text, size_px, weight)
    cap_px = _cap_height(size_px, weight)
    pad_y_px = (height_px - cap_px) // 2
    pad_x_px = max(3, int(round(cap_px * 0.6)))
    x_px, y_px = top_left_px
    _fill_rounded(frame_bgr, (x_px, y_px, width_px, height_px),
                  int(round(cap_px * _RADIUS_RATIO)), background_bgr, opacity)
    _draw_text(frame_bgr, text, (x_px + pad_x_px, y_px + pad_y_px + cap_px), size_px,
               text_bgr, weight)


def _fill_rounded(frame_bgr: np.ndarray, rect_px: tuple[int, int, int, int], radius_px: int,
                  color: tuple[int, int, int], opacity: float):
    """Rellena in-place un rectángulo de esquinas redondeadas, con su opacidad."""
    x_px, y_px, width_px, height_px = (int(value) for value in rect_px)
    if width_px <= 0 or height_px <= 0:
        return
    _blend_mask(frame_bgr, _rounded_mask(width_px, height_px, int(radius_px)), (x_px, y_px),
                color, opacity)


@functools.lru_cache(maxsize=512)
def _rounded_mask(width_px: int, height_px: int, radius_px: int) -> np.ndarray:
    """La forma de una tarjeta. Se guarda: cada etiqueta repite la suya frame a frame."""
    mask = np.zeros((height_px, width_px), np.uint8)
    radius_px = max(0, min(radius_px, width_px // 2, height_px // 2))
    if radius_px == 0:
        mask[:] = 255
    else:
        cv2.rectangle(mask, (radius_px, 0), (width_px - 1 - radius_px, height_px - 1), 255,
                      cv2.FILLED)
        cv2.rectangle(mask, (0, radius_px), (width_px - 1, height_px - 1 - radius_px), 255,
                      cv2.FILLED)
        for center in ((radius_px, radius_px), (width_px - 1 - radius_px, radius_px),
                       (radius_px, height_px - 1 - radius_px),
                       (width_px - 1 - radius_px, height_px - 1 - radius_px)):
            cv2.circle(mask, center, radius_px, 255, cv2.FILLED, cv2.LINE_AA)
    mask.setflags(write=False)
    return mask


def _blend_mask(frame_bgr: np.ndarray, mask: np.ndarray, top_left_px: tuple[int, int],
                color: tuple[int, int, int], opacity: float = 1.0):
    """
    Mezcla in-place `color` sobre el frame con la máscara (0-255) como cobertura, recortada
    contra los bordes. Con cobertura y opacidad completas el píxel queda exactamente `color`.

    La mezcla la hace OpenCV y no numpy: suelta el GIL mientras trabaja, y este código corre
    en los hilos de inferencia y en el de la GUI a la vez. Hecha en numpy, cada etiqueta
    frenaba a los demás hilos aunque no dibujaran nada.
    """
    frame_height_px, frame_width_px = frame_bgr.shape[:2]
    x_px, y_px = top_left_px
    x1, y1 = max(0, x_px), max(0, y_px)
    x2 = min(frame_width_px, x_px + mask.shape[1])
    y2 = min(frame_height_px, y_px + mask.shape[0])
    if x2 <= x1 or y2 <= y1:
        return
    coverage = mask[y1 - y_px:y2 - y_px, x1 - x_px:x2 - x_px]
    region = frame_bgr[y1:y2, x1:x2]
    weight = cv2.multiply(coverage, 1.0 / 255.0 * float(opacity), dtype=cv2.CV_32F)
    solid = np.empty_like(region)
    solid[:] = color
    region[:] = cv2.blendLinear(solid, region, weight, 1.0 - weight)
