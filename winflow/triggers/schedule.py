"""Schedule-based workflow triggers.

These lightweight triggers reuse the existing BaseTrigger API and WorkflowRunner,
allowing workflows to run without introducing a second scheduler system.

Configuration contract
----------------------
Schedule fields are read from the trigger's top level (canonical form) or from a
nested legacy ``config`` object (``trigger.config.time`` as written by the older
trigger editor). Both locations are merged by :func:`normalize_schedule_config`,
which is the only place schedule configuration is interpreted. Validation and
scheduling both use its result, so they cannot disagree.

Precedence rule: top-level and nested values are equivalent sources. If the same
logical field is given more than once with different values, configuration is
rejected rather than choosing one silently. Missing required values are never
defaulted (for example, a daily trigger without a valid time is an error, not
midnight).

Accepted fields by trigger type:

* ``startup``   - no time fields are required or read.
* ``daily``     - ``time`` (alias ``at``) in ``HH:MM`` 24-hour format, or a legacy
                  integer hour 0-23 (``14`` means ``14:00``).
* ``weekly``    - ``time``/``at`` plus ``days`` (alias ``weekdays``): names
                  (``mon``..``sun``, full names accepted), or integers 0-6 where
                  Monday is 0.
* ``one_time``  - ``datetime`` (alias ``date_time``) as ``YYYY-MM-DDTHH:MM[:SS]``,
                  or ``date`` (alias ``day``) as ``YYYY-MM-DD`` together with
                  ``time``/``at``. Must be in the future at validation time.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, time as dt_time, timedelta
from threading import Timer
from typing import Any

from winflow.core.errors import TriggerConfigurationError
from winflow.core.logger import get_logger
from winflow.triggers.base import BaseTrigger

SCHEDULE_TRIGGER_TYPES: frozenset[str] = frozenset({"startup", "daily", "weekly", "one_time"})

_TIME_PATTERN = re.compile(r"^(\d{1,2}):(\d{2})$")
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?$")

_WEEKDAY_NAMES: dict[str, int] = {
    "mon": 0, "monday": 0,
    "tue": 1, "tues": 1, "tuesday": 1,
    "wed": 2, "wednesday": 2,
    "thu": 3, "thurs": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}


class ScheduleConfigurationError(TriggerConfigurationError):
    """Raised when a schedule trigger configuration is missing, invalid, or contradictory."""


@dataclass(frozen=True)
class ScheduleSpec:
    """Normalized schedule configuration shared by validation and scheduling."""

    trigger_type: str
    time_of_day: dt_time | None = None
    weekdays: frozenset[int] = field(default_factory=frozenset)
    run_at: datetime | None = None


# --------------------------------------------------------------------------- #
# Field parsers
# --------------------------------------------------------------------------- #

def parse_weekday_codes(raw: Any) -> set[int]:
    """Lenient weekday parser kept for backward compatibility (Mon=0).

    Scheduling no longer uses this; :func:`normalize_schedule_config` applies the
    strict parser below so that unknown weekday names are rejected.
    """
    if raw is None:
        return set()
    if isinstance(raw, int):
        codes = {raw % 7}
    elif isinstance(raw, str):
        codes = set()
        for part in (piece.strip().lower() for piece in raw.split(",")):
            if part.isdigit():
                codes.add(int(part) % 7)
            elif part in _WEEKDAY_NAMES:
                codes.add(_WEEKDAY_NAMES[part])
    elif isinstance(raw, (list, tuple, set)):
        codes = set()
        for item in raw:
            codes |= parse_weekday_codes(item)
    else:
        return set()
    return {code % 7 for code in codes}


_WEEKDAY_CANONICAL_NAMES: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _parse_time(value: Any, key: str) -> dt_time:
    """Parse a time-of-day value.

    Accepts ``HH:MM`` strings (24-hour) and legacy integer hours 0-23, where ``14``
    means ``14:00``. Booleans, floats, and any other type are rejected.
    """
    if isinstance(value, bool):
        raise ScheduleConfigurationError(
            f"'{key}' must be HH:MM text or an integer hour 0-23, not a boolean."
        )
    if isinstance(value, int):
        if 0 <= value <= 23:
            return dt_time(hour=value, minute=0)
        raise ScheduleConfigurationError(
            f"'{key}' integer hour {value} is out of range; integer hours must be 0-23 (14 means 14:00)."
        )
    if not isinstance(value, str):
        raise ScheduleConfigurationError(
            f"'{key}' must be HH:MM text or an integer hour 0-23, got {type(value).__name__}."
        )
    match = _TIME_PATTERN.fullmatch(value.strip())
    if match is None:
        raise ScheduleConfigurationError(
            f"'{key}' value {value!r} is not a valid time; use 24-hour HH:MM (e.g. '14:30')."
        )
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ScheduleConfigurationError(
            f"'{key}' value {value!r} is out of range; hours must be 00-23 and minutes 00-59."
        )
    return dt_time(hour=hour, minute=minute)


def _parse_date(value: Any, key: str) -> date:
    if not isinstance(value, str) or _DATE_PATTERN.fullmatch(value.strip()) is None:
        raise ScheduleConfigurationError(
            f"'{key}' value {value!r} is not a valid date; use YYYY-MM-DD (e.g. '2026-12-31')."
        )
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ScheduleConfigurationError(f"'{key}' value {value!r} is not a real calendar date.") from exc


def _parse_datetime(value: Any, key: str) -> datetime:
    if not isinstance(value, str) or _DATETIME_PATTERN.fullmatch(value.strip()) is None:
        raise ScheduleConfigurationError(
            f"'{key}' value {value!r} is not a valid local date-time; use "
            "YYYY-MM-DDTHH:MM (e.g. '2026-12-31T09:00')."
        )
    try:
        return datetime.fromisoformat(value.strip().replace(" ", "T", 1))
    except ValueError as exc:
        raise ScheduleConfigurationError(f"'{key}' value {value!r} is not a real date-time.") from exc


def _parse_weekday_item(item: Any, key: str) -> set[int]:
    if isinstance(item, bool):
        raise ScheduleConfigurationError(f"'{key}' must contain weekday names or integers 0-6, not booleans.")
    if isinstance(item, int):
        if 0 <= item <= 6:
            return {item}
        raise ScheduleConfigurationError(f"'{key}' integer {item} is out of range; use 0 (Mon) to 6 (Sun).")
    if isinstance(item, str):
        codes: set[int] = set()
        for part in item.split(","):
            token = part.strip().lower()
            if not token:
                continue
            if token.isdigit() and 0 <= int(token) <= 6:
                codes.add(int(token))
            elif token in _WEEKDAY_NAMES:
                codes.add(_WEEKDAY_NAMES[token])
            else:
                raise ScheduleConfigurationError(
                    f"'{key}' contains unknown weekday {part.strip()!r}; use mon..sun or 0-6."
                )
        return codes
    raise ScheduleConfigurationError(
        f"'{key}' must be a weekday name, a comma-separated string, or a list of them."
    )


def _parse_weekdays(value: Any, key: str) -> frozenset[int]:
    if isinstance(value, (list, tuple, set)):
        codes: set[int] = set()
        for item in value:
            codes |= _parse_weekday_item(item, key)
    else:
        codes = _parse_weekday_item(value, key)
    if not codes:
        raise ScheduleConfigurationError(f"'{key}' must name at least one weekday.")
    return frozenset(codes)


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #

def _candidates(cfg: dict[str, Any], names: tuple[str, ...]) -> list[tuple[str, Any]]:
    """Collect present (non-null) values for the given aliases, top-level and nested."""
    sources: list[tuple[str, dict[str, Any]]] = [("", cfg)]
    nested = cfg.get("config")
    if isinstance(nested, dict):
        sources.append(("config.", nested))
    elif nested is not None:
        raise ScheduleConfigurationError("'config' must be an object when present.")
    found: list[tuple[str, Any]] = []
    for prefix, source in sources:
        for name in names:
            value = source.get(name)
            if value is None or (isinstance(value, str) and not value.strip()):
                # A blank value means "not entered"; it is treated as absent so the
                # required-field error is reported, never a guessed default.
                continue
            found.append((f"{prefix}{name}", value))
    return found


def _resolve(cfg: dict[str, Any], names: tuple[str, ...], parser: Any, label: str) -> Any:
    """Return the single agreed value for a logical field, or None if absent.

    Raises if any two present representations parse to different values.
    """
    parsed = [(name, parser(value, name)) for name, value in _candidates(cfg, names)]
    if not parsed:
        return None
    distinct = {value for _, value in parsed}
    if len(distinct) > 1:
        described = ", ".join(f"'{name}'={_value_text(value)}" for name, value in parsed)
        raise ScheduleConfigurationError(
            f"Conflicting values for {label}: {described}. Keep one value "
            "(top-level fields are canonical; remove the duplicate)."
        )
    return parsed[0][1]


def _value_text(value: Any) -> str:
    if isinstance(value, dt_time):
        return value.strftime("%H:%M")
    if isinstance(value, datetime):
        return value.isoformat(timespec="minutes")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, frozenset):
        return ",".join(str(code) for code in sorted(value))
    return repr(value)


def canonical_schedule_fields(spec: ScheduleSpec) -> dict[str, Any]:
    """Deterministic canonical representation of a normalized schedule.

    Times are ``HH:MM`` (legacy integer hours become ``HH:00``), weekdays are sorted
    short names, and one-time values are ``YYYY-MM-DDTHH:MM:SS``.
    """
    fields: dict[str, Any] = {"type": spec.trigger_type}
    if spec.time_of_day is not None:
        fields["time"] = spec.time_of_day.strftime("%H:%M")
    if spec.weekdays:
        fields["days"] = [_WEEKDAY_CANONICAL_NAMES[code] for code in sorted(spec.weekdays)]
    if spec.run_at is not None:
        fields["datetime"] = spec.run_at.strftime("%Y-%m-%dT%H:%M:%S")
    return fields


def normalize_schedule_config(trigger_cfg: Any) -> ScheduleSpec:
    """Interpret a schedule trigger configuration into a canonical ScheduleSpec.

    Does not check whether a one-time run is in the future; see
    :func:`validate_schedule_trigger_config` for load-time validation.

    Raises:
        ScheduleConfigurationError: If the type is unsupported, a required field is
            missing or invalid, or two representations of one field disagree.
    """
    if not isinstance(trigger_cfg, dict):
        raise ScheduleConfigurationError("Schedule trigger configuration must be an object.")
    trigger_type = str(trigger_cfg.get("type", "")).strip().lower()
    if trigger_type not in SCHEDULE_TRIGGER_TYPES:
        raise ScheduleConfigurationError(
            f"Unsupported schedule trigger type {trigger_type!r}; expected one of "
            f"{', '.join(sorted(SCHEDULE_TRIGGER_TYPES))}."
        )

    if trigger_type == "startup":
        return ScheduleSpec(trigger_type="startup")

    if trigger_type == "daily":
        time_of_day = _resolve(trigger_cfg, ("time", "at"), _parse_time, "time")
        if time_of_day is None:
            raise ScheduleConfigurationError(
                "Daily schedule requires 'time' in 24-hour HH:MM format (e.g. '14:30')."
            )
        return ScheduleSpec(trigger_type="daily", time_of_day=time_of_day)

    if trigger_type == "weekly":
        time_of_day = _resolve(trigger_cfg, ("time", "at"), _parse_time, "time")
        if time_of_day is None:
            raise ScheduleConfigurationError(
                "Weekly schedule requires 'time' in 24-hour HH:MM format (e.g. '14:30')."
            )
        weekdays = _resolve(trigger_cfg, ("days", "weekdays"), _parse_weekdays, "weekdays")
        if weekdays is None:
            raise ScheduleConfigurationError(
                "Weekly schedule requires 'days' (e.g. ['mon', 'wed', 'fri'])."
            )
        return ScheduleSpec(trigger_type="weekly", time_of_day=time_of_day, weekdays=weekdays)

    # one_time
    date_value = _resolve(trigger_cfg, ("date", "day"), _parse_date, "date")
    time_value = _resolve(trigger_cfg, ("time", "at"), _parse_time, "time")
    datetime_value = _resolve(trigger_cfg, ("datetime", "date_time"), _parse_datetime, "datetime")
    if datetime_value is not None:
        if date_value is not None and date_value != datetime_value.date():
            raise ScheduleConfigurationError(
                f"One-time schedule 'date' {date_value.isoformat()} conflicts with "
                f"'datetime' {_value_text(datetime_value)}."
            )
        if time_value is not None and (time_value.hour, time_value.minute) != (
            datetime_value.hour,
            datetime_value.minute,
        ):
            raise ScheduleConfigurationError(
                f"One-time schedule 'time' {_value_text(time_value)} conflicts with "
                f"'datetime' {_value_text(datetime_value)}."
            )
        run_at = datetime_value
    else:
        if date_value is None:
            raise ScheduleConfigurationError(
                "One-time schedule requires 'datetime' (YYYY-MM-DDTHH:MM) or 'date' (YYYY-MM-DD) with 'time'."
            )
        if time_value is None:
            raise ScheduleConfigurationError(
                "One-time schedule with 'date' requires 'time' in 24-hour HH:MM format; it will not default to midnight."
            )
        run_at = datetime.combine(date_value, time_value)
    return ScheduleSpec(trigger_type="one_time", run_at=run_at)


def validate_schedule_structure(trigger_cfg: Any) -> ScheduleSpec:
    """Structural validation: required fields, formats, and conflicts.

    Applies to every schedule, regardless of whether the trigger or its workflow is
    enabled. Disabling something never makes malformed configuration valid.
    """
    return normalize_schedule_config(trigger_cfg)


def require_schedule_activation_eligibility(
    trigger_cfg: Any,
    *,
    workflow_enabled: bool,
    now: datetime | None = None,
) -> None:
    """Temporal eligibility: an enabled one-time schedule must be in the future.

    Only applies when both the workflow and the trigger are enabled, because only then
    can the schedule run. Expired disabled schedules remain loadable and editable.

    Raises:
        ScheduleConfigurationError: If the schedule is due to run but is not in the future.
    """
    spec = normalize_schedule_config(trigger_cfg)
    trigger_enabled = bool(trigger_cfg.get("enabled", True))
    if spec.trigger_type != "one_time" or not (workflow_enabled and trigger_enabled):
        return
    current = now or datetime.now()
    if spec.run_at is None or spec.run_at <= current:
        raise ScheduleConfigurationError(
            f"One-time schedule time {_value_text(spec.run_at)} is not in the future "
            f"(current local time {current.isoformat(timespec='minutes')}). "
            "Set a new future date/time before enabling this workflow or trigger."
        )


def validate_schedule_trigger_config(
    trigger_cfg: Any,
    now: datetime | None = None,
    *,
    workflow_enabled: bool = True,
) -> ScheduleSpec:
    """Structural validation followed by activation eligibility (both always applied).

    Raises:
        ScheduleConfigurationError: On invalid structure, or on an expired one-time
            schedule that is enabled in an enabled workflow.
    """
    spec = validate_schedule_structure(trigger_cfg)
    require_schedule_activation_eligibility(trigger_cfg, workflow_enabled=workflow_enabled, now=now)
    return spec


def calculate_next_run(trigger_cfg: dict[str, Any], now: datetime | None = None) -> datetime | None:
    """Return the next run time for a schedule config, or None if nothing is pending.

    Raises:
        ScheduleConfigurationError: If the configuration is invalid. Invalid
            configuration is never converted into a guessed run time.
    """
    if not isinstance(trigger_cfg, dict):
        return None
    trigger_type = str(trigger_cfg.get("type", "")).strip().lower()
    if not trigger_type or trigger_type == "manual":
        return None
    if not bool(trigger_cfg.get("enabled", True)):
        return None
    if trigger_type not in SCHEDULE_TRIGGER_TYPES:
        return None
    spec = normalize_schedule_config(trigger_cfg)
    current = now or datetime.now()

    if spec.trigger_type == "startup":
        return current
    if spec.trigger_type == "one_time":
        if spec.run_at is None or spec.run_at <= current:
            return None
        return spec.run_at
    if spec.trigger_type == "daily":
        today_at = datetime.combine(current.date(), spec.time_of_day)
        if today_at > current:
            return today_at
        return today_at + timedelta(days=1)
    # weekly: search today and the following week for the next allowed weekday.
    for offset in range(8):
        candidate_date = current.date() + timedelta(days=offset)
        if candidate_date.weekday() not in spec.weekdays:
            continue
        run_dt = datetime.combine(candidate_date, spec.time_of_day)
        if run_dt > current:
            return run_dt
    return None


class ScheduleTrigger(BaseTrigger):
    """Schedule-based trigger using the existing trigger callback pipeline."""

    def __init__(self, name: str, config: dict[str, Any] | None = None, logger: logging.Logger | None = None) -> None:
        super().__init__(name=name, config=config or {})
        self.logger = logger or get_logger()
        self._timer: Timer | None = None
        self._lock = threading.RLock()
        self._last_triggered: datetime | None = None

    @property
    def trigger_type(self) -> str:
        return str(self.config.get("type", "manual")).strip().lower()

    @property
    def enabled(self) -> bool:
        workflow = bool(self.config.get("workflow_enabled", True))
        trigger = bool(self.config.get("enabled", True))
        return workflow and trigger

    def _build_event(self, trigger_time: datetime | None = None) -> dict[str, Any]:
        return {
            "workflow_id": self.config.get("workflow_id", self.name),
            "workflow_name": self.config.get("workflow_name", self.config.get("workflow_id", self.name)),
            "trigger_type": self.trigger_type,
            "trigger_name": self.name,
            "scheduled": True,
            "timestamp": (trigger_time or datetime.now()).isoformat(timespec="seconds"),
            "manual": False,
        }

    def _schedule_next_run(self) -> None:
        if not self.enabled:
            return
        try:
            next_run = calculate_next_run(self.config)
        except ScheduleConfigurationError as exc:
            self.logger.error("Schedule trigger '%s' has invalid configuration; not scheduled: %s", self.name, exc)
            return
        if next_run is None:
            self.logger.info("Schedule trigger '%s' has no future run configured.", self.name)
            return
        delay = max((next_run - datetime.now()).total_seconds(), 0.0)
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            self._timer = Timer(delay, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        if not self.enabled or self._callback is None:
            return
        now = datetime.now()
        self._last_triggered = now
        event = self._build_event(now)
        try:
            self._callback(event)
        except Exception as exc:
            self.logger.exception("Scheduled workflow execution failed for '%s': %s", self.name, exc)
        finally:
            if self.enabled:
                self._schedule_next_run()

    def start(self, callback: Any) -> None:
        # Structure is checked even when disabled, so malformed config never passes
        # merely because it is switched off. Failure happens before any state exists.
        normalize_schedule_config(self.config)
        if not self.enabled:
            self.logger.info("Schedule trigger '%s' is disabled; not started.", self.name)
            return
        # An enabled one-time schedule must still lie in the future when it is started;
        # this mirrors load-time validation for configs that bypassed it.
        validate_schedule_trigger_config(self.config)
        with self._lock:
            if self._is_running:
                return
            self._callback = callback
            self._is_running = True
        trigger_type = self.trigger_type
        if trigger_type == "startup":
            try:
                self._callback(self._build_event(datetime.now()))
            except Exception as exc:
                self.logger.exception("Startup trigger '%s' failed: %s", self.name, exc)
            return
        self._schedule_next_run()
        self.logger.info("Schedule trigger '%s' started for type '%s'.", self.name, trigger_type)

    def stop(self) -> None:
        with self._lock:
            self._is_running = False
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None

    def set_workflow_executing(self, executing: bool) -> None:
        """Compatibility helper for the existing trigger callback flow."""
        self._is_running = executing or self._is_running


__all__ = [
    "SCHEDULE_TRIGGER_TYPES",
    "ScheduleConfigurationError",
    "ScheduleSpec",
    "ScheduleTrigger",
    "calculate_next_run",
    "canonical_schedule_fields",
    "normalize_schedule_config",
    "parse_weekday_codes",
    "require_schedule_activation_eligibility",
    "validate_schedule_structure",
    "validate_schedule_trigger_config",
]
