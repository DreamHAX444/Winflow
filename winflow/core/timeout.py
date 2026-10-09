"""Action execution timeout subsystem for WinFlow.

Python threads cannot be safely force-killed. A timed action therefore receives
cooperative cancellation, and this wrapper does not return until its worker has
actually stopped. This can make an action's observed duration exceed its timeout
when a native call is blocking, but it prevents orphaned desktop automation and
keeps the caller's desktop lock held until the action is quiescent.
"""

import math
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
    """Execute a callable with a timeout deadline and safe worker cleanup.

    A timeout signals the worker's thread-local cancellation token and waits for
    the operation to cooperate or finish. It never returns while the action is
    still running, so a retry cannot overlap the previous attempt.

    Args:
        operation: Zero-argument callable to execute.
        timeout_seconds: Maximum allowed runtime in seconds. Non-positive values
            preserve the existing behavior and disable the per-action timeout.
        context: ExecutionContext to monitor for workflow and emergency cancellation.
        operation_name: Descriptive name for timeout errors.

    Returns:
        Result of the operation.

    Raises:
        EmergencyStopTriggered: If the workflow or emergency stop is cancelled.
        ActionExecutionError: If execution exceeds timeout_seconds.
    """
    context.check_cancellation()

    try:
        timeout_seconds = float(timeout_seconds)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ActionExecutionError("Action timeout must be a finite number of seconds.") from exc
    if not math.isfinite(timeout_seconds):
        raise ActionExecutionError("Action timeout must be a finite number of seconds.")
    if timeout_seconds <= 0.0:
        return operation()

    result_box: list[T] = []
    error_box: list[BaseException] = []
    completed_event = threading.Event()
    action_cancel_event = threading.Event()

    def worker() -> None:
        try:
            cancellation_scope = getattr(context, "action_cancellation_scope", None)
            if callable(cancellation_scope):
                with cancellation_scope(action_cancel_event):
                    result_box.append(operation())
            else:
                result_box.append(operation())
        except BaseException as exc:
            error_box.append(exc)
        finally:
            completed_event.set()

    # Non-daemon is intentional: a native call cannot be killed safely. The
    # caller waits for completion before releasing its execution lock.
    thread = threading.Thread(target=worker, name=f"WinFlowAction-{operation_name}")
    thread.start()
    deadline = time.monotonic() + timeout_seconds

    try:
        while True:
            context.check_cancellation()
            if completed_event.is_set():
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                break
            completed_event.wait(timeout=min(0.05, remaining))
    except BaseException:
        action_cancel_event.set()
        completed_event.wait()
        thread.join()
        raise

    timed_out = not completed_event.is_set()
    if timed_out:
        action_cancel_event.set()
        # Do not permit a retry, release the desktop lock, or return to the
        # trigger loop until the previous action has stopped manipulating input.
        completed_event.wait()
        thread.join()
        raise ActionExecutionError(
            f"Action '{operation_name}' timed out after {timeout_seconds} seconds. "
            "The action has stopped before execution continued."
        )

    thread.join()
    context.check_cancellation()
    if error_box:
        raise error_box[0]
    return result_box[0] if result_box else None
