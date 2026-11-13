"""
Traducción del estado de un servicio, de una cámara y de la licencia al texto y el color de
su chip.

Único lugar de la UI donde se decide cómo se ve algo caído: antes cada vista repetía la
misma tabla de `status -> etiqueta`, y agregar un estado significaba editar dos archivos. La
página de inicio resume lo mismo que muestran el panel de cada cámara y las pestañas de
diagnóstico, así que las tres leen de acá y no pueden decir cosas distintas.

Los servicios son los seis canales de salida del equipo, los mismos que `com_status`
publica al PLC: esa es la lista canónica y de ahí sale también qué estados cuentan
como "andando" (`com_status.is_active`). El vocabulario de estados lo definen los
dueños de cada subsistema —`system/video/abstract_video_server.py`,
`system/telemetry/backends/abstract_backend.py` y el servidor de Modbus—, todos con
las mismas palabras: `disabled | starting | connecting | active | connected | error`.
Acá sólo se elige con qué texto y con qué color se muestra cada una.

Módulo de presentación: sin Qt, sin I/O. Devuelve strings, así que se testea sin
levantar la GUI.
"""

from system.formats import com_status
from system.license import manager

from ui.strings import tr
from ui.widgets.status_chip import CHIP_ERROR, CHIP_INFO, CHIP_OK, CHIP_WARNING

# Servicios que la UI muestra. El valor es la clave estable con la que las vistas
# los piden; el texto que se ve sale de la tabla de idiomas.
SERVICE_MODBUS_TCP = "modbus_tcp"
SERVICE_MODBUS_RTU = "modbus_rtu"
SERVICE_VIDEO_HTTP = "video_http"
SERVICE_VIDEO_RTSP = "video_rtsp"
SERVICE_INFLUXDB = "influxdb"
SERVICE_MQTT = "mqtt"

SERVICES = (
    SERVICE_MODBUS_TCP,
    SERVICE_MODBUS_RTU,
    SERVICE_VIDEO_HTTP,
    SERVICE_VIDEO_RTSP,
    SERVICE_INFLUXDB,
    SERVICE_MQTT,
)

# Estado -> (clave del texto, estado del chip). Un servicio apagado a propósito no es
# una falla: va en ámbar y no en rojo, para que el rojo signifique siempre "quiso
# levantar y no pudo".
_CHIP_BY_STATUS = {
    "disabled":   ("status_disabled",   CHIP_WARNING),
    "starting":   ("status_starting",   CHIP_WARNING),
    "connecting": ("status_connecting", CHIP_WARNING),
    "error":      ("status_error",      CHIP_ERROR),
}


# Estado de la licencia -> color del chip. Un equipo sin licencia va en rojo y uno
# corriendo desde fuentes en celeste: el segundo no es una falla del equipo, es un build de
# desarrollo, y pintarlo de rojo enseñaría a ignorar el rojo.
_CHIP_BY_LICENSE_STATE = {
    manager.STATE_VALID:            CHIP_OK,
    manager.STATE_UNLICENSED_BUILD: CHIP_INFO,
    manager.STATE_ABSENT:           CHIP_ERROR,
    manager.STATE_INVALID:          CHIP_ERROR,
    manager.STATE_FOREIGN:          CHIP_ERROR,
    manager.STATE_EXPIRED:          CHIP_ERROR,
    manager.STATE_TAMPERED:         CHIP_ERROR,
}


def get_label(service: str) -> str:
    """Nombre del servicio en el idioma activo."""
    return tr(f"service_{service}")


def describe(service: str, status: str) -> tuple[str, str]:
    """
    (texto del chip, estado del chip) para ese servicio en ese estado.

    Los estados que cuentan como andando los define `com_status`, así que el chip
    verde de la UI y el bit que lee el PLC no pueden discrepar. Un estado que no
    está en la tabla se muestra tal cual y en ámbar: es información, no una falla
    confirmada.
    """
    text, chip_state = describe_status(status)
    return f"{get_label(service)}: {text}", chip_state


def describe_status(status: str) -> tuple[str, str]:
    """(texto, estado del chip) de un estado de servicio, sin el nombre del servicio."""
    if com_status.is_active(status):
        return tr("status_active"), CHIP_OK
    text_key, chip_state = _CHIP_BY_STATUS.get(status, ("", CHIP_WARNING))
    if not text_key:
        return status or tr("status_unknown"), chip_state
    return tr(text_key), chip_state


def describe_camera(status: dict) -> tuple[str, str]:
    """
    (texto, estado del chip) de una cámara, desde las claves de
    `AbstractCameraDriver.get_status()`.

    Los cuatro estados se distinguen porque piden cosas distintas: una cámara mal
    configurada necesita que alguien corrija el config y reinicie, una desconectada se
    recupera sola, y una deshabilitada está así porque alguien lo pidió. El orden de los
    `if` es el de la acción más urgente primero.
    """
    if status.get("error"):
        return tr("camera_misconfigured"), CHIP_ERROR
    if not status.get("connected"):
        return tr("camera_disconnected"), CHIP_ERROR
    if not status.get("capture_enabled", True):
        return tr("camera_disabled"), CHIP_WARNING
    return tr("camera_connected"), CHIP_OK


def describe_license(status: dict) -> tuple[str, str]:
    """
    (texto, estado del chip) de la licencia, desde `LicenseManager.get_status()`. Con un
    dict vacío —el cableado todavía no publicó— dice «sin datos».
    """
    state = str(status.get("state") or "")
    if not state:
        return tr("lic_state_unknown"), CHIP_INFO
    return tr(f"lic_state_{state}"), _CHIP_BY_LICENSE_STATE.get(state, CHIP_INFO)
