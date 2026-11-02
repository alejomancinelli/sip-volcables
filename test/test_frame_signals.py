"""Tests de las funciones de flanco que usa un fork en `_on_signals_changed()`.

Módulo puro: cada caso arma dos fotos con dicts y compara.
"""

from types import MappingProxyType

import pytest

from system import gpio_control
from system.frame_signals import (
    FrameSignals,
    gpio_fell,
    gpio_rose,
    plc_changed,
    plc_fell,
    plc_rose,
)


def _signals(gpio: dict | None = None, **plc) -> FrameSignals:
    return FrameSignals(gpio_inputs=MappingProxyType(dict(gpio or {})),
                        plc=MappingProxyType(plc))


class TestFrameSignals:
    def test_it_starts_empty(self):
        signals = FrameSignals()
        assert dict(signals.gpio_inputs) == {} and dict(signals.plc) == {}

    def test_it_cannot_be_modified(self):
        """Los hilos de captura la leen sin lock: se reemplaza entera, nunca se modifica."""
        with pytest.raises(AttributeError):
            FrameSignals().plc = {}


class TestEdges:
    def test_gpio_rises_from_zero_to_one(self):
        assert gpio_rose(_signals({1: 0}), _signals({1: 1}), 1)

    def test_gpio_falls_from_one_to_zero(self):
        assert gpio_fell(_signals({1: 1}), _signals({1: 0}), 1)

    @pytest.mark.parametrize("before, after", [(1, 1), (0, 0), (1, 0)])
    def test_no_gpio_rise_without_the_change(self, before, after):
        assert not gpio_rose(_signals({1: before}), _signals({1: after}), 1)

    def test_the_first_gpio_reading_is_not_an_edge(self):
        """Antes de la primera lectura no hay nada: arrancar con la entrada en 1 no dispara."""
        assert not gpio_rose(_signals(), _signals({1: 1}), 1)

    def test_a_read_error_in_between_is_not_an_edge(self):
        """No se sabe qué pasó en la línea mientras no se la pudo leer."""
        error = gpio_control.READ_ERROR
        assert not gpio_rose(_signals({1: error}), _signals({1: 1}), 1)
        assert not gpio_fell(_signals({1: error}), _signals({1: 0}), 1)

    def test_another_channel_is_not_an_edge(self):
        assert not gpio_rose(_signals({1: 0, 2: 0}), _signals({1: 0, 2: 1}), 1)

    def test_a_plc_variable_rises_and_falls(self):
        off, on = _signals(process_ok=False), _signals(process_ok=True)
        assert plc_rose(off, on, "process_ok") and not plc_rose(on, off, "process_ok")
        assert plc_fell(on, off, "process_ok") and not plc_fell(off, on, "process_ok")

    def test_a_number_rises_from_zero(self):
        assert plc_rose(_signals(level=0), _signals(level=3), "level")
        assert not plc_rose(_signals(level=2), _signals(level=3), "level")

    def test_a_counter_change_is_read_with_plc_changed(self):
        assert plc_changed(_signals(seq=6), _signals(seq=7), "seq")
        assert not plc_changed(_signals(seq=7), _signals(seq=7), "seq")

    def test_a_counter_that_wraps_around_still_changed(self):
        """El PLC cuenta 1-65535 y vuelve a 1."""
        assert plc_changed(_signals(seq=65535), _signals(seq=1), "seq")
