"""Screen automation engine — image matching, clicking, typing, waiting.

Uses ``mss`` for multi-monitor screen capture and ``cv2`` for template
matching. Mouse is parked to (0, 0) after clicks and when buttons are not
found, to avoid hover effects interfering with subsequent screenshots.
"""

from __future__ import annotations

import fnmatch
import glob
import logging
import os
import re
import subprocess
import sys
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

    template_gray = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    template_std = float(np.std(template_gray))

    # Low-variance templates are prone to false positives with CCOEFF_NORMED,
    # so use SQDIFF_NORMED where lower is better.
    if template_std < 10.0:
        result = cv2.matchTemplate(screen, template, cv2.TM_SQDIFF_NORMED)
        min_val, _max_val, min_loc, _max_loc = cv2.minMaxLoc(result)
        sqdiff_threshold = max(0.01, 1.0 - confidence)
        if min_val <= sqdiff_threshold:
            cx = int(min_loc[0] + tw / 2)
            cy = int(min_loc[1] + th / 2)
            origin_x, origin_y = _virtual_desktop_origin()
            vx = cx + origin_x
            vy = cy + origin_y
            logger.debug(
                "Found %s at image (%d, %d) -> virtual (%d, %d) with sqdiff %.3f <= %.3f (low-variance template)",
                os.path.basename(image_path),
                cx,
                cy,
                vx,
                vy,
                min_val,
                sqdiff_threshold,
            )
            return (vx, vy)

        logger.debug(
            "Did not find %s (best sqdiff %.3f > %.3f, low-variance template)",
            os.path.basename(image_path),
            min_val,
            sqdiff_threshold,
        )
        return None

    result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
    _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(result)

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


def _enumerate_button_candidates(button: str, buttonpath: str) -> list[str]:
    """Return candidate image paths for a button reference in the buttonpath."""
    if os.path.isabs(button):
        return [button] if os.path.isfile(button) else []

    if os.path.dirname(button):
        candidate = os.path.normpath(os.path.join(buttonpath, button))
        return [candidate] if os.path.isfile(candidate) else []

    candidates: list[str] = []
    base, _ext = os.path.splitext(button)
    try:
        entries = sorted(os.listdir(buttonpath))
    except FileNotFoundError:
        return []

    for entry in entries:
        full_path = os.path.join(buttonpath, entry)
        if not os.path.isfile(full_path):
            continue

        entry_base, _entry_ext = os.path.splitext(entry)
        if entry_base == base or re.fullmatch(rf"{re.escape(base)}(_\d+)?", entry_base):
            candidates.append(full_path)

    if not candidates and any(char in button for char in "*?["):
        candidates = sorted(glob.glob(os.path.join(buttonpath, button)))

    return candidates


def _find_button(button: str, buttonpath: str, confidence: float = 0.8) -> tuple[str, tuple[int, int]] | tuple[None, None]:
    """Search for a button image using one or more candidate files."""
    candidates = _enumerate_button_candidates(button, buttonpath)
    if not candidates:
        # Fallback: try the literal button path under buttonpath
        literal = os.path.join(buttonpath, button)
        if os.path.isfile(literal):
            candidates = [literal]

    for candidate in candidates:
        pos = find_button(candidate, confidence)
        if pos is not None:
            return candidate, pos

    return None, None


def click_button(
    button: str,
    buttonpath: str,
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
        image_path, pos = _find_button(button, buttonpath, confidence)
        if pos is not None:
            print(f"Found {os.path.basename(image_path)} at ({pos[0]}, {pos[1]})")
            pyautogui.click(pos[0], pos[1])
            park_mouse()
            logger.info("Clicked: %s", os.path.basename(image_path))
            return True

        # Not found — park mouse to clear hover before retry
        park_mouse()

        if deadline is not None and time.monotonic() >= deadline:
            logger.warning(
                "Timed out waiting to click: %s", button
            )
            return False

        time.sleep(POLL_INTERVAL)


def click_if_exists(
    button: str,
    buttonpath: str,
    confidence: float = 0.8,
) -> bool:
    """Click a button only if it exists on screen.

    If the button is not found, do nothing and continue.
    """
    image_path, pos = _find_button(button, buttonpath, confidence)
    if pos is None:
        logger.info("Button not found, skipping: %s", button)
        return True

    print(f"Found {os.path.basename(image_path)} at ({pos[0]}, {pos[1]})")
    pyautogui.click(pos[0], pos[1])
    park_mouse()
    logger.info("Clicked existing: %s", os.path.basename(image_path))
    return True


def click_double_button(
    button: str,
    buttonpath: str,
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
        image_path, pos = _find_button(button, buttonpath, confidence)
        if pos is not None:
            print(f"Found {os.path.basename(image_path)} at ({pos[0]}, {pos[1]})")
            pyautogui.click(pos[0], pos[1], clicks=2, interval=0.1)
            park_mouse()
            logger.info("Double-clicked: %s", os.path.basename(image_path))
            return True

        # Not found — park mouse to clear hover before retry
        park_mouse()

        if deadline is not None and time.monotonic() >= deadline:
            logger.warning(
                "Timed out waiting to double-click: %s", button
            )
            return False

        time.sleep(POLL_INTERVAL)


def wait_appear(
    button: str,
    buttonpath: str,
    confidence: float = 0.8,
    timeout: float = float("inf"),
) -> bool:
    """Wait until a button image appears on screen.

    This wait mode must not move the mouse; it only polls screenshots.

    Returns:
        True if the button appeared, False on timeout.
    """
    deadline = time.monotonic() + timeout if timeout != float("inf") else None

    while True:
        image_path, pos = _find_button(button, buttonpath, confidence)
        if pos is not None:
            logger.info("Appeared: %s", os.path.basename(image_path))
            return True

        if deadline is not None and time.monotonic() >= deadline:
            logger.warning(
                "Timed out waiting to appear: %s", button
            )
            return False

        time.sleep(POLL_INTERVAL)


def _get_window_titles() -> list[str]:
    """Return the titles of currently visible windows on Windows."""
    if os.name != "nt":
        return []

    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return []

    user32 = ctypes.windll.user32

    titles: list[str] = []

    def enum_windows_callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True

        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True

        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if title:
            titles.append(title)
        return True

    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    cb = callback_type(enum_windows_callback)
    user32.EnumWindows(cb, 0)
    return titles


def run_command(command: str, background: bool = False) -> bool:
    """Run a shell command and return True when it exits successfully.

    If background is True, the command is started and the function returns
    immediately without waiting for process termination.
    """
    logger.info("Running command: %s (background=%s)", command, background)

    if background:
        try:
            subprocess.Popen(command, shell=True)
            return True
        except Exception as exc:
            logger.error("Command failed to start: %s", exc)
            return False

    try:
        completed = subprocess.run(
            command,
            shell=True,
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception as exc:
        logger.error("Command failed: %s", exc)
        return False

    if completed.stdout:
        print(completed.stdout.strip())
    if completed.stderr:
        print(completed.stderr.strip(), file=sys.stderr)

    if completed.returncode != 0:
        logger.error("Command exited with code %d: %s", completed.returncode, command)
        return False

    return True


def _perform_window_action(title: str, action: str) -> None:
    """Perform an action on a matching window on Windows."""
    if os.name != "nt":
        return

    try:
        import ctypes
    except ImportError:
        return

    user32 = ctypes.windll.user32

    def enum_windows_callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True

        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True

        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value.strip() == title:
            if action == "focus":
                user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                user32.SetForegroundWindow(hwnd)
            elif action == "minimize":
                user32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
            elif action == "close":
                user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
            return False
        return True

    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    cb = callback_type(enum_windows_callback)
    user32.EnumWindows(cb, 0)


def _close_matching_windows(pattern: str) -> bool:
    """Close every visible window whose title matches the given pattern."""
    if os.name != "nt":
        return False

    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return False

    user32 = ctypes.windll.user32
    closed = 0

    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True

        length = user32.GetWindowTextLengthW(hwnd)
        if length <= 0:
            return True

        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value.strip()
        if title and fnmatch.fnmatchcase(title.lower(), pattern.lower()):
            user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE
            nonlocal closed
            closed += 1
        return True

    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    cb = callback_type(callback)
    user32.EnumWindows(cb, 0)

    if closed:
        logger.info("Closed %d matching windows: %s", closed, pattern)
        return True
    return False


def wait_for_window(pattern: str, timeout: float = float("inf"), action: str = "focus") -> bool:
    """Wait until a window title matches the given wildcard pattern.

    Supports the same patterns as Python's fnmatch, including ``*`` for any
    sequence of characters. When a match is found, the window may be acted
    on according to the configured action.
    """
    deadline = time.monotonic() + timeout if timeout != float("inf") else None

    while True:
        titles = _get_window_titles()
        for title in titles:
            if fnmatch.fnmatchcase(title.lower(), pattern.lower()):
                logger.info("Found matching window title: %s", title)
                if action == "close_all":
                    return _close_matching_windows(pattern)
                _perform_window_action(title, action)
                return True

        if deadline is not None and time.monotonic() >= deadline:
            if action == "close_all":
                logger.info(
                    "No matching windows found for pattern %s; continuing without error.",
                    pattern,
                )
                return True
            logger.warning("Timed out waiting for window matching pattern: %s", pattern)
            return False

        time.sleep(POLL_INTERVAL)


def wait_disappear(
    button: str,
    buttonpath: str,
    confidence: float = 0.8,
    timeout: float = float("inf"),
) -> bool:
    """Wait until a button image is no longer on screen.

    This wait mode must not move the mouse; it only polls screenshots.

    Returns:
        True if the button disappeared, False on timeout.
    """
    deadline = time.monotonic() + timeout if timeout != float("inf") else None

    while True:
        image_path, pos = _find_button(button, buttonpath, confidence)
        if pos is None:
            logger.info("Disappeared: %s", button)
            return True

        if deadline is not None and time.monotonic() >= deadline:
            logger.warning(
                "Timed out waiting to disappear: %s", button
            )
            return False

        time.sleep(POLL_INTERVAL)


def click_and_type(
    button: str,
    buttonpath: str,
    text: str,
    enter: bool = False,
    confidence: float = 0.8,
    timeout: float = float("inf"),
) -> bool:
    """Click a button, type text, optionally press Enter, then park mouse.

    Returns:
        True if the button was found and text typed, False on timeout.
    """
    if not click_button(button, buttonpath, confidence, timeout):
        return False

    pyautogui.write(text)
    logger.info("Typed: %s", text)

    if enter:
        pyautogui.press("enter")
        logger.info("Pressed Enter")

    park_mouse()
    return True


def type_text(text: str, enter: bool = False) -> bool:
    """Type text into the currently focused field."""
    pyautogui.write(text)
    logger.info("Typed: %s", text)

    if enter:
        pyautogui.press("enter")
        logger.info("Pressed Enter")

    return True