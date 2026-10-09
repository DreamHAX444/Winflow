"""Where a workflow's trigger lives, resolved the same way the engine resolves it.

The engine uses ``config.get("trigger") or workflow.get("trigger")``: a non-empty
root-level trigger wins, and an empty value counts as absent. The validator checks
both locations. The editor uses this module so it reads, edits, and writes the same
trigger the engine would run, and never silently discards the other copy.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

ROOT = "root"
NESTED = "workflow"
BOTH = "both"
NONE = "none"
CONFLICT = "conflict"


@dataclass(frozen=True)
class TriggerLocation:
    """Describes which trigger representation(s) a configuration contains.

    ``kind`` is one of: ``none`` (no trigger), ``root`` (only ``trigger``),
    ``workflow`` (only ``workflow.trigger``), ``both`` (both, identical), or
    ``conflict`` (both, different). ``trigger`` is the effective trigger dictionary
    for every kind except ``none`` and ``conflict`` (where it is ``None``).
    """

    kind: str
    trigger: dict[str, Any] | None
    message: str = ""


def _present(value: Any) -> bool:
    return isinstance(value, dict) and bool(value)


def resolve_trigger_location(config: dict[str, Any]) -> TriggerLocation:
    """Classify the trigger placement of ``config``."""
    workflow = config.get("workflow")
    root = config.get("trigger")
    nested = workflow.get("trigger") if isinstance(workflow, dict) else None
    root_on = _present(root)
    nested_on = _present(nested)
    if root_on and nested_on:
        if root == nested:
            return TriggerLocation(BOTH, deepcopy(root))
        return TriggerLocation(
            CONFLICT,
            None,
            "The workflow defines two different triggers ('trigger' and "
            "'workflow.trigger'). Remove one of them in the workflow file before "
            "editing triggers here.",
        )
    if root_on:
        return TriggerLocation(ROOT, deepcopy(root))
    if nested_on:
        return TriggerLocation(NESTED, deepcopy(nested))
    return TriggerLocation(NONE, None)


def write_trigger(config: dict[str, Any], trigger: dict[str, Any] | None) -> None:
    """Store ``trigger`` (or remove it when ``None``) in the representation that exists.

    * Existing root-only trigger: the root copy is replaced.
    * Existing nested-only trigger: the nested copy is replaced.
    * Both identical: both copies are replaced, so they stay identical.
    * No trigger yet: a new trigger is written at the root (the canonical location).
    * Conflict: refused; the configuration is left unchanged.

    Raises:
        ValueError: On a conflicting configuration.
    """
    location = resolve_trigger_location(config)
    if location.kind == CONFLICT:
        raise ValueError(location.message)

    def assign(container: dict[str, Any], key: str) -> None:
        if trigger is None:
            container.pop(key, None)
        else:
            container[key] = deepcopy(trigger)

    if location.kind in (NESTED, BOTH):
        assign(config["workflow"], "trigger")
    if location.kind in (ROOT, BOTH, NONE):
        assign(config, "trigger")
