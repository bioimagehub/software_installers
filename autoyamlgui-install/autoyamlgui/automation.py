"""Screen automation engine — image matching, clicking, typing, waiting.

Uses ``mss`` for multi-monitor screen capture and ``cv2`` for template
matching. Mouse is parked to (0, 0) after clicks and when buttons are not
found, to avoid hover effects interfering with subsequent screenshots.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Tuple

import cv2
import mss
import numpy as np
import pyautogui

logger = logging.getLogger(__name__)

# Polling interval for wait/click operations (seconds)
POLL_INTERVAL = 0.5

# Disable pyautogui's fail-safe for now (mouse moving to corner triggers it).
# We can re-enable later if desired.
pyautogui.FAILSAFE = False


# ---------------------------------------------------------------------------
# Screen capture
# ---------------------------------------------------------------------------

_sct = mss.MSS()


def capture_screen() -> np.ndarray:
    """Capture the full virtual desktop (all monitors) as a BGR numpy array.

    Returns:
        A numpy array of shape (H, W, 3) in BGR order, suitable for OpenCV.
    """
    # mss.grab() with one monitor captures all monitors combined
    monitor = _sct.monitors[0]  # monitor 0 = the combined virtual desktop
    raw = _sct.grab(monitor)
    # raw is BGRA; convert to BGR
    img = np.array(raw)[:, :, :3]
    return img


def _virtual_desktop_origin() -> tuple[int, int]:
    """Return the (left, top) origin of the virtual desktop from mss.

    When a second monitor is positioned above the primary, the origin has
    a negative top value. Coordinates from cv2.matchTemplate are in image
    space (0-based), so we must add this origin to get virtual desktop
    coordinates for pyautogui.
    """
    mon = _sct.monitors[0]
    return (mon["left"], mon["top"])


# ---------------------------------------------------------------------------
# Image matching
# ---------------------------------------------------------------------------


def find_button(image_path: str, confidence: float = 0.8) -> Tuple[int, int] | None:
    """Find a button image on screen using template matching.

    Args:
        image_path: Absolute path to the button image file.
        confidence: Match threshold (0.0–1.0). Higher = stricter.

    Returns:
        (x, y) center coordinates in virtual desktop space, or None if not
        found.
    """
    if not os.path.isfile(image_path):
        logger.warning("Button image not found: %s", image_path)
        return None

    template = cv2.imread(image_path, cv2.IMREAD_COLOR)
    if template is None:
        logger.warning("Could not read button image: %s", image_path)
        return None

    screen = capture_screen()

    th, tw = template.shape[:2]
    sh, sw = screen.shape[:2]

    if tw > sw or th > sh:
        logger.warning(
            "Button image %s (%dx%d) is larger than screen (%dx%d)",
            image_path,
            tw,
            th,
            sw,
            sh,
        )
        return None

    result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

    if max_val >= confidence:
        cx = int(max_loc[0] + tw / 2)
        cy = int(max_loc[1] + th / 2)
        # Convert from image-space to virtual-desktop coordinates
        origin_x, origin_y = _virtual_desktop_origin()
        vx = cx + origin_x
        vy = cy + origin_y
        logger.debug(
            "Found %s at image (%d, %d) -> virtual (%d, %d) with confidence %.3f",
            os.path.basename(image_path),
            cx,
            cy,
            vx,
            vy,
            max_val,
        )
        return (vx, vy)

    logger.debug(
        "Did not find %s (best confidence %.3f < %.2f)",
        os.path.basename(image_path),
        max_val,
        confidence,
    )
    return None


# ---------------------------------------------------------------------------
# Mouse helpers
# ---------------------------------------------------------------------------


def park_mouse() -> None:
    """Move the mouse to (0, 0) to clear hover states."""
    pyautogui.moveTo(0, 0)


# ---------------------------------------------------------------------------
# Action functions
# ---------------------------------------------------------------------------


def click_button(
    image_path: str,
    confidence: float = 0.8,
    timeout: float = float("inf"),
) -> bool:
    """Poll for a button on screen, click it, then park the mouse.

    Args:
        image_path: Absolute path to the button image.
        confidence: Match threshold.
        timeout: Max seconds to wait. ``inf`` means wait forever.

    Returns:
        True if the button was found and clicked, False on timeout.
    """
    deadline = time.monotonic() + timeout if timeout != float("inf") else None

    while True:
        pos = find_button(image_path, confidence)
        if pos is not None:
            print(f"Found {os.path.basename(image_path)} at ({pos[0]}, {pos[1]})")
            # With DPI awareness set in __init__.py, pyautogui uses
            # physical pixel coordinates that match mss/find_button directly.
            pyautogui.click(pos[0], pos[1])
            park_mouse()
            logger.info("Clicked: %s", os.path.basename(image_path))
            return True

        # Not found — park mouse to clear hover before retry
        park_mouse()

        if deadline is not None and time.monotonic() >= deadline:
            logger.warning(
                "Timed out waiting to click: %s", os.path.basename(image_path)
            )
            return False

        time.sleep(POLL_INTERVAL)


def click_double_button(
    image_path: str,
    confidence: float = 0.8,
    timeout: float = float("inf"),
) -> bool:
    """Poll for a button on screen, double-click it, then park the mouse.

    Args:
        image_path: Absolute path to the button image.
        confidence: Match threshold.
        timeout: Max seconds to wait. ``inf`` means wait forever.

    Returns:
        True if the button was found and double-clicked, False on timeout.
    """
    deadline = time.monotonic() + timeout if timeout != float("inf") else None

    while True:
        pos = find_button(image_path, confidence)
        if pos is not None:
            print(f"Found {os.path.basename(image_path)} at ({pos[0]}, {pos[1]})")
            # With DPI awareness set in __init__.py, pyautogui uses
            # physical pixel coordinates that match mss/find_button directly.
            pyautogui.click(pos[0], pos[1], clicks=2, interval=0.1)
            park_mouse()
            logger.info("Double-clicked: %s", os.path.basename(image_path))
            return True

        # Not found — park mouse to clear hover before retry
        park_mouse()

        if deadline is not None and time.monotonic() >= deadline:
            logger.warning(
                "Timed out waiting to double-click: %s", os.path.basename(image_path)
            )
            return False

        time.sleep(POLL_INTERVAL)


def wait_appear(
    image_path: str,
    confidence: float = 0.8,
    timeout: float = float("inf"),
) -> bool:
    """Wait until a button image appears on screen.

    Returns:
        True if the button appeared, False on timeout.
    """
    deadline = time.monotonic() + timeout if timeout != float("inf") else None

    while True:
        pos = find_button(image_path, confidence)
        if pos is not None:
            logger.info("Appeared: %s", os.path.basename(image_path))
            return True

        if deadline is not None and time.monotonic() >= deadline:
            logger.warning(
                "Timed out waiting to appear: %s", os.path.basename(image_path)
            )
            return False

        time.sleep(POLL_INTERVAL)


def wait_disappear(
    image_path: str,
    confidence: float = 0.8,
    timeout: float = float("inf"),
) -> bool:
    """Wait until a button image is no longer on screen.

    Returns:
        True if the button disappeared, False on timeout.
    """
    deadline = time.monotonic() + timeout if timeout != float("inf") else None

    while True:
        pos = find_button(image_path, confidence)
        if pos is None:
            logger.info("Disappeared: %s", os.path.basename(image_path))
            return True

        # Still visible — park mouse to avoid hover effects
        park_mouse()

        if deadline is not None and time.monotonic() >= deadline:
            logger.warning(
                "Timed out waiting to disappear: %s", os.path.basename(image_path)
            )
            return False

        time.sleep(POLL_INTERVAL)


def click_and_type(
    image_path: str,
    text: str,
    enter: bool = False,
    confidence: float = 0.8,
    timeout: float = float("inf"),
) -> bool:
    """Click a button, type text, optionally press Enter, then park mouse.

    Returns:
        True if the button was found and text typed, False on timeout.
    """
    if not click_button(image_path, confidence, timeout):
        return False

    pyautogui.write(text)
    logger.info("Typed: %s", text)

    if enter:
        pyautogui.press("enter")
        logger.info("Pressed Enter")

    park_mouse()
    return True