"""
Diálogo de polígono: dibujar con el mouse una zona sobre el frame de una cámara.

Es el hermano del diálogo de nivel para un área que no es un rectángulo: una zona de
exclusión, un sector del piso. Trabaja sobre el frame en vivo —quien lo abre le conecta la
señal de frames del panel— y los vértices que guarda son del frame de cámara, en píxeles:
los mismos que lee el motor.

- **Click** sobre la imagen agrega un vértice al final; sobre un lado del polígono ya
  cerrado, lo inserta en ese lado, que es como se refina una zona sin rehacerla.
- **Arrastrar** un vértice lo mueve. **Click derecho** sobre un vértice lo borra.
- **Borrar zona** deja el polígono vacío; guardado así, la clave queda en null: sin zona.

La zona se ve tramada —un tinte, rayas diagonales y el borde— para que se lea como un área
y no como un recuadro más.

Guarda una lista de `[x, y]` en la clave que se le dé, y persiste el archivo al aceptar. Con
uno o dos vértices no hay polígono y no se puede guardar. **La clave es un parámetro**: quien
pide la zona es la pestaña dueña de esa sección, y este diálogo no sabe qué se mide con ella.

El video se puede pausar (`PauseButton`) para dibujar sobre un frame quieto.
"""

import math

import cv2
import numpy as np
from PySide6.QtCore import QPointF, Qt, Slot
from PySide6.QtGui import (
    QBrush, QColor, QImage, QMouseEvent, QPainter, QPen, QPixmap, QPolygonF, QResizeEvent,
)
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from system.config_manager import ConfigManager

from ui import theme
from ui.strings import tr
from ui.widgets.form import add_hint_row
from ui.widgets.pause_button import PauseButton

MIN_VERTICES = 3

_MIN_CANVAS_WIDTH_PX = 640
_MIN_CANVAS_HEIGHT_PX = 400
_DIALOG_WIDTH_PX = 760
_DIALOG_HEIGHT_PX = 580
_OUTLINE_WIDTH_PX = 2
_FILL_ALPHA = 60                # 0–255: el tinte deja ver el frame debajo
_HANDLE_RADIUS_PX = 5
_GRAB_DISTANCE_PX = 10          # a cuánto del puntero se agarra un vértice o un lado


class PolygonDialog(QDialog):
    """
    Diálogo para dibujar la zona de una cámara.

    Se abre con `exec()`. Guardar escribe los vértices en `config_key` —null sin ninguno— y
    persiste; cancelar no deja nada. Los frames entran por `update_frame()`, que es el slot
    que hay que conectar a la señal de frames de esa cámara. `title_key` es la clave de
    idiomas del título, para que la ventana diga qué zona se está dibujando.
    """

    def __init__(self, config_manager: ConfigManager, camera_slot: str, config_key: str,
                 parent=None, *, title_key: str = "polygon_title"):
        super().__init__(parent)
        self._config = config_manager
        self._config_key = config_key

        camera_name = str(self._config.get(f"cameras.{camera_slot}.name", "") or camera_slot)
        self.setWindowTitle(f"{tr(title_key)} — {camera_name}")
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        self.resize(_DIALOG_WIDTH_PX, _DIALOG_HEIGHT_PX)

        self._canvas = PolygonCanvas()
        self._canvas.points_changed = self._on_points_changed

        layout = QVBoxLayout(self)
        layout.addWidget(self._canvas, stretch=1)
        layout.addWidget(self._build_info_box())
        layout.addLayout(self._build_button_row())

        self._canvas.set_points(_read_points(self._config.get(self._config_key)))
        self._on_points_changed(self._canvas.points)

    # ── API pública ──────────────────────────────────────────────────────────

    @Slot(object)
    def update_frame(self, frame_bgr: np.ndarray | None):
        """Slot para la señal de frames de la cámara. Ignora un frame vacío o en pausa."""
        if frame_bgr is None or frame_bgr.size == 0 or self._pause_button.is_paused:
            return
        self._canvas.set_frame(frame_bgr)

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_info_box(self) -> QGroupBox:
        box = QGroupBox(tr("polygon_box"))
        form = QFormLayout(box)
        self._count_label = QLabel()
        form.addRow(self._count_label)
        add_hint_row(form, tr("polygon_hint"))
        return box

    def _build_button_row(self) -> QHBoxLayout:
        self._pause_button = PauseButton()

        clear_button = QPushButton(tr("polygon_clear"))
        clear_button.clicked.connect(self._on_clear_clicked)

        cancel_button = QPushButton(tr("dialog_cancel"))
        cancel_button.setObjectName("cancelButton")
        cancel_button.clicked.connect(self.reject)

        self._save_button = QPushButton(tr("polygon_save"))
        self._save_button.setObjectName("saveButton")
        self._save_button.clicked.connect(self._on_save_clicked)

        row = QHBoxLayout()
        row.addWidget(self._pause_button)
        row.addWidget(clear_button)
        row.addStretch()
        row.addWidget(cancel_button)
        row.addWidget(self._save_button)
        return row

    # ── Internos ─────────────────────────────────────────────────────────────

    def _on_points_changed(self, points: list[tuple[int, int]]):
        """Cuenta los vértices y habilita guardar sólo con un polígono o sin ninguno."""
        is_savable = not points or len(points) >= MIN_VERTICES
        if not points:
            self._count_label.setText(tr("polygon_empty"))
        elif is_savable:
            self._count_label.setText(tr("polygon_vertices").format(count=len(points)))
        else:
            self._count_label.setText(tr("polygon_too_few").format(count=len(points),
                                                                   min=MIN_VERTICES))
        self._save_button.setEnabled(is_savable)

    def _on_clear_clicked(self):
        self._canvas.set_points([])

    def _on_save_clicked(self):
        points = self._canvas.points
        self._config.set(self._config_key, [[x, y] for x, y in points] if points else None)
        self._config.save()
        self.accept()


class PolygonCanvas(QLabel):
    """
    Lienzo del frame con un polígono editable con el mouse: ver el docstring del módulo.

    Guarda los vértices en píxeles del frame y los reproyecta en cada frame recibido, así
    sobreviven a que la ventana cambie de tamaño. `points_changed` es un callback
    `(vértices)` que el diálogo asigna. Sin frame no hay sobre qué dibujar, y el mouse no hace
    nada.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("videoFeed")
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumSize(_MIN_CANVAS_WIDTH_PX, _MIN_CANVAS_HEIGHT_PX)

        self.points_changed = None
        self._points: list[tuple[int, int]] = []
        self._dragged_index: int | None = None
        self._last_frame: np.ndarray | None = None
        self._scale = 1.0
        self._offset_x_px = 0
        self._offset_y_px = 0
        self._frame_width_px = 0
        self._frame_height_px = 0

    @property
    def points(self) -> list[tuple[int, int]]:
        return list(self._points)

    def set_points(self, points: list[tuple[int, int]]):
        """Reemplaza los vértices, en píxeles del frame, y avisa."""
        self._points = [(int(x), int(y)) for x, y in points]
        self._dragged_index = None
        self._on_points_edited()

    def set_frame(self, frame_bgr: np.ndarray):
        """Escala el frame al lienzo, calcula la conversión y dibuja el polígono."""
        if frame_bgr.ndim == 2:
            frame_bgr = cv2.cvtColor(frame_bgr, cv2.COLOR_GRAY2BGR)
        frame_bgr = np.ascontiguousarray(frame_bgr)
        self._last_frame = frame_bgr
        frame_height_px, frame_width_px = frame_bgr.shape[:2]

        image = QImage(frame_bgr.data, frame_width_px, frame_height_px,
                       3 * frame_width_px, QImage.Format_BGR888)
        scaled = QPixmap.fromImage(image).scaled(
            self.size(), Qt.KeepAspectRatio, Qt.FastTransformation
        )
        if scaled.width() <= 0 or scaled.height() <= 0:
            return

        self._frame_width_px = frame_width_px
        self._frame_height_px = frame_height_px
        self._scale = frame_width_px / scaled.width()
        self._offset_x_px = (self.width() - scaled.width()) // 2
        self._offset_y_px = (self.height() - scaled.height()) // 2

        canvas = QPixmap(self.size())
        canvas.fill(Qt.black)
        painter = QPainter(canvas)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.drawPixmap(self._offset_x_px, self._offset_y_px, scaled)
        self._paint_polygon(painter)
        painter.end()
        self.setPixmap(canvas)

    def resizeEvent(self, event: QResizeEvent):
        """Redibuja el último frame al nuevo tamaño: en pausa no llega otro que lo haga."""
        super().resizeEvent(event)
        if self._last_frame is not None:
            self.set_frame(self._last_frame)

    # ── Mouse ────────────────────────────────────────────────────────────────

    def mousePressEvent(self, event: QMouseEvent):
        if not self._frame_width_px:
            return
        position = event.position()
        index = self._find_vertex(position)
        if event.button() == Qt.RightButton:
            if index is not None:
                del self._points[index]
                self._on_points_edited()
            return
        if event.button() != Qt.LeftButton:
            return
        if index is None:
            edge = self._find_edge(position)
            index = len(self._points) if edge is None else edge + 1
            self._points.insert(index, self._to_frame(position))
            self._on_points_edited()
        self._dragged_index = index

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._dragged_index is None:
            return
        self._points[self._dragged_index] = self._to_frame(event.position())
        self._on_points_edited()

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._dragged_index = None

    # ── Internos ─────────────────────────────────────────────────────────────

    def _on_points_edited(self):
        if self._last_frame is not None:
            self.set_frame(self._last_frame)
        if self.points_changed is not None:
            self.points_changed(self.points)

    def _paint_polygon(self, painter: QPainter):
        """El polígono tramado, o la línea abierta mientras no llega a tres vértices."""
        if not self._points:
            return
        color = theme.get_color("polygon_outline", True)
        vertices = [self._to_widget(point) for point in self._points]
        painter.setPen(QPen(color, _OUTLINE_WIDTH_PX))
        if len(vertices) >= MIN_VERTICES:
            polygon = QPolygonF(vertices)
            tint = QColor(color)
            tint.setAlpha(_FILL_ALPHA)
            painter.setBrush(QBrush(tint))
            painter.drawPolygon(polygon)
            painter.setBrush(QBrush(color, Qt.BDiagPattern))
            painter.drawPolygon(polygon)
        else:
            painter.drawPolyline(QPolygonF(vertices))
        painter.setBrush(QBrush(color))
        painter.setPen(QPen(Qt.white, 1))
        for vertex in vertices:
            painter.drawEllipse(vertex, _HANDLE_RADIUS_PX, _HANDLE_RADIUS_PX)

    def _to_widget(self, point: tuple[int, int]) -> QPointF:
        return QPointF(point[0] / self._scale + self._offset_x_px,
                       point[1] / self._scale + self._offset_y_px)

    def _to_frame(self, position: QPointF) -> tuple[int, int]:
        """El píxel del frame bajo el puntero, recortado al frame."""
        x_px = int((position.x() - self._offset_x_px) * self._scale)
        y_px = int((position.y() - self._offset_y_px) * self._scale)
        return (max(0, min(x_px, self._frame_width_px - 1)),
                max(0, min(y_px, self._frame_height_px - 1)))

    def _find_vertex(self, position: QPointF) -> int | None:
        """El vértice más cercano al puntero, si está a mano."""
        distances = [math.dist((position.x(), position.y()), (vertex.x(), vertex.y()))
                     for vertex in map(self._to_widget, self._points)]
        if not distances or min(distances) > _GRAB_DISTANCE_PX:
            return None
        return distances.index(min(distances))

    def _find_edge(self, position: QPointF) -> int | None:
        """El índice del vértice donde arranca el lado bajo el puntero, con el polígono cerrado."""
        if len(self._points) < MIN_VERTICES:
            return None
        vertices = [self._to_widget(point) for point in self._points]
        best_index, best_distance = None, float(_GRAB_DISTANCE_PX)
        for index, start in enumerate(vertices):
            end = vertices[(index + 1) % len(vertices)]
            distance = _distance_to_segment(position, start, end)
            if distance <= best_distance:
                best_index, best_distance = index, distance
        return best_index


def _read_points(value: object) -> list[tuple[int, int]]:
    """Los vértices guardados en el config; nada si la clave no es una lista de `[x, y]`."""
    if not isinstance(value, list):
        return []
    points = []
    for point in value:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            return []
        points.append((int(point[0]), int(point[1])))
    return points


def _distance_to_segment(point: QPointF, start: QPointF, end: QPointF) -> float:
    dx, dy = end.x() - start.x(), end.y() - start.y()
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.dist((point.x(), point.y()), (start.x(), start.y()))
    t = max(0.0, min(1.0, ((point.x() - start.x()) * dx + (point.y() - start.y()) * dy)
                     / length_sq))
    return math.dist((point.x(), point.y()), (start.x() + t * dx, start.y() + t * dy))
