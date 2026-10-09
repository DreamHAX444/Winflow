"""Wait/delay action for WinFlow.

Supports fractional seconds and responsive cancellation awareness.
"""

import time
from typing import TYPE_CHECKING, Any

from winflow.actions.base import BaseAction
from winflow.core.errors import ActionExecutionError

if TYPE_CHECKING:
    from winflow.engine.execution_context import ExecutionContext


class WaitAction(BaseAction):
    """Pause workflow execution for a configurable duration with cancellation responsiveness."""

    def __init__(
        self,
        name: str = "wait",
        params: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(name=name, params=params)

    def execute(self, context: "ExecutionContext", **kwargs: Any) -> dict[str, Any]:
        context.check_cancellation()
        opts = {**self.params, **kwargs}

        seconds = opts.get("seconds")
        milliseconds = opts.get("milliseconds", opts.get("duration_ms"))

        if seconds is None and milliseconds is None:
            raise ActionExecutionError("WaitAction requires 'seconds' or 'milliseconds' parameter.")

        total_seconds = 0.0
        if seconds is not None:
            try:
                total_seconds += float(seconds)
            except (ValueError, TypeError) as exc:
                raise ActionExecutionError(f"Wait 'seconds' must be numeric, got: {seconds}") from exc

        if milliseconds is not None:
            try:
                total_seconds += float(milliseconds) / 1000.0
            except (ValueError, TypeError) as exc:
                raise ActionExecutionError(f"Wait 'milliseconds' must be numeric, got: {milliseconds}") from exc

        if total_seconds < 0:
            raise ActionExecutionError(f"Wait duration cannot be negative: {total_seconds} seconds")

        # Responsive sleep slicing: checks cancellation every 50ms
        deadline = time.time() + total_seconds
        while time.time() < deadline:
            context.check_cancellation()
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            sleep_chunk = min(0.05, remaining)
            time.sleep(sleep_chunk)

        context.check_cancellation()
        return {"action": "wait", "duration_seconds": total_seconds}
