"""
Builds the config.yaml of one device from the project's full config.yaml.

The project's config declares every camera position and every pipeline the plant can
have; a device has some cameras, and runs some pipelines on some of them. This keeps what
the device has and removes the rest, so the full config doubles as the catalog: a new job
is a new pipeline there, a new position is a new camera, and this script needs no change
to offer either.

**Slots keep their names.** A camera slot is a position with its own job —`camera_3`
is the same camera on every device— so the PLC map, the dashboards and the `process:`
keys mean the same everywhere. Nothing is renumbered. The camera's `name` is only what
the operator reads, and is edited from the app's configuration panel.

**Which cameras a pipeline sees is chosen per device.** The cameras a pipeline declares
in the full config are the default, not a limit: a device may skip person detection on a
camera, or run it alone on a camera no other pipeline uses.

What is removed, and how:
  - A camera the device does not have leaves `cameras:` and every place that names its
    slot: a dict key (`roi.<camera>`, the per-camera values of `process:`) or a list item
    (a pipeline's `cameras`, `image_collector.cameras`).
  - A pipeline the device does not run leaves the same way. So does one whose declared
    cameras are all gone, unless it is given others: an empty `cameras` list means
    *every* camera, and keeping it would point the pipeline at cameras it was never meant
    to see.
  - A pipeline that keeps running but not on a camera loses that camera from its
    `cameras` and its `roi`.
  - Any other list that ends up empty is reported, not fixed: in this config an empty
    list often means "all" (`image_collector.cameras`), so emptying it can widen what it
    selects, and whether that is right depends on the device.
  - Models stay. A model only loads when a running pipeline names it, so an unused one
    costs nothing, and which pipeline uses which model is code, not config.

The output is one config.yaml, written without the comments of the input: it is the
device's own copy, the one the app rewrites from the configuration panel anyway. Run once
per device, from the repo, and copy the result to the device's `installation/config.yaml`.

Without arguments it guides you: it asks which cameras this device has, which of them
each pipeline sees, the device id and where to write, and shows the result before
writing. By default the file goes to `out/<device_id>/config.yaml` next to this script,
which is not versioned:

    .venv/bin/python setup/device-config/device_config.py

The same, without questions, for a script. `--pipeline SLOT` runs it on the cameras it
declares; `--pipeline SLOT=CAM,CAM` on those; a pipeline that is not named is removed,
and with no `--pipeline` at all every one keeps its declared cameras:

    .venv/bin/python setup/device-config/device_config.py \\
        --cameras camera_3 camera_4 \\
        --pipeline pipeline_1 --pipeline pipeline_2=camera_4 \\
        --set system.device_id=VOLC-02 --output /path/to/config.yaml

`--set dotted.key=value` changes a value that already exists; the value is parsed as
YAML. A key that does not exist is an error, so a typo does not add a setting nobody
reads.

Exits 0 when the file is written, 1 when cancelled, 2 when the selection or a `--set` is
invalid.
"""

import argparse
import copy
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass, field

import yaml

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_SCRIPT_DIR))
DEFAULT_BASE_PATH = os.path.join(_REPO_ROOT, "config.yaml")
DEFAULT_OUTPUT_DIR = os.path.join(_SCRIPT_DIR, "out")

EXIT_OK = 0
EXIT_CANCELLED = 1
EXIT_INVALID = 2

# Reads one answer from the person; `input` in the terminal, a scripted list in the tests.
Ask = Callable[[str], str]


@dataclass
class DeviceConfig:
    """The device's config and what the trim did that someone should look at."""
    config: dict
    dropped_pipelines: list[str] = field(default_factory=list)
    idle_cameras: list[str] = field(default_factory=list)
    emptied_lists: list[str] = field(default_factory=list)


def load_config(path: str) -> dict:
    """Reads a config.yaml; an empty file is an empty dict."""
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def save_config(config: dict, path: str):
    """Writes the config with the same options the app's ConfigManager uses."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(config, handle, default_flow_style=False, sort_keys=False,
                       allow_unicode=True)


def build_device_config(base: dict, *, cameras: list[str] | None = None,
                        pipelines: dict[str, list[str] | None] | None = None) -> DeviceConfig:
    """
    Returns a trimmed copy of `base`; `base` is not modified.

    `cameras` are the camera slots the device has; None keeps them all. `pipelines` maps
    each pipeline the device runs to the cameras it sees there; None as a value means the
    ones it declares that the device has, and None as the whole argument does that for
    every pipeline. A pipeline that is not in the map, or is given no cameras, is removed;
    so is one left without its declared cameras when `pipelines` is None. Raises
    ValueError for a slot that is not in `base`, a pipeline given a camera the device does
    not have, or one named in `pipelines` with None whose declared cameras are all gone.
    """
    all_cameras = list(base.get("cameras", {}) or {})
    sections = _get_pipeline_sections(base)
    kept_cameras = _validate_selection("camera", cameras, all_cameras)
    is_explicit = pipelines is not None
    if pipelines is None:
        pipelines = dict.fromkeys(sections)
    unknown = [slot for slot in pipelines if slot not in sections]
    if unknown:
        raise ValueError(f"Unknown pipeline slot(s): {', '.join(unknown)}. "
                         f"Available: {', '.join(sections) or 'none'}.")

    # Pipeline -> its cameras on this device; None keeps the "all" of one without a list.
    camera_lists: dict[str, list[str] | None] = {}
    dropped_pipelines = []
    for pipeline_slot in sections:
        if pipeline_slot not in pipelines:
            continue
        requested = pipelines[pipeline_slot]
        if requested is None:
            declared = sections[pipeline_slot].get("cameras", []) or []
            if not declared:
                camera_lists[pipeline_slot] = None
                continue
            requested = [slot for slot in declared if slot in kept_cameras]
            if not requested and is_explicit:
                raise ValueError(f"Pipeline '{pipeline_slot}' was selected, but none of its "
                                 f"cameras ({', '.join(declared)}) is on this device.")
            if not requested:
                dropped_pipelines.append(pipeline_slot)
                continue
        missing = [slot for slot in requested if slot not in kept_cameras]
        if missing:
            raise ValueError(f"Pipeline '{pipeline_slot}' was given camera(s) this device "
                             f"does not have: {', '.join(missing)}.")
        if requested:
            camera_lists[pipeline_slot] = [slot for slot in kept_cameras if slot in requested]

    config = copy.deepcopy(base)
    for pipeline_slot, pipeline_cameras in camera_lists.items():
        if pipeline_cameras is not None:
            _assign_cameras(config["inference"]["pipelines"][pipeline_slot], pipeline_cameras)

    removed_slots = ({slot for slot in all_cameras if slot not in kept_cameras}
                     | {slot for slot in sections if slot not in camera_lists})
    emptied_lists = []
    _strip_slots(config, removed_slots, "", emptied_lists)

    seen_cameras = set()
    for pipeline_cameras in camera_lists.values():
        seen_cameras.update(kept_cameras if pipeline_cameras is None else pipeline_cameras)
    idle_cameras = [slot for slot in kept_cameras if slot not in seen_cameras]
    return DeviceConfig(config, dropped_pipelines, idle_cameras, emptied_lists)


def apply_overrides(config: dict, assignments: list[str]):
    """
    Applies `dotted.key=value` assignments in place; the value is parsed as YAML.

    Raises ValueError for a malformed assignment or a key that is not already there.
    """
    for assignment in assignments:
        key, separator, raw_value = assignment.partition("=")
        if not separator or not key.strip():
            raise ValueError(f"'{assignment}' is not key=value.")

        *parents, leaf = key.strip().split(".")
        node = config
        for part in parents:
            node = node.get(part) if isinstance(node, dict) else None
            if not isinstance(node, dict):
                break
        if not isinstance(node, dict) or leaf not in node:
            raise ValueError(f"'{key.strip()}' is not in the config.")
        node[leaf] = yaml.safe_load(raw_value) if raw_value.strip() else ""


def describe(config: dict) -> str:
    """The cameras and pipelines of a config, one per line, for choosing a selection."""
    lines = ["Cameras:"]
    for slot, section in (config.get("cameras", {}) or {}).items():
        lines.append(f"  {slot:<12} {(section or {}).get('name', '')}")

    lines.append("Pipelines:")
    for slot, section in _get_pipeline_sections(config).items():
        cameras = ", ".join(section.get("cameras", []) or []) or "all cameras"
        lines.append(f"  {slot:<12} {section.get('name', '')}  [{section.get('type', '')}]"
                     f"  on {cameras}")
    return "\n".join(lines)


def run_wizard(base: dict, ask: Ask = input) -> tuple[DeviceConfig, str] | None:
    """
    Asks the cameras, which of them each pipeline sees, the device id and where to
    write, and shows the result.

    Returns the device's config and the output path, or None when the person declines at
    the end. Nothing is written here.
    """
    camera_sections = base.get("cameras", {}) or {}
    names = {slot: str((section or {}).get("name", "")) for slot, section
             in camera_sections.items()}
    all_cameras = list(camera_sections)

    print("\nCameras in the project:")
    _print_numbered(all_cameras, names)
    picked = _ask_numbers("Which ones does this device have? Numbers separated by spaces",
                          len(all_cameras), list(range(len(all_cameras))), ask)
    cameras = [all_cameras[index] for index in picked]

    print("\nCameras on this device:")
    _print_numbered(cameras, names)
    print("\nWhich of them does each pipeline see? 0 = it does not run on this device.")
    pipelines = {}
    for pipeline_slot, section in _get_pipeline_sections(base).items():
        declared = section.get("cameras", []) or cameras
        default = [index for index, slot in enumerate(cameras) if slot in declared]
        picked = _ask_numbers(f"  {pipeline_slot:<12} {section.get('name', '')}",
                              len(cameras), default, ask, allow_none=True)
        if picked:
            pipelines[pipeline_slot] = [cameras[index] for index in picked]

    device = build_device_config(base, cameras=cameras, pipelines=pipelines)
    system = device.config.setdefault("system", {})
    current_id = str(system.get("device_id", "") or "")
    device_id = ask(f"\nDevice id [{current_id}]: ").strip() or current_id
    system["device_id"] = device_id

    default_path = os.path.join(DEFAULT_OUTPUT_DIR, device_id or "device", "config.yaml")
    output_path = ask(f"Write to [{default_path}]: ").strip() or default_path

    print(f"\n{describe(device.config)}")
    _print_notes(device)
    if os.path.exists(output_path):
        if not _confirm(f"{output_path} already exists. Overwrite it?", False, ask):
            return None
    elif not _confirm("Write it?", True, ask):
        return None
    return device, output_path


def _get_pipeline_sections(config: dict) -> dict:
    """`inference.pipelines`, with every section as a dict."""
    pipelines = (config.get("inference", {}) or {}).get("pipelines", {}) or {}
    return {slot: section or {} for slot, section in pipelines.items()}


def _validate_selection(kind: str, selection: list[str] | None,
                        available: list[str]) -> list[str]:
    """The selection in the order of the config; every slot must exist."""
    if selection is None:
        return list(available)
    if not selection:
        raise ValueError(f"The {kind} selection is empty.")
    unknown = [slot for slot in selection if slot not in available]
    if unknown:
        raise ValueError(f"Unknown {kind} slot(s): {', '.join(unknown)}. "
                         f"Available: {', '.join(available) or 'none'}.")
    return [slot for slot in available if slot in selection]


def _assign_cameras(pipeline_section: dict, cameras: list[str]):
    """Sets the pipeline's cameras and drops the ROIs of the ones it no longer sees."""
    pipeline_section["cameras"] = list(cameras)
    roi = pipeline_section.get("roi")
    if isinstance(roi, dict):
        for slot in [slot for slot in roi if slot not in cameras]:
            del roi[slot]


def _strip_slots(node: object, slots: set[str], path: str, emptied_lists: list[str]):
    """Removes, in place, every dict key and list item that is one of `slots`."""
    if isinstance(node, dict):
        for key in [key for key in node if key in slots]:
            del node[key]
        for key, value in node.items():
            _strip_slots(value, slots, f"{path}.{key}" if path else str(key), emptied_lists)
    elif isinstance(node, list):
        had_items = bool(node)
        node[:] = [item for item in node if not (isinstance(item, str) and item in slots)]
        if had_items and not node:
            emptied_lists.append(path)
        for item in node:
            _strip_slots(item, slots, path, emptied_lists)


def _print_numbered(slots: list[str], names: dict[str, str]):
    for number, slot in enumerate(slots, start=1):
        print(f"  {number}) {slot:<12} {names.get(slot, '')}")


def _ask_numbers(question: str, count: int, default: list[int], ask: Ask, *,
                 allow_none: bool = False) -> list[int]:
    """
    Asks for numbers from 1 to `count` until the answer is valid, and returns them as
    0-based indexes in ascending order. Enter takes `default`; `0` means none, when
    `allow_none`.
    """
    hint = " ".join(str(index + 1) for index in default) or "0"
    valid = f"numbers from 1 to {count}" + (", or 0 for none" if allow_none else "")
    while True:
        answer = ask(f"{question} [{hint}]: ").strip()
        if not answer:
            return list(default)
        if allow_none and answer == "0":
            return []
        tokens = answer.replace(",", " ").split()
        if all(token.isdigit() and 1 <= int(token) <= count for token in tokens):
            return sorted({int(token) - 1 for token in tokens})
        print(f"  Use {valid}.")


def _confirm(question: str, default: bool, ask: Ask) -> bool:
    """Asks a yes/no question until the answer is one; Enter takes the default."""
    hint = "[Y/n]" if default else "[y/N]"
    while True:
        answer = ask(f"{question} {hint} ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes", "n", "no"):
            return answer.startswith("y")


def _print_notes(device: DeviceConfig):
    for pipeline_slot in device.dropped_pipelines:
        print(f"NOTE: pipeline '{pipeline_slot}' removed: none of its cameras is on this device.")
    for camera_slot in device.idle_cameras:
        print(f"NOTE: camera '{camera_slot}' runs no pipeline: it only captures.")
    for path in device.emptied_lists:
        print(f"WARN: '{path}' is now an empty list, which may mean 'all'. Check it.")


def _parse_pipeline_args(values: list[str] | None) -> dict[str, list[str] | None] | None:
    """`SLOT` or `SLOT=CAM,CAM`, as the map `build_device_config` takes."""
    if values is None:
        return None
    pipelines = {}
    for value in values:
        slot, separator, cameras = value.partition("=")
        pipelines[slot.strip()] = ([camera.strip() for camera in cameras.split(",")
                                    if camera.strip()] if separator else None)
    return pipelines


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build one device's config.yaml from the project's full config. "
                    "Without arguments, it asks.")
    parser.add_argument("--base", default=DEFAULT_BASE_PATH,
                        help="full config to start from (default: the repo's config.yaml)")
    parser.add_argument("--list", action="store_true",
                        help="show the cameras and pipelines of the base config and exit")
    parser.add_argument("--cameras", nargs="+", metavar="SLOT",
                        help="camera slots this device has (default: all)")
    parser.add_argument("--pipeline", action="append", metavar="SLOT[=CAM,CAM]",
                        dest="pipelines",
                        help="a pipeline this device runs, on its declared cameras or on "
                             "these; repeatable (default: all, on their declared cameras)")
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        dest="assignments", help="change an existing value; repeatable")
    parser.add_argument("--output", help="where to write the device's config.yaml")
    parser.add_argument("--force", action="store_true", help="overwrite --output if it exists")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, ask: Ask = input) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    base = load_config(args.base)
    if args.list:
        print(describe(base))
        return EXIT_OK

    if not (args.cameras or args.pipelines or args.assignments or args.output):
        try:
            answer = run_wizard(base, ask)
        except (KeyboardInterrupt, EOFError):
            answer = None
        if answer is None:
            print("\nCancelled: nothing written.")
            return EXIT_CANCELLED
        device, output_path = answer
        save_config(device.config, output_path)
        print(f"Written: {output_path}")
        return EXIT_OK

    if not args.output:
        print("ERROR: --output is required with --cameras, --pipeline or --set.")
        return EXIT_INVALID
    if os.path.exists(args.output) and not args.force:
        print(f"ERROR: {args.output} already exists; pass --force to overwrite it.")
        return EXIT_INVALID

    try:
        device = build_device_config(base, cameras=args.cameras,
                                     pipelines=_parse_pipeline_args(args.pipelines))
        apply_overrides(device.config, args.assignments)
    except ValueError as error:
        print(f"ERROR: {error}")
        return EXIT_INVALID

    _print_notes(device)
    save_config(device.config, args.output)
    print(describe(device.config))
    print(f"Written: {args.output}")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
