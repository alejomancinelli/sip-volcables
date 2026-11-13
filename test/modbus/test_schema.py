"""Tests de la maquinaria genérica del esquema de registros: carga del YAML,
codificación con escala, la copia del config que se publica al PLC, y qué filas
puede escribir el PLC y con qué rango.

Nada de acá abre el mapa que se despacha con el repo —eso es de test_registers.py—
ni levanta un servidor Modbus: el esquema no conoce al servidor, y el config se
reemplaza por un doble que sólo recuerda valores.
"""

from dataclasses import FrozenInstanceError

import pytest

from system.modbus.schema import (
    PRODUCER_CONFIG,
    PRODUCER_HEALTH,
    PRODUCER_INFERENCE,
    PRODUCER_PLC,
    PRODUCERS,
    UINT16_MAX,
    Reg,
    RegisterSchema,
    default_map_path,
    load_registers,
    to_uint16,
    write_range,
)


class _FakeConfig:
    """ConfigManager mínimo: `get` y `set` con clave punteada, sin archivo ni Lock."""

    def __init__(self, **values):
        self._values = dict(values)
        self.saved = False

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)

    def set(self, key: str, value: object) -> bool:
        self._values[key] = value
        return True


def _write_map(tmp_path, text: str) -> str:
    path = tmp_path / "register_map.yaml"
    path.write_text(text, encoding="utf-8")
    return str(path)


class TestToUint16:
    def test_none_is_zero(self):
        assert to_uint16(None) == 0

    def test_plain_value_passes_through(self):
        assert to_uint16(37) == 37

    def test_scale_multiplies(self):
        assert to_uint16(41.5, 10) == 415

    def test_rounds_to_nearest(self):
        assert to_uint16(41.46, 10) == 415
        assert to_uint16(41.44, 10) == 414

    def test_saturates_at_the_top(self):
        assert to_uint16(10 ** 9) == UINT16_MAX
        assert to_uint16(6553.6, 10) == UINT16_MAX

    def test_clamps_negatives_to_zero(self):
        """Un holding register no tiene signo: una temperatura bajo cero da 0."""
        assert to_uint16(-15.0, 10) == 0

    def test_accepts_numeric_strings(self):
        assert to_uint16("41.5", 10) == 415


class TestRegDefaults:
    def test_minimal_row_only_needs_the_four_required_fields(self):
        reg = Reg(1, "heartbeat", "Heartbeat", PRODUCER_HEALTH)
        assert reg.scale == 1
        assert reg.is_bool is False
        assert reg.unit == ""
        assert reg.config_key is None

    def test_is_frozen(self):
        """El mapa se arma al importar y se comparte entre hilos sin lock."""
        reg = Reg(1, "heartbeat", "Heartbeat", PRODUCER_HEALTH)
        with pytest.raises(FrozenInstanceError):
            reg.addr = 2

    def test_producers_vocabulary(self):
        assert PRODUCERS == (PRODUCER_INFERENCE, PRODUCER_HEALTH, PRODUCER_CONFIG, PRODUCER_PLC)


class TestLoadRegisters:
    def test_reads_a_valid_map(self, tmp_path):
        path = _write_map(tmp_path, """
- addr: 1
  name: heartbeat
  desc: Heartbeat
  producer: health
- addr: 2
  name: cpu_temp_c
  desc: Temperatura de CPU
  producer: health
  unit: °C
  scale: 10
""")
        registers = load_registers(path)
        assert [r.addr for r in registers] == [1, 2]
        assert registers[1].scale == 10
        assert registers[1].unit == "°C"

    def test_empty_file_is_a_valid_empty_map(self, tmp_path):
        """Hay proyectos que todavía no publican nada."""
        assert load_registers(_write_map(tmp_path, "")) == []

    def test_comments_only_file_is_an_empty_map(self, tmp_path):
        assert load_registers(_write_map(tmp_path, "# nada todavía\n")) == []

    def test_a_mapping_at_the_root_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="lista de registros"):
            load_registers(_write_map(tmp_path, "addr: 1\nname: x\n"))

    def test_a_non_mapping_entry_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="mapa de claves"):
            load_registers(_write_map(tmp_path, "- 1\n- 2\n"))

    def test_unknown_key_is_rejected(self, tmp_path):
        """Una clave mal escrita se ignoraría en silencio y el registro saldría mal."""
        with pytest.raises(ValueError, match="desconocida"):
            load_registers(_write_map(tmp_path, """
- addr: 1
  name: heartbeat
  desc: Heartbeat
  producer: health
  scala: 10
"""))

    def test_missing_required_field_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="falta"):
            load_registers(_write_map(tmp_path, "- addr: 1\n  name: heartbeat\n"))

    def test_empty_required_field_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="falta"):
            load_registers(_write_map(tmp_path, """
- addr: 1
  name: heartbeat
  desc: ''
  producer: health
"""))

    def test_unknown_producer_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="producer"):
            load_registers(_write_map(tmp_path, """
- addr: 1
  name: heartbeat
  desc: Heartbeat
  producer: telemetry
"""))

    def test_address_zero_is_rejected(self, tmp_path):
        """El mapa es base-1: el registro 1 es el 40001 del PLC."""
        with pytest.raises(ValueError, match="base-1"):
            load_registers(_write_map(tmp_path, """
- addr: 0
  name: heartbeat
  desc: Heartbeat
  producer: health
"""))

    def test_error_message_names_the_file_and_the_row(self, tmp_path):
        path = _write_map(tmp_path, """
- addr: 1
  name: heartbeat
  desc: Heartbeat
  producer: health
- addr: 2
  name: broken
  desc: Roto
  producer: nope
""")
        with pytest.raises(ValueError) as excinfo:
            load_registers(path)
        assert "entrada 2" in str(excinfo.value)
        assert path in str(excinfo.value)

    def test_default_map_path_points_into_the_package(self):
        assert default_map_path().endswith("register_map.yaml")


class TestLoadPlcRows:
    """Lo que el mapa deja escribir al PLC, y lo que hace que no cargue."""

    def test_a_plc_row_with_a_range_loads(self, tmp_path):
        registers = load_registers(_write_map(tmp_path, """
- addr: 106
  name: product_code
  desc: Código del producto
  producer: plc
  min_value: 0
  max_value: 999
"""))
        assert registers[0].producer == PRODUCER_PLC
        assert (registers[0].min_value, registers[0].max_value) == (0, 999)

    def test_a_plc_row_without_a_range_loads(self, tmp_path):
        """Un contador de disparo usa el registro entero."""
        registers = load_registers(_write_map(tmp_path, """
- addr: 101
  name: pipeline_1_trigger_seq
  desc: Disparo
  producer: plc
"""))
        assert registers[0].min_value is None and registers[0].max_value is None

    @pytest.mark.parametrize("config_key", [
        "process.max_load_y_px.camera_1",
        "inference.pipelines.pipeline_1.min_result_confidence_pct",
        "cameras.camera_1.exposure_us",
    ])
    def test_a_config_row_publishes_any_key(self, tmp_path, config_key):
        """El PLC sólo la lee: publicar un valor del config no cambia nada en el equipo."""
        registers = load_registers(_write_map(tmp_path, f"""
- addr: 115
  name: threshold
  desc: Umbral
  producer: config
  config_key: {config_key}
"""))
        assert registers[0].config_key == config_key

    def test_a_config_row_without_a_config_key_is_rejected(self, tmp_path):
        """No habría qué publicar, y nadie más escribe esa fila: el PLC leería 0."""
        with pytest.raises(ValueError, match="necesita config_key"):
            load_registers(_write_map(tmp_path, """
- addr: 10
  name: threshold
  desc: Umbral
  producer: config
"""))

    def test_apply_is_no_longer_a_field(self, tmp_path):
        """Era el hook para que el PLC escribiera el config, y el PLC no lo escribe."""
        with pytest.raises(ValueError, match="desconocida"):
            load_registers(_write_map(tmp_path, """
- addr: 117
  name: min_load_pct
  desc: Carga mínima
  producer: config
  config_key: process.min_load_pct
  apply: on_min_load_changed
"""))

    @pytest.mark.parametrize("field", ["config_key: process.min_load_pct",
                                       "default: 1"])
    def test_a_plc_row_cannot_point_at_the_config(self, tmp_path, field):
        with pytest.raises(ValueError, match="no va al config"):
            load_registers(_write_map(tmp_path, f"""
- addr: 105
  name: event_active
  desc: Evento
  producer: plc
  {field}
"""))

    @pytest.mark.parametrize("producer", ["health", "inference", "config"])
    def test_a_range_on_a_row_the_plc_does_not_write_is_rejected(self, tmp_path, producer):
        """Ahí no lo controla nadie, y la tabla del integrador diría que sí."""
        with pytest.raises(ValueError, match="sólo van en filas que escribe el PLC"):
            load_registers(_write_map(tmp_path, f"""
- addr: 1
  name: cpu_usage_pct
  desc: CPU
  producer: {producer}
  max_value: 100
"""))

    def test_a_range_on_a_boolean_row_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="is_bool"):
            load_registers(_write_map(tmp_path, """
- addr: 105
  name: event_active
  desc: Evento
  producer: plc
  is_bool: true
  max_value: 1
"""))

    def test_min_above_max_is_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="mayor que max_value"):
            load_registers(_write_map(tmp_path, """
- addr: 106
  name: product_code
  desc: Producto
  producer: plc
  min_value: 10
  max_value: 5
"""))

    @pytest.mark.parametrize("bound", ["max_value: 6553.6", "min_value: -1"])
    def test_a_range_that_does_not_fit_the_register_is_rejected(self, tmp_path, bound):
        """`to_uint16` saturaría, y el rango aceptado no sería el declarado."""
        with pytest.raises(ValueError, match="no entra en un registro"):
            load_registers(_write_map(tmp_path, f"""
- addr: 108
  name: belt_speed_mps
  desc: Velocidad de la cinta
  producer: plc
  unit: m/s
  scale: 10
  {bound}
"""))

    @pytest.mark.parametrize("bound", ["max_value: cien", "max_value: true"])
    def test_a_non_numeric_bound_is_rejected(self, tmp_path, bound):
        with pytest.raises(ValueError, match="tiene que ser un número"):
            load_registers(_write_map(tmp_path, f"""
- addr: 106
  name: product_code
  desc: Producto
  producer: plc
  {bound}
"""))

    def test_the_error_names_the_file_the_entry_and_the_row(self, tmp_path):
        path = _write_map(tmp_path, """
- addr: 1
  name: heartbeat
  desc: Heartbeat
  producer: health
- addr: 105
  name: camera_switch
  desc: Cámara
  producer: plc
  config_key: cameras.camera_1.enabled
""")
        with pytest.raises(ValueError) as excinfo:
            load_registers(path)
        message = str(excinfo.value)
        assert path in message
        assert "entrada 2" in message
        assert "camera_switch" in message


class TestMaxAge:
    """Una fila de inferencia que vuelve a 0 sin un resultado que la renueve."""

    def test_an_inference_row_keeps_its_max_age(self, tmp_path):
        registers = load_registers(_write_map(tmp_path, """
- addr: 3
  name: camera_3_presence
  desc: Presencia
  producer: inference
  max_age_s: 3
"""))
        assert registers[0].max_age_s == 3

    def test_without_it_a_row_never_expires(self):
        assert Reg(1, "x", "x", PRODUCER_INFERENCE).max_age_s is None

    @pytest.mark.parametrize("producer, extra", [("health", ""), ("plc", ""),
                                                 ("config", "config_key: process.x")])
    def test_it_only_goes_on_inference_rows(self, tmp_path, producer, extra):
        """En otra fila no hay un resultado que la renueve, y nadie la vencería."""
        with pytest.raises(ValueError, match="sólo va en filas de inferencia"):
            load_registers(_write_map(tmp_path, f"""
- addr: 3
  name: some_value
  desc: Algo
  producer: {producer}
  max_age_s: 3
  {extra}
"""))

    @pytest.mark.parametrize("value", [0, -1, "'3'", "true"])
    def test_it_has_to_be_a_positive_number_of_seconds(self, tmp_path, value):
        with pytest.raises(ValueError, match="max_age_s"):
            load_registers(_write_map(tmp_path, f"""
- addr: 3
  name: camera_3_presence
  desc: Presencia
  producer: inference
  max_age_s: {value}
"""))

    def test_the_schema_lists_only_the_rows_that_expire(self):
        schema = RegisterSchema([
            Reg(3, "camera_3_presence", "Presencia", PRODUCER_INFERENCE, max_age_s=3),
            Reg(5, "coverage_pct", "Cobertura", PRODUCER_INFERENCE),
        ])
        assert schema.max_age_by_name() == {"camera_3_presence": 3.0}


class TestWritable:
    def setup_method(self):
        self.schema = RegisterSchema([
            Reg(1, "heartbeat", "Latido", PRODUCER_HEALTH),
            Reg(3, "object_count", "Objetos", PRODUCER_INFERENCE),
            Reg(101, "pipeline_1_trigger_seq", "Disparo", PRODUCER_PLC),
            Reg(105, "event_active", "Evento", PRODUCER_PLC, is_bool=True),
            Reg(106, "product_code", "Producto", PRODUCER_PLC, min_value=0, max_value=999),
            Reg(115, "max_load_y_px", "Límite", PRODUCER_PLC, unit="px",
                min_value=10, max_value=4000),
            Reg(117, "belt_speed_mps", "Velocidad", PRODUCER_PLC, unit="m/s", scale=10,
                min_value=0, max_value=100),
            Reg(120, "min_load_pct", "Carga", PRODUCER_CONFIG, unit="%",
                config_key="process.min_load_pct"),
        ])

    def test_only_the_plc_rows_are_writable(self):
        assert set(self.schema.write_ranges()) == {101, 105, 106, 115, 117}
        assert [r.addr for r in self.schema.plc_regs()] == [101, 105, 106, 115, 117]

    def test_a_config_row_is_read_only(self):
        """El umbral se publica para que el PLC lo muestre; cambiarlo es de la interfaz."""
        assert 120 not in self.schema.write_ranges()

    def test_a_row_the_app_writes_is_not_writable(self):
        assert not {1, 3} & set(self.schema.write_ranges())

    def test_an_undeclared_address_is_not_writable(self):
        """El hueco entre dos filas escribibles no es escribible."""
        assert 102 not in self.schema.write_ranges()

    def test_without_a_range_the_whole_register_is_valid(self):
        assert self.schema.write_ranges()[101] == (0, UINT16_MAX)

    def test_a_boolean_row_accepts_only_zero_and_one(self):
        assert self.schema.write_ranges()[105] == (0, 1)

    def test_both_bounds_are_the_declared_ones(self):
        assert self.schema.write_ranges()[115] == (10, 4000)

    def test_the_raw_range_is_scaled(self):
        """100 % con escala x10 es 1000 en el registro."""
        assert self.schema.write_ranges()[117] == (0, 1000)

    def test_the_physical_range_stays_in_the_unit(self):
        """Es el que lee el integrador en la tabla, antes de multiplicar por la escala."""
        assert write_range(self.schema.registers[6]) == (0, 100)

    def test_without_a_max_the_physical_range_is_the_whole_register_unscaled(self):
        reg = Reg(118, "speed", "Velocidad", PRODUCER_PLC, scale=100)
        assert write_range(reg) == (0, UINT16_MAX / 100)
        assert RegisterSchema([reg]).write_ranges()[118] == (0, UINT16_MAX)

    def test_the_caller_cannot_change_the_ranges(self):
        """El servidor guarda lo que recibe; el esquema es inmutable y compartido."""
        self.schema.write_ranges()[101] = (0, 1)
        assert self.schema.write_ranges()[101] == (0, UINT16_MAX)

    def test_an_empty_map_has_nothing_writable(self):
        schema = RegisterSchema([])
        assert schema.write_ranges() == {}
        assert schema.plc_regs() == []


class TestDecode:
    def setup_method(self):
        self.schema = RegisterSchema([
            Reg(1, "cpu_usage_pct", "CPU", PRODUCER_HEALTH, unit="%"),
            Reg(2, "cpu_temp_c", "Temp", PRODUCER_HEALTH, unit="°C", scale=10),
            Reg(105, "event_active", "Evento", PRODUCER_PLC, is_bool=True),
            Reg(106, "product_code", "Producto", PRODUCER_PLC),
        ])

    def test_unscaled_value(self):
        assert self.schema.decode("cpu_usage_pct", 37) == 37

    def test_scaled_value(self):
        assert self.schema.decode("cpu_temp_c", 415) == pytest.approx(41.5)

    def test_boolean(self):
        assert self.schema.decode("event_active", 1) is True
        assert self.schema.decode("event_active", 0) is False

    def test_a_plc_row_decodes(self):
        """Leer lo que mandó el PLC es justamente lo que la app hace con esas filas."""
        assert self.schema.decode("product_code", 42) == 42

    def test_is_the_inverse_of_encode(self):
        assert self.schema.decode("cpu_temp_c", self.schema.encode("cpu_temp_c", 41.5)) \
            == pytest.approx(41.5)

    def test_unknown_name_raises(self):
        with pytest.raises(KeyError):
            self.schema.decode("no_existe", 0)


class TestEncodeRefusesPlcRows:
    """Una fila `plc` tiene un solo dueño: si la app la escribiera, pisaría al PLC."""

    def setup_method(self):
        self.schema = RegisterSchema([
            Reg(1, "heartbeat", "Latido", PRODUCER_HEALTH),
            Reg(105, "event_active", "Evento", PRODUCER_PLC, is_bool=True),
            Reg(115, "max_load_y_px", "Límite", PRODUCER_CONFIG,
                config_key="process.max_load_y_px", default=430),
        ])

    def test_encode_raises(self):
        with pytest.raises(ValueError, match="sólo el PLC"):
            self.schema.encode("event_active", 1)

    def test_encode_batch_raises_even_with_other_rows(self):
        with pytest.raises(ValueError, match="sólo el PLC"):
            self.schema.encode_batch({"heartbeat": 1, "event_active": 1})

    def test_config_rows_still_encode(self):
        """Los umbrales los publica la app: es la única que escribe esas filas."""
        assert self.schema.encode("max_load_y_px", 620) == 620

    def test_the_mirror_never_includes_a_plc_row(self):
        assert self.schema.mirror_batch(_FakeConfig()) == {115: 430}


class TestRegisterSchemaValidation:
    def test_duplicate_address_is_rejected(self):
        with pytest.raises(ValueError, match="Dirección Modbus duplicada"):
            RegisterSchema([
                Reg(1, "a", "A", PRODUCER_HEALTH),
                Reg(1, "b", "B", PRODUCER_HEALTH),
            ])

    def test_duplicate_name_is_rejected(self):
        with pytest.raises(ValueError, match="Nombre de registro duplicado"):
            RegisterSchema([
                Reg(1, "a", "A", PRODUCER_HEALTH),
                Reg(2, "a", "B", PRODUCER_HEALTH),
            ])

    def test_empty_map_is_valid(self):
        schema = RegisterSchema([])
        assert schema.registers == []
        assert schema.max_addr() == 0
        assert schema.descriptions() == {}


class TestIndexes:
    def setup_method(self):
        self.schema = RegisterSchema([
            Reg(1, "status", "Estado", PRODUCER_HEALTH),
            Reg(5, "coverage_pct", "Cobertura", PRODUCER_INFERENCE, unit="%", scale=10),
            Reg(9, "confidence_pct", "Confianza", PRODUCER_INFERENCE),
            Reg(20, "interval_s", "Intervalo", PRODUCER_CONFIG,
                config_key="collector.interval_s", default=60),
        ])

    def test_addr_by_name(self):
        assert self.schema.addr("coverage_pct") == 5

    def test_unknown_name_raises(self):
        """Pedir una dirección que no existe se cae acá, no en el PLC."""
        with pytest.raises(KeyError):
            self.schema.addr("no_existe")

    def test_max_addr(self):
        assert self.schema.max_addr() == 20

    def test_descriptions(self):
        assert self.schema.descriptions() == {1: "Estado", 5: "Cobertura",
                                              9: "Confianza", 20: "Intervalo"}

    def test_config_regs_are_only_the_config_rows(self):
        assert [r.addr for r in self.schema.config_regs()] == [20]


class TestEncode:
    def setup_method(self):
        self.schema = RegisterSchema([
            Reg(1, "cpu_usage_pct", "CPU", PRODUCER_HEALTH, unit="%"),
            Reg(2, "cpu_temp_c", "Temp", PRODUCER_HEALTH, unit="°C", scale=10),
            Reg(3, "collecting", "Recolectando", PRODUCER_HEALTH, is_bool=True),
        ])

    def test_unscaled_value(self):
        assert self.schema.encode("cpu_usage_pct", 37) == 37

    def test_scaled_value(self):
        """El productor entrega el valor físico; la escala es del esquema."""
        assert self.schema.encode("cpu_temp_c", 41.5) == 415

    def test_boolean_ignores_the_scale(self):
        assert self.schema.encode("collecting", True) == 1
        assert self.schema.encode("collecting", False) == 0

    def test_boolean_takes_truthiness(self):
        assert self.schema.encode("collecting", "algo") == 1
        assert self.schema.encode("collecting", None) == 0

    def test_none_becomes_zero(self):
        assert self.schema.encode("cpu_temp_c", None) == 0

    def test_saturates_instead_of_wrapping(self):
        """Un valor absurdo tiene que quedar en el techo, no dar la vuelta a 0."""
        assert self.schema.encode("cpu_temp_c", 10 ** 6) == UINT16_MAX

    def test_encode_batch_maps_names_to_addresses(self):
        assert self.schema.encode_batch({"cpu_usage_pct": 37, "cpu_temp_c": 41.5}) == {
            1: 37, 2: 415,
        }

    def test_encode_batch_of_nothing_is_empty(self):
        assert self.schema.encode_batch({}) == {}

    def test_encode_batch_rejects_an_unknown_name(self):
        with pytest.raises(KeyError):
            self.schema.encode_batch({"no_existe": 1})


class TestMirrorBatch:
    def setup_method(self):
        self.schema = RegisterSchema([
            Reg(1, "cpu_usage_pct", "CPU", PRODUCER_HEALTH),
            Reg(10, "interval_s", "Intervalo", PRODUCER_CONFIG,
                config_key="collector.interval_s", default=60),
            Reg(11, "enabled", "Habilitado", PRODUCER_CONFIG, is_bool=True,
                config_key="collector.enabled", default=False),
            Reg(12, "threshold_pct", "Umbral", PRODUCER_CONFIG, scale=10,
                config_key="inference.threshold_pct", default=0),
        ])

    def test_mirrors_only_the_config_rows(self):
        config = _FakeConfig(**{"collector.interval_s": 90})
        batch = self.schema.mirror_batch(config)
        assert 1 not in batch, "una fila health no se espeja desde config"
        assert batch[10] == 90

    def test_uses_the_default_when_the_key_is_missing(self):
        assert self.schema.mirror_batch(_FakeConfig())[10] == 60

    def test_applies_the_scale(self):
        config = _FakeConfig(**{"inference.threshold_pct": 7.5})
        assert self.schema.mirror_batch(config)[12] == 75

    def test_encodes_booleans(self):
        config = _FakeConfig(**{"collector.enabled": True})
        assert self.schema.mirror_batch(config)[11] == 1


class TestBitfield:
    def test_a_row_declares_its_word(self, tmp_path):
        path = _write_map(tmp_path, """
- addr: 1
  name: system_status_bitfield
  desc: Estado del sistema
  producer: health
  bitfield: system_status
""")
        assert load_registers(path)[0].bitfield == "system_status"

    def test_a_row_without_it_is_not_a_word(self, tmp_path):
        path = _write_map(tmp_path, """
- addr: 1
  name: heartbeat
  desc: Heartbeat
  producer: health
""")
        assert load_registers(path)[0].bitfield == ""

    def test_a_word_that_is_not_text_is_rejected(self, tmp_path):
        path = _write_map(tmp_path, """
- addr: 1
  name: system_status_bitfield
  desc: Estado del sistema
  producer: health
  bitfield: 3
""")
        with pytest.raises(ValueError, match="bitfield"):
            load_registers(path)

    def test_the_schema_lists_only_the_words(self):
        schema = RegisterSchema([
            Reg(1, "system_status_bitfield", "Estado", PRODUCER_HEALTH, bitfield="system_status"),
            Reg(2, "heartbeat", "Latido", PRODUCER_HEALTH),
        ])
        assert schema.bitfields() == {1: "system_status"}
