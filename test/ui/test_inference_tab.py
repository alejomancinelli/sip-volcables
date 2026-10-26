"""
Tests de la pestaña de inferencia: qué secciones edita y que `load()`/`save()` no deformen
lo que hay en el config.

Construye widgets, así que usa un `QApplication` sin pantalla —plataforma `offscreen`—.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt                                           # noqa: E402
from PySide6.QtWidgets import QApplication, QTabWidget                  # noqa: E402

from system.inference.pipeline_factory import (                         # noqa: E402
    DEFAULT_PIPELINE, REGISTERED_PIPELINES,
)
from ui.views.config.inference_tab import InferenceTab                  # noqa: E402
from ui.widgets.form import build_slot_tabs                             # noqa: E402

_MODEL = "segmenter"
_PIPELINE = "pipeline_1"


@pytest.fixture(scope="module", autouse=True)
def qt_app():
    """Un QApplication para todo el módulo: Qt no admite dos en el mismo proceso."""
    app = QApplication.instance() or QApplication([])
    yield app


class _MockConfig:
    """ConfigManager mínimo con `get` punteado y `set`, sin archivo ni Lock."""

    def __init__(self, **overrides):
        self._values = {
            "inference.models": {_MODEL: {}},
            f"inference.models.{_MODEL}.type": "mock",
            "inference.pipelines": {_PIPELINE: {}},
            f"inference.pipelines.{_PIPELINE}.cameras": ["camera_1"],
            f"inference.pipelines.{_PIPELINE}.frames_per_cycle": 3,
            "inference.overlay.font_scale": 1.6,
        }
        self._values.update(overrides)
        self.written: dict = {}

    def get(self, key: str, default: object = None) -> object:
        return self._values.get(key, default)

    def set(self, key: str, value: object):
        self.written[key] = value


def _tab(**overrides) -> tuple[InferenceTab, _MockConfig]:
    config = _MockConfig(**overrides)
    return InferenceTab(config), config


class TestOverlay:
    def test_save_does_not_touch_the_overlay(self):
        """El anotado se edita sólo en el config.yaml: la pestaña no escribe ninguna clave."""
        tab, config = _tab()
        tab.save()
        assert not [key for key in config.written if key.startswith("inference.overlay")]


class TestClassNames:
    def test_save_does_not_write_them(self):
        """Se escriben en el config.yaml: un orden cambiado invierte los porcentajes."""
        tab, config = _tab(**{f"inference.models.{_MODEL}.class_names": ["ok", "nok"]})
        tab.save()
        assert f"inference.models.{_MODEL}.class_names" not in config.written

    def test_save_does_not_add_them_when_the_config_leaves_them_out(self):
        tab, config = _tab()
        tab.save()
        assert not [key for key in config.written if key.endswith(".class_names")]


class TestRoundTrip:
    def test_the_model_and_the_pipeline_go_back_as_they_were(self):
        tab, config = _tab()
        tab.save()
        assert config.written[f"inference.models.{_MODEL}.type"] == "mock"
        assert config.written[f"inference.pipelines.{_PIPELINE}.cameras"] == ["camera_1"]
        assert config.written[f"inference.pipelines.{_PIPELINE}.frames_per_cycle"] == 3


class TestPipelineType:
    def test_without_type_it_shows_and_saves_the_default(self):
        tab, config = _tab()
        tab.save()
        assert config.written[f"inference.pipelines.{_PIPELINE}.type"] == DEFAULT_PIPELINE

    def test_a_registered_type_goes_back_as_it_was(self):
        tab, config = _tab(**{f"inference.pipelines.{_PIPELINE}.type": DEFAULT_PIPELINE})
        tab.save()
        assert config.written[f"inference.pipelines.{_PIPELINE}.type"] == DEFAULT_PIPELINE

    def test_an_unregistered_type_is_not_lost_on_save(self):
        """Abrir la pestaña y guardar no puede cambiar qué clase corre el slot."""
        tab, config = _tab(**{f"inference.pipelines.{_PIPELINE}.type": "person"})
        tab.save()
        assert config.written[f"inference.pipelines.{_PIPELINE}.type"] == "person"

    def test_the_combo_offers_the_registered_types(self):
        tab, _ = _tab()
        combo = tab._pipeline_forms[_PIPELINE]["type"]
        options = [combo.itemText(i) for i in range(combo.count())]
        assert options == list(REGISTERED_PIPELINES)


def _tab_labels(tabs: QTabWidget) -> list[str]:
    return [tabs.tabText(i) for i in range(tabs.count())]


def _many_slots(count: int) -> dict:
    """Un config con `count` modelos y `count` pipelines."""
    return {
        "inference.models": {f"model_{i}": {} for i in range(1, count + 1)},
        "inference.pipelines": {f"pipeline_{i}": {} for i in range(1, count + 1)},
    }


class TestSections:
    def test_models_and_pipelines_have_a_bar_each_models_first(self):
        tab, _ = _tab()
        models, pipelines = tab.findChildren(QTabWidget)
        assert _tab_labels(models) == [_MODEL]
        assert _tab_labels(pipelines) == [_PIPELINE]

    def test_one_sub_tab_per_slot(self):
        tab, _ = _tab(**_many_slots(3))
        models, pipelines = tab.findChildren(QTabWidget)
        assert _tab_labels(models) == ["model_1", "model_2", "model_3"]
        assert _tab_labels(pipelines) == ["pipeline_1", "pipeline_2", "pipeline_3"]

    def test_every_slot_round_trips_through_its_sub_tab(self):
        overrides = _many_slots(10)
        overrides.update({f"inference.pipelines.pipeline_{i}.frames_per_cycle": i
                          for i in range(1, 11)})
        tab, config = _tab(**overrides)
        tab.save()
        assert [config.written[f"inference.pipelines.pipeline_{i}.frames_per_cycle"]
                for i in range(1, 11)] == list(range(1, 11))


class TestSlotTabs:
    def test_a_long_bar_scrolls_instead_of_eliding(self):
        """Diez rótulos no entran en la tarjeta: flechas, y ningún slot cortado a la mitad."""
        tabs = build_slot_tabs()
        assert tabs.usesScrollButtons()
        assert tabs.elideMode() == Qt.ElideNone

    def test_ten_pipelines_keep_their_full_labels(self):
        tab, _ = _tab(**_many_slots(10))
        _, pipelines = tab.findChildren(QTabWidget)
        assert _tab_labels(pipelines) == [f"pipeline_{i}" for i in range(1, 11)]
        assert pipelines.usesScrollButtons()


_ROI_KEY = f"inference.pipelines.{_PIPELINE}.roi.camera_1"


def _roi_fields(tab: InferenceTab) -> dict:
    return tab._roi_forms[(_PIPELINE, "camera_1")]


class TestPipelineRoi:
    """El ROI de un pipeline por cámara: el de la cámara, uno propio o el frame entero."""

    def test_there_is_a_group_per_camera_of_the_pipeline(self):
        tab, _ = _tab(**{f"inference.pipelines.{_PIPELINE}.cameras": ["camera_1", "camera_2"]})
        assert sorted(tab._roi_forms) == [(_PIPELINE, "camera_1"), (_PIPELINE, "camera_2")]

    def test_without_a_list_every_declared_camera_has_a_group(self):
        tab, _ = _tab(**{f"inference.pipelines.{_PIPELINE}.cameras": [],
                         "cameras": {"camera_1": {}, "camera_2": {}}})
        assert len(tab._roi_forms) == 2

    def test_a_config_without_it_saves_nothing(self):
        """Abrir la pestaña y guardar no agrega una clave que el config no tenía."""
        tab, config = _tab()
        tab.save()
        assert not [key for key in config.written if key.startswith(_ROI_KEY)]

    def test_an_own_roi_goes_back_as_it_was(self):
        roi = {"enabled": True, "x_px": 10, "y_px": 20, "width_px": 30, "height_px": 40,
               "full_resolution": True, "output_short_side_px": 224}
        tab, config = _tab(**{_ROI_KEY: roi})
        tab.save()
        assert {key: config.written[f"{_ROI_KEY}.{key}"] for key in roi} == roi

    def test_a_disabled_one_is_the_whole_frame_and_stays_disabled(self):
        tab, config = _tab(**{_ROI_KEY: {"enabled": False}})
        assert _roi_fields(tab)["mode"].currentData() == "full"
        tab.save()
        assert config.written[f"{_ROI_KEY}.enabled"] is False

    def test_back_to_the_camera_roi_writes_null(self):
        """Sin sección rige el de la cámara: es lo que el motor lee como «no tiene propio»."""
        tab, config = _tab(**{_ROI_KEY: {"enabled": True}})
        fields = _roi_fields(tab)
        fields["mode"].setCurrentIndex(fields["mode"].findData("camera"))
        tab.save()
        assert config.written[_ROI_KEY] is None

    def test_the_geometry_is_editable_only_for_its_own_roi(self):
        tab, _ = _tab()
        fields = _roi_fields(tab)
        assert not fields["spins"]["x_px"].isEnabled()
        fields["mode"].setCurrentIndex(fields["mode"].findData("own"))
        assert fields["spins"]["x_px"].isEnabled()

    def test_the_view_size_needs_full_resolution(self):
        tab, _ = _tab(**{_ROI_KEY: {"enabled": True}})
        fields = _roi_fields(tab)
        assert not fields["output_short_side_px"].isEnabled()
        fields["full_resolution"].setChecked(True)
        assert fields["output_short_side_px"].isEnabled()

    def test_drawing_asks_for_the_dialog_with_the_pipeline_key(self):
        tab, _ = _tab(**{_ROI_KEY: {"enabled": True}})
        requests = []
        tab.open_roi_requested.connect(lambda *args: requests.append(args))
        tab._on_draw_roi(_PIPELINE, "camera_1")
        assert requests == [("camera_1", _ROI_KEY, "inf_roi_title")]

    def test_reload_brings_the_geometry_the_dialog_wrote(self):
        tab, config = _tab(**{_ROI_KEY: {"enabled": True}})
        tab._on_draw_roi(_PIPELINE, "camera_1")
        config._values[_ROI_KEY] = {"enabled": True, "x_px": 7, "width_px": 9}
        tab.reload_roi("camera_1")
        assert _roi_fields(tab)["spins"]["x_px"].value() == 7
