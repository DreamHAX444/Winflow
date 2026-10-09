"""Verification registry for WinFlow.

Manages registration, lookup, and instantiation of state verification checks.
Behaves consistently with ActionRegistry and TriggerRegistry.
"""

from collections.abc import Callable

from winflow.core.errors import RegistryError, VerificationNotFoundError
from winflow.verification.base import BaseVerification


class VerificationRegistry:
    """Registry maintaining available BaseVerification classes by name."""

    def __init__(self) -> None:
        self._verifications: dict[str, type[BaseVerification]] = {}

    def register(
        self,
        name: str,
        verification_cls: type[BaseVerification],
        allow_override: bool = False,
    ) -> None:
        """Register a verification class under a unique name.

        Args:
            name: Identifier name for the verification.
            verification_cls: Class inheriting from BaseVerification.
            allow_override: Whether to permit overwriting an existing name.

        Raises:
            RegistryError: If verification_cls is not a subclass of BaseVerification,
                          or if name is already registered without allow_override.
        """
        if not isinstance(name, str) or not name.strip():
            raise RegistryError("Verification name must be a non-empty string.")

        if not (isinstance(verification_cls, type) and issubclass(verification_cls, BaseVerification)):
            raise RegistryError(
                f"Verification '{name}' must be a subclass of BaseVerification, got: {verification_cls}"
            )

        name = name.strip()
        if name in self._verifications and not allow_override:
            raise RegistryError(
                f"Verification '{name}' is already registered. Set allow_override=True to replace."
            )

        self._verifications[name] = verification_cls

    def unregister(self, name: str) -> None:
        """Remove a verification from the registry.

        Args:
            name: Name of the verification to remove.

        Raises:
            VerificationNotFoundError: If verification is not registered.
        """
        if name not in self._verifications:
            raise VerificationNotFoundError(f"Cannot unregister unknown verification: '{name}'")
        del self._verifications[name]

    def get(self, name: str) -> type[BaseVerification]:
        """Retrieve a verification class by name.

        Args:
            name: Verification identifier.

        Returns:
            The registered BaseVerification class.

        Raises:
            VerificationNotFoundError: If the verification is not found.
        """
        if name not in self._verifications:
            raise VerificationNotFoundError(f"Verification '{name}' is not registered.")
        return self._verifications[name]

    def has(self, name: str) -> bool:
        """Check if a verification name is registered."""
        return name in self._verifications

    def list_verifications(self) -> list[str]:
        """List all currently registered verification names."""
        return sorted(list(self._verifications.keys()))

    def clear(self) -> None:
        """Clear all registered verifications."""
        self._verifications.clear()


_DEFAULT_VERIFICATION_REGISTRY: VerificationRegistry | None = None


def get_verification_registry() -> VerificationRegistry:
    """Retrieve the global default VerificationRegistry instance, populated with defaults."""
    global _DEFAULT_VERIFICATION_REGISTRY
    if _DEFAULT_VERIFICATION_REGISTRY is None:
        _DEFAULT_VERIFICATION_REGISTRY = VerificationRegistry()
        from winflow.verification import register_desktop_verifications
        register_desktop_verifications(_DEFAULT_VERIFICATION_REGISTRY)
    return _DEFAULT_VERIFICATION_REGISTRY


def register_verification(
    name: str | None = None,
    registry: VerificationRegistry | None = None,
) -> Callable[[type[BaseVerification]], type[BaseVerification]]:
    """Decorator to register a verification class into the verification registry.

    Args:
        name: Optional custom name. If omitted, uses class name.
        registry: Target registry. Defaults to global verification registry.
    """
    target_reg = registry or get_verification_registry()

    def decorator(cls: type[BaseVerification]) -> type[BaseVerification]:
        ver_name = name or cls.__name__
        target_reg.register(ver_name, cls)
        return cls

    return decorator
