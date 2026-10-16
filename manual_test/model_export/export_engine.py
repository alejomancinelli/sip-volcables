"""
Exporta modelos de ultralytics (.pt) a .engine de TensorRT, cuadrados o rectangulares.

Uso — en la Jetson que va a correr el engine, con el venv que tiene ultralytics:

    .venv/bin/python manual_test/model_export/export_engine.py              todos
    .venv/bin/python manual_test/model_export/export_engine.py person_det   uno

Lee el config.yaml de al lado. Por cada modelo calcula el tamaño de entrada, exporta con
ultralytics y deja el engine al lado del .pt con el tamaño en el nombre:
`det_384x640_fp16.engine`. Después se apunta `inference.models.<slot>.path` a ese archivo,
con `type: yolo_det`, `yolo_seg` o `yolo_cls`.

**Rectangular o cuadrado es un interruptor**: se exportan los dos y se elige cuál corre
cambiando `path`. El modelo lee el tamaño del engine, no del config, así que no hay nada
más que tocar para comparar.

El tamaño rectangular sale de la relación de aspecto de lo que mira el modelo, redondeado
para arriba al múltiplo de 32 —el paso de YOLO—: 640 de lado largo sobre un frame de
640x360 da 384x640. Los clasificadores van siempre cuadrados, porque se entrenan así.

Qué mirar:
    - Que exporte sin error y en la consola de TensorRT no haya capas que caigan a FP32.
      El builder tarda minutos y usa memoria: con el escritorio abierto, el NX de 8 GB
      puede quedarse sin RAM aunque haya tope de workspace.
    - Que la app, al cargar, diga «entrada 640x384» en el log del modelo.
    - Ultralytics es sólo para exportar: la app corre el engine sin él.

Para entregar, el engine se cifra con la herramienta del repositorio de firma (ver
docs/model_protection.md). La app lo abre igual, cifrado o no.
"""

import math
import shutil
import sys
from pathlib import Path

import yaml

_REPO_ROOT = next(
    (p for p in Path(__file__).resolve().parents if (p / "system").is_dir()),
    None,
)
if _REPO_ROOT is None:
    raise SystemExit("No se encontró la raíz del repo: ningún directorio padre tiene system/.")

_CONFIG_PATH = Path(__file__).resolve().parent / "config.yaml"
_YOLO_STRIDE_PX = 32
_CLASSIFY_TASK = "classify"


def compute_input_hw(*, long_side_px: int, source_w: int, source_h: int,
                     is_rectangular: bool) -> tuple[int, int]:
    """
    (alto, ancho) de la entrada del engine. Rectangular: el lado largo y el corto con la
    relación de aspecto de la fuente, redondeado para arriba al paso de YOLO.
    """
    if not is_rectangular or source_w <= 0 or source_h <= 0:
        return long_side_px, long_side_px
    short_px = long_side_px * min(source_w, source_h) / max(source_w, source_h)
    short_px = int(math.ceil(short_px / _YOLO_STRIDE_PX) * _YOLO_STRIDE_PX)
    if source_w >= source_h:
        return short_px, long_side_px
    return long_side_px, short_px


def _export(slot: str, spec: dict) -> bool:
    from ultralytics import YOLO

    weights = Path(spec["weights"])
    if not weights.is_absolute():
        weights = _REPO_ROOT / weights
    if not weights.exists():
        print(f"  {slot}: no existe {weights}")
        return False

    model = YOLO(str(weights))
    is_rectangular = bool(spec.get("rectangular", False))
    if model.task == _CLASSIFY_TASK and is_rectangular:
        print(f"  {slot}: es un clasificador, se exporta cuadrado")
        is_rectangular = False
    input_h, input_w = compute_input_hw(
        long_side_px=int(spec.get("long_side_px", 640)),
        source_w=int(spec.get("source_width_px", 0)),
        source_h=int(spec.get("source_height_px", 0)),
        is_rectangular=is_rectangular)
    is_half = bool(spec.get("half", True))

    print(f"  {slot}: {model.task}, entrada {input_w}x{input_h}, "
          f"{'FP16' if is_half else 'FP32'}. TensorRT tarda unos minutos...")
    exported = Path(model.export(
        format="engine", imgsz=[input_h, input_w], half=is_half,
        workspace=float(spec.get("workspace_gb", 1)), dynamic=False, batch=1, device=0,
    ))
    target = exported.with_name(
        f"{weights.stem}_{input_h}x{input_w}_{'fp16' if is_half else 'fp32'}.engine")
    shutil.move(str(exported), str(target))
    print(f"  {slot}: {target.relative_to(_REPO_ROOT)}")
    print(f"    inference.models.{slot}.path: {target.relative_to(_REPO_ROOT).as_posix()}")
    return True


def main() -> int:
    if not _CONFIG_PATH.exists():
        print(f"Falta {_CONFIG_PATH}.")
        return 2
    models = (yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8")) or {}).get("models") or {}
    selected = sys.argv[1:] or list(models)
    unknown = [slot for slot in selected if slot not in models]
    if unknown:
        print(f"No están en {_CONFIG_PATH.name}: {', '.join(unknown)}. "
              f"Disponibles: {', '.join(models)}.")
        return 2
    try:
        import ultralytics  # noqa: F401 — sólo para avisar antes de empezar si falta
    except ImportError:
        print("Falta ultralytics en este venv: se instala con el framework del fork "
              "(setup/jetson/requirements.txt).")
        return 2

    failed = [slot for slot in selected if not _export(slot, models[slot])]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
