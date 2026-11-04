import threading
import time

import numpy as np

from system.logger import logger

from .abstract_driver import AbstractCameraDriver

try:
    from pypylon import pylon, genicam
    _PYPYLON_AVAILABLE = True
    _IMPORT_ERROR = ""
except ImportError as error:
    _PYPYLON_AVAILABLE = False
    # El motivo, por lo mismo que en `st_driver`: «no está» y «está pero no cargan sus
    # DLL» se arreglan en lugares distintos y Windows levanta `ImportError` en los dos.
    #
    # Acá el caso normal es el primero: pypylon trae adentro todo el runtime de pylon
    # —los transport layer, los GenICam—, así que no depende de que haya un SDK
    # instalado. Si no se puede importar, es que el paquete no viajó.
    _IMPORT_ERROR = str(error)

# Clase de dispositivo GenICam con la que pypylon expone las GigE de Basler.
_DEVICE_CLASS_GIGE = "BaslerGigE"

_DEFAULT_EXPOSURE_TIME_US = 15000.0
_DEFAULT_GAIN = 1.5
_DEFAULT_FPS_LIMIT = 15.0

# Nombre de cada parámetro en orden de preferencia: el de SFNC 2 y después el de las ace
# GigE clásicas, que hablan SFNC 1. Las unidades son las mismas en los dos.
_EXPOSURE_NODES = ("ExposureTime", "ExposureTimeAbs")                    # µs
_FRAME_RATE_NODES = ("AcquisitionFrameRate", "AcquisitionFrameRateAbs")  # fps
_GAIN_DB_NODES = ("Gain", "GainAbs")                                     # dB
_GAIN_RAW_NODE = "GainRaw"                                               # cuentas del sensor
_TEMPERATURE_NODES = ("DeviceTemperature", "TemperatureAbs")             # °C
_MODEL_NODE = "DeviceModelName"


def _raw_from_db_2048(gain_db: float) -> float:
    """Inversa de `dB = -20·log10((2048 - GainRaw) / 2048)`."""
    return 2048 * (1 - 10 ** (-gain_db / 20))


# De dB a GainRaw para los modelos que sólo exponen GainRaw. Las fórmulas son las de la
# tabla «Gain Properties» de Basler (docs.baslerweb.com/gain), invertidas; un modelo nuevo
# se agrega con la suya. La relación cambia con cada sensor, así que uno que falta no se
# adivina: la fórmula equivocada no falla, entrega otra imagen.
_GAIN_RAW_FROM_DB = {
    "acA4024-8gc": _raw_from_db_2048,
    "acA4024-8gm": _raw_from_db_2048,
    "acA5472-5gc": _raw_from_db_2048,
    "acA5472-5gm": _raw_from_db_2048,
}

_STALL_MIN_S = 5.0        # sin frames y con la sesión viva, piso antes de reconectar
_STALL_PERIODS = 3        # períodos de frame que también tienen que pasar sin ninguno
_GAP_REPORT_MIN_S = 1.0   # un hueco más largo se informa cuando vuelven los frames
_GAP_REPORT_PERIODS = 2
_WARN_INTERVAL_S = 30.0   # tope de frecuencia de un aviso que se repite

# TlFactory y EnumerateDevices de pypylon no son thread-safe cuando varios hilos
# los llaman a la vez durante la inicialización GigE. Este lock serializa los
# connect() para evitar corrupción de heap en C++.
_pylon_connect_lock = threading.Lock()


class BaslerDriver(AbstractCameraDriver):
    """
    Implementación de AbstractCameraDriver para cámaras Basler GigE vía pypylon.

    Claves de config_cam que usa:
        address     : IP con la que se busca la cámara en la enumeración GigE
        acquisition:
            exposure_time_us  : float
            gain              : float, en dB — ver _apply_gain para las que no lo hablan
            fps_limit         : float
            packet_size_bytes : int, 0 = no se toca
            packet_delay_ns   : int, 0 = no se toca
        rotation    : opcional — la aplica la base

    **Un parámetro que la cámara no tiene se avisa, una vez.** pypylon no levanta excepción
    por un nodo inexistente: devuelve un placeholder que no se puede escribir, y saltearlo
    en silencio dejaba a la cámara con lo que tuviera guardado mientras el config decía
    otra cosa.

    **Un frame que tarda no es una cámara perdida.** Un `RetrieveResult` vencido sólo baja
    la sesión si pylon dice que la cámara se fue o si no llega ningún resultado por
    `_stall_limit_s()`. Reconectar por un hueco de medio segundo —un pico de CPU en el
    equipo— enumera y reabre la cámara justo cuando el equipo no da abasto, y el hueco
    se convertía en minutos sin imagen.
    """

    def __init__(self, config_cam: dict):
        super().__init__(config_cam)
        self._address = config_cam.get("address")
        self._acquisition = config_cam.get("acquisition", {})
        self._camera = None
        self._converter = None
        # Anti-spam del warning de temperatura (ver get_status).
        self._temp_warned = False
        # Parámetros ya avisados como no escritos. No se limpia al reconectar: es la misma
        # cámara configurada, y el aviso se repetiría en cada vuelta.
        self._unwritten_warned: set = set()
        # Último aviso por tipo y cuántos se callaron desde entonces (ver _warn_throttled).
        self._throttled: dict = {}
        self._last_result_s = time.monotonic()
        self.is_connected = False

    # ── AbstractCameraDriver interface ────────────────────────────────────────

    def connect(self) -> bool:
        if not _PYPYLON_AVAILABLE:
            logger.error(
                f"[BaslerDriver] No se pudo cargar pypylon ({_IMPORT_ERROR}). pypylon "
                f"trae su propio runtime de pylon, así que no hace falta instalar el SDK "
                f"de Basler: lo que falta es el paquete. No se puede abrir la Basler.")
            return False

        with _pylon_connect_lock:
            return self._connect_locked()

    def disconnect(self):
        try:
            if self._camera and self._camera.IsGrabbing():
                self._camera.StopGrabbing()
            if self._camera and self._camera.IsOpen():
                self._camera.Close()
        except Exception as e:
            logger.error(f"Error cerrando la Basler {self._address}: {e}")
        finally:
            # Nullificar las referencias C++ antes de que el GC de Python corra
            # los destructores — evita el "FATAL: exception not rethrown" al salir.
            self._camera = None
            self._converter = None
            self.is_connected = False

    def get_frame(self, timeout_ms: int = 500) -> np.ndarray | None:
        """
        El último frame, o None. Un vencimiento no baja la sesión: ver el docstring de la
        clase. Un frame incompleto tampoco —la cámara está respondiendo—, pero se avisa.
        """
        if not self._capture_enabled:
            return None
        if not self.is_connected or not self._camera or not self._camera.IsGrabbing():
            return None

        try:
            grab_result = self._camera.RetrieveResult(
                timeout_ms, pylon.TimeoutHandling_ThrowException
            )
            bgr_image = None
            if grab_result.GrabSucceeded():
                # `converted` debe mantenerse vivo hasta que .copy() termine.
                # GetArray() devuelve una vista de memoria C++ del PylonImage;
                # si el objeto se destruye antes del copy, el heap se corrompe.
                converted = self._converter.Convert(grab_result)
                bgr_image = converted.GetArray().copy()
                del converted
            else:
                self._warn_throttled(
                    "grab_failed",
                    f"[Basler {self._address}] Frame incompleto, descartado: "
                    f"{grab_result.ErrorDescription} (0x{grab_result.ErrorCode:08X}). "
                    f"Suele ser pérdida de paquetes GigE.")

            grab_result.Release()
            self._note_result()
            return self._deliver(bgr_image) if bgr_image is not None else None
        except genicam.TimeoutException:
            self._on_timeout()
            return None
        except genicam.GenericException as e:
            # Desconexión física u otra falla del SDK: se marca para que el thread de
            # captura dispare su bloque de reconexión en la próxima iteración.
            logger.warning(f"[Basler {self._address}] GenericException en get_frame: {e}")
            self.is_connected = False
            return None
        except Exception as e:
            logger.warning(f"[Basler {self._address}] Error en get_frame: {e}")
            self.is_connected = False
            return None

    def get_status(self) -> dict:
        """Estado para telemetría. Un 0.0 en `temperature` significa sin lectura."""
        status = {
            "connected": self.is_connected,
            "capture_enabled": self._capture_enabled,
            "temperature": 0.0,
            "fps_estimated": self._measured_fps(),
        }
        if self.is_connected and self._camera:
            try:
                status["temperature"] = self._read_temperature()
                self._temp_warned = False
            except Exception as e:
                # Sin el latch esto inunda el log.
                if not self._temp_warned:
                    logger.warning(
                        f"[Basler {self._address}] No se pudo leer la temperatura: {e}. "
                        f"Queda en 0 (sin lectura)."
                    )
                    self._temp_warned = True
        return status

    # ── Conexión ─────────────────────────────────────────────────────────────

    def _connect_locked(self) -> bool:
        # Liberar la cámara anterior antes de crear otra: sin esto cada reconexión
        # filtra el handle C++ de pypylon.
        if self._camera is not None:
            try:
                if self._camera.IsGrabbing():
                    self._camera.StopGrabbing()
                if self._camera.IsOpen():
                    self._camera.Close()
            except Exception as e:
                # Con la cámara inalcanzable el cierre no llega, y ella sigue dándose por
                # tomada hasta que vence su heartbeat: el próximo Open() puede fallar con
                # «controlled by another application» sin que haya otra aplicación.
                logger.warning(
                    f"[Basler {self._address}] No se pudo cerrar la sesión anterior: {e}")
            self._camera = None
            self._converter = None

        try:
            tl_factory = pylon.TlFactory.GetInstance()

            matched_device = None
            for info in tl_factory.EnumerateDevices():
                if (info.GetDeviceClass() == _DEVICE_CLASS_GIGE
                        and info.GetIpAddress() == self._address):
                    matched_device = info
                    break

            if matched_device is None:
                logger.error(f"No ruteable: no hay hardware Basler en la dirección {self._address}")
                return False

            self._camera = pylon.InstantCamera(tl_factory.CreateDevice(matched_device))

            self._converter = pylon.ImageFormatConverter()
            self._converter.OutputPixelFormat = pylon.PixelType_BGR8packed
            self._converter.OutputBitAlignment = pylon.OutputBitAlignment_MsbAligned

            self._camera.Open()
            self._apply_camera_params()
            # Con la captura deshabilitada se abre la cámara pero no se pide stream:
            # la sesión queda lista y el ancho de banda del bus libre.
            if self._capture_enabled:
                self._start_grabbing()

            self.is_connected = True
            # Puede ser otra cámara física, así que se permite volver a avisar si
            # la temperatura sigue sin leerse.
            self._temp_warned = False
            logger.info(f"Basler {self._address} conectada y capturando.")
            return True

        except Exception as e:
            logger.error(f"Excepción grave conectando al stream Basler {self._address}: {e}")
            self.is_connected = False
            return False

    def _start_grabbing(self):
        self._camera.StartGrabbing(
            pylon.GrabStrategy_LatestImageOnly, pylon.GrabLoop_ProvidedByUser
        )
        # El primer frame se espera desde acá: sin esto, una sesión abierta después de un
        # rato con la captura cortada se daría por trabada al primer vencimiento.
        self._last_result_s = time.monotonic()

    def _apply_capture_enabled(self):
        # Cortar el grab libera el ancho de banda GigE, que con varias cámaras sobre
        # una sola NIC es el recurso escaso. La sesión con la cámara no se cierra.
        if not self.is_connected or not self._camera:
            return
        try:
            if self._capture_enabled and not self._camera.IsGrabbing():
                self._start_grabbing()
                logger.info(f"Basler {self._address}: captura habilitada.")
            elif not self._capture_enabled and self._camera.IsGrabbing():
                self._camera.StopGrabbing()
                logger.info(f"Basler {self._address}: captura deshabilitada.")
        except Exception as e:
            logger.warning(f"[Basler {self._address}] No se pudo cambiar la captura: {e}")

    # ── Vencimientos ─────────────────────────────────────────────────────────

    def _on_timeout(self):
        """Decide si un `RetrieveResult` vencido es una cámara perdida o sólo un hueco."""
        try:
            removed = bool(self._camera.IsCameraDeviceRemoved())
        except Exception:
            removed = True
        if removed:
            logger.warning(f"[Basler {self._address}] La cámara dejó de responder y pylon "
                           f"cerró la sesión. Se reconecta.")
            self.is_connected = False
            return

        stalled_s = time.monotonic() - self._last_result_s
        if stalled_s >= self._stall_limit_s():
            logger.warning(f"[Basler {self._address}] {stalled_s:.1f} s sin frames con la "
                           f"sesión abierta. Se reconecta.")
            self.is_connected = False

    def _note_result(self):
        """Marca que la cámara entregó algo, e informa el hueco si fue largo."""
        now_s = time.monotonic()
        gap_s = now_s - self._last_result_s
        if gap_s > max(_GAP_REPORT_MIN_S, _GAP_REPORT_PERIODS * self._frame_period_s()):
            self._warn_throttled(
                "gap",
                f"[Basler {self._address}] {gap_s:.1f} s sin frames; la cámara siguió "
                f"respondiendo y no se reconectó. Un hueco así suele ser el equipo sin CPU "
                f"libre, no la red.")
        self._last_result_s = now_s

    def _frame_period_s(self) -> float:
        """Lo que tarda un frame según el config: el período de `fps_limit` o la exposición."""
        fps_limit = float(self._acquisition.get("fps_limit", _DEFAULT_FPS_LIMIT) or 0)
        exposure_s = float(self._acquisition.get(
            "exposure_time_us", _DEFAULT_EXPOSURE_TIME_US) or 0) / 1e6
        return max(1.0 / fps_limit if fps_limit > 0 else 0.0, exposure_s)

    def _stall_limit_s(self) -> float:
        return max(_STALL_MIN_S, _STALL_PERIODS * self._frame_period_s())

    def _warn_throttled(self, kind: str, message: str):
        """Un warning que se repite sale a lo sumo cada `_WARN_INTERVAL_S`, con lo que calló."""
        now_s = time.monotonic()
        last_s, silenced = self._throttled.get(kind, (None, 0))
        if last_s is not None and now_s - last_s < _WARN_INTERVAL_S:
            self._throttled[kind] = (last_s, silenced + 1)
            return
        if silenced:
            message += f" ({silenced} más desde el aviso anterior)"
        logger.warning(message)
        self._throttled[kind] = (now_s, 0)

    # ── Parámetros de la cámara ──────────────────────────────────────────────

    def _apply_camera_params(self):
        exposure_time_us = float(
            self._acquisition.get("exposure_time_us", _DEFAULT_EXPOSURE_TIME_US)
        )
        fps_limit = float(self._acquisition.get("fps_limit", _DEFAULT_FPS_LIMIT))

        # **Free-run declarado, no heredado.** El modo de disparo vive en la memoria no
        # volátil de la cámara: una que viene de otro montaje con `TriggerMode` en On abre,
        # acepta exposición y ganancia, arranca la adquisición y no entrega un solo frame.
        # pylon lo reporta como «Grab timed out», que se lee como un problema de red — es
        # una de las tres causas que su propio mensaje enumera, y la única que no se
        # arregla tocando el equipo.
        self._write_first(("TriggerMode",), "Off")
        self._write_first(("ExposureAuto",), "Off")
        self._write_first(_EXPOSURE_NODES, exposure_time_us)
        self._write_first(("GainAuto",), "Off")
        self._apply_gain()
        self._write_first(("AcquisitionFrameRateEnable",), True)
        self._write_first(_FRAME_RATE_NODES, fps_limit)

        # Tamaño de paquete y demora entre paquetes. Deciden si llega alguna imagen y son
        # de la cámara, no del equipo: un paquete más grande que el MTU de la placa se
        # descarta entero, así que el canal de control anda perfecto y no entra una sola
        # imagen. Sin esta clave la única forma de corregirlo sería el visor del
        # fabricante, y en la planta no está instalado. Vacío o 0 = no se toca.
        packet_size_bytes = int(self._acquisition.get("packet_size_bytes", 0) or 0)
        if packet_size_bytes > 0:
            self._write_first(("GevSCPSPacketSize",), packet_size_bytes)
        packet_delay_ns = int(self._acquisition.get("packet_delay_ns", 0) or 0)
        if packet_delay_ns > 0:
            self._apply_packet_delay(packet_delay_ns)

    def _apply_gain(self):
        """
        `gain` va en dB al nodo en dB; si la cámara sólo tiene `GainRaw`, convertido con
        la fórmula de su modelo y recortado al rango que ella acepta.

        El modelo se lee de la cámara y no de `model` del config, que es informativo: la
        conversión tiene que ser la del sensor que está enchufado. Un modelo que no está en
        `_GAIN_RAW_FROM_DB` deja la ganancia guardada en la cámara, y el log lo dice.
        """
        gain_db = float(self._acquisition.get("gain", _DEFAULT_GAIN))
        raw = self._first_writable((_GAIN_RAW_NODE,))
        if self._first_writable(_GAIN_DB_NODES) is not None or raw is None:
            self._write_first(_GAIN_DB_NODES, gain_db)
            return

        model = self._read_model_name()
        to_raw = _GAIN_RAW_FROM_DB.get(model)
        if to_raw is None:
            self._warn_unwritten(
                _GAIN_DB_NODES,
                f"La Basler {self._address} ({model or 'modelo sin nombre'}) expone la "
                f"ganancia sólo como GainRaw y el driver no tiene su conversión desde dB: "
                f"`gain` no se aplica y queda la guardada en la cámara.")
            return
        _, node = raw
        self._write_first((_GAIN_RAW_NODE,),
                          self._clamp_to_node(node, round(to_raw(gain_db)), "gain", gain_db))

    def _clamp_to_node(self, node, value: int, key: str, declared: float) -> int:
        """`value` dentro del rango del nodo. Un recorte se avisa con la clave del config."""
        try:
            low, high = node.GetMin(), node.GetMax()
        except Exception:
            return value
        clamped = min(max(value, low), high)
        if clamped != value:
            self._warn_unwritten(
                (key, "clamped"),
                f"`{key}: {declared}` queda fuera del rango de la Basler {self._address}: "
                f"se escribe {clamped}, su {'máximo' if clamped == high else 'mínimo'}.")
        return clamped

    def _read_model_name(self) -> str:
        node = self._node(_MODEL_NODE)
        try:
            return str(node.GetValue()) if node is not None and genicam.IsReadable(node) else ""
        except Exception:
            return ""

    def _apply_packet_delay(self, delay_ns: int):
        """
        `GevSCPD` va en ticks del reloj de la cámara, no en ns: se convierte con su frecuencia.

        Sin la frecuencia no se escribe. Un número en otra unidad no falla: cambia la demora
        —125 veces más larga en una ace a 125 MHz— y lo que se ve es una cámara lenta.
        """
        try:
            tick_hz = float(self._camera.GevTimestampTickFrequency.GetValue())
        except Exception:
            tick_hz = 0.0
        if tick_hz <= 0:
            self._warn_unwritten(
                ("GevSCPD",),
                f"La Basler {self._address} no informa GevTimestampTickFrequency: sin ella "
                f"`packet_delay_ns` no se puede pasar a ticks y no se aplica.")
            return
        self._write_first(("GevSCPD",), max(1, round(delay_ns * tick_hz / 1e9)))

    def _read_temperature(self) -> float:
        for name in _TEMPERATURE_NODES:
            node = self._node(name)
            if node is not None and genicam.IsReadable(node):
                return float(node.GetValue())
        raise RuntimeError(f"no tiene {' ni '.join(_TEMPERATURE_NODES)} legible")

    def _write_first(self, names: tuple, value: str | float | bool) -> bool:
        """
        Escribe `value` en el primero de `names` que la cámara deje escribir.

        Ninguno escribible se avisa una vez; un valor que la cámara rechaza, también.
        Devuelve si se escribió.
        """
        found = self._first_writable(names)
        if found is None:
            self._warn_unwritten(
                names,
                f"La Basler {self._address} no tiene {' ni '.join(names)} escribible: "
                f"queda el valor guardado en la cámara.")
            return False
        name, node = found
        try:
            node.SetValue(value)
            return True
        except Exception as e:
            self._warn_unwritten(
                names, f"No se pudo escribir {name}={value} en la Basler {self._address}: {e}")
            return False

    def _first_writable(self, names: tuple):
        """El primer `(nombre, nodo)` de `names` que se puede escribir, o None."""
        for name in names:
            node = self._node(name)
            if node is not None and genicam.IsWritable(node):
                return name, node
        return None

    def _node(self, name: str):
        # pypylon devuelve un placeholder por un nodo que la cámara no tiene; una versión
        # vieja levanta excepción. Los dos casos son «no está».
        try:
            return getattr(self._camera, name)
        except Exception:
            return None

    def _warn_unwritten(self, names: tuple, message: str):
        if names in self._unwritten_warned:
            return
        self._unwritten_warned.add(names)
        logger.warning(message)
