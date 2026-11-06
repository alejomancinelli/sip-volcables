"""
Presencia filtrada en el tiempo: cuándo una detección que va y viene pasa a ser «hay algo».

Módulo puro: sin Qt, sin ConfigManager, sin numpy y sin I/O. El tiempo entra por parámetro
—el instante del frame— y no se le pide a ningún reloj, como en `rolling.py`: así una
liberación de cinco segundos se prueba sin esperarlos.

Un detector por frame se equivoca en los dos sentidos: un frame suelto ve una persona donde
hay un poste, y una persona de verdad se pierde un frame detrás de un camión. El filtro es
asimétrico porque los dos errores no cuestan lo mismo:

  - **Confirma por cantidad**: `confirm_hits` frames con detección entre los últimos
    `confirm_window`, no necesariamente seguidos. Con N seguidos, algo real que el modelo
    pierde un frame de cada tres no se confirmaría nunca, y ése es el error que importa.
  - **Libera por tiempo**: `release_s` segundos sin ninguna detección. En segundos y no en
    frames porque es un tiempo de seguridad, y el ritmo de inferencia cambia con la carga.
    Liberado, la próxima confirmación arranca de cero.

Un frame que no se pudo juzgar —no confiable, sin vista— no se le pasa: no confirma ni
resetea nada. Cuánto vale el último veredicto sin frames nuevos no lo decide esto, sino
quien lo publica.

**Maquinaria genérica**: no sabe qué se detecta ni dónde.
"""

from collections import deque


class PresenceFilter:
    """
    Presencia confirmada de una fuente —una cámara, un par (pipeline, cámara)—.

    No es thread-safe: uno por fuente, actualizado desde un solo hilo.
    """

    def __init__(self, *, confirm_hits: int, confirm_window: int, release_s: float):
        if not 1 <= confirm_hits <= confirm_window:
            raise ValueError(f"Hacen falta 1 <= confirm_hits <= confirm_window: "
                             f"{confirm_hits} de {confirm_window}.")
        if release_s < 0:
            raise ValueError(f"release_s no puede ser negativo: {release_s}.")
        self._confirm_hits = int(confirm_hits)
        self._release_s = float(release_s)
        self._recent: deque[bool] = deque(maxlen=int(confirm_window))
        self._last_seen_s = 0.0
        self._is_present = False

    @property
    def is_present(self) -> bool:
        return self._is_present

    def update(self, timestamp_s: float, is_seen: bool) -> bool:
        """Suma lo que vio un frame y devuelve si la presencia está confirmada."""
        self._recent.append(bool(is_seen))
        if is_seen:
            self._last_seen_s = float(timestamp_s)
        if not self._is_present:
            self._is_present = sum(self._recent) >= self._confirm_hits
        elif not is_seen and float(timestamp_s) - self._last_seen_s >= self._release_s:
            self._is_present = False
            self._recent.clear()
        return self._is_present
