"""Initial workflow variables declared in configuration.

Canonical location: top-level ``variables`` (written by the workflow editor and
the bundled samples). ``workflow.variables`` is accepted as a legacy alias; when
both are present, shared keys must carry equal values.

Precedence at run time (lowest to highest):
  1. Config-declared variables (deep-copied per run; never written back to config).
  2. Explicit runtime overrides supplied by the caller (for example step tests).
  3. Runtime metadata written by the engine (``manual_run``, loop variables,
     restart counter). Config may not declare these names, so they never clash.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from winflow.core.errors import SchemaValidationError

VARIABLE_KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

# Names owned by the runtime; configuration may not declare them.
RESERVED_VARIABLE_NAMES: frozenset[str] = frozenset({"manual_run"})


def _variable_blocks(config: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    blocks: list[tuple[str, dict[str, Any]]] = []
    if "variables" in config:
        top = config["variables"]
        if not isinstance(top, dict):
            raise SchemaValidationError("'variables' must be an object of name/value pairs")
        blocks.append(("variables", top))
    workflow = config.get("workflow")
    if isinstance(workflow, dict) and "variables" in workflow:
        legacy = workflow["variables"]
        if not isinstance(legacy, dict):
            raise SchemaValidationError("'workflow.variables' must be an object of name/value pairs")
        blocks.append(("workflow.variables", legacy))
    return blocks


def config_variables(config: dict[str, Any]) -> dict[str, Any]:
    """Return a validated, deep-copied mapping of config-declared variables.

    Raises:
        SchemaValidationError: On malformed blocks, invalid keys, reserved names,
            or conflicting values between the canonical and legacy locations.
    """
    merged: dict[str, Any] = {}
    for path, block in _variable_blocks(config):
        for key, value in block.items():
            if not isinstance(key, str) or not VARIABLE_KEY_PATTERN.match(key):
                raise SchemaValidationError(
                    f"'{path}' key {key!r} is invalid: use letters, digits, and underscores, "
                    "starting with a letter (so it can be referenced as {{ name }})."
                )
            if key in RESERVED_VARIABLE_NAMES:
                raise SchemaValidationError(
                    f"'{path}' key {key!r} is reserved for the runtime and cannot be declared."
                )
            if key in merged and merged[key] != value:
                raise SchemaValidationError(
                    f"Variable {key!r} is declared in both 'variables' and 'workflow.variables' "
                    "with different values; keep one declaration."
                )
            merged[key] = value
    return copy.deepcopy(merged)


def initial_variables(
    config: dict[str, Any],
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the starting variable mapping for one run.

    Config declarations are copied first; explicit overrides replace them key by key.
    The result is a fresh dict, so nothing is shared with ``config`` or other runs.
    """
    values = config_variables(config)
    if overrides:
        values.update(copy.deepcopy(overrides))
    return values
