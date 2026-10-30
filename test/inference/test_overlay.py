"""Tests del dibujado: qué respeta cada flag, qué colores usa y que el frame de cámara
nunca se toque.

Se verifica que el dibujado modifique los píxeles que corresponden, no cómo queda: el
aspecto no es afirmable, la mutación sí.
"""

import colorsys

import numpy as np

from system.inference import overlay
from system.inference.result import (
    ROI_STATE_NOK, ROI_STATE_OK, ROI_STATE_WARNING, Detection, InferenceResult,
)

_NO_EXTRAS = {"draw_timestamp": False, "draw_roi": False}


def _frame(level: int = 128) -> np.ndarray:
    return np.full((120, 200, 3), level, np.uint8)


def _detection(**overrides) -> Detection:
    fields = {"class_index": 0, "class_name": "grain", "confidence_pct": 90.0,
              "bbox_px": (40, 40, 90, 90)}
    fields.update(overrides)
    return Detection(**fields)


def _result(**overrides) -> InferenceResult:
    fields = {"camera_slot": "camera_1", "source_bgr": _frame(),
              "detections": [_detection()], "inference_time_ms": 12.0,
              "confidence_pct": 90.0}
    fields.update(overrides)
    return InferenceResult(**fields)


def _changed_pixels(before: np.ndarray, after: np.ndarray) -> int:
    return int(np.count_nonzero(np.any(before != after, axis=2)))


class TestClassColors:
    def test_the_palette_cycles(self):
        first = overlay.get_class_color_bgr(0)
        assert overlay.get_class_color_bgr(len(overlay.DEFAULT_CLASS_COLORS_BGR)) == first

    def test_two_classes_get_different_colors(self):
        assert overlay.get_class_color_bgr(0) != overlay.get_class_color_bgr(1)

    def test_the_config_palette_wins(self):
        assert overlay.get_class_color_bgr(0, ((1, 2, 3),)) == (1, 2, 3)


class TestAnnotate:
    def test_it_returns_a_new_frame(self):
        result = _result()
        annotated = overlay.annotate(result)
        assert annotated is not None
        assert annotated is not result.source_bgr

    def test_the_camera_frame_is_never_touched(self):
        result = _result()
        overlay.annotate(result)
        assert np.array_equal(result.source_bgr, _frame())

    def test_without_a_frame_there_is_nothing_to_annotate(self):
        assert overlay.annotate(_result(source_bgr=None)) is None

    def test_a_gray_frame_comes_back_in_color(self):
        result = _result(source_bgr=np.full((120, 200), 128, np.uint8))
        assert overlay.annotate(result).shape == (120, 200, 3)

    def test_the_box_is_drawn(self):
        options = overlay.OverlayOptions(draw_labels=False, **_NO_EXTRAS)
        annotated = overlay.annotate(_result(), options)
        assert _changed_pixels(_frame(), annotated) > 0

    def test_boxes_can_be_turned_off(self):
        options = overlay.OverlayOptions(draw_boxes=False, draw_labels=False, **_NO_EXTRAS)
        assert _changed_pixels(_frame(), overlay.annotate(_result(), options)) == 0

    def test_a_detection_outside_the_frame_is_skipped(self):
        options = overlay.OverlayOptions(draw_labels=False, **_NO_EXTRAS)
        result = _result(detections=[_detection(bbox_px=(500, 500, 600, 600))])
        assert _changed_pixels(_frame(), overlay.annotate(result, options)) == 0

    def test_the_label_adds_pixels_over_the_box(self):
        boxes_only = overlay.OverlayOptions(draw_labels=False, **_NO_EXTRAS)
        with_label = overlay.OverlayOptions(draw_labels=True, **_NO_EXTRAS)
        assert (_changed_pixels(_frame(), overlay.annotate(_result(), with_label))
                > _changed_pixels(_frame(), overlay.annotate(_result(), boxes_only)))

    def test_the_mask_tints_the_inside_of_the_box(self):
        options = overlay.OverlayOptions(draw_boxes=False, draw_labels=False, **_NO_EXTRAS)
        result = _result(detections=[_detection(mask=np.ones((50, 50), np.uint8))])
        assert _changed_pixels(_frame(), overlay.annotate(result, options)) == 50 * 50

    def test_masks_can_be_turned_off(self):
        options = overlay.OverlayOptions(draw_boxes=False, draw_labels=False,
                                         draw_masks=False, **_NO_EXTRAS)
        result = _result(detections=[_detection(mask=np.ones((50, 50), np.uint8))])
        assert _changed_pixels(_frame(), overlay.annotate(result, options)) == 0

    def test_a_mask_that_does_not_match_its_bbox_is_ignored(self):
        """Contra el borde del frame el bbox se recorta y la máscara ya no encaja."""
        options = overlay.OverlayOptions(draw_boxes=False, draw_labels=False, **_NO_EXTRAS)
        result = _result(detections=[_detection(bbox_px=(180, 100, 230, 150),
                                               mask=np.ones((50, 50), np.uint8))])
        assert _changed_pixels(_frame(), overlay.annotate(result, options)) == 0

    def test_the_roi_is_marked(self):
        options = overlay.OverlayOptions(draw_boxes=False, draw_labels=False,
                                         draw_timestamp=False)
        result = _result(detections=[], roi_px=(10, 10, 100, 60))
        assert _changed_pixels(_frame(), overlay.annotate(result, options)) > 0


class TestDrawLine:
    def test_it_draws_across_the_frame(self):
        """Cruza el ancho completo; el antialiasing tiñe además las filas vecinas."""
        frame = _frame()
        overlay.draw_line(frame, (0, 60), (200, 60), (0, 0, 255))
        assert np.all(frame[60] != 128)
        assert np.all(frame[57] == 128) and np.all(frame[63] == 128)

    def test_the_label_adds_pixels(self):
        plain, labelled = _frame(), _frame()
        overlay.draw_line(plain, (0, 60), (200, 60), (0, 0, 255))
        overlay.draw_line(labelled, (0, 60), (200, 60), (0, 0, 255), label="MAX LOAD")
        assert _changed_pixels(_frame(), labelled) > _changed_pixels(_frame(), plain)

    def test_an_empty_frame_is_ignored(self):
        overlay.draw_line(np.zeros((0, 0, 3), np.uint8), (0, 0), (1, 1), (0, 0, 255))


class TestMaskStyle:
    """
    Contorno y relleno son dueños de píxeles distintos.

    Se afirma sobre dos píxeles conocidos de una máscara llena en el bbox (40,40)-(90,90):
    el borde y el interior. Sin cajas ni etiquetas, que pintan encima del borde y taparían
    lo que se está midiendo.
    """

    _BORDER = (40, 40)
    _INSIDE = (60, 60)

    def _annotated(self, style: str) -> np.ndarray:
        detection = _detection(bbox_px=(40, 40, 90, 90), mask=np.ones((50, 50), np.uint8))
        return overlay.annotate(
            _result(detections=[detection]),
            overlay.OverlayOptions(draw_masks=True, mask_style=style,
                                   draw_boxes=False, draw_labels=False, **_NO_EXTRAS))

    def _pixel(self, style: str, at: tuple[int, int]) -> tuple:
        return tuple(int(value) for value in self._annotated(style)[at])

    def test_the_outline_leaves_the_inside_untouched(self):
        assert self._pixel(overlay.MASK_STYLE_OUTLINE, self._INSIDE) == (128, 128, 128)

    def test_the_fill_tints_the_inside_without_hiding_it(self):
        """Translúcido: ni el frame ni el color pelado de la clase."""
        inside = self._pixel(overlay.MASK_STYLE_FILL, self._INSIDE)
        assert inside not in ((128, 128, 128), overlay.get_class_color_bgr(0))

    def test_the_outline_is_the_class_color_without_blending(self):
        """Nítido a propósito: con cientos de instancias es lo único que separa una
        de la otra, y mezclado se pierde contra el relleno."""
        assert (self._pixel(overlay.MASK_STYLE_OUTLINE, self._BORDER)
                == overlay.get_class_color_bgr(0))

    def test_the_fill_alone_has_no_sharp_edge(self):
        """El borde de una máscara rellena se mezcla como el resto: si sale nítido, se
        está dibujando el contorno en un modo que no lo pidió."""
        assert (self._pixel(overlay.MASK_STYLE_FILL, self._BORDER)
                == self._pixel(overlay.MASK_STYLE_FILL, self._INSIDE))

    def test_both_draws_the_two_of_them(self):
        assert (self._pixel(overlay.MASK_STYLE_BOTH, self._BORDER)
                == self._pixel(overlay.MASK_STYLE_OUTLINE, self._BORDER))
        assert (self._pixel(overlay.MASK_STYLE_BOTH, self._INSIDE)
                == self._pixel(overlay.MASK_STYLE_FILL, self._INSIDE))


class TestFontScale:
    """`font_scale: 0` calcula el tamaño del texto a partir del ancho del frame."""

    def test_a_configured_scale_is_respected(self):
        options = overlay.OverlayOptions(font_scale=0.42)
        assert overlay.resolve_font_scale(_frame(), ["texto"], options) == 0.42

    def test_a_narrow_frame_gets_smaller_text(self):
        options = overlay.OverlayOptions(font_scale=0.0)
        lines = ["una linea larga de resumen"]
        angosto = overlay.resolve_font_scale(np.zeros((50, 120, 3), np.uint8), lines, options)
        ancho = overlay.resolve_font_scale(np.zeros((50, 900, 3), np.uint8), lines, options)
        assert angosto < ancho

    def test_it_stays_inside_the_bounds(self):
        options = overlay.OverlayOptions(font_scale=0.0)
        for width_px in (20, 120, 900, 4000):
            scale = overlay.resolve_font_scale(
                np.zeros((50, width_px, 3), np.uint8), ["x" * 40], options)
            assert overlay._AUTO_FONT_MIN <= scale <= overlay._AUTO_FONT_MAX

    def test_the_panel_fits_inside_the_frame(self):
        """
        Se mide contra el ancho menos el margen y el relleno del panel.

        El frame va angosto a propósito: más ancho la escala queda contra su tope y el
        panel entra igual, así que el test no diría nada.
        """
        frame = np.zeros((80, 200, 3), np.uint8)
        options = overlay.OverlayOptions(font_scale=0.0)
        lines = ["N12   87.3 %", "N6    12.7 %"]
        assert overlay.resolve_font_scale(frame, lines, options) < overlay._AUTO_FONT_MAX
        overlay.draw_text_panel(frame, lines, options=options)
        assert _changed_pixels(frame[:, -1:], np.zeros((80, 1, 3), np.uint8)) == 0


class TestTextPanelColours:
    """
    Un panel puede pintar cada línea de su color, y es lo que deja que el resumen de
    clases coincida con el color de las máscaras. Lo que no se declara cae en el del panel.
    """

    _LINES = ["primera", "segunda"]

    def _colours(self, frame_bgr: np.ndarray) -> set:
        return {tuple(int(c) for c in colour) for colour in frame_bgr.reshape(-1, 3)}

    def test_each_line_takes_its_own_colour(self):
        frame = _frame()
        colours = ((1, 2, 3), (4, 5, 6))
        overlay.draw_text_panel(frame, self._LINES, line_colors_bgr=colours)
        assert set(colours) <= self._colours(frame)

    def test_a_line_without_a_colour_falls_back_to_the_panel(self):
        """`None` es «no tengo color propio», no «negro»."""
        frame = _frame()
        overlay.draw_text_panel(frame, self._LINES, text_bgr=(9, 9, 9),
                                line_colors_bgr=((1, 2, 3), None))
        painted = self._colours(frame)
        assert (1, 2, 3) in painted and (9, 9, 9) in painted

    def test_fewer_colours_than_lines_is_not_an_error(self):
        frame = _frame()
        overlay.draw_text_panel(frame, self._LINES, text_bgr=(9, 9, 9),
                                line_colors_bgr=((1, 2, 3),))
        painted = self._colours(frame)
        assert (1, 2, 3) in painted and (9, 9, 9) in painted

    def test_without_colours_the_panel_is_of_one_colour(self):
        """El comportamiento de siempre: quien no los pasa no cambia de dibujo."""
        frame, plain = _frame(), _frame()
        overlay.draw_text_panel(frame, self._LINES, line_colors_bgr=())
        overlay.draw_text_panel(plain, self._LINES)
        assert np.array_equal(frame, plain)


class TestTextPanelLayout:
    """
    Los pares `(etiqueta, valor)` van en dos columnas con los valores alineados a la
    derecha, y el aire entre líneas crece con el texto.

    El texto se pinta de rojo puro sobre un frame gris: así cada píxel de texto se distingue
    del fondo del panel y del frame aunque el antialiasing lo mezcle con el negro.
    """

    _RED_BGR = (0, 0, 255)

    def _text_bands(self, frame_bgr: np.ndarray) -> list[np.ndarray]:
        """Filas con texto agrupadas por línea: cada grupo es una línea del panel."""
        is_text = ((frame_bgr[..., 2] > 64) & (frame_bgr[..., 0] == 0)
                   & (frame_bgr[..., 1] == 0))
        rows = np.flatnonzero(is_text.any(axis=1))
        bands = np.split(rows, np.flatnonzero(np.diff(rows) > 1) + 1)
        return [is_text[band] for band in bands]

    def test_the_values_end_in_the_same_column(self):
        frame = np.full((200, 600, 3), 128, np.uint8)
        overlay.draw_text_panel(frame, [("A:", "1.0%"), ("LONGER LABEL:", "100.0%")],
                                options=overlay.OverlayOptions(font_scale=1.0),
                                text_bgr=self._RED_BGR)
        bands = self._text_bands(frame)
        assert len(bands) == 2
        right_edges = [int(np.flatnonzero(band.any(axis=0)).max()) for band in bands]
        assert abs(right_edges[0] - right_edges[1]) <= 1

    def test_the_space_between_lines_grows_with_the_text(self):
        def gap_px(font_scale: float) -> int:
            frame = np.full((300, 600, 3), 128, np.uint8)
            overlay.draw_text_panel(frame, ["AB", "CD"],
                                    options=overlay.OverlayOptions(font_scale=font_scale),
                                    text_bgr=self._RED_BGR)
            rows = np.flatnonzero(np.any(
                (frame[..., 2] > 64) & (frame[..., 0] == 0) & (frame[..., 1] == 0), axis=1))
            return int(np.diff(rows).max()) - 1

        assert gap_px(2.0) > gap_px(0.5)
        assert gap_px(2.0) > overlay._LINE_PAD_PX


class TestTextThickness:
    """El grosor del trazo acompaña al tamaño: un texto grande con trazo de 1 px se ve
    pálido y roto, que es lo que pasaba después de escalarlo con el ancho del frame."""

    def test_it_grows_with_the_scale(self):
        assert (overlay.text_thickness(0.3) <= overlay.text_thickness(1.0)
                < overlay.text_thickness(2.5))

    def test_it_is_never_zero(self):
        assert overlay.text_thickness(0.0) == 1

    def test_the_default_scale_keeps_the_stroke_it_always_had(self):
        """Un fork con `font_scale` fijo tiene que seguir viéndose igual."""
        assert overlay.text_thickness(overlay.OverlayOptions().font_scale) == 1


class TestOverlappingFill:
    """
    El relleno se pinta por unión de la clase, no por instancia.

    Teñir cada máscara por separado mezcla el color tantas veces como instancias se
    superpongan: una zona con muchas detecciones encimadas sale más saturada que otra
    igual de cubierta pero con pocas, así que el color deja de decir qué clase es. Y lo
    que se ve deja de coincidir con lo que se mide, que cuenta cada píxel una sola vez.
    """

    _BOX = (40, 40, 90, 90)

    def _filled(self, class_index: int = 0):
        return _detection(class_index=class_index,
                          mask=np.ones((50, 50), np.uint8), bbox_px=self._BOX)

    def _painted(self, detections: list) -> np.ndarray:
        frame = _frame()
        overlay.draw_detections(frame, detections, options=overlay.OverlayOptions(
            draw_boxes=False, draw_labels=False,
            class_colors_bgr=((0, 0, 255), (0, 255, 0))))
        return frame

    def test_ten_stacked_instances_look_like_one(self):
        assert np.array_equal(self._painted([self._filled()]),
                              self._painted([self._filled()] * 10))

    def test_where_two_classes_overlap_the_last_one_wins(self):
        """El mismo criterio con el que se cuentan los píxeles en `metrics`."""
        both = self._painted([self._filled(0), self._filled(1)])
        alone = self._painted([self._filled(1)])
        assert np.array_equal(both, alone)

    def test_the_tint_still_reaches_the_frame(self):
        frame = self._painted([self._filled()])
        assert _changed_pixels(_frame(), frame) > 0

    def test_two_classes_side_by_side_keep_their_own_colour(self):
        left = _detection(class_index=0, mask=np.ones((20, 20), np.uint8),
                          bbox_px=(10, 10, 30, 30))
        right = _detection(class_index=1, mask=np.ones((20, 20), np.uint8),
                           bbox_px=(60, 10, 80, 30))
        frame = self._painted([left, right])
        assert frame[20, 20, 2] > frame[20, 20, 1]    # rojo a la izquierda
        assert frame[20, 70, 1] > frame[20, 70, 2]    # verde a la derecha

    def test_an_outline_is_still_drawn_per_instance(self):
        """El contorno es lo que deja ver cada instancia por separado."""
        frame = _frame()
        overlay.draw_detections(frame, [self._filled()], options=overlay.OverlayOptions(
            draw_boxes=False, draw_labels=False,
            mask_style=overlay.MASK_STYLE_OUTLINE))
        assert _changed_pixels(_frame(), frame) > 0


def _hue_deg(color_bgr: tuple) -> float:
    blue, green, red = (channel / 255 for channel in color_bgr)
    hue, _saturation, _value = colorsys.rgb_to_hsv(red, green, blue)
    return hue * 360


def _saturation(color_bgr: tuple) -> float:
    blue, green, red = (channel / 255 for channel in color_bgr)
    return colorsys.rgb_to_hsv(red, green, blue)[1]


class TestPipelineStyle:
    def test_no_color_is_green_red_or_yellow(self):
        """Esos tres son del estado: un pipeline que los usara de identidad se confundiría con un veredicto."""
        for color in overlay.PIPELINE_COLORS_BGR:
            if _saturation(color) < 0.2:       # el blanco no tiene tono
                continue
            hue = _hue_deg(color)
            assert 165 < hue < 335, f"{color} cae en el rango de rojo, amarillo o verde"

    def test_the_colors_are_different(self):
        assert len(set(overlay.PIPELINE_COLORS_BGR)) == len(overlay.PIPELINE_COLORS_BGR)

    def test_the_first_lap_is_solid(self):
        for index, color in enumerate(overlay.PIPELINE_COLORS_BGR):
            assert overlay.get_pipeline_style(index) == overlay.PipelineStyle(color, False)

    def test_the_second_lap_repeats_the_colors_dashed(self):
        count = len(overlay.PIPELINE_COLORS_BGR)
        for index in range(count):
            first = overlay.get_pipeline_style(index)
            second = overlay.get_pipeline_style(index + count)
            assert second == overlay.PipelineStyle(first.color_bgr, True)

    def test_two_pipelines_never_look_the_same(self):
        styles = {overlay.get_pipeline_style(index)
                  for index in range(2 * len(overlay.PIPELINE_COLORS_BGR))}
        assert len(styles) == 2 * len(overlay.PIPELINE_COLORS_BGR)

    def test_a_negative_index_is_the_first(self):
        assert overlay.get_pipeline_style(-3) == overlay.get_pipeline_style(0)


class TestDrawRoi:
    _BLUE = overlay.PIPELINE_COLORS_BGR[0]
    _ROI = (40, 30, 100, 60)

    def _draw(self, **option_fields) -> np.ndarray:
        frame = _frame()
        overlay.draw_roi(frame, self._ROI, options=overlay.OverlayOptions(
            roi_color_bgr=self._BLUE, **option_fields))
        return frame

    def _has_color_near(self, frame: np.ndarray, x_px: int, y_px: int) -> bool:
        window = frame[y_px - 2:y_px + 3, x_px]
        return bool(np.any(np.all(window == self._BLUE, axis=1)))

    def test_it_draws_the_border_in_the_pipeline_color(self):
        frame = self._draw()
        assert self._has_color_near(frame, 100, 90)       # borde de abajo
        assert self._has_color_near(frame, 100, 30)       # borde de arriba

    def test_it_leaves_the_inside_alone(self):
        frame = self._draw()
        assert not np.any(np.all(frame[40:80, 50:130] == self._BLUE, axis=2))

    def test_a_label_names_the_pipeline_on_the_corner(self):
        with_label = self._draw(roi_label="pipeline_1")
        without = self._draw()
        assert _changed_pixels(with_label, without) > 0

    def test_an_accented_name_is_drawn_without_its_accents(self, monkeypatch):
        """La fuente de OpenCV sólo dibuja ASCII: «ó» saldría como «??»."""
        texts = []
        real_put_text = overlay.cv2.putText
        monkeypatch.setattr(overlay.cv2, "putText",
                            lambda frame, text, *args: texts.append(text)
                            or real_put_text(frame, text, *args))
        self._draw(roi_label="Verificación de calza ñ")
        assert texts == ["Verificacion de calza n"]

    def test_without_options_it_is_the_old_grey_box_without_a_name(self):
        frame = _frame()
        overlay.draw_roi(frame, self._ROI)
        assert not np.any(np.all(frame == self._BLUE, axis=2))
        assert _changed_pixels(frame, _frame()) > 0

    def test_a_dashed_border_leaves_gaps_a_solid_one_does_not(self):
        solid = self._draw()
        dashed = self._draw(roi_is_dashed=True)
        row_solid = int(np.count_nonzero(np.all(solid[90, 40:140] == self._BLUE, axis=1)))
        row_dashed = int(np.count_nonzero(np.all(dashed[90, 40:140] == self._BLUE, axis=1)))
        assert 0 < row_dashed < row_solid

    def test_an_empty_frame_is_ignored(self):
        overlay.draw_roi(np.zeros((0, 0, 3), np.uint8), self._ROI)

    def test_annotate_draws_it_with_the_options_of_its_pipeline(self):
        options = overlay.OverlayOptions(draw_timestamp=False, roi_color_bgr=self._BLUE)
        annotated = overlay.annotate(_result(roi_px=self._ROI, detections=[]), options)
        assert self._has_color_near(annotated, 100, 90)

    def test_annotate_draws_nothing_without_a_roi(self):
        options = overlay.OverlayOptions(draw_timestamp=False, roi_color_bgr=self._BLUE)
        annotated = overlay.annotate(_result(roi_px=None, detections=[]), options)
        assert _changed_pixels(annotated, _frame()) == 0


class TestRoiState:
    """El borde del ROI se pinta por el veredicto, y un resultado inválido nunca de estado."""

    _BLUE = overlay.PIPELINE_COLORS_BGR[0]
    _ROI = (40, 30, 100, 60)

    def _annotate(self, **result_fields) -> np.ndarray:
        options = overlay.OverlayOptions(draw_timestamp=False, roi_color_bgr=self._BLUE,
                                         roi_label="p1")
        return overlay.annotate(
            _result(roi_px=self._ROI, detections=[], **result_fields), options)

    def _bottom_edge(self, frame: np.ndarray) -> list:
        """Los píxeles de la mitad del borde de abajo, lejos de la etiqueta."""
        return [tuple(pixel) for pixel in frame[88:94, 100]]

    def _has(self, frame: np.ndarray, color: tuple) -> bool:
        return color in self._bottom_edge(frame)

    def test_the_state_colors_are_green_red_and_yellow_and_none_of_a_pipeline(self):
        assert set(overlay.ROI_STATE_COLORS_BGR) == {ROI_STATE_OK, ROI_STATE_NOK, ROI_STATE_WARNING}
        assert not set(overlay.ROI_STATE_COLORS_BGR.values()) & set(overlay.PIPELINE_COLORS_BGR)

    def test_the_state_colors_are_in_the_ranges_the_pipelines_avoid(self):
        for state, expected in ((ROI_STATE_OK, (75, 165)), (ROI_STATE_WARNING, (25, 75))):
            hue = _hue_deg(overlay.ROI_STATE_COLORS_BGR[state])
            assert expected[0] < hue < expected[1], state
        red_hue = _hue_deg(overlay.ROI_STATE_COLORS_BGR[ROI_STATE_NOK])
        assert red_hue < 25 or red_hue > 335

    def test_without_a_state_the_border_is_the_pipeline_color(self):
        assert self._has(self._annotate(), self._BLUE)

    def test_each_state_paints_its_color_and_not_the_pipeline_one(self):
        for state, color in overlay.ROI_STATE_COLORS_BGR.items():
            frame = self._annotate(roi_state=state)
            assert self._has(frame, color), state
            assert not self._has(frame, self._BLUE), state

    def test_the_label_keeps_the_pipeline_color_and_says_the_verdict(self):
        """Quien no distingue el verde del rojo igual lee «NOK»."""
        painted = self._annotate(roi_state=ROI_STATE_NOK)
        plain = self._annotate()
        label_area = (slice(10, 32), slice(40, 100))
        assert not np.array_equal(painted[label_area], plain[label_area])
        assert np.any(np.all(painted[label_area] == self._BLUE, axis=2))

    def test_nok_is_thicker_than_ok(self):
        nok = self._annotate(roi_state=ROI_STATE_NOK)
        ok = self._annotate(roi_state=ROI_STATE_OK)
        count_nok = np.count_nonzero(np.all(nok[80:100, 100] == overlay.ROI_STATE_COLORS_BGR[ROI_STATE_NOK], axis=1))
        count_ok = np.count_nonzero(np.all(ok[80:100, 100] == overlay.ROI_STATE_COLORS_BGR[ROI_STATE_OK], axis=1))
        assert count_nok > count_ok

    def test_an_invalid_result_is_never_painted_with_a_state(self):
        for state in overlay.ROI_STATE_COLORS_BGR:
            frame = self._annotate(roi_state=state, is_valid=False, invalid_reason="dark_frame")
            assert not any(self._has(frame, color)
                           for color in overlay.ROI_STATE_COLORS_BGR.values()), state

    def test_an_invalid_result_has_a_dashed_grey_border_and_its_reason(self):
        invalid = self._annotate(roi_state=ROI_STATE_OK, is_valid=False,
                                 invalid_reason="dark_frame")
        solid = self._annotate()
        row = invalid[90, 40:140]
        grey = np.all(row == overlay._INVALID_ROI_BGR, axis=1)
        assert 0 < np.count_nonzero(grey) < 100          # punteado: con huecos
        assert not np.array_equal(invalid[10:32, 40:200], solid[10:32, 40:200])

    def test_an_invalid_result_without_a_reason_still_says_it_is_invalid(self):
        frame = self._annotate(is_valid=False, invalid_reason="")
        assert not self._has(frame, self._BLUE)

    def test_without_a_roi_there_is_nothing_to_paint(self):
        options = overlay.OverlayOptions(draw_timestamp=False)
        annotated = overlay.annotate(
            _result(roi_px=None, detections=[], roi_state=ROI_STATE_NOK), options)
        assert _changed_pixels(annotated, _frame()) == 0

    def test_with_draw_roi_off_the_state_is_not_drawn_either(self):
        options = overlay.OverlayOptions(draw_timestamp=False, draw_roi=False)
        annotated = overlay.annotate(
            _result(roi_px=self._ROI, detections=[], roi_state=ROI_STATE_NOK), options)
        assert _changed_pixels(annotated, _frame()) == 0

    def test_the_options_of_a_result_without_state_paint_as_before(self):
        frame = _frame()
        overlay.draw_roi(frame, self._ROI, options=overlay.OverlayOptions(
            roi_color_bgr=self._BLUE))
        assert self._has(frame, self._BLUE)

    def test_a_state_outside_the_vocabulary_is_drawn_as_none(self):
        assert self._has(self._annotate(roi_state="purple"), self._BLUE)
