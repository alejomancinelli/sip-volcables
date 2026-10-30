# Capas de ROI: del ROI de cámara al ROI por pipeline, y el recorte en la adquisición

Estado: **implementado** —fases 1, 1b y 2, y la guarda del tamaño del frame (4.4)—. Parte de
una revisión del razonamiento «el recorte en la interfaz de la cámara es lo mejor, el de cada
pipeline está bien, y el ROI de la pestaña de cámara ya no tiene sentido».

**Lo que cambió respecto de este plan al implementarlo:** `full_resolution` y el lado de
salida de la vista ya no existen. El corte del video nativo lo decide la cámara —todo ROI en
una cámara `rtsp` o `video_file` sin preprocessor se corta del nativo— y el tamaño de la vista
lo fija la entrada del modelo que la lee, leída del `.engine` (`AbstractModel.peek_input()`).
La migración borra esas dos claves y avisa si pedían otra cosa. Donde el texto de abajo los
menciona, describe la propuesta y no el código. El estado vigente está en `CLAUDE.md` y en
`docs/jetson.md` §4.1.1 y §4.4.

## 1. Qué hay hoy (verificado en el código)

Tres cosas que se llaman ROI y no son lo mismo:

| Capa | Dónde vive | Qué hace de verdad |
|---|---|---|
| **ROI de cámara** | `cameras.<slot>.roi` | Recorte por software, después de decodificar y rotar. Lo usan: el motor como *fallback* de todo pipeline sin ROI propio (`engine._read_roi`), `lens_health` como región de medición (`get_analysis_region`), el recuadro azul de `camera_panel._draw_roi` y las `conditions` de la calibración de óptica. No ahorra red ni decodificación. |
| **ROI de pipeline** | `inference.pipelines.<slot>.roi.<cámara>` | Recorte propio de un modelo en una cámara. Con `full_resolution` el decodificador lo corta del video nativo (vista) y el dataset guarda esa región. Ahorra GPU, y CPU de reescalado; no red. |
| **Recorte en la interfaz de la cámara** (AOI / Width-Height-Offset del sensor) | **No existe en la app.** `basler_driver` y `st_driver` sólo fijan exposición, ganancia, fps y paquetes GigE; no tocan Width/Height/OffsetX/OffsetY. Si hoy lo usás, está hecho desde pylon Viewer / StCamera y la app no lo sabe. Las 4 cámaras de este fork son RTSP (`decode:` sólo aplica a RTSP y video), donde esa capa directamente no existe. | Es la única capa que ahorra **red**. |

El ROI de pipeline hoy se ve así en la GUI: un grupo por (pipeline, cámara) dentro de la
pestaña Inferencia —combo de tres modos, cuatro spin boxes, `full_resolution`, lado de salida,
botón que abre un diálogo modal con **un solo** rectángulo—. En el monitor, `camera_panel`
sólo dibuja el ROI de cámara: los de los pipelines no se ven fuera del frame anotado.

## 2. Evaluación del razonamiento

**Acierto: el recorte en la cámara es el mejor, y por más que la red.** Un AOI de sensor
también sube el fps máximo posible (menos filas que leer), baja el CPU de conversión y de
copia a numpy, y achica el dataset. Eso es verdad para GigE y es la razón de peso.

**Acierto: el ROI de pipeline sigue teniendo sentido** —varios modelos mirando partes
distintas—, y por más razones que la que das: es el único con dataset propio por región y el
único que puede pedir resolución nativa por modelo.

**Acierto con matiz: el ROI de cámara actual ya no tiene sentido como recorte de
inferencia.** Hoy es un *default* que los pipelines heredan; el modo «La de la cámara» del
combo existe sólo para eso. Pero no es redundante del todo, y quitarlo sin más rompe dos
cosas:

1. **`lens_health` lo usa como región de medición.** Es otra pregunta —¿dónde hay textura
   estática y nítida para medir el vidrio?—, no «qué mira el modelo». Si se borra el ROI de
   cámara hay que darle a la óptica su propia región (`lens_health.roi` por cámara) o medir
   el frame entero. Además `conditions.roi` de las calibraciones ya hechas quedaría
   desactualizado.
2. **Un fork con `cameras.<slot>.roi.enabled: true` cambiaría de comportamiento en
   silencio**: sus pipelines sin ROI propio pasarían a inferir el frame entero. Es justo el
   tipo de falla que este repo evita («no da un error, da un equipo que mide peor»). Hace
   falta migración, no borrado.

**Lo que falta en el razonamiento:**

- **El recorte de adquisición no sustituye a los de pipeline; los contiene.** Es un solo
  rectángulo para todos los consumidores de la cámara, así que su tamaño mínimo es la
  *unión* de los ROI de sus pipelines. En `camera_3`, con el grano en una zona y un detector
  de personas que quiere la escena entera, la unión es el frame completo y el AOI no ahorra
  nada. Es una optimización por cámara, que se aplica sólo cuando la unión es chica.
- **Cambia el espacio de coordenadas de todo lo que está en píxeles del frame**: ROI de
  pipeline, `scale_px_per_mm`, `max_load_y_px`, annotations, ROI de óptica, y la calibración
  de lente (`camera_matrix` está referida al sensor completo: con un AOI hay que correr el
  punto principal por el offset, o prohibir la combinación como ya se hace con las vistas
  nativas y el preprocessor). Igual que `rotation` y `decode` hoy: cambiarlo pide rehacer los
  rectángulos.
- **Los streams crudos y el dataset de cámara pierden lo que queda afuera.** Es
  irrecuperable, y el repo ya tiene la regla de que el dataset es lo único que no se puede
  regenerar. Por eso el recorte de adquisición debería ser una decisión de puesta en marcha,
  explícita, y nunca un default.
- **Para RTSP, el recorte «en la cámara» no ahorra red**: el stream ya sale codificado del
  servidor/cámara a su tamaño. Lo que se puede hacer ahí es lo que ya existe (la vista
  nativa y `decode.width_px/height_px`), que ahorra CPU y no red.
**Conclusión:** tres capas con un solo concepto cada una —*qué se adquiere* (cámara),
*qué mira cada modelo* (pipeline), *qué se muestra* (UI)—, en vez de tres «ROI». El ROI de
cámara actual se retira como recorte de inferencia; el recorte de adquisición queda del
lado de la cámara, fuera de la app (sección 4).

## 3. Plan

Cada fase se cierra sola y se commitea aparte: la maquinaria va en commits separados de lo
del fork (ver «Cómo devolver una mejora al template» del README).

### Fase 1 — GUI de ROI de pipelines (sin cambiar comportamiento)

Objetivo: ver y editar **todos** los rectángulos de una cámara juntos, que es como se
piensan.

1. **Una función única que resuelve el ROI** `resolve_pipeline_roi(pipeline_section, camera_slot,
   camera_roi)` en `system/inference/regions.py` (módulo nuevo, puro; `select_pipeline_roi` se
   mudó ahí desde `preprocessing.py`), que antes dejaba el *fallback* a la cámara en
   `engine._read_roi`. La GUI no puede reimplementar esa
   regla: el recuadro dibujado y el que mide el motor tienen que salir del mismo lugar. El
   motor pasa a usarla.
2. **Diálogo de regiones por cámara, con una pestaña por pipeline** (clase nueva
   `PipelineRegionsDialog` en `ui/dialogs/`; `roi_dialog.py` queda para un solo rectángulo,
   que `lens_health` va a seguir necesitando en la Fase 2). Se abre sobre el frame en vivo
   de esa cámara, el mismo que ya alimenta al diálogo actual. Arriba, el canvas dibuja los
   rectángulos de **todos** los pipelines de esa cámara, cada uno con su color; abajo, una
   pestaña por pipeline que la mira (los que no la miran no aparecen). La pestaña activa
   tiene su rectángulo sólido y editable —arrastrar, asas en los bordes—; los de las otras
   se ven huecos y finos, para ver cuánto se pisan. Cada pestaña lleva: casilla «Región
   propia» (apagada = frame completo), los cuatro números como entrada secundaria,
   `full_resolution` y lado de salida. Cambiar de pestaña no cierra nada; aceptar escribe
   todo y persiste una sola vez.
3. **Aviso y vista previa, dentro del diálogo** (confirmados):
   - **Vista previa** de lo que recibe el modelo: el recorte del frame en vivo al tamaño de
     salida del pipeline, junto a su pestaña. Sale del frame de referencia, así que con
     `full_resolution` es una aproximación (le falta el detalle nativo) y la etiqueta lo
     dice. Una vista previa con la vista nativa real queda como mejora aparte: exige que el
     frame del diálogo traiga `get_views()`.
   - **Avisos**, en una franja bajo el canvas y también en la fila-resumen de la pestaña
     Inferencia: el ROI se sale del frame; `full_resolution` pedido en una cámara con
     corrección de lente o ajuste de imagen (hoy sólo sale en el log como `no_view`); lado
     de salida mayor que lo que el recorte tiene (agranda en vez de reducir); rectángulo
     real de la vista nativa distinto del pedido (el decodificador corta en pares); y, de la
     guarda de la sección 4, que el tamaño del frame cambió desde que se dibujó.
   - **Superposición** entre pipelines, como porcentaje, para ver cuánto del frame comparten.
4. **Un solo lugar para abrirlo: la pestaña Inferencia.** Cada pipeline ya tiene su
   sub-pestaña; ahí, por cada cámara que mira, queda una fila-resumen («camera_4: 256,95 ·
   218×220 · nativo · 224 px», más el aviso si lo hay) con el botón «Editar regiones…», que
   abre el diálogo de esa cámara con la pestaña de ese pipeline seleccionada. La pestaña
   Cámaras **no** tiene botón al diálogo ni lo tendrá. Los spin boxes sueltos por pipeline
   se van de la pestaña, y el modo «La de la cámara» sigue andando hasta la Fase 2.
5. **Monitor: el frame completo recibido, con los ROI dibujados encima.** Hoy el ROI que se
   ve es el de cámara, y sólo en modo crudo; en modo anotado lo dibuja `overlay.draw_roi`,
   gris, de 1 px y sin nombre. Esto pasa a ser un solo criterio:
   - **Anotado** (el frame se dibuja una vez y viaja a la UI, al stream y al dataset): el
     ROI lo dibuja el overlay con el color del pipeline y su nombre en el borde. Los
     pipelines de frame completo no dibujan rectángulo. El composer ya combina los de varios
     pipelines sobre una cámara.
   - **Crudo**: lo dibuja `camera_panel` con los mismos colores y nombres, tomando los
     rectángulos de la función del punto 1. Es la única vez que el panel dibuja ROI; en
     anotado no, para no duplicar.
   - **El color de un pipeline es uno solo** y sale de una función de `overlay.py`
     (`get_pipeline_color_bgr(index)`, paleta propia **sin verde, rojo ni amarillo**, índice por
     orden en `inference.pipelines`):
     el overlay, el panel y el diálogo la leen, y `theme.py` conserva `roi_outline`, que todavía usan los diálogos de un solo rectángulo. Esos tres
     colores quedan reservados para el estado (Fase 1b): azul, cian, magenta, violeta, blanco
     y gris dan seis pipelines distinguibles, y más allá de seis lo que los distingue es el
     nombre y el trazo (continuo/punteado), no un color parecido a otro. Un test falla si la
     paleta contiene un tono dentro del rango de esos tres. Es una
     excepción deliberada a «`theme.py` único dueño de los colores»: lo que se dibuja sobre
     el frame ya es del overlay, y la UI ya lo lee.
   - **`crop_to_roi` contradice esto**: con él el monitor muestra el recorte de un pipeline
     y no el frame. Está en `false` y sólo en `config.yaml`; se retira en la Fase 2
     (el recorte que importa ya es el dataset y la vista de cada pipeline). Hasta entonces,
     con `crop_to_roi: true` el monitor no muestra el frame completo, y está documentado.
6. Textos nuevos en `ui/strings.py` (es/en/pt) y `docs/ui.md`. La UI no arma lógica de
   geometría: el motor, el overlay, el panel y el diálogo comparten la función del punto 1.
7. Tests: `test/ui/test_inference_tab.py` (guarda las mismas claves que hoy para las mismas
   entradas; el `null` de «sin ROI propio» sigue sin escribirse al abrir y guardar),
   `test/ui/test_camera_panel.py` (dibuja los ROI de los pipelines en crudo y ninguno en
   anotado), `test/inference/test_overlay.py` (color y nombre por pipeline), uno nuevo de
   `PipelineRegionsDialog` y su canvas (conversión pantalla↔frame, selección, límites, cada
   aviso, la vista previa, sin pantalla real) y `test/inference/test_engine.py` para que el
   motor resuelva igual con la función extraída.

### Fase 1b — ROI pintado por el veredicto

Sí se puede, y encaja justo por lo de arriba: como los colores de identidad no usan verde,
rojo ni amarillo, esos tres pueden significar **estado** sin confundirse con un pipeline.

1. **Quién dice el estado es el pipeline, igual que con `labels`.** Sólo el código del
   proyecto sabe qué `OK` / `NOK` equivale un veredicto («clasificación del grano»,
   «persona en zona»). El analyzer no sirve: no modifica el resultado. Entonces
   `InferenceResult` gana `roi_state` (vocabulario `ROI_STATE_*` en `result.py`: sin estado,
   `ok`, `nok`, `warning`) y el pipeline lo declara al lado de sus `labels`, que el motor ya
   copia al resultado. Es un campo de contrato: va a la lista de `result.py`, a `to_dict()` y
   al JSON del dataset, para que lo guardado diga qué se vio.
2. **Reglas del dibujo** (`overlay.draw_roi`, que ya usan el anotado propio y `draw_onto`
   del composer):
   - sin estado: borde del color del pipeline, como hoy;
   - `ok` / `nok` / `warning`: borde verde / rojo / amarillo, **y el nombre del pipeline
     sigue en su color propio** en la etiqueta, para no perder la identidad;
   - **un resultado no confiable** (`is_valid: false`) **nunca se pinta**: borde gris
     punteado con el motivo, aunque el pipeline haya dicho `ok`. Es la misma regla de
     `result.py` —los números de un resultado inválido no son una medición—, y un recuadro
     verde sobre algo que no se midió es peor que ninguno;
   - **el color solo no alcanza**: el texto del veredicto va en la etiqueta («grano · NOK») y
     el `nok` además va con trazo más grueso. Un operador daltónico o un stream en blanco y
     negro no pueden perder la alarma.
3. **Vida del estado.** Lo trae el resultado, así que el composer lo trata como el resto: se
   descarta pasada su vigencia y el recuadro vuelve a su color de identidad, y un pipeline
   cerrado por la compuerta de frames saca el recuadro del anotado, como ya hace. No se
   inventa memoria de «el último NOK».
4. **Sólo en anotado.** El modo crudo no tiene resultado: el panel sigue dibujando la
   geometría del config con el color del pipeline. Pintar ahí por el último resultado sería
   mostrar un estado en un frame que no lo produjo.
5. **Los colores del estado son del overlay** (`ROI_STATE_COLORS_BGR`). Los chips de la UI son
   tonos translúcidos del QSS, no valores sobre un frame, así que no hay nada que comparar: se
   parecen a propósito y cada uno tiene su dueño. Un test verifica que caen en los rangos de
   tono que la paleta de pipelines evita.
6. En el **template** ningún pipeline pone `roi_state` —sólo el de una etapa con un modelo
   sintético, para probarlo y para que se vea en `manual_test/ui/`—; el pipeline del fork lo
   completa. Queda documentado en
   `result.py`, en `docs/ui.md` y en el README de «qué se toca en un fork».
7. Tests: `test/inference/test_overlay.py` (cada estado, el inválido, el trazo), `test_result.py`
   (campo y `to_dict()`), `test_engine.py` (el estado del pipeline llega al resultado),
   `test_composer.py` (vigencia) y el de paleta sin verde/rojo/amarillo.

### Fase 2 — Retirar el ROI de cámara como recorte de inferencia

1. **Migración, no borrado.** Al arrancar, si una cámara tiene `roi.enabled: true`, se copia
   a `inference.pipelines.<p>.roi.<cámara>` de cada pipeline que la mira y no declara el
   suyo, con un aviso en el log por cada copia y el `config.yaml` guardado. Es una
   función pura probada aparte, y se corre una sola vez.
2. El motor deja de caer al ROI de cámara: sin ROI propio, frame entero. Se quita el modo
   «la de la cámara», el grupo ROI de `cameras_tab.py` y `inference.overlay.crop_to_roi`
   (con un aviso de arranque si un config todavía lo trae en `true`).
3. **`lens_health` recibe su propia región**: `cameras.<slot>.lens_health.roi` (misma forma),
   con la herramienta de dibujo en la pestaña de óptica, y la migración copia el ROI de
   cámara ahí si estaba activo. Las referencias de calibración existentes llevan el ROI viejo
   en `conditions`, y como la migración le da a la óptica ese mismo rectángulo, no cambian y
   la pantalla no marca nada.
4. `docs/jetson.md` §4.4 se reescribe (queda `illumination_min`, y el brillo se mide sobre
   la región de cada pipeline); `CLAUDE.md` pierde las frases del ROI de cámara.
5. Tests de la migración (con y sin pipelines con ROI propio, cámara no usada), del motor sin
   *fallback*, y de `lens_health` con su región.

## 4. El recorte en la cámara queda fuera de la app

Decidido: el recorte de interfaz (AOI de pylon / StCamera, o lo que ofrezca la cámara IP) lo
configura quien instala, con la herramienta del fabricante, y **la app no lo escribe ni lo
muestra**. La antigua Fase 3 (clave `acquisition.crop`, lectura de vuelta, asistente de unión)
se descarta; si aparece una instalación GigE donde la unión de ROI sea chica y haga falta
administrarlo desde la app, se reabre con este documento como base.

Lo que sí hay que dejar escrito y comprobado, porque la app lo va a sufrir aunque no lo
configure:

1. **Los drivers GigE no tocan Width/Height/Offset, así que lo que la cámara tenga se
   respeta.** `basler_driver` escribe `TriggerMode`, exposición, ganancia, fps y paquetes, y
   nada más. Lo que no está verificado: que el AOI **sobreviva a un corte de luz**. En
   Basler hay que guardarlo en un *user set* y marcarlo como el de arranque; si no, la cámara
   vuelve al sensor completo y todos los ROI quedan corridos sin ningún error. Para Sentech
   hay que verlo en StCamera. Va a `docs/` (una sección en `docs/jetson.md` o
   `docs/calibration.md`), con el orden en pylon Viewer: *Image Format Control* → achicar
   `Width` y `Height` primero y recién después `OffsetX` / `OffsetY`, respetando el
   incremento del modelo.
2. **Se hace antes de dibujar ningún ROI.** Es lo mismo que `rotation` y `decode`: todo lo
   que está en píxeles del frame se rehace si cambia. Va en el orden de puesta en marcha del
   README: recorte de cámara → rotación → ROI de pipelines → calibraciones.
3. **Corrección de lente con recorte**: `calibration.camera_matrix` se calibra con el frame
   que entrega la cámara. Si el recorte se hizo antes de calibrar, está bien; si después, el
   punto principal queda corrido. Una línea en la documentación alcanza.
4. **Una guarda barata en la app** (candidata a la Fase 1, no obligatoria): guardar con
   cada ROI el tamaño del frame sobre el que se dibujó, y mostrar un aviso en el diálogo y en
   el log al arrancar cuando el frame entregado ya no es de ese tamaño. Es lo único que
   convierte «el AOI se perdió tras un corte de luz» o «alguien lo cambió en el visor» de
   rectángulos silenciosamente corridos en una alarma. El ROI ya hace `min()` contra el frame
   y no avisa.
5. **RTSP.** No hay una interfaz común: depende de cada cámara IP. Algunas exponen en su web
   un recorte o una resolución de sub-stream; la mayoría sólo resolución y bitrate. Lo que sí
   hay siempre es la resolución del stream, que se elige en la cámara y es el equivalente de
   `decode`. Hay que mirarlo por modelo, y no se promete.

## 5. Decisiones tomadas

- GUI: diálogo por cámara con una pestaña por pipeline, el rectángulo editable de la
  pestaña activa y los demás de referencia; incluye vista previa y avisos.
- Se abre **sólo desde la pestaña Inferencia**, no desde Cámaras.
- Monitor: siempre el frame completo recibido, con los ROI de los pipelines dibujados encima
  (con color y nombre por pipeline).
- Colores de pipeline: sin verde, rojo ni amarillo, reservados para estado.
- ROI pintado por veredicto (OK verde / NOK rojo / warning amarillo), declarado por el
  pipeline, nunca en un resultado inválido, y siempre con texto además del color.
- Recorte de adquisición: externo, sin soporte en la app y sin mostrarlo. Lo que se pierde
  afuera del recorte no se muestra (aceptado).

## 6. Orden recomendado

Fase 1 sola resuelve lo de la GUI y no toca el comportamiento: va primero, con la guarda del
punto 4.4 y la documentación del recorte externo (4.1–4.3), que son baratas. La 1b sigue,
porque se apoya en el dibujo por pipeline de la 1 y es el único cambio de contrato
(`InferenceResult`). Fase 2 después,
porque sin ella «La de la cámara» sigue conviviendo con la GUI nueva.
