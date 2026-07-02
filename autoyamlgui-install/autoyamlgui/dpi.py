"""DPI scaling helpers for Windows.

On Windows with display scaling > 100% (e.g. 150%), there is a mismatch:
  - ``mss`` captures in **physical** pixels (the real screen resolution)
  - ``pynput`` reports mouse clicks in **logical** pixels (scaled down)
  - ``pyautogui`` also works in **logical** pixels

This module computes the scaling factor by comparing the physical screen
size (from ``mss``) against the logical screen size (from Windows
``GetSystemMetrics``) and provides helpers to convert between the two
coordinate spaces.

All coordinates in this project follow this convention:
  - **Physical** pixels: used by ``mss`` screenshots and ``cv2`` template matching
  - **Logical** pixels: used by ``pynput`` (click capture) and ``pyautogui`` (clicking)

Conversion:
  physical = logical * scale
  logical  = physical / scale
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

_scale: float = 1.0
_detected: bool = False


def _detect_scale() -> float:
    """Detect the DPI scaling factor by comparing physical vs logical screen size.

    Returns the scale factor (e.g. 1.5 for 150% scaling, 1.0 for 100%).
    Falls back to 1.0 on non-Windows or if detection fails.
    """
    if sys.platform != "win32":
        return 1.0

    try:
        import ctypes
        import mss

        # Logical screen size (what pynput/pyautogui see when not DPI-aware)
        SM_CXSCREEN = 0
        SM_CYSCREEN = 1
        logical_w = ctypes.windll.user32.GetSystemMetrics(SM_CXSCREEN)
        logical_h = ctypes.windll.user32.GetSystemMetrics(SM_CYSCREEN)

        # Physical screen size (what mss captures)
        sct = mss.MSS()
        monitor = sct.monitors[0]
        physical_w = monitor["width"]
        physical_h = monitor["height"]

        if logical_w > 0 and logical_h > 0:
            scale_x = physical_w / logical_w
            scale_y = physical_h / logical_h
            # Use the average; they should be equal in practice
            scale = (scale_x + scale_y) / 2.0
            logger.info(
                "DPI scale detected: %.3f (logical %dx%d, physical %dx%d)",
                scale, logical_w, logical_h, physical_w, physical_h,
            )
            return scale
    except Exception as e:
        logger.warning("Could not detect DPI scale: %s", e)

    return 1.0


def get_scale() -> float:
    """Return the cached DPI scale factor, detecting it on first call."""
    global _scale, _detected
    if not _detected:
        _scale = _detect_scale()
        _detected = True
    return _scale


def logical_to_physical(x: int, y: int) -> tuple[int, int]:
    """Convert pynput/logical coordinates to mss/physical coordinates."""
    s = get_scale()
    return (int(round(x * s)), int(round(y * s)))


def physical_to_logical(x: int, y: int) -> tuple[int, int]:
    """Convert mss/physical coordinates to pyautogui/logical coordinates."""
    s = get_scale()
    return (int(round(x / s)), int(round(y / s)))