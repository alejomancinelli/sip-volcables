"""Tests de las variables que escribe el PLC: la cola, el drenado y el log de los cambios.

Sin servidor ni Qt: las escrituras entran por `on_write()` como las entregaría el
servidor, y el reloj del log es un doble.
"""

import pytest

from system.modbus.plc_variables import _LOG_PERIOD_S, PlcVariables
from system.modbus.schema import (
    PRODUCER_CONFIG,
    PRODUCER_HEALTH,
    PRODUCER_PLC,
    Reg,
    RegisterSchema,
)

# Un disparo, una variable booleana, una con rango y una con escala, más una fila de la app
# y un umbral publicado, que no son variables.
_MAP = RegisterSchema([
    Reg(81, "heartbeat", "Latido", PRODUCER_HEALTH),
    Reg(10, "max_load_y_px", "Límite", PRODUCER_CONFIG, config_key="process.max_load_y_px"),
    Reg(101, "measure_seq", "Disparo", PRODUCER_PLC),
    Reg(105, "process_ok", "Proceso OK", PRODUCER_PLC, is_bool=True),
    Reg(106, "product_code", "Producto", PRODUCER_PLC, min_value=0, max_value=999),
    Reg(108, "belt_speed_mps", "Velocidad", PRODUCER_PLC, unit="m/s", scale=100,
        min_value=0, max_value=10),
])


class _Clock:
    """Reloj que avanza sólo cuando el test lo dice."""

    def __init__(self):
        self.now_s = 1000.0

    def __call__(self) -> float:
        return self.now_s


def _drain(variables: PlcVariables, current, *writes: dict):
    for write in writes:
        variables.on_write(write)
    return variables.drain(current)


class TestRows:
    def test_every_plc_row_is_a_variable(self):
        """Un contador de disparo también: el fork decide que lo lee como evento."""
        assert PlcVariables(_MAP).addrs == {101, 105, 106, 108}

    def test_every_variable_starts_at_rest(self):
        """El 0 decodificado: lo que tiene el registro hasta que el PLC escribe."""
        assert dict(PlcVariables(_MAP).rest_values()) == {
            "measure_seq": 0, "process_ok": False, "product_code": 0, "belt_speed_mps": 0.0}

    def test_a_map_without_plc_rows_has_nothing_to_drain(self):
        assert PlcVariables(RegisterSchema([Reg(81, "heartbeat", "Latido",
                                                PRODUCER_HEALTH)])).addrs == frozenset()


class TestDrain:
    def test_nothing_written_is_no_change(self):
        variables = PlcVariables(_MAP)
        assert variables.drain(variables.rest_values()) is None

    def test_a_write_comes_out_decoded(self):
        variables = PlcVariables(_MAP)
        plc = _drain(variables, variables.rest_values(), {105: 1, 108: 150})
        assert plc["process_ok"] is True
        assert plc["belt_speed_mps"] == pytest.approx(1.5)

    def test_writes_are_applied_in_the_order_they_arrived(self):
        variables = PlcVariables(_MAP)
        plc = _drain(variables, variables.rest_values(), {106: 5}, {106: 7})
        assert plc["product_code"] == 7

    def test_rewriting_the_same_value_is_no_change(self, caplog):
        """El PLC escribe sus variables en cada ciclo: eso no es un cambio."""
        variables = PlcVariables(_MAP)
        with caplog.at_level("INFO"):
            assert _drain(variables, variables.rest_values(), {106: 0}) is None
        assert "product_code" not in caplog.text

    def test_the_current_values_are_not_modified(self):
        """Son la foto que leen los hilos de captura: lo nuevo sale en una copia."""
        variables = PlcVariables(_MAP)
        current = variables.rest_values()
        plc = _drain(variables, current, {106: 42})
        assert current["product_code"] == 0 and plc["product_code"] == 42

    def test_the_result_cannot_be_modified(self):
        variables = PlcVariables(_MAP)
        plc = _drain(variables, variables.rest_values(), {106: 42})
        with pytest.raises(TypeError):
            plc["product_code"] = 1

    def test_an_address_that_is_not_a_plc_row_is_ignored(self):
        variables = PlcVariables(_MAP)
        assert _drain(variables, variables.rest_values(), {81: 3}) is None

    def test_the_queue_is_empty_after_a_drain(self):
        variables = PlcVariables(_MAP)
        plc = _drain(variables, variables.rest_values(), {106: 5})
        assert variables.drain(plc) is None


class TestLog:
    def test_a_change_is_logged_with_the_old_and_new_value(self, caplog):
        variables = PlcVariables(_MAP)
        with caplog.at_level("INFO"):
            _drain(variables, variables.rest_values(), {108: 150})
        assert "[Modbus] PLC: belt_speed_mps: 0.0 → 1.5 m/s" in caplog.text

    def test_a_variable_that_changes_often_logs_once_per_period(self, caplog):
        """Una analógica que cambia en cada drenado rotaría el log en horas."""
        variables = PlcVariables(_MAP, clock=_Clock())
        plc = variables.rest_values()
        with caplog.at_level("INFO"):
            for raw in (150, 160, 170):
                plc = _drain(variables, plc, {108: raw})
        assert caplog.text.count("belt_speed_mps") == 1
        assert plc["belt_speed_mps"] == pytest.approx(1.7)

    def test_the_next_line_counts_the_unlogged_changes(self, caplog):
        clock = _Clock()
        variables = PlcVariables(_MAP, clock=clock)
        plc = variables.rest_values()
        for raw in (150, 160, 170):
            plc = _drain(variables, plc, {108: raw})
        clock.now_s += _LOG_PERIOD_S
        with caplog.at_level("INFO"):
            _drain(variables, plc, {108: 180})
        assert "1.7 → 1.8 m/s (+2 cambios sin loguear)" in caplog.text

    def test_the_log_period_is_per_variable(self, caplog):
        variables = PlcVariables(_MAP, clock=_Clock())
        plc = variables.rest_values()
        with caplog.at_level("INFO"):
            plc = _drain(variables, plc, {108: 150})
            _drain(variables, plc, {106: 5})
        assert "product_code: 0 → 5" in caplog.text
