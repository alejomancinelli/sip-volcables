"""
Constructores de los campos y los contenedores de un formulario de configuración.

Toda pestaña de `ui/views/config/` se arma con estas funciones, y por eso los anchos,
los altos de fila y el aspecto de una tarjeta son iguales en todas sin que ninguna
los repita. Una pestaña nueva no elige medidas: pide `build_group_box()` y
`add_form_row()`.

Módulo de presentación: no lee ni escribe el config. Los valores entran por
parámetro y quien los saca del config es la pestaña, que es la que sabe qué clave
alimenta cada campo.
"""

from PySide6.QtCore import QEvent, QObject, QSize, Qt
from PySide6.QtWidgets import (
    QAbstractScrollArea, QAbstractSpinBox, QApplication, QCheckBox, QComboBox,
    QDoubleSpinBox, QFormLayout, QGraphicsOpacityEffect, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel,
    QLineEdit, QProxyStyle, QScrollArea, QSizePolicy, QSpinBox, QStyle, QStyleFactory,
    QStyleOptionTabWidgetFrame, QTabBar, QTabWidget, QVBoxLayout, QWidget,
)

_LABEL_MIN_WIDTH_PX = 240    # ancho mínimo de la etiqueta, para alinear todas las filas
_ROW_MIN_HEIGHT_PX = 28      # alto mínimo de una fila, para que todas midan igual
_BOX_MIN_WIDTH_PX = 640      # ancho mínimo de un QGroupBox
_CARD_MIN_WIDTH_PX = 720     # ancho mínimo de la tarjeta de una pestaña
# Ancho del contenido de una página —su encabezado, sus pestañas y sus tarjetas—: la columna
# de 1100 px de la web menos su aire a los lados. De ahí en más, fondo a los costados.
PAGE_CONTENT_WIDTH_PX = 1036
_CARD_WIDTH_PX = PAGE_CONTENT_WIDTH_PX   # una tarjeta centrada llena la columna de la página
_FULL_CARD_MAX_WIDTH_PX = 6400   # tope de una tarjeta a todo el ancho, para una tabla
# Sin aire a los costados, para que las tarjetas arranquen en el borde del encabezado de la
# página; arriba, el que separa la barra de pestañas de la primera tarjeta.
_CARD_MARGINS_PX = (0, 16, 0, 4)
_DISABLED_OPACITY = 0.35     # opacidad de un campo que su checkbox de habilitado apagó
_CHECK_COLUMN_GAP_PX = 16
_CHECK_ROW_GAP_PX = 8

_CARD_OBJECT_NAME = "configCard"     # lo pinta el QSS del tema
_HINT_OBJECT_NAME = "hintLabel"
_SLOT_TABS_OBJECT_NAME = "slotTabs"  # el QSS la achica: va adentro de una tarjeta, no es la página
_GENERAL_TAB_PROPERTY = "hasGeneralTab"  # el QSS tiñe la última pestaña de esa barra


def build_group_box(title: str) -> tuple[QGroupBox, QFormLayout]:
    """Devuelve un QGroupBox con su QFormLayout, listos para agregarle filas."""
    box = QGroupBox(title)
    box.setMinimumWidth(_BOX_MIN_WIDTH_PX)
    form = _build_form_layout()
    box.setLayout(form)
    return box, form


def build_form_page() -> tuple[QWidget, QFormLayout]:
    """Devuelve una página con su QFormLayout, para el formulario de una sub-pestaña."""
    page = QWidget()
    form = _build_form_layout()
    page.setLayout(form)
    return page, form


def build_slot_tabs(*, in_card: bool = True, fit_to_current: bool = False) -> QTabWidget:
    """
    Barra de sub-pestañas, una por slot —cámara, modelo, pipeline—.

    `fit_to_current` la hace medir lo que la sub-pestaña a la vista, para cuando las páginas
    no tienen el mismo alto: una cámara que miran dos pipelines y otra que mira uno.

    Con más slots de los que entran a lo ancho, Qt muestra flechas de desplazamiento en la
    barra y no corta ningún rótulo: un slot cortado a la mitad no se distingue de otro. Se
    fija explícito para que no dependa del estilo ni del tema.

    `in_card` la achica un paso, para cuando va adentro de una tarjeta. En False tiene el
    tamaño de las de la página: es lo que corresponde cuando es la única barra a la vista,
    porque la página esconde la suya con una sola pestaña.
    """
    tabs = _SlotTabWidget(fit_to_current)
    if in_card:
        tabs.setObjectName(_SLOT_TABS_OBJECT_NAME)
    tabs.setUsesScrollButtons(True)
    tabs.setElideMode(Qt.ElideNone)
    return tabs


class _SlotTabWidget(QTabWidget):
    """
    La barra de `build_slot_tabs()`. Con `fit_to_current` mide lo que la sub-pestaña a la
    vista y no lo que la más alta: sin eso una página corta queda con el alto de la más
    larga, y la pestaña con el scroll de un contenido que no está.

    No alcanza con la política de tamaño de las páginas ocultas: `QStackedLayout` le pide el
    alto para un ancho (`heightForWidth`) a cada página directamente, sin mirar la política,
    y una página con un texto que se parte en líneas —la descripción de una sección— sigue
    reclamando el suyo. Por eso se descuenta el de la pila y se suma el de la página a la
    vista. Vale para la barra arriba, que es la única que se usa.
    """

    def __init__(self, fit_to_current: bool, parent=None):
        super().__init__(parent)
        self._is_fit_to_current = fit_to_current
        if fit_to_current:
            self.currentChanged.connect(self._fit)

    def tabInserted(self, index: int):
        super().tabInserted(index)
        if self._is_fit_to_current:
            self._fit(self.currentIndex())

    def heightForWidth(self, width: int) -> int:
        total_px = super().heightForWidth(width)
        current = self.currentWidget()
        stack = current.parentWidget() if current is not None else None
        if not self._is_fit_to_current or stack is None or not stack.hasHeightForWidth():
            return total_px
        option = QStyleOptionTabWidgetFrame()
        self.initStyleOption(option)
        padding = self.style().sizeFromContents(QStyle.CT_TabWidget, option, QSize(0, 0), self)
        stack_width_px = width - padding.width()
        page_px = (current.heightForWidth(stack_width_px) if current.hasHeightForWidth()
                   else current.sizeHint().height())
        page_px = max(page_px, current.minimumSizeHint().height())
        return total_px - stack.heightForWidth(stack_width_px) + page_px

    def _fit(self, index: int):
        for position in range(self.count()):
            policy = QSizePolicy.Preferred if position == index else QSizePolicy.Ignored
            self.widget(position).setSizePolicy(QSizePolicy.Preferred, policy)
        # Sin esto la barra se queda con el alto que ya calculó hasta el próximo relayout.
        self.updateGeometry()


def add_general_tab(tabs: QTabWidget, widget: QWidget, title: str) -> int:
    """
    Agrega, última, la sub-pestaña de lo que vale para todos los slots —la detección de
    lente sucio de todas las cámaras—. Devuelve su índice.

    Va con otro tinte para que no se lea como un slot más. Lo pone el QSS sobre la última
    pestaña de una barra marcada con `hasGeneralTab`, así que tiene que ser la última que se
    agrega, y el color sigue al tema sin que nadie lo repinte.
    """
    tabs.setProperty(_GENERAL_TAB_PROPERTY, True)
    return tabs.addTab(widget, title)


def _build_form_layout() -> QFormLayout:
    form = QFormLayout()
    form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
    form.setSpacing(8)
    return form


def add_form_row(form: QFormLayout, label: str, widget: QWidget) -> QWidget:
    """Agrega una fila etiqueta + campo con las medidas del formulario, y devuelve el campo."""
    label_widget = QLabel(label)
    label_widget.setMinimumWidth(_LABEL_MIN_WIDTH_PX)
    label_widget.setMinimumHeight(_ROW_MIN_HEIGHT_PX)
    widget.setMinimumHeight(_ROW_MIN_HEIGHT_PX)
    form.addRow(label_widget, widget)
    return widget


def add_check_row(form: QFormLayout, text: str, checked: bool) -> QCheckBox:
    """Agrega un checkbox suelto —sin etiqueta a la izquierda— y lo devuelve."""
    check = QCheckBox(text)
    check.setChecked(bool(checked))
    check.setMinimumHeight(_ROW_MIN_HEIGHT_PX)
    form.addRow("", check)
    return check


def add_hint_row(form: QFormLayout, text: str) -> QLabel:
    """Agrega una nota al pie de un grupo. El color lo pone el QSS por objectName."""
    hint = QLabel(text)
    hint.setObjectName(_HINT_OBJECT_NAME)
    hint.setWordWrap(True)
    form.addRow("", hint)
    return hint


def build_spin_box(min_value: int, max_value: int, value: int) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(min_value, max_value)
    spin.setValue(int(value))
    return spin


def build_double_spin_box(min_value: float, max_value: float, value: float, *,
                          decimals: int = 2, step: float = 0.1,
                          special_value_text: str = "") -> QDoubleSpinBox:
    """
    Spin decimal. `special_value_text` es lo que muestra en el mínimo, si se le da.

    Es para las claves donde el mínimo no es un número sino un modo —0 = automático, 0 =
    nativo—: sin esto el operador ve un 0 que parece un valor y el rango no le dice que
    significa otra cosa.
    """
    spin = QDoubleSpinBox()
    spin.setRange(min_value, max_value)
    spin.setDecimals(decimals)
    spin.setSingleStep(step)
    if special_value_text:
        spin.setSpecialValueText(special_value_text)
    spin.setValue(float(value))
    return spin


def build_combo_box(items: list, current: str) -> QComboBox:
    """
    Combo con `items` y `current` seleccionado.

    **Un `current` que no está en la lista se agrega como opción y queda elegido.** Sin
    eso el combo caería en su primer item y el `save()` escribiría ese valor: abrir el
    panel y guardar cambiaría, en silencio, una cámara sin configurar a la primera
    marca del catálogo, o un modelo que el fork todavía no registró en la fábrica al
    primero que haya. La opción intrusa se ve en pantalla, que es lo que hace falta para
    darse cuenta de que hay que elegir.
    """
    combo = QComboBox()
    options = [str(item) for item in items]
    if current not in ("", None) and str(current) not in options:
        options.insert(0, str(current))
    combo.addItems(options)
    if current not in ("", None):
        combo.setCurrentText(str(current))
    return combo


def set_combo_value(combo: QComboBox, value: object):
    """
    Selecciona `value` en el combo, agregándolo como opción si no está.

    Es lo que hace falta en un `load()`: `setCurrentText()` sobre un combo no editable
    es un **no-op silencioso** cuando el item no existe, así que el combo se queda en
    su primera opción y el `save()` la escribe encima del valor del config. Con esto un
    valor desconocido se ve en pantalla y vuelve al archivo tal como estaba.

    Sólo para los combos cuyo texto **es** el valor del config. Si el texto se traduce,
    el par es `build_value_combo_box()` + `set_combo_data()`.
    """
    text = "" if value is None else str(value)
    if not text:
        return
    if combo.findText(text) < 0:
        combo.insertItem(0, text)
    combo.setCurrentText(text)


def build_value_combo_box(options: dict, current: object) -> QComboBox:
    """
    Combo cuyo valor de config viaja en el item, no en el texto que se muestra.

    `options` va del texto visible al valor que va al config, y ese valor se lee con
    `currentData()`. Es lo que corresponde cuando el texto se traduce: reconstruir el
    valor a partir de lo que se ve lo ata al idioma con el que se armó el combo, y
    después de cambiar de idioma el `save()` escribe la etiqueta —«Sob demanda»— en
    lugar del valor.

    Un `current` que no está entre los valores se agrega como opción con su forma
    cruda, por el mismo motivo que en `build_combo_box()`.
    """
    combo = QComboBox()
    for label, value in options.items():
        combo.addItem(str(label), value)
    set_combo_data(combo, current)
    return combo


def set_combo_data(combo: QComboBox, value: object):
    """
    Selecciona el item cuyo dato es `value`, agregándolo como opción si no está.

    El par de `build_value_combo_box()` para el `load()` de una pestaña: busca por el
    valor del config y no por el texto, que cambia con el idioma. Un valor desconocido
    se agrega mostrando su forma cruda y queda elegido, así vuelve al archivo tal como
    estaba; uno vacío deja el combo donde está, porque no hay nada que mostrar.
    """
    for index in range(combo.count()):
        if combo.itemData(index) == value:
            combo.setCurrentIndex(index)
            return
    text = "" if value is None else str(value)
    if not text:
        return
    combo.insertItem(0, text, value)
    combo.setCurrentIndex(0)


def build_inline_row(main: QWidget, *extras: QWidget) -> QWidget:
    """
    Un campo y lo que lo acompaña en la misma línea —un botón que lo marca sobre la imagen,
    un estado—. El primero se lleva el ancho que sobra.
    """
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    # Un campo de texto es de alto fijo y quedaría más bajo que el botón de al lado.
    main.setSizePolicy(main.sizePolicy().horizontalPolicy(), QSizePolicy.Preferred)
    layout.addWidget(main, stretch=1)
    for widget in extras:
        layout.addWidget(widget)
    return row


def build_check_grid(checks: list[QCheckBox], columns: int) -> QWidget:
    """Casillas en `columns` columnas del mismo ancho: una por cámara, una por archivo."""
    grid_widget = QWidget()
    grid = QGridLayout(grid_widget)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(_CHECK_COLUMN_GAP_PX)
    grid.setVerticalSpacing(_CHECK_ROW_GAP_PX)
    for index, check in enumerate(checks):
        grid.addWidget(check, index // columns, index % columns)
    for column in range(columns):
        grid.setColumnStretch(column, 1)
    return grid_widget


def build_line_edit(value: str, placeholder: str = "") -> QLineEdit:
    line = QLineEdit(str(value if value is not None else ""))
    line.setPlaceholderText(placeholder)
    return line


def build_readonly_line_edit(value: object) -> QLineEdit:
    """Campo de sólo lectura, para lo que se muestra pero se edita en el config.yaml."""
    line = QLineEdit(str(value if value is not None else ""))
    line.setReadOnly(True)
    line.setObjectName("readonlyField")
    return line


def wrap_in_card(inner: QWidget, *, center: bool = True, scroll: bool = False) -> QWidget:
    """
    Envuelve el contenido de una pestaña en la tarjeta del tema.

    `center` deja el contenido centrado con fondo a los lados, que es lo que quieren
    los grupos de campos; en False ocupa todo el ancho, para una tabla. `scroll`
    agrega la barra vertical, para las pestañas que no entran en pantalla.

    **Toda tarjeta centrada mide lo mismo, en todas las pestañas**: se estira hasta
    `_CARD_WIDTH_PX` y por debajo de eso ocupa lo que haya, con el mínimo de
    `_CARD_MIN_WIDTH_PX`. Si tomara el ancho que pide su contenido, cada pestaña tendría el
    suyo y el fondo saltaría al cambiar de una a otra. No es un ancho fijo: con el scroll
    horizontal apagado, en una pantalla chica cortaría los campos.

    Por lo mismo, una tarjeta centrada con scroll queda en el mismo lugar haya barra
    vertical o no: sin barra se reserva su ancho a la derecha. Si no, la pestaña larga
    quedaría corrida medio ancho de barra respecto de la corta.
    """
    card = QWidget()
    card.setObjectName(_CARD_OBJECT_NAME)
    card.setAttribute(Qt.WA_StyledBackground, True)
    card.setMinimumWidth(_CARD_MIN_WIDTH_PX)
    card.setMaximumWidth(_CARD_WIDTH_PX if center else _FULL_CARD_MAX_WIDTH_PX)
    card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    card_layout = QVBoxLayout(card)
    card_layout.setContentsMargins(*_CARD_MARGINS_PX)
    card_layout.addWidget(inner)

    wrapper = QWidget()
    wrapper_layout = QHBoxLayout(wrapper)
    wrapper_layout.setContentsMargins(0, 0, 0, 0)
    # La tarjeta tiene el stretch: crece primero hasta su tope, y lo que sobra va a los
    # costados.
    if center:
        wrapper_layout.addStretch(0)
    wrapper_layout.addWidget(card, 1)
    if center:
        wrapper_layout.addStretch(0)

    if not scroll:
        return wrapper

    area = _BarReservingScrollArea(wrapper_layout) if center else QScrollArea()
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.viewport().setAutoFillBackground(False)
    area.setWidget(wrapper)
    return area


class _BarReservingScrollArea(QScrollArea):
    """
    Área con scroll que, sin barra vertical, reserva su ancho a la derecha del contenido.

    Con margen o con barra el contenido tiene el mismo ancho, así que reservarlo no hace
    aparecer ni desaparecer la barra: no oscila.
    """

    def __init__(self, content_layout: QHBoxLayout, parent=None):
        super().__init__(parent)
        self._content_layout = content_layout
        self.verticalScrollBar().rangeChanged.connect(self._reserve_bar_width)

    def showEvent(self, event):
        super().showEvent(event)
        # Un contenido que nunca necesita barra no cambia el rango, y el margen se pone acá.
        self._reserve_bar_width()

    def _reserve_bar_width(self):
        bar = self.verticalScrollBar()
        # El ancho se pide al estilo y no a la barra, que oculta no tiene medida; con el
        # QSS aplicado, el estilo devuelve el ancho que declara la hoja.
        width_px = 0 if bar.maximum() > 0 else self.style().pixelMetric(
            QStyle.PM_ScrollBarExtent, None, bar)
        self._content_layout.setContentsMargins(0, 0, width_px, 0)


_GUARDED_TYPES = (QAbstractSpinBox, QComboBox, QTabBar)


class WheelGuard(QObject):
    """
    Filtro de eventos: la rueda del mouse no cambia el valor de un spin ni de un combo, ni
    la pestaña elegida de una barra, y sigue desplazando la página que los contiene.

    Sin esto, bajar por una pestaña larga con la rueda cambia el valor de cada campo que
    pasa debajo del puntero —una exposición, un umbral— sin que el operador lo note, y al
    pasar por una barra de sub-pestañas lo deja editando otra cámara u otro pipeline. Un
    valor se cambia tipeando, con las flechas o con el teclado; una pestaña, con un clic.

    Se instala con `install_wheel_guard()`, que lo pone en cada uno de esos widgets y no en
    la aplicación: instalado en la `QApplication` veía pasar cada evento de cada widget —el
    video, los timers, cada repintado— por Python, y sólo eso era un tercio de lo que
    tardaba cambiar de página.
    """

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() != QEvent.Wheel or not isinstance(
                watched, (QAbstractSpinBox, QComboBox, QTabBar)):
            return False
        area = _find_scroll_area(watched)
        if area is not None:
            QApplication.sendEvent(area.viewport(), event.clone())
        return True


class _WheelGuardStyle(QProxyStyle):
    """
    El estilo de la aplicación con un gancho: cada widget que se pule y es de un tipo que la
    rueda cambiaría recibe el `WheelGuard`. Qt pule cada widget antes de mostrarlo —también
    con la hoja de estilo, que delega en este estilo—, así que alcanza a toda la interfaz,
    diálogos y widgets del fork incluidos, sin que nadie lo instale a mano.
    """

    def __init__(self, base_style: QStyle, guard: WheelGuard):
        super().__init__(base_style)
        self.guard = guard

    def polish(self, target):
        result = super().polish(target)
        if isinstance(target, _GUARDED_TYPES):
            # Instalarlo dos veces no lo duplica: Qt deja uno solo.
            target.installEventFilter(self.guard)
        return result


def install_wheel_guard(app: QApplication) -> WheelGuard:
    """
    Pone el `WheelGuard` en cada spin, combo y barra de pestañas de la aplicación, los que ya
    existen y los que se creen después. Llamarlo de nuevo devuelve el que ya está.
    """
    current = app.style()
    if isinstance(current, _WheelGuardStyle):
        return current.guard
    guard = WheelGuard(app)
    # Una copia del estilo actual, por nombre: el proxy se queda con la base, y la de ahora
    # la borra `setStyle()` al reemplazarla.
    app.setStyle(_WheelGuardStyle(QStyleFactory.create(current.name()), guard))
    return guard


def _find_scroll_area(widget: QWidget) -> QAbstractScrollArea | None:
    """El área con scroll más cercana que contiene al widget, o None."""
    parent = widget.parentWidget()
    while parent is not None and not isinstance(parent, QAbstractScrollArea):
        parent = parent.parentWidget()
    return parent


def wire_enable_toggle(check: QCheckBox, dependents: list):
    """
    Ata un checkbox de habilitado a los campos que dependen de él.

    Desmarcado los deshabilita y los atenúa. Un dependiente que también es checkbox
    re-emite su `toggled` al volver a habilitarse, así una cadena de tres niveles
    —recolección → dedup → sus campos— queda consistente sin que el llamador la
    recorra a mano.
    """
    def apply_state(checked: bool, widgets: list = dependents):
        for widget in widgets:
            set_field_enabled(widget, checked)
            if isinstance(widget, QCheckBox):
                widget.toggled.emit(widget.isChecked() and checked)

    check.toggled.connect(apply_state)
    apply_state(check.isChecked())


def set_field_enabled(widget: QWidget, enabled: bool):
    """Prende o apaga un campo; apagado, lo atenúa como `wire_enable_toggle()`."""
    widget.setEnabled(enabled)
    if enabled:
        widget.setGraphicsEffect(None)
    else:
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(_DISABLED_OPACITY)
        widget.setGraphicsEffect(effect)

