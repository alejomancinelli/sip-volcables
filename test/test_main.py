"""Tests del cableado: la salida del proceso y el manejo de señales.

`main.py` no lo importa nadie, así que lo poco testeable de él es lo que decide sin
depender de subsistemas. Acá no se arma la aplicación ni se levanta un event loop."""

import asyncio
import signal
import threading

import numpy as np

import main
from system.camera.lens_health import (
    CONDITION_KEYS, LensHealthMonitor, Measurement, Region,
)
from system import gpio_control
from system.formats import gpio_status
from system.inference import pipeline_factory
from system.inference.abstract_pipeline import AbstractPipeline
from system.inference.hooks import EngineHooks
from system.inference.result import InferenceResult
from system.modbus import registers
from system.modbus.server import STATUS_DISABLED, STATUS_ERROR

_EXIT_CODE = 3


class _MockConfig:
    """ConfigManager mínimo: `get` con clave punteada, sin archivo ni Lock."""

    def __init__(self, **overrides):
        self._values = dict(overrides)

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)


class TestExit:
    def test_by_default_it_returns_instead_of_killing_the_process(self):
        """Matar el proceso saltea los `atexit`: no puede ser el default."""
        assert main._exit(_EXIT_CODE, _MockConfig()) == _EXIT_CODE

    def test_hard_exit_terminates_without_unwinding(self, monkeypatch):
        killed = []
        monkeypatch.setattr(main.os, "_exit", killed.append)
        main._exit(_EXIT_CODE, _MockConfig(**{"system.hard_exit": True}))
        assert killed == [_EXIT_CODE]

    def test_the_log_is_flushed_either_way(self, monkeypatch):
        """Lo último que se escribe es lo que explica por qué se cerró."""
        calls = []
        monkeypatch.setattr(main.logging, "shutdown", lambda: calls.append("shutdown"))
        monkeypatch.setattr(main.os, "_exit", lambda code: calls.append("exit"))
        main._exit(_EXIT_CODE, _MockConfig(**{"system.hard_exit": True}))
        assert calls == ["shutdown", "exit"]


class TestUnlicensedCamera:
    """El status que el cableado publica por una cámara que la licencia deja afuera."""

    def test_it_uses_the_stable_keys_of_a_driver(self):
        # Lo consumen la grilla, el bitfield que va al PLC y la serie de telemetría, y
        # ninguno pregunta de dónde salió el status. Una clave de menos acá es un KeyError
        # en el tick de telemetría, lejos de donde se escribió.
        assert set(main._UNLICENSED_CAMERA_STATUS) >= {
            "connected", "capture_enabled", "temperature", "fps_estimated"}

    def test_it_reads_as_a_camera_that_cannot_operate(self):
        status = main._UNLICENSED_CAMERA_STATUS
        assert status["connected"] is False
        assert status["capture_enabled"] is False

    def test_it_says_why(self):
        """Sin motivo, la pantalla muestra una cámara caída y mandan a revisar un cable."""
        assert main._UNLICENSED_CAMERA_STATUS.get("error")


class TestUnlicensedCameraOnScreen:
    """
    Que la cámara fuera de cupo llegue al widget del monitor.

    El status ya viaja por `_on_camera_status()`, que alimenta los chips de la UI, el FPS
    del diagnóstico y la palabra que va al PLC. Lo que faltaba es el widget del área
    central: se alimenta conectando la señal de un `CaptureThread`, y esta cámara no tiene
    ninguno. Sin este empujón el chip se queda con el placeholder de arranque para
    siempre, y una cámara que dice «Iniciando» y no arranca nunca manda a revisar un cable
    que está bien.
    """

    class _Content:
        """Widget del monitor que acepta status, que es lo único que se le pide."""

        def __init__(self):
            self.updates: list = []

        def update_status(self, status: dict, camera_slot: str):
            self.updates.append((camera_slot, status))

    class _Ui:
        def __init__(self, content):
            self.monitor_content = content

    def _application(self, content):
        app = main.Application.__new__(main.Application)
        app._ui = self._Ui(content)
        return app

    def test_the_status_reaches_the_monitor_widget(self):
        content = self._Content()
        self._application(content)._push_status_to_content(
            main._UNLICENSED_CAMERA_STATUS, "camera_3")
        assert content.updates == [("camera_3", main._UNLICENSED_CAMERA_STATUS)]

    def test_a_widget_without_the_method_is_not_a_crash(self):
        """El fork elige el widget del centro y no está obligado a aceptar status."""
        content = object()
        self._application(content)._push_status_to_content({}, "camera_3")

    def test_headless_has_no_widget_to_push_to(self):
        """Sin ventana `monitor_content` es None, y el cableado no pregunta si la hay."""
        self._application(None)._push_status_to_content({}, "camera_3")


class TestInterruptSignals:
    def test_ctrl_c_and_sigterm_are_always_handled(self):
        assert {signal.SIGINT, signal.SIGTERM} <= set(main._interrupt_signals())

    def test_ctrl_break_is_handled_where_it_exists(self):
        """ONLY_WINDOWS: sin atenderlo el proceso muere sin ejecutar un paso del cierre."""
        expected = hasattr(signal, "SIGBREAK")
        handled = any(getattr(s, "name", "") == "SIGBREAK" for s in main._interrupt_signals())
        assert handled is expected

    def test_the_handler_schedules_the_quit_instead_of_running_it(self, monkeypatch):
        """
        El handler puede correr con un lock tomado, y en Qt 6 `quit()` corre `stop()` en el
        acto: cerrar desde ahí trababa el cierre con hilos que pedían ese lock.
        """
        scheduled, quits = [], []

        class FakeTimer:
            @staticmethod
            def singleShot(interval_ms: int, slot: object):
                scheduled.append((interval_ms, slot))

        class FakeApp:
            def quit(self):
                quits.append(True)

        app = FakeApp()
        monkeypatch.setattr(main, "QTimer", FakeTimer)
        main._build_interrupt_handler(app)(signal.SIGTERM, None)
        assert quits == []
        assert scheduled == [(0, app.quit)]


class TestModbusServerWiring:
    """
    Que el motivo por el que el mapa no cargó llegue al servidor.

    `registers.py` sólo lo detecta y lo deja en `LOAD_ERROR`; quien decide es el
    cableado. Si no se lo pasa, los dos transportes toman su puerto y sirven el datastore
    vacío —un PLC leyendo ceros, indistinguible de «no hay medición»—, que es justo lo que
    `registers.py` existe para evitar. Que el servidor sepa negarse ya está probado en
    `test/modbus/test_server.py`; lo que se prueba acá es que se entere.
    """

    _REASON = "No se pudo cargar register_map.yaml: [Errno 2] No such file or directory"

    class _MockConfig:
        """ConfigManager mínimo, con lo que el servidor le pide al construirse.

        Los dos transportes van apagados: el bloqueo le gana al `disabled`, así que la
        diferencia se ve igual y ningún test de esta clase toma un puerto de verdad.
        """

        def __init__(self):
            self._values = {
                "modbus.slave_id": 1,
                "modbus.tcp": {"enabled": False},
                "modbus.rtu": {"enabled": False},
            }

        def get(self, key: str, default: object = None) -> object:
            return self._values.get(key, default)

    def test_without_a_map_the_server_refuses_to_serve(self, monkeypatch, caplog):
        monkeypatch.setattr(main, "REGISTER_MAP_ERROR", self._REASON)
        server = main._build_modbus_server(self._MockConfig())
        with caplog.at_level("ERROR"):
            asyncio.run(server.run_tcp_server())
        assert server.tcp_status == STATUS_ERROR
        assert self._REASON in caplog.text

    def test_with_the_map_loaded_nothing_blocks_it(self, monkeypatch):
        """
        El caso normal, mirado donde se distingue: apagado por config el estado es
        `disabled`. Si el bloqueo se disparara igual sería `error`, que es otra cosa y
        manda a mirar otro archivo.
        """
        monkeypatch.setattr(main, "REGISTER_MAP_ERROR", "")
        server = main._build_modbus_server(self._MockConfig())
        asyncio.run(server.run_tcp_server())
        assert server.tcp_status == STATUS_DISABLED

    def test_it_passes_the_error_that_registers_reports(self):
        """
        No una copia ni un texto propio: el mismo valor. Un segundo vocabulario de «no
        cargó el mapa» sería otra cosa más que mantener de acuerdo.
        """
        assert main.REGISTER_MAP_ERROR is registers.LOAD_ERROR


class TestInferenceFields:
    """
    Los fields de un punto de telemetría de inferencia.

    Lo que se cuida es el proyecto con ciclo: el scheduler cierra los N frames y el tick ve
    un solo resultado ya resumido, así que agregarlo de nuevo no mide nada y tapa lo que sí
    midió.
    """

    def _cycle_result(self) -> InferenceResult:
        """Lo que emite el scheduler al cerrar un ciclo: la media y su dispersión."""
        return InferenceResult(
            camera_slot="camera_1",
            metrics={"load_pct": 50.0, "load_pct_std": 10.0, "load_pct_min": 40.0,
                     "load_pct_max": 60.0, "sample_count": 5},
        )

    def test_the_cycle_dispersion_travels_untouched(self):
        fields = main._build_inference_fields([self._cycle_result()])
        assert (fields["load_pct_std"], fields["load_pct_min"],
                fields["load_pct_max"]) == (10.0, 40.0, 60.0)

    def test_nothing_gets_a_second_round_of_suffixes(self):
        """`load_pct_max_max` es el síntoma de agregar lo ya agregado."""
        fields = main._build_inference_fields([self._cycle_result()])
        assert not [name for name in fields if name.endswith(("_max_max", "_min_min",
                                                              "_std_std", "_max_mean"))]

    def test_the_sample_count_is_the_frames_of_the_cycle(self):
        """Cuántos ciclos entraron al punto ya lo dice `result_count`."""
        fields = main._build_inference_fields([self._cycle_result()])
        assert (fields["sample_count"], fields["result_count"]) == (5, 1)

    def test_without_a_cycle_the_stats_are_derived(self):
        """Con `frames_per_cycle: 1` no llega nada resumido y el tick es el que agrega."""
        results = [InferenceResult(camera_slot="camera_1", metrics={"load_pct": value})
                   for value in (40.0, 60.0)]
        fields = main._build_inference_fields(results)
        assert (fields["load_pct_mean"], fields["load_pct_std"],
                fields["sample_count"]) == (50.0, 10.0, 2)


class _FrameSink:
    """Motor o servidor de video: registra lo que le empujan."""

    def __init__(self, pipeline_slot: str = "", *, raw_clients: bool = False):
        self.pipeline_slot = pipeline_slot
        self.raw_clients = raw_clients
        self.pushed: list = []

    def push_frame(self, frame_bgr, camera_slot, ignore_interval=False):
        self.pushed.append((frame_bgr, camera_slot, ignore_interval))

    def has_raw_clients(self, camera_slot: str) -> bool:
        return self.raw_clients

    def push_raw(self, camera_slot, frame_bgr):
        self.pushed.append((frame_bgr, camera_slot))


class _CycleStub:
    """El scheduler: qué pipelines miden de a varios frames, y el permiso siempre en sí."""

    def __init__(self, *cycled: str):
        self._cycled = cycled

    def is_cycled(self, pipeline_slot: str) -> bool:
        return pipeline_slot in self._cycled

    def take_frame(self, camera_slot: str, pipeline_slot: str) -> bool:
        return True


class _ContentSpy:
    mode = "raw"

    def __init__(self):
        self.shown: list = []

    def update_frame(self, frame_bgr, camera_slot):
        self.shown.append(frame_bgr)


def _build_frame_application(*, engines=(), cycled=(), raw_clients=False, content=None):
    """`Application` sin `__init__` —sí con su QObject— y sólo lo que toca el camino del frame."""
    import types
    from PySide6.QtCore import QObject
    app = main.Application.__new__(main.Application)
    QObject.__init__(app)
    app._engines = list(engines)
    app._scheduler = _CycleStub(*cycled)
    app._http_video = _FrameSink(raw_clients=raw_clients)
    app._rtsp_video = _FrameSink()
    app._display_adjust = None
    app._last_raw_frame = {}
    app._gui_frames = {}
    app._gui_frames_lock = threading.Lock()
    app._ui = types.SimpleNamespace(monitor_content=content)
    app.notified = []
    app._frame_for_gui.connect(app.notified.append)
    return app


class TestFramePath:
    """
    El frame de cámara no pasa entero por la GUI: la inferencia y el video crudo lo toman
    en el hilo de captura, y la GUI recibe sólo el último de cada cámara cuando puede.
    """

    def test_an_uncycled_engine_gets_the_frame_in_the_capture_thread(self):
        engine = _FrameSink("pipeline_1")
        app = _build_frame_application(engines=[engine])
        frame = np.zeros((4, 4, 3), np.uint8)
        app._on_frame_captured(frame, "camera_1")
        assert engine.pushed == [(frame, "camera_1", False)]

    def test_a_cycled_engine_waits_for_the_gui(self):
        """Su permiso lo maneja el scheduler, que no es thread-safe."""
        engine = _FrameSink("pipeline_1")
        app = _build_frame_application(engines=[engine], cycled=["pipeline_1"])
        frame = np.zeros((4, 4, 3), np.uint8)
        app._on_frame_captured(frame, "camera_1")
        assert engine.pushed == []
        app._on_frame_for_gui("camera_1")
        assert engine.pushed == [(frame, "camera_1", True)]

    def test_the_raw_stream_gets_the_frame_only_with_clients(self):
        app = _build_frame_application()
        app._on_frame_captured(np.zeros((4, 4, 3), np.uint8), "camera_1")
        assert app._http_video.pushed == []
        app._http_video.raw_clients = True
        app._on_frame_captured(np.zeros((4, 4, 3), np.uint8), "camera_1")
        assert len(app._http_video.pushed) == 1

    def test_the_gui_is_notified_once_while_a_frame_waits(self):
        """Con la GUI ocupada, la cola tenía un frame de 9 MB por cada uno capturado."""
        app = _build_frame_application()
        for _ in range(5):
            app._on_frame_captured(np.zeros((4, 4, 3), np.uint8), "camera_1")
        assert app.notified == ["camera_1"]

    def test_the_gui_takes_the_latest_frame(self):
        content = _ContentSpy()
        app = _build_frame_application(content=content)
        frames = [np.full((4, 4, 3), value, np.uint8) for value in (1, 2, 3)]
        for frame in frames:
            app._on_frame_captured(frame, "camera_1")
        app._on_frame_for_gui("camera_1")
        assert content.shown == [frames[-1]]

    def test_after_the_gui_takes_it_the_next_frame_notifies_again(self):
        app = _build_frame_application(content=_ContentSpy())
        app._on_frame_captured(np.zeros((4, 4, 3), np.uint8), "camera_1")
        app._on_frame_for_gui("camera_1")
        app._on_frame_captured(np.zeros((4, 4, 3), np.uint8), "camera_1")
        assert app.notified == ["camera_1", "camera_1"]

    def test_each_camera_has_its_own_mailbox(self):
        app = _build_frame_application()
        app._on_frame_captured(np.zeros((4, 4, 3), np.uint8), "camera_1")
        app._on_frame_captured(np.zeros((4, 4, 3), np.uint8), "camera_2")
        assert app.notified == ["camera_1", "camera_2"]


class _GrainPipeline(AbstractPipeline):
    model_slots = ("grain",)

    def _run(self, frame_bgr, camera_slot):
        return []


class _PersonPipeline(AbstractPipeline):
    model_slots = ("person",)

    def _run(self, frame_bgr, camera_slot):
        return []


class _LicenseStub:
    """La licencia: habilita todo menos los features que se le nombren."""

    def __init__(self, *denied: str):
        self._denied = denied

    def has_feature(self, feature: str) -> bool:
        return feature not in self._denied


def _build_engine_application(monkeypatch, pipelines: dict, *denied_features: str):
    """`Application` sin `__init__` —sí con su QObject—, con lo que lee `_build_engines()`."""
    from PySide6.QtCore import QObject
    monkeypatch.setitem(pipeline_factory._PIPELINE_CLASSES, "grain", _GrainPipeline)
    monkeypatch.setitem(pipeline_factory._PIPELINE_CLASSES, "person", _PersonPipeline)
    monkeypatch.setattr(main, "build_preprocessor", lambda config: None)
    monkeypatch.setattr(main, "build_hooks", lambda config: EngineHooks())
    values = {"inference.pipelines": pipelines}
    for pipeline_slot, section in pipelines.items():
        values.update({f"inference.pipelines.{pipeline_slot}.{key}": value
                       for key, value in section.items()})
    app = main.Application.__new__(main.Application)
    QObject.__init__(app)
    app._config = _MockConfig(**values)
    app._license = _LicenseStub(*denied_features)
    app._display_adjust = None
    return app


class TestEngines:
    """Un hilo por pipeline habilitado y licenciado, con la clase que pide su `type`."""

    def test_each_slot_gets_the_class_its_type_declares(self, monkeypatch):
        app = _build_engine_application(monkeypatch, {
            "pipeline_1": {"type": "grain"}, "pipeline_2": {"type": "person"}})
        engines = app._build_engines()
        assert [type(engine._pipeline) for engine in engines] == [_GrainPipeline,
                                                                  _PersonPipeline]

    def test_two_slots_of_the_same_type_get_their_own_engine(self, monkeypatch):
        app = _build_engine_application(monkeypatch, {
            "pipeline_1": {"type": "person"}, "pipeline_2": {"type": "person"}})
        engines = app._build_engines()
        assert [engine.pipeline_slot for engine in engines] == ["pipeline_1", "pipeline_2"]
        assert engines[0]._pipeline is not engines[1]._pipeline

    def test_a_disabled_pipeline_gets_no_engine(self, monkeypatch):
        app = _build_engine_application(monkeypatch, {
            "pipeline_1": {"type": "grain"}, "pipeline_2": {"enabled": False}})
        assert [engine.pipeline_slot for engine in app._build_engines()] == ["pipeline_1"]

    def test_an_unlicensed_feature_gets_no_engine(self, monkeypatch):
        app = _build_engine_application(monkeypatch, {
            "pipeline_1": {"type": "grain"},
            "pipeline_2": {"type": "person", "feature": "safety"}}, "safety")
        assert [engine.pipeline_slot for engine in app._build_engines()] == ["pipeline_1"]


class TestDatasetGating:
    """
    Con la recolección apagada, o sin esa cámara, el resultado no se serializa: corre por
    frame en la GUI.
    """

    def _application(self, collector_active: bool):
        import types
        app = main.Application.__new__(main.Application)
        app._annotate_cycle = lambda result: None
        app._ui = types.SimpleNamespace(monitor_content=None)
        app._pending_results = {}
        app._store_metrics = lambda result: None
        app._collector = types.SimpleNamespace(
            should_collect=lambda camera_slot: collector_active)
        app.sent = []
        app._send_to_dataset = app.sent.append
        return app

    def test_nothing_is_built_with_the_collector_off(self):
        app = self._application(collector_active=False)
        app._on_cycle_complete(InferenceResult(camera_slot="camera_1"))
        assert app.sent == []

    def test_the_result_goes_to_the_dataset_with_the_collector_on(self):
        app = self._application(collector_active=True)
        result = InferenceResult(camera_slot="camera_1")
        app._on_cycle_complete(result)
        assert app.sent == [result]

    def test_the_telemetry_keeps_the_numbers_without_the_frames(self):
        """Guardado entero, cada resultado retenía dos frames de 9 MB hasta el tick."""
        from system.inference.result import Detection
        app = self._application(collector_active=False)
        result = InferenceResult(camera_slot="camera_1", metrics={"load_pct": 60.0})
        result.source_bgr = np.zeros((8, 8, 3), np.uint8)
        result.annotated_bgr = np.zeros((8, 8, 3), np.uint8)
        result.detections = [Detection(class_index=0, class_name="ok",
                                       confidence_pct=90.0, bbox_px=(0, 0, 4, 4))]
        app._on_cycle_complete(result)

        (kept,) = app._pending_results[("camera_1", "")]
        assert (kept.source_bgr, kept.annotated_bgr, kept.detections) == (None, None, [])
        assert kept.metrics == result.metrics and kept.timestamp_s == result.timestamp_s
        assert result.source_bgr is not None, "el resultado original no se toca"


class _WritableMockConfig(_MockConfig):
    """`_MockConfig` que además acepta `set()` y `save()`, como hace la calibración."""

    def __init__(self, **overrides):
        super().__init__(**overrides)
        self.saved = 0

    def set(self, key: str, value: object):
        self._values[key] = value

    def save(self) -> bool:
        self.saved += 1
        return True


class _SpyUi:
    """Lo único que el cableado de la óptica le pide a la interfaz."""

    def __init__(self):
        self.refreshed: list = []

    def refresh_lens_reference(self, camera_slot: str):
        self.refreshed.append(camera_slot)


def _build_lens_application(**overrides):
    """
    `Application` sin `__init__`, con sólo lo que el cableado de la óptica toca.

    Construirla de verdad levantaría cámaras, Modbus y un event loop; lo que se prueba
    acá es la decisión, que no depende de nada de eso.
    """
    app = main.Application.__new__(main.Application)
    app._config = _WritableMockConfig(**overrides)
    app._ui = _SpyUi()
    app._lens_calibrations = {}
    app._lens_monitor = LensHealthMonitor(window_s=60.0, threshold_pct=50.0)
    return app


class TestLensInterval:
    def test_it_takes_the_interval_from_the_config(self):
        app = _build_lens_application(**{"lens_health.interval_s": 30})
        assert app._read_lens_interval_ms() == 30000

    def test_without_the_key_it_falls_back_to_the_default(self):
        app = _build_lens_application()
        assert app._read_lens_interval_ms() == int(main._LENS_DEFAULT_INTERVAL_S * 1000)

    def test_an_absurdly_short_interval_is_clamped(self):
        """Medir más seguido que esto no llena antes una ventana de horas, y cada
        medición es una pasada del Laplaciano por cámara."""
        app = _build_lens_application(**{"lens_health.interval_s": 0.05})
        assert app._read_lens_interval_ms() == int(main._LENS_MIN_INTERVAL_S * 1000)

    def test_a_zero_interval_reads_as_a_value_that_is_not_there(self):
        """Un 0 no es «medir sin parar»: acá el interruptor es `enabled`, así que un 0 es
        un error de tipeo y vale más el default que el ritmo más agresivo posible."""
        app = _build_lens_application(**{"lens_health.interval_s": 0})
        assert app._read_lens_interval_ms() == int(main._LENS_DEFAULT_INTERVAL_S * 1000)


class TestLensDirtyBit:
    """Qué llega al bit de lente sucio de la palabra de estado de la cámara."""

    def _feed(self, app, camera_slot: str, variance_reference: float):
        app._lens_monitor.set_reference(camera_slot, variance_reference)
        frame = np.zeros((80, 120, 3), np.uint8)
        frame[::8, :] = 255
        for now_s in range(0, 200, 10):
            app._lens_monitor.update(camera_slot, frame, None, now_s=now_s)

    def test_an_uncalibrated_camera_never_reports_a_dirty_lens(self):
        """«No disponible» no es un vidrio sucio: sin referencia no hay nada que afirmar."""
        app = _build_lens_application()
        self._feed(app, "camera_1", 0.0)
        assert app._is_lens_dirty("camera_1") is False

    def test_sharpness_below_the_threshold_reports_a_dirty_lens(self):
        app = _build_lens_application()
        self._feed(app, "camera_1", 1e9)      # referencia inalcanzable
        assert app._is_lens_dirty("camera_1") is True

    def test_a_camera_that_never_measured_does_not_report_one(self):
        assert _build_lens_application()._is_lens_dirty("camera_9") is False


class TestLensCalibration:
    def _measurement(self, variance: float):
        region = Region(0, 0, 10, 10, False)
        return Measurement(variance=variance, luma=100.0, region=region,
                           width_px=10, height_px=10)

    def test_it_does_not_write_anything_before_the_last_sample(self):
        app = _build_lens_application(**{"lens_health.calibration_samples": 3})
        app._lens_calibrations["camera_1"] = ([], [])
        for _ in range(2):
            app._collect_lens_calibration("camera_1", self._measurement(400.0))
        assert app._config.saved == 0
        assert "camera_1" in app._lens_calibrations

    def test_the_last_sample_writes_the_reference_and_persists_it(self):
        app = _build_lens_application(**{"lens_health.calibration_samples": 3})
        app._lens_calibrations["camera_1"] = ([], [])
        for _ in range(3):
            app._collect_lens_calibration("camera_1", self._measurement(400.0))
        prefix = "cameras.camera_1.lens_health.reference"
        assert app._config.get(f"{prefix}.variance") == 400.0
        assert app._config.get(f"{prefix}.sample_count") == 3
        assert app._config.saved == 1

    def test_it_records_the_conditions_it_calibrated_under(self):
        """Sin esto no hay forma de avisar que alguien movió la exposición después."""
        app = _build_lens_application(**{"lens_health.calibration_samples": 1})
        app._lens_calibrations["camera_1"] = ([], [])
        app._collect_lens_calibration("camera_1", self._measurement(400.0))
        conditions = app._config.get("cameras.camera_1.lens_health.reference.conditions")
        assert set(conditions) == set(CONDITION_KEYS)

    def test_it_stamps_when_the_calibration_was_taken(self):
        app = _build_lens_application(**{"lens_health.calibration_samples": 1})
        app._lens_calibrations["camera_1"] = ([], [])
        app._collect_lens_calibration("camera_1", self._measurement(400.0))
        assert app._config.get("cameras.camera_1.lens_health.reference.calibrated_at")

    def test_the_new_reference_takes_effect_without_a_restart(self):
        """Se le pasa al monitor además de escribirla: si no, la cámara recién calibrada
        seguiría en «no disponible» hasta reiniciar."""
        app = _build_lens_application(**{"lens_health.calibration_samples": 1})
        app._lens_calibrations["camera_1"] = ([], [])
        app._collect_lens_calibration("camera_1", self._measurement(400.0))
        assert app._lens_monitor.get_status(
            "camera_1", now_s=0.0)["reference_variance"] == 400.0

    def test_it_tells_the_screen_to_reload_the_reference(self):
        app = _build_lens_application(**{"lens_health.calibration_samples": 1})
        app._lens_calibrations["camera_1"] = ([], [])
        app._collect_lens_calibration("camera_1", self._measurement(400.0))
        assert app._ui.refreshed == ["camera_1"]

    def test_calibrating_one_camera_leaves_the_others_alone(self):
        app = _build_lens_application(**{"lens_health.calibration_samples": 1})
        app._lens_monitor.set_reference("camera_2", 900.0)
        app._lens_calibrations["camera_1"] = ([], [])
        app._collect_lens_calibration("camera_1", self._measurement(400.0))
        assert app._lens_monitor.get_status(
            "camera_2", now_s=0.0)["reference_variance"] == 900.0


# ── GPIO ─────────────────────────────────────────────────────────────────────────

def _build_gpio_application(monkeypatch, **overrides):
    """
    `Application` sin `__init__`, con un `GpioController` de verdad en modo simulado.

    Sin `gpiod` el controlador degrada adentro, que es el camino de cualquier equipo de
    desarrollo y el que tiene que seguir publicando: lo que se prueba es que el cableado
    no pregunte y deje la marca de «sin GPIO» en las dos palabras.
    """
    monkeypatch.setattr(gpio_control, "_GPIOD_AVAILABLE", False)
    config = _MockConfig(**{
        "gpio.enabled": True,
        "gpio.outputs": {"DO1": 51, "DO2": 52},
        "gpio.inputs": {"DI1": 105, "DI2": 106},
        **overrides,
    })
    app = main.Application.__new__(main.Application)
    app._gpio = gpio_control.GpioController(config)
    app._gpio_inputs = {}
    return app


class TestGpioRegisters:
    def test_both_words_say_there_is_no_gpio_without_the_library(self, monkeypatch):
        """Una entrada en cero y un equipo sin de dónde leerla no pueden verse igual."""
        registers = _build_gpio_application(monkeypatch)._build_gpio_registers()
        unavailable = 1 << gpio_status.BIT_UNAVAILABLE
        assert registers["gpio_inputs_bitfield"] & unavailable
        assert registers["gpio_outputs_bitfield"] & unavailable

    def test_the_inputs_word_carries_the_last_polled_reading(self, monkeypatch):
        app = _build_gpio_application(monkeypatch)
        app._on_gpio_inputs_updated({1: 0, 2: 1})
        word = app._build_gpio_registers()["gpio_inputs_bitfield"]
        assert word == gpio_status.pack_inputs({2: 1}, hardware_available=False)

    def test_the_outputs_word_echoes_what_was_commanded(self, monkeypatch):
        app = _build_gpio_application(monkeypatch)
        app._gpio.set_output(2, 1)
        word = app._build_gpio_registers()["gpio_outputs_bitfield"]
        assert word == gpio_status.pack_outputs({2: 1}, hardware_available=False)

    def test_both_words_have_a_row_in_the_map(self, monkeypatch):
        """Sin fila, `_mapped_only` las descarta en silencio y el PLC nunca las ve."""
        names = set(_build_gpio_application(monkeypatch)._build_gpio_registers())
        assert names <= main._REGISTER_NAMES

    def test_an_analyzer_metric_cannot_overwrite_them(self, monkeypatch):
        names = set(_build_gpio_application(monkeypatch)._build_gpio_registers())
        assert names <= main._HEALTH_REGISTER_NAMES


class _StopRecorder:
    """Cualquier subsistema que `stop()` toque: anota cada llamada en un log compartido."""

    def __init__(self, name: str, calls: list):
        self._name = name
        self._calls = calls

    def __getattr__(self, method: str):
        def record(*args, **kwargs):
            self._calls.append((self._name, method))
            return True
        return record


class TestGpioShutdown:
    def test_the_polling_stops_before_the_lines_are_released(self):
        """Cerrar las líneas con el hilo leyéndolas lo deja leyendo una línea liberada."""
        calls: list = []
        app = main.Application.__new__(main.Application)
        for name in ("_ui", "_scheduler", "_metrics_poller", "_telemetry", "_gpio_thread",
                     "_gpio", "_collector", "_http_video", "_rtsp_video", "_modbus",
                     "_modbus_thread"):
            setattr(app, name, _StopRecorder(name, calls))
        app._timers = []
        app._capture_threads = {}
        app._engines = []

        app.stop()

        assert calls.index(("_gpio_thread", "wait")) < calls.index(("_gpio", "close"))


class TestHeadlessUi:
    def test_it_answers_everything_the_window_answers(self):
        """El cableado no pregunta si hay ventana: un método que falte es un crash en headless."""
        window_methods = {name for name in vars(main._QtUi)
                          if callable(getattr(main._QtUi, name)) and not name.startswith("_")}
        assert window_methods <= set(dir(main._HeadlessUi))
