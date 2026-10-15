# Plan: el PLC escribe — disparo de medición, umbrales del proceso y variables

**Estado: diseño, sin código.** Branch `machinery/plc-commands`, sobre `main` (v0.9.0, con
las compuertas de frames y la palabra de estado por pipeline ya adentro).

Este documento está escrito para que otra instancia lo tome sin haber visto la
conversación que lo originó: el porqué de cada decisión está acá y no por referencia. Se
implementa por pasos (§9) y **cada paso se valida antes de empezar el siguiente**.

---

## 1. Qué se quiere

Hoy el PLC sólo lee (40001-40100). Se quiere que también escriba, y **sólo para tres
cosas**:

1. **Disparar una medición de un ciclo**, descartando la que esté en curso si la hay.
2. **Cambiar un umbral del proceso.** El valor nuevo queda en `config.yaml` y se ve en la
   interfaz, igual que si lo hubiera cambiado el operador.
3. **Mandar una variable que use un pipeline.** Por ejemplo, «empieza un evento»: el
   pipeline ya está habilitado y cargado, pero no infiere hasta que el PLC lo dice.

**El PLC no prende ni apaga nada**: ni pipelines, ni cámaras, ni protocolos, ni el overlay,
ni el dataset. Eso se configura desde la interfaz o el archivo, como hoy. No es una
convención: el esquema rechaza al cargar una fila escribible que apunte fuera de lo
permitido (§5.2), así que un mapa que lo intente no arranca.

Todo esto es **maquinaria**: el cómo vive en el template y cada fork declara en su
`register_map.yaml` qué filas escribibles usa. **El mapa es el interruptor**: un fork sin
filas escribibles se comporta exactamente como hoy, sin ninguna clave de config nueva.

## 2. Qué direcciones escribe el PLC

**40101-40200** (registros 101-200), a continuación del bloque de lectura y con la misma
regla **reg N ⇔ 4000N**.

| Bloque | Quién escribe | Qué |
|---|---|---|
| 40001-40100 | el equipo | lo de hoy, más las respuestas a lo que escribe el PLC (§2.2) |
| 40101-40200 | el PLC | disparos, variables y umbrales (§2.1) |

Por qué ahí y no en un rango aparte como 41001:

- **Un solo datastore y una sola regla.** No hay offset nuevo que traducir entre el
  código, la documentación y el PLC.
- **Cada bloque entra en una sola petición.** FC03 lee hasta 125 registros y FC16 escribe
  hasta 123: los 100 del bloque de escritura se escriben de una vez, y el PLC los puede
  releer con un FC03 para confirmar.
- **`register_count` no hay que acordarse de subirlo.** El servidor expone
  `max(modbus.register_count, SCHEMA.max_addr())`, así que declarar la fila 200 alcanza.

### 2.1 Bloque de escritura, 40101-40200

| Reg | Nombre | Qué | Persiste |
|---|---|---|---|
| 40101-40110 | `pipeline_N_trigger_seq`, en 100+N | disparo del pipeline N, contador 1-65535 | no |
| 40111-40140 | variables, con el nombre que elija el fork | lo que lee la compuerta de un pipeline | no |
| 40141-40200 | umbrales, con el nombre de su clave | espejo R/W de `config.yaml` | sí |

Cómo escribe el PLC cada sub-bloque, y por qué importa:

- **Disparos**: un FC06 cuando quiere medir.
- **Variables**: de forma cíclica, un FC16 sobre 40111-40140. Así un reinicio del equipo se
  repara solo en el ciclo siguiente del PLC (§3, P4).
- **Umbrales**: **sólo al cambiar**. Un PLC que los reescribe cada ciclo deshace en
  milisegundos lo que el operador cambie en la pantalla, y nada lo avisa. Va en la
  documentación del integrador.

### 2.2 Las respuestas, en el bloque de lectura

Lo que el equipo contesta lo escribe el equipo, así que va donde el PLC ya lee: en
40092-40097, que hoy está libre.

| Reg | Nombre | Qué |
|---|---|---|
| 40092 | `app_session_id` | número al azar, distinto de 0, nuevo en cada arranque del equipo |
| 40093 | `pipeline_1_state_bitfield` | la palabra que ya existe sin fila, con bits nuevos (§5.1) |
| 40094 | `pipeline_1_done_seq` | el último disparo que se terminó de medir |
| 40095-40096 | ídem `pipeline_2` | |
| 40097 | reservado | |

Alcanza para dos pipelines con disparo. Un fork con más toma las filas que necesite del
final del rango 3-50, que es suyo; no se mueve nada de lo existente.

## 3. Problemas con la composición actual

**P1. El servidor es de sólo lectura por contrato.** `_ReadOnlyHoldingRegisters.validate()`
acepta sólo FC03, `test_writes_are_rejected` lo fija y `CLAUDE.md` lo tiene como decisión
vigente. → Pasa a servir FC06 y FC16 **sólo sobre las filas escribibles del mapa**. **Cambiar
ese test se confirma antes de tocarlo**; con un mapa sin filas escribibles el test original
sigue valiendo tal cual.

**P2. pymodbus escribiría a medias.** Su `async_OLD_setValues` (3.15) valida registro por
registro *mientras* escribe: un FC16 que se mete en una fila no escribible deja escrita la
primera mitad. → El contexto valida la petición **entera** antes de tocar nada: 0x02 si
alguna dirección no es escribible, 0x03 si algún valor cae fuera del rango de su fila.
Verificado en la fuente: el método devuelve `ExcCodes | None` y `register_message.py` lo
convierte en `ExceptionResponse`.

**P3. Sondear pierde escrituras.** El espejo que ya trae el esquema (`detect_plc_writes()`)
compara una vez por segundo: dos disparos dentro del mismo segundo son uno, y una escritura
que cae entre la detección y el `update_block()` del tick queda pisada. → **Las escrituras
entran por evento**: el contexto, bajo el mismo lock con que escribe, deja `{addr: raw}` en
una `queue.SimpleQueue`, y un QTimer de 100 ms en el hilo de la GUI la drena. Se borra
`detect_plc_writes()`; `apply_plc_writes()` se queda, alimentado por la cola.

**P4. El datastore vive en RAM y arranca en 0.** Si el equipo se reinicia, lo que escribió
el PLC desaparece. → Por clase:
  - **Disparos**: el 0 es «nunca disparó», así que el próximo número del PLC es nuevo. Un
    disparo que cae durante el reinicio se pierde, y el PLC lo ve porque `done_seq` no llega.
  - **Variables**: vuelven a 0. Por eso **una variable en 0 tiene que significar lo de
    reposo** («no hay evento»), y por eso el PLC las escribe de forma cíclica.
    `app_session_id` le dice al PLC que el equipo arrancó de nuevo.
  - **Umbrales**: se persisten en el config y se re-espejan al arrancar, **antes de levantar
    el servidor**, para que el PLC nunca lea un 0 que no es el valor.

**P5. Dos escritores sobre el mismo registro.** El tick escribe por nombre: si algún día
incluye una fila del PLC, le pisa el valor cada segundo. → **Un solo dueño por fila.** Las
filas de disparo y de variables (`producer: plc`) no las escribe nunca la app, y lo
garantiza el esquema: `encode()` levanta con un nombre `plc`. Los umbrales
(`producer: config`) son el único espejo compartido, y con la cola de P3 quedan
consistentes: si la app re-espeja X mientras está encolado el Y del PLC, el drenado aplica Y
y el tick siguiente espeja Y.

**P6. `process:` se lee una sola vez.** `hooks.build_hooks()` lo lee al armar el motor, así
que un umbral del proceso escrito por el PLC no tendría efecto hasta reiniciar. → El motor
gana `set_hooks(hooks)`, que reemplaza de una vez la referencia al `EngineHooks` congelado;
asignar una referencia es atómico y el hilo de inferencia toma la nueva en el frame
siguiente. Cuando un drenado aplica alguna clave `process.*`, el cableado llama
`build_hooks(config)` **una vez** y se lo pasa a cada motor. Se reconstruyen todos juntos, así
que se conserva el invariante de `hooks.py`: el annotator que dibuja un límite y el analyzer
que mide contra él leen el mismo número.

**P7. La pantalla mostraría un valor viejo, y lo guardaría.** `ConfigView` guarda todas las
pestañas desde los valores del formulario: si el PLC cambió un umbral después de que se cargó
la pestaña, el próximo «Guardar» del operador escribe el valor viejo encima, sin aviso. →
**La pantalla se actualiza con lo que escribió el PLC**, campo por campo, con el mismo
recurso que ya usan `refresh_roi_fields()` y `refresh_lens_reference()`: recargar lo que
cambió sin descartar lo que el operador esté editando en otro campo. Si el operador tenía a
medio editar justo ese campo, gana el PLC —es el valor que ya está midiendo— y queda una
línea en el log. Con el formulario al día, «Guardar» no revierte nada.

**P8. Persistir cuesta disco y comentarios.** `ConfigManager.save()` reescribe el archivo
entero, y los comentarios del YAML se pierden igual que hoy al guardar desde la UI. → Se
persiste **sólo si el valor cambió**, y los guardados se agrupan: a lo sumo uno por segundo.
Va a `config.yaml` y no a un archivo aparte porque la corrida de imágenes sueltas
(`offline.py`) lee el config: con los umbrales en otro lado, una imagen pasaría con valores
que no son los de la línea.

**P9. Un disparo nuevo con la ronda en curso.** Se pide descartar la medición en curso. El
problema es el frame que ya estaba en el motor: su resultado llega después del descarte y el
scheduler, que ya abrió la ronda nueva y está esperando, lo tomaría como el primero de ella.
→ `push_frame()` gana un `tag` opcional que el motor copia al `InferenceResult`; el scheduler
le pone a cada ronda un número propio y descarta en `on_result_ready()` los resultados con
otro. Un resultado sin tag —el camino sin ciclo— no cambia.

**P10. Un frame «después del disparo» puede ser de antes.** En free-run el primer frame que
llega después del disparo pudo haberse expuesto antes, y `frame_ready` no trae el instante
de captura. → `inference.pipelines.<p>.trigger_settle_ms` (default: un período de frame al
fps medido) descarta los frames que llegan antes de ese margen. La solución de fondo es el
disparo de la cámara por hardware (§10).

**P11. El ack tiene que salir con los resultados.** Las métricas salen en el tick de 1 s,
desde `_metrics_by_pair`. Si `done_seq` saliera por otro camino, el PLC podría ver el ack con
los números de la medición anterior. → Al cerrar una ronda disparada, el cableado escribe
**en un solo `update_block()`** las métricas del ciclo, la palabra del pipeline y `done_seq`.
El servidor ya garantiza que un bloque se lee entero de antes o entero de después.

**P12. Las compuertas no son para pipelines con ciclo.** Lo dice `_check_frame_gates()`: con
una cámara cortada la ronda espera hasta su timeout. → Una variable corta los frames de un
pipeline **sin ciclo**; en uno con ciclo, el equivalente es el disparo. El aviso de arranque
que ya existe cubre el caso.

**P13. La licencia no se puede saltear por Modbus.** → Un pipeline sin licencia no tiene hilo
y su disparo se contesta en el acto con «rechazado». Con `degrade` el disparo se mide, pero el
ack sale con el bit «resultados retenidos»: sin él, el PLC leería como nueva una medición
vieja.

**P14. Modbus no tiene autenticación.** Cualquiera que llegue al puerto puede escribir. →
Sólo las filas declaradas son escribibles, cada una con su rango, sólo sobre las claves de
§5.2, y cada escritura aplicada se loguea a INFO con `nombre: viejo → nuevo`. Una lista de IPs
permitidas queda como pregunta abierta (§8).

## 4. Tres clases de registro escrito por el PLC

| | Disparo | Variable | Umbral |
|---|---|---|---|
| `producer` | `plc` | `plc` | `config` |
| Forma | contador de secuencia | valor, o 0/1 con `is_bool` | valor físico escalado, con rango |
| Quién escribe el registro | sólo el PLC | sólo el PLC | los dos (espejo) |
| Se persiste | no | no | sí, en `config.yaml` |
| Se ve en la pantalla | tabla de Modbus | tabla de Modbus | tabla de Modbus **y** su campo de configuración |
| Al reiniciar el equipo | 0 | 0 (reposo) | se re-espeja del config |
| Cómo confirma el PLC | `done_seq` == su número | relee el registro | relee el registro |

**Por qué un contador y no un bit para el disparo.** Con un bit el PLC escribe 1, espera el
ack y escribe 0; si el equipo no vio el flanco, el disparo se pierde sin que nadie lo sepa.
Con un contador hay una sola escritura, reescribir el mismo número no vuelve a disparar, y el
ack es el mismo número de vuelta. El 0 queda reservado para «nunca disparó», así que el PLC
cuenta de 1 a 65535 y vuelve a 1.

## 5. Los tres casos de uso, en código

### 5.1 Disparar una medición

Sirve tanto si la ronda la abre el reloj como si la abre sólo el PLC:

```yaml
inference:
  pipelines:
    pipeline_1:
      frames_per_cycle: 5
      cycle_trigger: interval     # interval (default): rondas por cycle_interval_s, y el
                                  #   disparo abre una ya. plc: sólo el disparo abre rondas
      trigger_settle_ms: 0        # 0 = un período de frame al fps medido
```

«Tener ciclo» (`_is_cycle()` y `has_measurement_cycle()`) pasa a ser
`frames_per_cycle > 1 or cycle_trigger == "plc"`: un pipeline de un frame disparado también
necesita el permiso del scheduler, que es lo que hace que el frame sea el de después del
disparo y no cualquiera.

Al drenar un `pipeline_N_trigger_seq` distinto del último visto, el cableado llama
`scheduler.request_round(pipeline, seq)`:

- **Con una ronda en curso**, la descarta sin emitir —un ciclo vacío publica ceros, y esto no
  es una medición en cero— y abre una nueva con número propio (P9). La palabra del pipeline
  lo dice en el ack de la nueva.
- Prende la captura si estaba apagada entre ciclos y descarta los frames de antes de
  `trigger_settle_ms` (P10).
- Junta `frames_per_cycle` por cámara, en ronda, como hoy.
- Al cerrar la última cámara, un solo `update_block` con métricas, palabra y `done_seq = seq`
  (P11).
- Si no se puede medir —pipeline sin ciclo, sin modelo, sin licencia— contesta en el acto con
  `done_seq = seq` y el bit de rechazo, en vez de dejar al PLC esperando su timeout.
- Si vence `capture_timeout_s`, cierra con `done_seq = seq` y el bit de medición vacía: el PLC
  siempre recibe una respuesta.

```
PLC                                   Equipo
 │ escribe trigger_seq = N ───────────► │ drenado: N nuevo → request_round(p, N)
 │                                      │ ¿ronda en curso? se descarta, sin publicar
 │                                      │ palabra: midiendo = 1
 │                                      │ espera trigger_settle_ms, junta los frames
 │ ◄─────────────────────────────────── │ UN update_block { métricas, palabra, done_seq = N }
 │ ve done_seq == N → lee resultados    │
```

Bits nuevos en `system/formats/pipeline_status.py`, a continuación del 6:

| Bit | Qué |
|---|---|
| 7 | midiendo un disparo |
| 8 | la medición publicada reemplazó a una descartada por un disparo nuevo |
| 9 | último disparo rechazado: sin ciclo, sin modelo o sin licencia |
| 10 | última medición vacía: timeout o ningún resultado confiable |
| 11 | resultados retenidos por la licencia |

### 5.2 Cambiar un umbral del proceso

Filas `producer: config` con dos campos nuevos en `Reg`: `min_value` y `max_value`, en la
unidad física (no `min`/`max`, que taparían los builtins).

```yaml
- addr: 141
  name: max_load_y_px_camera_1
  desc: Altura del límite de carga en la cámara 1
  producer: config
  config_key: process.max_load_y_px.camera_1
  unit: px
  min_value: 0
  max_value: 4000

- addr: 142
  name: pipeline_1_min_result_confidence_pct
  desc: Confianza media mínima de un resultado del pipeline 1
  producer: config
  config_key: inference.pipelines.pipeline_1.min_result_confidence_pct
  unit: '%'
  min_value: 0
  max_value: 100
```

**Qué claves acepta el esquema**, y nada más —lo que hace cumplir §1—:

- `process.*`, que es la sección de lo que se mide y la única cuya forma es del fork.
- Los umbrales del resultado de un pipeline: `inference.pipelines.<p>.min_result_confidence_pct`
  y `min_detections`.

Una fila escribible con cualquier otra clave hace fallar `load_registers()` con el archivo y
la fila, y el servidor no arranca, como con cualquier mapa roto. Quedan afuera a propósito
los `enabled`, todo `cameras.*`, los protocolos, el overlay y el dataset; y también el
`min_confidence_pct` de un modelo, porque con pesos protegidos lo pisa su metadata y el PLC
escribiría un número que nadie usa.

Camino de una escritura:

1. El contexto valida dirección y rango, escribe bajo lock y encola `{141: 620}`.
2. El drenado llama `SCHEMA.apply_plc_writes()`, que hace `cfg.set()` sólo si el valor cambió.
3. Si alguna clave fue `process.*`, se reconstruyen los hooks (P6). Las del pipeline las toma
   el motor solo, en el frame siguiente, por el cambio de `revision`.
4. Se agenda el guardado agrupado (P8).
5. `ui.refresh_config_keys(keys)` → `ConfigView` → la pestaña dueña de cada clave recarga ese
   campo (P7). En headless es un no-op, como el resto de la UI.
6. El tick siguiente re-espeja el valor, que es lo que el PLC relee como confirmación.

Para P7, `AbstractConfigTab` gana `refresh_keys(key_paths)`. Su default recarga la pestaña
entera si alguna clave es de su sección; las pestañas que el PLC toca de verdad —la del
proceso y la de inferencia— lo implementan campo por campo.

### 5.3 Una variable para un pipeline

Filas `producer: plc` en 40111-40140, con el nombre que elija el fork:

```yaml
- addr: 111
  name: event_active
  desc: El PLC avisa que hay un evento en curso (1) o no (0)
  producer: plc
  is_bool: true
```

Llegan a donde ya se decide si un frame entra a un pipeline: las **compuertas de frames**.
`FrameSignals` gana el campo que su docstring ya anunciaba:

```python
@dataclasses.dataclass(frozen=True)
class FrameSignals:
    gpio_inputs: Mapping[int, int] = ...
    plc: Mapping[str, object] = ...      # nombre de la fila -> valor decodificado
```

El drenado arma un `FrameSignals` nuevo y lo reemplaza entero, igual que hace hoy
`_on_gpio_inputs_updated()`, así que los hilos de captura lo siguen leyendo sin lock. El fork
escribe su compuerta en `_build_frame_gates()`:

```python
def _build_frame_gates(self):
    def during_event(camera_slot, signals):
        return signals.plc.get("event_active") == 1
    return {"pipeline_2": during_event}
```

Con eso el pipeline queda cargado y sin inferir mientras la variable está en 0, y todo lo que
ya hace una compuerta vale igual: no gasta GPU, los registros quedan con su último valor, el
overlay se va en el frame siguiente y el bit 6 de la palabra del pipeline lo dice.

Si un fork necesita el valor **adentro** del cálculo —un código de producto que cambia la
escala— y no sólo para cortar frames, el camino es que el resultado lleve la foto de las
señales del momento del push. Queda como pregunta abierta (§8) porque cambia la firma de
`push_frame()` y ningún caso lo pide todavía.

## 6. Qué se ve en la pantalla

- **Tabla de Modbus** (diagnóstico): hoy muestra lo que escribe la app. El tick le suma las
  filas del PLC, leídas del servidor con `get_register()`, así que disparos y variables se ven
  junto al resto.
- **Pestañas de configuración**: los umbrales, con el valor nuevo apenas se aplica (P7).
- **Log**: una línea por escritura aplicada (`max_load_y_px_camera_1: 600 → 620 (PLC)`) y una
  por disparo descartado.

## 7. Lo que no se hace

Prender o apagar cualquier cosa desde el PLC, ajustar la cámara (decisión vigente), tocar el
overlay o el dataset, umbrales de modelo, coils, FC23 y valores de 32 bits.

## 8. Preguntas abiertas

- ¿Si el operador está editando justo el campo que el PLC cambia, gana el PLC (lo propuesto) o
  se le pregunta?
- ¿Las variables también tienen que llegar al analyzer o al pipeline (§5.3), o alcanza con las
  compuertas?
- ¿Hace falta una lista de IPs que pueden escribir por TCP? Requiere un spike: pymodbus 3.15 no
  le pasa la conexión al contexto.
- ¿El disparo también puede venir de una entrada de GPIO? Es el mismo `request_round()`; sólo
  cambia quién lo llama.

## 9. Pasos

Cada paso termina con `ci/check.py` en verde y se valida antes de seguir.

1. **Esquema.** `producer: plc`, `min_value`/`max_value`, las claves permitidas de §5.2,
   `writable_addrs()`, `is_valid_raw()`, `decode()` público, `encode()` que levanta con una fila
   `plc`. Se borra `detect_plc_writes()`. Tests en `test/modbus/test_schema.py`.
2. **Servidor.** `_ReadOnlyHoldingRegisters` → `_HoldingRegisters(writable, is_valid_raw,
   on_write)`: validación de la petición entera, 0x02 / 0x03, escritura y `on_write` bajo el
   lock. `register_count` derivado del mapa. El servidor sigue sin conocer el mapa: recibe el
   conjunto y las dos funciones del cableado. P1 se confirma acá.
3. **Umbrales.** Espejo antes de arrancar el servidor, drenado, `apply_plc_writes()` desde la
   cola, `InferenceThread.set_hooks()`, guardado agrupado, `refresh_keys()` en las pestañas.
4. **Variables.** `FrameSignals.plc` y las filas del PLC en la tabla de Modbus.
5. **Disparo.** `cycle_trigger`, `request_round()` con descarte, `tag` en `push_frame()` y en el
   resultado, `trigger_settle_ms`, los bits 7-11, el bloque atómico de P11,
   `app_session_id`.
6. **Mapa y documentos.** Filas de 40092-40097 y ejemplos comentados de 40101-40200 en
   `register_map.yaml`; `export_map.py` con columnas de acceso y rango; `docs/modbus_map.*`
   regenerado; `docs/plc_commands.md` para el integrador (cómo escribe cada sub-bloque, el
   handshake, qué pasa en un reinicio); `manual_test/modbus_write/` con un cliente pymodbus que
   cambia un umbral, prende una variable y dispara contra la app corriendo; `CLAUDE.md` (la
   decisión de sólo lectura cambia; contratos). Sube la versión.

## 10. Relación con otros planes

- **Captura por trigger de software** (`CLAUDE.md` la da por diseñada en
  `.claude/plans/software-trigger-capture.md`, pero ese archivo no está en el repo): ése
  dispara la *cámara*; éste dispara la *medición* sobre una cámara en free-run. Se componen:
  con trigger de cámara, `trigger_settle_ms` deja de hacer falta.
- **Compuertas de frames** (en `main`): una variable del PLC es una señal más para ellas, y por
  eso el caso 3 casi no necesita código nuevo.
