"""Load and validate a YAML config file into a Config object."""

from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass
from typing import List, Union

import yaml

from .config import (
    ButtonStep,
    CommandStep,
    Config,
    Defaults,
    Environment,
    RepeatStep,
    Variables,
    WaitStep,
    WindowStep,
    parse_step,
)


@dataclass
class VariableRun:
    """A single execution run with its own variable context."""

    context: dict[str, object]
    steps: List[Union[ButtonStep, WaitStep, WindowStep, CommandStep, RepeatStep]]


@dataclass
class ParsedConfig:
    """A fully validated and resolved config ready for execution."""

    name: str | None
    environment: Environment
    defaults: Defaults
    steps: List[Union[ButtonStep, WaitStep, WindowStep, CommandStep, RepeatStep]]
    variable_runs: List[VariableRun] | None = None

    @property
    def effective_runs(self) -> List[VariableRun]:
        if self.variable_runs:
            return self.variable_runs
        return [VariableRun(context={}, steps=self.steps)]


_VARIABLE_PATTERN = re.compile(r"\{\{\s*([A-Za-z0-9_\.:-]+)\s*\}\}")


def _read_text_file(path: str) -> str:
    """Read a text file using a robust fallback chain for common encodings."""
    with open(path, "rb") as handle:
        raw_bytes = handle.read()

    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return raw_bytes.decode(encoding)
        except UnicodeDecodeError:
            continue

    return raw_bytes.decode("utf-8", errors="replace")


def _apply_context(value, context: dict[str, object]):
    """Recursively substitute variable placeholders in a YAML value."""
    if isinstance(value, str):
        return _VARIABLE_PATTERN.sub(
            lambda match: str(context.get(match.group(1), match.group(0))),
            value,
        )
    if isinstance(value, list):
        return [_apply_context(item, context) for item in value]
    if isinstance(value, dict):
        return {key: _apply_context(item, context) for key, item in value.items()}
    return value


def _strip_surrounding_quotes(value: str) -> str:
    """Remove matching single or double quotes from the start and end of a string."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _resolve_variable_values(variables: Variables | None, config_path: str) -> list[tuple[dict[str, object], list[dict]]]:
    """Resolve the variable values and their corresponding expanded step lists."""
    if not variables:
        return [(dict(), [])]

    if variables.source == "list":
        values = variables.values or []
    else:
        base_dir = os.path.dirname(config_path)
        path = variables.path or ""
        if not os.path.isabs(path):
            path = os.path.join(base_dir, path)
        contents = _read_text_file(path)
        values = [
            _strip_surrounding_quotes(line.strip())
            for line in contents.splitlines()
            if line.strip()
        ]

    if not values:
        return []

    runs: list[tuple[dict[str, object], list[dict]]] = []
    for index, value in enumerate(values, start=1):
        context = {
            variables.name: value,
            "index": index,
            "count": len(values),
        }
        runs.append((context, []))
    return runs


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

    raw = yaml.safe_load(_read_text_file(path))

    if not isinstance(raw, dict):
        raise ValueError("Config file must contain a YAML mapping at the top level.")

    # Validate top-level structure
    config = Config(**raw)

    variable_contexts = []
    if config.variables:
        if config.variables.source == "list":
            values = config.variables.values or []
        else:
            base_dir = os.path.dirname(path)
            values_path = config.variables.path or ""
            if not os.path.isabs(values_path):
                values_path = os.path.join(base_dir, values_path)
            contents = _read_text_file(values_path)
            values = [
                _strip_surrounding_quotes(line.strip())
                for line in contents.splitlines()
                if line.strip()
            ]

        if values:
            for index, value in enumerate(values, start=1):
                variable_contexts.append(
                    {
                        config.variables.name: value,
                        "index": index,
                        "count": len(values),
                    }
                )

    if not variable_contexts:
        variable_contexts = [{}]

    variable_runs: list[VariableRun] = []
    for context in variable_contexts:
        expanded_raw = copy.deepcopy(raw)
        expanded_raw = _apply_context(expanded_raw, context)
        expanded_config = Config(**expanded_raw)

        parsed_steps: list[ButtonStep | WaitStep | WindowStep | CommandStep | RepeatStep] = []
        for i, raw_step in enumerate(expanded_config.steps):
            try:
                step = parse_step(raw_step, expanded_config.defaults)
            except Exception as e:
                raise ValueError(f"Error in step {i + 1}: {e}") from e
            parsed_steps.append(step)

        variable_runs.append(VariableRun(context=context, steps=parsed_steps))

    return ParsedConfig(
        name=config.name,
        environment=config.environment,
        defaults=config.defaults,
        steps=variable_runs[0].steps if variable_runs else [],
        variable_runs=variable_runs,
    )