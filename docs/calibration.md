# Calibrar un equipo en la puesta en marcha

Cómo se usan los calibradores de una instalación: dónde están, cómo se abren, qué hay que
dejarles y qué se hace con lo que devuelven. Es el documento de quien pone el equipo en
marcha, con el equipo delante. Cómo se escribe y se registra un calibrador nuevo es otro
documento, para quien desarrolla el fork: [calibration/README.md](../calibration/README.md).

Un calibrador mide algo de esta planta —la escala de la imagen, un umbral contra muestras
pesadas a mano— y devuelve un valor que va al `config.yaml` de la instalación. Se corre en
la puesta en marcha y otra vez cuando cambia el montaje: se mueve una cámara, se cambia un
lente, entra otro producto. Cuándo hay que repetir cada uno lo dice su propia guía.

## Qué calibradores hay

Depende del fork: el template no trae ninguno, y cada proyecto agrega los suyos.

- **En el entregable**, cada calibrador es un lanzador al lado del de la app —`Calibrar
  mallas.cmd`, `Calibrar escala.cmd`— y tiene una carpeta en
  `installation/calibration/<nombre>/` con su guía, `README.md`, que explica cómo se usa
  ese en particular. **Leerla antes de empezar**: dice qué hay que preparar, y una corrida
  sin eso termina bien y no sirve.
- **Desde fuentes**, `main.py --calibrate` sin nombre lista los que hay, con una línea cada
  uno. La guía de cada uno está en `calibration/<nombre>/README.md` del repo.

## Antes de empezar

- **La instalación tiene que arrancar.** El calibrador lee el mismo `config.yaml` que la
  app —el de `installation/`—, así que las cámaras, el ROI y lo que dependa de la planta
  tienen que estar configurados antes.
- **Si el calibrador mira el video en vivo, la app tiene que estar cerrada.** Una cámara
  GigE la controla un solo programa por vez: con la app abierta, el calibrador no consigue
  la cámara.
- **Lo que pida su guía**: el patrón impreso, las muestras pesadas, el vidrio limpio, la
  cámara enfocada.

No hace falta la licencia. La puesta en marcha viene antes que ella —la solicitud se genera
en este mismo equipo—, y un calibrador no publica nada al PLC ni a la telemetría.

## Cómo se abre

**En Windows**, doble clic en el lanzador. La consola queda abierta al terminar, esperando
una tecla, para que se pueda leer el resultado. Para pasarle
opciones, desde una consola abierta en la carpeta de la instalación:

    "Calibrar mallas.cmd" --opcion valor

**En la Jetson**, desde una terminal en la carpeta de la instalación:

    bash "Calibrar mallas.sh" --opcion valor

**Desde fuentes**, con el intérprete del venv:

    .venv\Scripts\python.exe main.py --calibrate mesh --opcion valor
    .venv\Scripts\python.exe main.py otro-config.yaml --calibrate mesh

Todo lo que va después del nombre es del calibrador: qué opciones tiene lo dice su guía.
Una ruta de config distinta va **antes** de `--calibrate`, porque lo que va después se lo
lleva el calibrador.

## Dónde van las entradas

Cada calibrador tiene su carpeta, armada al instalar:

    installation/
        config.yaml
        calibration/
            <nombre>/
                README.md         la guía de este calibrador
                samples/          lo que entra: lo que hay que dejarle antes de correrlo
                    LEEME.txt     qué va acá y con qué formato
                    example.csv   un ejemplo del formato, si el calibrador lo trae
                out/              lo que sale: un reporte por corrida
                    LEEME.txt

Qué subcarpetas tiene cada uno depende del calibrador, y cada una lleva un `LEEME.txt` que
dice qué espera. Desde fuentes es la misma estructura, en `calibration/<nombre>/` del repo;
ahí las entradas y las salidas no se versionan, porque son de una planta y de un día.

## Qué sale, y qué hacer con eso

**Cada corrida deja un reporte en `out/`**, con la fecha y la hora en el nombre: qué entró,
qué valor salió y qué se descartó. Qué mirar para saber si la corrida es buena —el error,
cuántas muestras entraron— lo dice la guía del calibrador. Una corrida mala no se arregla
editando el resultado: se corrige la entrada y se vuelve a correr.

**El valor llega al config de una de dos formas**, y la guía dice cuál:

- **Lo escribe el calibrador.** Queda en `installation/config.yaml`, y el config anterior
  en `config.yaml.bak`, al lado. El `.bak` guarda sólo la versión inmediatamente anterior:
  dos corridas seguidas pierden la primera.
- **Lo propone.** Un valor que cambia lo que lee el PLC no se escribe solo: el calibrador lo
  imprime en la consola y lo deja en el reporte, y una persona lo copia al config —desde la
  pestaña de configuración de la app o con un editor de texto—.

En los dos casos **después se reinicia la app**. Algunos valores el equipo los toma en
caliente, pero los de las cámaras sólo al arrancar, y reiniciar es la única forma de estar
seguro de que todo leyó el valor nuevo. Después, verificar en la app lo que la guía diga
que hay que mirar.

## Después de calibrar

- **Actualizar el programa no toca la calibración.** Una actualización reemplaza `program/`
  entero, y el config, las entradas y los reportes viven en `installation/`.
- **El valor calibrado es de esta planta.** Si el repo del fork versiona el config de la
  instalación, el valor se trae al repo; si no, la próxima entrega lo pisa con el del repo.
- **El reporte de la corrida que se entregó se guarda.** Se copia a `docs/` del repo del
  fork y se nombra en el commit que trae el valor al config: es la evidencia de con qué se
  calibró, y en el equipo nadie la va a buscar.

## Cuando algo falla

El calibrador escribe en el mismo log que la app —`installation/data/logs/app.log`, salvo
que el config diga otra carpeta en `system.paths.logs`—, y ahí queda el motivo de lo que no
llega a mostrarse en la consola.

| Qué se ve | Por qué | Qué hacer |
|---|---|---|
| «No hay un calibrador «x»», con la lista de los que hay | el nombre está mal escrito | usar uno de la lista; desde el lanzador no pasa |
| «Esta instalación no tiene ninguno registrado» | el ejecutable se compiló sin ese calibrador | recompilar con el fork que lo registra |
| `ModuleNotFoundError: calibration...` | el registro apunta a un módulo que no está en el ejecutable | recompilar; la suite del fork tiene que haberlo frenado |
| La cámara no abre | la app está abierta y tiene la cámara | cerrar la app y volver a correr el calibrador |
| Falta una carpeta de entrada | se borró después de instalar | crearla con el mismo nombre; qué va adentro lo dice la guía del calibrador |
