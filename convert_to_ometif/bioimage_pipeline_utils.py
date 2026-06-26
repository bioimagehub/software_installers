"""Core IO and path utilities for convert-to-ometif.

This module is intentionally lean and only contains helpers used by
convert_to_tif.py and extract_metadata.py.
"""

from typing import Any, Optional, Union
import logging
import os
import sys
import tempfile

import numpy as np
import tifffile
from bioio import BioImage


logger = logging.getLogger(__name__)


def fix_java_home_problem() -> None:
    """Apply the Windows jgo/scyjava parent POM workaround before JVM startup."""
    import jdk4py
    import scyjava.config

    os.environ["JAVA_HOME"] = str(jdk4py.JAVA_HOME)
    scyjava.config.set_java_constraints(fetch="never")

    from pathlib import Path
    import jgo.maven._core as _jgo_core

    def _fixed_pom_parent(self, pom):
        from jgo.maven._pom import POM as _POM

        if pom.element("parent") is None:
            return None
        g = pom.value("parent/groupId")
        a = pom.value("parent/artifactId")
        v = pom.value("parent/version")
        assert g and a and v
        relative_path = pom.value("parent/relativePath")
        if (
            isinstance(pom.source, Path)
            and relative_path
            and (parent_path := pom.source.parent / relative_path).exists()
            and parent_path.is_file()
        ):
            parent_pom = _POM(parent_path)
            if g == parent_pom.groupId and a == parent_pom.artifactId and v == parent_pom.version:
                return parent_pom
        pom_artifact = self.project(g, a).at_version(v).artifact(packaging="pom")
        return _POM(pom_artifact.resolve())

    _jgo_core.MavenContext.pom_parent = _fixed_pom_parent


def _configure_bioformats_safe_io(input_path: str) -> None:
    """Best-effort: prevent Bio-Formats from writing cache files next to source data."""
    if not input_path.lower().endswith((".ims", ".czi", ".lif", ".nd2", ".oib", ".oif")):
        return

    tmp_dir = os.environ.get("BIOFORMATS_MEMO_DIR") or tempfile.gettempdir()

    os.environ.setdefault("BIOFORMATS_DISABLE_MEMOIZATION", "1")
    os.environ.setdefault("OME_BIOFORMATS_MEMOIZER_DISABLED", "1")
    os.environ.setdefault("LOCI_FORMATS_MEMOIZER_DISABLED", "1")
    os.environ.setdefault("BIOFORMATS_MEMO_DIR", tmp_dir)
    os.environ.setdefault("OME_BIOFORMATS_MEMOIZER_DIR", tmp_dir)
    os.environ.setdefault("LOCI_FORMATS_MEMOIZER_DIR", tmp_dir)

    try:
        import scyjava  # type: ignore

        logback_path = os.path.join(tmp_dir, "bioformats-logback.xml")
        if not os.path.exists(logback_path):
            with open(logback_path, "w", encoding="utf-8") as fh:
                fh.write(
                    """
<configuration>
    <contextListener class="ch.qos.logback.classic.jul.LevelChangePropagator">
        <resetJUL>true</resetJUL>
    </contextListener>
    <appender name="STDOUT" class="ch.qos.logback.core.ConsoleAppender">
        <encoder>
            <pattern>%d{HH:mm:ss.SSS} [%thread] %-5level %logger - %msg%n</pattern>
        </encoder>
    </appender>
    <logger name="loci" level="WARN"/>
    <logger name="ome" level="WARN"/>
    <logger name="org.scijava" level="WARN"/>
    <logger name="org.janelia" level="WARN"/>
    <logger name="net.imagej" level="WARN"/>
    <root level="WARN">
        <appender-ref ref="STDOUT"/>
    </root>
</configuration>
""".strip()
                )

        if os.path.exists(logback_path):
            scyjava.config.add_option(f"-Dlogback.configurationFile={logback_path}")

        scyjava.config.add_option("-Dorg.slf4j.simpleLogger.defaultLogLevel=warn")
        scyjava.config.add_option("-Dorg.slf4j.simpleLogger.showDateTime=false")
        scyjava.config.add_option("-Dorg.slf4j.simpleLogger.showThreadName=false")
        scyjava.config.add_option("-Dscijava.log.level=WARN")
        os.environ.setdefault("SCIJAVA_LOG_LEVEL", "WARN")
    except Exception:
        os.environ.setdefault("org.slf4j.simpleLogger.defaultLogLevel", "warn")
        os.environ.setdefault("scijava.log.level", "WARN")


def _build_bioformats_error_message(path: str, original_error: Exception) -> str:
    """Build an actionable Bio-Formats initialization error message."""
    chain_messages = []
    seen = set()
    current: Optional[BaseException] = original_error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain_messages.append(str(current))
        current = current.__cause__ if current.__cause__ is not None else current.__context__

    error_text = "\n".join(chain_messages)
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    is_maven_parent_path_failure = (
        "formats-gpl" in error_text and "Permission denied" in error_text and "..\\.." in error_text
    )

    lines = [
        f"\n{'=' * 70}",
        f"ERROR: Failed to load {os.path.basename(path)}",
        f"{'=' * 70}\n",
        "This file format requires Bio-Formats (Java), but Bio-Formats failed to initialize.",
        f"Python environment: {python_version}",
        f"Original error: {original_error}",
        "",
    ]

    if is_maven_parent_path_failure:
        lines.extend(
            [
                "Detected issue: jgo/scyjava Maven dependency resolution failed on Windows",
                "while resolving Bio-Formats parent POMs (path ending in '.pom\\..\\..').",
                "This is not a missing Java executable issue.",
                "",
                "Recommended fixes (in order):",
                "1. Use Python 3.11 for UV env creation (preferred for Bio-Formats on Windows):",
                "   environment: uv@3.11:convert-to-tif",
                "   or set UV_DEFAULT_PYTHON=3.11 before creating the UV env.",
                "2. If problem persists, use Conda environment:",
                "   conda env create -f conda_envs/convert_to_tif.yml",
                "   and use 'environment: convert_to_tif' in your YAML config.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "Recommended fixes (in order):",
                "1. Recreate this UV env with Python 3.11 (best Bio-Formats compatibility on Windows):",
                "   environment: uv@3.11:convert-to-tif",
                "   or set UV_DEFAULT_PYTHON=3.11 before creating the UV env.",
                "",
                "2. If problem persists, use Conda environment instead of UV:",
                "",
                "   Create Conda environment from: conda_envs/convert_to_tif.yml",
                "   conda env create -f conda_envs/convert_to_tif.yml",
                "",
                "   Run your pipeline with the Conda environment:",
                "   run_pipeline.exe pipeline_configs/your_config.yaml",
                "   (use 'environment: convert_to_tif' in your YAML config)",
                "",
                "NOTE: Most formats (ND2, LIF, CZI, DV, TIFF) work without Bio-Formats.",
                "      Only exotic formats require the Conda environment.",
                "",
            ]
        )

    lines.append(f"{'=' * 70}\n")
    return "\n".join(lines)


def get_default_maxcores() -> int:
    detected_cores = os.cpu_count() or 1
    return max(1, detected_cores - 1)


def resolve_maxcores(maxcores: Optional[int], task_count: Optional[int] = None) -> int:
    resolved = get_default_maxcores() if maxcores is None else maxcores
    if resolved < 1:
        raise ValueError(f"--maxcores must be at least 1, got {resolved}")
    if task_count is not None:
        resolved = min(resolved, task_count)
    return max(1, resolved)


def split_compound_extension(path: str) -> tuple[str, str]:
    lower = path.lower()
    for compound_ext in (".ome.tiff", ".ome.tif"):
        if lower.endswith(compound_ext):
            return path[: -len(compound_ext)], path[-len(compound_ext) :]
    return os.path.splitext(path)


def strip_tiff_suffix(path: str) -> str:
    base, ext = split_compound_extension(path)
    if ext.lower() in {".ome.tif", ".ome.tiff", ".tif", ".tiff"}:
        return base
    return os.path.splitext(path)[0]


def resolve_output_path(path: str, extension: Optional[str], suffix: str = "") -> str:
    base, detected_extension = split_compound_extension(path)

    if extension is None:
        target_extension = detected_extension
    else:
        target_extension = extension
        if target_extension and not target_extension.startswith("."):
            target_extension = f".{target_extension}"

    return f"{base}{suffix}{target_extension}"


def normalize_output_format(output_format: str) -> str:
    fmt = str(output_format).strip().lower()
    aliases = {
        "ome.tif": "ome.tif",
        "tif": "tif",
        "tiff": "tif",
        "npy": "npy",
        "ilastik-h5": "ilastik-h5",
        "h5": "ilastik-h5",
    }
    if fmt not in aliases:
        raise ValueError(f"Unsupported output format: {output_format}")
    return aliases[fmt]


def output_extension_for_format(output_format: str, tiff_extension: str = ".ome.tif") -> str:
    fmt = normalize_output_format(output_format)
    if fmt in {"tif", "ome.tif"}:
        ext = tiff_extension
    elif fmt == "npy":
        ext = ".npy"
    else:
        ext = ".h5"
    if ext and not ext.startswith("."):
        ext = f".{ext}"
    return ext


def save_with_output_format(img: Union[BioImage, np.ndarray], path: str, output_format: str, **kwargs) -> None:
    fmt = normalize_output_format(output_format)
    if fmt in {"tif", "ome.tif"}:
        save_tczyx_image(img, path, **kwargs)
    elif fmt == "npy":
        arr = getattr(img, "data", img)
        np.save(path, np.asarray(arr))
    else:
        save_ilastik_h5(img, path)


def _default_input_dims_order_for_ndim(ndim: int) -> str:
    defaults = {1: "X", 2: "YX", 3: "ZYX", 4: "CZYX", 5: "TCZYX"}
    if ndim not in defaults:
        raise ValueError(f"Unsupported array ndim={ndim}; expected 1-5 dimensions")
    return defaults[ndim]


def _to_tczyx(arr: np.ndarray, input_dims_order: Optional[str] = None) -> np.ndarray:
    if arr.ndim < 1 or arr.ndim > 5:
        raise ValueError(f"Unsupported array shape {arr.shape}; expected 1D-5D array")

    order = _default_input_dims_order_for_ndim(arr.ndim) if input_dims_order is None else input_dims_order.strip().upper()
    if len(order) != arr.ndim:
        raise ValueError(f"input_dims_order '{order}' length must match array ndim {arr.ndim}")
    if any(dim not in "TCZYX" for dim in order):
        raise ValueError(f"input_dims_order '{order}' contains invalid dims; only T,C,Z,Y,X are allowed")
    if len(set(order)) != len(order):
        raise ValueError(f"input_dims_order '{order}' contains duplicate dimensions")

    arr_work = np.asarray(arr)
    current_order = order
    for dim in "TCZYX":
        if dim not in current_order:
            arr_work = np.expand_dims(arr_work, axis=0)
            current_order = dim + current_order

    perm = [current_order.index(dim) for dim in "TCZYX"]
    return np.transpose(arr_work, perm)


def _extract_array_from_npy_payload(payload: Any, path: str) -> np.ndarray:
    if isinstance(payload, np.ndarray) and payload.dtype == object and payload.shape == ():
        payload = payload.item()

    if isinstance(payload, dict):
        for preferred_key in ("masks", "labels", "mask", "segmentation"):
            if preferred_key in payload:
                return np.asarray(payload[preferred_key])
        raise ValueError(
            f"Could not find mask/image array in dict-based npy file '{path}'. "
            f"Available keys: {list(payload.keys())}"
        )

    arr = np.asarray(payload)
    if arr.dtype == object:
        raise ValueError(
            f"Unsupported object array payload in '{path}'. Provide input_dims_order and a numeric array payload."
        )
    return arr


def load_tczyx_image(path: str, input_dims_order: Optional[str] = None) -> BioImage:
    """Load file into a BioImage object in TCZYX-compatible semantics."""
    logger.debug("Loading input file: %s", path)
    if input_dims_order is None:
        env_dims = os.environ.get("RP_INPUT_DIMS_ORDER", "").strip()
        if env_dims:
            input_dims_order = env_dims
            logger.debug("Using RP_INPUT_DIMS_ORDER override: %s", input_dims_order)

    if not os.path.exists(path):
        raise FileNotFoundError(f"File not found: {path}")

    lower_path = path.lower()

    if lower_path.endswith((".tif", ".tiff", ".ome.tif", ".ome.tiff")):
        has_ome_suffix = lower_path.endswith((".ome.tif", ".ome.tiff"))
        is_ome_tiff = False
        try:
            with tifffile.TiffFile(path) as tif:
                is_ome_tiff = bool(getattr(tif, "is_ome", False))
        except Exception:
            pass

        if is_ome_tiff:
            try:
                import bioio_ome_tiff

                logger.debug("Selected reader: bioio_ome_tiff for %s", path)
                return BioImage(path, reader=bioio_ome_tiff.Reader)
            except Exception:
                logger.debug("bioio_ome_tiff reader failed, falling back for %s", path, exc_info=True)
                pass

        tifffile_logger = logging.getLogger("bioio_tifffile.reader")
        previous_tifffile_level = tifffile_logger.level
        if has_ome_suffix:
            tifffile_logger.setLevel(logging.ERROR)
        try:
            import bioio_tifffile

            logger.debug("Selected reader: bioio_tifffile for %s", path)
            return BioImage(path, reader=bioio_tifffile.Reader)
        except Exception:
            logger.debug("bioio_tifffile reader failed, trying generic BioImage for %s", path, exc_info=True)
            pass
        finally:
            if has_ome_suffix:
                tifffile_logger.setLevel(previous_tifffile_level)

        logger.debug("Selected reader: generic BioImage for TIFF path %s", path)
        return BioImage(path)

    if lower_path.endswith(".nd2"):
        import bioio_nd2

        logger.debug("Selected reader: bioio_nd2 for %s", path)
        return BioImage(path, reader=bioio_nd2.Reader)

    if lower_path.endswith(".lif"):
        import bioio_lif

        logger.debug("Selected reader: bioio_lif for %s", path)
        return BioImage(path, reader=bioio_lif.Reader)

    if lower_path.endswith(".czi"):
        import bioio_czi

        logger.debug("Selected reader: bioio_czi for %s", path)
        return BioImage(path, reader=bioio_czi.Reader)

    if lower_path.endswith(".dv"):
        import bioio_dv

        logger.debug("Selected reader: bioio_dv for %s", path)
        return BioImage(path, reader=bioio_dv.Reader)

    if lower_path.endswith(".ims"):
        # Prefer local pure-Python Imaris reader when available,
        # then fall back to Bio-Formats for maximum compatibility.
        try:
            import bioio_imaris

            logger.debug("Selected reader: local bioio_imaris for %s", path)
            return BioImage(path, reader=bioio_imaris.Reader)
        except Exception:
            logger.debug("Local bioio_imaris reader failed for %s, falling back to Bio-Formats", path, exc_info=True)
            fix_java_home_problem()
            _configure_bioformats_safe_io(path)
            try:
                import bioio_bioformats  # type: ignore

                logger.debug("Selected reader: bioio_bioformats fallback for %s", path)
                return BioImage(path, reader=bioio_bioformats.Reader)
            except Exception as exc:
                raise RuntimeError(_build_bioformats_error_message(path, exc)) from exc

    if lower_path.endswith((".h5", ".hdf5")):
        import bioio_ilastik_h5

        logger.debug("Selected reader: local bioio_ilastik_h5 for %s", path)
        return BioImage(path, reader=bioio_ilastik_h5.IlastikH5Reader)

    if lower_path.endswith(".npy"):
        logger.debug("Loading NumPy payload from %s", path)
        payload = np.load(path, allow_pickle=True)
        arr = _extract_array_from_npy_payload(payload, path)
        return BioImage(_to_tczyx(arr, input_dims_order=input_dims_order))

    if lower_path.endswith(".npz"):
        logger.debug("Loading NPZ payload from %s", path)
        with np.load(path, allow_pickle=True) as archive:
            selected_key = None
            for preferred_key in ("masks", "labels", "mask", "segmentation"):
                if preferred_key in archive:
                    selected_key = preferred_key
                    break
            if selected_key is None:
                keys = list(archive.keys())
                if not keys:
                    raise ValueError(f"Empty npz archive: {path}")
                selected_key = keys[0]
            arr = _extract_array_from_npy_payload(archive[selected_key], path)
        return BioImage(_to_tczyx(arr, input_dims_order=input_dims_order))

    fix_java_home_problem()
    _configure_bioformats_safe_io(path)
    try:
        import bioio_bioformats  # type: ignore

        logger.debug("Selected reader: bioio_bioformats generic fallback for %s", path)
        return BioImage(path, reader=bioio_bioformats.Reader)
    except Exception as exc:
        raise RuntimeError(_build_bioformats_error_message(path, exc)) from exc


def save_tczyx_image(img: Union[BioImage, np.ndarray], path: str, **kwargs) -> None:
    """Save a BioImage or ndarray as OME-TIFF in TCZYX order."""
    try:
        from bioio.writers import OmeTiffWriter
    except ImportError:
        from bioio_ome_tiff import OmeTiffWriter

    arr = getattr(img, "data", img)
    arr = np.asarray(arr)
    while arr.ndim < 5:
        arr = arr[np.newaxis, ...]

    kwargs.pop("dim_order", None)

    ome_xml = None
    if isinstance(img, BioImage):
        if hasattr(img, "ome_xml") and img.ome_xml is not None:
            ome_xml = img.ome_xml
        elif hasattr(img, "metadata") and isinstance(img.metadata, dict):
            ome_xml = img.metadata.get("ome_xml", None)

    if os.path.exists(path):
        os.remove(path)

    if ome_xml is not None:
        OmeTiffWriter.save(arr, path, dim_order="TCZYX", ome_xml=ome_xml, **kwargs)
    else:
        OmeTiffWriter.save(arr, path, dim_order="TCZYX", **kwargs)


def save_ilastik_h5(img: Union[BioImage, np.ndarray], path: str, dataset_name: str = "exported_data") -> None:
    """Save TCZYX input to Ilastik-compatible H5 as TZYXC with axis tags."""
    try:
        import h5py
        import json
    except Exception as exc:
        raise ImportError("Saving ilastik-h5 requires h5py") from exc

    arr = getattr(img, "data", img)
    arr = np.asarray(arr)
    while arr.ndim < 5:
        arr = arr[np.newaxis, ...]

    arr_tzyxc = np.transpose(arr, (0, 2, 3, 4, 1))
    axis_configs = [
        {"key": "t", "typeFlags": 8, "resolution": 0, "description": ""},
        {"key": "z", "typeFlags": 2, "resolution": 0, "description": ""},
        {"key": "y", "typeFlags": 2, "resolution": 0, "description": ""},
        {"key": "x", "typeFlags": 2, "resolution": 0, "description": ""},
        {"key": "c", "typeFlags": 1, "resolution": 0, "description": ""},
    ]

    if os.path.exists(path):
        os.remove(path)

    with h5py.File(path, "w") as file_handle:
        dataset = file_handle.create_dataset(dataset_name, data=arr_tzyxc)
        dataset.attrs["axistags"] = json.dumps({"axes": axis_configs})


def collapse_filename(file_path: str, base_folder: str, delimiter: str = "__") -> str:
    """Flatten a path relative to base_folder by replacing separators with delimiter."""
    rel_path = os.path.relpath(file_path, start=base_folder)
    return delimiter.join(rel_path.split(os.sep))
