"""Tests del catálogo de palabras de estado: qué bits devuelve para decodificar una palabra.

Modulo puro: se pide una palabra por nombre y se comparan tuplas.
"""

from system.formats import camera_health, catalog, com_status, gpio_status


class TestListBits:
    def test_an_unknown_word_is_none(self):
        assert catalog.list_bits("no_existe", 0) is None

    def test_every_defined_bit_is_listed_even_when_off(self):
        bits = catalog.list_bits("com_status", 0)
        assert [bit.index for bit in bits] == sorted(com_status.DESCRIPTIONS)
        assert not any(bit.is_set for bit in bits)

    def test_a_set_bit_is_marked_and_described(self):
        word = 1 << camera_health.BIT_DIRTY_LENS
        dirty = next(bit for bit in catalog.list_bits("camera_health", word)
                     if bit.index == camera_health.BIT_DIRTY_LENS)
        assert dirty.is_set
        assert dirty.description == camera_health.DESCRIPTIONS[camera_health.BIT_DIRTY_LENS]

    def test_a_set_bit_without_a_meaning_is_listed_without_a_description(self):
        bits = catalog.list_bits("com_status", 1 << 12)
        undefined = [bit for bit in bits if bit.index == 12]
        assert undefined == [catalog.Bit(12, "", True)]

    def test_an_unset_bit_without_a_meaning_is_left_out(self):
        assert all(bit.index in com_status.DESCRIPTIONS
                   for bit in catalog.list_bits("com_status", 0))


class TestGpioWords:
    def test_the_input_word_has_a_state_and_an_error_bit_per_channel(self):
        indexes = {bit.index for bit in catalog.list_bits("gpio_inputs", 0)}
        for channel in range(gpio_status.MAX_CHANNELS):
            assert gpio_status.STATE_SHIFT + channel in indexes
            assert gpio_status.ERROR_SHIFT + channel in indexes
        assert gpio_status.BIT_UNAVAILABLE in indexes

    def test_the_output_word_has_no_error_bits(self):
        indexes = {bit.index for bit in catalog.list_bits("gpio_outputs", 0)}
        assert not any(gpio_status.ERROR_SHIFT + channel in indexes
                       for channel in range(gpio_status.MAX_CHANNELS))

    def test_a_packed_input_word_decodes_to_the_channel_that_was_on(self):
        word = gpio_status.pack_inputs({2: 1})
        on = [bit.index for bit in catalog.list_bits("gpio_inputs", word) if bit.is_set]
        assert on == [gpio_status.STATE_SHIFT + 1]
