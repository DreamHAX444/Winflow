"""Mouse automation actions for WinFlow.

Implements move, click, double_click, right_click, and scroll.
"""

from typing import TYPE_CHECKING, Any

from winflow.actions.base import BaseAction
from winflow.backend import AutomationBackend, get_backend
from winflow.core.errors import ActionExecutionError

if TYPE_CHECKING:
    from winflow.engine.execution_context import ExecutionContext


class MoveAction(BaseAction):
    """Move mouse cursor to specific coordinates."""

    def __init__(
        self,
        name: str = "move",
        params: dict[str, Any] | None = None,
        backend: AutomationBackend | None = None,
    ) -> None:
        super().__init__(name=name, params=params)
        self.backend = backend or get_backend()

    def execute(self, context: "ExecutionContext", **kwargs: Any) -> dict[str, Any]:
        context.check_cancellation()
        opts = {**self.params, **kwargs}

        if "x" not in opts or "y" not in opts:
            raise ActionExecutionError("MoveAction requires 'x' and 'y' coordinates.")

        try:
            x = int(opts["x"])
            y = int(opts["y"])
        except (ValueError, TypeError) as exc:
            raise ActionExecutionError(f"Coordinates must be integers, got x={opts.get('x')}, y={opts.get('y')}") from exc

        if x < 0 or y < 0:
            raise ActionExecutionError(f"Coordinates cannot be negative: x={x}, y={y}")

        duration_ms = int(opts.get("duration_ms", 0))
        self.backend.mouse.move(x=x, y=y, duration_ms=duration_ms)
        context.check_cancellation()
        return {"action": "move", "x": x, "y": y, "duration_ms": duration_ms}


class ClickAction(BaseAction):
    """Click mouse button at current position or specified coordinates."""

    def __init__(
        self,
        name: str = "click",
        params: dict[str, Any] | None = None,
        backend: AutomationBackend | None = None,
    ) -> None:
        super().__init__(name=name, params=params)
        self.backend = backend or get_backend()

    def execute(self, context: "ExecutionContext", **kwargs: Any) -> dict[str, Any]:
        context.check_cancellation()
        opts = {**self.params, **kwargs}

        x = opts.get("x")
        y = opts.get("y")
        if x is not None:
            try:
                x = int(x)
            except (ValueError, TypeError) as exc:
                raise ActionExecutionError(f"Coordinate 'x' must be an integer, got: {x}") from exc
            if x < 0:
                raise ActionExecutionError(f"Coordinate 'x' cannot be negative: {x}")

        if y is not None:
            try:
                y = int(y)
            except (ValueError, TypeError) as exc:
                raise ActionExecutionError(f"Coordinate 'y' must be an integer, got: {y}") from exc
            if y < 0:
                raise ActionExecutionError(f"Coordinate 'y' cannot be negative: {y}")

        button = str(opts.get("button", "left")).lower()
        if button not in {"left", "right", "middle"}:
            raise ActionExecutionError(f"Invalid mouse button '{button}'. Must be 'left', 'right', or 'middle'.")

        clicks = int(opts.get("clicks", 1))
        if clicks < 1:
            raise ActionExecutionError(f"Clicks count must be >= 1, got: {clicks}")

        interval_ms = int(opts.get("interval_ms", 50))
        self.backend.mouse.click(x=x, y=y, button=button, clicks=clicks, interval_ms=interval_ms)
        context.check_cancellation()
        return {"action": "click", "x": x, "y": y, "button": button, "clicks": clicks}


class DoubleClickAction(BaseAction):
    """Perform a double click."""

    def __init__(
        self,
        name: str = "double_click",
        params: dict[str, Any] | None = None,
        backend: AutomationBackend | None = None,
    ) -> None:
        super().__init__(name=name, params=params)
        self.backend = backend or get_backend()

    def execute(self, context: "ExecutionContext", **kwargs: Any) -> dict[str, Any]:
        context.check_cancellation()
        opts = {**self.params, **kwargs}
        x = opts.get("x")
        y = opts.get("y")
        button = str(opts.get("button", "left")).lower()
        if x is not None:
            x = int(x)
        if y is not None:
            y = int(y)

        self.backend.mouse.double_click(x=x, y=y, button=button)
        context.check_cancellation()
        return {"action": "double_click", "x": x, "y": y, "button": button}


class RightClickAction(BaseAction):
    """Perform a right click."""

    def __init__(
        self,
        name: str = "right_click",
        params: dict[str, Any] | None = None,
        backend: AutomationBackend | None = None,
    ) -> None:
        super().__init__(name=name, params=params)
        self.backend = backend or get_backend()

    def execute(self, context: "ExecutionContext", **kwargs: Any) -> dict[str, Any]:
        context.check_cancellation()
        opts = {**self.params, **kwargs}
        x = opts.get("x")
        y = opts.get("y")
        if x is not None:
            x = int(x)
        if y is not None:
            y = int(y)

        self.backend.mouse.right_click(x=x, y=y)
        context.check_cancellation()
        return {"action": "right_click", "x": x, "y": y}


class ScrollAction(BaseAction):
    """Perform mouse wheel scroll.
    
    Amount meaning:
        Positive values (> 0) scroll UP (away from user).
        Negative values (< 0) scroll DOWN (toward user).
    """

    def __init__(
        self,
        name: str = "scroll",
        params: dict[str, Any] | None = None,
        backend: AutomationBackend | None = None,
    ) -> None:
        super().__init__(name=name, params=params)
        self.backend = backend or get_backend()

    def execute(self, context: "ExecutionContext", **kwargs: Any) -> dict[str, Any]:
        context.check_cancellation()
        opts = {**self.params, **kwargs}
        if "amount" not in opts:
            raise ActionExecutionError("ScrollAction requires 'amount' parameter.")

        try:
            amount = int(opts["amount"])
        except (ValueError, TypeError) as exc:
            raise ActionExecutionError(f"Scroll amount must be an integer, got: {opts.get('amount')}") from exc

        x = opts.get("x")
        y = opts.get("y")
        if x is not None:
            x = int(x)
        if y is not None:
            y = int(y)

        self.backend.mouse.scroll(amount=amount, x=x, y=y)
        context.check_cancellation()
        return {"action": "scroll", "amount": amount, "x": x, "y": y}
