from typing import Union, Optional, Any
from bioio import BioImage
import os
import tempfile
#import bioio_ome_tiff, bioio_tifffile, bioio_nd2, bioio_lif, bioio_czi, bioio_dv # done when needed
import numpy as np
import warnings
import sys
import logging

import tifffile



# Suppress all cryptography-related warnings (TripleDES, Blowfish deprecations from paramiko)
warnings.filterwarnings('ignore', category=Warning)
warnings.simplefilter('ignore')




def fix_java_home_problem():
    import jdk4py
    import scyjava.config

    # Point JAVA_HOME at jdk4py's bundled OpenJDK 21 (must happen before JVM starts).
    os.environ["JAVA_HOME"] = str(jdk4py.JAVA_HOME)
    # Prevent scyjava from overriding JAVA_HOME with its own (broken) JVM finder.
    scyjava.config.set_java_constraints(fetch="never")

    # Workaround for jgo 2.1.2 bug on Windows: relativePath in a POM is relative to
    # the POM's *directory*, not the POM file path itself. Without this, jgo tries
    # to parse a directory as XML and raises PermissionError.
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
        relativePath = pom.value("parent/relativePath")
        if (
            isinstance(pom.source, Path)
            and relativePath
            and (parent_path := pom.source.parent / relativePath).exists()
            and parent_path.is_file()
        ):
            parent_pom = _POM(parent_path)
            if g == parent_pom.groupId and a == parent_pom.artifactId and v == parent_pom.version:
                return parent_pom
        pom_artifact = self.project(g, a).at_version(v).artifact(packaging="pom")
        return _POM(pom_artifact.resolve())
    _jgo_core.MavenContext.pom_parent = _fixed_pom_parent





def _configure_bioformats_safe_io(input_path: str) -> None:
    """Best-effort: prevent Bio-Formats from touching the source folder.

    Bio-Formats may write .bfmemo cache files next to inputs (e.g., .ims).
    We try to disable memoization or at least redirect it to a temp dir.
    """
    # Only apply for file types typically handled by Bio-Formats where sidecar writes are common
    if not input_path.lower().endswith((".ims", ".czi", ".lif", ".nd2", ".oib", ".oif")):
        return

    tmp_dir = os.environ.get("BIOFORMATS_MEMO_DIR", None) or tempfile.gettempdir()

    # Set a variety of known env vars / system properties consulted by wrappers
    os.environ.setdefault("BIOFORMATS_DISABLE_MEMOIZATION", "1")
    os.environ.setdefault("OME_BIOFORMATS_MEMOIZER_DISABLED", "1")
    os.environ.setdefault("LOCI_FORMATS_MEMOIZER_DISABLED", "1")
    os.environ.setdefault("BIOFORMATS_MEMO_DIR", tmp_dir)
    os.environ.setdefault("OME_BIOFORMATS_MEMOIZER_DIR", tmp_dir)
    os.environ.setdefault("LOCI_FORMATS_MEMOIZER_DIR", tmp_dir)

    # Reduce Java-side log verbosity (SLF4J/Logback/SciJava) before JVM starts
    try:
        import scyjava  # type: ignore
        # Prepare a minimal Logback configuration to clamp logs to WARN.
        logback_path = os.path.join(tmp_dir, "bioformats-logback.xml")
        if not os.path.exists(logback_path):
            try:
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
            except Exception:
                # If writing fails, continue with system properties below
                pass

        # Force Logback to use our configuration if available
        if os.path.exists(logback_path):
            scyjava.config.add_option(f"-Dlogback.configurationFile={logback_path}")

        # SLF4J simple logger (harmless if not the active binding)
        scyjava.config.add_option("-Dorg.slf4j.simpleLogger.defaultLogLevel=warn")
        scyjava.config.add_option("-Dorg.slf4j.simpleLogger.showDateTime=false")
        scyjava.config.add_option("-Dorg.slf4j.simpleLogger.showThreadName=false")
        # SciJava logger level
        scyjava.config.add_option("-Dscijava.log.level=WARN")
        # Fallback envs in case properties aren’t picked up by the active binding
        os.environ.setdefault("SCIJAVA_LOG_LEVEL", "WARN")
    except Exception:
        # If scyjava isn't available yet, the properties below may still be picked up via env when JVM starts
        os.environ.setdefault("org.slf4j.simpleLogger.defaultLogLevel", "warn")
        os.environ.setdefault("scijava.log.level", "WARN")


def _build_bioformats_error_message(path: str, original_error: Exception) -> str:
    """Create a detailed, actionable Bio-Formats initialization error message."""
    chain_messages = []
    seen = set()
    current: Optional[BaseException] = original_error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain_messages.append(str(current))
        next_exc = current.__cause__ if current.__cause__ is not None else current.__context__
        current = next_exc

    error_text = "\n".join(chain_messages)
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    is_maven_parent_path_failure = (
        "formats-gpl" in error_text and "Permission denied" in error_text and "..\\.." in error_text
    )

    lines = [
        f"\n{'='*70}",
        f"ERROR: Failed to load {os.path.basename(path)}",
        f"{'='*70}\n",
        "This file format requires Bio-Formats (Java), but Bio-Formats failed to initialize.",
        f"Python environment: {python_version}",
        f"Original error: {original_error}",
        "",
    ]

    if is_maven_parent_path_failure:
        lines.extend([
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
        ])
    else:
        lines.extend([
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
        ])

    lines.append(f"{'='*70}\n")
    return "\n".join(lines)


def get_default_maxcores() -> int:
    """Return the standard default core count for parallel CLI work."""
    detected_cores = os.cpu_count() or 1
    return max(1, detected_cores - 1)


def resolve_maxcores(maxcores: Optional[int], task_count: Optional[int] = None) -> int:
    """Resolve a user-provided maxcores value against defaults and task count."""
    resolved = get_default_maxcores() if maxcores is None else maxcores
    if resolved < 1:
        raise ValueError(f"--maxcores must be at least 1, got {resolved}")
    if task_count is not None:
        resolved = min(resolved, task_count)
    return max(1, resolved)


def split_compound_extension(path: str) -> tuple[str, str]:
    """Split path into stem and extension, preserving compound OME-TIFF suffixes."""
    lower = path.lower()
    for compound_ext in (".ome.tiff", ".ome.tif"):
        if lower.endswith(compound_ext):
            return path[:-len(compound_ext)], path[-len(compound_ext):]
    return os.path.splitext(path)


def strip_tiff_suffix(path: str) -> str:
    """Return path without a trailing TIFF suffix (.ome.tif/.ome.tiff/.tif/.tiff)."""
    base, ext = split_compound_extension(path)
    if ext.lower() in {".ome.tif", ".ome.tiff", ".tif", ".tiff"}:
        return base
    return os.path.splitext(path)[0]


def resolve_output_path(path: str, extension: Optional[str], suffix: str = "") -> str:
    """Resolve output path by replacing extension and inserting suffix before extension.

    OME-TIFF compound suffixes are treated as a single extension, so:
    - ``file.ome.tif`` + ``suffix='_x'`` + ``extension='.h5'`` -> ``file_x.h5``
    - ``file.ome.tif`` + ``suffix='_x'`` + ``extension='.ome.tif'`` -> ``file_x.ome.tif``
    """
    base, detected_extension = split_compound_extension(path)

    if extension is None:
        target_extension = detected_extension
    else:
        target_extension = extension
        if target_extension and not target_extension.startswith("."):
            target_extension = f".{target_extension}"

    return f"{base}{suffix}{target_extension}"


def resolve_output_suggix(path: str, extension: Optional[str], suffix: str = "") -> str:
    """Backward-compatible alias for resolve_output_path (keeps historical typo)."""
    return resolve_output_path(path, extension, suffix)


def normalize_output_format(output_format: str) -> str:
    """Normalize output-format aliases to canonical values."""
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
    """Return file extension for a normalized output format."""
    fmt = normalize_output_format(output_format)
    if fmt in {"tif", "ome.tif"}:
        ext = tiff_extension
    elif fmt == "npy":
        ext = ".npy"
    else:  # ilastik-h5
        ext = ".h5"
    if ext and not ext.startswith("."):
        ext = f".{ext}"
    return ext


def save_with_output_format(img: Union[BioImage, np.ndarray], path: str, output_format: str, **kwargs) -> None:
    """Save output using a standardized output-format switch.

    Supported formats: ``tif``/``ome.tif``, ``npy``, ``ilastik-h5``.
    Extra kwargs are passed only to TIFF writer and ignored for npy/h5.
    """
    fmt = normalize_output_format(output_format)
    if fmt in {"tif", "ome.tif"}:
        save_tczyx_image(img, path, **kwargs)
    elif fmt == "npy":
        arr = getattr(img, 'data', img)
        np.save(path, np.asarray(arr))
    else:
        save_ilastik_h5(img, path)


def _default_input_dims_order_for_ndim(ndim: int) -> str:
    """Return conservative defaults for array-only formats."""
    defaults = {
        1: "X",
        2: "YX",
        3: "ZYX",
        4: "CZYX",
        5: "TCZYX",
    }
    if ndim not in defaults:
        raise ValueError(f"Unsupported array ndim={ndim}; expected 1-5 dimensions")
    return defaults[ndim]


def _to_tczyx(arr: np.ndarray, input_dims_order: Optional[str] = None) -> np.ndarray:
    """Convert array from user-provided order into TCZYX."""
    if arr.ndim < 1 or arr.ndim > 5:
        raise ValueError(f"Unsupported array shape {arr.shape}; expected 1D-5D array")

    if input_dims_order is None:
        order = _default_input_dims_order_for_ndim(arr.ndim)
    else:
        order = input_dims_order.strip().upper()

    if len(order) != arr.ndim:
        raise ValueError(
            f"input_dims_order '{order}' length must match array ndim {arr.ndim}"
        )
    if any(dim not in "TCZYX" for dim in order):
        raise ValueError(
            f"input_dims_order '{order}' contains invalid dims; only T,C,Z,Y,X are allowed"
        )
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
    """Extract image/mask array from npy payload, including Cellpose dict outputs."""
    if isinstance(payload, np.ndarray) and payload.dtype == object and payload.shape == ():
        payload = payload.item()

    if isinstance(payload, dict):
        # Cellpose *_seg.npy stores a dict with a 'masks' key.
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


def load_tczyx_image(path: str, input_dims_order: Optional[str] = None):
    """
    Load an image as a BioImage object, ensuring the data is always 5D (TCZYX).
    The file format is determined by the file extension. This function standardizes
    all images to TCZYX order for safe downstream processing.


    example use:
    from bioio import BioImage

    # Get a BioImage object
    img = BioImage("my_file.tiff")  # selects the first scene found
    img.data  # returns 5D TCZYX numpy array
    img.xarray_data  # returns 5D TCZYX xarray data array backed by numpy
    img.dims  # returns a Dimensions object
    img.dims.order  # returns string "TCZYX"
    img.dims.X  # returns size of X dimension
    img.shape  # returns tuple of dimension sizes in TCZYX order
    img.get_image_data("CZYX", T=0)  # returns 4D CZYX numpy array

    """
    # Optional global override so existing CLIs can support array dim remapping
    # without adding new argparse flags in every module.
    if input_dims_order is None:
        env_dims = os.environ.get("RP_INPUT_DIMS_ORDER", "").strip()
        if env_dims:
            input_dims_order = env_dims

    # Load the image using the appropriate reader based on the file extension
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
            # If detection fails, continue with generic reader fallback below.
            pass

        # If filename uses explicit OME suffix, prefer the OME reader first,
        # but only when TIFF metadata confirms it is actually OME.
        # This avoids noisy "Exclusive reader attempt failed" logs when files
        # are named *.ome.tif but contain non-OME ImageJ-style payloads.
        if has_ome_suffix and is_ome_tiff:
            try:
                import bioio_ome_tiff
                img = BioImage(path, reader=bioio_ome_tiff.Reader)
                return img
            except Exception:
                pass

        # Only try the OME reader when the TIFF is actually OME-TIFF.
        # This avoids noisy "Failed to parse XML" errors for ImageJ-style TIFFs.
        if is_ome_tiff and not has_ome_suffix:
            try:
                import bioio_ome_tiff
                img = BioImage(path, reader=bioio_ome_tiff.Reader)
                return img
            except Exception:
                pass

        # Prefer tifffile reader for non-OME TIFFs (e.g., ImageJ save_mask output).
        # For files named *.ome.tif(f) that are not valid OME XML internally,
        # bioio_tifffile emits a repetitive install hint even when plugin exists.
        # We already attempted bioio-ome-tiff above, so mute only that specific warning path.
        tifffile_logger = logging.getLogger("bioio_tifffile.reader")
        previous_tifffile_level = tifffile_logger.level
        if has_ome_suffix:
            tifffile_logger.setLevel(logging.ERROR)
        try:
            import bioio_tifffile
            img = BioImage(path, reader=bioio_tifffile.Reader)
            return img
        except Exception:
            pass
        finally:
            if has_ome_suffix:
                tifffile_logger.setLevel(previous_tifffile_level)

        # Final generic fallback
        try:
            img = BioImage(path)
            return img
        except Exception:
            pass
    elif lower_path.endswith(".nd2"):
        import bioio_nd2
        img = BioImage(path, reader=bioio_nd2.Reader)
        return img
    elif lower_path.endswith(".lif"):
        import bioio_lif
        img = BioImage(path, reader=bioio_lif.Reader)
        return img
    elif lower_path.endswith(".czi"):
        import bioio_czi
        img = BioImage(path, reader=bioio_czi.Reader)
        return img
    elif lower_path.endswith(".dv"):
        import bioio_dv
        img = BioImage(path, reader=bioio_dv.Reader)
        return img
    elif lower_path.endswith((".h5", ".hdf5")):
        import bioio_ilastik_h5
        img = BioImage(path, reader=bioio_ilastik_h5.IlastikH5Reader)
        return img
    elif lower_path.endswith(".npy"):
        payload = np.load(path, allow_pickle=True)
        arr = _extract_array_from_npy_payload(payload, path)
        arr_tczyx = _to_tczyx(arr, input_dims_order=input_dims_order)
        return BioImage(arr_tczyx)
    elif lower_path.endswith(".npz"):
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

        arr_tczyx = _to_tczyx(arr, input_dims_order=input_dims_order)
        return BioImage(arr_tczyx)
    # The bioformats-based reader is now the universal fallback for any format, including .ims, to ensure maximum compatibility.
    # elif lower_path.endswith(".ims"):
    #     # Try custom bioio_imaris reader first (faster, pure Python)
    #     try:
    #         from standard_code.python.bioio_imaris import Reader as ImarisReader
    #         img = BioImage(path, reader=ImarisReader)
    #         return img
    #     except Exception:
    #         pass
    #     # Fall back to Bio-Formats if bioio_imaris fails
    #     _configure_bioformats_safe_io(path)
    #     try:
    #         import bioio_bioformats  # type: ignore
    #         img = BioImage(path, reader=bioio_bioformats.Reader)
    #         return img
    #     except Exception as e:
    #         raise RuntimeError(
    #             f"\n{'='*70}\n"
    #             f"ERROR: Failed to load {os.path.basename(path)}\n"
    #             f"{'='*70}\n\n"
    #             f"This file format requires Bio-Formats (Java), which failed to initialize.\n"
    #             f"Original error: {e}\n\n"
    #             f"SOLUTION: Use Conda environment instead of UV for Bio-Formats support:\n\n"
    #             f"1. Create Conda environment from: conda_envs/convert_to_tif.yml\n"
    #             f"   conda env create -f conda_envs/convert_to_tif.yml\n\n"
    #             f"2. Run your pipeline with the Conda environment:\n"
    #             f"   run_pipeline.exe pipeline_configs/your_config.yaml\n"
    #             f"   (use 'environment: convert_to_tif' in your YAML config)\n\n"
    #             f"NOTE: Most formats (ND2, LIF, CZI, DV, TIFF) work without Bio-Formats.\n"
    #             f"      Only exotic formats require the Conda environment.\n"
    #             f"{'='*70}\n"
    #         ) from e
    else:
        import jdk4py
        import scyjava.config
        # Apply Java fixes at import time, before any code can accidentally trigger JVM startup.
        # TODO make this only run once!
        fix_java_home_problem()

        # Unknown format - try Bio-Formats as last resort
        _configure_bioformats_safe_io(path)
        try:
            import bioio_bioformats  # type: ignore
            img = BioImage(path, reader=bioio_bioformats.Reader)
            return img
        except Exception as e:
            raise RuntimeError(_build_bioformats_error_message(path, e)) from e
    raise ValueError(f"Unsupported file format for: {path}")

# Deprecated alias for backward compatibility
def load_bioio(path: str) -> BioImage:
    import warnings
    warnings.warn("load_bioio is deprecated, use load_tczyx_image instead.", DeprecationWarning)
    return load_tczyx_image(path)


def save_tczyx_image(img: Union[BioImage, np.ndarray], path: str, **kwargs) -> None:
    """
    Save a BioImage or numpy array to disk as OME-TIFF, ensuring TCZYX order.
    This function should be used for all image saving to guarantee consistency.
    """
    try:
        from bioio.writers import OmeTiffWriter
    except ImportError:
        from bioio_ome_tiff import OmeTiffWriter
    # If BioImage, extract .data; if np.ndarray, use as is
    arr = getattr(img, 'data', img)
    # Ensure 5D TCZYX
    import numpy as np
    arr = np.asarray(arr)
    while arr.ndim < 5:
        arr = arr[np.newaxis, ...]
    # Remove dim_order from kwargs if present to avoid multiple values error
    if "dim_order" in kwargs:
        kwargs.pop("dim_order")

    ome_xml = None
    # Only try to preserve OME-XML if input is BioImage
    from bioio import BioImage
    if isinstance(img, BioImage):
        if hasattr(img, 'ome_xml') and img.ome_xml is not None:
            ome_xml = img.ome_xml
        elif hasattr(img, 'metadata') and isinstance(img.metadata, dict):
            ome_xml = img.metadata.get('ome_xml', None)

    # force overwrite if file exists
    if os.path.exists(path):
        os.remove(path)
    if ome_xml is not None:

        OmeTiffWriter.save(arr, path, dim_order="TCZYX", ome_xml=ome_xml, **kwargs)
    else:
        OmeTiffWriter.save(arr, path, dim_order="TCZYX", **kwargs)


def save_ilastik_h5(img: Union[BioImage, np.ndarray], path: str, dataset_name: str = "exported_data") -> None:
    """Save image data as Ilastik-compatible HDF5 in TZYXC order.

    Input is expected to be TCZYX (or lower dimensional data that can be expanded
    to TCZYX). The HDF5 dataset is written as TZYXC with VIGRA-style ``axistags``.
    """
    try:
        import h5py
        import json
    except Exception as exc:
        raise ImportError("Saving ilastik-h5 requires h5py") from exc

    arr = getattr(img, 'data', img)
    arr = np.asarray(arr)
    while arr.ndim < 5:
        arr = arr[np.newaxis, ...]

    # Ilastik expects channel-last tensors. Convert TCZYX -> TZYXC.
    arr_tzyxc = np.transpose(arr, (0, 2, 3, 4, 1))

    axis_configs = [
        {'key': 't', 'typeFlags': 8, 'resolution': 0, 'description': ''},
        {'key': 'z', 'typeFlags': 2, 'resolution': 0, 'description': ''},
        {'key': 'y', 'typeFlags': 2, 'resolution': 0, 'description': ''},
        {'key': 'x', 'typeFlags': 2, 'resolution': 0, 'description': ''},
        {'key': 'c', 'typeFlags': 1, 'resolution': 0, 'description': ''}
    ]

    if os.path.exists(path):
        os.remove(path)

    with h5py.File(path, 'w') as f:
        dset = f.create_dataset(dataset_name, data=arr_tzyxc)
        dset.attrs['axistags'] = json.dumps({'axes': axis_configs})

# Deprecated alias for backward compatibility
def save_bioio(img, path, **kwargs):
    import warnings
    warnings.warn("save_bioio is deprecated, use save_tczyx_image instead.", DeprecationWarning)
    return save_tczyx_image(img, path, **kwargs)

def get_files_to_process(folder_path: str, extension: str, search_subfolders: bool) -> list:
    """
    Get a list of files in the specified folder with the specified extension.
    WARNING: This function will be deprecated in the future. Please use get_files_to_process2 with a glob pattern instead.
    """
    import warnings
    warnings.warn("get_files_to_process will be deprecated in the future. Please use get_files_to_process2 with a glob pattern instead.", FutureWarning)
    files_to_process = []
    if search_subfolders:
        for dirpath, _, filenames in os.walk(folder_path):
            for filename in filenames:
                if filename.endswith(extension):
                    files_to_process.append(os.path.join(dirpath, filename))
    else:
        with os.scandir(folder_path) as it:
            for entry in it:
                if entry.is_file() and entry.name.endswith(extension):
                    files_to_process.append(entry.path)
    files_to_process = [file_path.replace("\\", "/") for file_path in files_to_process]
    files_to_process = sorted(files_to_process)
    return files_to_process

def get_files_to_process2(search_pattern: str, search_subfolders: bool) -> list:
    """
    Get a list of files matching a glob pattern. Example: 'folder/*.tif' or 'folder/somefile*.tif'.
    If search_subfolders is True, will use '**' for recursive search if not already present in the pattern.
    Returns a sorted list of file paths with forward slashes.
    """
    import glob
    import os
    # If recursive search requested and not already in pattern, add '**/'
    if search_subfolders and '**' not in search_pattern:
        parts = os.path.split(search_pattern)
        search_pattern = os.path.join(parts[0], '**', parts[1])
    files_to_process = glob.glob(search_pattern, recursive=search_subfolders)
    files_to_process = [file_path.replace("\\", "/") for file_path in files_to_process]
    files_to_process = sorted(files_to_process)
    return files_to_process

def collapse_filename(file_path: str, base_folder: str, delimiter: str = "__") -> str:
    """
    Collapse a full file path into a single string filename that encodes the relative
    path structure using a custom delimiter.

    Parameters:
    - file_path: The full path to the file.
    - base_folder: The base directory from which the relative path is derived.
    - delimiter: The string used to replace path separators (default: "__").

    Returns:
    - A string that represents the relative path as a flat filename, with
      directory separators replaced by the delimiter.
    """
    # Compute the path relative to the base folder
    rel_path = os.path.relpath(file_path, start=base_folder)
    
    # Replace os-specific separators with the chosen delimiter
    collapsed = delimiter.join(rel_path.split(os.sep))
    
    return collapsed

def uncollapse_filename(collapsed: str, base_folder: str, delimiter: str = "__") -> str:
    """
    Reconstruct the original file path from a collapsed filename.
    
    Parameters:
    - collapsed: The collapsed filename string.
    - base_folder: The base directory to prepend to the reconstructed path.
    - delimiter: The delimiter used in the collapse_filename function.
    
    Returns:
    - The reconstructed original file path.
    """
    parts = collapsed.split(delimiter)
    rel_path = os.path.join(*parts)

    original_path:str = os.path.join(base_folder, rel_path)
    return original_path

# ...existing code...

def get_grouped_files_to_process(
    search_patterns: dict[str, str],
    search_subfolders: bool
) -> dict[str, dict[str, str]]:
    """
    Group files from multiple search patterns by their common basename.
    
    This function finds files matching multiple glob patterns and groups them by
    the portion of the filename that matches the '*' wildcard. This is useful when
    you have related files with different suffixes or in different locations.
    
    Parameters:
    -----------
    search_patterns : dict[str, str]
        Dictionary mapping pattern names to glob patterns. The patterns should
        contain a '*' wildcard that will be used for matching. The pattern name
        becomes the key in the nested result dictionary.
        Example: {
            'image': 'input/*.tif',
            'mask': 'masks/*_mask.tif',
            'tracking': 'tracking/*_tracked.tif'
        }
    
    search_subfolders : bool
        If True, searches recursively using '**' pattern.
    
    Returns:
    --------
    dict[str, dict[str, str]]
        Nested dictionary where:
        - Outer key: basename (the part matching '*')
        - Inner key: pattern name (from input dict)
        - Inner value: full file path
        
        Example result: {
            'image001': {
                'image': 'input/image001.tif',
                'mask': 'masks/image001_mask.tif',
                'tracking': 'tracking/image001_tracked.tif'
            },
            'image002': {
                'image': 'input/image002.tif',
                'mask': 'masks/image002_mask.tif'
            }
        }
    
    Raises:
    -------
    ValueError
        If any pattern doesn't contain a '*' wildcard
        If pattern names are duplicated
    
    Notes:
    ------
    - Files are only included in groups where the basename matches
    - A group may have missing patterns if no matching file is found
    - Use this when you need to process related files together
    
    Examples:
    ---------
    >>> patterns = {
    ...     'image': './input/*.tif',
    ...     'mask': './masks/*_mask.tif'
    ... }
    >>> groups = get_grouped_files_to_process(patterns, search_subfolders=False)
    >>> for basename, files in groups.items():
    ...     if 'image' in files and 'mask' in files:
    ...         process_pair(files['image'], files['mask'])
    """
    import re
    import os
    
    # Validate inputs
    if not search_patterns:
        raise ValueError("search_patterns dictionary cannot be empty")
    
    # Check for duplicate pattern names
    if len(search_patterns) != len(set(search_patterns.keys())):
        raise ValueError("Pattern names must be unique")
    
    # Validate all patterns have '*'
    for name, pattern in search_patterns.items():
        if '*' not in pattern:
            raise ValueError(f"Pattern '{name}' must contain a '*' wildcard: {pattern}")
    
    # For each pattern, find files and extract basename
    pattern_files = {}  # pattern_name -> [(basename, full_path), ...]
    
    for pattern_name, pattern in search_patterns.items():
        files = get_files_to_process2(pattern, search_subfolders)
        
        # Get just the pattern filename part (not directory)
        pattern_filename = os.path.basename(pattern)
        
        # Find the first '*' in the pattern filename to extract basename
        # This handles patterns with multiple wildcards like '*_suffix*.ext'
        first_star_idx = pattern_filename.find('*')
        
        # Get prefix (before first *) and what comes after
        prefix = pattern_filename[:first_star_idx]
        after_first_star = pattern_filename[first_star_idx + 1:]
        
        # Find the next '*' in after_first_star to determine the anchor string
        second_star_idx = after_first_star.find('*')
        if second_star_idx == -1:
            # No second star, anchor is everything after first star
            anchor = after_first_star
        else:
            # Second star exists, anchor is the fixed part between first and second star
            anchor = after_first_star[:second_star_idx]
        
        basenames_and_paths = []
        for file_path in files:
            filename = os.path.basename(file_path)
            
            # Remove prefix if present
            working_name = filename
            if prefix:
                if not working_name.startswith(prefix):
                    # Prefix doesn't match, skip this file
                    continue
                working_name = working_name[len(prefix):]
            
            # Find where anchor starts in the remaining part
            if anchor:
                anchor_idx = working_name.find(anchor)
                if anchor_idx == -1:
                    # Anchor not found, skip this file
                    continue
                basename = working_name[:anchor_idx]
            else:
                # No anchor (pattern ends with *), use everything before extension
                basename = os.path.splitext(working_name)[0]
            
            basenames_and_paths.append((basename, file_path))
        
        pattern_files[pattern_name] = basenames_and_paths
    
    # Group by basename
    grouped: dict[str, dict[str, str]] = {}
    
    for pattern_name, basename_path_list in pattern_files.items():
        for basename, file_path in basename_path_list:
            if basename not in grouped:
                grouped[basename] = {}
            grouped[basename][pattern_name] = file_path
    
    # Sort by basename for consistency
    grouped = dict(sorted(grouped.items()))
    
    return grouped



def split_comma_separated_strstring(value:str) -> list[str]:
    return list(map(str, value.split(',')))    

def split_comma_separated_intstring(value:str) -> list[int]:
    return list(map(int, value.split(',')))    


def mask_to_rois(mask: np.ndarray):
    """
    Convert a labeled mask (indexed image, TCZYX or lower) to a list of ImageJ ROI objects.
    Each unique label (except 0) in each (T, C, Z) plane is converted to a ROI.
    """
    from skimage import measure
    from roifile import ImagejRoi, roiwrite

    
    rois = []
    shape = mask.shape
    # Pad shape to 5D if needed
    while len(shape) < 5:
        mask = np.expand_dims(mask, axis=0)
        shape = mask.shape
    T, C, Z, Y, X = mask.shape
    for t in range(T):
        for c in range(C):
            for z in range(Z):
                plane = mask[t, c, z]
                labels = np.unique(plane)
                for label in labels:
                    if label == 0:
                        continue
                    mask_bin = (plane == label).astype(np.uint8)
                    contours = measure.find_contours(mask_bin, 0.5)
                    for contour in contours:
                        coords = np.fliplr(contour).astype(np.int16)
                        if len(coords) < 3:
                            continue
                        roi = ImagejRoi.frompoints(coords)
                        rois.append(roi)
    return rois


def show_image(
    image: Union[BioImage, np.ndarray, str],
    mask: Union[BioImage, np.ndarray, str, None] = None,
    title: Optional[str] = None,
    alpha: float = 0.3,
    timer: float = -1,
    show_area_chart: Optional[bool] = None
) -> None:
    """
    Quick visualization of mask segmentation over time.
    
    Layout:
    - Top row: First and last timepoint overlays (max Z projection)
    - Bottom: Stacked area chart showing pixel counts per object ID across all timepoints
    
    Args:
        image: BioImage, numpy array (TCZYX), or path to image file
        mask: Optional mask as BioImage, numpy array (TCZYX), or path to mask file
        title: Optional title for the figure. If None, uses image filename if available.
        alpha: Transparency of mask overlay (0-1, default: 0.3)
        timer: Duration to show the plot in seconds. If -1 (default), blocks until user closes.
               If > 0, auto-closes after the specified duration.
        show_area_chart: Whether to include the area chart in the visualization. 
                         If None (default), automatically includes area chart only if T>1.
                         Set to True to force area chart display, False to skip it (shows only image overlays).
    
    Examples:
        >>> show_image("path/to/image.tif", mask="path/to/mask.tif")  # Auto-show area chart if T>1
        >>> show_image(img_array, mask=mask_array, show_area_chart=True)  # Force area chart even if T=1
        >>> show_image(img, mask=mask, show_area_chart=False)  # Skip area chart, show only overlays
        >>> show_image(img, mask=mask, timer=1.0)  # Auto-close after 1 second
    """
    import matplotlib.pyplot as plt
    from matplotlib.colors import ListedColormap
    from pathlib import Path
    
    # Load image if it's a path
    if isinstance(image, str):
        image_path = image
        image = load_tczyx_image(image)
    else:
        image_path = None
    
    # Convert to numpy array if BioImage
    if hasattr(image, 'data'):
        img_data = np.asarray(image.data)  # Force conversion from memoryview to numpy array
    else:
        img_data = np.asarray(image)
    
    # Ensure 5D
    while img_data.ndim < 5:
        img_data = img_data[np.newaxis, ...]
    
    T, C, Z, Y, X = img_data.shape
    
    # Auto-determine whether to show area chart: default is show if T > 1
    if show_area_chart is None:
        show_area_chart = (T > 1)
    
    # Always show the plot - show_area_chart only controls the bottom chart panel
    # (This used to skip display entirely, but that was confusing)
    
    # Load and process mask if provided
    mask_data = None
    mT = T  # Default to image timepoints
    if mask is not None:
        if isinstance(mask, str):
            mask = load_tczyx_image(mask)
        
        if hasattr(mask, 'data'):
            mask_data = np.asarray(mask.data)  # Force conversion from memoryview to numpy array
        else:
            mask_data = np.asarray(mask)
        
        # Ensure 5D
        while mask_data.ndim < 5:
            mask_data = mask_data[np.newaxis, ...]
        
        mT, mC, mZ, mY, mX = mask_data.shape
        
        # Validate dimensions
        if (Y, X) != (mY, mX):
            raise ValueError(f"Image and mask XY dimensions must match. Got image: ({Y}, {X}), mask: ({mY}, {mX})")
    
    # Create figure layout based on whether we have mask data and area chart
    if mask_data is not None and show_area_chart:
        # With mask and area chart: 2x2 grid (top: first/last timepoint, bottom: area chart)
        fig = plt.figure(figsize=(10, 7))
        gs = fig.add_gridspec(2, 2, height_ratios=[1, 1], hspace=0.3, wspace=0.2)
        ax_first = fig.add_subplot(gs[0, 0])
        ax_last = fig.add_subplot(gs[0, 1])
        ax_chart = fig.add_subplot(gs[1, :])
    else:
        # Without mask or without area chart: just show first and last timepoint side by side
        fig, (ax_first, ax_last) = plt.subplots(1, 2, figsize=(10, 4))
        ax_chart = None
    
    # Helper function to create distinct colors for each label
    def get_label_colors(max_label: int) -> dict:
        """Generate distinct colors for each label ID."""
        np.random.seed(42)
        colors = {}
        for label_id in range(1, max_label + 1):
            hue = (label_id * 0.618033988749895) % 1.0  # Golden ratio for good distribution
            h = hue * 6
            x = 1 - abs((h % 2) - 1)
            
            if h < 1:
                r, g, b = 1, x, 0
            elif h < 2:
                r, g, b = x, 1, 0
            elif h < 3:
                r, g, b = 0, 1, x
            elif h < 4:
                r, g, b = 0, x, 1
            elif h < 5:
                r, g, b = x, 0, 1
            else:
                r, g, b = 1, 0, x
            
            colors[label_id] = (r, g, b)
        return colors
    
    # Get max projection of first channel for display
    img_first = np.max(img_data[0, 0, :, :, :], axis=0)
    img_last = np.max(img_data[-1, 0, :, :, :], axis=0)
    
    # Normalize intensities
    vmin = np.percentile(img_data[0, 0], 1)
    vmax = np.percentile(img_data[0, 0], 99)
    
    # Show first timepoint
    ax_first.imshow(img_first, cmap='gray', vmin=vmin, vmax=vmax, interpolation='nearest')
    ax_first.set_title(f'T=0 (MIP)', fontsize=10)
    ax_first.axis('off')
    
    # Show last timepoint
    ax_last.imshow(img_last, cmap='gray', vmin=vmin, vmax=vmax, interpolation='nearest')
    ax_last.set_title(f'T={T-1} (MIP)', fontsize=10)
    ax_last.axis('off')
    
    # Overlay masks if provided
    if mask_data is not None:
        # Get all unique labels across all timepoints
        all_labels = set()
        for t in range(mT):
            mask_mip = np.max(mask_data[t, 0, :, :, :], axis=0)
            all_labels.update(np.unique(mask_mip))
        all_labels.discard(0)  # Remove background
        max_label = max(all_labels) if all_labels else 0
        
        if max_label > 0:
            label_colors = get_label_colors(max_label)
            
            # Create colormap for overlays
            colors_list = [(0, 0, 0, 0)]  # Background transparent
            for i in range(1, max_label + 1):
                if i in label_colors:
                    r, g, b = label_colors[i]
                    colors_list.append((r, g, b, alpha))
                else:
                    colors_list.append((0, 0, 0, alpha))
            mask_cmap = ListedColormap(colors_list)
            
            # Overlay on first timepoint
            mask_first = np.max(mask_data[0, 0, :, :, :], axis=0)
            mask_overlay_first = np.ma.masked_where(mask_first == 0, mask_first)
            ax_first.imshow(mask_overlay_first, cmap=mask_cmap, alpha=1.0, interpolation='nearest',
                          vmin=0, vmax=max_label)
            n_obj_first = len(np.unique(mask_first)) - 1
            ax_first.set_title(f'T=0 ({n_obj_first} objects)', fontsize=10)
            
            # Overlay on last timepoint
            mask_last = np.max(mask_data[-1, 0, :, :, :], axis=0)
            mask_overlay_last = np.ma.masked_where(mask_last == 0, mask_last)
            ax_last.imshow(mask_overlay_last, cmap=mask_cmap, alpha=1.0, interpolation='nearest',
                         vmin=0, vmax=max_label)
            n_obj_last = len(np.unique(mask_last)) - 1
            ax_last.set_title(f'T={T-1} ({n_obj_last} objects)', fontsize=10)
            
            # Calculate pixel counts per label per timepoint (only if we have chart axis)
            if ax_chart is not None:
                timepoints = []
                pixel_counts = {label: [] for label in sorted(all_labels)}
                
                for t in range(mT):
                    mask_mip = np.max(mask_data[t, 0, :, :, :], axis=0)
                    timepoints.append(t)
                    
                    for label_id in sorted(all_labels):
                        count = np.sum(mask_mip == label_id)
                        pixel_counts[label_id].append(count)
                
                # Create stacked area chart
                bottom = np.zeros(len(timepoints))
                for label_id in sorted(all_labels):
                    counts = pixel_counts[label_id]
                    color = label_colors.get(label_id, (0.5, 0.5, 0.5))
                    ax_chart.fill_between(timepoints, bottom, bottom + counts, 
                                         color=color, alpha=0.7, label=f'ID {label_id}')
                    bottom += counts
                
                ax_chart.set_xlabel('Timepoint', fontsize=10)
                ax_chart.set_ylabel('Pixel Count', fontsize=10)
                ax_chart.set_title('Object Pixel Counts Over Time', fontsize=10)
                ax_chart.grid(True, alpha=0.3)
                
                # Only show legend if not too many labels
                if len(all_labels) <= 20:
                    ax_chart.legend(loc='upper left', fontsize=8, ncol=min(5, len(all_labels)))
    
    # Set figure title
    if title is None and image_path:
        title = Path(image_path).stem
    
    if title:
        fig.suptitle(title, fontsize=12, fontweight='bold')
    
    # Show plot with optional timer
    if timer > 0:
        # Non-blocking show with auto-close timer
        plt.show(block=False)
        plt.pause(timer)
        plt.close(fig)
    else:
        # Block until user closes the window
        plt.show(block=True)


def save_imagej_roi(
    coordinates: np.ndarray,
    output_path: str,
    t: int = 0,
    c: int = 0,
    z: int = 0
) -> None:
    """
    Save a contour as an ImageJ ROI file.
    
    Args:
        coordinates: Nx2 array of (row, col) or (y, x) coordinates from find_contours
        output_path: Path where to save the .roi file
        t: Timepoint index (0-based)
        c: Channel index (0-based)
        z: Z-slice index (0-based)
    
    Example:
        >>> from skimage import measure
        >>> mask_single = (labeled == prop.label)
        >>> contours = measure.find_contours(mask_single, 0.5)
        >>> contour = max(contours, key=len)
        >>> save_imagej_roi(contour, "output.roi", t=0, c=0, z=5)
    """
    from roifile import ImagejRoi
    
    # Convert coordinates: find_contours returns (row, col), ImageJ expects (x, y)
    # So we need to flip: row,col -> col,row -> x,y
    coords_xy = np.fliplr(coordinates).astype(np.int16)
    
    if len(coords_xy) < 3:
        raise ValueError(f"ROI must have at least 3 points, got {len(coords_xy)}")
    
    # Create ROI and set position
    roi = ImagejRoi.frompoints(coords_xy)
    roi.position = z + 1  # ImageJ uses 1-based indexing for position
    
    # Save to file
    roi.tofile(output_path)

def save_mask(
    mask: np.ndarray,
    output_path: str,
    as_binary: bool = False
) -> None:
    """
    Save mask as ImageJ-compatible TIFF.
    
    Args:
        mask: 5D mask (T, C, Z, Y, X) in TCZYX order
        output_path: Output file path
        as_binary: If True, convert to 0/255 binary, else keep as-is
    """
    if as_binary:
        # Convert to binary (0 or 255) 8-bit mask
        mask_out = (mask > 0).astype(np.uint8) * 255
    else:
        # Handle different data types
        if mask.dtype == np.float32 or mask.dtype == np.float64:
            # Float types: preserve as float32
            mask_out = mask.astype(np.float32)
        elif np.issubdtype(mask.dtype, np.integer):
            # Integer types (int32, int64, etc.): convert to uint16 for ImageJ compatibility
            # ImageJ doesn't support int32/int64, but uint16 can handle labeled masks up to 65535
            mask_out = mask.astype(np.uint16)
        else:
            # Other types: keep as-is
            mask_out = mask
    
    # Remove C dimension (should be 1) and convert to TZYX for ImageJ
    if mask_out.shape[1] == 1:
        mask_out = mask_out[:, 0, :, :, :]  # (T, Z, Y, X)
    
    # Save as ImageJ-compatible TIFF
    # ImageJ expects TZYX order for stacks
    tifffile.imwrite(
        output_path,
        mask_out,
        imagej=True,
        metadata={'axes': 'TZYX'},
        compression='deflate'
    )

def save_imagej_rois_from_mask(
    mask: Union[np.ndarray, BioImage],
    output_path: str,
    name_pattern: str = "T{t}_C{c}_Z{z}_obj{label}"
) -> int:
    """
    Convert a labeled mask to individual ImageJ ROI files.
    
    Creates one .roi file per object in the mask. Each unique label (except 0)
    in each T,C,Z plane is converted to a separate ROI file.
    
    Args:
        mask: Labeled mask as numpy array (TCZYX or lower dimensions) or BioImage.
              Each unique integer value (except 0) represents a different object.
        output_path: Directory path where ROI files will be saved, or path to .zip file
                     for saving all ROIs in a single archive.
        name_pattern: Format string for ROI filenames. Available placeholders:
                      {t}, {c}, {z}, {label}. Only used if output_path is a directory.
                      Default: "T{t}_C{c}_Z{z}_obj{label}"
    
    Returns:
        Number of ROI files created
    
    Example:
        >>> # Save individual ROI files
        >>> count = save_imagej_rois_from_mask(
        ...     mask, 
        ...     "output_folder",
        ...     name_pattern="T{t}_C{c}_Z{z}_obj{label}.roi"
        ... )
        >>> print(f"Saved {count} ROI files")
        
        >>> # Save as a single zip archive
        >>> count = save_imagej_rois_from_mask(mask, "output_rois.zip")
    """
    from cv2 import findContours, RETR_EXTERNAL, CHAIN_APPROX_NONE
    from roifile import ImagejRoi, roiwrite
    
    # Convert to numpy array if BioImage
    if hasattr(mask, 'data'):
        mask_data = np.asarray(mask.data)
    else:
        mask_data = np.asarray(mask)
    
    # Ensure 5D
    while mask_data.ndim < 5:
        mask_data = mask_data[np.newaxis, ...]
    
    T, C, Z, Y, X = mask_data.shape
    
    # Determine if we're saving to a zip or individual files
    save_as_zip = output_path.lower().endswith('.zip')
    
    if not save_as_zip:
        os.makedirs(output_path, exist_ok=True)
    
    rois = []
    roi_count = 0
    empty_count = 0
    
    for t in range(T):
        for c in range(C):
            for z in range(Z):
                plane = mask_data[t, c, z]
                labels = np.unique(plane)
                
                for label in labels:
                    if label == 0:  # Skip background
                        continue
                    
                    # Create binary mask for this label
                    label_mask = (plane == label).astype(np.uint8)
                    
                    if label_mask.sum() == 0:
                        continue  # Skip if label has no pixels
                    
                    # Find contours using OpenCV
                    contours, _ = findContours(label_mask, mode=RETR_EXTERNAL, method=CHAIN_APPROX_NONE)
                    
                    if not contours:
                        empty_count += 1
                        continue
                    
                    # Take the largest contour by number of points
                    cmax = np.argmax([c.shape[0] for c in contours])
                    pix = contours[cmax].astype(int).squeeze()
                    
                    if pix.ndim != 2 or pix.shape[0] <= 4:
                        empty_count += 1
                        continue
                    
                    # Create ROI from contour points
                    roi = ImagejRoi.frompoints(pix)
                    roi.position = z + 1  # ImageJ uses 1-based indexing
                    
                    if save_as_zip:
                        # Add to list for bulk save
                        rois.append(roi)
                    else:
                        # Save individual file
                        roi_name = name_pattern.format(t=t, c=c, z=z, label=int(label))
                        if not roi_name.endswith('.roi'):
                            roi_name += '.roi'
                        roi_path = os.path.join(output_path, roi_name)
                        roi.tofile(roi_path)
                    
                    roi_count += 1
    
    # Save as zip if requested
    if save_as_zip and rois:
        roiwrite(output_path, rois)
    
    if empty_count > 0:
        print(f"Empty outlines found, saved {roi_count} ImageJ ROIs (skipped {empty_count} empty contours).")
    
    return roi_count


if __name__ == "__main__":
    # # Example usage
    folder_path = r"Z:\Schink\Oyvind\biphub_user_data\6849908 - IMB - Coen - Sarah - Photoconv\input_tif"
    # extension = ".tif"
    # search_subfolders = False

    # files_to_process = get_files_to_process(folder_path, extension, search_subfolders)
    # print("Files to process:", files_to_process)

    # for file_path in files_to_process:
    #     collapsed_name = collapse_filename(file_path, folder_path)
    #     print("Collapsed filename:", collapsed_name)
    #     original_path = uncollapse_filename(collapsed_name, folder_path)
    #     print("Original path:", original_path)

    # # Load a BioImage object
    #     img = load_bioio(file_path)
    #     print(img.shape)
