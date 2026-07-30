"""Tests for the Runner — step dispatch and repeat loop logic.

Automation functions are mocked so no real screen interaction happens.
"""

from unittest.mock import MagicMock, patch

import pytest

from autoyamlgui.config import ButtonStep, Command, CommandStep, RepeatStep, TypeStep, WaitStep, WindowStep
from autoyamlgui.loader import ParsedConfig
from autoyamlgui.runner import Runner


def make_config(steps, name="test"):
    """Build a ParsedConfig with minimal environment."""
    from autoyamlgui.config import Defaults, Environment

    return ParsedConfig(
        name=name,
        environment=Environment(buttonpath="/tmp"),
        defaults=Defaults(),
        steps=steps,
    )


# ---------------------------------------------------------------------------
# Basic step dispatch
# ---------------------------------------------------------------------------


class TestRunnerDispatch:
    @patch("autoyamlgui.runner.automation")
    def test_click_step(self, mock_automation):
        mock_automation.click_button.return_value = True
        step = ButtonStep(button="/tmp/start.png", command=Command.click)
        config = make_config([step])
        runner = Runner(config)
        assert runner.run() is True
        mock_automation.click_button.assert_called_once()

    @patch("autoyamlgui.runner.automation")
    def test_click_double_step(self, mock_automation):
        mock_automation.click_double_button.return_value = True
        step = ButtonStep(button="/tmp/start.png", command=Command.click_double)
        config = make_config([step])
        runner = Runner(config)
        assert runner.run() is True
        mock_automation.click_double_button.assert_called_once()

    @patch("autoyamlgui.runner.automation")
    def test_wait_appear_step(self, mock_automation):
        mock_automation.wait_appear.return_value = True
        step = ButtonStep(button="/tmp/x.png", command=Command.wait_appear)
        config = make_config([step])
        runner = Runner(config)
        assert runner.run() is True
        mock_automation.wait_appear.assert_called_once()

    @patch("autoyamlgui.runner.automation")
    def test_wait_disappear_step(self, mock_automation):
        mock_automation.wait_disappear.return_value = True
        step = ButtonStep(button="/tmp/x.png", command=Command.wait_disappear)
        config = make_config([step])
        runner = Runner(config)
        assert runner.run() is True
        mock_automation.wait_disappear.assert_called_once()

    @patch("autoyamlgui.runner.automation")
    def test_click_and_type_step(self, mock_automation):
        mock_automation.click_and_type.return_value = True
        step = ButtonStep(
            button="/tmp/search.png",
            command=Command.click_and_type,
            text="hello",
            enter=True,
        )
        config = make_config([step])
        runner = Runner(config)
        assert runner.run() is True
        mock_automation.click_and_type.assert_called_once()
        call_args = mock_automation.click_and_type.call_args[0]
        assert call_args[0] == "/tmp/search.png"
        assert call_args[1] == "/tmp"
        assert call_args[2] == "hello"
        assert call_args[3] is True

    @patch("autoyamlgui.runner.automation")
    def test_click_if_exists_step(self, mock_automation):
        mock_automation.click_if_exists.return_value = True
        step = ButtonStep(button="/tmp/optional.png", command=Command.click_if_exists)
        config = make_config([step])
        runner = Runner(config)
        assert runner.run() is True
        mock_automation.click_if_exists.assert_called_once_with(
            "/tmp/optional.png",
            "/tmp",
            0.8,
        )

    @patch("autoyamlgui.runner.automation")
    def test_window_step(self, mock_automation):
        mock_automation.wait_for_window.return_value = True
        step = WindowStep(window="* - Etomo", timeout="5s")
        config = make_config([step])
        runner = Runner(config)
        assert runner.run() is True
        mock_automation.wait_for_window.assert_called_once_with("* - Etomo", 5.0, "focus")

    @patch("autoyamlgui.runner.automation")
    def test_window_step_minimize(self, mock_automation):
        mock_automation.wait_for_window.return_value = True
        step = WindowStep(window="* - Etomo", timeout="5s", action="minimize")
        config = make_config([step])
        runner = Runner(config)
        assert runner.run() is True
        mock_automation.wait_for_window.assert_called_once_with("* - Etomo", 5.0, "minimize")

    @patch("autoyamlgui.runner.automation")
    def test_window_step_close_all(self, mock_automation):
        mock_automation.wait_for_window.return_value = True
        step = WindowStep(window="* - Etomo", timeout="5s", action="close_all")
        config = make_config([step])
        runner = Runner(config)
        assert runner.run() is True
        mock_automation.wait_for_window.assert_called_once_with("* - Etomo", 5.0, "close_all")

    @patch("autoyamlgui.runner.automation")
    def test_command_step(self, mock_automation):
        mock_automation.run_command.return_value = True
        step = CommandStep(cmd="start_program.exe")
        config = make_config([step])
        runner = Runner(config)
        assert runner.run() is True
        mock_automation.run_command.assert_called_once_with(
            "start_program.exe",
            background=False,
        )

    @patch("autoyamlgui.runner.automation")
    def test_command_step_background(self, mock_automation):
        mock_automation.run_command.return_value = True
        step = CommandStep(cmd="start_program.exe", background=True)
        config = make_config([step])
        runner = Runner(config)
        assert runner.run() is True
        mock_automation.run_command.assert_called_once_with(
            "start_program.exe",
            background=True,
        )

    @patch("autoyamlgui.runner.automation")
    def test_type_step(self, mock_automation):
        mock_automation.type_text.return_value = True
        step = TypeStep(type="notepad", enter=True)
        config = make_config([step])
        runner = Runner(config)
        assert runner.run() is True
        mock_automation.type_text.assert_called_once_with("notepad", True)

    @patch("autoyamlgui.runner.automation")
    def test_failed_step_aborts(self, mock_automation):
        mock_automation.click_button.return_value = False
        step = ButtonStep(button="/tmp/x.png", command=Command.click)
        config = make_config([step])
        runner = Runner(config)
        assert runner.run() is False

    @patch("autoyamlgui.runner.time.sleep")
    @patch("autoyamlgui.runner.automation")
    def test_wait_step(self, mock_automation, mock_sleep):
        step = WaitStep(wait=5.0)
        config = make_config([step])
        runner = Runner(config)
        assert runner.run() is True
        mock_sleep.assert_called_once_with(5.0)


# ---------------------------------------------------------------------------
# Repeat / loop logic
# ---------------------------------------------------------------------------


class TestRunnerRepeat:
    @patch("autoyamlgui.runner.time.sleep")
    @patch("autoyamlgui.runner.automation")
    def test_repeat_loops_correct_times(self, mock_automation, mock_sleep):
        """Steps 1 (click) and 2 (wait) should run 1 + 3*2 = 7 times total
        when repeat from=1 times=3.

        Flow: step1, step2, repeat(from=1,times=3) → step1, step2, repeat → ...
        """
        mock_automation.click_button.return_value = True

        step1 = ButtonStep(button="/tmp/a.png", command=Command.click)
        step2 = WaitStep(wait=1.0)
        step3 = RepeatStep(repeat={"from": 1, "times": 3, "delay": 0})

        config = make_config([step1, step2, step3])
        runner = Runner(config)
        assert runner.run() is True

        # Initial run: step1 + step2 = 1 click
        # Then 3 loop iterations: step1 + step2 each = 3 clicks
        # Total clicks = 1 + 3 = 4
        assert mock_automation.click_button.call_count == 4

    @patch("autoyamlgui.runner.time.sleep")
    @patch("autoyamlgui.runner.automation")
    def test_repeat_with_delay(self, mock_automation, mock_sleep):
        mock_automation.click_button.return_value = True

        step1 = ButtonStep(button="/tmp/a.png", command=Command.click)
        step2 = RepeatStep(repeat={"from": 1, "times": 2, "delay": 2.0})

        config = make_config([step1, step2])
        runner = Runner(config)
        assert runner.run() is True

        # Should have slept for the wait steps (none here) and loop delays
        # The delay sleep should have been called with 2.0
        delay_calls = [c for c in mock_sleep.call_args_list if c.args[0] == 2.0]
        assert len(delay_calls) >= 1

    @patch("autoyamlgui.runner.time.sleep")
    @patch("autoyamlgui.runner.automation")
    def test_repeat_one_time(self, mock_automation, mock_sleep):
        mock_automation.click_button.return_value = True

        step1 = ButtonStep(button="/tmp/a.png", command=Command.click)
        step2 = RepeatStep(repeat={"from": 1, "times": 1})

        config = make_config([step1, step2])
        runner = Runner(config)
        assert runner.run() is True

        # Initial + 1 repeat = 2 clicks
        assert mock_automation.click_button.call_count == 2