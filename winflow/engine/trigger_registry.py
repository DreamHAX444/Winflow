"""Trigger registry for WinFlow.

Manages registration, lookup, and instantiation of event triggers.
Allows modular addition of triggers without touching the core workflow engine.
"""

from collections.abc import Callable

from winflow.core.errors import RegistryError, TriggerNotFoundError
from winflow.triggers.base import BaseTrigger


class TriggerRegistry:
    """Registry maintaining available BaseTrigger classes by name."""

    def __init__(self) -> None:
        self._triggers: dict[str, type[BaseTrigger]] = {}

    def register(
        self,
        name: str,
        trigger_cls: type[BaseTrigger],
        allow_override: bool = False,
    ) -> None:
        """Register a trigger class under a unique name.

        Args:
            name: Identifier name for the trigger.
            trigger_cls: Class inheriting from BaseTrigger.
            allow_override: Whether to permit overwriting an existing name.

        Raises:
            RegistryError: If trigger_cls is not a subclass of BaseTrigger,
                          or if name is already registered without allow_override.
        """
        if not isinstance(name, str) or not name.strip():
            raise RegistryError("Trigger name must be a non-empty string.")

        if not (isinstance(trigger_cls, type) and issubclass(trigger_cls, BaseTrigger)):
            raise RegistryError(
                f"Trigger '{name}' must be a subclass of BaseTrigger, got: {trigger_cls}"
            )

        name = name.strip()
        if name in self._triggers and not allow_override:
            raise RegistryError(
                f"Trigger '{name}' is already registered. Set allow_override=True to replace."
            )

        self._triggers[name] = trigger_cls

    def unregister(self, name: str) -> None:
        """Remove a trigger from the registry.

        Args:
            name: Name of the trigger to remove.

        Raises:
            TriggerNotFoundError: If trigger is not registered.
        """
        if name not in self._triggers:
            raise TriggerNotFoundError(f"Cannot unregister unknown trigger: '{name}'")
        del self._triggers[name]

    def get(self, name: str) -> type[BaseTrigger]:
        """Retrieve a trigger class by name.

        Args:
            name: Trigger identifier.

        Returns:
            The registered BaseTrigger class.

        Raises:
            TriggerNotFoundError: If the trigger is not found.
        """
        if name not in self._triggers:
            raise TriggerNotFoundError(f"Trigger '{name}' is not registered.")
        return self._triggers[name]

    def has(self, name: str) -> bool:
        """Check if a trigger name is registered."""
        return name in self._triggers

    def list_triggers(self) -> list[str]:
        """List all currently registered trigger names."""
        return sorted(list(self._triggers.keys()))

    def clear(self) -> None:
        """Clear all registered triggers."""
        self._triggers.clear()


_DEFAULT_TRIGGER_REGISTRY: TriggerRegistry | None = None


def get_trigger_registry() -> TriggerRegistry:
    """Retrieve the global default TriggerRegistry instance, populated with defaults."""
    global _DEFAULT_TRIGGER_REGISTRY
    if _DEFAULT_TRIGGER_REGISTRY is None:
        _DEFAULT_TRIGGER_REGISTRY = TriggerRegistry()
        from winflow.triggers import register_desktop_triggers
        register_desktop_triggers(_DEFAULT_TRIGGER_REGISTRY)
    return _DEFAULT_TRIGGER_REGISTRY


def register_trigger(
    name: str | None = None,
    registry: TriggerRegistry | None = None,
) -> Callable[[type[BaseTrigger]], type[BaseTrigger]]:
    """Decorator to register a trigger class into the trigger registry.

    Args:
        name: Optional custom name. If omitted, uses trigger class name.
        registry: Target registry. Defaults to global trigger registry.
    """
    target_reg = registry or get_trigger_registry()

    def decorator(cls: type[BaseTrigger]) -> type[BaseTrigger]:
        trigger_name = name or cls.__name__
        target_reg.register(trigger_name, cls)
        return cls

    return decorator
