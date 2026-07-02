"""Tests for the Pydantic config schema and duration parsing."""

import pytest
from pydantic import ValidationError

from autoyamlgui.config import (
    ButtonStep,
    Command,
    Defaults,
    RepeatStep,
    WaitStep,
    parse_duration,
    parse_step,
)


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------


class TestParseDuration:
    def test_seconds(self):
        assert parse_duration("5s") == 5.0

    def test_milliseconds(self):
        assert parse_duration("500ms") == 0.5

    def test_minutes(self):
        assert parse_duration("2m") == 120.0

    def test_hours(self):
        assert parse_duration("1h") == 3600.0

    def test_days(self):
        assert parse_duration("1d") == 86400.0

    def test_decimal(self):
        assert parse_duration("1.5s") == 1.5

    def test_inf(self):
        assert parse_duration("inf") == float("inf")

    def test_bare_number(self):
        assert parse_duration(10) == 10.0
        assert parse_duration(2.5) == 2.5

    def test_invalid(self):
        with pytest.raises(ValueError):
            parse_duration("abc")

    def test_no_unit(self):
        with pytest.raises(ValueError):
            parse_duration("5")


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_defaults(self):
        d = Defaults()
        assert d.timeout == float("inf")
        assert d.confidence == 0.8

    def test_custom(self):
        d = Defaults(timeout="30s", confidence=0.9)
        assert d.timeout == 30.0
        assert d.confidence == 0.9

    def test_confidence_out_of_range(self):
        with pytest.raises(ValidationError):
            Defaults(confidence=1.5)

    def test_confidence_negative(self):
        with pytest.raises(ValidationError):
            Defaults(confidence=-0.1)


# ---------------------------------------------------------------------------
# ButtonStep
# ---------------------------------------------------------------------------


class TestButtonStep:
    def test_default_command(self):
        step = ButtonStep(button="start.png")
        assert step.command == Command.click

    def test_wait_disappear(self):
        step = ButtonStep(button="ollama.png", command="wait_disappear", timeout="1m")
        assert step.command == Command.wait_disappear
        assert step.timeout == 60.0

    def test_click_and_type_requires_text(self):
        with pytest.raises(ValidationError):
            ButtonStep(button="search.png", command="click_and_type")

    def test_click_and_type_with_text(self):
        step = ButtonStep(
            button="search.png",
            command="click_and_type",
            text="hello",
            enter=True,
        )
        assert step.text == "hello"
        assert step.enter is True

    def test_timeout_parsed(self):
        step = ButtonStep(button="x.png", timeout="2m")
        assert step.timeout == 120.0


# ---------------------------------------------------------------------------
# WaitStep
# ---------------------------------------------------------------------------


class TestWaitStep:
    def test_wait(self):
        step = WaitStep(wait="5s")
        assert step.wait == 5.0

    def test_wait_inf(self):
        step = WaitStep(wait="inf")
        assert step.wait == float("inf")


# ---------------------------------------------------------------------------
# RepeatStep
# ---------------------------------------------------------------------------


class TestRepeatStep:
    def test_basic(self):
        step = RepeatStep(repeat={"from": 2, "times": 3})
        assert step.from_step == 2
        assert step.times == 3
        assert step.delay == 0.0

    def test_with_delay(self):
        step = RepeatStep(repeat={"from": 1, "times": 5, "delay": "2s"})
        assert step.delay == 2.0

    def test_missing_from(self):
        with pytest.raises(ValidationError):
            RepeatStep(repeat={"times": 3})

    def test_missing_times(self):
        with pytest.raises(ValidationError):
            RepeatStep(repeat={"from": 1})

    def test_invalid_from(self):
        with pytest.raises(ValidationError):
            RepeatStep(repeat={"from": 0, "times": 3})

    def test_invalid_times(self):
        with pytest.raises(ValidationError):
            RepeatStep(repeat={"from": 1, "times": 0})


# ---------------------------------------------------------------------------
# parse_step
# ---------------------------------------------------------------------------


class TestParseStep:
    def test_parse_button_step(self):
        defaults = Defaults()
        step = parse_step({"button": "start.png"}, defaults)
        assert isinstance(step, ButtonStep)
        assert step.command == Command.click
        assert step.confidence == 0.8  # default applied

    def test_parse_wait_step(self):
        defaults = Defaults()
        step = parse_step({"wait": "5s"}, defaults)
        assert isinstance(step, WaitStep)
        assert step.wait == 5.0

    def test_parse_repeat_step(self):
        defaults = Defaults()
        step = parse_step({"repeat": {"from": 2, "times": 3}}, defaults)
        assert isinstance(step, RepeatStep)
        assert step.from_step == 2

    def test_parse_unknown_step(self):
        defaults = Defaults()
        with pytest.raises(ValueError, match="Unrecognized step"):
            parse_step({"unknown": "foo"}, defaults)

    def test_defaults_confidence_applied(self):
        defaults = Defaults(confidence=0.95)
        step = parse_step({"button": "x.png"}, defaults)
        assert step.confidence == 0.95

    def test_explicit_confidence_overrides_default(self):
        defaults = Defaults(confidence=0.95)
        step = parse_step({"button": "x.png", "confidence": 0.7}, defaults)
        assert step.confidence == 0.7