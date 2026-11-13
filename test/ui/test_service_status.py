"""
Tests de la traducción de estados a texto y color: la de un servicio, la de una cámara y la de
la licencia, que comparten la página de inicio, el panel de cada cámara y las pestañas.

Módulo sin Qt: devuelve strings, así que no hace falta un `QApplication`.
"""

from system.license import manager

from ui import service_status
from ui.strings import tr
from ui.widgets.status_chip import CHIP_ERROR, CHIP_INFO, CHIP_OK, CHIP_WARNING


class TestDescribeStatus:
    def test_an_active_service_is_ok(self):
        assert service_status.describe_status("active") == (tr("status_active"), CHIP_OK)

    def test_a_disabled_service_is_amber_and_not_red(self):
        """El rojo es «quiso levantar y no pudo»; apagado a propósito no es una falla."""
        assert service_status.describe_status("disabled")[1] == CHIP_WARNING

    def test_an_error_is_red(self):
        assert service_status.describe_status("error") == (tr("status_error"), CHIP_ERROR)

    def test_the_service_name_goes_in_front_only_in_describe(self):
        text, _ = service_status.describe("modbus_tcp", "active")
        assert text == f"{tr('service_modbus_tcp')}: {tr('status_active')}"


class TestDescribeCamera:
    def test_a_connected_camera_is_ok(self):
        status = {"connected": True, "capture_enabled": True}
        assert service_status.describe_camera(status) == (tr("camera_connected"), CHIP_OK)

    def test_a_disconnected_camera_is_red(self):
        status = {"connected": False, "capture_enabled": True}
        assert service_status.describe_camera(status) == (tr("camera_disconnected"), CHIP_ERROR)

    def test_a_camera_with_capture_off_is_amber(self):
        status = {"connected": True, "capture_enabled": False}
        assert service_status.describe_camera(status) == (tr("camera_disabled"), CHIP_WARNING)

    def test_an_error_wins_over_being_connected(self):
        """Mal configurada pide que alguien toque el config: es lo más urgente."""
        status = {"connected": True, "capture_enabled": True, "error": "driver desconocido"}
        assert service_status.describe_camera(status) == (tr("camera_misconfigured"), CHIP_ERROR)


class TestDescribeLicense:
    def test_a_valid_licence_is_ok(self):
        assert service_status.describe_license({"state": manager.STATE_VALID}) == \
            (tr("lic_state_valid"), CHIP_OK)

    def test_running_from_sources_is_not_red(self):
        """Un build de desarrollo no es una falla del equipo."""
        assert service_status.describe_license(
            {"state": manager.STATE_UNLICENSED_BUILD})[1] == CHIP_INFO

    def test_every_state_without_a_usable_licence_is_red(self):
        for state in (manager.STATE_ABSENT, manager.STATE_INVALID, manager.STATE_FOREIGN,
                      manager.STATE_EXPIRED, manager.STATE_TAMPERED):
            assert service_status.describe_license({"state": state})[1] == CHIP_ERROR, state

    def test_without_a_status_it_says_there_is_no_data(self):
        assert service_status.describe_license({}) == (tr("lic_state_unknown"), CHIP_INFO)
