"""
Qué aceleradores puede usar este equipo, probados y no supuestos.

Maquinaria genérica: no conoce la app ni su config. Contesta tres preguntas:

  - **Qué elementos de GStreamer existen** (`find_elements`): `nvvidconv` (VIC),
    `nvv4l2h264enc` (NVENC), `nvv4l2decoder` (NVDEC), `nvjpegenc` (NVJPG)...
  - **Si cada motor anda de verdad** (`check_pipeline` sobre `build_pipeline_checks`).
    Que un elemento exista no alcanza: el plugin se instala igual en todos los módulos y
    el motor puede no estar —el Orin Nano no tiene NVENC y, según el foro de NVIDIA,
    tampoco codificador JPEG—. Cada prueba corre un pipeline corto con
    `videotestsrc` hasta el EOS y devuelve '' o el error.
  - **Qué ofrecen las librerías de Python** (`probe_python_libs`): si el OpenCV del
    venv trae GStreamer o CUDA, la versión de VPI y cuántos DLA ve TensorRT.

Que el pipeline termine bien dice que el elemento funciona, no que haya usado el motor:
un elemento podría caer a software sin avisar. Eso lo confirma `tegrastats` mientras la
prueba corre, y lo hace la prueba manual de `manual_test/jetson_accelerators/`.

Sin GStreamer —Windows, un equipo pelado— nada propaga excepciones: los elementos dan
False y cada prueba devuelve el motivo.
"""

import re

try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import GLib, Gst
    _GSTREAMER_AVAILABLE = True
    _IMPORT_ERROR = ""
except (ImportError, ValueError) as e:
    _GSTREAMER_AVAILABLE = False
    _IMPORT_ERROR = str(e)

# Elementos que interesan: los de la Jetson y sus equivalentes por CPU, para que el
# reporte diga también qué camino por software queda si falta el de hardware.
ELEMENT_NAMES = (
    "nvvidconv", "nvv4l2h264enc", "nvv4l2h265enc", "nvv4l2decoder", "nvjpegenc",
    "nvjpegdec", "x264enc", "x265enc", "jpegenc", "videoconvert", "videoscale",
    "appsrc", "appsink", "rtspsrc",
)

_DEFAULT_TIMEOUT_S = 20.0
_NS_PER_S = 1_000_000_000

_BUILD_INFO_RE = {
    "gstreamer": re.compile(r"^\s*GStreamer:\s*(.+)$", re.MULTILINE),
    "cuda": re.compile(r"^\s*NVIDIA CUDA:\s*(.+)$", re.MULTILINE),
}


def _even(value_px: int) -> int:
    return value_px - value_px % 2


def build_pipeline_checks(width_px: int = 1280, height_px: int = 720,
                          frame_count: int = 30) -> dict[str, str]:
    """
    Un pipeline de prueba por motor: `{motor: launch string}`.

    Cada uno genera `frame_count` frames de `width_px`×`height_px` y los pasa por un
    solo motor. Con pocos frames chicos la prueba dura menos que un intervalo de
    `tegrastats` y el motor no llega a verse encendido: para atribuir carga, más frames.
    La de NVDEC necesita `x264enc` para fabricar el H.264 que decodifica.
    """
    source = (f"videotestsrc num-buffers={frame_count} "
              f"! video/x-raw,format=I420,width={width_px},height={height_px}")
    # La mitad, en pares: NV12 submuestrea el croma y no acepta un lado impar.
    half = f"width={_even(width_px // 2)},height={_even(height_px // 2)}"
    nvmm = "video/x-raw(memory:NVMM)"
    sink = "fakesink sync=false"
    return {
        "vic": (f"{source} ! nvvidconv ! {nvmm},format=NV12,{half} "
                f"! nvvidconv ! video/x-raw,format=BGRx ! {sink}"),
        "nvenc_h264": f"{source} ! nvvidconv ! {nvmm},format=I420 ! nvv4l2h264enc ! {sink}",
        "nvenc_h265": f"{source} ! nvvidconv ! {nvmm},format=NV12 ! nvv4l2h265enc ! {sink}",
        "nvdec_h264": (f"{source} ! x264enc speed-preset=ultrafast ! h264parse "
                       f"! nvv4l2decoder ! {sink}"),
        "nvjpg_encode": f"{source} ! nvvidconv ! {nvmm},format=I420 ! nvjpegenc ! {sink}",
        "nvjpg_decode": f"{source} ! jpegenc ! nvjpegdec ! {sink}",
    }


def find_elements(names: tuple[str, ...] = ELEMENT_NAMES) -> dict[str, bool]:
    """Si cada elemento de GStreamer está instalado. Todos False sin GStreamer."""
    if not _GSTREAMER_AVAILABLE:
        return {name: False for name in names}
    Gst.init(None)
    return {name: Gst.ElementFactory.find(name) is not None for name in names}


def check_pipeline(launch: str, timeout_s: float = _DEFAULT_TIMEOUT_S) -> str:
    """
    Corre `launch` hasta el EOS: '' si terminó bien, el motivo si no.

    No necesita un loop de GLib: espera el EOS o el error en el bus, con `timeout_s`
    como techo. El pipeline queda en NULL al salir, pase lo que pase.
    """
    if not _GSTREAMER_AVAILABLE:
        return f"GStreamer no disponible: {_IMPORT_ERROR}"
    Gst.init(None)
    try:
        pipeline = Gst.parse_launch(launch)
    except GLib.Error as e:
        return str(e)
    try:
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            return _pop_error(pipeline) or "el pipeline no pasó a PLAYING"
        message = pipeline.get_bus().timed_pop_filtered(
            int(timeout_s * _NS_PER_S), Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if message is None:
            return f"sin EOS en {timeout_s:g} s"
        if message.type == Gst.MessageType.ERROR:
            error, _ = message.parse_error()
            return error.message
        return ""
    finally:
        pipeline.set_state(Gst.State.NULL)


def _pop_error(pipeline) -> str:
    """El error que dejó en el bus un cambio de estado fallido, o ''."""
    message = pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR)
    if message is None:
        return ""
    error, _ = message.parse_error()
    return error.message


def parse_build_information(build_info: str) -> dict:
    """`{"gstreamer": str, "cuda": str}` de `cv2.getBuildInformation()`; '' lo que no figura."""
    values = {}
    for key, pattern in _BUILD_INFO_RE.items():
        match = pattern.search(build_info)
        values[key] = match.group(1).strip() if match else ""
    return values


def _probe_opencv() -> dict:
    try:
        import cv2
        return {"version": cv2.__version__,
                **parse_build_information(cv2.getBuildInformation()), "error": ""}
    except Exception as e:
        return {"version": "", "gstreamer": "", "cuda": "", "error": str(e)}


def _probe_vpi() -> dict:
    try:
        import vpi
        backends = [name for name in ("CPU", "CUDA", "PVA", "VIC", "OFA")
                    if hasattr(vpi.Backend, name)]
        return {"version": str(getattr(vpi, "__version__", "?")),
                "backends": backends, "error": ""}
    except Exception as e:
        return {"version": "", "backends": [], "error": str(e)}


def _probe_tensorrt() -> dict:
    try:
        import tensorrt as trt
        # Armar el builder inicializa CUDA: tarda un segundo, y es lo que hace falta para
        # que TensorRT cuente los DLA de este módulo.
        builder = trt.Builder(trt.Logger(trt.Logger.ERROR))
        return {"version": trt.__version__, "dla_cores": int(builder.num_DLA_cores),
                "error": ""}
    except Exception as e:
        return {"version": "", "dla_cores": 0, "error": str(e)}


def probe_python_libs() -> dict:
    """
    Qué ofrecen las librerías de Python del venv:

      opencv    {"version", "gstreamer", "cuda", "error"}  según su build
      vpi       {"version", "backends", "error"}            backends que declara
      tensorrt  {"version", "dla_cores", "error"}           DLA que ve en este módulo

    Una librería que no importa deja su motivo en `error`; las demás se reportan igual.
    """
    return {"opencv": _probe_opencv(), "vpi": _probe_vpi(), "tensorrt": _probe_tensorrt()}
