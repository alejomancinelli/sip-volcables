# Lo que escribe el PLC

Qué registros puede escribir el PLC, cómo, y qué pasa del lado del equipo. Es el documento
del integrador que programa el PLC, y de quien atiende un llamado de «el PLC escribe y el
equipo no hace nada» o «el PLC recibe una excepción».

La tabla de registros de cada instalación está en [modbus_map.md](modbus_map.md) —o en
`modbus_map.csv`, para abrir en Excel—. Este documento explica cómo se usan; qué registro
es cuál lo dice esa tabla.

## Qué escribe el PLC y qué no

El mapa tiene dos bloques, con la regla de siempre: **el registro N es el 4000N**.

| Bloque | Lo escribe | Qué hay |
|---|---|---|
| 40001-40100 | el equipo | estado, salud, resultados de la medición y los umbrales del proceso publicados para el HMI |
| 40101-40150 | el PLC | las variables que declara cada instalación |

**El PLC escribe sólo las filas con acceso «escribe el PLC»** en la tabla, y sólo dentro de
su columna «Rango». Todo lo demás se lee y nada más: el equipo contesta con una excepción
cualquier escritura fuera de esas filas.

El PLC **no prende ni apaga nada** —las mediciones, las cámaras, las comunicaciones— ni cambia la
configuración del equipo. Los umbrales del proceso que aparecen en el bloque de lectura son
una copia para que el HMI los muestre: los cambia el operador desde la pantalla del equipo,
y el registro sigue el valor nuevo en el segundo siguiente.

## Cómo se escribe

- **Funciones**: FC06 para un registro, FC16 para varios seguidos. La lectura es siempre
  FC03. No se sirven coils, FC23 ni las demás funciones.
- **Unit id**: el configurado en el equipo, en la configuración de Modbus («Slave id», por
  defecto 1). Por TCP, un unit id equivocado recibe la excepción 0x0B.
- **Escala**: un valor con escala x100 se escribe multiplicado: 1,50 m/s se escribe 150. El
  rango de la tabla está en la unidad física, antes de multiplicar.
- **Sólo enteros de 0 a 65535**: no hay negativos —una temperatura bajo cero necesita un
  offset acordado con el proyecto— ni valores de 32 bits.

Lo que contesta el equipo:

| Respuesta | Cuándo |
|---|---|
| Normal (FC06 devuelve el eco) | Se escribió. Releer con FC03 da el mismo valor |
| Excepción 0x02 | La dirección no es una fila que escribe el PLC, o la función no se sirve |
| Excepción 0x03 | El valor está fuera del rango de la fila |
| Excepción 0x0B | El unit id no es el del equipo (sólo por TCP) |

**Un FC16 se acepta entero o no se acepta.** Si uno solo de sus registros es una dirección no
escribible o un valor fuera de rango, la petición entera se rechaza y no se escribe ninguno:
no queda la primera mitad escrita. El bloque de status del cliente Modbus del PLC (por
ejemplo, `STATUS` de `MB_CLIENT` en Siemens) informa la excepción; el programa del PLC tiene
que mirarlo después de cada escritura.

## Dos formas de usar una variable

Lo que escribe el PLC el equipo lo recibe como una variable con nombre. Qué significa —y si
se lee como un nivel o como un evento— lo decide cada proyecto, y lo dice la descripción de
la fila en la tabla.

### Nivel: un ON/OFF o un valor

«El proceso está OK, se puede inferir», «la cinta va a 1,5 m/s», «el producto es el 42».
Importa el valor que **tiene**, mientras lo tiene.

- **Escribir de forma cíclica**, por ejemplo un FC16 sobre todas las variables en cada ciclo
  del PLC. Reescribir el mismo valor no cambia nada del lado del equipo, y así un reinicio
  del equipo se repara solo en el ciclo siguiente.
- **El 0 es el reposo**: «no hay evento», «el PLC no manda nada». Es el valor que tienen
  todas las variables cuando el equipo arranca.

### Evento: un contador de disparo

«Medí ahora». Importa que el valor **cambie**: cada cambio es un evento.

- **Se usa un contador, no un bit.** El PLC escribe el número siguiente cada vez que quiere
  un evento: 1, 2, 3… hasta 65535, y vuelve a 1. Una sola escritura por evento.
- **No se escribe 0**: el 0 es «nunca disparó», el valor después de un reinicio, y no pide
  nada.
- **Reescribir el mismo número no vuelve a disparar.** Es lo que deja escribir el contador
  de forma cíclica junto con las demás variables.
- **El número identifica el disparo.** Queda guardado con los datos de esa medición, así que
  si el PLC anota «disparo 37 = 1520 kg en la báscula», el disparo 37 se encuentra del lado
  del equipo.

Por qué no un bit que se pone en 1 y vuelve a 0: si las dos escrituras caen dentro del mismo
período en que el equipo aplica lo escrito (ver «Tiempos»), el equipo no ve ningún cambio y
el disparo se pierde sin que nadie se entere. Con un contador, a lo sumo ve un salto de 5 a 7
y sabe que fueron dos.

**El equipo no contesta un disparo.** No hay un registro de «medición terminada»: los
resultados se leen de forma continua, como siempre. Un proyecto que necesite confirmarlo
declara su propio registro en la tabla.

## Tiempos

- **El equipo aplica lo que escribió el PLC cada 100 ms.** Lo escrito dentro de ese lapso
  llega como un solo cambio, con el valor final: un ON/OFF que va y vuelve en menos de
  100 ms no se ve.
- **Una variable de nivel toma efecto en la imagen siguiente** de la cámara: el equipo no
  espera a nada más.
- **Los registros que escribe el equipo se actualizan una vez por segundo**, el latido
  (40081) incluido.

## Si el equipo se reinicia

- El latido (40081) se detiene mientras tanto y vuelve a contar desde abajo: es como el PLC
  se entera.
- Todo lo que escribió el PLC vuelve a 0. Las variables de nivel se reponen solas si el PLC
  las escribe de forma cíclica.
- Un disparo que cae durante el reinicio se pierde: no hay medición con ese número.
- Los umbrales publicados están desde el primer momento: el equipo los escribe antes de
  aceptar conexiones, así que el PLC nunca lee un 0 que no es el valor.

## Seguridad

**Modbus no tiene autenticación.** Cualquiera que llegue al puerto del equipo puede escribir
las filas que escribe el PLC, dentro de su rango, y nada más. Si eso importa, el puerto se
restringe en la red —firewall, VLAN—, no en el equipo.

## Cuando algo no anda

- **El PLC recibe una excepción**: la tabla de arriba dice por qué. El log del equipo deja
  una línea por cada escritura rechazada, con la dirección, el valor y el motivo; la misma
  dirección con el mismo motivo se repite a lo sumo una vez por minuto, con cuántas se
  callaron en el medio.
- **El PLC escribe y el equipo no hace nada**: el log del equipo deja una línea por cada
  valor que cambia (`[Modbus] PLC: nombre: viejo → nuevo`), a lo sumo una cada 10 s por
  variable, con cuántos cambios se callaron en el medio. Si no aparece, el valor no
  cambió —reescribir el mismo número de un contador no dispara— o la escritura fue rechazada.
  La pestaña Modbus del diagnóstico muestra lo que el PLC dejó en cada fila.
- **Probarlo sin PLC**: Modbus Poll, QModMaster o `mbpoll` contra el puerto del equipo
  escriben igual que un PLC. Con la dirección base 0 —la opción por defecto de casi todos—
  el registro 40101 se pide como 100.
