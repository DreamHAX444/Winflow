"""Schedule-based workflow triggers.

These lightweight triggers reuse the existing BaseTrigger API and WorkflowRunner,
allowing workflows to run without introducing a second scheduler system.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, timedelta
from threading import Timer
from typing import Any

from winflow.core.logger import get_logger
from winflow.triggers.base import BaseTrigger


def parse_weekday_codes(raw: Any) -> set[int]:
    """Normalize weekday configuration to integers 0-6 (Mon=0 in Python)."""
    if raw is None:
        return set()
    if isinstance(raw, int):
        codes = {raw % 7}
    elif isinstance(raw, str):
        parts = [piece.strip().lower() for piece in raw.split(",") if piece.strip()]
        codes = set()
        weekday_names = {
            "mon": 0,
            "monday": 0,
            "tue": 1,
            "tues": 1,
            "tuesday": 1,
            "wed": 2,
            "wednesday": 2,
            "thu": 3,
            "thurs": 3,
            "thursday": 3,
            "fri": 4,
            "friday": 4,
            "sat": 5,
            "saturday": 5,
            "sun": 6,
            "sunday": 6,
        }
        for part in parts:
            if part.isdigit():
                codes.add(int(part) % 7)
            elif part in weekday_names:
                codes.add(weekday_names[part])
    elif isinstance(raw, (list, tuple, set)):
        codes = set()
        for item in raw:
            codes |= parse_weekday_codes(item)
    else:
        return set()
    return {code % 7 for code in codes}


def _time_to_datetime(raw: Any, fallback_day: date | None = None) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        if len(text) == 5 and text[2] == ":":
            try:
                hour, minute = [int(part) for part in text.split(":", 1)]
                if not (0 <= hour <= 23 and 0 <= minute <= 59):
                    raise ValueError
                base = fallback_day or date.today()
                return datetime.combine(base, datetime.min.time()).replace(hour=hour, minute=minute)
            except ValueError:
                return None
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    if isinstance(raw, (int, float)):
        try:
            hour = int(raw)
            base = fallback_day or date.today()
            return datetime.combine(base, datetime.min.time()).replace(hour=hour)
        except ValueError:
            return None
    return None


def calculate_next_run(trigger_cfg: dict[str, Any], now: datetime | None = None) -> datetime | None:
    """Return the next valid schedule time for a trigger config or None if unschedulable."""
    if not isinstance(trigger_cfg, dict):
        return None
    trigger_type = str(trigger_cfg.get("type", "")).strip().lower()
    if not trigger_type or trigger_type == "manual":
        return None
    if not bool(trigger_cfg.get("enabled", True)):
        return None
    current = now or datetime.now()
    try:
        if trigger_type == "startup":
            return current
        if trigger_type == "one_time":
            dt = _time_to_datetime(trigger_cfg.get("datetime") or trigger_cfg.get("date_time"))
            if dt is None:
                date_value = trigger_cfg.get("date") or trigger_cfg.get("day")
                time_value = trigger_cfg.get("time") or trigger_cfg.get("at") or "00:00"
                if date_value:
                    dt = _time_to_datetime(f"{date_value}T{time_value}")
            if dt is None and trigger_cfg.get("date"):
                date_value = trigger_cfg.get("date")
                time_value = trigger_cfg.get("time") or "00:00"
                try:
                    dt = datetime.fromisoformat(f"{date_value}T{time_value}")
                except ValueError:
                    dt = None
            if dt is None or dt <= current:
                return None
            return dt
        if trigger_type == "daily":
            time_value = trigger_cfg.get("time") or trigger_cfg.get("at") or "00:00"
            today_at = _time_to_datetime(time_value, current.date())
            if today_at is None:
                return None
            if today_at > current:
                return today_at
            return today_at + timedelta(days=1)
        if trigger_type == "weekly":
            allowed = parse_weekday_codes(trigger_cfg.get("days") or trigger_cfg.get("weekdays"))
            if not allowed:
                return None
            time_value = trigger_cfg.get("time") or trigger_cfg.get("at") or "00:00"
            target_time = _time_to_datetime(time_value, current.date())
            if target_time is None:
                return None
            for offset in range(7):
                candidate_date = current.date() + timedelta(days=offset)
                weekday = candidate_date.weekday()
                if weekday not in allowed:
                    continue
                run_dt = target_time.replace(
                    year=candidate_date.year,
                    month=candidate_date.month,
                    day=candidate_date.day,
                )
                if run_dt > current:
                    return run_dt
            next_week_start = current.date() + timedelta(days=7)
            for offset in range(7):
                candidate_date = next_week_start + timedelta(days=offset)
                weekday = candidate_date.weekday()
                if weekday not in allowed:
                    continue
                run_dt = target_time.replace(
                    year=candidate_date.year,
                    month=candidate_date.month,
                    day=candidate_date.day,
                )
                return run_dt
            return None
    except (TypeError, ValueError):
        return None
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
        next_run = calculate_next_run(self.config)
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


__all__ = ["ScheduleTrigger", "calculate_next_run", "parse_weekday_codes"]
