import threading
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any


class ExecutionTraceEvent:
    def __init__(
        self,
        event: str,
        execution_id: str,
        workflow_id: str,
        step: int | None = None,
        action: str | None = None,
        status: str | None = None,
        duration_ms: float | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.event = event
        self.execution_id = execution_id
        self.workflow_id = workflow_id
        self.step = step
        self.action = action
        self.status = status
        self.duration_ms = duration_ms
        self.metadata = metadata or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "event": self.event,
            "execution_id": self.execution_id,
            "workflow_id": self.workflow_id,
            "step": self.step,
            "action": self.action,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
        }

class EventBus:
    """Thread-safe event subscriber registry."""
    def __init__(self):
        self._subscribers: list[Callable[[ExecutionTraceEvent], None]] = []
        self._lock = threading.Lock()

    def subscribe(self, callback: Callable[[ExecutionTraceEvent], None]) -> None:
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def unsubscribe(self, callback: Callable[[ExecutionTraceEvent], None]) -> None:
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def publish(self, event: ExecutionTraceEvent) -> None:
        with self._lock:
            subs = list(self._subscribers)
        for sub in subs:
            try:
                sub(event)
            except Exception:
                pass  # Do not crash the engine

_global_bus = EventBus()

def get_event_bus() -> EventBus:
    return _global_bus
