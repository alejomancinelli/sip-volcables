"""Tests de la lectura de `tegrastats`: cómo se traduce cada forma de valor, qué se deja
afuera de la línea, y el ciclo de vida del proceso lector.

El proceso es un doble que publica líneas y se queda abierto hasta que el test lo
suelta, así que se prueba desde cualquier equipo, con o sin Jetson."""

import subprocess
import threading
import time

import pytest

from tools.jetson import tegrastats
from tools.jetson.tegrastats import TegrastatsReader, parse_line

# Línea de un Orin NX con JetPack 6 en reposo: motores apagados.
_IDLE_LINE = (
    "10-15-2026 10:00:00 RAM 3527/7620MB (lfb 2x4MB) SWAP 0/3810MB (cached 0MB) "
    "CPU [12%@1510,8%@1510,10%@1510,5%@1510,off,off] EMC_FREQ 3%@3199 "
    "GR3D_FREQ 0%@[408] NVENC off NVDEC off NVJPG off NVJPG1 off VIC off OFA off "
    "NVDLA0 off PVA0_FREQ off APE 174 cpu@47.5C soc2@45.8C gpu@46.4C tj@47.5C "
    "VDD_IN 5432mW/5432mW VDD_CPU_GPU_CV 1234mW/1234mW VDD_SOC 1500mW/1500mW"
)

# La misma con NVDEC, el VIC y NVJPG trabajando.
_BUSY_LINE = (
    "RAM 4100/7620MB SWAP 0/3810MB CPU [30%@1510,22%@1510] EMC_FREQ 21%@3199 "
    "GR3D_FREQ 78%@[408,408] NVENC 1113 NVDEC 704 NVJPG 499 VIC_FREQ 25%@729 "
    "NVDLA0 off tj@59.8C VDD_IN 11800mW/11500mW"
)


def _wait_until(condition, timeout_s: float = 2.0) -> bool:
    deadline_s = time.monotonic() + timeout_s
    while time.monotonic() < deadline_s:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


class _FakeProcess:
    """
    `subprocess.Popen` de `tegrastats`: publica `lines` y se queda abierto.

    Como el real, el stdout no se cierra hasta que alguien corta el proceso —`terminate`
    o `finish`, que simula que murió solo con `return_code`—.
    """

    def __init__(self, lines: list[str]):
        self._lines = lines
        self._released = threading.Event()
        self.returncode = None
        self.stdout = self._stream()

    def _stream(self):
        for line in self._lines:
            yield line + "\n"
        self._released.wait(5.0)

    def finish(self, return_code: int):
        self.returncode = return_code
        self._released.set()

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self):
        self.finish(-15)

    def kill(self):
        self.finish(-9)

    def wait(self, timeout: float | None = None) -> int:
        self._released.wait(timeout)
        return self.returncode


@pytest.fixture
def fake_tegrastats(monkeypatch):
    """`tegrastats` en el PATH, lanzado como un `_FakeProcess` con las líneas que se le den."""
    launched = {"argv": None, "process": None}

    def install(lines: list[str]) -> dict:
        def fake_popen(argv: list, **_kwargs) -> _FakeProcess:
            launched["argv"] = argv
            launched["process"] = _FakeProcess(lines)
            return launched["process"]

        monkeypatch.setattr(tegrastats.shutil, "which", lambda _name: "/usr/bin/tegrastats")
        monkeypatch.setattr(tegrastats.subprocess, "Popen", fake_popen)
        return launched

    return install


class TestParseLine:
    def test_an_engine_that_is_off_runs_at_zero(self):
        engines = parse_line(_IDLE_LINE)
        assert engines["nvenc"] == {"mhz": 0, "load_pct": 0}
        assert engines["vic"] == {"mhz": 0, "load_pct": 0}

    def test_load_and_frequency_come_out_of_the_percent_at_form(self):
        assert parse_line(_IDLE_LINE)["emc"] == {"mhz": 3199, "load_pct": 3}

    def test_a_bare_number_is_a_frequency_without_a_known_load(self):
        assert parse_line(_BUSY_LINE)["nvdec"] == {"mhz": 704, "load_pct": None}

    def test_the_freq_suffix_is_dropped_from_the_name(self):
        engines = parse_line(_BUSY_LINE)
        assert engines["vic"] == {"mhz": 729, "load_pct": 25}
        assert "vic_freq" not in engines

    def test_a_per_gpc_frequency_reports_the_highest(self):
        assert parse_line("GR3D_FREQ 78%@[306,408]")["gr3d"] == {"mhz": 408, "load_pct": 78}

    def test_a_load_without_frequency_keeps_the_load(self):
        assert parse_line("GR3D_FREQ 12%")["gr3d"] == {"mhz": 0, "load_pct": 12}

    def test_only_the_known_engines_come_out(self):
        engines = parse_line(_IDLE_LINE)
        assert set(engines) <= set(tegrastats.ENGINE_NAMES)
        # Ni el procesador de audio ni los rails, aunque tengan forma de `NOMBRE número`.
        assert "ape" not in engines
        assert not any(name.startswith("vdd") for name in engines)

    def test_an_engine_the_module_does_not_have_is_absent(self):
        """El NX de 8 GB tiene un solo DLA: el segundo no está en la línea ni en el dict."""
        assert "nvdla1" not in parse_line(_IDLE_LINE)
        assert "nvdla0" in parse_line(_IDLE_LINE)

    def test_a_line_without_engines_is_empty(self):
        assert parse_line("") == {}
        assert parse_line("RAM 3527/7620MB SWAP 0/3810MB") == {}


class TestReaderWithoutTegrastats:
    def test_it_does_not_start_and_says_why(self, monkeypatch):
        monkeypatch.setattr(tegrastats.shutil, "which", lambda _name: None)
        reader = TegrastatsReader()
        assert reader.start() is False
        assert "PATH" in reader.error
        assert reader.get_engines() == {}
        assert reader.is_running is False

    def test_a_launch_that_fails_is_a_reason_not_an_exception(self, monkeypatch):
        def failing_popen(_argv: list, **_kwargs):
            raise PermissionError("denied")

        monkeypatch.setattr(tegrastats.shutil, "which", lambda _name: "/usr/bin/tegrastats")
        monkeypatch.setattr(tegrastats.subprocess, "Popen", failing_popen)
        reader = TegrastatsReader()
        assert reader.start() is False
        assert "denied" in reader.error


class TestReaderLifecycle:
    def test_it_reports_the_engines_of_the_last_line(self, fake_tegrastats):
        fake_tegrastats([_IDLE_LINE, _BUSY_LINE])
        reader = TegrastatsReader()
        assert reader.start() is True
        try:
            assert _wait_until(lambda: reader.get_last_line() == _BUSY_LINE)
            assert reader.get_engines()["nvdec"] == {"mhz": 704, "load_pct": None}
            assert reader.error == ""
        finally:
            reader.stop()

    def test_the_interval_is_passed_to_tegrastats(self, fake_tegrastats):
        launched = fake_tegrastats([_IDLE_LINE])
        reader = TegrastatsReader(interval_ms=100)
        reader.start()
        reader.stop()
        assert launched["argv"][1:] == ["--interval", "100"]

    def test_starting_twice_does_not_launch_a_second_process(self, fake_tegrastats):
        launched = fake_tegrastats([_IDLE_LINE])
        reader = TegrastatsReader()
        reader.start()
        first = launched["process"]
        try:
            assert reader.start() is True
            assert launched["process"] is first
        finally:
            reader.stop()

    def test_stop_ends_the_process(self, fake_tegrastats):
        launched = fake_tegrastats([_IDLE_LINE])
        reader = TegrastatsReader()
        reader.start()
        reader.stop()
        assert launched["process"].returncode is not None
        assert reader.is_running is False
        # Cortado a propósito no es una falla.
        assert reader.error == ""

    def test_a_process_that_dies_leaves_no_reading_and_its_exit_code(self, fake_tegrastats):
        launched = fake_tegrastats([_BUSY_LINE])
        reader = TegrastatsReader()
        reader.start()
        assert _wait_until(lambda: reader.get_engines() != {})

        launched["process"].finish(1)
        assert _wait_until(lambda: reader.get_engines() == {})
        assert "1" in reader.error
        reader.stop()

    def test_a_stale_reading_is_not_reported(self, fake_tegrastats):
        """Un `tegrastats` trabado no puede dejar una carga congelada pasando por real."""
        fake_tegrastats([_BUSY_LINE])
        reader = TegrastatsReader(interval_ms=100)
        reader.start()
        try:
            assert _wait_until(lambda: reader.get_engines() != {})
            reader._last_line_s -= 10.0
            assert reader.get_engines() == {}
        finally:
            reader.stop()

    def test_the_returned_dict_is_a_copy(self, fake_tegrastats):
        fake_tegrastats([_BUSY_LINE])
        reader = TegrastatsReader()
        reader.start()
        try:
            assert _wait_until(lambda: reader.get_engines() != {})
            reader.get_engines()["nvdec"]["mhz"] = 0
            assert reader.get_engines()["nvdec"]["mhz"] == 704
        finally:
            reader.stop()


def test_the_launch_never_inherits_stdin(monkeypatch):
    """Lanzado sin consola, un stdin heredado es un handle inválido."""
    captured = {}

    def recording_popen(_argv: list, **kwargs) -> _FakeProcess:
        captured.update(kwargs)
        return _FakeProcess([_IDLE_LINE])

    monkeypatch.setattr(tegrastats.shutil, "which", lambda _name: "/usr/bin/tegrastats")
    monkeypatch.setattr(tegrastats.subprocess, "Popen", recording_popen)
    reader = TegrastatsReader()
    reader.start()
    reader.stop()
    assert captured["stdin"] is subprocess.DEVNULL
