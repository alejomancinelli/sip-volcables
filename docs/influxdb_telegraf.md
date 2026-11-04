# La telemetría en formato Telegraf

La misma estructura de [`influxdb.md`](influxdb.md), escrita como la documenta un plugin de
Telegraf: el árbol measurement → tags → fields con el tipo de cada campo, y un ejemplo de
salida en line protocol. Sirve para saber qué filtrar antes de abrir el Data Explorer.

Lo que hay que tener en la cabeza para leerlo:

- **Sólo `inference` trae estadísticos.** `system`, `network`, `camera` y `services` son una foto por
  segundo: cada punto es la lectura de ese instante y no hay `_min`, `_max` ni `_mean`. Si un
  panel quiere el máximo de CPU de la última hora, lo calcula la consulta
  (`aggregateWindow(fn: max)`), no el equipo.
- **En `inference` el estadístico es un tag, no un sufijo.** `kind` dice qué es el punto
  —conteo, latencia o lo medido— y `stat` qué estadístico lleva; el campo tiene el nombre
  limpio (`count_person`). Para ver sólo las medias se filtra `stat == "mean"`.
- **Tipos de line protocol:** `1i` es entero, `1.0` es float, `"…"` es string. InfluxDB fija
  el tipo de un campo con el primer punto que lo escribe dentro de un shard, y rechaza el
  punto que después llega con otro tipo.

## Metrics

- system — hardware del equipo; 1 punto por segundo
  - tags:
    - device (`system.device_id` del config)
  - fields:
    - cpu_usage_pct (integer, %)
    - gpu_usage_pct (integer, %)
    - cpu_temp_c (integer, °C; 0 si el equipo no lo expone)
    - gpu_temp_c (integer, °C; 0 si el equipo no lo expone)
    - ram_used_mb (integer, MiB)
    - ram_total_mb (integer, MiB)
    - disk_free_gb (integer, GiB libres en `system_monitor.disk_path`)
    - power_w (float, W; 0.0 sin sensor)
    - overcurrent_count (integer, eventos de sobrecorriente desde el arranque)
    - engine_\<motor\>_active (integer, 0/1 — el motor de la Jetson se usó hace poco; su
      media en una ventana es la fracción del tiempo en uso) — sólo en una Jetson
    - engine_\<motor\>_mhz (integer, MHz mientras está activo; 0 si no) — sólo en una Jetson

- network — tráfico de red; 1 punto por interfaz por segundo
  - tags:
    - device
    - interface (el nombre que da el sistema: `eth0` en la Jetson, `Ethernet` en Windows)
  - fields:
    - rx_mbps (float, Mbps recibidos desde la muestra anterior)
    - tx_mbps (float, Mbps enviados)

- camera — estado de cada cámara; 1 punto por cámara por segundo
  - tags:
    - device
    - camera (el slot: `camera_1`, `camera_2`, …)
  - fields:
    - connected (integer, 0/1 — hay sesión con el hardware)
    - capture_enabled (integer, 0/1 — se le están pidiendo frames)
    - misconfigured (integer, 0/1 — el driver no se pudo construir)
    - fps_estimated (float, fps medidos sobre los frames entregados)
    - temperature_c (float, °C; 0 si el driver no la da)
    - illumination_pct (integer, brillo medio del frame entero 0–100)
    - lens_state (integer: 0 limpio, 1 alarma de lente sucio, 2 no disponible)
    - lens_sharpness_pct (integer, nitidez máxima de la ventana contra la referencia)

- services — los seis canales de salida; 1 punto por segundo
  - tags:
    - device
  - fields (integer, 0/1 — 1 es «levantó y está andando», el mismo criterio que el bit del PLC):
    - modbus_tcp
    - modbus_rtu
    - video_http
    - video_rtsp
    - influxdb
    - mqtt

- inference — los resultados agregados; 7 puntos por par (cámara, pipeline) que produjo algo
  en el último segundo, todos sellados con el instante de su último resultado
  - tags (en los siete):
    - device
    - camera (el slot de la cámara)
    - pipeline (el slot del pipeline: `pipeline_1`, …)
    - kind (`count`, `time` o `result`)
  - `kind=count` — 1 punto, sin `stat`; fields:
    - result_count (integer, resultados que entraron a la muestra)
    - invalid_count (integer, cuántos no eran una medición)
    - invalid_reason (string, el motivo del último descarte o `""`: `no_model`,
      `dark_frame`, `low_confidence`, `few_detections`, `error`)
    - sample_count (integer, resultados confiables que entraron a lo medido)
  - `kind=time` — 2 puntos, tag `stat` = `mean` | `max`, sobre todos los resultados; fields:
    - total_ms (float, la pasada entera: recorte, etapas y filtros)
    - \<model_slot\>_ms (float) — uno por etapa: `model_1_ms`
    - cycle_ms (float, el período de la cámara: de un frame al siguiente de la misma
      cámara; no sale si la muestra trae sólo el primer frame)
  - `kind=result` — 4 puntos, tag `stat` = `mean` | `std` | `min` | `max`, sólo sobre los
    confiables; fields, todos float:
    - confidence_pct
    - detection_count
    - \<metric\> — una por clave de `metrics` del analyzer, con su nombre sin sufijo

  `std` es el desvío poblacional: 0.0 con una sola muestra. Un par sin ningún confiable no
  publica los cuatro de `result`. Con el `metrics.py` del template las claves son
  `detection_count`, `count_<clase>` y `fraction_<clase>_pct`, así que una clase `person`
  agrega dos campos —`count_person` y `fraction_person_pct`— a cada uno de los cuatro
  puntos de `result`.

## Example Output

Un segundo de un equipo con dos cámaras, un pipeline de una etapa sobre `camera_1`, y una red
con `eth0` y `wlan0`. Los timestamps son en nanosegundos.

```text
system,device=planta_01 cpu_usage_pct=34i,gpu_usage_pct=61i,cpu_temp_c=52i,gpu_temp_c=48i,ram_used_mb=5120i,ram_total_mb=15823i,disk_free_gb=212i,power_w=14.6,overcurrent_count=0i 1793620800000000000
network,device=planta_01,interface=eth0 rx_mbps=38.2,tx_mbps=4.1 1793620800000000000
network,device=planta_01,interface=wlan0 rx_mbps=0.0,tx_mbps=0.0 1793620800000000000
camera,device=planta_01,camera=camera_1 connected=1i,capture_enabled=1i,misconfigured=0i,fps_estimated=9.8,temperature_c=41.5,illumination_pct=63i,lens_state=0i,lens_sharpness_pct=97i 1793620800000000000
camera,device=planta_01,camera=camera_2 connected=0i,capture_enabled=1i,misconfigured=0i,fps_estimated=0.0,temperature_c=0.0,illumination_pct=0i,lens_state=2i,lens_sharpness_pct=0i 1793620800000000000
services,device=planta_01 modbus_tcp=1i,modbus_rtu=0i,video_http=1i,video_rtsp=0i,influxdb=1i,mqtt=0i 1793620800000000000
inference,device=planta_01,camera=camera_1,pipeline=pipeline_1,kind=count result_count=5i,invalid_count=1i,invalid_reason="dark_frame",sample_count=4i 1793620800812000000
inference,device=planta_01,camera=camera_1,pipeline=pipeline_1,kind=time,stat=mean total_ms=41.7,model_1_ms=39.9,cycle_ms=101.2 1793620800812000000
inference,device=planta_01,camera=camera_1,pipeline=pipeline_1,kind=time,stat=max total_ms=58.3,model_1_ms=55.0,cycle_ms=118.6 1793620800812000000
inference,device=planta_01,camera=camera_1,pipeline=pipeline_1,kind=result,stat=mean confidence_pct=82.4,detection_count=3.25,count_person=3.25,fraction_person_pct=100.0 1793620800812000000
inference,device=planta_01,camera=camera_1,pipeline=pipeline_1,kind=result,stat=std confidence_pct=3.1,detection_count=0.43,count_person=0.43,fraction_person_pct=0.0 1793620800812000000
inference,device=planta_01,camera=camera_1,pipeline=pipeline_1,kind=result,stat=min confidence_pct=78.0,detection_count=3.0,count_person=3.0,fraction_person_pct=100.0 1793620800812000000
inference,device=planta_01,camera=camera_1,pipeline=pipeline_1,kind=result,stat=max confidence_pct=86.5,detection_count=4.0,count_person=4.0,fraction_person_pct=100.0 1793620800812000000
```

Los siete de `inference` comparten el instante —el del último resultado, 812 ms después del
tick— y se distinguen sólo por `kind` y `stat`.

`camera_2` está caída: sale igual, con `connected=0i`, porque un hueco en la serie no
distingue una cámara caída de un equipo que no publica.

## Cómo se filtra

En Flux, cada línea de arriba se abre en una fila por campo: `_measurement`, los tags,
`_field` y `_value`. Filtrar es elegir esas columnas.

```flux
// Una métrica de hardware, tal cual: un valor por segundo.
from(bucket: "…") |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "system" and r._field == "cpu_usage_pct")

// El máximo de CPU por minuto lo calcula la consulta, no el equipo.
  |> aggregateWindow(every: 1m, fn: max)

// El tráfico de entrada, una línea por interfaz.
  |> filter(fn: (r) => r._measurement == "network" and r._field == "rx_mbps")
  |> group(columns: ["interface"])

// Sólo las medias de lo medido, de un pipeline.
  |> filter(fn: (r) => r._measurement == "inference" and r.pipeline == "pipeline_1"
                       and r.kind == "result" and r.stat == "mean")

// Una métrica con su media y su máximo en el mismo panel: una línea por `stat`.
  |> filter(fn: (r) => r._measurement == "inference" and r.kind == "result"
                       and r._field == "count_person" and (r.stat == "mean" or r.stat == "max"))
  |> group(columns: ["stat"])

// Los picos de latencia de cada etapa.
  |> filter(fn: (r) => r._measurement == "inference" and r.kind == "time" and r.stat == "max")
  |> group(columns: ["_field"])

// Una cámara entera, todos sus campos.
  |> filter(fn: (r) => r._measurement == "camera" and r.camera == "camera_1")
```
