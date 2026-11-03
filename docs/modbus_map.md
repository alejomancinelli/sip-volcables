# Mapa de registros Modbus

Generado por `python -m system.modbus.export_map` desde `system/modbus/register_map.yaml`. No editar a mano: los cambios se hacen en el YAML y se vuelve a generar.

Holding registers, direccionamiento base-1: el registro N es el que el PLC lee como 4000N. Todo se lee con FC03. Las filas con acceso «escribe el PLC» se escriben con FC06 o FC16, dentro de su rango: fuera de rango la respuesta es la excepción 0x03, y sobre cualquier otro registro la 0x02. Cómo y cuándo escribir está en `docs/plc_commands.md`.

Los valores con escala viajan multiplicados por ella: un registro con escala x10 y valor 415 son 41,5 en su unidad. El rango está en esa misma unidad.

| Registro | PLC | Nombre | Descripción | Unidad | Escala | Acceso | Rango | Origen |
|---|---|---|---|---|---|---|---|---|
| 1 | 40001 | system_status_bitfield | Estado del sistema (bitfield) | - | - | lectura | - | health |
| 2 | 40002 | com_status_bitfield | Canales de comunicación (bitfield) | - | - | lectura | - | health |
| 51 | 40051 | camera_1_state_bitfield | Estado de la cámara 1 (bitfield) | - | - | lectura | - | health |
| 52 | 40052 | camera_1_temperature_c | Temperatura de la cámara 1 | °C | x10 | lectura | - | health |
| 53 | 40053 | camera_1_fps | Frames por segundo medidos de la cámara 1 | fps | x10 | lectura | - | health |
| 54 | 40054 | camera_1_illumination_pct | Brillo medio del ROI de la cámara 1 | % | - | lectura | - | inference |
| 81 | 40081 | heartbeat | Heartbeat / watchdog — cambia en cada ciclo mientras la app viva | - | - | lectura | - | health |
| 82 | 40082 | cpu_usage_pct | CPU en uso | % | - | lectura | - | health |
| 83 | 40083 | gpu_usage_pct | GPU en uso | % | - | lectura | - | health |
| 84 | 40084 | ram_used_mb | RAM usada | MB | - | lectura | - | health |
| 85 | 40085 | ram_total_mb | RAM total del equipo | MB | - | lectura | - | health |
| 86 | 40086 | disk_free_gb | Espacio libre en disco | GB | x10 | lectura | - | health |
| 87 | 40087 | cpu_temp_c | Temperatura de CPU | °C | x10 | lectura | - | health |
| 88 | 40088 | gpu_temp_c | Temperatura de GPU | °C | x10 | lectura | - | health |
| 89 | 40089 | power_w | Consumo estimado | W | x10 | lectura | - | health |
| 90 | 40090 | gpio_inputs_bitfield | Entradas digitales (bitfield) | - | - | lectura | - | health |
| 91 | 40091 | gpio_outputs_bitfield | Salidas digitales comandadas (bitfield) | - | - | lectura | - | health |
| 98 | 40098 | clock_epoch_s_high | Reloj del equipo, palabra alta (epoch UTC = alta*65536 + baja) | s | - | lectura | - | health |
| 99 | 40099 | clock_epoch_s_low | Reloj del equipo, palabra baja | s | - | lectura | - | health |
| 100 | 40100 | license_days_remaining | Días hasta el vencimiento de la licencia | días | - | lectura | - | health |
| 101 | 40101 | ramp_state | Plataforma — 0 sin información, 1 horizontal, 2 inclinada; con 2 el grano y la calza dejan de medir | - | - | escribe el PLC | 0-2 | plc |
