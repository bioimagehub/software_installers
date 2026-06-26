from __future__ import annotations
from typing import Any, Optional, Tuple, Iterable, List

import h5py
import numpy as np
import dask.array as da

from bioio_base.reader import Reader as BaseReader
from bioio_base import types
from bioio_base import dimensions


class IlastikH5Reader(BaseReader):
    """
    Bioio reader for Ilastik HDF5 files saved in TZYXC format.
    Converts TCZYX to TCZYX for compatibility with bioio standard.
    """
    
    _cached_data: Optional[np.ndarray] = None
    _cached_axes: Optional[list] = None
    _h5_path: Optional[str] = None
    _dataset_key: Optional[str] = None

    def __init__(self, image: Any, **kwargs: Any):
        """Initialize the reader with the image path."""
        # Store path before calling super().__init__()
        if isinstance(image, (str, bytes)):
            self._h5_path = str(image)
        super().__init__(image, **kwargs)

    @classmethod
    def _is_supported_image(cls, image: Any, **kwargs: Any) -> bool:
        """Check if this is a valid Ilastik H5 file."""
        try:
            if isinstance(image, (str, bytes)):
                with h5py.File(image, 'r') as f:
                    if 'exported_data' in f or 'data' in f:
                        return True
                    dataset_count = sum(1 for obj in f.values() if isinstance(obj, h5py.Dataset))
                    return dataset_count == 1
            return False
        except Exception:
            return False

    def _resolve_dataset_key(self, f: h5py.File) -> str:
        """Resolve H5 dataset key for image data.

        Preferred keys are ``exported_data`` (Ilastik export) and ``data``.
        As a fallback, accept files containing exactly one top-level dataset.
        """
        if self._dataset_key is not None and self._dataset_key in f:
            return self._dataset_key

        if 'exported_data' in f:
            self._dataset_key = 'exported_data'
            return self._dataset_key
        if 'data' in f:
            self._dataset_key = 'data'
            return self._dataset_key

        dataset_keys = [k for k, v in f.items() if isinstance(v, h5py.Dataset)]
        if len(dataset_keys) == 1:
            self._dataset_key = dataset_keys[0]
            return self._dataset_key

        raise ValueError(
            "No supported image dataset found in H5 file. "
            "Expected 'exported_data' or 'data', or exactly one top-level dataset."
        )

    @property
    def scenes(self) -> Tuple[str, ...]:
        """Return available scenes (always single scene for H5)."""
        return ("Image",)
    
    @classmethod
    def supports_extension(cls, ext: str) -> bool:
        return ext.lower() in (".h5", ".hdf5")

    def _read_delayed(self) -> types.ArrayLike:
        """Load data lazily using Dask."""
        import xarray as xr
        
        img_path = getattr(self, '_image', None) or self._h5_path
        with h5py.File(img_path, 'r') as f:
            dataset_key = self._resolve_dataset_key(f)
            data = f[dataset_key][:]
            axes = self._get_axes(f, dataset_key)
            
        # Convert TZYXC to TCZYX for BioImage
        axis_map = {ax: i for i, ax in enumerate(axes)}
        perm = [axis_map[a] for a in 'TCZYX']
        tczyx_data = np.transpose(data, perm)
        
        # Cache for other methods
        self._cached_data = tczyx_data
        self._cached_axes = ['T', 'C', 'Z', 'Y', 'X']
        
        # Return as xarray.DataArray with dimension labels
        # BaseReader will handle converting to dask if needed
        xarr = xr.DataArray(
            tczyx_data,  # Use numpy array, not dask
            dims=['T', 'C', 'Z', 'Y', 'X'],
            coords={
                'T': np.arange(tczyx_data.shape[0]),
                'C': np.arange(tczyx_data.shape[1]),
                'Z': np.arange(tczyx_data.shape[2]),
                'Y': np.arange(tczyx_data.shape[3]),
                'X': np.arange(tczyx_data.shape[4]),
            }
        )
        
        return xarr

    def _read_immediate(self) -> types.ArrayLike:
        """Load data immediately (same as delayed for H5)."""
        return self._read_delayed()

    def _get_axes(self, f: h5py.File, dataset_key: str) -> list[str]:
        """Extract axis order from HDF5 file attributes."""
        if 'axistags' in f[dataset_key].attrs:
            import json
            axistags = f[dataset_key].attrs['axistags']
            if isinstance(axistags, bytes):
                axistags = axistags.decode('utf-8')
            axes_json = json.loads(axistags)
            return [a['key'].upper() for a in axes_json['axes']]
        return ['T', 'Z', 'Y', 'X', 'C']

    def _get_shape(self) -> Tuple[int, int, int, int, int]:
        """Return shape in TCZYX order."""
        if self._cached_data is not None:
            return tuple(self._cached_data.shape)
        
        # Read temporarily to get shape
        img_path = getattr(self, '_image', None) or self._h5_path
        with h5py.File(img_path, 'r') as f:
            dataset_key = self._resolve_dataset_key(f)
            data_shape = f[dataset_key].shape
            axes = self._get_axes(f, dataset_key)
        
        # Convert to TCZYX order
        axis_map = {ax: i for i, ax in enumerate(axes)}
        return tuple(data_shape[axis_map[a]] for a in 'TCZYX')

    def _get_dtype(self) -> np.dtype:
        """Return data type."""
        img_path = getattr(self, '_image', None) or self._h5_path
        with h5py.File(img_path, 'r') as f:
            dataset_key = self._resolve_dataset_key(f)
            return np.dtype(f[dataset_key].dtype)

    def _get_dims(self) -> str:
        """Return dimension order string."""
        return dimensions.DEFAULT_DIMENSION_ORDER  # "TCZYX"

    def _get_physical_pixel_sizes(self) -> types.PhysicalPixelSizes:
        """Return pixel sizes (default to 1.0 if not available)."""
        return types.PhysicalPixelSizes(Z=1.0, Y=1.0, X=1.0)

    def _get_channel_names(self) -> Optional[Iterable[str]]:
        """Return channel names if available."""
        img_path = getattr(self, '_image', None) or self._h5_path
        with h5py.File(img_path, 'r') as f:
            if 'channel_names' in f.attrs:
                return list(f.attrs['channel_names'])
        return None
