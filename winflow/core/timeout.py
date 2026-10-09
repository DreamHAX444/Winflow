"""Action execution timeout subsystem for WinFlow.

Provides controlled timeout wrappers for action execution without unsafe thread-killing.
"""

import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, TypeVar

from winflow.core.errors import ActionExecutionError

if TYPE_CHECKING:
    from winflow.engine.execution_context import ExecutionContext

T = TypeVar("T")


def execute_with_timeout(
    operation: Callable[[], T],
    timeout_seconds: float,
    context: "ExecutionContext",
    operation_name: str = "operation",
) -> T:
    """Execute a callable with a strict timeout and cancellation checks.

    Args:
        operation: Zero-argument callable to execute.
        timeout_seconds: Maximum allowed runtime in seconds.
        context: ExecutionContext to monitor for cancellation.
        operation_name: Descriptive name for timeout errors.

    Returns:
        Result of the operation.

    Raises:
        EmergencyStopTriggered: If emergency stop is triggered.
        ActionExecutionError: If execution exceeds timeout_seconds.
    """
    context.check_cancellation()

    if timeout_seconds <= 0.0:
        return operation()

    result_box = []
    error_box = []
    completed_event = threading.Event()

    def worker() -> None:
        try:
            res = operation()
            result_box.append(res)
        except Exception as exc:
            error_box.append(exc)
        finally:
            completed_event.set()

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()

    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        context.check_cancellation()
        if completed_event.wait(timeout=min(0.05, max(0.001, deadline - time.time()))):
            break

    context.check_cancellation()

    if not completed_event.is_set():
        raise ActionExecutionError(
            f"Action '{operation_name}' timed out after {timeout_seconds} seconds."
        )

    if error_box:
        raise error_box[0]

    return result_box[0] if result_box else None
