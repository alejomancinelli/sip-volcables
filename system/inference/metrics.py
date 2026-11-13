"""
Métricas del proceso: qué significan las detecciones en este proyecto.

Módulo puro: sin Qt, sin ConfigManager y sin I/O.

**Específico del proyecto — no cross-portar tal cual.** Es el archivo donde el fork
escribe su cuenta: el porcentaje volumétrico por fracción, el largo de la pieza, el
conteo de defectos, lo que el cliente mide. El template deja lo que sirve en cualquier
fork —conteo y reparto por clase— y nada más.

Contrato del analyzer, que es cómo lo llama el motor:
  - Recibe un InferenceResult ya armado: detecciones filtradas por el modelo, confianza
    promediada, tiempos medidos y los `labels` que dejaron las etapas. Sólo se lo llama
    cuando el resultado es confiable, así que acá no hay que revisar `is_valid`.
  - Los `labels` son texto y no salen al PLC como están: si un veredicto tiene que llegar
    al registro, acá se traduce a un número o a un bit —`belt_full: result.labels.get(
    "belt") == "full"`—, porque el analyzer es el que sabe a qué equivale.
  - Devuelve un **dict plano** de valores numéricos o booleanos. Plano porque su salida
    va tal cual al panel del frame anotado, al JSON del dataset, a los fields de la
    telemetría y —escalada por el esquema— a los registros del PLC; un dict anidado no
    entra en ninguno de los cuatro.
  - **No modifica el resultado ni toca los frames**, y no guarda estado entre llamadas:
    el promedio en el tiempo se hace afuera, con `analysis.average_metrics`. La excepción
    es un filtro que lo declara y lleva su propio estado por par: el de personas
    (`person_safety.py`), que se suma con `build_analyzer()` como las calzas
    (`stopper_check.py`).

`hooks.py` lo entrega al motor y por eso el motor sigue siendo genérico:

    engine = InferenceThread(config, pipeline, analyzer=compute_metrics)

Cuando la cuenta necesita parámetros de la planta —una escala de píxel, un umbral, la
altura de un límite—, esto pasa a ser una factory que los recibe ya leídos, igual que los
annotators y las condiciones del recolector. Quien lee la sección `process` del config es
`hooks.py`, y así esta función se sigue testeando con tres números y sin archivo:

    engine = InferenceThread(config, pipeline,
                             analyzer=build_analyzer(scale_px_per_mm={"camera_1": 3.2}))

Sobre los nombres de las claves: son las que terminan en la telemetría y en la doc del
integrador, así que van en inglés, con su unidad como sufijo, y una vez publicadas no se
renombran a la ligera. Si una métrica es por clase, el nombre de la clase se prefija
—`count_<clase>`— porque los nombres de clase son datos de configuración y podrían
chocar con una clave fija.
"""

from collections.abc import Callable

from . import analysis
from .pipeline import FLOW_CLASS, GRAIN_FLOW_LABEL
from .result import InferenceResult


def compute_metrics(result: InferenceResult) -> dict:
    """
    Métricas del proceso para un resultado confiable.

    Las genéricas —cuántas detecciones hubo, cuántas de cada clase y qué porcentaje
    representa cada clase— y, en un resultado del grano, `flow_ok`: 1 si cae grano y 0
    cualquier otra cosa. El 0 es el estado seguro: también es lo que lee el PLC sin dato.
    """
    metrics: dict = {"detection_count": result.detection_count}
    verdict = result.labels.get(GRAIN_FLOW_LABEL)
    if verdict is not None:
        metrics["flow_ok"] = int(verdict == FLOW_CLASS)

    for class_name, count in analysis.count_by_class(result.detections).items():
        metrics[f"count_{class_name}"] = count
    for class_name, fraction_pct in analysis.class_fractions_pct(result.detections).items():
        metrics[f"fraction_{class_name}_pct"] = fraction_pct

    return metrics


def build_analyzer(*extras: Callable[[InferenceResult], dict]
                   ) -> Callable[[InferenceResult], dict]:
    """
    `compute_metrics` más las métricas de cada extra, en un solo dict plano.

    Sin extras devuelve `compute_metrics` tal cual. Un extra que repite una clave pisa la
    genérica.
    """
    if not extras:
        return compute_metrics

    def analyze(result: InferenceResult) -> dict:
        metrics = compute_metrics(result)
        for extra in extras:
            metrics.update(extra(result))
        return metrics

    analyze.__name__ = f"metrics({', '.join(getattr(e, '__name__', repr(e)) for e in extras)})"
    return analyze
