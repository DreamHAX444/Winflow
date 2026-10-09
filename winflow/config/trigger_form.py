"""Form-to-configuration merging for the trigger editor.

The trigger editor never rebuilds a trigger from scratch. Each save starts from the
trigger dictionary that was loaded and changes only the keys the form owns. Unknown
keys, regex matchers, deduplication, cooldown, ``while_running`` and ``enabled`` are
kept unless the user changed them. Nothing here imports Qt, so the rules can be
tested directly.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from winflow.triggers.schedule import SCHEDULE_TRIGGER_TYPES as SCHEDULE_FAMILY_TYPES

NOTIFICATION_TYPE = "windows_notification"
NOTIFICATION_FIELDS: tuple[str, ...] = ("application", "title", "body")
MATCH_MODES: tuple[str, ...] = ("contains", "exact", "regex")
WHILE_RUNNING_CHOICES: tuple[str, ...] = ("ignore", "queue", "restart")
_DEFAULT_DEDUPE_WINDOW = 10.0


class TriggerFormError(ValueError):
    """Raised when the form values cannot be saved as a trigger. The message is user-facing."""


@dataclass
class NotificationFormState:
    """Values shown by the notification section of the trigger editor."""

    criteria: dict[str, dict[str, str]] = field(default_factory=dict)
    match_all: bool = False
    case_sensitive: bool = False
    dedupe_enabled: bool = True
    dedupe_window: float = _DEFAULT_DEDUPE_WINDOW
    cooldown: float = 0.0
    while_running: str = "ignore"


@dataclass
class ScheduleFormState:
    """Values shown by the schedule section of the trigger editor."""

    schedule_type: str = "daily"
    time_text: str = ""


def _is_notification(trigger: dict[str, Any] | None) -> bool:
    return isinstance(trigger, dict) and trigger.get("type") == NOTIFICATION_TYPE


def _is_schedule(trigger: dict[str, Any] | None) -> bool:
    return isinstance(trigger, dict) and trigger.get("type") in SCHEDULE_FAMILY_TYPES


def _normalize_criterion(value: Any) -> dict[str, str] | None:
    """Return ``{mode, value}`` for a loaded criterion, or None when it is absent."""
    if value is None or value == "":
        return None
    if isinstance(value, dict):
        mode = str(value.get("mode", "contains"))
        text = value.get("value", "")
        if "contains" in value and "value" not in value:
            mode, text = "contains", value.get("contains", "")
        return {"mode": mode, "value": str(text)}
    return {"mode": "contains", "value": str(value)}


def notification_form_from_trigger(trigger: dict[str, Any] | None) -> NotificationFormState:
    """Read the notification form values from a trigger dictionary.

    Criteria are read from ``match`` first, then from the legacy top-level fields,
    matching the backend's resolution order. Values the form cannot represent are
    left in the dictionary and reported by the backend on save.
    """
    state = NotificationFormState()
    if not _is_notification(trigger):
        return state
    assert trigger is not None
    match = trigger.get("match") if isinstance(trigger.get("match"), dict) else {}
    for name in NOTIFICATION_FIELDS:
        criterion = _normalize_criterion(match.get(name) if name in match else trigger.get(name))
        if criterion is not None:
            state.criteria[name] = criterion
    state.case_sensitive = bool(match.get("case_sensitive", False))
    state.match_all = trigger.get("match_any") is True or match.get("match_any") is True

    dedupe = trigger.get("deduplication", {})
    if isinstance(dedupe, bool):
        state.dedupe_enabled = dedupe
        state.dedupe_window = _DEFAULT_DEDUPE_WINDOW
    elif isinstance(dedupe, dict):
        state.dedupe_enabled = bool(dedupe.get("enabled", True))
        state.dedupe_window = float(dedupe.get("window_seconds", _DEFAULT_DEDUPE_WINDOW))
    state.cooldown = float(trigger.get("cooldown_seconds", 0.0) or 0.0)
    policy_key = "while_running" if "while_running" in trigger or "while_running_policy" not in trigger else "while_running_policy"
    state.while_running = str(trigger.get(policy_key, "ignore")).strip().lower() or "ignore"
    return state


def schedule_form_from_trigger(trigger: dict[str, Any] | None) -> ScheduleFormState:
    """Read the schedule form values, accepting top-level or nested ``time``/``at``."""
    state = ScheduleFormState()
    if not _is_schedule(trigger):
        return state
    assert trigger is not None
    state.schedule_type = str(trigger.get("type"))
    nested = trigger.get("config") if isinstance(trigger.get("config"), dict) else {}
    for container in (trigger, nested):
        for key in ("time", "at"):
            if container.get(key) not in (None, ""):
                state.time_text = str(container[key])
                return state
    return state


def _apply_enabled(result: dict[str, Any], original: dict[str, Any] | None, enabled: bool) -> None:
    """Write ``enabled`` only when it was present or the user turned the trigger off.

    An enabled trigger with no ``enabled`` key is equivalent to ``enabled: true``, so
    the key is left absent. A trigger is never turned on silently: when the original
    had ``enabled: false`` and the user checks the box, ``enabled: true`` is written.
    """
    had_key = isinstance(original, dict) and "enabled" in original
    if enabled and not had_key:
        result.pop("enabled", None)
    else:
        result["enabled"] = bool(enabled)


def _validate_regex(field_name: str, value: str, case_sensitive: bool) -> None:
    try:
        re.compile(value, 0 if case_sensitive else re.IGNORECASE)
    except re.error as exc:
        raise TriggerFormError(f"The {field_name} regular expression is invalid: {exc}") from exc


_MISSING = object()


def _merge_deduplication(base: dict[str, Any], form: NotificationFormState) -> None:
    """Update deduplication without changing its shape unless the values change.

    * Dictionary form: update ``enabled`` and ``window_seconds`` in place.
    * Absent and left at the engine default (enabled, 10 s): stay absent.
    * Boolean form: keep the boolean when it still describes the form values.
    """
    is_default = bool(form.dedupe_enabled) and float(form.dedupe_window) == _DEFAULT_DEDUPE_WINDOW
    existing = base.get("deduplication", _MISSING)
    if isinstance(existing, dict):
        dedupe = dict(existing)
        dedupe["enabled"] = bool(form.dedupe_enabled)
        dedupe["window_seconds"] = float(form.dedupe_window)
        base["deduplication"] = dedupe
    elif existing is _MISSING:
        if not is_default:
            base["deduplication"] = {"enabled": bool(form.dedupe_enabled), "window_seconds": float(form.dedupe_window)}
    elif isinstance(existing, bool):
        if existing != bool(form.dedupe_enabled) or float(form.dedupe_window) != _DEFAULT_DEDUPE_WINDOW:
            base["deduplication"] = {"enabled": bool(form.dedupe_enabled), "window_seconds": float(form.dedupe_window)}


def merge_notification_trigger(
    original: dict[str, Any] | None,
    form: NotificationFormState,
    enabled: bool,
) -> dict[str, Any]:
    """Return a new notification trigger built from ``original`` and the form.

    Raises:
        TriggerFormError: If the matcher is empty without match-all, if match-all is
            combined with criteria, or if a regular expression does not compile.
    """
    base = deepcopy(original) if _is_notification(original) else {}
    assert isinstance(base, dict)
    base["type"] = NOTIFICATION_TYPE

    criteria = {
        name: {"mode": c["mode"], "value": c["value"].strip()}
        for name, c in form.criteria.items()
        if c.get("value", "").strip()
    }
    if form.match_all and criteria:
        raise TriggerFormError(
            "'Match all notifications' cannot be combined with application, title, or body "
            "criteria. Clear the criteria or uncheck 'Match all notifications'."
        )
    if not form.match_all and not criteria:
        raise TriggerFormError(
            "Add at least one notification criterion (application, title, or body), or check "
            "'Match all notifications' to match every notification."
        )
    for name, c in criteria.items():
        if c["mode"] not in MATCH_MODES:
            raise TriggerFormError(f"Unsupported match mode '{c['mode']}' for {name}.")
        if c["mode"] == "regex":
            _validate_regex(name, c["value"], form.case_sensitive)

    # Criteria always live in "match"; drop the legacy top-level forms we now replace.
    for name in NOTIFICATION_FIELDS:
        base.pop(name, None)
    match = base.get("match") if isinstance(base.get("match"), dict) else {}
    match.pop("match_any", None)
    base.pop("match_any", None)
    match.pop("case_sensitive", None)
    base.pop("case_sensitive", None)
    if form.match_all:
        base["match_any"] = True
    else:
        for name in NOTIFICATION_FIELDS:
            if name in criteria:
                match[name] = dict(criteria[name])
            else:
                match.pop(name, None)
        if form.case_sensitive:
            match["case_sensitive"] = True
    if match or "match" in base:
        base["match"] = match

    _merge_deduplication(base, form)

    if "cooldown_seconds" in base:
        if float(base["cooldown_seconds"] or 0.0) != float(form.cooldown):
            base["cooldown_seconds"] = float(form.cooldown)
    elif form.cooldown > 0:
        base["cooldown_seconds"] = float(form.cooldown)

    if form.while_running not in WHILE_RUNNING_CHOICES:
        raise TriggerFormError(f"While Running must be one of: {', '.join(WHILE_RUNNING_CHOICES)}.")
    policy_key = "while_running" if "while_running" in base or "while_running_policy" not in base else "while_running_policy"
    if policy_key in base or form.while_running != "ignore":
        base[policy_key] = form.while_running

    _apply_enabled(base, original, enabled)
    return base


_SCHEDULE_TIME_KEYS = ("time", "at")


def merge_schedule_trigger(
    original: dict[str, Any] | None,
    form: ScheduleFormState,
    enabled: bool,
) -> dict[str, Any]:
    """Return a new schedule trigger built from ``original`` and the form.

    The time is written to the location that already holds it (top level or nested
    ``config``). When there is no time yet, a new value goes into ``config``, the same
    shape the schedule editor has always produced. Other keys, such as ``date`` or
    ``days``, are kept. The full value is checked by the configuration validator.
    """
    base = deepcopy(original) if _is_schedule(original) else {}
    assert isinstance(base, dict)
    base["type"] = form.schedule_type
    time_text = form.time_text.strip()
    if time_text:
        placed = False
        for key in _SCHEDULE_TIME_KEYS:
            if key in base:
                base[key] = time_text
                placed = True
                break
        if not placed:
            nested = base.get("config")
            if isinstance(nested, dict):
                for key in _SCHEDULE_TIME_KEYS:
                    if key in nested:
                        nested[key] = time_text
                        placed = True
                        break
                if not placed:
                    nested["time"] = time_text
            else:
                base["config"] = {"time": time_text}
    _apply_enabled(base, original, enabled)
    return base
