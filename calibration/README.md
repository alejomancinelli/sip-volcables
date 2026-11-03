# Calibradores

Herramientas de puesta en marcha: corren una vez por instalación —o cuando cambia el
montaje—, miran la línea o lo que la línea dejó, y devuelven números que terminan en el
`config.yaml` de esa planta. Dos ejemplos de forks: la escala px/cm medida sobre el video en
vivo, y los umbrales de malla ajustados contra zarandeos pesados a mano.

El template no trae ningún calibrador, porque qué se calibra es lo más específico de cada
instalación. Trae dónde van, cómo se ordenan y qué reglas siguen.

Este documento es para quien escribe un calibrador. Cómo se usan en la planta —abrirlos,
dejarles las entradas, llevar el resultado al config— está en
[docs/calibration.md](../docs/calibration.md), y vale para todos; lo propio de cada uno va
en su `README.md`.

## Qué es un calibrador y qué no

Un calibrador **produce valores del config de una instalación**. Por eso no es:

- **Una prueba con hardware.** Lo de `manual_test/` verifica que algo anda y no deja nada
  en el config.
- **Una función de la app.** La referencia de la óptica (`lens_health`) se calibra desde
  la pestaña de configuración porque el operador la repite cada vez que limpia el vidrio.
  El criterio es quién la corre: si es el operador, en producción, va en la UI; si es quien
  pone el equipo en marcha, una vez, va acá.

## La jerarquía

    calibration/
        README.md                     esto
        registry.py                   la tabla de calibradores: una línea por cada uno
        <tool>/                       un calibrador por carpeta: scale, mesh, lens
            <tool>_calibrator.py      el punto de entrada: run(config, argv) -> int
            README.md                 cómo se usa, para quien calibra (ver más abajo)
            samples/                  lo que entra: una corrida, de una planta, de un día
                .gitkeep
                example.csv           opcional: un ejemplo del formato, que sí se versiona
                <kind>/               opcional: una subcarpeta por tipo de entrada
            out/                      lo que sale: reporte, tablas, gráficos
                .gitkeep

**Una carpeta por calibrador**, con el nombre en inglés y en snake_case como cualquier
módulo, y sin `__init__.py`: como `system/` y `tools/`, son paquetes de espacio de nombres. Lo que use un solo calibrador vive en su carpeta, al lado del punto de entrada. Lo
que compartan dos y no sepa de calibración —un ajuste de curva, leer un JSON del dataset—
va a `tools/`, donde lo puede usar cualquiera.

**`samples/` y `out/` no se versionan**, salvo su `.gitkeep` y el ejemplo de formato (las
reglas están en el `.gitignore` de la raíz). Una corrida es de esa planta y de ese día, y
lo que queda de ella es el valor que produjo, que va al `config.yaml` del fork y ese sí se
versiona. Si una corrida tiene que quedar como evidencia —la que fijó los umbrales que se
entregaron—, su reporte se copia a `docs/` del fork y se nombra en el commit que cambia el
config.

## Las reglas

**`run(config, argv) -> int` es el punto de entrada, y el config se recibe.** `config` es
el `ConfigManager` de la instalación, ya resuelto; `argv` es lo que el calibrador recibe en
la línea de comandos y parsea él; el entero es el código de salida. Un calibrador que abre
su propio YAML puede terminar leyendo otro archivo que el que lee la app, porque compilado
el programa y la instalación están en carpetas distintas. Los parámetros de la herramienta
—tolerancias, el tamaño del tablero— son flags; los de la planta salen de `config`.

**Las rutas salen de `DATA_DIR`, nunca de `__file__` ni del directorio actual.** Se piden
con `paths.resolve("calibration/<tool>/samples", ...)`. Desde fuentes `DATA_DIR` es la raíz
del repo y la ruta cae en esta carpeta; compilado cae en `installation/calibration/<tool>/`,
al lado del config de esa planta, y no en `program/`, que se reemplaza entero en cada
actualización. Es la misma ruta relativa en los dos casos, y por eso el calibrador no
necesita saber cómo corre. `__file__` en un compilado apunta a `program/`, y el directorio
actual depende de cómo se lanzó.

**Escribir el valor o proponerlo es una decisión del calibrador, y su README la dice.**
Si escribe, lo hace con `config.set()` y `config.save()`, que dejan el `.bak` y mantienen
un solo dueño del archivo; nunca reescribiendo el YAML por su cuenta. Un valor que cambia
lo que lee el PLC se propone y no se escribe: se imprime y queda en el reporte de `out/`, y
lo copia una persona, porque cambia todas las mediciones que vengan después.

**Cada corrida deja su reporte en `out/`**, con el instante en el nombre
(`<AAAAMMDD_HHMMSS>_<qué>.<ext>`): lo que entró, lo que salió y lo que se descartó y por
qué. Un calibrador que sólo imprime en la consola deja el resultado en una ventana que
alguien va a cerrar.

**Del repo importa lo que necesite, y del repo no lo importa nadie.** Un calibrador está al
nivel de `main.py`: puede usar `system/`, `tools/` y `ui/`. Lo que calcula va en funciones
puras, separadas de la lectura de archivos y de la ventana, para que `test/calibration/`
lo pruebe sin muestras ni pantalla, igual que el resto de la suite. Una librería que use
—matplotlib, por ejemplo— va a `requirements.txt` como cualquier otra dependencia.

## Cómo se registra, y cómo llega al equipo de la planta

**Un calibrador comparte el ejecutable de la app**: es un subcomando, no un programa
aparte.

    .venv\Scripts\python.exe main.py --calibrate <tool> [opciones]     desde fuentes
    "Calibrar <algo>.cmd" [opciones]                                   en el entregable

Así llega compilado a una planta donde no hay Python, y recibe el config de la instalación
ya resuelto. Un `.exe` por calibrador sería otra compilación de Nuitka, otro binario sin
firmar para el antivirus del cliente, y otro lugar que elige qué `config.yaml` leer.

**Se registra con una línea en `registry.py`**, y es lo único que se toca fuera de su
carpeta:

    CalibrationTool(
        name="mesh",                                    # el subcomando y la carpeta
        module="calibration.mesh.mesh_calibrator",      # el que expone run()
        launcher="Calibrar mallas",                     # el lanzador del entregable
        description="Ajusta los umbrales de malla contra los zarandeos pesados.",
        folders=(                                       # lo que el entregable crea vacío
            ("samples/sieve", "Los CSV del zarandeo: timestamp;class;grams.\n"),
            ("out", "Lo que deja cada corrida, con el instante en el nombre.\n"),
        ),
    ),

De esa fila sale todo lo demás, y por eso ningún archivo de la maquinaria se edita para
agregar un calibrador:

- **`main.py`** despacha `--calibrate <name>` antes de armar la ventana y antes de la
  licencia —la puesta en marcha viene antes que ella, y un calibrador no publica nada—,
  e importa el módulo recién entonces: el arranque normal no paga las dependencias de
  ningún calibrador. Un nombre que no está lista los que hay.
- **`build/build.py`** compila el paquete `calibration` entero, en las dos formas del
  build. Hace falta pedirlo porque el registro importa cada calibrador por nombre, y un
  import armado con un string Nuitka no lo sigue.
- **`build/make_release.py`** escribe un lanzador por calibrador al lado de los de la app
  —en Windows deja la consola abierta al terminar, para que se lea el resultado— y crea
  sus `folders` en `installation/calibration/<name>/`, cada una con un `LEEME.txt` que
  tiene la nota de la fila y el `example.*` que haya en el repo. El `README.md` del
  calibrador viaja con ellas, porque quien lo lee está en la planta. Antes de armar nada
  revisa la tabla, y una fila mal escrita corta el entregable con el motivo.

`installation/` es lo que sobrevive a una actualización, así que las entradas y los
reportes de una calibración no se pierden al reemplazar `program/`. Y es la misma ruta
relativa a `DATA_DIR` que desde fuentes, que es lo que hace funcionar la regla de las rutas.

`test/calibration/test_registry.py` revisa la tabla que se entrega: en el template está
vacía, y en un fork es el test que frena una fila mal escrita o un módulo que no está donde
dice. Las pruebas de lo que calcula cada calibrador van al lado, en
`test/calibration/<tool>/`.

## La documentación de cada calibrador

Cada `<tool>/README.md` es para quien calibra, no para quien programa: alguien con el
equipo delante que tiene que saber qué preparar y qué hacer con lo que sale. Lleva estas
secciones, en este orden:

    # <Qué calibra>

    ## Para qué
    Qué valor produce, qué clave del config llena, y qué pasa en la planta si está mal.

    ## Cuándo se repite
    En la puesta en marcha, y qué cambio en la línea obliga a correrlo otra vez: mover la
    cámara, cambiar el lente, otro producto.

    ## Qué hace falta antes
    El patrón, las muestras pesadas, el vidrio limpio, la cámara enfocada. Lo que, si
    falta, hace que la corrida no sirva aunque termine bien.

    ## Qué va en samples/
    Cada tipo de entrada, su formato y de dónde sale. Apunta al example que se versiona.

    ## Cómo se corre
    El comando, desde fuentes y desde el entregable, con las flags que tiene.

    ## Qué deja en out/ y cómo se lee
    Cada archivo, y qué mirar para saber si la corrida es buena: el error, cuántas
    muestras entraron, qué se descartó.

    ## Dónde termina el valor
    Si lo escribe solo o lo propone; en ese caso, qué clave se edita y cómo se verifica
    después en la app.
