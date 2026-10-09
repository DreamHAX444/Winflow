"""Window management actions for WinFlow.

Implements focus_window and get_active_window with flexible targeting criteria.
"""

from typing import TYPE_CHECKING, Any

from winflow.actions.base import BaseAction
from winflow.backend import AutomationBackend, get_backend
from winflow.core.errors import ActionExecutionError

if TYPE_CHECKING:
    from winflow.engine.execution_context import ExecutionContext


class FocusWindowAction(BaseAction):
    """Find and focus a target window matching configurable criteria."""

    def __init__(
        self,
        name: str = "focus_window",
        params: dict[str, Any] | None = None,
        backend: AutomationBackend | None = None,
    ) -> None:
        super().__init__(name=name, params=params)
        self.backend = backend or get_backend()

    def _extract_target(self, opts: dict[str, Any]) -> dict[str, Any]:
        target: dict[str, Any] = {}
        
        # Support V1 target dictionary
        if "target" in opts and isinstance(opts["target"], dict):
            target.update(opts["target"])

        # Support V2 UI match_type / match_value
        if "match_type" in opts and "match_value" in opts:
            m_type = opts["match_type"]
            if m_type == "exe_name": 
                m_type = "executable_name"
            target[m_type] = opts["match_value"]

        # Allow flat criteria directly on parameters
        for key in (
            "title_contains",
            "title_exact",
            "process_name",
            "executable_name",
            "hwnd",
            "pid",
            "process_id",
        ):
            if key in opts and (opts[key] is not None or key == "hwnd"):
                target[key] = opts[key]
                
        return {
            key: value
            for key, value in target.items()
            if value or (key in {"hwnd", "pid", "process_id"} and key in target)
        }

    def execute(self, context: "ExecutionContext", **kwargs: Any) -> dict[str, Any]:
        context.check_cancellation()
        opts = {**self.params, **kwargs}
        target = self._extract_target(opts)

        if not target:
            raise ActionExecutionError(
                "FocusWindowAction requires target criteria such as a window title, "
                "application, process ID, or HWND."
            )

        win = self.backend.window.find_window(target)
        if not win:
            raise ActionExecutionError(
                "Target window could not be found confidently. Check that the "
                "window is open and its saved title or application criteria are current."
            )

        state = str(opts.get("state", "restore")).lower()
        hwnd = win.get("hwnd")
        if not hwnd:
            raise ActionExecutionError(
                "The matched target window no longer has a valid window handle."
            )
        success = self.backend.window.focus_window({"hwnd": hwnd}, state=state)
        if not success:
            raise ActionExecutionError(
                "The matched target window was found but could not be brought "
                "to the foreground."
            )

        window_title = win.get("title", "")
        context.update_window(window_title)
        context.check_cancellation()

        return {
            "action": "focus_window",
            "title": window_title,
            "hwnd": win.get("hwnd"),
            "pid": win.get("pid"),
            "process_name": win.get("process_name"),
        }


class GetActiveWindowAction(BaseAction):
    """Retrieve details on the active foreground window and update ExecutionContext."""

    def __init__(
        self,
        name: str = "get_active_window",
        params: dict[str, Any] | None = None,
        backend: AutomationBackend | None = None,
    ) -> None:
        super().__init__(name=name, params=params)
        self.backend = backend or get_backend()

    def execute(self, context: "ExecutionContext", **kwargs: Any) -> dict[str, Any]:
        context.check_cancellation()
        opts = {**self.params, **kwargs}

        info = self.backend.window.get_active_window()
        window_title = info.get("title", "")
        context.update_window(window_title)

        var_name = opts.get("variable_name")
        if var_name and isinstance(var_name, str):
            context.set_variable(var_name, info)

        context.check_cancellation()
        return {
            "action": "get_active_window",
            "title": window_title,
            "hwnd": info.get("hwnd"),
            "pid": info.get("pid"),
            "process_name": info.get("process_name"),
        }
