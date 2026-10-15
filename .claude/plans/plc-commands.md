# Plan: el PLC escribe — comandos, parámetros y disparo de medición

**Estado: diseño, sin código.** Branch `machinery/plc-commands`, sobre `main` (v0.9.0, con
las compuertas de frames y la palabra de estado por pipeline ya adentro).

Este documento está escrito para que otra instancia lo tome sin haber visto la
conversación que lo originó: el porqué de cada decisión está acá y no por referencia. Se
implementa por pasos (§8) y **cada paso se valida antes de empezar el siguiente**.

---

## 1. Qué se quiere

Hoy el PLC sólo lee. Se quiere que, con el Modbus activo, el equipo también **lea lo que el
PLC escribe** para tres cosas:

1. **Prender y apagar** partes del proceso en marcha: la inferencia de un pipeline, el
   overlay, la recolección del dataset.
2. **Parámetros de configuración**, como los umbrales de un resultado confiable.
3. **Forzar una medición** en las aplicaciones con ciclo: el PLC sabe cuándo la pieza está
   en posición, y el equipo mide entonces, no cada `cycle_interval_s`.

Y cuatro más que salen de lo mismo y conviene diseñar juntas (§6): el latido del PLC con su
política de pérdida de enlace, el id de sesión del equipo, el pedido de guardar una imagen,
y el número de lote o de pieza para la trazabilidad.

Todo esto es **maquinaria**: el cómo vive en el template y cada fork declara en su
`register_map.yaml` qué filas usa. Un fork que no declara ninguna fila escribible se
comporta exactamente como hoy.

## 2. Cómo está hoy

- **El servidor es de sólo lectura por contrato.** `_ReadOnlyHoldingRegisters.validate()`
  (`system/modbus/server.py`) acepta sólo FC03; FC06 y FC16 vuelven con 0x02, y
  `test/modbus/test_server.py::test_writes_are_rejected` lo fija. `CLAUDE.md` lo tiene como
  decisión vigente: «El mapa se sirve de sólo lectura… el bloque de configuración
  escribible por el PLC existe en la maquinaria pero el template no lo usa».
- **El esquema ya trae un espejo R/W que nadie llama.** `PRODUCER_CONFIG`, `mirror_batch()`,
  `detect_plc_writes()` y `apply_plc_writes()` en `system/modbus/schema.py`. Detecta
  escrituras **por sondeo**: compara el registro contra lo último que escribió la app.
- **`FrameSignals` ya está pensado para esto.** Su docstring en `main.py`: «Una fuente
  nueva —un registro que escriba el PLC— es un campo más, y las compuertas que no lo usan
  no cambian».
- **El mapa ocupa 1-100 y `modbus.register_count` es 100.** No hay lugar libre arriba.
- **Qué se relee en caliente y qué no** — esto decide qué puede tocar el PLC:

| Clave | Quién la lee | ¿En caliente? |
|---|---|---|
| `inference.pipelines.<p>.min_detections`, `min_result_confidence_pct`, `interval_s` | motor, `_setting()` con caché por `revision` | sí, en el frame siguiente |
| `cameras.<c>.illumination_min`, `cameras.<c>.roi` | motor, igual | sí |
| `inference.overlay.*` | motor, `_cached("overlay_options")` | sí |
| `frames_per_cycle`, `cycle_interval_s`, `capture_timeout_s` | scheduler, `_read_option()` en cada uso | sí |
| `image_collector.enabled`, `mode`, intervalos | colector, en cada uso | sí |
| `inference.models.<m>.min_confidence_pct` | modelo, propiedad leída por `predict()` | sí, **salvo que la metadata de unos pesos cifrados la declare: ahí gana la metadata** |
| `inference.pipelines.<p>.enabled` | motor por push, pero `_build_engines()` al arrancar | **sólo para apagar**: uno que arrancó apagado no tiene hilo |
| `process.*` | `hooks.build_hooks()`, una sola vez | **no** |
| exposición, ganancia, fps, `cameras.<c>.enabled` | driver al abrir | **no, y por decisión** («En las cámaras no se ajusta nada en caliente») |

## 3. Problemas con la composición actual

Cada uno con la consecuencia concreta y la resolución que toma este plan.

**P1. El servidor escribiría a medias.** El `async_OLD_setValues` de pymodbus 3.15 valida
registro por registro *mientras* escribe: un FC16 que empieza en una fila escribible y se
mete en una de sólo lectura deja escrita la primera mitad y después contesta 0x02. Tampoco
hay validación de valores. → El contexto valida la petición **entera** antes de tocar nada:
direcciones (0x02 si alguna no es escribible) y valores (0x03, `ILLEGAL_VALUE`, si alguno
cae fuera del rango de su fila). Verificado en la fuente: `async_OLD_setValues` devuelve
`ExcCodes | None` y `register_message.py` convierte ese código en `ExceptionResponse`, así
que 0x03 sale sin tocar nada interno.

**P2. Sondear pierde comandos y pisa escrituras.** `detect_plc_writes()` corre en el tick de
1 s: un pulso 0→1→0 del PLC dentro del segundo no existe para el equipo, y una escritura que
cae entre la detección y el `update_block()` del mismo tick queda pisada por el valor que
la app había calculado. → **Las escrituras entran por evento**: el contexto, en el hilo del
servidor y bajo el mismo lock, deja `{addr: raw}` en una `queue.SimpleQueue`; un QTimer de
100 ms en el hilo de la GUI la drena. Se elimina `detect_plc_writes()`; `apply_plc_writes()`
se queda, alimentado por la cola.

**P3. Dos escritores sobre el mismo registro.** El tick de registros escribe por nombre: si
alguna vez incluye una fila del PLC —un nombre armado con el slot que coincide— le pisa el
comando con un 0 cada segundo. → Cada fila tiene **un solo dueño**. Las filas de comandos y
disparos (`producer: plc`) no las escribe la app nunca, y lo garantiza el esquema:
`encode()`/`encode_batch()` levantan con un nombre `plc`. Las de parámetros
(`producer: config`) son el único espejo compartido, y la cola de P2 las deja
consistentes: si la app re-espeja X mientras está encolado el Y del PLC, el drenado aplica
Y y el tick siguiente espeja Y.

**P4. El datastore está en RAM y arranca en 0.** Si el equipo se reinicia, todo lo que el
PLC escribió desaparece. Un PLC que escribe «al cambiar» cree que el pipeline sigue en
pausa mientras corre. → Tres reglas:
  - **En todo comando, 0 es lo nominal** (lo que dice el config). Un bit prendido es un
    desvío pedido por el PLC: pausar, apagar el overlay. Un equipo recién arrancado hace lo
    nominal, que nunca es lo peligroso.
  - **El equipo publica un `app_session_id`** al azar en cada arranque. Cuando cambia, el
    PLC sabe que tiene que volver a mandar sus comandos. Se recomienda además que el PLC
    los escriba de forma cíclica, que es la práctica corriente.
  - **El efecto se publica aparte del pedido**: el PLC compara lo que pidió con lo que
    informa la palabra de estado del pipeline (bit nuevo de pausa, §5.1).
  Los parámetros sí sobreviven: se persisten en `config.yaml` y se re-espejan al arrancar.

**P5. `enabled` es configuración, no un interruptor.** Un pipeline con `enabled: false` al
arrancar no tiene hilo, así que el PLC no lo puede prender; y un `cfg.set(enabled)` desde el
PLC se persistiría, convirtiendo una pausa de la línea en una edición del config que
sobrevive al reinicio. → **La pausa del PLC no toca `enabled`**: es estado de corrida, se
aplica con la compuerta de frames que ya existe (§5.1), y `enabled` sigue siendo de la
instalación. El PLC puede pausar y reanudar lo que arrancó; no puede levantar lo que no.

**P6. Una compuerta sobre un pipeline con ciclo traba la ronda.** Lo dice
`_check_frame_gates()`: el scheduler espera la cámara de turno y, cortada, la ronda no
avanza. → Para un pipeline con ciclo la pausa va al scheduler (`set_paused()`): aborta la
ronda en curso **sin emitir** —un ciclo vacío publica ceros, y una pausa no es una medición
en cero— y no abre otra hasta que se reanude. Los registros quedan con su último valor,
igual que con la compuerta.

**P7. `process:` se lee una sola vez.** Un parámetro del proceso escrito por el PLC no
tendría efecto hasta reiniciar. → El motor gana `set_hooks(hooks)`, que reemplaza de una vez
la referencia al `EngineHooks` congelado (asignar una referencia es atómico; el hilo de
inferencia toma la nueva en el frame siguiente). Una fila del PLC sobre `process.*` declara
`apply: rebuild_hooks`, y ese hook de `main.py` vuelve a llamar `build_hooks(config)` y se
lo pasa a cada motor. **Se reconstruyen todos juntos**, así que se conserva el invariante de
`hooks.py`: el annotator que dibuja un límite y el analyzer que mide contra él leen el mismo
número.

**P8. Con pesos protegidos, el PLC escribiría en el vacío.** La metadata cifrada gana sobre
el config (`AbstractModel._get_option`), así que un `min_confidence_pct` escrito por el PLC
quedaría en el archivo, en el registro, en la pantalla… y el modelo seguiría usando el
suyo. → Al arrancar, después de cargar los modelos, el cableado avisa por cada fila cuyo
`config_key` el modelo toma de su metadata, y **esa fila deja de ser escribible** (0x02): un
error que se ve es mejor que un umbral que miente. Se recomienda no mapear umbrales de
modelo y usar los del pipeline (`min_result_confidence_pct`), que son del resultado.

**P9. La pantalla de configuración revierte lo que escribió el PLC.** `ConfigView` guarda
todas las pestañas cargadas desde los valores del formulario: si el PLC cambió un umbral
después de que se cargó la pestaña, el próximo «Guardar» del operador escribe el valor
viejo, y nada avisa. Al revés también: si el PLC escribe de forma cíclica, lo que cambia el
operador dura un ciclo. → **Un dueño por parámetro, decidido por la instalación**: una
clave con fila escribible en el mapa la maneja el PLC, y en la UI su campo se muestra de
sólo lectura con la nota «lo maneja el PLC» (texto en `ui/strings.py`). Las pestañas
genéricas preguntan si la clave está tomada con una función del esquema, sin conocer el
mapa. El `save()` de esas pestañas no escribe las claves tomadas.

**P10. Persistir cuesta disco y comentarios.** `ConfigManager.save()` reescribe el archivo
entero (los comentarios del YAML se pierden, igual que hoy al guardar desde la UI), y un PLC
que escribe cíclicamente cada 100 ms gastaría una eMMC de Jetson en meses. → Se persiste
**sólo si el valor cambió**, y el guardado se agrupa: a lo sumo uno cada
`modbus.writes.save_debounce_s` (default 5 s). Se persiste en `config.yaml` y no en un
archivo aparte porque la corrida de imágenes sueltas (`offline.py`) lee el config: con los
valores en otro lado, una imagen pasaría con umbrales que no son los de la línea.

**P11. La licencia no se puede saltear por Modbus.** → Un pipeline sin licencia no tiene
hilo y no hay comando que lo levante. Con `degrade`, los comandos se aceptan y un disparo se
contesta, pero con el bit «resultados retenidos por licencia» (§5.3): un PLC que recibe el
ack sin ese bit leería como nueva una medición vieja. Con `absent`/`invalid`, todo disparo se
contesta en el acto con «disparo rechazado».

**P12. Modbus no tiene autenticación.** Habilitar escrituras le da el control del proceso a
cualquiera que llegue al puerto 502. → Tres capas: `modbus.writes.enabled: false` por
defecto (con eso el datastore se comporta exactamente como hoy); rango por fila y 0x03 fuera
de él; y cada escritura aplicada se loguea a INFO con `nombre: viejo → nuevo`. Una lista de
IPs permitidas para el TCP queda como pregunta abierta (§9): pymodbus 3.15 no le pasa la
conexión al contexto y hay que ver si se puede sin tocar su servidor. En RTU la seguridad
es el cable.

**P13. No hay lugar en el mapa ni en una sola lectura.** El mapa llega a 100. Además FC03
lee como máximo 125 registros y FC16 escribe 123, así que el bloque que el PLC escribe tiene
que ser contiguo y caber en un FC16. → Bloques nuevos de 101 a 199 (§7), y
`register_count` deja de ser algo que el integrador tenga que acordarse de subir: el
servidor expone `max(modbus.register_count, SCHEMA.max_addr())`.

**P14. Un frame «después del disparo» puede ser de antes.** En free-run, el primer frame que
llega después del disparo pudo haberse expuesto antes: exposición más transferencia es
cerca de un período de frame, y `frame_ready` no trae el instante de captura. Para una pieza
que llega a posición, ese frame puede no tenerla. → `inference.pipelines.<p>.trigger_settle_ms`
(default: un período al fps medido de la cámara) descarta los frames que llegan antes. La
solución de fondo es el disparo por hardware, que es el plan diferido de captura por
trigger (ver §10).

**P15. El ack tiene que salir con los resultados.** Las métricas salen en el tick de 1 s,
desde `_metrics_by_pair`. Si el ack de un disparo se publicara por otro camino, el PLC podría
ver el ack con los resultados de la medición anterior. → Al cerrar una ronda disparada, el
cableado escribe **en un solo `update_block()`** las métricas del ciclo, la palabra del ciclo
y `done_seq`. El servidor ya garantiza que un bloque se lee entero de antes o entero de
después.

**P16. Valores de 32 bits y con signo.** Un número de lote no entra en 16 bits y el esquema
no sabe partir valores en dos palabras (el reloj se parte a mano en
`_build_clock_registers()`, cuyo docstring ya dice que el día que haga falta el esquema tiene
que aprenderlo). `to_uint16()` satura en 0, así que tampoco hay negativos. → El lote (§6.4)
depende de que el esquema aprenda `words: 2`; los negativos no se piden en ningún caso de
uso y quedan fuera.

**P17. Un test fija el contrato que cambia.** `test_writes_are_rejected` dice «el mapa es de
sólo lectura». Pasa a ser `test_writes_outside_the_writable_rows_are_rejected`, y con
`writes.enabled: false` el test original sigue valiendo tal cual. **Cambiar ese test se
confirma antes de tocarlo**, explicando por qué cambia el contrato.

## 4. Tres clases de registro escrito por el PLC

| | Comandos | Parámetros | Disparos |
|---|---|---|---|
| Qué son | estado deseado de la corrida | valores del config | un pedido puntual |
| `producer` | `plc` (nuevo) | `config` (ya existe) | `plc` |
| Forma | bitfield, 0 = nominal | valor físico escalado, con rango | contador de secuencia |
| Quién escribe el registro | sólo el PLC | los dos (espejo) | sólo el PLC |
| Se persiste | no | sí, en `config.yaml` | no |
| Al reiniciar el equipo | vuelve a 0 (nominal) | se re-espeja del config | vuelve a 0 |
| Cómo sabe el PLC que se aplicó | palabra de estado del pipeline | relee el mismo registro | `done_seq` == su secuencia |

**Por qué un contador y no un bit para el disparo.** Con un bit, el PLC tiene que escribir
1, esperar el ack y escribir 0, y si el equipo no vio el flanco porque la escritura llegó
durante un reinicio, el disparo se pierde sin que nadie lo sepa. Con un contador hay una
sola escritura, es idempotente —reescribir el mismo número no dispara de nuevo—, un
disparo que llega dos veces se reconoce, y el ack es el mismo número de vuelta. El 0 queda
reservado para «nunca disparó», así que el contador del PLC va de 1 a 65535 y vuelve a 1.

**Por qué holding registers y no coils (FC05).** El servidor no expone coils, y todos los
PLC escriben holding registers con FC06/FC16. Un bitfield en un registro es un solo FC16 por
ciclo del PLC para todos los comandos.

## 5. Los tres casos de uso, en código

### 5.1 Prender y apagar

Una palabra de comando global y una por pipeline, cada una con su módulo puro en
`system/formats/` («nadie corre bits afuera»):

- `formats/system_command.py` — `system_command_bitfield`: bit 0 pausar toda la inferencia,
  bit 1 apagar el dataset, bit 2 apagar el overlay.
- `formats/pipeline_command.py` — `pipeline_<N>_command_bitfield`: bit 0 pausar el pipeline,
  bit 1 apagar su overlay, bit 2 apagar su dataset.

Los dos exponen `unpack(word) -> <dataclass congelada>`, simétrico al `pack()` de las
palabras de estado. Un bit fuera de `WORD_MASK` se rechaza con 0x03 (P1).

En `main.py`:

```python
@dataclasses.dataclass(frozen=True)
class PlcCommands:
    """Lo que pide el PLC ahora. Se reemplaza entero, como FrameSignals."""
    system: system_command.SystemCommand = system_command.NOMINAL
    by_pipeline: Mapping[str, pipeline_command.PipelineCommand] = ...

@dataclasses.dataclass(frozen=True)
class FrameSignals:
    gpio_inputs: Mapping[int, int] = ...
    plc: PlcCommands = PlcCommands()      # el campo que el docstring ya anunciaba
```

- **Pausa.** Una compuerta de maquinaria, `_is_paused_by_plc(pipeline, signals)`, que
  `_is_gate_open()` evalúa **antes** que la del fork y en AND con ella. Con eso la pausa
  hereda todo lo que ya hace la compuerta: el modelo queda cargado, no se gasta GPU, los
  registros quedan con su último valor, el overlay se va en el frame siguiente y la cámara
  sin ningún pipeline corriendo se ve en vivo. Para un pipeline con ciclo, además,
  `scheduler.set_paused(pipeline, True)` (P6). La palabra de estado del pipeline gana
  `BIT_PAUSED_BY_PLC = 7`, separado del 6 (compuerta del fork): el PLC tiene que poder
  distinguir «lo pausé yo» de «la planta dice que no hay proceso».
- **Overlay.** El `annotate_gate` pasa a ser por motor:
  `lambda camera, p=pipeline_slot: self._is_annotated_watched(camera) and not self._overlay_off(p)`.
  Sin anotado, `_show_live_when_idle()` ya muestra el frame en vivo.
- **Dataset.** `_on_cycle_complete()` pregunta por el bit antes de `should_collect()`.

Ninguno de los tres toca el config. El drenado de la cola (P2) arma un `PlcCommands` nuevo y
reemplaza `_frame_signals` de una vez, igual que hace hoy `_on_gpio_inputs_updated()`.

### 5.2 Parámetros

Filas `producer: config` con dos campos nuevos en `Reg`: `min_value` y `max_value`, en la
unidad física (no se llaman `min`/`max` para no tapar los builtins). El esquema gana
`writable_addrs()`, `is_valid_raw(addr, raw)` —lo que llama el contexto del servidor antes
de escribir— y `is_plc_owned(config_key)` —lo que pregunta la UI (P9)—.

```yaml
- addr: 161
  name: pipeline_1_min_result_confidence_pct
  desc: Confianza media mínima de un resultado del pipeline 1
  producer: config
  config_key: inference.pipelines.pipeline_1.min_result_confidence_pct
  unit: '%'
  min_value: 0
  max_value: 100

- addr: 165
  name: max_load_y_px_camera_1          # un parámetro de `process:`, del fork
  desc: Altura del límite de carga en la cámara 1
  producer: config
  config_key: process.max_load_y_px.camera_1
  unit: px
  min_value: 0
  max_value: 4000
  apply: rebuild_hooks
```

Camino de una escritura:

1. El contexto valida rango y dueño, escribe bajo lock y encola `{161: 80}`.
2. El drenado llama `SCHEMA.apply_plc_writes(config, writes, hooks, batch)`: `cfg.set()` y,
   si la fila declara `apply`, el hook (`rebuild_hooks`, P7).
3. Se marca el config sucio; el guardado agrupado (P10) lo persiste.
4. El motor lo toma en el frame siguiente por el cambio de `revision`; el tick de registros
   re-espeja el valor, que es el que el PLC relee como confirmación.

Al arrancar: `mirror_batch()` escribe el valor del config en cada fila **antes de levantar
el servidor**, para que el PLC nunca lea un 0 que no es el valor. Avisos de arranque: una
fila cuya clave no se relee en caliente (tabla de §2) se avisa como «necesita reinicio», y
una que la metadata de los pesos pisa queda no escribible (P8).

### 5.3 Forzar una medición

Un modo nuevo del scheduler, declarado por pipeline:

```yaml
inference:
  pipelines:
    pipeline_1:
      frames_per_cycle: 5
      cycle_trigger: plc          # interval (default) | plc
      trigger_settle_ms: 0        # 0 = un período de frame al fps medido
```

Con `cycle_trigger: plc` la ronda no se abre por `cycle_interval_s` sino por
`scheduler.request_round(pipeline, seq)`. «Tener ciclo» (`_is_cycle()` y
`has_measurement_cycle()`) pasa a ser `frames_per_cycle > 1 or cycle_trigger == "plc"`: un
pipeline de un frame disparado también necesita el permiso del scheduler, que es lo que
hace que el frame sea el de después del disparo y no cualquiera.

Registros del pipeline (§7): `pipeline_<N>_trigger_seq` (lo escribe el PLC),
`pipeline_<N>_done_seq` y `pipeline_<N>_cycle_bitfield` (los escribe el equipo, con su
módulo `formats/pipeline_cycle.py`: bit 0 listo, bit 1 midiendo, bit 2 disparo ignorado por
estar midiendo, bit 3 disparo rechazado —en pausa, sin modelo o sin licencia—, bit 4 última
medición vacía —timeout o ningún resultado confiable—, bit 5 resultados retenidos por
licencia).

```
PLC                                   Equipo
 │ espera cycle.listo = 1               │
 │ escribe trigger_seq = N ───────────► │ drenado: seq nuevo → request_round(p, N)
 │                                      │ cycle.midiendo = 1, prende la captura si estaba
 │                                      │ apagada, descarta frames hasta trigger_settle_ms
 │                                      │ junta frames_per_cycle por cámara, en ronda
 │                                      │ última cámara cerrada:
 │                                      │   UN update_block { métricas del ciclo,
 │ ◄─────────────────────────────────── │                    cycle_bitfield, done_seq = N }
 │ ve done_seq == N → lee resultados    │
```

- Un disparo con la ronda en curso no se encola: bit 2 y `done_seq` sin cambiar. El PLC
  espera `listo`.
- Un disparo que no se puede atender (pausado, sin modelo, sin licencia) se contesta **en el
  acto** con `done_seq = N` y el bit 3, en vez de dejar al PLC esperando hasta su timeout.
- Un ciclo que vence por `capture_timeout_s` cierra con `done_seq = N` y el bit 4: el PLC
  siempre recibe una respuesta.
- El `on_demand` del dataset ya guarda una imagen por medición disparada: es el camino de
  `save_now()` de hoy.

## 6. Lo que conviene agregar con lo mismo

**6.1 Latido del PLC.** `plc_heartbeat`: el PLC lo incrementa. Si no cambia en
`modbus.writes.plc_timeout_s` (default 10 s), el enlace se da por perdido y se aplica
`modbus.writes.on_link_loss`: `nominal` (default: los comandos vuelven a 0, todo corre como
dice el config) o `hold` (quedan los últimos). `nominal` es el default porque, para un
pipeline de seguridad, pausado sin nadie que lo reanude es el peor estado posible. Sin
ninguna fila `plc_heartbeat` en el mapa no hay vigilancia. Se publica en `plc_link_bitfield`.

**6.2 Id de sesión.** `app_session_id`, un número al azar distinto de 0 en cada arranque
(P4). Es lo que le dice al PLC «volvé a mandar tus comandos».

**6.3 Guardar esta imagen.** `camera_<N>_save_request_seq`, con el mismo handshake de
contador: el PLC marca que la pieza salió mala más adelante en la línea y pide que se guarde
la última medición de esa cámara. Sale por `collector.save_now()` con el último resultado del
par. Es el dataset de los casos difíciles, elegidos por el proceso y no al azar.

**6.4 Lote o pieza.** `lot_id_high` / `lot_id_low` (32 bits): va al contexto del dataset
(`_build_dataset_context`) y como tag de la telemetría, para poder buscar las imágenes de un
lote reclamado. Depende de que el esquema aprenda valores de dos palabras (P16); es el
último paso.

**Lo que no se hace, a propósito:** ajustar la cámara desde el PLC (decisión vigente),
levantar un pipeline que arrancó apagado (P5), calibrar el lente (necesita el vidrio limpio y
una persona delante), reiniciar la aplicación, coils y FC23.

## 7. Qué registros escribe el PLC — mapa propuesto

Los registros 1-100 no se mueven: un PLC ya programado no se reprograma. Lo nuevo va de 101 a
199, en tres bloques. Como el de cámaras, cada bloque por pipeline tiene paso fijo y en el
template se declara sólo `pipeline_1`.

**101-130 — escritos por el PLC** (`producer: plc`). Contiguos: el PLC los manda en un solo
FC16 cada ciclo.

| Reg | Nombre | Qué |
|---|---|---|
| 101 | `plc_heartbeat` | contador del PLC; su ausencia dispara `on_link_loss` |
| 102 | `system_command_bitfield` | pausa general, dataset off, overlay off |
| 103 + 4·(N−1) | `pipeline_N_command_bitfield` | pausa, overlay off, dataset off del pipeline |
| 104 + 4·(N−1) | `pipeline_N_trigger_seq` | disparo de medición, 1-65535 |
| 105-106 + 4·(N−1) | reservados | del pipeline N |

Hasta `pipeline_7` en 127-130.

**131-160 — respuesta del equipo** (`producer: health`, sólo lectura).

| Reg | Nombre | Qué |
|---|---|---|
| 131 | `app_session_id` | cambia en cada arranque |
| 132 | `plc_link_bitfield` | escrituras habilitadas, latido vencido, última escritura rechazada |
| 133 | `plc_write_rejected_count` | escrituras rechazadas desde el arranque |
| 134 | `plc_last_rejected_addr` | la última dirección rechazada |
| 137 + 3·(N−1) | `pipeline_N_state_bitfield` | la palabra que ya existe sin fila, con el bit 7 nuevo |
| 138 + 3·(N−1) | `pipeline_N_done_seq` | ack del disparo |
| 139 + 3·(N−1) | `pipeline_N_cycle_bitfield` | listo, midiendo, ignorado, rechazado, vacío, retenido |

**161-199 — parámetros** (`producer: config`, R/W). El template declara de ejemplo los
umbrales de `pipeline_1` (`min_result_confidence_pct`, `min_detections`), el
`illumination_min` de `camera_1` y el `cycle_interval_s` de `pipeline_1` (escala 10); el fork
agrega los de `process:`.

El PLC lee con dos FC03 (1-100 y 101-199), y escribe con un FC16 sobre 101-130 cada ciclo y
FC06/FC16 sobre 161-199 sólo al cambiar un parámetro.

## 8. Pasos

Cada paso termina con `ci/check.py` en verde y se valida antes de seguir.

1. **Esquema.** `producer: plc`, `min_value`/`max_value`, `writable_addrs()`,
   `is_valid_raw()`, `is_plc_owned()`, `decode()` público, `encode()` que levanta con una fila
   `plc`. Se borra `detect_plc_writes()`. Tests en `test/modbus/test_schema.py`.
2. **Servidor.** `_ReadOnlyHoldingRegisters` → `_HoldingRegisters(writable, is_valid_raw,
   on_write)`: validación entera de la petición, 0x02 / 0x03, escritura bajo el lock,
   `on_write` bajo el lock. `modbus.writes.enabled: false` deja el conjunto escribible vacío.
   `register_count` derivado (P13). El servidor sigue sin conocer el mapa: recibe el
   conjunto y las dos funciones del cableado. P17 se confirma acá.
3. **Palabras.** `system_command`, `pipeline_command`, `pipeline_cycle`, `plc_link` y el bit 7
   de `pipeline_status`, con sus tests.
4. **Comandos.** Drenado, `PlcCommands` en `FrameSignals`, compuerta de pausa,
   `scheduler.set_paused()`, overlay y dataset, latido con `on_link_loss`, `app_session_id`.
   Tests en `test_main.py` y `test_capture_scheduler.py`.
5. **Parámetros.** Espejo al arrancar antes del servidor, `apply_plc_writes()` desde la cola,
   guardado agrupado, `apply: rebuild_hooks` con `InferenceThread.set_hooks()`, avisos de
   arranque (P8 y claves frías), campos de sólo lectura en la UI.
6. **Disparo.** `cycle_trigger: plc`, `request_round()`, `trigger_settle_ms`, la palabra del
   ciclo, el bloque atómico de P15.
7. **Mapa y documentos.** Las filas de §7 en `register_map.yaml`; `export_map.py` con
   columnas de acceso y rango; `docs/modbus_map.*` regenerado; `docs/plc_commands.md` para
   el integrador (handshake, reinicio, latido, límites de FC03/FC16);
   `manual_test/modbus_write/` con un cliente pymodbus que pausa, cambia un umbral y dispara
   contra la app corriendo; `CLAUDE.md` (la decisión de sólo lectura cambia, contratos,
   «Qué todavía no existe»). Sube la versión.
8. **Lote** (6.3 y 6.4), después de que el esquema aprenda `words: 2`.

## 9. Preguntas abiertas

- ¿`modbus.writes.enabled` en `false` por defecto? Recomendado: sí; un fork lo prende.
- ¿`on_link_loss` por defecto `nominal` o `hold`? Recomendado: `nominal` (§6.1).
- ¿Un dueño por parámetro con la UI en sólo lectura (P9), o «el último que escribe gana»
  con un aviso? Recomendado: un dueño.
- Lista de IPs permitidas para escribir por TCP: ¿se necesita en alguna planta? Hace falta
  un spike para ver si pymodbus 3.15 expone la conexión sin reemplazar su servidor.
- ¿El modo `cycle_trigger: plc` debería aceptar también una entrada de GPIO como fuente del
  disparo? Es el mismo `request_round()`; sólo cambia quién lo llama.

## 10. Relación con otros planes

- **Captura por trigger de software** (`CLAUDE.md` la da por diseñada en
  `.claude/plans/software-trigger-capture.md`, pero ese archivo no está en el repo): ése
  dispara la *cámara*; éste dispara la *medición* sobre una cámara en free-run. Se componen:
  con trigger de cámara, `trigger_settle_ms` deja de hacer falta.
- **Compuertas de frames** (en `main`): la pausa del PLC es una compuerta de maquinaria más,
  y por eso hereda su comportamiento sin código nuevo en el composer ni en el motor.
