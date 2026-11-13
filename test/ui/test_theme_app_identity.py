"""
Tests del ícono de la aplicación en `ui/theme.py`: el que declara un fork en `ui/icons/app/`,
y qué pasa cuando no declara ninguno.

Aparte de `test_theme.py` porque rasterizar necesita un `QApplication` —sin pantalla,
plataforma `offscreen`— y aquél corre sin ninguno.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication                              # noqa: E402

from ui import theme                                                    # noqa: E402

_BLUE_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
             '<rect width="10" height="10" fill="#0000ff"/></svg>')
_RED_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
            '<rect width="10" height="10" fill="#ff0000"/></svg>')


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def app_dir(tmp_path, monkeypatch):
    """Una carpeta de identidad vacía, en lugar de la del repo."""
    monkeypatch.setattr(theme, "_APP_ICONS_DIR", str(tmp_path))
    return tmp_path


def _center_rgb(pixmap) -> tuple:
    color = pixmap.toImage().pixelColor(pixmap.width() // 2, pixmap.height() // 2)
    return color.red(), color.green(), color.blue()


class TestAppIcon:
    def test_without_one_the_icon_is_empty(self, app_dir):
        assert theme.get_app_icon().isNull()
        assert theme.render_app_icon(52) is None

    def test_it_comes_in_every_size(self, app_dir):
        (app_dir / "icon.svg").write_text(_BLUE_SVG)
        sizes = {size.width() for size in theme.get_app_icon().availableSizes()}
        assert {16, 24, 32, 256} <= sizes

    def test_the_small_sizes_use_the_simplified_drawing(self, app_dir):
        (app_dir / "icon.svg").write_text(_BLUE_SVG)
        (app_dir / "icon_16.svg").write_text(_RED_SVG)
        assert _center_rgb(theme.render_app_icon(16)) == (255, 0, 0)
        assert _center_rgb(theme.render_app_icon(52)) == (0, 0, 255)

    def test_without_the_simplified_drawing_the_small_sizes_use_the_big_one(self, app_dir):
        (app_dir / "icon.svg").write_text(_BLUE_SVG)
        assert _center_rgb(theme.render_app_icon(16)) == (0, 0, 255)

    def test_an_unreadable_icon_is_none(self, app_dir):
        (app_dir / "icon.svg").write_text("esto no es un svg")
        assert theme.render_app_icon(52) is None


class TestRepoIcon:
    def test_the_icon_of_this_repo_is_readable(self):
        """El de SIP Volcables: un SVG que QtSvg no entiende dejaría la ventana sin ícono."""
        assert theme.render_app_icon(256) is not None
        assert theme.render_app_icon(16) is not None
