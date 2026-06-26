"""
Minimalistic image format converter using BioIO.
Converts various image formats to OME-TIFF with optional Z-projection.
Saves metadata and ROIs as YAML sidecars.

MIT License - BIPHUB, University of Oslo
"""
import os
import argparse
import logging
import re
import json
import sys
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from importlib.metadata import PackageNotFoundError, version as package_version
from typing import Any, Iterator, Optional, cast
from concurrent.futures import ProcessPoolExecutor, as_completed
import numpy as np
from tqdm import tqdm

from bioio import BioImage
import yaml

import bioimage_pipeline_utils as rp
import extract_metadata

try:
    from gooey import Gooey, GooeyParser
    HAS_GOOEY = True
except Exception:
    Gooey = None
    GooeyParser = argparse.ArgumentParser
    HAS_GOOEY = False

# Module-level logger
logger = logging.getLogger(__name__)

PROJECT_NAME = "convert-to-ometif"
FALLBACK_VERSION = "0.1.0"
GUI_STATE_FILENAME = "last_run_state.json"
COMMON_IMAGE_EXTENSIONS = [
    ".nd2",
    ".czi",
    ".lif",
    ".dv",
    ".ims",
    ".oib",
    ".oif",
    ".obf",
    ".tif",
    ".tiff",
    ".ome.tif",
    ".ome.tiff",
    ".npy",
]


@contextmanager
def _mute_console_logging() -> Iterator[None]:
    """Temporarily suppress console stream logging without affecting file handlers."""
    root_logger = logging.getLogger()
    muted_handlers: list[tuple[logging.Handler, int]] = []

    for handler in root_logger.handlers:
        if isinstance(handler, logging.StreamHandler):
            stream = getattr(handler, "stream", None)
            if stream in (sys.stdout, sys.stderr):
                muted_handlers.append((handler, handler.level))
                handler.setLevel(logging.CRITICAL + 1)

    try:
        yield
    finally:
        for handler, original_level in muted_handlers:
            handler.setLevel(original_level)


def _configure_logging(log_level: str) -> None:
    """Configure console logging and optional debug file logging."""
    level = getattr(logging, log_level.upper())
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Avoid duplicate handlers across repeated runs in the same process.
    for existing in list(root_logger.handlers):
        root_logger.removeHandler(existing)

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    if level <= logging.DEBUG:
        log_file = os.path.abspath("convert_to_ometif.debug.log")
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
        logger.debug("Debug file logging enabled: %s", log_file)


def _silence_worker_console_logging() -> None:
    """Initializer for ProcessPool workers to prevent console log noise."""
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
    root_logger.addHandler(logging.NullHandler())
    root_logger.setLevel(logging.CRITICAL + 1)


def _passthrough_decorator(*_args, **_kwargs):
    def decorator(func):
        return func

    return decorator


GOOEY_DECORATOR = cast(Any, Gooey) if HAS_GOOEY and Gooey is not None else _passthrough_decorator


def get_application_version() -> str:
    """Return the installed package version, or a local fallback value."""
    try:
        return package_version(PROJECT_NAME)
    except PackageNotFoundError:
        return FALLBACK_VERSION


def parse_extension_filters(extensions: Optional[str]) -> Optional[list[str]]:
    """Parse a comma-separated extension filter string into normalized suffixes."""
    if extensions is None:
        return None

    value = extensions.strip()
    if not value or value.lower() in {"*", "all", "any"}:
        return None

    normalized: list[str] = []
    seen: set[str] = set()
    for part in value.split(","):
        suffix = part.strip().lower()
        if not suffix:
            continue
        if suffix.startswith("*."):
            suffix = suffix[1:]
        elif suffix == "*":
            return None
        elif not suffix.startswith("."):
            suffix = f".{suffix}"

        if suffix not in seen:
            normalized.append(suffix)
            seen.add(suffix)

    parsed = normalized or None
    logger.debug("Parsed extension filters: %s", parsed if parsed is not None else "all")
    return parsed


def matches_extension_filters(file_path: str, extension_filters: Optional[list[str]]) -> bool:
    """Return True when file_path matches the requested extension filters."""
    if not extension_filters:
        return True

    lower_path = file_path.lower()
    return any(lower_path.endswith(suffix) for suffix in extension_filters)


def parse_selected_files(input_files: Optional[str]) -> list[str]:
    """Parse selected files from CLI or Gooey MultiFileChooser output."""
    if input_files is None:
        return []

    value = input_files.strip()
    if not value:
        return []

    parts = [segment.strip() for segment in re.split(r"[;|\n\r]+", value) if segment.strip()]
    resolved: list[str] = []
    seen: set[str] = set()

    for raw_part in parts:
        normalized = raw_part.strip('"').strip("'")
        file_path = os.path.abspath(normalized)
        if os.path.isfile(file_path):
            key = file_path.lower()
            if key not in seen:
                resolved.append(file_path)
                seen.add(key)

    logger.debug("Resolved %d explicit input file(s)", len(resolved))
    return resolved


def summarize_file_types(files: list[str]) -> str:
    """Return a compact summary of the file types in files."""
    if not files:
        return "0 files"

    counts: Counter[str] = Counter()
    extensions = sorted(COMMON_IMAGE_EXTENSIONS, key=len, reverse=True)
    for file_path in files:
        lower_path = file_path.lower()
        matched_extension = None
        for extension in extensions:
            if lower_path.endswith(extension):
                matched_extension = extension
                break
        if matched_extension is None:
            matched_extension = Path(file_path).suffix.lower() or "[no extension]"
        counts[matched_extension] += 1

    parts = [f"{count} {extension}" for extension, count in sorted(counts.items(), key=lambda item: item[0])]
    return f"{len(files)} files ({', '.join(parts)})"


def get_gui_state_path() -> Path:
    """Return the per-user path used to remember the last GUI selections."""
    base_dir = os.environ.get("APPDATA")
    if base_dir:
        state_dir = Path(base_dir) / "BIPHUB" / PROJECT_NAME
    else:
        state_dir = Path.home() / f".{PROJECT_NAME}"
    return state_dir / GUI_STATE_FILENAME


def load_gui_state() -> dict[str, object]:
    """Load remembered GUI selections if available."""
    state_path = get_gui_state_path()
    try:
        with state_path.open("r", encoding="utf-8") as handle:
            state = json.load(handle)
        if isinstance(state, dict):
            logger.debug("Loaded GUI state from %s", state_path)
            return state
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.debug("Ignoring unreadable GUI state at %s: %s", state_path, exc)
    return {}


def save_gui_state(state: dict[str, object]) -> None:
    """Persist the latest GUI selections for the next launch."""
    state_path = get_gui_state_path()
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        with state_path.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
    except Exception as exc:
        logger.debug("Unable to save GUI state to %s: %s", state_path, exc)


def build_gui_defaults() -> dict[str, object]:
    """Return parser defaults combined with any remembered GUI selections."""
    state = load_gui_state()
    defaults: dict[str, object] = {
        "input_files": state.get("input_files", None),
        "input_folder": state.get("input_folder", ""),
        "recursive": state.get("recursive", False),
        "extensions": state.get("extensions", ",".join(COMMON_IMAGE_EXTENSIONS)),
        "output_folder": state.get("output_folder", None),
        "projection_method": state.get("projection_method", None),
        "collapse_delimiter": state.get("collapse_delimiter", "__"),
        "no_parallel": state.get("no_parallel", False),
        "maxcores": state.get("maxcores", None),
        "no_metadata": state.get("no_metadata", False),
        "output_suffix": state.get("output_suffix", ""),
        "output_format": state.get("output_format", "tif"),
        "dry_run": state.get("dry_run", False),
        "split": state.get("split", False),
        "scene_filter": state.get("scene_filter", "all"),
        "scene_filter_strings": state.get("scene_filter_strings", None),
        "scene_merge_channel": state.get("scene_merge_channel", False),
        "channels": state.get("channels", None),
        "log_level": state.get("log_level", "WARNING"),
        "input_dims_order": state.get("input_dims_order", None),
    }
    if defaults.get("input_folder"):
        defaults["input_files"] = ""
    elif defaults.get("input_files"):
        defaults["input_folder"] = ""
    return defaults


def remember_run_state(args: argparse.Namespace, selected_channels: Optional[list[int]], explicit_files: Optional[list[str]]) -> None:
    """Capture the most useful fields from the current run for the next GUI launch."""
    state: dict[str, object] = {
        "input_files": ";".join(explicit_files) if explicit_files else "",
        "input_folder": "" if explicit_files else (args.input_folder or ""),
        "recursive": bool(args.recursive),
        "extensions": args.extensions,
        "output_folder": args.output_folder,
        "projection_method": args.projection_method,
        "collapse_delimiter": args.collapse_delimiter,
        "no_parallel": bool(args.no_parallel),
        "maxcores": args.maxcores,
        "no_metadata": bool(args.no_metadata),
        "output_suffix": args.output_suffix,
        "output_format": args.output_format,
        "dry_run": bool(args.dry_run),
        "split": bool(args.split),
        "scene_filter": args.scene_filter,
        "scene_filter_strings": args.scene_filter_strings,
        "scene_merge_channel": bool(args.scene_merge_channel),
        "channels": args.channels,
        "log_level": args.log_level,
        "input_dims_order": args.input_dims_order,
    }
    if selected_channels is not None:
        state["channels"] = ",".join(str(channel) for channel in selected_channels)
    save_gui_state(state)


def resolve_input_sources(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> tuple[Optional[list[str]], Optional[str], bool, Optional[list[str]]]:
    """Resolve input as explicit files or as a folder with extension filters."""
    raw_input_files = (args.input_files or "").strip()
    raw_input_folder = (args.input_folder or "").strip()
    selected_files = parse_selected_files(args.input_files)
    logger.debug(
        "Resolving input sources: input_files=%r input_folder=%r recursive=%s",
        raw_input_files,
        raw_input_folder,
        args.recursive,
    )

    if selected_files:
        if raw_input_folder:
            parser.error("Use either --input-files or --input-folder, not both.")
        logger.debug("Using explicit file mode")
        return selected_files, None, False, None

    if raw_input_files:
        parser.error("No valid files were found in --input-files. Clear it to use folder fallback.")

    input_folder = raw_input_folder or "."
    resolved_folder = os.path.abspath(input_folder)
    if not os.path.isdir(resolved_folder):
        parser.error(f"Input folder not found: {resolved_folder}")

    extension_filters = parse_extension_filters(args.extensions)
    logger.debug(
        "Using folder mode: folder=%s recursive=%s extensions=%s",
        resolved_folder,
        args.recursive,
        extension_filters if extension_filters is not None else "all",
    )
    return None, resolved_folder, args.recursive, extension_filters



def parse_channel_selection(channels: Optional[str]) -> Optional[list[int]]:
    """Parse channel selection text into zero-based channel indices."""
    if channels is None:
        return None

    value = channels.strip()
    if not value or value.lower() in {"all", "none", "null"}:
        return None

    if value.lower().startswith("x"):
        value = value[1:].strip()

    parsed: list[int]
    if value.startswith("["):
        try:
            raw = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid --channels JSON list: {channels}") from exc
        if not isinstance(raw, list) or not all(isinstance(v, int) for v in raw):
            raise ValueError("--channels JSON must be a list of integers, e.g. [0,2]")
        parsed = [int(v) for v in raw]
    else:
        try:
            parsed = [int(part.strip()) for part in value.split(",") if part.strip()]
        except ValueError as exc:
            raise ValueError(f"Invalid --channels value: {channels}. Use e.g. 0,2") from exc

    if not parsed:
        return None

    seen: set[int] = set()
    unique: list[int] = []
    for idx in parsed:
        if idx < 0:
            raise ValueError(f"--channels cannot contain negative indices: {idx}")
        if idx not in seen:
            unique.append(idx)
            seen.add(idx)
    return unique


def select_channels(
    data: np.ndarray,
    channel_names: list[str],
    channels: Optional[list[int]],
) -> tuple[np.ndarray, list[str]]:
    """Select requested channel indices from a TCZYX/TCYX array."""
    if channels is None:
        return data, channel_names

    if data.ndim < 2:
        raise ValueError("Channel selection requires data with a channel axis")

    channel_count = int(data.shape[1])
    for idx in channels:
        if idx < 0 or idx >= channel_count:
            raise ValueError(
                f"Invalid --channels index {idx}; valid range is 0-{channel_count - 1}"
            )

    selected_data = np.take(data, channels, axis=1)

    if channel_names and len(channel_names) == channel_count:
        selected_names = [channel_names[idx] for idx in channels]
    else:
        selected_names = [f"Channel_{idx}" for idx in channels]

    return selected_data, selected_names


def project_z(data: np.ndarray, method: str, axis: int = 0) -> np.ndarray:
    """
    Apply Z-projection to image data.
    
    Args:
        data: Input image array
        method: Projection method ('max', 'sum', 'mean', 'median', 'min', 'std')
        axis: Axis to project along (default: 0)
    
    Returns:
        Projected image array
    """
    if method == "max":
        return np.max(data, axis=axis)
    elif method == "sum":
        return np.sum(data, axis=axis)
    elif method == "mean":
        return np.mean(data, axis=axis)
    elif method == "median":
        return np.median(data, axis=axis)
    elif method == "min":
        return np.min(data, axis=axis)
    elif method == "std":
        return np.std(data, axis=axis)
    else:
        logger.warning(f"Unknown projection method '{method}', using max")
        return np.max(data, axis=axis)


def get_scene_dimensions(img: BioImage, scene_id: str) -> tuple[int, int]:
    """
    Get the physical dimensions (Y, X pixel count) of a scene.
    
    Args:
        img: BioImage object
        scene_id: Scene identifier
    
    Returns:
        Tuple of (height, width) in pixels
    """
    img.set_scene(scene_id)
    shape = img.shape  # TCZYX
    return (shape[-2], shape[-1])  # Y, X


def filter_scenes(
    scenes: tuple[str, ...] | list[str],
    img: BioImage,
    scene_filter: str = "largest",
    scene_filter_strings: Optional[list[str]] = None,
) -> list[str]:
    """
    Filter scenes according to the requested strategy.

    Args:
        scenes: All available scene names.
        img: BioImage object (used for dimension-based filters).
        scene_filter: Filtering mode.
            - ``all``      – keep every scene.
            - ``largest``  – keep scenes whose YX pixel count equals the maximum (default).
            - ``smallest`` – keep scenes whose YX pixel count equals the minimum.
            - ``includes`` – keep scenes whose name contains ANY of *scene_filter_strings*.
            - ``excludes`` – keep scenes whose name does NOT contain ANY of *scene_filter_strings*.
        scene_filter_strings: Required for ``includes`` / ``excludes`` modes.

    Returns:
        Filtered list of scene names.
    """
    if scene_filter == "all":
        return list(scenes)

    if scene_filter in ("largest", "smallest"):
        scene_dims = {}
        for scene_id in scenes:
            dims = get_scene_dimensions(img, scene_id)
            scene_dims[scene_id] = dims
            logger.info(f"Scene '{scene_id}': {dims[0]}x{dims[1]} pixels")

        pixel_counts = [dims[0] * dims[1] for dims in scene_dims.values()]
        if scene_filter == "largest":
            target = max(pixel_counts)
            label = "lower resolution pyramid level"
        else:
            target = min(pixel_counts)
            label = "higher resolution level"

        kept = []
        for scene_id, dims in scene_dims.items():
            if dims[0] * dims[1] == target:
                kept.append(scene_id)
            else:
                logger.info(f"Skipping scene '{scene_id}' - {label}")
        return kept

    if scene_filter == "includes":
        if not scene_filter_strings:
            logger.warning("scene_filter='includes' requires --scene-filter-strings; returning all scenes")
            return list(scenes)
        kept = [
            s for s in scenes
            if any(f in s for f in scene_filter_strings)
        ]
        skipped = [s for s in scenes if s not in kept]
        for s in skipped:
            logger.info(f"Skipping scene '{s}' - does not match includes filter")
        return kept

    if scene_filter == "excludes":
        if not scene_filter_strings:
            logger.warning("scene_filter='excludes' requires --scene-filter-strings; returning all scenes")
            return list(scenes)
        kept = [
            s for s in scenes
            if not any(f in s for f in scene_filter_strings)
        ]
        skipped = [s for s in scenes if s not in kept]
        for s in skipped:
            logger.info(f"Skipping scene '{s}' - matches excludes filter")
        return kept

    logger.warning(f"Unknown scene_filter '{scene_filter}', falling back to 'largest'")
    return filter_scenes(scenes, img, scene_filter="largest")


def extract_scene_timestamp(scene_name: str) -> Optional[str]:
    """Extract timestamp in HH:MM:SS format from a scene name."""
    match = re.search(r"\d{2}:\d{2}:\d{2}", scene_name)
    if match:
        return match.group(0)
    return None


def group_scenes_by_timestamp(scenes: list[str]) -> list[tuple[str, list[str]]]:
    """
    Group scene names by timestamp while preserving original scene order.

    Scenes without timestamp are kept as single-item groups.
    """
    groups: dict[str, list[str]] = {}
    ordered_keys: list[str] = []

    for scene_name in scenes:
        timestamp = extract_scene_timestamp(scene_name)
        key = timestamp if timestamp is not None else f"no_timestamp::{scene_name}"
        if key not in groups:
            groups[key] = []
            ordered_keys.append(key)
        groups[key].append(scene_name)

    return [(key, groups[key]) for key in ordered_keys]


def convert_single_file(
    input_path: str,
    output_path: str,
    output_format: str = "tif",
    projection_method: Optional[str] = None,
    save_metadata: bool = True,
    split: bool = False,
    scene_filter: str = "largest",
    scene_filter_strings: Optional[list[str]] = None,
    scene_merge_channel: bool = False,
    channels: Optional[list[int]] = None,
) -> bool:
    """
    Convert a single image file to OME-TIFF.
    Handles multi-scene files by saving each scene separately.

    Args:
        input_path: Path to input image file
        output_path: Path to output TIFF file
        projection_method: Optional Z-projection method
        save_metadata: Whether to save metadata YAML sidecar
        split: If True, save each T, C, Z slice as individual file in a folder
        scene_filter: Scene selection strategy ('all', 'largest', 'smallest',
            'includes', 'excludes').
        scene_filter_strings: Filter strings used with 'includes' / 'excludes'.
        scene_merge_channel: If True, group filtered scenes by timestamp in scene name
            and merge each timestamp-group into channel dimension (C).
        channels: Optional zero-based channel indices to keep, e.g. [0, 2].

    Returns:
        True if successful, False otherwise
    """
    try:
        logger.info(f"Converting: {os.path.basename(input_path)}")
        
        # Load image using BioIO with proper format detection
        img = rp.load_tczyx_image(input_path)
        
        # Check for multiple scenes
        scenes = img.scenes
        logger.info(f"Found {len(scenes)} scene(s)")

        # Filter scenes according to the requested strategy
        if len(scenes) > 1:
            logger.info(f"Applying scene filter: '{scene_filter}' (strings={scene_filter_strings})")
            scenes_to_process = filter_scenes(
                scenes=scenes,
                img=img,
                scene_filter=scene_filter,
                scene_filter_strings=scene_filter_strings,
            )
        else:
            scenes_to_process = list(scenes)

        if not scenes_to_process:
            logger.warning(f"Scene filter removed all scenes from {input_path} - skipping file")
            return False

        if scene_merge_channel and len(scenes_to_process) > 1:
            scene_groups = group_scenes_by_timestamp(scenes_to_process)
            logger.info(
                f"Merging scenes by timestamp: {len(scenes_to_process)} scenes -> "
                f"{len(scene_groups)} output group(s)"
            )
        else:
            scene_groups = [(scene_id, [scene_id]) for scene_id in scenes_to_process]

        logger.info(f"Processing {len(scene_groups)} output group(s)")

        # Process each output group
        for scene_idx, (group_key, scene_ids_in_group) in enumerate(scene_groups):
            merged_data = None
            merged_channel_names = []
            physical_pixel_sizes = None
            representative_scene_id = scene_ids_in_group[0]

            for scene_id in scene_ids_in_group:
                img.set_scene(scene_id)
            
                # Get data for this scene using Dask for better performance
                # Dask provides lazy loading and is ~38% faster for large files
                dask_data = img.dask_data
                data = dask_data.compute()

                logger.info(f"Loaded data shape: {data.shape}, ndim: {data.ndim} for scene '{scene_id}'")

                # Extract metadata for this scene BEFORE any RGB conversion
                # This includes physical pixel sizes, channel names, and other OME metadata
                channel_names = None
                original_channel_count = data.shape[1] if data.ndim >= 2 else 1  # Get C from TCZYX

                try:
                    if physical_pixel_sizes is None and hasattr(img, 'physical_pixel_sizes'):
                        physical_pixel_sizes = img.physical_pixel_sizes
                    if hasattr(img, 'channel_names'):
                        # Convert channel names to regular Python strings to avoid np.str_ issues
                        channel_names = [str(name) for name in img.channel_names]
                    logger.info(f"Extracted metadata - Pixel sizes: {physical_pixel_sizes}, Channels: {channel_names}")
                except Exception as e:
                    logger.warning(f"Could not extract metadata: {e}")

                # Handle RGB images (6D: TCZYXS where S=3 for RGB)
                if data.ndim == 6 and data.shape[-1] == 3:
                    logger.info("Detected RGB image - converting to separate channels")
                    # Reshape from (T, C, Z, Y, X, 3) to (T, C*3, Z, Y, X)
                    T, C, Z, Y, X, S = data.shape
                    # Split RGB into separate channels: R, G, B
                    data = data.transpose(0, 1, 5, 2, 3, 4)  # (T, C, S, Z, Y, X)
                    data = data.reshape(T, C * S, Z, Y, X)  # (T, C*3, Z, Y, X)
                    logger.info(f"Converted RGB to {C * S} channels, new shape: {data.shape}")

                    # Update channel names for RGB split
                    if channel_names and len(channel_names) == C:
                        # Expand channel names: each channel becomes R, G, B variants
                        new_channel_names = []
                        for ch_name in channel_names:
                            new_channel_names.extend([f"{ch_name}_R", f"{ch_name}_G", f"{ch_name}_B"])
                        channel_names = new_channel_names
                        logger.info(f"Updated channel names for RGB: {channel_names}")
                    elif C == 1:
                        # Simple case: single channel RGB becomes R, G, B
                        channel_names = ["Red", "Green", "Blue"]
                        logger.info(f"Set default RGB channel names: {channel_names}")

                if scene_merge_channel and len(scene_ids_in_group) > 1:
                    if merged_data is None:
                        merged_data = data
                    else:
                        if merged_data.shape[0] != data.shape[0] or merged_data.shape[2:] != data.shape[2:]:
                            logger.warning(
                                f"Skipping scene '{scene_id}' due to shape mismatch for merge: "
                                f"{data.shape} vs {merged_data.shape}"
                            )
                            continue
                        merged_data = np.concatenate([merged_data, data], axis=1)

                    base_channel = scene_id.split("/")[-1]
                    if original_channel_count == 1:
                        merged_channel_names.append(base_channel)
                    elif channel_names and len(channel_names) == original_channel_count:
                        merged_channel_names.extend(channel_names)
                    else:
                        merged_channel_names.extend(
                            [f"{base_channel}_C{idx}" for idx in range(original_channel_count)]
                        )
                else:
                    merged_data = data
                    if channel_names:
                        merged_channel_names = channel_names
                    else:
                        merged_channel_names = [f"Channel_{idx}" for idx in range(original_channel_count)]

            if merged_data is None:
                logger.warning(f"No scene data available for group '{group_key}', skipping")
                continue

            # Determine output path for this scene/group
            if len(scene_groups) > 1:
                base, ext = rp.split_compound_extension(output_path)
                if not ext:
                    ext = ".tif"
                scene_output_path = f"{base}_{scene_idx + 1}{ext}"
            else:
                scene_output_path = output_path

            logger.info(
                f"Processing group '{group_key}' with {len(scene_ids_in_group)} scene(s) -> "
                f"{os.path.basename(scene_output_path)}"
            )

            if channels is not None:
                merged_data, merged_channel_names = select_channels(
                    merged_data,
                    merged_channel_names,
                    channels,
                )
                logger.info(
                    "Applied channel selection %s -> %d channel(s)",
                    channels,
                    merged_data.shape[1],
                )

            # Apply projection if requested
            if projection_method:
                logger.info(f"Applying {projection_method} projection")
                
                # Check if Z dimension exists and is > 1
                if merged_data.ndim >= 3:
                    # Project along Z axis for TCZYX-like data.
                    z_axis = None
                    has_z_axis = False
                    did_project = False
                    try:
                        # Try to determine Z axis from dims
                        dim_order = img.dims.order
                        if 'Z' in dim_order:
                            z_axis = dim_order.index('Z')
                            has_z_axis = True
                    except Exception:
                        has_z_axis = False

                    if has_z_axis and z_axis is not None and z_axis < merged_data.ndim:
                        if merged_data.shape[z_axis] > 1:
                            # Use vectorized reductions; apply_along_axis is extremely slow here.
                            merged_data = project_z(merged_data, projection_method, axis=z_axis)
                            did_project = True
                        else:
                            logger.info("Skipping projection because Z dimension size is 1")
                    else:
                        logger.info("Skipping projection because no Z dimension was detected")

                    if did_project:
                        logger.info(f"After projection, data shape: {merged_data.shape}, ndim: {merged_data.ndim}")
            
            logger.info(
                f"Data shape before save: {merged_data.shape}, "
                f"ndim: {merged_data.ndim}, projection_method: {projection_method}"
            )
            
            # Save scene data with metadata preservation
            os.makedirs(os.path.dirname(scene_output_path), exist_ok=True)
            
            # Check if split mode is enabled
            if split:
                if rp.normalize_output_format(output_format) != "tif":
                    raise ValueError("--split requires --output-format tif")
                # Save each T, C, Z slice as individual file
                # Use deterministic naming scheme: basename_Z#_C#.ome.tif
                split_folder = rp.strip_tiff_suffix(scene_output_path)
                os.makedirs(split_folder, exist_ok=True)
                
                # Get basename for files (without path and extension)
                basename = os.path.basename(rp.strip_tiff_suffix(scene_output_path))
                
                logger.info(f"Split mode: Saving individual slices to {split_folder}")
                logger.info(f"Using basename: {basename}")
                
                # Handle both 5D (TCZYX) and 4D (TCYX after projection) data
                if merged_data.ndim == 5:
                    T, C, Z, Y, X = merged_data.shape
                elif merged_data.ndim == 4:
                    # After Z-projection, data is TCYX
                    T, C, Y, X = merged_data.shape
                    Z = 1
                    # Reshape to 5D for uniform processing
                    merged_data = merged_data[:, :, np.newaxis, :, :]
                else:
                    raise ValueError(f"Unexpected data dimensions: {merged_data.ndim}D (expected 4D or 5D)")
                
                logger.info(f"Scene {scene_idx}, Dimensions: T={T}, C={C}, Z={Z}, Y={Y}, X={X}")
                
                # Save files using deterministic naming: basename_Z#_C#.ome.tif
                # This matches the naming used by Bio-Formats Exporter.
                for z in range(Z):
                    for c in range(C):
                        # For multi-timepoint, we need T in filename too
                        if T > 1:
                            # Extended format for timepoints: basename_T#_Z#_C#.ome.tif
                            for t in range(T):
                                slice_data = merged_data[t, c, z, :, :]
                                slice_filename = f"{basename}_T{t}_Z{z}_C{c}.ome.tif"
                                slice_path = os.path.join(split_folder, slice_filename)
                                # Save via shared helper so OME metadata (including physical pixel sizes)
                                # is preserved consistently across pipeline modules.
                                rp.save_tczyx_image(
                                    slice_data[np.newaxis, np.newaxis, np.newaxis, :, :],
                                    slice_path,
                                    physical_pixel_sizes=physical_pixel_sizes,
                                )
                        else:
                            # Standard format (matches Bio-Formats Exporter): basename_Z#_C#.ome.tif
                            slice_data = merged_data[0, c, z, :, :]
                            slice_filename = f"{basename}_Z{z}_C{c}.ome.tif"
                            slice_path = os.path.join(split_folder, slice_filename)
                            # Save via shared helper so OME metadata (including physical pixel sizes)
                            # is preserved consistently across pipeline modules.
                            rp.save_tczyx_image(
                                slice_data[np.newaxis, np.newaxis, np.newaxis, :, :],
                                slice_path,
                                physical_pixel_sizes=physical_pixel_sizes,
                            )
                
                logger.info(f"Saved {Z * C * T} individual slice files for scene {scene_idx}")
                logger.info(f"Example first split file: {basename}_Z0_C0.ome.tif")
                
            else:
                # Standard save mode (single file)
                save_data = merged_data
                if save_data.ndim == 4:
                    # Keep TCZYX semantics after projection by inserting singleton Z.
                    save_data = save_data[:, :, np.newaxis, :, :]

                # Build kwargs for saving with metadata
                save_kwargs = {}
                if physical_pixel_sizes is not None:
                    save_kwargs['physical_pixel_sizes'] = physical_pixel_sizes
                if merged_channel_names:
                    save_kwargs['channel_names'] = merged_channel_names
                
                # Save with metadata
                rp.save_with_output_format(save_data, scene_output_path, output_format, **save_kwargs)
                logger.info(f"Saved: {scene_output_path}")
            
            # Save metadata if requested
            if save_metadata:
                metadata_path = rp.resolve_output_path(scene_output_path, extension=".yaml", suffix="_metadata")
                try:
                    metadata = extract_metadata.get_all_metadata(input_path, output_file=None)

                    # Keep metadata channel information aligned with the actual saved output
                    if "Image metadata" in metadata:
                        image_meta = metadata["Image metadata"]
                        image_dims = image_meta.get("Image dimensions")
                        if isinstance(image_dims, dict):
                            if merged_data.ndim >= 2:
                                image_dims["C"] = int(merged_data.shape[1])

                        if merged_channel_names:
                            image_meta["Channels"] = [{"Name": str(name)} for name in merged_channel_names]
                    
                    # Add scene and conversion info
                    metadata["Convert to tif"] = {
                        "Scene_names": [str(s) for s in scene_ids_in_group],
                        "Merged_channel_names": merged_channel_names,
                        "Selected_channel_indices": channels or [],
                        "Scene_merge_channel": scene_merge_channel,
                        "Scene_group_key": group_key,
                        "Scene_index": scene_idx + 1,
                        "Total_scenes_processed": len(scene_groups),
                        "Scene_filter": scene_filter,
                        "Scene_filter_strings": scene_filter_strings or [],
                    }
                    if projection_method:
                        metadata["Convert to tif"]["Projection"] = {"Method": projection_method}
                    
                    with open(metadata_path, 'w', encoding='utf-8') as f:
                        yaml.safe_dump(metadata, f, sort_keys=False)
                    logger.info(f"Saved metadata: {metadata_path}")
                    
                    # Generate reassembly macro (only for split mode)
                    if split:
                        try:
                            import generate_nis_reassembly_macro
                            split_folder = rp.strip_tiff_suffix(scene_output_path)
                            output_nd2 = split_folder + ".nd2"
                            macro_path = generate_nis_reassembly_macro.generate_macro(
                                split_folder=split_folder,
                                output_nd2=output_nd2,
                                metadata_yaml=metadata_path
                            )
                            logger.info(f"Generated NIS reassembly macro: {macro_path}")
                        except Exception as e:
                            logger.warning(f"Failed to generate NIS reassembly macro: {e}")
                            
                except Exception as e:
                    logger.warning(f"Failed to save metadata: {e}")
        
        return True
        
    except Exception as e:
        logger.error(f"Failed to convert {input_path}: {e}")
        return False


def process_files(
    input_pattern: Optional[str] = None,
    input_folder: Optional[str] = None,
    recursive: bool = False,
    explicit_files: Optional[list[str]] = None,
    output_folder: Optional[str] = None,
    output_format: str = "tif",
    projection_method: Optional[str] = None,
    collapse_delimiter: str = "__",
    maxcores: Optional[int] = None,
    no_parallel: bool = False,
    save_metadata: bool = True,
    output_extension: str = "",
    dry_run: bool = False,
    split: bool = False,
    scene_filter: str = "largest",
    scene_filter_strings: Optional[list[str]] = None,
    scene_merge_channel: bool = False,
    channels: Optional[list[int]] = None,
    extension_filters: Optional[list[str]] = None,
    use_gooey: bool = False,
) -> None:
    """
    Process files from explicit selection or from an input folder.

    Args:
        input_pattern: Legacy file search pattern (deprecated; kept for compatibility)
        input_folder: Folder to scan for files when explicit_files is not provided.
        recursive: Whether to scan input_folder recursively.
        explicit_files: Optional explicit file list to process directly
        output_folder: Output directory (default: input_dir + '_tif')
        projection_method: Optional Z-projection method
        collapse_delimiter: Delimiter for collapsing subfolder paths
        maxcores: Maximum CPU cores to use for parallel processing
        no_parallel: Disable parallel processing
        save_metadata: Whether to save metadata YAML sidecars
        output_extension: Additional extension to add before .ome.tif
        dry_run: Only print planned actions without executing
        split: If True, save each T, C, Z slice as individual file in a folder
        scene_filter: Scene selection strategy ('all', 'largest', 'smallest',
            'includes', 'excludes').
        scene_filter_strings: Filter strings used with 'includes' / 'excludes'.
        scene_merge_channel: If True, group filtered scenes by timestamp and merge
            each group into channel dimension.
        channels: Optional zero-based channel indices to keep.
    """
    if explicit_files:
        logger.debug("Processing explicit files mode")
        files = [os.path.abspath(file_path) for file_path in explicit_files if os.path.isfile(file_path)]
    else:
        logger.debug("Processing folder mode")
        resolved_folder = input_folder
        if not resolved_folder and input_pattern:
            if '**' in input_pattern:
                resolved_folder = input_pattern.split('**', 1)[0].rstrip('/\\')
            else:
                resolved_folder = str(Path(input_pattern).parent)

        if not resolved_folder:
            logger.error("No input files selected and no input folder provided")
            return

        resolved_folder = os.path.abspath(resolved_folder)
        if not os.path.isdir(resolved_folder):
            logger.error("Input folder not found: %s", resolved_folder)
            return

        files = []
        if recursive:
            logger.debug("Scanning recursively under %s", resolved_folder)
            for dirpath, _, filenames in os.walk(resolved_folder):
                for filename in filenames:
                    files.append(os.path.join(dirpath, filename))
        else:
            logger.debug("Scanning top-level folder %s", resolved_folder)
            with os.scandir(resolved_folder) as entries:
                for entry in entries:
                    if entry.is_file():
                        files.append(entry.path)

    if extension_filters:
        before = len(files)
        files = [file_path for file_path in files if matches_extension_filters(file_path, extension_filters)]
        logger.debug("Extension filter reduced file list: %d -> %d", before, len(files))

    if not files:
        suffix_text = f" with extensions {', '.join(extension_filters)}" if extension_filters else ""
        if explicit_files:
            logger.error("No valid input files were selected%s", suffix_text)
        else:
            mode = "recursively" if recursive else "in folder"
            source_folder = input_folder or "(unknown)"
            logger.error("No files found %s: %s%s", mode, source_folder, suffix_text)
        return

    if not use_gooey:
        print(f"Found files: {summarize_file_types(files)}")
    else:
        logger.info("Found %d file(s) to process", len(files))

    if explicit_files:
        base_folder = os.path.commonpath(files)
        if not os.path.isdir(base_folder):
            base_folder = str(Path(files[0]).parent)
    else:
        base_folder = os.path.abspath(input_folder) if input_folder else str(Path(files[0]).parent)

    if output_folder is None:
        output_folder = base_folder + "_tif"
    logger.info("Output folder: %s", output_folder)

    if split and rp.normalize_output_format(output_format) != "tif":
        raise ValueError("--split only supports --output-format tif")

    output_ext = rp.output_extension_for_format(output_format, tiff_extension=".ome.tif")
    file_pairs: list[tuple[str, str]] = []
    for src in files:
        collapsed = rp.collapse_filename(src, base_folder, collapse_delimiter)
        out_name = os.path.basename(rp.resolve_output_path(collapsed, extension=output_ext, suffix=output_extension))
        out_path = os.path.join(output_folder, out_name)
        file_pairs.append((src, out_path))
    logger.debug("Prepared %d file pair(s)", len(file_pairs))

    if dry_run:
        print(f"[DRY RUN] Would process {len(file_pairs)} files")
        print(f"[DRY RUN] Output folder: {output_folder}")
        if projection_method:
            print(f"[DRY RUN] Projection method: {projection_method}")
        print(f"[DRY RUN] Scene filter: {scene_filter} (strings={scene_filter_strings})")
        print(f"[DRY RUN] Scene merge channel: {scene_merge_channel}")
        print(f"[DRY RUN] Channels: {channels if channels is not None else 'all'}")
        for src, dst in file_pairs:
            print(f"[DRY RUN] {src} -> {dst}")
        return

    if no_parallel or len(file_pairs) == 1:
        logger.debug("Sequential processing selected")
        total = len(file_pairs)
        done = 0
        for src, dst in file_pairs:
            success = convert_single_file(
                src,
                dst,
                output_format,
                projection_method,
                save_metadata,
                split,
                scene_filter,
                scene_filter_strings,
                scene_merge_channel,
                channels,
            )
            if not success:
                logger.error("Failed: %s", src)
            done += 1
            if use_gooey:
                logger.info("Progress: %d/%d", done, total)
        return

    max_workers = rp.resolve_maxcores(maxcores, len(file_pairs))
    logger.info("Processing with %d workers", max_workers)
    logger.debug("Starting parallel processing for %d file(s)", len(file_pairs))

    with ProcessPoolExecutor(max_workers=max_workers, initializer=_silence_worker_console_logging) as executor:
        futures = {
            executor.submit(
                convert_single_file,
                src,
                dst,
                output_format,
                projection_method,
                save_metadata,
                split,
                scene_filter,
                scene_filter_strings,
                scene_merge_channel,
                channels,
            ): (src, dst)
            for src, dst in file_pairs
        }

        failed_sources: list[str] = []
        future_exceptions: list[tuple[str, str]] = []
        total = len(futures)
        done = 0

        if use_gooey:
            print(f"progress: 0/{total}", flush=True)
            for future in as_completed(futures):
                src, dst = futures[future]
                try:
                    success = future.result()
                    if not success:
                        failed_sources.append(src)
                        logger.error("Failed: %s", src)
                    else:
                        logger.debug("Completed: %s -> %s", src, dst)
                except Exception as exc:
                    future_exceptions.append((src, str(exc)))
                    logger.error("Exception processing %s: %s", src, exc)
                finally:
                    done += 1
                    print(f"progress: {done}/{total}", flush=True)
        else:
            with _mute_console_logging():
                for future in tqdm(
                    as_completed(futures),
                    total=total,
                    desc="Processing files",
                    unit="file",
                    file=sys.stdout,
                    dynamic_ncols=True,
                ):
                    src, dst = futures[future]
                    try:
                        success = future.result()
                        if not success:
                            failed_sources.append(src)
                            logger.error("Failed: %s", src)
                        else:
                            logger.debug("Completed: %s -> %s", src, dst)
                    except Exception as exc:
                        future_exceptions.append((src, str(exc)))
                        logger.error("Exception processing %s: %s", src, exc)

        for src in failed_sources:
            logger.error("Failed: %s", src)
        for src, message in future_exceptions:
            logger.error("Exception processing %s: %s", src, message)


def build_parser(use_gooey: bool = False) -> argparse.ArgumentParser:
    """Build the CLI or Gooey parser."""
    defaults = build_gui_defaults() if use_gooey else {}
    parser_cls = GooeyParser if use_gooey and HAS_GOOEY else argparse.ArgumentParser
    parser = parser_cls(
        prog="convert-to-ometif",
        description="Minimalistic image converter to OME-TIFF with optional Z-projection.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Example YAML config for run_pipeline.exe:
---
run:
  - name: Convert ND2 files to OME-TIFF (keep largest scene only)
    environment: uv@3.11:default
    commands:
      - python
      - "%REPO%/standard_code/python/convert_to_tif.py"
      - --input-folder
      - "%YAML%/input"
      - --recursive
      - --extensions
      - .nd2
      - --output-folder
      - "%YAML%/output"
      - --log-level
      - INFO

  - name: Convert OBF files, keep only MLE scenes
    environment: uv@3.11:default
    commands:
      - python
      - "%REPO%/standard_code/python/convert_to_tif.py"
      - --input-folder
      - "%YAML%/input"
      - --recursive
      - --extensions
      - .obf
      - --output-folder
      - "%YAML%/output"
      - --scene-filter
      - includes
      - --scene-filter-strings
      - /MLE
      - --scene-merge-channel
      - --log-level
      - INFO

  - name: Convert CZI files, exclude overview scenes
    environment: uv@3.11:default
    commands:
      - python
      - "%REPO%/standard_code/python/convert_to_tif.py"
      - --input-folder
      - "%YAML%/input"
      - --recursive
      - --extensions
      - .czi
      - --output-folder
      - "%YAML%/output"
      - --scene-filter
      - excludes
      - --scene-filter-strings
      - Overview
      - --log-level
      - INFO

  - name: Convert LIF files, process all scenes with max projection
    environment: uv@3.11:default
    commands:
      - python
      - "%REPO%/standard_code/python/convert_to_tif.py"
      - --input-folder
      - "%YAML%/input"
      - --recursive
      - --extensions
      - .lif
      - --output-folder
      - "%YAML%/output"
      - --scene-filter
      - all
      - --projection-method
      - max
      - --log-level
      - INFO
        """
    )

    def chooser_kwargs(widget: str) -> dict:
        if use_gooey and HAS_GOOEY:
            return {"widget": widget}
        return {}

    parser.add_argument(
        "--input-files",
        type=str,
        default=defaults.get("input_files", None),
        help=(
            "Optional file selection. If one or more files are selected, those files are processed. "
            "If empty, the folder input is used instead."
        ),
        **chooser_kwargs("MultiFileChooser")
    )
    parser.add_argument(
        "--input-folder",
        type=str,
        default=defaults.get("input_folder", ""),
        help="Optional folder input used when no files are selected",
        **chooser_kwargs("DirChooser")
    )
    parser.add_argument("--recursive", action="store_true", default=bool(defaults.get("recursive", False)), help="When using folder fallback, search subfolders recursively")
    parser.add_argument(
        "--extensions",
        type=str,
        default=defaults.get("extensions", ",".join(COMMON_IMAGE_EXTENSIONS)),
        help=(
            "Comma-separated extension filter for folder fallback inputs. "
            "Examples: .nd2,.czi or tif,tiff. Use '*' or 'all' to disable filtering."
        )
    )

    parser.add_argument("--output-folder", type=str, default=defaults.get("output_folder", None), help="Output folder (default: input_folder + '_tif')", **chooser_kwargs("DirChooser"))
    parser.add_argument("--projection-method", type=str, default=defaults.get("projection_method", None), choices=["max", "sum", "mean", "median", "min", "std"], help="Z-projection method (default: no projection)")
    parser.add_argument("--collapse-delimiter", type=str, default=defaults.get("collapse_delimiter", "__"), help="Delimiter for collapsing subfolder paths (default: '__')")
    parser.add_argument("--no-parallel", action="store_true", default=bool(defaults.get("no_parallel", False)), help="Disable parallel processing (process files sequentially)")
    parser.add_argument("--maxcores", type=int, default=defaults.get("maxcores", None), help="Maximum CPU cores to use for parallel processing (default: all available CPU cores minus 1). Ignored if --no-parallel is set.")
    parser.add_argument("--no-metadata", action="store_true", default=bool(defaults.get("no_metadata", False)), help="Skip saving metadata YAML sidecars")
    parser.add_argument("--output-suffix", type=str, default=defaults.get("output_suffix", ""), help="Additional suffix to add before .ome.tif")
    parser.add_argument("--output-format", type=str, choices=["tif", "npy", "ilastik-h5"], default=defaults.get("output_format", "tif"), help="Output format (default: tif). Note: --split requires tif.")
    parser.add_argument("--dry-run", action="store_true", default=bool(defaults.get("dry_run", False)), help="Print planned actions without executing")
    parser.add_argument("--split", action="store_true", default=bool(defaults.get("split", False)), help="Save each T, C, Z slice as individual file in a folder (maximum compatibility)")

    merging_group = parser.add_argument_group("Merging", "Options for STED scene filtering and scene-to-channel merging")
    merging_group.add_argument("--scene-filter", type=str, default=defaults.get("scene_filter", "all"), choices=["all", "largest", "smallest", "includes", "excludes"], help=("Scene selection strategy for multi-scene files (default: all). " "'all' keeps every scene. " "'largest'/'smallest' selects by YX pixel count. " "'includes' keeps scenes whose name contains any --scene-filter-strings value. " "'excludes' removes scenes whose name contains any --scene-filter-strings value."))
    merging_group.add_argument("--scene-filter-strings", type=str, nargs="+", default=defaults.get("scene_filter_strings", None), metavar="STRING", help=("One or more substrings used with --scene-filter includes/excludes. " "Example: --scene-filter-strings /MLE  or  --scene-filter-strings Overview Tile"))
    merging_group.add_argument("--scene-merge-channel", action="store_true", default=bool(defaults.get("scene_merge_channel", False)), help=("Group filtered scenes by HH:MM:SS timestamp in scene name and merge " "each timestamp-group into the channel dimension"))
    merging_group.add_argument("--channels", type=str, default=defaults.get("channels", None), help=("Optional zero-based channel indices to keep. Examples: '0,2', 'x0,2', '[0,2]'. " "Default keeps all channels."))

    parser.add_argument("--version", action="version", version=f"%(prog)s {get_application_version()}")
    parser.add_argument("--log-level", type=str, default=defaults.get("log_level", "WARNING"), choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Logging level (default: WARNING)")
    parser.add_argument("--input-dims-order", type=str, default=defaults.get("input_dims_order", None), help="Optional input dimensions order for array-like inputs (for example ZYX or CZYX).")
    return parser


def run_main(argv: Optional[list[str]] = None, use_gooey: bool = False) -> None:
    """Shared runtime for CLI and GUI execution."""
    parser = build_parser(use_gooey=use_gooey)
    args = parser.parse_args(argv)

    _configure_logging(args.log_level)

    if args.input_dims_order:
        os.environ["RP_INPUT_DIMS_ORDER"] = args.input_dims_order
    explicit_files, input_folder, recursive, extension_filters = resolve_input_sources(args, parser)

    if not use_gooey:
        print("Input args:")
        print(f"  input_files={args.input_files}")
        print(f"  input_folder={args.input_folder}")
        print(f"  recursive={args.recursive}")
        print(f"  extensions={args.extensions}")
        print(f"  output_folder={args.output_folder}")
        print(f"  output_format={args.output_format}")
        print(f"  projection_method={args.projection_method}")
        print(f"  no_parallel={args.no_parallel}")
        print(f"  split={args.split}")
        print(f"  scene_filter={args.scene_filter}")
        print(f"  scene_merge_channel={args.scene_merge_channel}")
        print(f"  channels={args.channels}")

    logger.debug(
        "Runtime configuration: files=%s folder=%s recursive=%s output_folder=%s output_format=%s dry_run=%s",
        len(explicit_files) if explicit_files else 0,
        input_folder,
        recursive,
        args.output_folder,
        args.output_format,
        args.dry_run,
    )

    selected_channels = parse_channel_selection(args.channels)

    remember_run_state(args, selected_channels, explicit_files)

    process_files(
        explicit_files=explicit_files,
        input_folder=input_folder,
        recursive=recursive,
        output_folder=args.output_folder,
        output_format=args.output_format,
        projection_method=args.projection_method,
        collapse_delimiter=args.collapse_delimiter,
        maxcores=args.maxcores,
        no_parallel=args.no_parallel,
        save_metadata=not args.no_metadata,
        output_extension=args.output_suffix,
        dry_run=args.dry_run,
        split=args.split,
        scene_filter=args.scene_filter,
        scene_filter_strings=args.scene_filter_strings,
        scene_merge_channel=args.scene_merge_channel,
        channels=selected_channels,
        extension_filters=extension_filters,
        use_gooey=use_gooey,
    )


@GOOEY_DECORATOR(
    program_name="Convert To OME-TIFF",
    program_description="Convert microscopy and image files to OME-TIFF, TIFF, NPY, or Ilastik H5.",
    default_size=(900, 760),
    required_cols=1,
    optional_cols=2,
    navigation="TABBED",
    tabbed_groups=True,
    clear_before_run=True,
    terminal_font_family="Consolas",
    progress_regex=r"^progress:\s+(?P<current>\d+)/(?P<total>\d+)$",
    progress_expr="current / total * 100",
)
def launch_gui() -> None:
    """Launch the Gooey desktop interface."""
    run_main(use_gooey=True)


def main(argv: Optional[list[str]] = None) -> None:
    """Main CLI entry point."""
    run_main(argv=argv, use_gooey=False)


def entrypoint(argv: Optional[list[str]] = None) -> None:
    """Run CLI when arguments are supplied, otherwise start the Gooey GUI."""
    # When Gooey runs the program it spawns a child process with the GOOEY
    # environment variable set and passes --ignore-gooey (already stripped from
    # sys.argv by the @Gooey decorator at import time). That child must run in
    # Gooey mode so progress is emitted as newline-terminated "progress: N/total"
    # lines that Gooey's stdout reader can parse live. Routing it through main()
    # would instead use the tqdm progress bar, whose carriage-return output is
    # never newline-terminated until completion, so Gooey buffers it and only
    # shows the progress once every file has finished.
    if argv is None and HAS_GOOEY and os.environ.get("GOOEY") == "1":
        launch_gui()
        return

    effective_argv = sys.argv[1:] if argv is None else argv
    if not effective_argv:
        if not HAS_GOOEY:
            raise SystemExit(
                "No command-line arguments were provided and Gooey is unavailable. "
                "Install Gooey or run with CLI arguments."
            )
        launch_gui()
        return

    main(argv=effective_argv)


if __name__ == "__main__":
    entrypoint()
