# Tamaños de imagen: del video al modelo

Qué tamaño tiene la imagen en cada paso, quién la reduce y cómo vuelven las detecciones al
frame que se ve. Los números son de un ejemplo genérico. Los parámetros uno por uno están
en [`jetson.md`](jetson.md) §4 y §5; acá va el recorrido completo.

## 1. El ejemplo

Una cámara RTSP de 1920×1080, decodificada con `nvdec`, y tres pipelines sobre ella:

| Pipeline | Modelo | Entrada del modelo | ROI |
|---|---|---|---|
| A | detector | 640×384 | no — frame entero |
| B | detector | 320×320 | sí — una zona chica |
| C | segmentador | 640×640 | no — frame entero |

```yaml
cameras:
  camera_1:
    driver: rtsp
    decode: {backend: nvdec, width_px: 640, height_px: 360}
```

## 2. Por dónde pasa un frame

```
                       ┌─ VIC: reduce a 640×360 ────────────► frame de referencia (UNO)
1920×1080 → NVDEC → tee┤                                          │
                       └─ VIC: corta el ROI de B en nativo ──► vista "B" (viaja con el frame)
                                                                  │
          ┌───────────────────────────────────────────────────────┤
          ▼                          ▼                            ▼
     A: letterbox            B: usa la vista               C: letterbox
     640×360 → 640×384       (ya viene a su entrada)       640×360 → 640×640
     (CPU, casi sólo relleno)                              (CPU, sólo relleno)
```

- **El frame de referencia es uno solo** y lo comparten los tres pipelines, la UI, los
  streams y el dataset. No se copia por pipeline: nadie lo modifica, y cada modelo arma su
  propia entrada en un buffer nuevo.
- **A y C** reciben el frame de 640×360 y cada modelo lo lleva a su entrada en su propio
  preproceso, por CPU: letterbox en detectores y segmentadores, lado corto y centro en
  clasificadores. Funciona con cualquier imagen, entera o recortada.
- **B** tiene ROI y la cámara decodifica video, así que su región **no** sale del frame
  reducido: un segundo VIC la corta del video nativo y la entrega ya al tamaño de la
  entrada de B, leída del `.engine` al arrancar. El VIC sólo reduce a la entrada del modelo
  en este caso.
- En una cámara **GigE** no hay vistas: el frame ya es el del sensor, y el ROI se recorta
  de él por CPU.

## 3. Elegir `decode.width_px` / `height_px`

Se escriben a mano en `cameras.<slot>.decode` (no están en la UI) y piden reiniciar.
`0/0` es el tamaño nativo. No los fija ningún modelo porque es el tamaño de la cámara para
toda la app.

**Con un solo modelo mirando el frame entero:** ese tamaño, con el aspecto de la cámara.
Un detector de 640×384 sobre una cámara 16:9 → `640×360`: el letterbox no reduce nada,
sólo rellena 24 filas.

**Con varios modelos sobre el frame entero:** el del más grande, con el aspecto de la
cámara. Los más chicos reducen desde ahí por CPU.

**Un pipeline con ROI no entra en la cuenta** en una cámara RTSP o de video: su región
sale del nativo. Sí entra en una GigE o en una cámara con corrección de lente o
`image_adjust`, donde el ROI se recorta del frame de referencia: ahí el ROI reducido tiene
que medir al menos la entrada de su modelo.

| Caso | `decode` |
|---|---|
| Un detector de 640 sobre el frame | 640×360 |
| Detectores de 640 y de 1280 | 1280×720 |
| Un solo pipeline, y con ROI | lo mínimo para mirar: 640×360 |
| Juntar dataset a resolución completa | 0×0 |

Al cambiarlo hay que rehacer todo lo que está en píxeles del frame: ROI, `process:`,
calibración de lente, referencia de nitidez. La lista está en [`jetson.md`](jetson.md) §5.

## 4. Varios modelos, distintas entradas: ¿copiar el frame o igualarlos?

**Copiar el frame por pipeline no hace falta y no ayuda.** El frame es de sólo lectura y
cada modelo ya produce su propia imagen de entrada; una copia agregaría memoria y tiempo
sin cambiar lo que ve ningún modelo.

Lo que sí cuesta es que cada modelo reduce el frame por su cuenta, por CPU. Tres opciones,
en orden de preferencia:

1. **Exportar los modelos con la misma entrada, o con el aspecto de la cámara**
   ([`jetson.md`](jetson.md) §6). Con `decode` igual a esa entrada nadie reduce: sólo
   rellenan.
2. **Aceptar la reducción.** Es un `cv2.resize` por frame y por modelo, sin medir todavía
   en la Jetson; con uno o dos modelos sobre un frame ya reducido no suele ser lo que limita.
3. **Cambio de código, no existe hoy:** una vista de frame entero por pipeline, para que el
   VIC entregue a cada modelo su entrada. Son las mismas vistas de §2 con el rectángulo
   completo. Cuesta una rama de VIC y una copia a numpy por frame y por vista —las vistas
   de ROI a 224 px midieron +7 puntos de un núcleo con dos cámaras a 15 fps—, así que
   conviene sólo si la reducción por CPU aparece medida como un problema.

## 5. Dos modelos seguidos en un pipeline

El orden de las etapas está en `_run()` del pipeline (`system/inference/pipeline.py`).

- **La primera etapa fija el tamaño de la vista.** Con ROI, el VIC entrega la región a la
  entrada de la primera; un pipeline que quiera otra la declara en `view_model_slot`.
- **La segunda recibe lo que `_run()` le pase.** Un detector seguido de un clasificador:
  `_run()` recorta cada detección de la región que recibió y se la pasa al clasificador,
  que la lleva a su entrada por CPU. Los modelos YOLO no usan las detecciones previas
  (`previous`): el recorte lo hace el pipeline.
- **Esa región no es nativa.** Sin ROI es el frame de referencia (640×360); con ROI, la
  vista ya reducida a la entrada de la primera etapa. Un objeto de 100×80 px ahí son
  300×240 en el nativo: si la segunda etapa mira un detalle, recibe un recorte agrandado y
  borroso. La regla: el recorte de la detección tiene que medir al menos la entrada de la
  segunda etapa. Si no llega, el config no tiene cómo pedir la vista a resolución completa
  —sale siempre a la entrada de la primera etapa—: hace falta una vista por etapa, que no
  existe todavía. Costaría copiar la vista grande (+21 puntos de un núcleo medidos contra +7
  a 224 px).
- **Coordenadas:** lo que devuelve `_run()` tiene que estar en el espacio de la región que
  recibió. Si la segunda etapa detecta dentro de un recorte, `_run()` le suma el origen de
  ese recorte; de la región al frame completo lo lleva el motor (§6).
- Los dos modelos corren en el mismo hilo, uno detrás del otro: el tiempo del pipeline es
  la suma, y cada etapa deja el suyo en `stage_times_ms`.

## 6. El ROI: cómo se recorta, se mapea y se dibuja

**Se dibuja y se guarda en el frame de referencia** (640×360 en el ejemplo), en
`inference.pipelines.<slot>.roi.<cámara>`. Es el único espacio de coordenadas de la app.

Siguiendo a B, con un ROI de `x=200, y=100, 180×220`:

| Paso | Imagen | Quién |
|---|---|---|
| ROI en el frame de referencia | 180×220 en (200, 100) | config |
| Corte en el video nativo (×3) | 540×660 en (600, 300) | VIC |
| Reducido a la entrada de B, entrando en 320×320 | 262×320 | VIC (5-tap) |
| B lo rellena a 320×320 e infiere | 320×320 | modelo |
| Caja detectada en la vista | (100, 50) – (160, 130) | modelo |
| × (180/262, 220/320) | (69, 34) – (110, 89) | motor |
| + origen del ROI | **(269, 134) – (310, 189)** en el frame | motor |

- **Sí, se mapea:** el motor escala por lo que la vista cubre y corre por el origen del ROI
  (`scale_detections` y `offset_detections` en `result.py`). Las máscaras se reescalan con
  vecino más cercano. Con un ROI recortado del frame (GigE) la escala es 1 y sólo se corre.
- **En el anotado** se ve el frame completo con el recuadro del ROI del color del pipeline,
  verde o rojo si el pipeline declara un veredicto (`roi_state`), y las detecciones ya en
  coordenadas del frame. Con varios pipelines en la misma cámara sale un solo anotado con
  todas las capas (`composer.py`).
- **En el dataset** de B se guarda la vista —lo que vio el modelo— en
  `<cámara>-<pipeline>/`, aparte del frame de la cámara.
- El corte se fija al abrir la cámara: cambiar el ROI pide reiniciar. Un frame que llega
  sin su vista sale `no_view` y no se infiere sobre el recorte reducido.

## 7. Recortar en la cámara para bajar el ancho de banda

> **Sin verificar en un equipo.** Lo de abajo sale de [`jetson.md`](jetson.md) §4.4.1 y
> falta probarlo con una cámara de verdad.

El ROI de la app no baja el tráfico de red: el video llega entero y se corta después. Lo que
sí lo baja es el recorte de la propia cámara (AOI en pylon o StCamera) o, en una RTSP, la
resolución del stream en su web. Lo configura quien instala, con la herramienta del
fabricante; la app no lo escribe.

- **Va primero**: el frame que entrega la cámara pasa a ser el recorte. Orden: recorte de la
  cámara → rotación → `decode` → regiones → calibraciones.
- **Es uno solo por cámara**: tiene que cubrir la unión de las regiones de todos sus
  pipelines. Con un pipeline que mira la escena entera no ahorra nada.
- **Lo que queda afuera no existe** para ningún pipeline, stream ni dataset.
- **Tiene que sobrevivir a un corte de luz** (en Basler, guardarlo en un *user set* de
  arranque). Si se pierde, las regiones guardan el tamaño del frame sobre el que se
  dibujaron y la app avisa cuando llega otro.
- En una RTSP, bajar la resolución del stream también achica el nativo del que se cortan
  las vistas: el ROI de B pierde el detalle que la vista le daba.

Qué medir al probarlo: tráfico en la interfaz de red, fps entregado, CPU del proceso y que
los recuadros sigan en su lugar después de reiniciar la cámara.
