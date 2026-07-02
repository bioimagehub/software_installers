"""autoyamlgui — cross-platform GUI automation driven by YAML config files."""

from __future__ import annotations

import sys

__version__ = "0.1.0"


def _enable_dpi_awareness() -> None:
    """Make the process DPI-aware on Windows before any GUI library is imported.

    This must run as early as possible (in __init__.py) so that pynput, mss,
    pyautogui, and tkinter all see the same coordinate space — physical pixels.

    Without this, libraries like tkinter may set DPI awareness at different
    times, causing pynput to report physical coordinates while our scale
    detection (which uses GetSystemMetrics) also sees physical coordinates,
    making the scale appear to be 1.0 even when Windows scaling is 150%.
    By setting it explicitly and early, all libraries are consistent.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        # Try Per-Monitor v2 (Windows 10 1703+).
        # SetProcessDpiAwarenessContext expects a DPI_AWARENESS_CONTEXT handle,
        # which is an opaque pointer-sized value. The predefined values are
        # small negative integers that must be passed as pointer-sized ints.
        try:
            func = ctypes.windll.user32.SetProcessDpiAwarenessContext
            func.argtypes = [ctypes.c_void_p]
            func.restype = ctypes.c_bool
            # PER_MONITOR_AWARE_V2 = -4
            if func(ctypes.c_void_p(-4)):
                return
        except (AttributeError, OSError):
            pass

        # Fall back to Per-Monitor v1 (Windows 8.1+).
        try:
            func = ctypes.windll.shcore.SetProcessDpiAwareness
            func.argtypes = [ctypes.c_int]
            func.restype = ctypes.c_long  # HRESULT
            # PROCESS_PER_MONITOR_DPI_AWARE = 2
            hr = func(2)
            if hr == 0:  # S_OK
                return
        except (AttributeError, OSError):
            pass

        # Last resort: system DPI aware (Vista+).
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass
    except Exception:
        pass


_enable_dpi_awareness()