"""Thread-safe emergency stop component for WinFlow.

Provides a centralized cancellation mechanism that workflows, actions,
loops, and external hotkey handlers can subscribe to.
"""

import threading
from collections.abc import Callable

from winflow.core.errors import EmergencyStopTriggered


class EmergencyStop:
    """Thread-safe emergency stop mechanism for cancelling automation."""

    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._listeners: list[Callable[[], None]] = []
        self._reason: str | None = None

    def request_stop(self, reason: str = "Emergency stop requested") -> None:
        """Trigger an emergency stop across all running components.
        
        Args:
            reason: Optional explanation for why the stop was requested.
        """
        with self._lock:
            self._reason = reason
            self._stop_event.set()
            callbacks = list(self._listeners)

        # Notify listeners outside the lock to prevent deadlocks
        for callback in callbacks:
            try:
                callback()
            except Exception:
                # Callback failures should not prevent the stop signal from propagating
                pass

    def trigger(self, reason: str = "Emergency stop requested") -> None:
        """Alias for request_stop."""
        self.request_stop(reason=reason)

    def is_stopped(self) -> bool:
        """Return True if an emergency stop has been requested."""
        return self._stop_event.is_set()

    def is_triggered(self) -> bool:
        """Alias for is_stopped."""
        return self.is_stopped()

    def reset(self) -> None:
        """Reset the emergency stop state so new workflows can execute."""
        with self._lock:
            self._stop_event.clear()
            self._reason = None

    def check_stop(self) -> None:
        """Raise EmergencyStopTriggered if stop has been requested.
        
        Raises:
            EmergencyStopTriggered: If emergency stop is active.
        """
        if self.is_stopped():
            reason = self._reason or "Emergency stop active"
            raise EmergencyStopTriggered(f"Execution halted: {reason}")

    def get_reason(self) -> str | None:
        """Return the reason provided when emergency stop was requested."""
        with self._lock:
            return self._reason

    def add_listener(self, callback: Callable[[], None]) -> None:
        """Register a callback listener to be invoked on emergency stop.
        
        Args:
            callback: Callable taking no arguments.
        """
        with self._lock:
            if callback not in self._listeners:
                self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[], None]) -> None:
        """Unregister a previously added callback listener.
        
        Args:
            callback: The callback to remove.
        """
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)


_GLOBAL_EMERGENCY_STOP: EmergencyStop | None = None
_GLOBAL_STOP_LOCK = threading.Lock()


def get_emergency_stop() -> EmergencyStop:
    """Retrieve or create the global EmergencyStop singleton instance."""
    global _GLOBAL_EMERGENCY_STOP
    with _GLOBAL_STOP_LOCK:
        if _GLOBAL_EMERGENCY_STOP is None:
            _GLOBAL_EMERGENCY_STOP = EmergencyStop()
        return _GLOBAL_EMERGENCY_STOP


def set_emergency_stop(instance: EmergencyStop) -> None:
    """Override the global EmergencyStop instance (useful for testing)."""
    global _GLOBAL_EMERGENCY_STOP
    with _GLOBAL_STOP_LOCK:
        _GLOBAL_EMERGENCY_STOP = instance
