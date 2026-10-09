"""Windows Notification Trigger for WinFlow.

Provides a concrete BaseTrigger implementation that detects Windows toast notifications,
evaluates pattern match rules, deduplicates events, applies cooldown policies,
and requests workflow execution through the engine.
"""

import logging
import threading
from collections.abc import Callable
from typing import Any

from winflow.core.emergency_stop import EmergencyStop, get_emergency_stop
from winflow.core.errors import TriggerConfigurationError
from winflow.core.execution_lock import DesktopExecutionLock, get_desktop_lock
from winflow.core.logger import get_logger
from winflow.triggers.base import BaseTrigger
from winflow.triggers.cooldown import CooldownTracker
from winflow.triggers.deduplication import Deduplicator
from winflow.triggers.event import NotificationEvent
from winflow.triggers.matcher import validate_notification_trigger_config
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

        # Matching criteria are validated here (not just at config load) so that a
        # trigger built without validation cannot run as an accidental match-all.
        # Raises TriggerConfigurationError for empty or legacy/unsupported criteria.
        self.matcher = validate_notification_trigger_config(self.config)

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

        # Concurrent trigger policy: "ignore" (default), "queue", "restart".
        # Unsupported policies are rejected rather than mapped to another policy.
        requested_policy = str(
            self.config.get("while_running", self.config.get("while_running_policy", "ignore"))
        ).lower().strip()
        if requested_policy in ("terminate_and_restart", "run_concurrently"):
            raise TriggerConfigurationError(
                f"while_running '{requested_policy}' is not supported yet; "
                "use 'ignore', 'queue', or 'restart'."
            )
        if requested_policy not in ("ignore", "queue", "restart"):
            raise TriggerConfigurationError(
                f"Unrecognized while_running policy '{requested_policy}'; "
                "use 'ignore', 'queue', or 'restart'."
            )
        self.while_running_policy = requested_policy

        # Initialize event source
        self._source = source or create_notification_source(
            source_type=self.config.get("source_type", "auto"),
            db_path=self.config.get("db_path"),
            poll_interval=float(self.config.get("poll_interval", 0.25)),
            start_from_current=bool(self.config.get("start_from_current", True)),
        )

        # Engine owns the atomic execution gate and applies while_running policy.
        # This flag is retained for status/compatibility only; event admission is
        # intentionally not performed here, before the engine callback.
        self._is_executing_workflow = False
        self._lock = threading.Lock()

    @property
    def source(self) -> BaseNotificationSource:
        """Return the underlying notification source."""
        return self._source

    def set_workflow_executing(self, executing: bool) -> None:
        """Update compatibility/status state; execution policy is engine-owned."""
        with self._lock:
            self._is_executing_workflow = bool(executing)

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

        # 4. Execution admission is owned by WorkflowEngine. The engine reserves
        # its per-listener state atomically in the callback before starting a
        # worker, so rapid events cannot race a local flag update here.
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
