"""
Textos de la interfaz en los idiomas soportados.

Tabla de datos, no maquinaria: la clave es un identificador estable en inglés y el
valor es el texto que ve el operador en cada idioma. Ningún widget escribe un
literal visible: pide `tr("nav_monitor")` y el texto sale de acá.

El idioma sale de `ui.language` del config y queda fijo por corrida: cada widget
resuelve su texto al construirse, así que cambiarlo con la aplicación andando dejaría
la pantalla partida en dos idiomas. El panel de configuración lo guarda y avisa que
toma efecto al reiniciar; `set_language()` está para las pruebas y para quien arme la
UI. Un idioma que no está en la tabla cae a `_FALLBACK_LANGUAGE` y lo avisa una sola
vez; una clave que falta devuelve la clave misma, para que el hueco se vea en pantalla
en vez de romper la vista.

Para agregar un idioma se suma su código a LANGUAGES y su entrada a cada texto. Un
texto sin traducir cae al idioma de respaldo, así que se puede traducir de a partes
sin dejar la UI a medio armar.
"""

from system.config_manager import ConfigManager
from system.logger import logger

LANGUAGES = ("es", "en", "pt")
_FALLBACK_LANGUAGE = "es"

# Clave -> texto por idioma. Agrupada por zona de la UI, no alfabéticamente: se
# edita mirando la pantalla.
_TEXTS: dict[str, dict[str, str]] = {
    # ── Ventana principal ────────────────────────────────────────────────────
    # Páginas de la barra lateral: el mismo texto va en la barra, en la miga de pan y en el
    # título de la página.
    "nav_home":               {"es": "Inicio",         "en": "Home",          "pt": "Início"},
    "nav_monitor":            {"es": "Monitor",        "en": "Monitor",       "pt": "Monitor"},
    "nav_process":            {"es": "Proceso",        "en": "Process",       "pt": "Processo"},
    "nav_cameras":            {"es": "Cámaras",        "en": "Cameras",       "pt": "Câmeras"},
    "nav_inference":          {"es": "Inferencia",     "en": "Inference",     "pt": "Inferência"},
    "nav_modbus":             {"es": "Modbus",         "en": "Modbus",        "pt": "Modbus"},
    "nav_video_telemetry":    {"es": "Video y telemetría",
                               "en": "Video & telemetry",
                               "pt": "Vídeo e telemetria"},
    "nav_system":             {"es": "Sistema",        "en": "System",        "pt": "Sistema"},
    "nav_logs":               {"es": "Logs",           "en": "Logs",          "pt": "Logs"},
    "nav_gpio":               {"es": "Entradas y salidas",
                               "en": "Inputs & outputs",
                               "pt": "Entradas e saídas"},
    # La línea bajo el título de cada página: para orientarse, no un manual.
    "nav_process_description": {"es": "Los parámetros de lo que se mide en esta planta.",
                                "en": "The parameters of what this plant measures.",
                                "pt": "Os parâmetros do que se mede nesta planta."},
    "nav_cameras_description": {"es": "Conexión y adquisición de cada cámara, y la salud de su "
                                      "óptica.",
                                "en": "Connection and acquisition of each camera, and the "
                                      "health of its optics.",
                                "pt": "Conexão e aquisição de cada câmera, e a saúde da sua "
                                      "óptica."},
    "nav_inference_description": {"es": "Modelos, pipelines y regiones de cada cámara, y las "
                                        "imágenes que se guardan para reentrenar.",
                                  "en": "Models, pipelines and regions of each camera, and the "
                                        "images kept for retraining.",
                                  "pt": "Modelos, pipelines e regiões de cada câmera, e as "
                                        "imagens guardadas para retreinar."},
    "nav_modbus_description": {"es": "El canal con el PLC: qué consulta, qué registros salen "
                                     "y dónde escucha el equipo.",
                               "en": "The channel to the PLC: what it polls, which registers "
                                     "go out and where the device listens.",
                               "pt": "O canal com o CLP: o que consulta, quais registradores "
                                     "saem e onde o equipamento escuta."},
    "nav_video_telemetry_description": {"es": "Streams de video para mirar la línea y "
                                              "telemetría hacia InfluxDB y MQTT.",
                                        "en": "Video streams to watch the line and telemetry "
                                              "to InfluxDB and MQTT.",
                                        "pt": "Streams de vídeo para acompanhar a linha e "
                                              "telemetria para InfluxDB e MQTT."},
    "nav_system_description": {"es": "Hardware, cámaras y red en vivo, la licencia y la "
                                     "identidad del equipo.",
                               "en": "Live hardware, cameras and network, the licence and "
                                     "the device identity.",
                               "pt": "Hardware, câmeras e rede ao vivo, a licença e a "
                                     "identidade do equipamento."},
    "nav_logs_description":   {"es": "Lo que registra cada subsistema, con filtro por nivel "
                                     "y búsqueda.",
                               "en": "What every subsystem logs, filtered by level and "
                                     "searchable.",
                               "pt": "O que cada subsistema registra, com filtro por nível "
                                     "e busca."},
    "nav_gpio_description":   {"es": "Estado de las entradas digitales y comando de las "
                                     "salidas.",
                               "en": "State of the digital inputs and control of the outputs.",
                               "pt": "Estado das entradas digitais e comando das saídas."},
    # Rótulos de los grupos de la barra lateral; se muestran en mayúsculas.
    "nav_group_operation":    {"es": "Operación",      "en": "Operation",     "pt": "Operação"},
    "nav_group_vision":       {"es": "Visión",         "en": "Vision",        "pt": "Visão"},
    "nav_group_integration":  {"es": "Integración",    "en": "Integration",   "pt": "Integração"},
    "nav_group_device":       {"es": "Equipo",         "en": "Device",        "pt": "Equipamento"},
    "sidebar_company":        {"es": "Ingeniería Electrónica Argentina",
                               "en": "Ingeniería Electrónica Argentina",
                               "pt": "Ingeniería Electrónica Argentina"},
    "nav_collapse":           {"es": "Contraer menú",  "en": "Collapse menu", "pt": "Recolher menu"},
    "nav_expand":             {"es": "Expandir menú",  "en": "Expand menu",   "pt": "Expandir menu"},
    "status_ready":           {"es": "Listo.",         "en": "Ready.",        "pt": "Pronto."},
    "status_started":         {"es": "Sistema inicializado.",
                               "en": "System initialised.",
                               "pt": "Sistema inicializado."},
    "status_config_saved":    {"es": "Configuración guardada.",
                               "en": "Settings saved.",
                               "pt": "Configuração salva."},
    "status_theme_not_saved": {"es": "El tema se aplicó, pero no se pudo guardar en config.yaml.",
                               "en": "The theme was applied but could not be saved to "
                                     "config.yaml.",
                               "pt": "O tema foi aplicado, mas não pôde ser salvo em "
                                     "config.yaml."},
    "search_button":          {"es": "Buscar ajustes, páginas…", "en": "Search settings, pages…",
                               "pt": "Buscar ajustes, páginas…"},
    "search_placeholder":     {"es": "Buscar páginas, pestañas o ajustes…",
                               "en": "Search pages, tabs or settings…",
                               "pt": "Buscar páginas, abas ou ajustes…"},
    "search_group_pages":     {"es": "Ir a",           "en": "Go to",         "pt": "Ir para"},
    "search_group_settings":  {"es": "Ajustes",        "en": "Settings",      "pt": "Ajustes"},
    "search_group_actions":   {"es": "Acciones",       "en": "Actions",       "pt": "Ações"},
    "search_empty":           {"es": "Sin resultados.", "en": "No results.",  "pt": "Sem resultados."},
    "search_footer":          {"es": "↑↓ para moverse · Enter para abrir · Esc para cerrar",
                               "en": "↑↓ to move · Enter to open · Esc to close",
                               "pt": "↑↓ para mover · Enter para abrir · Esc para fechar"},
    "theme_to_light":         {"es": "Pasar al tema claro", "en": "Switch to light theme",
                               "pt": "Mudar para o tema claro"},
    "theme_to_dark":          {"es": "Pasar al tema oscuro", "en": "Switch to dark theme",
                               "pt": "Mudar para o tema escuro"},
    # El id del proyecto y la versión del programa, abajo a la derecha. El separador va
    # en la tabla y no en el widget para que un proyecto pueda darlo vuelta.
    "footer_version":         {"es": "{project_id}  ·  v{version}",
                               "en": "{project_id}  ·  v{version}",
                               "pt": "{project_id}  ·  v{version}"},
    "footer_version_only":    {"es": "v{version}", "en": "v{version}", "pt": "v{version}"},

    # ── Panel y grilla de cámaras ────────────────────────────────────────────
    "camera_no_signal":       {"es": "SIN SEÑAL",      "en": "NO SIGNAL",     "pt": "SEM SINAL"},
    "camera_overlay_off":     {"es": "ANOTADO DESACTIVADO",
                               "en": "ANNOTATION OFF",
                               "pt": "ANOTACAO DESATIVADA"},
    "camera_pipeline_off":    {"es": "INFERENCIA DESACTIVADA",
                               "en": "INFERENCE OFF",
                               "pt": "INFERENCIA DESATIVADA"},
    "camera_waiting_inference": {"es": "ESPERANDO INFERENCIA",
                               "en": "WAITING FOR INFERENCE",
                               "pt": "AGUARDANDO INFERÊNCIA"},
    "camera_starting":        {"es": "Iniciando",      "en": "Starting",      "pt": "Iniciando"},
    "camera_connected":       {"es": "Conectada",      "en": "Connected",     "pt": "Conectada"},
    "camera_disconnected":    {"es": "Desconectada",   "en": "Disconnected",  "pt": "Desconectada"},
    "camera_disabled":        {"es": "Deshabilitada",  "en": "Disabled",      "pt": "Desabilitada"},
    "camera_misconfigured":   {"es": "Mal configurada",
                               "en": "Misconfigured",  "pt": "Mal configurada"},
    "camera_no_cameras":      {"es": "No hay cámaras configuradas.",
                               "en": "No cameras configured.",
                               "pt": "Nenhuma câmera configurada."},
    "camera_mode_raw":        {"es": "Video crudo",    "en": "Raw video",     "pt": "Vídeo cru"},
    "camera_mode_annotated":  {"es": "Video anotado",  "en": "Annotated video",
                               "pt": "Vídeo anotado"},
    "camera_show_all":        {"es": "Ver todas las cámaras",
                               "en": "Show all cameras",
                               "pt": "Ver todas as câmeras"},
    "camera_focus_tooltip":   {"es": "Ver todas las cámaras o una sola en grande",
                               "en": "Show all cameras, or just one enlarged",
                               "pt": "Ver todas as câmeras ou apenas uma ampliada"},

    # ── Página de inicio ─────────────────────────────────────────────────────
    # El estado general, en el rótulo de la placa.
    "home_state_ok":          {"es": "Equipo en marcha",
                               "en": "System running",
                               "pt": "Equipamento em operação"},
    "home_state_warning":     {"es": "Equipo con avisos",
                               "en": "System with warnings",
                               "pt": "Equipamento com avisos"},
    "home_state_error":       {"es": "Sin licencia válida",
                               "en": "No valid licence",
                               "pt": "Sem licença válida"},
    "home_tagline":           {"es": "Visión artificial en línea: captura de cámaras, "
                                     "inferencia y publicación de resultados al PLC.",
                               "en": "In-line machine vision: camera capture, inference and "
                                     "results published to the PLC.",
                               "pt": "Visão artificial em linha: captura de câmeras, "
                                     "inferência e publicação de resultados ao CLP."},
    "home_spec_device":       {"es": "Equipo",         "en": "Device",        "pt": "Equipamento"},
    "home_spec_version":      {"es": "Versión",        "en": "Version",       "pt": "Versão"},
    "home_spec_uptime":       {"es": "En servicio",    "en": "Uptime",        "pt": "Em serviço"},
    "home_uptime_minutes":    {"es": "{minutes} min",  "en": "{minutes} min", "pt": "{minutes} min"},
    "home_uptime_hours":      {"es": "{hours} h {minutes:02d} min",
                               "en": "{hours} h {minutes:02d} min",
                               "pt": "{hours} h {minutes:02d} min"},
    "home_uptime_days":       {"es": "{days} d {hours:02d} h",
                               "en": "{days} d {hours:02d} h",
                               "pt": "{days} d {hours:02d} h"},
    "home_ring_total":        {"es": "de {total}",     "en": "of {total}",    "pt": "de {total}"},
    "home_ring_caption":      {"es": "Cámaras en línea",
                               "en": "Cameras online",
                               "pt": "Câmeras on-line"},
    "home_ring_detail":       {"es": "{count} pipelines de inferencia",
                               "en": "{count} inference pipelines",
                               "pt": "{count} pipelines de inferência"},
    "home_cameras":           {"es": "Cámaras",        "en": "Cameras",       "pt": "Câmeras"},
    "home_open_monitor":      {"es": "Ver monitor",    "en": "Open monitor",  "pt": "Ver monitor"},
    "home_camera_address":    {"es": "Dirección",      "en": "Address",       "pt": "Endereço"},
    "home_camera_fps":        {"es": "FPS",            "en": "FPS",           "pt": "FPS"},
    "home_camera":            {"es": "Cámara",         "en": "Camera",        "pt": "Câmera"},
    "home_camera_state":      {"es": "Estado",         "en": "Status",        "pt": "Estado"},
    "home_inference":         {"es": "Inferencia",     "en": "Inference",     "pt": "Inferência"},
    "home_inference_pipeline": {"es": "Pipeline",      "en": "Pipeline",      "pt": "Pipeline"},
    "home_inference_cycle":   {"es": "Ciclo",          "en": "Cycle",         "pt": "Ciclo"},
    "home_inference_time":    {"es": "Inferencia",     "en": "Inference",     "pt": "Inferência"},
    "home_inference_age":     {"es": "Último resultado",
                               "en": "Last result",
                               "pt": "Último resultado"},
    "home_no_result":         {"es": "Sin resultados", "en": "No results",    "pt": "Sem resultados"},
    "home_no_pipelines":      {"es": "No hay pipelines habilitados.",
                               "en": "No pipelines enabled.",
                               "pt": "Nenhum pipeline habilitado."},
    "home_age_now":           {"es": "ahora",          "en": "now",           "pt": "agora"},
    "home_age_seconds":       {"es": "hace {seconds} s",
                               "en": "{seconds} s ago",
                               "pt": "há {seconds} s"},
    "home_age_minutes":       {"es": "hace {minutes} min",
                               "en": "{minutes} min ago",
                               "pt": "há {minutes} min"},
    "home_age_hours":         {"es": "hace {hours} h", "en": "{hours} h ago", "pt": "há {hours} h"},
    "home_heartbeat":         {"es": "Latido al PLC",  "en": "PLC heartbeat", "pt": "Batimento ao CLP"},
    "home_services":          {"es": "Servicios",      "en": "Services",      "pt": "Serviços"},
    "home_license":           {"es": "Licencia",       "en": "Licence",       "pt": "Licença"},
    "home_results":           {"es": "Resultados",     "en": "Results",       "pt": "Resultados"},
    "home_results_verdicts":  {"es": "Veredictos",     "en": "Verdicts",      "pt": "Veredictos"},
    "home_results_waiting":   {"es": "Esperando resultados",
                               "en": "Waiting for results",
                               "pt": "Aguardando resultados"},
    # Un veredicto es una métrica `<nombre>_ok` del analyzer; su rótulo es `verdict_<nombre>_ok`
    # y, si no está en la tabla, el nombre de la métrica tal cual.
    "verdict_ok":             {"es": "OK",             "en": "OK",            "pt": "OK"},
    "verdict_nok":            {"es": "NOK",            "en": "NOK",           "pt": "NOK"},
    "verdict_no_data":        {"es": "Sin dato",       "en": "No data",       "pt": "Sem dado"},
    "verdict_safety_ok":      {"es": "Personas",       "en": "People",        "pt": "Pessoas"},
    "verdict_flow_ok":        {"es": "Grano",          "en": "Grain",         "pt": "Grão"},
    "verdict_raised_ok":      {"es": "Calza elevada",  "en": "Stopper raised",
                               "pt": "Calço elevado"},
    "verdict_contact_ok":     {"es": "Contacto",       "en": "Contact",       "pt": "Contato"},

    # ── Configuración ────────────────────────────────────────────────────────
    "config_save":            {"es": "Guardar y aplicar",
                               "en": "Save and apply",
                               "pt": "Salvar e aplicar"},
    "config_discard":         {"es": "Descartar",      "en": "Discard",       "pt": "Descartar"},
    # La barra que aparece al editar algo; {tabs} son los títulos de las pestañas con cambios.
    "save_bar_message":       {"es": "Cambios sin guardar en {tabs}",
                               "en": "Unsaved changes in {tabs}",
                               "pt": "Alterações não salvas em {tabs}"},
    "config_saved_title":     {"es": "Configuración guardada",
                               "en": "Settings saved",
                               "pt": "Configuração salva"},
    "config_saved_body":      {"es": "Los cambios se guardaron en el config.yaml.\n\n"
                                     "Reiniciá la aplicación para que tengan efecto.",
                               "en": "Changes were written to config.yaml.\n\n"
                                     "Restart the application for them to take effect.",
                               "pt": "As alterações foram salvas no config.yaml.\n\n"
                                     "Reinicie a aplicação para que tenham efeito."},
    "config_save_error":      {"es": "No se pudo guardar la configuración.",
                               "en": "Settings could not be saved.",
                               "pt": "Não foi possível salvar a configuração."},
    "config_restart_note":    {"es": "Los cambios toman efecto al reiniciar la aplicación.",
                               "en": "Changes take effect when the application restarts.",
                               "pt": "As alterações têm efeito ao reiniciar a aplicação."},

    "tab_cameras":            {"es": "Cámaras",        "en": "Cameras",       "pt": "Câmeras"},
    "tab_video":              {"es": "Video",          "en": "Video",         "pt": "Vídeo"},
    "tab_inference":          {"es": "Inferencia",     "en": "Inference",     "pt": "Inferência"},
    "tab_process":            {"es": "Proceso",        "en": "Process",       "pt": "Processo"},
    "tab_collector":          {"es": "Dataset",        "en": "Dataset",       "pt": "Dataset"},
    "tab_telemetry":          {"es": "Telemetría",     "en": "Telemetry",     "pt": "Telemetria"},
    # La página ya se llama Modbus: la pestaña que lo configura dice qué es.
    "tab_modbus_config":      {"es": "Configuración",  "en": "Configuration", "pt": "Configuração"},
    "tab_system":             {"es": "Sistema",        "en": "System",        "pt": "Sistema"},
    "tab_hardware":           {"es": "Hardware",       "en": "Hardware",      "pt": "Hardware"},
    "tab_logs":               {"es": "Logs",           "en": "Logs",          "pt": "Logs"},
    # Las pestañas en vivo de Modbus y de Video y telemetría, al lado de las que configuran.
    "tab_modbus_registers":   {"es": "Registros",      "en": "Registers",     "pt": "Registradores"},
    "tab_video_streams":      {"es": "Streams",        "en": "Streams",       "pt": "Streams"},
    "tab_modbus_stats":       {"es": "Estadísticas",   "en": "Statistics",    "pt": "Estatísticas"},
    "tab_license":            {"es": "Licencia",       "en": "Licence",       "pt": "Licença"},

    # ── Campos comunes ───────────────────────────────────────────────────────
    "field_host":             {"es": "Host",           "en": "Host",          "pt": "Host"},
    "field_port":             {"es": "Puerto",         "en": "Port",          "pt": "Porta"},
    "field_fps":              {"es": "FPS",            "en": "FPS",           "pt": "FPS"},
    "field_name":             {"es": "Nombre",         "en": "Name",          "pt": "Nome"},
    "field_url":              {"es": "URL",            "en": "URL",           "pt": "URL"},
    "field_user":             {"es": "Usuario",        "en": "User",          "pt": "Usuário"},

    # ── Interfaces de red: por dónde escucha un servidor (interface_picker) ──
    "net_listen_on":          {"es": "Escuchar en",    "en": "Listen on",     "pt": "Escutar em"},
    "net_listen_hint":        {"es": "Por dónde lo alcanzan los clientes",
                               "en": "Where clients reach it",
                               "pt": "Por onde os clientes o alcançam"},
    "net_all_interfaces":     {"es": "Todas las interfaces", "en": "All interfaces",
                               "pt": "Todas as interfaces"},
    "net_loopback":           {"es": "Sólo este equipo", "en": "This device only",
                               "pt": "Só este equipamento"},
    "net_no_link":            {"es": "sin enlace",     "en": "no link",       "pt": "sem enlace"},
    "net_interfaces_hint":    {"es": "Todas marcadas incluye también las que se agreguen",
                               "en": "All checked also includes any added later",
                               "pt": "Todas marcadas inclui também as que forem adicionadas"},
    "net_none_found":         {"es": "El equipo no informó interfaces de red: se conserva lo que "
                                     "diga el config.",
                               "en": "The device reported no network interfaces: the config "
                                     "value is kept.",
                               "pt": "O equipamento não informou interfaces de rede: mantém-se o "
                                     "que diz o config."},
    "net_unknown_interfaces": {"es": "También en el config: {names}, que este equipo no tiene. Se "
                                     "conserva al guardar.",
                               "en": "Also in the config: {names}, which this device does not "
                                     "have. Kept on save.",
                               "pt": "Também no config: {names}, que este equipamento não tem. "
                                     "Mantém-se ao salvar."},

    # ── Cámaras (configuración) ──────────────────────────────────────────────
    "cam_box_connection":     {"es": "Conexión",       "en": "Connection",    "pt": "Conexão"},
    "cam_connection_description": {"es": "Qué cámara es y dónde se la encuentra. Se aplica al "
                                         "reiniciar.",
                                   "en": "Which camera it is and where to find it. Applied on "
                                         "restart.",
                                   "pt": "Qual câmera é e onde encontrá-la. Aplicado ao "
                                         "reiniciar."},
    "cam_box_acquisition":    {"es": "Adquisición",    "en": "Acquisition",   "pt": "Aquisição"},
    "cam_acquisition_description": {"es": "Cómo se toman las imágenes de esta cámara. Se aplica al reiniciar.",
                                    "en": "How this camera takes its images. Applied on restart.",
                                    "pt": "Como esta câmera tira as imagens. Aplicado ao reiniciar."},
    "cam_box_calibration":    {"es": "Corrección de lente",
                               "en": "Lens correction",
                               "pt": "Correção de lente"},
    "cam_box_image_adjust":   {"es": "Ajuste de imagen",
                               "en": "Image adjustment",
                               "pt": "Ajuste de imagem"},
    "cam_enabled":            {"es": "Cámara habilitada",
                               "en": "Camera enabled",
                               "pt": "Câmera habilitada"},
    "cam_brand":              {"es": "Fabricante",     "en": "Brand",         "pt": "Fabricante"},
    "cam_model":              {"es": "Modelo",         "en": "Model",         "pt": "Modelo"},
    "cam_address":            {"es": "Dirección (IP, URL o archivo)",
                               "en": "Address (IP, URL or file)",
                               "pt": "Endereço (IP, URL ou arquivo)"},
    "cam_rotation":           {"es": "Rotación",       "en": "Rotation",      "pt": "Rotação"},
    "cam_fps_limit":          {"es": "Límite de FPS",  "en": "FPS limit",     "pt": "Limite de FPS"},
    "cam_exposure":           {"es": "Exposición", "en": "Exposure", "pt": "Exposição"},
    "cam_gain":               {"es": "Ganancia",       "en": "Gain",          "pt": "Ganho"},
    "cam_illumination_min":   {"es": "Iluminación mínima",
                               "en": "Minimum illumination",
                               "pt": "Iluminação mínima"},
    "cam_roi_x":              {"es": "X (px):",        "en": "X (px):",       "pt": "X (px):"},
    "cam_roi_y":              {"es": "Y (px):",        "en": "Y (px):",       "pt": "Y (px):"},
    "cam_roi_width":          {"es": "Ancho (px):",    "en": "Width (px):",   "pt": "Largura (px):"},
    "cam_roi_height":         {"es": "Alto (px):",     "en": "Height (px):",  "pt": "Altura (px):"},
    "cam_calibration_note":   {"es": "Los intrínsecos del lente se editan en el config.yaml.",
                               "en": "Lens intrinsics are edited in config.yaml.",
                               "pt": "Os intrínsecos da lente são editados no config.yaml."},
    "cam_brightness_factor":  {"es": "Brillo",   "en": "Brightness", "pt": "Brilho"},
    "cam_clahe":              {"es": "Contraste local (CLAHE)",
                               "en": "Local contrast (CLAHE)",
                               "pt": "Contraste local (CLAHE)"},
    "cam_image_adjust_note":  {"es": "Cambia la imagen que mide el modelo y guarda el dataset. "
                                     "En una instalación nueva, x1.0.",
                               "en": "Changes the image the model measures and the dataset "
                                     "stores. On a new installation, x1.0.",
                               "pt": "Muda a imagem que o modelo mede e o dataset guarda. Em "
                                     "uma instalação nova, x1.0."},
    "cam_illumination_hint":  {"es": "En % de brillo",
                               "en": "In % of brightness",
                               "pt": "Em % de brilho"},
    "cam_brightness_hint":    {"es": "Multiplicador; x1.0 = sin cambio",
                               "en": "Multiplier; x1.0 = no change",
                               "pt": "Multiplicador; x1.0 = sem alteração"},
    "cam_clahe_hint":         {"es": "0 = apagado", "en": "0 = off", "pt": "0 = desligado"},
    "cam_calibration_set":    {"es": "Corrección de lente configurada",
                               "en": "Lens correction configured",
                               "pt": "Correção de lente configurada"},
    "cam_calibration_unset":  {"es": "Sin corrección de lente",
                               "en": "No lens correction",
                               "pt": "Sem correção de lente"},

    # ── Unidades: la aclaración debajo de un campo ──────────────────────────
    "unit_seconds":           {"es": "En segundos", "en": "In seconds", "pt": "Em segundos"},
    "unit_hours":             {"es": "En horas", "en": "In hours", "pt": "Em horas"},
    "unit_pixels":            {"es": "En píxeles", "en": "In pixels", "pt": "Em pixels"},
    "unit_microseconds":      {"es": "En microsegundos",
                               "en": "In microseconds",
                               "pt": "Em microssegundos"},
    "unit_fps":               {"es": "En frames por segundo",
                               "en": "In frames per second",
                               "pt": "Em quadros por segundo"},

    # ── Óptica / lente sucio (configuración) ─────────────────────────────────
    "lens_card_eyebrow":      {"es": "Óptica",   "en": "Optics", "pt": "Óptica"},
    "lens_general_eyebrow":   {"es": "Todas las cámaras", "en": "All cameras",
                               "pt": "Todas as câmeras"},
    "cam_optics_tab":         {"es": "Óptica",         "en": "Optics",        "pt": "Óptica"},
    "lens_box_detection":     {"es": "Detección de lente sucio",
                               "en": "Dirty lens detection",
                               "pt": "Detecção de lente suja"},
    "lens_enabled":           {"es": "Detección habilitada",
                               "en": "Detection enabled",
                               "pt": "Detecção habilitada"},
    "lens_interval":          {"es": "Intervalo de medición",
                               "en": "Measurement interval",
                               "pt": "Intervalo de medição"},
    "lens_window":            {"es": "Ventana de análisis",
                               "en": "Analysis window",
                               "pt": "Janela de análise"},
    "lens_threshold":         {"es": "Nitidez mínima",
                               "en": "Minimum sharpness",
                               "pt": "Nitidez mínima"},
    "lens_analysis_width":    {"es": "Ancho de análisis",
                               "en": "Analysis width",
                               "pt": "Largura de análise"},
    "lens_calibration_samples": {"es": "Muestras de calibración",
                                 "en": "Calibration samples",
                                 "pt": "Amostras de calibração"},
    "lens_method_note":       {"es": "Igual para todas las cámaras. La alarma se afirma cuando pasó la ventana entera.",
                               "en": "The same for every camera. The alarm is raised once the full window has elapsed.",
                               "pt": "Igual para todas as câmeras. O alarme é afirmado quando a janela inteira passou."},
    "lens_width_note":        {"es": "En píxeles; cambiarlo obliga a calibrar de nuevo",
                               "en": "In pixels; changing it requires recalibrating",
                               "pt": "Em pixels; alterá-lo obriga a calibrar de novo"},
    "lens_box_reference":     {"es": "Referencia de vidrio limpio",
                               "en": "Clean-glass reference",
                               "pt": "Referência de vidro limpo"},
    "lens_reference_variance": {"es": "Varianza de referencia",
                                "en": "Reference variance",
                                "pt": "Variância de referência"},
    "lens_threshold_hint":    {"es": "En % de la referencia",
                               "en": "In % of the reference",
                               "pt": "Em % da referência"},
    "lens_samples_hint":      {"es": "Una por intervalo de medición",
                               "en": "One per measurement interval",
                               "pt": "Uma por intervalo de medição"},
    "lens_variance_hint":     {"es": "Del Laplaciano, con el vidrio limpio",
                               "en": "Of the Laplacian, with clean glass",
                               "pt": "Do Laplaciano, com o vidro limpo"},
    "lens_reference_state":   {"es": "Estado",         "en": "State",         "pt": "Estado"},
    "lens_uncalibrated":      {"es": "Sin calibrar — la detección no informa nada",
                               "en": "Not calibrated — detection reports nothing",
                               "pt": "Sem calibrar — a detecção não informa nada"},
    "lens_calibrated_on":     {"es": "Calibrada el {date} con {samples} muestras "
                                     "(dispersión {dispersion} %)",
                               "en": "Calibrated on {date} with {samples} samples "
                                     "(dispersion {dispersion}%)",
                               "pt": "Calibrada em {date} com {samples} amostras "
                                     "(dispersão {dispersion} %)"},
    "lens_calibrating":       {"es": "Calibrando: {samples} muestras, una cada {interval} s "
                                     "(unos {minutes} min)",
                               "en": "Calibrating: {samples} samples, one every {interval} s "
                                     "(about {minutes} min)",
                               "pt": "Calibrando: {samples} amostras, uma a cada {interval} s "
                                     "(uns {minutes} min)"},
    "lens_calibrate":         {"es": "Calibrar",       "en": "Calibrate",     "pt": "Calibrar"},
    "lens_calibrate_note":    {"es": "Limpiar el vidrio antes de calibrar. Se puede copiar a "
                                     "mano la de un equipo gemelo.",
                               "en": "Clean the glass before calibrating. A twin unit's can "
                                     "be copied by hand.",
                               "pt": "Limpar o vidro antes de calibrar. Pode-se copiar à mão "
                                     "a de um equipamento gêmeo."},
    "lens_box_region":        {"es": "Región de nitidez", "en": "Sharpness region",
                               "pt": "Região de nitidez"},
    "lens_region_note":       {"es": "Una zona con textura fija y nítida. Sin región se mide "
                                     "el frame entero.",
                               "en": "An area with fixed, sharp texture. Without a region the "
                                     "whole frame is measured.",
                               "pt": "Uma zona com textura fixa e nítida. Sem região mede-se o "
                                     "frame inteiro."},
    "lens_region_enabled":    {"es": "Medir sólo en la región",
                               "en": "Measure only within the region",
                               "pt": "Medir só na região"},
    "lens_draw_region":       {"es": "Dibujar",        "en": "Draw",          "pt": "Desenhar"},
    "lens_region_x":          {"es": "X",        "en": "X",      "pt": "X"},
    "lens_region_y":          {"es": "Y",        "en": "Y",      "pt": "Y"},
    "lens_region_width":      {"es": "Ancho",    "en": "Width",  "pt": "Largura"},
    "lens_region_height":     {"es": "Alto",     "en": "Height", "pt": "Altura"},
    "lens_roi_title":         {"es": "Región de la óptica", "en": "Lens region",
                               "pt": "Região da óptica"},
    "lens_calibrate_started": {"es": "Calibrando la óptica",
                               "en": "Calibrating optics",
                               "pt": "Calibrando a óptica"},
    "lens_calibrate_body":    {"es": "Juntando {samples} muestras de «{camera}», una cada "
                                     "{interval} s. La referencia se guarda sola al "
                                     "terminar.",
                               "en": "Collecting {samples} samples from “{camera}”, one every "
                                     "{interval} s. The reference is saved automatically when "
                                     "done.",
                               "pt": "Juntando {samples} amostras de «{camera}», uma a cada "
                                     "{interval} s. A referência é salva sozinha ao terminar."},
    "lens_conditions_changed": {"es": "Cambiaron las condiciones desde la calibración: {reason}",
                                "en": "Conditions changed since calibration: {reason}",
                                "pt": "As condições mudaram desde a calibração: {reason}"},
    "rotation_none":          {"es": "Sin rotación",   "en": "No rotation",   "pt": "Sem rotação"},
    "rotation_90cw":          {"es": "90° horario",    "en": "90° clockwise", "pt": "90° horário"},
    "rotation_90ccw":         {"es": "90° antihorario",
                               "en": "90° counter-clockwise",
                               "pt": "90° anti-horário"},
    "rotation_180":           {"es": "180°",           "en": "180°",          "pt": "180°"},

    # ── Video (configuración) ────────────────────────────────────────────────
    "video_card_servers":     {"es": "Servidores",     "en": "Servers",       "pt": "Servidores"},
    "video_box_http":         {"es": "HTTP (MJPEG)",   "en": "HTTP (MJPEG)",  "pt": "HTTP (MJPEG)"},
    "video_http_description": {"es": "El stream liviano, para mirar en el navegador. Se aplica "
                                     "al reiniciar.",
                               "en": "The lightweight stream, to watch in a browser. Applied on "
                                     "restart.",
                               "pt": "O stream leve, para ver no navegador. Aplicado ao "
                                     "reiniciar."},
    "video_http_enabled":     {"es": "Servidor HTTP habilitado",
                               "en": "HTTP server enabled",
                               "pt": "Servidor HTTP habilitado"},
    "video_box_rtsp":         {"es": "RTSP",           "en": "RTSP",          "pt": "RTSP"},
    "video_rtsp_description": {"es": "Resolución completa, para un NVR o un reproductor. Necesita "
                                     "GStreamer. Se aplica al reiniciar.",
                               "en": "Full resolution, for an NVR or a player. Needs GStreamer. "
                                     "Applied on restart.",
                               "pt": "Resolução completa, para um NVR ou um reprodutor. Precisa "
                                     "do GStreamer. Aplicado ao reiniciar."},
    "video_rtsp_enabled":     {"es": "Servidor RTSP habilitado",
                               "en": "RTSP server enabled",
                               "pt": "Servidor RTSP habilitado"},
    "video_jpeg_quality":     {"es": "Calidad JPEG",   "en": "JPEG quality",  "pt": "Qualidade JPEG"},
    "video_jpeg_quality_hint": {"es": "De 1 a 100", "en": "From 1 to 100", "pt": "De 1 a 100"},
    "video_frame_width":      {"es": "Ancho del frame", "en": "Frame width",  "pt": "Largura do frame"},
    "video_frame_width_hint": {"es": "En píxeles; 0 = el de la cámara",
                               "en": "In pixels; 0 = the camera's",
                               "pt": "Em pixels; 0 = o da câmera"},
    "video_codec":            {"es": "Codec",          "en": "Codec",         "pt": "Codec"},
    "video_codec_hint":       {"es": "Los de hardware, sólo en la Jetson",
                               "en": "Hardware ones only on the Jetson",
                               "pt": "Os de hardware, só na Jetson"},
    "video_bitrate":          {"es": "Bitrate",        "en": "Bitrate",       "pt": "Bitrate"},
    "video_bitrate_hint":     {"es": "En kbit/s; 0 = el del encoder",
                               "en": "In kbit/s; 0 = encoder default",
                               "pt": "Em kbit/s; 0 = o do encoder"},
    "video_net_interfaces":   {"es": "Interfaces de red", "en": "Network interfaces",
                               "pt": "Interfaces de rede"},
    "video_log_connections":  {"es": "Loguear conexiones de clientes",
                               "en": "Log client connections",
                               "pt": "Registrar conexões de clientes"},
    "video_card_display":     {"es": "Visualización",  "en": "Display",       "pt": "Visualização"},
    "video_box_display":      {"es": "Ajuste de visualización",
                               "en": "Display adjustment",
                               "pt": "Ajuste de visualização"},
    "video_display_description": {"es": "Sólo la interfaz y los streams: el modelo y el dataset "
                                        "siguen viendo el frame de la cámara. Se aplica al guardar.",
                                  "en": "Only the interface and the streams: the model and the "
                                        "dataset still see the camera frame. Applied on save.",
                                  "pt": "Só a interface e os streams: o modelo e o dataset "
                                        "continuam vendo o frame da câmera. Aplicado ao salvar."},
    "video_gamma":            {"es": "Gamma",          "en": "Gamma",         "pt": "Gama"},
    "video_gamma_hint":       {"es": "Menos de 1 aclara las sombras",
                               "en": "Below 1 brightens the shadows",
                               "pt": "Abaixo de 1 clareia as sombras"},
    "video_clahe_clip":       {"es": "Contraste local (CLAHE)",
                               "en": "Local contrast (CLAHE)",
                               "pt": "Contraste local (CLAHE)"},
    "video_clahe_hint":       {"es": "0 = apagado; cuesta más que el gamma",
                               "en": "0 = off; costs more than gamma",
                               "pt": "0 = desligado; custa mais que a gama"},

    # ── Inferencia (configuración) ───────────────────────────────────────────
    "inf_box_models":         {"es": "Modelos",        "en": "Models",        "pt": "Modelos"},
    "inf_box_pipelines":      {"es": "Pipelines",      "en": "Pipelines",     "pt": "Pipelines"},
    "inf_model_section":      {"es": "Pesos y umbral", "en": "Weights and threshold",
                               "pt": "Pesos e limiar"},
    "inf_model_description":  {"es": "De dónde se carga el modelo y qué detecciones descarta. "
                                     "Se aplica al reiniciar.",
                               "en": "Where the model is loaded from and which detections it "
                                     "drops. Applied on restart.",
                               "pt": "De onde o modelo é carregado e quais detecções descarta. "
                                     "Aplicado ao reiniciar."},
    "inf_model_type":         {"es": "Tipo",           "en": "Type",          "pt": "Tipo"},
    "inf_model_path":         {"es": "Pesos",          "en": "Weights",       "pt": "Pesos"},
    "inf_min_confidence":     {"es": "Confianza mínima", "en": "Minimum confidence",
                               "pt": "Confiança mínima"},
    "inf_min_confidence_hint": {"es": "En %; por debajo, la detección se descarta",
                                "en": "In %; below it the detection is dropped",
                                "pt": "Em %; abaixo, a detecção é descartada"},
    "inf_model_browse":       {"es": "Examinar…",      "en": "Browse…",       "pt": "Procurar…"},
    "inf_model_browse_title": {"es": "Elegir los pesos del modelo", "en": "Choose the model weights",
                               "pt": "Escolher os pesos do modelo"},
    "inf_model_browse_filter": {"es": "Pesos del modelo (*.pt *.engine *.onnx *.enc);;"
                                      "Todos los archivos (*)",
                                "en": "Model weights (*.pt *.engine *.onnx *.enc);;"
                                      "All files (*)",
                                "pt": "Pesos do modelo (*.pt *.engine *.onnx *.enc);;"
                                      "Todos os arquivos (*)"},
    "inf_pipeline_section":   {"es": "Pipeline",       "en": "Pipeline",      "pt": "Pipeline"},
    "inf_pipeline_description": {"es": "Qué clase corre y qué cámaras mira. Se aplica al "
                                       "reiniciar.",
                                 "en": "Which class runs and which cameras it looks at. "
                                       "Applied on restart.",
                                 "pt": "Qual classe roda e quais câmeras olha. Aplicado ao "
                                       "reiniciar."},
    "inf_pipeline_enabled":   {"es": "Pipeline habilitado", "en": "Pipeline enabled",
                               "pt": "Pipeline habilitado"},
    "inf_pipeline_slot_hint": {"es": "En el config: {slot}", "en": "In the config: {slot}",
                               "pt": "No config: {slot}"},
    "inf_pipeline_type":      {"es": "Tipo",           "en": "Type",          "pt": "Tipo"},
    "inf_pipeline_cameras":   {"es": "Cámaras",        "en": "Cameras",       "pt": "Câmeras"},
    "inf_pipeline_cameras_hint": {"es": "Al menos una. Para no usar el pipeline, deshabilitalo.",
                                  "en": "At least one. To stop the pipeline, disable it.",
                                  "pt": "Pelo menos uma. Para não usar o pipeline, desabilite-o."},
    "inf_result_section":     {"es": "Resultado",      "en": "Result",        "pt": "Resultado"},
    "inf_result_description": {"es": "Cuándo un resultado cuenta como medición. Si no llega, sale "
                                     "igual, marcado no confiable.",
                               "en": "When a result counts as a measurement. If it falls short, "
                                     "it still goes out, marked unreliable.",
                               "pt": "Quando um resultado conta como medição. Se não chegar, sai "
                                     "igual, marcado como não confiável."},
    "inf_pipeline_interval":  {"es": "Intervalo mínimo", "en": "Minimum interval",
                               "pt": "Intervalo mínimo"},
    "inf_pipeline_interval_hint": {"es": "En ms, entre dos inferencias de una cámara",
                                   "en": "In ms, between two inferences of a camera",
                                   "pt": "Em ms, entre duas inferências de uma câmera"},
    "inf_every_frame":        {"es": "Cada frame",     "en": "Every frame",   "pt": "Cada frame"},
    "inf_min_detections":     {"es": "Detecciones mínimas", "en": "Minimum detections",
                               "pt": "Detecções mínimas"},
    "inf_min_result_conf":    {"es": "Confianza mínima", "en": "Minimum confidence",
                               "pt": "Confiança mínima"},
    "inf_min_result_conf_hint": {"es": "En %, promedio del resultado",
                                 "en": "In %, result average",
                                 "pt": "Em %, média do resultado"},
    "inf_cycle_section":      {"es": "Ciclo de medición", "en": "Measurement cycle",
                               "pt": "Ciclo de medição"},
    "inf_cycle_description":  {"es": "Con 1 imagen se infiere de continuo, hasta el intervalo "
                                     "mínimo. Con más, se mide por rondas.",
                               "en": "With 1 frame it infers continuously, up to the minimum "
                                     "interval. With more, it measures in rounds.",
                               "pt": "Com 1 imagem infere de forma contínua, até o intervalo "
                                     "mínimo. Com mais, mede por rodadas."},
    "inf_frames_per_cycle":   {"es": "Imágenes por medición", "en": "Frames per measurement",
                               "pt": "Imagens por medição"},
    "inf_cycle_interval":     {"es": "Espera entre mediciones", "en": "Wait between measurements",
                               "pt": "Espera entre medições"},
    "inf_cycle_interval_hint": {"es": "En segundos, entre una ronda y la siguiente",
                                "en": "In seconds, between one round and the next",
                                "pt": "Em segundos, entre uma rodada e a seguinte"},
    "inf_no_wait":            {"es": "Sin espera",     "en": "No wait",       "pt": "Sem espera"},
    "inf_capture_timeout":    {"es": "Tiempo máximo por imagen", "en": "Timeout per frame",
                               "pt": "Tempo máximo por imagem"},
    "inf_capture_timeout_hint": {"es": "En segundos; pasado, la imagen se da por perdida",
                                 "en": "In seconds; after it the frame counts as lost",
                                 "pt": "Em segundos; depois, a imagem é dada como perdida"},
    "inf_no_limit":           {"es": "Sin límite",     "en": "No limit",      "pt": "Sem limite"},
    "inf_idle_cameras_label": {"es": "Entre mediciones", "en": "Between measurements",
                               "pt": "Entre medições"},
    "inf_idle_cameras":       {"es": "Apagar la captura", "en": "Stop capture",
                               "pt": "Desligar a captura"},
    "inf_idle_cameras_hint":  {"es": "Libera la red; el video en vivo queda sin imagen.",
                               "en": "Frees the network; the live view goes blank.",
                               "pt": "Libera a rede; o vídeo ao vivo fica sem imagem."},
    "inf_regions_section":    {"es": "Región de cada pipeline", "en": "Each pipeline's region",
                               "pt": "Região de cada pipeline"},
    "inf_regions_description": {"es": "Se dibujan todas juntas sobre el frame en vivo. Se aplica "
                                      "al reiniciar.",
                                "en": "They are drawn together on the live frame. Applied on "
                                      "restart.",
                                "pt": "São desenhadas juntas sobre o frame ao vivo. Aplicado ao "
                                      "reiniciar."},
    "inf_roi_box":            {"es": "ROI en",         "en": "ROI on",        "pt": "ROI em"},
    "inf_roi_mode":           {"es": "Región:",        "en": "Region:",       "pt": "Região:"},
    "inf_roi_mode_own":       {"es": "Propia de este pipeline",
                               "en": "This pipeline's own",
                               "pt": "Própria deste pipeline"},
    "inf_roi_mode_full":      {"es": "Frame completo", "en": "Whole frame",   "pt": "Frame completo"},
    "inf_box_regions":        {"es": "Regiones (ROI)", "en": "Regions (ROI)",
                               "pt": "Regiões (ROI)"},
    "inf_pipeline_name":      {"es": "Nombre",         "en": "Name",          "pt": "Nome"},
    "inf_roi_note":           {"es": "En una cámara que decodifica video la región se corta del "
                                     "video nativo, no del frame reducido, y el VIC la entrega "
                                     "a la entrada del modelo. Se aplica al reiniciar.",
                               "en": "On a camera that decodes video the region is cut from the "
                                     "native video, not the reduced frame, and the VIC hands it "
                                     "over at the model's input size. It applies on restart.",
                               "pt": "Em uma câmera que decodifica vídeo a região é cortada do "
                                     "vídeo nativo, não do frame reduzido, e o VIC a entrega no "
                                     "tamanho de entrada do modelo. Vale ao reiniciar."},
    "inf_region_view_auto":   {"es": "salida según el modelo", "en": "output per the model",
                               "pt": "saída conforme o modelo"},
    "regions_problem_native_preprocessed": {
        "es": "Esta cámara tiene corrección de lente o ajuste de imagen: la región se corta del "
              "frame de referencia y no del video nativo.",
        "en": "This camera has lens correction or image adjustment: the region is cut from the "
              "reference frame, not from the native video.",
        "pt": "Esta câmera tem correção de lente ou ajuste de imagem: a região é cortada do "
              "frame de referência e não do vídeo nativo."},
    "regions_problem_too_small": {
        "es": "La región mide menos de 16 px por lado: el decodificador no puede cortarla del "
              "video nativo y el pipeline no infiere.",
        "en": "The region is under 16 px per side: the decoder cannot cut it from the native "
              "video and the pipeline does not infer.",
        "pt": "A região mede menos de 16 px por lado: o decodificador não pode cortá-la do "
              "vídeo nativo e o pipeline não infere."},
    "inf_regions_edit":       {"es": "Editar regiones…", "en": "Edit regions…",
                               "pt": "Editar regiões…"},
    "inf_region_native":      {"es": "nativo",         "en": "native",        "pt": "nativo"},
    "regions_title":          {"es": "Regiones de los pipelines",
                               "en": "Pipeline regions",
                               "pt": "Regiões dos pipelines"},
    "regions_hint":           {"es": "Elegí un pipeline y arrastrá sobre la imagen: dentro del "
                                     "recuadro lo mueve, en el borde lo estira, afuera dibuja "
                                     "uno nuevo. Los demás se ven finos, para comparar.",
                               "en": "Pick a pipeline and drag on the image: inside the box "
                                     "moves it, on the edge resizes it, outside draws a new "
                                     "one. The others show thin, for reference.",
                               "pt": "Escolha um pipeline e arraste sobre a imagem: dentro da "
                                     "caixa move, na borda estica, fora desenha uma nova. As "
                                     "outras aparecem finas, para comparar."},
    "regions_no_pipelines":   {"es": "Ningún pipeline mira esta cámara.",
                               "en": "No pipeline looks at this camera.",
                               "pt": "Nenhum pipeline olha esta câmera."},
    "regions_waiting_frame":  {"es": "Esperando un frame de la cámara…",
                               "en": "Waiting for a camera frame…",
                               "pt": "Aguardando um frame da câmera…"},
    "regions_save":           {"es": "Guardar regiones", "en": "Save regions",
                               "pt": "Salvar regiões"},
    "regions_preview_box":    {"es": "Lo que recibe el modelo", "en": "What the model gets",
                               "pt": "O que o modelo recebe"},
    "regions_preview_whole":  {"es": "Frame completo.", "en": "Whole frame.",
                               "pt": "Frame completo."},
    "regions_preview_approx": {"es": "Aproximada: sale del frame reducido; con la vista nativa "
                                     "el modelo tiene más detalle.",
                               "en": "Approximate: taken from the reduced frame; with the "
                                     "native view the model gets more detail.",
                               "pt": "Aproximada: sai do frame reduzido; com a vista nativa o "
                                     "modelo tem mais detalhe."},
    "regions_overlap":        {"es": "Se pisa con {pipeline}: {pct} %",
                               "en": "Overlaps {pipeline}: {pct} %",
                               "pt": "Sobrepõe {pipeline}: {pct} %"},
    "regions_problem_outside_frame": {
        "es": "El recuadro se sale del frame.",
        "en": "The box goes outside the frame.",
        "pt": "A caixa sai do frame."},
    "regions_problem_frame_size_changed": {
        "es": "Se dibujó sobre un frame de {drawn} y ahora llega uno de {now}: el recuadro "
              "puede estar corrido.",
        "en": "It was drawn on a {drawn} frame and now a {now} one arrives: the box may be "
              "shifted.",
        "pt": "Foi desenhada sobre um frame de {drawn} e agora chega um de {now}: a caixa "
              "pode estar deslocada."},

    # ── Proceso (configuración) ──────────────────────────────────────────────
    "process_empty_note":     {"es": "Esta instalación todavía no declaró parámetros de "
                                     "proceso editables desde la pantalla.\n\n"
                                     "Los parámetros de lo que se mide —escalas de píxel, "
                                     "límites de carga, umbrales— son distintos en cada "
                                     "proyecto, así que el template no los trae: viven en la "
                                     "sección «process:» del config.yaml y hoy se editan ahí.\n\n"
                                     "Para ponerlos en esta pantalla se completa "
                                     "ui/views/config/process_tab.py, que tiene los dos "
                                     "patrones —un valor suelto y un mapa por cámara— en su "
                                     "docstring. Ver docs/ui.md.",
                               "en": "This installation has not declared any process "
                                     "parameters editable from the screen yet.\n\n"
                                     "The parameters of what gets measured —pixel scales, "
                                     "load limits, thresholds— differ in every project, so the "
                                     "template does not ship them: they live in the «process:» "
                                     "section of config.yaml and are edited there for now.\n\n"
                                     "To bring them to this screen, fill in "
                                     "ui/views/config/process_tab.py, whose docstring has both "
                                     "patterns —a plain value and a per-camera map—. "
                                     "See docs/ui.md.",
                               "pt": "Esta instalação ainda não declarou parâmetros de "
                                     "processo editáveis pela tela.\n\n"
                                     "Os parâmetros do que se mede —escalas de pixel, limites "
                                     "de carga, limiares— são diferentes em cada projeto, "
                                     "então o template não os traz: vivem na seção «process:» "
                                     "do config.yaml e hoje se editam ali.\n\n"
                                     "Para colocá-los nesta tela, complete "
                                     "ui/views/config/process_tab.py, cujo docstring tem os "
                                     "dois padrões —um valor solto e um mapa por câmera—. "
                                     "Ver docs/ui.md."},

    # ── Dataset (configuración) ──────────────────────────────────────────────
    "col_box_general":        {"es": "Recolección",    "en": "Collection",    "pt": "Coleta"},
    "col_card_storage":       {"es": "Almacenamiento", "en": "Storage",       "pt": "Armazenamento"},
    "col_collection_section": {"es": "Cuándo se guarda", "en": "When to save",
                               "pt": "Quando salvar"},
    "col_collection_description": {"es": "Imágenes para reentrenar los modelos. Prenderla o "
                                         "apagarla no exige reiniciar.",
                                   "en": "Images to retrain the models. Turning it on or off "
                                         "needs no restart.",
                                   "pt": "Imagens para retreinar os modelos. Ligar ou desligar "
                                         "não exige reiniciar."},
    "col_enabled":            {"es": "Recolección habilitada", "en": "Collection enabled",
                               "pt": "Coleta habilitada"},
    "col_mode":               {"es": "Modo",           "en": "Mode",          "pt": "Modo"},
    "col_mode_hint":          {"es": "A pedido: una imagen por medición. Se aplica al reiniciar.",
                               "en": "On demand: one image per measurement. Applied on restart.",
                               "pt": "Sob demanda: uma imagem por medição. Aplicado ao "
                                     "reiniciar."},
    "col_mode_on_demand":     {"es": "A pedido",       "en": "On demand",     "pt": "Sob demanda"},
    "col_mode_interval":      {"es": "Por intervalo",  "en": "By interval",   "pt": "Por intervalo"},
    "col_interval_min":       {"es": "Intervalo mínimo", "en": "Minimum interval",
                               "pt": "Intervalo mínimo"},
    "col_interval_max":       {"es": "Intervalo máximo", "en": "Maximum interval",
                               "pt": "Intervalo máximo"},
    "col_interval_hint":      {"es": "Cada guardado, al azar entre los dos",
                               "en": "Each save, at random between the two",
                               "pt": "Cada salvamento, ao acaso entre os dois"},
    "col_content_section":    {"es": "Qué se guarda",  "en": "What is saved", "pt": "O que é salvo"},
    "col_content_description": {"es": "Lo que lleva cada guardado. El JSON tiene las detecciones "
                                      "y el contexto de la medición.",
                                "en": "What each save carries. The JSON has the detections and "
                                      "the measurement context.",
                                "pt": "O que cada salvamento leva. O JSON tem as detecções e o "
                                      "contexto da medição."},
    "col_save_original":      {"es": "Frame crudo",    "en": "Raw frame",     "pt": "Frame cru"},
    "col_save_annotated":     {"es": "Frame anotado",  "en": "Annotated frame",
                               "pt": "Frame anotado"},
    "col_save_json":          {"es": "JSON de inferencia", "en": "Inference JSON",
                               "pt": "JSON de inferência"},
    "col_box_cameras":        {"es": "Cámaras",        "en": "Cameras",       "pt": "Câmeras"},
    "col_cameras_note":       {"es": "Con todas marcadas entra también una cámara que se agregue "
                                     "después.",
                               "en": "With all checked, a camera added later is saved too.",
                               "pt": "Com todas marcadas, uma câmera adicionada depois também "
                                     "entra."},
    "col_cameras_hint":       {"es": "Al menos una. Para no guardar, deshabilitá la recolección.",
                               "en": "At least one. To save nothing, disable collection.",
                               "pt": "Pelo menos uma. Para não salvar, desabilite a coleta."},
    "col_space_section":      {"es": "Espacio",        "en": "Space",         "pt": "Espaço"},
    "col_space_description":  {"es": "Al llegar a un tope se borra lo más viejo para hacer lugar.",
                               "en": "On reaching a limit the oldest is deleted to make room.",
                               "pt": "Ao atingir um limite o mais antigo é apagado para abrir "
                                     "espaço."},
    "col_max_images":         {"es": "Máximo de imágenes", "en": "Maximum images",
                               "pt": "Máximo de imagens"},
    "col_max_images_hint":    {"es": "Por cámara",     "en": "Per camera",    "pt": "Por câmera"},
    "col_min_free_space":     {"es": "Espacio libre mínimo", "en": "Minimum free space",
                               "pt": "Espaço livre mínimo"},
    "col_min_free_space_hint": {"es": "En GB, en el disco del dataset",
                                "en": "In GB, on the dataset disk",
                                "pt": "Em GB, no disco do dataset"},
    "col_box_dedup":          {"es": "Duplicados",     "en": "Duplicates",    "pt": "Duplicados"},
    "col_dedup_description":  {"es": "Descarta un frame casi igual a uno de los últimos guardados "
                                     "de esa cámara.",
                               "en": "Drops a frame nearly equal to one of that camera's last "
                                     "saves.",
                               "pt": "Descarta um frame quase igual a um dos últimos salvos dessa "
                                     "câmera."},
    "col_dedup_enabled":      {"es": "Descartar duplicados", "en": "Drop duplicates",
                               "pt": "Descartar duplicados"},
    "col_history_len":        {"es": "Historial comparado", "en": "Compared history",
                               "pt": "Histórico comparado"},
    "col_history_len_hint":   {"es": "Últimos guardados contra los que se compara",
                               "en": "Last saves it is compared against",
                               "pt": "Últimos salvos com os quais se compara"},
    "col_hamming":            {"es": "Umbral de Hamming", "en": "Hamming threshold",
                               "pt": "Limiar de Hamming"},
    "col_hamming_hint":       {"es": "De 0 a 64; más alto descarta más",
                               "en": "From 0 to 64; higher drops more",
                               "pt": "De 0 a 64; mais alto descarta mais"},
    "col_mode_no_cycle":      {"es": "A pedido no guarda nada: ningún pipeline tiene ciclo de "
                                     "medición. Usar Por intervalo.",
                               "en": "On demand saves nothing: no pipeline has a measurement "
                                     "cycle. Use By interval.",
                               "pt": "Sob demanda não salva nada: nenhum pipeline tem ciclo de "
                                     "medição. Usar Por intervalo."},

    # ── Telemetría (configuración) ───────────────────────────────────────────
    "tel_card_destinations":  {"es": "Destinos",       "en": "Destinations",  "pt": "Destinos"},
    "tel_box_influxdb":       {"es": "InfluxDB",       "en": "InfluxDB",      "pt": "InfluxDB"},
    "tel_influx_description": {"es": "Series de tiempo para los dashboards, un punto por segundo. "
                                     "Se aplica al reiniciar.",
                               "en": "Time series for the dashboards, one point per second. "
                                     "Applied on restart.",
                               "pt": "Séries temporais para os dashboards, um ponto por segundo. "
                                     "Aplicado ao reiniciar."},
    "tel_influx_enabled":     {"es": "InfluxDB habilitado",
                               "en": "InfluxDB enabled",
                               "pt": "InfluxDB habilitado"},
    "tel_box_mqtt":           {"es": "MQTT",           "en": "MQTT",          "pt": "MQTT"},
    "tel_mqtt_description":   {"es": "El mismo dato en JSON, un tópico por measurement. Se aplica "
                                     "al reiniciar.",
                               "en": "The same data as JSON, one topic per measurement. Applied "
                                     "on restart.",
                               "pt": "O mesmo dado em JSON, um tópico por measurement. Aplicado "
                                     "ao reiniciar."},
    "tel_mqtt_enabled":       {"es": "MQTT habilitado", "en": "MQTT enabled", "pt": "MQTT habilitado"},
    "tel_org":                {"es": "Organización",   "en": "Organisation",  "pt": "Organização"},
    "tel_bucket":             {"es": "Bucket",         "en": "Bucket",        "pt": "Bucket"},
    "tel_topic_base":         {"es": "Base de tópicos",
                               "en": "Topic base",     "pt": "Base de tópicos"},
    "tel_topic_base_hint":    {"es": "Publica en <base>/<measurement>",
                               "en": "Publishes to <base>/<measurement>",
                               "pt": "Publica em <base>/<measurement>"},
    "tel_mqtt_port_hint":     {"es": "0 = 1883, u 8883 con TLS",
                               "en": "0 = 1883, or 8883 with TLS",
                               "pt": "0 = 1883, ou 8883 com TLS"},
    "tel_security":           {"es": "Seguridad",      "en": "Security",      "pt": "Segurança"},
    "tel_tls":                {"es": "Conectar con TLS", "en": "Connect over TLS",
                               "pt": "Conectar com TLS"},
    "tel_influx_token_note":  {"es": "El token sale de INFLUXDB_TOKEN, del entorno o del .env.",
                               "en": "The token comes from INFLUXDB_TOKEN, in the environment or the .env file.",
                               "pt": "O token vem de INFLUXDB_TOKEN, do ambiente ou do .env."},
    "tel_mqtt_password_note": {"es": "La password sale de MQTT_PASSWORD, del entorno o del .env.",
                               "en": "The password comes from MQTT_PASSWORD, in the environment or the .env file.",
                               "pt": "A senha vem de MQTT_PASSWORD, do ambiente ou do .env."},

    # ── Modbus (configuración) ───────────────────────────────────────────────
    "mb_card_server":         {"es": "Esclavo Modbus", "en": "Modbus slave",  "pt": "Escravo Modbus"},
    "mb_identity_section":    {"es": "Identificación", "en": "Identification", "pt": "Identificação"},
    "mb_identity_description": {"es": "Cómo lo ve el PLC. El mapa de registros se edita en "
                                      "register_map.yaml, no acá.",
                                "en": "How the PLC sees it. The register map is edited in "
                                      "register_map.yaml, not here.",
                                "pt": "Como o CLP o vê. O mapa de registradores se edita em "
                                      "register_map.yaml, não aqui."},
    "mb_slave_id":            {"es": "Dirección de esclavo", "en": "Slave address",
                               "pt": "Endereço do escravo"},
    "mb_slave_id_hint":       {"es": "De 1 a 247",     "en": "From 1 to 247", "pt": "De 1 a 247"},
    "mb_register_count":      {"es": "Registros expuestos",
                               "en": "Exposed registers",
                               "pt": "Registradores expostos"},
    "mb_map_extent":          {"es": "El mapa cargado llega al {max_addr} ({plc_addr} en el PLC)",
                               "en": "The loaded map reaches {max_addr} ({plc_addr} on the PLC)",
                               "pt": "O mapa carregado vai até {max_addr} ({plc_addr} no CLP)"},
    "mb_box_tcp":             {"es": "Modbus TCP",     "en": "Modbus TCP",    "pt": "Modbus TCP"},
    "mb_tcp_description":     {"es": "El servidor en la red del PLC. Se aplica al reiniciar.",
                               "en": "The server on the PLC network. Applied on restart.",
                               "pt": "O servidor na rede do CLP. Aplicado ao reiniciar."},
    "mb_tcp_enabled":         {"es": "Servidor TCP habilitado",
                               "en": "TCP server enabled",
                               "pt": "Servidor TCP habilitado"},
    "mb_port_hint":           {"es": "502 es el estándar",
                               "en": "502 is the standard",
                               "pt": "502 é o padrão"},
    "mb_box_rtu":             {"es": "Modbus RTU (RS-485)", "en": "Modbus RTU (RS-485)",
                               "pt": "Modbus RTU (RS-485)"},
    "mb_rtu_description":     {"es": "La línea serie: tiene que coincidir con la del maestro. Se "
                                     "aplica al reiniciar.",
                               "en": "The serial line: it has to match the master's. Applied on "
                                     "restart.",
                               "pt": "A linha serial: tem que coincidir com a do mestre. Aplicado "
                                     "ao reiniciar."},
    "mb_rtu_enabled":         {"es": "Servidor RTU habilitado",
                               "en": "RTU server enabled",
                               "pt": "Servidor RTU habilitado"},
    "mb_serial_port":         {"es": "Puerto serie",   "en": "Serial port",   "pt": "Porta serial"},
    "mb_baudrate":            {"es": "Velocidad",      "en": "Baud rate",     "pt": "Velocidade"},
    "mb_baudrate_hint":       {"es": "En bps",         "en": "In bps",        "pt": "Em bps"},
    "mb_parity":              {"es": "Paridad",        "en": "Parity",        "pt": "Paridade"},
    "mb_parity_none":         {"es": "Ninguna",        "en": "None",          "pt": "Nenhuma"},
    "mb_parity_even":         {"es": "Par",            "en": "Even",          "pt": "Par"},
    "mb_parity_odd":          {"es": "Impar",          "en": "Odd",           "pt": "Ímpar"},
    "mb_stop_bits":           {"es": "Bits de parada", "en": "Stop bits",     "pt": "Bits de parada"},
    # La trama como la escribe un manual: «115200 bps · 8N1 · esclavo 1».
    "mb_frame":               {"es": "Trama: {baudrate} bps · {data_bits}{parity}{stop_bits} · "
                                     "esclavo {slave_id}",
                               "en": "Frame: {baudrate} bps · {data_bits}{parity}{stop_bits} · "
                                     "slave {slave_id}",
                               "pt": "Trama: {baudrate} bps · {data_bits}{parity}{stop_bits} · "
                                     "escravo {slave_id}"},

    # ── Sistema (configuración) ──────────────────────────────────────────────
    "sys_card":               {"es": "Equipo",         "en": "Device",        "pt": "Equipamento"},
    "sys_box_project":        {"es": "Identidad",      "en": "Identity",      "pt": "Identidade"},
    "sys_project_description": {"es": "Qué instalación es. Se fija al armar el equipo y no se "
                                      "edita desde acá.",
                                "en": "Which installation this is. Set when the device is built "
                                      "and not edited here.",
                                "pt": "Qual instalação é esta. Definida ao montar o "
                                      "equipamento e não editada aqui."},
    "sys_box_general":        {"es": "Registro y carpetas", "en": "Logging and folders",
                               "pt": "Registro e pastas"},
    "sys_general_description": {"es": "Cuánto detalle va al log y dónde se guardan los logs y "
                                      "el dataset. Toma efecto al reiniciar.",
                                "en": "How much detail goes to the log and where logs and the "
                                      "dataset are stored. Takes effect on restart.",
                                "pt": "Quanto detalhe vai para o log e onde ficam os logs e o "
                                      "dataset. Tem efeito ao reiniciar."},
    "sys_box_interface":      {"es": "Interfaz",       "en": "Interface",     "pt": "Interface"},
    "sys_interface_description": {"es": "El idioma de la pantalla. El tema se cambia desde la "
                                        "barra superior.",
                                  "en": "The screen language. The theme is switched from the "
                                        "top bar.",
                                  "pt": "O idioma da tela. O tema é trocado na barra "
                                        "superior."},
    "sys_box_monitor":        {"es": "Monitor de hardware",
                               "en": "Hardware monitor",
                               "pt": "Monitor de hardware"},
    "sys_monitor_description": {"es": "Qué disco y qué interfaces de red mide la pestaña "
                                      "Hardware y la telemetría.",
                                "en": "Which disk and network interfaces the Hardware tab and "
                                      "telemetry measure.",
                                "pt": "Qual disco e quais interfaces de rede a aba Hardware e "
                                      "a telemetria medem."},
    "sys_client":             {"es": "Cliente",        "en": "Client",        "pt": "Cliente"},
    "sys_project_id":         {"es": "Id de proyecto", "en": "Project id",    "pt": "Id do projeto"},
    "sys_app_name":           {"es": "Nombre de la aplicación",
                               "en": "Application name",
                               "pt": "Nome da aplicação"},
    "sys_device_id":          {"es": "Id del equipo",  "en": "Device id",     "pt": "Id do equipamento"},
    "sys_device_id_note":     {"es": "Se cambia en config.yaml. Identifica al equipo en la "
                                     "telemetría: cambiarlo parte la historia en dos series.",
                               "en": "Changed in config.yaml. It identifies the device in "
                                     "telemetry: changing it splits the history in two series.",
                               "pt": "Alterado em config.yaml. Identifica o equipamento na "
                                     "telemetria: alterá-lo divide o histórico em duas séries."},
    "sys_log_level":          {"es": "Nivel de log",   "en": "Log level",     "pt": "Nível de log"},
    "sys_log_level_hint":     {"es": "DEBUG sólo para buscar una falla: llena el disco.",
                               "en": "DEBUG only to chase a fault: it fills the disk.",
                               "pt": "DEBUG só para procurar uma falha: enche o disco."},
    "sys_logs_path":          {"es": "Carpeta de logs", "en": "Logs folder",  "pt": "Pasta de logs"},
    "sys_dataset_path":       {"es": "Carpeta del dataset",
                               "en": "Dataset folder", "pt": "Pasta do dataset"},
    "sys_language":           {"es": "Idioma",         "en": "Language",      "pt": "Idioma"},
    "sys_interface_note":     {"es": "Toma efecto al reiniciar la aplicación.",
                               "en": "Takes effect when the application restarts.",
                               "pt": "Tem efeito ao reiniciar a aplicação."},
    # El nombre de cada idioma en su propio idioma: quien no entiende el actual reconoce el suyo.
    "language_es":            {"es": "Español",        "en": "Español",       "pt": "Español"},
    "language_en":            {"es": "English",        "en": "English",       "pt": "English"},
    "language_pt":            {"es": "Português",      "en": "Português",     "pt": "Português"},
    "sys_disk_path":          {"es": "Partición medida", "en": "Measured partition",
                               "pt": "Partição medida"},
    "sys_disk_path_hint":     {"es": "Una carpeta de esa partición. Con un punto, la de la "
                                     "aplicación.",
                               "en": "A folder on that partition. A dot is the application's.",
                               "pt": "Uma pasta dessa partição. Com um ponto, a da "
                                     "aplicação."},
    "sys_net_interfaces":     {"es": "Interfaces medidas",
                               "en": "Measured interfaces",
                               "pt": "Interfaces medidas"},
    "sys_net_interfaces_hint": {"es": "Toman efecto al reiniciar.",
                                "en": "Take effect on restart.",
                                "pt": "Têm efeito ao reiniciar."},

    # ── Hardware (diagnóstico) ───────────────────────────────────────────────
    "hw_waiting":             {"es": "Esperando la primera medición.",
                               "en": "Waiting for the first reading.",
                               "pt": "Aguardando a primeira medição."},
    "hw_card_hardware":       {"es": "Hardware",       "en": "Hardware",      "pt": "Hardware"},
    "hw_card_hardware_description": {"es": "Uso del equipo en los últimos dos minutos, medido "
                                           "una vez por segundo.",
                                     "en": "Device usage over the last two minutes, measured "
                                           "once a second.",
                                     "pt": "Uso do equipamento nos últimos dois minutos, "
                                           "medido uma vez por segundo."},
    "hw_cpu":                 {"es": "CPU",            "en": "CPU",           "pt": "CPU"},
    "hw_gpu":                 {"es": "GPU",            "en": "GPU",           "pt": "GPU"},
    "hw_memory":              {"es": "Memoria",        "en": "Memory",        "pt": "Memória"},
    "hw_disk":                {"es": "Disco",          "en": "Disk",          "pt": "Disco"},
    "hw_power":               {"es": "Potencia",       "en": "Power",         "pt": "Potência"},
    "hw_temperature_foot":    {"es": "Temperatura {value} °C", "en": "Temperature {value} °C",
                               "pt": "Temperatura {value} °C"},
    "hw_no_temperature":      {"es": "Sin sensor de temperatura",
                               "en": "No temperature sensor",
                               "pt": "Sem sensor de temperatura"},
    "hw_memory_unit":         {"es": "/ {total} GB",   "en": "/ {total} GB",  "pt": "/ {total} GB"},
    "hw_memory_foot":         {"es": "{pct} % en uso", "en": "{pct} % in use", "pt": "{pct} % em uso"},
    "hw_disk_unit":           {"es": "GB libres",      "en": "GB free",       "pt": "GB livres"},
    "hw_disk_foot":           {"es": "En la partición de {path}",
                               "en": "On the partition of {path}",
                               "pt": "Na partição de {path}"},
    "hw_disk_foot_app":       {"es": "En la partición de la aplicación",
                               "en": "On the application's partition",
                               "pt": "Na partição da aplicação"},
    "hw_overcurrent_none":    {"es": "Sin sobrecorriente", "en": "No overcurrent",
                               "pt": "Sem sobrecorrente"},
    "hw_overcurrent":         {"es": "{count} eventos de sobrecorriente",
                               "en": "{count} overcurrent events",
                               "pt": "{count} eventos de sobrecorrente"},
    "hw_card_temperatures":   {"es": "Temperaturas",   "en": "Temperatures",  "pt": "Temperaturas"},
    "hw_card_temperatures_description": {"es": "Cada zona térmica que publica el equipo, con "
                                               "el nombre que le da la plataforma.",
                                         "en": "Every thermal zone the device publishes, "
                                               "named as the platform names it.",
                                         "pt": "Cada zona térmica que o equipamento publica, "
                                               "com o nome que a plataforma lhe dá."},
    "hw_temperatures_empty":  {"es": "El equipo no publica zonas térmicas.",
                               "en": "The device publishes no thermal zones.",
                               "pt": "O equipamento não publica zonas térmicas."},
    "hw_card_engines":        {"es": "Aceleradores",   "en": "Accelerators",  "pt": "Aceleradores"},
    "hw_card_engines_description": {"es": "Los motores de la Jetson usados en los últimos "
                                          "segundos, y a qué frecuencia.",
                                    "en": "The Jetson engines used in the last seconds, and "
                                          "at which clock.",
                                    "pt": "Os motores da Jetson usados nos últimos segundos, "
                                          "e em qual frequência."},
    "hw_engine":              {"es": "Motor",          "en": "Engine",        "pt": "Motor"},
    "hw_engine_state":        {"es": "Estado",         "en": "State",         "pt": "Estado"},
    "hw_engine_clock":        {"es": "Frecuencia",     "en": "Clock",         "pt": "Frequência"},
    "hw_engine_active":       {"es": "En uso",         "en": "In use",        "pt": "Em uso"},
    "hw_engine_idle":         {"es": "En reposo",      "en": "Idle",          "pt": "Em repouso"},
    "hw_card_cameras":        {"es": "Cámaras",        "en": "Cameras",       "pt": "Câmeras"},
    "hw_card_cameras_description": {"es": "Lo que informa cada cámara: su estado, los cuadros "
                                          "por segundo contra su límite y la temperatura del "
                                          "sensor.",
                                    "en": "What every camera reports: its state, frames per "
                                          "second against its limit and the sensor "
                                          "temperature.",
                                    "pt": "O que cada câmera informa: seu estado, os quadros "
                                          "por segundo contra seu limite e a temperatura do "
                                          "sensor."},
    "hw_camera":              {"es": "Cámara",         "en": "Camera",        "pt": "Câmera"},
    "hw_camera_state":        {"es": "Estado",         "en": "State",         "pt": "Estado"},
    "hw_camera_fps":          {"es": "FPS",            "en": "FPS",           "pt": "FPS"},
    "hw_camera_trend":        {"es": "Últimos dos minutos", "en": "Last two minutes",
                               "pt": "Últimos dois minutos"},
    "hw_camera_temperature":  {"es": "Temperatura",    "en": "Temperature",   "pt": "Temperatura"},
    "hw_camera_view_misses":  {"es": "Vistas perdidas", "en": "Missed views", "pt": "Vistas perdidas"},
    "hw_card_network":        {"es": "Red",            "en": "Network",       "pt": "Rede"},
    "hw_card_network_description": {"es": "Las interfaces que mide el monitor, con su dirección "
                                          "y el tráfico de cada sentido.",
                                    "en": "The interfaces the monitor measures, with their "
                                          "address and the traffic each way.",
                                    "pt": "As interfaces que o monitor mede, com seu endereço "
                                          "e o tráfego em cada sentido."},
    "hw_network_empty":       {"es": "El monitor no mide ninguna interfaz.",
                               "en": "The monitor measures no interface.",
                               "pt": "O monitor não mede nenhuma interface."},
    "hw_net_interface":       {"es": "Interfaz",       "en": "Interface",     "pt": "Interface"},
    "hw_net_address":         {"es": "Dirección IPv4", "en": "IPv4 address",  "pt": "Endereço IPv4"},
    "hw_net_link":            {"es": "Enlace",         "en": "Link",          "pt": "Enlace"},
    "hw_net_rx":              {"es": "Recibe",         "en": "Receives",      "pt": "Recebe"},
    "hw_net_tx":              {"es": "Envía",          "en": "Sends",         "pt": "Envia"},
    "hw_net_up":              {"es": "Con enlace",     "en": "Link up",       "pt": "Com enlace"},
    "hw_net_down":            {"es": "Sin enlace",     "en": "No link",       "pt": "Sem enlace"},

    # ── Diagnóstico ──────────────────────────────────────────────────────────
    "diag_modbus_table_note": {"es": "Mapa de registros para integrar el equipo con el PLC. Los "
                                     "valores se actualizan en vivo.",
                               "en": "Register map to integrate the device with the PLC. "
                                     "Values update live.",
                               "pt": "Mapa de registradores para integrar o equipamento ao "
                                     "CLP. Os valores são atualizados ao vivo."},
    "diag_modbus_copy_csv":   {"es": "Copiar CSV",     "en": "Copy CSV",      "pt": "Copiar CSV"},
    "diag_modbus_copied":     {"es": "Copiado",        "en": "Copied",        "pt": "Copiado"},
    "diag_modbus_bits_prompt": {"es": "Elegí una palabra de estado en la tabla para ver sus bits.",
                                "en": "Pick a status word in the table to see its bits.",
                                "pt": "Escolha uma palavra de estado na tabela para ver seus "
                                      "bits."},
    "diag_modbus_bit_undefined": {"es": "Sin significado definido", "en": "No defined meaning",
                                  "pt": "Sem significado definido"},
    "diag_modbus_register":   {"es": "Registro",       "en": "Register",      "pt": "Registrador"},
    "diag_modbus_desc":       {"es": "Descripción",    "en": "Description",   "pt": "Descrição"},
    "diag_modbus_value":      {"es": "Valor",          "en": "Value",         "pt": "Valor"},
    "diag_modbus_type":       {"es": "Tipo",           "en": "Type",          "pt": "Tipo"},
    "diag_modbus_unit":       {"es": "Unidad",         "en": "Unit",          "pt": "Unidade"},
    "diag_modbus_raw":        {"es": "Crudo",          "en": "Raw",           "pt": "Bruto"},
    "diag_modbus_type_read":  {"es": "Lectura",        "en": "Read",          "pt": "Leitura"},
    "diag_modbus_type_write": {"es": "Escribe el PLC", "en": "PLC writes",    "pt": "O CLP escreve"},
    "diag_modbus_filter_all": {"es": "Todos",          "en": "All",           "pt": "Todos"},
    "diag_modbus_filter_words": {"es": "Palabras de estado", "en": "Status words",
                                 "pt": "Palavras de estado"},
    "diag_modbus_search":     {"es": "Buscar por registro, nombre o descripción",
                               "en": "Search by register, name or description",
                               "pt": "Buscar por registrador, nome ou descrição"},
    "diag_modbus_true":       {"es": "Sí",             "en": "Yes",           "pt": "Sim"},
    "diag_modbus_false":      {"es": "No",             "en": "No",            "pt": "Não"},
    # Un botón de un control segmentado: el rótulo y cuántas filas hay de ese tipo.
    "diag_filter_count":      {"es": "{label} · {count}", "en": "{label} · {count}",
                               "pt": "{label} · {count}"},
    "diag_stats_slave":       {"es": "esclavo {slave_id}", "en": "slave {slave_id}",
                               "pt": "escravo {slave_id}"},
    "diag_stats_rate":        {"es": "Consultas por minuto", "en": "Requests per minute",
                               "pt": "Consultas por minuto"},
    "diag_stats_requests":    {"es": "Consultas contestadas", "en": "Requests answered",
                               "pt": "Consultas respondidas"},
    "diag_stats_reads":       {"es": "Lecturas (FC03)", "en": "Reads (FC03)", "pt": "Leituras (FC03)"},
    "diag_stats_writes":      {"es": "Escrituras (FC06/FC16)", "en": "Writes (FC06/FC16)",
                               "pt": "Escritas (FC06/FC16)"},
    "diag_stats_exceptions":  {"es": "Excepciones",    "en": "Exceptions",    "pt": "Exceções"},
    "diag_stats_exceptions_last": {"es": "{count} · última 0x{code:02X}",
                                   "en": "{count} · last 0x{code:02X}",
                                   "pt": "{count} · última 0x{code:02X}"},
    "diag_stats_clients":     {"es": "Clientes conectados", "en": "Connected clients",
                               "pt": "Clientes conectados"},
    "diag_stats_port":        {"es": "Puerto serie",   "en": "Serial port",   "pt": "Porta serial"},
    "diag_stats_port_open":   {"es": "Abierto",        "en": "Open",          "pt": "Aberta"},
    "diag_stats_port_closed": {"es": "Cerrado",        "en": "Closed",        "pt": "Fechada"},
    "diag_stats_last_request": {"es": "Última consulta", "en": "Last request",
                                "pt": "Última consulta"},
    "diag_stats_age_s":       {"es": "hace {age} s",   "en": "{age} s ago",   "pt": "há {age} s"},
    "diag_stats_age_min":     {"es": "hace {age} min", "en": "{age} min ago", "pt": "há {age} min"},
    "diag_stats_reset":       {"es": "Reiniciar",      "en": "Reset",         "pt": "Reiniciar"},
    "diag_streams_http":      {"es": "HTTP (MJPEG)",   "en": "HTTP (MJPEG)",  "pt": "HTTP (MJPEG)"},
    "diag_streams_http_description": {"es": "Para mirar en el navegador: un stream crudo y uno "
                                            "anotado por cámara.",
                                      "en": "To watch in a browser: one raw and one annotated "
                                            "stream per camera.",
                                      "pt": "Para ver no navegador: um stream cru e um anotado "
                                            "por câmera."},
    "diag_streams_rtsp":      {"es": "RTSP",           "en": "RTSP",          "pt": "RTSP"},
    "diag_streams_rtsp_description": {"es": "Los mismos streams a resolución completa, para un "
                                            "NVR o un reproductor.",
                                      "en": "The same streams at full resolution, for an NVR or "
                                            "a player.",
                                      "pt": "Os mesmos streams em resolução completa, para um "
                                            "NVR ou um reprodutor."},
    "diag_streams_empty":     {"es": "Sin URLs: el servidor no está publicando.",
                               "en": "No URLs: the server is not publishing.",
                               "pt": "Sem URLs: o servidor não está publicando."},
    "diag_streams_clients":   {"es": "Clientes conectados", "en": "Connected clients",
                               "pt": "Clientes conectados"},
    "diag_streams_count":     {"es": "Streams publicados", "en": "Published streams",
                               "pt": "Streams publicados"},
    "diag_streams_copy":      {"es": "Copiar URL",     "en": "Copy URL",      "pt": "Copiar URL"},
    "diag_streams_open":      {"es": "Abrir en el navegador", "en": "Open in the browser",
                               "pt": "Abrir no navegador"},
    "diag_streams_port":      {"es": "puerto {port}",  "en": "port {port}",   "pt": "porta {port}"},
    "diag_streams_native_width": {"es": "ancho de la cámara", "en": "camera width",
                                  "pt": "largura da câmera"},
    "diag_log_all":           {"es": "Todos",          "en": "All",           "pt": "Todos"},
    "diag_log_clear":         {"es": "Limpiar",        "en": "Clear",         "pt": "Limpar"},
    "diag_log_time":          {"es": "Hora",           "en": "Time",          "pt": "Hora"},
    "diag_log_module":        {"es": "Módulo",         "en": "Module",        "pt": "Módulo"},
    "diag_log_level":         {"es": "Nivel",          "en": "Level",         "pt": "Nível"},
    "diag_log_message":       {"es": "Mensaje",        "en": "Message",       "pt": "Mensagem"},
    "diag_log_debug":         {"es": "Depuración",     "en": "Debug",         "pt": "Depuração"},
    "diag_log_info":          {"es": "Información",    "en": "Info",          "pt": "Informação"},
    "diag_log_warning":       {"es": "Avisos",         "en": "Warnings",      "pt": "Avisos"},
    "diag_log_error":         {"es": "Errores",        "en": "Errors",        "pt": "Erros"},
    "diag_log_search":        {"es": "Buscar en el mensaje o el módulo",
                               "en": "Search the message or the module",
                               "pt": "Buscar na mensagem ou no módulo"},
    "diag_log_pause":         {"es": "Pausar",         "en": "Pause",         "pt": "Pausar"},
    "diag_log_resume":        {"es": "Reanudar",       "en": "Resume",        "pt": "Retomar"},
    "diag_log_shown":         {"es": "{shown} de {total} líneas guardadas (máximo {max_rows}) · "
                                     "la más nueva arriba",
                               "en": "{shown} of {total} stored lines (up to {max_rows}) · "
                                     "newest on top",
                               "pt": "{shown} de {total} linhas guardadas (máximo {max_rows}) · "
                                     "a mais nova em cima"},
    "diag_log_paused":        {"es": "En pausa · {count} líneas nuevas sin mostrar",
                               "en": "Paused · {count} new lines not shown",
                               "pt": "Em pausa · {count} linhas novas não exibidas"},

    # ── Licencia ─────────────────────────────────────────────────────────────
    # Los estados espejan el vocabulario STATE_* de system/license/manager.py: la clave
    # es "lic_state_" + el estado, así que agregar un estado allá es agregar un texto acá.
    "lic_state_valid":        {"es": "Licencia válida",
                               "en": "Valid licence",
                               "pt": "Licença válida"},
    "lic_state_absent":       {"es": "Sin licencia",
                               "en": "No licence",
                               "pt": "Sem licença"},
    "lic_state_invalid":      {"es": "Licencia inválida",
                               "en": "Invalid licence",
                               "pt": "Licença inválida"},
    "lic_state_foreign":      {"es": "Licencia de otro equipo",
                               "en": "Licence of another device",
                               "pt": "Licença de outro equipamento"},
    "lic_state_expired":      {"es": "Licencia vencida",
                               "en": "Expired licence",
                               "pt": "Licença vencida"},
    "lic_state_tampered":     {"es": "Reloj del equipo atrasado",
                               "en": "Device clock set back",
                               "pt": "Relógio do equipamento atrasado"},
    "lic_state_unlicensed_build": {"es": "Ejecución desde fuentes",
                               "en": "Running from sources",
                               "pt": "Execução a partir das fontes"},
    "lic_state_unknown":      {"es": "Sin datos",      "en": "No data",       "pt": "Sem dados"},
    "lic_card":               {"es": "Licencia",       "en": "Licence",       "pt": "Licença"},
    "lic_box_state":          {"es": "Estado",         "en": "State",         "pt": "Estado"},
    "lic_state_description":  {"es": "Si el equipo puede medir y publicar, y hasta cuándo.",
                               "en": "Whether the device may measure and publish, and until "
                                     "when.",
                               "pt": "Se o equipamento pode medir e publicar, e até quando."},
    "lic_license_description": {"es": "El archivo instalado: para quién y para qué proyecto se "
                                      "emitió.",
                                "en": "The installed file: whom and which project it was "
                                      "issued for.",
                                "pt": "O arquivo instalado: para quem e para qual projeto foi "
                                      "emitido."},
    "lic_machine_description": {"es": "Cuántas piezas del hardware coinciden con las de la "
                                      "licencia.",
                                "en": "How many hardware parts match the licence.",
                                "pt": "Quantas peças do hardware coincidem com as da "
                                      "licença."},
    "lic_entitlements_description": {"es": "Cuántas cámaras y qué módulos se compraron.",
                                     "en": "How many cameras and which modules were bought.",
                                     "pt": "Quantas câmeras e quais módulos foram comprados."},
    "lic_box_license":        {"es": "Licencia",       "en": "Licence",       "pt": "Licença"},
    "lic_box_machine":        {"es": "Equipo",         "en": "Device",        "pt": "Equipamento"},
    "lic_box_entitlements":   {"es": "Qué habilita",   "en": "What it grants","pt": "O que habilita"},
    "lic_box_renewal":        {"es": "Renovación",     "en": "Renewal",       "pt": "Renovação"},
    "lic_id":                 {"es": "Identificador",  "en": "Identifier",    "pt": "Identificador"},
    "lic_client":             {"es": "Cliente",        "en": "Client",        "pt": "Cliente"},
    "lic_project":            {"es": "Proyecto",       "en": "Project",       "pt": "Projeto"},
    "lic_issued":             {"es": "Emitida",        "en": "Issued",        "pt": "Emitida"},
    "lic_expires":            {"es": "Vence",          "en": "Expires",       "pt": "Vence"},
    "lic_perpetual":          {"es": "Perpetua",       "en": "Perpetual",     "pt": "Perpétua"},
    "lic_expired":            {"es": "Vencida",        "en": "Expired",       "pt": "Vencida"},
    # Dos claves y no una con «día(s)»: el chip lo lee el operador todos los días y un
    # paréntesis en la pantalla se nota. La de singular se usa sólo con days == 1.
    "lic_days_left":          {"es": "Vence en {days} días",
                               "en": "Expires in {days} days",
                               "pt": "Vence em {days} dias"},
    "lic_days_left_one":      {"es": "Vence mañana",
                               "en": "Expires tomorrow",
                               "pt": "Vence amanhã"},
    "lic_fingerprint":        {"es": "Huella del equipo",
                               "en": "Device fingerprint",
                               "pt": "Impressão do equipamento"},
    "lic_fingerprint_value":  {"es": "{matches} de {required} exigidas",
                               "en": "{matches} of {required} required",
                               "pt": "{matches} de {required} exigidas"},
    "lic_sources":            {"es": "Fuentes leídas", "en": "Sources read", "pt": "Fontes lidas"},
    "lic_build":              {"es": "Build",          "en": "Build",         "pt": "Build"},
    "lic_build_compiled":     {"es": "Compilado",      "en": "Compiled",      "pt": "Compilado"},
    "lic_build_sources":      {"es": "Desde fuentes (no se verifica)",
                               "en": "From sources (not enforced)",
                               "pt": "A partir das fontes (não verificado)"},
    "lic_max_cameras":        {"es": "Cámaras",        "en": "Cameras",       "pt": "Câmeras"},
    "lic_cameras_unlimited":  {"es": "Sin cupo",       "en": "No limit",      "pt": "Sem cota"},
    "lic_features":           {"es": "Módulos",        "en": "Modules",       "pt": "Módulos"},
    "lic_renewal_hint":       {"es": "Para renovar: exportar la solicitud, enviarla al "
                                     "proveedor e instalar el archivo que devuelve.",
                               "en": "To renew: export the request, send it to the "
                                     "supplier and install the file they return.",
                               "pt": "Para renovar: exportar a solicitação, enviá-la ao "
                                     "fornecedor e instalar o arquivo devolvido."},
    "lic_export_button":      {"es": "Exportar solicitud…",
                               "en": "Export request…",
                               "pt": "Exportar solicitação…"},
    "lic_install_button":     {"es": "Instalar licencia…",
                               "en": "Install licence…",
                               "pt": "Instalar licença…"},
    "lic_export_title":       {"es": "Guardar la solicitud de licencia",
                               "en": "Save the licence request",
                               "pt": "Salvar a solicitação de licença"},
    "lic_install_title":      {"es": "Elegir el archivo de licencia",
                               "en": "Choose the licence file",
                               "pt": "Escolher o arquivo de licença"},
    "lic_request_filter":     {"es": "Solicitud de licencia (*.json)",
                               "en": "Licence request (*.json)",
                               "pt": "Solicitação de licença (*.json)"},
    "lic_license_filter":     {"es": "Archivo de licencia (*.lic)",
                               "en": "Licence file (*.lic)",
                               "pt": "Arquivo de licença (*.lic)"},

    # Barra roja persistente en el footer, y cartel al abrir. Los dos usan el mismo
    # criterio que el bit que va al PLC —`should_report_invalid()`— así que aparecen
    # juntos y dicen lo mismo: no se agrega un tercer vocabulario de "¿está mal la
    # licencia?" en la UI.
    "lic_startup_warning_title": {"es": "Licencia",     "en": "Licence",    "pt": "Licença"},
    "lic_startup_warning_body": {
        "es": "El equipo está funcionando sin una licencia válida:\n\n{reason}\n\n"
              "Para generar la solicitud o instalar la licencia recibida, ir a "
              "Diagnóstico → Licencia.",
        "en": "The equipment is running without a valid licence:\n\n{reason}\n\n"
              "To generate a request or install the licence you received, go to "
              "Diagnostics → Licence.",
        "pt": "O equipamento está funcionando sem uma licença válida:\n\n{reason}\n\n"
              "Para gerar a solicitação ou instalar a licença recebida, ir a "
              "Diagnóstico → Licença.",
    },

    # ── Servicios ────────────────────────────────────────────────────────────
    "service_modbus_tcp":     {"es": "Modbus TCP",     "en": "Modbus TCP",    "pt": "Modbus TCP"},
    "service_modbus_rtu":     {"es": "Modbus RTU",     "en": "Modbus RTU",    "pt": "Modbus RTU"},
    "service_video_http":     {"es": "Video HTTP",     "en": "HTTP video",    "pt": "Vídeo HTTP"},
    "service_video_rtsp":     {"es": "Video RTSP",     "en": "RTSP video",    "pt": "Vídeo RTSP"},
    "service_influxdb":       {"es": "InfluxDB",       "en": "InfluxDB",      "pt": "InfluxDB"},
    "service_mqtt":           {"es": "MQTT",           "en": "MQTT",          "pt": "MQTT"},
    "status_disabled":        {"es": "Deshabilitado",  "en": "Disabled",      "pt": "Desabilitado"},
    "status_starting":        {"es": "Iniciando",      "en": "Starting",      "pt": "Iniciando"},
    "status_connecting":      {"es": "Conectando",     "en": "Connecting",    "pt": "Conectando"},
    "status_active":          {"es": "OK",             "en": "OK",            "pt": "OK"},
    "status_error":           {"es": "Error",          "en": "Error",         "pt": "Erro"},
    "status_unknown":         {"es": "Desconocido",    "en": "Unknown",       "pt": "Desconhecido"},

    # ── Diálogos ─────────────────────────────────────────────────────────────
    "roi_title":              {"es": "Definir ROI",    "en": "Define ROI",    "pt": "Definir ROI"},
    "roi_box_coords":         {"es": "Coordenadas (píxeles)",
                               "en": "Coordinates (pixels)",
                               "pt": "Coordenadas (pixels)"},
    "roi_hint":               {"es": "Arrastrá sobre la imagen para dibujar el ROI; desde un "
                                     "borde o una esquina lo estirás y desde adentro lo movés.",
                               "en": "Drag on the image to draw the ROI; drag an edge or a "
                                     "corner to resize it, or its inside to move it.",
                               "pt": "Arraste sobre a imagem para desenhar o ROI; por uma borda "
                                     "ou um canto você o estica, e por dentro o move."},
    "roi_reset":              {"es": "Restablecer",    "en": "Reset",         "pt": "Restabelecer"},
    "roi_save":               {"es": "Guardar ROI",    "en": "Save ROI",      "pt": "Salvar ROI"},
    "level_title":            {"es": "Marcar altura",  "en": "Mark height",   "pt": "Marcar altura"},
    "level_box_coords":       {"es": "Altura (píxeles)",
                               "en": "Height (pixels)",
                               "pt": "Altura (pixels)"},
    "level_y":                {"es": "Y (0 = arriba)", "en": "Y (0 = top)",   "pt": "Y (0 = topo)"},
    "level_hint":             {"es": "Hacé click o arrastrá sobre la imagen para ubicar la "
                                     "línea; es horizontal y cubre todo el ancho.",
                               "en": "Click or drag on the image to place the line; it is "
                                     "horizontal and spans the whole width.",
                               "pt": "Clique ou arraste sobre a imagem para posicionar a "
                                     "linha; é horizontal e cobre toda a largura."},
    "level_save":             {"es": "Guardar altura", "en": "Save height",   "pt": "Salvar altura"},
    "level_no_snapshot":      {"es": "No hay un resultado de inferencia de esta cámara para "
                                     "medir: el video quedó congelado en el último frame.",
                               "en": "There is no inference result from this camera to "
                                     "measure: the video froze on the last frame.",
                               "pt": "Não há resultado de inferência desta câmera para "
                                     "medir: o vídeo congelou no último quadro."},
    "polygon_title":          {"es": "Dibujar zona",   "en": "Draw zone",     "pt": "Desenhar zona"},
    "polygon_box":            {"es": "Zona (píxeles del frame)",
                               "en": "Zone (frame pixels)",
                               "pt": "Zona (pixels do quadro)"},
    "polygon_vertices":       {"es": "{count} vértices", "en": "{count} vertices",
                               "pt": "{count} vértices"},
    "polygon_empty":          {"es": "Sin zona",       "en": "No zone",       "pt": "Sem zona"},
    "polygon_too_few":        {"es": "{count} vértices: hacen falta al menos {min}, o ninguno "
                                     "para no tener zona.",
                               "en": "{count} vertices: at least {min} are needed, or none to "
                                     "have no zone.",
                               "pt": "{count} vértices: são necessários pelo menos {min}, ou "
                                     "nenhum para não ter zona."},
    "polygon_hint":           {"es": "Click sobre la imagen agrega un vértice; sobre un lado, "
                                     "lo inserta ahí. Arrastrá un vértice para moverlo y "
                                     "hacé click derecho para borrarlo.",
                               "en": "Click on the image to add a vertex; on an edge, it is "
                                     "inserted there. Drag a vertex to move it and right-click "
                                     "to delete it.",
                               "pt": "Clique na imagem para adicionar um vértice; sobre um "
                                     "lado, ele é inserido ali. Arraste um vértice para movê-lo "
                                     "e clique com o botão direito para apagá-lo."},
    "polygon_clear":          {"es": "Borrar zona",    "en": "Clear zone",    "pt": "Apagar zona"},
    "polygon_save":           {"es": "Guardar zona",   "en": "Save zone",     "pt": "Salvar zona"},
    "dialog_cancel":          {"es": "Cancelar",       "en": "Cancel",        "pt": "Cancelar"},
    "dialog_pause":           {"es": "Pausar video",   "en": "Pause video",   "pt": "Pausar vídeo"},
    "dialog_resume":          {"es": "Reanudar video", "en": "Resume video",  "pt": "Retomar vídeo"},
    "gpio_box_inputs":        {"es": "Entradas digitales (DI)",
                               "en": "Digital inputs (DI)",
                               "pt": "Entradas digitais (DI)"},
    "gpio_box_outputs":       {"es": "Salidas digitales (DO) — click para conmutar",
                               "en": "Digital outputs (DO) — click to toggle",
                               "pt": "Saídas digitais (DO) — clique para comutar"},
    "gpio_simulated":         {"es": "Hardware GPIO no disponible — modo simulado.",
                               "en": "GPIO hardware unavailable — simulated mode.",
                               "pt": "Hardware GPIO indisponível — modo simulado."},
    "gpio_high":              {"es": "ALTO",           "en": "HIGH",          "pt": "ALTO"},
    "gpio_low":               {"es": "BAJO",           "en": "LOW",           "pt": "BAIXO"},
    "gpio_on":                {"es": "ON",             "en": "ON",            "pt": "ON"},
    "gpio_off":               {"es": "OFF",            "en": "OFF",           "pt": "OFF"},
}

_language = ""
_warned_languages: set = set()


def get_language() -> str:
    """Idioma activo. En el primer llamado sale de `ui.language` del config."""
    global _language
    if not _language:
        set_language(str(ConfigManager().get("ui.language", _FALLBACK_LANGUAGE)))
    return _language


def set_language(language: str):
    """Fija el idioma activo. Uno no soportado cae al de respaldo y lo avisa."""
    global _language
    code = (language or "").strip().lower()
    if code not in LANGUAGES:
        if code not in _warned_languages:
            _warned_languages.add(code)
            logger.warning(
                f"[UI] Idioma '{language}' no soportado; se usa '{_FALLBACK_LANGUAGE}'. "
                f"Válidos: {', '.join(LANGUAGES)}."
            )
        code = _FALLBACK_LANGUAGE
    _language = code


def has_text(key: str) -> bool:
    """Si la clave está en la tabla: para un rótulo que puede no tenerla, sin el aviso de `tr()`."""
    return key in _TEXTS


def tr(key: str) -> str:
    """
    Texto de `key` en el idioma activo.

    Sin traducción al idioma activo cae al de respaldo; sin la clave devuelve la
    clave, para que el hueco se vea en pantalla y no rompa la vista.
    """
    texts = _TEXTS.get(key)
    if texts is None:
        logger.warning(f"[UI] Texto '{key}' no está en la tabla de idiomas.")
        return key
    return texts.get(get_language()) or texts.get(_FALLBACK_LANGUAGE, key)
