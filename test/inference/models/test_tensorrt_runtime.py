"""Tests del runtime de TensorRT: el formato del `.engine` de ultralytics, qué engines se
aceptan y qué pasa sin el runtime.

La pasada de inferencia necesita la GPU y no se ejercita acá: lo que se prueba es lo que
este módulo decide antes de llegar a ella."""

import json
import sys

import pytest

from system.inference.models import tensorrt_runtime
from system.inference.models.tensorrt_runtime import (TensorRtRunner, TensorSpec,
                                                      split_ultralytics_engine, validate_io)

_ENGINE = b"\x00\x01TRT-ENGINE-BYTES\xff" * 8


def _with_header(metadata: dict, engine: bytes = _ENGINE) -> bytes:
    """Un `.engine` como lo escribe ultralytics: largo, JSON y engine."""
    meta = json.dumps(metadata).encode()
    return len(meta).to_bytes(4, byteorder="little", signed=True) + meta + engine


def _specs(*, inputs: int = 1, input_shape: tuple = (1, 3, 384, 640),
           output_shape: tuple = (1, 84, 5040), dtype: str = "FLOAT") -> list[TensorSpec]:
    specs = [TensorSpec(f"images{i}", input_shape, dtype, True) for i in range(inputs)]
    if output_shape:
        specs.append(TensorSpec("output0", output_shape, dtype, False))
    return specs


class TestSplitUltralyticsEngine:
    def test_the_metadata_and_the_engine_come_apart(self):
        metadata, engine = split_ultralytics_engine(
            _with_header({"task": "detect", "imgsz": [384, 640]}))
        assert metadata == {"task": "detect", "imgsz": [384, 640]}
        assert engine == _ENGINE

    def test_an_engine_without_header_is_used_as_it_is(self):
        """Los primeros bytes de un engine pelado, leídos como largo, dan un absurdo."""
        raw = b"\xff\xff\xff\x7f" + _ENGINE
        assert split_ultralytics_engine(raw) == ({}, raw)

    def test_a_header_that_is_not_json_means_no_header(self):
        raw = (8).to_bytes(4, byteorder="little") + b"\x80\x81\x82\x83\x84\x85\x86\x87" + _ENGINE
        assert split_ultralytics_engine(raw) == ({}, raw)

    def test_a_length_past_the_end_means_no_header(self):
        raw = (10_000).to_bytes(4, byteorder="little") + b"{}"
        assert split_ultralytics_engine(raw) == ({}, raw)

    def test_too_short_to_have_a_header(self):
        assert split_ultralytics_engine(b"\x01\x02") == ({}, b"\x01\x02")


class TestValidateIo:
    def test_one_fixed_input_and_an_output_is_fine(self):
        assert validate_io(_specs()) == ""

    def test_two_inputs_are_not_handled(self):
        assert "2 entradas" in validate_io(_specs(inputs=2))

    def test_an_engine_without_outputs_is_rejected(self):
        assert "salidas" in validate_io(_specs(output_shape=()))

    def test_a_dynamic_shape_asks_for_a_fixed_export(self):
        assert "dynamic=False" in validate_io(_specs(input_shape=(-1, 3, 384, 640)))

    def test_an_unknown_type_is_rejected(self):
        assert "FP8" in validate_io(_specs(dtype="FP8"))

    def test_the_input_must_be_an_image_batch(self):
        assert "(1, 3, alto, ancho)" in validate_io(_specs(input_shape=(1, 640)))


class TestDtypeName:
    def test_an_enum_member_gives_its_name(self):
        class _DataType:
            name = "HALF"

        assert tensorrt_runtime._dtype_name(_DataType()) == "HALF"

    def test_a_printed_enum_gives_the_last_part(self):
        assert tensorrt_runtime._dtype_name("DataType.FLOAT") == "FLOAT"


class TestRunnerWithoutRuntime:
    def test_load_says_the_runtime_is_missing(self, monkeypatch):
        """Sin TensorRT el modelo queda en error con el motivo; la app arranca igual."""
        monkeypatch.setitem(sys.modules, "tensorrt", None)
        reason = TensorRtRunner().load(_ENGINE)
        assert "runtime de inferencia" in reason

    def test_close_before_load_does_nothing(self):
        runner = TensorRtRunner()
        runner.close()
        runner.close()


@pytest.mark.parametrize("dtype", ["FLOAT", "HALF", "INT32", "INT64", "INT8", "UINT8", "BOOL"])
def test_every_accepted_type_has_a_torch_type(dtype):
    assert dtype in tensorrt_runtime._TORCH_DTYPES
