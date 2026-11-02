"""Tests de la tabla que lee el integrador: qué filas escribe el PLC y con qué rango.

No escribe archivos: prueba las filas que arma `export_map`, que son las mismas para el
Markdown y para el CSV.
"""

from system.modbus import export_map
from system.modbus.schema import PRODUCER_CONFIG, PRODUCER_HEALTH, PRODUCER_PLC, Reg


def _column(reg: Reg, name: str) -> str:
    return export_map._row(reg)[export_map._COLUMNS.index(name)]


class TestAccess:
    def test_a_plc_row_is_written_by_the_plc(self):
        assert _column(Reg(105, "process_ok", "OK", PRODUCER_PLC), "Acceso") == "escribe el PLC"

    def test_a_published_threshold_is_only_read(self):
        """El PLC lee el umbral para el HMI; cambiarlo es de la interfaz."""
        reg = Reg(10, "max_load_y_px", "Límite", PRODUCER_CONFIG, config_key="process.x")
        assert _column(reg, "Acceso") == "lectura"

    def test_a_health_row_is_only_read(self):
        assert _column(Reg(81, "heartbeat", "Latido", PRODUCER_HEALTH), "Acceso") == "lectura"


class TestRange:
    def test_a_row_the_plc_does_not_write_has_no_range(self):
        assert _column(Reg(81, "heartbeat", "Latido", PRODUCER_HEALTH), "Rango") == "-"

    def test_a_boolean_takes_zero_and_one(self):
        reg = Reg(105, "process_ok", "OK", PRODUCER_PLC, is_bool=True)
        assert _column(reg, "Rango") == "0-1"

    def test_the_declared_range_is_in_the_physical_unit(self):
        """Como en el YAML: la escala tiene su columna y el integrador la aplica igual."""
        reg = Reg(108, "belt_speed_mps", "Velocidad", PRODUCER_PLC, unit="m/s", scale=100,
                  min_value=0, max_value=10)
        assert _column(reg, "Rango") == "0-10"

    def test_without_a_range_it_is_the_whole_register(self):
        assert _column(Reg(101, "measure_seq", "Disparo", PRODUCER_PLC), "Rango") == "0-65535"

    def test_without_a_range_a_scaled_row_divides_the_register(self):
        reg = Reg(108, "belt_speed_mps", "Velocidad", PRODUCER_PLC, unit="m/s", scale=100)
        assert _column(reg, "Rango") == "0-655.35"


class TestMarkdown:
    def test_it_no_longer_says_the_map_is_read_only(self):
        """Con filas que escribe el PLC, «de sólo lectura» le diría lo contrario al integrador."""
        text = export_map.build_markdown()
        assert "sólo lectura" not in text
        assert "docs/plc_commands.md" in text

    def test_every_row_has_one_cell_per_column(self):
        reg = Reg(105, "process_ok", "OK", PRODUCER_PLC, is_bool=True)
        assert len(export_map._row(reg)) == len(export_map._COLUMNS)
