"""
Tests de la tabla de eventos: el orden, el tope FIFO y el filtro, y que una línea nueva
no reconstruya la tabla entera.

Construye widgets, así que necesita un `QApplication` sin pantalla —plataforma
`offscreen`—, igual que `test_process_tab.py`.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                              # noqa: E402

from ui.widgets.log_table import EventLogTable                          # noqa: E402

_MESSAGE_COLUMN = 2     # sin la columna de módulo: hora, nivel, mensaje


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


def _messages(table: EventLogTable) -> list:
    return [table.item(row, _MESSAGE_COLUMN).text() for row in range(table.rowCount())]


class TestAppend:
    def test_the_newest_line_is_on_top(self):
        table = EventLogTable(max_rows=5)
        for message in ("a", "b", "c"):
            table.append("INFO", message)
        assert _messages(table) == ["c", "b", "a"]

    def test_a_full_table_drops_the_oldest_line(self):
        table = EventLogTable(max_rows=3)
        for message in ("a", "b", "c", "d"):
            table.append("INFO", message)
        assert _messages(table) == ["d", "c", "b"]

    def test_a_new_line_does_not_rebuild_the_others(self):
        """Reconstruir por línea eran cientos de items por evento en el hilo de la GUI."""
        table = EventLogTable(max_rows=5)
        table.append("INFO", "a")
        first_item = table.item(0, _MESSAGE_COLUMN)
        table.append("INFO", "b")
        assert table.item(1, _MESSAGE_COLUMN) is first_item


class TestFilter:
    def test_a_filtered_out_line_is_kept_but_not_shown(self):
        table = EventLogTable(max_rows=5)
        table.set_filter("ERROR")
        table.append("INFO", "info")
        table.append("ERROR", "error")
        assert _messages(table) == ["error"]
        table.set_filter("")
        assert _messages(table) == ["error", "info"]

    def test_dropping_a_hidden_line_does_not_remove_a_shown_one(self):
        table = EventLogTable(max_rows=2)
        table.set_filter("ERROR")
        table.append("INFO", "info")
        table.append("ERROR", "e1")
        table.append("ERROR", "e2")
        assert _messages(table) == ["e2", "e1"]

    def test_dropping_a_shown_line_removes_it(self):
        table = EventLogTable(max_rows=2)
        table.set_filter("ERROR")
        table.append("ERROR", "e1")
        table.append("ERROR", "e2")
        table.append("ERROR", "e3")
        assert _messages(table) == ["e3", "e2"]


class TestLevels:
    def test_critical_is_filtered_with_the_errors(self):
        table = EventLogTable(max_rows=5)
        table.set_filter("ERROR")
        table.append("CRITICAL", "critical")
        table.append("INFO", "info")
        assert _messages(table) == ["critical"]

    def test_the_counts_follow_the_buffer(self):
        """Se cuenta lo guardado, con o sin filtro, y lo que se cae del buffer deja de contar."""
        table = EventLogTable(max_rows=2)
        table.set_filter("INFO")
        table.append("WARNING", "w")
        table.append("ERROR", "e")
        table.append("CRITICAL", "c")
        assert table.get_counts() == {"DEBUG": 0, "INFO": 0, "WARNING": 0, "ERROR": 2}

    def test_clearing_resets_the_counts(self):
        table = EventLogTable(max_rows=5)
        table.append("ERROR", "e")
        table.clear_log()
        assert table.get_counts()["ERROR"] == 0


class TestSearch:
    def test_it_matches_the_message_or_the_module_ignoring_case(self):
        table = EventLogTable(max_rows=5, show_module=True)
        table.append("INFO", "Puerto serie abierto", "server")
        table.append("INFO", "Hilo iniciado", "capture_thread")
        table.append("INFO", "Otra cosa", "main")
        table.set_search("SERIE")
        assert table.rowCount() == 1
        table.set_search("capture")
        assert table.rowCount() == 1
        table.set_search("")
        assert table.rowCount() == 3

    def test_a_new_line_that_does_not_match_is_not_shown(self):
        table = EventLogTable(max_rows=5)
        table.set_search("modbus")
        table.append("INFO", "cámara conectada")
        assert table.rowCount() == 0


class TestPause:
    def test_lines_that_arrive_paused_are_kept_and_shown_on_resume(self):
        table = EventLogTable(max_rows=5)
        table.append("INFO", "a")
        table.set_paused(True)
        table.append("INFO", "b")
        assert _messages(table) == ["a"]
        assert table.pending_count == 1
        table.set_paused(False)
        assert _messages(table) == ["b", "a"]
        assert table.pending_count == 0
