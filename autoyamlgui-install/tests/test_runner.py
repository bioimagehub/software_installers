"""Tests for the Runner — step dispatch and repeat loop logic.

Automation functions are mocked so no real screen interaction happens.
"""

from unittest.mock import MagicMock, patch

import pytest

from autoyamlgui.config import ButtonStep, Command, RepeatStep, WaitStep
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
        call_kwargs = mock_automation.click_and_type.call_args
        assert call_kwargs[0][1] == "hello"  # text arg
        assert call_kwargs[0][2] is True  # enter arg

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