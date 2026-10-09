"""Windows Notification Trigger for WinFlow.

Provides a concrete BaseTrigger implementation that detects Windows toast notifications,
evaluates pattern match rules, deduplicates events, applies cooldown policies,
and requests workflow execution through the engine.
"""

import logging
import threading
from collections import deque
from collections.abc import Callable
from typing import Any

from winflow.core.emergency_stop import EmergencyStop, get_emergency_stop
from winflow.core.execution_lock import DesktopExecutionLock, get_desktop_lock
from winflow.core.logger import get_logger
from winflow.triggers.base import BaseTrigger
from winflow.triggers.cooldown import CooldownTracker
from winflow.triggers.deduplication import Deduplicator
from winflow.triggers.event import NotificationEvent
from winflow.triggers.matcher import NotificationMatcher
from winflow.triggers.sources import create_notification_source
from winflow.triggers.sources.base import BaseNotificationSource


class WindowsNotificationTrigger(BaseTrigger):
    """Concrete trigger listening for Windows notifications."""

    def __init__(
        self,
        name: str = "windows_notification",
        config: dict[str, Any] | None = None,
        source: BaseNotificationSource | None = None,
        desktop_lock: DesktopExecutionLock | None = None,
        emergency_stop: EmergencyStop | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize WindowsNotificationTrigger.

        Args:
            name: Trigger instance name.
            config: Trigger configuration dictionary.
            source: Optional custom notification source (defaults to auto-detected source).
            desktop_lock: Global desktop execution lock.
            emergency_stop: EmergencyStop mechanism.
            logger: Custom logger.
        """
        super().__init__(name=name, config=config)
        self.logger = logger or get_logger()
        self.desktop_lock = desktop_lock or get_desktop_lock()
        self.emergency_stop = emergency_stop or get_emergency_stop()

        # Accept the V1 configurator's legacy per-field `contains` criteria.
        match_cfg = self.config.get("match")
        if match_cfg is None:
            match_cfg = {}
            for field_name in ("application", "title", "body"):
                criterion = self.config.get(field_name)
                if isinstance(criterion, dict) and "contains" in criterion:
                    match_cfg[field_name] = {
                        "mode": "contains",
                        "value": criterion["contains"],
                    }
                elif criterion is not None:
                    match_cfg[field_name] = criterion
        self.matcher = NotificationMatcher(config=match_cfg)

        # Parse deduplication config
        dedupe_cfg = self.config.get("deduplication", {})
        if isinstance(dedupe_cfg, bool):
            dedupe_enabled = dedupe_cfg
            dedupe_window = 10.0
        elif isinstance(dedupe_cfg, dict):
            dedupe_enabled = bool(dedupe_cfg.get("enabled", True))
            dedupe_window = float(dedupe_cfg.get("window_seconds", 10.0))
        else:
            dedupe_enabled = True
            dedupe_window = 10.0
        self.deduplicator = Deduplicator(enabled=dedupe_enabled, window_seconds=dedupe_window)

        # Parse cooldown
        cooldown_sec = float(self.config.get("cooldown_seconds", 0.0))
        self.cooldown_tracker = CooldownTracker(cooldown_seconds=cooldown_sec)

        # Concurrent trigger policy: "ignore" (default), "queue", "restart"
        legacy_policies = {
            "terminate_and_restart": "queue",
            "run_concurrently": "queue",
        }
        self.while_running_policy = legacy_policies.get(
            str(self.config.get("while_running", self.config.get("while_running_policy", "ignore"))).lower().strip(),
            str(self.config.get("while_running", self.config.get("while_running_policy", "ignore"))).lower().strip(),
        )
        if self.while_running_policy not in ("ignore", "queue", "restart"):
            self.logger.warning(
                "Unrecognized while_running policy '%s', falling back to 'ignore'.",
                self.while_running_policy,
            )
            self.while_running_policy = "ignore"

        # Initialize event source
        self._source = source or create_notification_source(
            source_type=self.config.get("source_type", "auto"),
            db_path=self.config.get("db_path"),
            poll_interval=float(self.config.get("poll_interval", 0.25)),
            start_from_current=bool(self.config.get("start_from_current", True)),
        )

        self._is_executing_workflow = False
        self._queued_events: deque[NotificationEvent] = deque()
        self._lock = threading.Lock()

    @property
    def source(self) -> BaseNotificationSource:
        """Return the underlying notification source."""
        return self._source

    def set_workflow_executing(self, executing: bool) -> None:
        """Update workflow execution state to enforce concurrent policies.

        Args:
            executing: True if a triggered workflow is currently running.
        """
        with self._lock:
            self._is_executing_workflow = executing

            # If execution finished and we have queued events, trigger next queued event
            if not executing and self._queued_events and self._callback is not None:
                next_event = self._queued_events.popleft()
                self.logger.info(
                    "Workflow finished. Processing queued notification event '%s'.",
                    next_event.event_id,
                )
                threading.Thread(
                    target=self._execute_trigger,
                    args=(next_event,),
                    name="WinFlowQueuedTriggerWorker",
                    daemon=True,
                ).start()

    def start(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Start listening for Windows notifications and invoke callback on match.

        Args:
            callback: Function called with event data when a matching notification triggers.
        """
        with self._lock:
            if self._is_running:
                self.logger.warning("WindowsNotificationTrigger '%s' is already running.", self.name)
                return

            self._callback = callback
            self._is_running = True

        self._source.start(on_event=self._handle_notification_event)
        self.logger.info("WindowsNotificationTrigger '%s' started.", self.name)

    def stop(self) -> None:
        """Stop listening for Windows notifications cleanly."""
        with self._lock:
            if not self._is_running:
                return

            self._is_running = False
            self._queued_events.clear()

        self._source.stop()
        self.logger.info("WindowsNotificationTrigger '%s' stopped.", self.name)

    def _handle_notification_event(self, event: NotificationEvent) -> None:
        """Internal processing pipeline for each incoming notification event."""
        if self.emergency_stop.is_triggered():
            self.logger.info("Notification ignored: EmergencyStop active.")
            return

        self.logger.debug("Notification received: %s", event.safe_summary())

        # 1. Match criteria evaluation
        match_result = self.matcher.matches(event)
        if not match_result:
            self.logger.debug(
                "Notification '%s' ignored - match criteria not met: %s",
                event.event_id,
                match_result.reason,
            )
            return

        self.logger.info(
            "Notification matched: app='%s', title='%s', id='%s'.",
            event.application_id,
            event.title,
            event.event_id,
        )

        # 2. Deduplication evaluation
        if self.deduplicator.is_duplicate(event):
            self.logger.info(
                "Notification ignored - duplicate event '%s' within deduplication window.",
                event.event_id,
            )
            return

        # 3. Cooldown evaluation
        if self.cooldown_tracker.is_in_cooldown():
            rem = self.cooldown_tracker.remaining_cooldown()
            self.logger.info(
                "Notification ignored - cooldown active (%.1fs remaining).",
                rem,
            )
            return

        # 4. Concurrent execution policy check
        with self._lock:
            if self._is_executing_workflow:
                if self.while_running_policy == "ignore":
                    self.logger.info(
                        "Notification ignored - workflow is already running (policy=ignore)."
                    )
                    return
                elif self.while_running_policy == "queue":
                    self.logger.info(
                        "Workflow is currently running. Enqueuing notification event '%s' (policy=queue).",
                        event.event_id,
                    )
                    self._queued_events.append(event)
                    return
                elif self.while_running_policy == "restart":
                    self.logger.info(
                        "Workflow is currently running. Enqueuing notification event '%s' (policy=restart).",
                        event.event_id,
                    )
                    self._queued_events.append(event)
                    return
                else:
                    return

        # 5. Passed all filters -> execute trigger
        self._execute_trigger(event)

    def _execute_trigger(self, event: NotificationEvent) -> None:
        """Record cooldown and emit event to callback."""
        self.cooldown_tracker.record_trigger()
        payload = event.to_dict()
        self.logger.info(
            "Workflow trigger accepted for notification event '%s'.",
            event.event_id,
        )
        self.emit_event(payload)
