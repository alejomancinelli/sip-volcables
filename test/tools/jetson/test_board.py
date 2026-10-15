"""Tests de la lectura del estado de la Jetson: módulo, L4T, modo de potencia, relojes y
contadores de sobrecorriente.

Los archivos de `/proc`, `/etc`, `/var/lib` y sysfs se montan en `tmp_path`, así que se
prueba desde cualquier equipo. Ningún archivo existe hasta que el test lo escribe: un
kernel que no publica un nodo es un caso real."""

import pytest

from tools.jetson import board

_NVPMODEL_CONF = """
< POWER_MODEL ID=0 NAME=MAXN >
CPU_ONLINE CORE_0 1
< POWER_MODEL ID=1 NAME=10W >
< POWER_MODEL ID=2 NAME=15W >
< POWER_MODEL ID=3 NAME=20W >
< PM_CONFIG DEFAULT=2 >
"""


class _FakeBoard:
    """Monta en `tmp_path` los archivos que lee el módulo y apunta sus rutas ahí."""

    def __init__(self, tmp_path, monkeypatch):
        self.root = tmp_path
        monkeypatch.setattr(board, "_MODEL_PATH", str(tmp_path / "model"))
        monkeypatch.setattr(board, "_TEGRA_RELEASE_PATH", str(tmp_path / "nv_tegra_release"))
        monkeypatch.setattr(board, "_NVPMODEL_STATUS_PATH", str(tmp_path / "nvpmodel_status"))
        monkeypatch.setattr(board, "_NVPMODEL_CONF_PATH", str(tmp_path / "nvpmodel.conf"))
        monkeypatch.setattr(board, "_CPUFREQ_POLICY_GLOB", str(tmp_path / "cpufreq" / "policy*"))
        monkeypatch.setattr(board, "_GPU_DEVFREQ_GLOB", str(tmp_path / "devfreq" / "17000000.*"))
        monkeypatch.setattr(board, "_OVERCURRENT_GLOB",
                            str(tmp_path / "hwmon" / "hwmon*" / "oc*_event_cnt"))
        monkeypatch.setattr(board, "_DEVFREQ_GLOB", str(tmp_path / "devfreq" / "*"))
        monkeypatch.setattr(board, "_RUNTIME_STATUS_GLOBS", tuple(
            str(tmp_path / "platform" / ("*/" * depth) / "power" / "runtime_status")
            for depth in (1, 2)))

    def write(self, relative: str, text: str):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def set_cpu_policy(self, name: str, cur_khz: int, min_khz: int, max_khz: int):
        self.write(f"cpufreq/{name}/scaling_cur_freq", f"{cur_khz}\n")
        self.write(f"cpufreq/{name}/scaling_min_freq", f"{min_khz}\n")
        self.write(f"cpufreq/{name}/scaling_max_freq", f"{max_khz}\n")

    def set_gpu(self, cur_hz: int, min_hz: int, max_hz: int, name: str = "17000000.gpu"):
        self.write(f"devfreq/{name}/cur_freq", f"{cur_hz}\n")
        self.write(f"devfreq/{name}/min_freq", f"{min_hz}\n")
        self.write(f"devfreq/{name}/max_freq", f"{max_hz}\n")


@pytest.fixture
def jetson(tmp_path, monkeypatch) -> _FakeBoard:
    return _FakeBoard(tmp_path, monkeypatch)


class TestIdentity:
    def test_the_module_name_loses_the_trailing_nul(self, jetson):
        jetson.write("model", "NVIDIA Jetson Orin NX Engineering Reference Developer Kit\0")
        assert board.read_module() == "NVIDIA Jetson Orin NX Engineering Reference Developer Kit"

    def test_the_release_is_major_and_revision(self):
        line = "# R36 (release), REVISION: 4.0, GCID: 37537400, BOARD: generic, EABI: aarch64"
        assert board.parse_l4t_release(line) == "R36.4.0"

    def test_an_unknown_release_text_is_empty(self):
        assert board.parse_l4t_release("not a tegra release") == ""

    def test_outside_a_jetson_everything_is_empty(self, jetson):
        assert board.read_module() == ""
        assert board.read_l4t_release() == ""
        assert board.read_power_mode() == {}
        assert board.read_clocks() == {"cpu": {}, "gpu": {}}
        assert board.read_overcurrent_counts() == {}
        assert board.read_devfreq() == {}
        assert board.find_engine_devices() == {}


class TestPowerMode:
    def test_the_id_gets_its_name_from_the_table(self):
        assert board.parse_power_mode("pmode:0003 fmode:quiet", _NVPMODEL_CONF) == {
            "id": 3, "name": "20W"}

    def test_an_id_missing_from_the_table_keeps_the_id(self):
        assert board.parse_power_mode("pmode:0009", _NVPMODEL_CONF) == {"id": 9, "name": ""}

    def test_a_status_without_a_mode_is_empty(self):
        assert board.parse_power_mode("garbage", _NVPMODEL_CONF) == {}

    def test_it_reads_both_files(self, jetson):
        jetson.write("nvpmodel_status", "pmode:0000\n")
        jetson.write("nvpmodel.conf", _NVPMODEL_CONF)
        assert board.read_power_mode() == {"id": 0, "name": "MAXN"}

    def test_without_the_table_the_name_is_empty(self, jetson):
        jetson.write("nvpmodel_status", "pmode:0002\n")
        assert board.read_power_mode() == {"id": 2, "name": ""}


class TestClocks:
    def test_cpu_policies_and_gpu_come_out_in_mhz(self, jetson):
        jetson.set_cpu_policy("policy0", 1_510_400, 729_600, 1_510_400)
        jetson.set_gpu(408_000_000, 306_000_000, 408_000_000)
        assert board.read_clocks() == {
            "cpu": {"policy0": {"cur_mhz": 1510, "min_mhz": 729, "max_mhz": 1510}},
            "gpu": {"17000000.gpu": {"cur_mhz": 408, "min_mhz": 306, "max_mhz": 408}},
        }

    def test_a_policy_with_a_missing_file_is_skipped(self, jetson):
        jetson.write("cpufreq/policy4/scaling_cur_freq", "1510400\n")
        assert board.read_clocks()["cpu"] == {}

    def test_jetpack_5_gpu_node_name_is_found_too(self, jetson):
        jetson.set_gpu(306_000_000, 306_000_000, 918_000_000, name="17000000.ga10b")
        assert "17000000.ga10b" in board.read_clocks()["gpu"]

    def test_pinned_means_every_minimum_equals_its_maximum(self, jetson):
        jetson.set_cpu_policy("policy0", 1_510_400, 1_510_400, 1_510_400)
        jetson.set_gpu(408_000_000, 408_000_000, 408_000_000)
        assert board.are_clocks_pinned(board.read_clocks()) is True

    def test_one_free_clock_means_not_pinned(self, jetson):
        jetson.set_cpu_policy("policy0", 1_510_400, 1_510_400, 1_510_400)
        jetson.set_gpu(306_000_000, 306_000_000, 408_000_000)
        assert board.are_clocks_pinned(board.read_clocks()) is False

    def test_without_clocks_pinned_is_unknown(self):
        assert board.are_clocks_pinned({"cpu": {}, "gpu": {}}) is None


class TestDevfreq:
    def test_every_node_comes_out_in_mhz(self, jetson):
        jetson.set_gpu(408_000_000, 306_000_000, 408_000_000)
        jetson.write("devfreq/15340000.vic/cur_freq", "729600000\n")
        jetson.write("devfreq/15340000.vic/min_freq", "115200000\n")
        jetson.write("devfreq/15340000.vic/max_freq", "729600000\n")
        assert board.read_devfreq() == {
            "15340000.vic": {"cur_mhz": 729, "min_mhz": 115, "max_mhz": 729},
            "17000000.gpu": {"cur_mhz": 408, "min_mhz": 306, "max_mhz": 408},
        }


class TestEngineDevices:
    def test_only_engine_devices_are_found_at_any_depth(self, jetson):
        jetson.write("platform/bus@0/15340000.vic/power/runtime_status", "suspended\n")
        jetson.write("platform/15480000.nvdec/power/runtime_status", "active\n")
        jetson.write("platform/bus@0/3100000.serial/power/runtime_status", "active\n")
        devices = board.find_engine_devices()
        assert set(devices) == {"15340000.vic", "15480000.nvdec"}

    def test_a_node_whose_name_merely_contains_an_engine_is_not_one(self, jetson):
        """En el Orin NX, `13800000.display/iommu/device` se colaba: «device» contiene «vic»."""
        jetson.write("platform/13800000.display/device/power/runtime_status", "unsupported\n")
        jetson.write("platform/bus@0/15880000.nvdla0/power/runtime_status", "suspended\n")
        assert set(board.find_engine_devices()) == {"15880000.nvdla0"}

    def test_states_are_read_from_the_found_paths(self, jetson):
        jetson.write("platform/bus@0/15340000.vic/power/runtime_status", "suspended\n")
        devices = board.find_engine_devices()
        assert board.read_runtime_states(devices) == {"15340000.vic": "suspended"}

        jetson.write("platform/bus@0/15340000.vic/power/runtime_status", "active\n")
        assert board.read_runtime_states(devices) == {"15340000.vic": "active"}

    def test_engines_are_named_without_their_address(self, jetson):
        jetson.write("platform/bus@0/15340000.vic/power/runtime_status", "active\n")
        jetson.write("devfreq/15340000.vic/cur_freq", "512000000\n")
        jetson.write("devfreq/15340000.vic/min_freq", "115200000\n")
        jetson.write("devfreq/15340000.vic/max_freq", "729600000\n")
        engines = board.read_engines(board.find_engine_devices())
        assert engines == {"vic": {"is_active": True, "mhz": 512}}

    def test_a_second_instance_gets_a_number_as_in_tegrastats(self, jetson):
        jetson.write("platform/bus@0/15540000.nvjpg/power/runtime_status", "suspended\n")
        jetson.write("platform/bus@0/15380000.nvjpg/power/runtime_status", "suspended\n")
        assert set(board.read_engines(board.find_engine_devices())) == {"nvjpg", "nvjpg1"}

    def test_a_suspended_engine_reports_no_frequency(self, jetson):
        """NVJPG informa su máximo en devfreq aun suspendido: eso no es estar trabajando."""
        jetson.write("platform/bus@0/15380000.nvjpg/power/runtime_status", "suspended\n")
        jetson.write("devfreq/15380000.nvjpg/cur_freq", "729600000\n")
        jetson.write("devfreq/15380000.nvjpg/min_freq", "115200000\n")
        jetson.write("devfreq/15380000.nvjpg/max_freq", "729600000\n")
        engines = board.read_engines(board.find_engine_devices())
        assert engines == {"nvjpg": {"is_active": False, "mhz": 0}}

    def test_an_active_engine_without_devfreq_is_active_at_zero(self, jetson):
        jetson.write("platform/bus@0/15880000.nvdla0/power/runtime_status", "active\n")
        engines = board.read_engines(board.find_engine_devices())
        assert engines == {"nvdla0": {"is_active": True, "mhz": 0}}

    def test_a_node_that_vanished_is_left_out(self, jetson):
        assert board.read_runtime_states({"15340000.vic": str(jetson.root / "gone")}) == {}


class TestOvercurrent:
    def test_each_counter_is_named_after_its_hwmon(self, jetson):
        jetson.write("hwmon/hwmon3/name", "soctherm_oc\n")
        jetson.write("hwmon/hwmon3/oc1_event_cnt", "0\n")
        jetson.write("hwmon/hwmon3/oc2_event_cnt", "4\n")
        assert board.read_overcurrent_counts() == {"soctherm_oc/oc1": 0, "soctherm_oc/oc2": 4}

    def test_a_hwmon_without_a_name_uses_its_directory(self, jetson):
        jetson.write("hwmon/hwmon5/oc1_event_cnt", "2\n")
        assert board.read_overcurrent_counts() == {"hwmon5/oc1": 2}

    def test_an_unreadable_counter_is_skipped(self, jetson):
        jetson.write("hwmon/hwmon3/oc1_event_cnt", "n/a\n")
        assert board.read_overcurrent_counts() == {}
