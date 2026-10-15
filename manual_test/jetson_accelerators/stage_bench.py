"""
Prueba manual: cada etapa que la app hace hoy por CPU, contra su versión en un motor de
la Jetson.

Uso — con el intérprete del venv, en la Jetson y sin sudo:

    .venv/bin/python manual_test/jetson_accelerators/stage_bench.py
    .venv/bin/python manual_test/jetson_accelerators/stage_bench.py jpeg decode

Sin argumentos corre las etapas de `bench.stages` del config.yaml de al lado; con nombres,
sólo esas. En la PC de desarrollo corre las variantes por CPU y saltea las de GStreamer
con el motivo, que sirve para probar el script, no para medir.

Por cada variante imprime:

    wall ms/frame   tiempo de reloj por frame
    CPU ms/frame    tiempo de CPU del proceso entero por frame, sumando todos sus hilos
                    —los de GStreamer también—. Es lo que la variante le saca al resto
                    de la app, y es la columna que decide.
    motores         qué motor se encendió mientras corría, según `tegrastats`

Las etapas, y de qué parte de la app sale cada una:

    resize     cv2.resize vs nvvidconv (VIC), ida y vuelta desde un array de numpy:
               el frame de una cámara GigE ya está en memoria de CPU.
    jpeg       reducir + cv2.imencode (stream MJPEG, dataset) vs nvvidconv + nvjpegenc.
    h264       los pipelines tal como los arma `rtsp_server` (`h264_sw`, `h264_hw`) y la
               variante propuesta para la fase 1: BGRx directo a nvvidconv, sin la
               conversión a I420 por CPU. Mide caudal, no latencia.
    decode     cv2.VideoCapture (rtsp_driver, video_file_driver) vs nvv4l2decoder
               (NVDEC), con y sin reducir en el VIC al salir del decodificador.
    debayer    cv2.cvtColor de Bayer a BGR (st_driver). Sólo CPU: es la referencia para
               decidir si vale moverlo a la GPU.
    remap      cv2.remap con mapas CV_16SC2 (undistort). Sólo CPU, referencia.
    letterbox  la entrada del modelo como la arma ultralytics —reducir, rellenar,
               normalizar, HWC→CHW— por CPU; con la reducción en el VIC; y en la GPU
               con torch si el venv lo tiene.

Qué mirar:
    - La columna CPU ms/frame de cada par. Multiplicada por los fps y las cámaras dice
      cuánto CPU libera mover esa etapa.
    - Que la variante de hardware encienda su motor. Una que no lo enciende está cayendo a
      software.
    - En `resize` y `jpeg`, si la ida y vuelta desde numpy se come la ganancia: en volcables
      el VIC redujo al salir del decodificador, con el frame ya en su memoria; acá el frame
      tiene que subir primero, como pasa con una cámara GigE.
    - Correr con y sin `sudo jetson_clocks`, y en el modo de potencia de producción: el
      encabezado dice cuál estaba.
    - Que los contadores de sobrecorriente del final no hayan subido: si subieron, el
      módulo recortó relojes y la corrida no vale.
"""

import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import cv2
import numpy as np
import psutil

# La raíz del repo es el primer ancestro que contiene `system/`, no un número fijo de
# niveles: así el script sobrevive a que lo muevan de carpeta.
_REPO_ROOT = next(
    (p for p in Path(__file__).resolve().parents if (p / "system").is_dir()),
    None,
)
if _REPO_ROOT is None:
    raise SystemExit("No se encontró la raíz del repo: ningún directorio padre tiene system/.")
sys.path.insert(0, str(_REPO_ROOT))

from engine_watch import EngineWatch, format_peaks     # noqa: E402
from system.config_manager import ConfigManager        # noqa: E402
from system.video import rtsp_server                   # noqa: E402
from tools.jetson import board, capabilities           # noqa: E402
from tools.jetson.tegrastats import TegrastatsReader   # noqa: E402

try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import Gst
    Gst.init(None)
    _GST_ERROR = ""
except (ImportError, ValueError) as e:
    Gst = None
    _GST_ERROR = f"GStreamer no disponible: {e}"

_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"

_NS_PER_S = 1_000_000_000
_FRAME_NS = _NS_PER_S // 30         # sello de cada buffer; el ritmo no importa, sync=false
_PULL_TIMEOUT_S = 5.0
_EOS_TIMEOUT_S = 120.0
_SYNTHETIC_FRAMES = 30              # frames distintos que se repiten en las etapas
_LETTERBOX_FILL = 114               # gris de relleno de ultralytics
_GENERATED_VIDEO_FPS = 10

# Intrínsecos de mentira para `remap`: lo que se mide es el costo, no la corrección.
_REMAP_DIST_COEFFS = np.array([[-0.28, 0.07, 0.0, 0.0, 0.0]])

_ALL_STAGES = ("resize", "jpeg", "h264", "decode", "debayer", "remap", "letterbox")


class _Result(NamedTuple):
    stage: str
    variant: str
    wall_ms: float | None
    cpu_ms: float | None
    engines: str
    note: str


class _Settings(NamedTuple):
    width_px: int
    height_px: int
    frame_count: int
    warmup_frames: int
    output_size_px: tuple[int, int]
    jpeg_width_px: int
    jpeg_quality: int
    model_size_px: tuple[int, int]      # (ancho, alto)
    video_path: str
    interval_s: float


# ── Frames de entrada ────────────────────────────────────────────────────────

def _build_frames(width_px: int, height_px: int) -> list[np.ndarray]:
    """
    `_SYNTHETIC_FRAMES` frames distintos: gradiente, un bloque que se mueve y una banda de
    ruido. Un frame fijo dejaría al H.264 sin nada que codificar y al JPEG sin detalle.
    """
    rng = np.random.default_rng(0)
    gradient = np.linspace(0, 255, width_px, dtype=np.float32)
    base = np.repeat(gradient[None, :, None], height_px, axis=0).repeat(3, axis=2)
    frames = []
    for i in range(_SYNTHETIC_FRAMES):
        frame = base.astype(np.uint8)
        x_px = int((i / _SYNTHETIC_FRAMES) * (width_px - width_px // 5))
        cv2.rectangle(frame, (x_px, height_px // 4), (x_px + width_px // 5, height_px // 2),
                      (40, 180, 220), -1)
        band = slice(height_px * 2 // 3, height_px * 5 // 6)
        frame[band] = rng.integers(0, 256, frame[band].shape, dtype=np.uint8)
        frames.append(frame)
    return frames


# ── Medición ─────────────────────────────────────────────────────────────────

def _cpu_s(process: psutil.Process) -> float:
    times = process.cpu_times()
    return times.user + times.system


def _measure(settings: _Settings, run_one: Callable[[int], None]) -> tuple[float, float]:
    """(wall ms/frame, CPU ms/frame) de `run_one(i)` sobre `frame_count` frames."""
    for i in range(settings.warmup_frames):
        run_one(i)
    process = psutil.Process()
    cpu_start_s, wall_start_s = _cpu_s(process), time.perf_counter()
    for i in range(settings.frame_count):
        run_one(i)
    wall_s, cpu_s = time.perf_counter() - wall_start_s, _cpu_s(process) - cpu_start_s
    return wall_s * 1000 / settings.frame_count, cpu_s * 1000 / settings.frame_count


def _run_variant(stage: str, variant: str, settings: _Settings, reader: TegrastatsReader,
                 body: Callable[[], tuple[float, float, str]]) -> _Result:
    """Corre una variante con `tegrastats` mirando. Un error es una fila, no una caída."""
    with EngineWatch(reader, settings.interval_s) as watch:
        try:
            wall_ms, cpu_ms, note = body()
        except Exception as e:
            wall_ms, cpu_ms, note = None, None, f"error: {e}"
    result = _Result(stage, variant, wall_ms, cpu_ms, format_peaks(watch), note)
    _print_result(result)
    return result


def _skipped(stage: str, variant: str, reason: str) -> _Result:
    result = _Result(stage, variant, None, None, "-", reason)
    _print_result(result)
    return result


def _print_result(result: _Result):
    wall = f"{result.wall_ms:8.2f}" if result.wall_ms is not None else "       -"
    cpu = f"{result.cpu_ms:8.2f}" if result.cpu_ms is not None else "       -"
    print(f"  {result.stage:9s} {result.variant:22s} {wall} {cpu}   "
          f"{result.engines:32s} {result.note}")


# ── GStreamer ────────────────────────────────────────────────────────────────

class _AppPipeline:
    """
    Pipeline en PLAYING con un `appsrc` llamado `source` y/o un `appsink` llamado `sink`.

    `push` y `pull` de a un frame dan la ida y vuelta síncrona que vería la app si una
    función le pasara el frame al motor y esperara el resultado.
    """

    def __init__(self, launch: str, source_caps: str = ""):
        self._pipeline = Gst.parse_launch(launch)
        self._source = self._pipeline.get_by_name("source")
        self._sink = self._pipeline.get_by_name("sink")
        if self._source is not None and source_caps:
            self._source.set_property("caps", Gst.Caps.from_string(source_caps))
        self._pts_ns = 0
        if self._pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self.close()
            raise RuntimeError(self._bus_error() or "el pipeline no pasó a PLAYING")

    def push(self, array: np.ndarray):
        buffer = Gst.Buffer.new_wrapped(array.tobytes())
        buffer.pts = buffer.dts = self._pts_ns
        buffer.duration = _FRAME_NS
        self._pts_ns += _FRAME_NS
        flow = self._source.emit("push-buffer", buffer)
        if flow != Gst.FlowReturn.OK:
            raise RuntimeError(self._bus_error() or f"push-buffer devolvió {flow}")

    def pull(self) -> tuple[bytes, object] | None:
        """(bytes, caps) del próximo sample; None en el EOS."""
        sample = self._sink.emit("try-pull-sample", int(_PULL_TIMEOUT_S * _NS_PER_S))
        if sample is None:
            if self._sink.get_property("eos"):
                return None
            raise RuntimeError(self._bus_error() or f"sin sample en {_PULL_TIMEOUT_S} s")
        buffer = sample.get_buffer()
        is_mapped, info = buffer.map(Gst.MapFlags.READ)
        if not is_mapped:
            raise RuntimeError("no se pudo mapear el buffer")
        try:
            data = bytes(info.data)
        finally:
            buffer.unmap(info)
        return data, sample.get_caps()

    def finish(self):
        """Manda EOS y espera a que el pipeline lo procese entero."""
        self._source.emit("end-of-stream")
        message = self._pipeline.get_bus().timed_pop_filtered(
            int(_EOS_TIMEOUT_S * _NS_PER_S), Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if message is None:
            raise RuntimeError(f"sin EOS en {_EOS_TIMEOUT_S:g} s")
        if message.type == Gst.MessageType.ERROR:
            raise RuntimeError(message.parse_error()[0].message)

    def close(self):
        self._pipeline.set_state(Gst.State.NULL)

    def _bus_error(self) -> str:
        message = self._pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR)
        return message.parse_error()[0].message if message is not None else ""


def _bgrx_caps(width_px: int, height_px: int) -> str:
    return f"video/x-raw,format=BGRx,width={width_px},height={height_px},framerate=30/1"


def _to_bgr(data: bytes, caps: object) -> np.ndarray:
    """Un buffer BGRx de nvvidconv como array BGR, respetando el relleno de cada fila."""
    structure = caps.get_structure(0)
    width_px, height_px = structure.get_value("width"), structure.get_value("height")
    row_bytes = len(data) // height_px
    rows = np.frombuffer(data, np.uint8)[:row_bytes * height_px].reshape(height_px, row_bytes)
    return cv2.cvtColor(rows[:, :width_px * 4].reshape(height_px, width_px, 4),
                        cv2.COLOR_BGRA2BGR)


def _round_trip(settings: _Settings, frames_bgrx: list[np.ndarray], launch: str,
                convert: Callable[[bytes, object], object]) -> tuple[float, float, object]:
    """Mide push + pull de a un frame por `launch`. Devuelve también la última salida."""
    pipeline = _AppPipeline(launch, _bgrx_caps(settings.width_px, settings.height_px))
    last = {}

    def run_one(i: int):
        pipeline.push(frames_bgrx[i % len(frames_bgrx)])
        pulled = pipeline.pull()
        last["output"] = convert(*pulled)

    try:
        wall_ms, cpu_ms = _measure(settings, run_one)
    finally:
        pipeline.close()
    return wall_ms, cpu_ms, last.get("output")


def _to_bgrx(frames: list[np.ndarray]) -> list[np.ndarray]:
    return [cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA) for frame in frames]


# ── Etapas ───────────────────────────────────────────────────────────────────

def _bench_resize(settings: _Settings, frames: list[np.ndarray],
                  reader: TegrastatsReader) -> list[_Result]:
    out_w, out_h = settings.output_size_px
    results = []

    def cpu():
        wall_ms, cpu_ms = _measure(settings, lambda i: cv2.resize(
            frames[i % len(frames)], (out_w, out_h), interpolation=cv2.INTER_LINEAR))
        return wall_ms, cpu_ms, f"{out_w}x{out_h} bilineal"
    results.append(_run_variant("resize", "cpu cv2.resize", settings, reader, cpu))

    if Gst is None:
        return results + [_skipped("resize", "vic nvvidconv", _GST_ERROR)]

    def vic():
        # BGR→BGRx va dentro de la medición: nvvidconv no acepta BGR empaquetado.
        launch = (f"appsrc name=source format=time ! nvvidconv interpolation-method=1 "
                  f"! video/x-raw,format=BGRx,width={out_w},height={out_h} "
                  f"! appsink name=sink sync=false")
        pipeline = _AppPipeline(launch, _bgrx_caps(settings.width_px, settings.height_px))

        def run_one(i: int):
            pipeline.push(cv2.cvtColor(frames[i % len(frames)], cv2.COLOR_BGR2BGRA))
            _to_bgr(*pipeline.pull())

        try:
            wall_ms, cpu_ms = _measure(settings, run_one)
        finally:
            pipeline.close()
        return wall_ms, cpu_ms, "incluye BGR a BGRx y vuelta"
    results.append(_run_variant("resize", "vic nvvidconv", settings, reader, vic))
    return results


def _jpeg_size_px(settings: _Settings) -> tuple[int, int]:
    """Ancho del MJPEG y alto con la misma relación de aspecto, en pares."""
    width_px = min(settings.jpeg_width_px, settings.width_px)
    height_px = round(settings.height_px * width_px / settings.width_px)
    return width_px - width_px % 2, height_px - height_px % 2


def _bench_jpeg(settings: _Settings, frames: list[np.ndarray],
                reader: TegrastatsReader) -> list[_Result]:
    jpeg_w, jpeg_h = _jpeg_size_px(settings)
    quality = settings.jpeg_quality
    results = []

    def cpu():
        sizes = []

        def run_one(i: int):
            small = cv2.resize(frames[i % len(frames)], (jpeg_w, jpeg_h),
                               interpolation=cv2.INTER_AREA)
            _, buffer = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, quality])
            sizes.append(buffer.size)

        wall_ms, cpu_ms = _measure(settings, run_one)
        return wall_ms, cpu_ms, f"{jpeg_w}x{jpeg_h} q{quality}, {np.mean(sizes) / 1024:.0f} KB"
    results.append(_run_variant("jpeg", "cpu resize+imencode", settings, reader, cpu))

    if Gst is None:
        return results + [_skipped("jpeg", "nvjpg nvjpegenc", _GST_ERROR)]

    def nvjpg():
        launch = (f"appsrc name=source format=time ! nvvidconv interpolation-method=1 "
                  f"! video/x-raw(memory:NVMM),format=I420,width={jpeg_w},height={jpeg_h} "
                  f"! nvjpegenc quality={quality} ! appsink name=sink sync=false")
        sizes = []
        wall_ms, cpu_ms, _ = _round_trip(settings, _to_bgrx(frames), launch,
                                         lambda data, _caps: sizes.append(len(data)))
        return wall_ms, cpu_ms, (f"{jpeg_w}x{jpeg_h} q{quality}, "
                                 f"{np.mean(sizes) / 1024:.0f} KB; frames ya en BGRx")
    results.append(_run_variant("jpeg", "nvjpg nvjpegenc", settings, reader, nvjpg))
    return results


def _encode_throughput(settings: _Settings, frames: list[np.ndarray], launch: str,
                       caps: str) -> tuple[float, float, str]:
    """Empuja todos los frames sin esperar salida y mide hasta el EOS: caudal del encoder."""
    pipeline = _AppPipeline(launch, caps)
    process = psutil.Process()
    count = settings.frame_count
    try:
        cpu_start_s, wall_start_s = _cpu_s(process), time.perf_counter()
        for i in range(count):
            pipeline.push(frames[i % len(frames)])
        pipeline.finish()
        wall_s, cpu_s = time.perf_counter() - wall_start_s, _cpu_s(process) - cpu_start_s
    finally:
        pipeline.close()
    return wall_s * 1000 / count, cpu_s * 1000 / count, "caudal, con arranque incluido"


def _bench_h264(settings: _Settings, frames: list[np.ndarray],
                reader: TegrastatsReader) -> list[_Result]:
    if Gst is None:
        return [_skipped("h264", name, _GST_ERROR)
                for name in ("h264_sw", "h264_hw", "h264_hw bgrx")]
    width_px, height_px = settings.width_px, settings.height_px
    bgr_caps = f"video/x-raw,format=BGR,width={width_px},height={height_px},framerate=30/1"
    results = []

    for codec in ("h264_sw", "h264_hw"):
        # El pipeline tal como lo arma el servidor RTSP, con un fakesink donde iría el
        # cliente: lo que se mide es lo que se entrega.
        launch = rtsp_server._build_launch_string(codec) + " ! fakesink sync=false"
        results.append(_run_variant(
            "h264", f"{codec} (rtsp_server)", settings, reader,
            lambda launch=launch: _encode_throughput(settings, frames, launch, bgr_caps)))

    def proposed():
        launch = ("appsrc name=source is-live=true block=true format=GST_FORMAT_TIME "
                  "! nvvidconv ! video/x-raw(memory:NVMM),format=NV12 ! nvv4l2h264enc "
                  "! rtph264pay config-interval=1 pt=96 ! fakesink sync=false")
        frames_bgrx = _to_bgrx(frames)
        wall_ms, cpu_ms, note = _encode_throughput(
            settings, frames_bgrx, launch, _bgrx_caps(width_px, height_px))
        return wall_ms, cpu_ms, note + "; frames ya en BGRx"
    results.append(_run_variant("h264", "h264_hw bgrx (fase 1)", settings, reader, proposed))
    return results


def _resolve_video(settings: _Settings, workdir: Path) -> tuple[str, str]:
    """(ruta del video, nota). Sin `video_path`, fabrica uno con x264enc."""
    if settings.video_path:
        path = Path(settings.video_path)
        if not path.is_absolute():
            path = _REPO_ROOT / path
        return (str(path), f"{path.name}") if path.exists() else ("", f"no existe {path}")
    if Gst is None:
        return "", _GST_ERROR
    path = workdir / "bench.mp4"
    frame_total = settings.frame_count + settings.warmup_frames + _SYNTHETIC_FRAMES
    error = capabilities.check_pipeline(
        f"videotestsrc num-buffers={frame_total} pattern=smpte "
        f"! video/x-raw,format=I420,width={settings.width_px},height={settings.height_px},"
        f"framerate={_GENERATED_VIDEO_FPS}/1 ! x264enc speed-preset=ultrafast key-int-max=30 "
        f"! h264parse ! mp4mux ! filesink location={path.as_posix()}",
        timeout_s=_EOS_TIMEOUT_S)
    return ("", f"no se pudo fabricar el video: {error}") if error else (str(path), "generado")


def _bench_decode(settings: _Settings, reader: TegrastatsReader) -> list[_Result]:
    out_w, out_h = settings.output_size_px
    results = []
    with tempfile.TemporaryDirectory() as workdir:
        video_path, video_note = _resolve_video(settings, Path(workdir))
        if not video_path:
            return [_skipped("decode", "todas", video_note)]

        for variant, resize in (("cpu cv2.VideoCapture", False), ("cpu + cv2.resize", True)):
            def cpu(resize=resize):
                capture = cv2.VideoCapture(video_path, cv2.CAP_FFMPEG)

                def run_one(_i: int):
                    is_read, frame = capture.read()
                    if not is_read:
                        capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        is_read, frame = capture.read()
                    if resize and is_read:
                        cv2.resize(frame, (out_w, out_h), interpolation=cv2.INTER_LINEAR)

                try:
                    wall_ms, cpu_ms = _measure(settings, run_one)
                finally:
                    capture.release()
                return wall_ms, cpu_ms, video_note
            results.append(_run_variant("decode", variant, settings, reader, cpu))

        if Gst is None:
            return results + [_skipped("decode", "nvdec", _GST_ERROR)]

        for variant, scale in (("nvdec", ""),
                               ("nvdec + vic resize", f",width={out_w},height={out_h}")):
            def nvdec(scale=scale):
                launch = (f"filesrc location={Path(video_path).as_posix()} ! parsebin "
                          f"! nvv4l2decoder disable-dpb=true ! nvvidconv interpolation-method=1 "
                          f"! video/x-raw,format=BGRx{scale} "
                          f"! appsink name=sink sync=false max-buffers=4")
                pipeline = _AppPipeline(launch)
                pulled = {"count": 0}

                def run_one(_i: int):
                    sample = pipeline.pull()
                    if sample is None:
                        raise RuntimeError(f"el video tiene {pulled['count']} frames; bajar "
                                           f"frame_count o usar uno más largo")
                    _to_bgr(*sample)
                    pulled["count"] += 1

                try:
                    wall_ms, cpu_ms = _measure(settings, run_one)
                finally:
                    pipeline.close()
                return wall_ms, cpu_ms, video_note
            results.append(_run_variant("decode", variant, settings, reader, nvdec))
    return results


def _bench_debayer(settings: _Settings, frames: list[np.ndarray],
                   reader: TegrastatsReader) -> list[_Result]:
    bayers = [frame[:, :, 1].copy() for frame in frames[:4]]

    def cpu():
        wall_ms, cpu_ms = _measure(settings, lambda i: cv2.cvtColor(
            bayers[i % len(bayers)], cv2.COLOR_BAYER_RG2BGR))
        return wall_ms, cpu_ms, "referencia; sin variante de hardware todavía"
    return [_run_variant("debayer", "cpu cv2.cvtColor", settings, reader, cpu)]


def _bench_remap(settings: _Settings, frames: list[np.ndarray],
                 reader: TegrastatsReader) -> list[_Result]:
    width_px, height_px = settings.width_px, settings.height_px
    camera_matrix = np.array([[width_px, 0, width_px / 2],
                              [0, width_px, height_px / 2], [0, 0, 1]], dtype=np.float64)
    map_xy, map_interp = cv2.initUndistortRectifyMap(
        camera_matrix, _REMAP_DIST_COEFFS, None, camera_matrix,
        (width_px, height_px), cv2.CV_16SC2)

    def cpu():
        wall_ms, cpu_ms = _measure(settings, lambda i: cv2.remap(
            frames[i % len(frames)], map_xy, map_interp, cv2.INTER_LINEAR))
        return wall_ms, cpu_ms, "referencia; VPI (CUDA/VIC) en una fase siguiente"
    return [_run_variant("remap", "cpu cv2.remap", settings, reader, cpu)]


def _letterbox_geometry(settings: _Settings) -> tuple[int, int, int, int]:
    """(ancho, alto) reducidos y (x, y) donde van dentro de la entrada del modelo."""
    model_w, model_h = settings.model_size_px
    scale = min(model_w / settings.width_px, model_h / settings.height_px)
    resized_w = round(settings.width_px * scale)
    resized_h = round(settings.height_px * scale)
    return resized_w, resized_h, (model_w - resized_w) // 2, (model_h - resized_h) // 2


def _pad_and_normalize(resized_bgr: np.ndarray, settings: _Settings,
                       offset_px: tuple[int, int]) -> np.ndarray:
    """Relleno gris, BGR→RGB, 0–1 en float32 y HWC→CHW: lo que sigue a la reducción."""
    model_w, model_h = settings.model_size_px
    canvas = np.full((model_h, model_w, 3), _LETTERBOX_FILL, np.uint8)
    x_px, y_px = offset_px
    canvas[y_px:y_px + resized_bgr.shape[0], x_px:x_px + resized_bgr.shape[1]] = resized_bgr
    return np.ascontiguousarray(canvas[:, :, ::-1].transpose(2, 0, 1), dtype=np.float32) / 255.0


def _bench_letterbox(settings: _Settings, frames: list[np.ndarray],
                     reader: TegrastatsReader) -> list[_Result]:
    resized_w, resized_h, x_px, y_px = _letterbox_geometry(settings)
    model_w, model_h = settings.model_size_px
    geometry = f"{settings.width_px}x{settings.height_px} a {model_w}x{model_h}"
    results = []

    def cpu():
        def run_one(i: int):
            resized = cv2.resize(frames[i % len(frames)], (resized_w, resized_h),
                                 interpolation=cv2.INTER_LINEAR)
            _pad_and_normalize(resized, settings, (x_px, y_px))
        wall_ms, cpu_ms = _measure(settings, run_one)
        return wall_ms, cpu_ms, geometry
    results.append(_run_variant("letterbox", "cpu (como ultralytics)", settings, reader, cpu))

    if Gst is None:
        results.append(_skipped("letterbox", "vic resize + cpu", _GST_ERROR))
    else:
        def vic():
            launch = (f"appsrc name=source format=time ! nvvidconv interpolation-method=1 "
                      f"! video/x-raw,format=BGRx,width={resized_w},height={resized_h} "
                      f"! appsink name=sink sync=false")
            wall_ms, cpu_ms, _ = _round_trip(
                settings, _to_bgrx(frames), launch,
                lambda data, caps: _pad_and_normalize(_to_bgr(data, caps), settings,
                                                      (x_px, y_px)))
            return wall_ms, cpu_ms, geometry + "; frames ya en BGRx"
        results.append(_run_variant("letterbox", "vic resize + cpu", settings, reader, vic))

    results.append(_bench_letterbox_torch(settings, frames, reader, geometry))
    return results


def _bench_letterbox_torch(settings: _Settings, frames: list[np.ndarray],
                           reader: TegrastatsReader, geometry: str) -> _Result:
    """La misma entrada armada en la GPU: sube el frame entero y todo lo demás es CUDA."""
    try:
        import torch
        import torch.nn.functional as functional
    except ImportError as e:
        return _skipped("letterbox", "torch cuda", f"sin torch: {e}")
    if not torch.cuda.is_available():
        return _skipped("letterbox", "torch cuda", "torch no ve CUDA")

    resized_w, resized_h, x_px, y_px = _letterbox_geometry(settings)
    model_w, model_h = settings.model_size_px
    pad = (x_px, model_w - resized_w - x_px, y_px, model_h - resized_h - y_px)

    def gpu():
        def run_one(i: int):
            tensor = torch.from_numpy(frames[i % len(frames)]).cuda()
            tensor = tensor.permute(2, 0, 1)[[2, 1, 0]].unsqueeze(0).float().div_(255.0)
            tensor = functional.interpolate(tensor, size=(resized_h, resized_w),
                                            mode="bilinear", align_corners=False)
            functional.pad(tensor, pad, value=_LETTERBOX_FILL / 255.0)
            torch.cuda.synchronize()
        wall_ms, cpu_ms = _measure(settings, run_one)
        return wall_ms, cpu_ms, geometry + "; queda en la GPU"
    return _run_variant("letterbox", "torch cuda", settings, reader, gpu)


# ── Principal ────────────────────────────────────────────────────────────────

def _read_settings(config: ConfigManager) -> _Settings:
    return _Settings(
        width_px=int(config.get("bench.width_px", 1920)),
        height_px=int(config.get("bench.height_px", 1080)),
        frame_count=int(config.get("bench.frame_count", 200)),
        warmup_frames=int(config.get("bench.warmup_frames", 10)),
        output_size_px=(int(config.get("bench.output_width_px", 640)),
                        int(config.get("bench.output_height_px", 360))),
        jpeg_width_px=int(config.get("bench.jpeg_width_px", 960)),
        jpeg_quality=int(config.get("bench.jpeg_quality", 80)),
        model_size_px=(int(config.get("bench.model_width_px", 640)),
                       int(config.get("bench.model_height_px", 384))),
        video_path=str(config.get("bench.video_path", "") or ""),
        interval_s=int(config.get("tegrastats_interval_ms", 100)) / 1000.0,
    )


def _print_header(settings: _Settings, reader: TegrastatsReader):
    mode = board.read_power_mode()
    pinned = board.are_clocks_pinned(board.read_clocks())
    print(f"Equipo: {board.read_module() or '(no es una Jetson)'}  "
          f"L4T {board.read_l4t_release() or '-'}  "
          f"modo {mode.get('name') or '-'}  "
          f"relojes fijados: {'-' if pinned is None else ('sí' if pinned else 'no')}")
    print(f"Entrada {settings.width_px}x{settings.height_px}, {settings.frame_count} frames "
          f"medidos + {settings.warmup_frames} de calentamiento por variante.")
    if reader.error:
        print(f"tegrastats: {reader.error}")
    print(f"\n  {'etapa':9s} {'variante':22s} {'wall ms':>8s} {'CPU ms':>8s}   "
          f"{'motores':32s} nota")


def main() -> int:
    if not _CONFIG_PATH.exists():
        print(f"Falta {_CONFIG_PATH}.")
        return 2
    config = ConfigManager(str(_CONFIG_PATH))
    settings = _read_settings(config)
    stages = sys.argv[1:] or list(config.get("bench.stages", _ALL_STAGES) or _ALL_STAGES)
    unknown = [stage for stage in stages if stage not in _ALL_STAGES]
    if unknown:
        print(f"Etapas desconocidas: {', '.join(unknown)}. Opciones: {', '.join(_ALL_STAGES)}.")
        return 2

    frames = _build_frames(settings.width_px, settings.height_px)
    reader = TegrastatsReader(interval_ms=round(settings.interval_s * 1000))
    reader.start()
    overcurrent_before = board.read_overcurrent_counts()
    _print_header(settings, reader)

    benches = {
        "resize": lambda: _bench_resize(settings, frames, reader),
        "jpeg": lambda: _bench_jpeg(settings, frames, reader),
        "h264": lambda: _bench_h264(settings, frames, reader),
        "decode": lambda: _bench_decode(settings, reader),
        "debayer": lambda: _bench_debayer(settings, frames, reader),
        "remap": lambda: _bench_remap(settings, frames, reader),
        "letterbox": lambda: _bench_letterbox(settings, frames, reader),
    }
    try:
        for stage in stages:
            benches[stage]()
    finally:
        reader.stop()

    overcurrent_after = board.read_overcurrent_counts()
    raised = {name: count - overcurrent_before.get(name, 0)
              for name, count in overcurrent_after.items()
              if count != overcurrent_before.get(name, 0)}
    if raised:
        print(f"\nATENCIÓN: subieron contadores de sobrecorriente {raised}: el módulo recortó "
              f"relojes durante la corrida y los tiempos no valen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
