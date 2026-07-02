"""Pydantic models for the autoyamlgui YAML config schema."""

from __future__ import annotations

import re
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------

_DURATION_RE = re.compile(r"^(\d+(?:\.\d+)?)(ms|s|m|h|d)$", re.IGNORECASE)
_UNIT_TO_SECONDS = {
    "ms": 0.001,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
}


def parse_duration(value: str | float) -> float:
    """Parse a duration string like '5s', '1m', '500ms', 'inf' into seconds.

    A bare number is interpreted as seconds. ``inf`` returns ``float('inf')``.
    """
    if isinstance(value, (int, float)):
        return float(value)

    value = value.strip().lower()

    if value == "inf":
        return float("inf")

    match = _DURATION_RE.match(value)
    if not match:
        raise ValueError(
            f"Invalid duration '{value}'. "
            "Expected a number with unit (ms, s, m, h, d) or 'inf'."
        )

    number = float(match.group(1))
    unit = match.group(2).lower()
    return number * _UNIT_TO_SECONDS[unit]


# ---------------------------------------------------------------------------
# Step models
# ---------------------------------------------------------------------------


class Command(str, Enum):
    """Actions that can be performed on a button image."""

    click = "click"
    click_double = "click_double"
    wait_appear = "wait_appear"
    wait_disappear = "wait_disappear"
    click_and_type = "click_and_type"


class ButtonStep(BaseModel):
    """Find a button image on screen and perform an action on it."""

    button: str = Field(..., description="Button image filename")
    command: Command = Field(default=Command.click, description="Action to perform")
    timeout: float = Field(default=float("inf"), description="Max wait time")
    confidence: float | None = Field(
        default=None, description="Match threshold 0-1 (overrides defaults)"
    )
    text: str | None = Field(
        default=None, description="Text to type (for click_and_type)"
    )
    enter: bool = Field(
        default=False, description="Press Enter after typing (for click_and_type)"
    )

    @field_validator("timeout", mode="before")
    @classmethod
    def validate_timeout(cls, v):
        if v is None:
            return float("inf")
        return parse_duration(v)

    @model_validator(mode="after")
    def validate_click_and_type(self):
        if self.command == Command.click_and_type and not self.text:
            raise ValueError("'text' is required when command is 'click_and_type'")
        return self


class WaitStep(BaseModel):
    """Pause execution for a fixed duration."""

    wait: str | float = Field(..., description="Duration to wait")

    @field_validator("wait", mode="before")
    @classmethod
    def validate_wait(cls, v):
        return parse_duration(v)


class RepeatStep(BaseModel):
    """Jump back to an earlier step and run a number of iterations."""

    repeat: dict = Field(..., description="Loop configuration")

    @model_validator(mode="after")
    def validate_repeat(self):
        r = self.repeat
        if "from" not in r:
            raise ValueError("'repeat.from' is required")
        if "times" not in r:
            raise ValueError("'repeat.times' is required")
        if not isinstance(r["from"], int) or r["from"] < 1:
            raise ValueError("'repeat.from' must be a positive 1-based step index")
        if not isinstance(r["times"], int) or r["times"] < 1:
            raise ValueError("'repeat.times' must be a positive integer")
        if "delay" in r:
            r["delay"] = parse_duration(r["delay"])
        return self

    @property
    def from_step(self) -> int:
        return self.repeat["from"]

    @property
    def times(self) -> int:
        return self.repeat["times"]

    @property
    def delay(self) -> float:
        return self.repeat.get("delay", 0.0)


# Discriminated union: detect step type by which key is present
Step = Annotated[
    Union[ButtonStep, WaitStep, RepeatStep],
    Field(discriminator=None),
]


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------


class Defaults(BaseModel):
    """Default values inherited by every step."""

    timeout: float = Field(default=float("inf"), description="Default max wait time")
    confidence: float = Field(default=0.8, description="Default match threshold 0-1")

    @field_validator("timeout", mode="before")
    @classmethod
    def validate_timeout(cls, v):
        if v is None:
            return float("inf")
        return parse_duration(v)

    @field_validator("confidence")
    @classmethod
    def validate_confidence(cls, v):
        if not 0.0 <= v <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        return v


class Environment(BaseModel):
    """Global environment settings."""

    buttonpath: str = Field(..., description="Directory where button images are stored")


class Config(BaseModel):
    """Top-level YAML config."""

    name: str | None = Field(default=None, description="Script label for logging")
    defaults: Defaults = Field(default_factory=Defaults)
    environment: Environment
    steps: list[dict] = Field(..., description="Ordered list of steps to execute")

    @field_validator("steps")
    @classmethod
    def validate_steps(cls, v):
        if not v:
            raise ValueError("'steps' must contain at least one step")
        return v


# ---------------------------------------------------------------------------
# Step parsing helper
# ---------------------------------------------------------------------------


def parse_step(raw: dict, defaults: Defaults) -> ButtonStep | WaitStep | RepeatStep:
    """Parse a raw step dict into the appropriate step model, applying defaults."""
    if "button" in raw:
        step = ButtonStep(**raw)
        # Apply defaults where not explicitly set
        if step.confidence is None:
            step.confidence = defaults.confidence
        if step.timeout == float("inf") and defaults.timeout != float("inf"):
            step.timeout = defaults.timeout
        return step
    elif "wait" in raw:
        return WaitStep(**raw)
    elif "repeat" in raw:
        return RepeatStep(**raw)
    else:
        raise ValueError(
            f"Unrecognized step: {raw}. "
            "Expected one of: 'button', 'wait', 'repeat'."
        )