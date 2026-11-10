"""
Las pestañas de configuración: las crea, las carga y las persiste una sola vez.

No es una pantalla. Cada pestaña de `ui/views/config/` es dueña de su sección y sabe
cargarla y guardarla; las páginas de `ui/navigation.py` las muestran donde corresponde, y
esto las crea, las recorre y llama al `save()` del ConfigManager cuando todas escribieron.
No conoce ninguna clave del config.

**Un solo guardado para todas, en cualquier página.** El «Guardar y aplicar» de una página
guarda también lo editado en las otras: guardar sólo una dejaría lo demás editado en
pantalla y sin persistir, que se lee como guardado y no lo está.

Guardar es atómico para el usuario: si una pestaña levanta una excepción se avisa y no
se persiste nada, así el config.yaml no queda con la mitad de los cambios.

**Sabe qué pestañas tienen cambios sin guardar**, y lo publica por `pending_changed` para que
la página muestre la barra de guardar sólo cuando hay algo que guardar. Lo hace sin que las
pestañas declaren nada: compara cada campo —texto, número, combo, checkbox— con lo que
mostraba después de la última carga o el último guardado. Un campo que vuelve a su valor
deja de contar. Lo que escribe y persiste un diálogo —el ROI, la altura, la zona— no es un
cambio pendiente: ya está en el archivo, así que al recargarse el campo su valor pasa a ser
el de referencia.
"""

from collections.abc import Callable
from functools import partial

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractSpinBox, QCheckBox, QComboBox, QDoubleSpinBox, QLineEdit, QMessageBox, QSpinBox,
    QWidget,
)

from system.config_manager import ConfigManager
from system.logger import logger
from ui.strings import tr
from ui.views.config.abstract_tab import AbstractConfigTab
from ui.views.config.cameras_tab import CamerasTab


class ConfigTabs(QObject):
    """Dueño de las pestañas de configuración. Emite `config_saved` cuando el archivo se escribió."""

    config_saved = Signal()               # el config.yaml se persistió sin errores
    # (slot de cámara, clave del config donde va el rectángulo, clave de idioma del título).
    # Lo manda la pestaña que lo pide: esto lo reenvía sin leerlo.
    open_roi_requested = Signal(str, str, str)
    # (slot de cámara, slot del pipeline elegido): editar las regiones de los pipelines de esa
    # cámara. Lo manda la pestaña que lo pide y esto lo reenvía sin leerlo.
    open_regions_requested = Signal(str, str)
    # (slot de cámara, clave del config donde va la altura, clave de idioma del título). Igual
    # que el rectángulo: lo manda la pestaña que lo pide y esto lo reenvía sin leerlo.
    open_level_requested = Signal(str, str, str)
    # (slot de cámara, clave del config donde va la zona, clave de idioma del título). Igual
    # que la altura: lo manda la pestaña que lo pide y esto lo reenvía sin leerlo.
    open_polygon_requested = Signal(str, str, str)
    calibrate_lens_requested = Signal(str)  # slot de la cámara cuya óptica hay que calibrar
    # Títulos de las pestañas con cambios sin guardar, en el orden de la navegación; vacía
    # cuando no queda nada pendiente. Sale sólo cuando la lista cambia.
    pending_changed = Signal(object)

    def __init__(self, config_manager: ConfigManager, tab_classes: tuple[type, ...],
                 parent=None):
        super().__init__(parent)
        self._config = config_manager
        self._tabs: list[AbstractConfigTab] = [tab_class(self._config)
                                               for tab_class in tab_classes]
        # Pestañas que cargaron bien. Una que no cargó tiene los campos en su valor por
        # defecto, así que guardarla escribiría esos defaults encima de la config real.
        self._loaded_tabs: set = set()
        # Lo que mostraba cada campo después de la última carga o el último guardado.
        self._baseline: dict[QWidget, object] = {}
        self._pending_titles: list[str] = []
        self._is_recheck_scheduled = False

        # Cualquier pestaña que sepa dibujar un rectángulo, sin nombrar ninguna: una
        # pestaña nueva con otro rectángulo se engancha sola.
        self._roi_requester = None
        self._regions_requester = None
        self._level_requester = None
        self._polygon_requester = None
        for tab in self._tabs:
            for name, slot in (("open_roi_requested", self._on_roi_requested),
                               ("open_regions_requested", self._on_regions_requested),
                               ("open_level_requested", self._on_level_requested),
                               ("open_polygon_requested", self._on_polygon_requested)):
                signal = getattr(tab, name, None)
                if signal is not None:
                    signal.connect(partial(slot, tab))
        cameras_tab = self.get_tab(CamerasTab)
        if cameras_tab is not None:
            cameras_tab.calibrate_requested.connect(self.calibrate_lens_requested)

        # Cargar acá y no dejarlo librado a cada pestaña: una que se olvide de hacerlo en
        # su constructor arranca con los campos en su default y el primer guardado los
        # persiste. El invariante lo garantiza quien las crea, no cada una por su cuenta.
        self.reload()

    # ── API pública ──────────────────────────────────────────────────────────

    def get_tab(self, tab_class: type) -> AbstractConfigTab | None:
        """La pestaña de esa clase, o None si no está entre las que se crearon."""
        for tab in self._tabs:
            if isinstance(tab, tab_class):
                return tab
        return None

    def reload(self):
        """
        Recarga todas las pestañas desde el config, descartando lo que se haya editado.

        Es lo que hace el botón de descartar, y también lo que hay que llamar cuando el
        `config.yaml` cambió por afuera de la aplicación.

        Una pestaña que falla al cargar no corta a las demás y queda marcada: no se la
        guarda, porque sus campos tienen el valor por defecto y persistirlos borraría la
        configuración real de esa sección sin que nada parezca haber fallado.
        """
        for tab in self._tabs:
            try:
                tab.load()
            except Exception as error:
                self._loaded_tabs.discard(tab)
                logger.error(
                    f"[UI] La pestaña '{tr(tab.TITLE_KEY)}' no pudo cargar su "
                    f"configuración: {error}. No se va a guardar.")
            else:
                self._loaded_tabs.add(tab)
        self._reset_baseline()

    def get_pending_titles(self) -> list[str]:
        """Títulos de las pestañas con cambios sin guardar; vacía si no hay ninguno."""
        return list(self._pending_titles)

    def save(self, parent: QWidget):
        """
        Lleva todas las pestañas al config y lo persiste, avisando el resultado sobre `parent`.

        Si una pestaña falla no se persiste nada.
        """
        try:
            for tab in self._tabs:
                if tab not in self._loaded_tabs:
                    logger.error(
                        f"[UI] No se guarda la pestaña '{tr(tab.TITLE_KEY)}': no había "
                        f"cargado su configuración y escribiría valores por defecto.")
                    continue
                tab.save()
        except Exception as error:
            logger.error(f"[UI] No se pudo armar la configuración a guardar: {error}")
            QMessageBox.warning(parent, tr("config_saved_title"), tr("config_save_error"))
            return
        if not self._config.save():
            QMessageBox.warning(parent, tr("config_saved_title"), tr("config_save_error"))
            return
        logger.info("[UI] Configuración guardada.")
        self._reset_baseline()
        self.config_saved.emit()
        QMessageBox.information(parent, tr("config_saved_title"), tr("config_saved_body"))

    def refresh_roi_fields(self, camera_slot: str):
        """
        Recarga el rectángulo tras cerrarse el diálogo, en la pestaña que lo pidió.

        Sólo esa: recargarlas todas descartaría lo que el operador esté editando en otra
        —abrir el dibujo de un ROI no es motivo para perder una exposición a medio cambiar—.
        """
        if self._roi_requester is not None:
            self._refresh_keeping_edits(lambda: self._roi_requester.reload_roi(camera_slot))

    def refresh_region_fields(self):
        """Recarga las filas de región tras cerrarse el diálogo, sólo en la pestaña que lo pidió."""
        if self._regions_requester is not None:
            self._refresh_keeping_edits(self._regions_requester.reload_regions)

    def refresh_level_fields(self, camera_slot: str):
        """Recarga la altura tras cerrarse su diálogo, sólo en la pestaña que la pidió."""
        if self._level_requester is not None:
            self._refresh_keeping_edits(lambda: self._level_requester.reload_level(camera_slot))

    def refresh_polygon_fields(self, camera_slot: str):
        """Recarga la zona tras cerrarse su diálogo, sólo en la pestaña que la pidió."""
        if self._polygon_requester is not None:
            self._refresh_keeping_edits(
                lambda: self._polygon_requester.reload_polygon(camera_slot))

    def refresh_lens_reference(self, camera_slot: str):
        """Recarga la referencia de una cámara cuando la calibración terminó de escribirla."""
        cameras_tab = self.get_tab(CamerasTab)
        if cameras_tab is not None:
            self._refresh_keeping_edits(lambda: cameras_tab.refresh_reference(camera_slot))

    def build_level_inspector(self, camera_slot: str, result_source):
        """
        El inspector del diálogo de la altura, si la pestaña que la pidió sabe armarlo.

        Es opcional: una pestaña sin `build_level_inspector()` abre el diálogo sin medidas en
        pausa. Ver `ui/dialogs/level_dialog.py`.
        """
        builder = getattr(self._level_requester, "build_level_inspector", None)
        if builder is None or result_source is None:
            return None
        return builder(camera_slot, result_source)

    # ── Cambios pendientes ───────────────────────────────────────────────────

    def _reset_baseline(self):
        """Lo que muestra cada campo pasa a ser la referencia: no queda nada pendiente."""
        self._track_fields()
        self._baseline = self._read_fields()
        self._recheck()

    def _refresh_keeping_edits(self, refresh: Callable[[], None]):
        """
        Corre la recarga que pidió un diálogo sin perder lo que el operador tenga editado.

        Sólo los campos que la recarga movió toman su valor nuevo como referencia: es el que
        el diálogo ya persistió. Los demás siguen comparándose con la referencia de antes.
        """
        before = self._read_fields()
        refresh()
        self._track_fields()
        for widget, value in self._read_fields().items():
            if before.get(widget, _MISSING) != value:
                self._baseline[widget] = value
        self._recheck()

    def _track_fields(self):
        """Engancha el aviso de cambio en cada campo que todavía no lo tenga."""
        for tab in self._tabs:
            for widget in _iter_fields(tab):
                if widget.property(_TRACKED_PROPERTY):
                    continue
                widget.setProperty(_TRACKED_PROPERTY, True)
                _get_change_signal(widget).connect(self._schedule_recheck)

    def _read_fields(self) -> dict[QWidget, object]:
        return {widget: _read_value(widget) for tab in self._tabs for widget in _iter_fields(tab)}

    def _schedule_recheck(self, *_):
        # Un `load()` mueve decenas de campos de una vez: se revisa una sola vez, al final.
        if not self._is_recheck_scheduled:
            self._is_recheck_scheduled = True
            QTimer.singleShot(0, self._recheck)

    def _recheck(self):
        self._is_recheck_scheduled = False
        titles = [tr(tab.TITLE_KEY) for tab in self._tabs if self._has_changes(tab)]
        if titles != self._pending_titles:
            self._pending_titles = titles
            self.pending_changed.emit(list(titles))

    def _has_changes(self, tab: AbstractConfigTab) -> bool:
        return any(self._baseline.get(widget, value) != value
                   for widget in _iter_fields(tab)
                   for value in (_read_value(widget),))

    # ── Internos ─────────────────────────────────────────────────────────────

    def _on_roi_requested(self, tab, camera_slot: str, config_prefix: str, title_key: str):
        """Recuerda quién pidió el rectángulo y reenvía el pedido tal cual."""
        self._roi_requester = tab
        self.open_roi_requested.emit(camera_slot, config_prefix, title_key)

    def _on_regions_requested(self, tab, camera_slot: str, pipeline_slot: str):
        """Recuerda quién pidió las regiones y reenvía el pedido tal cual."""
        self._regions_requester = tab
        self.open_regions_requested.emit(camera_slot, pipeline_slot)

    def _on_level_requested(self, tab, camera_slot: str, config_key: str, title_key: str):
        """Recuerda quién pidió la altura y reenvía el pedido tal cual."""
        self._level_requester = tab
        self.open_level_requested.emit(camera_slot, config_key, title_key)

    def _on_polygon_requested(self, tab, camera_slot: str, config_key: str, title_key: str):
        """Recuerda quién pidió la zona y reenvía el pedido tal cual."""
        self._polygon_requester = tab
        self.open_polygon_requested.emit(camera_slot, config_key, title_key)


_MISSING = object()
_TRACKED_PROPERTY = "_pending_tracked"   # el campo ya avisa sus cambios


def _iter_fields(tab: QWidget) -> list[QWidget]:
    """
    Los campos editables de la pestaña. El `QLineEdit` de adentro de un spin o de un combo
    no cuenta aparte: su valor ya lo da el widget que lo contiene.
    """
    fields = []
    for widget in tab.findChildren(QWidget):
        if isinstance(widget, (QSpinBox, QDoubleSpinBox, QComboBox, QCheckBox)):
            fields.append(widget)
        elif isinstance(widget, QLineEdit) and not widget.isReadOnly() \
                and not isinstance(widget.parentWidget(), (QAbstractSpinBox, QComboBox)):
            fields.append(widget)
    return fields


def _read_value(widget: QWidget) -> object:
    if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
        return widget.value()
    if isinstance(widget, QComboBox):
        return widget.currentText(), widget.currentData()
    if isinstance(widget, QCheckBox):
        return widget.isChecked()
    return widget.text()


def _get_change_signal(widget: QWidget):
    if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
        return widget.valueChanged
    if isinstance(widget, QComboBox):
        return widget.currentIndexChanged
    if isinstance(widget, QCheckBox):
        return widget.toggled
    return widget.textChanged
