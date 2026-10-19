"""
Un `.engine` de TensorRT abierto desde bytes, y la pasada de inferencia.

Maquinaria genérica: no sabe qué tarea corre ni cómo se decodifica su salida; eso es de
quien lo usa (`yolo_model.py`). Acá está lo que depende de TensorRT y de la GPU, y nada
más, para que el resto se pruebe sin ninguna de las dos.

Qué fija este módulo:
  - **Carga desde bytes, nunca desde una ruta**: los pesos pueden venir cifrados y
    escribirlos a disco tiraría la protección (ver `AbstractModel._read_weights`).
  - **Entiende el formato de ultralytics**: un `.engine` exportado por ultralytics
    empieza con 4 bytes de largo y un JSON de metadata —tarea, nombres de clase, tamaño
    de entrada— antes del engine. `split_ultralytics_engine` los separa; un engine sin
    ese encabezado se usa tal cual.
  - **La forma de la entrada sale del engine**, no del config: un engine exportado a
    384x640 recibe 384x640. Por eso elegir entre uno cuadrado y uno rectangular es elegir
    qué archivo apunta `path`. Un engine de forma dinámica no se acepta: se exporta con
    `dynamic=False`, que es el default de ultralytics.
  - **Los buffers viven en la GPU como tensores de torch**: la entrada se copia una vez
    por pasada y las salidas vuelven a numpy en float32. Una sola entrada.

`tensorrt` y `torch` se importan al cargar, no al importar el módulo: sin ellos el modelo
queda en error con el motivo y la app arranca igual.
"""

import json
from typing import NamedTuple

import numpy as np

_HEADER_LENGTH_BYTES = 4
# Un encabezado más largo que esto no es metadata de ultralytics: son los primeros bytes
# de un engine pelado, que se leyeron como un largo.
_MAX_METADATA_BYTES = 1 << 20

# Tipos de TensorRT -> nombre del dtype de torch.
_TORCH_DTYPES = {
    "FLOAT": "float32", "HALF": "float16", "INT32": "int32", "INT64": "int64",
    "INT8": "int8", "UINT8": "uint8", "BOOL": "bool",
}


class TensorSpec(NamedTuple):
    """Un tensor de entrada o salida del engine."""

    name: str
    shape: tuple[int, ...]
    dtype: str          # nombre de TensorRT: FLOAT, HALF...
    is_input: bool


def split_ultralytics_engine(data: bytes) -> tuple[dict, bytes]:
    """
    (metadata, engine) de un `.engine` de ultralytics: 4 bytes little-endian con el largo
    del JSON, el JSON, y el engine. Sin ese encabezado devuelve ({}, data).
    """
    if len(data) <= _HEADER_LENGTH_BYTES:
        return {}, data
    length = int.from_bytes(data[:_HEADER_LENGTH_BYTES], byteorder="little", signed=True)
    end = _HEADER_LENGTH_BYTES + length
    if length <= 0 or length > _MAX_METADATA_BYTES or end >= len(data):
        return {}, data
    try:
        metadata = json.loads(data[_HEADER_LENGTH_BYTES:end].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}, data
    if not isinstance(metadata, dict):
        return {}, data
    return metadata, data[end:]


def validate_io(specs: list[TensorSpec]) -> str:
    """'' si los tensores del engine son los que este runtime sabe correr; el motivo si no."""
    inputs = [spec for spec in specs if spec.is_input]
    if len(inputs) != 1:
        return f"el engine tiene {len(inputs)} entradas; se espera una"
    if not any(not spec.is_input for spec in specs):
        return "el engine no tiene salidas"
    for spec in specs:
        if any(size < 0 for size in spec.shape):
            return (f"el tensor '{spec.name}' tiene forma dinámica {spec.shape}: exportar "
                    f"con dynamic=False")
        if spec.dtype not in _TORCH_DTYPES:
            return f"el tensor '{spec.name}' es de un tipo que no se maneja: {spec.dtype}"
    if len(inputs[0].shape) != 4:
        return f"la entrada tiene forma {inputs[0].shape}; se espera (1, 3, alto, ancho)"
    return ""


def _dtype_name(dtype: object) -> str:
    """'FLOAT' a partir de `trt.DataType.FLOAT`, sin depender de cómo lo imprima la versión."""
    return str(getattr(dtype, "name", dtype)).rsplit(".", 1)[-1].upper()


class TensorRtRunner:
    """
    Un engine deserializado, con sus buffers en la GPU. Un solo hilo lo usa: el del
    pipeline, que es el dueño del modelo.
    """

    def __init__(self):
        self._torch = None
        self._engine = None
        self._context = None
        self._buffers: dict = {}
        self._specs: list[TensorSpec] = []

    @property
    def input_spec(self) -> TensorSpec:
        return next(spec for spec in self._specs if spec.is_input)

    @property
    def output_shapes(self) -> list[tuple[int, ...]]:
        """Forma de cada salida, en el orden en que las declara el engine."""
        return [spec.shape for spec in self._specs if not spec.is_input]

    @property
    def input_hw(self) -> tuple[int, int]:
        """(alto, ancho) de la entrada, que es a lo que se lleva cada frame."""
        _, _, height_px, width_px = self.input_spec.shape
        return height_px, width_px

    def load(self, engine_bytes: bytes) -> str:
        """Deserializa el engine y reserva sus buffers: '' si quedó listo, el motivo si no."""
        try:
            import tensorrt as trt
            import torch
        except ImportError as e:
            return f"falta el runtime de inferencia ({e}); en la Jetson va con JetPack"
        if not torch.cuda.is_available():
            return "torch no ve CUDA: el engine necesita la GPU"

        runtime = trt.Runtime(trt.Logger(trt.Logger.WARNING))
        engine = runtime.deserialize_cuda_engine(engine_bytes)
        if engine is None:
            return ("TensorRT no pudo abrir el engine: se exporta en el mismo módulo y "
                    "la misma versión de TensorRT que lo corre")

        specs = [
            TensorSpec(name, tuple(int(size) for size in engine.get_tensor_shape(name)),
                       _dtype_name(engine.get_tensor_dtype(name)),
                       engine.get_tensor_mode(name) == trt.TensorIOMode.INPUT)
            for name in (engine.get_tensor_name(i) for i in range(engine.num_io_tensors))
        ]
        reason = validate_io(specs)
        if reason:
            return reason

        context = engine.create_execution_context()
        buffers = {}
        for spec in specs:
            dtype = getattr(torch, _TORCH_DTYPES[spec.dtype])
            buffers[spec.name] = torch.empty(spec.shape, dtype=dtype, device="cuda")
            context.set_tensor_address(spec.name, buffers[spec.name].data_ptr())

        self._torch, self._engine, self._context = torch, engine, context
        self._buffers, self._specs = buffers, specs
        return ""

    def run(self, input_nchw: np.ndarray) -> dict[str, np.ndarray]:
        """Una pasada: la entrada float32 NCHW, y cada salida por nombre en float32."""
        torch = self._torch
        input_buffer = self._buffers[self.input_spec.name]
        input_buffer.copy_(torch.from_numpy(input_nchw).to(input_buffer.dtype))
        stream = torch.cuda.current_stream()
        if not self._context.execute_async_v3(stream.cuda_stream):
            raise RuntimeError("TensorRT no pudo ejecutar el engine")
        stream.synchronize()
        return {spec.name: self._buffers[spec.name].float().cpu().numpy()
                for spec in self._specs if not spec.is_input}

    def close(self):
        """Suelta el engine y los buffers. Idempotente."""
        self._buffers = {}
        self._context = None
        self._engine = None
        if self._torch is not None:
            self._torch.cuda.empty_cache()
        self._torch = None
