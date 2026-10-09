import time
import threading
from typing import Any

from winflow.engine.execution_context import ExecutionContext
from winflow.storage.execution_history import BaseExecutionHistory


class EngineStatusManager:
    def __init__(self, history: BaseExecutionHistory):
        self.history = history
        self.active_contexts: dict[str, ExecutionContext] = {}
        self._lock = threading.RLock()

    def register_execution(self, context: ExecutionContext) -> None:
        with self._lock:
            self.active_contexts[context.execution_id] = context

    def unregister_execution(self, execution_id: str) -> None:
        with self._lock:
            self.active_contexts.pop(execution_id, None)

    def get_status(self) -> dict[str, Any]:
        """Global engine status."""
        with self._lock:
            active_executions = list(self.active_contexts)
        is_running = bool(active_executions)
        return {
            "engine_status": "RUNNING" if is_running else "IDLE",
            "active_executions": active_executions,
        }

    def get_current_execution(self, execution_id: str) -> dict[str, Any] | None:
        """Live execution state."""
        with self._lock:
            ctx = self.active_contexts.get(execution_id)
        if not ctx:
            # Fallback to history
            record = self.history.get_record(execution_id)
            if not record:
                return None
            elapsed = (record.end_time or time.time()) - record.start_time
            return {
                "execution_id": record.execution_id,
                "workflow_id": record.workflow_id,
                "workflow_status": record.status,
                "current_step": record.current_step,
                "current_action": None,
                "current_window": None,
                "loop_iteration": 0,
                "retry_attempt": 0,
                "started_at": record.start_time,
                "elapsed_time": elapsed,
                "last_error": record.error,
            }
            
        elapsed = time.time() - ctx.start_time
        
        status = "RUNNING"
        if ctx.is_paused():
            status = "PAUSED"
        elif ctx._cancel_event.is_set():
            status = "CANCELLED"
            
        return {
            "execution_id": ctx.execution_id,
            "workflow_id": ctx.workflow_id,
            "workflow_status": status,
            "current_step": ctx.current_step,
            "current_action": getattr(ctx, "current_action", "unknown"),
            "current_window": getattr(ctx, "current_window", None),
            "loop_iteration": ctx.loop_iteration,
            "retry_attempt": getattr(ctx, "retry_attempt", 0), # if tracked
            "started_at": ctx.start_time,
            "elapsed_time": elapsed,
            "last_error": ctx._cancel_reason,
        }

    def get_execution_history(self, limit: int = 100) -> list[dict[str, Any]]:
        records = self.history.list_records(limit=limit)
        return [r.to_dict() for r in records]

    def get_recent_events(self, execution_id: str) -> list[dict[str, Any]]:
        if hasattr(self.history, "get_events"):
            return self.history.get_events(execution_id) # type: ignore
        return []

    def get_diagnostics(self, execution_id: str) -> dict[str, Any] | None:
        # Fetch latest snapshot event metadata or search directory
        events = self.get_recent_events(execution_id)
        for e in reversed(events):
            if e.get("status") == "FAILED" and e.get("metadata", {}).get("screenshot_path"):
                return e.get("metadata")
        return None
