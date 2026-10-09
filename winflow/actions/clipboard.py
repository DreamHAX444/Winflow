"""Clipboard automation actions for WinFlow.

Implements clipboard_read, clipboard_write, and clipboard_clear with
ExecutionContext state integration and preservation support.
"""

from typing import TYPE_CHECKING, Any

from winflow.actions.base import BaseAction
from winflow.backend import AutomationBackend, get_backend
from winflow.core.errors import ActionExecutionError

if TYPE_CHECKING:
    from winflow.engine.execution_context import ExecutionContext


class ClipboardReadAction(BaseAction):
    """Read clipboard text and store in ExecutionContext."""

    def __init__(
        self,
        name: str = "clipboard_read",
        params: dict[str, Any] | None = None,
        backend: AutomationBackend | None = None,
    ) -> None:
        super().__init__(name=name, params=params)
        self.backend = backend or get_backend()

    def execute(self, context: "ExecutionContext", **kwargs: Any) -> dict[str, Any]:
        context.check_cancellation()
        opts = {**self.params, **kwargs}

        content = self.backend.clipboard.read_text()
        context.set_clipboard(content)

        var_name = opts.get("variable_name")
        if var_name and isinstance(var_name, str):
            context.set_variable(var_name, content)

        context.check_cancellation()
        return {"action": "clipboard_read", "length": len(content), "variable_name": var_name}


class ClipboardWriteAction(BaseAction):
    """Write text to system clipboard and update ExecutionContext."""

    def __init__(
        self,
        name: str = "clipboard_write",
        params: dict[str, Any] | None = None,
        backend: AutomationBackend | None = None,
    ) -> None:
        super().__init__(name=name, params=params)
        self.backend = backend or get_backend()

    def execute(self, context: "ExecutionContext", **kwargs: Any) -> dict[str, Any]:
        context.check_cancellation()
        opts = {**self.params, **kwargs}

        text = opts.get("text")
        if text is None:
            # Check if variable_name was provided as source
            var_name = opts.get("variable_name")
            if var_name:
                text = context.get_variable(var_name)

        if text is None:
            raise ActionExecutionError("ClipboardWriteAction requires 'text' or 'variable_name' with existing value.")

        text_str = str(text)

        # Optional preservation preparation: backup previous clipboard if requested
        if opts.get("preserve_previous", False):
            try:
                prev = self.backend.clipboard.read_text()
                context.set_variable("_clipboard_backup", prev)
            except Exception:
                pass

        self.backend.clipboard.write_text(text_str)
        context.set_clipboard(text_str)
        context.check_cancellation()
        return {"action": "clipboard_write", "length": len(text_str)}


class ClipboardClearAction(BaseAction):
    """Clear system clipboard content and update ExecutionContext."""

    def __init__(
        self,
        name: str = "clipboard_clear",
        params: dict[str, Any] | None = None,
        backend: AutomationBackend | None = None,
    ) -> None:
        super().__init__(name=name, params=params)
        self.backend = backend or get_backend()

    def execute(self, context: "ExecutionContext", **kwargs: Any) -> dict[str, Any]:
        context.check_cancellation()
        self.backend.clipboard.clear()
        context.set_clipboard(None)
        context.check_cancellation()
        return {"action": "clipboard_clear"}
