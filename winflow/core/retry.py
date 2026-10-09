"""Reusable retry execution component for WinFlow.

Executes operations with configurable retry attempts, cancellation-aware delays,
and structured logging.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

from winflow.core.logger import get_logger

if TYPE_CHECKING:
    from winflow.engine.execution_context import ExecutionContext

T = TypeVar("T")


@dataclass
class RetryConfig:
    """Configuration for retry attempts and delays."""
    attempts: int = 1
    delay_seconds: float = 0.0


class RetryExecutor:
    """Executes callables with configurable retries and responsive cancellation."""

    def __init__(
        self,
        config: RetryConfig,
        context: "ExecutionContext",
        logger: logging.Logger | None = None,
    ) -> None:
        self.config: RetryConfig = config
        self.context: ExecutionContext = context
        self.logger: logging.Logger = logger or get_logger()

    def execute(
        self,
        operation: Callable[[int], T],
        operation_name: str = "operation",
        on_retry: Callable[[int, Exception], None] | None = None,
    ) -> T:
        """Execute operation with retry attempts.

        Args:
            operation: Callable taking current attempt index (1-based) returning result.
            operation_name: Human-readable name for logging.
            on_retry: Optional callback invoked on failure before delay.

        Returns:
            Result of operation on success.

        Raises:
            EmergencyStopTriggered: If cancelled or stopped.
            Exception: Last caught exception when retries are exhausted.
        """
        attempts = max(1, self.config.attempts)
        delay = max(0.0, self.config.delay_seconds)
        last_exception: Exception | None = None

        for attempt in range(1, attempts + 1):
            self.context.check_cancellation()

            try:
                return operation(attempt)
            except Exception as exc:
                self.context.check_cancellation()
                last_exception = exc

                if attempt < attempts:
                    self.logger.warning(
                        "Attempt %d/%d failed for '%s': %s. Retrying in %.2fs...",
                        attempt,
                        attempts,
                        operation_name,
                        exc,
                        delay,
                    )
                    if on_retry:
                        try:
                            on_retry(attempt, exc)
                        except Exception:
                            pass

                    # Cancellation-aware sleep
                    deadline = time.time() + delay
                    while time.time() < deadline:
                        self.context.check_cancellation()
                        chunk = min(0.05, max(0.001, deadline - time.time()))
                        time.sleep(chunk)
                else:
                    self.logger.error(
                        "All %d retry attempts exhausted for '%s': %s",
                        attempts,
                        operation_name,
                        exc,
                    )

        if last_exception is not None:
            raise last_exception

        raise RuntimeError(f"Retry execution failed without an exception for '{operation_name}'")
