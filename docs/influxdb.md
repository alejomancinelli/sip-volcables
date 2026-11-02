# Estructura de la telemetría en InfluxDB

Qué series publica el equipo, con qué tags y con qué nombres de campo. Es el contrato con
los dashboards: una vez que un panel consulta un campo, renombrarlo lo rompe.

**Estado: implementado.** Las cinco series salen por muestra, con el tag `device` y cada
punto sellado con el instante en que se midió. El muestreo es de un segundo
y no se configura.

**Esto vale también para MQTT.** Los dos backends reciben el mismo registro, así que las
series, los tags y los nombres de campo de acá son los que publica el broker. Cómo se ve
eso en el cable —el tópico y el JSON— está en [`mqtt.md`](mqtt.md).

La misma estructura, resumida como la documenta un plugin de Telegraf —el árbol de tags y
campos con sus tipos y un ejemplo en line protocol—, está en
[`influxdb_telegraf.md`](influxdb_telegraf.md).

El público es quien arma los dashboards y quien verifica una instalación desde afuera:
con InfluxDB se contesta «¿esto viene midiendo bien desde que lo dejamos?» sin entrar al
equipo, que es para lo que existe la telemetría acá.

## Las cinco series

| Serie | Qué sale por muestra |
|---|---|
| `system` — hardware | 1 punto |
| `network` — tráfico por interfaz | 1 por interfaz de red |
| `camera` — estado por cámara | 1 por cámara |
| `services` — los seis canales | 1 punto, seis campos |
| `inference` — el resultado | 7 por par (cámara, pipeline) con resultados nuevos: 1 de conteo, 2 de latencia y 4 de lo medido |

Las cuatro primeras responden «¿el equipo está sano?», que es lo que no se puede ver desde
afuera de otra forma.

## Cuántos puntos por segundo

**La cola no es el límite.** El hilo de telemetría la drena de continuo mientras junta el
batch, así que en régimen aguanta mucho más de lo que estas series producen: medido con un
backend doble, **50 puntos/s sin descartar uno solo**.

Lo que sí se achica al publicar más seguido son dos cosas:

1. **El margen ante un InfluxDB trabado.** La cola guarda 1000 puntos —menos de 1 KB cada
   uno, ~1 MB llena— y sólo se acumula mientras la escritura está bloqueada. A `N` puntos/s
   tolera `1000/N` segundos de escritura lenta antes de empezar a descartar:

   | Puntos/s | Margen |
   |---|---|
   | 19 — 2 cámaras | ~53 s |
   | 35 — 4 cámaras | ~29 s |
   | 100 | 10 s — una ventana de publicación |
   | 200 | 5 s — el timeout HTTP de InfluxDB |

   Por debajo de una ventana de publicación de margen, **una sola escritura lenta ya pierde
   datos**. Ese es el umbral que avisa el arranque, y por eso el aviso habla de margen y no
   de tope.

   **Con InfluxDB en el mismo equipo, que es lo previsto, esto casi no pasa.** Un servidor
   caído en la misma máquina rechaza la conexión en el acto y la escritura falla en
   milisegundos; el bloqueo de 5 s es de una red que no contesta. Lo que todavía la traba
   es un InfluxDB vivo pero lento: compactando, con el disco ocupado por el dataset, o sin
   CPU porque la inferencia se la lleva.

2. **El volumen guardado**, que no se cuenta en puntos sino en valores: InfluxDB guarda
   cada campo en su propia serie, así que lo que ocupa es puntos × campos. Partir un punto
   en siete con `kind` y `stat` no lo cambia —son los mismos valores, con el estadístico en
   un tag en vez de en el nombre— y lo único que agrega es repetir los tags y el instante en
   cada línea del cable. Lo que sí lo multiplica es medir más seguido o sumar campos, y eso
   decide la retención y si hace falta downsampling del lado de InfluxDB.

Con 4 cámaras en un pipeline, una interfaz de red y el muestreo en 1 s: 1 de `system` + 1
de `network` + 4 de `camera` + 1 de `services` + 4 × 7 de `inference` = **35 puntos/s**, ~29 s
de margen con la cola de 1000. Con 2 cámaras son 19; con una sola, 11. El arranque hace esta
cuenta —contando
interfaces y pares (cámara, pipeline)— y avisa si el margen baja de una ventana de
publicación. **Ojo con `system_monitor.net_interfaces: []`**: mide todas las interfaces
menos loopback, y en un Windows con adaptadores virtuales eso son varios puntos por
segundo que nadie mira. En planta conviene listar las que importan.

Igual la telemetría se publica **por muestra y no por evento**, y no por la cola: una serie
de salud por evento no agregaría información, y el detalle por resultado ya va al dataset y
al PLC.

### Dos intervalos que no son lo mismo, y ninguno está en el config

Los dos se llaman «intervalo», hacen cosas distintas y los dos son constantes de código:

| | Qué controla | Dónde |
|---|---|---|
| `_SAMPLE_INTERVAL_MS` | **cada cuánto se mide** — la resolución de los datos | `main.py`, 1000 ms |
| `_PUBLISH_INTERVAL_S` | **cada cuánto se escribe** — el batch al backend | `persistence.py`, 10 s |

Entre los dos, cada batch lleva diez muestras de cada serie y el dashboard las ve hasta
10 s después de medidas. **Cada punto viaja con el instante en que se midió**, así que la
ventana de escritura no deforma la serie: sólo agrega latencia.

Ninguno es configuración, y por razones distintas:

- **El muestreo** no lo es porque no hay instalación que quiera otro: un punto por segundo
  es la resolución con la que se miran estas series. Más fino no agrega información —el
  hardware no cambia más rápido— y más grueso pierde el detalle de un pico.
- **La ventana de escritura** no lo es por decisión del módulo, que lo dice en su
  docstring: no cambia el dato, sólo cada cuánto salen las escrituras.

Si un dashboard en vivo con 10 s de retraso molesta, lo que se baja es `_PUBLISH_INTERVAL_S`
—y se pagan escrituras más chicas y más frecuentes—, no el muestreo.

## Tags

Seis. Los cuatro primeros dicen de dónde salió el punto y ninguno es un literal en el
código; los dos últimos parten la serie `inference` y sus valores son un vocabulario fijo:

| Tag | De dónde sale | Para qué |
|---|---|---|
| `device` | `system.device_id` del config | distinguir equipos que comparten bucket |
| `interface` | el nombre que da el sistema (`eth0`, `Ethernet`) | una serie por interfaz de red |
| `camera` | la clave del slot (`camera_1`) | una serie por cámara, mismos campos |
| `pipeline` | la clave del slot (`pipeline_1`) | separar dos pipelines sobre la misma cámara |
| `kind` | `count`, `time` o `result` | separar conteos, latencia y lo medido |
| `stat` | `mean`, `std`, `min` o `max` | el estadístico; el campo lleva el nombre limpio |

**La cámara va en un tag y no en el nombre del campo.** Es la diferencia entre

    camera,camera=camera_1 fps=9.8          ← una serie por cámara, un solo panel
    system cam01_fps=9.8,cam02_fps=5.9      ← dos campos, y el panel se reescribe al sumar una

Con el tag, agregar una cámara agrega series y los dashboards no se tocan: se agrupa por
`camera`. Con el prefijo en el campo, cada cámara nueva es una consulta nueva. La interfaz
de red sigue la misma regla.

## Las series

### `system` — hardware del equipo

Una por muestra, tag `device`. Los campos son los de `SystemMonitor.get_metrics()`, con el
mismo nombre: no hay traducción en el camino.

| Campo | Unidad |
|---|---|
| `cpu_usage_pct`, `gpu_usage_pct` | % |
| `cpu_temp_c`, `gpu_temp_c` | °C |
| `ram_used_mb`, `ram_total_mb` | MB |
| `disk_free_gb` | GB |
| `power_w` | W, siempre float |
| `overcurrent_count` | eventos de sobrecorriente desde el arranque |
| `engine_<motor>_active` | 0 / 1 — el motor de la Jetson se usó hace poco |
| `engine_<motor>_mhz` | MHz mientras está activo; 0 si no |

**Cada campo tiene un tipo fijo.** InfluxDB fija el tipo de un campo con el primer punto
que lo escribe y rechaza el que después llega con otro. Todos los de esta serie son
enteros salvo `power_w`, que es float venga de los rails de la Jetson, de nvidia-smi o del
0.0 sin sensor: es `SystemMonitor` el que lo garantiza.

**Ojo con los anidados.** `get_metrics()` devuelve `net_mbps` como
`{iface: {rx_mbps, tx_mbps}}`, `engines` como `{motor: {is_active, mhz}}` y `temps_c` como
`{zona: °C}`, y **los fields de Influx son escalares**: un dict no entra. La red sale en su
propia serie, `network`, y los motores se aplanan —`engine_vic_active`—; filtrar por tipo
sería la forma silenciosa de perder el tráfico entero.

**Los motores** son los de la Jetson: `vic`, `nvenc`, `nvdec`, `nvjpg`, `nvjpg1`, `ofa`,
`nvdla0`, `pva0`, según el módulo. Fuera de una Jetson no hay ningún campo `engine_*`.
- `engine_<motor>_active` es un entero y no un booleano: su **media en una ventana es la
  fracción del tiempo que el motor estuvo en uso**, que es lo que sirve graficar.
- No es carga en %. Sale de sysfs sin root, y «activo» quiere decir «usado en los últimos
  segundos»: el kernel tarda en suspenderlo.
- Un `overcurrent_count` que sube es que el módulo recortó relojes para protegerse. Las
  métricas de ese momento no valen como rendimiento normal.

`temps_c` no se publica: es el detalle por zona térmica que ya resumen `cpu_temp_c` y
`gpu_temp_c`, y sus nombres cambian entre equipos, así que no sirve para un dashboard
portable. Queda para el log de diagnóstico.

### `network` — una serie por interfaz

Una por interfaz por muestra, tags `device` e `interface`. Las interfaces son las de
`system_monitor.net_interfaces`, o todas menos loopback si la lista está vacía.

| Campo | Unidad |
|---|---|
| `rx_mbps` | Mbps recibidos desde la muestra anterior |
| `tx_mbps` | Mbps enviados |

La interfaz va en un tag y no en el nombre del campo —antes era `net_eth0_rx_mbps`—: los
nombres cambian entre equipos (`eth0` en la Jetson, `Ethernet` en Windows), y con el tag
los campos son siempre los mismos dos y el panel agrupa por `interface`.

### `camera` — una serie por cámara

Una por cámara por muestra, tags `device` y `camera`. Campos de
`AbstractCameraDriver.get_status()` más la iluminación, que la mide la inferencia:

| Campo | Qué es |
|---|---|
| `connected` | 0/1 — hay sesión con el hardware |
| `capture_enabled` | 0/1 — se le están pidiendo frames |
| `fps_estimated` | fps medidos sobre los frames entregados |
| `temperature_c` | °C, 0 si el driver no la da |
| `illumination_pct` | brillo medio del ROI, 0–100 |
| `misconfigured` | 0/1 — el driver no se pudo construir |
| `lens_state` | 0 limpio, 1 lente sucio, 2 no disponible — `STATE_*` de `lens_health.py` |
| `lens_sharpness_pct` | nitidez máxima de la ventana contra la referencia calibrada |

`connected` y `capture_enabled` van separados a propósito: una cámara deshabilitada no es
lo mismo que una caída, y el gráfico tiene que poder distinguirlas. `lens_sharpness_pct` es
la tendencia que deja ver el vidrio ensuciarse semanas antes de que `lens_state` pase a 1.

### `inference` — el resultado, agregado

Siete puntos por par `(cámara, pipeline)` que haya producido resultados desde la muestra
anterior, todos con tags `device`, `camera` y `pipeline`, y partidos por `kind` y `stat`
para que un panel elija qué mira filtrando tags y no leyendo nombres de campo:

| `kind` | `stat` | Campos | Sobre qué |
|---|---|---|---|
| `count` | — | `result_count`, `invalid_count`, `sample_count`, `invalid_reason` | la muestra |
| `time` | `mean`, `max` | `total_ms`, `<model_slot>_ms` por etapa y `cycle_ms` | todos los resultados |
| `result` | `mean`, `std`, `min`, `max` | `confidence_pct`, `detection_count` y las métricas del analyzer | sólo los confiables |

- `result_count` son los resultados de la muestra; `invalid_count`, cuántos no eran una
  medición; `invalid_reason`, el motivo del último descarte —vocabulario `REASON_*`—, vacío
  si no hubo; `sample_count`, cuántos confiables entraron a lo medido.
- En `result` el campo lleva **el nombre de la métrica sin sufijo**: `count_person` con
  `stat=max` es el máximo. Comparar la media con el máximo es agrupar por `stat`.
- **Todo lo de `time` y `result` es float.** Los puntos de un mismo `kind` comparten nombres
  de campo, e InfluxDB rechaza el que cambia de tipo.
- Un par sin ningún confiable publica `count` y `time`, y no `result`: no hay qué resumir, y
  el `sample_count=0` ya lo dice.

**Se agrega, no se muestrea.** Con una inferencia de 200 ms y una muestra por segundo
entran cinco resultados: promediarlos usa los cinco, y el desvío dice si el proceso estuvo
estable dentro de ese segundo. Quedarse con el último tiraría cuatro de cada cinco. Con
`frames_per_cycle` en más de uno llega un ciclo ya resumido, y su desvío y su rango —los de
sus N frames— viajan tal cual en vez de derivarse de nuevo sobre una sola muestra.

Qué se agrega sobre qué no es un detalle: lo medido **sólo sobre los confiables**, porque
promediar la confianza de un descarte da un número que no significa nada; la latencia
**sobre todos**, porque un descarte también gastó su pasada. La latencia lleva el máximo
porque un pico es lo que se quiere ver, y el promedio lo esconde.

`total_ms` mide lo que tarda un frame en el motor —preprocesado, etapas y filtros—; no
incluye la espera en la cola ni el analyzer, el dibujado y la publicación. `cycle_ms` es el
tiempo real entre un frame y el siguiente **de la misma cámara**, así que sí los incluye, y
con varias cámaras en un pipeline suma además el turno de las otras: dos cámaras con 100 ms
por frame dan un ciclo de unos 200 ms por cámara. Cuenta todo frame que el motor procesa,
también el descartado por poca luz. La primera muestra de una cámara no tiene frame
anterior y no entra: sin ningún período en la muestra, el campo no sale.

**La iluminación no está acá**: sale por cámara en la serie `camera`, que ya es el brillo
del ROI que mide la inferencia.

**Los descartes se publican**: no como puntos propios, sino en `invalid_count` y
`invalid_reason`, que es lo que permite preguntar cuántas mediciones se cayeron y por qué.
Un panel que grafique la medición usa `stat=mean` y mira `sample_count` para saber sobre
cuántas muestras está.

**Cada punto se sella con el instante del último resultado**, no con el de la muestra: el
valor vale por cuándo se midió. Es lo que hace el `time_s=` de `push_data()`.

Las claves de `metrics` son las del analyzer, sin renombrar: el nombre de la métrica es el
mismo en el panel del anotado, en el JSON del dataset, en los fields de acá y —cuando
tiene fila— en el registro Modbus. Una sola fuente de nombres.

### `services` — los seis canales de salida

Una por muestra, tag `device`, un campo por canal con el estado como 0/1:

| Campo |
|---|
| `modbus_tcp`, `modbus_rtu`, `video_http`, `video_rtsp`, `influxdb`, `mqtt` |

Son los mismos seis de `system/formats/com_status.py` y el 1 significa lo mismo que el
bit: **levantó y está andando**. Así el panel y la palabra que lee el PLC no pueden
discrepar.

Con `influxdb` hay una limitación obvia y vale decirla: mientras InfluxDB está caído, el
punto que dice que se cayó no llega en el momento. El backend guarda en memoria lo que no
pudo escribir —hasta 6000 puntos: ~5 min con 2 cámaras, ~3 min con 4— y lo manda cuando el
servidor vuelve,
cada punto con su instante: un corte corto aparece después como `influxdb = 0` en su lugar
de la serie, y uno más largo deja un hueco donde se descartaron los puntos más viejos. Al
cerrar la aplicación, lo pendiente se pierde. Para lo que interesa, que son los otros cinco
canales, alcanza.

**El tope está pensado para InfluxDB en el mismo equipo**, donde un corte es el servicio que
se reinicia o que todavía no levantó cuando arrancó la aplicación: segundos, o un par de
minutos. Para el arranque, lo que corresponde es que el servicio de la aplicación dependa
del de InfluxDB, no un buffer más grande. Agrandarlo para cubrir un corte de red largo pide
antes partir la escritura de recuperación, que hoy sale entera en un solo pedido (ver el
docstring de `influxdb_backend.py`). Del mismo equipo sale también la otra cuenta a
cuidar: InfluxDB comparte el disco con el dataset, así que el bucket lleva una retención.

## Lo que NO va a InfluxDB

| Dato | Dónde va | Por qué no acá |
|---|---|---|
| Los frames | dataset en disco, streams de video | InfluxDB es de series numéricas |
| Cada detección | JSON del dataset, al lado del anotado | son cientos por frame |
| Cada detalle de cada resultado | dataset y registros | la muestra ya trae media, desvío y rango; el detalle por frame es del dataset |
| Las zonas térmicas por nombre | log | sus nombres cambian entre equipos |

## Verificaciones típicas

```sql
-- ¿El equipo estuvo midiendo? Huecos = estuvo caído o sin publicar.
-- `result_count` es cuántos resultados entraron a cada muestra: si baja, se infiere menos.
from(bucket: "…") |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "inference" and r._field == "result_count")

-- Una métrica con su media y su máximo en el mismo panel: una línea por `stat`.
from(bucket: "…") |> range(start: -6h)
  |> filter(fn: (r) => r._measurement == "inference" and r.kind == "result"
                       and r._field == "count_person" and (r.stat == "mean" or r.stat == "max"))
  |> group(columns: ["stat"])

-- ¿Qué etapa se lleva el tiempo? Los picos, por etapa.
from(bucket: "…") |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "inference" and r.kind == "time" and r.stat == "max")
  |> group(columns: ["_field"])

-- ¿Cuántas mediciones se descartaron, y por qué?
from(bucket: "…") |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "inference" and r._field == "invalid_reason")
  |> group(columns: ["_value"]) |> count()

-- Tráfico de entrada de cada interfaz, una línea por interfaz.
from(bucket: "…") |> range(start: -1h)
  |> filter(fn: (r) => r._measurement == "network" and r._field == "rx_mbps")
  |> group(columns: ["interface"])

-- ¿Se está quedando sin disco?
from(bucket: "…") |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "system" and r._field == "disk_free_gb")

-- ¿Alguna cámara perdió fps?
from(bucket: "…") |> range(start: -6h)
  |> filter(fn: (r) => r._measurement == "camera" and r._field == "fps_estimated")
  |> group(columns: ["camera"])
```

## Publicar cada resultado

Si una instalación necesitara un punto por resultado —depurar una puesta en marcha, o un
proceso donde cada frame es una medición que no se puede promediar—, el cambio es chico y
está en un solo lugar. En `_on_result_ready()` de `main.py`, en vez de dejar el resultado
en el buzón:

    pair_tags = {**self._telemetry_tags,
                 "camera": result.camera_slot, "pipeline": result.pipeline_slot}
    for point_tags, fields in _build_inference_points([result]):   # el mismo armador
        self._telemetry.push_data(_MEASUREMENT_INFERENCE, fields,
                                  tags={**pair_tags, **point_tags},
                                  time_s=result.timestamp_s)

Con un solo resultado, `sample_count` da 1, `stat=std` da 0 —que es la respuesta correcta:
no hay dispersión que reportar— y `mean`, `min` y `max` son el valor. **La forma de la serie no
cambia**, así que los dashboards siguen andando: es la misma consulta con más puntos.

Lo que conviene mirar antes:

| | Agregado (hoy) | Por resultado |
|---|---|---|
| Puntos/s de `inference`, 2 cámaras a 5 resultados/s | 14 | 70 |
| Margen ante un InfluxDB trabado, cola de 1000 | ~53 s con todo lo demás | ~13 s |
| Dispersión dentro del segundo | la da `stat=std` | se ve en los puntos |

Publicar por resultado repite el mismo valor en `mean`, `min` y `max` y un 0 en `std`: el
mismo dato ocupa siete puntos.

Y hay que sacar el buzón del camino: si se publica por resultado **y** además queda el
tick, la misma medición sale dos veces.
