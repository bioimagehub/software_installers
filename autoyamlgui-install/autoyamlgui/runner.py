"""Step executor — runs the parsed config steps in order, handling loops."""

from __future__ import annotations

import logging
import time

from . import automation
from .config import ButtonStep, Command, CommandStep, RepeatStep, TypeStep, WaitStep, WindowStep
from .loader import ParsedConfig

logger = logging.getLogger(__name__)


class Runner:
    """Executes the steps in a ParsedConfig sequentially.

    Supports ``repeat`` steps that jump back to an earlier step index.
    """

    def __init__(self, config: ParsedConfig):
        self.config = config
        self.index = 0
        # Track active loops: list of (from_index_0based, remaining_iterations, delay)
        self._loop_stack: list[tuple[int, int, float]] = []

    def run(self) -> bool:
        """Execute all steps. Returns True if all succeeded, False on any failure."""
        name = self.config.name or "unnamed"
        logger.info("Starting script: %s (%d steps)", name, len(self.config.steps))

        while self.index < len(self.config.steps):
            step = self.config.steps[self.index]
            step_num = self.index + 1  # 1-based for logging

            if isinstance(step, WaitStep):
                logger.info("Step %d: wait %.1fs", step_num, step.wait)
                time.sleep(step.wait)
                self.index += 1

            elif isinstance(step, WindowStep):
                ok = self._run_window_step(step_num, step)
                if not ok:
                    logger.error("Step %d failed, aborting.", step_num)
                    return False
                self.index += 1

            elif isinstance(step, TypeStep):
                ok = self._run_type_step(step_num, step)
                if not ok:
                    logger.error("Step %d failed, aborting.", step_num)
                    return False
                self.index += 1

            elif isinstance(step, ButtonStep):
                ok = self._run_button_step(step_num, step)
                if not ok:
                    logger.error("Step %d failed, aborting.", step_num)
                    return False
                self.index += 1

            elif isinstance(step, CommandStep):
                ok = self._run_command_step(step_num, step)
                if not ok:
                    logger.error("Step %d failed, aborting.", step_num)
                    return False
                self.index += 1

            elif isinstance(step, RepeatStep):
                self._handle_repeat(step_num, step)

            else:
                logger.error("Step %d: unknown step type: %s", step_num, type(step))
                return False

            # Check if a loop needs to repeat
            self._check_loops()

        logger.info("Script '%s' completed successfully.", name)
        return True

    # -------------------------------------------------------------------
    # Button step
    # -------------------------------------------------------------------

    def _run_window_step(self, step_num: int, step: WindowStep) -> bool:
        """Execute a window step and return True on success."""
        logger.info(
            "Step %d: window — %s (timeout=%.1fs, action=%s)",
            step_num,
            step.window,
            step.timeout,
            step.action,
        )
        return automation.wait_for_window(step.window, step.timeout, step.action)

    def _run_command_step(self, step_num: int, step: CommandStep) -> bool:
        """Execute a shell command step and return True on success."""
        logger.info(
            "Step %d: cmd — %s (background=%s)",
            step_num,
            step.cmd,
            step.background,
        )
        return automation.run_command(step.cmd, background=step.background)

    def _run_type_step(self, step_num: int, step: TypeStep) -> bool:
        """Execute a typing-only step and return True on success."""
        logger.info("Step %d: type — %r (enter=%s)", step_num, step.type, step.enter)
        return automation.type_text(step.type, step.enter)

    def _run_button_step(self, step_num: int, step: ButtonStep) -> bool:
        """Execute a button step and return True on success."""
        cmd = step.command
        logger.info(
            "Step %d: %s — %s (timeout=%.1fs, confidence=%.2f)",
            step_num,
            cmd.value,
            step.button,
            step.timeout,
            step.confidence or 0.8,
        )

        buttonpath = self.config.environment.buttonpath

        if cmd == Command.click:
            return automation.click_button(
                step.button,
                buttonpath,
                step.confidence or 0.8,
                step.timeout,
            )
        elif cmd == Command.click_double:
            return automation.click_double_button(
                step.button,
                buttonpath,
                step.confidence or 0.8,
                step.timeout,
            )
        elif cmd == Command.wait_appear:
            return automation.wait_appear(
                step.button,
                buttonpath,
                step.confidence or 0.8,
                step.timeout,
            )
        elif cmd == Command.wait_disappear:
            return automation.wait_disappear(
                step.button,
                buttonpath,
                step.confidence or 0.8,
                step.timeout,
            )
        elif cmd == Command.click_and_type:
            return automation.click_and_type(
                step.button,
                buttonpath,
                step.text or "",
                step.enter,
                step.confidence or 0.8,
                step.timeout,
            )
        elif cmd == Command.click_if_exists:
            return automation.click_if_exists(
                step.button,
                buttonpath,
                step.confidence or 0.8,
            )
        else:
            logger.error("Unknown command: %s", cmd)
            return False

    # -------------------------------------------------------------------
    # Repeat step
    # -------------------------------------------------------------------

    def _handle_repeat(self, step_num: int, step: RepeatStep) -> None:
        """Set up a loop: jump back to the target step index."""
        from_0based = step.from_step - 1  # convert 1-based to 0-based

        if from_0based < 0 or from_0based >= len(self.config.steps):
            logger.error(
                "Step %d: repeat.from=%d is out of range (1-%d)",
                step_num,
                step.from_step,
                len(self.config.steps),
            )
            return

        # If we already have an active loop for this repeat step, decrement
        for i, (target, remaining, delay) in enumerate(self._loop_stack):
            if target == from_0based and remaining > 0:
                # This is a continuation — decrement handled in _check_loops
                return

        # New loop: push onto stack
        self._loop_stack.append((from_0based, step.times, step.delay))
        logger.info(
            "Step %d: repeat — jump to step %d, %d times, delay=%.1fs",
            step_num,
            step.from_step,
            step.times,
            step.delay,
        )
        self.index = from_0based

    def _check_loops(self) -> None:
        """After each step, check if a loop should iterate or end."""
        if not self._loop_stack:
            return

        # Peek at the top of the loop stack
        target, remaining, delay = self._loop_stack[-1]

        # If we've reached the repeat step again, it's time to iterate
        # The repeat step itself is at index > target
        if self.index > target and remaining > 0:
            if delay > 0:
                logger.debug("Loop delay: %.1fs", delay)
                time.sleep(delay)

            remaining -= 1
            if remaining > 0:
                self._loop_stack[-1] = (target, remaining, delay)
                logger.info("Loop: %d iterations remaining", remaining)
                self.index = target
            else:
                # Loop finished — pop and continue past the repeat step
                self._loop_stack.pop()
                logger.info("Loop complete")
                # Move past the repeat step
                # Find the repeat step index and skip it
                for i in range(target + 1, len(self.config.steps)):
                    if isinstance(self.config.steps[i], RepeatStep):
                        self.index = i + 1
                        return
                self.index = len(self.config.steps)