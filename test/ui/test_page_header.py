"""
Tests del encabezado de página: el rótulo con su filete, el título y la descripción opcional.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QWidget            # noqa: E402

from ui.widgets.page_header import PageHeader                          # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


class TestPageHeader:
    def test_the_eyebrow_is_in_capitals_next_to_its_bar(self):
        header = PageHeader("Proceso", eyebrow="Operación")
        assert header.findChild(QLabel, "pageEyebrow").text() == "OPERACIÓN"
        assert header.findChild(QWidget, "pageEyebrowBar") is not None

    def test_without_an_eyebrow_there_is_neither_label_nor_bar(self):
        header = PageHeader("Proceso")
        assert header.findChild(QLabel, "pageEyebrow") is None
        assert header.findChild(QWidget, "pageEyebrowBar") is None

    def test_the_title_is_shown_as_given(self):
        assert PageHeader("Proceso").findChild(QLabel, "viewTitle").text() == "Proceso"

    def test_a_two_line_description_does_not_squash_the_title(self):
        """El alto se mide con el tope de la descripción, no con el ancho de la página."""
        header = PageHeader("Inferencia", eyebrow="Visión",
                            description="Modelos, pipelines y regiones de cada cámara, y las "
                                        "imágenes que se guardan para reentrenar.")
        width_px = 1036
        header.resize(width_px, header.heightForWidth(width_px))
        header.show()
        QApplication.processEvents()
        title = header.findChild(QLabel, "viewTitle")
        description = header.findChild(QLabel, "pageDescription")
        assert title.height() >= title.sizeHint().height()
        assert description.height() >= description.heightForWidth(description.width())

    def test_the_description_is_optional(self):
        assert PageHeader("Proceso").findChild(QLabel, "pageDescription") is None
        header = PageHeader("Proceso", description="Lo que se mide.")
        assert header.findChild(QLabel, "pageDescription").text() == "Lo que se mide."
