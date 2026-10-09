"""Window verification implementations for WinFlow.

Provides window_exists and window_active checks.
"""

from typing import TYPE_CHECKING, Any

from winflow.backend import AutomationBackend
from winflow.verification.base import BaseVerification, VerificationResult

if TYPE_CHECKING:
    from winflow.engine.execution_context import ExecutionContext


class WindowExistsVerification(BaseVerification):
    """Verifies whether a window matching target criteria currently exists."""

    def __init__(
        self,
        name: str = "window_exists",
        params: dict[str, Any] | None = None,
        backend: AutomationBackend | None = None,
    ) -> None:
        super().__init__(name=name, params=params, backend=backend)

    def _extract_target(self, opts: dict[str, Any]) -> dict[str, Any]:
        if "target" in opts and isinstance(opts["target"], dict):
            return dict(opts["target"])
        target = {}
        for k in ("title_contains", "title_exact", "process_name", "executable_name", "hwnd"):
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
                error="WindowExistsVerification requires target criteria (e.g. title_contains, process_name).",
            )

        win = self.backend.window.find_window(target)
        if win:
            return VerificationResult(
                success=True,
                verification_type=self.name,
                details={"found_window": win, "target": target},
            )

        return VerificationResult(
            success=False,
            verification_type=self.name,
            error=f"Window matching target {target} was not found.",
            details={"target": target},
        )


class WindowActiveVerification(BaseVerification):
    """Verifies that the foreground/active window strictly matches target criteria."""

    def __init__(
        self,
        name: str = "window_active",
        params: dict[str, Any] | None = None,
        backend: AutomationBackend | None = None,
    ) -> None:
        super().__init__(name=name, params=params, backend=backend)

    def _extract_target(self, opts: dict[str, Any]) -> dict[str, Any]:
        if "target" in opts and isinstance(opts["target"], dict):
            return dict(opts["target"])
        target = {}
        for k in ("title_contains", "title_exact", "process_name", "executable_name", "hwnd"):
            if k in opts and opts[k] is not None:
                target[k] = opts[k]
        return target

    def _matches(self, active: dict[str, Any], target: dict[str, Any]) -> bool:
        title = active.get("title", "")
        proc = active.get("process_name", "")
        hwnd = active.get("hwnd")

        if "hwnd" in target and hwnd != target["hwnd"]:
            return False
        if "title_exact" in target and title != target["title_exact"]:
            return False
        if "title_contains" in target and target["title_contains"].lower() not in title.lower():
            return False
        if "process_name" in target and target["process_name"].lower() != proc.lower():
            return False
        if "executable_name" in target and target["executable_name"].lower() != proc.lower():
            return False
        return True

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
                error="WindowActiveVerification requires target criteria (e.g. title_contains, process_name).",
            )

        active = self.backend.window.get_active_window()
        if self._matches(active, target):
            return VerificationResult(
                success=True,
                verification_type=self.name,
                details={"active_window": active, "target": target},
            )

        return VerificationResult(
            success=False,
            verification_type=self.name,
            error=(
                f"Foreground window '{active.get('title')}' (proc: '{active.get('process_name')}', "
                f"hwnd: {active.get('hwnd')}) does not match target {target}."
            ),
            details={"active_window": active, "target": target},
        )
