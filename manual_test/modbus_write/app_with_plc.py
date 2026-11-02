"""
Prueba manual: la app de verdad, con lo que escribe el PLC entrando por Modbus.

Uso — con el intérprete del venv; `writable_server.py` tiene que estar cortado, porque
toma el mismo puerto:

    Windows:  .venv\\Scripts\\python.exe manual_test\\modbus_write\\app_with_plc.py
    Linux:    .venv/bin/python manual_test/modbus_write/app_with_plc.py [--headless]

Corre el `main.py` del repo, sin copiarlo, con esta carpeta como instalación
(`SIP_DATA_DIR`): el `config.yaml` de al lado —dos cámaras mock y dos pipelines mock— y el
`register_map.yaml` de al lado. Lo único que agrega es lo que agregaría un fork en el bloque
marcado de `main.py`:

    - **Una compuerta** (`_build_frame_gates`): `pipeline_1`, sobre «Cinta», infiere sólo
      mientras el PLC dice que hay un evento (`event_active` = 1, en 40105). Es una
      variable leída como **nivel**.
    - **Un disparo** (`_on_signals_changed`): cada número nuevo que el PLC escribe en
      `measure_seq` (40101) le pide una medición a `pipeline_2`, sobre «Báscula», que mide
      sólo a pedido (`cycle_trigger: request`). Es una variable leída como **evento**, con
      `plc_changed()`.

Los logs y el dataset quedan en `data/`, que no se versiona. Del lado del PLC, Modbus Poll
o `plc_client.py --app` sobre 127.0.0.1:5503, unit id 1.

Qué mirar:
    - **Al arrancar no infiere nada.** `event_active` está en 0, su reposo, y nadie pidió una
      medición: las dos cámaras se ven en vivo, sin overlay.
    - **Escribir 1 en 40105** deja `[Modbus] PLC: event_active: False → True` en el log, y en
      el frame siguiente aparece el overlay en «Cinta». Escribir 0 lo vuelve a cortar.
    - **Escribir un número nuevo en 40101** —1, después 2, y así— deja
      `[Scheduler] pipeline_2: ronda pedida.`, «Báscula» muestra el anotado de esa medición,
      y aparece un JSON nuevo en `data/dataset/` con `"measure_seq": N` en `signals`, junto
      con las demás variables del PLC del momento. Reescribir el mismo número no pide nada.
    - **Dos números seguidos, rápido**: el segundo llega con la ronda del primero en curso y
      queda `[Prueba] Ronda en curso: el disparo 7 se ignora.` Es la política de esta prueba;
      un fork puede dejarlo pendiente para cuando cierre la ronda.
    - **La pestaña Modbus del diagnóstico** muestra 40101-40108 con lo que escribió el PLC,
      y 40010-40012 con los umbrales del config.
    - **Los umbrales se publican y no se escriben**: 40010-40012 leen 430, 30 y 600, y
      escribirlos da 0x02 con un warning en el log. Cambiar `min_result_confidence_pct` desde
      la pestaña de inferencia y guardar: 40011 cambia en el tick siguiente.
    - Reiniciar la app deja las variables en 0 otra vez: el PLC las escribe de forma
      cíclica, así que en la planta se reponen solas.
"""

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent

# Antes de importar nada del repo: `system/paths.py` lee la instalación al importarse, y de
# ahí salen el config, el mapa de registros, los logs y el dataset.
os.environ["SIP_DATA_DIR"] = str(_HERE)

_REPO_ROOT = next(
    (p for p in _HERE.parents if (p / "system").is_dir()),
    None,
)
if _REPO_ROOT is None:
    raise SystemExit("No se encontró la raíz del repo: ningún directorio padre tiene system/.")
sys.path.insert(0, str(_REPO_ROOT))

import main  # noqa: E402
from system.frame_signals import plc_changed  # noqa: E402
from system.logger import logger  # noqa: E402

_MEASURE_COUNTER = "measure_seq"
_GATED_PIPELINE = "pipeline_1"
_ON_REQUEST_PIPELINE = "pipeline_2"


class _PlcDemoApplication(main.Application):
    """La app del template, con lo que agregaría un fork en el bloque marcado de main.py."""

    def _build_frame_gates(self):
        def while_event_active(camera_slot, signals):
            return signals.plc.get("event_active") is True
        return {_GATED_PIPELINE: while_event_active}

    def _on_signals_changed(self, previous, current):
        # El 0 es «nunca disparó»: volver a 0 no pide nada.
        if not plc_changed(previous, current, _MEASURE_COUNTER):
            return
        seq = current.plc.get(_MEASURE_COUNTER)
        if not seq:
            return
        if not self._scheduler.request_round(_ON_REQUEST_PIPELINE):
            logger.info(f"[Prueba] Ronda en curso: el disparo {seq} se ignora.")


if __name__ == "__main__":
    # `main()` construye `Application` por nombre: con esto arma la de la prueba.
    main.Application = _PlcDemoApplication
    raise SystemExit(main.main([str(_HERE / "config.yaml"), *sys.argv[1:]]))
