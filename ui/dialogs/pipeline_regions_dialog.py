"""
Diálogo de regiones: la región de cada pipeline en una cámara, sobre el frame en vivo.

Una pestaña por pipeline que mira esa cámara y, encima, el frame con el rectángulo de
todos: el de la pestaña activa va grueso y se edita con el mouse, los otros van finos, para
ver cuánto se pisan. Cada pestaña dice si el pipeline usa una región propia o el frame
completo.

Trabaja sobre el frame en vivo de esa cámara: quien lo abre le conecta la señal de frames
del panel, así que el operador recorta sobre lo que la cámara está viendo y no sobre una
captura vieja. Las coordenadas son del frame de referencia, en píxeles: las que lee el motor.

Junto a cada pestaña muestra **lo que el modelo recibe** —el recorte— y
avisa lo que el motor sólo diría en el log: el rectángulo se sale del frame, pide el video
nativo en una cámara que no lo puede cortar, o se dibujó sobre un frame de otro tamaño que
el que llega ahora. Los avisos son de `regions.find_region_problems()`.

Al aceptar escribe sólo las regiones que se tocaron, en
`inference.pipelines.<pipeline>.roi.<cámara>`, y persiste el archivo. Cancelar no deja nada.
Las regiones propias llevan además el tamaño del frame sobre el que se dibujaron.

**Los nombres que se muestran son los del operador**: el de la cámara
(`cameras.<slot>.name`) en el título y el de cada pipeline (`inference.pipelines.<slot>.name`)
en su pestaña, en su rectángulo y en los avisos. Sin nombre se muestra el slot, que sigue
siendo la identidad.

**Ni el corte del video nativo ni su tamaño se eligen**: en una cámara que decodifica video
(`rtsp`, `video_file`) toda región se corta del video a su resolución original, antes de
reducirlo al tamaño de la cámara, y el VIC de la Jetson la entrega siempre a la entrada del
modelo, leída de sus pesos. En una cámara GigE el frame ya es nativo, y en una con corrección de
lente o ajuste de imagen la región se corta del frame de referencia y un aviso lo dice.

El video se puede pausar (`PauseButton`) para dibujar sobre un frame quieto: la vista previa y
los avisos siguen trabajando sobre ese frame.
"""

import cv2
import numpy as np
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QSpinBox,
    QTabWidget, QVBoxLayout, QWidget,
)

from system.config_manager import ConfigManager
from system.inference import overlay
from system.inference.preprocessing import can_cut_native, loses_native_cut
from system.inference.regions import (
    FRAME_HEIGHT_KEY, FRAME_WIDTH_KEY, PROBLEM_FRAME_SIZE_CHANGED, PROBLEM_NATIVE_PREPROCESSED,
    PROBLEM_OUTSIDE_FRAME, PROBLEM_TOO_SMALL, PipelineRegion, clip_roi_px,
    find_region_problems,
    get_overlap_pct, list_camera_regions,
)

from ui.strings import tr
from ui.widgets.form import (
    add_form_row, add_hint_row, build_form_page, build_spin_box, build_value_combo_box,
)
from ui.widgets.pause_button import PauseButton
from ui.widgets.region_canvas import CanvasRegion, RectPx, RegionCanvas

_MAX_FRAME_SIDE_PX = 8192
_PREVIEW_WIDTH_PX = 240
_PREVIEW_HEIGHT_PX = 160
_DIALOG_WIDTH_PX = 900
_DIALOG_HEIGHT_PX = 860

# Las dos formas de la región de un pipeline en una cámara. Son de la pantalla: el config
# las dice con el `enabled` de la sección.
_MODE_OWN = "own"
_MODE_FULL = "full"

_GEOMETRY_FIELDS = (
    ("x_px", "cam_roi_x"),
    ("y_px", "cam_roi_y"),
    ("width_px", "cam_roi_width"),
    ("height_px", "cam_roi_height"),
)


class _RegionPage:
    """Los campos de una pestaña —un pipeline— y lo que se sabía de él al abrir."""

    def __init__(self, region: PipelineRegion, *, stored_frame_size_px: tuple[int, int]):
        self.region = region
        self.stored_frame_size_px = stored_frame_size_px
        self.is_touched = False
        self.page: QWidget
        self.mode: QComboBox
        self.spins: dict[str, QSpinBox]
        self.preview: QLabel
        self.preview_note: QLabel
        self.overlap: QLabel


class PipelineRegionsDialog(QDialog):
    """
    Diálogo de regiones de una cámara. Se abre con `exec()`.

    Los frames entran por `update_frame()`, que es el slot que hay que conectar a la señal
    de frames de esa cámara. `initial_pipeline` elige la pestaña con la que abre.
    """

    def __init__(self, config_manager: ConfigManager, camera_slot: str, parent=None, *,
                 initial_pipeline: str = ""):
        super().__init__(parent)
        self._config = config_manager
        self._camera_slot = camera_slot
        self._last_frame: np.ndarray | None = None
        self._pages: list[_RegionPage] = []
        self._is_native_cut = can_cut_native(config_manager, camera_slot)
        self._is_native_lost = loses_native_cut(config_manager, camera_slot)

        camera_name = str(self._config.get(f"cameras.{camera_slot}.name", "") or camera_slot)
        self.setWindowTitle(f"{tr('regions_title')} — {camera_name}")
        self.setWindowFlags(Qt.Dialog | Qt.WindowCloseButtonHint)
        self.resize(_DIALOG_WIDTH_PX, _DIALOG_HEIGHT_PX)

        self._canvas = RegionCanvas()
        self._canvas.region_edited.connect(self._on_region_edited)
        self._problems_label = QLabel()
        self._problems_label.setObjectName("warningBanner")
        self._problems_label.setWordWrap(True)
        self._problems_label.hide()
        self._tabs = QTabWidget()
        self._empty_label = QLabel(tr("regions_no_pipelines"))
        self._empty_label.hide()

        layout = QVBoxLayout(self)
        layout.addWidget(self._canvas, stretch=1)
        hint = QLabel(tr("regions_hint"))
        hint.setObjectName("hintLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(self._problems_label)
        layout.addWidget(self._tabs)
        layout.addWidget(self._empty_label)
        layout.addLayout(self._build_button_row())

        self._build_pages(initial_pipeline)
        self._tabs.currentChanged.connect(lambda _index: self._refresh())
        self._refresh()

    # ── API pública ──────────────────────────────────────────────────────────

    @Slot(object)
    def update_frame(self, frame_bgr: np.ndarray | None):
        """Slot para la señal de frames de la cámara. Ignora un frame vacío o en pausa."""
        if frame_bgr is None or frame_bgr.size == 0 or self._pause_button.is_paused:
            return
        is_new_size = (self._last_frame is None
                       or frame_bgr.shape[:2] != self._last_frame.shape[:2])
        self._last_frame = frame_bgr
        self._canvas.set_frame(frame_bgr)
        if is_new_size:
            self._refresh()
        else:
            self._refresh_preview()

    # ── Construcción ─────────────────────────────────────────────────────────

    def _build_button_row(self) -> QHBoxLayout:
        reset_button = QPushButton(tr("roi_reset"))
        reset_button.setObjectName("resetButton")
        reset_button.clicked.connect(self._on_reset_clicked)
        self._pause_button = PauseButton()

        cancel_button = QPushButton(tr("dialog_cancel"))
        cancel_button.setObjectName("cancelButton")
        cancel_button.clicked.connect(self.reject)

        self._save_button = QPushButton(tr("regions_save"))
        self._save_button.setObjectName("saveButton")
        self._save_button.clicked.connect(self._on_save_clicked)

        row = QHBoxLayout()
        row.addWidget(reset_button)
        row.addWidget(self._pause_button)
        row.addStretch()
        row.addWidget(cancel_button)
        row.addWidget(self._save_button)
        return row

    def _build_pages(self, initial_pipeline: str):
        pipelines = self._config.get("inference.pipelines", {}) or {}
        regions = list_camera_regions(pipelines, self._camera_slot, include_disabled=True)
        if not regions:
            self._tabs.hide()
            self._empty_label.show()
            self._save_button.setEnabled(False)
            return

        for region in regions:
            stored_size = (int(region.roi.get(FRAME_WIDTH_KEY, 0) or 0),
                           int(region.roi.get(FRAME_HEIGHT_KEY, 0) or 0))
            page = _RegionPage(region, stored_frame_size_px=stored_size)
            self._build_page(page)
            self._pages.append(page)
            self._tabs.addTab(page.page, region.name)
        for index, page in enumerate(self._pages):
            if page.region.pipeline_slot == initial_pipeline:
                self._tabs.setCurrentIndex(index)

    def _build_page(self, page: _RegionPage):
        region = page.region
        form_page, form = build_form_page()
        page.page = QWidget()

        modes = {tr("inf_roi_mode_own"): _MODE_OWN, tr("inf_roi_mode_full"): _MODE_FULL}
        page.mode = add_form_row(form, tr("inf_roi_mode"),
                                 build_value_combo_box(modes, _get_initial_mode(region)))
        page.spins = {key: add_form_row(form, tr(label_key),
                                        build_spin_box(0, _MAX_FRAME_SIDE_PX, 0))
                      for key, label_key in _GEOMETRY_FIELDS}
        add_hint_row(form, tr("inf_roi_note"))

        roi = region.roi
        for key, spin in page.spins.items():
            spin.setValue(int(roi.get(key, 0) or 0))
        self._apply_page_state(page)

        page.preview = QLabel()
        page.preview.setObjectName("videoFeed")
        page.preview.setAlignment(Qt.AlignCenter)
        page.preview.setFixedSize(_PREVIEW_WIDTH_PX, _PREVIEW_HEIGHT_PX)
        page.preview_note = QLabel()
        page.preview_note.setObjectName("hintLabel")
        page.preview_note.setWordWrap(True)
        page.overlap = QLabel()
        page.overlap.setObjectName("hintLabel")
        page.overlap.setWordWrap(True)

        preview_column = QVBoxLayout()
        preview_column.addWidget(QLabel(tr("regions_preview_box")))
        preview_column.addWidget(page.preview)
        preview_column.addWidget(page.preview_note)
        preview_column.addWidget(page.overlap)
        preview_column.addStretch()

        row = QHBoxLayout(page.page)
        row.addWidget(form_page, stretch=1)
        row.addLayout(preview_column)

        for spin in page.spins.values():
            spin.valueChanged.connect(lambda _value, page=page: self._on_page_changed(page))
        page.mode.currentIndexChanged.connect(
            lambda _index, page=page: self._on_page_changed(page))

    # ── Estado de una pestaña ────────────────────────────────────────────────

    def _apply_page_state(self, page: _RegionPage):
        """Habilita los campos que corresponden al modo: sólo la región propia se edita."""
        is_own = page.mode.currentData() == _MODE_OWN
        for spin in page.spins.values():
            spin.setEnabled(is_own)

    def _read_page_roi(self, page: _RegionPage) -> dict:
        """El ROI que describe la pestaña, con la forma del config."""
        frame_size_px = self._get_frame_size_px()
        drawn_size_px = frame_size_px if page.is_touched and frame_size_px \
            else page.stored_frame_size_px
        roi = {"enabled": page.mode.currentData() == _MODE_OWN}
        roi.update({key: spin.value() for key, spin in page.spins.items()})
        if all(drawn_size_px):
            roi[FRAME_WIDTH_KEY], roi[FRAME_HEIGHT_KEY] = drawn_size_px
        return roi

    def _get_rect_px(self, page: _RegionPage) -> RectPx | None:
        """El rectángulo de esa pestaña en el frame, o None si ve el frame completo."""
        if page.mode.currentData() == _MODE_FULL:
            return None
        roi = {**self._read_page_roi(page), "enabled": True}
        frame_size_px = self._get_frame_size_px()
        if frame_size_px:
            return clip_roi_px(roi, *frame_size_px)
        if roi.get("width_px", 0) > 0 and roi.get("height_px", 0) > 0:
            return (roi["x_px"], roi["y_px"], roi["width_px"], roi["height_px"])
        return None

    def _get_frame_size_px(self) -> tuple[int, int] | None:
        if self._last_frame is None:
            return None
        height_px, width_px = self._last_frame.shape[:2]
        return (width_px, height_px)

    def _get_current_page(self) -> _RegionPage | None:
        index = self._tabs.currentIndex()
        return self._pages[index] if 0 <= index < len(self._pages) else None

    # ── Refresco ─────────────────────────────────────────────────────────────

    def _refresh(self):
        """Redibuja las regiones y rehace los avisos, la vista previa y los solapes."""
        regions = []
        current = self._get_current_page()
        for page in self._pages:
            style = overlay.get_pipeline_style(page.region.index)
            regions.append(CanvasRegion(
                name=page.region.name, color_bgr=style.color_bgr,
                rect_px=self._get_rect_px(page), is_dashed=style.is_dashed,
                is_active=page is current,
                is_editable=page.mode.currentData() == _MODE_OWN))
        self._canvas.set_regions(regions)
        self._refresh_problems()
        self._refresh_preview()
        self._refresh_overlap()

    def _refresh_problems(self):
        """Avisos de todas las pestañas: el de la activa abajo del lienzo y un ⚠ en las demás."""
        frame_size_px = self._get_frame_size_px()
        current = self._get_current_page()
        messages = []
        for index, page in enumerate(self._pages):
            problems = find_region_problems(
                self._read_page_roi(page), frame_size_px=frame_size_px,
                is_native_lost=self._is_native_lost, is_native_cut=self._is_native_cut)
            title = page.region.name
            self._tabs.setTabText(index, f"⚠ {title}" if problems else title)
            if page is current:
                messages = [self._describe_problem(problem, page) for problem in problems]
        self._problems_label.setText("\n".join(messages))
        self._problems_label.setVisible(bool(messages))

    def _describe_problem(self, problem: str, page: _RegionPage) -> str:
        if problem == PROBLEM_OUTSIDE_FRAME:
            return tr("regions_problem_outside_frame")
        if problem == PROBLEM_NATIVE_PREPROCESSED:
            return tr("regions_problem_native_preprocessed")
        if problem == PROBLEM_TOO_SMALL:
            return tr("regions_problem_too_small")
        if problem == PROBLEM_FRAME_SIZE_CHANGED:
            drawn_width_px, drawn_height_px = page.stored_frame_size_px
            now_width_px, now_height_px = self._get_frame_size_px()
            return tr("regions_problem_frame_size_changed").format(
                drawn=f"{drawn_width_px}x{drawn_height_px}",
                now=f"{now_width_px}x{now_height_px}")
        return problem

    def _refresh_preview(self):
        """Muestra, en la pestaña activa, lo que recibe el modelo."""
        page = self._get_current_page()
        if page is None:
            return
        if self._last_frame is None:
            page.preview.setText(tr("regions_waiting_frame"))
            return
        rect_px = self._get_rect_px(page)
        crop = _crop_frame(self._last_frame, rect_px)
        is_view = (rect_px is not None and self._is_native_cut
                   and page.mode.currentData() == _MODE_OWN)
        page.preview.setPixmap(_build_pixmap(crop, _PREVIEW_WIDTH_PX, _PREVIEW_HEIGHT_PX))
        height_px, width_px = crop.shape[:2]
        note = f"{width_px}×{height_px} px"
        if rect_px is None:
            note = f"{tr('regions_preview_whole')} {note}"
        elif is_view:
            note = f"{note}\n{tr('regions_preview_approx')}"
        page.preview_note.setText(note)

    def _refresh_overlap(self):
        """Cuánto de la región activa cubren las demás."""
        current = self._get_current_page()
        if current is None:
            return
        rect_px = self._get_rect_px(current)
        lines = []
        if rect_px is not None:
            for page in self._pages:
                other_px = self._get_rect_px(page)
                if page is current or other_px is None:
                    continue
                overlap_pct = get_overlap_pct(rect_px, other_px)
                if overlap_pct > 0:
                    lines.append(tr("regions_overlap").format(
                        pipeline=page.region.name, pct=round(overlap_pct)))
        current.overlap.setText("\n".join(lines))

    # ── Eventos ──────────────────────────────────────────────────────────────

    def _on_page_changed(self, page: _RegionPage):
        """Un campo cambió: la pestaña pasa a tocada y todo se rehace desde sus campos."""
        page.is_touched = True
        self._apply_page_state(page)
        self._refresh()

    def _on_region_edited(self, x_px: int, y_px: int, width_px: int, height_px: int):
        """El mouse movió la región activa: se pasa a los números sin volver a dibujarla."""
        page = self._get_current_page()
        if page is None:
            return
        page.is_touched = True
        for key, value in (("x_px", x_px), ("y_px", y_px),
                           ("width_px", width_px), ("height_px", height_px)):
            spin = page.spins[key]
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)
        self._refresh_problems()
        self._refresh_preview()
        self._refresh_overlap()

    def _on_reset_clicked(self):
        page = self._get_current_page()
        if page is None:
            return
        page.is_touched = True
        for spin in page.spins.values():
            spin.blockSignals(True)
            spin.setValue(0)
            spin.blockSignals(False)
        self._refresh()

    def _on_save_clicked(self):
        """Escribe las pestañas que se tocaron, persiste una sola vez y cierra."""
        for page in self._pages:
            if page.is_touched:
                self._write_page(page)
        self._config.save()
        self.accept()

    def _write_page(self, page: _RegionPage):
        key = f"inference.pipelines.{page.region.pipeline_slot}.roi.{self._camera_slot}"
        for field_key, value in self._read_page_roi(page).items():
            self._config.set(f"{key}.{field_key}", value)


def _get_initial_mode(region: PipelineRegion) -> str:
    return _MODE_OWN if region.roi.get("enabled", False) else _MODE_FULL


def _crop_frame(frame_bgr: np.ndarray, rect_px: RectPx | None) -> np.ndarray:
    """El recorte del frame, o el frame entero si no hay rectángulo."""
    if rect_px is None:
        return frame_bgr
    x_px, y_px, width_px, height_px = rect_px
    return frame_bgr[y_px:y_px + height_px, x_px:x_px + width_px]


def _build_pixmap(image_bgr: np.ndarray, max_width_px: int, max_height_px: int) -> QPixmap:
    """La imagen como pixmap que entra en el recuadro, sin deformarla."""
    if image_bgr.ndim == 2:
        image_bgr = cv2.cvtColor(image_bgr, cv2.COLOR_GRAY2BGR)
    image_bgr = np.ascontiguousarray(image_bgr)
    height_px, width_px = image_bgr.shape[:2]
    image = QImage(image_bgr.data, width_px, height_px, 3 * width_px, QImage.Format_BGR888)
    return QPixmap.fromImage(image).scaled(
        max_width_px, max_height_px, Qt.KeepAspectRatio, Qt.SmoothTransformation)
