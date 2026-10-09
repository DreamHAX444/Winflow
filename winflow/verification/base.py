"""Base verification interface and results for WinFlow.

All verification implementations (window_active, window_exists, process_running, etc.)
must inherit from BaseVerification.
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from winflow.backend import AutomationBackend, get_backend

if TYPE_CHECKING:
    from winflow.engine.execution_context import ExecutionContext


@dataclass
class VerificationResult:
    """Detailed result of a state verification check."""
    success: bool
    verification_type: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    elapsed_seconds: float = 0.0

    def __bool__(self) -> bool:
        """Allow checking truthiness directly (e.g. `if result: ...`)."""
        return self.success


class BaseVerification(ABC):
    """Abstract base class for state verification checks."""

    def __init__(
        self,
        name: str = "",
        params: dict[str, Any] | None = None,
        backend: AutomationBackend | None = None,
    ) -> None:
        """Initialize the verification check.

        Args:
            name: Identifier for this verification check.
            params: Default configuration parameters.
            backend: Automation backend instance.
        """
        self.name: str = name
        self.params: dict[str, Any] = params or {}
        self.backend: AutomationBackend = backend or get_backend()

    @abstractmethod
    def verify(
        self,
        context: "ExecutionContext",
        **kwargs: Any,
    ) -> bool | VerificationResult:
        """Perform a single verification check against the current state.

        Args:
            context: Current workflow ExecutionContext.
            **kwargs: Dynamic verification arguments.

        Returns:
            VerificationResult or boolean indicating success.
        """

    def poll_verify(
        self,
        context: "ExecutionContext",
        timeout_seconds: float = 0.0,
        poll_interval_seconds: float = 0.2,
        **kwargs: Any,
    ) -> VerificationResult:
        """Poll verification until success or timeout, with cancellation checks.

        Args:
            context: Current workflow ExecutionContext.
            timeout_seconds: Maximum seconds to poll (0 or negative means single check).
            poll_interval_seconds: Interval between polling attempts.
            **kwargs: Verification parameters.

        Returns:
            Final VerificationResult.
        """
        context.check_cancellation()
        start_time = time.time()
        deadline = start_time + max(0.0, timeout_seconds)

        while True:
            context.check_cancellation()
            raw_res = self.verify(context, **kwargs)

            # Normalize raw bool to VerificationResult
            if isinstance(raw_res, bool):
                res = VerificationResult(
                    success=raw_res,
                    verification_type=self.name,
                    elapsed_seconds=time.time() - start_time,
                )
            else:
                res = raw_res
                res.elapsed_seconds = time.time() - start_time

            if res.success or timeout_seconds <= 0.0 or time.time() >= deadline:
                return res

            # Responsive polling sleep
            remaining = deadline - time.time()
            sleep_total = min(max(0.01, poll_interval_seconds), remaining)
            sleep_deadline = time.time() + sleep_total

            while time.time() < sleep_deadline:
                context.check_cancellation()
                chunk = min(0.05, sleep_deadline - time.time())
                if chunk <= 0:
                    break
                time.sleep(chunk)
