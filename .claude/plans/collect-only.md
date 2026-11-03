# Juntar el dataset sin inferir: `collect_only` por pipeline

Estado: **propuesta**, sin código. Sale de la pregunta «¿puedo no correr la inferencia y
sólo juntar imágenes a resolución completa para entrenar, y después arrancar el modelo?».

## 1. Qué hay hoy (verificado en el código)

- **El dataset lo alimenta sólo la inferencia.** `main._on_cycle_complete()` →
  `_send_to_dataset(result)` → `collector.push_frame()` (modo `interval`) o `save_now()`
  (modo `on_demand`, sólo con ciclo). Si el pipeline no tiene hilo —deshabilitado, sin
  licencia— no hay resultado y no se guarda nada.
- **Un pipeline sin modelo igual corre**, pero sale `no_model` en cada frame y la vista se
  pide al tamaño de un modelo que tiene que existir: `build_camera_views()` lee la entrada
  con `pipeline.peek_view_input()`, que abre el `.engine`. Sin engine, la vista sale nativa
  —por accidente, no por diseño— y el log dice «el modelo no informa su entrada».
- **Qué imagen se guarda** (`_send_to_dataset`): con ROI propio, `result.region_bgr` en el
  subset `<cámara>-<pipeline>/`; sin ROI, `source_bgr`. En una cámara de video (`rtsp`,
  `video_file`) la región es la vista del decodificador **a la entrada del modelo** (224×227
  para el grano); en una GigE es el recorte del frame del sensor, ya nativo.
- **No hay forma de pedir la vista nativa para el dataset**: `output_short_side_px` se sacó
  a propósito (la vista sale siempre a la entrada del modelo).

## 2. Propuesta

Una clave por pipeline, **`inference.pipelines.<slot>.collect_only: true`**. Con ella el
pipeline hace todo lo que hace siempre **menos cargar y correr los modelos**:

| Qué | Con `collect_only` |
|---|---|
| Hilo, cola, `interval_s`, cámaras, compuerta de frames | Igual. Un pipeline cortado por su compuerta no junta: las imágenes salen sólo mientras el proceso corre. |
| Modelos | No se cargan. No hace falta que exista el `.engine`; la GPU queda libre. |
| Región | Igual: el ROI del pipeline, recortado o como vista. |
| **Tamaño de la vista** | **Nativo**: sin modelo no hay entrada a la que ajustarla. `build_camera_views()` no consulta `peek_view_input()` para ese pipeline. |
| Iluminación | Se mide igual y `dark_frame` gana sobre `collecting` (ver §4.3). |
| Resultado | Uno por frame, `is_valid: False`, `invalid_reason: "collecting"` (`REASON_COLLECTING` nuevo en `result.py`), sin detecciones, sin `labels`, sin `roi_state`. |
| Analyzer, métricas, PLC | No corre el analyzer (el resultado no es válido), así que sus registros quedan con el último valor, como con cualquier inválido. |
| Palabra del pipeline | Bit nuevo **`BIT_COLLECTING = 7`** en `formats/pipeline_status.py`: «sólo junta imágenes, no mide». También quedan prendidos «sin modelo» y «resultado no confiable», porque es lo que pasa; el bit 7 dice por qué. |
| Monitor y anotado | El ROI sale gris y punteado con «(collecting)», que es lo que ya hace el overlay con un inválido. No hay código nuevo de dibujo. |
| Dataset | El camino de siempre: `_send_to_dataset()`, subset `<cámara>-<pipeline>/`, filtros, dedup, tope. El JSON lleva `inference.invalid_reason: "collecting"` y `detections: []`, que es la marca de «imagen sin etiquetar» para quien entrena. |
| Telemetría | Entra en `invalid_count` / `invalid_reason` como todo inválido. Nada nuevo en las series. |

El flujo de puesta en marcha queda así:

1. Dibujar las regiones (diálogo de regiones).
2. `collect_only: true`, `image_collector.enabled: true` con ese pipeline en
   `image_collector.pipelines`, modo `interval`. Reiniciar.
3. Juntar días u horas. Las imágenes caen en `<cámara>-<pipeline>/` a resolución nativa.
4. Entrenar, exportar el `.engine`, ponerlo en el `path` del modelo.
5. `collect_only: false`. Reiniciar: la vista pasa a salir a la entrada del modelo.

Cambiar la clave **exige reiniciar**, como todo lo que toca las vistas: el corte se fija al
abrir la cámara y los modelos se cargan al arrancar el hilo.

### Por qué así y no de otra forma

- **No un camino aparte de la cámara al colector.** El colector ya admite un productor de
  frames crudos, pero así no habría región ni vista nativa —las vistas sólo se cortan para
  pipelines que corren—, no respetaría la compuerta, y el subset por pipeline se tendría que
  rearmar a mano. Pasando por el motor sale todo eso gratis y la imagen guardada es
  exactamente la que después va a recibir el modelo, con el mismo recorte.
- **No juntar a resolución nativa mientras el modelo infiere.** Sería la otra opción para
  reentrenar en producción, pero cuesta una segunda vista por pipeline (VIC + copia de la
  vista grande, +21 puntos de un núcleo medidos contra +7 a 224 px). Queda para cuando haga
  falta; `collect_only` cubre la puesta en marcha, que es el caso de hoy.
- **Una clave y no un `mode: infer|collect`.** Hay dos estados y un booleano afirmativo los
  dice; si aparece un tercero se migra.

## 3. Cambios, archivo por archivo

### Fase 1 — maquinaria, usable desde el config

- `system/inference/result.py`: `REASON_COLLECTING = "collecting"` en el vocabulario.
- `system/inference/engine.py`:
  - lee `collect_only` una sola vez al arrancar (`run()`), como se lee la carga del
    pipeline: con la clave prendida no llama a `pipeline.load()` y loguea en INFO que el
    pipeline sólo junta imágenes;
  - en `_process()`, el orden de los motivos pasa a ser `no_view` → `dark_frame` →
    `collecting` → `no_model`: con `collect_only` el pipeline no se corre y no se avisa
    `no_model` cada 10 s;
  - `get_status()` agrega `collecting: bool` (clave estable nueva, documentada);
  - `process()` (el camino de la corrida offline) respeta la misma clave: ver §4.4.
- `system/inference/preprocessing.py`: `build_camera_views()` no consulta
  `peek_view_input()` para un pipeline con `collect_only` —vista nativa— y lo dice en su
  línea de log.
- `system/formats/pipeline_status.py`: `BIT_COLLECTING = 7`, `WORD_MASK = 0x00FF`,
  descripción, y `pack(..., collecting: bool)`.
- `main.py`:
  - `_build_pipeline_words()`: pasa `collecting` desde `engine.get_status()`;
  - al arrancar, un aviso si un pipeline con `collect_only` no va a guardar nada —colector
    apagado, o el pipeline fuera de `image_collector.pipelines`, o modo `on_demand`—: es un
    pipeline que no mide ni junta, y no se ve de otra forma;
  - con ciclo (`frames_per_cycle > 1`) ver §4.2.
- `system/inference/offline.py`: ver §4.4.
- `config.yaml` del fork y de `manual_test/ui/`: la clave en `false` en cada pipeline, para
  que se vea que existe.

### Fase 2 — interfaz

- `ui/views/config/inference_tab.py`, página del pipeline: casilla «Sólo juntar imágenes
  (no infiere)» con una nota: se aplica al reiniciar, guarda la región a tamaño nativo y
  necesita la recolección prendida con este pipeline (pestaña Dataset).
- Sección «Regiones (ROI)»: la línea de un pipeline que junta dice «nativo · juntando
  imágenes» en vez de «salida según el modelo».
- `ui/dialogs/pipeline_regions_dialog.py`: la nota de la vista previa dice que el dataset
  se guarda a tamaño nativo cuando el pipeline junta.
- `ui/strings.py`: los textos (es/en/pt).
- Diagnóstico de inferencia, si muestra el estado del motor: «juntando imágenes» en vez de
  «sin modelo».

### Fase 3 — documentación

- `CLAUDE.md`: una decisión vigente («juntar sin inferir es un modo del pipeline, no un
  camino aparte») y el mapa si cambia algo.
- `docs/jetson.md` §4.14: el párrafo del dataset que hoy dice que no se puede juntar a
  resolución nativa desde una cámara de video pasa a apuntar a `collect_only`.
- `docs/image_sizes.md`: el tamaño de la vista con `collect_only`.
- `docs/ui.md`: la casilla y la línea de la región.
- `docs/offline_testing.md`: `collecting` en «Cómo leer el resultado», si la corrida offline
  lo respeta.
- `docs/influxdb.md` no cambia: el motivo ya viaja en `invalid_reason`.
- El mapa Modbus no cambia: la palabra del pipeline todavía no tiene dirección.

## 4. Decisiones abiertas

### 4.1 ¿Junta sin licencia?

Hoy, sin licencia utilizable (`absent`/`invalid`) o sin el `feature` del pipeline, **no se
le levanta hilo**, así que tampoco juntaría.

- **Recomendado: igual que inferir.** Una sola regla —el pipeline corre si la licencia lo
  habilita—, y la licencia se puede emitir antes de juntar porque sólo pide la huella del
  equipo. Desde fuentes no hay enforcement, así que en desarrollo no molesta.
- Alternativa: `collect_only` arranca aunque el `feature` no esté. Sirve para un piloto
  —juntar imágenes en la planta de un cliente antes de vender—, pero abre una excepción en
  `_build_engines()` y en `has_feature()` que hay que defender después.

### 4.2 Pipelines con ciclo

Con `frames_per_cycle > 1` el scheduler descarta los inválidos y, si ninguno vale, publica
**ceros** al PLC con un aviso por ciclo (`_build_empty_result`). Un pipeline que junta daría
ceros y un warning cada ciclo, que es justo lo que no tiene que pasar.

- **Recomendado para la primera versión:** con `collect_only` el pipeline se trata como sin
  ciclo —`is_cycled()` devuelve False— y se avisa al arrancar. Se pierde el manejo de luz
  del ciclo mientras se junta; en este fork todos los pipelines tienen
  `frames_per_cycle: 1`, así que hoy no afecta.
- Lo completo —el ciclo sigue, el colector guarda el representativo con `save_now()`— queda
  para cuando un fork con ciclo lo necesite.

### 4.3 Frames oscuros

Con la iluminación por debajo de `illumination_min` el resultado sale `dark_frame` y **se
guarda igual**, como hoy con un pipeline que infiere: `_send_to_dataset()` no filtra por
motivo. Recomendado no cambiarlo acá; el que quiera excluirlos tiene `save_conditions`.
Alternativa: no guardar los `dark_frame` de un pipeline que junta, con el argumento de que
el modelo nunca los va a ver.

### 4.4 La corrida offline

`OfflineRunner` construye los motores con el mismo config. Con `collect_only` prendido
devolvería `collecting` en cada imagen, y la corrida offline existe justamente para probar
el modelo. **Recomendado:** la corrida offline lo apaga en memoria —como ya hace con la
migración de ROI, `save=False`— y lo dice en una línea de la consola.

## 5. Tests

- `test/inference/test_engine.py`: con `collect_only` no se carga el pipeline, el
  resultado sale `collecting` con la región y sin detecciones; `no_view` y `dark_frame`
  ganan; `get_status()["collecting"]`; con la clave apagada, nada cambia.
- `test/inference/test_preprocessing.py`: la vista de un pipeline que junta no lleva
  `output_box_px`, y no se le pregunta al modelo.
- `test/formats/test_pipeline_status.py`: el bit 7, la máscara y `describe()`.
- `test/inference/test_result.py`: el motivo nuevo está en el vocabulario.
- `test/inference/test_offline.py`: la corrida offline infiere aunque la clave esté
  prendida.
- `test/camera/test_capture_scheduler.py`, si se toma §4.2: un pipeline que junta no es
  ciclado.
- `test/ui/test_inference_tab.py`: la casilla carga y guarda, y la línea de la región.
- Regenerar `docs/tests.md`.

## 6. Prueba con la cámara

Con `camera_4` (RTSP, `decode` 640×360) y `pipeline_1` (grano, ROI propio):

1. `collect_only: true` en `pipeline_1`; colector prendido, `interval`, con
   `interval_min_s`/`interval_max_s` en 5–10 s para no esperar; reiniciar.
2. En el log: `pipeline_1` no carga modelos, la vista de `camera_4` sale «a tamaño nativo».
3. En el monitor: el ROI de `pipeline_1` gris y punteado con «(collecting)»; los demás
   pipelines de la cámara siguen dibujando lo suyo.
4. En `data/.../camera_4-pipeline_1/`: imágenes del tamaño del recorte en el video nativo
   —para un stream de 1920×1080 y el ROI de 231×221 sobre 640×360, unos 693×663— y el JSON
   con `"invalid_reason": "collecting"`.
5. En la GPU (tegrastats o `nvidia-smi`): sin el modelo de grano cargado.
6. Cargar el CPU de esa cámara contra el de antes: la vista nativa cuesta copiarla.
7. `collect_only: false`, reiniciar: la vista vuelve a 224×227 y el grano infiere.

## 7. Lo que no cubre

- **Un pipeline sin ROI en una cámara de video** guarda el frame entero **reducido**
  (`decode`), no el nativo: las vistas son rectángulos del ROI. Juntar el frame entero a
  resolución nativa necesita la vista de frame entero por pipeline, que sigue postergada.
  Mientras tanto, la salida es dibujar un ROI que cubra todo el frame.
- **Reentrenar en producción** a resolución nativa con el modelo andando (§2).
