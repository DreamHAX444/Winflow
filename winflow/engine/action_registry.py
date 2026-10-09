"""Action registry for WinFlow.

Manages registration, lookup, and instantiation of automation action handlers.
Extensible so new action types can be plugged in without modifying the engine.
"""

from collections.abc import Callable

from winflow.actions.base import BaseAction
from winflow.core.errors import ActionNotFoundError, RegistryError


class ActionRegistry:
    """Registry maintaining available BaseAction classes by name."""

    def __init__(self) -> None:
        self._actions: dict[str, type[BaseAction]] = {}

    def register(
        self,
        name: str,
        action_cls: type[BaseAction],
        allow_override: bool = False,
    ) -> None:
        """Register an action class with a unique name.

        Args:
            name: Identifier name for the action.
            action_cls: Class inheriting from BaseAction.
            allow_override: Whether to permit overwriting an existing name.

        Raises:
            RegistryError: If action_cls is not a subclass of BaseAction,
                          or if name is already registered without allow_override.
        """
        if not isinstance(name, str) or not name.strip():
            raise RegistryError("Action name must be a non-empty string.")

        if not (isinstance(action_cls, type) and issubclass(action_cls, BaseAction)):
            raise RegistryError(
                f"Action '{name}' must be a subclass of BaseAction, got: {action_cls}"
            )

        name = name.strip()
        if name in self._actions and not allow_override:
            raise RegistryError(
                f"Action '{name}' is already registered. Set allow_override=True to replace."
            )

        self._actions[name] = action_cls

    def unregister(self, name: str) -> None:
        """Remove an action from the registry.

        Args:
            name: Name of the action to unregister.

        Raises:
            ActionNotFoundError: If action is not registered.
        """
        if name not in self._actions:
            raise ActionNotFoundError(f"Cannot unregister unknown action: '{name}'")
        del self._actions[name]

    def get(self, name: str) -> type[BaseAction]:
        """Retrieve an action class by name.

        Args:
            name: Action identifier.

        Returns:
            The registered BaseAction class.

        Raises:
            ActionNotFoundError: If the action is not found.
        """
        if name not in self._actions:
            raise ActionNotFoundError(f"Action '{name}' is not registered.")
        return self._actions[name]

    def has(self, name: str) -> bool:
        """Check if an action name is registered."""
        return name in self._actions

    def list_actions(self) -> list[str]:
        """List all currently registered action names."""
        return sorted(list(self._actions.keys()))

    def clear(self) -> None:
        """Clear all registered actions."""
        self._actions.clear()


_DEFAULT_ACTION_REGISTRY: ActionRegistry | None = None


def get_action_registry() -> ActionRegistry:
    """Retrieve the global default ActionRegistry instance."""
    global _DEFAULT_ACTION_REGISTRY
    if _DEFAULT_ACTION_REGISTRY is None:
        _DEFAULT_ACTION_REGISTRY = ActionRegistry()
        from winflow.actions import register_desktop_actions
        register_desktop_actions(_DEFAULT_ACTION_REGISTRY)
    return _DEFAULT_ACTION_REGISTRY


def register_action(
    name: str | None = None,
    registry: ActionRegistry | None = None,
) -> Callable[[type[BaseAction]], type[BaseAction]]:
    """Decorator to register an action class into the action registry.

    Args:
        name: Optional custom name. If omitted, uses action class name.
        registry: Target registry. Defaults to global action registry.
    """
    target_reg = registry or get_action_registry()

    def decorator(cls: type[BaseAction]) -> type[BaseAction]:
        action_name = name or cls.__name__
        target_reg.register(action_name, cls)
        return cls

    return decorator
