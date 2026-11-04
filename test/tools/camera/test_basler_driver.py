"""
Tests del driver Basler: qué parámetros llegan a la cámara y cuándo se da por perdida.

No hay cámara ni pypylon de verdad: `pylon` y `genicam` se reemplazan por dobles y la
cámara se inyecta ya abierta, sin pasar por `connect()`. Los dobles se instalan con
`raising=False` porque sin pypylon el driver no define esos nombres, y la suite corre
igual en un equipo sin él. La cámara doble reproduce lo que hace pypylon con un nodo que
no existe —un placeholder que no se puede leer ni escribir, sin excepción—, que es justo
lo que hacía que una ace GigE clásica ignorara el config sin avisar.
"""

import numpy as np
import pytest

from tools.camera import basler_driver

_ADDRESS = "192.168.0.5"


class _GenericException(Exception):
    pass


class _TimeoutException(_GenericException):
    pass


class _Genicam:
    GenericException = _GenericException
    TimeoutException = _TimeoutException

    @staticmethod
    def IsWritable(node) -> bool:
        return node.writable

    @staticmethod
    def IsReadable(node) -> bool:
        return node.readable


class _Pylon:
    TimeoutHandling_ThrowException = 1


class _Node:
    def __init__(self, value=None, *, writable: bool = True, readable: bool = True,
                 rejects: bool = False, value_range: tuple = (-10**9, 10**9)):
        self.value = value
        self.writable = writable
        self.readable = readable
        self._rejects = rejects
        self._range = value_range
        self.writes: list = []

    def GetMin(self):
        return self._range[0]

    def GetMax(self):
        return self._range[1]

    def SetValue(self, value):
        if self._rejects:
            raise _GenericException("fuera de rango")
        self.writes.append(value)
        self.value = value

    def GetValue(self):
        return self.value


_PLACEHOLDER = _Node(writable=False, readable=False)

# Nodos de una ace GigE clásica (SFNC 1), como la acA4024-8gc.
_SFNC1 = ("DeviceModelName", "TriggerMode", "ExposureAuto", "ExposureTimeAbs", "GainAuto", "GainRaw",
          "AcquisitionFrameRateEnable", "AcquisitionFrameRateAbs", "GevSCPSPacketSize",
          "GevSCPD", "TemperatureAbs")
# Nodos de una cámara SFNC 2.
_SFNC2 = ("DeviceModelName", "TriggerMode", "ExposureAuto", "ExposureTime", "GainAuto", "Gain",
          "AcquisitionFrameRateEnable", "AcquisitionFrameRate", "GevSCPSPacketSize",
          "GevSCPD", "DeviceTemperature")


class _GrabResult:
    def __init__(self, succeeded: bool = True):
        self._succeeded = succeeded
        self.ErrorDescription = "" if succeeded else "The buffer was incompletely grabbed."
        self.ErrorCode = 0 if succeeded else 0xE1000014

    def GrabSucceeded(self) -> bool:
        return self._succeeded

    def Release(self):
        pass


class _Converted:
    def GetArray(self):
        return np.zeros((4, 4, 3), np.uint8)


class _Converter:
    def Convert(self, _grab_result):
        return _Converted()


_TIMEOUT = object()


class _Camera:
    """InstantCamera abierta y adquiriendo. Un nodo que no está es un placeholder."""

    def __init__(self, node_names: tuple, results: tuple = (), ranges: dict | None = None,
                 **values):
        ranges = ranges or {}
        self.nodes = {name: _Node(values.get(name), **({"value_range": ranges[name]}
                                                      if name in ranges else {}))
                      for name in node_names}
        self.results = list(results)
        self.removed = False

    def __getattr__(self, name):
        nodes = self.__dict__.get("nodes", {})
        return nodes.get(name, _PLACEHOLDER)

    def IsGrabbing(self) -> bool:
        return True

    def IsCameraDeviceRemoved(self) -> bool:
        return self.removed

    def RetrieveResult(self, _timeout_ms, _handling):
        item = self.results.pop(0) if self.results else _TIMEOUT
        if item is _TIMEOUT:
            raise _TimeoutException("Grab timed out")
        return item


class _Logger:
    def __init__(self):
        self.warnings: list = []

    def warning(self, message, *_args, **_kwargs):
        self.warnings.append(message)

    def info(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass


class _Clock:
    def __init__(self):
        self.now_s = 1000.0

    def monotonic(self) -> float:
        return self.now_s


@pytest.fixture
def log(monkeypatch):
    """Instala los dobles de pypylon, un reloj manejable y un logger que guarda."""
    monkeypatch.setattr(basler_driver, "genicam", _Genicam, raising=False)
    monkeypatch.setattr(basler_driver, "pylon", _Pylon, raising=False)
    monkeypatch.setattr(basler_driver, "time", _Clock())
    fake_logger = _Logger()
    monkeypatch.setattr(basler_driver, "logger", fake_logger)
    return fake_logger


def _driver(camera: _Camera, **acquisition) -> basler_driver.BaslerDriver:
    """Un driver ya conectado a `camera`, como lo deja `connect()`."""
    driver = basler_driver.BaslerDriver({"address": _ADDRESS, "acquisition": acquisition})
    driver._camera = camera
    driver._converter = _Converter()
    driver.is_connected = True
    driver._last_result_s = basler_driver.time.monotonic()
    return driver


class TestParameterNames:
    """Cada parámetro va al nodo que la cámara tiene: el de SFNC 2 o el `*Abs` de SFNC 1."""

    def test_a_classic_ace_gets_exposure_and_frame_rate_on_the_abs_nodes(self, log):
        camera = _Camera(_SFNC1)
        _driver(camera, exposure_time_us=20000, fps_limit=3)._apply_camera_params()
        assert camera.nodes["ExposureTimeAbs"].writes == [20000.0]
        assert camera.nodes["AcquisitionFrameRateAbs"].writes == [3.0]

    def test_an_sfnc2_camera_gets_them_on_the_standard_nodes(self, log):
        camera = _Camera(_SFNC2)
        _driver(camera, exposure_time_us=20000, fps_limit=3, gain=2.0)._apply_camera_params()
        assert camera.nodes["ExposureTime"].writes == [20000.0]
        assert camera.nodes["AcquisitionFrameRate"].writes == [3.0]
        assert camera.nodes["Gain"].writes == [2.0]
        assert log.warnings == []

    def test_free_run_is_declared(self, log):
        camera = _Camera(_SFNC1)
        _driver(camera)._apply_camera_params()
        assert camera.nodes["TriggerMode"].writes == ["Off"]

    def test_a_parameter_the_camera_lacks_is_warned(self, log):
        camera = _Camera(tuple(n for n in _SFNC2 if n != "ExposureTime"))
        _driver(camera)._apply_camera_params()
        assert any("ExposureTime ni ExposureTimeAbs" in w for w in log.warnings)

    def test_the_warning_is_not_repeated_on_every_reconnect(self, log):
        camera = _Camera(tuple(n for n in _SFNC2 if n != "ExposureTime"))
        driver = _driver(camera)
        driver._apply_camera_params()
        driver._apply_camera_params()
        assert sum("ExposureTime ni ExposureTimeAbs" in w for w in log.warnings) == 1

    def test_a_value_the_camera_rejects_is_warned(self, log):
        camera = _Camera(_SFNC2)
        camera.nodes["AcquisitionFrameRate"] = _Node(rejects=True)
        _driver(camera, fps_limit=500)._apply_camera_params()
        assert any("AcquisitionFrameRate=500.0" in w for w in log.warnings)


class TestGain:
    """
    `gain` es siempre dB. Una cámara que sólo tiene `GainRaw` lo recibe convertido con la
    fórmula de su modelo, que se lee de la cámara: sin clave de config aparte.
    """

    def _classic(self, model: str = "acA4024-8gc") -> _Camera:
        return _Camera(_SFNC1, ranges={"GainRaw": (0, 1957)}, DeviceModelName=model)

    def test_a_db_node_takes_the_value_as_is(self, log):
        camera = _Camera(_SFNC2)
        _driver(camera, gain=6.0)._apply_camera_params()
        assert camera.nodes["Gain"].writes == [6.0]

    def test_a_db_node_named_gain_abs_also_takes_it(self, log):
        camera = _Camera(tuple(n for n in _SFNC1 if n != "GainRaw") + ("GainAbs",))
        _driver(camera, gain=6.0)._apply_camera_params()
        assert camera.nodes["GainAbs"].writes == [6.0]

    def test_an_aca4024_gets_the_raw_value_of_its_formula(self, log):
        """6 dB es la mitad de la amplitud: (2048 - raw) / 2048 ≈ 0,5."""
        camera = self._classic()
        _driver(camera, gain=6.0)._apply_camera_params()
        assert camera.nodes["GainRaw"].writes == [1022]
        assert log.warnings == []

    def test_the_raw_value_reads_back_as_the_declared_db(self, log):
        camera = self._classic()
        _driver(camera, gain=12.0)._apply_camera_params()
        raw = camera.nodes["GainRaw"].writes[0]
        assert -20 * np.log10((2048 - raw) / 2048) == pytest.approx(12.0, abs=0.01)

    def test_zero_db_is_zero_raw(self, log):
        camera = self._classic()
        _driver(camera, gain=0.0)._apply_camera_params()
        assert camera.nodes["GainRaw"].writes == [0]

    def test_a_gain_above_the_camera_range_is_clamped_and_said(self, log):
        camera = self._classic()
        _driver(camera, gain=40.0)._apply_camera_params()
        assert camera.nodes["GainRaw"].writes == [1957]
        assert any("`gain: 40.0`" in w and "máximo" in w for w in log.warnings)

    def test_a_model_without_a_formula_is_not_guessed(self, log):
        camera = self._classic(model="acA9999-1gc")
        _driver(camera, gain=6.0)._apply_camera_params()
        assert camera.nodes["GainRaw"].writes == []
        assert any("acA9999-1gc" in w and "GainRaw" in w for w in log.warnings)

    def test_the_model_comes_from_the_camera_not_the_config(self, log):
        """`model` del config es informativo: la fórmula es la del sensor enchufado."""
        camera = self._classic(model="acA9999-1gc")
        driver = basler_driver.BaslerDriver({"address": _ADDRESS, "model": "acA4024-8gc",
                                             "acquisition": {"gain": 6.0}})
        driver._camera = camera
        driver._apply_camera_params()
        assert camera.nodes["GainRaw"].writes == []


class TestPacketDelay:
    """`packet_delay_ns` llega a `GevSCPD` convertido a ticks del reloj de la cámara."""

    def test_the_delay_is_converted_with_the_tick_frequency(self, log):
        camera = _Camera(_SFNC1 + ("GevTimestampTickFrequency",),
                         GevTimestampTickFrequency=125_000_000)
        _driver(camera, packet_delay_ns=10_000)._apply_camera_params()
        assert camera.nodes["GevSCPD"].writes == [1250]

    def test_without_the_tick_frequency_it_is_not_written(self, log):
        camera = _Camera(_SFNC1)
        _driver(camera, packet_delay_ns=10_000)._apply_camera_params()
        assert camera.nodes["GevSCPD"].writes == []
        assert any("GevTimestampTickFrequency" in w for w in log.warnings)

    def test_zero_leaves_the_camera_as_it_is(self, log):
        camera = _Camera(_SFNC1 + ("GevTimestampTickFrequency",),
                         GevTimestampTickFrequency=125_000_000)
        _driver(camera, packet_delay_ns=0)._apply_camera_params()
        assert camera.nodes["GevSCPD"].writes == []


class TestTemperature:
    def test_a_classic_ace_reads_temperature_abs(self, log):
        camera = _Camera(_SFNC1, TemperatureAbs=41.5)
        assert _driver(camera).get_status()["temperature"] == pytest.approx(41.5)

    def test_without_any_temperature_node_it_reports_zero(self, log):
        camera = _Camera(("TriggerMode",))
        assert _driver(camera).get_status()["temperature"] == 0.0


class TestStalls:
    """
    Un frame que tarda no baja la sesión. Reconectar por cada vencimiento de medio segundo
    —un pico de CPU del equipo— reabría la cámara justo cuando no había CPU para hacerlo,
    y la cámara quedaba en loop de conexión.
    """

    def test_a_single_timeout_keeps_the_session(self, log):
        driver = _driver(_Camera(_SFNC1))
        assert driver.get_frame() is None
        assert driver.is_connected

    def test_a_frame_after_a_timeout_is_delivered(self, log):
        driver = _driver(_Camera(_SFNC1, results=(_TIMEOUT, _GrabResult())))
        driver.get_frame()
        assert driver.get_frame() is not None
        assert driver.is_connected

    def test_a_removed_camera_is_dropped_at_the_first_timeout(self, log):
        camera = _Camera(_SFNC1)
        camera.removed = True
        driver = _driver(camera)
        driver.get_frame()
        assert not driver.is_connected

    def test_a_stall_past_the_limit_drops_the_session(self, log):
        driver = _driver(_Camera(_SFNC1), fps_limit=10)
        basler_driver.time.now_s += basler_driver._STALL_MIN_S + 0.1
        driver.get_frame()
        assert not driver.is_connected

    def test_a_slow_frame_rate_stretches_the_limit(self, log):
        """A 0,5 fps un frame tarda 2 s: tres períodos son 6 s, más que el piso."""
        driver = _driver(_Camera(_SFNC1), fps_limit=0.5, exposure_time_us=1000)
        basler_driver.time.now_s += basler_driver._STALL_MIN_S + 0.5
        driver.get_frame()
        assert driver.is_connected

    def test_a_long_gap_is_reported_when_frames_come_back(self, log):
        driver = _driver(_Camera(_SFNC1, results=(_GrabResult(),)), fps_limit=10)
        basler_driver.time.now_s += 3.0
        driver.get_frame()
        assert any("3.0 s sin frames" in w for w in log.warnings)

    def test_a_failed_grab_keeps_the_session_and_says_why(self, log):
        driver = _driver(_Camera(_SFNC1, results=(_GrabResult(succeeded=False),)))
        assert driver.get_frame() is None
        assert driver.is_connected
        assert any("incompletely grabbed" in w and "0xE1000014" in w for w in log.warnings)

    def test_repeated_failed_grabs_are_throttled(self, log):
        failed = tuple(_GrabResult(succeeded=False) for _ in range(5))
        driver = _driver(_Camera(_SFNC1, results=failed))
        for _ in failed:
            driver.get_frame()
        assert sum("incompletely grabbed" in w for w in log.warnings) == 1

    def test_a_failed_grab_counts_as_the_camera_answering(self, log):
        """Llegan paquetes: reconectar no arregla un frame incompleto."""
        driver = _driver(_Camera(_SFNC1, results=(_GrabResult(succeeded=False),)))
        basler_driver.time.now_s += basler_driver._STALL_MIN_S - 1
        driver.get_frame()
        basler_driver.time.now_s += basler_driver._STALL_MIN_S - 1
        driver.get_frame()
        assert driver.is_connected
