# La interfaz gráfica

Cómo se toca `ui/`: dónde vive cada atributo —color, texto, medida, estado— y qué hay
que escribir para agregar una pantalla, un campo de configuración o un widget.

El público de este documento es quien forkea el template y tiene que armar la vista de
operador de su instalación. El mapa del repo y las decisiones generales están en
[CLAUDE.md](../CLAUDE.md); las convenciones de nombres, comentarios y límites, en
`.claude/skills/`.

## Cómo se la mira andando

    .venv\Scripts\python.exe manual_test\ui\ui_app.py       # Windows
    .venv/bin/python manual_test/ui/ui_app.py               # Linux

Levanta la aplicación completa con cámaras sintéticas y su propio `config.yaml` al lado.
No son dobles: los hilos de captura, el de inferencia, el monitor de hardware, el
servidor HTTP de video, los bitfields y el esquema de registros son los del repo. Lo
único simulado son los frames —del `mock` driver— y el estado de los servicios que la
prueba no levanta.

Es además **la referencia de cómo se cablea la UI**: lo que hace `_DemoApp` es lo que va
a hacer `main.py`, con los mismos métodos públicos. Su docstring lista qué mirar y qué
probar, incluida una advertencia que conviene leer antes de apretar Guardar: **el
guardado reescribe el `config.yaml` y se pierden los comentarios**, porque
`ConfigManager.save()` vuelca el diccionario en memoria y el YAML no los conserva. La
copia comentada es la de git.

## La regla de oro

**Ningún atributo visual se escribe dos veces.** Cada uno tiene un dueño, y si hay que
cambiarlo se cambia ahí:

| Atributo | Dónde vive | Cómo se usa |
|---|---|---|
| Color de un widget estándar | token de `ui/theme.py`, usado en `ui/styles/app.qss` | selector de QSS; nadie llama a `setStyleSheet()` |
| Color de algo que se dibuja | `ui/theme.py` (`get_palette`, `get_color`) | el widget lo pide en su `set_dark()` |
| Tipografía | `ui/fonts/` y `FONT_*` de `ui/theme.py` | la pone el QSS con `font-family` |
| Ícono | `ui/icons/tabler/` (Tabler, los de la guía visual) | `theme.get_icon("nombre", color)` |
| Color de una serie de gráfica | `ui/theme.py` (`SERIES_COLORS`) | `RealtimeChart(series_role="cpu")` |
| Color de una detección | `system/inference/overlay.py` | **no es de la UI**: el frame llega anotado |
| Estilo del anotado (máscaras, texto, grosor, paleta) | `inference.overlay` en `config.yaml` | **no se edita desde la UI**: ninguna pestaña lo muestra |
| Texto que ve el operador | `ui/strings.py` | `tr("clave")`; nunca un literal |
| Medida (ancho, alto, margen) | constante `_ALGO_PX` del módulo que la usa | si la usan dos módulos, va en `ui/widgets/form.py` |
| Ancho de una pestaña | `_CARD_WIDTH_PX` en `ui/widgets/form.py` | uno solo para toda tarjeta centrada, en Configuración y en Diagnóstico: no se ajusta por pestaña |
| Alto de una pestaña | no se declara: la envuelve un área con scroll | ver «Agregar una pestaña» |
| Estado de un servicio | `ui/service_status.py` | `describe(service, status)` |
| Estado de un chip | `ui/widgets/status_chip.py` (`CHIP_*`) | `chip.set_state(CHIP_OK)` |
| Valor de configuración | `config.yaml` | la pestaña dueña de esa sección |

Si un color, un texto o una medida aparecen en dos archivos, uno de los dos está mal.

## La identidad visual

La interfaz sigue el **sistema de diseño de IEA** —la propuesta web «Sistema de diseño
para software IEA», armada sobre la guía «Componentes Visuales IEA»—: donde las dos
difieren, manda la web. Lo que se lleva a Qt:

- **El acento es el celeste IEA** (`#28A7E0`): foco, pestaña elegida, ícono de la vista
  activa. El botón principal es el azul medio en el tema claro y el celeste en el oscuro.
- **La marca vive en la barra lateral** (`ui/widgets/sidebar.py`), que es oscura en los
  dos temas: logo sin la bajada, la tarjeta con el nombre de la aplicación y el cliente,
  la navegación agrupada y «Ingeniería Electrónica Argentina» al pie. Lleva la textura de
  la web —un halo celeste arriba a la izquierda y una grilla de plano que se desvanece— y
  un filete celeste contra el borde en la vista activa; las dos se dibujan con `QPainter`
  porque el QSS no tiene degradés radiales ni máscaras. El botón del pie la contrae a un
  riel de íconos, con la esfera del logo arriba y el texto de cada ítem en su tooltip; lo
  que se esconde deja su lugar, así que ningún ícono se mueve. El área de trabajo queda
  limpia: sin degradés ni pies corporativos. La barra superior lleva la miga de pan y, a la
  derecha, la búsqueda y el cambio de tema —un sol en el oscuro, una luna en el claro, como la
  web—; la de estado, en Ubuntu Mono, el mensaje, la versión y el reloj.
- **La búsqueda es la paleta de la web.** El campo de la barra superior —o `Ctrl+K`— abre
  `ui/widgets/command_palette.py` sobre la ventana: con el campo vacío, las páginas y el
  cambio de tema; al escribir, también las pestañas y cada sección y campo de las pestañas
  de configuración, sin mayúsculas ni tildes. Elegir un ajuste va a su página, elige su
  pestaña y su sub-pestaña, lo trae a la vista con el foco y pinta su rótulo del color de
  marca unos segundos. Los campos no se declaran en ningún lado: `ui/widgets/field_search.py`
  los lee de cada `FieldGrid` y cada `SettingsSection` ya armados, así que **un campo nuevo
  aparece en la búsqueda si se agrega con `add_field()`**, y uno que la pestaña esconde no.
- **Tres tipografías, empaquetadas en `ui/fonts/`** porque un equipo de planta no las
  tiene ni tiene internet: Exo para títulos y valores medidos, Ubuntu para la interfaz,
  Ubuntu Mono para datos técnicos. Las registra `theme.load_fonts()` al armar la ventana.
- **Las tarjetas son los `QGroupBox`**: fondo de tarjeta, borde fino, esquinas redondeadas
  y el título adentro. La tarjeta de una pestaña es transparente, así que los grupos
  flotan sobre el fondo de la página.
- **Botones**: el de por defecto es de contorno; el principal se pide con
  `objectName` `saveButton` o `primaryButton`, y los que borran algo (`resetButton`,
  `clearButton`) van en el rojo de estado, suave.
- **Pestañas subrayadas**, sin fondo: la elegida lleva una línea celeste debajo.
- **El color aparece sólo cuando significa algo.** Verde, ámbar y rojo son de estado y
  son iguales en todas las soluciones: el rojo siempre es crítico.

Los chips de estado y los indicadores de GPIO conservan sus colores de antes: se
revisan aparte.

## Colores y temas

Hay dos temas y **una sola hoja de estilos**: `ui/styles/app.qss` es una plantilla cuyos
colores son tokens (`$card`, `$border`, `$brand`), y `theme.load_stylesheet(dark)` los
reemplaza con los del tema. Una regla nueva vale sola para los dos temas; un color nuevo
es un token en las dos tablas de `theme.py`, y un test verifica que las dos declaren las
mismas claves y que la hoja no quede con ningún token sin reemplazar. La plantilla ve
también los roles de la paleta de lo que se dibuja (`$video_background`), así que un
mismo color no se escribe dos veces.

Qt resuelve las rutas de `url()` del QSS contra el directorio de trabajo: la flecha de los
combos y la tilde del checkbox (`ui/icons/qss/`) entran con `$qss_icons_dir`, que es
absoluta. Esos dos SVG llevan su color fijo —el QSS no colorea una imagen—, elegido para
leerse en los dos temas. Los campos numéricos no llevan flechas, como en la web: el número se
tipea o se mueve con el teclado y la rueda, y va en Ubuntu Mono.

La línea entre el QSS y `theme.py` es una sola pregunta: **¿puede Qt pintar ese widget
solo?**

- **Sí** → va al QSS. Botones, inputs, tablas, pestañas, groupbox, labels.
- **No** → va a `theme.py`. Todo lo que se dibuja con `QPainter`, las series de
  QtCharts y los colores de los *items* de una `QTableWidget`, que el QSS no alcanza.

El tema se aplica desde arriba: `MainWindow.apply_theme(dark)` carga la hoja de estilos
y llama al `apply_theme()` de cada vista, que lo baja a los widgets que se pintan por
código. Un widget nuevo que dibuja tiene que exponer `set_dark(bool)` y que su vista lo
llame; si no, se queda con el tema del arranque.

**El único estilo inline permitido es una animación**, porque el QSS no interpola
colores: hoy es el destello del footer (`main_window.py`). Cualquier otro
`setStyleSheet()` en un widget es un error de límite.

### Estados por propiedad de Qt

Los colores que dependen de un estado no se aplican por código: el widget deja el estado
en una propiedad de Qt y el QSS lo pinta.

```python
self.setProperty("state", "ok")
self.style().unpolish(self)     # Qt no reevalúa el QSS solo
self.style().polish(self)
```

```css
QLabel#statusChip[state="ok"] { background-color: rgba(190,255,190,120); }
```

Las propiedades en uso son `state` (chips, indicadores de GPIO), `selected` (el botón
elegido de un control segmentado), `alert` (la barra de estado sin licencia válida), `warn` (un valor que pide
atención: excepciones del Modbus, sobrecorriente, vistas perdidas, el log en pausa),
`error` (el resultado de instalar una licencia que falló) y `first` (la primera métrica de
la fila de Hardware, que no lleva filete a la izquierda). La vista activa de la
barra lateral no usa una propiedad: sus botones son *checkable* y el QSS pinta
`:checked`. Agregar un estado es una regla en `app.qss`.

## Textos e idiomas

`ui/strings.py` es la tabla de idiomas: **la clave es un identificador estable en
inglés** y el valor es el texto en cada idioma soportado (`es`, `en`, `pt`).

```python
from ui.strings import tr
label = QLabel(tr("cam_box_roi"))
```

- **Ningún widget escribe un literal visible.** Si hace falta un texto nuevo, se agrega
  la clave con sus tres traducciones y se usa `tr()`.
- Un texto sin traducir cae al idioma de respaldo (`es`), así que se puede traducir de a
  partes.
- Una clave que no existe devuelve la clave misma y deja un warning: el hueco se ve en
  pantalla en vez de romper la vista.
- El idioma sale de `ui.language` del config y **queda fijo por corrida**: cada widget
  resuelve su texto al construirse, así que cambiarlo con la aplicación andando dejaría
  la pantalla partida en dos idiomas. El panel lo guarda como cualquier otra clave y la
  aclaración del campo, en la sección «Interfaz» de la pestaña Sistema, avisa que toma efecto
  al reiniciar. El combo muestra cada idioma en su propio idioma (`language_<código>`) y
  guarda el código.
- **El tema sí se cambia en caliente, y por eso no está en la pestaña Sistema**: es el botón
  de la barra superior, a mano desde cualquier página. Lo aplica `MainWindow.apply_theme()` y
  lo persiste en `ui.dark_mode` en el momento —sólo esa clave: lo editado y sin guardar en
  las pestañas vive en sus campos, no en el config—, así que el próximo arranque abre igual. `set_language()`
  queda para las pruebas y para quien arme la UI antes de mostrarla.
- Para agregar un idioma: sumar su código a `LANGUAGES` y su entrada a cada texto.

Los nombres del código siguen en inglés y los comentarios en español, como en todo el
repo: lo que cambia de idioma es sólo lo que ve el operador.

## Estructura

```
ui/
  strings.py            tabla de textos por idioma + tr()
  theme.py              tokens y paletas de los dos temas, el QSS, tipografías e íconos
  service_status.py     estado de un servicio, una cámara o la licencia -> texto y color del chip
  navigation.py         las páginas: grupo, ícono y qué pestañas muestra cada una
  main_window.py        barra lateral, barra superior, las páginas y la barra de estado
  views/
    home_view.py        la página de inicio: placa, veredictos, cámaras, tiempos, servicios
    monitor_view.py     área central VACÍA (la llena el fork), sin barras propias
    page_view.py        una página: título, sus pestañas y, si edita, Guardar/Descartar
    config_tabs.py      crea las pestañas de configuración y las guarda juntas
    diagnostics_tabs.py crea las pestañas de diagnóstico y les reparte lo que llega
    gpio_view.py        la página de entradas y salidas; existe sólo con controlador
    config/             una pestaña por sección del config.yaml
      abstract_tab.py   el contrato: TITLE_KEY, load(), save()
      cameras_tab.py video_tab.py inference_tab.py collector_tab.py
      telemetry_tab.py modbus_tab.py system_tab.py
      process_tab.py    VACÍA en el template: la llena cada fork
    diagnostics/        una pestaña por tema de diagnóstico
      abstract_tab.py   el contrato: TITLE_KEY, apply_theme()
      hardware_tab.py modbus_tab.py video_tab.py logs_tab.py
  widgets/
    form.py             constructores de campos, filas, grupos y tarjetas
    settings_card.py    tarjeta de ajustes: secciones con título y descripción, campos en columnas;
                        la pila de secciones de una sub-pestaña y la lista de filas
    page_header.py      encabezado de una página: rótulo con filete, título y una línea
    save_bar.py         la barra flotante de cambios sin guardar, con Descartar y Guardar
    sidebar.py          la barra lateral: marca, navegación agrupada, textura y riel contraído
    status_chip.py      chip de estado con cuatro colores semánticos; `pill=True`, la píldora
    log_table.py        tabla de eventos FIFO con filtro por nivel
    realtime_chart.py   gráfica de línea de ventana fija
    camera_panel.py     feed de UNA cámara + chips + las regiones de sus pipelines (en crudo)
    camera_grid.py      N paneles, con foco y modo crudo/anotado
    region_canvas.py    el frame con las regiones de varios pipelines, una editable con el mouse
    pause_button.py     pausar y reanudar el video de un diálogo de dibujo
    command_palette.py  la paleta de búsqueda: el campo, la lista agrupada y la regla del filtro
    field_search.py     los campos y secciones de una pestaña, y llegar a uno: pestaña, scroll y foco
  dialogs/
    roi_dialog.py       dibujar UN rectángulo con el mouse sobre el frame en vivo (la región de la óptica)
    pipeline_regions_dialog.py  las regiones de todos los pipelines de una cámara, una pestaña
                        por pipeline, con vista previa y avisos; se abre sólo desde la pestaña
                        Inferencia con `open_regions_requested`
    level_dialog.py     marcar una altura —una línea horizontal— sobre el frame en vivo; la
                        pide una pestaña con `open_level_requested`, igual que el ROI. En
                        pausa puede medir sobre el último frame inferido si esa pestaña
                        arma un inspector (`build_level_inspector()`)
    polygon_dialog.py   dibujar una zona —un polígono, tramado— sobre el frame en vivo:
                        click agrega un vértice, arrastrar lo mueve, click derecho lo borra;
                        la pide una pestaña con `open_polygon_requested`, igual que la altura
  styles/               app.qss: la plantilla de los dos temas
  fonts/                Exo, Ubuntu y Ubuntu Mono, con sus licencias
  icons/                logos, el .ico de la ventana, tabler/ (íconos, MIT) y qss/
```

## Las regiones de los pipelines

La región de cada pipeline en cada cámara **se edita en un solo lugar**: el diálogo de
regiones (`ui/dialogs/pipeline_regions_dialog.py`), que se abre con «Editar regiones…» desde
la tarjeta **«Regiones (ROI)»** de la pestaña Inferencia —debajo de «Modelos» y «Pipelines»—,
que tiene una sub-pestaña por cámara y en cada una una fila por pipeline que la mira, con el
color del pipeline, su nombre y la región en una línea. La pestaña Cámaras no tiene botón a él.

**Se muestran los nombres del operador y no los slots**: el de la cámara
(`cameras.<slot>.name`) y el de cada pipeline (`inference.pipelines.<slot>.name`, opcional, se
escribe en la página del pipeline). Sin nombre se muestra el slot, que sigue siendo la
identidad —con él se elige la pestaña y bajo él se guarda—. El mismo nombre va en el recuadro
del panel en crudo y en el del anotado, en una etiqueta oscura con un cuadrado del color del
pipeline. El anotado la escribe en la Ubuntu de la app, con tildes; sin Pillow cae a la fuente
de OpenCV, que sólo dibuja ASCII («Verificación» sale «Verificacion»).
`inference.overlay.draw_roi_label: false` saca la etiqueta en los dos —el nombre, y en el
anotado también el veredicto escrito— y deja el recuadro.

- **Una pestaña por pipeline que mira esa cámara**, con el frame en vivo arriba y el rectángulo
  de todos encima. El de la pestaña activa va grueso y se edita —arrastrarlo desde adentro lo
  mueve, desde un borde o una esquina lo estira, desde afuera dibuja uno nuevo—; los otros
  van finos, para ver cuánto se pisan, y el diálogo dice el porcentaje.
- **Vista previa**: el recorte, que es lo que recibe el modelo. En una cámara que decodifica
  video sale del frame reducido —la de verdad sale del nativo—, y la etiqueta lo marca como
  aproximado.
- **Corte del video nativo y salida del VIC: no se eligen.** En una cámara que decodifica video
  (`rtsp`, `video_file`) la región se corta siempre del video a su resolución original, *antes*
  de reducirlo al tamaño de la cámara (`decode`), y el **VIC** —el escalador por hardware de la
  Jetson— la entrega siempre a la entrada del modelo, leída del `.engine`: no hay campo para
  pisarla. En una cámara GigE el frame ya es nativo; en una con corrección de lente o ajuste de
  imagen la región sale del frame reducido y un aviso lo dice. Se aplica al reiniciar. Detalle
  en `docs/jetson.md` §4.1.1.
- **Pausar el video**: el botón «Pausar video» congela el frame para dibujar sobre una imagen
  quieta; la vista previa y los avisos siguen sobre ese frame. Lo tienen también el diálogo de
  ROI, el de nivel y el de zona (`ui/widgets/pause_button.py`).
- **Avisos**, de `system/inference/regions.py` (`find_region_problems()`): el rectángulo se
  sale del frame, el config pide el video nativo en una cámara con corrección de lente o ajuste de
  imagen, o se dibujó sobre un frame de otro tamaño que el que llega ahora. Una pestaña con
  aviso lleva un ⚠ y el texto se ve debajo del lienzo.
- **Al aceptar** se escriben sólo las pestañas que se tocaron, y las regiones propias
  llevan `frame_width_px` / `frame_height_px`: el tamaño del frame sobre el que se
  dibujaron. El motor las compara con el frame que llega y avisa en el log si cambió.

En el monitor, el frame que se ve es siempre el completo, con las regiones dibujadas encima
—un recuadro por pipeline, con su nombre—. En **anotado** las dibuja el overlay; en **crudo**,
el panel de cámara. Nunca los dos: el panel sabe en qué modo está por `set_mode()`.

**El color de un pipeline es uno solo** y sale de `overlay.get_pipeline_style(index)`, donde
`index` es su posición en `inference.pipelines`: el overlay, el panel y el diálogo lo leen de
ahí. La paleta **no usa verde, rojo ni amarillo**, que quedan para el estado. Con más
pipelines que colores se repiten, y la segunda vuelta va punteada.

**El recuadro se pinta por el veredicto.** Un pipeline puede declarar, con
`_set_roi_state()` junto a sus `labels`, que su región está `ok`, `nok` o en `warning`
(`ROI_STATE_*` de `result.py`), y el overlay pinta el borde de verde, rojo o amarillo. Sólo el
código del proyecto sabe qué veredicto es «bien» o «mal», así que el template no lo decide: la
única excepción es el pipeline de una etapa con el modelo sintético, para verlo en
`manual_test/ui/`. Reglas del dibujo:

- **El nombre del pipeline sigue en su color propio** y lleva el veredicto en texto
  («grain OK», «grain NOK»): el color solo no alcanza para un operador daltónico ni en un
  stream en blanco y negro. El trazo es el mismo en los tres estados: uno más grueso para el
  `nok` hacía saltar el recuadro cada vez que cambiaba el veredicto.
- **Un resultado no confiable nunca se pinta de estado**: va gris, punteado y con el motivo
  («grain (dark_frame)»), aunque el pipeline haya dicho `ok`. Sus números no son una medición,
  y un recuadro verde sobre algo que no se midió es peor que ninguno.
- **El estado vive lo que vive su resultado**: pasada la vigencia del composer, o cerrada la
  compuerta de frames, el recuadro vuelve a su color o desaparece. No hay memoria del último
  `nok`.
- **Sólo en anotado.** En crudo no hay resultado, y el panel dibuja la geometría del config con
  el color del pipeline.
- El verde, el rojo y el amarillo del overlay son valores sobre el frame (`overlay.ROI_STATE_COLORS_BGR`)
  y no los tonos translúcidos de los chips de la UI; se parecen a propósito y no se comparan.

## La página de inicio

`HomeView` es la «Inicio» del sistema de diseño (`iea-ui/src/pages/Inicio.tsx`), llevada a
Qt. Es lo primero que se ve al abrir, y es breve a propósito: presenta el equipo y dice si
lo que tiene anda. El uso del hardware no va acá —tiene su pestaña—, ni la IP del equipo,
que tiene más de una interfaz.

```
┌ ● EQUIPO EN MARCHA ──────────────────────────────────────┬───────────┐
│ Nombre de la aplicación                                   │   ( 3 )   │  placa «noche IEA»,
│ Visión artificial en línea: …                             │   de 4    │  igual en los dos temas
│ Equipo   Versión   En servicio                            │ Cámaras … │
└───────────────────────────────────────────────────────────┴───────────┘
┌ Resultados ─────────────────────────────────────────────────────────┐
│ Pipeline   Cámara   [Personas: OK]  [Calza elevada: OK] [Contacto: NOK]
└─────────────────────────────────────────────────────────────────────┘
┌ Cámaras ──────────────────────────────────────────── Ver monitor ↗ ┐
│ Cámara        Dirección                        Estado        FPS   │  todas en una tarjeta
└────────────────────────────────────────────────────────────────────┘
┌ Inferencia ──────────────────────┐ ┌ Servicios ────────────────────┐  mismo ancho y alto
│ Pipeline Cámara Ciclo Inf. Último│ │ los 6 canales · Licencia · Latido
└──────────────────────────────────┘ └───────────────────────────────┘
```

- **La placa** lleva el estado general en su rótulo: rojo sin licencia válida
  (`should_report_invalid`, el mismo criterio que la barra de estado), ámbar si una cámara
  habilitada o un servicio están en error, verde si no. Un servicio apagado a propósito, una
  cámara que arranca y un veredicto NOK no son un aviso del equipo. La textura —halo,
  grilla, las tres órbitas y la esfera de `Orbits` de la web— y el anillo de cámaras se dibujan
  con `QPainter`, con los colores `nameplate_*` de la paleta; los textos, con los tokens
  `nameplate_*` del QSS. La caja del anillo es opaca: el QSS no tiene `backdrop-blur`, y
  translúcida dejaba ver la órbita pasando por detrás.
  **El ícono de la aplicación va al lado del nombre** si el fork lo declara en
  `ui/icons/app/icon.svg`, que es también el ícono de la ventana (ver `ui/theme.py`).
- **Los resultados** son los veredictos de cada par (pipeline, cámara): las métricas
  `<algo>_ok` del analyzer (`analysis.is_verdict_key()`), las mismas que dibuja el anotado y
  salen al PLC. 1 es OK y 0 NOK; vencido, «Sin dato». Los arma `main.py` una vez por segundo
  (`_build_verdicts()`) con el mismo vencimiento que el registro: el `max_age_s` de la fila,
  o 3 s si no tiene. El rótulo es `verdict_<nombre>` de `strings.py`; un fork que agrega un
  veredicto agrega su texto, y sin él se muestra el nombre de la métrica.
- **Las cámaras** van todas en una tarjeta, una fila cada una. La dirección de una RTSP es la
  URL entera —sin usuario ni clave, si alguien los escribió en ella—; la de un video, el
  nombre del archivo, y la de una GigE, su IP o su serie. El estado sale de
  `service_status.describe_camera()`, igual que el chip del panel. Una cámara con
  `enabled: false` dice «Deshabilitada» y no cuenta en el anillo ni en el estado general.
- **La inferencia** tiene una fila por par habilitado, agrupadas por pipeline: el ciclo
  (`cycle_time_ms`, entre dos frames de la cámara), la inferencia (`inference_time_ms`, lo
  que tardó el último) y hace cuánto llegó. Los tiempos se guardan al llegar y se pintan una
  vez por segundo; la edad sigue avanzando sin resultados nuevos, que es lo que delata un par
  caído o cortado por su compuerta.
- **Los servicios** son los seis canales de `service_status.SERVICES`, en el orden en que
  los lee el PLC —los chips que antes tenía el monitor—, más la licencia y el latido: el
  contador del registro `heartbeat` tal como sale al PLC.
- **Las columnas no bailan.** FPS y tiempos van en monoespaciada, rellenados a ancho fijo; la
  píldora de una cámara mide lo que su estado más largo, y las de resultados miden todas lo
  mismo —se mide al mostrarse la página, con el QSS ya aplicado—. Los estados van en la píldora del sistema de diseño (`StatusChip(pill=True)`),
  con los tokens `ok`, `warn`, `crit` y sus `_soft`.

Recibe lo suyo por la ventana: `set_service_status()`, `update_camera_status()`,
`update_license()`, `update_inference_times()` —desde cada `InferenceResult`—, y
`update_heartbeat()` y `update_verdicts()`, en cada tick de los registros. La línea bajo el
nombre es `system.app_description` del config —«Sistema Inteligente de Visión para
Autodescarga»—, en el idioma de la planta y sin pasar por `strings.py`: es de la instalación
como el nombre, y reescribir un texto del template en cada fork chocaría en cada merge. Sin
ella va la genérica, `home_tagline`.

## La vista de monitor viene vacía

`MonitorView` es **sólo un área central vacía**, sin barras propias: el video se lleva todo
el espacio. El log de eventos tiene su página y el estado de cada canal de salida está en
la pestaña de ese canal. Qué se mira mientras la línea trabaja es lo más específico de
cada instalación, así que el template no lo decide.

Cada cámara de `CameraGrid` es una tarjeta con una sola fila arriba —el nombre a la
izquierda y sus chips, en la versión chica (`StatusChip(compact=True)`), a la derecha— y el
video en todo lo demás.

El fork le pasa su widget:

```python
window.monitor_view.set_content(CameraGrid(cfg))     # el caso normal, ya escrito
```

`CameraGrid` es genérica y cubre lo que casi todos los proyectos necesitan:

- **muestra todas las cámaras configuradas**, no las que están andando: una cámara caída
  queda en su lugar con «SIN SEÑAL», porque el operador tiene que ver que falta y no
  descubrirlo porque la grilla cambió de forma;
- **selector de foco**: ver todas, o una sola ocupando toda el área. Se salta de una
  cámara a otra sin pasar por la grilla completa, porque un operador que compara dos
  cámaras no quiere dar dos pasos por cada cambio;
- **selector de modo** crudo / anotado, que publica por `mode_changed` y **no elige qué
  frame entra**: eso lo decide el cableado, que es el que conoce el hilo de captura y el
  de inferencia.

Un proyecto que quiera la grilla más sus propios paneles arma su layout y le pasa ese.

### Otra disposición de cámaras

`CameraGrid` toma por defecto todas las cámaras de `cameras:`, y con `camera_slots`
toma sólo esas y en ese orden. Eso alcanza para cualquier disposición sin escribir un
widget de cero ni editar la grilla: tres cámaras en una pantalla y tres en otra son dos
grillas dentro del widget del fork.

```python
class DualBeltView(QWidget):
    def __init__(self, config, parent=None):
        super().__init__(parent)
        self._grids = (
            CameraGrid(config, camera_slots=("camera_1", "camera_2", "camera_3")),
            CameraGrid(config, camera_slots=("camera_4", "camera_5", "camera_6")),
        )
        tabs = QTabWidget()
        tabs.addTab(self._grids[0], tr("mi_pantalla_entrada"))
        tabs.addTab(self._grids[1], tr("mi_pantalla_salida"))
        QVBoxLayout(self).addWidget(tabs)

    # La misma API por slot que la grilla: así el cableado no cambia.
    def update_frame(self, frame_bgr, camera_slot):
        for grid in self._grids:
            grid.update_frame(frame_bgr, camera_slot)
```

Tres cosas que hacen que esto funcione y conviene no romper:

- **La API por slot es lo que se conserva, no la clase.** Mientras el widget exponga
  `update_frame(frame, slot)` y `update_status(status, slot)`, `main.py` conecta las
  señales de captura igual que con una grilla sola. Si en cambio expone `grid_a` y
  `grid_b` y deja que el cableado reparta, cada fork reescribe ese reparto.
- **Cada grilla descarta lo que no es suyo**, así que se le puede ofrecer el frame a
  todas y no hace falta un mapa de slot a grilla. `camera_slots` está disponible si el
  compositor igual prefiere repartir.
- **Un panel en una pestaña que no está al frente devuelve `isVisible() == False`**, y
  el `annotate_gate` del motor es `(camera_slot) -> bool`: gateando por visibilidad, el
  overlay no se dibuja para las cámaras que nadie está mirando. Con una sola grilla que
  muestra todo, esa optimización no existe.

Un slot que no está en `cameras:` se descarta con un warning y no frena la grilla: las
demás se muestran igual. Y la agrupación en sí va en el código del widget, no en el
`config.yaml`: la jerarquía del config espeja los módulos, no las pantallas.

## Las páginas

La barra lateral agrupa las páginas por **subsistema**, no por tipo de pantalla:

| Grupo | Página | Pestañas |
|---|---|---|
| Operación | Inicio | — (la presentación del equipo; la que se ve al abrir) |
| | Monitor | — (el centro lo pone el fork) |
| | Proceso | Proceso |
| Visión | Cámaras | Cámaras (una sub-pestaña por cámara, con su óptica, y la última, «Óptica», con lo que vale para todas) |
| | Inferencia | Inferencia · Dataset |
| Integración | Modbus | Estadísticas · Registros · Configuración |
| | Video y telemetría | Streams · Video · Telemetría |
| Equipo | Sistema | Hardware · Licencia · Sistema |
| | Logs | Logs |
| | Entradas y salidas | — (sólo con controlador de GPIO) |

Una página junta las pestañas que **editan** una sección del config con las que **muestran**
ese subsistema andando: quien revisa el Modbus ve el mapa que configura y los registros que
salen sin cambiar de lugar. Lo dice una sola tabla, `NAVIGATION` de `ui/navigation.py`: para
agregar una página, o mover una pestaña de una a otra, se edita esa tabla y nada más.

Cada página arranca con su encabezado, como en la web: el grupo de la barra lateral como
rótulo chico con un filete celeste, el título en Exo y **una línea** que dice de qué es la
página (`description_key` en la tabla). Todo —encabezado, barra de pestañas y tarjetas— va en
una columna centrada de `PAGE_CONTENT_WIDTH_PX` (la de 1100 px de la web), así que el título y
el contenido arrancan en el mismo borde y en una pantalla ancha lo que sobra queda a los
costados. Una pestaña no agrega margen propio alrededor de sus tarjetas: si no, quedarían
corridas respecto del título.

Las pestañas no son de la página. Las de configuración las crea `ConfigTabs` y **se guardan
todas juntas**: el «Guardar y aplicar» de cualquier página persiste lo editado en todas, y si
una pestaña falla no se escribe nada.

**«Descartar» y «Guardar y aplicar» no están a la vista hasta que algo cambia.** Viven en una
barra flotante (`SaveBar`), abajo y al centro, que aparece con la primera edición y dice en
qué pestañas hay cambios —también las de otras páginas, porque el guardado es uno—. Para eso
`ConfigTabs` compara cada campo con lo que mostraba después de la última carga o el último
guardado, sin que la pestaña declare nada: un campo nuevo de una pestaña nueva ya cuenta. Un
campo que vuelve a su valor deja de contar, y lo que escribe y persiste un diálogo —el ROI,
la altura, la zona— no es un cambio pendiente: ya está en el archivo. Las de diagnóstico las crea `DiagnosticsTabs` desde el
arranque, para que el log y las gráficas ya traigan lo que pasó cuando alguien abre la página.
La página (`PageView`) sólo las muestra, y con una sola pestaña esconde la barra.

## Agregar una pestaña de configuración

Una pestaña es dueña de **una sección** de `config.yaml`. Agregar una sección es un
archivo nuevo y una línea en la página que la muestra, en `ui/navigation.py`; ningún otro
archivo se toca.

```python
class MiTab(AbstractConfigTab):
    TITLE_KEY = "tab_mi_seccion"      # clave de ui/strings.py
    # El alto no se declara: la página envuelve toda pestaña de configuración en un
    # área con scroll, así que puede tener los campos que necesite.

    def __init__(self, config_manager: ConfigManager, parent=None):
        super().__init__(config_manager, parent)
        box, form = build_group_box(tr("mi_box"))
        self._mi_campo = add_form_row(form, tr("mi_label"), build_spin_box(0, 100, 50))
        layout = QVBoxLayout(self)
        layout.addWidget(box)
        self.load()

    def load(self):
        self._mi_campo.setValue(int(self._config.get("mi_seccion.mi_campo", 50)))

    def save(self):
        self._config.set("mi_seccion.mi_campo", self._mi_campo.value())
```

### La forma de la web: tarjetas de ajustes

Una pestaña con la forma del sistema de diseño se arma con `SettingsCard`
(`ui/widgets/settings_card.py`), y es la que usan todas las de configuración —Cámaras, Inferencia, Dataset, Proceso,
Modbus, Video y Telemetría—: una tarjeta por tema, con un rótulo
chico arriba; secciones separadas por un filete, cada una con un título y una descripción de
**una o dos líneas** a la izquierda; y los campos a la derecha, en columnas, con el rótulo
arriba de cada uno y, si hace falta, una aclaración corta debajo («En milisegundos»).

```python
card = SettingsCard(tr("mi_tarjeta"))
section = card.add_section(tr("mi_seccion"), tr("mi_seccion_descripcion"), columns=3)
self._mi_campo = section.add_field(tr("mi_rotulo"), build_spin_box(0, 100, 50),
                                   hint=tr("mi_aclaracion"))
```

Un campo con un botón al lado —«Marcar» sobre la imagen— va con `build_inline_row()`, y las
sub-pestañas por slot entran a todo el ancho con `add_row()`; adentro de cada página, los
campos se arman con `FieldGrid`, la misma grilla sin la tarjeta. Lo que vale para todos los
slots no va adentro de uno: va en una sub-pestaña propia, la última, con `add_general_tab()`,
que el QSS tiñe con el color de la marca para que no se lea como un slot más —es la «Óptica»
de la página Cámaras—. **Los párrafos largos no van
en la pantalla**: lo que explica en detalle qué hace un valor es del docstring del módulo
que lo usa.

Cuando cada slot tiene **varias secciones** —un pipeline: qué corre, cuándo su resultado es una
medición y su ciclo—, la barra va arriba de todo en la tarjeta, con `add_row()`, y cada página
es un `SectionStack`: las mismas secciones con sus filetes, sin otra tarjeta alrededor. Varios
ítems del mismo tipo dentro de una sección —las regiones de los pipelines de una cámara— van en
un `SettingsList`, la lista de la web: un borde fino y un filete entre filas, cada fila con su
botón a la derecha.

```python
card = SettingsCard(tr("inf_box_pipelines"))
tabs = build_slot_tabs()
for pipeline_slot in self._config.get("inference.pipelines", {}) or {}:
    page = SectionStack()
    identity = page.add_section(tr("inf_pipeline_section"), tr("inf_pipeline_description"),
                                columns=3)
    ...                                   # identity.add_field(...)
    tabs.addTab(page, pipeline_slot)
card.add_row(tabs)
```

Tres reglas del contrato, que están en `ui/views/config/abstract_tab.py`:

1. **`save()` no persiste.** Deja los valores con `set()` y nada más; el `save()` del
   archivo lo llama `ConfigTabs` una vez, cuando todas las pestañas escribieron. Así
   un error en la pestaña cinco no deja el `config.yaml` a medio escribir.
2. **`load()` se puede llamar muchas veces.** Es lo que hace el botón de descartar.
3. **`TITLE_KEY` es una clave de idioma**, no un texto.

### La rueda del mouse no cambia valores

Ningún spin ni combo de la interfaz cambia con la rueda, ni ninguna barra de pestañas: bajar
por una pestaña larga cambiaba el valor de cada campo que pasaba debajo del puntero —una
exposición, un umbral— sin que nadie lo notara, y al pasar por una barra de sub-pestañas dejaba
editando otra cámara u otro pipeline. Lo hace `WheelGuard` (`ui/widgets/form.py`), un filtro
que `MainWindow` instala una vez con `install_wheel_guard()`: un estilo de la aplicación lo pone
en cada spin, combo y barra de pestañas cuando Qt los pule, así que alcanza a toda pestaña,
diálogo y widget del fork nuevos sin que hagan nada: la rueda sobre un campo o una barra
desplaza la página que lo contiene. Un valor se cambia tipeando o con las flechas, y una
pestaña con un clic. No va sobre la `QApplication`: ahí veía pasar por Python cada evento de
cada widget —el video, los timers, cada repintado—, y eso era un tercio de lo que tardaba
cambiar de página.

Las páginas se arman solas un rato después de mostrarse la ventana, de a una por vuelta del
loop de eventos (`MainWindow._prewarm_next_page()`): armar una cuesta de 150 ms a casi un
segundo, y hacerlo al abrirla era una pausa la primera vez que se entraba a cada una.

### Por qué interfaz escucha un servidor

Nadie tipea un nombre de interfaz ni una dirección: `ui/widgets/interface_picker.py` ofrece las
que el equipo tiene, leídas de `tools/net_interfaces.py`. Un servidor que escucha en **una
dirección** —Modbus TCP, el video HTTP— lleva `build_address_combo()`, con «Todas las
interfaces», cada interfaz con su IPv4 y «Sólo este equipo»; uno que acepta **varias
interfaces por nombre** —el RTSP, el monitor de hardware— lleva un `InterfaceChecklist`, con
la regla de la lista de cámaras del dataset: todas marcadas es `[]`, que incluye las que se
agreguen, y la última no se desmarca. Un valor del config que el equipo no tiene no se pierde:
la dirección queda como opción del combo y el nombre se conserva y se avisa debajo de las
casillas. Lo que nadie tocó vuelve al archivo tal como estaba.

### La trampa de los combos

**Un valor del config que no está entre las opciones de un combo se pierde en
silencio.** `setCurrentText()` sobre un combo no editable es un no-op cuando el item no
existe: el combo se queda en su primera opción y el `save()` la escribe encima. Abrir el
panel y guardar alcanzaba para cambiar la marca de una cámara sin configurar a la
primera del catálogo.

Por eso **todo combo alimentado por el config se carga con `set_combo_value()`**, que
agrega el valor desconocido como opción y lo deja elegido: vuelve al archivo tal como
estaba y se ve en pantalla que hay que elegir.

```python
set_combo_value(self._tipo, self._config.get("inference.models.model_1.type", "mock"))
```

**Y si el texto del combo se traduce, el valor no puede salir del texto.** Los niveles de
log, los tipos de modelo o la paridad se muestran tal como se guardan, así que ahí el par
de arriba alcanza. Pero rotación, modo del dataset, marca de cámara y codec muestran una
etiqueta y guardan otra cosa, y reconstruir el valor desde la etiqueta lo ata al idioma
con el que se armó el combo: después de cambiar de idioma el mapeo no encontraba nada y
el `save()` escribía «Sob demanda» donde iba `on_demand`. Esos combos se arman con
`build_value_combo_box()` —el valor va en el item— y se leen con `currentData()`:

```python
self._mode_combo = add_form_row(
    form, tr("col_mode"),
    build_value_combo_box({tr(key): value for key, value in _MODES.items()},
                          _MODE_ON_DEMAND),
)
...
set_combo_data(self._mode_combo, self._config.get("image_collector.mode", _MODE_ON_DEMAND))
...
self._config.set("image_collector.mode", self._mode_combo.currentData())
```

`set_combo_data()` mantiene la red de arriba: un valor que no está entre las opciones
entra como opción con su forma cruda y vuelve al archivo igual. Un `rotation` que el repo
no entiende se muestra tal cual; caer a «sin rotación» cambiaría en silencio cómo entra
la imagen.

Un combo así se conecta por `currentIndexChanged` y no por `currentTextChanged`: el
índice es lo que identifica la opción cuando el texto no la identifica.

### Una sub-pestaña por slot

Cuando una sección tiene slots —cámaras, modelos, pipelines—, cada slot es una
sub-pestaña de una barra armada con `build_slot_tabs()`, y los campos de cada página son
un `FieldGrid` o, si el slot tiene varias secciones, un `SectionStack` (ver arriba). Así una
pestaña con slots se ve igual en todas, y la barra se porta igual con muchos: con más slots
de los que entran a lo ancho muestra flechas de desplazamiento y no corta ningún rótulo,
porque un slot cortado a la mitad no se distingue de otro.

**Una barra mide la página más alta**, aunque no esté a la vista. Si las páginas no miden lo
mismo —la óptica general contra una cámara, una cámara que miran dos pipelines contra una que
mira uno— se arma con `build_slot_tabs(fit_to_current=True)` y mide la de la vista. No alcanza
con la política de tamaño de las páginas ocultas: Qt le pide a cada una su alto para el ancho
disponible sin mirarla, y un texto que se parte en líneas lo tiene.

Dos familias de slots no comparten barra. Inferencia tiene tres tarjetas, «Modelos»,
«Pipelines» y «Regiones (ROI)», cada una con la suya, y los pipelines van debajo porque
nombran modelos. La sub-pestaña de un pipeline lleva el nombre del operador, que cambia
mientras se tipea, y el slot se ve debajo del campo del nombre.

## Agregar una pestaña de diagnóstico

Una pestaña de diagnóstico **muestra, no edita**: recibe lo que le empujan y no tiene
`load()` ni `save()`. Se agrega igual —un archivo y una línea en la página que la muestra,
en `ui/navigation.py`— y expone `apply_theme()` si dibuja algo por código.

Acá el scroll sí se declara, con `IS_SCROLLABLE`: una pestaña que apila contenido de
alto fijo lo pone en True, y una que ya tiene una tabla adentro lo deja en False, porque
la tabla scrollea sola y anidar las dos barras deja la de afuera moviendo algo que nunca
crece. `IS_CENTERED = False` es para las que tienen que ocupar todo el ancho.

Una pestaña de diagnóstico tampoco pone margen propio: su tarjeta arranca en el borde del
encabezado, como las de configuración. Si muestra estado y detalle, usa la misma
`SettingsCard`; si es una tabla, la tabla va adentro de una tarjeta con `objectName` `cardTable`, que le saca
el borde y el fondo propios, y arriba una franja con los chips y qué es la tabla —la de
Registros, como el mapa Modbus de la web—. Varias tarjetas lado a lado, una por cosa que se
compara —la de Estadísticas, una por transporte; la de Streams, una por servidor, con sus
URLs en filas que se copian con un botón—, van en una fila, cada una con su
`settingsCard`; una tendencia chica sin ejes es un `Sparkline` —con `low` y `high` cuando la
escala es fija, como un porcentaje—.

Hardware es el ejemplo de una pestaña que apila secciones, una tarjeta por tema: arriba la
fila de métricas del equipo —CPU, GPU, memoria, disco y potencia, cada una con su número
grande, su tendencia y una línea al pie—, después las temperaturas por zona junto a los
aceleradores de la Jetson —que no aparecen fuera de una—, y debajo las cámaras y las
interfaces de red, cada una en una tabla liviana con un filete entre filas. Un valor que el
equipo no expone —el monitor y el driver lo informan como 0— se ve como «—», no como un cero
que parece medido. Las filas de zonas, motores e interfaces se arman con la primera medición:
cuáles hay depende del equipo.

Logs es el ejemplo de una tabla con controles: la tarjeta lleva arriba el filtro —un control
segmentado, `segmentedControl`, con cuántas líneas guardadas hay de cada nivel—, la búsqueda
y «Pausar»; la tabla en el medio y al pie cuántas líneas se ven de cuántas. CRITICAL se
filtra y se pinta con los errores, y en pausa el buffer sigue llenándose.

Registros sigue al mapa Modbus de la web y dice de cada fila lo mismo que la tabla del
integrador: la dirección, el tipo —una píldora, «Lectura» apagada o «Escribe el PLC» en el
color de la marca—, la descripción con el nombre del registro debajo, la unidad con su escala
y el valor dos veces: crudo, el entero que lee el PLC, y convertido a su unidad con
`SCHEMA.decode()`, que es el dueño de la escala; la pantalla no multiplica ni divide nada.
Arriba, el mismo control segmentado que el log —todos, lectura, lo que escribe el PLC y las
palabras de estado, con cuántas hay de cada uno— y una búsqueda por dirección, nombre o
descripción, para un mapa de fork con decenas de filas.

Registros decodifica una palabra de estado sin correr un bit: la fila declara en el mapa
qué palabra es (`bitfield`), y qué dice cada bit lo contesta `system/formats/catalog.py`,
que lo toma del módulo dueño. Una palabra nueva es una fila en el mapa y una entrada en el
catálogo; la pestaña no se toca.

`DiagnosticsTabs.set_service_status()` reparte a todas las pestañas que tengan ese
método, y **una pestaña que no conoce un servicio lo ignora**: así el cableado publica
todo en un solo lugar y no tiene que saber quién lo dibuja.

Una pestaña que además necesita **disparar una acción** —no sólo mostrar— la emite por
una señal y `DiagnosticsTabs` la reenvía hacia arriba hasta `MainWindow`, que es la única con la
que el cableado habla. La de licencia lo hace con dos: `license_export_requested(str)` y
`license_install_requested(str)`, cada una con la ruta que el operador eligió. La
pestaña no sabe qué pasa después, y el cableado no sabe qué botón se apretó.

Cuando esa acción tiene un resultado que el operador tiene que ver, vuelve por un método
—`show_license_result(ok, mensaje)`— y **no se mezcla con el estado**: el mensaje lo
limpia el próximo clic, no el próximo estado, así el orden en que el cableado llame a
los dos métodos deja de importar.

## Qué NO hace la UI

`ui/` es presentación. Estas cinco cosas son errores de límite, no atajos:

1. **No abre cámaras, sockets, ni archivos** que no sean sus propios estilos, iconos y
   tipografías.
   Un `QFileDialog` **no** es una excepción a esto: devuelve una ruta y no toca el
   archivo. La pestaña de licencia es el ejemplo — pregunta dónde guardar la solicitud o
   cuál `.lic` instalar, emite la ruta por una señal, y el que abre, verifica y copia es
   el cableado. Elegir una ruta es presentación; leerla y escribirla, no.
2. **No arma direcciones ni rutas de protocolo.** Las URLs de los streams llegan hechas
   por `set_stream_urls()`: la forma de la ruta la conoce el servidor de video, y si la
   UI la compusiera el formato quedaría definido en dos lugares.
3. **No corre bits ni conoce direcciones de Modbus.** La tabla de registros sale de
   `SCHEMA.descriptions()`; no hay una sola dirección escrita en `ui/`.
4. **No calcula.** Si hace falta un número, lo calcula el módulo dueño del dato. La
   pestaña de hardware consume las claves de `SystemMonitor.get_metrics()` tal como
   salen.
5. **No importa un `QThread` ni un servidor.** Lo que sí puede importar es una tabla de
   datos (`CAMERA_CATALOG`), un módulo puro (`system/formats/com_status.py`) o un
   vocabulario con dueño único (`MODE_RAW`, `REGISTERED_MODELS`).

## Cómo la cablea `main.py`

El cableado le habla **sólo a `MainWindow`**, que reparte a las vistas. No hay que saber
qué pantalla dibuja cada cosa:

```python
window = MainWindow(cfg)
window.monitor_view.set_content(CameraGrid(cfg))

# Estado de los servicios (vocabulario STATUS_* de cada subsistema)
window.set_service_status(service_status.SERVICE_MODBUS_TCP, modbus.tcp_status)
window.set_service_status(service_status.SERVICE_VIDEO_RTSP, rtsp.status)

# URLs de los streams, pedidas a quien las publica
window.set_stream_urls(service_status.SERVICE_VIDEO_RTSP, rtsp.get_stream_urls())

# Métricas, tiempos, latido, veredictos, registros y logs
window.update_hardware_metrics(monitor.get_metrics())
window.update_inference_times(result.camera_slot, result.pipeline_slot,
                              result.cycle_time_ms, result.inference_time_ms)
window.update_heartbeat(heartbeat)
window.update_verdicts({(camera_slot, pipeline_slot): {"safety_ok": 1}})
window.update_modbus_values(batch)
window.log_event("WARNING", "mensaje", "Modulo")

# GPIO: sin esto la página de entradas y salidas no aparece
window.set_gpio(gpio_ctrl, gpio_thread)
```

Los frames van a la grilla con la firma que emiten los hilos de captura,
`(frame, camera_slot)`, así que se conectan directo:

```python
capture_thread.frame_ready.connect(grid.update_frame)
capture_thread.status_updated.connect(grid.update_status)
capture_thread.status_updated.connect(window.update_camera_status)
```

Qué frame entra —el crudo o el anotado— lo decide el cableado según `grid.mode`, que es
lo que el operador eligió en el selector.

## Lo que falta

- **`main.py`**: no existe todavía. Hasta que exista, quien construye esta ventana es
  `manual_test/ui/ui_app.py`, que hace de cableado.
- **Los intrínsecos del lente** se muestran pero no se editan: una matriz 3x3 y hasta 14
  coeficientes no son un formulario, y equivocar un dígito ahí mueve todas las
  coordenadas del proyecto. Se editan en el `config.yaml`.
- **La pestaña de proceso llega vacía**, y es a propósito: `process:` es la única sección
  cuya *forma* cambia entre proyectos, así que el template no puede declarar sus campos.
  La pestaña existe para que el punto de extensión se vea, y su docstring trae los dos
  patrones que cubren casi todo —un valor suelto y un mapa por cámara—. La validación de
  lo obligatorio no va ahí: un valor cuya ausencia corrompe una medición tiene que frenar
  el arranque, y eso lo chequea `system/inference/hooks.py`, que es quien lee la sección
  y la inyecta.
- **No hay tests de `ui/`.** La suite del repo corre sin GUI, y así se queda. Lo que sí
  se puede testear sin Qt es `service_status.describe()`, que devuelve strings.
