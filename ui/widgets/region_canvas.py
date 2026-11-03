"""
Lienzo del frame con los rectángulos de varios pipelines, uno de ellos editable.

Dibuja el frame en vivo y, encima, la región de cada pipeline con su color y su nombre.
La región **activa** va gruesa y se edita con el mouse —arrastrarla desde adentro la mueve,
desde un borde o una esquina la estira, desde afuera dibuja una nueva—; las demás van finas
y sólo sirven de referencia para ver cuánto se pisan.

Trabaja en píxeles del frame: el lienzo convierte entre el frame y la pantalla y no deja
salir coordenadas de widget. No sabe de qué pipeline es cada rectángulo ni dónde se
guardan: recibe `CanvasRegion` y avisa por `region_edited` cuando cambia la activa.

La geometría del arrastre (`get_handle_at`, `drag_rect`, `build_rect_from_points`) son
funciones puras del módulo, para probarlas sin pantalla.
"""

from typing import NamedTuple

import cv2
import numpy as np
from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import (
    QColor, QImage, QMouseEvent, QPainter, QPen, QPixmap, QResizeEvent,
)
from PySide6.QtWidgets import QLabel, QSizePolicy

from ui.strings import tr

RectPx = tuple[int, int, int, int]      # (x, y, ancho, alto) en píxeles del frame

_MIN_CANVAS_WIDTH_PX = 640
_MIN_CANVAS_HEIGHT_PX = 300
_MIN_REGION_SIDE_PX = 10        # menos que esto es un click, no una región
_HANDLE_MARGIN_PX = 6           # tolerancia del mouse sobre un borde, en pantalla
_HANDLE_SIZE_PX = 8
_ACTIVE_PEN_WIDTH_PX = 3
_REFERENCE_PEN_WIDTH_PX = 1
_LABEL_TEXT_COLOR = QColor(20, 20, 20)

# Dónde se agarró el rectángulo. Los bordes y esquinas se nombran por los puntos
# cardinales de lo que se mueve: «nw» mueve el borde de arriba y el de la izquierda.
HANDLE_MOVE = "move"
_HANDLE_CURSORS = {
    "nw": Qt.SizeFDiagCursor, "se": Qt.SizeFDiagCursor,
    "ne": Qt.SizeBDiagCursor, "sw": Qt.SizeBDiagCursor,
    "n": Qt.SizeVerCursor, "s": Qt.SizeVerCursor,
    "e": Qt.SizeHorCursor, "w": Qt.SizeHorCursor,
    HANDLE_MOVE: Qt.SizeAllCursor,
}


class CanvasRegion(NamedTuple):
    """
    Un rectángulo a dibujar. `rect_px` en None es «sin rectángulo» (el frame completo).

    `color_bgr` es el del pipeline, el mismo que usa el overlay. Sólo la región activa y
    editable responde al mouse.
    """

    name: str
    color_bgr: tuple[int, int, int]
    rect_px: RectPx | None
    is_dashed: bool = False
    is_active: bool = False
    is_editable: bool = False


def get_handle_at(rect_px: RectPx, point_px: tuple[float, float], scale: float,
                  margin_px: int = _HANDLE_MARGIN_PX) -> str:
    """
    Qué parte del rectángulo está bajo el mouse: una esquina, un borde, `HANDLE_MOVE` si
    está adentro, o '' si está afuera.

    `point_px` va en píxeles del frame y `scale` es píxeles de pantalla por píxel de
    frame: el margen se mide en pantalla, para que sea igual de fácil agarrar un borde con
    el frame reducido que con el frame grande.
    """
    x_px, y_px, width_px, height_px = rect_px
    margin = margin_px / scale
    point_x, point_y = point_px
    if not (x_px - margin <= point_x <= x_px + width_px + margin
            and y_px - margin <= point_y <= y_px + height_px + margin):
        return ""
    handle = ""
    if abs(point_y - y_px) <= margin:
        handle += "n"
    elif abs(point_y - (y_px + height_px)) <= margin:
        handle += "s"
    if abs(point_x - x_px) <= margin:
        handle += "w"
    elif abs(point_x - (x_px + width_px)) <= margin:
        handle += "e"
    return handle or HANDLE_MOVE


def drag_rect(rect_px: RectPx, handle: str, delta_x_px: int, delta_y_px: int,
              frame_size_px: tuple[int, int]) -> RectPx:
    """
    El rectángulo después de arrastrar `handle` esa cantidad de píxeles.

    Mover lo deja adentro del frame sin cambiarle el tamaño; estirar no deja que un lado
    pase al opuesto ni que el rectángulo mida menos de `_MIN_REGION_SIDE_PX`.
    """
    x_px, y_px, width_px, height_px = rect_px
    frame_width_px, frame_height_px = frame_size_px
    if handle == HANDLE_MOVE:
        return (_clamp(x_px + delta_x_px, 0, frame_width_px - width_px),
                _clamp(y_px + delta_y_px, 0, frame_height_px - height_px),
                width_px, height_px)

    left, top, right, bottom = x_px, y_px, x_px + width_px, y_px + height_px
    if "w" in handle:
        left = _clamp(left + delta_x_px, 0, right - _MIN_REGION_SIDE_PX)
    if "e" in handle:
        right = _clamp(right + delta_x_px, left + _MIN_REGION_SIDE_PX, frame_width_px)
    if "n" in handle:
        top = _clamp(top + delta_y_px, 0, bottom - _MIN_REGION_SIDE_PX)
    if "s" in handle:
        bottom = _clamp(bottom + delta_y_px, top + _MIN_REGION_SIDE_PX, frame_height_px)
    return (left, top, right - left, bottom - top)


def build_rect_from_points(start_px: tuple[float, float], end_px: tuple[float, float],
                           frame_size_px: tuple[int, int]) -> RectPx | None:
    """
    El rectángulo que forman dos puntos del frame, acotado al frame, o None si es un click.

    Un rectángulo más chico que `_MIN_REGION_SIDE_PX` no se publica: aceptarlo dejaría
    una región de dos píxeles sin que el operador la pida.
    """
    frame_width_px, frame_height_px = frame_size_px
    x1 = _clamp(round(min(start_px[0], end_px[0])), 0, frame_width_px)
    y1 = _clamp(round(min(start_px[1], end_px[1])), 0, frame_height_px)
    x2 = _clamp(round(max(start_px[0], end_px[0])), 0, frame_width_px)
    y2 = _clamp(round(max(start_px[1], end_px[1])), 0, frame_height_px)
    if x2 - x1 < _MIN_REGION_SIDE_PX or y2 - y1 < _MIN_REGION_SIDE_PX:
        return None
    return (x1, y1, x2 - x1, y2 - y1)


class RegionCanvas(QLabel):
    """
    Frame con las regiones encima; la activa se edita con el mouse.

    Guarda las regiones en coordenadas del frame y las reproyecta en cada repintado, así
    sobreviven a que la ventana cambie de tamaño.
    """

    # Nuevo rectángulo de la región activa, en píxeles del frame: (x, y, ancho, alto).
    # Sale en cada movimiento del mouse mientras se arrastra, y una vez más al soltar.
    region_edited = Signal(int, int, int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("videoFeed")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(_MIN_CANVAS_WIDTH_PX, _MIN_CANVAS_HEIGHT_PX)
        # Ignorado: el pixmap que se pinta tiene el tamaño del widget, y con la política por
        # defecto ese tamaño pasa a ser lo que el widget pide, así que nunca podría achicarse.
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.setMouseTracking(True)
        self.setText(tr("regions_waiting_frame"))

        self._regions: list[CanvasRegion] = []
        self._base_pixmap: QPixmap | None = None
        self._last_frame: np.ndarray | None = None
        self._frame_size_px = (0, 0)
        self._scale = 1.0           # píxeles de pantalla por píxel de frame
        self._offset_px = (0, 0)    # esquina del frame dentro del widget
        self._drag_handle = ""
        self._drag_start_px: tuple[float, float] | None = None
        self._drag_origin: RectPx | None = None

    # ── API pública ──────────────────────────────────────────────────────────

    @property
    def frame_size_px(self) -> tuple[int, int]:
        """(ancho, alto) del último frame, o (0, 0) si todavía no llegó ninguno."""
        return self._frame_size_px

    def set_frame(self, frame_bgr: np.ndarray):
        """Escala el frame al lienzo, calcula la conversión y dibuja las regiones."""
        if frame_bgr.ndim == 2:
            frame_bgr = cv2.cvtColor(frame_bgr, cv2.COLOR_GRAY2BGR)
        self._last_frame = np.ascontiguousarray(frame_bgr)
        self._rebuild_base()
        self._render()

    def set_regions(self, regions: list[CanvasRegion]):
        """Reemplaza lo que se dibuja encima del frame."""
        self._regions = list(regions)
        self._render()

    # ── Mouse ────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() != Qt.LeftButton or not self._frame_size_px[0]:
            return
        active = self._get_editable_region()
        if active is None:
            return
        point_px = self._to_frame(event.position().toPoint())
        handle = get_handle_at(active.rect_px, point_px, self._scale) if active.rect_px else ""
        self._drag_handle = handle
        self._drag_start_px = point_px
        self._drag_origin = active.rect_px

    def mouseMoveEvent(self, event: QMouseEvent):
        point_px = self._to_frame(event.position().toPoint())
        if self._drag_start_px is None:
            self._update_cursor(point_px)
            return
        if self._drag_handle and self._drag_origin is not None:
            moved = drag_rect(
                self._drag_origin, self._drag_handle,
                round(point_px[0] - self._drag_start_px[0]),
                round(point_px[1] - self._drag_start_px[1]), self._frame_size_px)
        else:
            moved = build_rect_from_points(self._drag_start_px, point_px, self._frame_size_px)
        if moved is not None:
            self._apply_edit(moved)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._drag_start_px = None
            self._drag_origin = None
            self._drag_handle = ""

    def resizeEvent(self, event: QResizeEvent):
        super().resizeEvent(event)
        if self._last_frame is not None:
            self._rebuild_base()
            self._render()

    # ── Internos ─────────────────────────────────────────────────────────────

    def _get_editable_region(self) -> CanvasRegion | None:
        for region in self._regions:
            if region.is_active and region.is_editable:
                return region
        return None

    def _apply_edit(self, rect_px: RectPx):
        """Deja el rectángulo nuevo en la región activa, lo dibuja y lo publica."""
        self._regions = [
            region._replace(rect_px=rect_px) if region.is_active and region.is_editable
            else region for region in self._regions]
        self._render()
        self.region_edited.emit(*rect_px)

    def _update_cursor(self, point_px: tuple[float, float]):
        active = self._get_editable_region()
        handle = (get_handle_at(active.rect_px, point_px, self._scale)
                  if active is not None and active.rect_px else "")
        if active is None:
            self.unsetCursor()
        else:
            self.setCursor(_HANDLE_CURSORS.get(handle, Qt.CrossCursor))

    def _to_frame(self, point: QPoint) -> tuple[float, float]:
        """Un punto del widget en píxeles del frame."""
        return ((point.x() - self._offset_px[0]) / self._scale,
                (point.y() - self._offset_px[1]) / self._scale)

    def _to_widget(self, rect_px: RectPx) -> QRect:
        x_px, y_px, width_px, height_px = rect_px
        return QRect(round(x_px * self._scale) + self._offset_px[0],
                     round(y_px * self._scale) + self._offset_px[1],
                     round(width_px * self._scale), round(height_px * self._scale))

    def _rebuild_base(self):
        """Escala el último frame al widget y fija la conversión de coordenadas."""
        frame_bgr = self._last_frame
        frame_height_px, frame_width_px = frame_bgr.shape[:2]
        image = QImage(frame_bgr.data, frame_width_px, frame_height_px, 3 * frame_width_px,
                       QImage.Format_BGR888)
        scaled = QPixmap.fromImage(image).scaled(
            self.size(), Qt.KeepAspectRatio, Qt.FastTransformation)
        if scaled.width() <= 0 or scaled.height() <= 0:
            return
        self._frame_size_px = (frame_width_px, frame_height_px)
        self._scale = scaled.width() / frame_width_px
        self._offset_px = ((self.width() - scaled.width()) // 2,
                           (self.height() - scaled.height()) // 2)
        base = QPixmap(self.size())
        base.fill(Qt.black)
        painter = QPainter(base)
        painter.drawPixmap(self._offset_px[0], self._offset_px[1], scaled)
        painter.end()
        self._base_pixmap = base

    def _render(self):
        """Dibuja las regiones sobre el frame escalado. Las activas, arriba de las demás."""
        if self._base_pixmap is None:
            return
        canvas = QPixmap(self._base_pixmap)
        painter = QPainter(canvas)
        for region in sorted(self._regions, key=lambda item: item.is_active):
            if region.rect_px is not None:
                self._paint_region(painter, region)
        painter.end()
        self.setPixmap(canvas)

    def _paint_region(self, painter: QPainter, region: CanvasRegion):
        rect = self._to_widget(region.rect_px)
        paint_region_box(painter, rect, region.name, region.color_bgr, region.is_dashed,
                         _ACTIVE_PEN_WIDTH_PX if region.is_active
                         else _REFERENCE_PEN_WIDTH_PX)
        if region.is_active and region.is_editable:
            blue, green, red = region.color_bgr
            half = _HANDLE_SIZE_PX // 2
            for corner_x, corner_y in ((rect.left(), rect.top()), (rect.right(), rect.top()),
                                       (rect.left(), rect.bottom()),
                                       (rect.right(), rect.bottom())):
                painter.fillRect(corner_x - half, corner_y - half, _HANDLE_SIZE_PX,
                                 _HANDLE_SIZE_PX, QColor(red, green, blue))


def paint_region_box(painter: QPainter, rect: QRect, name: str,
                     color_bgr: tuple[int, int, int], is_dashed: bool, pen_width_px: int):
    """
    Dibuja un recuadro de región con su nombre sobre el borde de arriba.

    Es lo que dibujan el lienzo del diálogo y el panel de cámara, para que una región se
    vea igual en los dos. El color es el que el overlay le da al pipeline.
    """
    blue, green, red = color_bgr
    color = QColor(red, green, blue)
    pen = QPen(color, pen_width_px)
    if is_dashed:
        pen.setStyle(Qt.DashLine)
    painter.setPen(pen)
    painter.drawRect(rect)

    metrics = painter.fontMetrics()
    label_rect = QRect(rect.left(), rect.top() - metrics.height() - 2,
                       metrics.horizontalAdvance(name) + 6, metrics.height() + 2)
    if label_rect.top() < 0:
        label_rect.moveTop(rect.top())
    painter.fillRect(label_rect, color)
    painter.setPen(_LABEL_TEXT_COLOR)
    painter.drawText(label_rect.adjusted(3, 0, 0, 0), Qt.AlignVCenter, name)


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(value, max(minimum, maximum)))
