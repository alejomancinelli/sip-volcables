"""
Tests de la tabla de navegación: que cada pestaña tenga una sola página y que todo lo que
nombra la tabla exista.

No construye widgets: importa las clases de pestaña, que no necesitan un `QApplication`
para importarse.
"""

import os

from ui import navigation
from ui.strings import _TEXTS
from ui.views.config.abstract_tab import AbstractConfigTab
from ui.views.config.modbus_tab import ModbusTab as ModbusConfigTab
from ui.views.diagnostics.abstract_tab import AbstractDiagnosticsTab
from ui.views.diagnostics.modbus_stats_tab import ModbusStatsTab
from ui.views.diagnostics.modbus_tab import ModbusTab as ModbusRegistersTab

_ICONS_DIR = os.path.join(os.path.dirname(navigation.__file__), "icons", "tabler")


class TestPages:
    def test_page_keys_are_unique(self):
        keys = [page.key for page in navigation.get_pages()]
        assert len(keys) == len(set(keys))

    def test_each_tab_appears_in_a_single_page(self):
        """Una pestaña es un solo widget: en dos páginas, la segunda se la llevaría."""
        tabs = [tab_class for page in navigation.get_pages() for tab_class in page.tabs]
        assert len(tabs) == len(set(tabs))

    def test_every_tab_is_a_config_or_a_diagnostics_tab(self):
        """Si no es de ninguna de las dos, nadie la crea y la página se arma sin ella."""
        for page in navigation.get_pages():
            for tab_class in page.tabs:
                assert issubclass(tab_class, (AbstractConfigTab, AbstractDiagnosticsTab)), \
                    f"{tab_class.__name__} en la página '{page.key}'"

    def test_the_home_page_is_first_and_built_by_the_window(self):
        """Es la que se ve al abrir: la primera de la barra, sin pestañas."""
        first = navigation.get_pages()[0]
        assert first.key == navigation.PAGE_HOME
        assert first.tabs == ()

    def test_the_monitor_and_gpio_pages_are_built_by_the_window(self):
        pages = {page.key: page for page in navigation.get_pages()}
        assert pages[navigation.PAGE_MONITOR].tabs == ()
        assert pages[navigation.PAGE_GPIO].tabs == ()

    def test_the_modbus_page_shows_whether_it_works_before_how_it_is_set(self):
        """Es el canal con el PLC: primero las estadísticas, después los registros, al final
        la configuración."""
        assert navigation.get_page("modbus").tabs == (
            ModbusStatsTab, ModbusRegistersTab, ModbusConfigTab)


class TestNamedResources:
    def test_every_title_and_group_has_a_text(self):
        keys = [group_key for group_key, _ in navigation.NAVIGATION]
        keys += [page.title_key for page in navigation.get_pages()]
        missing = [key for key in keys if key not in _TEXTS]
        assert not missing

    def test_every_page_description_has_a_text(self):
        missing = [page.description_key for page in navigation.get_pages()
                   if page.description_key and page.description_key not in _TEXTS]
        assert not missing

    def test_every_page_is_found_with_its_group(self):
        for group_key, pages in navigation.NAVIGATION:
            for page in pages:
                assert navigation.get_page(page.key) is page
                assert navigation.get_group_key(page.key) == group_key

    def test_every_tab_title_has_a_text(self):
        missing = [tab_class.TITLE_KEY for page in navigation.get_pages()
                   for tab_class in page.tabs if tab_class.TITLE_KEY not in _TEXTS]
        assert not missing

    def test_every_icon_is_a_bundled_file(self):
        missing = [page.icon for page in navigation.get_pages()
                   if not os.path.isfile(os.path.join(_ICONS_DIR, f"{page.icon}.svg"))]
        assert not missing


class TestTabClasses:
    def test_config_tabs_are_split_from_diagnostics_tabs(self):
        config_tabs = navigation.get_tab_classes(AbstractConfigTab)
        diagnostics_tabs = navigation.get_tab_classes(AbstractDiagnosticsTab)
        assert config_tabs and diagnostics_tabs
        assert not set(config_tabs) & set(diagnostics_tabs)
        assert all(issubclass(tab_class, AbstractConfigTab) for tab_class in config_tabs)
