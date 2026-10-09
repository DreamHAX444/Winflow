"""Base action interface for WinFlow.

All action implementations (mouse, keyboard, clipboard, wait, window, etc.)
must inherit from BaseAction.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from winflow.engine.execution_context import ExecutionContext


class BaseAction(ABC):
    """Abstract base class for all automation actions."""

    def __init__(self, name: str = "", params: dict[str, Any] | None = None) -> None:
        """Initialize the action.

        Args:
            name: Identifier or name for this action.
            params: Default configuration parameters for the action.
        """
        self.name: str = name
        self.params: dict[str, Any] = params or {}

    @abstractmethod
    def execute(self, context: "ExecutionContext", **kwargs: Any) -> Any:
        """Execute the action logic within the provided context.

        Args:
            context: Current workflow ExecutionContext.
            **kwargs: Dynamic or overridden action parameters.

        Returns:
            Result or status of the action execution.
        """
