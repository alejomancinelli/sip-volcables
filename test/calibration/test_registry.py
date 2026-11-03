"""
Tests de la tabla de calibradores: qué se puede registrar y cómo se lanza uno.

Ningún calibrador real se importa: los que corren acá son módulos falsos puestos en
`sys.modules`, así que esto no depende de lo que traiga cada fork.
"""

import importlib.util
import sys
import types

import pytest

from calibration import registry
from calibration.registry import USAGE_ERROR, CalibrationTool


def _tool(name: str = "mesh", **overrides) -> CalibrationTool:
    fields = {"module": f"calibration.{name}.{name}_calibrator",
              "launcher": f"Calibrar {name}", "description": f"Calibra {name}."}
    fields.update(overrides)
    return CalibrationTool(name=name, **fields)


def _install_module(monkeypatch, name: str, run) -> None:
    """Pone un módulo con esa función `run` donde `importlib` lo va a buscar."""
    module = types.ModuleType(name)
    module.run = run
    monkeypatch.setitem(sys.modules, name, module)


def _is_findable(module: str) -> bool:
    """Si el módulo está donde dice, sin importarlo: puede traer dependencias pesadas."""
    try:
        return importlib.util.find_spec(module) is not None
    except ModuleNotFoundError:
        return False


class TestShippedTable:
    """
    La tabla que se entrega. En el template está vacía; en un fork, estos tests son los
    que frenan una fila mal escrita antes de que `make_release.py` la empaquete.
    """

    def test_it_has_no_problems(self):
        assert registry.check_tools(registry.TOOLS) == []

    def test_each_module_is_where_it_says(self):
        assert [tool.module for tool in registry.TOOLS if not _is_findable(tool.module)] == []


class TestCheckTools:
    def test_a_well_formed_tool_passes(self):
        tool = _tool(folders=(("samples/sieve", "nota"), ("out", "nota")))
        assert registry.check_tools((tool,)) == []

    @pytest.mark.parametrize("name", ["Mesh", "mesh calibrator", "2mesh", "mesh-cal", ""])
    def test_the_name_has_to_work_as_a_word_and_a_package(self, name):
        problems = registry.check_tools((_tool(name, module=f"calibration.{name}.x"),))
        assert any("snake_case" in problem for problem in problems)

    def test_two_tools_cannot_share_a_name(self):
        problems = registry.check_tools((_tool(), _tool(launcher="Otro")))
        assert any("dos calibradores" in problem for problem in problems)

    def test_two_tools_cannot_share_a_launcher(self):
        """Windows no distingue mayúsculas: el segundo `.cmd` pisaría al primero."""
        problems = registry.check_tools((_tool("mesh", launcher="Calibrar"),
                                         _tool("scale", launcher="calibrar")))
        assert any("lanzador" in problem for problem in problems)

    def test_the_module_lives_in_its_own_folder(self):
        problems = registry.check_tools((_tool(module="calibration.scale.mesh_calibrator"),))
        assert any("no está en calibration/mesh/" in problem for problem in problems)

    @pytest.mark.parametrize("launcher", ["", "   ", "Calibrar/mallas", "¿Mallas?"])
    def test_the_launcher_has_to_be_a_file_name(self, launcher):
        problems = registry.check_tools((_tool(launcher=launcher),))
        assert any("nombre de archivo" in problem for problem in problems)

    @pytest.mark.parametrize("folder", ["../config", "/tmp/out", "\\out", ".", "", "a/../.."])
    def test_a_folder_cannot_leave_the_tool_directory(self, folder):
        problems = registry.check_tools((_tool(folders=((folder, "nota"),)),))
        assert any("sale de calibration/mesh/" in problem for problem in problems)

    def test_each_problem_is_reported_once(self):
        problems = registry.check_tools((_tool(), _tool(), _tool()))
        assert len(problems) == len(set(problems))


class TestRunTool:
    def test_the_tool_gets_the_config_and_the_rest_of_the_line(self, monkeypatch):
        calls = []
        _install_module(monkeypatch, "calibration.mesh.mesh_calibrator",
                        lambda config, argv: calls.append((config, argv)) or 0)
        monkeypatch.setattr(registry, "TOOLS", (_tool(),))
        config = object()

        assert registry.run_tool(config, ["mesh", "--samples", "a.csv"]) == 0
        assert calls == [(config, ["--samples", "a.csv"])]

    def test_its_exit_code_comes_back(self, monkeypatch):
        _install_module(monkeypatch, "calibration.mesh.mesh_calibrator",
                        lambda config, argv: 7)
        monkeypatch.setattr(registry, "TOOLS", (_tool(),))
        assert registry.run_tool(object(), ["mesh"]) == 7

    def test_an_unknown_name_lists_what_there_is(self, monkeypatch, capsys):
        monkeypatch.setattr(registry, "TOOLS", (_tool("mesh"), _tool("scale")))
        assert registry.run_tool(object(), ["meshh"]) == USAGE_ERROR
        message = capsys.readouterr().err
        assert "«meshh»" in message
        assert "mesh" in message and "scale" in message

    def test_a_missing_name_says_so(self, monkeypatch, capsys):
        monkeypatch.setattr(registry, "TOOLS", (_tool(),))
        assert registry.run_tool(object(), []) == USAGE_ERROR
        assert "Falta el nombre" in capsys.readouterr().err

    def test_without_tools_it_says_there_are_none(self, monkeypatch, capsys):
        monkeypatch.setattr(registry, "TOOLS", ())
        assert registry.run_tool(object(), ["mesh"]) == USAGE_ERROR
        assert "ninguno registrado" in capsys.readouterr().err

    def test_listing_the_tools_does_not_import_them(self, monkeypatch):
        """El módulo no existe: si se importara para listarlo, esto levantaría."""
        monkeypatch.setattr(registry, "TOOLS",
                            (_tool(module="calibration.mesh.does_not_exist"),))
        assert registry.run_tool(object(), ["other"]) == USAGE_ERROR
