"""Load and validate a YAML config file into a Config object."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import List, Union

import yaml

from .config import (
    ButtonStep,
    Config,
    Defaults,
    Environment,
    RepeatStep,
    WaitStep,
    parse_step,
)


@dataclass
class ParsedConfig:
    """A fully validated and resolved config ready for execution."""

    name: str | None
    environment: Environment
    defaults: Defaults
    steps: List[Union[ButtonStep, WaitStep, RepeatStep]]


def load_config(path: str) -> ParsedConfig:
    """Load a YAML config file, validate it, and resolve button paths.

    Args:
        path: Path to the YAML config file.

    Returns:
        A ParsedConfig with validated steps and resolved button image paths.

    Raises:
        FileNotFoundError: If the config file does not exist.
        pydantic.ValidationError: If the config is invalid.
        ValueError: If a step is unrecognized or a button image is missing.
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError("Config file must contain a YAML mapping at the top level.")

    # Validate top-level structure
    config = Config(**raw)

    # Parse each step and apply defaults
    parsed_steps: list[ButtonStep | WaitStep | RepeatStep] = []
    for i, raw_step in enumerate(config.steps):
        try:
            step = parse_step(raw_step, config.defaults)
        except Exception as e:
            raise ValueError(f"Error in step {i + 1}: {e}") from e
        parsed_steps.append(step)

    # Resolve button image paths to absolute paths
    buttonpath = config.environment.buttonpath
    for step in parsed_steps:
        if isinstance(step, ButtonStep):
            step.button = os.path.join(buttonpath, step.button)

    return ParsedConfig(
        name=config.name,
        environment=config.environment,
        defaults=config.defaults,
        steps=parsed_steps,
    )