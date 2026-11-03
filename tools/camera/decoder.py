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

**Vistas.** Reducir el frame le quita detalle a un modelo que mira una zona chica. Una
vista es un rectángulo del frame que se corta del video **nativo**, antes de reducirlo, y
se entrega aparte con cada frame (`FrameView`). Se piden en `decode.views` —el cableado las
arma con el ROI de cada pipeline que corre en esa cámara—:

    views:
      pipeline_1: {x_px: 210, y_px: 40, width_px: 180, height_px: 220,
                   output_short_side_px: 224}      # 0 = el que pida `output_box_px`

El rectángulo está en el frame que entrega la cámara —reducido y rotado—, que es el que se
ve y se dibuja; el corte se calcula contra el tamaño nativo cuando se conoce, al abrir.
`output_short_side_px` reduce la vista conservando su forma: el lado corto a esos px. Sin él,
`output_box_px` —la entrada del modelo que la lee— dice a qué tamaño llevarla para que el
modelo no tenga que reescalarla (`output_covers` si después recorta el centro, como un
clasificador, y no si rellena, como un detector); sin ninguno de los dos, el tamaño nativo del
recorte. La imagen sale sin rotar, como el frame: rotar es del driver.

    NVDEC → tee ─┬─ VIC: reduce ───────────────► frame
                 └─ VIC: corta en nativo (5-tap) ► vista, apareada con el frame por PTS

Las reglas de este camino salen de medirlo en la Jetson —`.claude/plans/decoder-views-
jetson-findings.md`— y cada una evita una falla concreta:
  - El corte se fija **una vez**, en el probe del evento CAPS del decodificador, que es
    cuando se conoce el tamaño nativo y antes del primer buffer. Cambiarle el tamaño con
    el pipeline andando da frames negros sin ningún error, así que si el video cambia de
    tamaño el pipeline se rearma.
  - **El probe captura las ramas, nunca el pipeline**: un closure que retiene el pipeline
    arma un ciclo que el recolector no corta, y cada reapertura deja dos descriptores
    abiertos.
  - Coordenadas **pares** y al menos 16 px por lado: impares el VIC corre el recorte un
    píxel, y por debajo de 15 el error tira el pipeline entero, frame principal incluido.
    Un rectángulo que no da se rechaza al abrir, con el motivo en el log.
  - La vista se reduce con 5-tap: es lo que más se parece al `INTER_AREA` con el que
    reduce el clasificador; el bilineal del frame le agrega aliasing a una reducción x3.
  - Vista y frame se aparean por PTS, con la vista en `max-buffers=1`: con 2 queda un
    frame atrás. Una vista que no aparece se informa ausente, nunca vieja.

Por CPU es lo mismo con OpenCV: se corta del frame nativo antes del `cv2.resize`, con
`INTER_AREA` para reducir. Es el camino de la máquina de desarrollo y el de las imágenes
sueltas (`render()`).
"""

import math
from abc import ABC, abstractmethod
from collections.abc import Callable
from functools import partial
from typing import NamedTuple
from urllib.parse import unquote, urlsplit, urlunsplit

import cv2
import numpy as np

from system.logger import logger

from .abstract_driver import ROTATE_90_CCW, ROTATE_90_CW, ROTATE_180, FrameView

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
_NVVIDCONV_5TAP = 2         # el más parecido a INTER_AREA al reducir una vista
_PARAMETER_SETS_EVERY_KEYFRAME = -1     # `config-interval` de los parsers h264/h265
_FILE_READ_TIMEOUT_MS = 5000
_FILE_QUEUE_BUFFERS = 2     # un archivo no descarta: el decodificador espera al lector

_MIN_VIEW_SIDE_PX = 15 + 1  # por debajo de 15 px el VIC tira el pipeline; par
_PAIR_ATTEMPTS = 4          # re-pulls para aparear una vista; nunca hizo falta más de uno
_REPULL_TIMEOUT_MS = 100    # espera a la vista o al frame que viene detrás

# Nombres de los elementos de las ramas de vista, con el índice de la vista al final.
_VIEW_CONVERTER = "view_conv_"
_VIEW_CAPS = "view_caps_"
_VIEW_SINK = "view_sink_"

# Una rama en vivo guarda sólo el último frame: la entrega always-fresh. Un archivo no
# descarta: el decodificador espera a que el driver lea, rama por rama.
_LIVE_QUEUE = "queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0"
_LIVE_SINK = "max-buffers=1 drop=true sync=false"
_FILE_QUEUE = "queue"
_FILE_SINK = f"max-buffers={_FILE_QUEUE_BUFFERS} drop=false sync=false"


class ViewSettings(NamedTuple):
    """Una vista pedida: un rectángulo del frame que se corta del video nativo."""

    name: str
    rect_px: tuple[int, int, int, int]   # x, y, ancho, alto en el frame que entrega la cámara
    output_short_side_px: int            # lado corto de la vista; 0 = ver `output_box_px`
    # Caja a la que llevar la vista cuando no se pide un lado corto —la entrada del modelo
    # que la va a leer—: (ancho, alto), (0, 0) = el tamaño del recorte nativo. Con
    # `output_covers` la vista cubre la caja (el modelo recorta el centro); sin él entra
    # en la caja (el modelo rellena). Nunca agranda: lo que falta lo pone el modelo.
    output_box_px: tuple[int, int] = (0, 0)
    output_covers: bool = False


class DecodeSettings(NamedTuple):
    """La sección `decode` de una cámara, ya validada y con `auto` resuelto."""

    backend: str        # cpu | nvdec
    width_px: int       # 0 = el del video
    height_px: int
    views: tuple[ViewSettings, ...] = ()
    rotation: str | None = None   # la de la cámara: el rectángulo de una vista es del frame rotado

    @property
    def size_px(self) -> tuple[int, int] | None:
        """(ancho, alto) de salida, o None si sale al tamaño del video."""
        return (self.width_px, self.height_px) if self.width_px and self.height_px else None

    def describe(self) -> str:
        """'nvdec a 640x360', para el log de la cámara."""
        size = f"{self.width_px}x{self.height_px}" if self.size_px else "tamaño nativo"
        views = f", con las vistas {', '.join(view.name for view in self.views)}" \
            if self.views else ""
        return f"{self.backend} a {size}{views}"


class ViewCrop(NamedTuple):
    """Dónde se corta una vista en el video nativo, a qué tamaño sale y qué cubre."""

    left: int                            # [left, right) x [top, bottom) en el video
    top: int                             # nativo sin rotar, en coordenadas pares
    right: int
    bottom: int
    output_size: tuple[int, int]         # (ancho, alto) de la imagen entregada, sin rotar
    rect_px: tuple[int, int, int, int]   # lo que cubre, en el frame que entrega la cámara


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
    default —cpu, tamaño nativo, sin vistas—, salvo `nvdec` en un equipo sin NVDEC, que se
    mantiene y se avisa como error: la cámara no va a abrir, y es a propósito.
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
    return DecodeSettings(backend, width_px, height_px, _read_views(section.get("views"), label),
                          config_cam.get("rotation"))


def _read_views(section: object, label: str) -> tuple[ViewSettings, ...]:
    """Las vistas de `decode.views`, por nombre; lo que no es una sección se avisa y se saltea."""
    if not section:
        return ()
    if not isinstance(section, dict):
        logger.warning(f"{label}`decode.views` no es un mapa ({section!r}): sin vistas.")
        return ()
    views = []
    for name, view in sorted(section.items()):
        if not isinstance(view, dict):
            logger.warning(f"{label}la vista '{name}' no es una sección ({view!r}): se saltea.")
            continue
        rect_px = tuple(max(0, int(view.get(key, 0) or 0))
                        for key in ("x_px", "y_px", "width_px", "height_px"))
        box = view.get("output_box_px") or (0, 0)
        output_box_px = (max(0, int(box[0])), max(0, int(box[1]))) \
            if isinstance(box, (list, tuple)) and len(box) == 2 else (0, 0)
        views.append(ViewSettings(str(name), rect_px,
                                  max(0, int(view.get("output_short_side_px", 0) or 0)),
                                  output_box_px, bool(view.get("output_covers", False))))
    return tuple(views)


# ── Vistas: dónde se corta cada una ──────────────────────────────────────────

def plan_view(view: ViewSettings, *, native_size: tuple[int, int],
              output_size: tuple[int, int], rotation: str | None) -> ViewCrop | None:
    """
    El corte de una vista en el video nativo, o None si el rectángulo no da 16 px por lado.

    `output_size` es el tamaño al que sale el frame del decodificador, sin rotar. El
    rectángulo se acota al frame como el ROI del motor —un ancho o alto en 0 llega hasta el
    borde—, se lleva al frame sin rotar, se escala al nativo y se agranda hasta coordenadas
    pares. `rect_px` es lo que el corte cubre de verdad, de vuelta en el frame rotado: puede
    ser un píxel más grande que lo pedido.
    """
    native_w, native_h = native_size
    out_w, out_h = output_size
    is_turned = rotation in (ROTATE_90_CW, ROTATE_90_CCW)
    reference_w, reference_h = (out_h, out_w) if is_turned else (out_w, out_h)

    x, y, width, height = view.rect_px
    x, y = min(x, reference_w - 1), min(y, reference_h - 1)
    width = min(width or reference_w - x, reference_w - x)
    height = min(height or reference_h - y, reference_h - y)
    x1, y1, x2, y2 = _unrotate_rect((x, y, x + width, y + height), rotation, out_w, out_h)

    scale_x, scale_y = native_w / out_w, native_h / out_h
    left = _floor_even(x1 * scale_x)
    top = _floor_even(y1 * scale_y)
    right = min(_ceil_even(x2 * scale_x), native_w & ~1)
    bottom = min(_ceil_even(y2 * scale_y), native_h & ~1)
    if right - left < _MIN_VIEW_SIDE_PX or bottom - top < _MIN_VIEW_SIDE_PX:
        return None

    covered = _rotate_rect((left / scale_x, top / scale_y, right / scale_x, bottom / scale_y),
                           rotation, out_w, out_h)
    cx1, cy1, cx2, cy2 = (round(value) for value in covered)
    return ViewCrop(left, top, right, bottom,
                    compute_view_size(right - left, bottom - top, view.output_short_side_px,
                                      view.output_box_px, view.output_covers),
                    (cx1, cy1, cx2 - cx1, cy2 - cy1))


def plan_views(settings: DecodeSettings,
               native_size: tuple[int, int]) -> tuple[dict[str, ViewCrop], list[str]]:
    """({nombre: corte} de las vistas que dan, [nombres de las que no]) contra ese video."""
    output_size = settings.size_px or native_size
    plan: dict[str, ViewCrop] = {}
    rejected: list[str] = []
    for view in settings.views:
        crop = plan_view(view, native_size=native_size, output_size=output_size,
                         rotation=settings.rotation)
        if crop is None:
            rejected.append(view.name)
        else:
            plan[view.name] = crop
    return plan, rejected


def compute_view_size(crop_width_px: int, crop_height_px: int, output_short_side_px: int,
                      output_box_px: tuple[int, int] = (0, 0),
                      output_covers: bool = False) -> tuple[int, int]:
    """
    (ancho, alto) de la vista, conservando la forma del recorte.

    Un `output_short_side_px` explícito manda: el lado corto a esos px. Sin él, si hay una
    caja, la vista es la que el modelo que la lee dejaría igual al llevarla a su entrada:
    `output_covers` la cubre (como el clasificador, que después recorta el centro) y sin él
    entra en la caja (como el detector, que después rellena). **Nunca agranda**: un recorte
    más chico que la caja sale a su tamaño y lo que falta lo pone el modelo. Sin nada de
    eso, el tamaño del recorte nativo.
    """
    if output_short_side_px > 0:
        scale = output_short_side_px / min(crop_width_px, crop_height_px)
    elif output_box_px[0] > 0 and output_box_px[1] > 0:
        box_width_px, box_height_px = output_box_px
        ratios = (box_width_px / crop_width_px, box_height_px / crop_height_px)
        scale = min(1.0, max(ratios) if output_covers else min(ratios))
        if output_covers:
            return (max(1, min(crop_width_px, math.ceil(crop_width_px * scale))),
                    max(1, min(crop_height_px, math.ceil(crop_height_px * scale))))
    else:
        return crop_width_px, crop_height_px
    return max(1, round(crop_width_px * scale)), max(1, round(crop_height_px * scale))


def cut_views(native_bgr: np.ndarray, plan: dict[str, ViewCrop]) -> dict[str, FrameView]:
    """
    Las vistas cortadas del frame nativo, cada una su propio array.

    Reducir es con `INTER_AREA`, lo más parecido al 5-tap del VIC y a lo que hace el
    clasificador con su entrada. Una vista sin reducir se copia: un recorte de numpy
    retendría el frame nativo entero mientras la vista viva.
    """
    views: dict[str, FrameView] = {}
    for name, crop in plan.items():
        region = native_bgr[crop.top:crop.bottom, crop.left:crop.right]
        if (region.shape[1], region.shape[0]) == crop.output_size:
            image = region.copy()
        else:
            interpolation = (cv2.INTER_AREA if crop.output_size[0] < region.shape[1]
                             else cv2.INTER_LINEAR)
            image = cv2.resize(region, crop.output_size, interpolation=interpolation)
        views[name] = FrameView(image, crop.rect_px)
    return views


def render(native_bgr: np.ndarray,
           settings: DecodeSettings) -> tuple[np.ndarray, dict[str, FrameView]]:
    """
    (frame, vistas) de un frame nativo, como los entregaría el decodificador. Sin rotar.

    Es el camino por CPU entero en una función, para quien tiene el frame nativo y ningún
    video abierto: la corrida de imágenes sueltas. Una vista que no da no sale.
    """
    plan, _ = plan_views(settings, (native_bgr.shape[1], native_bgr.shape[0]))
    views = cut_views(native_bgr, plan)
    if settings.size_px is None:
        return native_bgr, views
    return cv2.resize(native_bgr, settings.size_px, interpolation=cv2.INTER_LINEAR), views


def _log_plan(plan: dict[str, ViewCrop], rejected: list[str], native_size: tuple[int, int]):
    native = f"{native_size[0]}x{native_size[1]}"
    for name in rejected:
        logger.error(f"[Decoder] La vista '{name}' queda por debajo de {_MIN_VIEW_SIDE_PX} px "
                     f"por lado en el video nativo de {native}: no se corta.")
    for name, crop in plan.items():
        logger.info(f"[Decoder] Vista '{name}': {crop.right - crop.left}x"
                    f"{crop.bottom - crop.top} del video de {native} a "
                    f"{crop.output_size[0]}x{crop.output_size[1]}; cubre {crop.rect_px}.")


def _unrotate_rect(corners: tuple, rotation: str | None, out_w: int, out_h: int) -> tuple:
    """(x1, y1, x2, y2) del frame rotado, llevadas al frame sin rotar de `out_w` x `out_h`."""
    x1, y1, x2, y2 = corners
    if rotation == ROTATE_90_CW:
        return y1, out_h - x2, y2, out_h - x1
    if rotation == ROTATE_90_CCW:
        return out_w - y2, x1, out_w - y1, x2
    if rotation == ROTATE_180:
        return out_w - x2, out_h - y2, out_w - x1, out_h - y1
    return corners


def _rotate_rect(corners: tuple, rotation: str | None, out_w: int, out_h: int) -> tuple:
    """La inversa de `_unrotate_rect`: del frame sin rotar al rotado."""
    x1, y1, x2, y2 = corners
    if rotation == ROTATE_90_CW:
        return out_h - y2, x1, out_h - y1, x2
    if rotation == ROTATE_90_CCW:
        return y1, out_w - x2, y2, out_w - x1
    if rotation == ROTATE_180:
        return out_w - x2, out_h - y2, out_w - x1, out_h - y1
    return corners


def _floor_even(value: float) -> int:
    return int(math.floor(value)) & ~1


def _ceil_even(value: float) -> int:
    return (int(math.ceil(value)) + 1) & ~1


def pair_by_pts(main: object, pull_main: Callable[[], object],
                pulls: dict[str, Callable[[], object]], get_pts: Callable[[object], int],
                *, attempts: int = _PAIR_ATTEMPTS) -> tuple[object, dict[str, object]]:
    """
    (frame, {nombre: vista}) apareados por PTS: las vistas que no tienen el del frame no van.

    Cada rama entrega su último sample, así que el frame y una vista pueden quedar a un
    frame de distancia. Se vuelve a pedir del lado que va atrás: la vista si es más vieja,
    el frame si la vista ya es del siguiente. Con `attempts` re-pulls por lado alcanza:
    medido, nunca hizo falta más de uno. `pull_main` y cada `pull` devuelven None si no
    llegó nada en su espera.
    """
    views = {name: pull() for name, pull in pulls.items()}
    for _ in range(attempts):
        target_pts = get_pts(main)
        is_main_behind = False
        for name, pull in pulls.items():
            sample = views[name]
            for _ in range(attempts):
                if sample is None or get_pts(sample) >= target_pts:
                    break
                sample = pull()
            views[name] = sample
            if sample is not None and get_pts(sample) > target_pts:
                is_main_behind = True
        if not is_main_behind:
            break
        newer = pull_main()
        if newer is None:
            break
        main = newer
    target_pts = get_pts(main)
    return main, {name: sample for name, sample in views.items()
                  if sample is not None and get_pts(sample) == target_pts}


# ── Capture: la API que ven los drivers ──────────────────────────────────────

class Capture(ABC):
    """
    Un video abierto, con la misma API por los dos caminos.

    `grab()` avanza un frame sin convertirlo y `retrieve()` convierte el último que se
    agarró: así un driver que descarta frames no paga la conversión de los que tira.
    `read()` es las dos cosas. El frame que devuelven es propio y BGR.

    Las vistas del último frame salen de `retrieve_views()`, después de `retrieve()` o
    `read()`; `get_view_names()` dice cuáles se están cortando, para que el driver cuente
    las que faltaron.
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

    def retrieve_views(self) -> dict[str, FrameView]:
        """Las vistas del último frame, sin rotar. Vacío sin vistas pedidas."""
        return {}

    def get_view_names(self) -> tuple[str, ...]:
        """Las vistas que se están cortando: las pedidas menos las que no dieron."""
        return ()

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
    """
    El camino por CPU: `cv2.VideoCapture`, las vistas cortadas del frame nativo y un
    `cv2.resize` si la cámara pide tamaño.
    """

    def __init__(self, capture: cv2.VideoCapture, settings: DecodeSettings):
        self._capture = capture
        self._settings = settings
        self._plan: dict[str, ViewCrop] = {}
        self._planned_size: tuple[int, int] | None = None
        self._views: dict[str, FrameView] = {}

    def grab(self) -> bool:
        return bool(self._capture.grab())

    def retrieve(self) -> tuple[bool, np.ndarray | None]:
        return self._render(*self._capture.retrieve())

    def read(self) -> tuple[bool, np.ndarray | None]:
        return self._render(*self._capture.read())

    def retrieve_views(self) -> dict[str, FrameView]:
        return self._views

    def get_view_names(self) -> tuple[str, ...]:
        return tuple(self._plan)

    def seek(self, frame_index: int) -> bool:
        return bool(self._capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index))

    def get_fps(self) -> float:
        return float(self._capture.get(cv2.CAP_PROP_FPS) or 0.0)

    def release(self):
        self._capture.release()

    def _render(self, is_read: bool, frame: np.ndarray | None) -> tuple[bool, np.ndarray | None]:
        self._views = {}
        if not is_read or frame is None:
            return is_read, frame
        if self._settings.views:
            self._views = cut_views(frame, self._get_plan(frame))
        if self._settings.size_px is None:
            return True, frame
        return True, cv2.resize(frame, self._settings.size_px, interpolation=cv2.INTER_LINEAR)

    def _get_plan(self, frame: np.ndarray) -> dict[str, ViewCrop]:
        """El corte de las vistas para el tamaño de este frame; se recalcula si cambia."""
        native_size = (frame.shape[1], frame.shape[0])
        if native_size != self._planned_size:
            self._plan, rejected = plan_views(self._settings, native_size)
            self._planned_size = native_size
            _log_plan(self._plan, rejected, native_size)
        return self._plan


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
    Pipeline de una cámara RTSP: rtspsrc → NVDEC → VIC → appsink, y una rama por vista.

    - TCP fijo: por UDP una pérdida deja artefactos, y con varias cámaras en el enlace es
      lo que pasa. Es lo que se midió en la Jetson.
    - `disable-dpb` saca los frames de a uno en vez de en ráfagas.
    - El appsink guarda sólo el último y descarta el resto: la entrega always-fresh.
    - Sin vistas es el pipeline de siempre; con vistas, el decodificador se reparte con un
      `tee` y cada rama guarda sólo su último frame.
    """
    source = (f"rtspsrc name=source latency=0 protocols=tcp "
              f"tcp-timeout={int(read_timeout_ms) * _US_PER_MS} ! parsebin ")
    if not settings.views:
        return (f"{source}! nvv4l2decoder disable-dpb=true {_build_output(settings)} "
                f"! appsink name=sink {_LIVE_SINK}")
    return (f"{source}! nvv4l2decoder name=dec disable-dpb=true ! tee name=t "
            f"{_build_branches(settings, _LIVE_QUEUE, _LIVE_SINK)}")


def build_file_launch(settings: DecodeSettings) -> str:
    """
    Pipeline de un archivo: filesrc → NVDEC → VIC → appsink, y una rama por vista.

    No descarta: el driver decide qué frame corresponde al reloj y lee de a uno, así que el
    decodificador espera al lector —con vistas, a que se lean todas las ramas—. Sin
    `disable-dpb`, porque una grabación puede traer B-frames y sin el buffer saldrían
    fuera de orden.
    """
    if not settings.views:
        return (f"filesrc name=source ! parsebin ! nvv4l2decoder {_build_output(settings)} "
                f"! appsink name=sink {_FILE_SINK}")
    return (f"filesrc name=source ! parsebin ! nvv4l2decoder name=dec ! tee name=t "
            f"{_build_branches(settings, _FILE_QUEUE, _FILE_SINK)}")


def _build_output(settings: DecodeSettings) -> str:
    """Salida del VIC: BGRx en memoria de CPU, al tamaño de la cámara si lo pide."""
    size = f",width={settings.width_px},height={settings.height_px}" if settings.size_px else ""
    return (f"! nvvidconv interpolation-method={_NVVIDCONV_BILINEAR} "
            f"! video/x-raw,format=BGRx{size}")


def _build_branches(settings: DecodeSettings, queue: str, sink: str) -> str:
    """
    Las ramas del `tee`: la del frame y una por vista. El corte y el tamaño de cada vista
    no van acá: los fija el probe cuando se conoce el tamaño nativo.
    """
    branches = [f"t. ! {queue} {_build_output(settings)} ! appsink name=sink {sink}"]
    for index in range(len(settings.views)):
        branches.append(
            f"t. ! {queue} ! nvvidconv name={_VIEW_CONVERTER}{index} "
            f"interpolation-method={_NVVIDCONV_5TAP} "
            f"! capsfilter name={_VIEW_CAPS}{index} caps=video/x-raw,format=BGRx "
            f"! appsink name={_VIEW_SINK}{index} {sink}")
    return " ".join(branches)


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


class _ViewBranch:
    """
    Una rama de vista del pipeline: sus elementos y el corte que le tocó al abrir.

    `crop` en None es una vista que no dio: la rama existe —el pipeline se armó antes de
    saber el tamaño nativo— pero sale chica y no se entrega.
    """

    def __init__(self, view: ViewSettings, converter, capsfilter, sink):
        self.view = view
        self.converter = converter
        self.capsfilter = capsfilter
        self.sink = sink
        self.crop: ViewCrop | None = None


class _NativeSize:
    """El tamaño nativo que anunció el decodificador, y si después anunció otro."""

    def __init__(self):
        self.size: tuple[int, int] | None = None
        self.has_changed = False


def _watch_native_size(decoder_element, branches: tuple[_ViewBranch, ...],
                       settings: DecodeSettings, native: _NativeSize):
    """
    Fija el corte de cada vista cuando el decodificador anuncia el tamaño nativo, antes
    del primer buffer. Un segundo tamaño distinto no se aplica —cambiarle el tamaño al
    corte en marcha da frames negros—: se marca, y el pipeline se rearma.

    El closure captura las ramas y el estado, **nunca el pipeline** ni la `Capture` que lo
    tiene: ese ciclo no lo corta el recolector y cada reapertura dejaba dos descriptores
    abiertos.
    """
    def on_event(_pad, info):
        event = info.get_event()
        if event.type != Gst.EventType.CAPS:
            return Gst.PadProbeReturn.OK
        structure = event.parse_caps().get_structure(0)
        size = (structure.get_value("width"), structure.get_value("height"))
        if native.size is None:
            native.size = size
            _apply_plan(branches, settings, size)
        elif size != native.size:
            native.has_changed = True
        return Gst.PadProbeReturn.OK

    decoder_element.get_static_pad("src").add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM, on_event)


def _apply_plan(branches: tuple[_ViewBranch, ...], settings: DecodeSettings,
                native_size: tuple[int, int]):
    """Le pone a cada rama su corte y su tamaño; la que no da sale de 16x16 y no se lee."""
    plan, rejected = plan_views(settings, native_size)
    for branch in branches:
        crop = plan.get(branch.view.name)
        branch.crop = crop
        if crop is None:
            _set_caps_size(branch.capsfilter, _MIN_VIEW_SIDE_PX, _MIN_VIEW_SIDE_PX)
            continue
        for name, value in (("left", crop.left), ("top", crop.top),
                            ("right", crop.right), ("bottom", crop.bottom)):
            branch.converter.set_property(name, value)
        _set_caps_size(branch.capsfilter, *crop.output_size)
    _log_plan(plan, rejected, native_size)


def _set_caps_size(capsfilter, width_px: int, height_px: int):
    """El tamaño de salida de una rama: sin él el VIC estira el recorte al tamaño del video."""
    capsfilter.set_property("caps", Gst.Caps.from_string(
        f"video/x-raw,format=BGRx,width={width_px},height={height_px}"))


def _read_pts(sample) -> int:
    return sample.get_buffer().pts


def _sample_to_bgr(sample) -> np.ndarray | None:
    """Un sample BGRx como array BGR propio, o None si no se pudo mapear."""
    buffer = sample.get_buffer()
    structure = sample.get_caps().get_structure(0)
    is_mapped, info = buffer.map(Gst.MapFlags.READ)
    if not is_mapped:
        return None
    try:
        return bgrx_to_bgr(bytes(info.data), structure.get_value("width"),
                           structure.get_value("height"))
    finally:
        buffer.unmap(info)


class _GstCapture(Capture):
    """
    El camino por la Jetson: un pipeline de GStreamer que termina en un appsink BGRx, y uno
    más por vista.

    `grab()` saca el sample del appsink —y apareja el de cada vista— y `retrieve()` recién
    lo copia a numpy. El primer sample se saca al abrir —es lo que confirma que el video
    anda— y se devuelve en el primer `grab()`. En vivo las vistas se aparean por PTS; en
    un archivo, que no descarta, por orden.
    """

    def __init__(self, pipeline, sink, read_timeout_ms: int, *,
                 branches: tuple[_ViewBranch, ...] = (), native: _NativeSize | None = None,
                 is_live: bool = False):
        self._pipeline = pipeline
        self._sink = sink
        self._read_timeout_ns = int(read_timeout_ms) * _NS_PER_MS
        self._branches = branches
        self._native = native
        self._is_live = is_live
        self._pending = None
        self._sample = None
        self._view_samples: dict[str, object] = {}
        self._fps = 0.0
        self._is_resize_logged = False

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
        if self._native is not None and self._native.has_changed:
            if not self._is_resize_logged:
                logger.warning("[Decoder] El video cambió de tamaño: se reabre para volver a "
                               "cortar las vistas.")
                self._is_resize_logged = True
            return False
        if self._pending is not None:
            self._sample, self._pending = self._pending, None
        else:
            self._sample = self._sink.emit("try-pull-sample", self._read_timeout_ns)
            if self._sample is None:
                error = self._pop_error()
                if error:
                    logger.debug(f"[Decoder] {error}")
                return False
        if self._branches:
            self._sample, self._view_samples = self._pair(self._sample)
        return True

    def retrieve(self) -> tuple[bool, np.ndarray | None]:
        if self._sample is None:
            return False, None
        frame = _sample_to_bgr(self._sample)
        return frame is not None, frame

    def retrieve_views(self) -> dict[str, FrameView]:
        views: dict[str, FrameView] = {}
        for branch in self._branches:
            sample = self._view_samples.get(branch.view.name)
            if sample is None or branch.crop is None:
                continue
            image = _sample_to_bgr(sample)
            if image is not None:
                views[branch.view.name] = FrameView(image, branch.crop.rect_px)
        return views

    def get_view_names(self) -> tuple[str, ...]:
        return tuple(branch.view.name for branch in self._branches if branch.crop is not None)

    def seek(self, frame_index: int) -> bool:
        if self._fps <= 0:
            return False
        position_ns = int(frame_index / self._fps * Gst.SECOND)
        self._pending = self._sample = None
        self._view_samples = {}
        return bool(self._pipeline.seek_simple(
            Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE, position_ns))

    def get_fps(self) -> float:
        return self._fps

    def release(self):
        self._pending = self._sample = None
        self._view_samples = {}
        self._pipeline.set_state(Gst.State.NULL)

    def _pair(self, main) -> tuple[object, dict[str, object]]:
        """
        (frame, {nombre: sample}) con las vistas de ese frame. Se piden todas las ramas,
        también las que no dieron: en un archivo, una rama que nadie lee frena el `tee`.
        """
        if not self._is_live:
            samples = {branch.view.name: self._pull(branch.sink, self._read_timeout_ns)
                       for branch in self._branches}
            return main, {name: sample for name, sample in samples.items() if sample is not None}
        repull_ns = _REPULL_TIMEOUT_MS * _NS_PER_MS
        return pair_by_pts(main, partial(self._pull, self._sink, repull_ns),
                           {branch.view.name: partial(self._pull, branch.sink, repull_ns)
                            for branch in self._branches},
                           _read_pts)

    def _pull(self, sink, timeout_ns: int):
        return sink.emit("try-pull-sample", timeout_ns)

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


def _open_gst(launch: str, properties: dict, settings: DecodeSettings, *,
              open_timeout_ms: int, read_timeout_ms: int,
              is_live: bool = False) -> Capture | None:
    """
    Arma el pipeline, le pone las propiedades a `source` y espera el primer frame.
    Con `is_live` los parsers que `parsebin` arma repiten SPS/PPS en cada keyframe. Con
    vistas, engancha el probe que les fija el corte.
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

    branches = tuple(
        _ViewBranch(view, pipeline.get_by_name(f"{_VIEW_CONVERTER}{index}"),
                    pipeline.get_by_name(f"{_VIEW_CAPS}{index}"),
                    pipeline.get_by_name(f"{_VIEW_SINK}{index}"))
        for index, view in enumerate(settings.views))
    native = _NativeSize() if branches else None
    if branches:
        _watch_native_size(pipeline.get_by_name("dec"), branches, settings, native)

    capture = _GstCapture(pipeline, pipeline.get_by_name("sink"), read_timeout_ms,
                          branches=branches, native=native, is_live=is_live)
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
                         settings, open_timeout_ms=open_timeout_ms,
                         read_timeout_ms=read_timeout_ms, is_live=True)

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
    return _OpenCvCapture(capture, settings)


def open_file(path: str, settings: DecodeSettings) -> Capture | None:
    """Abre un archivo de video por el backend de `settings`; None si no abrió."""
    if settings.backend == BACKEND_NVDEC:
        return _open_gst(build_file_launch(settings), {"location": path}, settings,
                         open_timeout_ms=_FILE_READ_TIMEOUT_MS,
                         read_timeout_ms=_FILE_READ_TIMEOUT_MS)

    capture = cv2.VideoCapture(path)
    if not capture.isOpened():
        capture.release()
        return None
    return _OpenCvCapture(capture, settings)
