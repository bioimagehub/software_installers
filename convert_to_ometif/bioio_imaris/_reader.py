# USE Bioformats instead of this!!!

from __future__ import annotations
from typing import Any, Dict, Optional, Tuple, Iterable

import numpy as np
import dask.array as da
import fsspec
import xarray as xr

from bioio_base.reader import Reader as BaseReader
from bioio_base import types
from bioio_base import dimensions
from bioio_base.constants import METADATA_UNPROCESSED


class Reader(BaseReader):
    """Pure-Python Imaris .ims reader for bioio (TCZYX)."""

    _ims: Any

    @staticmethod
    def _is_supported_image(
        fs: fsspec.AbstractFileSystem,
        path: str,
        **kwargs: Any,
    ) -> bool:
        """Return True when file extension is .ims."""
        return str(path).lower().endswith(".ims")

    @classmethod
    def supports_extension(cls, ext: str) -> bool:
        return ext.lower() == ".ims"

    @property
    def scenes(self) -> Tuple[str, ...]:
        """Imaris reader currently exposes a single logical scene."""
        return ("Image:0",)

    def _open_ims(self) -> Any:
        """Lazy-open .ims backend and cache handle."""
        if getattr(self, "_ims", None) is None:
            from imaris_ims_file_reader.ims import ims as ImarisIMS  # type: ignore
            self._ims = ImarisIMS(self._image)
        return self._ims

    def _to_xarray(self, delayed: bool) -> xr.DataArray:
        """Build an xarray.DataArray in TCZYX order."""
        ims_obj = self._open_ims()
        if delayed:
            arr = da.from_array(ims_obj, chunks=getattr(ims_obj, "chunks", None))
            arr = arr.astype(getattr(ims_obj, "dtype", np.uint16), copy=False)
        else:
            arr = np.asarray(ims_obj)

        return xr.DataArray(
            data=arr,
            dims=tuple(dimensions.DEFAULT_DIMENSION_ORDER),
            attrs={METADATA_UNPROCESSED: self._get_metadata()},
        )

    def _read_delayed(self) -> types.ArrayLike:
        return self._to_xarray(delayed=True)

    def _read_immediate(self) -> xr.DataArray:
        return self._to_xarray(delayed=False)

    def _get_shape(self) -> Tuple[int, int, int, int, int]:
        shp = getattr(self._ims, "shape", None)
        if shp is None:
            # Some versions expose dims via properties
            t = int(getattr(self._ims, "TimePoints", 1))
            c = int(getattr(self._ims, "Channels", [None]).__len__())
            z = int(getattr(self._ims, "SizeZ", 1))
            y = int(getattr(self._ims, "SizeY", 1))
            x = int(getattr(self._ims, "SizeX", 1))
            return (t, c, z, y, x)
        return tuple(int(x) for x in shp)

    def _get_dtype(self) -> np.dtype:
        return np.dtype(getattr(self._ims, "dtype", np.uint16))

    def _get_channel_names(self) -> Optional[Iterable[str]]:
        try:
            chans = getattr(self._ims, "Channels", None)
            if chans is None:
                return None
            names = []
            for idx, ch in enumerate(chans):
                name = None
                try:
                    name = ch.get("Name", None)
                except Exception:
                    pass
                names.append(name if name else f"C{idx}")
            return names
        except Exception:
            return None

    def _get_physical_pixel_sizes(self) -> types.PhysicalPixelSizes:
        # Try to pull voxel size from metadata; fall back to 1.0 µm
        try:
            params = getattr(self._ims, "Parameters", {})
            ext = None
            if isinstance(params, dict):
                ext = params.get("Extents", {}).get("Spacing", None)
            if ext is None:
                # alternate key shapes sometimes occur
                ext = params.get("Spacing", None) if isinstance(params, dict) else None
            if ext is not None and len(ext) >= 3:
                return types.PhysicalPixelSizes(z=float(ext[0]), y=float(ext[1]), x=float(ext[2]))
        except Exception:
            pass
        return types.PhysicalPixelSizes(1.0, 1.0, 1.0)

    def _get_dims(self) -> str:
        return dimensions.DEFAULT_DIMENSION_ORDER  # "TCZYX"

    def _get_metadata(self) -> Dict[str, Any]:
        md: Dict[str, Any] = {}
        try:
            params = getattr(self._ims, "Parameters", None)
            if params is not None:
                try:
                    md["imaris_parameters"] = dict(params)
                except Exception:
                    md["imaris_parameters"] = params
            rl = getattr(self._ims, "ResolutionLevels", None)
            if rl is not None:
                md["resolution_levels"] = int(rl)
            tp = getattr(self._ims, "TimePoints", None)
            if tp is not None:
                md["time_points"] = int(tp)
        except Exception:
            pass
        return md
