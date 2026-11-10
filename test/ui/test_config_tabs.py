"""
Tests de las pestañas de configuración: qué cuenta como un cambio sin guardar y cuándo deja de
contar, y la barra de guardar que se muestra según eso.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Signal                                      # noqa: E402
from PySide6.QtWidgets import (                                        # noqa: E402
    QApplication, QCheckBox, QComboBox, QLabel, QLineEdit, QSpinBox, QVBoxLayout,
)

from ui.strings import tr                                              # noqa: E402
from ui.views import config_tabs as config_tabs_module                 # noqa: E402
from ui.views.config.abstract_tab import AbstractConfigTab             # noqa: E402
from ui.views.config_tabs import ConfigTabs                            # noqa: E402
from ui.views.page_view import PageView                                # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture(autouse=True)
def silent_message_boxes(monkeypatch):
    """`save()` avisa con un cartel modal: en un test bloquearía la corrida."""
    monkeypatch.setattr(config_tabs_module.QMessageBox, "information", lambda *_: None)
    monkeypatch.setattr(config_tabs_module.QMessageBox, "warning", lambda *_: None)


class _MockConfig:
    """ConfigManager mínimo con `get`, `set` y un `save` que siempre funciona."""

    def __init__(self, values: dict):
        self._values = dict(values)
        self.save_count = 0

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)

    def set(self, key: str, value: object):
        self._values[key] = value

    def save(self) -> bool:
        self.save_count += 1
        return True


class _FieldsTab(AbstractConfigTab):
    """Una pestaña con un campo de cada tipo, y una altura que escribe su propio diálogo."""

    TITLE_KEY = "tab_process"
    open_level_requested = Signal(str, str, str)

    def __init__(self, config_manager, parent=None):
        super().__init__(config_manager, parent)
        self.name = QLineEdit()
        self.count = QSpinBox()
        self.count.setRange(0, 1000)
        self.enabled = QCheckBox()
        self.mode = QComboBox()
        self.mode.addItems(["a", "b"])
        self.level = QSpinBox()
        self.level.setRange(0, 1000)
        layout = QVBoxLayout(self)
        for widget in (self.name, self.count, self.enabled, self.mode, self.level):
            layout.addWidget(widget)

    def load(self):
        self.name.setText(str(self._config.get("demo.name", "")))
        self.count.setValue(int(self._config.get("demo.count", 0)))
        self.enabled.setChecked(bool(self._config.get("demo.enabled", False)))
        self.mode.setCurrentText(str(self._config.get("demo.mode", "a")))
        self.reload_level("")

    def save(self):
        self._config.set("demo.name", self.name.text())
        self._config.set("demo.count", self.count.value())
        self._config.set("demo.enabled", self.enabled.isChecked())
        self._config.set("demo.mode", self.mode.currentText())

    def reload_level(self, camera_slot: str):
        self.level.setValue(int(self._config.get("demo.level", 0)))


class _OtherTab(AbstractConfigTab):
    TITLE_KEY = "tab_cameras"

    def __init__(self, config_manager, parent=None):
        super().__init__(config_manager, parent)
        self.gain = QSpinBox()
        QVBoxLayout(self).addWidget(self.gain)

    def load(self):
        self.gain.setValue(int(self._config.get("other.gain", 1)))

    def save(self):
        self._config.set("other.gain", self.gain.value())


def _build(values: dict | None = None) -> tuple[ConfigTabs, _MockConfig, list]:
    config = _MockConfig(values or {"demo.name": "uno", "demo.count": 5})
    tabs = ConfigTabs(config, (_FieldsTab, _OtherTab))
    published: list = []
    tabs.pending_changed.connect(published.append)
    return tabs, config, published


def _settle():
    """El aviso se junta y sale al volver al loop de Qt: un `load()` mueve muchos campos."""
    QApplication.processEvents()


class TestPendingChanges:
    def test_nothing_is_pending_after_loading(self):
        tabs, _, _ = _build()
        _settle()
        assert tabs.get_pending_titles() == []

    def test_a_default_shown_for_a_missing_key_is_not_a_change(self):
        """`demo.enabled` no está en el config: la pestaña muestra su default, nadie lo editó."""
        tabs, _, _ = _build()
        _settle()
        assert tabs.get_pending_titles() == []

    @pytest.mark.parametrize("edit", [
        lambda tab: tab.name.setText("dos"),
        lambda tab: tab.count.setValue(6),
        lambda tab: tab.enabled.setChecked(True),
        lambda tab: tab.mode.setCurrentText("b"),
    ])
    def test_editing_any_kind_of_field_is_a_change(self, edit):
        tabs, _, _ = _build()
        edit(tabs.get_tab(_FieldsTab))
        _settle()
        assert tabs.get_pending_titles() == [tr("tab_process")]

    def test_only_the_tabs_that_changed_are_named(self):
        tabs, _, _ = _build()
        tabs.get_tab(_OtherTab).gain.setValue(3)
        _settle()
        assert tabs.get_pending_titles() == [tr("tab_cameras")]

    def test_putting_a_field_back_clears_the_change(self):
        tabs, _, _ = _build()
        field = tabs.get_tab(_FieldsTab).count
        field.setValue(6)
        _settle()
        field.setValue(5)
        _settle()
        assert tabs.get_pending_titles() == []

    def test_the_change_is_published_once_when_it_appears_and_once_when_it_goes(self):
        tabs, _, published = _build()
        field = tabs.get_tab(_FieldsTab).count
        field.setValue(6)
        field.setValue(7)
        _settle()
        field.setValue(5)
        _settle()
        assert published == [[tr("tab_process")], []]

    def test_discarding_clears_the_changes(self):
        tabs, _, _ = _build()
        tabs.get_tab(_FieldsTab).count.setValue(6)
        _settle()
        tabs.reload()
        _settle()
        assert tabs.get_pending_titles() == []

    def test_saving_clears_the_changes(self):
        tabs, config, _ = _build()
        tabs.get_tab(_FieldsTab).count.setValue(6)
        _settle()
        tabs.save(None)
        _settle()
        assert config.save_count == 1 and tabs.get_pending_titles() == []


class TestDialogRefresh:
    """Lo que escribe un diálogo ya está en el archivo: recargarlo no es un cambio pendiente."""

    def _request_level(self, tabs: ConfigTabs):
        tabs.get_tab(_FieldsTab).open_level_requested.emit("camera_1", "demo.level", "")

    def test_a_value_the_dialog_persisted_is_not_pending(self):
        tabs, config, _ = _build()
        self._request_level(tabs)
        config.set("demo.level", 265)
        tabs.refresh_level_fields("camera_1")
        _settle()
        assert tabs.get_tab(_FieldsTab).level.value() == 265
        assert tabs.get_pending_titles() == []

    def test_an_edit_made_before_the_dialog_stays_pending(self):
        tabs, config, _ = _build()
        tabs.get_tab(_FieldsTab).count.setValue(6)
        self._request_level(tabs)
        config.set("demo.level", 265)
        tabs.refresh_level_fields("camera_1")
        _settle()
        assert tabs.get_pending_titles() == [tr("tab_process")]


class TestSaveBar:
    def _page(self) -> PageView:
        page = PageView("Proceso", tabs=[_OtherTab(_MockConfig({}))], can_save=True)
        page.resize(1200, 800)
        page.show()
        return page

    def test_it_starts_hidden(self):
        assert not self._page().is_save_bar_visible

    def test_it_appears_with_a_change_and_names_the_tabs(self):
        page = self._page()
        page.set_pending(["Proceso", "Cámaras"])
        assert page.is_save_bar_visible
        assert page.findChild(QLabel, "saveBarMessage").text() == \
            tr("save_bar_message").format(tabs="Proceso, Cámaras")

    def test_it_hides_again_when_nothing_is_pending(self):
        page = self._page()
        page.set_pending(["Proceso"])
        page.set_pending([])
        assert not page.is_save_bar_visible

    def test_a_page_that_does_not_edit_has_no_bar(self):
        page = PageView("Logs", tabs=[], can_save=False)
        page.set_pending(["Proceso"])
        assert not page.is_save_bar_visible
