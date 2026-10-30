"""
Dibujado del resultado sobre el frame: cajas, máscaras, etiquetas, panel de información
y timestamp.

Módulo puro: sin Qt, sin ConfigManager, sin hilos y sin I/O. Recibe un resultado y
devuelve una copia anotada del frame.

Maquinaria genérica: reutilizable entre proyectos. Todo resultado de inferencia tiene un
frame anotado —es lo que mira el operador en la UI, lo que sale por el stream
`annotated` y lo que guarda el dataset—, y sale de acá una sola vez: el mismo array
viaja a los tres destinos en vez de que cada uno vuelva a dibujar lo mismo.

Por eso también lo puede importar `ui/`: es nivel 1 y no sabe que existe una GUI. Un
panel que quiera redibujar un resultado guardado llama a `annotate()` y obtiene
exactamente lo que vio el operador.

Acá va sólo lo que sirve en cualquier fork: lo que sale de la inferencia —cajas,
máscaras, etiquetas, el panel, el ROI, el timestamp— y las primitivas con las que se
dibuja cualquier referencia (`draw_line`, `draw_text_panel`). Los dibujos propios de una
instalación —un límite de carga, una zona de rechazo— se arman con esas primitivas en
`annotations.py` y entran por el `annotator` del motor, no acá: este archivo se copia al
próximo proyecto tal cual.

Dos restricciones que no se ven en las firmas:
  - **El texto dibujado es ASCII.** Las fuentes Hershey de OpenCV no tienen acentos ni
    eñes: lo que se pase con tildes sale con signos de pregunta. Los nombres de clase y
    las claves de métricas ya vienen en inglés desde el config, así que alcanza con no
    traducir las etiquetas acá.
  - **`annotate()` copia el frame** y los `draw_*` dibujan in-place sobre lo que reciben.
    El frame de la cámara no se toca nunca: lo comparten la captura, el stream crudo y
    el dataset.
"""

import datetime
import unicodedata
from dataclasses import dataclass, field
from typing import NamedTuple

import cv2
import numpy as np

from .result import (ROI_STATE_NOK, ROI_STATE_OK, ROI_STATE_WARNING, Detection,
                     InferenceResult)

# Paleta por índice de clase, en BGR. Se recorre en círculo, así que sirve para
# cualquier cantidad de clases; el config la puede reemplazar entera.
DEFAULT_CLASS_COLORS_BGR = (
    (217, 116, 50),    # azul
    (10, 120, 255),    # naranja
    (95, 200, 95),     # verde
    (59, 235, 255),    # amarillo
    (200, 120, 220),   # violeta
    (60, 60, 230),     # rojo
)

# Un color por pipeline para su ROI, en BGR. **Sin verde, rojo ni amarillo**: son los
# colores del estado —un ROI que se pinta por su veredicto— y un pipeline que los usara de
# identidad se confundiría con un resultado. Con más pipelines que colores se repiten, y
# la segunda vuelta va punteada.
PIPELINE_COLORS_BGR = (
    (230, 130, 40),    # azul
    (225, 215, 40),    # cian
    (200, 60, 225),    # magenta
    (215, 95, 135),    # violeta
    (245, 245, 245),   # blanco
)

# Con qué se pinta el borde del ROI según el veredicto del pipeline (`roi_state`). Son los
# tres colores que la paleta de pipelines no usa, para que un estado nunca se confunda con
# una identidad. El texto va en la etiqueta además del color: no se puede perder la alarma
# en un stream en blanco y negro ni para un operador daltónico.
ROI_STATE_COLORS_BGR = {
    ROI_STATE_OK: (80, 190, 70),        # verde
    ROI_STATE_NOK: (50, 50, 225),       # rojo
    ROI_STATE_WARNING: (30, 205, 250),  # amarillo
}
_ROI_STATE_TEXT = {ROI_STATE_OK: "OK", ROI_STATE_NOK: "NOK", ROI_STATE_WARNING: "WARN"}
_INVALID_ROI_BGR = (150, 150, 150)      # un resultado que no es una medición no se pinta de estado

_FONT = cv2.FONT_HERSHEY_SIMPLEX
_PANEL_BG_BGR = (0, 0, 0)
_PANEL_TEXT_BGR = (235, 235, 235)
_INVALID_TEXT_BGR = (60, 60, 230)
_ROI_BGR = (180, 180, 180)
_LABEL_TEXT_BGR = (20, 20, 20)
# Cómo se dibuja la máscara de una detección. Con pocas instancias el relleno se lee
# mejor; con cientos —granos, semillas, partículas— se convierte en una mancha y el
# contorno es lo único que deja ver dónde termina cada una.
MASK_STYLE_FILL = "fill"          # sólo el relleno translúcido
MASK_STYLE_OUTLINE = "outline"    # sólo el contorno
MASK_STYLE_BOTH = "both"          # contorno nítido y relleno translúcido
MASK_STYLES = (MASK_STYLE_FILL, MASK_STYLE_OUTLINE, MASK_STYLE_BOTH)

_MASK_ALPHA = 0.45        # peso del color de la máscara sobre el frame
_MARGIN_PX = 10           # aire entre el panel y el borde del frame
_INNER_PAD_PX = 8
_LINE_PAD_PX = 6          # aire mínimo entre dos líneas del panel
_LINE_PAD_RATIO = 0.4     # ídem, en fracción del alto del texto: crece con la escala
_COLUMN_GAP = "  "        # aire entre la etiqueta y el valor, medido con la fuente del panel
_TIMESTAMP_RATIO = 0.8    # el timestamp va más chico que el panel: es referencia, no dato
_LABEL_SCALE = 0.55       # etiqueta de una referencia dibujada con draw_line()
_ROI_THICKNESS_PX = 2
_ROI_NOK_THICKNESS_PX = 4  # el rojo además engorda: es el que no se puede pasar por alto
_DASH_LENGTH_PX = 10      # largo de un trazo y de su hueco en un ROI punteado

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

    `font_scale` en 0 calcula el tamaño del texto a partir del ancho del frame. Es lo que
    hace falta cuando el lienzo cambia de tamaño —un recorte de 130 px y un frame de
    1920—: una escala fija que se lee bien en uno tapa al otro o desaparece.
    """

    draw_boxes: bool = True
    draw_masks: bool = True
    mask_style: str = MASK_STYLE_FILL
    mask_alpha: float = _MASK_ALPHA
    draw_labels: bool = True
    draw_roi: bool = True
    draw_timestamp: bool = True
    font_scale: float = 0.6   # 0 = automática, según el ancho del frame
    thickness: int = 2
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
    """Dibuja in-place las máscaras, las cajas y las etiquetas, con el color de cada clase."""
    if frame_bgr is None or frame_bgr.size == 0:
        return
    opts = options or OverlayOptions()
    height_px, width_px = frame_bgr.shape[:2]

    # Relleno por clase, acumulado y teñido una sola vez al final: ver `_stack_fill`.
    fill_by_color: dict = {}

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
                _stack_fill(fill_by_color, color, detection.mask, (x1, y1, x2, y2),
                            (height_px, width_px))
            if opts.mask_style in (MASK_STYLE_OUTLINE, MASK_STYLE_BOTH):
                _outline_mask(frame_bgr, detection.mask, (x1, y1, x2, y2), color)
        if opts.draw_boxes:
            cv2.rectangle(frame_bgr, (x1, y1), (x2, y2), color, opts.thickness)
        if opts.draw_labels:
            label = f"{detection.class_name} {detection.confidence_pct:.0f}%"
            _draw_label(frame_bgr, label, (x1, y1), color, opts)

    for color, fill in fill_by_color.items():
        _blend_fill(frame_bgr, fill, color, opts.mask_alpha)


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
    en una cámara con varios se distinguen. Encima va el estado:
      - `state` (un `ROI_STATE_*`) pinta el borde del color del veredicto y lo escribe en
        la etiqueta, y el `nok` además va más grueso;
      - `invalid_reason` no vacío gana sobre `state`: el resultado no es una medición, así
        que el borde va gris y punteado con el motivo, nunca de un color de estado.
    La etiqueta conserva siempre el color del pipeline.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return
    opts = options or OverlayOptions()
    color = opts.roi_color_bgr
    is_dashed = opts.roi_is_dashed
    thickness = _ROI_THICKNESS_PX
    label = opts.roi_label
    if invalid_reason:
        color, is_dashed = _INVALID_ROI_BGR, True
        label = f"{label} ({invalid_reason})".strip()
    elif state in ROI_STATE_COLORS_BGR:
        color, is_dashed = ROI_STATE_COLORS_BGR[state], False
        if state == ROI_STATE_NOK:
            thickness = _ROI_NOK_THICKNESS_PX
        label = f"{label} {_ROI_STATE_TEXT[state]}".strip()

    x_px, y_px, width_px, height_px = (int(value) for value in roi_px)
    top_left = (x_px, y_px)
    bottom_right = (x_px + width_px, y_px + height_px)
    if is_dashed:
        _draw_dashed_rectangle(frame_bgr, top_left, bottom_right, color, thickness)
    else:
        cv2.rectangle(frame_bgr, top_left, bottom_right, color, thickness)
    if label:
        _draw_label(frame_bgr, label, top_left, opts.roi_color_bgr, opts)


def draw_line(frame_bgr: np.ndarray, start_px: tuple[int, int], end_px: tuple[int, int],
              color_bgr: tuple[int, int, int], *, thickness: int = 1, label: str = ""):
    """
    Dibuja in-place una línea y, si se le da, su etiqueta pegada al extremo izquierdo.

    Primitiva genérica para las referencias que no salen de la inferencia: un límite de
    carga, un eje de calibración, el borde de una zona. La geometría no se decide acá —es
    de cada instalación—: llega en píxeles ya resueltos.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return
    cv2.line(frame_bgr, (int(start_px[0]), int(start_px[1])),
             (int(end_px[0]), int(end_px[1])), color_bgr, thickness, cv2.LINE_AA)
    if not label:
        return
    x_px = min(int(start_px[0]), int(end_px[0])) + _INNER_PAD_PX
    y_px = min(int(start_px[1]), int(end_px[1])) - _INNER_PAD_PX
    cv2.putText(frame_bgr, label, (x_px, max(_MARGIN_PX, y_px)),
                _FONT, _LABEL_SCALE, color_bgr, text_thickness(_LABEL_SCALE), cv2.LINE_AA)


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

    widest_px = max(cv2.getTextSize(line, _FONT, 1.0, 1)[0][0] for line in lines)
    # Descontando el margen y el relleno del panel: si se mide contra el ancho pelado, el
    # texto entra en la cuenta y se corta contra el borde igual.
    available_px = max(1, frame_bgr.shape[1] - _MARGIN_PX - 2 * _INNER_PAD_PX)
    target_px = available_px * _AUTO_TEXT_WIDTH_FRACTION
    return max(_AUTO_FONT_MIN, min(_AUTO_FONT_MAX, target_px / max(1, widest_px)))


def text_thickness(font_scale: float) -> int:
    """
    Grosor del trazo para ese tamaño de texto.

    El tamaño ya se adapta al ancho del frame; el grosor tiene que acompañarlo. Un trazo
    de un píxel en un texto grande se ve pálido y roto, que es justo lo que pasa en el
    frame completo después de haberlo ajustado en un recorte.
    """
    return max(1, int(round(font_scale / _TEXT_THICKNESS_STEP)))


def draw_text_panel(frame_bgr: np.ndarray, lines: list, *,
                    options: OverlayOptions | None = None,
                    text_bgr: tuple[int, int, int] = _PANEL_TEXT_BGR,
                    line_colors_bgr: tuple = ()):
    """
    Dibuja in-place un panel de texto con fondo opaco, arriba a la izquierda.

    El fondo opaco no es estética: sobre un frame claro el texto sin fondo no se lee, y
    el operador mira esto para decidir.

    Cada línea es un texto o un par `(etiqueta, valor)`. Los pares se dibujan en dos
    columnas, con los valores alineados a la derecha: la fuente es proporcional, así que
    rellenar con espacios no alinea nada, y una columna de números desalineada se lee mal.
    Con todos los valores en el mismo formato —`33.4%`, `100.0%`— el punto decimal cae en
    el mismo lugar.

    `line_colors_bgr` pinta línea por línea, en el mismo orden que `lines`; lo que sobra,
    falta o venga en `None` cae en `text_bgr`. Es lo que deja que un panel que enumera
    clases use el color con el que están dibujadas: dos referencias del mismo dato que no
    coinciden de color obligan a leer el nombre para saber qué es cuál.
    """
    if frame_bgr is None or frame_bgr.size == 0 or not lines:
        return
    opts = options or OverlayOptions()
    rows = [(str(line[0]), str(line[1])) if isinstance(line, tuple) else (str(line), None)
            for line in lines]

    font_scale = resolve_font_scale(
        frame_bgr, [label if value is None else label + _COLUMN_GAP + value
                    for label, value in rows], opts)
    thickness = text_thickness(font_scale)

    def measure(text: str) -> tuple[int, int]:
        return cv2.getTextSize(text, _FONT, font_scale, thickness)[0]

    pairs = [(label, value) for label, value in rows if value is not None]
    widths_px = [measure(label)[0] for label, value in rows if value is None]
    columns_width_px = 0
    if pairs:
        columns_width_px = (max(measure(label)[0] for label, _ in pairs)
                            + measure(_COLUMN_GAP)[0]
                            + max(measure(value)[0] for _, value in pairs))
        widths_px.append(columns_width_px)
    text_width_px = max(widths_px)
    value_right_px = _MARGIN_PX + _INNER_PAD_PX + columns_width_px
    line_height_px = max(measure(text)[1] for row in rows for text in row if text is not None)
    line_pad_px = max(_LINE_PAD_PX, int(round(line_height_px * _LINE_PAD_RATIO)))

    panel_width_px = text_width_px + 2 * _INNER_PAD_PX
    panel_height_px = (len(rows) * line_height_px + (len(rows) - 1) * line_pad_px
                       + 2 * _INNER_PAD_PX)
    cv2.rectangle(frame_bgr, (_MARGIN_PX, _MARGIN_PX),
                  (_MARGIN_PX + panel_width_px, _MARGIN_PX + panel_height_px),
                  _PANEL_BG_BGR, cv2.FILLED)

    for i, (label, value) in enumerate(rows):
        y_px = _MARGIN_PX + _INNER_PAD_PX + (i + 1) * line_height_px + i * line_pad_px
        color = (line_colors_bgr[i] if i < len(line_colors_bgr) else None) or text_bgr
        cv2.putText(frame_bgr, label, (_MARGIN_PX + _INNER_PAD_PX, y_px),
                    _FONT, font_scale, color, thickness, cv2.LINE_AA)
        if value is not None:
            cv2.putText(frame_bgr, value, (value_right_px - measure(value)[0], y_px),
                        _FONT, font_scale, color, thickness, cv2.LINE_AA)


def draw_timestamp(frame_bgr: np.ndarray, timestamp_s: float, *,
                   options: OverlayOptions | None = None):
    """
    Dibuja in-place la fecha y hora del resultado, abajo a la derecha.

    Con `font_scale` en 0 se adapta al ancho del frame igual que el panel, un punto más
    chico: en un recorte angosto un timestamp de tamaño fijo se come media imagen.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        return
    text = datetime.datetime.fromtimestamp(timestamp_s).strftime("%Y-%m-%d %H:%M:%S")
    font_scale = resolve_font_scale(frame_bgr, [text], options) * _TIMESTAMP_RATIO
    thickness = text_thickness(font_scale)
    (text_width_px, text_height_px), baseline_px = cv2.getTextSize(
        text, _FONT, font_scale, thickness)
    height_px, width_px = frame_bgr.shape[:2]
    x_px = width_px - text_width_px - _MARGIN_PX
    y_px = height_px - _MARGIN_PX
    cv2.rectangle(frame_bgr, (x_px - _INNER_PAD_PX, y_px - text_height_px - baseline_px),
                  (width_px, height_px), _PANEL_BG_BGR, cv2.FILLED)
    cv2.putText(frame_bgr, text, (x_px, y_px - baseline_px // 2),
                _FONT, font_scale, _PANEL_TEXT_BGR, thickness, cv2.LINE_AA)


def _stack_fill(fill_by_color: dict, color: tuple[int, int, int], mask: np.ndarray,
                bbox_px: tuple[int, int, int, int], frame_shape: tuple[int, int]):
    """
    Acumula la máscara sobre el lienzo de su color, en lugar de teñir el frame ya.

    **El relleno se pinta por UNIÓN y no por instancia.** Teñir cada máscara por separado
    mezcla el color tantas veces como instancias se superpongan, así que una zona con
    muchas detecciones encimadas sale más saturada que una con pocas aunque las dos estén
    igual de cubiertas: el color deja de decir qué clase es y pasa a decir cuántos
    polígonos se pisaron ahí. Y lo que se ve deja de coincidir con lo que se mide, que
    cuenta cada píxel una sola vez.

    Un píxel que dos clases se disputan queda de la última que lo reclame, que es el mismo
    criterio con el que se cuenta.
    """
    x1, y1, x2, y2 = bbox_px
    if mask.shape[:2] != (y2 - y1, x2 - x1):
        # La máscara es del bbox sin recortar: si el bbox se cortó contra el borde del
        # frame, no se la puede indexar contra la región.
        return
    selected = mask > 0
    if not selected.any():
        return
    for other_color, other_fill in fill_by_color.items():
        if other_color != color:
            other_fill[y1:y2, x1:x2][selected] = False
    fill = fill_by_color.get(color)
    if fill is None:
        fill = np.zeros(frame_shape, dtype=bool)
        fill_by_color[color] = fill
    fill[y1:y2, x1:x2][selected] = True


def _blend_fill(frame_bgr: np.ndarray, fill: np.ndarray, color: tuple[int, int, int],
                alpha: float = _MASK_ALPHA):
    """Tiñe in-place, de una sola pasada, todos los píxeles de una clase."""
    if not fill.any():
        return
    tint = np.empty_like(frame_bgr)
    tint[:] = color
    alpha = min(1.0, max(0.0, float(alpha)))
    blended = cv2.addWeighted(frame_bgr, 1.0 - alpha, tint, alpha, 0)
    frame_bgr[fill] = blended[fill]


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


def _to_drawable_text(text: str) -> str:
    """
    El texto sin tildes ni eñes: la fuente Hershey de OpenCV sólo dibuja ASCII, y el nombre
    de un pipeline que pone el operador («Verificación de calza») saldría con «??».
    """
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def _draw_label(frame_bgr: np.ndarray, text: str, origin_px: tuple[int, int],
                color: tuple[int, int, int], options: OverlayOptions):
    """Etiqueta con fondo del color de la clase, pegada arriba de la caja."""
    x_px, y_px = origin_px
    text = _to_drawable_text(text)
    # Con la escala en automático la etiqueta también se adapta: leer `font_scale` crudo
    # la dibujaba en cero, o sea invisible.
    font_scale = resolve_font_scale(frame_bgr, [_AUTO_LABEL_LINE], options)
    thickness = text_thickness(font_scale)
    (text_width_px, text_height_px), baseline_px = cv2.getTextSize(
        text, _FONT, font_scale, thickness)
    top_px = y_px - text_height_px - baseline_px - 2
    # Contra el borde de arriba la etiqueta se dibuja dentro de la caja.
    if top_px < 0:
        top_px = y_px + 2
    cv2.rectangle(frame_bgr, (x_px, top_px),
                  (x_px + text_width_px + 4, top_px + text_height_px + baseline_px + 2),
                  color, cv2.FILLED)
    cv2.putText(frame_bgr, text, (x_px + 2, top_px + text_height_px + 2),
                _FONT, font_scale, _LABEL_TEXT_BGR, thickness, cv2.LINE_AA)
