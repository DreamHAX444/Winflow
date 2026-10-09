"""Process verification implementations for WinFlow.

Provides process_running check.
"""

from typing import TYPE_CHECKING, Any

from winflow.backend import AutomationBackend
from winflow.verification.base import BaseVerification, VerificationResult

if TYPE_CHECKING:
    from winflow.engine.execution_context import ExecutionContext


class ProcessRunningVerification(BaseVerification):
    """Verifies whether an expected process is currently running."""

    def __init__(
        self,
        name: str = "process_running",
        params: dict[str, Any] | None = None,
        backend: AutomationBackend | None = None,
    ) -> None:
        super().__init__(name=name, params=params, backend=backend)

    def _extract_target(self, opts: dict[str, Any]) -> dict[str, Any]:
        if "target" in opts and isinstance(opts["target"], dict):
            return dict(opts["target"])
        target = {}
        for k in ("process_name", "executable_name", "pid"):
            if k in opts and opts[k] is not None:
                target[k] = opts[k]
        return target

    def verify(
        self,
        context: "ExecutionContext",
        **kwargs: Any,
    ) -> VerificationResult:
        context.check_cancellation()
        opts = {**self.params, **kwargs}
        target = self._extract_target(opts)

        if not target:
            return VerificationResult(
                success=False,
                verification_type=self.name,
                error="ProcessRunningVerification requires 'process_name', 'executable_name', or 'pid'.",
            )

        win = self.backend.window.find_window(target)
        if win and win.get("pid"):
            return VerificationResult(
                success=True,
                verification_type=self.name,
                details={"pid": win.get("pid"), "process_name": win.get("process_name"), "target": target},
            )

        return VerificationResult(
            success=False,
            verification_type=self.name,
            error=f"Process matching {target} was not detected as running.",
            details={"target": target},
        )
