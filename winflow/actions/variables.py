from typing import TYPE_CHECKING, Any

from winflow.actions.base import BaseAction
from winflow.core.errors import ActionExecutionError

if TYPE_CHECKING:
    from winflow.engine.execution_context import ExecutionContext


class SetVariableAction(BaseAction):
    """Sets a runtime variable in the execution context."""

    def __init__(self, name: str = "set_variable", params: dict[str, Any] | None = None) -> None:
        super().__init__(name=name, params=params)

    def execute(self, context: "ExecutionContext", **kwargs: Any) -> Any:
        context.check_cancellation()
        opts = {**self.params, **kwargs}
        key = opts.get("name", opts.get("key"))
        value = opts.get("value")
        
        if not isinstance(key, str) or not key.strip():
            raise ActionExecutionError("SetVariableAction requires a non-empty 'name' parameter.")
            
        context.set_variable(key.strip(), value)
        context.check_cancellation()
        return {"action": "set_variable", "name": key.strip()}
