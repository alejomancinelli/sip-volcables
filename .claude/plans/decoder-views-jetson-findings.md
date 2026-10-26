# Vistas del decodificador en la Jetson: hallazgos de los experimentos

Validación del diseño «un decoder, dos ramas» antes de escribir el código: una rama que reduce a
640x360 para el detector de personas y otra que recorta el ROI a resolución nativa para el
clasificador de flujo de grano. No se tocó código del repo; los scripts viven fuera
(`/tmp/claude-1000/-home-iea-Documents-sip-volcables/efc68f34-eee0-4ca9-b6d0-9484442ba639/scratchpad/exp/`)
y están completos en el apéndice. Los números salen de las corridas del 2026-10-23.

## Resumen

| Pregunta | Respuesta corta |
|---|---|
| 1. Cámaras | cam_3 **1920x1080**, cam_4 **2560x1440**, las dos H.264 a **15 fps** (no 10). |
| 2. Semántica del crop | `left/top/right/bottom` son **coordenadas absolutas** del rectángulo fuente (right = x2, bottom = y2), no márgenes. Diferencia media contra numpy: ≤ 0.0013. |
| 3. Cuándo fijarlo | **Sí desde el probe de CAPS del decoder, antes del primer buffer.** Cambiar la posición en PLAYING aplica en el buffer siguiente, pero desde otro hilo puede dar un frame con el recorte a medio aplicar. **Cambiar el tamaño en PLAYING da frames negros sin error.** Con la salida sin fijar en las caps el VIC reescala el recorte al tamaño del input: un crop a tamaño nativo **exige** `width/height` en las caps. |
| 4. Estabilidad del tee | 11 min con dos cámaras: sin errores, sin fugas, 15 fps en las dos ramas. Ninguna variante de `queue` ni `num-extra-surfaces` cambió nada. Los huecos de >300 ms existen igual sin el tee. |
| 5. Apareo | Con la vista en `max-buffers=1` el 92–100 % coincide a la primera (según qué tan lento sea el consumidor) y el bucle robusto converge siempre; con `max-buffers=2` casi nunca. Latencia agregada: 2–3 ms de media, ≤ 14 ms. |
| 6. Plan B (nvcompositor) | Anda en vivo, pero hay que fijar tres cosas a mano y agrega entre 10 y 50 ms de latencia media. Probablemente oculta los stalls de la fuente. No gana en CPU. |
| 7. Calidad del reescalado | **5-tap (`interpolation-method=2`)** es lo más parecido a `INTER_AREA` (MAD 0.34 / 0.59). El camino de hoy (640x360 → ROI → resize) pierde 31–46 % de nitidez. |
| 8. Costo | Vista nativa: +21 puntos de CPU (≈ +60 %). Vista reducida por el VIC a 224: +7 puntos (≈ +20 %). El camino por CPU cuesta ~4x lo de hoy. |
| 9. Lo que muerde | Coordenadas **pares** y recorte **≥ 16x16** (un recorte más chico tira el pipeline entero, rama principal incluida). **Fuga de fds** si el closure del probe captura el pipeline. Tras una caída el pipeline no se recupera solo. |
| 10. `flip-method` | Seguimiento: el crop es de **origen** (sin rotar); con 90° las caps de salida llevan el tamaño **girado** (ancho y alto intercambiados). El resultado es **idéntico bit a bit** a `cv2.rotate`. 180° en el VIC es gratis; 90° no es más barato que `cv2.rotate` en la rama principal. Las reglas de 9.1 no cambian (el piso fatal es < 15 px por dimensión). Sección 10. |

**Recomendación: dos appsinks** (tee, vista con `max-buffers=1`, bucle de apareo robusto), con la vista
**reducida por el VIC a 224 de lado corto con 5-tap**. Detalle y razones en «Recomendación».

## Entorno

| | |
|---|---|
| Módulo | NVIDIA Jetson Orin NX **8 GB** (Seeed reComputer Industrial J201, imagen `mfi_recomputer-industrial-orin-nx-8g-j201-6.1-36.4.0-2024-12-05`), modo de potencia **20 W** (`nvpmodel` 3) |
| L4T / JetPack | **R36.4.0** (`nvidia-l4t-core` 36.4.0-20240912212859), **JetPack 6.1** (`nvidia-jetpack` 6.1+b123), kernel 5.15.148-tegra |
| GStreamer | **1.20.3**; `nvidia-l4t-gstreamer` **36.4.7-20250918154033** (más nuevo que el core, que es 36.4.0); plugins `nvvidconv` 1.2.3, `nvv4l2decoder` 1.14.0, `nvcompositor` 1.16.3 |
| Python del experimento | el venv del proyecto: Python 3.10.12, OpenCV 4.8.1 (con FFmpeg), numpy 1.26.4, psutil 7.2.2, PyGObject 3.42.1 |
| Fuente | `rtsp://10.8.51.1:8554/cam_3` y `/cam_4` por TCP, `latency=0` |
| Carga ajena | La máquina **no estaba ociosa**: `rustdesk` (~60 % de un núcleo), `Xorg` (~30 %), VS Code y Claude. El CPU del sistema rondó 30–35 % durante las corridas. Por eso todos los números de CPU son **del proceso** (`psutil`), no del sistema, y cada configuración de la matriz de costos se corrió dos veces (repiten a ±1.5 puntos). |

Limitaciones de medición que conviene tener presentes:

- **No hay carga de VIC ni de NVDEC.** `tegrastats` sin root sólo informa `GR3D_FREQ`, y sysfs sólo
  dice `active`/`suspended` y la frecuencia. Como proxy se midió el tiempo por frame **dentro de cada
  `nvvidconv`** (entrada → salida, por PTS), que incluye esperas y es una cota superior del trabajo del
  VIC. Para medir la carga real: `sudo tegrastats --interval 1000` en paralelo con `bench.py` (sección
  «Reproducir»).
- **El VIC trabajó a su reloj mínimo.** Durante las corridas `cur_freq` del VIC fue 115.2 MHz todo el
  tiempo (máximo 729.6 MHz), y NVDEC osciló entre 115 y 218 MHz. Los milisegundos por frame del VIC de
  este documento son a ese reloj; hay margen si hiciera falta.
- Los fps de las cámaras (15) y el hecho de que cam_3 y cam_4 tengan tamaños distintos cambian el
  recorte nativo de cada una: 540x660 en cam_3 y 720x880 en cam_4.

## 1. Resolución y fps nativos

```
cam_3: 1920x1080, H.264 Main, framerate en las caps 15007/1000, PTS dt mediana 66.4 ms -> 15.07 fps,
       llegada 15.26 fps en 15 s
cam_4: 2560x1440, H.264 constrained-baseline level 5, framerate en las caps 0/1 (el SPS no trae
       timing), PTS dt mediana 66.5 ms -> 15.05 fps, llegada 15.22 fps en 15 s
```

Evidencia: `q1_probe.py` (decodifica nativo a BGRx 15 s y cuenta). El servidor repite las grabaciones a
**15 fps**, no a 10 como decía el planteo. Eso importa para dimensionar el costo (todo escala con los
fps).

Consecuencia de que cam_4 anuncie `framerate=0/1`: `_read_fps()` de `tools/camera/decoder.py` devuelve 0.0
para ella (`numerator / denominator` con numerador 0), que es correcto, pero un agregador como
`nvcompositor` la toma como 30 fps (ver pregunta 6).

El ROI de prueba (x=210, y=40, w=180, h=220 en 640x360) mapeado con `native_size / (640, 360)`:

| cámara | factor | `left, top, right, bottom` nativos | tamaño |
|---|---|---|---|
| cam_3 (1920x1080) | 3.0 | 630, 120, 1170, 780 | 540x660 |
| cam_4 (2560x1440) | 4.0 | 840, 160, 1560, 1040 | 720x880 |

Son pares sólo porque 3 y 4 son factores enteros y las coordenadas del ROI son pares; en general hay que
redondearlas (pregunta 9).

## 2. Semántica de `left/top/right/bottom` en `nvvidconv`

**Respuesta: coordenadas absolutas del rectángulo fuente.** El recorte es `[left, right) x [top, bottom)`.
Hay que fijar también `width/height` en las caps (ver pregunta 3), si no el VIC reescala.

Prueba (`q2_crop_semantics.py`): un tee del decoder sobre un clip de archivo; una rama decodifica nativo
sin crop y la otra recorta en el VIC. Se aparean 40 frames por orden y se compara el recorte numpy de la
nativa contra la salida del VIC:

| cámara | crop (l, t, r, b) | salida VIC | hipótesis «absoluto» (numpy `[t:b, l:r]`) | hipótesis «márgenes» |
|---|---|---|---|---|
| cam_3 | 630,120,1170,780 (ROI) | 540x660 | **max 3, media 0.0003** | numpy daría 120x180: no coincide |
| cam_3 | 100,50,700,450 | 600x400 | max 8, media 0.0013 | numpy daría 1120x580: no coincide |
| cam_4 | 840,160,1560,1040 (ROI) | 720x880 | max 9, media 0.0006 | numpy daría 160x240: no coincide |
| cam_4 | 100,50,700,450 | 600x400 | max 7, media 0.0009 | numpy daría 1760x940: no coincide |

(Con la hipótesis «márgenes» el tamaño ni siquiera coincide; con «absoluto» coincide el tamaño y los
píxeles.) La diferencia residual (máx. 3–9 sobre 255, en unos pocos píxeles) es redondeo de la conversión
de color, no un desplazamiento. Con coordenadas pares y un recorte ≥ 16 px la diferencia media cae a
0.0000–0.0003 (pregunta 9).

Los clips: `record_clip.py` graba N segundos de la cámara a un `.h264` que **arranca en un IDR**
(`recordings/` estaba vacío, así que los clips se grabaron en `clips/`). Dos cosas aprendidas ahí:

- Un `.mkv` grabado sin esperar al primer IDR **cuelga el decoder** al reproducirlo desde archivo
  (`cam_4.mkv` no entregó un solo frame).
- El `.h264` crudo de cam_4 **no trae PTS** (salvo el primero): para aparear ramas desde archivo hay que
  hacerlo por orden, no por PTS.

## 3. Cuándo se puede fijar el crop

El tamaño nativo sólo se conoce después de negociar caps. Se probó con `q3_crop_timing.py` (un caso por
proceso, ventana de 55 frames, cam_3 y cam_4 con el mismo resultado) y `q3e_tearing.py`.

### (a) Desde un probe en el src pad del decoder, con el evento CAPS: **funciona**

```python
def on_dec_event(pad, info):
    event = info.get_event()
    if event.type == Gst.EventType.CAPS:
        structure = event.parse_caps().get_structure(0)
        left, top, right, bottom = native_roi(structure.get_value("width"), structure.get_value("height"))
        view_conv.set_property("left", left)      # ... top, right, bottom
        view_caps.set_property("caps", Gst.Caps.from_string(
            f"video/x-raw,format=BGRx,width={right - left},height={bottom - top}"))
    return Gst.PadProbeReturn.OK
decoder.get_static_pad("src").add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM, on_dec_event)
```

El probe corre con `buffers ya vistos por nvvidconv: 0`, es decir antes del primer buffer, y el frame 0
ya sale con el recorte correcto (diferencia media 0.0002 en cam_3, 0.0006 en cam_4). También anda en vivo
(el banco `bench.py` lo usa en todas las corridas con vista y las 11 min salen limpias).

**Ojo con qué captura el closure del probe**: si captura el pipeline, hay una **fuga de dos fds por cada
reapertura** (pregunta 9). Capturar sólo los dos elementos (o `weakref`s) no fuga.

Si el stream cambia de resolución a mitad de camino, el decoder manda otro evento CAPS y el probe se
dispararía de nuevo con un tamaño distinto: eso cae en el caso (c). Conviene fijar el crop una sola vez y
reconstruir el pipeline si el tamaño nativo cambia.

### (b) Cambiar la posición en PLAYING: aplica en el buffer siguiente, pero **no es atómico**

Con el tamaño fijado en las caps y el cambio hecho **desde el hilo de streaming** (un probe en el sink
pad del `nvvidconv`, justo antes de que entre el buffer 30):

```
crop efectivo por frame desde el 26:  AAAABBBBBBBBBBBBBBBBBBBBBBBBB
```

El cambio aplica exactamente en el buffer siguiente al `set_property`. Pero desde **otro hilo**, con los
cuatro `set_property` uno tras otro cada 10 ms (`q3e_tearing.py`, 100 frames por corrida, 3 corridas por
cámara): 0, 0, 1 frame «mixto» en cam_3 y 1, 1, 0 en cam_4. **≈ 0.5 % de los frames salen con un recorte
a medio aplicar** (por ejemplo `left/top` del rectángulo nuevo y `right/bottom` del viejo). Los cuatro
valores no se actualizan bajo un mismo lock.

→ Regla: el crop se fija **una vez, antes de PLAYING o desde el probe de CAPS**. Si alguna vez tiene que
cambiar en caliente, hacerlo desde un probe del hilo de streaming. Dado que CLAUDE.md fija que el ROI de
la cámara se cambia en `config.yaml` y se reinicia, no hace falta cambiarlo en caliente.

### (c) Cambiar el **tamaño** del crop en PLAYING: **rompe, y sin error en el bus**

- **c1, con las caps de salida actualizadas junto con el crop** (la forma «correcta»): el appsink recibe
  las caps nuevas en el buffer 30 (`(540, 660)` → `(660, 740)`), **pero los frames son negros** y la
  consola se llena de
  `nvbufsurface: NvBufSurfaceCopy: buffer size mismatch` / `failed to copy` (30 pares en la ventana
  medida). No hay mensaje de ERROR en el bus. Es corrupción silenciosa. El `nvvidconv` no reasigna su pool
  de salida al renegociar. Probado en cam_3 y cam_4.
- **c2, caps de salida sin tocar**: no hay error; el VIC **reescala el recorte nuevo al tamaño de salida
  viejo** (el contenido coincide con `cv2.resize` bilineal del recorte nuevo, diferencia media 1.08).
- **d, caps de salida sin tamaño** (`video/x-raw,format=BGRx` a secas): la salida **toma el tamaño del
  input** (1920x1080 / 2560x1440), y el recorte se estira a ese tamaño; cambiar el crop cambia el
  contenido pero nunca el tamaño de salida. Es la prueba de que **un crop a tamaño nativo no puede dejarse
  sin tamaño en las caps**: hay que saber el tamaño nativo para fijarlo, y por eso (a).

→ Para cambiar el tamaño del crop: reconstruir el pipeline, como ya hace el driver ante cualquier cambio
de config.

## 4. Estabilidad del tee sobre la salida NVMM del decoder

Dos cámaras a la vez, modo `native` (vista a tamaño nativo), consumidor Python por cámara
(`bench.py`). Pipeline por cámara (ver «Launch strings»): `decoder → tee → {principal, vista}`.

### Corrida larga: 11 minutos, sin problemas

`bench.py --mode native --cams 3,4 --duration 660 --queue leaky1 --pair robust`:

| | cam_3 | cam_4 |
|---|---|---|
| fps principal / vista | 15.01 / 15.01 | 15.00 / 15.00 |
| mensajes de ERROR/WARNING en el bus | 0 | 0 |
| huecos entre frames > 300 ms | 10 (1 de > 1 s, máx 1789 ms) | 30 (máx 466 ms) |
| frames apareados a la primera | 9902 / 9904 | 9900 / 9903 |

Proceso: fds 101 → 100, RSS 145 → 157 MB (el mismo crecimiento de unos MB se ve en todas las corridas de
90–120 s y se estabiliza), 57.6 % de un núcleo.

### Los huecos > 300 ms **no son del tee**

La rama principal **sola** (sin tee, sin vista) los tiene también: en la corrida `main_only_d120`,
2 huecos en cam_3 (máx 1733 ms) y 5 en cam_4. cam_4 los tiene sistemáticamente más (30 vs 10 en 11 min),
en todas las variantes, con y sin tee. No se aisló si son del servidor de repetición, de la red o del
decodificador; lo único que se puede afirmar es que **no los agrega el tee**.

### Matriz de variantes (90 s cada una, dos cámaras)

«Rápido» = consumidor sin demora; «lento» = 120 ms de demora por iteración (más que el período de 66 ms,
para forzar descartes). `fps de la vista` < fps principal en las corridas «rápidas» es un artefacto de ese
banco (la vista se retiraba con `try-pull` sin esperar); con el apareo de la sección 5 las dos ramas dan
15/15.

| variante | CPU proceso | fps c3 (princ/vista) | fps c4 | huecos >300 ms c3/c4 | máx hueco c3/c4 (ms) | pulls None c3/c4 | mensajes bus |
|---|---|---|---|---|---|---|---|
| main_only_d120 | 25.1% | 8.04/0.0 | 8.09/0.0 | 2/5 | 1733/560 | 3/1 | 0 |
| leaky1_d0 | 51.4% | 15.03/11.6 | 15.02/11.77 | 5/8 | 1164/428 | 2/2 | 0 |
| leaky2_d0 | 51.4% | 15.01/11.62 | 15.01/11.79 | 1/5 | 312/665 | 1/1 | 0 |
| plain2_d0 | 52.0% | 15.01/11.8 | 15.0/11.69 | 1/12 | 312/407 | 2/2 | 0 |
| default_d0 | 50.4% | 15.01/11.74 | 15.01/11.74 | 6/8 | 432/598 | 1/1 | 0 |
| leaky1_d120 | 40.3% | 8.1/8.05 | 7.97/7.77 | 1/5 | 325/554 | 1/1 | 0 |
| leaky2_d120 | 41.5% | 8.18/8.17 | 8.04/7.88 | 0/4 | 251/365 | 1/1 | 0 |
| plain2_d120 | 39.8% | 8.09/8.03 | 7.82/7.6 | 1/12 | 530/636 | 2/1 | 0 |
| default_d120 | 40.1% | 8.1/8.03 | 7.95/7.75 | 1/6 | 426/403 | 1/2 | 0 |
| leaky1_d120_x4 | 40.7% | 8.12/8.02 | 7.93/7.64 | 1/3 | 356/552 | 2/1 | 0 |
| default_d120_x4 | 40.2% | 8.07/8.04 | 7.98/7.77 | 1/6 | 1414/544 | 2/2 | 0 |
| leaky1_d120_x8 | 41.3% | 8.1/8.04 | 7.98/7.74 | 1/6 | 342/397 | 1/2 | 0 |
| default_d120_x8 | 43.6% | 8.18/8.17 | 8.07/7.98 | 0/3 | 131/573 | 1/1 | 0 |

(Variantes: `leaky1` = `queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0`;
`leaky2` lo mismo con 2; `plain2` = `queue max-size-buffers=2 max-size-bytes=0 max-size-time=0` sin leaky;
`default` = `queue` a secas; `_x4`/`_x8` = `nvv4l2decoder num-extra-surfaces=4/8`.)

Lectura:

- **Ninguna variante deja de entregar frames, ni emite errores, ni se distingue de las otras** en huecos:
  las diferencias (0–12 huecos en 90 s) están dentro del ruido de la fuente y no ordenan por variante.
- **`num-extra-surfaces`: no hace falta.** No cambió los huecos ni los fps y cada superficie extra cuesta
  fds (101 → 107–109 con 4 y → 116 con 8). Alcanza con el pool por defecto porque los dos `nvvidconv`
  copian a memoria de sistema y sueltan la superficie NVMM enseguida.
- **Queue: `leaky=downstream max-size-buffers=1`** es la que se recomienda por diseño (acota la latencia
  y la cantidad de superficies del decoder retenidas aunque una rama se trabe), pero **no se observó una
  diferencia medible** contra la `queue` por defecto. El riesgo teórico de la por defecto (hasta 200
  buffers retenidos si una rama se atasca) no se materializó porque el appsink con `drop=true` nunca
  aplica contrapresión.
- **Lo que sí fuga: un appsink sobre memoria NVMM.** Si la rama de vista termina en `appsink` *sin*
  `nvvidconv` (las muestras quedan en NVMM), cada apertura/cierre deja 6 fds de `/dmabuf` colgados
  (`q9_leak2.py nvmm_sink`). No aplica al diseño (las dos ramas terminan en BGRx de memoria de sistema),
  pero es un buen motivo para no «ahorrarse» el `nvvidconv`.
- **Costo del tee**: en la comparación de igual consumidor (120 ms), 25 % de un núcleo sin tee contra
  40 % con tee y vista nativa.

## 5. Apareo de la rama principal y la vista

Se saca el sample principal y después el de la vista, y se comparan los PTS. «A la primera» = iguales
tras un pull de cada lado. Dos cámaras, 90 s por variante, `queue leaky1`. El consumidor «50 ms» y
«120 ms» mete esa demora tras cada iteración, para simular un hilo de captura que además hace otras
cosas.

| apareo | view max-buffers | consumidor | a la primera c3 | a la primera c4 | re-pulls c3/c4 | renuncias | latencia agregada media / p95 / máx (ms) c3 | c4 |
|---|---|---|---|---|---|---|---|---|
| naive | 1 | rápido (0 ms) | 100.0% | 100.0% | 0/0 | 0 | 1.8 / 3.6 / 10 | 2.8 / 5.2 / 13 |
| naive | 1 | 50 ms | 99.9% | 97.8% | 0/0 | 0 | 1.3 / 3.4 / 71 | 2.1 / 5.1 / 59 |
| naive | 1 | lento (120 ms) | 95.9% | 92.4% | 0/0 | 0 | 0.1 / 0.2 / 3 | 0.3 / 0.3 / 31 |
| naive | 2 | rápido (0 ms) | 100.0% | 100.0% | 0/0 | 0 | 1.7 / 3.6 / 12 | 2.8 / 5.2 / 13 |
| naive | 2 | 50 ms | 47.2% | 3.2% | 0/0 | 0 | 0.7 / 3.2 / 9 | 0.2 / 0.2 / 5 |
| naive | 2 | lento (120 ms) | 1.6% | 3.2% | 0/0 | 0 | 0.1 / 0.2 / 2 | 0.1 / 0.2 / 1 |
| robust | 1 | rápido (0 ms) | 100.0% | 100.0% | 0/0 | 0 | 1.9 / 3.6 / 10 | 2.7 / 5.4 / 13 |
| robust | 1 | lento (120 ms) | 94.7% | 91.7% | 39/59 | 0 | 0.3 / 0.7 / 10 | 0.5 / 3.5 / 8 |
| robust | 2 | rápido (0 ms) | 100.0% | 100.0% | 0/0 | 0 | 1.8 / 3.5 / 11 | 2.7 / 5.4 / 14 |
| robust | 2 | lento (120 ms) | 19.0% | 25.5% | 595/545 | 0 | 0.2 / 0.4 / 8 | 0.3 / 1.5 / 5 |

En la corrida de 11 min con apareo robusto (`max-buffers=1`, consumidor rápido): 9902/9904 y 9900/9903 a
la primera; los 5 restantes se resolvieron con un re-pull (`pair_gave_up = 0`).

Lectura:

- **`max-buffers=1` en la vista.** Con 2, el appsink retiene un frame viejo y la vista queda
  sistemáticamente **un frame atrás** de la principal apenas el consumidor no es instantáneo (PTS de la
  vista −1 frame en el 53–96 % de los apareos con 50 ms y 94–96 % con 120 ms): el apareo ingenuo casi nunca coincide, y el robusto tiene
  que re-pullear en cada iteración. No aporta nada y cuesta un frame de edad.
- **Con consumidor lento y `max-buffers=1`, 5–8 % de los apareos naïve son de frames vecinos** (±1): un
  frame nuevo llegó a una rama entre los dos pulls. El robusto lo corrige.
- **Latencia que agrega el apareo**: es el tiempo desde que llega el sample principal hasta que el par
  está completo, y es básicamente la diferencia de llegada entre las dos ramas: **2–3 ms de media, p95
  ≤ 5.4 ms, máx 14 ms** con consumidor rápido (cam_4 más que cam_3 porque su VIC tarda más); con
  consumidor lento casi cero (la vista ya está esperando).

### Bucle de apareo robusto (el que se midió)

```python
def pair(main_sink, view_sink, repull_timeout_ns=100_000_000):
    main = main_sink.emit("try-pull-sample", read_timeout_ns)       # bloquea hasta el frame siguiente
    if main is None:
        return None, None
    view = view_sink.emit("try-pull-sample", repull_timeout_ns)
    for _ in range(4):
        if view is None:
            return main, None                                       # la vista no llegó: seguir sin ella
        main_pts, view_pts = main.get_buffer().pts, view.get_buffer().pts
        if view_pts == main_pts:
            return main, view
        if view_pts < main_pts:                                     # la vista va atrás: pedir la siguiente
            view = view_sink.emit("try-pull-sample", repull_timeout_ns)
        else:                                                       # la principal va atrás
            newer = main_sink.emit("try-pull-sample", repull_timeout_ns)
            if newer is None:
                return main, None
            main = newer
    return main, None                                               # no convergió (nunca pasó en las corridas)
```

Criterio de diseño: si la vista no llega, devolver `(main, None)` y que el pipeline que usa la vista
trate un frame sin vista; no bloquear el flujo principal esperándola.

## 6. Plan B: `nvcompositor` (un solo buffer con las dos imágenes)

**Funciona con RTSP en vivo, pero hay que forzar tres cosas y no gana en nada medible.** Script
`q6_compositor.py` (comparación de latencia) y `bench.py --mode comp_*` (costo); el diagrama es
`decoder → tee → {nvvidconv 640x360 RGBA → sink_0, nvvidconv crop RGBA → sink_1} → nvcompositor →
nvvidconv → BGRx → appsink`. El lienzo es `640 + ancho_vista` de ancho (1180x660 con la vista nativa de
cam_3, 1360x880 con la de cam_4, **864x360** con la vista reducida a 224x274); Python parte el array en
dos vistas de numpy sin copiar (`frame[:360, :640]` y `frame[:oh, 640:]`). Colores y recorte verificados
(`out/q6_file_cam3.png`, `out/q6_cam*.png`); la parte «principal» del lienzo contra la rama principal
independiente da MAD medio 0.016–0.115.

Lo que hay que hacer para que ande (cada uno se encontró fallando):

1. **`nvcompositor start-time-selection=first`.** Sin eso, en vivo **no sale ni un buffer** (y la rama
   principal tampoco, porque el tee queda detenido): el agregador arranca por timeout antes de que las
   entradas tengan caps (RTSP tarda 1–2 s) y la negociación falla
   (`gst_video_aggregator_find_best_format: Nothing compatible`, `This pad is invalid`). La propiedad
   `latency=2000000000` también lo hace andar, pero agrega 2 s de latencia declarada; no se usó.
2. **No poner `framerate` en las caps de entrada del compositor** (`framerate=15/1` después del
   `nvvidconv`): da `Internal data stream error` (se probó con archivo y en vivo).
3. **Poner `framerate=15/1` en las caps de salida del compositor** (después de `nvcompositor`). Sin eso
   cam_4, cuyo decoder anuncia `framerate=0/1`, sale a **30 fps con cada frame duplicado** (30.01 fps
   medidos), con bastante más CPU (71 % contra 46 % con el framerate fijado).

Con eso, comparado por **contenido** contra la rama principal independiente del mismo decoder (los PTS
del lienzo no coinciden con los del decoder: el agregador los reasigna):

| | cam_3, vista nativa | cam_3, vista 224 | cam_4, vista nativa | cam_4, vista 224 |
|---|---|---|---|---|
| fps del lienzo | 15.00 | 14.98 | 14.98 | 14.98 |
| latencia del lienzo menos la de la rama principal, media / p95 / máx (ms) | 9.4 / 13.3 / 57 *(sin `out-fps`)* | 10.8 / 47 / 131 | **49.7 / 111 / 176** | **47.1 / 105 / 643** |
| huecos entre lienzos > 300 ms | 0 | 0 | 0 | 0 |
| separar el lienzo en Python (ms) | 1.6 | 1.0 | 2.1 | 1.4 |

Lectura:

- **Latencia**: entre ~10 ms (cam_3) y **~50 ms de media con picos de 100+ ms (cam_4)**. La causa es que el
  agregador emite en su **propio reloj de 15 Hz**, no en cuanto llega el frame: la fase entre ese reloj y
  la llegada de los frames decide cuánto espera cada uno (hasta un período, 66 ms). Con dos appsinks el
  retraso del apareo es 2–3 ms.
- **Probablemente oculta los stalls de la fuente (inferencia, no se verificó frame a frame).** Los lienzos
  casi no tienen huecos > 300 ms: 0 en todas las corridas de la tabla y en la de 5 min, y 0–2 en las
  corridas de la matriz de costos (contra 0–12 en las demás configuraciones, que miden la misma fuente
  con la misma carga). Lo esperable de un agregador en vivo con reloj propio es que **repita el último
  frame** cuando la fuente se atasca: el consumidor vería un video fluido que está congelado. Para un
  sistema que mide el proceso con lo que ve la cámara, eso es lo contrario de la entrega always-fresh del
  driver; antes de usarlo habría que comprobarlo (por ejemplo, comparando el contenido de lienzos
  consecutivos durante un hueco).
- **Si la cámara real no entrega exactamente 15.000 fps** el agregador duplica o descarta frames para
  mantener su reloj (no se probó con una cámara real, sólo con el servidor de repetición).
- **CPU**: 54 % (nativa) y 44–45 % (reducida) de un núcleo para dos cámaras, igual que los dos appsinks
  (ver pregunta 8). Más fds (≈145 contra ≈102) por los buffers NVMM de las entradas. En 5 min con
  `comp_resized`: 41.6 % de CPU, RSS 137 → 141 MB, fds 145 → 145, sin errores.

→ El plan B resuelve el apareo por construcción, pero ese problema ya está resuelto en 2–3 ms con
`max-buffers=1` y apareo por PTS, y el plan B introduce un reloj propio, duplicados y latencia variable.
**No recomendado.**

## 7. Calidad al reducir el recorte nativo a 224 de lado corto

Referencia: recorte nativo → `cv2.resize(INTER_AREA)` a 224 de lado corto, que es lo que hace
`prepare_center_crop` del clasificador. Contra ella, el VIC haciendo **recorte y reducción en un solo
paso** (`nvvidconv left= top= right= bottom= interpolation-method=N ! video/x-raw,format=BGRx,width=224,height=274`).
10 frames de cada cámara (`q7_downscale_quality.py`). «Nitidez» = varianza del Laplaciano de la salida
dividida por la de la referencia (1.0 = igual; > 1 = más borde, típicamente aliasing; < 1 = más suave).

**cam_3: recorte 540x660 → 224x274 (reducción x2.41)**

| camino | MAD vs INTER_AREA | nitidez / ref | ms/frame en nvvidconv |
|---|---|---|---|
| VIC 0 nearest | 1.798 | 2.081 | 2.06 |
| VIC 1 bilinear | 1.004 | 1.677 | 2.19 |
| **VIC 2 5-tap** | **0.335** | **0.977** | 2.42 |
| VIC 3 10-tap | 0.489 | 1.143 | 2.39 |
| VIC 4 smart | 0.335 | 0.977 | 2.42 |
| VIC 5 nicest | 0.489 | 1.143 | 2.89 |
| VIC nativo + `cv2.INTER_AREA` (control) | 0.000 | 1.000 | 3.37 |
| **hoy**: VIC a 640x360 → ROI → `cv2.resize` bilineal hacia arriba | 2.099 | 0.691 | 3.52 |

**cam_4: recorte 720x880 → 224x274 (reducción x3.21)**

| camino | MAD vs INTER_AREA | nitidez / ref | ms/frame en nvvidconv |
|---|---|---|---|
| VIC 0 nearest | 2.564 | 2.147 | 2.54 |
| VIC 1 bilinear | 1.769 | 1.829 | 2.64 |
| **VIC 2 5-tap** | **0.586** | **1.127** | 3.20 |
| VIC 3 10-tap | 0.565 | 0.878 | 3.53 |
| VIC 4 smart | 0.586 | 1.127 | 3.35 |
| VIC 5 nicest | 0.565 | 0.878 | 3.25 |
| VIC nativo + `cv2.INTER_AREA` (control) | 0.001 | 1.000 | 4.99 |
| **hoy** | 2.909 | 0.539 | 3.97 |

Zone plate sintética 720x880 → 224x274 (prueba de aliasing sin ambigüedad; varianza de la imagen
reducida, referencia `INTER_AREA` = 7052.9):

| método | MAD vs INTER_AREA | varianza |
|---|---|---|
| 0 nearest | 9.85 | 8169 (+16 %: aliasing) |
| 1 bilinear | 4.90 | 7932 (+12 %) |
| 2 5-tap | 5.86 | 6935 (−1.7 %) |
| 3 10-tap | 7.99 | 6440 (−8.7 %: más suave) |
| 4 smart | 5.86 | 6935 |
| 5 nicest | 7.99 | 6440 |

(La zone plate no es una referencia perfecta: `INTER_AREA` es un filtro de caja, así que el MAD absoluto
es alto para todos. Lo que importa es el orden y la varianza: nearest y bilinear **suman** energía de alta
frecuencia —aliasing—, 5-tap queda al nivel de la referencia y 10-tap/nicest atenúan de más.)

**Respuestas:**

- **Sí, el VIC puede reducir el recorte sin aliasing, con 5-tap (`interpolation-method=2`).** En imágenes
  reales es lo más cercano a `INTER_AREA` (MAD 0.34 / 0.59, nitidez 0.98 / 1.13) y en la zone plate es el
  único que iguala su varianza. Bilinear —el método que usa hoy el decoder— queda a MAD 1.0 / 1.8 y con
  +68 % / +83 % de «borde» (aliasing). 5-tap y «smart» dan **exactamente lo mismo** (3 y 4 son
  indistinguibles aquí, igual que 3 y 5); usar 2.
- **El camino de hoy es el peor para esta tarea**: bajar a 640x360 y recortar de ahí pierde 31 % (cam_3) y
  46 % (cam_4) de nitidez respecto de la referencia nativa, con MAD 2.1 / 2.9. Valida la premisa del
  planteo.
- 10-tap/nicest no ganan sobre 5-tap (en cam_4 igualan el MAD pero quedan más suaves) y el modo de
  trabajo es el mismo; no hay motivo para usarlos.
- **No hace falta que la vista salga a tamaño nativo** para conservar la resolución que usa el
  clasificador, que igual la reduce a 224. Si sale a 224x274 con 5-tap, `prepare_center_crop` recibe una
  imagen con `scale = 1.0`, no reescala y sólo recorta el centro 224x224.
- Lo que **no** se midió: el efecto sobre la precisión del clasificador. MAD ≈ 0.3–0.6 sobre 255 es
  pequeño, pero la decisión definitiva se debería tomar corriendo el clasificador sobre las dos
  versiones de un lote de frames reales.
- Imágenes: `out/q7_cam3_sidebyside.png`, `out/q7_cam4_sidebyside.png` (zoom x2, los 6 métodos más
  referencia y camino de hoy), `out/q7_cam*_diffx8.png` (diferencia x8 contra la referencia) y
  `out/q7_zoneplate.png`.

## 8. Costo con dos cámaras a 15 fps

120 s por corrida tras 10 s de calentamiento, dos repeticiones intercaladas. «CPU» es el **proceso**
(`psutil`: user + system, % de **un** núcleo), con dos hilos consumidores, el apareo robusto en b y c, y
las mismas conversiones a numpy que hace el repo (`bytes()` + `cvtColor` BGRA→BGR). «Python ms/frame» es
sólo pasar el sample a numpy. «nvvidconv ms» es el tiempo de cada `nvvidconv` de la entrada a la salida
(proxy del VIC; incluye esperas).

| config | CPU proceso % de 1 núcleo (r1 / r2) | CPU ms por frame principal (r1 / r2) | Python ms/frame principal c3,c4 | Python ms/frame vista c3,c4 | nvvidconv ms principal c3,c4 | nvvidconv ms vista c3,c4 | RSS MB |
|---|---|---|---|---|---|---|---|
| **a. hoy**: 640x360 solo | 37 / 36 | 12.2 / 12.0 | 0.82, 0.88 | – | 5.88, 9.28 | – | 134→139 |
| **b.** + vista a **tamaño nativo** | 57 / 59 | 19.0 / 19.6 | 0.80, 0.84 | 0.75, 1.15 | 6.45, 9.99 | 6.66, 10.11 | 145→155 |
| **c.** + vista reducida por el VIC a 224 (**5-tap**) | 44 / 43 | 14.5 / 14.3 | 0.79, 0.84 | 0.34, 0.35 | 6.44, 9.98 | 5.26, 7.63 | 135→142 |
| c'. + vista reducida (bilineal) | 42 | 13.8 | 0.79, 0.82 | 0.34, 0.35 | 6.39, 9.80 | 5.18, 7.21 | 134→140 |
| **d. CPU**: OpenCV/FFmpeg nativo, recorte numpy + `cv2.resize` a 640x360 | 144 / 138 | 48.0 / 45.8 | 4.16, 4.33 (recorte + resize) | 62 (es `read()`: red + decodificación por CPU) | – | – | 272→273 |
| B1. compositor, vista nativa | 54 / 54 | 17.9 / 18.2 | 1.70, 2.33 (el lienzo entero) | – | – | – | 163→170 |
| B2. compositor, vista 224 (5-tap) | 45 / 44 | 15.0 / 14.7 | 1.00, 1.08 | – | – | – | 134→137 |

(Notas: «CPU ms por frame» = tiempo de CPU del proceso dividido por los frames principales de las dos
cámaras. En la fila d, la columna «vista» no es una vista: es el tiempo de `read()`, que bloquea esperando
el siguiente frame y decodifica, por eso es ≈ el período de 66 ms.)

Lectura:

- **La vista nativa cuesta +21 puntos de CPU (+60 %).** Casi toda la diferencia es mover bytes: son
  1.4 MB (cam_3) y 2.5 MB (cam_4) por frame, a 30 frames/s en total, copiados dos veces (el `bytes()` y el
  `cvtColor`) además de la escritura del VIC a memoria de sistema.
- **La vista reducida a 224 cuesta +7 puntos (+20 %)**: el VIC escribe 0.25 MB por frame y Python gasta
  0.35 ms. El tiempo del VIC de la vista (5.3 / 7.6 ms) es incluso menor que el de la principal, porque
  escribe menos. **5-tap no cuesta más que bilineal en CPU** (14.3–14.5 contra 13.8 ms/frame, dentro del
  ruido entre repeticiones); en el VIC 5-tap tarda ~+0.1–0.4 ms por frame.
- **El camino por CPU cuesta 4x lo de hoy** (140 % contra 36 %) y no usa el VIC ni NVDEC, así que
  «recorte y reducción en CPU» no es una alternativa para dos cámaras a 15 fps: ocupa 1.4 núcleos de 6.
- **Compositor**: igual de caro que los dos appsinks (54 % y 44–45 %), así que no compensa por CPU.
- Referencia de lo que trabaja el VIC (proxy, ms por frame en `nvvidconv` × 15 fps, suma de las ramas):
  cam_3 ≈ 9 % (a) → 20 % (b) / 17 % (c); cam_4 ≈ 14 % (a) → 30 % (b) / 26 % (c). Son cotas superiores a
  115 MHz; la carga real del VIC y de NVDEC **no se pudo medir sin root**.
- Tiempo del decoder (entrada → salida del `nvv4l2decoder`): cam_3 20 ms de media, cam_4 27 ms
  (p95 35 / 44 ms), igual con y sin tee.

## 9. Lo que además muerde

### 9.1 Geometría del crop

Pruebas en `q9_geometry.py` (cam_3, 1920x1080) y `q9_align.py`:

| caso | resultado |
|---|---|
| origen **par** (l, t, r, b todos pares) | exacto (MAD 0.0000–0.0003) |
| **cualquier lado impar** (left, top, right o bottom, uno a la vez) | **no da error pero el recorte no es exacto**: MAD 1.0–2.8 (el VIC lo alinea y reescala ~1 px). Desplazar el origen +1 o +3 da MAD 4.2; +2, +4, +6, +8, +16 da ≈ 0 |
| ancho 33, 181, alto 661, 181x221 | funcionan en tamaño, y tienen el mismo ± 2 de MAD si algún lado es impar |
| tamaño **1x1, 2x2, 3x3, 4x4, 6x6, 8x8, 10x10, 12x12** | **ERROR FATAL**: `gst_nvvconv_transform: NvBufSurfTransform Failed` → `Internal data stream error` que **detiene todo el pipeline, rama principal incluida** |
| tamaño 16x16 y 32x32 | andan (MAD 0.003 y 0.000) |
| `right > ancho nativo` / `bottom > alto nativo` | **sin error**: el VIC recorta hasta el borde y **reescala** al tamaño fijado en las caps (la vista sale con el tamaño pedido pero distorsionada) |
| `right <= left` | las caps salen con ancho negativo y el pipeline ni se arma (`can't handle caps ... width=-100`) |
| `right=bottom=0` con caps sin tamaño | sin crop |
| caps de otro tamaño que el crop (541x661, 539x660) | funcionan: el VIC reescala |

**Reglas para el mapeo del ROI** (el probe de CAPS debería aplicarlas): las **cuatro** coordenadas pares
(`left`/`top` hacia abajo, `right`/`bottom` hacia arriba), recorte dentro de `[0, nativo]` y **al menos
16x16** (la sección 10.5 afina el piso: 14 es fatal y 15 anda, pero impar), y si el ROI cae por debajo de eso, no crear la
rama o dejar la vista deshabilitada con un log; un error acá mata la medición principal.

```python
def native_roi(roi, native_w, native_h, ref=(640, 360), min_px=16):
    """ROI en coordenadas de 640x360 -> (left, top, right, bottom) nativos, pares y dentro del frame."""
    x, y, w, h = roi
    scale_x, scale_y = native_w / ref[0], native_h / ref[1]
    left, top = int(math.floor(x * scale_x)) & ~1, int(math.floor(y * scale_y)) & ~1
    right = (int(math.ceil((x + w) * scale_x)) + 1) & ~1
    bottom = (int(math.ceil((y + h) * scale_y)) + 1) & ~1
    right, bottom = min(right, native_w & ~1), min(bottom, native_h & ~1)
    if right - left < min_px or bottom - top < min_px:
        return None
    return left, top, right, bottom
```

(El ROI de prueba da `(630,120,1170,780)` en 1920x1080 y `(840,160,1560,1040)` en 2560x1440; con un ROI
de coordenadas impares, `(211,41,181,221)`, da `(632,122,1176,786)` en 1920x1080. Sólo se verificó la
aritmética, no es un resultado de GStreamer.)

### 9.2 Relleno de filas BGRx y tamaños impares

**No hay relleno** de fila en ningún tamaño probado (33, 181, 539, 540, 541, 1370, 1920 de ancho):
`row_bytes == ancho * 4` y el buffer mide `ancho * alto * 4` exactos. `bgrx_to_bgr` del repo, que calcula
`row_bytes = len(data) // height`, sigue siendo correcta y no hace falta tocarla; sólo no se debería
asumir relleno nulo en otra versión de L4T. Caps de salida con ancho o alto impares (539x660, 541x661)
negocian bien.

### 9.3 Caídas y reaperturas del stream

Con un proxy TCP local (`tcp_proxy.py`) que puede cortar la conexión sin tocar el servidor real
(`q9_reconnect.py`), sobre el pipeline de dos ramas:

- **Servidor caído (RST)**: a los ~0.2 s el bus recibe `EOS` y luego `ERROR: Could not read from
  resource` + `Internal data stream error`. El pipeline queda en estado `playing` pero **no entrega
  frames y no se recupera solo** aunque el servidor vuelva (se esperó 16 s tras restablecer). Hay que
  reconstruir.
- **Red congelada con la conexión abierta (blackhole)**: **no hay ningún mensaje en el bus** ni en 12 s
  ni en 30 s (`tcp-timeout` sólo aplica a la conexión, no a lecturas), el consumidor sólo ve
  `try-pull-sample` devolver `None`. Cuando el tráfico vuelve (tras 10 s) el mismo pipeline reanuda a los
  0.1 s. → La detección de cámara caída por timeout de `try-pull-sample` (el `read_timeout_ms` que ya usa
  `_GstCapture.grab()`) es la correcta y no hay que depender del bus.
- **Reapertura (lo que hace el driver)**: `set_state(NULL)` tarda 10–110 ms con el tee y ramas con
  muestras sin retirar (no se cuelga). Reabrir, a primer frame de las dos ramas: **1.4–2.0 s** (mediana
  1.98 s en 15 ciclos; el tamaño de la vista fue 540x660 en los 15; 0 fallos). El tiempo lo domina el
  RTSP (SDP + primer IDR), no el tee.

### 9.4 Fuga de descriptores al reabrir (¡ojo!)

Con el pipeline de dos ramas, **cada reapertura deja 2 fds colgados** (hilos y memoria estables): 12
ciclos pasan de 59 a 81 fds. Un enlace que se cae cada 30 s agotaría el `ulimit -n` de 1024 en ~8 h.
El pipeline de hoy **no** tiene el problema (fds planos en 58).

Causa (`q9_leak.py`, `q9_leak2.py`, `q9_leak3.py`): el **closure del probe de CAPS capturaba el
pipeline** (`p.get_by_name(...)` dentro del probe). Eso arma un ciclo Python ↔ GObject que el `gc` no
puede cortar (`del p; gc.collect()` no lo arregla), el pipeline nunca se finaliza y retiene sus fds.

| variante del probe | fds tras cada ciclo (10 ciclos) |
|---|---|
| closure captura el **pipeline** | 59, 61, 63, 65, … 77 (+2 por ciclo) |
| closure captura **sólo los dos elementos** (`nvvidconv` y `capsfilter`) | 57 plano |
| closure captura `weakref`s a los elementos | 57 plano (un 59 transitorio) |

→ **Capturar los elementos (o weakrefs), no el pipeline.** En `decoder.py` ya se hace bien con
`deep-element-added` (el lambda no captura nada), así que es una regla para el probe nuevo. Conviene un
test que abra y cierre N veces y compare `len(os.listdir("/proc/self/fd"))`.

### 9.5 Otras observaciones

- Sin `config-interval=-1` en el parser, **cam_3 no entrega frames** (el primer `gst-launch` de prueba
  se colgó; cam_4 sí abrió). Es lo que ya resuelve `repeat_parameter_sets`; con el tee sigue siendo
  necesario, y los experimentos lo aplican por `deep-element-added`.
- Un `nvvidconv` con `width/height` que no coinciden con el tamaño del crop **no es un error**: reescala
  (útil para (c), peligroso si se quería exactitud).
- `nvvidconv` en la salida sólo acepta BGRx/RGBA/GRAY8 (no BGR): el `cvtColor` BGRA→BGR en Python no se
  puede evitar con el VIC.
- `nvv4l2decoder` no parece reservar menos superficies por tener dos consumidores: no se vio ningún
  atasco del pool con el tee (pregunta 4).

## 10. `flip-method`: giro en el VIC junto con el crop

Seguimiento de las preguntas 2, 3 y 9. Mismo entorno y mismas reglas (clips de `clips/cam_3.h264` y
`cam_4.h264`, nada en el repo, scripts en el apéndice). `flip-method` valores usados: **1** = 90° antihorario
(`counterclockwise`), **2** = 180°, **3** = 90° horario (`clockwise`). La rotación equivalente de OpenCV
resultó ser `ROTATE_90_COUNTERCLOCKWISE`, `ROTATE_180` y `ROTATE_90_CLOCKWISE` respectivamente (con la
rotación equivocada, MAD 37.6 en cam_3 y 54.8 en cam_4).

**Resumen**

| Pregunta | Respuesta corta |
|---|---|
| 1. ¿En qué espacio van las coordenadas del crop? | En el **frame de origen, sin rotar**. El VIC recorta primero y gira después. |
| 2. ¿Qué `width/height` se fijan? | Con 1 y 3, el **tamaño de la imagen ya girada**: ancho y alto **intercambiados** (rama principal `360x640`, vista recortada `alto_recorte x ancho_recorte`). Con 2 no se intercambia. |
| 3. ¿Sigue andando la receta del probe de CAPS? | Sí, con dos ajustes: llevar el ROI del frame **girado** al de origen antes de fijar `left/top/right/bottom`, y fijar las caps de la vista con el tamaño girado. |
| 4. Contra `cv2.rotate` | **Bit a bit idéntico** (0 píxeles distintos). En CPU: 180° en el VIC es gratis (+0.5 puntos contra +2.0 de `cv2.rotate`); **90° en el VIC no es más barato** que `cv2.rotate` sobre 640x360 (+4.5 puntos contra +3.0). |
| 5. ¿Cambian las reglas de 9.1? | No: los cuatro lados **pares** y el piso por dimensión valen igual con cualquier giro. Se afina el piso: el error fatal es **< 15 px** por dimensión (14 falla, 15 anda pero impar). |

### 10.1 ¿Las coordenadas del crop son de origen o de la imagen girada?

**De origen.** Mismo método que la pregunta 2: un tee del decoder, una rama nativa sin flip y otra con
`nvvidconv interpolation-method=0 flip-method=N left= top= right= bottom=`; 4 frames por orden, comparados
con dos hipótesis calculadas en numpy (`q10_flip_semantics.py`):

- `H_origen` = `cv2.rotate(frame[t:b, l:r], rot)`: se recorta el frame sin rotar y se rota el recorte.
- `H_rotada` = `cv2.rotate(frame, rot)[t:b, l:r]`: se rota todo y se recorta con las mismas coordenadas.

| cámara | recorte de origen (l, t, r, b) | flip | salida del VIC | MAD vs `H_origen` | MAD vs `H_rotada` |
|---|---|---|---|---|---|
| cam_3 (1920x1080) | 630,120,1170,780 (540x660) | 1 (90° ccw) | 660x540 | **0.0002** | tamaño distinto (la hipótesis da 540x660) |
| cam_3 | idem | 2 (180°) | 540x660 | **0.0002** | **34.79** |
| cam_3 | idem | 3 (90° cw) | 660x540 | **0.0002** | tamaño distinto |
| cam_4 (2560x1440) | 840,160,1560,1040 (720x880) | 1 | 880x720 | **0.0008** | tamaño distinto |
| cam_4 | idem | 2 | 720x880 | **0.0008** | **43.92** |
| cam_4 | idem | 3 | 880x720 | **0.0008** | tamaño distinto |

El 180° es la prueba limpia (mismo tamaño en las dos hipótesis: coincide con `H_origen` y se aleja
34–44 de `H_rotada`). Con 90° la hipótesis `H_rotada` ni siquiera da el tamaño de la salida. El residuo
0.0002–0.0008 es el mismo del crop sin giro (pregunta 2): la rotación del VIC no agrega error.

Control sin crop (la imagen entera, caps con el tamaño girado): `flip-method` contra `cv2.rotate` da
**MAD 0.0000** en los tres giros, en las dos cámaras.

Una trampa de la comparación, por si se repite: con `interpolation-method=1` en la rama con flip y el
`nearest` por defecto en la rama de referencia aparece una diferencia media **0.11** con |d| ≤ 1 (error
con signo ≈ +0.1 en los tres canales). No es del flip: pasa igual sin girar (`q10_flip_bias.py`:
`flip 0 + interpolation 1` da MAD 0.1120; `flip 2` con la misma interpolación da 0.0000).

### 10.2 Qué `width/height` hay que fijar con `flip-method` 1 o 3

El VIC gira y entrega la imagen girada, así que **las caps de salida llevan el tamaño girado**:

**Rama principal** (frame entero reducido; referencia = VIC a 640x360 sin giro + `cv2.rotate`, es decir el
camino de hoy):

| flip | caps `640x360` | caps `360x640` | caps sin tamaño |
|---|---|---|---|
| 1 (90° ccw) | salida 640x360 (**imagen estirada**; no coincide con la referencia de 360x640) | **MAD 0.000** | salida = tamaño girado del input (1080x1920 / 1440x2560) |
| 2 (180°) | **MAD 0.000** | salida 360x640 (estirada) | salida = tamaño del input |
| 3 (90° cw) | salida 640x360 (estirada) | **MAD 0.000** | salida 1080x1920 / 1440x2560 |

Mismo resultado en cam_3 y cam_4. No hay error de negociación en ningún caso: con las caps equivocadas el VIC
reescala sin avisar (igual que con el crop de la sección 3).

**Vista recortada** (crop de origen `cw x ch`; referencia = crop sin giro + `cv2.rotate` del recorte, bit a bit):

| flip | caps `cw x ch` | caps `ch x cw` |
|---|---|---|
| 1 | salida `cw x ch` (reescalada; no coincide) | **MAD 0.0000**, salida `ch x cw` |
| 2 | **MAD 0.0000**, salida `cw x ch` | salida `ch x cw` (reescalada) |
| 3 | salida `cw x ch` (reescalada) | **MAD 0.0000**, salida `ch x cw` |

Es decir, la vista sale con el tamaño **del recorte ya girado**: `ch x cw` con 1 y 3, `cw x ch` con 2. Con
caps sin tamaño el recorte se estira al tamaño girado del frame entero (como en la sección 3d).

### 10.3 La receta del probe de CAPS con `flip-method`

**Sigue andando**, con dos cosas distintas a la sección 3a. Probado con `q10_probe_flip.py` (archivo:
comparación numérica; vivo: tamaños y flujo), con el probe capturando sólo los elementos (sección 9.4):

1. **El ROI del operador está en el frame girado y el crop es de origen** (10.1), así que el probe tiene
   que pasar el ROI de la imagen girada a coordenadas de origen. El ROI se escala al tamaño girado del
   nativo (`H x W` con 1 y 3), se pone en coordenadas pares (reglas de 9.1) y se rota hacia atrás:

   ```python
   # (rl, rt, rr, rb) = rectángulo del ROI en la imagen YA girada, a resolución nativa, pares y dentro del frame
   # native_w, native_h = tamaño del frame de ORIGEN (el del evento CAPS del decoder)
   if flip == 1:      # 90° antihorario:  x' = y,        y' = W-1-x
       left, top, right, bottom = native_w - rb, rl, native_w - rt, rr
   elif flip == 3:    # 90° horario:      x' = H-1-y,    y' = x
       left, top, right, bottom = rt, native_h - rr, rb, native_h - rl
   else:              # 180°
       left, top, right, bottom = native_w - rr, native_h - rb, native_w - rl, native_h - rt
   # caps de la vista: width = rr - rl, height = rb - rt (el tamaño del recorte ya girado)
   ```

   Como `native_w` y `native_h` son pares, coordenadas pares en la imagen girada dan coordenadas pares en
   la de origen (si el nativo fuera impar no valdría). El tamaño de referencia del frame girado es
   `360x640` con 1 y 3 (y `640x360` con 2), y por eso el escalado usa `rot_w / 360` y `rot_h / 640`.
2. **Las caps de la rama principal van intercambiadas** (`width=360,height=640` con 1 y 3) y se fijan en el
   launch string; el tamaño girado de la vista las fija el probe.

Resultados, ROI en la imagen girada `x=100,y=150,w=150,h=250` (de 360x640; con 180° el ROI de prueba de
siempre, de 640x360), primeros 4 frames comparados con `cv2.rotate(nativa, rot)[rt:rb, rl:rr]`:

| cámara | flip | rect en la imagen girada nativa | crop de **origen** que fija el probe | vista | MAD (4 frames) |
|---|---|---|---|---|---|
| cam_3 | 1 | 300,450,750,1200 | 720,300,1470,750 | 450x750 | 0.0001–0.0002 |
| cam_3 | 2 | 630,120,1170,780 | 750,300,1290,960 | 540x660 | 0.0002 |
| cam_3 | 3 | 300,450,750,1200 | 450,330,1200,780 | 450x750 | 0.0005 |
| cam_4 | 1 | 400,600,1000,1600 | 960,400,1960,1000 | 600x1000 | 0.0003 |
| cam_4 | 2 | 840,160,1560,1040 | 1000,400,1720,1280 | 720x880 | 0.0020 |
| cam_4 | 3 | 400,600,1000,1600 | 600,440,1600,1040 | 600x1000 | 0.0010–0.0012 |

En vivo (RTSP, ~13 s por combinación, probe + apareo del banco): las seis combinaciones entregan la rama
principal con el tamaño girado esperado (360x640 o 640x360) y la vista con el tamaño del recorte girado, con
185–193 frames por rama y sin mensajes de error. Una captura de cada una en `out/q10_live_cam*_flip*_{main,view}.png`
(la de `flip1` de cam_3 se ve girada 90° antihorario, con el texto del timestamp de abajo hacia arriba).

Nota de un tropiezo del test que no es del VIC: con la `queue leaky` en el modo archivo, el apareo por
índice se desalinea (los frames 2 a 4 salían con MAD 0.2–3.6). En archivo hay que usar `queue` normal.

No se probó **cambiar** `flip-method` en PLAYING; en todos los casos va fijo en el launch string, que es
lo que el diseño necesita (la orientación es de la instalación, no del operador).

### 10.4 Contra `cv2.rotate`: contenido y costo

**Contenido.** VIC reduce y gira en una pasada (`nvvidconv interpolation-method=1 flip-method=N` con las
caps giradas) contra el camino de hoy (VIC a 640x360 bilineal sin girar y `cv2.rotate`), 20 frames por
combinación (`q10_content.py`):

| cámara | flip | salida | MAD | máx abs | píxeles distintos |
|---|---|---|---|---|---|
| cam_3 y cam_4 | 1 (90° ccw) | 360x640 | 0.00000 | 0 | 0.00000 % |
| cam_3 y cam_4 | 2 (180°) | 640x360 | 0.00000 | 0 | 0.00000 % |
| cam_3 y cam_4 | 3 (90° cw) | 360x640 | 0.00000 | 0 | 0.00000 % |

**Idénticos bit a bit.** Reducir y girar en el VIC da exactamente lo mismo que reducir y girar en la CPU.
Nada cambia para el modelo ni para el dataset.

**Costo.** Rama principal, dos cámaras a 15 fps, 120 s por corrida y dos repeticiones intercaladas
(`run_q10_matrix.sh`; mismo banco y mismas condiciones que la pregunta 8, incluida la carga ajena de la
máquina). «CPU» es el proceso, % de un núcleo; «Python ms/frame» incluye el `cv2.rotate` cuando lo hay;
«nvvidconv ms» es el proxy del VIC de la pregunta 8.

| config | CPU % de 1 núcleo (r1 / r2) | CPU ms por frame (r1 / r2) | Python ms/frame c3, c4 | nvvidconv ms c3, c4 | RSS MB | huecos >300 ms c3/c4 (r1+r2) |
|---|---|---|---|---|---|---|
| a. hoy sin giro (referencia) | 35.4 / 35.4 | 11.8 / 11.8 | 0.83, 0.88 | 5.85, 9.24 | 135→139 | 6/14 |
| b. VIC 640x360 + `cv2.rotate` 90° ccw | 38.2 / 38.5 | 12.7 / 12.8 | 1.53, 1.55 | 5.90, 9.24 | 135→138 | 6/20 |
| c. VIC reduce + `flip-method=1` (360x640) | 40.3 / 39.6 | 13.4 / 13.2 | 0.84, 0.87 | 7.14, 10.43 | 135→136 | 4/20 |
| d. VIC 640x360 + `cv2.rotate` 180° | 37.8 / 36.8 | 12.7 / 12.3 | 1.49, 1.50 | 5.90, 9.29 | 130→132 | 6/9 |
| e. VIC reduce + `flip-method=2` (640x360) | 35.9 / 35.8 | 11.9 / 12.2 | 0.81, 0.86 | 5.90, 9.23 | 125→131 | 8/17 |

Lectura:

- **180°: el VIC lo hace gratis.** +0.5 puntos de CPU sobre no girar (35.9 contra 35.4, dentro del ruido
  entre repeticiones) contra +2.0 de `cv2.rotate` (37.3). Sin costo en el tiempo del VIC (5.90 / 9.23 ms).
- **90°: el VIC no es más barato; es algo más caro.** `flip-method=1` cuesta +4.5 puntos sobre no girar
  (40.0) contra +3.0 de `cv2.rotate` (38.4), y el `nvvidconv` tarda +1.3 ms por frame en cam_3 y +1.2 en
  cam_4 (7.14 / 10.43 contra 5.90 / 9.24). La diferencia entre b y c (1.6 puntos) es mayor que la
  dispersión entre repeticiones (≤ 1.0), pero chica; la causa no se identificó (sin root no se ve el VIC, y
  `GR3D_FREQ` promedió 11–13.6 % en todas las corridas, dentro del ruido de la máquina). Probablemente el
  giro de 90° no es una copia en el VIC como el de 180°, pero **es una hipótesis, no una medición**.
- **Por qué importa poco en la rama principal**: `cv2.rotate` sobre 640x360 cuesta 0.34 ms (microbenchmark
  aislado, `q10_rotate_cost.py`) y +0.7 ms por frame dentro del banco. No hay ahorro que justifique
  cambiar el camino de hoy sólo por CPU: la ventaja de mover el giro al VIC en esta rama es **no ocupar
  Python con él**, no menos CPU total.
- **Dónde sí pesa: la vista recortada.** `cv2.rotate` sobre los tamaños de la vista cuesta (microbenchmark,
  un hilo):

  | imagen | 90° ccw | 180° |
  |---|---|---|
  | rama principal 640x360 | 0.34 ms | 0.38 ms |
  | vista reducida 224x274 (la recomendada) | 0.09 ms | 0.09 ms |
  | recorte nativo cam_3 540x660 | 0.56 ms | 0.58 ms |
  | recorte nativo cam_4 720x880 | 1.53 ms | 1.05 ms |

  Con la vista reducida a 224 (la recomendación de la pregunta 7), girar en la CPU cuesta 0.09 ms y no
  vale la pena complicar el probe: **girar la vista en el VIC sólo se justifica con la vista a tamaño
  nativo**, donde el costo de CPU de rotar crece a 0.6–1.5 ms por frame por cámara. El costo en el VIC de
  rotar la vista no se midió por separado.
- No cambian los fps (15.0 en todas) ni los huecos: 4–8 en cam_3 y 9–20 en cam_4 sumando las dos
  repeticiones, sin relación con la configuración.

### 10.5 ¿Cambia `flip-method` las reglas de la sección 9.1?

**No.** `q10_rules.py` repite las pruebas de 9.1 con giro, con el crop en coordenadas de origen y las caps
giradas, comparando con `cv2.rotate(nativa[t:b, l:r])` (cam_3):

| regla | flip 1 | flip 2 | flip 3 |
|---|---|---|---|
| origen par, 540x660 | MAD 0.0002 | 0.0002 | 0.0002 |
| `left+1` / `top+1` / `right+1` / `bottom+1` (un lado impar) | 2.27 / 1.19 / 0.99 / 2.83 | 2.27 / 2.54 / 0.99 / 1.19 | 0.99 / 2.54 / 2.29 / 1.19 |
| los cuatro `+1` (todo impar) | 4.19 | 4.19 | 4.19 |
| los cuatro `+2` (todo par, desplazado) | 0.0000 | 0.0000 | 0.0000 |

(Mismos órdenes de magnitud que sin giro: un lado impar da MAD 1–3 —el VIC alinea y reescala ~1 px—, todo
impar 4.2, y desplazar el origen un número par lo deja exacto.) Los valores por lado cambian de orden entre
giros porque el lado impar de origen cae en otro borde de la imagen girada; la regla es la misma.

Tamaño mínimo, recorte de origen cuadrado y de origen par, para cada giro (incluido el 0 de control):

| lado | 8 | 12 | 14 | 15 | 16 | 18 | 32 |
|---|---|---|---|---|---|---|---|
| flip 0, 1, 2 y 3 | **FATAL** | **FATAL** | **FATAL** | MAD 0.74–0.92 | 0.0026 | 0.0000 | 0.0000 |

Mismo piso **por dimensión de origen**, con o sin giro: `16x100` y `100x16` andan (0.0000 y 0.0056); `12x100`,
`100x12`, `12x16` y `16x12` dan error fatal, con `flip 0` y con `flip 1`. Que una dimensión esté bien no
salva a la otra.

**Corrección a la sección 9.1**: allí se dijo que el fallo «empieza entre 12 y 16». Con 14 y 15 probados:
**14 es fatal y 15 anda** (pero, siendo impar, inexacto: MAD ≈ 0.8). El umbral es **< 15 px por
dimensión**; la regla práctica de «par y ≥ 16» sigue siendo la segura. Si el ROI se define en la imagen
girada, el piso se aplica al recorte **de origen**: un ROI de `14x400` en la imagen girada es `400x14` de
origen y falla (con 15 en lugar de 14 andaría, aunque impar e inexacto). El probe debe validar el tamaño del rectángulo ya mapeado a origen (y, como siempre,
no crear la rama si no da: el error mata también la rama principal).

### 10.6 Launch strings con giro

Rama principal y vista, giro de 90° antihorario (`flip-method=1`); el probe de la sección 3a completa
`view_conv` (en coordenadas de **origen**, 10.3) y `view_caps` (tamaño girado del recorte):

```
rtspsrc name=source latency=0 protocols=tcp tcp-timeout=<read_timeout_ms*1000>
 ! parsebin ! nvv4l2decoder name=dec disable-dpb=true
 ! tee name=t
 t. ! queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0
    ! nvvidconv interpolation-method=1 flip-method=1
    ! video/x-raw,format=BGRx,width=360,height=640
    ! appsink name=sink max-buffers=1 drop=true sync=false
 t. ! queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0
    ! nvvidconv name=view_conv interpolation-method=2 flip-method=1
    ! capsfilter name=view_caps caps=video/x-raw,format=BGRx
    ! appsink name=view max-buffers=1 drop=true sync=false
```

Con `flip-method=3` es idéntico; con `flip-method=2` las caps de la rama principal vuelven a ser
`width=640,height=360`. Para la vista reducida a 224 de lado corto (pregunta 7), el probe fija
`width/height` con el lado corto del recorte **girado** en 224.

## Launch strings que funcionan

Por cámara RTSP, dos appsinks (lo recomendado). `name=dec`, `name=view_conv` y `name=view_caps` son los
nombres que usa el probe; `interpolation-method=2` es 5-tap:

```
rtspsrc name=source latency=0 protocols=tcp tcp-timeout=<read_timeout_ms*1000>
 ! parsebin ! nvv4l2decoder name=dec disable-dpb=true
 ! tee name=t
 t. ! queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0
    ! nvvidconv interpolation-method=1
    ! video/x-raw,format=BGRx,width=640,height=360
    ! appsink name=sink max-buffers=1 drop=true sync=false
 t. ! queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0
    ! nvvidconv name=view_conv interpolation-method=2
    ! capsfilter name=view_caps caps=video/x-raw,format=BGRx
    ! appsink name=view max-buffers=1 drop=true sync=false
```

El probe de CAPS del decoder (sección 3a) completa `left/top/right/bottom` de `view_conv` y las caps de
`view_caps` con `width=224,height=<…>` (vista reducida) o con el tamaño del recorte (vista nativa); para
la reducida el alto sale de `224 * alto_recorte / ancho_recorte` redondeado a par (274 para las dos
cámaras de prueba). Sin la rama de vista (pipeline sin ROI) queda exactamente el string de hoy.

Variante para archivo (`filesrc`, reproducible; sin `disable-dpb`, con `max-buffers=0 drop=false`):

```
filesrc location=clips/cam_3.h264 ! h264parse ! nvv4l2decoder ! tee name=t
 t. ! queue ! nvvidconv ! video/x-raw,format=BGRx ! appsink name=full emit-signals=true max-buffers=0 drop=false sync=false
 t. ! queue ! nvvidconv left=630 top=120 right=1170 bottom=780 ! video/x-raw,format=BGRx,width=540,height=660
    ! appsink name=view emit-signals=true max-buffers=0 drop=false sync=false
```

Plan B (sólo si se quisiera; ver por qué no):

```
rtspsrc ... ! parsebin ! nvv4l2decoder disable-dpb=true ! tee name=t
 t. ! queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0 ! nvvidconv interpolation-method=1
    ! video/x-raw(memory:NVMM),format=RGBA,width=640,height=360 ! comp.sink_0
 t. ! queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0
    ! nvvidconv interpolation-method=2 left=630 top=120 right=1170 bottom=780
    ! video/x-raw(memory:NVMM),format=RGBA,width=224,height=274 ! comp.sink_1
 nvcompositor name=comp start-time-selection=first
    sink_0::xpos=0 sink_0::ypos=0 sink_0::width=640 sink_0::height=360
    sink_1::xpos=640 sink_1::ypos=0 sink_1::width=224 sink_1::height=274
 ! video/x-raw(memory:NVMM),format=RGBA,width=864,height=360,framerate=15/1
 ! nvvidconv ! video/x-raw,format=BGRx ! appsink name=sink max-buffers=1 drop=true sync=false
```

## Recomendación

**Dos appsinks.** Puntos que la sostienen:

1. **Estabilidad**: 11 min con dos cámaras sin errores, sin fugas de fds (con la regla del probe) y sin
   huecos que no estén ya en la rama principal sola.
2. **Apareo**: 100 % a la primera con un consumidor rápido y 92–96 % con uno que tarda el doble que el
   período, y el bucle robusto converge siempre; la latencia agregada es de 2–3 ms. El compositor, que
   «resolvería» el apareo por construcción, agrega 10–50 ms con picos de más de 100 ms, reloj propio y
   y, con toda probabilidad, frames repetidos que enmascaran los stalls de la fuente.
3. **Costo**: igual de caro el compositor que los dos appsinks, así que no hay ganancia que compense.
4. **Vista reducida por el VIC a 224 con 5-tap** y no a tamaño nativo: casi no se distingue de
   `INTER_AREA` (MAD 0.3–0.6), conserva la resolución que el clasificador usa, y cuesta +7 puntos de CPU
   contra +21 de la vista nativa. Reducir en el VIC es además lo que ya hace la app con el decoder.
   Si se quisiera la vista a tamaño nativo (por ejemplo para guardar el ROI en el dataset sin pérdida),
   funciona y cuesta +21 puntos con dos cámaras.
5. **Configuración**: vista con `max-buffers=1 drop=true`; `queue leaky=downstream max-size-buffers=1` en
   las dos ramas; sin `num-extra-surfaces`.

Reglas de implementación que salen de los experimentos (cada una evita un fallo concreto):

- Fijar el crop **una vez**, desde el probe de CAPS del decoder, **capturando sólo los elementos**.
- Coordenadas **pares**, dentro del frame y recorte **≥ 16x16**; si el ROI no da, no crear la rama.
- Un cambio de ROI o de tamaño nativo → **reconstruir el pipeline**; nunca cambiar el tamaño en caliente
  (frames negros sin error).
- Detectar la cámara caída por el timeout de `try-pull-sample` (el blackhole no avisa por el bus) y
  reconstruir; tras un RST el pipeline no se recupera solo.
- Un test de reapertura que cuente fds.
- La rama de vista termina siempre en un `nvvidconv` a BGRx de memoria de sistema (un appsink sobre NVMM
  fuga `/dmabuf`).

Lo que quedó **sin medir** (para no sacar conclusiones de más): carga real del VIC/NVDEC en porcentaje
(sin root), efecto de las distintas interpolaciones sobre la precisión del clasificador, comportamiento
con una cámara real (todo se probó contra el servidor de repetición a 15.000 fps), y el origen de los
huecos de la fuente (más frecuentes en cam_4).

## Reproducir

Los scripts del apéndice van todos en un directorio (en el experimento, `scratchpad/exp/`), con subcarpetas
`clips/`, `logs/` y `out/`; se corren con el Python del venv del proyecto (hace falta `gi` y `psutil`).

```
# clips reproducibles (un IDR al inicio); 8 s alcanzan
python record_clip.py 3 8 ; python record_clip.py 4 8

python q1_probe.py                                  # Q1
python q2_crop_semantics.py                         # Q2
for c in 3 4; do for k in a b c1 c2 d; do python q3_crop_timing.py $c $k; done; done   # Q3 (resultados en logs/q3_*.txt)
python q3e_tearing.py 3 ; python q3e_tearing.py 4   # Q3 (e): tearing desde otro hilo
./run_q4_matrix.sh                                  # Q4: matriz de 13 variantes (~26 min)
python bench.py --mode native --cams 3,4 --duration 660 --queue leaky1 --pair robust --tag q4_long_native_robust
./run_q5_matrix.sh                                  # Q5 (~20 min)
python q6_file.py 3 ; python q6_compositor.py --cam 3 --view native --comp-props start-time-selection=first --out-fps 15
python q7_downscale_quality.py                      # Q7
./run_q8_matrix.sh                                  # Q8 (~30 min)
python q9_geometry.py ; python q9_align.py
python q9_reconnect.py                              # Q9 (usa tcp_proxy.py)
for v in main main_gc tee tee_gc; do python q9_leak.py $v 12; done
for v in none fake nvmm_sink nocrop full; do python q9_leak2.py $v; done
for v in strong elements weak; do python q9_leak3.py $v; done
python make_tables.py                               # las tablas de este documento
# sección 10 (flip-method)
python q10_flip_semantics.py 3 ; python q10_flip_semantics.py 4     # 10.1 y 10.2
python q10_flip_bias.py                                             # el 0.11 de la interpolación
python q10_probe_flip.py file 3 ; python q10_probe_flip.py file 4   # 10.3 contra numpy
python q10_probe_flip.py live 3 ; python q10_probe_flip.py live 4   # 10.3 en vivo
python q10_content.py                                               # 10.4 contenido
./run_q10_matrix.sh                                                 # 10.4 costo (~25 min)
python q10_rotate_cost.py                                           # 10.4 cv2.rotate por tamaño
python q10_rules.py                                                 # 10.5
```

Para tener la carga de VIC y NVDEC (hace falta root), en otra terminal durante cualquier corrida de
`bench.py`:

```
sudo tegrastats --interval 1000 | grep -oE "(VIC|NVDEC|NVENC)[A-Z_]* [0-9%@\[\],]+"
```

`bench.py` también guarda su propia salida de `tegrastats` en `logs/tegra_<tag>.txt`, pero sin root sólo
trae `GR3D_FREQ`.

## Apéndice: scripts

### `common.py`

```python
"""Helpers compartidos de los experimentos (fuera del repo)."""
import os, sys, time, threading
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib
import numpy as np
import cv2
Gst.init(None)

NS = 1_000_000_000
CAMS = {3: "rtsp://10.8.51.1:8554/cam_3", 4: "rtsp://10.8.51.1:8554/cam_4"}
ROI_640 = (210, 40, 180, 220)   # x, y, w, h en 640x360
VIEW_W, VIEW_H = 640, 360


def repeat_parameter_sets(element):
    if element.find_property("config-interval") is not None:
        element.set_property("config-interval", -1)


def build(launch, live=True):
    p = Gst.parse_launch(launch)
    if live:
        p.connect("deep-element-added", lambda b, s, e: repeat_parameter_sets(e))
    return p


def rtsp_head(cam, extra=""):
    return (f"rtspsrc name=source location={CAMS[cam]} latency=0 protocols=tcp tcp-timeout=5000000 "
            f"! parsebin ! nvv4l2decoder disable-dpb=true {extra}")


def roi_native(native_w, native_h, roi=ROI_640):
    """ROI de 640x360 -> (left, top, right, bottom) absolutos nativos."""
    sx, sy = native_w / VIEW_W, native_h / VIEW_H
    x, y, w, h = roi
    l, t = round(x * sx), round(y * sy)
    r, b = round((x + w) * sx), round((y + h) * sy)
    return l, t, r, b


def bgrx_to_bgr(data, w, h):
    row = len(data) // h
    rows = np.frombuffer(data, np.uint8)[:row * h].reshape(h, row)
    return cv2.cvtColor(rows[:, :w * 4].reshape(h, w, 4), cv2.COLOR_BGRA2BGR)


def sample_to_bgr(sample):
    buf = sample.get_buffer()
    st = sample.get_caps().get_structure(0)
    ok, info = buf.map(Gst.MapFlags.READ)
    assert ok
    try:
        return bgrx_to_bgr(bytes(info.data), st.get_value("width"), st.get_value("height"))
    finally:
        buf.unmap(info)


def sample_row_bytes(sample):
    buf = sample.get_buffer()
    st = sample.get_caps().get_structure(0)
    return buf.get_size() // st.get_value("height"), st.get_value("width"), st.get_value("height")


def shutdown(p):
    p.set_state(Gst.State.NULL)

```

### `record_clip.py`

```python
"""Graba N segundos de una cámara RTSP a un .h264 (byte-stream) que arranca en un IDR,
con SPS/PPS antes de cada keyframe. Es el 'archivo de recordings/' de los experimentos."""
import sys
from common import *
cam, secs = int(sys.argv[1]), float(sys.argv[2])
p = build(f"rtspsrc location={CAMS[cam]} latency=0 protocols=tcp ! rtph264depay ! h264parse config-interval=-1 "
          f"! video/x-h264,stream-format=byte-stream,alignment=au ! appsink name=sink max-buffers=0 drop=false sync=false")
sink = p.get_by_name("sink")
p.set_state(Gst.State.PLAYING)
out = open(f"clips/cam_{cam}.h264", "wb")
started, n, t0 = False, 0, None
end = time.time() + secs + 30
while True:
    s = sink.emit("try-pull-sample", 3 * NS)
    if s is None:
        break
    b = s.get_buffer()
    if not started:
        if b.has_flags(Gst.BufferFlags.DELTA_UNIT):
            continue
        started, t0 = True, time.time()
    ok, info = b.map(Gst.MapFlags.READ); out.write(info.data); b.unmap(info); n += 1
    if time.time() - t0 > secs:
        break
out.close(); shutdown(p)
print(f"cam_{cam}: {n} AUs")

```

### `q1_probe.py`

```python
"""Q1: resolución nativa y fps de cam_3/cam_4 (decodificado nativo, sin resize)."""
import sys, statistics
from common import *

for cam in (3, 4):
    p = build(rtsp_head(cam) + "! nvvidconv ! video/x-raw,format=BGRx ! appsink name=sink max-buffers=1 drop=true sync=false")
    sink = p.get_by_name("sink")
    p.set_state(Gst.State.PLAYING)
    pts, arr = [], []
    t_end = time.time() + 15
    first = None
    while time.time() < t_end:
        s = sink.emit("try-pull-sample", 2 * NS)
        if s is None:
            continue
        if first is None:
            first = s.get_caps().get_structure(0).to_string()
        pts.append(s.get_buffer().pts); arr.append(time.monotonic())
    shutdown(p)
    d = [(b - a) / 1e9 for a, b in zip(pts, pts[1:]) if b > a]
    da = [b - a for a, b in zip(arr, arr[1:])]
    print(f"cam_{cam}: {first}")
    print(f"  frames={len(pts)} en 15 s; fps llegada={ (len(arr)-1)/(arr[-1]-arr[0]):.2f}; "
          f"PTS dt mediana={statistics.median(d)*1000:.1f} ms (fps PTS={1/statistics.median(d):.2f}); "
          f"llegada dt p50={statistics.median(da)*1000:.1f} max={max(da)*1000:.0f} ms")

```

### `q2_crop_semantics.py`

```python
"""Q2: semántica de left/top/right/bottom de nvvidconv, contra el recorte en numpy.

Un tee del decoder, una rama nativa sin crop y otra con crop; se aparean por PTS y se
compara el recorte numpy de la nativa contra la salida del VIC. Se prueban las dos
hipótesis: coordenadas absolutas (right=x2, bottom=y2) y márgenes.
"""
import sys
from common import *

MAX = 40

def run(cam, crop, caps_extra=""):
    l, t, r, b = crop
    clip = f"clips/cam_{cam}.h264"
    launch = (f"filesrc location={clip} ! h264parse ! nvv4l2decoder ! tee name=t "
              f"t. ! queue ! nvvidconv ! video/x-raw,format=BGRx ! appsink name=full emit-signals=true max-buffers=0 drop=false sync=false "
              f"t. ! queue ! nvvidconv left={l} top={t} right={r} bottom={b} ! video/x-raw,format=BGRx{caps_extra} ! "
              f"appsink name=crop emit-signals=true max-buffers=0 drop=false sync=false")
    p = build(launch, live=False)
    got = {"full": {}, "crop": {}}
    meta = {}
    def on(name):
        def cb(sink):
            s = sink.emit("pull-sample")
            if len(got[name]) < MAX:
                got[name][len(got[name])] = sample_to_bgr(s)   # por orden: el .h264 de cam_4 no trae PTS
                meta[name] = sample_row_bytes(s)
            return Gst.FlowReturn.OK
        return cb
    for n in got:
        p.get_by_name(n).connect("new-sample", on(n))
    p.set_state(Gst.State.PLAYING)
    bus = p.get_bus()
    deadline = time.time() + 30
    while time.time() < deadline and (len(got["full"]) < MAX or len(got["crop"]) < MAX):
        m = bus.timed_pop_filtered(100_000_000, Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if m is not None:
            if m.type == Gst.MessageType.ERROR:
                print("  ERROR", m.parse_error()[0].message)
            break
    shutdown(p)
    return got, meta

def compare(cam, crop, label, fix_size=True):
    l_, t_, r_, b_ = crop
    extra = f',width={r_-l_},height={b_-t_}' if fix_size else ''
    got, meta = run(cam, crop, extra)
    common_pts = sorted(set(got["full"]) & set(got["crop"]))
    if not common_pts:
        print(f"  {label}: sin PTS comunes (full={len(got['full'])} crop={len(got['crop'])})")
        return
    tag = '' if fix_size else ' (tamaño sin fijar)'
    l, t, r, b = crop
    sample_crop = got["crop"][common_pts[0]]
    ch, cw = sample_crop.shape[:2]
    print(f"  {label}{tag}: crop=({l},{t},{r},{b}) salida VIC {cw}x{ch}; apareados={len(common_pts)}; "
          f"row_bytes/width/height={meta['crop']}")
    full = got["full"][common_pts[0]]
    H, W = full.shape[:2]
    hyps = {
        "absoluto [L,R)x[T,B)": (t, b, l, r),
        "margenes [L,W-R)x[T,H-B)": (t, H - b, l, W - r),
        "absoluto, ancho = R": (t, t + b, l, l + r),
    }
    for name, (y0, y1, x0, x1) in hyps.items():
        ref = full[y0:y1, x0:x1]
        if ref.shape != sample_crop.shape:
            print(f"    hipótesis {name}: tamaño numpy {ref.shape[1]}x{ref.shape[0]} != VIC {cw}x{ch}")
            continue
        diffs_max, diffs_mean = [], []
        for pts in common_pts:
            ref = got["full"][pts][y0:y1, x0:x1].astype(np.int16)
            d = np.abs(ref - got["crop"][pts].astype(np.int16))
            diffs_max.append(d.max()); diffs_mean.append(d.mean())
        print(f"    hipótesis {name}: |diff| max={max(diffs_max)} mean={np.mean(diffs_mean):.4f} "
              f"({len(common_pts)} frames)")
    # guarda el par para mirarlo
    cv2.imwrite(f"out/q2_cam{cam}_{label}_vic.png", sample_crop) if fix_size else None

os.makedirs("out", exist_ok=True)
for cam, native in ((3, (1920, 1080)), (4, (2560, 1440))):
    print(f"cam_{cam} nativa {native}")
    compare(cam, roi_native(*native), "roi")
    compare(cam, (100, 50, 700, 450), "esquina")        # crop distinto, para ver que no depende del ROI
    compare(cam, roi_native(*native), "roi", fix_size=False)

```

### `q3_crop_timing.py`

```python
"""Q3: cuándo se puede fijar el crop. Un caso por proceso:  q3_crop_timing.py <cam> <caso>

  a    crop y caps de salida fijados desde un probe del src pad del decoder, con el evento CAPS
  b    mismo tamaño, otra posición, cambiado en PLAYING (tamaño fijado en las caps)
  c1   tamaño distinto en PLAYING, caps de salida actualizadas junto con el crop
  c2   tamaño distinto en PLAYING, caps de salida sin tocar
  d    salida sin fijar (= tamaño del input); crop A -> C en PLAYING
Se apareaa por índice contra una rama nativa sin crop (referencia en numpy). Los resultados van
a logs/q3_<cam>_<caso>.txt: el stdout lo ensucian los mensajes en C de nvbufsurface.
"""
import sys
from common import *

CAM, CASE = int(sys.argv[1]), sys.argv[2]
W, H = {3: (1920, 1080), 4: (2560, 1440)}[CAM]
CHANGE_AT, STOP_AT = 30, 55
KEEP = set(range(0, 4)) | set(range(CHANGE_AT - 4, STOP_AT))
RES = open(f"logs/q3_{CAM}_{CASE}.txt", "w")
def say(*a):
    print(*a, file=RES, flush=True)

crop_a = roi_native(W, H)
crop_b = (crop_a[0] + 60, crop_a[1] + 20, crop_a[2] + 60, crop_a[3] + 20)
crop_c = (crop_a[0], crop_a[1], crop_a[2] + 120, crop_a[3] + 80)
size = lambda c: (c[2] - c[0], c[3] - c[1])
say(f"cam_{CAM} nativa {W}x{H}; A={crop_a} {size(crop_a)}, B={crop_b}, C={crop_c} {size(crop_c)}")

fixed = lambda c: f",width={size(c)[0]},height={size(c)[1]}"
crop_props = lambda c: f"left={c[0]} top={c[1]} right={c[2]} bottom={c[3]} "
if CASE == "a":
    init_props, init_size = "", ""
elif CASE == "d":
    init_props, init_size = crop_props(crop_a), ""
else:
    init_props, init_size = crop_props(crop_a), fixed(crop_a)

launch = (f"filesrc location=clips/cam_{CAM}.h264 ! h264parse ! nvv4l2decoder name=dec ! tee name=t "
          f"t. ! queue ! nvvidconv ! video/x-raw,format=BGRx ! appsink name=full emit-signals=true max-buffers=0 drop=false sync=false "
          f"t. ! queue ! nvvidconv name=vc {init_props}! capsfilter name=cf caps=video/x-raw,format=BGRx{init_size} ! "
          f"appsink name=view emit-signals=true max-buffers=0 drop=false sync=false")
p = build(launch, live=False)
vc, cf = p.get_by_name("vc"), p.get_by_name("cf")
full, view, view_caps, counts = {}, {}, {}, {"full": 0, "view": 0, "vc_in": 0}
stop = threading.Event()

def set_crop(crop, caps_size=None):
    for k, v in zip(("left", "top", "right", "bottom"), crop):
        vc.set_property(k, v)
    if caps_size:
        cf.set_property("caps", Gst.Caps.from_string(
            f"video/x-raw,format=BGRx,width={caps_size[0]},height={caps_size[1]}"))

def on_full(sink):
    s = sink.emit("pull-sample"); i = counts["full"]; counts["full"] += 1
    if i in KEEP: full[i] = sample_to_bgr(s)
    return Gst.FlowReturn.OK
def on_view(sink):
    s = sink.emit("pull-sample"); i = counts["view"]; counts["view"] += 1
    st = s.get_caps().get_structure(0)
    view_caps[i] = (st.get_value("width"), st.get_value("height"))
    if i in KEEP: view[i] = sample_to_bgr(s)
    if i >= STOP_AT: stop.set()
    return Gst.FlowReturn.OK
p.get_by_name("full").connect("new-sample", on_full)
p.get_by_name("view").connect("new-sample", on_view)

hooks = {}
def vc_in(pad, info):
    i = counts["vc_in"]; counts["vc_in"] += 1
    if i in hooks: hooks[i]()
    return Gst.PadProbeReturn.OK
vc.get_static_pad("sink").add_probe(Gst.PadProbeType.BUFFER, vc_in)

if CASE == "a":
    def dec_probe(pad, info):
        ev = info.get_event()
        if ev.type == Gst.EventType.CAPS:
            st = ev.parse_caps().get_structure(0)
            w, h = st.get_value("width"), st.get_value("height")
            c = roi_native(w, h)
            say(f"probe CAPS del decoder: {w}x{h} -> crop {c}; buffers ya vistos por nvvidconv: {counts['vc_in']}")
            set_crop(c, size(c))
        return Gst.PadProbeReturn.OK
    p.get_by_name("dec").get_static_pad("src").add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM, dec_probe)
elif CASE == "b":
    hooks[CHANGE_AT] = lambda: set_crop(crop_b)
elif CASE == "c1":
    hooks[CHANGE_AT] = lambda: set_crop(crop_c, size(crop_c))
elif CASE == "c2":
    hooks[CHANGE_AT] = lambda: set_crop(crop_c)
elif CASE == "d":
    hooks[CHANGE_AT] = lambda: set_crop(crop_c)

p.set_state(Gst.State.PLAYING)
bus = p.get_bus()
errors = []
t0 = time.time()
while not stop.is_set() and time.time() - t0 < 40:
    m = bus.timed_pop_filtered(100_000_000, Gst.MessageType.ERROR | Gst.MessageType.EOS)
    if m is not None:
        if m.type == Gst.MessageType.ERROR:
            errors.append(m.parse_error()[0].message)
        break
shutdown(p)

def diff(i, crop):
    if i not in full or i not in view: return None
    l, t, r, b = crop
    ref = full[i][t:b, l:r]
    if ref.shape != view[i].shape: return None
    return float(np.abs(ref.astype(np.int16) - view[i].astype(np.int16)).mean())

say(f"frames: full={counts['full']} view={counts['view']}; errores bus={errors}")
changes = [(i, s) for i, s in sorted(view_caps.items()) if i == 0 or s != view_caps[i - 1]]
say(f"tamaño de la vista (índice, WxH) en cada cambio: {changes}")
if CASE == "a":
    say(f"diff medio vs numpy con crop A, frames 0..3: {[diff(i, crop_a) for i in range(4)]}")
elif CASE == "b":
    seq = []
    for i in range(CHANGE_AT - 4, STOP_AT):
        da, db = diff(i, crop_a), diff(i, crop_b)
        seq.append("A" if da is not None and da < 0.5 else "B" if db is not None and db < 0.5 else "?")
    say(f"crop efectivo por frame desde {CHANGE_AT - 4}: {''.join(seq)}  (set justo antes de que el buffer {CHANGE_AT} entre al VIC)")
elif CASE in ("c1", "c2", "d"):
    seq = []
    for i in range(CHANGE_AT - 4, STOP_AT):
        da, dc = diff(i, crop_a), diff(i, crop_c)
        seq.append("A" if da is not None and da < 0.5 else "C" if dc is not None and dc < 0.5 else "?")
    say(f"crop efectivo por frame desde {CHANGE_AT - 4}: {''.join(seq)}  (set justo antes del buffer {CHANGE_AT}; '?' = otro tamaño o escalado)")
    if CASE == "c2" or CASE == "d":
        # el VIC escala el crop nuevo a la salida vieja: se compara contra el recorte reescalado
        i = STOP_AT - 1
        if i in view and i in full:
            l, t, r, b = crop_c
            ref = cv2.resize(full[i][t:b, l:r], (view[i].shape[1], view[i].shape[0]), interpolation=cv2.INTER_LINEAR)
            say(f"frame {i}: vista {view[i].shape[1]}x{view[i].shape[0]}; diff vs recorte C reescalado (cv2 bilineal) = {np.abs(ref.astype(np.int16) - view[i].astype(np.int16)).mean():.3f}")
RES.close()

# ── diagnóstico: dónde cae realmente la vista dentro del frame completo (template matching) ──
if CASE in ("c1", "c2", "d"):
    out = open(f"logs/q3_{CAM}_{CASE}.txt", "a")
    for i in (CHANGE_AT - 1, CHANGE_AT, CHANGE_AT + 1, STOP_AT - 1):
        if i in view and i in full:
            v, f = view[i], full[i]
            if v.shape[0] <= f.shape[0] and v.shape[1] <= f.shape[1]:
                res = cv2.matchTemplate(f, v, cv2.TM_SQDIFF_NORMED)
                mn, _, loc, _ = cv2.minMaxLoc(res)
                print(f"  frame {i}: vista {v.shape[1]}x{v.shape[0]} aparece en x={loc[0]} y={loc[1]} (error normalizado {mn:.5f})", file=out)
            else:
                print(f"  frame {i}: vista {v.shape[1]}x{v.shape[0]} no cabe en el frame", file=out)
            cv2.imwrite(f"out/q3_{CAM}_{CASE}_view{i}.png", v)
    out.close()

```

### `q3e_tearing.py`

```python
"""Q3 (e): cambiar el crop desde OTRO hilo, a 100 Hz, entre dos posiciones del mismo tamaño.
¿Algún frame sale con un crop a medio aplicar (left/top de uno, right/bottom del otro)?"""
import sys, random
from common import *
CAM = int(sys.argv[1]); W, H = {3: (1920, 1080), 4: (2560, 1440)}[CAM]
A = roi_native(W, H)
B = (A[0] + 60, A[1] + 20, A[2] + 60, A[3] + 20)
sz = (A[2] - A[0], A[3] - A[1])
launch = (f"filesrc location=clips/cam_{CAM}.h264 ! h264parse ! nvv4l2decoder ! tee name=t "
          f"t. ! queue ! nvvidconv ! video/x-raw,format=BGRx ! appsink name=full emit-signals=true max-buffers=0 drop=false sync=false "
          f"t. ! queue ! nvvidconv name=vc left={A[0]} top={A[1]} right={A[2]} bottom={A[3]} ! video/x-raw,format=BGRx,width={sz[0]},height={sz[1]} ! "
          f"appsink name=view emit-signals=true max-buffers=0 drop=false sync=false")
p = build(launch, live=False)
vc = p.get_by_name("vc")
full, view = [], []
LIMIT = 100
def on(lst, keep):
    def cb(sink):
        s = sink.emit("pull-sample")
        if len(lst) < LIMIT: lst.append(sample_to_bgr(s))
        return Gst.FlowReturn.OK
    return cb
p.get_by_name("full").connect("new-sample", on(full, 1))
p.get_by_name("view").connect("new-sample", on(view, 1))
stop = threading.Event()
def flipper():
    cur = A
    while not stop.is_set():
        cur = B if cur == A else A
        # el orden de los set_property es el de un código ingenuo: uno por uno
        for k, v in zip(("left", "top", "right", "bottom"), cur):
            vc.set_property(k, v)
        time.sleep(0.01)
th = threading.Thread(target=flipper, daemon=True); th.start()
p.set_state(Gst.State.PLAYING)
bus = p.get_bus()
bus.timed_pop_filtered(30 * NS, Gst.MessageType.EOS | Gst.MessageType.ERROR)
stop.set(); shutdown(p)
cnt = {"A": 0, "B": 0, "mixto/otro": 0}
for f, v in zip(full, view):
    ds = {}
    for name, c in (("A", A), ("B", B)):
        ref = f[c[1]:c[3], c[0]:c[2]]
        ds[name] = float(np.abs(ref.astype(np.int16) - v.astype(np.int16)).mean())
    best = min(ds, key=ds.get)
    cnt[best if ds[best] < 0.5 else "mixto/otro"] += 1
print(f"cam_{CAM}: frames={min(len(full), len(view))} -> {cnt}", file=open(f"logs/q3e_{CAM}.txt", "w"))

```

### `bench.py`

```python
"""
Banco de pruebas en vivo (Q4, Q5, Q8): una o dos cámaras RTSP, con o sin la rama de vista.

  python bench.py --mode {main,native,resized,cpu_native} --cams 3,4 --duration 60
                  [--queue {default,leaky1,leaky2,plain2}] [--extra N] [--view-max 1|2]
                  [--pair {none,naive,robust}] [--delay-ms 0] [--method 1..5] [--tag nombre]
                  [--url-base rtsp://host:puerto]

Modos
  main        hoy: rtspsrc ! parsebin ! nvv4l2decoder ! nvvidconv(640x360) ! appsink
  native      + tee, y una vista recortada en el tamaño nativo (el crop se fija desde el probe de CAPS)
  resized     + tee, y una vista recortada y reducida por el VIC a 224 de lado corto
  cpu_native  OpenCV/FFmpeg a tamaño nativo, recorte en numpy y cv2.resize a 640x360

Escribe logs/bench_<tag>.json con el resumen. Un hilo consumidor por cámara; el tiempo de
Python por frame es el de pasar el sample a numpy (la misma bgrx_to_bgr del repo).
"""
import argparse, json, re, statistics, subprocess
import psutil
from common import *

ap = argparse.ArgumentParser()
ap.add_argument("--mode", default="main")
ap.add_argument("--cams", default="3,4")
ap.add_argument("--duration", type=float, default=60)
ap.add_argument("--warmup", type=float, default=8)
ap.add_argument("--queue", default="leaky1")
ap.add_argument("--extra", type=int, default=0)
ap.add_argument("--view-max", type=int, default=1)
ap.add_argument("--pair", default="none")
ap.add_argument("--delay-ms", type=float, default=0)
ap.add_argument("--method", type=int, default=1)
ap.add_argument("--short-side", type=int, default=224)
ap.add_argument("--tag", default=None)
ap.add_argument("--url-base", default="rtsp://10.8.51.1:8554")
ap.add_argument("--flip", type=int, default=0, help="flip-method del nvvidconv de la rama principal (1 ccw, 2 180, 3 cw)")
ap.add_argument("--cv2-rotate", default="none", choices=["none", "ccw", "cw", "180"], help="cv2.rotate en Python sobre la rama principal")
ap.add_argument("--no-sink-pull", action="store_true", help="no consume nada: aísla el costo del pipeline")
args = ap.parse_args()
TAG = args.tag or f"{args.mode}_{args.queue}"
CAMS_RUN = [int(c) for c in args.cams.split(",")]
QUEUES = {
    "default": "queue",
    "leaky1": "queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0",
    "leaky2": "queue leaky=downstream max-size-buffers=2 max-size-bytes=0 max-size-time=0",
    "plain2": "queue max-size-buffers=2 max-size-bytes=0 max-size-time=0",
}
Q = QUEUES[args.queue]
# con flip 1 o 3 el VIC entrega la imagen girada, así que las caps llevan el ancho y el alto intercambiados (sección 10)
_MAIN_SIZE = "width=360,height=640" if args.flip in (1, 3) else "width=640,height=360"
_FLIP = f"flip-method={args.flip} " if args.flip else ""
MAIN_TAIL = (f"nvvidconv interpolation-method=1 {_FLIP}name=vcm ! video/x-raw,format=BGRx,{_MAIN_SIZE} "
             "! appsink name=sink max-buffers=1 drop=true sync=false")
CV2_ROT = {"ccw": cv2.ROTATE_90_COUNTERCLOCKWISE, "cw": cv2.ROTATE_90_CLOCKWISE, "180": cv2.ROTATE_180}.get(args.cv2_rotate)


def launch_for(cam):
    url = f"{args.url_base}/cam_{cam}"
    extra = f"num-extra-surfaces={args.extra} " if args.extra else ""
    head = (f"rtspsrc name=source location={url} latency=0 protocols=tcp tcp-timeout=5000000 "
            f"! parsebin ! nvv4l2decoder name=dec disable-dpb=true {extra}")
    if args.mode == "main":
        return head + "! " + MAIN_TAIL
    if args.mode.startswith("comp_"):
        return head + comp_launch(cam)
    return (head + f"! tee name=t t. ! {Q} ! {MAIN_TAIL} "
            f"t. ! {Q} ! nvvidconv name=vcv interpolation-method={args.method if args.mode == 'resized' else 1} "
            f"! capsfilter name=cf caps=video/x-raw,format=BGRx "
            f"! appsink name=view max-buffers={args.view_max} drop=true sync=false")


NATIVE = {3: (1920, 1080), 4: (2560, 1440)}


def comp_launch(cam):
    """Plan B: nvcompositor con la imagen a 640x360 y el recorte (nativo o reducido por el VIC) lado a lado."""
    l, t, r, b = roi_native(*NATIVE[cam])
    cw, ch = r - l, b - t
    if args.mode == "comp_resized":
        ow = args.short_side if cw <= ch else round(args.short_side * cw / ch)
        oh = args.short_side if ch < cw else round(args.short_side * ch / cw)
        ow, oh = ow + ow % 2, oh + oh % 2
        method = args.method
    else:
        ow, oh, method = cw, ch, 1
    return (f"! tee name=t t. ! {Q} ! nvvidconv interpolation-method=1 ! video/x-raw(memory:NVMM),format=RGBA,width=640,height=360 ! comp.sink_0 "
            f"t. ! {Q} ! nvvidconv interpolation-method={method} left={l} top={t} right={r} bottom={b} "
            f"! video/x-raw(memory:NVMM),format=RGBA,width={ow},height={oh} ! comp.sink_1 "
            f"nvcompositor name=comp start-time-selection=first sink_0::xpos=0 sink_0::ypos=0 sink_0::width=640 sink_0::height=360 "
            f"sink_1::xpos=640 sink_1::ypos=0 sink_1::width={ow} sink_1::height={oh} "
            f"! video/x-raw(memory:NVMM),format=RGBA,width={640 + ow},height={max(360, oh)},framerate=15/1 "
            f"! nvvidconv ! video/x-raw,format=BGRx ! appsink name=sink max-buffers=1 drop=true sync=false")


class Cam:
    def __init__(self, cam):
        self.cam = cam
        self.stats = {"main_frames": 0, "view_frames": 0, "pulls_none": 0, "bus": [], "pair_first_ok": 0,
                      "pair_total": 0, "pair_repulls": 0, "pair_gave_up": 0, "view_missing": 0}
        self.t_main_py, self.t_view_py, self.gaps, self.pair_lat_ms, self.pts_diff_frames = [], [], [], [], []
        self.vc_ms = {"main": [], "view": []}     # nvvidconv: de la entrada a la salida, por rama
        self.dec_ms = []                          # decodificador: entrada -> salida
        self.crop_info = None
        self.stop = threading.Event()
        self.measuring = threading.Event()
        self.timeline = []                        # (t, frames_main acumulados)

    # ── pipeline ────────────────────────────────────────────────────────────
    def build(self):
        self.p = build(launch_for(self.cam))
        self.sink = self.p.get_by_name("sink")
        self.view = self.p.get_by_name("view")
        if self.view is not None:
            dec_src = self.p.get_by_name("dec").get_static_pad("src")
            dec_src.add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM, self._on_dec_event)
        self._add_latency_probes()

    def _on_dec_event(self, pad, info):
        ev = info.get_event()
        if ev.type != Gst.EventType.CAPS:
            return Gst.PadProbeReturn.OK
        st = ev.parse_caps().get_structure(0)
        w, h = st.get_value("width"), st.get_value("height")
        l, t, r, b = roi_native(w, h)
        vc, cf = self.p.get_by_name("vcv"), self.p.get_by_name("cf")
        for k, v in zip(("left", "top", "right", "bottom"), (l, t, r, b)):
            vc.set_property(k, v)
        cw, ch = r - l, b - t
        if args.mode == "resized":
            ow = args.short_side if cw <= ch else round(args.short_side * cw / ch)
            oh = args.short_side if ch < cw else round(args.short_side * ch / cw)
            ow, oh = ow + ow % 2, oh + oh % 2
        else:
            ow, oh = cw, ch
        cf.set_property("caps", Gst.Caps.from_string(f"video/x-raw,format=BGRx,width={ow},height={oh}"))
        self.crop_info = {"native": (w, h), "crop": (l, t, r, b), "view_out": (ow, oh)}
        return Gst.PadProbeReturn.OK

    def _add_latency_probes(self):
        """Tiempo dentro de cada nvvidconv (entrada -> salida, por PTS) y dentro del decodificador."""
        def timer(element_name, bucket, key):
            el = self.p.get_by_name(element_name)
            if el is None:
                return
            t_in = {}
            def on_in(pad, info):
                t_in[info.get_buffer().pts] = time.monotonic(); return Gst.PadProbeReturn.OK
            def on_out(pad, info):
                t0 = t_in.pop(info.get_buffer().pts, None)
                if t0 is not None and self.measuring.is_set():
                    bucket[key].append((time.monotonic() - t0) * 1000)
                if len(t_in) > 50:
                    t_in.clear()
                return Gst.PadProbeReturn.OK
            el.get_static_pad("sink").add_probe(Gst.PadProbeType.BUFFER, on_in)
            el.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, on_out)
        timer("vcm", self.vc_ms, "main")
        timer("vcv", self.vc_ms, "view")
        # decodificador: se mide con una lista aparte
        holder = {"dec": self.dec_ms}
        timer("dec", holder, "dec")

    # ── consumo ─────────────────────────────────────────────────────────────
    def _to_numpy(self, sample, bucket):
        t0 = time.perf_counter()
        buf = sample.get_buffer(); st = sample.get_caps().get_structure(0)
        ok, info = buf.map(Gst.MapFlags.READ)
        try:
            frame = bgrx_to_bgr(bytes(info.data), st.get_value("width"), st.get_value("height"))
        finally:
            buf.unmap(info)
        if CV2_ROT is not None:
            frame = cv2.rotate(frame, CV2_ROT)
        if self.measuring.is_set():
            bucket.append((time.perf_counter() - t0) * 1000)
        return frame

    def run(self):
        self.build()
        self.p.set_state(Gst.State.PLAYING)
        last_main = None
        period_ns = 66_666_667
        while not self.stop.is_set():
            m = self.sink.emit("try-pull-sample", NS)          # 1 s: más que eso es un stall
            if m is None:
                self.stats["pulls_none"] += 1
                self._drain_bus()
                continue
            t_m = time.monotonic()
            v = None
            if args.no_sink_pull:
                continue
            measuring = self.measuring.is_set()
            if self.view is not None and args.pair != "none":
                m, v = self._pair(m, period_ns, measuring)
                t_pair = time.monotonic()
                if measuring:
                    self.pair_lat_ms.append((t_pair - t_m) * 1000)
            elif self.view is not None:
                v = self.view.emit("try-pull-sample", 0)       # sin esperar: la vista 'más fresca' que haya
            if m is not None:
                self._to_numpy(m, self.t_main_py if measuring else [])
                if measuring:
                    self.stats["main_frames"] += 1
                    if last_main is not None:
                        self.gaps.append((t_m - last_main) * 1000)
                last_main = t_m
            if v is not None:
                self._to_numpy(v, self.t_view_py if measuring else [])
                if measuring:
                    self.stats["view_frames"] += 1
            if args.delay_ms:
                time.sleep(args.delay_ms / 1000)
            self._drain_bus()
        self.p.set_state(Gst.State.NULL)

    def _pair(self, m, period_ns, measuring):
        """Aparea el sample principal con el de la vista por PTS, repidiendo el que vaya atrás."""
        v = self.view.emit("try-pull-sample", 100 * 1_000_000)
        first_ok = v is not None and v.get_buffer().pts == m.get_buffer().pts
        if measuring:
            self.stats["pair_total"] += 1
            self.stats["pair_first_ok"] += int(first_ok)
            if v is not None:
                self.pts_diff_frames.append(round((v.get_buffer().pts - m.get_buffer().pts) / period_ns))
            else:
                self.stats["view_missing"] += 1
        if args.pair == "naive":
            return m, v
        for _ in range(4):
            if v is None:
                return m, None
            dm, dv = m.get_buffer().pts, v.get_buffer().pts
            if dv == dm:
                return m, v
            if measuring:
                self.stats["pair_repulls"] += 1
            if dv < dm:
                v = self.view.emit("try-pull-sample", 100 * 1_000_000)
            else:
                m2 = self.sink.emit("try-pull-sample", 100 * 1_000_000)
                if m2 is None:
                    return m, None
                m = m2
        if measuring:
            self.stats["pair_gave_up"] += 1
        return m, None

    def _drain_bus(self):
        bus = self.p.get_bus()
        while True:
            msg = bus.pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.WARNING)
            if msg is None:
                break
            kind = "ERROR" if msg.type == Gst.MessageType.ERROR else "WARNING"
            self.stats["bus"].append(f"{kind}: {(msg.parse_error() if kind == 'ERROR' else msg.parse_warning())[0].message}")


class CpuCam(Cam):
    """OpenCV/FFmpeg a tamaño nativo: recorte en numpy y cv2.resize a 640x360."""

    def run(self):
        os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"
        cap = cv2.VideoCapture(f"{args.url_base}/cam_{self.cam}", cv2.CAP_FFMPEG,
                               [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000])
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        last = None
        while not self.stop.is_set():
            t0 = time.perf_counter()
            ok, frame = cap.read()
            t_read = time.perf_counter()
            if not ok:
                self.stats["pulls_none"] += 1
                continue
            h, w = frame.shape[:2]
            l, t, r, b = roi_native(w, h)
            t1 = time.perf_counter()
            view = np.ascontiguousarray(frame[t:b, l:r])
            small = cv2.resize(frame, (640, 360), interpolation=cv2.INTER_LINEAR)
            t2 = time.perf_counter()
            now = time.monotonic()
            if self.measuring.is_set():
                self.stats["main_frames"] += 1
                self.t_main_py.append((t2 - t1) * 1000)          # recorte + resize
                self.t_view_py.append((t_read - t0) * 1000)      # read(): espera de red + decodificación en CPU
                if last is not None:
                    self.gaps.append((now - last) * 1000)
            last = now
            if args.delay_ms:
                time.sleep(args.delay_ms / 1000)
        cap.release()


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * q))] if xs else None

def summ(xs):
    return None if not xs else {"n": len(xs), "mean": round(statistics.fmean(xs), 3), "p50": round(pct(xs, .5), 3),
                                "p95": round(pct(xs, .95), 3), "max": round(max(xs), 3)}


def tegra_reader(path):
    try:
        return subprocess.Popen(["tegrastats", "--interval", "1000"], stdout=open(path, "w"), stderr=subprocess.DEVNULL)
    except OSError:
        return None


def engine_sysfs():
    base = "/sys/devices/platform/bus@0/13e00000.host1x"
    out = {}
    for name, dev in (("vic", "15340000.vic"), ("nvdec", "15480000.nvdec"), ("nvenc", "154c0000.nvenc")):
        try:
            out[name] = open(f"{base}/{dev}/power/runtime_status").read().strip()
        except OSError:
            out[name] = "?"
    return out


def main():
    cams = [(CpuCam if args.mode == "cpu_native" else Cam)(c) for c in CAMS_RUN]
    tegra = tegra_reader(f"logs/tegra_{TAG}.txt")
    threads = [threading.Thread(target=c.run, daemon=True) for c in cams]
    proc = psutil.Process()
    for th in threads:
        th.start()
    time.sleep(args.warmup)
    for c in cams:
        c.measuring.set()
    cpu0, wall0 = proc.cpu_times(), time.monotonic()
    sys_cpu0 = psutil.cpu_times()
    rss0, fds0 = proc.memory_info().rss, proc.num_fds()
    engines = []
    t_end = wall0 + args.duration
    cpu_samples = []
    prev = (cpu0, wall0)
    while time.monotonic() < t_end:
        time.sleep(min(10, max(0.1, t_end - time.monotonic())))
        now_cpu, now = proc.cpu_times(), time.monotonic()
        busy = (now_cpu.user - prev[0].user) + (now_cpu.system - prev[0].system)
        cpu_samples.append(round(100 * busy / (now - prev[1]), 1))
        engines.append(engine_sysfs())
        for c in cams:
            c.timeline.append((round(now - wall0, 1), c.stats["main_frames"]))
        prev = (now_cpu, now)
    cpu1, wall1 = proc.cpu_times(), time.monotonic()
    sys_cpu1 = psutil.cpu_times()
    rss1, fds1 = proc.memory_info().rss, proc.num_fds()
    for c in cams:
        c.measuring.clear()
    for c in cams:
        c.stop.set()
    for th in threads:
        th.join(timeout=10)
    if tegra:
        tegra.terminate()
    wall = wall1 - wall0
    cpu_s = (cpu1.user - cpu0.user) + (cpu1.system - cpu0.system)
    total_frames = sum(c.stats["main_frames"] for c in cams)
    sys_busy = sum(getattr(sys_cpu1, f) - getattr(sys_cpu0, f) for f in ("user", "system", "irq", "softirq", "nice"))
    sys_all = sum(getattr(sys_cpu1, f) - getattr(sys_cpu0, f) for f in sys_cpu1._fields if f not in ("guest", "guest_nice"))
    result = {
        "tag": TAG, "args": vars(args), "wall_s": round(wall, 1),
        "process_cpu_pct_of_one_core": round(100 * cpu_s / wall, 1),
        "process_cpu_pct_of_machine_6cores": round(100 * cpu_s / wall / os.cpu_count(), 1),
        "process_cpu_samples_10s": cpu_samples,
        "cpu_ms_per_main_frame": round(1000 * cpu_s / max(1, total_frames), 2),
        "system_cpu_busy_pct_of_machine": round(100 * sys_busy / max(1e-9, sys_all), 1),
        "rss_mb_start_end": [round(rss0 / 2**20), round(rss1 / 2**20)], "fds_start_end": [fds0, fds1],
        "engines_runtime_status_samples": engines[:3] + engines[-1:],
        "cams": {},
    }
    for c in cams:
        s = c.stats
        result["cams"][c.cam] = {
            "crop_info": c.crop_info, "fps_main": round(s["main_frames"] / wall, 2),
            "fps_view": round(s["view_frames"] / wall, 2), "stats": {k: v for k, v in s.items() if k != "bus"},
            "bus_messages": sorted(set(s["bus"]))[:6], "bus_count": len(s["bus"]),
            "frame_gap_ms": summ(c.gaps), "gaps_over_300ms": sum(1 for g in c.gaps if g > 300),
            "gaps_over_1s": sum(1 for g in c.gaps if g > 1000),
            "py_ms_main": summ(c.t_main_py), "py_ms_view": summ(c.t_view_py),
            "nvvidconv_ms_main": summ(c.vc_ms["main"]), "nvvidconv_ms_view": summ(c.vc_ms["view"]),
            "decoder_ms": summ(c.dec_ms), "pair_added_ms": summ(c.pair_lat_ms),
            "pts_diff_frames_hist": {str(k): c.pts_diff_frames.count(k) for k in sorted(set(c.pts_diff_frames))},
        }
    json.dump(result, open(f"logs/bench_{TAG}.json", "w"), indent=1, default=str)
    print(json.dumps({k: v for k, v in result.items() if k not in ("args", "cams", "process_cpu_samples_10s", "engines_runtime_status_samples")}))
    for cam, r in result["cams"].items():
        print(cam, {k: r[k] for k in ("fps_main", "fps_view", "frame_gap_ms", "gaps_over_300ms", "gaps_over_1s", "py_ms_main", "py_ms_view")})
        if r["bus_messages"]:
            print("  bus:", r["bus_messages"])

main()
sys.stdout.flush(); sys.stderr.flush(); os._exit(0)

```

### `run_q4_matrix.sh`

```bash
#!/bin/bash
# Q4: estabilidad del tee sobre la salida NVMM del decodificador, dos cámaras a la vez.
# Variantes de queue x consumidor rápido/lento x num-extra-surfaces. 90 s cada una.
cd "$(dirname "$0")"
PY=/home/iea/Documents/sip-volcables/.venv/bin/python
run() { # tag, args...
  tag=$1; shift
  timeout 200 $PY bench.py --mode native --cams 3,4 --duration 90 --warmup 8 --tag "$tag" "$@" >logs/out_$tag.txt 2>logs/err_$tag.txt
  echo "$tag exit=$?" >> logs/q4_matrix.progress
}
: > logs/q4_matrix.progress
for q in leaky1 leaky2 plain2 default; do
  for d in 0 120; do
    run q4_${q}_d${d} --queue $q --delay-ms $d
  done
done
for e in 4 8; do
  for q in leaky1 default; do
    run q4_${q}_d120_x${e} --queue $q --delay-ms 120 --extra $e
  done
done
run q4_main_only_d120 --mode main --delay-ms 120
echo DONE >> logs/q4_matrix.progress

```

### `run_q5_matrix.sh`

```bash
#!/bin/bash
# Q5: apareo por PTS de la rama principal y la vista. Dos cámaras, 90 s cada variante.
cd "$(dirname "$0")"
PY=/home/iea/Documents/sip-volcables/.venv/bin/python
run() { tag=$1; shift
  timeout 200 $PY bench.py --mode native --cams 3,4 --duration 90 --warmup 8 --queue leaky1 --tag "$tag" "$@" >logs/out_$tag.txt 2>logs/err_$tag.txt
  echo "$tag exit=$?" >> logs/q5_matrix.progress; }
: > logs/q5_matrix.progress
for pair in naive robust; do for vm in 1 2; do for d in 0 120; do
  run q5_${pair}_vm${vm}_d${d} --pair $pair --view-max $vm --delay-ms $d
done; done; done
for vm in 1 2; do run q5_naive_vm${vm}_d50 --pair naive --view-max $vm --delay-ms 50; done
echo DONE >> logs/q5_matrix.progress

```

### `run_q8_matrix.sh`

```bash
#!/bin/bash
# Q8: costo con dos cámaras a 15 fps. 120 s por corrida, dos repeticiones intercaladas.
cd "$(dirname "$0")"
PY=/home/iea/Documents/sip-volcables/.venv/bin/python
run() { tag=$1; shift
  timeout 260 $PY bench.py --cams 3,4 --duration 120 --warmup 10 --tag "$tag" "$@" >logs/out_$tag.txt 2>logs/err_$tag.txt
  echo "$tag exit=$?" >> logs/q8_matrix.progress; }
: > logs/q8_matrix.progress
for rep in 1 2; do
  run q8a_main_r$rep            --mode main
  run q8b_native_r$rep          --mode native  --queue leaky1 --pair robust
  run q8c_resized_m2_r$rep      --mode resized --queue leaky1 --pair robust --method 2
  run q8d_cpu_native_r$rep      --mode cpu_native
  run q8e_comp_native_r$rep     --mode comp_native
  run q8f_comp_resized_m2_r$rep --mode comp_resized --method 2
done
run q8g_resized_m1_r1 --mode resized --queue leaky1 --pair robust --method 1
echo DONE >> logs/q8_matrix.progress

```

### `q6_file.py`

```python
"""Q6 (diagnóstico): el mismo lienzo del nvcompositor, pero sobre un clip de archivo, para ver cuántos buffers salen y cómo."""
import sys
from common import *
cam = int(sys.argv[1]); fps = sys.argv[2] if len(sys.argv) > 2 else ""
NW, NH = {3: (1920, 1080), 4: (2560, 1440)}[cam]
l, t, r, b = roi_native(NW, NH); cw, ch = r - l, b - t
Q = "queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0"
fr = f",framerate={fps}/1" if fps else ""
launch = (f"filesrc location=clips/cam_{cam}.h264 ! h264parse ! nvv4l2decoder ! tee name=t "
          f"t. ! {Q} ! nvvidconv ! video/x-raw(memory:NVMM),format=RGBA,width=640,height=360{fr} ! comp.sink_0 "
          f"t. ! {Q} ! nvvidconv left={l} top={t} right={r} bottom={b} ! video/x-raw(memory:NVMM),format=RGBA,width={cw},height={ch}{fr} ! comp.sink_1 "
          f"nvcompositor name=comp sink_0::xpos=0 sink_0::ypos=0 sink_0::width=640 sink_0::height=360 "
          f"sink_1::xpos=640 sink_1::ypos=0 sink_1::width={cw} sink_1::height={ch} "
          f"! video/x-raw(memory:NVMM),format=RGBA,width={640+cw},height={max(360,ch)} ! nvvidconv ! video/x-raw,format=BGRx "
          f"! appsink name=sink emit-signals=true max-buffers=0 drop=false sync=false")
p = build(launch, live=False)
pts, frames = [], []
def cb(sink):
    s = sink.emit("pull-sample"); pts.append(s.get_buffer().pts)
    if len(frames) < 3: frames.append(sample_to_bgr(s))
    return Gst.FlowReturn.OK
p.get_by_name("sink").connect("new-sample", cb)
p.set_state(Gst.State.PLAYING)
m = p.get_bus().timed_pop_filtered(20 * NS, Gst.MessageType.EOS | Gst.MessageType.ERROR)
print("fin:", m.type if m else "timeout", m.parse_error()[0].message if m and m.type == Gst.MessageType.ERROR else "")
shutdown(p)
print(f"cam_{cam} fps={fps or '-'}: canvases={len(pts)} de 122 esperados; PTS ms primeros: {[round(x/1e6) for x in pts[:6]]}")
if frames:
    cv2.imwrite(f"out/q6_file_cam{cam}.png", frames[-1]); print("guardado out/q6_file_cam%d.png" % cam, frames[-1].shape)
sys.stdout.flush(); os._exit(0)

```

### `q6_compositor.py`

```python
"""
Q6: plan B, un solo buffer con la imagen reducida y el recorte lado a lado (nvcompositor).

  python q6_compositor.py --cam 3 --view {native,resized} [--fps 15] [--duration 40] [--method 1] [--latency-ns N]

Un tee del decoder: (A) rama principal sola, que sirve de referencia de latencia; (B) las dos
entradas del nvcompositor, 640x360 a la izquierda y el recorte (nativo o reducido por el VIC)
a la derecha; el lienzo sale por un nvvidconv a BGRx y un appsink. Se compara, por PTS, cuándo
llega el lienzo contra cuándo llega la rama principal.
"""
import argparse, statistics, psutil
from common import *

ap = argparse.ArgumentParser()
ap.add_argument("--cam", type=int, default=3)
ap.add_argument("--view", default="native")
ap.add_argument("--fps", type=int, default=0, help="si > 0, se fija framerate=N/1 en las entradas del compositor")
ap.add_argument("--duration", type=float, default=40)
ap.add_argument("--method", type=int, default=1)
ap.add_argument("--out-fps", type=int, default=0, help="framerate=N/1 en la caps de SALIDA del compositor")
ap.add_argument("--latency-ns", type=int, default=-1)
ap.add_argument("--comp-props", default="", help="propiedades extra del nvcompositor, ej. start-time-selection=first")
ap.add_argument("--url-base", default="rtsp://10.8.51.1:8554")
ap.add_argument("--short-side", type=int, default=224)
args = ap.parse_args()

NW, NH = {3: (1920, 1080), 4: (2560, 1440)}[args.cam]
l, t, r, b = roi_native(NW, NH)
cw, ch = r - l, b - t
if args.view == "resized":
    ow = args.short_side if cw <= ch else round(args.short_side * cw / ch)
    oh = args.short_side if ch < cw else round(args.short_side * ch / cw)
    ow, oh = ow + ow % 2, oh + oh % 2
else:
    ow, oh = cw, ch
CW, CH = 640 + ow, max(360, oh)
fr = f",framerate={args.fps}/1" if args.fps else ""
lat = (f"latency={args.latency_ns} " if args.latency_ns >= 0 else "") + (args.comp_props + " " if args.comp_props else "")
out_fr = f",framerate={args.out_fps}/1" if args.out_fps else ""
launch = (f"rtspsrc location={args.url_base}/cam_{args.cam} latency=0 protocols=tcp tcp-timeout=5000000 "
          f"! parsebin ! nvv4l2decoder name=dec disable-dpb=true ! tee name=t "
          f"t. ! queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0 ! nvvidconv interpolation-method=1 "
          f"! video/x-raw,format=BGRx,width=640,height=360 ! appsink name=ref max-buffers=1 drop=true sync=false "
          f"t. ! queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0 ! nvvidconv interpolation-method=1 "
          f"! video/x-raw(memory:NVMM),format=RGBA,width=640,height=360{fr} ! comp.sink_0 "
          f"t. ! queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0 ! nvvidconv interpolation-method={args.method if args.view == 'resized' else 1} "
          f"left={l} top={t} right={r} bottom={b} "
          f"! video/x-raw(memory:NVMM),format=RGBA,width={ow},height={oh}{fr} ! comp.sink_1 "
          f"nvcompositor name=comp {lat}sink_0::xpos=0 sink_0::ypos=0 sink_0::width=640 sink_0::height=360 "
          f"sink_1::xpos=640 sink_1::ypos=0 sink_1::width={ow} sink_1::height={oh} "
          f"! video/x-raw(memory:NVMM),format=RGBA,width={CW},height={CH}{out_fr} ! nvvidconv ! video/x-raw,format=BGRx "
          f"! appsink name=sink max-buffers=1 drop=true sync=false")
print(f"cam_{args.cam}: nativa {NW}x{NH}, crop {cw}x{ch} -> vista {ow}x{oh}; lienzo {CW}x{CH}; fps de entrada fijado: {args.fps or 'no'}")
p = build(launch)
arr_ref, arr_comp, dec_t = {}, {}, {}
dec_src = p.get_by_name("dec").get_static_pad("src")
def on_dec(pad, info):
    dec_t[info.get_buffer().pts] = time.monotonic()
    if len(dec_t) > 300: dec_t.pop(next(iter(dec_t)))
    return Gst.PadProbeReturn.OK
dec_src.add_probe(Gst.PadProbeType.BUFFER, on_dec)

stats = {"ref": 0, "comp": 0}
match_diff, main_mad, ref_lat, comp_lat, comp_vs_ref, comp_gaps, split_ms, comp_pts_known = [], [], [], [], [], [], [], []
last_comp = [None]
measuring = threading.Event()
sample_frame = {}

import collections
ref_thumbs = collections.deque(maxlen=12)       # (llegada, miniatura 32x18 BGR, frame 640x360 BGR)
def thumb(bgr): return cv2.resize(bgr, (32, 18), interpolation=cv2.INTER_AREA).astype(np.int16)

def on_ref(sink):
    s = sink.emit("pull-sample"); now = time.monotonic(); pts = s.get_buffer().pts
    arr_ref[pts] = now
    if measuring.is_set():
        fr_ = sample_to_bgr(s); ref_thumbs.append((now, thumb(fr_), fr_))
    if measuring.is_set():
        stats["ref"] += 1
        if pts in dec_t: ref_lat.append((now - dec_t[pts]) * 1000)
    if len(arr_ref) > 300: arr_ref.pop(next(iter(arr_ref)))
    return Gst.FlowReturn.OK

def on_comp(sink):
    s = sink.emit("pull-sample"); now = time.monotonic(); pts = s.get_buffer().pts
    t0 = time.perf_counter()
    frame = sample_to_bgr(s)
    main, view = frame[:360, :640], frame[:oh, 640:640 + ow]
    t1 = time.perf_counter()
    if measuring.is_set():
        stats["comp"] += 1
        split_ms.append((t1 - t0) * 1000)
        if last_comp[0] is not None: comp_gaps.append((now - last_comp[0]) * 1000)
        if pts in dec_t:
            comp_lat.append((now - dec_t[pts]) * 1000); comp_pts_known.append(1)
        else:
            comp_pts_known.append(0)
        if ref_thumbs:
            tc = thumb(main)
            best = min(list(ref_thumbs), key=lambda x: np.abs(x[1] - tc).mean())
            d = float(np.abs(best[1] - tc).mean())
            match_diff.append(d)
            if d < 1.5:
                comp_vs_ref.append((now - best[0]) * 1000)
                main_mad.append(float(np.abs(best[2].astype(np.int16) - main.astype(np.int16)).mean()))
        sample_frame.setdefault("f", frame.copy())
    last_comp[0] = now
    return Gst.FlowReturn.OK

for name, cb in (("ref", on_ref), ("sink", on_comp)):
    el = p.get_by_name(name); el.set_property("emit-signals", True); el.connect("new-sample", cb)

proc = psutil.Process()
p.set_state(Gst.State.PLAYING)
time.sleep(8)
measuring.set()
c0, w0 = proc.cpu_times(), time.monotonic()
bus = p.get_bus(); msgs = []
while time.monotonic() - w0 < args.duration:
    m = bus.timed_pop_filtered(500_000_000, Gst.MessageType.ERROR | Gst.MessageType.WARNING)
    if m is not None:
        e = m.parse_error() if m.type == Gst.MessageType.ERROR else m.parse_warning()
        msgs.append(f"{'ERROR' if m.type == Gst.MessageType.ERROR else 'WARN'}: {e[0].message}")
        if m.type == Gst.MessageType.ERROR: break
wall = time.monotonic() - w0
c1 = proc.cpu_times(); measuring.clear()
shutdown(p)
def s(xs): return None if not xs else {"n": len(xs), "mean": round(statistics.fmean(xs), 1), "p50": round(sorted(xs)[len(xs)//2], 1), "p95": round(sorted(xs)[int(len(xs)*.95)], 1), "max": round(max(xs), 1)}
print(f"fps rama principal: {stats['ref']/wall:.2f}; fps lienzo: {stats['comp']/wall:.2f}; mensajes del bus: {sorted(set(msgs))[:5]}")
print(f"CPU del proceso: {100*((c1.user-c0.user)+(c1.system-c0.system))/wall:.1f} % de un núcleo")
print(f"lienzo: PTS del lienzo coincide con el del decoder en {sum(comp_pts_known)}/{len(comp_pts_known)} frames")
print(f"latencia decoder->appsink, rama principal (ms): {s(ref_lat)}")
print(f"latencia decoder->appsink, lienzo (ms):         {s(comp_lat)}")
print(f"lienzo menos rama principal, apareados por contenido (ms; + = el lienzo llega después): {s(comp_vs_ref)} ({len(comp_vs_ref)}/{len(match_diff)} apareados)")
print(f"parte principal del lienzo vs la rama principal: MAD medio {statistics.fmean(main_mad) if main_mad else None}")
print(f"hueco entre lienzos (ms): {s(comp_gaps)}; >300 ms: {sum(1 for g in comp_gaps if g > 300)}; >1 s: {sum(1 for g in comp_gaps if g > 1000)}")
print(f"separar el lienzo en Python (ms): {s(split_ms)}")
if "f" in sample_frame:
    cv2.imwrite(f"out/q6_cam{args.cam}_{args.view}.png", sample_frame["f"])
    print(f"lienzo guardado en out/q6_cam{args.cam}_{args.view}.png")
sys.stdout.flush(); os._exit(0)

```

### `q7_downscale_quality.py`

```python
"""
Q7: calidad de reducir el recorte nativo a 224 de lado corto.

Referencia: recorte nativo (numpy) -> cv2.resize INTER_AREA, que es lo que hace el clasificador
(`prepare_center_crop`). Contra ella: nvvidconv con interpolation-method 0..5 haciendo el recorte
y la reducción en un solo paso, y el camino de hoy (VIC a 640x360, recorte del ROI de ahí,
cv2.resize bilineal hacia arriba).

Corre sobre clips/cam_N.h264 (frames reproducibles) y sobre una zone plate sintética (aliasing).
"""
import sys
from common import *

N_FRAMES = 10
SHORT = 224
METHODS = {0: "nearest", 1: "bilinear", 2: "5-tap", 3: "10-tap", 4: "smart", 5: "nicest"}


def decode_frames(cam, tail, n=N_FRAMES, skip=20):
    """Primeros n frames (tras `skip`) de cam_N.h264 pasados por `tail`, como BGR; y ms/frame del nvvidconv."""
    launch = (f"filesrc location=clips/cam_{cam}.h264 ! h264parse ! nvv4l2decoder ! nvvidconv name=vc {tail} "
              f"! appsink name=sink max-buffers=0 drop=false sync=false emit-signals=true")
    p = build(launch, live=False)
    frames, t_in, ms = [], {}, []
    vc = p.get_by_name("vc")
    def on_in(pad, info):
        t_in[len(t_in)] = time.monotonic(); return Gst.PadProbeReturn.OK
    def on_out(pad, info):
        i = len(ms); t0 = t_in.get(i)
        if t0 is not None: ms.append((time.monotonic() - t0) * 1000)
        return Gst.PadProbeReturn.OK
    vc.get_static_pad("sink").add_probe(Gst.PadProbeType.BUFFER, on_in)
    vc.get_static_pad("src").add_probe(Gst.PadProbeType.BUFFER, on_out)
    count = {"n": 0}
    def cb(sink):
        s = sink.emit("pull-sample"); i = count["n"]; count["n"] += 1
        if skip <= i < skip + n:
            frames.append(sample_to_bgr(s))
        return Gst.FlowReturn.OK
    p.get_by_name("sink").connect("new-sample", cb)
    p.set_state(Gst.State.PLAYING)
    p.get_bus().timed_pop_filtered(30 * NS, Gst.MessageType.EOS | Gst.MessageType.ERROR)
    shutdown(p)
    return frames, ms[skip:skip + n]


def lap_var(img):
    return float(cv2.Laplacian(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())


def mad(a, b):
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())


def label(img, text):
    out = img.copy()
    cv2.putText(out, text, (4, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1, cv2.LINE_AA)
    return out


results = {}
for cam, (W, H) in ((3, (1920, 1080)), (4, (2560, 1440))):
    l, t, r, b = roi_native(W, H)
    cw, ch = r - l, b - t
    ow = SHORT if cw <= ch else round(SHORT * cw / ch)
    oh = SHORT if ch < cw else round(SHORT * ch / cw)
    ow, oh = ow + ow % 2, oh + oh % 2
    print(f"\ncam_{cam}: nativa {W}x{H}, crop {cw}x{ch} -> salida {ow}x{oh} (reducción x{cw/ow:.2f} en X, x{ch/oh:.2f} en Y)")
    native, _ = decode_frames(cam, "! video/x-raw,format=BGRx")
    ref = [cv2.resize(f[t:b, l:r], (ow, oh), interpolation=cv2.INTER_AREA) for f in native]
    ref_lap = np.mean([lap_var(x) for x in ref])
    rows = {}
    # VIC: recorte + reducción en un solo paso, por método
    outs = {}
    for m, name in METHODS.items():
        frames, ms = decode_frames(cam, f"interpolation-method={m} left={l} top={t} right={r} bottom={b} "
                                   f"! video/x-raw,format=BGRx,width={ow},height={oh}")
        outs[m] = frames
        rows[f"vic {m} {name}"] = (np.mean([mad(a, c) for a, c in zip(frames, ref)]),
                                   np.mean([lap_var(x) for x in frames]) / ref_lap, np.mean(ms))
    # VIC recorta a tamaño nativo y OpenCV reduce (control: debe dar ~ la referencia)
    nat_view, ms = decode_frames(cam, f"left={l} top={t} right={r} bottom={b} ! video/x-raw,format=BGRx,width={cw},height={ch}")
    two_step = [cv2.resize(f, (ow, oh), interpolation=cv2.INTER_AREA) for f in nat_view]
    rows["VIC nativo + cv2 INTER_AREA"] = (np.mean([mad(a, c) for a, c in zip(two_step, ref)]),
                                           np.mean([lap_var(x) for x in two_step]) / ref_lap, np.mean(ms))
    # el camino de hoy: 640x360 -> ROI -> cv2.resize hacia arriba
    small, ms = decode_frames(cam, "interpolation-method=1 ! video/x-raw,format=BGRx,width=640,height=360")
    x, y, w, h = ROI_640
    today = [cv2.resize(f[y:y + h, x:x + w], (ow, oh), interpolation=cv2.INTER_LINEAR) for f in small]
    rows["hoy: 640x360 -> ROI -> resize"] = (np.mean([mad(a, c) for a, c in zip(today, ref)]),
                                            np.mean([lap_var(x) for x in today]) / ref_lap, np.mean(ms))
    print(f"  {'camino':34s} {'MAD vs INTER_AREA':>18s} {'nitidez (Laplaciano) / ref':>28s} {'ms/frame nvvidconv':>20s}")
    for k, (d, s, ms_) in rows.items():
        print(f"  {k:34s} {d:18.3f} {s:28.3f} {ms_:20.2f}")
    results[cam] = rows
    # imagen lado a lado (frame 5), con el zoom x2 en vecino más cercano para ver el aliasing
    i = 5
    tiles = [label(ref[i], "cv2 INTER_AREA (ref)")] + [label(outs[m][i], f"VIC {m} {n}") for m, n in METHODS.items()] \
            + [label(today[i], "hoy: 640x360->ROI")]
    sheet = np.hstack([cv2.resize(x, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST) for x in tiles[:4]])
    sheet2 = np.hstack([cv2.resize(x, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST) for x in tiles[4:]] +
                       [np.zeros_like(cv2.resize(tiles[0], None, fx=2, fy=2))] * (4 - len(tiles[4:])))
    os.makedirs("out", exist_ok=True)
    cv2.imwrite(f"out/q7_cam{cam}_sidebyside.png", np.vstack([sheet, sheet2]))
    # diferencia amplificada contra la referencia
    diffs = [label(np.clip(np.abs(outs[m][i].astype(np.int16) - ref[i].astype(np.int16)) * 8, 0, 255).astype(np.uint8), f"|VIC {m}-ref| x8") for m in METHODS]
    cv2.imwrite(f"out/q7_cam{cam}_diffx8.png", np.hstack([cv2.resize(x, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST) for x in diffs[:3]]) if False else np.vstack([
        np.hstack([cv2.resize(x, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST) for x in diffs[:3]]),
        np.hstack([cv2.resize(x, None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST) for x in diffs[3:]])]))

# ── zone plate: aliasing sin ambigüedad ────────────────────────────────────
print("\nzone plate sintética 720x880 -> 224x274 (cuanto menos se parezca a una reducción antialiasada, más aliasing)")
cw, ch, ow, oh = 720, 880, 224, 274
def zone_plate_frame():
    p = build(f"videotestsrc pattern=zone-plate num-buffers=1 kx2=20 ky2=20 kt=0 ! video/x-raw,format=BGRx,width={cw},height={ch} ! appsink name=sink", live=False)
    p.set_state(Gst.State.PLAYING)
    s = p.get_by_name("sink").emit("pull-sample"); f = sample_to_bgr(s); shutdown(p); return f
zp = zone_plate_frame()
zref = cv2.resize(zp, (ow, oh), interpolation=cv2.INTER_AREA)
tiles = [label(zref, "cv2 INTER_AREA")]
for m, name in METHODS.items():
    p = build(f"videotestsrc pattern=zone-plate num-buffers=1 kx2=20 ky2=20 kt=0 ! video/x-raw,format=BGRx,width={cw},height={ch} "
              f"! nvvidconv interpolation-method={m} ! video/x-raw,format=BGRx,width={ow},height={oh} ! appsink name=sink", live=False)
    p.set_state(Gst.State.PLAYING)
    s = p.get_by_name("sink").emit("pull-sample"); out = sample_to_bgr(s); shutdown(p)
    # energía fuera de banda: lo que hay en las altas frecuencias de la reducción (el aliasing pliega ahí lo que sobra)
    print(f"  VIC {m} {name:9s}: MAD vs INTER_AREA = {mad(out, zref):6.2f}   varianza = {out.var():8.1f} (ref {zref.var():.1f})")
    tiles.append(label(out, f"VIC {m} {name}"))
cv2.imwrite("out/q7_zoneplate.png", np.hstack(tiles[:4]) if False else np.vstack([np.hstack(tiles[:4]), np.hstack(tiles[4:] + [np.zeros_like(tiles[0])])]))
print("imágenes en out/q7_*.png")

```

### `q9_geometry.py`

```python
"""
Q9a: recortes de tamaño impar o fuera de rango en nvvidconv, contra numpy (cam_3 nativa 1920x1080).
¿Hay relleno al final de cada fila (row_bytes != ancho*4)? ¿El contenido coincide? ¿Qué falla y cómo?
"""
import sys
from common import *

CAM = 3
W, H = 1920, 1080

def case(name, crop, caps_size="auto", frames=3):
    l, t, r, b = crop
    if caps_size == "auto":
        caps = f",width={r - l},height={b - t}"
    elif caps_size is None:
        caps = ""
    else:
        caps = f",width={caps_size[0]},height={caps_size[1]}"
    launch = (f"filesrc location=clips/cam_{CAM}.h264 ! h264parse ! nvv4l2decoder ! tee name=t "
              f"t. ! queue ! nvvidconv ! video/x-raw,format=BGRx ! appsink name=full emit-signals=true max-buffers=0 drop=false sync=false "
              f"t. ! queue ! nvvidconv left={l} top={t} right={r} bottom={b} ! video/x-raw,format=BGRx{caps} ! "
              f"appsink name=view emit-signals=true max-buffers=0 drop=false sync=false")
    try:
        p = build(launch, live=False)
    except GLib.Error as e:
        return f"{name:34s} crop={crop} caps={caps_size}: no se arma el pipeline: {e.message}"
    full, view, info = [], [], {}
    def on(lst, key):
        def cb(sink):
            s = sink.emit("pull-sample")
            if len(lst) < frames:
                lst.append(sample_to_bgr(s))
                if key == "view" and "row" not in info:
                    info["row"] = sample_row_bytes(s)
                    info["size"] = s.get_buffer().get_size()
            return Gst.FlowReturn.OK
        return cb
    p.get_by_name("full").connect("new-sample", on(full, "full"))
    p.get_by_name("view").connect("new-sample", on(view, "view"))
    p.set_state(Gst.State.PLAYING)
    bus = p.get_bus()
    deadline = time.time() + 15
    err = ""
    while time.time() < deadline and (len(full) < frames or len(view) < frames):
        m = bus.timed_pop_filtered(100_000_000, Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if m is not None:
            if m.type == Gst.MessageType.ERROR:
                e = m.parse_error(); err = f"ERROR bus: {e[0].message} [{e[1]}]"
            break
    shutdown(p)
    if not view:
        return f"{name:34s} crop={crop} caps={caps_size}: SIN FRAMES en la vista. {err}"
    row, w, h = info["row"]
    padded = f"relleno {row - w * 4} B/fila" if row != w * 4 else "sin relleno"
    v, f = view[0], full[0]
    ref = f[t:b, l:r]
    cmp_txt = (f"MAD vs numpy={np.abs(ref.astype(np.int16) - v.astype(np.int16)).mean():.4f}"
               if ref.shape == v.shape else f"tamaño numpy {ref.shape[1]}x{ref.shape[0]} != vista {v.shape[1]}x{v.shape[0]}")
    return (f"{name:34s} crop={crop} caps={caps_size}: vista {w}x{h}, buffer {info['size']} B, "
            f"row_bytes={row} ({padded}); {cmp_txt}. {err}")

base = (630, 120, 1170, 780)
tests = [
    ("par, alineado (referencia)", base),
    ("ancho impar 181", (630, 120, 811, 780)),
    ("alto impar 661", (630, 120, 1170, 781)),
    ("left/top impares", (631, 121, 1170, 780)),
    ("todo impar 181x221", (631, 121, 812, 342)),
    ("ancho 33", (630, 120, 663, 780)),
    ("ancho 3 x 3", (630, 120, 633, 123)),
    ("2 x 2", (630, 120, 632, 122)),
    ("1 x 1", (630, 120, 631, 121)),
    ("right > ancho nativo", (630, 120, 2000, 780)),
    ("bottom > alto nativo", (630, 120, 1170, 1200)),
    ("right <= left", (700, 120, 600, 780)),
    ("crop nativo completo 0,0,W,H", (0, 0, W, H)),
    ("right=bottom=0 (sin crop)", (0, 0, 0, 0)),
]
for name, crop in tests:
    print(case(name, crop), flush=True)
print(case("caps de otro tamaño que el crop", base, (541, 661)), flush=True)
print(case("caps con ancho impar sin crop impar", base, (539, 660)), flush=True)

```

### `q9_align.py`

```python
"""
Q9a (bis): ¿qué alineación necesita el crop para ser exacto, y cuál es el tamaño mínimo que no mata el pipeline?
Cada caso corre en el mismo proceso; un error fatal del bus se informa y se sigue con el siguiente.
"""
import sys
from common import *
W, H = 1920, 1080

def run(crop, frames=3):
    l, t, r, b = crop
    launch = (f"filesrc location=clips/cam_3.h264 ! h264parse ! nvv4l2decoder ! tee name=t "
              f"t. ! queue ! nvvidconv ! video/x-raw,format=BGRx ! appsink name=full emit-signals=true max-buffers=0 drop=false sync=false "
              f"t. ! queue ! nvvidconv left={l} top={t} right={r} bottom={b} ! video/x-raw,format=BGRx,width={r-l},height={b-t} ! "
              f"appsink name=view emit-signals=true max-buffers=0 drop=false sync=false")
    p = build(launch, live=False)
    full, view = [], []
    for name, lst in (("full", full), ("view", view)):
        def cb(sink, lst=lst):
            s = sink.emit("pull-sample")
            if len(lst) < frames: lst.append(sample_to_bgr(s))
            return Gst.FlowReturn.OK
        p.get_by_name(name).connect("new-sample", cb)
    p.set_state(Gst.State.PLAYING)
    bus = p.get_bus(); err = ""; t0 = time.time()
    while time.time() - t0 < 10 and (len(full) < frames or len(view) < frames):
        m = bus.timed_pop_filtered(100_000_000, Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if m is not None:
            if m.type == Gst.MessageType.ERROR: err = "ERROR FATAL"
            break
    shutdown(p)
    if not view: return None, err
    ref = full[0][t:b, l:r]
    return (float(np.abs(ref.astype(np.int16) - view[0].astype(np.int16)).mean()) if ref.shape == view[0].shape else -1), err

print("alineación de left/top/right/bottom (tamaño 540x660 fijo; se desplaza el origen)")
for off in (0, 1, 2, 3, 4, 6, 8, 16):
    d, e = run((630 + off, 120 + off, 1170 + off, 780 + off))
    print(f"  origen desplazado +{off:2d} (l={630+off}, t={120+off}): MAD={d if d is None else round(d, 4)} {e}")
print("solo un lado impar a la vez")
for name, c in (("left impar", (631, 120, 1170, 780)), ("top impar", (630, 121, 1170, 780)),
                ("right impar", (630, 120, 1171, 780)), ("bottom impar", (630, 120, 1170, 781))):
    d, e = run(c); print(f"  {name:13s} {c}: MAD={d if d is None else round(d, 4)} {e}")
print("tamaño mínimo (cuadrado, origen par)")
for s in (1, 2, 3, 4, 6, 8, 10, 12, 16, 32):
    d, e = run((630, 120, 630 + s, 120 + s)); print(f"  {s:3d}x{s:<3d}: MAD={d if d is None else round(d, 4)} {e}")
sys.stdout.flush(); os._exit(0)

```

### `tcp_proxy.py`

```python
"""Proxy TCP con tres modos, para simular que una cámara RTSP se cae sin tocar el servidor real.
   pass       reenvía
   reset      cierra las conexiones con RST y rechaza las nuevas (servidor caído)
   blackhole  sigue conectado pero no pasa un byte (cable cortado / red congelada)"""
import socket, struct, threading, time

class Proxy:
    def __init__(self, listen_port, target):
        self.mode = "pass"
        self.target = target
        self.conns = []
        self.lock = threading.Lock()
        self.srv = socket.socket(); self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", listen_port)); self.srv.listen(16)
        threading.Thread(target=self._accept, daemon=True).start()

    def set_mode(self, mode):
        self.mode = mode
        if mode == "reset":
            self._kill_all()

    def _kill_all(self):
        with self.lock:
            conns, self.conns = self.conns, []
        for s in conns:
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                s.close()
            except OSError:
                pass

    def _accept(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            if self.mode == "reset":
                c.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)); c.close(); continue
            try:
                u = socket.create_connection(self.target, timeout=5)
            except OSError:
                c.close(); continue
            with self.lock:
                self.conns += [c, u]
            threading.Thread(target=self._pump, args=(c, u), daemon=True).start()
            threading.Thread(target=self._pump, args=(u, c), daemon=True).start()

    def _pump(self, a, b):
        try:
            while True:
                d = a.recv(65536)
                if not d:
                    break
                if self.mode == "blackhole":
                    continue
                b.sendall(d)
        except OSError:
            pass
        for s in (a, b):
            try: s.close()
            except OSError: pass

```

### `q9_reconnect.py`

```python
"""
Q9b: la caída y la reapertura del stream con el pipeline de dos ramas (tee).

  open_loop   N ciclos build -> PLAYING -> primer frame de cada rama -> NULL, midiendo el tiempo,
              los fds y la memoria; y un NULL con muestras sin retirar en los appsinks
  reset       el servidor se cae (RST) y vuelve: qué ve el consumidor, qué dice el bus, ¿rtspsrc se recupera solo?
  blackhole   la red se congela con la conexión abierta: ¿cuánto tarda en notarse y cómo?
Cámara 3 por un proxy local (127.0.0.1:18554) que se puede cortar.
"""
import sys, psutil
from common import *
from tcp_proxy import Proxy

PORT = 18554
proxy = Proxy(PORT, ("10.8.51.1", 8554))
CAM = 3
proc = psutil.Process()

def make(timeout_us=5_000_000):
    launch = (f"rtspsrc name=source location=rtsp://127.0.0.1:{PORT}/cam_{CAM} latency=0 protocols=tcp tcp-timeout={timeout_us} "
              f"! parsebin ! nvv4l2decoder name=dec disable-dpb=true ! tee name=t "
              f"t. ! queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0 ! nvvidconv interpolation-method=1 "
              f"! video/x-raw,format=BGRx,width=640,height=360 ! appsink name=sink max-buffers=1 drop=true sync=false "
              f"t. ! queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0 ! nvvidconv name=vcv interpolation-method=1 "
              f"! capsfilter name=cf caps=video/x-raw,format=BGRx ! appsink name=view max-buffers=1 drop=true sync=false")
    p = build(launch)
    def on_caps(pad, info):
        ev = info.get_event()
        if ev.type == Gst.EventType.CAPS:
            st = ev.parse_caps().get_structure(0)
            l, t, r, b = roi_native(st.get_value("width"), st.get_value("height"))
            vc = p.get_by_name("vcv")
            for k, v in zip(("left", "top", "right", "bottom"), (l, t, r, b)):
                vc.set_property(k, v)
            p.get_by_name("cf").set_property("caps", Gst.Caps.from_string(f"video/x-raw,format=BGRx,width={r-l},height={b-t}"))
        return Gst.PadProbeReturn.OK
    p.get_by_name("dec").get_static_pad("src").add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM, on_caps)
    return p

def bus_msgs(p):
    out = []
    bus = p.get_bus()
    while True:
        m = bus.pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.WARNING | Gst.MessageType.EOS)
        if m is None:
            return out
        if m.type == Gst.MessageType.EOS:
            out.append("EOS")
        else:
            e = m.parse_error() if m.type == Gst.MessageType.ERROR else m.parse_warning()
            out.append(("ERROR" if m.type == Gst.MessageType.ERROR else "WARN") + f": {e[0].message}")

def open_loop(n=15):
    print(f"== open_loop: {n} ciclos build -> PLAYING -> 1er frame de cada rama -> NULL")
    rows = []
    fds0, rss0, thr0 = proc.num_fds(), proc.memory_info().rss, proc.num_threads()
    for i in range(n):
        t0 = time.monotonic()
        p = make(); p.set_state(Gst.State.PLAYING)
        m = p.get_by_name("sink").emit("try-pull-sample", 8 * NS); t_m = time.monotonic() - t0
        v = p.get_by_name("view").emit("try-pull-sample", 8 * NS); t_v = time.monotonic() - t0
        vs = None if v is None else v.get_caps().get_structure(0)
        if i % 2 == 1:
            time.sleep(0.5)            # que las ramas acumulen muestras sin retirar antes de cerrar
        t1 = time.monotonic(); p.set_state(Gst.State.NULL); t_null = time.monotonic() - t1
        rows.append((t_m, t_v, t_null, None if vs is None else (vs.get_value("width"), vs.get_value("height"))))
    fds1, rss1, thr1 = proc.num_fds(), proc.memory_info().rss, proc.num_threads()
    tm, tv, tn = [r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows]
    print(f"   1er frame principal: mediana {statistics.median(tm):.2f} s (min {min(tm):.2f}, max {max(tm):.2f}); "
          f"1er frame de la vista: mediana {statistics.median(tv):.2f} s (max {max(tv):.2f}); "
          f"set_state(NULL): mediana {statistics.median(tn)*1000:.0f} ms, max {max(tn)*1000:.0f} ms")
    print(f"   tamaños de la vista: {sorted(set(r[3] for r in rows), key=str)}; fallos (sin frame): {sum(1 for r in rows if r[3] is None)}")
    print(f"   fds {fds0}->{fds1}; hilos {thr0}->{thr1}; RSS {rss0/2**20:.0f}->{rss1/2**20:.0f} MB")

def watch(p, seconds, label):
    """Consume las dos ramas y anota cuándo dejan de llegar frames y qué dice el bus."""
    t0 = time.monotonic(); last = t0; events = []; n = 0; resumed = None; gap_started = None
    while time.monotonic() - t0 < seconds:
        m = p.get_by_name("sink").emit("try-pull-sample", int(0.25 * NS))
        v = p.get_by_name("view").emit("try-pull-sample", 0)
        now = time.monotonic()
        if m is not None:
            if gap_started is not None and now - gap_started > 0.5:
                events.append(f"+{now - t0:5.1f}s frames de vuelta tras {now - gap_started:.1f} s de hueco")
            gap_started = None; last = now; n += 1
        elif gap_started is None:
            gap_started = last
        for msg in bus_msgs(p):
            events.append(f"+{now - t0:5.1f}s bus {msg}")
    return n, events

def scenario(kind, drop_at=8, restore_at=18, total=34):
    print(f"\n== {kind}: se corta a los {drop_at} s, se restablece a los {restore_at} s (el mismo pipeline sigue vivo)")
    proxy.set_mode("pass")
    p = make(timeout_us=5_000_000); p.set_state(Gst.State.PLAYING)
    t0 = time.monotonic(); events = []
    def timeline():
        time.sleep(drop_at); events.append(f"+{time.monotonic() - t0:5.1f}s [acción] proxy -> {kind}"); proxy.set_mode(kind)
        time.sleep(restore_at - drop_at); events.append(f"+{time.monotonic() - t0:5.1f}s [acción] proxy -> pass"); proxy.set_mode("pass")
    threading.Thread(target=timeline, daemon=True).start()
    n, ev = watch(p, total, kind)
    for e in sorted(events + ev):
        print("  ", e)
    print(f"   frames principales recibidos: {n}; estado final del pipeline: {p.get_state(0)[1].value_nick}")
    t1 = time.monotonic(); p.set_state(Gst.State.NULL); print(f"   set_state(NULL) tras el corte: {(time.monotonic() - t1) * 1000:.0f} ms")
    return p

def rebuild_after_drop(kind):
    print(f"\n== reconstruir tras {kind}: lo que hace el driver (abrir de nuevo y esperar el primer frame)")
    proxy.set_mode("pass")
    p = make(timeout_us=5_000_000); p.set_state(Gst.State.PLAYING)
    assert p.get_by_name("sink").emit("try-pull-sample", 8 * NS) is not None
    proxy.set_mode(kind)
    t0 = time.monotonic()
    miss = 0
    while time.monotonic() - t0 < 12:
        if p.get_by_name("sink").emit("try-pull-sample", int(0.25 * NS)) is None:
            miss += 1
        msgs = bus_msgs(p)
        if msgs:
            print(f"   +{time.monotonic() - t0:4.1f}s bus: {msgs[0]}"); break
    t1 = time.monotonic(); p.set_state(Gst.State.NULL); print(f"   set_state(NULL): {(time.monotonic() - t1) * 1000:.0f} ms")
    proxy.set_mode("pass")
    t2 = time.monotonic(); q = make(); q.set_state(Gst.State.PLAYING)
    m = q.get_by_name("sink").emit("try-pull-sample", 8 * NS); v = q.get_by_name("view").emit("try-pull-sample", 8 * NS)
    print(f"   reabierto: principal={'ok' if m else 'NO'} vista={'ok' if v else 'NO'} en {time.monotonic() - t2:.2f} s")
    q.set_state(Gst.State.NULL)

import statistics
which = sys.argv[1:] or ["open_loop", "reset", "blackhole", "rebuild"]
if "open_loop" in which: open_loop()
if "reset" in which: scenario("reset")
if "blackhole" in which: scenario("blackhole", total=40)
if "rebuild" in which:
    rebuild_after_drop("reset"); rebuild_after_drop("blackhole")
sys.stdout.flush(); os._exit(0)

```

### `q9_leak.py`

```python
"""
Q9b (bis): ¿fuga de fds / hilos / memoria al reabrir? Variantes, cada una en su proceso:
  main      el pipeline de hoy (una rama, sin probes)
  tee       las dos ramas con los probes del banco, sin liberar la referencia
  tee_gc    lo mismo, con `del p; gc.collect()` tras cada NULL
  main_gc   el de hoy, con gc
"""
import sys, gc, psutil
from common import *
variant, n = sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 12
proc = psutil.Process()
MAIN = "nvvidconv interpolation-method=1 ! video/x-raw,format=BGRx,width=640,height=360 ! appsink name=sink max-buffers=1 drop=true sync=false"
Qs = "queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0"
HEAD = "rtspsrc location=rtsp://10.8.51.1:8554/cam_3 latency=0 protocols=tcp tcp-timeout=5000000 ! parsebin ! nvv4l2decoder name=dec disable-dpb=true "

def make():
    if variant.startswith("main"):
        return build(HEAD + "! " + MAIN)
    p = build(HEAD + f"! tee name=t t. ! {Qs} ! {MAIN} t. ! {Qs} ! nvvidconv name=vcv ! capsfilter name=cf caps=video/x-raw,format=BGRx ! appsink name=view max-buffers=1 drop=true sync=false")
    def on_caps(pad, info):
        ev = info.get_event()
        if ev.type == Gst.EventType.CAPS:
            st = ev.parse_caps().get_structure(0)
            l, t, r, b = roi_native(st.get_value("width"), st.get_value("height"))
            for k, v in zip(("left", "top", "right", "bottom"), (l, t, r, b)): p.get_by_name("vcv").set_property(k, v)
            p.get_by_name("cf").set_property("caps", Gst.Caps.from_string(f"video/x-raw,format=BGRx,width={r-l},height={b-t}"))
        return Gst.PadProbeReturn.OK
    p.get_by_name("dec").get_static_pad("src").add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM, on_caps)
    return p

rows = []
for i in range(n):
    p = make(); p.set_state(Gst.State.PLAYING)
    p.get_by_name("sink").emit("try-pull-sample", 8 * NS)
    p.set_state(Gst.State.NULL)
    if variant.endswith("_gc"):
        del p; gc.collect()
    time.sleep(0.3)
    rows.append((proc.num_fds(), proc.num_threads(), round(proc.memory_info().rss / 2**20)))
print(f"{variant}: (fds, hilos, RSS MB) tras cada ciclo: {rows[0]} {rows[1]} {rows[2]} ... {rows[-2]} {rows[-1]}")
sys.stdout.flush(); os._exit(0)

```

### `q9_leak2.py`

```python
"""Q9b (ter): qué fds se acumulan al reabrir, y qué parte del pipeline de dos ramas los retiene.
Variantes de la segunda rama:  full (crop+caps+appsink), nocrop (nvvidconv plano), fake (fakesink), none (tee con una sola rama)."""
import sys, collections
from common import *
variant, n = sys.argv[1], 8
Qs = "queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0"
MAIN = "nvvidconv interpolation-method=1 ! video/x-raw,format=BGRx,width=640,height=360 ! appsink name=sink max-buffers=1 drop=true sync=false"
HEAD = "rtspsrc location=rtsp://10.8.51.1:8554/cam_3 latency=0 protocols=tcp tcp-timeout=5000000 ! parsebin ! nvv4l2decoder disable-dpb=true ! tee name=t "
SECOND = {
    "full": f"t. ! {Qs} ! nvvidconv left=630 top=120 right=1170 bottom=780 ! video/x-raw,format=BGRx,width=540,height=660 ! appsink name=view max-buffers=1 drop=true sync=false",
    "nocrop": f"t. ! {Qs} ! nvvidconv ! video/x-raw,format=BGRx,width=540,height=660 ! appsink name=view max-buffers=1 drop=true sync=false",
    "fake": f"t. ! {Qs} ! fakesink sync=false",
    "none": "",
    "nvmm_sink": f"t. ! {Qs} ! appsink name=view max-buffers=1 drop=true sync=false",
}[variant]
def kinds():
    c = collections.Counter()
    for f in os.listdir("/proc/self/fd"):
        try: t = os.readlink(f"/proc/self/fd/{f}")
        except OSError: continue
        c[("socket" if t.startswith("socket") else "pipe" if t.startswith("pipe") else "anon_inode:" + t.split(":")[1].strip("[]") if t.startswith("anon_inode") else t)] += 1
    return c
base = None; hist = []
for i in range(n):
    p = build(HEAD + f"t. ! {Qs} ! {MAIN} {SECOND}")
    p.set_state(Gst.State.PLAYING); p.get_by_name("sink").emit("try-pull-sample", 8 * NS)
    p.set_state(Gst.State.NULL); del p; time.sleep(0.3)
    k = kinds(); hist.append(sum(k.values()))
    if i == 1: base = k
last = kinds()
grew = {k: last[k] - base.get(k, 0) for k in last if last[k] != base.get(k, 0)}
print(f"{variant:10s} fds por ciclo: {hist}; crecimiento entre el ciclo 2 y el {n}: {grew}")
sys.stdout.flush(); os._exit(0)

```

### `q9_leak3.py`

```python
"""Q9b (quater): el probe de CAPS que fija el crop, ¿fuga según qué captura su closure?
  strong   el closure captura el pipeline (p.get_by_name dentro del probe)
  elements el closure captura sólo nvvidconv y capsfilter
  weak     el closure captura weakrefs a nvvidconv y capsfilter
"""
import sys, weakref
from common import *
variant, n = sys.argv[1], 10
Qs = "queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0"
LAUNCH = ("rtspsrc location=rtsp://10.8.51.1:8554/cam_3 latency=0 protocols=tcp tcp-timeout=5000000 ! parsebin ! nvv4l2decoder name=dec disable-dpb=true ! tee name=t "
          f"t. ! {Qs} ! nvvidconv interpolation-method=1 ! video/x-raw,format=BGRx,width=640,height=360 ! appsink name=sink max-buffers=1 drop=true sync=false "
          f"t. ! {Qs} ! nvvidconv name=vcv ! capsfilter name=cf caps=video/x-raw,format=BGRx ! appsink name=view max-buffers=1 drop=true sync=false")

def apply(vc, cf, w, h):
    l, t, r, b = roi_native(w, h)
    for k, v in zip(("left", "top", "right", "bottom"), (l, t, r, b)): vc.set_property(k, v)
    cf.set_property("caps", Gst.Caps.from_string(f"video/x-raw,format=BGRx,width={r-l},height={b-t}"))

def make():
    p = build(LAUNCH)
    if variant == "strong":
        def probe(pad, info):
            ev = info.get_event()
            if ev.type == Gst.EventType.CAPS:
                st = ev.parse_caps().get_structure(0)
                apply(p.get_by_name("vcv"), p.get_by_name("cf"), st.get_value("width"), st.get_value("height"))
            return Gst.PadProbeReturn.OK
    elif variant == "elements":
        vc, cf = p.get_by_name("vcv"), p.get_by_name("cf")
        def probe(pad, info):
            ev = info.get_event()
            if ev.type == Gst.EventType.CAPS:
                st = ev.parse_caps().get_structure(0)
                apply(vc, cf, st.get_value("width"), st.get_value("height"))
            return Gst.PadProbeReturn.OK
    else:
        vc, cf = weakref.ref(p.get_by_name("vcv")), weakref.ref(p.get_by_name("cf"))
        def probe(pad, info):
            ev = info.get_event()
            if ev.type == Gst.EventType.CAPS and vc() is not None and cf() is not None:
                st = ev.parse_caps().get_structure(0)
                apply(vc(), cf(), st.get_value("width"), st.get_value("height"))
            return Gst.PadProbeReturn.OK
    p.get_by_name("dec").get_static_pad("src").add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM, probe)
    return p

hist = []
for i in range(n):
    p = make(); p.set_state(Gst.State.PLAYING)
    ok = p.get_by_name("view").emit("try-pull-sample", 8 * NS) is not None
    p.set_state(Gst.State.NULL); del p; time.sleep(0.3)
    hist.append(len(os.listdir("/proc/self/fd")) if ok else -1)
print(f"{variant:9s} fds tras cada ciclo: {hist}")
sys.stdout.flush(); os._exit(0)

```

### `make_tables.py`

```python
"""Genera las tablas markdown del documento de hallazgos a partir de logs/bench_*.json."""
import json, glob, statistics as st

def load(tag): return json.load(open(f"logs/bench_{tag}.json"))

print("### Q4 matriz de estabilidad (2 cámaras, 90 s)")
print("| variante | CPU proceso | fps c3 (princ/vista) | fps c4 | huecos >300 ms c3/c4 | máx hueco c3/c4 (ms) | None c3/c4 | mensajes bus |")
print("|---|---|---|---|---|---|---|---|")
for tag in ["q4_main_only_d120","q4_leaky1_d0","q4_leaky2_d0","q4_plain2_d0","q4_default_d0","q4_leaky1_d120","q4_leaky2_d120","q4_plain2_d120","q4_default_d120","q4_leaky1_d120_x4","q4_default_d120_x4","q4_leaky1_d120_x8","q4_default_d120_x8"]:
    r=load(tag); c3,c4=r["cams"]["3"],r["cams"]["4"]
    print(f"| {tag[3:]} | {r['process_cpu_pct_of_one_core']}% | {c3['fps_main']}/{c3['fps_view']} | {c4['fps_main']}/{c4['fps_view']} | {c3['gaps_over_300ms']}/{c4['gaps_over_300ms']} | {c3['frame_gap_ms']['max']:.0f}/{c4['frame_gap_ms']['max']:.0f} | {c3['stats']['pulls_none']}/{c4['stats']['pulls_none']} | {c3['bus_count']+c4['bus_count']} |")

print("\n### Q5 apareo por PTS (2 cámaras, 90 s; 'a la primera' = PTS iguales tras un pull de cada lado)")
print("| apareo | view max-buffers | consumidor | a la primera c3 | a la primera c4 | re-pulls c3/c4 | renuncias | latencia agregada media / p95 / máx (ms) c3 | c4 |")
print("|---|---|---|---|---|---|---|---|---|")
for pair in ("naive","robust"):
  for vm in (1,2):
    for d in (0,50,120):
        tag=f"q5_{pair}_vm{vm}_d{d}"
        try: r=load(tag)
        except FileNotFoundError: continue
        c3,c4=r["cams"]["3"],r["cams"]["4"]
        f=lambda c:f"{100*c['stats']['pair_first_ok']/max(1,c['stats']['pair_total']):.1f}%"
        pa=lambda c:f"{c['pair_added_ms']['mean']:.1f} / {c['pair_added_ms']['p95']:.1f} / {c['pair_added_ms']['max']:.0f}"
        dl={0:"rápido (0 ms)",50:"50 ms",120:"lento (120 ms)"}[d]
        print(f"| {pair} | {vm} | {dl} | {f(c3)} | {f(c4)} | {c3['stats']['pair_repulls']}/{c4['stats']['pair_repulls']} | {c3['stats']['pair_gave_up']+c4['stats']['pair_gave_up']} | {pa(c3)} | {pa(c4)} |")

print("\n### Q4 corrida larga (11 min, native, leaky1, apareo robusto)")
r=load("q4_long_native_robust")
print({k:r[k] for k in ("wall_s","process_cpu_pct_of_one_core","rss_mb_start_end","fds_start_end")})
for c,v in r["cams"].items():
    print(c, "fps",v["fps_main"],v["fps_view"],"huecos>300",v["gaps_over_300ms"],">1s",v["gaps_over_1s"],"máx",v["frame_gap_ms"]["max"],"pull None",v["stats"]["pulls_none"],"bus",v["bus_count"],"1ra vez ok",v["stats"]["pair_first_ok"],"/",v["stats"]["pair_total"])

print("\n### Q8 costos (2 cámaras, 15 fps, 120 s; dos repeticiones)")
print("| config | CPU proceso % de 1 núcleo (r1 / r2) | CPU ms por frame (r1 / r2) | Python ms/frame principal c3,c4 | Python ms/frame vista c3,c4 | nvvidconv ms principal c3,c4 | nvvidconv ms vista c3,c4 | RSS MB |")
print("|---|---|---|---|---|---|---|---|")
rows=[("a. hoy: 640x360 solo","q8a_main"),("b. + vista nativa","q8b_native"),("c. + vista reducida (5-tap)","q8c_resized_m2"),("c'. + vista reducida (bilineal)","q8g_resized_m1"),("d. CPU (OpenCV/FFmpeg nativo)","q8d_cpu_native"),("B1. compositor, vista nativa","q8e_comp_native"),("B2. compositor, vista reducida (5-tap)","q8f_comp_resized_m2")]
for name,base in rows:
    rs=[]
    for rep in (1,2):
        try: rs.append(load(f"{base}_r{rep}"))
        except FileNotFoundError: pass
    if not rs:
        try: rs=[load(base)]
        except FileNotFoundError: continue
    cpu=" / ".join(f"{r['process_cpu_pct_of_one_core']:.0f}" for r in rs)
    mspf=" / ".join(f"{r['cpu_ms_per_main_frame']:.1f}" for r in rs)
    def pick(key):
        out=[]
        for cam in ("3","4"):
            v=[r["cams"][cam][key]["mean"] for r in rs if r["cams"][cam][key]]
            out.append(f"{st.mean(v):.2f}" if v else "–")
        return ",".join(out)
    rss=rs[-1]["rss_mb_start_end"]
    print(f"| {name} | {cpu} | {mspf} | {pick('py_ms_main')} | {pick('py_ms_view')} | {pick('nvvidconv_ms_main')} | {pick('nvvidconv_ms_view')} | {rss[0]}→{rss[1]} |")

```

### `q10_flip_semantics.py`

```python
"""
Sección 10 (Q1 y Q2): crop + flip-method en un mismo nvvidconv.

  Q1  ¿las coordenadas del crop son del frame de origen (sin rotar) o de la imagen ya rotada?
      Un tee del decoder: una rama nativa sin flip y otra con `flip-method=N left= top= right= bottom=`;
      se compara contra las dos hipótesis calculadas en numpy con cv2.rotate.
  Q2  ¿qué width/height hay que fijar en las caps de salida (¿se intercambian con 1 y 3?), para la
      rama principal (todo el frame a 360x640 / 640x360) y para una vista recortada?

Corre sobre clips/cam_3.h264 (frames reproducibles), por orden.   python q10_flip_semantics.py [cam]
"""
import sys
from common import *

CAM = int(sys.argv[1]) if len(sys.argv) > 1 else 3
W, H = {3: (1920, 1080), 4: (2560, 1440)}[CAM]
FRAMES = 4
# flip-method -> la rotación de OpenCV que debería ser (la comparación numérica lo confirma o no)
CV_ROT = {1: cv2.ROTATE_90_COUNTERCLOCKWISE, 2: cv2.ROTATE_180, 3: cv2.ROTATE_90_CLOCKWISE}
NAME = {1: "90 ccw", 2: "180", 3: "90 cw"}


def run(flip, crop, caps_size, interp=1):
    """Decodifica FRAMES frames: la nativa y la rama con crop+flip. crop None = sin crop."""
    crop_props = f"left={crop[0]} top={crop[1]} right={crop[2]} bottom={crop[3]} " if crop else ""
    size = f",width={caps_size[0]},height={caps_size[1]}" if caps_size else ""
    launch = (f"filesrc location=clips/cam_{CAM}.h264 ! h264parse ! nvv4l2decoder ! tee name=t "
              f"t. ! queue ! nvvidconv ! video/x-raw,format=BGRx ! appsink name=full emit-signals=true max-buffers=0 drop=false sync=false "
              f"t. ! queue ! nvvidconv interpolation-method={interp} flip-method={flip} {crop_props}! video/x-raw,format=BGRx{size} ! "
              f"appsink name=view emit-signals=true max-buffers=0 drop=false sync=false")
    p = build(launch, live=False)
    full, view = [], []
    for name, lst in (("full", full), ("view", view)):
        def cb(sink, lst=lst):
            s = sink.emit("pull-sample")
            if len(lst) < FRAMES:
                lst.append(sample_to_bgr(s))
            return Gst.FlowReturn.OK
        p.get_by_name(name).connect("new-sample", cb)
    p.set_state(Gst.State.PLAYING)
    bus, err, t0 = p.get_bus(), "", time.time()
    while time.time() - t0 < 15 and (len(full) < FRAMES or len(view) < FRAMES):
        m = bus.timed_pop_filtered(100_000_000, Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if m is not None:
            if m.type == Gst.MessageType.ERROR:
                err = "ERROR bus: " + m.parse_error()[0].message
            break
    shutdown(p)
    return full, view, err


def mad(a, b):
    return None if a.shape != b.shape else float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())


def worst(pairs):
    vals = [mad(a, b) for a, b in pairs]
    return None if any(v is None for v in vals) else max(vals)


# ── Q1: ¿en qué espacio están las coordenadas del crop? ──────────────────────
crop = roi_native(W, H)                       # (l, t, r, b) en el frame de origen
l, t, r, b = crop
cw, ch = r - l, b - t
print(f"cam_{CAM} nativa {W}x{H}; crop (l,t,r,b) = {crop}, {cw}x{ch}\n")
print("Q1: ¿coordenadas del crop en el frame de ORIGEN o en la imagen ya ROTADA?")
print("    H_origen  = rotar(full[t:b, l:r])        (se recorta el frame sin rotar y se rota el recorte)")
print("    H_rotada  = rotar(full)[t:b, l:r]        (se rota todo y se recorta con esas coordenadas)")
for flip in (1, 2, 3):
    rot = CV_ROT[flip]
    # tamaño de salida natural del recorte rotado
    out_size = (ch, cw) if flip in (1, 3) else (cw, ch)
    full, view, err = run(flip, crop, out_size, interp=0)     # misma interpolación (nearest) que la rama nativa
    if not view:
        print(f"  flip {flip} ({NAME[flip]}): sin frames. {err}")
        continue
    v0 = view[0]
    h_src = [(cv2.rotate(f[t:b, l:r], rot), v) for f, v in zip(full, view)]
    h_rot = []
    for f, v in zip(full, view):
        rf = cv2.rotate(f, rot)
        h_rot.append((rf[t:b, l:r], v))
    print(f"  flip {flip} ({NAME[flip]}): salida {v0.shape[1]}x{v0.shape[0]} con caps {out_size[0]}x{out_size[1]}; "
          f"MAD vs H_origen = {worst(h_src)}; vs H_rotada = {worst(h_rot)}   {err}")

# comprobación de que las rotaciones de OpenCV son las correctas, sin crop (la imagen entera, sin escalar)
print("\n    control sin crop (frame entero, caps con el tamaño rotado): flip-method vs cv2.rotate")
for flip in (1, 2, 3):
    out_size = (H, W) if flip in (1, 3) else (W, H)
    full, view, err = run(flip, None, out_size, interp=0)
    if not view:
        print(f"    flip {flip}: sin frames. {err}"); continue
    best = {nm: worst([(cv2.rotate(f, rr), v) for f, v in zip(full, view)])
            for nm, rr in (("90 ccw", cv2.ROTATE_90_COUNTERCLOCKWISE), ("180", cv2.ROTATE_180), ("90 cw", cv2.ROTATE_90_CLOCKWISE))}
    print(f"    flip {flip} ({NAME[flip]}): salida {view[0].shape[1]}x{view[0].shape[0]}; MAD vs cv2.rotate: "
          + ", ".join(f"{k}={('-' if v is None else f'{v:.4f}')}" for k, v in best.items()))

# ── Q2: ¿qué width/height hay que fijar en las caps? ─────────────────────────
print("\nQ2a: rama PRINCIPAL (frame entero reducido por el VIC). Referencia: VIC a 640x360 + cv2.rotate (el camino de hoy)")
for flip in (1, 2, 3):
    rot = CV_ROT[flip]
    # referencia de hoy: VIC a 640x360 sin flip, y rotación en la CPU
    full_small, small, e0 = run(0, None, (640, 360))
    today = [cv2.rotate(s, rot) for s in small]
    for label, size in (("640x360 (sin intercambiar)", (640, 360)), ("360x640 (intercambiadas)", (360, 640)), ("sin fijar", None)):
        full, view, err = run(flip, None, size)
        if not view:
            print(f"  flip {flip} ({NAME[flip]}) caps {label}: sin frames. {err}"); continue
        v = view[0]
        d = worst(list(zip(today, view)))
        print(f"  flip {flip} ({NAME[flip]}) caps {label:28s}: salida {v.shape[1]}x{v.shape[0]}; MAD vs VIC+cv2.rotate = "
              f"{'tamaño distinto (' + str(today[0].shape[1]) + 'x' + str(today[0].shape[0]) + ')' if d is None else f'{d:.3f}'} {err}")

print("\nQ2b: VISTA recortada. Referencia: crop sin flip en el VIC y cv2.rotate del recorte (tamaño nativo, bit a bit)")
for flip in (1, 2, 3):
    rot = CV_ROT[flip]
    full0, view0, e0 = run(0, crop, (cw, ch))
    ref = [cv2.rotate(v, rot) for v in view0]
    for label, size in (("cw x ch (sin intercambiar)", (cw, ch)), ("ch x cw (intercambiadas)", (ch, cw)), ("sin fijar", None)):
        full, view, err = run(flip, crop, size)
        if not view:
            print(f"  flip {flip} ({NAME[flip]}) caps {label}: sin frames. {err}"); continue
        v = view[0]
        d = worst(list(zip(ref, view)))
        print(f"  flip {flip} ({NAME[flip]}) caps {label:28s}: salida {v.shape[1]}x{v.shape[0]}; MAD vs crop+cv2.rotate = "
              f"{'tamaño distinto (' + str(ref[0].shape[1]) + 'x' + str(ref[0].shape[0]) + ')' if d is None else f'{d:.4f}'} {err}")
sys.stdout.flush(); os._exit(0)

```

### `q10_flip_bias.py`

```python
"""¿De dónde sale la diferencia media ~0.11 entre `nvvidconv flip-method=N` y `cv2.rotate` de la rama nativa sin flip?
Se mira el error con signo por canal y el máximo, con flip 2 (no cambia el tamaño) y flip 0 de control."""
import sys
from common import *
CAM = 3
def grab(extra):
    launch = (f"filesrc location=clips/cam_{CAM}.h264 ! h264parse ! nvv4l2decoder ! nvvidconv {extra} ! video/x-raw,format=BGRx "
              f"! appsink name=sink emit-signals=true max-buffers=0 drop=false sync=false")
    p = build(launch, live=False); out = []
    def cb(sink):
        s = sink.emit("pull-sample")
        if len(out) < 4: out.append(sample_to_bgr(s))
        return Gst.FlowReturn.OK
    p.get_by_name("sink").connect("new-sample", cb)
    p.set_state(Gst.State.PLAYING); p.get_bus().timed_pop_filtered(15 * NS, Gst.MessageType.EOS | Gst.MessageType.ERROR); shutdown(p)
    return out
base = grab("")
for label, extra, rot in (("flip 0 (control: dos corridas iguales)", "", None), ("flip 0 + interpolation 1", "interpolation-method=1", None), ("flip 2", "flip-method=2", cv2.ROTATE_180)):
    other = grab(extra)
    d = []
    for a, b in zip(base, other):
        ref = cv2.rotate(a, rot) if rot is not None else a
        d.append(b.astype(np.int16) - ref.astype(np.int16))
    d = np.stack(d)
    print(f"{label:40s}: MAD={np.abs(d).mean():.4f} max|d|={np.abs(d).max()} error con signo por canal B,G,R = {[round(float(d[..., c].mean()), 4) for c in range(3)]}; "
          f"píxeles con |d|>=2: {100 * float((np.abs(d) >= 2).mean()):.4f} %")
sys.stdout.flush(); os._exit(0)

```

### `q10_probe_flip.py`

```python
"""
Sección 10 (Q3): la receta del probe de CAPS (sección 3a) con `flip-method` puesto.

El ROI lo dibuja el operador sobre el frame que ve, que está ROTADO (360x640 con 90°, 640x360 con
180°). El crop del VIC es en coordenadas de ORIGEN (Q1), así que el probe tiene que llevar el ROI
de la imagen rotada al frame de origen antes de fijar left/top/right/bottom. Se verifica contra
numpy:  cv2.rotate(frame_nativo, rot)[rt:rb, rl:rr]  (el recorte de la imagen rotada).

  python q10_probe_flip.py file <cam>    clip de archivo, compara con numpy (exacto)
  python q10_probe_flip.py live <cam>    RTSP en vivo: ¿llegan las dos ramas con el tamaño esperado?, 150 frames
"""
import sys, math
from common import *

MODE, CAM = sys.argv[1], int(sys.argv[2])
CV_ROT = {1: cv2.ROTATE_90_COUNTERCLOCKWISE, 2: cv2.ROTATE_180, 3: cv2.ROTATE_90_CLOCKWISE}
ROI_ROT = {1: (100, 150, 150, 250), 3: (100, 150, 150, 250), 2: (210, 40, 180, 220)}   # en el frame rotado de referencia


def rotated_native_size(flip, w, h):
    return (h, w) if flip in (1, 3) else (w, h)


def roi_to_source(flip, roi, native_w, native_h, min_px=16):
    """
    ROI en coordenadas del frame rotado de referencia (360x640 con 90°, 640x360 con 180°)
    -> (left, top, right, bottom) en el frame de ORIGEN, pares, y el tamaño de la imagen recortada.
    """
    rot_w, rot_h = rotated_native_size(flip, native_w, native_h)
    ref_w, ref_h = (360, 640) if flip in (1, 3) else (640, 360)
    x, y, w, h = roi
    sx, sy = rot_w / ref_w, rot_h / ref_h
    rl, rt = int(math.floor(x * sx)) & ~1, int(math.floor(y * sy)) & ~1
    rr, rb = (int(math.ceil((x + w) * sx)) + 1) & ~1, (int(math.ceil((y + h) * sy)) + 1) & ~1
    rr, rb = min(rr, rot_w & ~1), min(rb, rot_h & ~1)
    if rr - rl < min_px or rb - rt < min_px:
        return None
    if flip == 1:      # 90° antihorario: x' = y, y' = W-1-x
        src = (native_w - rb, rl, native_w - rt, rr)
    elif flip == 3:    # 90° horario: x' = H-1-y, y' = x
        src = (rt, native_h - rr, rb, native_h - rl)
    else:              # 180°
        src = (native_w - rr, native_h - rb, native_w - rl, native_h - rt)
    return src, (rl, rt, rr, rb)


def launch_for(flip, cam, filesrc):
    # en archivo no se descarta: el apareo es por índice. En vivo, la queue de la recomendación.
    q = "queue" if filesrc else "queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0"
    main_size = "width=360,height=640" if flip in (1, 3) else "width=640,height=360"
    head = (f"filesrc location=clips/cam_{cam}.h264 ! h264parse ! nvv4l2decoder name=dec ! tee name=t "
            if filesrc else
            f"rtspsrc location={CAMS[cam]} latency=0 protocols=tcp tcp-timeout=5000000 ! parsebin ! nvv4l2decoder name=dec disable-dpb=true ! tee name=t ")
    sink = ("emit-signals=true max-buffers=0 drop=false sync=false" if filesrc else "max-buffers=1 drop=true sync=false")
    ref = f"t. ! {q} ! nvvidconv interpolation-method=0 ! video/x-raw,format=BGRx ! appsink name=full emit-signals=true max-buffers=0 drop=false sync=false " if filesrc else ""
    return (head + ref +
            f"t. ! {q} ! nvvidconv interpolation-method=1 flip-method={flip} ! video/x-raw,format=BGRx,{main_size} ! appsink name=main {sink} "
            f"t. ! {q} ! nvvidconv name=vc interpolation-method=0 flip-method={flip} ! capsfilter name=cf caps=video/x-raw,format=BGRx ! "
            f"appsink name=view {sink}")


for flip in (1, 2, 3):
    p = build(launch_for(flip, CAM, MODE == "file"), live=(MODE == "live"))
    vc, cf = p.get_by_name("vc"), p.get_by_name("cf")
    info = {}
    def on_caps(pad, ev_info, vc=vc, cf=cf, flip=flip, info=info):     # captura SÓLO los elementos (sección 9.4)
        ev = ev_info.get_event()
        if ev.type == Gst.EventType.CAPS:
            st = ev.parse_caps().get_structure(0)
            nw, nh = st.get_value("width"), st.get_value("height")
            res = roi_to_source(flip, ROI_ROT[flip], nw, nh)
            if res is None:
                info["error"] = "ROI demasiado chico"; return Gst.PadProbeReturn.OK
            (l, t, r, b), (rl, rt, rr, rb) = res
            for k, v in zip(("left", "top", "right", "bottom"), (l, t, r, b)):
                vc.set_property(k, v)
            cf.set_property("caps", Gst.Caps.from_string(f"video/x-raw,format=BGRx,width={rr - rl},height={rb - rt}"))
            info.update(native=(nw, nh), source=(l, t, r, b), rotated=(rl, rt, rr, rb))
        return Gst.PadProbeReturn.OK
    p.get_by_name("dec").get_static_pad("src").add_probe(Gst.PadProbeType.EVENT_DOWNSTREAM, on_caps)

    names = ["main", "view"] + (["full"] if MODE == "file" else [])
    got = {n: [] for n in names}
    limit = 4 if MODE == "file" else 150
    counts = {n: 0 for n in names}
    for n in names:
        def cb(sink, n=n):
            s = sink.emit("pull-sample"); counts[n] += 1
            if len(got[n]) < limit or MODE == "live":
                fr = sample_to_bgr(s)
                if len(got[n]) < 4:
                    got[n].append(fr)
            return Gst.FlowReturn.OK
        el = p.get_by_name(n); el.set_property("emit-signals", True); el.connect("new-sample", cb)
    p.set_state(Gst.State.PLAYING)
    bus, err, t0 = p.get_bus(), "", time.time()
    while time.time() - t0 < (15 if MODE == "file" else 14):
        m = bus.timed_pop_filtered(100_000_000, Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if m is not None:
            if m.type == Gst.MessageType.ERROR:
                err = "ERROR bus: " + m.parse_error()[0].message
            break
        if MODE == "file" and min(len(got[n]) for n in names) >= 4:
            break
    shutdown(p)
    head = f"cam_{CAM} flip {flip}: nativa {info.get('native')}, ROI rotado {ROI_ROT[flip]} -> rect en imagen rotada {info.get('rotated')} -> crop en ORIGEN {info.get('source')}"
    if not got["view"]:
        print(head + f"\n   SIN FRAMES en la vista. {err} {info.get('error', '')}"); continue
    v, mn = got["view"][0], got["main"][0]
    if MODE == "file":
        rl, rt, rr, rb = info["rotated"]
        diffs = []
        for fu, vi in zip(got["full"], got["view"]):
            ref = cv2.rotate(fu, CV_ROT[flip])[rt:rb, rl:rr]
            diffs.append(None if ref.shape != vi.shape else float(np.abs(ref.astype(np.int16) - vi.astype(np.int16)).mean()))
        print(head + f"\n   vista {v.shape[1]}x{v.shape[0]} (esperada {rr - rl}x{rb - rt}); principal {mn.shape[1]}x{mn.shape[0]}; "
              f"MAD de la vista vs cv2.rotate(nativa)[recorte rotado] = {diffs}  {err}")
    else:
        print(head + f"\n   vista {v.shape[1]}x{v.shape[0]}; principal {mn.shape[1]}x{mn.shape[0]}; frames principal={counts['main']} vista={counts['view']} {err}")
        cv2.imwrite(f"out/q10_live_cam{CAM}_flip{flip}_main.png", mn); cv2.imwrite(f"out/q10_live_cam{CAM}_flip{flip}_view.png", v)
sys.stdout.flush(); os._exit(0)

```

### `q10_content.py`

```python
"""Sección 10 (Q4, contenido): VIC reduce y gira en una pasada (flip-method) vs el camino de hoy: VIC reduce a 640x360 y
cv2.rotate en la CPU. 20 frames de clips/cam_N.h264. Rama principal completa, sin crop."""
import sys
from common import *
N = 20
CV_ROT = {1: cv2.ROTATE_90_COUNTERCLOCKWISE, 2: cv2.ROTATE_180, 3: cv2.ROTATE_90_CLOCKWISE}
NAME = {1: "90 ccw", 2: "180", 3: "90 cw"}

def grab(cam, flip, size):
    launch = (f"filesrc location=clips/cam_{cam}.h264 ! h264parse ! nvv4l2decoder ! nvvidconv interpolation-method=1 "
              f"{'flip-method=%d ' % flip if flip else ''}! video/x-raw,format=BGRx,width={size[0]},height={size[1]} "
              f"! appsink name=sink emit-signals=true max-buffers=0 drop=false sync=false")
    p = build(launch, live=False); out = []
    def cb(sink):
        s = sink.emit("pull-sample")
        if len(out) < N: out.append(sample_to_bgr(s))
        return Gst.FlowReturn.OK
    p.get_by_name("sink").connect("new-sample", cb)
    p.set_state(Gst.State.PLAYING); p.get_bus().timed_pop_filtered(20 * NS, Gst.MessageType.EOS | Gst.MessageType.ERROR); shutdown(p)
    return out

for cam in (3, 4):
    plain = grab(cam, 0, (640, 360))
    for flip in (1, 2, 3):
        size = (360, 640) if flip in (1, 3) else (640, 360)
        today = [cv2.rotate(f, CV_ROT[flip]) for f in plain]
        vic = grab(cam, flip, size)
        d = np.stack([a.astype(np.int16) - b.astype(np.int16) for a, b in zip(today, vic)])
        print(f"cam_{cam} flip {flip} ({NAME[flip]}): {len(vic)} frames, salida {vic[0].shape[1]}x{vic[0].shape[0]}; "
              f"MAD={np.abs(d).mean():.5f} max|d|={np.abs(d).max()} píxeles distintos={100 * float((d != 0).mean()):.5f} %")
sys.stdout.flush(); os._exit(0)

```

### `q10_rules.py`

```python
"""
Sección 10 (Q5): ¿flip-method cambia las reglas de la sección 9.1 (coordenadas pares, recorte >= 16x16)?
Crop en coordenadas de ORIGEN (Q1), caps con el tamaño intercambiado si el giro es de 90°. Se compara contra
cv2.rotate(frame_nativo[t:b, l:r]). Un error fatal se informa y se sigue con el siguiente caso.
"""
import sys
from common import *
CV_ROT = {0: None, 1: cv2.ROTATE_90_COUNTERCLOCKWISE, 2: cv2.ROTATE_180, 3: cv2.ROTATE_90_CLOCKWISE}

def run(flip, crop, frames=3):
    l, t, r, b = crop
    cw, ch = r - l, b - t
    ow, oh = (ch, cw) if flip in (1, 3) else (cw, ch)
    launch = (f"filesrc location=clips/cam_3.h264 ! h264parse ! nvv4l2decoder ! tee name=t "
              f"t. ! queue ! nvvidconv ! video/x-raw,format=BGRx ! appsink name=full emit-signals=true max-buffers=0 drop=false sync=false "
              f"t. ! queue ! nvvidconv {'flip-method=%d ' % flip if flip else ''}left={l} top={t} right={r} bottom={b} "
              f"! video/x-raw,format=BGRx,width={ow},height={oh} ! appsink name=view emit-signals=true max-buffers=0 drop=false sync=false")
    p = build(launch, live=False)
    full, view = [], []
    for name, lst in (("full", full), ("view", view)):
        def cb(sink, lst=lst):
            s = sink.emit("pull-sample")
            if len(lst) < frames: lst.append(sample_to_bgr(s))
            return Gst.FlowReturn.OK
        p.get_by_name(name).connect("new-sample", cb)
    p.set_state(Gst.State.PLAYING)
    bus, fatal, t0 = p.get_bus(), False, time.time()
    while time.time() - t0 < 10 and (len(full) < frames or len(view) < frames):
        m = bus.timed_pop_filtered(100_000_000, Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if m is not None:
            fatal = m.type == Gst.MessageType.ERROR
            break
    shutdown(p)
    if not view:
        return None, "ERROR FATAL" if fatal else "sin frames"
    ref = full[0][t:b, l:r]
    if CV_ROT[flip] is not None: ref = cv2.rotate(ref, CV_ROT[flip])
    return (float(np.abs(ref.astype(np.int16) - view[0].astype(np.int16)).mean()) if ref.shape == view[0].shape else -1.0), ""

fmt = lambda d, e: e if d is None else f"MAD={d:.4f}"
print("paridad de cada coordenada de ORIGEN (recorte base 540x660 en 630,120; un lado impar a la vez)")
for flip in (1, 2, 3):
    row = []
    for name, c in (("par", (630, 120, 1170, 780)), ("left+1", (631, 120, 1170, 780)), ("top+1", (630, 121, 1170, 780)),
                    ("right+1", (630, 120, 1171, 780)), ("bottom+1", (630, 120, 1170, 781)), ("todo+1", (631, 121, 1171, 781)), ("todo+2", (632, 122, 1172, 782))):
        row.append(f"{name}: {fmt(*run(flip, c))}")
    print(f"  flip {flip}: " + " | ".join(row))
print("tamaño mínimo, cuadrado (origen par)")
for flip in (0, 1, 2, 3):
    row = []
    for s in (8, 12, 14, 15, 16, 18, 32):
        row.append(f"{s}: {fmt(*run(flip, (630, 120, 630 + s, 120 + s)))}")
    print(f"  flip {flip}: " + " | ".join(row))
print("tamaño mínimo, NO cuadrado (¿el piso es por dimensión de origen o de salida?)")
for flip in (0, 1):
    row = []
    for w, h in ((16, 100), (100, 16), (12, 100), (100, 12), (16, 16), (12, 16), (16, 12)):
        row.append(f"{w}x{h}: {fmt(*run(flip, (630, 120, 630 + w, 120 + h)))}")
    print(f"  flip {flip} (tamaño de ORIGEN w x h): " + " | ".join(row))
sys.stdout.flush(); os._exit(0)

```

### `q10_rotate_cost.py`

```python
"""Sección 10 (Q4, complemento): costo de cv2.rotate en la CPU según el tamaño de la imagen (BGR uint8), un hilo, sin cámaras.
Sirve para estimar cuánto se ahorraría rotando en el VIC la vista recortada, que sale a tamaño nativo o reducida."""
import time, cv2, numpy as np
for label, (w, h) in (("rama principal 640x360", (640, 360)), ("vista reducida 224x274", (224, 274)),
                      ("recorte nativo cam_3 540x660", (540, 660)), ("recorte nativo cam_4 720x880", (720, 880))):
    img = np.random.randint(0, 255, (h, w, 3), np.uint8)
    out = []
    for name, code in (("90 ccw", cv2.ROTATE_90_COUNTERCLOCKWISE), ("180", cv2.ROTATE_180)):
        for _ in range(50): cv2.rotate(img, code)
        t0 = time.perf_counter()
        for _ in range(500): cv2.rotate(img, code)
        out.append(f"{name}: {(time.perf_counter() - t0) / 500 * 1000:.3f} ms")
    print(f"{label:32s} " + " | ".join(out))

```

### `run_q10_matrix.sh`

```bash
#!/bin/bash
# Sección 10 (Q4, costo): rama principal 640x360 (o 360x640 girada), dos cámaras a 15 fps, 120 s, dos repeticiones intercaladas.
cd "$(dirname "$0")"
PY=/home/iea/Documents/sip-volcables/.venv/bin/python
run() { tag=$1; shift
  timeout 260 $PY bench.py --mode main --cams 3,4 --duration 120 --warmup 10 --tag "$tag" "$@" >logs/out_$tag.txt 2>logs/err_$tag.txt
  echo "$tag exit=$?" >> logs/q10_matrix.progress; }
: > logs/q10_matrix.progress
for rep in 1 2; do
  run q10a_none_r$rep
  run q10b_cv2_ccw_r$rep   --cv2-rotate ccw
  run q10c_flip1_r$rep     --flip 1
  run q10d_cv2_180_r$rep   --cv2-rotate 180
  run q10e_flip2_r$rep     --flip 2
done
echo DONE >> logs/q10_matrix.progress

```
