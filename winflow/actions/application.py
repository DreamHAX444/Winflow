"""Application management actions for WinFlow.

Implements launch_application with configurable arguments and startup waiting.
"""

from typing import TYPE_CHECKING, Any

from winflow.actions.base import BaseAction
from winflow.backend import AutomationBackend, get_backend
from winflow.core.errors import ActionExecutionError

if TYPE_CHECKING:
    from winflow.engine.execution_context import ExecutionContext


class LaunchApplicationAction(BaseAction):
    """Launch an executable application without shell wrapping."""

    def __init__(
        self,
        name: str = "launch_application",
        params: dict[str, Any] | None = None,
        backend: AutomationBackend | None = None,
    ) -> None:
        super().__init__(name=name, params=params)
        self.backend = backend or get_backend()

    def execute(self, context: "ExecutionContext", **kwargs: Any) -> dict[str, Any]:
        context.check_cancellation()
        opts = {**self.params, **kwargs}

        path = opts.get("path")
        if not path or not isinstance(path, str) or not path.strip():
            raise ActionExecutionError("LaunchApplicationAction requires a non-empty string 'path'.")

        path_str = path.strip()
        args = opts.get("arguments", [])
        if not isinstance(args, list):
            raise ActionExecutionError(f"Arguments must be a list, got: {type(args).__name__}")
        arg_list = [str(a) for a in args]

        wait_for_start = bool(opts.get("wait_for_start", False))
        timeout = float(opts.get("timeout_seconds", 10.0))

        try:
            app_info = self.backend.window.launch_application(
                path=path_str,
                arguments=arg_list,
                wait_for_start=wait_for_start,
                timeout_seconds=timeout,
            )
        except Exception as exc:
            if isinstance(exc, ActionExecutionError):
                raise
            raise ActionExecutionError(f"Failed to launch application '{path_str}': {exc}") from exc

        var_name = opts.get("variable_name")
        if var_name and isinstance(var_name, str):
            context.set_variable(var_name, app_info)

        context.check_cancellation()
        return {
            "action": "launch_application",
            "path": path_str,
            "pid": app_info.get("pid"),
            "status": app_info.get("status", "STARTED"),
        }
