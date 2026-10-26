# Jetson: los aceleradores de hardware y cómo configurarlos

Qué motores de la Jetson usa la aplicación, qué parámetros del `config.yaml` los controlan y
—lo que más importa al integrador— **qué le hace cada uno a la imagen**: la que ve el
modelo, la que se guarda en el dataset y la que se muestra en los streams y en la pantalla.
Es el documento de quien instala un equipo y de quien arma un fork sobre una Jetson.

Qué hace cada parámetro en el código está en su módulo; el razonamiento de por qué se
eligió así, en `.claude/plans/jetson-accelerators.md`. Acá va lo que hace falta para
configurarlo bien.

- [1. Qué motores usa la aplicación](#1-qué-motores-usa-la-aplicación)
- [2. Por dónde pasa un frame](#2-por-dónde-pasa-un-frame)
- [3. Preparar el equipo](#3-preparar-el-equipo)
- [4. Parámetros de configuración](#4-parámetros-de-configuración)
- [5. Elegir el tamaño de decodificación](#5-elegir-el-tamaño-de-decodificación)
- [6. Exportar los modelos a TensorRT](#6-exportar-los-modelos-a-tensorrt)
- [7. Recetas](#7-recetas)
- [8. Verificar que anda y diagnosticar](#8-verificar-que-anda-y-diagnosticar)
- [9. Lo que no se acelera, a propósito](#9-lo-que-no-se-acelera-a-propósito)

---

## 1. Qué motores usa la aplicación

Además de la GPU, el módulo tiene motores de función fija que hacen un solo trabajo, barato
y en paralelo con la CPU. La aplicación usa tres:

| Motor | Para qué lo usa la app | Se controla con |
|---|---|---|
| **NVDEC** | Decodificar el video de una cámara RTSP o de un video grabado | `cameras.<slot>.decode.backend` |
| **VIC** | Reducir el frame al salir del decodificador; cortar las vistas de los ROI a resolución nativa; escalar y convertir color antes de codificar el RTSP | `cameras.<slot>.decode.width_px/height_px`, `inference.pipelines.<slot>.roi.<cámara>.full_resolution` y `video.rtsp.codec: *_hw` |
| **NVENC** | Codificar los streams RTSP H.264/H.265 | `video.rtsp.codec: h264_hw` o `h265_hw` |

Y la **GPU**, con TensorRT, para correr los modelos (sección 6).

Qué módulo tiene qué:

| Módulo | NVDEC | VIC | NVENC | Notas |
|---|---|---|---|---|
| Orin NX | sí | sí | sí | El de referencia de este proyecto (JetPack 6.1, L4T R36.4, TensorRT 10.3) |
| AGX Orin | sí | sí | sí | |
| Orin Nano | sí | sí | **no** | El RTSP queda en los codecs `_sw` |
| PC de desarrollo (x86) | no | no | no | Todo va por CPU; `auto` lo resuelve solo |

La tabla sale de fuentes secundarias: **lo que vale es lo que el equipo informa**. En la
Jetson, `manual_test/jetson_accelerators/probe_report.py` imprime qué motores existen y
prueba cada uno con un pipeline corto.

**Dos reglas de la aplicación que conviene tener presentes:**

- **Un motor rinde cuando el frame ya está en su memoria.** Un frame que sale de NVDEC está
  en memoria de motor, así que reducirlo en el VIC es casi gratis. Un frame que ya es un
  array de numpy —el de una cámara GigE, o cualquiera que la app ya tocó— tendría que subir y
  volver, y esas copias cuestan más de lo que ahorra el motor. Por eso **no hay aceleración
  de hardware para las cámaras GigE**: su ventaja de la Jetson está en el modelo, en el
  encoder del RTSP y en los relojes.
- **El camino acelerado no es un segundo camino.** `decode.backend` cambia *quién* decodifica,
  no *qué* ve la app: el mismo `config.yaml` corre en la PC de desarrollo y en la planta.

---

## 2. Por dónde pasa un frame

```
cámara RTSP ──► decodificar ──► rotar ──► preprocessor ──► ROI ──► modelo ──► overlay
                (NVDEC + VIC)   (CPU)     lente + brillo    recorte  TensorRT  anotado
                tamaño decode             (por cámara)      + luz    (GPU)
                     │                         │
                     ▼                         ▼
                stream crudo               modelo, dataset, stream anotado
                (lo del sensor)            (el «frame de referencia»)
```

Los puntos que gobiernan todo lo demás:

1. **El tamaño de decodificación es el tamaño de la cámara para toda la app.** El
   decodificador entrega el frame ya reducido a `decode.width_px × decode.height_px`, y de
   ahí en adelante nadie conoce el original: el ROI, las detecciones, el dataset, los dos
   streams, la pantalla y las calibraciones en píxeles están en ese tamaño.
2. **La rotación se aplica después de decodificar**, por CPU. El tamaño de `decode` es el
   previo a rotar: con `rotation: 90cw` y `decode` de 640×360, la app recibe 360×640.
3. **El frame de referencia** es el que sale del `preprocessor` (corrección de lente y ajuste
   de brillo). Es el que ve el modelo, el que se guarda en el dataset y el lienzo sobre el
   que se dibuja el anotado. Un solo espacio de coordenadas: las cajas no pueden caer
   corridas.
4. **El stream crudo muestra lo que entrega la cámara**, sin corrección de lente ni ajuste
   de medición. El anotado muestra el frame de referencia.

Qué parámetro toca qué imagen:

| Parámetro | Modelo | Dataset | Stream crudo | Stream anotado | Pantalla |
|---|:-:|:-:|:-:|:-:|:-:|
| `decode.width_px/height_px` | ✔ | ✔ | ✔ | ✔ | ✔ |
| `decode.backend` | ✔ (píxeles casi iguales) | ✔ | ✔ | ✔ | ✔ |
| `rotation` | ✔ | ✔ | ✔ | ✔ | ✔ |
| `calibration` (lente) | ✔ | ✔ | — | ✔ | ✔ (anotado) |
| `image_adjust` | ✔ | ✔ | — | ✔ | ✔ (anotado) |
| `roi` | ✔ (recorta) | — | — | dibuja el recuadro | idem |
| `video.display` (gamma, CLAHE) | — | — | ✔ | ✔ | ✔ |
| `video.http`, `video.rtsp` | — | — | ✔ | ✔ | — |

---

## 3. Preparar el equipo

El orden de instalación está en el [README](../README.md); lo que importa acá es lo que
afecta el rendimiento.

### Modo de energía y relojes

`setup/jetson/setup-jetson.sh` los informa siempre y los cambia sólo con su bandera, porque
el modo de energía depende del gabinete de la planta:

```bash
bash setup/jetson/setup-jetson.sh --power-mode=20W --clocks-at-boot --headless-boot
```

| Bandera | Qué hace | Por qué importa |
|---|---|---|
| `--power-mode=NAME` | Fija el modo de `nvpmodel` por nombre y lo deja persistente | Un modo bajo hace que todos los motores parezcan lentos |
| `--clocks-at-boot` | Una unidad de systemd corre `jetson_clocks` en cada arranque | El pre y post proceso del modelo es Python: corre más rápido con la CPU fija en su máximo. No sobrevive a un reinicio sin la unidad |
| `--headless-boot` | Arranca en consola, sin escritorio | El escritorio gasta memoria y CPU; en el módulo de 8 GB además evita quedarse sin RAM al exportar un engine |

La referencia de este proyecto es **20W con relojes fijados**. `MAXN` disparó una alerta de
sobrecorriente y se descartó: si la telemetría muestra `overcurrent_count` subiendo, el
módulo recortó relojes y los tiempos dejan de valer.

### Librerías

El framework de inferencia entra por `setup/jetson/` (`constraints.txt`, `requirements.txt`,
`requirements-nodeps.txt`) y lo verifica `setup/venv-setup/verify_env.py`. La app corre el
engine con `tensorrt` y `torch` (que sólo aporta los buffers de la GPU); **ultralytics se
usa únicamente para exportar el engine**, no hace falta para correr.

NVDEC y el RTSP por hardware usan GStreamer a través de PyGObject (`gi`), que
`venv-setup.sh` enlaza al venv. Sin `gi` o sin los elementos `nvv4l2decoder` y `nvvidconv`,
el decodificador por hardware no está disponible.

---

## 4. Parámetros de configuración

### 4.1 Decodificación — `cameras.<slot>.decode`

Se aplica a las cámaras `rtsp` y a los videos (`video_file`). Las GigE no lo leen: su frame
ya llega por el SDK del fabricante.

```yaml
decode:
  backend: auto      # cpu | nvdec | auto
  width_px: 640      # 0 y 0 = el tamaño del stream
  height_px: 360
```

| Clave | Valores | Qué hace |
|---|---|---|
| `backend` | `cpu` | OpenCV con FFmpeg decodifica por CPU. Es el default. |
| | `nvdec` | `nvv4l2decoder` (NVDEC) y `nvvidconv` (VIC) por GStreamer. Si el equipo no los tiene, **no cae a la CPU**: la cámara no abre y el log dice por qué. |
| | `auto` | `nvdec` si el equipo lo tiene, `cpu` si no. |
| `width_px`, `height_px` | enteros | Tamaño al que sale el frame. Tienen que ir **los dos o ninguno**: con uno solo, se ignoran y sale al tamaño del stream. |

**Qué le hacen a la imagen:**

- **El tamaño** es el efecto grande. Reduce la resolución de todo lo que ve la app. Lo hace el
  VIC con filtro bilineal en `nvdec`, y `cv2.resize` bilineal en `cpu`, así que cambiar de
  backend no cambia el tamaño ni el filtro. No conserva la relación de aspecto: es lo que
  se declara.
- **El backend** cambia quién trabaja, no el frame. Los píxeles no son idénticos: el
  decodificador por hardware y FFmpeg no convierten el color igual, y la diferencia es de un
  punto o dos de brillo (del orden de un 1 %), algo mayor en los claros. No cambia lo que se
  ve ni el orden de canales. Al cambiar de backend en una instalación que ya mide, se revisa
  `illumination_min` y, si el modelo trabaja cerca del umbral, `min_confidence_pct`.
- **Reducir es lo que ahorra CPU.** NVDEC a tamaño completo apenas gana: copiar el frame
  grande a numpy cuesta casi lo que decodificar. Con la reducción en el VIC, el frame grande
  nunca sale de la memoria de la Jetson.

**Cómo elegir `backend`:** en una planta con Jetson, **`nvdec` explícito**: si el decodificador
falta, la cámara no abre y se nota, en vez de pasar a gastar CPU sin que nadie lo vea. `auto`
es para un `config.yaml` que se comparte entre la PC de desarrollo y el equipo. El log de
arranque dice cuál quedó: `capturando (nvdec a 640x360)` o `capturando (cpu a tamaño nativo)`.

**Cambiarlo obliga a revisar** todo lo que está en píxeles de la cámara; ver la
[sección 5](#5-elegir-el-tamaño-de-decodificación).

### 4.1.1 Vistas — el ROI de un pipeline a resolución nativa

Reducir el frame le quita detalle a un modelo que mira una zona chica: un ROI de 180 px en
640×360 son 540 px en el video de 1920×1080. Un pipeline puede pedir que su ROI se corte del
video **nativo**, antes de reducirlo. Es una *vista*: llega con cada frame y sólo la usa ese
pipeline. El resto de la app —la pantalla, los streams, los otros pipelines— sigue con el
frame reducido. Se declara en el ROI del pipeline, no en la cámara:

```yaml
inference:
  pipelines:
    pipeline_1:
      roi:
        camera_3:
          enabled: true
          x_px: 210                   # en el frame reducido y rotado: el que se ve y se dibuja
          y_px: 40
          width_px: 180
          height_px: 220
          full_resolution: true
          output_short_side_px: 224   # 0 = el recorte a tamaño nativo
```

| Qué | Cómo |
|---|---|
| Quién corta | Con `nvdec`, un segundo `nvvidconv` (VIC) sobre la salida del decodificador, en paralelo al que reduce el frame, y la vista se aparea con el frame por PTS. Con `cpu`, OpenCV corta el frame nativo antes de reducirlo. |
| Cómo reduce | 5-tap en el VIC e `INTER_AREA` en la CPU: lo más parecido a cómo reduce el clasificador su entrada. El bilineal del frame le agrega aliasing a una reducción x3. |
| Tamaño | `output_short_side_px` lleva el lado corto a esos px y conserva la forma: con 224 y un clasificador de 224, el center crop sólo recorta. 0 = el recorte a tamaño nativo. |
| Cuándo se aplica | Al abrir la cámara: el corte se fija cuando el decodificador anuncia el tamaño nativo. Cambiar el rectángulo obliga a reiniciar; hasta entonces se mide —y se dibuja— el de la vista, y el log lo avisa. |
| Límites | Coordenadas pares y al menos 16 px por lado en el video nativo: un rectángulo menor no se corta, y el log dice cuál. No se corta en una cámara con corrección de lente o ajuste de imagen, porque la vista no pasa por el preprocessor. |
| Si falta | El resultado de ese pipeline sale inválido con `no_view`: no se infiere sobre el recorte reducido. La cámara cuenta las faltas en `view_misses` de su estado. |
| Rotación | El ROI se dibuja sobre el frame rotado; el corte se calcula sobre el video sin rotar, y la vista se rota igual que el frame. |

Costo medido en un Orin NX con dos cámaras a 15 fps (1920×1080 y 2560×1440), sobre un 36 % de
un núcleo de base: **+7 puntos** con la vista reducida a 224 por el VIC, **+21** con la vista a
tamaño nativo, que es casi todo copiar bytes. Por CPU, decodificar y cortar cuesta unas
**4 veces** lo de hoy, así que en la Jetson es `nvdec`. Las mediciones y las reglas están en
`.claude/plans/decoder-views-jetson-findings.md`; la prueba en el equipo, en
`manual_test/decoder_views/`.

El dataset de un pipeline con ROI propio guarda la vista, que es lo que vio el modelo
([sección 4.14](#414-dataset--image_collector)).

### 4.2 Adquisición — `cameras.<slot>.acquisition`

| Clave | Qué hace en una cámara RTSP |
|---|---|
| `fps_limit` | **Tope de frames que entrega la cámara a la app**; 0 = todos los que lleguen. No es el ritmo del sensor: el fps real lo fija la cámara o el servidor RTSP. Los frames de más se descartan antes de convertirlos y copiarlos a numpy. Bajarlo reduce CPU y no cambia la calidad de ningún frame. |
| `exposure_time_us`, `gain` | **No se aplican** a una cámara RTSP y se ignoran sin avisar: se configuran en el servidor web de la cámara. Las GigE sí los aplican. |

La app no sube el ritmo de una cámara: con una de 15 fps, un `fps_limit` de 30 entrega 15.

### 4.3 Rotación — `cameras.<slot>.rotation`

`null | 90cw | 90ccw | 180`. Rota el frame decodificado por CPU y afecta a **todo** lo que
sale de la cámara: modelo, dataset y streams. Con 90° se intercambian ancho y alto, y el
ROI y las calibraciones en píxeles están en el frame ya rotado. Cambiarla pide rehacer el ROI.

### 4.4 ROI e iluminación — `cameras.<slot>.roi`, `illumination_min`

```yaml
roi:
  enabled: false
  x_px: 0
  y_px: 0
  width_px: 0     # 0 = hasta el borde
  height_px: 0
illumination_min: 0   # 0–100
```

| Clave | Qué hace |
|---|---|
| `roi.*` | Recorta el frame de referencia **antes** de pasarlo al modelo: sólo lo de adentro se infiere. Las coordenadas son **píxeles del frame decodificado y rotado** y no se reescalan. Las detecciones vuelven al espacio del frame completo antes de salir. |
| `illumination_min` | Brillo medio mínimo del ROI (0–100). Por debajo, **no se infiere** y el resultado sale inválido con el motivo. Se mide después del ajuste de medición, así que cambiar `image_adjust` o el backend obliga a revisarlo. |

Un ROI chico es el caso donde el tamaño de decodificación más duele: ver sección 5.

### 4.5 Corrección de lente — `cameras.<slot>.calibration`

```yaml
calibration:
  camera_matrix: []   # 3x3; [] = sin corrección
  dist_coeffs: []     # 4, 5, 8, 12 o 14 coeficientes
```

Corrige la distorsión del lente **antes del modelo**, y lo que devuelve es el frame de
referencia: modelo, dataset y anotado ven la imagen corregida; el stream crudo, no. Cambia
*dónde está* cada píxel. Es el paso más caro que corre por CPU, así que sólo se declara si
la medición lo necesita. Un modelo entrenado con imágenes crudas mide peor sobre corregidas:
se reentrena con el dataset corregido o no se corrige. Los mapas se arman con el tamaño del
primer frame, de modo que **la calibración vale para un tamaño de decodificación**: si se
cambia, se recalibra.

### 4.6 Ajuste de medición — `cameras.<slot>.image_adjust`

```yaml
image_adjust:
  brightness_factor: 1.0   # x0.1–x5.0
  clahe_clip: 0.0          # 0–8; 0 = apagado
```

Cambia **lo que mide el modelo**, no lo que se muestra. Existe para reproducir un equipo o un
modelo entrenado sobre imágenes ajustadas así; **en una instalación nueva va en 1.0 y 0.0**.

| Clave | Qué hace en la imagen |
|---|---|
| `brightness_factor` | Multiplica cada píxel por el factor, **lineal y saturado en 255**: lo que se quema no vuelve. No es un gamma. |
| `clahe_clip` | Contraste local por celdas. Un valor alto convierte el ruido del sensor en textura. |

Consecuencias: el dataset guarda la imagen ajustada (se reentrena con eso), y el brillo del
ROI que se compara con `illumination_min` se mide después del ajuste. Una escena oscura se
arregla con luz, exposición o ganancia, que amplifican antes de cuantizar; la tolerancia a la
luz se entrena con aumentación. Un ajuste fijo ata el modelo a ese número: cambiarlo no da
error, da un equipo que mide peor sin avisar.

### 4.7 Salud de la óptica — `cameras.<slot>.lens_health`

Mide la nitidez del frame de la cámara y compara contra una referencia calibrada con el
vidrio limpio. **La referencia depende del tamaño del frame**: reducir de 1920×1080 a
640×360 baja la varianza del Laplaciano y la hace parecer un vidrio sucio. La calibración
registra exposición, ganancia, rotación y ROI como condiciones, pero **no el tamaño de
decodificación**, así que la pantalla no avisa si se cambia. Al cambiar `decode`, se vuelve
a calibrar.

### 4.8 Visualización — `video.display`

```yaml
video:
  display:
    gamma: 1.0        # 0.1–5.0
    clahe_clip: 0.0   # 0–8
```

Ajusta la imagen **sólo para que la mire una persona**: los dos streams y la pantalla. El
modelo y el dataset siguen recibiendo el frame de la cámara. `gamma` menor que 1 levanta las
sombras sin saturar los claros; mayor que 1 las hunde. `clahe_clip` cuesta bastante más CPU
que el gamma. Si lo que se busca es que el *modelo* vea la imagen ajustada, esa es otra
decisión: `image_adjust`.

### 4.9 Stream MJPEG — `video.http`

```yaml
http:
  enabled: true
  port: 8091
  jpeg_quality: 80
  frame_width_px: 960
```

Es el stream liviano, para un navegador. Se codifica por **CPU** (el JPEG por hardware no
rindió desde un array de numpy). Sin clientes conectados no se codifica nada.

| Clave | Qué hace en la imagen |
|---|---|
| `jpeg_quality` | 0–100. Menos calidad, bloques visibles y menos bytes por frame. |
| `frame_width_px` | Reduce el ancho antes de codificar (filtro por área, conserva el aspecto, **nunca amplía**); 0 = nativo. Cuesta CPU en proporción al tamaño codificado. |

Cada cliente recibe como máximo 20 fps. Es video de visualización: lo que se mide se calcula
sobre el frame completo, antes de llegar acá.

### 4.10 Stream RTSP — `video.rtsp`

```yaml
rtsp:
  enabled: false
  port: 8554
  codec: h264_hw        # h264_sw | h264_hw | h265_sw | h265_hw | mjpeg
  fps: 15
  frame_width_px: 0
  bitrate_kbps: 4000
  net_interfaces: []
```

Es el video de resolución completa, para un NVR o un reproductor. Sin clientes en un stream no
se codifica nada.

| Clave | Qué hace en la imagen |
|---|---|
| `codec` | `*_hw`: el VIC escala y convierte, y **NVENC** codifica; la CPU sólo pasa de BGR a BGRx. Cuesta aproximadamente la tercera parte de CPU que `h264_sw`. Sólo existe en una Jetson con NVENC (no en un Orin Nano). `*_sw`: codifica por CPU (x264/x265 en `ultrafast`) y anda en cualquier equipo. `mjpeg`: un JPEG por frame, mucho más ancho de banda. H.265 da el mismo aspecto con menos bitrate. |
| `fps` | Ritmo del stream, y es el que manda: si la cámara entrega más, se descartan frames; si entrega menos o se detiene, se repite el último al vencer cada período, así que el stream sale siempre a este ritmo —también el anotado, que llega al de la inferencia—. Sin eso un reproductor tarda del orden de 30 frames en mostrar el primero. Un `fps` menor que el de la cámara **atrasa** el stream respecto del tiempo real. |
| `frame_width_px` | Reduce el ancho antes de codificar (bilineal en el VIC con `_hw`; nunca amplía; lados pares); 0 = nativo. |
| `bitrate_kbps` | kbit/s del H.264/H.265; 0 = el del encoder. **Es la calidad visual del stream**: a un frame grande, un bitrate bajo da una imagen empastada y con bloques. 4000 kbit/s no alcanza para 5 MP. |
| `net_interfaces` | Interfaces por las que se publica; `[]` = todas. Se publica una instancia por interfaz. |

Todos los codecs H.264/H.265 emiten un keyframe por segundo, para que quien se conecta a
mitad de stream vea imagen enseguida. `codec`, `fps`, `bitrate_kbps` y `frame_width_px` se
toman **cuando un stream se abre**: cambiados desde la pantalla de configuración alcanza con
cerrar el reproductor y volver a abrirlo. Contar los streams contra el presupuesto de NVENC:
con N cámaras × {crudo, anotado} y todos mirados a la vez, el peor caso es el que cuenta.

### 4.11 Modelos — `inference.models.<slot>`

```yaml
models:
  person_det:
    type: yolo_det               # yolo_det | yolo_seg | yolo_cls (| mock)
    path: models/det_rect.engine
    min_confidence_pct: 50
    max_side_px: 0               # no aplica a los yolo_*
    class_names: [person]
    params:
      iou_threshold: 0.7
      max_detections: 300
```

| Clave | Qué hace |
|---|---|
| `type` | Qué implementación corre. `yolo_det` (cajas), `yolo_seg` (cajas y máscaras) y `yolo_cls` (una clase por frame) corren un `.engine` de TensorRT **sin ultralytics**. La tarea la declara el tipo, no el archivo: si el engine informa otra tarea, el modelo queda en error antes del primer frame. |
| `path` | El `.engine`. **Elegir entre un engine cuadrado o uno rectangular es elegir qué archivo apunta `path`**: el tamaño de entrada lo lee del engine, no del config. Carga desde bytes, así que un engine cifrado entra igual (ver [model_protection.md](model_protection.md)). |
| `min_confidence_pct` | Descarta la detección por debajo de este valor. Bajo a propósito si hay que contar cientos de objetos. |
| `max_side_px` | **No aplica a los `yolo_*`**: el tamaño lo fija el engine. |
| `class_names` | Nombre por índice de clase. Los que trae el engine en su metadata le ganan. |
| `params.iou_threshold` | Solapamiento desde el que dos cajas de la misma clase son una sola (NMS por clase). Menor = menos cajas duplicadas pero puede fundir objetos juntos. Default 0.7. |
| `params.max_detections` | Tope de detecciones por frame. Default 300. |

**Cómo llega el frame al modelo** (reproduce lo que ultralytics hace al entrenar):

- **Detección y segmentación:** *letterbox*. Se reduce conservando el aspecto hasta que
  entre en la entrada del engine, se centra y se **rellena con gris** (114). Si el frame y la
  entrada tienen el mismo aspecto no hay relleno. Con un engine cuadrado sobre un frame 16:9,
  casi la mitad de la entrada es relleno gris que se paga igual: es el motivo de los engines
  rectangulares.
- **Clasificación:** se reduce el lado corto al de la entrada y **se recorta el centro**. Lo
  que queda fuera del centro no se mira; si importa, se declara un ROI.

### 4.12 Pipelines — `inference.pipelines.<slot>`

| Clave | Qué hace |
|---|---|
| `cameras` | Qué cámaras entran a este hilo; `[]` = todas las que le lleguen. Las cámaras de un pipeline comparten el modelo, que se carga una vez. |
| `interval_s` | Período mínimo entre frames de **una misma cámara**; 0 = todos. Es el control principal de cuánta GPU y CPU gasta un pipeline: no cambia la imagen de ningún frame, cambia cuántos se miran. |
| `min_detections`, `min_result_confidence_pct` | Marcan el resultado **completo** como no confiable. Se emite igual, con el motivo; sus números no son una medición. |
| `roi.<cámara>` | ROI propio del pipeline en esa cámara: reemplaza al de la cámara sólo para él, y `enabled: false` es el frame entero. Con `full_resolution` se corta del video nativo ([sección 4.1.1](#411-vistas--el-roi-de-un-pipeline-a-resolución-nativa)). |

### 4.13 Anotado — `inference.overlay`

Dibuja el resultado sobre el frame de referencia, una sola vez, y el mismo array va a la
pantalla, al stream anotado y al dataset.

| Clave | Qué hace en la imagen |
|---|---|
| `draw_boxes`, `draw_masks`, `draw_labels`, `draw_roi`, `draw_timestamp` | Qué se dibuja. Las máscaras sólo si el modelo las devuelve. |
| `font_scale`, `thickness` | Tamaño del texto y del trazo **en píxeles del frame**. Se calibran para el tamaño de cada cámara: al reducir con `decode`, el texto sale proporcionalmente más grande. |
| `crop_to_roi` | El anotado es sólo el recorte del ROI. No se combina con varios pipelines sobre una cámara. |
| `class_colors_bgr` | Color por clase; `[]` = la paleta del módulo. |

Si nadie mira el stream anotado ni la pantalla, no se dibuja.

### 4.14 Dataset — `image_collector`

Guarda el frame de referencia: **al tamaño de decodificación, ya corregido y ajustado**. Es lo
único que no se puede regenerar, y los modelos se reentrenan con él. Si en el futuro puede
hacer falta un modelo de mayor resolución, se junta el dataset a tamaño nativo y se pasa al
tamaño reducido para producción.

Un pipeline con ROI propio guarda otra cosa: la región que vio —la vista nativa, si la pidió—,
en su propia carpeta `<cámara>-<pipeline>/`, con su tope y su historial de duplicados. Es la
imagen con la que se reentrena ese modelo, y el pipeline que mira el frame entero de la misma
cámara no le pisa el guardado. `image_collector.pipelines` elige de qué pipelines se guarda.
Para juntar un dataset a resolución completa sin pasar la app entera al tamaño nativo, la
vista va con `output_short_side_px: 0`.

### 4.15 Telemetría de los motores

La telemetría de hardware publica, leída de sysfs **sin root**, `engine_<motor>_active`
(0/1: el motor se usó hace unos segundos), `engine_<motor>_mhz` (frecuencia mientras está
activo; 0 si no) y `overcurrent_count`. Los nombres de motor son `vic`, `nvenc`, `nvdec`,
`nvjpg`, `nvdla0`… según el módulo. Detalle en [influxdb.md](influxdb.md) y [mqtt.md](mqtt.md).
No hay porcentaje de carga de cada motor: sólo lo publica `tegrastats` como root.

---

## 5. Elegir el tamaño de decodificación

Es la decisión que más pesa, y la que más cosas arrastra, porque la cámara pasa a *ser* de
ese tamaño. Sale de tres reglas:

1. **El ancho del ROI, ya reducido, tiene que ser al menos el ancho de entrada del modelo.**
   Un ROI de un tercio del ancho es 640 px a 1080p, pero 213 px a 640×360, y el modelo lo
   recibe estirado cerca de 3×: pierde detalle. Si no alcanza, se decodifica más grande
   (1280×720) o a tamaño nativo. El ROI se recorta *después* de reducir, salvo que el
   pipeline pida una vista ([sección 4.1.1](#411-vistas--el-roi-de-un-pipeline-a-resolución-nativa)),
   que lo corta del nativo y deja el resto de la app en el tamaño reducido.
2. **El tamaño de entrada del modelo es el piso, no el de las fotos con que se entrenó.** Un
   modelo entrenado con `imgsz=640` sobre imágenes 1080p las vio reducidas a 640 de ancho:
   frames de 640×360 le dan prácticamente los mismos píxeles. Uno entrenado a `imgsz=1280`
   necesita al menos 1280×720. El objeto más chico que importa debería seguir midiendo unos
   10–16 px en la entrada del modelo.
3. **Alto y ancho son fijos: el aspecto no se conserva.** Una cámara de reemplazo con otra
   relación (4:3 en lugar de 16:9) sale deformada hasta que se actualice `decode`. La
   configuración es del slot, no del dispositivo: elegir el tamaño con el aspecto de la cámara.

**Cambiar `decode.width_px/height_px` obliga a revisar, en este orden:**

| Qué | Por qué |
|---|---|
| El ROI (`roi.*`) | Está en píxeles del frame decodificado y no se reescala |
| `process:` — `scale_px_per_mm`, `max_load_y_px`, zonas, etc. | Son píxeles del frame: un valor viejo mide mal sin dar error |
| La calibración de lente (`calibration`) | Sus mapas valen para un tamaño |
| La referencia de nitidez (`lens_health.reference`) | Depende del tamaño y la pantalla no avisa |
| `inference.overlay.font_scale`, `thickness` | Se calibran por tamaño de frame |
| El engine (sección 6) | El aspecto del frame fija el del engine |
| El dataset | Se junta a este tamaño; el que ya hay no se mezcla sin pensarlo |

Videos 16:9 típicos: **640×360** (la mitad de lado largo, casi nada de CPU, suficiente para
un modelo de `imgsz=640` sobre el cuadro entero), **1280×720** (cuando el ROI es chico o el
modelo es de 1280) y **0×0** (nativo, para juntar dataset o cuando el ROI es muy chico).

---

## 6. Exportar los modelos a TensorRT

Un `.engine` está atado a la arquitectura de la GPU, a la versión de TensorRT y, en la
práctica, al **módulo**. **Se exporta en la Jetson y con el JetPack que va a correr la planta**,
nunca en la PC de desarrollo ni en otro módulo (TensorRT avisa al cargar un engine hecho en
otro dispositivo, y puede rendir peor o fallar).

```bash
.venv/bin/python manual_test/model_export/export_engine.py              # todos
.venv/bin/python manual_test/model_export/export_engine.py person_det   # uno
```

Lee `manual_test/model_export/config.yaml`, deja el engine al lado del `.pt` con el tamaño
en el nombre (`det_384x640_fp16.engine`) y se lo apunta con `inference.models.<slot>.path`.

| Clave | Qué hace |
|---|---|
| `weights` | El `.pt` entrenado. |
| `rectangular` | `true`: la entrada tiene el aspecto de lo que mira el modelo. `false`: cuadrada, como entrena ultralytics. **Los clasificadores van siempre cuadrados.** |
| `long_side_px` | Lado largo de la entrada; el `imgsz` con que se entrenó. Cada lado es múltiplo de 32. |
| `source_width_px`, `source_height_px` | Lo que ve el modelo: el tamaño de `decode` o, si la cámara usa ROI, el del ROI. De ahí sale el aspecto. |
| `half` | `true` = FP16: la mitad de memoria y más rápido que FP32. Es lo que se usa. |
| `workspace_gb` | Tope de memoria del *builder*. Sin tope, el módulo de 8 GB se quedó sin RAM. |

**Cómo se calcula la entrada rectangular:** el lado largo es `long_side_px` y el corto
mantiene el aspecto de la fuente, redondeado hacia arriba al múltiplo de 32.

| Fuente | `long_side_px` | Entrada (alto × ancho) |
|---|---|---|
| 640×360, 1280×720, 1920×1080 (16:9) | 640 | 384 × 640 |
| 640×480 (4:3) | 640 | 480 × 640 |
| ROI de 400×200 (2:1) | 640 | 320 × 640 |
| cualquiera, clasificador | 224 | 224 × 224 |

Cuando se cambia la forma del ROI o el tamaño de `decode`, **el engine se vuelve a exportar**.
El log de la app dice qué entrada leyó: `Modelo 'person_det': detection TensorRT, entrada
640x384` (ancho × alto).

Otras cosas a saber:

- **Un solo engine por modelo y de a un frame.** No se agrupan frames de varias cámaras en un
  batch: rinde un poco más pero cada cámara espera al lote entero y la latencia empeora.
- **DLA no se usa.** No dio ganancia con los modelos de este proyecto, y con segmentación
  perdió mucho.
- **INT8** es la reserva si falta margen: necesita un set de calibración del dataset **de esa
  planta**, y se vuelve a validar el resultado porque puede perder objetos chicos.
- La exportación tarda minutos y usa memoria: con el escritorio abierto, el módulo de 8 GB se
  puede quedar sin RAM aunque haya tope de workspace. Conviene `--headless-boot` o exportar
  con la app detenida.
- Para entregar, el engine se cifra con la herramienta del repositorio de firma. La app lo
  abre igual, cifrado o no.

---

## 7. Recetas

| Situación | `decode` | Engine | Resto |
|---|---|---|---|
| **Cámara 1080p/1440p, modelo de `imgsz=640`, ROI = el cuadro** | `nvdec`, 640×360 | Rectangular 384×640, FP16 | `image_adjust` en 1.0; `fps_limit` según lo que necesita el proceso |
| **ROI chico (menos de 640 px de ancho a 640×360)** | `nvdec` a 1280×720, o 0×0 | Aspecto del ROI | Verificar la regla 1 de la sección 5 |
| **Modelo entrenado a `imgsz=1280`** | `nvdec`, mínimo 1280×720 | Entrada 736×1280 (`long_side_px: 1280`) | Más GPU: subir `interval_s` |
| **Juntando dataset** | `nvdec` o `cpu` a **0×0** | — | Pasar a un tamaño reducido para producción, después de juntar |
| **Cámara con 5 MP o más y RTSP de salida** | `nvdec` reducido | — | `rtsp.codec: h264_hw`, `bitrate_kbps` acorde al tamaño |
| **Orin Nano** | `nvdec` | Igual | `rtsp.codec` queda en `*_sw` (sin NVENC) |
| **PC de desarrollo / sin Jetson** | `auto` (resuelve a `cpu`) | Sin engines: `mock` o un `.pt` propio | El mismo `config.yaml` sirve |
| **Cámara GigE** | no aplica | Rectangular igual | La ganancia está en el modelo, el RTSP y los relojes |

---

## 8. Verificar que anda y diagnosticar

**Qué mirar en el log al arrancar:**

- `[RtspDriver] … capturando (nvdec a 640x360).` Si dice `cpu`, `auto` resolvió a la CPU o el
  config no se leyó como se esperaba.
- `[Inference] Modelo '…': … TensorRT, entrada 640x384.` Es el tamaño del engine que
  quedó cargado.
- `[Decoder] … decode.backend: nvdec pero …` es el error de un equipo sin NVDEC.

**Herramientas manuales** (en la Jetson, con el intérprete del venv, ver los docstrings):

| Script | Qué contesta |
|---|---|
| `manual_test/jetson_accelerators/probe_report.py` | Qué motores y elementos tiene este equipo, y si cada uno se enciende de verdad |
| `manual_test/jetson_accelerators/stage_bench.py` | Cuánto CPU cuesta cada etapa por CPU y por motor, en este equipo y con estos relojes |
| `manual_test/model_export/export_engine.py` | Exporta los engines (sección 6) |
| `manual_test/offline_inference/run_images.py` | Qué detecta el pipeline sobre imágenes sueltas, con el mismo código que la línea |

Para comparar dos backends o dos tamaños **sobre las mismas imágenes**, se usa la corrida de
imágenes sueltas: ve exactamente lo que ve la línea.

**Problemas habituales:**

| Síntoma | Causa probable |
|---|---|
| La cámara no abre con `nvdec` y el log menciona elementos faltantes | El equipo no es una Jetson o falta `gi` / `nvv4l2decoder`. A propósito no cae a la CPU: usar `cpu` o `auto` |
| La cámara abre con `cpu` pero con `nvdec` nunca entrega frames | El servidor manda los parámetros de codificación (SPS/PPS) sólo en el SDP; la app ya fuerza a repetirlos en cada keyframe. Si persiste, probar el stream con `gst-launch-1.0 rtspsrc … ! parsebin ! nvv4l2decoder ! fakesink` y revisar el perfil y el codec |
| El ROI o las medidas en milímetros «se corrieron» | Se cambió `decode` o `rotation`: están en píxeles del frame |
| `lens_health` pasó a «sucio» sin tocar el vidrio | Se cambió `decode` o la exposición: recalibrar |
| Detecciones que cambiaron un poco al pasar de `cpu` a `nvdec` | Los píxeles no son idénticos (sección 4.1); revisar `min_confidence_pct` e `illumination_min` |
| Los tiempos de inferencia se disparan o varían mucho | Relojes sin fijar o modo de energía bajo (sección 3), o el módulo recortó relojes (`overcurrent_count`) |
| El engine tarda en cargar o rinde peor de lo esperado, y TensorRT avisa de «different models of devices» | El engine se exportó en otro módulo: re-exportar en este |
| El texto del anotado sale enorme | Se redujo `decode` y `font_scale` / `thickness` siguen calibrados para el tamaño anterior |
| La exportación se queda sin memoria | Bajar `workspace_gb`, cerrar el escritorio o exportar con `--headless-boot` |

---

## 9. Lo que no se acelera, a propósito

Para que nadie lo busque en el `config.yaml`:

- **NVJPG** (JPEG por hardware) y **el VIC para frames que ya son numpy**: copiar el frame a la
  memoria del motor y de vuelta cuesta lo que el motor ahorra. El MJPEG y el dataset
  codifican por CPU.
- **DLA**: no dio ganancia con estos modelos.
- **Debayer** y **corrección de lente**: corren por CPU. La corrección de lente es el paso
  más caro y sólo se declara si la medición lo necesita.
- **Cámaras GigE**: su frame sale del SDK en memoria de CPU y no hay camino de copia cero.
