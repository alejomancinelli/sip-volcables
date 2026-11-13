"""
Tests de la paleta: que los dos temas definan lo mismo.

`ui/theme.py` importa sólo `QColor`, que no necesita un `QApplication` ni una pantalla,
así que esto corre en la misma suite sin GUI que todo lo demás. Lo que se pinta con
widgets —y hace falta una ventana para mirarlo— no se prueba acá.
"""

from ui import theme


class TestPaletteKeys:
    """
    Las dos paletas declaran las mismas claves.

    Es lo que promete el docstring del módulo, y el modo en que se rompe es silencioso:
    un widget que pide un rol que sólo existe en el tema oscuro sale magenta en el claro
    —`get_color()` avisa, pero recién cuando alguien cambia de tema y mira.
    """

    def test_both_themes_declare_the_same_roles(self):
        assert set(theme.get_palette(True)) == set(theme.get_palette(False))

    def test_no_role_is_left_empty(self):
        for dark in (True, False):
            palette = theme.get_palette(dark)
            assert all(palette.values()), f"rol vacío en el tema {'oscuro' if dark else 'claro'}"


class TestFooterAlert:
    """
    El rol del footer sin licencia válida.

    La barra entera se pinta con esto mientras `should_report_invalid()` lo pida. Es un
    rol de paleta y no un color inline porque `ui/theme.py` es el único dueño de los
    colores de la UI.
    """

    def test_the_alert_role_exists_in_both_themes(self):
        for dark in (True, False):
            assert theme.get_palette(dark)["footer_alert"]

    def test_it_is_not_the_same_colour_as_the_normal_footer(self):
        """Si fueran iguales la alerta no se vería, y el test de arriba pasaría igual."""
        for dark in (True, False):
            palette = theme.get_palette(dark)
            assert palette["footer_alert"] != palette["footer_background"]

    def test_the_flash_colour_stays_distinct_from_the_alert(self):
        """
        El destello del mensaje interpola hacia el fondo, que sin licencia es el rojo.
        Con el mismo color de los dos lados no habría destello que ver.
        """
        for dark in (True, False):
            palette = theme.get_palette(dark)
            assert palette["footer_flash"] != palette["footer_alert"]


class TestStylesheetTokens:
    """
    La hoja de estilos es una plantilla para los dos temas, y sus colores son tokens.

    Lo que antes rompía en silencio —una regla en un tema y no en el otro— ahora no puede
    pasar; lo que sí puede pasar es un token que la plantilla nombra y un tema no define,
    y eso deja a Qt descartando la regla sin decir nada.
    """

    def test_both_themes_declare_the_same_tokens(self):
        assert set(theme.get_tokens(True)) == set(theme.get_tokens(False))

    def test_the_stylesheet_leaves_no_token_unreplaced_in_either_theme(self):
        for dark in (True, False):
            stylesheet = theme.load_stylesheet(dark)
            assert stylesheet, f"hoja vacía en el tema {'oscuro' if dark else 'claro'}"
            assert "$" not in stylesheet

    def test_the_themes_render_different_stylesheets(self):
        """Si los dos temas dieran la misma hoja, el de arriba pasaría igual."""
        assert theme.load_stylesheet(True) != theme.load_stylesheet(False)

    def test_a_palette_role_can_be_used_as_a_token(self):
        """Un color que pinta el QSS y uno que se dibuja por código salen del mismo valor."""
        rendered = theme.render_stylesheet("QLabel { color: $video_background; }", True)
        assert theme.get_palette(True)["video_background"] in rendered

    def test_an_unknown_token_keeps_the_rest_of_the_sheet(self):
        rendered = theme.render_stylesheet(
            "QLabel { color: $no_such_token; }\nQWidget { color: $foreground; }", False)
        assert "$no_such_token" in rendered
        assert theme.get_tokens(False)["foreground"] in rendered
