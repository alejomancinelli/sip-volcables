# Plan: el PLC escribe — disparo de medición y variables

**Estado: implementado, en v0.10.0.** Falta probarlo contra un PLC de verdad. Branch
`machinery/plc-commands`, sobre `main` (v0.9.0, con las compuertas de frames y la palabra de
estado por pipeline).

**El PLC no escribe el config.** La primera versión de este plan lo dejaba cambiar umbrales del
proceso, y se descartó el 2026-10-22 (ver «Lo que se descartó», al final). La configuración es
sólo de la interfaz y del archivo, y un umbral viaja en un solo sentido: del equipo al PLC,
para que el HMI lo muestre (§5.3).

El porqué de cada decisión está acá y no por referencia, para que se pueda retomar sin la
conversación que lo originó.

---

## 1. Qué se quiere

Antes el PLC sólo leía (40001-40100). Ahora también escribe, en **50 registros**, y **sólo para
dos cosas**:

1. **Disparar una medición.** El disparo también puede venir de una entrada de GPIO. Qué se
   hace con esa medición —guardarla para calibrar, compararla— es del fork; la maquinaria le
   da cómo enterarse del disparo y cómo pedir la medición (§5.1).
2. **Mandar una variable**, que se usa de dos formas:
   - **para decidir si se infiere**: «el proceso está OK, empezá a inferir». El pipeline ya
     está cargado, pero no infiere hasta que el PLC lo dice;
   - **para calcular con ella**: la velocidad de la cinta, para sincronizar dos cámaras o
     pasar de carga a toneladas por hora; un código de producto que elige qué límites del
     config aplica el analyzer.

Y en el otro sentido, que ya estaba en el esquema y no estaba cableado: **el equipo publica
umbrales del config** para que el HMI los muestre (§5.3).

**El PLC no prende ni apaga nada y no escribe el config**: ni pipelines, ni cámaras, ni
protocolos, ni el overlay, ni el dataset, ni un umbral. No es una convención: el esquema sólo
deja escribir las filas `producer: plc`, que no apuntan a ninguna clave del config.

Todo esto es **maquinaria**, y **el mapa es el interruptor**: un fork sin filas `plc` se
comporta como antes, sin ninguna clave de config nueva.

## 2. Qué direcciones escribe el PLC

**40101-40150** (registros 101-150), a continuación del bloque de lectura y con la misma regla
**reg N ⇔ 4000N**. No hay filas definidas: cada fork declara las suyas.

| Bloque | Quién escribe | Qué |
|---|---|---|
| 40001-40100 | el equipo | lo de antes, más los umbrales publicados (§5.3) |
| 40101-40150 | el PLC | variables, que el fork lee como nivel o como evento (§4) |

Por qué ahí y no en un rango aparte como 41001:

- **Un solo datastore y una sola regla**, sin offset nuevo que traducir.
- **Cada bloque entra en una sola petición.** FC03 lee hasta 125 registros y FC16 escribe
  hasta 123.
- **`register_count` no hay que acordarse de subirlo.** El servidor expone
  `max(modbus.register_count, SCHEMA.max_addr())`: llega hasta la última fila declarada y nada
  más, así que un fork que usa tres registros de escritura no expone cincuenta.

El PLC escribe un disparo con un FC06 cuando quiere medir, y las variables de forma cíclica,
con un FC16 sobre todas: así un reinicio del equipo se repara solo en el ciclo siguiente (P4).

**El equipo no contesta lo que escribe el PLC** (decidido el 2026-10-22). El PLC lee los
valores de forma continua, y el disparo existe para **guardar** esa medición (§5.1), no para
que el PLC espere su resultado. Se descartaron `app_session_id` —el latido ya delata un
reinicio—, un `done_seq` por pipeline y bits nuevos en la palabra del pipeline. Un fork que
necesite confirmar el disparo agrega su `done_seq` como una fila más, en su rango 3-50.

## 3. Problemas que había que resolver

Los números son los de la primera versión; P6, P7 y P8 eran de los umbrales y están en «Lo que
se descartó».

**P1. El servidor era de sólo lectura por contrato.** → Sirve FC06 y FC16 **sólo sobre las
filas `plc`**. Con un mapa sin ellas, sigue siendo de sólo lectura.

**P2. pymodbus escribía a medias.** Su `async_OLD_setValues` (3.15) valida registro por
registro *mientras* escribe: un FC16 que se mete en una fila ajena dejaba escrita la primera
mitad. → El contexto valida la petición **entera** antes de tocar nada: 0x02 si alguna
dirección no es escribible, 0x03 si algún valor cae fuera de su rango.

**P3. Sondear pierde escrituras.** Comparar los registros una vez por segundo hace de dos
disparos dentro del mismo segundo uno solo. → **Las escrituras entran por evento**: el
contexto, bajo el lock con que escribe, llama a `on_write({addr: raw})`; el cableado lo deja en
una `queue.SimpleQueue` y un QTimer de 100 ms en el hilo de la GUI la drena.

**P4. El datastore vive en RAM y arranca en 0.** → Por clase:
  - **Disparos**: el 0 es «nunca disparó», así que el próximo número del PLC es nuevo. Uno que
    cae durante el reinicio se pierde.
  - **Variables**: vuelven a 0, así que **el 0 tiene que ser el reposo** —«no hay evento», «el
    PLC no manda nada»— y el PLC las escribe de forma cíclica.
  - **Umbrales publicados**: el equipo los escribe antes de levantar el servidor, así que el
    PLC nunca lee un 0 que no es el valor.

**P5. Dos escritores sobre el mismo registro.** → **Un solo dueño por fila**: `encode()`
levanta con un nombre `plc`, así que la app nunca escribe una, y el PLC no puede escribir una
`config`.

**P9. Un disparo con la ronda en curso.** Descartar la ronda obligaría a marcar cada frame con
su ronda: el resultado que ya estaba en el motor llegaría después y se tomaría como el primero
de la nueva. → **La maquinaria no descarta**: `request_round()` contesta False y no toca nada.
Qué hacer con ese disparo es del fork (§5.1).

**P10. Un frame «después del disparo» puede ser de antes.** En free-run el primer frame que
llega pudo haberse expuesto antes del disparo, y `frame_ready` no trae el instante de captura.
→ Queda para el fork que lo necesite —un margen antes de pedir la ronda—; la solución de fondo
es disparar la cámara (§9).

**P12. Las compuertas no son para pipelines con ciclo**: con una cámara cortada, la ronda
espera hasta su timeout. → Una variable corta los frames de un pipeline **sin ciclo**; en uno
con ciclo, el equivalente es el disparo. El aviso de arranque que ya existía cubre el caso.

**P13. La licencia no se saltea por Modbus.** → Un pipeline sin licencia no tiene hilo ni
ronda, así que pedirle una medición no hace nada, y lo que el fork haga con una medición
disparada pasa por los mismos caminos y las mismas reglas que cualquier otra.

**P14. Modbus no tiene autenticación.** → Sólo las filas declaradas son escribibles, cada una
con su rango. Cada cambio aplicado se loguea a INFO y cada rechazo a WARNING, los dos con tope
(§6). **Sin lista de IPs, por ahora.**

**P15. El eco del FC06 relee con el código de la escritura.** `WriteSingleRegisterRequest`
arma la respuesta con `async_getValues(fc=6, …)`, no con FC03: con un `validate()` que
aceptara sólo FC03, el registro quedaba escrito y el PLC recibía 0x02, y reintentaba. (El FC16
no relee.) → `validate()` acepta FC06 y FC16 sobre las filas escribibles, con un test que
compara el eco con el valor escrito.

## 4. Lo que escribe el PLC: variables, leídas como nivel o como evento

**Para la maquinaria todo es una variable** (decidido el 2026-10-22). Toda fila `plc` llega
igual a `FrameSignals.plc`, sin sufijo que la trate distinto; la diferencia está en cómo la lee
el fork:

| | Nivel (ON/OFF, un valor) | Evento (disparo) |
|---|---|---|
| Ejemplo | `process_ok`, `belt_speed_mps` | un contador que el PLC incrementa |
| Dónde la lee el fork | una compuerta, por frame; el analyzer, en `result.signals` | `_on_signals_changed()`, cuando cambia (§5.1) |
| Pregunta que contesta | ¿infiero ahora?, ¿con qué valor calculo? | ¿pasó algo desde la última vez? |
| Al reiniciar el equipo | 0 (reposo) | 0 («nunca disparó») |

**Un disparo es un contador y no un bit.** Con un bit el PLC escribe 1 y después 0, y si el
equipo no vio el flanco, el disparo se pierde. Con un contador hay una sola escritura, y
reescribir el mismo número no vuelve a disparar. El número viaja en `signals` y queda en el
JSON de lo que se guardó, que es lo que empareja esa imagen con la referencia del PLC. El PLC
cuenta de 1 a 65535 y vuelve a 1; es una recomendación para el integrador, no algo que se
valide.

## 5. Los casos de uso, en código

### 5.1 Disparar una medición

**El disparo es del fork; la maquinaria le da tres piezas**, las que un fork no puede hacer
sin tocarla:

1. **Enterarse de que algo cambió.** `_on_signals_changed(previous, current)`, en el bloque del
   fork de `main.py` y vacío en el template. Lo llama el cableado en el hilo de la GUI cada vez
   que las señales cambian —una escritura del PLC o una lectura de GPIO distinta—, con la foto
   de antes y la de ahora. Uno que levanta se avisa en el log y no frena el drenado.
2. **Detectar el flanco**, con funciones de módulo sobre esas dos fotos: `gpio_rose` /
   `gpio_fell` (un `READ_ERROR` en el medio no es un flanco), `plc_rose` / `plc_fell` y
   `plc_changed` (cualquier cambio: un contador).
3. **Pedir una medición ahora.** `CaptureScheduler.request_round(pipeline_slot) -> bool` abre
   una ronda ya. Con `cycle_trigger: request` el pipeline mide **sólo** cuando se lo piden; con
   `interval` (default) sigue midiendo por reloj y el pedido adelanta la ronda. Contesta False,
   sin tocar nada, si hay una ronda en curso o si el pipeline no tiene ciclo (P9). «Tener
   ciclo» pasa a ser `frames_per_cycle > 1 or cycle_trigger == "request"`: el permiso del
   scheduler es lo que asegura que el frame sea posterior al pedido.

```yaml
inference:
  pipelines:
    pipeline_2:
      frames_per_cycle: 3
      cycle_trigger: request      # interval (default): por reloj. request: sólo a pedido
```

**`cycle_trigger` no va a la pestaña de inferencia** (2026-10-22). Sin el
`_on_signals_changed()` del fork un pipeline en `request` no mide nunca, así que ofrecerlo en
la pantalla dejaría al operador elegir algo que no anda solo. Se edita en el archivo. De paso
evita un cambio en caliente que el scheduler no maneja: de `request` a `interval` el pipeline
quedaba esperando un pedido hasta reiniciar.

**Ejemplo para un fork**, que no es maquinaria:

```python
def _on_signals_changed(self, previous, current):
    # Medir cuando el PLC incrementa su contador, o con un flanco de subida en DI1.
    if (plc_changed(previous, current, "measure_seq")
            or gpio_rose(previous, current, channel=1)):
        if not self._scheduler.request_round("pipeline_2"):
            logger.info("[Fork] Ronda en curso: el disparo se ignora.")
```

Lo que queda del lado del fork:

- **Guardar la medición.** Con `image_collector.mode: on_demand`, cada ronda de un pipeline con
  ciclo ya se guarda, así que una ronda pedida queda en el dataset sin código, con `signals`
  —el contador incluido— en su JSON. Guardar con el dataset apagado o sin deduplicación es del
  fork.
- **Un disparo con la ronda en curso**: ignorarlo, como arriba, o pedir la ronda en el
  `cycle_complete` siguiente.
- **Un frame posterior al disparo** (P10): esperar un período de frame antes de pedir la ronda.
- **Disparos seguidos.** Dos dentro de un mismo drenado (100 ms) llegan como un solo cambio: un
  contador que salta de N a N+2 fueron dos. Y cada frame lleva el número del momento en que
  entró, así que con disparos más seguidos que una ronda, sus frames pueden llevar el del
  siguiente: un fork que empareja contra referencias guarda el número del pedido en su
  contexto del dataset.

```
PLC / GPIO                          Equipo
 │ escribe measure_seq = N ───────► │ drenado → _on_signals_changed(antes, ahora)
 │                                  │   fork: plc_changed(...) → request_round("pipeline_2")
 │                                  │ junta los frames de la ronda
 │                                  │ cycle_complete → registros, telemetría, dataset
 │ sigue leyendo los valores        │
```

### 5.2 Una variable

Filas `producer: plc`, con el nombre, la escala y el rango que elija el fork:

```yaml
- addr: 105
  name: process_ok
  desc: El PLC avisa que el proceso está OK y se puede inferir (1) o no (0)
  producer: plc
  is_bool: true

- addr: 106
  name: belt_speed_mps
  desc: Velocidad de la cinta que mide el PLC
  producer: plc
  unit: m/s
  scale: 100
  min_value: 0
  max_value: 10
```

Una variable llega a dos lugares, que contestan preguntas distintas.

**¿Entra este frame al pipeline? — las compuertas.** El drenado arma un `FrameSignals` nuevo,
con `plc` (nombre → valor decodificado), y lo reemplaza entero, así que los hilos de captura lo
leen sin lock. El fork escribe su compuerta en `_build_frame_gates()`:

```python
def _build_frame_gates(self):
    def while_process_ok(camera_slot, signals):
        return signals.plc.get("process_ok") is True
    return {"pipeline_2": while_process_ok}
```

El pipeline queda cargado y sin inferir mientras la variable está en 0, con todo lo que ya hace
una compuerta: no gasta GPU, los registros quedan con su último valor, el overlay se va en el
frame siguiente y el bit 6 de la palabra del pipeline lo dice.

**¿Con qué valores se mide este frame? — la foto por frame.** Una compuerta no le pasa el valor
a nadie. Para calcular con una variable, **cada frame lleva la foto de las señales del momento
en que entró al motor**: `push_frame(frame, camera_slot, signals=...)`, que el motor copia a
`InferenceResult.signals`. El analyzer, el annotator y el dataset la leen del resultado:

```python
def analyze(result: InferenceResult) -> dict:
    speed_mps = result.signals.get("belt_speed_mps", 0.0)
    return {"load_tph": load_area_m2(result) * density_t_m3 * speed_mps * 3600}
```

Que sea la foto del push importa: si la velocidad cambia mientras un frame se infiere, el
resultado sale con la velocidad de **ese** frame, y el dataset guarda la que se usó. Con la
foto y el instante de cada resultado, el fork puede además sincronizar dos cámaras sin que la
maquinaria sepa qué es una cinta. El pipeline no la recibe —cambiaría el contrato de `_run()`—
y no hace falta: lo que depende de una variable se decide en el analyzer o el classifier.

**Un umbral no es una variable.** Lo que depende del producto —las recetas— va en el config,
donde lo edita el operador, y el PLC manda **cuál** se usa (`product_code`), no **qué** dice.

Un registro no tiene negativos —una temperatura bajo cero necesita un offset acordado— ni
valores de más de 16 bits.

### 5.3 Publicar un umbral para el HMI

Filas `producer: config`: copian una clave del config al registro, con su escala, para que el
HMI la muestre. Son de sólo lectura —una escritura da 0x02— y no llevan rango. **Van en el
bloque de lectura**, lo natural es el rango 3-50 del fork.

```yaml
- addr: 10
  name: max_load_y_px_camera_1
  desc: Altura del límite de carga en la cámara 1
  producer: config
  config_key: process.max_load_y_px.camera_1
  unit: px
```

`main.py` publica `SCHEMA.mirror_batch(config)` antes de levantar el servidor (P4) y en cada
tick, así que un cambio desde la interfaz llega al PLC en el segundo siguiente.

## 6. Qué se ve en la pantalla

- **Tabla de Modbus** (diagnóstico): suma las filas del PLC, leídas del servidor, junto al
  resto.
- **Log**: una línea por cambio aplicado (`belt_speed_mps: 1.2 → 1.5`), a lo sumo una cada
  10 s por variable y con cuántos cambios se callaron; y un warning por escritura rechazada, a
  lo sumo uno por minuto por dirección y motivo.

## 7. Lo que no se hace

Prender o apagar cualquier cosa desde el PLC, escribir el config o un umbral, ajustar la
cámara, tocar el overlay o el dataset, umbrales de modelo, coils, FC23 y valores de 32 bits.

## 8. Cómo se hizo

Cinco pasos, cada uno validado antes del siguiente:

1. **Esquema.** `producer: plc` con `min_value` / `max_value`, `decode()` público y `encode()`
   que levanta con una fila `plc`. Sólo las filas `plc` son escribibles; las `config` quedaron
   de sólo lectura, sin claves permitidas ni campo `apply`.
2. **Servidor.** Validación de la petición entera, 0x02 / 0x03, escritura y `on_write` bajo el
   lock, el eco del FC06 (P15), el warning con tope y `register_count` derivado del mapa.
3. **Variables.** El drenado, `FrameSignals.plc`, el log, la foto por frame, las variables en el
   JSON del dataset, las filas del PLC en la tabla de Modbus y los umbrales publicados. La
   prueba manual `manual_test/modbus_write/app_with_plc.py` levanta la app con cámaras mock y
   una compuerta, y `plc_client.py --app` corre los casos contra ella.
4. **Disparo.** `_on_signals_changed()`, los flancos, `cycle_trigger` y `request_round()`. La
   prueba manual agrega un pipeline que mide a pedido y comprueba que un número nuevo deje un
   JSON en el dataset con ese número en `signals`.
5. **Mapa y documentos.** Ejemplos comentados en `register_map.yaml`, `export_map.py` con
   columnas de acceso y rango, `docs/plc_commands.md` para el integrador, `CLAUDE.md` y
   `README.md`, y la versión a 0.10.0.

En la revisión posterior lo escribible pasó a decirse en un solo lugar: el esquema expone
`plc_regs()`, `write_range()` —la regla del rango, en unidad física— y `write_ranges()` —el
crudo por dirección—, y el servidor recibe sólo eso y `on_write`. Además salieron de
`main.py` las dos piezas que no son cableado: `FrameSignals`, `FrameGate` y los flancos a
`system/frame_signals.py`, y la cola, el drenado y el log de las variables a `PlcVariables`
(`system/modbus/plc_variables.py`), que la prueba manual del servidor usa igual que la app.

## 9. Relación con otros planes

- **Captura por trigger de software** (diseñada y diferida, ver `CLAUDE.md`): ésa dispara la
  *cámara*; éste dispara la *medición* sobre una cámara en free-run. Se componen: con trigger
  de cámara, el margen de P10 deja de hacer falta.
- **Compuertas de frames** (en `main`): una variable del PLC es una señal más para ellas.

## Lo que se descartó

**Umbrales del proceso escritos por el PLC** (filas `config` escribibles sobre `process.*` y
los umbrales del resultado de un pipeline). Se diseñó y se descartó el 2026-10-22, antes de
escribir su código, por lo que obligaba a resolver:

- **P6. `process:` se lee una sola vez**, al armar el motor, y sus números quedan adentro del
  classifier, el analyzer y el annotator. Aplicarlo en caliente pedía reconstruir los tres
  juntos, conservar el estado de un analyzer —las medias móviles arrancarían de cero—, decidir
  qué hacer si la fábrica rechaza el valor, y una medición de N frames mitad con un valor y
  mitad con otro.
- **P7. La pantalla mostraría un valor viejo, y lo guardaría** si el operador no vio el cambio.
- **P8. Un guardado del PLC borraría lo que el operador editó a mano**, porque `save()`
  reescribe el archivo entero.

Sin un segundo escritor del config, «el operador tiene preferencia» deja de ser una regla que
hacer cumplir. Lo que se pierde es que el PLC cambie un umbral: lo cambia el operador y el PLC
lo lee publicado (§5.3). Lo que cambia con el producto se resuelve con recetas en el config y el
código de producto como variable (§5.2).
