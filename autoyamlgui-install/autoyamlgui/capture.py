"""Capture mode — record mouse clicks, crop button images, and build a YAML config.

Workflow:
1. User runs ``autoyamlgui --capture``.
2. User chooses an output directory (via tkinter file dialog).
3. **Record mode**: the user clicks freely on screen. Each click + screenshot
   is stored in a pending list. No dialogs interrupt the flow.
4. The user presses **Ctrl+S** to process the pending clicks:
   - For each recorded click, a crop dialog opens showing the zoomed screenshot.
   - The user crops, names, and chooses a command.
   - The cropped image is saved and a step is appended to the YAML.
   - The YAML is saved immediately after processing.
5. After processing, recording resumes for the next batch of clicks.
6. The user presses **Escape** (or Ctrl+C) to finish and save the final YAML.
"""

from __future__ import annotations

import logging
import os
import queue
import time
from dataclasses import dataclass, field
from typing import List

import mss
import numpy as np
import yaml
from PIL import Image, ImageTk
from pynput import mouse, keyboard
import tkinter as tk
from tkinter import filedialog, messagebox

logger = logging.getLogger(__name__)

# Half-size of the zoom region around the click point (pixels)
ZOOM_HALF = 300

# Scale factor for the zoomed crop view
ZOOM_SCALE = 2


def _next_legacy_config_path(outdir: str) -> str:
    """Return the next available config legacy filename in the output dir."""
    idx = 1
    while True:
        candidate = os.path.join(outdir, f"config_legacy_{idx}.yaml")
        if not os.path.exists(candidate):
            return candidate
        idx += 1


@dataclass
class PendingClick:
    """A recorded click waiting to be processed (cropped + named)."""

    x: int
    y: int
    screenshot: Image.Image


@dataclass
class CapturedStep:
    """A fully processed step ready for the YAML config."""

    button: str
    command: str = "click"
    text: str | None = None
    enter: bool = False
    timeout: str = "inf"


@dataclass
class CaptureSession:
    """State for a capture session."""

    outdir: str
    buttons_dir: str
    buttonpath: str
    name: str = "Captured script"
    defaults: dict = field(
        default_factory=lambda: {"timeout": "inf", "confidence": 0.8}
    )
    steps: List[CapturedStep] = field(default_factory=list)
    pending: List[PendingClick] = field(default_factory=list)
    _sct: mss.MSS = field(default_factory=mss.MSS)
    _click_queue: "queue.Queue[tuple[int, int]]" = field(default_factory=queue.Queue)
    _save_queue: "queue.Queue[bool]" = field(default_factory=queue.Queue)
    _mouse_listener: mouse.Listener | None = None
    _kb_listener: keyboard.Listener | None = None
    _ctrl_held: bool = False
    _busy: bool = False
    _stop: bool = False

    def start(self) -> None:
        """Start listening for mouse clicks and keyboard shortcuts."""
        print()
        print("=" * 60)
        print("  Capture mode active")
        print("  Click freely to record button clicks.")
        print("  Press Ctrl+S to crop, name, and save the recorded clicks.")
        print("  Press Escape (or Ctrl+C) to finish and save the YAML.")
        print("=" * 60)
        print()

        # Start pynput listeners in background daemon threads
        self._mouse_listener = mouse.Listener(on_click=self._on_click)
        self._mouse_listener.daemon = True
        self._mouse_listener.start()

        self._kb_listener = keyboard.Listener(on_press=self._on_key, on_release=self._on_key_release)
        self._kb_listener.daemon = True
        self._kb_listener.start()

        time.sleep(1)

        # Create the single Tk root on the main thread.
        root = tk.Tk()
        root.withdraw()
        root.bind("<Escape>", lambda e: self._finish())

        while not self._stop:
            # Check for save signal (Ctrl+S)
            try:
                self._save_queue.get_nowait()
                self._process_pending(root)
            except queue.Empty:
                pass

            # Collect any new clicks
            try:
                click_data = self._click_queue.get(timeout=0.1)
                if not self._busy:
                    self._record_click(click_data[0], click_data[1])
            except queue.Empty:
                root.update()
                continue
            except KeyboardInterrupt:
                self._finish()
                break

        # Clean up listeners
        if self._mouse_listener:
            self._mouse_listener.stop()
        if self._kb_listener:
            self._kb_listener.stop()

        # Process any remaining pending clicks before destroying root
        if self.pending:
            print(f"\n{len(self.pending)} unprocessed click(s) remaining. Opening crop dialogs...")
            self._process_pending(root)

        root.destroy()

        self._save_yaml()

    def _on_click(self, x, y, button, pressed) -> None:
        """Called by pynput in background thread. Queue the click."""
        if self._stop or self._busy:
            return
        if pressed:
            self._click_queue.put((x, y))

    def _on_key(self, key) -> None:
        """Called by pynput keyboard listener on key press."""
        if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            self._ctrl_held = True
        else:
            # When Ctrl is held, key.char is a control character (e.g. '\x13' for Ctrl+S)
            char = None
            if hasattr(key, "char") and key.char:
                char = key.char
            elif hasattr(key, "vk") and key.vk is not None:
                # On Windows, vk gives the virtual key code regardless of modifiers
                # 'S' key = 0x53
                if key.vk == 0x53:
                    char = "s"
            if char and self._ctrl_held:
                if char.lower() == "s" or char == "\x13":
                    self._save_queue.put(True)

    def _on_key_release(self, key) -> None:
        """Called by pynput keyboard listener on key release."""
        if key in (keyboard.Key.ctrl_l, keyboard.Key.ctrl_r):
            self._ctrl_held = False

    def _finish(self) -> None:
        """Signal the main loop to stop."""
        self._stop = True

    def _record_click(self, click_x: int, click_y: int) -> None:
        """Record a click + screenshot without opening a dialog.

        With DPI awareness set early in __init__.py, pynput reports
        physical pixel coordinates in the virtual desktop space.

        mss.monitors[0] covers the full virtual desktop but may have a
        non-zero origin (e.g. negative top when a second monitor is above
        the primary). The grabbed screenshot is a flat image starting at
        (0,0), so we must offset the click by the monitor origin to map
        it correctly into the screenshot pixels.
        """
        monitor = self._sct.monitors[0]
        raw = self._sct.grab(monitor)
        screenshot = np.array(raw)[:, :, :3]
        screenshot_rgb = screenshot[:, :, ::-1]
        img = Image.fromarray(screenshot_rgb)

        # Offset click from virtual-desktop coords to screenshot-image coords
        origin_x = monitor["left"]
        origin_y = monitor["top"]
        img_x = click_x - origin_x
        img_y = click_y - origin_y

        self.pending.append(PendingClick(x=img_x, y=img_y, screenshot=img))
        print(f"  Recorded click #{len(self.pending)} at virtual ({click_x}, {click_y}) -> image ({img_x}, {img_y})  [origin ({origin_x}, {origin_y})]")

    def _process_pending(self, root: tk.Tk) -> None:
        """Process all pending clicks: open crop dialog for each, save images + steps."""
        if not self.pending:
            return

        self._busy = True
        total = len(self.pending)
        print(f"\nProcessing {total} recorded click(s)...")

        for i, click in enumerate(self.pending):
            print(f"\n  Click {i + 1} of {total} at ({click.x}, {click.y})")
            result = _show_crop_dialog(
                click.screenshot, click.x, click.y,
                step_index=len(self.steps),
            )
            if result is None:
                print("    Skipped.")
                continue

            action, name, command, text, enter, cropped_img = result

            if action == "skip":
                print("    Skipped (not saved).")
                continue

            filename = f"{name}.png"
            img_path = os.path.join(self.buttons_dir, filename)
            cropped_img.save(img_path)
            print(f"    Saved: {img_path}")
            step = CapturedStep(
                button=filename,
                command=command,
                text=text if command == "click_and_type" else None,
                enter=enter,
            )
            self.steps.append(step)
            print(f"    Step added: button={filename}, command={command}")

        self.pending.clear()
        self._busy = False

        # Save YAML immediately after processing
        self._save_yaml()

        # Ask the user if they want to quit
        if self._ask_quit():
            self._stop = True
        else:
            print("\n  Recording resumed. Click to record more, Ctrl+S to process.")

    def _ask_quit(self) -> bool:
        """Ask the user if they want to quit. Returns True if yes."""
        from tkinter import messagebox as mb

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        answer = mb.askyesno(
            "Save complete",
            f"YAML saved with {len(self.steps)} step(s).\n\nDo you want to quit?",
        )
        root.destroy()
        return answer

    def _save_yaml(self) -> None:
        """Write the captured steps to a YAML config file."""
        if not self.steps:
            print("\nNo steps captured. No YAML file written.")
            return

        config = {
            "name": self.name,
            "defaults": self.defaults,
            "environment": {"buttonpath": self.buttonpath},
            "steps": [],
        }

        for step in self.steps:
            entry = {"button": step.button}
            if step.command != "click":
                entry["command"] = step.command
            if step.text:
                entry["text"] = step.text
            if step.enter:
                entry["enter"] = True
            if step.timeout != "inf":
                entry["timeout"] = step.timeout
            config["steps"].append(entry)

        yaml_path = os.path.join(self.outdir, "config.yaml")
        with open(yaml_path, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        print(f"\nYAML config saved: {yaml_path}")
        print(f"  {len(self.steps)} step(s) total.")


# ---------------------------------------------------------------------------
# Crop dialog (tkinter) — must run on the main thread
# ---------------------------------------------------------------------------


def _show_crop_dialog(
    full_screenshot: Image.Image,
    click_x: int,
    click_y: int,
    step_index: int = 0,
) -> tuple[str, str | None, str | None, bool, Image.Image] | None:
    """Show the crop dialog on the main thread.

    Returns (action, name, command, text, enter, cropped_image) or None if cancelled.
    action is "save" or "delete" (delete = remove last step, then save this one).
    """
    left = max(0, click_x - ZOOM_HALF)
    top = max(0, click_y - ZOOM_HALF)
    right = min(full_screenshot.width, click_x + ZOOM_HALF)
    bottom = min(full_screenshot.height, click_y + ZOOM_HALF)

    zoomed = full_screenshot.crop((left, top, right, bottom))
    zoomed_large = zoomed.resize(
        (zoomed.width * ZOOM_SCALE, zoomed.height * ZOOM_SCALE), Image.NEAREST
    )

    root = tk.Toplevel()
    root.title("Capture Button — crop and name")
    root.attributes("-topmost", True)
    root.grab_set()  # modal — blocks interaction with other windows

    canvas = tk.Canvas(
        root,
        width=zoomed_large.width,
        height=zoomed_large.height,
        cursor="crosshair",
    )
    canvas.pack()

    photo = ImageTk.PhotoImage(zoomed_large)
    canvas.create_image(0, 0, anchor="nw", image=photo)
    canvas.image = photo  # prevent garbage collection

    state = {"start_x": None, "start_y": None, "rect_id": None, "crop_box": None}

    def on_press(event):
        state["start_x"] = event.x
        state["start_y"] = event.y
        if state["rect_id"]:
            canvas.delete(state["rect_id"])

    def on_drag(event):
        if state["start_x"] is not None:
            if state["rect_id"]:
                canvas.delete(state["rect_id"])
            state["rect_id"] = canvas.create_rectangle(
                state["start_x"], state["start_y"], event.x, event.y,
                outline="red", width=2,
            )

    def on_release(event):
        if state["start_x"] is not None:
            x1 = min(state["start_x"], event.x)
            y1 = min(state["start_y"], event.y)
            x2 = max(state["start_x"], event.x)
            y2 = max(state["start_y"], event.y)
            if x2 > x1 and y2 > y1:
                state["crop_box"] = (x1, y1, x2, y2)

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)

    frame = tk.Frame(root)
    frame.pack(pady=10, padx=10)

    tk.Label(frame, text="Button name:").grid(row=0, column=0, sticky="w")
    name_var = tk.StringVar()
    name_entry = tk.Entry(frame, textvariable=name_var, width=30)
    name_entry.grid(row=0, column=1, padx=5)

    tk.Label(frame, text="Command:").grid(row=1, column=0, sticky="w")
    command_var = tk.StringVar(value="click")
    tk.OptionMenu(
        frame,
        command_var,
        "click",
        "click_double",
        "wait_appear",
        "wait_disappear",
        "click_and_type",
    ).grid(row=1, column=1, padx=5, sticky="w")

    tk.Label(frame, text="Text (for click_and_type):").grid(row=2, column=0, sticky="w")
    text_var = tk.StringVar()
    tk.Entry(frame, textvariable=text_var, width=30).grid(row=2, column=1, padx=5)

    enter_var = tk.BooleanVar(value=False)
    tk.Checkbutton(frame, text="Press Enter after typing", variable=enter_var).grid(
        row=3, column=0, columnspan=2, sticky="w"
    )

    # Show how many steps have been saved so far
    tk.Label(frame, text=f"Steps saved so far: {step_index}").grid(
        row=4, column=0, columnspan=2, sticky="w"
    )

    result = {"value": None}

    def _validate_and_collect():
        """Shared validation for OK and Delete buttons."""
        name = name_var.get().strip()
        if not name:
            messagebox.showwarning("Name required", "Please enter a button name.")
            return None
        if not state["crop_box"]:
            messagebox.showwarning("Crop required", "Please draw a rectangle on the image.")
            return None

        x1, y1, x2, y2 = state["crop_box"]
        ox1 = x1 // ZOOM_SCALE + left
        oy1 = y1 // ZOOM_SCALE + top
        ox2 = x2 // ZOOM_SCALE + left
        oy2 = y2 // ZOOM_SCALE + top

        cropped = full_screenshot.crop((ox1, oy1, ox2, oy2))
        command = command_var.get()
        text = text_var.get().strip() if command == "click_and_type" else None
        enter = enter_var.get()
        return (name, command, text, enter, cropped)

    def on_ok():
        vals = _validate_and_collect()
        if vals is None:
            return
        name, command, text, enter, cropped = vals
        result["value"] = ("save", name, command, text, enter, cropped)
        root.destroy()

    def on_skip():
        result["value"] = ("skip", None, None, None, None, None)
        root.destroy()

    def on_cancel():
        root.destroy()

    btn_frame = tk.Frame(root)
    btn_frame.pack(pady=5)
    tk.Button(btn_frame, text="OK", command=on_ok, width=10).pack(side="left", padx=5)
    tk.Button(btn_frame, text="Skip this step", command=on_skip, width=14).pack(
        side="left", padx=5
    )
    tk.Button(btn_frame, text="Cancel", command=on_cancel, width=10).pack(
        side="left", padx=5
    )

    name_entry.focus_set()
    root.wait_window()  # block until the Toplevel is destroyed
    return result["value"]


# ---------------------------------------------------------------------------
# Entry point for capture mode
# ---------------------------------------------------------------------------


def run_capture() -> int:
    """Run the capture session. Returns exit code."""
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    outdir = filedialog.askdirectory(
        title="Choose output directory for the new YAML config and buttons folder"
    )

    if not outdir:
        root.destroy()
        print("No directory selected. Exiting.")
        return 1

    config_path = os.path.join(outdir, "config.yaml")
    session_name = "Captured script"
    session_defaults = {"timeout": "inf", "confidence": 0.8}
    session_steps: list[CapturedStep] = []
    run_existing_steps = False

    # Default button path for a new capture session
    buttonpath = os.path.join(outdir, "buttons")

    if os.path.isfile(config_path):
        answer = messagebox.askyesnocancel(
            "Existing config found",
            "config.yaml already exists in this folder.\n\n"
            "Yes: Continue working on the existing config\n"
            "No: Archive existing config and start a new one\n"
            "Cancel: Abort capture mode",
        )

        if answer is None:
            root.destroy()
            print("Capture mode cancelled by user.")
            return 1

        if answer is False:
            legacy_path = _next_legacy_config_path(outdir)
            os.replace(config_path, legacy_path)
            print(f"Archived existing config: {legacy_path}")
        else:
            with open(config_path, "r", encoding="utf-8") as f:
                existing = yaml.safe_load(f) or {}

            session_name = existing.get("name") or "Captured script"
            existing_defaults = existing.get("defaults")
            if isinstance(existing_defaults, dict):
                session_defaults = existing_defaults

            env = existing.get("environment")
            if isinstance(env, dict) and isinstance(env.get("buttonpath"), str):
                bp = env["buttonpath"]
                if os.path.isabs(bp):
                    buttonpath = bp
                else:
                    buttonpath = os.path.normpath(os.path.join(outdir, bp))

            for raw_step in existing.get("steps", []):
                if not isinstance(raw_step, dict) or "button" not in raw_step:
                    continue
                session_steps.append(
                    CapturedStep(
                        button=raw_step["button"],
                        command=raw_step.get("command", "click"),
                        text=raw_step.get("text"),
                        enter=bool(raw_step.get("enter", False)),
                        timeout=raw_step.get("timeout", "inf"),
                    )
                )
            print(f"Loaded existing config with {len(session_steps)} step(s): {config_path}")

            if session_steps:
                run_existing_steps = messagebox.askyesno(
                    "Run existing steps first?",
                    "Do you want to run the existing steps before starting new recordings?",
                )

    buttons_dir = buttonpath
    os.makedirs(buttons_dir, exist_ok=True)
    root.destroy()

    print(f"Output directory: {outdir}")
    print(f"Buttons folder:   {buttons_dir}")

    session = CaptureSession(
        outdir=outdir,
        buttons_dir=buttons_dir,
        buttonpath=buttons_dir,
        name=session_name,
        defaults=session_defaults,
        steps=session_steps,
    )

    if run_existing_steps and session_steps:
        print("\nRunning existing steps before recording...")
        try:
            from .loader import load_config
            from .runner import Runner

            parsed = load_config(config_path)
            success = Runner(parsed).run()
            if success:
                print("Existing steps completed. Starting recording mode.")
            else:
                print("Existing steps failed. Starting recording mode anyway.")
        except Exception as e:
            print(f"Could not run existing steps: {e}")
            print("Starting recording mode anyway.")

    session.start()
    return 0