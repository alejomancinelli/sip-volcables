"""
Tests de los constructores del formulario: la tarjeta de una pestaña no depende de lo que
tiene adentro, y la rueda no cambia el valor de un campo.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt                              # noqa: E402
from PySide6.QtGui import QWheelEvent                                       # noqa: E402
from PySide6.QtWidgets import (                                             # noqa: E402
    QApplication, QCheckBox, QLabel, QScrollArea, QStyleFactory, QVBoxLayout, QWidget,
)

import ui.widgets.form as form                                              # noqa: E402

_WIDE_WINDOW_PX = 1920
_NARROW_WINDOW_PX = 900
_WINDOW_HEIGHT_PX = 600


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


def _card_width(content: str, window_px: int, *, center: bool = True) -> int:
    """Ancho de la tarjeta de un contenido, mostrada en una ventana de `window_px`."""
    inner = QWidget()
    QVBoxLayout(inner).addWidget(QLabel(content))
    wrapped = form.wrap_in_card(inner, center=center, scroll=True)
    wrapped.resize(window_px, _WINDOW_HEIGHT_PX)
    wrapped.show()
    QApplication.processEvents()
    width = wrapped.findChild(QWidget, form._CARD_OBJECT_NAME).width()
    wrapped.close()
    return width


class TestCardWidth:
    def test_contents_of_different_width_give_the_same_card(self):
        """Si la tarjeta tomara el ancho del contenido, el fondo saltaría entre pestañas."""
        narrow = _card_width("Modo:", _WIDE_WINDOW_PX)
        wide = _card_width("Confianza mínima del resultado (%): " * 4, _WIDE_WINDOW_PX)
        assert narrow == wide == form._CARD_WIDTH_PX

    def test_a_narrow_window_shrinks_every_card_alike(self):
        """No es un ancho fijo: sin scroll horizontal, cortaría los campos."""
        narrow = _card_width("Modo:", _NARROW_WINDOW_PX)
        wide = _card_width("Confianza mínima del resultado (%): " * 4, _NARROW_WINDOW_PX)
        assert narrow == wide
        assert form._CARD_MIN_WIDTH_PX <= narrow < form._CARD_WIDTH_PX

    def test_a_card_that_is_not_centered_takes_the_whole_width(self):
        assert _card_width("tabla", _WIDE_WINDOW_PX, center=False) > form._CARD_WIDTH_PX


def _card_x(row_count: int) -> tuple[int, bool]:
    """Posición de la tarjeta de un contenido de `row_count` filas, en la ventana."""
    inner = QWidget()
    layout = QVBoxLayout(inner)
    for i in range(row_count):
        layout.addWidget(QLabel(f"fila {i}"))
    wrapped = form.wrap_in_card(inner, scroll=True)
    wrapped.resize(_WIDE_WINDOW_PX, _WINDOW_HEIGHT_PX)
    wrapped.show()
    QApplication.processEvents()
    card = wrapped.findChild(QWidget, form._CARD_OBJECT_NAME)
    x = card.mapTo(wrapped, card.rect().topLeft()).x()
    has_bar = wrapped.verticalScrollBar().isVisible()
    wrapped.close()
    return x, has_bar


class TestCardPosition:
    def test_the_card_does_not_move_when_the_scroll_bar_appears(self):
        short_x, short_has_bar = _card_x(2)
        long_x, long_has_bar = _card_x(200)
        assert (short_has_bar, long_has_bar) == (False, True)
        assert short_x == long_x


def _wheel(target: QWidget) -> None:
    """Una vuelta de rueda hacia abajo sobre el centro del widget, como la manda Qt."""
    center = QPointF(target.width() / 2, target.height() / 2)
    event = QWheelEvent(center, target.mapToGlobal(center), QPoint(0, 0), QPoint(0, -120),
                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(target, event)


class TestWheelGuard:
    @pytest.fixture
    def guarded(self, qt_app):
        guard = form.WheelGuard()
        qt_app.installEventFilter(guard)
        yield
        qt_app.removeEventFilter(guard)

    def test_the_wheel_does_not_change_a_spin_box(self, guarded):
        spin = form.build_spin_box(0, 100, 50)
        _wheel(spin)
        assert spin.value() == 50

    def test_the_wheel_does_not_change_a_combo_box(self, guarded):
        combo = form.build_combo_box(["a", "b", "c"], "a")
        _wheel(combo)
        assert combo.currentText() == "a"

    def test_the_wheel_scrolls_the_page_under_the_field(self, guarded):
        content = QWidget()
        layout = QVBoxLayout(content)
        spin = form.build_spin_box(0, 100, 50)
        layout.addWidget(spin)
        for _ in range(40):
            layout.addWidget(QLabel("fila"))
        area = QScrollArea()
        area.setWidget(content)
        area.resize(300, 200)
        area.show()
        QApplication.processEvents()
        _wheel(spin)
        assert area.verticalScrollBar().value() > 0
        assert spin.value() == 50

    def test_the_wheel_does_not_change_the_tab(self, guarded):
        """Pasar con la rueda por una barra dejaba editando otra cámara u otro pipeline."""
        tabs = form.build_slot_tabs()
        for label in ("uno", "dos", "tres"):
            tabs.addTab(QWidget(), label)
        tabs.resize(400, 200)
        tabs.show()
        QApplication.processEvents()
        _wheel(tabs.tabBar())
        assert tabs.currentIndex() == 0

    def test_without_the_guard_the_wheel_changes_the_tab(self):
        """Control: el evento que manda `_wheel` es uno que la barra atiende."""
        tabs = form.build_slot_tabs()
        for label in ("uno", "dos", "tres"):
            tabs.addTab(QWidget(), label)
        tabs.resize(400, 200)
        tabs.show()
        QApplication.processEvents()
        _wheel(tabs.tabBar())
        assert tabs.currentIndex() != 0

    def test_without_the_guard_the_wheel_changes_the_value(self):
        """Control: el doble del evento es el que Qt manda, no uno que el spin ignora."""
        spin = form.build_spin_box(0, 100, 50)
        _wheel(spin)
        assert spin.value() != 50



class TestCheckGrid:
    def test_checks_fill_a_row_before_going_down(self):
        checks = [QCheckBox(str(i)) for i in range(5)]
        widget = form.build_check_grid(checks, columns=4)
        grid = widget.layout()
        positions = [grid.getItemPosition(grid.indexOf(check))[:2] for check in checks]
        assert positions == [(0, 0), (0, 1), (0, 2), (0, 3), (1, 0)]


class TestSetFieldEnabled:
    def test_off_it_is_disabled_and_dimmed(self):
        spin = form.build_spin_box(0, 10, 5)
        form.set_field_enabled(spin, False)
        assert not spin.isEnabled() and spin.graphicsEffect() is not None

    def test_on_again_it_loses_the_dimming(self):
        spin = form.build_spin_box(0, 10, 5)
        form.set_field_enabled(spin, False)
        form.set_field_enabled(spin, True)
        assert spin.isEnabled() and spin.graphicsEffect() is None


class TestGeneralTab:
    def test_it_goes_last_and_marks_the_bar_for_its_tint(self):
        tabs = form.build_slot_tabs(in_card=False)
        tabs.addTab(QWidget(), "camera_1")
        index = form.add_general_tab(tabs, QWidget(), "General")
        assert index == tabs.count() - 1
        assert tabs.property("hasGeneralTab") is True

    def test_a_bar_without_it_is_not_marked(self):
        assert not form.build_slot_tabs().property("hasGeneralTab")


def _wrapped_page(label_count: int) -> QWidget:
    """Una página con textos que se parten en líneas: su alto depende del ancho."""
    page = QWidget()
    layout = QVBoxLayout(page)
    for _ in range(label_count):
        label = QLabel("un texto que se parte en varias líneas " * 4)
        label.setWordWrap(True)
        layout.addWidget(label)
    return page


def _short_and_tall_tabs(*, fit_to_current: bool):
    tabs = form.build_slot_tabs(fit_to_current=fit_to_current)
    tabs.addTab(_wrapped_page(1), "corta")
    tabs.addTab(_wrapped_page(10), "larga")
    return tabs


class TestFitToCurrent:
    _WIDTH_PX = 400

    def test_the_bar_measures_the_page_on_view(self):
        tabs = _short_and_tall_tabs(fit_to_current=True)
        short_px = tabs.heightForWidth(self._WIDTH_PX)
        tabs.setCurrentIndex(1)
        assert short_px < tabs.heightForWidth(self._WIDTH_PX)

    def test_without_it_qt_measures_the_tallest_page_even_hidden(self):
        """Es lo que obliga a la opción: con un texto que se parte, la política no alcanza."""
        tabs = _short_and_tall_tabs(fit_to_current=False)
        short_px = tabs.heightForWidth(self._WIDTH_PX)
        tabs.setCurrentIndex(1)
        assert short_px == tabs.heightForWidth(self._WIDTH_PX)


class TestWheelGuardInstall:
    """
    `install_wheel_guard()` lo pone en cada widget que la rueda cambiaría, y no en la
    aplicación: instalado ahí veía pasar cada evento de cada widget por Python.
    """

    @pytest.fixture
    def installed(self, qt_app):
        original = qt_app.style().name()
        guard = form.install_wheel_guard(qt_app)
        yield guard
        qt_app.setStyle(QStyleFactory.create(original))

    def test_a_spin_box_created_afterwards_is_guarded(self, installed):
        spin = form.build_spin_box(0, 100, 50)
        spin.show()
        _wheel(spin)
        assert spin.value() == 50

    def test_it_reaches_a_widget_under_a_style_sheet(self, installed):
        """La hoja de estilo de la ventana delega el pulido en el estilo de la aplicación."""
        window = QWidget()
        window.setStyleSheet("QSpinBox { padding: 2px; }")
        layout = QVBoxLayout(window)
        spin = form.build_spin_box(0, 100, 50)
        layout.addWidget(spin)
        window.show()
        QApplication.processEvents()
        _wheel(spin)
        assert spin.value() == 50

    def test_it_does_not_see_the_events_of_other_widgets(self, installed, monkeypatch):
        seen = []
        real_filter = form.WheelGuard.eventFilter
        monkeypatch.setattr(form.WheelGuard, "eventFilter",
                            lambda guard, watched, event: seen.append(watched)
                            or real_filter(guard, watched, event))
        label = QLabel("texto")
        label.show()
        label.setText("otro")
        QApplication.processEvents()
        assert label not in seen

    def test_installing_it_twice_keeps_one(self, installed, qt_app):
        assert form.install_wheel_guard(qt_app) is installed
