# Compilación con Nuitka

Las salidas de build no se versionan; sí este archivo, `build.py`, `make_release.py` y
`requirements.txt`.

## La forma del entregable

Nuitka compila código Python a C; PySide6, numpy, OpenCV y cualquier framework de
inferencia son extensiones ya compiladas que Nuitka **copia**, no compila. La pregunta no
es "¿se puede compilar la librería X?" sino "¿el entregable la lleva adentro o la busca en
la máquina?". Tres formas:

| Forma | Qué lleva | ¿Python en el destino? |
|---|---|---|
| Acelerada (sin `--standalone`) | sólo el código propio, compilado | **sí**, el venv completo |
| **Standalone** | todo, en una carpeta | no |
| Onefile | todo, en un `.exe` | no, pero descomprime a temp en cada arranque |

**Windows usa standalone y Linux usa la acelerada**, y lo decide `_ACCELERATED` en
`build.py`. Onefile queda descartado en la mayoría de los casos por el tiempo de
descompresión en cada arranque, y porque es la forma que más falsos positivos levanta en
los antivirus.

En Windows el equipo del cliente no tiene Python, y el standalone es lo que lo resuelve. En
una Jetson pasa lo contrario, y por eso ahí va la acelerada:

- **El standalone no termina.** Traduce a C todo lo que `main.py` alcanza —torch,
  torchvision, ultralytics y lo que ellos importan, miles de módulos— y con 8 GB de RAM son
  horas y un OOM. La acelerada compila sólo `system`, `tools` y `ui`: 1,7 minutos, el gcc
  más grande en 250 MB y el swap sin tocar (Orin NX 8 GB, 20W, Nuitka 4.2.2).
- **No ganaría nada.** CUDA, TensorRT, el driver y GStreamer tienen que salir del JetPack
  del equipo en cualquier forma, el `python3.10` del sistema está siempre, y la velocidad
  está en CUDA y no en el Python que lo llama: compilado mide lo mismo que desde fuentes.
- **Lo que hay que proteger es nuestro**, y eso sí se compila. Lo de terceros viaja suelto
  en `program/site-packages`: el venv que pasó `verify_env.py`, copiado tal cual.

El precio es que lo de terceros se puede editar en el equipo. Por eso `cryptography`, que
verifica la licencia, tiene un autocontrol en `system/license/verify.py`; compilarla no es
una opción, porque con Nuitka 4.2.2 su extensión de Rust carga vacía y ninguna licencia
valida.

## Cómo se compila

Nuitka no está en el venv del proyecto: se instala aparte, con su versión fijada en
`requirements.txt` (en la Jetson, con `-c setup/jetson/constraints.txt`):

    .venv\Scripts\python.exe -m pip install -r build\requirements.txt

Y después:

    .venv\Scripts\python.exe build\build.py                   compila
    .venv\Scripts\python.exe build\build.py --dry-run         muestra el comando y no compila
    .venv\Scripts\python.exe build\build.py --out build/out2  otro directorio de salida

**Las opciones viven en `build.py` y no acá**, con el motivo de cada una al lado. La razón
de que sea un script y no un comando para copiar es que una de las opciones no es una
constante: el `.exe` lleva la versión en sus propiedades, y ese número tiene un solo dueño
—`system/version.py`—. Escrito a mano acá sería una cuarta copia y la única que nadie
puede notar desactualizada.

`--dry-run` imprime el comando entero, así que sigue estando a mano para leerlo o para
correrlo con algo cambiado. Y lo que va después de `--` se agrega al final:

    .venv\Scripts\python.exe build\build.py -- --lto=yes

En Linux es lo mismo con `.venv/bin/python` y barras normales. **Se compila en la
plataforma donde va a correr**: Nuitka no compila cruzado, así que el entregable de una
Jetson se arma en una Jetson —ver «En la Jetson», más abajo—.

**El directorio de salida tiene que estar vacío**, y el script lo exige antes de arrancar.
Nuitka no pisa los `.c` que dejó una corrida anterior: aborta con un `AssertionError` que
no explica nada, o con un error del backend de Scons. Se corta con el motivo, o se borra
con `--force`.

## Lo que cada fork edita en `build.py`

Seis constantes al principio del archivo, marcadas en el propio docstring:

- `_COMPANY`, `_PRODUCT`, `_DESCRIPTION`, `_ICON` — identidad del programa en las
  propiedades del `.exe`. `_PRODUCT` es un placeholder (`APP_NAME`): conviene que
  coincida con `system.app_name` del `config.yaml`, que es el título de la ventana.
- `_EXCLUDED` — paquetes que **no** entran al ejecutable standalone, vacía por defecto (en
  la acelerada no se compila nada de terceros, así que no aplica). Un fork que
  usa un framework de inferencia pesado y no lo necesita en el entregable —por ejemplo,
  si exporta su modelo a ONNX Runtime y ya no corre TensorFlow— lo agrega acá. Nuitka
  empaqueta todo lo que `main.py` importa; esto es sólo para lo que el fork sabe que no
  hace falta.

## El entregable

Lo arma `build/make_release.py` a partir del `main.dist` de Nuitka:

    .venv\Scripts\python.exe build\make_release.py

El nombre de la carpeta y de los lanzadores sale de `system.app_name` en el
`config.yaml` que se está empaquetando, así que no hay un segundo nombre que mantener
sincronizado con el título de la ventana. Ver el docstring del script para la forma
completa del entregable, por qué el programa y los datos de la instalación quedan en
carpetas separadas (`program/` e `installation/`), por qué los accesos directos se crean
en el equipo y no viajan adentro del paquete, y qué hace `Verificar camara.cmd`.

**Los calibradores viajan sin tocar estos scripts.** Cada fila de `calibration/registry.py`
es un subcomando del mismo ejecutable (`--calibrate <nombre>`): `build.py` compila el
paquete `calibration` y `make_release.py` agrega su lanzador y sus carpetas vacías en
`installation/calibration/<nombre>/`. En Windows, `Crear accesos directos.cmd` le hace
además un acceso en el escritorio, con la consola a la vista porque ahí deja su resultado;
en la Jetson el `.desktop` es sólo el de la app. Cómo se arma uno está en
`calibration/README.md`.

## El servicio de Windows

Para un equipo sin pantalla que tiene que quedar siempre andando, `make_release.py` deja
`Instalar servicio.cmd` y `Quitar servicio.cmd`, y copia NSSM a `program
ssm.exe`. **NSSM
viaja en el entregable porque la planta no suele tener internet**: se baja una vez en la
máquina que arma el entregable y se deja en `packages
ssm.exe`, que —como el wheel de
stapipy— no se versiona. Es el `win64
ssm.exe` del build **2.24-101** de nssm.cc: el 2.24
publicado tiene problemas conocidos para arrancar servicios desde Windows 10 1703. Sin el
archivo el entregable se arma igual, avisa, y el `.cmd` de instalar lo dice en el equipo.

Cómo queda el servicio, y por qué:

- **Corre `program\main.exe --headless` y no el lanzador sin pantalla.** El lanzador larga
  el `.exe` con `start` y termina en el acto, y NSSM daría el servicio por caído.
- **`SIP_DATA_DIR` se lee de `_Entorno.cmd` al instalar** y se le fija al servicio, así que
  sigue viviendo en un solo archivo y el servicio lee el mismo `installation\` que la app
  con pantalla. Es una ruta absoluta: si se mueve la carpeta, se quita y se reinstala.
- **Arranque demorado** (`SERVICE_DELAYED_AUTO_START`), para que la red esté arriba, y
  reinicio a los 10 s si el proceso termina.
- **Cierre por Ctrl+C con 30 s de margen.** Es el mismo handler que atiende Ctrl+C en la
  consola; el default de NSSM, 1,5 s, mataría a `stop()` mientras espera a los hilos.
- **`data\logs\service.log`** junta la salida de consola, que sólo sirve si el proceso
  muere antes de abrir su log.
- **`Quitar servicio.cmd` no usa NSSM**: `net stop` y `sc delete` alcanzan, y así anda
  aunque `program\` se haya reemplazado por uno sin `nssm.exe`.

El nombre interno del servicio es el de la app sin tildes ni espacios —el mismo id que el
`.desktop` de la Jetson—, y el que se ve en Servicios es `system.app_name`. **Para cambiar
el config con el servicio instalado** hay que detenerlo, porque la adquisición es
excluyente y el puerto de los streams también:

    net stop <servicio>
    "<APP_NAME>.cmd"                  cambiar, guardar y cerrar la ventana
    net start <servicio>

Con el `.cmd` y no con `program\main.exe`: sin `SIP_DATA_DIR` la app editaría el config de
`program\`, que el servicio no lee. Editado a mano alcanza con un `net stop` y un
`net start`, porque el config se lee sólo al arrancar. Actualizar el programa es lo mismo:
detener, reemplazar `program\`, arrancar.

Corre como `SYSTEM`, y eso cambia dos cosas que con la app con pantalla no se ven:

- **Las variables del SDK de la cámara tienen que ser del sistema**, no del usuario, y el
  servicio las ve recién después de reiniciar el equipo. `Verificar camara.cmd` mira las
  del usuario que lo corre, así que puede dar todo bien y el servicio no capturar.
- **El firewall**: GigE recibe el video por UDP, y Windows pregunta una vez por programa si
  lo deja pasar. Un servicio no puede contestar, así que la app con pantalla se abre una
  vez antes y se acepta.

En la Jetson el equivalente sería una unidad de systemd, que no está armada.

## En la Jetson

El mismo par de scripts, corrido en una Jetson. `build.py` compila en la forma acelerada y
deja afuera las opciones que sólo existen para un `.exe` —icono, consola, propiedades del
archivo— y `make_release.py` arma la misma carpeta con lanzadores de bash:

    <APP_NAME>-<version>/
        program/                  main.bin, ui/styles, ui/icons, ui/fonts y site-packages/
        installation/             lo de esta planta — NO se toca al actualizar
        _entorno.sh               la ruta de datos, y nada más
        <APP_NAME>.sh
        <APP_NAME> sin pantalla.sh
        Crear accesos directos.sh    menú de aplicaciones y escritorio, con un .desktop
        Verificar camara.sh          le pregunta a ldd qué biblioteca del SDK falta
        <calibrador>.sh              uno por cada fila de calibration/registry.py
    <APP_NAME>-<version>.tar.gz   esto es lo que se copia al equipo

**Se copia el `.tar.gz`, no la carpeta.** Un pendrive en FAT32 o exFAT no guarda el
permiso de ejecución, y la carpeta copiada así llega con un `main.bin` que no arranca.
`--no-archive` lo saltea, para cuando se copia con `rsync` o `scp`.

**`program/site-packages` es el venv del equipo de build**, copiado: los mismos wheels
—torch y torchvision de NVIDIA, como dice el `README.md` de la raíz— sin Nuitka ni pytest.
Los paquetes que vienen de apt (`gi`, `tensorrt`) viajan como symlinks y resuelven contra
el sistema del equipo de la planta. Los lanzadores le apuntan `PYTHONPATH` a esa carpeta, y
`main.bin` se compila sin `site`, así que no importa nada más: ni el `dist-packages` de
JetPack ni el `~/.local` del usuario.

**La Jetson de build tiene que tener el mismo JetPack que la de la planta.** Las
extensiones de `site-packages` están compiladas contra su CUDA, su TensorRT y su glibc, y
los symlinks de apt tienen que encontrar las mismas versiones. `main.bin` trae `libpython`
estática, pero usa la librería estándar de `/usr/lib/python3.10`, que JetPack trae siempre.

**Antes de compilar, las dos claves del repositorio de firma.** `system/license/_public_key.py`
—sin ella el binario no conoce ninguna clave, toda licencia le da «inválida» y el equipo
queda para siempre en modo de puesta en marcha— y, si los pesos van cifrados,
`system/inference/models/_model_key.py`. Las dos están en el `.gitignore`, y `build.py`
las borra al terminar, compile o no: para reintentar hay que volver a generarlas. Si falta
la pública, lo avisa antes de arrancar. Ver `docs/licensing.md` y `docs/model_protection.md`.

En el equipo de la planta:

    tar xzf <APP_NAME>-<version>.tar.gz
    cd <APP_NAME>-<version>
    bash "Verificar camara.sh" <ip de la cámara>
    bash "Crear accesos directos.sh"

**El `.engine` no se compila acá**: está atado a la GPU y a la versión de TensorRT, así que
se genera en el equipo de la planta y se deja en `installation/models/`. La licencia,
tampoco: se pide desde la pestaña de licencia **del equipo de la planta**, porque la
solicitud lleva la huella de la máquina que la genera. Una licencia pedida desde la Jetson
de build sólo vale en la Jetson de build.

**Compilado, el handler de una señal corre en un lugar distinto que desde fuentes.** Python lo
ejecuta entre dos instrucciones no compiladas del hilo principal, y en el binario la primera
suele estar adentro de una librería con un lock tomado —el `deepcopy` de
`ConfigManager.get()`—. Por eso el handler de `main.py` sólo agenda el cierre en el event
loop: un handler que cierre ahí mismo traba el cierre con cualquier hilo que pida ese lock,
y Qt aborta al destruir el hilo todavía vivo. Vale para cualquier handler que se agregue.

## Qué falta medir en cada fork

- El tamaño y el tiempo de build reales de esta instalación, con sus exclusiones. Un
  `results.csv` al lado de este archivo —no versionado por defecto, ver `.gitignore`— es
  un lugar cómodo para llevar esa historia.
- El entregable en una máquina limpia, sin Python y sin el SDK de cámara instalado:
  `Verificar camara.cmd` está para eso.
- Con el servicio instalado: que el Ctrl+C de NSSM llegue al `.exe` compilado con
  `--windows-console-mode=attach` —el log tiene que terminar en `[Main] Subsistemas
  detenidos.` después de un `net stop`; si se corta antes, NSSM lo está matando—, y qué
  hace el antivirus con `nssm.exe`, que es un ejecutable chico y sin firmar.
- Si el equipo de build tiene antivirus corporativo, si compila con una exclusión para el
  directorio de salida, y si el `.exe` final sobrevive sin firma en el equipo de destino.
