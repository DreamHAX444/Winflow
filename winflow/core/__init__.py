"""WinFlow Core Module.

Contains centralized errors, emergency stop, logging, timeout, retry, and failure policy abstractions.
"""

from winflow.core.emergency_stop import (
    EmergencyStop,
    get_emergency_stop,
    set_emergency_stop,
)
from winflow.core.errors import (
    ActionExecutionError,
    ActionNotFoundError,
    ConfigurationError,
    DesktopLockError,
    DispatcherError,
    EmergencyStopTriggered,
    ListenerInitializationError,
    MalformedNotificationError,
    NotificationSubscriptionError,
    RegistryError,
    SchemaValidationError,
    StorageError,
    TriggerConfigurationError,
    TriggerError,
    TriggerNotFoundError,
    UnsupportedEnvironmentError,
    VerificationFailedError,
    VerificationNotFoundError,
    WinFlowError,
    WorkflowExecutionError,
)
from winflow.core.execution_lock import (
    DesktopExecutionLock,
    get_desktop_lock,
    reset_desktop_lock,
)
from winflow.core.failure_policy import (
    FailureAction,
    FailureConfig,
    FailureDecision,
    FailureHandler,
    FailurePolicy,
)
from winflow.core.logger import (
    WinFlowLogAdapter,
    create_context_logger,
    get_logger,
    log_step_event,
    setup_logger,
)
from winflow.core.retry import RetryConfig, RetryExecutor
from winflow.core.timeout import execute_with_timeout

__all__ = [
    "ActionExecutionError",
    "ActionNotFoundError",
    "ConfigurationError",
    "DesktopExecutionLock",
    "DesktopLockError",
    "DispatcherError",
    "EmergencyStop",
    "EmergencyStopTriggered",
    "FailureAction",
    "FailureConfig",
    "FailureDecision",
    "FailureHandler",
    "FailurePolicy",
    "ListenerInitializationError",
    "MalformedNotificationError",
    "NotificationSubscriptionError",
    "RegistryError",
    "RetryConfig",
    "RetryExecutor",
    "SchemaValidationError",
    "StorageError",
    "TriggerConfigurationError",
    "TriggerError",
    "TriggerNotFoundError",
    "UnsupportedEnvironmentError",
    "VerificationFailedError",
    "VerificationNotFoundError",
    "WinFlowError",
    "WinFlowLogAdapter",
    "WorkflowExecutionError",
    "create_context_logger",
    "execute_with_timeout",
    "get_desktop_lock",
    "get_emergency_stop",
    "get_logger",
    "log_step_event",
    "reset_desktop_lock",
    "set_emergency_stop",
    "setup_logger",
]

