"""Trigger cooldown subsystem for WinFlow.

Enforces a quiet period following successful workflow trigger activations,
preventing excessive repeated runs while keeping cooldown logically distinct from deduplication.
"""

import threading
import time


class CooldownTracker:
    """Thread-safe trigger cooldown tracker."""

    def __init__(self, cooldown_seconds: float = 0.0) -> None:
        """Initialize cooldown tracker.

        Args:
            cooldown_seconds: Minimum quiet period in seconds after a trigger event.
        """
        self.cooldown_seconds: float = max(0.0, float(cooldown_seconds))
        self._last_triggered_at: float | None = None
        self._lock = threading.Lock()

    @property
    def last_triggered_at(self) -> float | None:
        """Return the timestamp of the last recorded trigger, or None."""
        with self._lock:
            return self._last_triggered_at

    def is_in_cooldown(self, current_time: float | None = None) -> bool:
        """Check whether the cooldown period is currently active.

        Args:
            current_time: Optional timestamp override for testing.

        Returns:
            True if within cooldown period; False if cooldown is 0 or elapsed.
        """
        if self.cooldown_seconds <= 0.0:
            return False

        with self._lock:
            if self._last_triggered_at is None:
                return False
            now = current_time if current_time is not None else time.time()
            elapsed = now - self._last_triggered_at
            return elapsed < self.cooldown_seconds

    def remaining_cooldown(self, current_time: float | None = None) -> float:
        """Return the number of seconds remaining in the active cooldown period."""
        if self.cooldown_seconds <= 0.0:
            return 0.0

        with self._lock:
            if self._last_triggered_at is None:
                return 0.0
            now = current_time if current_time is not None else time.time()
            elapsed = now - self._last_triggered_at
            remaining = self.cooldown_seconds - elapsed
            return max(0.0, remaining)

    def record_trigger(self, current_time: float | None = None) -> None:
        """Record a successful trigger activation timestamp."""
        now = current_time if current_time is not None else time.time()
        with self._lock:
            self._last_triggered_at = now

    def reset(self) -> None:
        """Reset the cooldown state."""
        with self._lock:
            self._last_triggered_at = None
