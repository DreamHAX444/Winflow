"""Notification deduplication subsystem for WinFlow.

Prevents repeated processing of duplicate notifications within a sliding time window.
"""

import hashlib
import threading
import time

from winflow.triggers.event import NotificationEvent


class Deduplicator:
    """Thread-safe event deduplication engine with time-window pruning."""

    def __init__(self, enabled: bool = True, window_seconds: float = 10.0) -> None:
        """Initialize deduplicator.

        Args:
            enabled: Whether deduplication is active.
            window_seconds: Duration in seconds to retain notification signatures.
        """
        self.enabled: bool = enabled
        self.window_seconds: float = max(0.0, float(window_seconds))
        self._history: dict[str, float] = {}
        self._lock = threading.Lock()

    def _generate_signature(self, event: NotificationEvent) -> str:
        """Generate a deterministic hash signature representing the notification identity."""
        identity_str = f"{event.event_id}|{event.application_id}|{event.title}|{event.body}"
        return hashlib.sha256(identity_str.encode("utf-8", errors="replace")).hexdigest()

    def is_duplicate(self, event: NotificationEvent, current_time: float | None = None) -> bool:
        """Check if an event has already been seen within the active deduplication window.

        If not seen, records the event and returns False.
        If seen within window_seconds, returns True.

        Args:
            event: The candidate NotificationEvent.
            current_time: Optional timestamp override for deterministic testing.

        Returns:
            True if event is a duplicate within the active window; False otherwise.
        """
        if not self.enabled:
            return False

        now = current_time if current_time is not None else time.time()
        sig = self._generate_signature(event)

        with self._lock:
            # 1. Prune expired entries
            cutoff = now - self.window_seconds
            expired_keys = [k for k, ts in self._history.items() if ts < cutoff]
            for k in expired_keys:
                del self._history[k]

            # 2. Check if current signature exists within window
            if sig in self._history:
                return True

            # 3. Record new signature
            self._history[sig] = now
            return False

    def reset(self) -> None:
        """Clear all recorded event signatures."""
        with self._lock:
            self._history.clear()
