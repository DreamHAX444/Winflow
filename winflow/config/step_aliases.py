"""Canonical step-key normalization shared by the validator and the workflow runner.

Canonical keys:
  * ``timeout_seconds`` (legacy alias: ``timeout``)
  * ``verify`` (legacy alias: ``verification``)

When both a canonical key and its legacy alias are present they must agree;
otherwise the step is ambiguous and is rejected.

Metadata keys describe a step for humans and must never be forwarded to actions
as parameters.
"""

from __future__ import annotations

from typing import Any

from winflow.core.errors import ConfigurationError

TIMEOUT_KEY = "timeout_seconds"
TIMEOUT_ALIAS = "timeout"
VERIFY_KEY = "verify"
VERIFY_ALIAS = "verification"

# Keys that describe a step but are not action parameters.
STEP_METADATA_KEYS: frozenset[str] = frozenset(
    {"name", "step_id", "label", "description"}
)

# Keys the runner consumes structurally; never forwarded as parameters.
STEP_STRUCTURAL_KEYS: frozenset[str] = frozenset(
    {
        "action",
        "params",
        "verify",
        "verification",
        "retry",
        "failure",
        "timeout_seconds",
        "timeout",
        "condition",
        "then",
        "else",
        "steps",
        "do",
        "items",
        "max_iterations",
        "max_items",
    }
)


def _resolve_alias(
    step: dict[str, Any],
    canonical: str,
    legacy: str,
    path: str,
) -> tuple[bool, Any]:
    has_canonical = canonical in step
    has_legacy = legacy in step
    if has_canonical and has_legacy and step[canonical] != step[legacy]:
        raise ConfigurationError(
            f"{path} sets both '{canonical}' and legacy '{legacy}' to different values; "
            f"remove '{legacy}' or make them match."
        )
    if has_canonical:
        return True, step[canonical]
    if has_legacy:
        return True, step[legacy]
    return False, None


def canonical_step(step: dict[str, Any], path: str = "step") -> dict[str, Any]:
    """Return a copy of ``step`` with legacy timeout/verification keys normalized.

    Raises:
        ConfigurationError: If a canonical key and its legacy alias conflict.
    """
    normalized = dict(step)
    found, value = _resolve_alias(step, TIMEOUT_KEY, TIMEOUT_ALIAS, path)
    normalized.pop(TIMEOUT_ALIAS, None)
    if found:
        normalized[TIMEOUT_KEY] = value

    found, value = _resolve_alias(step, VERIFY_KEY, VERIFY_ALIAS, path)
    normalized.pop(VERIFY_ALIAS, None)
    if found:
        normalized[VERIFY_KEY] = value
    return normalized


def action_params_from_step(step: dict[str, Any]) -> dict[str, Any]:
    """Collect top-level legacy parameters that are not structural or metadata keys.

    ``step`` must already be canonical. Explicit ``params`` entries win over
    top-level keys with the same name.
    """
    params = dict(step.get("params") or {})
    reserved = STEP_STRUCTURAL_KEYS | STEP_METADATA_KEYS
    for key, value in step.items():
        if key in reserved:
            continue
        params.setdefault(key, value)
    return params
