# Estructura de la telemetría en MQTT

Qué tópicos publica el equipo y qué trae cada mensaje. Es el contrato con quien se
suscriba: una vez que un flujo de Node-RED o un SCADA lee un tópico, moverlo lo rompe.

**Estado: implementado.** El backend es `system/telemetry/backends/mqtt_backend.py` y su
sección de config es `telemetry.mqtt`.

El público es quien consume el dato desde afuera en vivo —un Node-RED, un SCADA, un broker
que reparte a varios— y no quiere ni una base de series ni el PLC en el medio.

## Los datos son los mismos que los de InfluxDB

**No hay una lista de campos propia de MQTT.** Los registros los arma un solo lugar
—`PersistenceThread`, en `system/telemetry/persistence.py`— y los dos backends reciben
exactamente el mismo batch: si InfluxDB recibe `fps_estimated`, MQTT lo publica con ese
nombre y ese valor.

Así que **las series, los tags y los nombres de campo están en
[`influxdb.md`](influxdb.md) y no se repiten acá**. Duplicar esas tablas sería tener el
contrato en dos archivos, que es justo el bug que se evita teniéndolo en uno.

Lo que sí es propio de MQTT, y es lo que documenta este archivo, es **la forma en el
cable**: en qué tópico sale cada serie y cómo se ve el JSON.

## Los tópicos

Uno por serie, bajo el prefijo del equipo:

    <topic_base>/<measurement>

`topic_base` sale de `telemetry.mqtt.topic_base` del config —por convención el
`project_id`— y `measurement` es el nombre de la serie. Con `topic_base: planta_norte`:

| Tópico | Qué sale por muestra |
|---|---|
| `planta_norte/system` | 1 mensaje: hardware del equipo |
| `planta_norte/network` | 1 por interfaz de red |
| `planta_norte/camera` | 1 por cámara |
| `planta_norte/services` | 1 mensaje: los seis canales de salida |
| `planta_norte/inference` | 7 por par (cámara, pipeline) con resultados nuevos, partidos por `kind` y `stat` |

**La cámara y el pipeline no van en el tópico, van adentro del mensaje.** El tópico nombra
la serie, no la instancia: agregar una cámara no agrega tópicos, agrega mensajes en el que
ya existe. Un suscriptor que quiera una sola cámara filtra por el campo `camera`; uno que
las quiera todas se suscribe una vez y no se toca al sumar la tercera.

## El mensaje

Un objeto JSON plano con **los tags y los fields fusionados**, más `time`:

    {**tags, **fields, "time": <epoch_s>}

Plano porque MQTT no distingue tag de field: esa separación es de InfluxDB, y un suscriptor
que recibe dos objetos anidados tiene que saber cuál mirar. Fusionados, el mensaje se lee
de una.

`time` es **el instante en que se midió**, epoch en segundos, no el de la publicación.
Entre los dos puede pasar una ventana entera (ver el ritmo, más abajo), así que un
suscriptor que selle con la hora de llegada deforma la serie.

### Ojo: los fields pisan a los tags

La fusión es en ese orden, así que **una métrica que se llame igual que un tag lo
sobreescribe sin avisar**. Los tags son `device`, `interface`, `camera`, `pipeline`, `kind` y `stat`: una métrica del
analyzer llamada `camera` reemplazaría al slot en el mensaje y el suscriptor perdería de
qué cámara vino. Es la misma razón por la que en el mapa Modbus el nombre de la métrica es
el contrato: los nombres se eligen una vez y valen para todos los destinos.

## Ejemplos

Los valores son de ejemplo; los nombres y los tipos son los reales.

`planta_norte/system`

```json
{
  "device": "vision_01",
  "cpu_usage_pct": 34, "gpu_usage_pct": 61,
  "cpu_temp_c": 52, "gpu_temp_c": 58,
  "ram_used_mb": 3820, "ram_total_mb": 16384,
  "disk_free_gb": 214, "power_w": 18.4, "overcurrent_count": 0,
  "engine_vic_active": 1, "engine_vic_mhz": 448,
  "engine_nvdec_active": 1, "engine_nvdec_mhz": 716,
  "engine_nvenc_active": 0, "engine_nvenc_mhz": 0,
  "time": 1757340012.412
}
```

`planta_norte/network` — uno por interfaz

```json
{
  "device": "vision_01", "interface": "eth0",
  "rx_mbps": 12.6, "tx_mbps": 0.9,
  "time": 1757340012.412
}
```

`planta_norte/camera` — uno por cámara

```json
{
  "device": "vision_01", "camera": "camera_1",
  "connected": 1, "capture_enabled": 1, "misconfigured": 0,
  "fps_estimated": 9.8, "temperature_c": 41.2, "illumination_pct": 63,
  "lens_state": 0, "lens_sharpness_pct": 97,
  "time": 1757340012.418
}
```

`planta_norte/services` — el 1 significa lo mismo que el bit que lee el PLC: levantó y está
andando.

```json
{
  "device": "vision_01",
  "modbus_tcp": 1, "modbus_rtu": 0,
  "video_http": 1, "video_rtsp": 0,
  "influxdb": 1, "mqtt": 1,
  "time": 1757340012.420
}
```

`planta_norte/inference` — siete por par (cámara, pipeline), uno por cada combinación de
`kind` y `stat` (ver [`influxdb.md`](influxdb.md#inference--el-resultado-agregado)). Acá van
tres de los siete; `load_height_mm` y `belt_coverage_pct` son métricas de ejemplo del
analyzer de un fork, y el resto es de la maquinaria. Los mensajes de `stat` `std`, `min` y
`max` tienen los mismos campos que el de `mean`.

```json
{
  "device": "vision_01", "camera": "camera_1", "pipeline": "pipeline_1", "kind": "count",
  "result_count": 5, "invalid_count": 1, "invalid_reason": "dark_frame", "sample_count": 4,
  "time": 1757340012.180
}
{
  "device": "vision_01", "camera": "camera_1", "pipeline": "pipeline_1",
  "kind": "time", "stat": "max",
  "total_ms": 241.0, "detector_ms": 212.4, "cycle_ms": 497.5,
  "time": 1757340012.180
}
{
  "device": "vision_01", "camera": "camera_1", "pipeline": "pipeline_1",
  "kind": "result", "stat": "mean",
  "confidence_pct": 87.4, "detection_count": 12.0,
  "load_height_mm": 812.4, "belt_coverage_pct": 71.3,
  "time": 1757340012.180
}
```

**El `time` de `inference` es más viejo que el de las otras tres** y es a propósito: es el
del último resultado que entró a la muestra, no el del tick. El punto vale por cuándo se
midió.

## El ritmo: ráfagas, no un mensaje por segundo

Los dos intervalos son los mismos que los de InfluxDB y están explicados allá: se **mide**
cada 1 s (`main.py`) y se **escribe** cada 10 s (`persistence.py`). Para un suscriptor eso
significa que los mensajes **no llegan de a uno por segundo**: llegan diez de golpe cada
diez segundos, cada uno con su propio `time`.

Un suscriptor que grafique tiene que usar el `time` de cada mensaje. Uno que sólo quiera el
valor actual se queda con el último de la ráfaga —que igual puede tener hasta 10 s—.

## Entrega: QoS 0, sin retain y sin sesión persistente

| | Qué implica para el suscriptor |
|---|---|
| **QoS 0** | lo que no se pudo publicar no se recupera |
| **sin retain** | al suscribirse no llega el último valor: hay que esperar la próxima ventana, hasta 10 s |
| **sin sesión persistente** | desconectarse y volver no recupera lo de mientras tanto |

Es lo que corresponde para telemetría periódica: el próximo valor llega enseguida y es más
útil que el viejo. Un dato que **no** puede perderse no va por acá: va al PLC por Modbus,
que es sincrónico y lo lee el que decide.

Mientras el backend no tenga sesión con el broker, `write()` descarta. Un corte deja un
hueco, y el hueco también informa.

## Identidad y credenciales

- El **client_id** es `system.device_id` del config. Dos equipos con el mismo `device_id`
  contra el mismo broker **se echan mutuamente**: es lo primero que hay que mirar si una
  serie aparece a saltos.
- El **usuario** va en `telemetry.mqtt.user` porque identifica; la **password** sale de la
  variable de entorno `MQTT_PASSWORD` y nunca del config, que se versiona y se edita desde
  la UI.
- Sin `paho-mqtt` instalado el backend degrada a no-op y lo deja en su `status`. No hay que
  preguntarle si está disponible.

## El canal es de una sola dirección

El backend **publica y no se suscribe a nada**: no hay comando, ni configuración, ni
disparo que entre por MQTT. Lo que entra al equipo desde afuera es el bloque escribible del
mapa Modbus, que el template no usa. Un proyecto que necesite recibir por MQTT está
agregando un camino de entrada, no ampliando este backend.

## Agregar un tópico

No se agrega acá. Un tópico nuevo es una serie nueva, y una serie nueva es un `push_data()`
más en `main.py` con un `measurement` que todavía no existe: sale por MQTT y por InfluxDB a
la vez, sin tocar ningún backend. Si además tiene que llegar al PLC, la métrica necesita su
fila en `system/modbus/register_map.yaml` con el mismo nombre.
