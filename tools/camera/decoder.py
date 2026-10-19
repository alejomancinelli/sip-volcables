"""
Cómo se decodifica el video de una cámara RTSP o de un archivo: por CPU con OpenCV, o con
NVDEC y el VIC de la Jetson por GStreamer.

Maquinaria genérica: no conoce la app. Lo usan los drivers que decodifican —`rtsp_driver`
y `video_file_driver`— y es el único lugar que sabe que hay dos caminos. Los dos devuelven
el mismo objeto, `Capture`, con la misma API, así que el driver no pregunta cuál le tocó.

Lo eligen tres claves de la sección `decode` de la cámara:

    backend     cpu   — OpenCV con FFmpeg, decodifica la CPU (el default)
                nvdec — nvv4l2decoder (NVDEC) y nvvidconv (VIC), por GStreamer
                auto  — nvdec si el equipo lo tiene, cpu si no
    width_px    tamaño al que sale el frame; los dos en 0 = el del video
    height_px

**El tamaño de salida es de la cámara, no del backend.** En `nvdec` lo hace el VIC al salir
del decodificador y en `cpu` un `cv2.resize`, los dos bilineales, así que cambiar de
backend cambia quién trabaja y no el frame que ve la app: el ROI, el dataset y los streams
quedan en el mismo espacio. Para la app la cámara pasa a ser de ese tamaño, como si se le
hubiera bajado la resolución.

Por qué reducir importa más que decodificar en hardware: medido en un Orin NX con
1920x1080, NVDEC solo ahorra poco, porque copiar el frame completo a numpy cuesta casi lo
mismo que decodificarlo. NVDEC con el VIC reduciendo a 640x360 cuesta la cuarta parte de
CPU que decodificar y reducir con OpenCV: el frame grande nunca sale de la memoria de la
Jetson.

`nvdec` pedido en un equipo que no lo tiene no cae a `cpu` sin avisar: se avisa al leer el
config y la cámara no abre. Una planta que cuenta con el VIC no puede pasar a consumir CPU
sin que nadie se entere.
"""

from abc import ABC, abstractmethod
from typing import NamedTuple
from urllib.parse import unquote, urlsplit, urlunsplit

import cv2
import numpy as np

from system.logger import logger

try:
    import gi
    gi.require_version("Gst", "1.0")
    from gi.repository import GLib, Gst
    _GSTREAMER_AVAILABLE = True
    _IMPORT_ERROR = ""
except (ImportError, ValueError) as e:
    _GSTREAMER_AVAILABLE = False
    _IMPORT_ERROR = str(e)

BACKEND_CPU = "cpu"
BACKEND_NVDEC = "nvdec"
BACKEND_AUTO = "auto"
BACKENDS = (BACKEND_CPU, BACKEND_NVDEC, BACKEND_AUTO)

_NVDEC_ELEMENTS = ("nvv4l2decoder", "nvvidconv")
_NS_PER_MS = 1_000_000
_US_PER_MS = 1_000
_NVVIDCONV_BILINEAR = 1     # el default de nvvidconv es vecino más cercano: pierde lo chico
_PARAMETER_SETS_EVERY_KEYFRAME = -1     # `config-interval` de los parsers h264/h265
_FILE_READ_TIMEOUT_MS = 5000
_FILE_QUEUE_BUFFERS = 2     # un archivo no descarta: el decodificador espera al lector


class DecodeSettings(NamedTuple):
    """La sección `decode` de una cámara, ya validada y con `auto` resuelto."""

    backend: str        # cpu | nvdec
    width_px: int       # 0 = el del video
    height_px: int

    @property
    def size_px(self) -> tuple[int, int] | None:
        """(ancho, alto) de salida, o None si sale al tamaño del video."""
        return (self.width_px, self.height_px) if self.width_px and self.height_px else None

    def describe(self) -> str:
        """'nvdec a 640x360', para el log de la cámara."""
        size = f"{self.width_px}x{self.height_px}" if self.size_px else "tamaño nativo"
        return f"{self.backend} a {size}"


_nvdec_reason: str | None = None


def get_nvdec_unavailable_reason() -> str:
    """'' si este equipo puede decodificar con NVDEC; si no, por qué. Se averigua una vez."""
    global _nvdec_reason
    if _nvdec_reason is None:
        if not _GSTREAMER_AVAILABLE:
            _nvdec_reason = f"GStreamer no disponible: {_IMPORT_ERROR}"
        else:
            Gst.init(None)
            missing = [name for name in _NVDEC_ELEMENTS if Gst.ElementFactory.find(name) is None]
            _nvdec_reason = (f"faltan los elementos {', '.join(missing)} (¿no es una Jetson?)"
                             if missing else "")
    return _nvdec_reason


def read_decode_settings(config_cam: dict, camera_label: str = "") -> DecodeSettings:
    """
    La sección `decode` de `config_cam`, validada. Lo que no se entiende se avisa y cae al
    default —cpu, tamaño nativo—, salvo `nvdec` en un equipo sin NVDEC, que se mantiene y
    se avisa como error: la cámara no va a abrir, y es a propósito.
    """
    section = config_cam.get("decode") or {}
    label = f"[Decoder] {camera_label}: " if camera_label else "[Decoder] "

    backend = str(section.get("backend", BACKEND_CPU) or BACKEND_CPU).strip().lower()
    if backend not in BACKENDS:
        logger.warning(f"{label}backend '{backend}' desconocido; se usa {BACKEND_CPU}. "
                       f"Opciones: {', '.join(BACKENDS)}.")
        backend = BACKEND_CPU
    if backend == BACKEND_AUTO:
        backend = BACKEND_CPU if get_nvdec_unavailable_reason() else BACKEND_NVDEC
    elif backend == BACKEND_NVDEC and get_nvdec_unavailable_reason():
        logger.error(f"{label}`decode.backend: nvdec` pero {get_nvdec_unavailable_reason()}. "
                     f"La cámara no va a abrir: usar `cpu` o `auto` en este equipo.")

    width_px = max(0, int(section.get("width_px", 0) or 0))
    height_px = max(0, int(section.get("height_px", 0) or 0))
    if bool(width_px) != bool(height_px):
        logger.warning(f"{label}`decode` necesita `width_px` y `height_px` juntos; con uno "
                       f"solo sale al tamaño del video.")
        width_px = height_px = 0
    return DecodeSettings(backend, width_px, height_px)


# ── Capture: la API que ven los drivers ──────────────────────────────────────

class Capture(ABC):
    """
    Un video abierto, con la misma API por los dos caminos.

    `grab()` avanza un frame sin convertirlo y `retrieve()` convierte el último que se
    agarró: así un driver que descarta frames no paga la conversión de los que tira.
    `read()` es las dos cosas. El frame que devuelven es propio y BGR.
    """

    @abstractmethod
    def grab(self) -> bool:
        """Avanza al frame siguiente; False si no llegó o se terminó el video."""

    @abstractmethod
    def retrieve(self) -> tuple[bool, np.ndarray | None]:
        """El frame del último `grab()`, convertido a BGR."""

    def read(self) -> tuple[bool, np.ndarray | None]:
        if not self.grab():
            return False, None
        return self.retrieve()

    @abstractmethod
    def seek(self, frame_index: int) -> bool:
        """Salta al frame `frame_index` de un archivo. False si no se pudo."""

    @abstractmethod
    def get_fps(self) -> float:
        """Frames por segundo que declara el video; 0.0 si no declara."""

    @abstractmethod
    def release(self):
        """Suelta el video. Silenciosa si ya estaba suelto."""


class _OpenCvCapture(Capture):
    """El camino por CPU: `cv2.VideoCapture`, y un `cv2.resize` si la cámara pide tamaño."""

    def __init__(self, capture: cv2.VideoCapture, size_px: tuple[int, int] | None):
        self._capture = capture
        self._size_px = size_px

    def grab(self) -> bool:
        return bool(self._capture.grab())

    def retrieve(self) -> tuple[bool, np.ndarray | None]:
        return self._resized(*self._capture.retrieve())

    def read(self) -> tuple[bool, np.ndarray | None]:
        return self._resized(*self._capture.read())

    def seek(self, frame_index: int) -> bool:
        return bool(self._capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index))

    def get_fps(self) -> float:
        return float(self._capture.get(cv2.CAP_PROP_FPS) or 0.0)

    def release(self):
        self._capture.release()

    def _resized(self, is_read: bool, frame: np.ndarray | None) -> tuple[bool, np.ndarray | None]:
        if not is_read or frame is None or self._size_px is None:
            return is_read, frame
        return True, cv2.resize(frame, self._size_px, interpolation=cv2.INTER_LINEAR)


def bgrx_to_bgr(data: bytes, width_px: int, height_px: int) -> np.ndarray:
    """
    Un buffer BGRx como array BGR propio. Respeta el relleno que nvvidconv puede dejar al
    final de cada fila, que hace que una fila mida más que `width_px * 4`.
    """
    row_bytes = len(data) // height_px
    rows = np.frombuffer(data, np.uint8)[:row_bytes * height_px].reshape(height_px, row_bytes)
    return cv2.cvtColor(rows[:, :width_px * 4].reshape(height_px, width_px, 4),
                        cv2.COLOR_BGRA2BGR)


def build_stream_launch(settings: DecodeSettings, read_timeout_ms: int) -> str:
    """
    Pipeline de una cámara RTSP: rtspsrc → NVDEC → VIC → appsink.

    - TCP fijo: por UDP una pérdida deja artefactos, y con varias cámaras en el enlace es
      lo que pasa. Es lo que se midió en la Jetson.
    - `disable-dpb` saca los frames de a uno en vez de en ráfagas.
    - El appsink guarda sólo el último y descarta el resto: la entrega always-fresh.
    """
    return (f"rtspsrc name=source latency=0 protocols=tcp "
            f"tcp-timeout={int(read_timeout_ms) * _US_PER_MS} "
            f"! parsebin ! nvv4l2decoder disable-dpb=true {_build_output(settings)} "
            f"! appsink name=sink max-buffers=1 drop=true sync=false")


def build_file_launch(settings: DecodeSettings) -> str:
    """
    Pipeline de un archivo: filesrc → NVDEC → VIC → appsink.

    No descarta: el driver decide qué frame corresponde al reloj y lee de a uno, así que el
    decodificador espera al lector. Sin `disable-dpb`, porque una grabación puede traer
    B-frames y sin el buffer saldrían fuera de orden.
    """
    return (f"filesrc name=source ! parsebin ! nvv4l2decoder {_build_output(settings)} "
            f"! appsink name=sink max-buffers={_FILE_QUEUE_BUFFERS} drop=false sync=false")


def _build_output(settings: DecodeSettings) -> str:
    """Salida del VIC: BGRx en memoria de CPU, al tamaño de la cámara si lo pide."""
    size = f",width={settings.width_px},height={settings.height_px}" if settings.size_px else ""
    return (f"! nvvidconv interpolation-method={_NVVIDCONV_BILINEAR} "
            f"! video/x-raw,format=BGRx{size}")


def split_credentials(url: str) -> tuple[str, str, str]:
    """
    (URL sin usuario ni password, usuario, password). rtspsrc los toma por propiedades:
    dejarlos en la URL los obligaría a sobrevivir el escapado del launch string.
    """
    parts = urlsplit(url)
    if parts.username is None:
        return url, "", ""
    host = parts.netloc.rsplit("@", 1)[1]
    return (urlunsplit(parts._replace(netloc=host)), unquote(parts.username),
            unquote(parts.password or ""))


class _GstCapture(Capture):
    """
    El camino por la Jetson: un pipeline de GStreamer que termina en un appsink BGRx.

    `grab()` saca el sample del appsink y `retrieve()` recién lo copia a numpy. El primer
    sample se saca al abrir —es lo que confirma que el video anda— y se devuelve en el
    primer `grab()`.
    """

    def __init__(self, pipeline, sink, read_timeout_ms: int):
        self._pipeline = pipeline
        self._sink = sink
        self._read_timeout_ns = int(read_timeout_ms) * _NS_PER_MS
        self._pending = None
        self._sample = None
        self._fps = 0.0

    def start(self, open_timeout_ms: int) -> str:
        """Arranca el pipeline y espera el primer frame: '' si llegó, el motivo si no."""
        if self._pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            return self._pop_error() or "el pipeline no arrancó"
        self._pending = self._sink.emit("try-pull-sample", int(open_timeout_ms) * _NS_PER_MS)
        if self._pending is None:
            return self._pop_error() or f"sin frames en {open_timeout_ms} ms"
        self._fps = _read_fps(self._pending.get_caps())
        return ""

    def grab(self) -> bool:
        if self._pending is not None:
            self._sample, self._pending = self._pending, None
            return True
        self._sample = self._sink.emit("try-pull-sample", self._read_timeout_ns)
        if self._sample is None:
            error = self._pop_error()
            if error:
                logger.debug(f"[Decoder] {error}")
            return False
        return True

    def retrieve(self) -> tuple[bool, np.ndarray | None]:
        if self._sample is None:
            return False, None
        buffer = self._sample.get_buffer()
        structure = self._sample.get_caps().get_structure(0)
        is_mapped, info = buffer.map(Gst.MapFlags.READ)
        if not is_mapped:
            return False, None
        try:
            frame = bgrx_to_bgr(bytes(info.data), structure.get_value("width"),
                                structure.get_value("height"))
        finally:
            buffer.unmap(info)
        return True, frame

    def seek(self, frame_index: int) -> bool:
        if self._fps <= 0:
            return False
        position_ns = int(frame_index / self._fps * Gst.SECOND)
        self._pending = self._sample = None
        return bool(self._pipeline.seek_simple(
            Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE, position_ns))

    def get_fps(self) -> float:
        return self._fps

    def release(self):
        self._pending = self._sample = None
        self._pipeline.set_state(Gst.State.NULL)

    def _pop_error(self) -> str:
        message = self._pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR)
        return message.parse_error()[0].message if message is not None else ""


def _read_fps(caps) -> float:
    """El framerate que declaran las caps del frame; 0.0 si es variable o no figura."""
    is_found, numerator, denominator = caps.get_structure(0).get_fraction("framerate")
    return numerator / denominator if is_found and denominator else 0.0


def repeat_parameter_sets(element) -> None:
    """
    Hace que un parser de video repita SPS/PPS antes de cada keyframe.

    NVDEC descarta cada frame hasta ver los parámetros en el stream, y un servidor RTSP
    que los manda sólo en el SDP —lo normal— lo deja sin entregar un solo frame. Lo
    cumplen los elementos con la propiedad `config-interval` (`h264parse`, `h265parse`);
    el resto queda como está, así que sirve para cualquier codec que arme `parsebin`.
    """
    if element.find_property("config-interval") is not None:
        element.set_property("config-interval", _PARAMETER_SETS_EVERY_KEYFRAME)


def _open_gst(launch: str, properties: dict, *, open_timeout_ms: int,
              read_timeout_ms: int, is_live: bool = False) -> Capture | None:
    """
    Arma el pipeline, le pone las propiedades a `source` y espera el primer frame.
    Con `is_live` los parsers que `parsebin` arma repiten SPS/PPS en cada keyframe.
    """
    reason = get_nvdec_unavailable_reason()
    if reason:
        # Ya se avisó como error al leer el config; acá se repetiría en cada reintento.
        logger.debug(f"[Decoder] NVDEC no disponible: {reason}.")
        return None
    try:
        pipeline = Gst.parse_launch(launch)
    except GLib.Error as e:
        logger.warning(f"[Decoder] No se pudo armar el pipeline de NVDEC: {e}")
        return None
    if is_live:
        pipeline.connect("deep-element-added",
                         lambda parent_bin, sub_bin, element: repeat_parameter_sets(element))
    source = pipeline.get_by_name("source")
    for name, value in properties.items():
        if value:
            source.set_property(name, value)

    capture = _GstCapture(pipeline, pipeline.get_by_name("sink"), read_timeout_ms)
    reason = capture.start(open_timeout_ms)
    if reason:
        logger.debug(f"[Decoder] El video no abrió por NVDEC: {reason}")
        capture.release()
        return None
    return capture


# ── Apertura ─────────────────────────────────────────────────────────────────

def open_stream(url: str, settings: DecodeSettings, *, open_timeout_ms: int,
                read_timeout_ms: int) -> Capture | None:
    """
    Abre un stream RTSP por el backend de `settings`; None si no abrió.

    No loguea la URL: puede traer la password, y la que se puede mostrar la tiene el
    driver. Por CPU, el motivo de un stream que no abre FFmpeg no lo distingue; por NVDEC
    va al log en debug.
    """
    if settings.backend == BACKEND_NVDEC:
        location, user, password = split_credentials(url)
        return _open_gst(build_stream_launch(settings, read_timeout_ms),
                         {"location": location, "user-id": user, "user-pw": password},
                         open_timeout_ms=open_timeout_ms, read_timeout_ms=read_timeout_ms,
                         is_live=True)

    capture = cv2.VideoCapture(url, cv2.CAP_FFMPEG, [
        cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, open_timeout_ms,
        cv2.CAP_PROP_READ_TIMEOUT_MSEC, read_timeout_ms,
    ])
    if not capture.isOpened():
        capture.release()
        return None
    # Buffer mínimo, hasta donde el backend lo respete: sin esto, un consumidor que se
    # atrasa deja una fila de frames viejos por delante del actual.
    capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return _OpenCvCapture(capture, settings.size_px)


def open_file(path: str, settings: DecodeSettings) -> Capture | None:
    """Abre un archivo de video por el backend de `settings`; None si no abrió."""
    if settings.backend == BACKEND_NVDEC:
        return _open_gst(build_file_launch(settings), {"location": path},
                         open_timeout_ms=_FILE_READ_TIMEOUT_MS,
                         read_timeout_ms=_FILE_READ_TIMEOUT_MS)

    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        capture.release()
        return None
    return _OpenCvCapture(capture, settings.size_px)
