"""Execution context for WinFlow workflows.

Maintains runtime state, variables, window tracking, clipboard cache,
and thread-safe cancellation integrated with the emergency stop subsystem.
"""

import threading
import time
import uuid
from contextlib import contextmanager
from collections.abc import Generator
from typing import Any

from winflow.core.emergency_stop import EmergencyStop, get_emergency_stop
from winflow.core.errors import ActionExecutionError, EmergencyStopTriggered


class ExecutionContext:
    """Encapsulates runtime state for a single workflow execution."""

    def __init__(
        self,
        workflow_id: str,
        execution_id: str | None = None,
        emergency_stop: EmergencyStop | None = None,
        variables: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the execution context.

        Args:
            workflow_id: Identifier of the workflow being executed.
            execution_id: Unique run ID (auto-generated if omitted).
            emergency_stop: EmergencyStop instance to bind to. Defaults to global instance.
            variables: Initial variable mappings.
        """
        self.workflow_id: str = workflow_id
        self.execution_id: str = execution_id or uuid.uuid4().hex
        self.current_step: int = 0
        self.current_action: str = "unknown"
        self.current_window: str | None = None
        self.loop_iteration: int = 0
        self.variables: dict[str, Any] = dict(variables or {})
        self.trigger_event: dict[str, Any] | None = None
        self.clipboard_data: str | None = None
        self.current_window: str | None = None
        self.previous_window: str | None = None
        self.start_time: float = time.time()

        self._cancel_event = threading.Event()
        self._paused_event = threading.Event()
        self._cancel_reason: str | None = None
        self._lock = threading.Lock()
        self._thread_state = threading.local()
        self._cleanup_callbacks: list[Any] = []

        # Link to emergency stop subsystem
        self.emergency_stop: EmergencyStop = (
            emergency_stop if emergency_stop is not None else get_emergency_stop()
        )

    def cancel(self, reason: str = "Execution cancelled") -> None:
        """Cancel this specific execution context."""
        with self._lock:
            self._cancel_reason = reason
            self._cancel_event.set()

    def is_cancelled(self) -> bool:
        """Return True if this execution or emergency stop was cancelled."""
        if self._cancel_event.is_set():
            return True
        if self.emergency_stop and self.emergency_stop.is_stopped():
            return True
        action_cancel = getattr(self._thread_state, "action_cancel_event", None)
        return bool(action_cancel is not None and action_cancel.is_set())

    @contextmanager
    def action_cancellation_scope(
        self,
        cancellation_event: threading.Event,
    ) -> Generator[None, None, None]:
        """Expose one timed action's cancellation token to its worker thread.

        The token is thread-local and does not poison the execution context, so a
        workflow may apply its configured retry policy after the timed worker has
        fully stopped.
        """
        previous = getattr(self._thread_state, "action_cancel_event", None)
        self._thread_state.action_cancel_event = cancellation_event
        try:
            yield
        finally:
            if previous is None:
                try:
                    del self._thread_state.action_cancel_event
                except AttributeError:
                    pass
            else:
                self._thread_state.action_cancel_event = previous

    def check_cancellation(self) -> None:
        """Raise EmergencyStopTriggered if execution was cancelled or emergency stop fired.

        Raises:
            EmergencyStopTriggered: When cancelled or emergency stop is active.
        """
        if self.emergency_stop and self.emergency_stop.is_stopped():
            reason = self.emergency_stop.get_reason() or "Emergency stop triggered"
            raise EmergencyStopTriggered(f"Execution aborted: {reason}")

        if self._cancel_event.is_set():
            with self._lock:
                reason = self._cancel_reason or "Context cancelled"
            raise EmergencyStopTriggered(f"Execution aborted: {reason}")

        action_cancel = getattr(self._thread_state, "action_cancel_event", None)
        if action_cancel is not None and action_cancel.is_set():
            raise ActionExecutionError("Timed action cancellation requested.")

        # Block if paused, but let workflow and timed-action cancellation wake it.
        if self._paused_event.is_set():
            while self._paused_event.is_set():
                action_cancel = getattr(self._thread_state, "action_cancel_event", None)
                if (
                    self._cancel_event.is_set()
                    or (self.emergency_stop and self.emergency_stop.is_stopped())
                    or (action_cancel is not None and action_cancel.is_set())
                ):
                    break
                time.sleep(0.1)
            # Re-check cancellation after un-pausing.
            if self._cancel_event.is_set() or (self.emergency_stop and self.emergency_stop.is_stopped()):
                reason = self._cancel_reason or "Context cancelled"
                raise EmergencyStopTriggered(f"Execution aborted: {reason}")
            action_cancel = getattr(self._thread_state, "action_cancel_event", None)
            if action_cancel is not None and action_cancel.is_set():
                raise ActionExecutionError("Timed action cancellation requested.")

    def pause(self) -> None:
        """Pause the workflow execution."""
        self._paused_event.set()

    def resume(self) -> None:
        """Resume the workflow execution."""
        self._paused_event.clear()

    def is_paused(self) -> bool:
        """Check if execution is currently paused."""
        return self._paused_event.is_set()

    def set_variable(self, key: str, value: Any) -> None:
        """Set a workflow runtime variable."""
        with self._lock:
            self.variables[key] = value

    def get_variable(self, key: str, default: Any = None) -> Any:
        """Retrieve a workflow runtime variable."""
        with self._lock:
            return self.variables.get(key, default)

    def update_window(self, new_window: str) -> None:
        """Update window tracking, shifting current window to previous window."""
        with self._lock:
            self.previous_window = self.current_window
            self.current_window = new_window

    def update_step(self, step_number: int) -> None:
        """Advance the current step indicator."""
        with self._lock:
            self.current_step = step_number

    def set_clipboard(self, data: str | None) -> None:
        """Update the clipboard data cache."""
        with self._lock:
            self.clipboard_data = data

    def register_cleanup(self, callback: Any) -> None:
        """Register an execution-scoped cleanup callback once."""
        if not callable(callback):
            raise TypeError("Execution cleanup callback must be callable.")
        with self._lock:
            if callback not in self._cleanup_callbacks:
                self._cleanup_callbacks.append(callback)

    def run_cleanups(self) -> list[str]:
        """Run registered cleanup callbacks and return any surfaced failures."""
        with self._lock:
            callbacks = list(reversed(self._cleanup_callbacks))
            self._cleanup_callbacks.clear()

        errors: list[str] = []
        for callback in callbacks:
            try:
                callback()
            except Exception as exc:
                errors.append(str(exc))
        return errors

    def elapsed_time(self) -> float:
        """Calculate elapsed seconds since execution began."""
        return time.time() - self.start_time

    def to_dict(self) -> dict[str, Any]:
        """Export context state to dictionary for logging or serialization."""
        with self._lock:
            return {
                "execution_id": self.execution_id,
                "workflow_id": self.workflow_id,
                "current_step": self.current_step,
                "loop_iteration": self.loop_iteration,
                "variables": dict(self.variables),
                "trigger_event": self.trigger_event,
                "clipboard_data": self.clipboard_data,
                "current_window": self.current_window,
                "previous_window": self.previous_window,
                "start_time": self.start_time,
                "is_cancelled": self.is_cancelled(),
                "elapsed_time": self.elapsed_time(),
            }
