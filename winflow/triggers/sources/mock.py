"""Mock notification source for WinFlow testing.

Allows deterministic injection of NotificationEvent instances without OS dependencies.
"""

import threading
from collections.abc import Callable

from winflow.triggers.event import NotificationEvent
from winflow.triggers.sources.base import BaseNotificationSource


class MockNotificationSource(BaseNotificationSource):
    """In-memory mock notification source for testing."""

    def __init__(self) -> None:
        super().__init__()
        self.dispatched_events: list[NotificationEvent] = []
        self._lock = threading.Lock()

    def start(self, on_event: Callable[[NotificationEvent], None]) -> None:
        """Start the mock source and set the event callback."""
        with self._lock:
            self._callback = on_event
            self._is_running = True

    def stop(self) -> None:
        """Stop the mock source."""
        with self._lock:
            self._is_running = False
            self._callback = None

    def inject_event(self, event: NotificationEvent) -> None:
        """Simulate the arrival of a notification event.

        Args:
            event: The NotificationEvent to inject.
        """
        with self._lock:
            if not self._is_running or self._callback is None:
                return
            self.dispatched_events.append(event)
            cb = self._callback

        # Dispatch outside lock to prevent deadlocks
        cb(event)
