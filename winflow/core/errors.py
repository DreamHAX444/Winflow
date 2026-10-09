"""Centralized exception hierarchy for the WinFlow automation framework."""



class WinFlowError(Exception):
    """Base exception for all WinFlow framework errors."""

    def __init__(self, message: str, details: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def __str__(self) -> str:
        if self.details:
            return f"{self.message} (Details: {self.details})"
        return self.message


class ConfigurationError(WinFlowError):
    """Raised when configuration loading, parsing, or structural reading fails."""


class SchemaValidationError(ConfigurationError):
    """Raised when a configuration fails schema validation rules."""


class RegistryError(WinFlowError):
    """Base exception for component registry errors."""


class ActionNotFoundError(RegistryError):
    """Raised when an action is requested but not found in ActionRegistry."""


class TriggerNotFoundError(RegistryError):
    """Raised when a trigger is requested but not found in TriggerRegistry."""


class VerificationNotFoundError(RegistryError):
    """Raised when a verification type is requested but not found in VerificationRegistry."""


class ActionExecutionError(WinFlowError):
    """Raised when an action encounters an error during execution."""


class VerificationFailedError(WinFlowError):
    """Raised when a post-action state verification check fails."""


class EmergencyStopTriggered(WinFlowError):
    """Raised when an operation is cancelled due to emergency stop activation."""


class WorkflowExecutionError(WinFlowError):
    """Raised when workflow execution encounters an unhandled runtime error."""


class WorkflowRestartRequested(WinFlowError):
    """Raised internally to request a full workflow restart from step 1."""


class StorageError(WinFlowError):
    """Raised when an error occurs during execution history persistence."""


class TriggerError(WinFlowError):
    """Base exception for all event trigger subsystem errors."""


class ListenerInitializationError(TriggerError):
    """Raised when an event listener fails to initialize."""


class UnsupportedEnvironmentError(TriggerError):
    """Raised when an event listener is invoked on an unsupported operating environment."""


class NotificationSubscriptionError(TriggerError):
    """Raised when subscribing or establishing notification event sources fails."""


class MalformedNotificationError(TriggerError):
    """Raised when incoming notification data fails parsing or validation."""


class TriggerConfigurationError(TriggerError):
    """Raised when a trigger configuration contains invalid or missing fields."""


class DispatcherError(TriggerError):
    """Raised when the event dispatcher encounters an internal fault."""


class DesktopLockError(WinFlowError):
    """Raised when desktop execution lock acquisition fails or is violated."""
