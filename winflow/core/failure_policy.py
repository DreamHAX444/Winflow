"""Failure policy resolution and handling subsystem for WinFlow.

Decouples failure policy interpretation (stop, retry, skip, continue, restart_workflow)
from the core workflow execution runner.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from winflow.core.errors import WorkflowExecutionError
from winflow.core.logger import get_logger

if TYPE_CHECKING:
    from winflow.engine.execution_context import ExecutionContext


class FailurePolicy(str, Enum):
    """Supported failure resolution policies."""
    STOP = "stop"
    RETRY = "retry"
    SKIP = "skip"
    CONTINUE = "continue"
    RESTART_WORKFLOW = "restart_workflow"


class FailureAction(str, Enum):
    """Runner instructions emitted by FailureHandler."""
    ABORT = "ABORT"
    SKIP = "SKIP"
    CONTINUE = "CONTINUE"
    RESTART = "RESTART"


@dataclass
class FailureConfig:
    """Configuration options for handling step failures."""
    policy: str = "stop"
    attempts: int = 1
    delay_seconds: float = 0.0
    max_restarts: int = 3

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "FailureConfig":
        if not data:
            return cls()
        return cls(
            policy=str(data.get("policy", "stop")).lower(),
            attempts=int(data.get("attempts", 1)),
            delay_seconds=float(data.get("delay_seconds", 0.0)),
            max_restarts=int(data.get("max_restarts", 3)),
        )


@dataclass
class FailureDecision:
    """Action instruction produced after evaluating a failure."""
    action: FailureAction
    policy: str
    message: str
    restart_count: int = 0


class FailureHandler:
    """Evaluates step failures and determines workflow continuation decisions."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger: logging.Logger = logger or get_logger()

    def handle_failure(
        self,
        error: Exception,
        failure_config: FailureConfig,
        context: "ExecutionContext",
        step_id: Any = None,
    ) -> FailureDecision:
        """Resolve a failure according to configured policy.

        Args:
            error: Caught failure exception.
            failure_config: FailureConfig for this step or workflow.
            context: Current ExecutionContext.
            step_id: Step number or name for logging context.

        Returns:
            FailureDecision instructing the runner what action to take.

        Raises:
            WorkflowExecutionError: If max restarts are exceeded or error forces abort.
        """
        context.check_cancellation()
        policy = failure_config.policy.strip().lower()

        if policy == FailurePolicy.STOP:
            self.logger.error("Step %s failed with policy 'STOP': %s", step_id, error)
            return FailureDecision(
                action=FailureAction.ABORT,
                policy=policy,
                message=str(error),
            )

        if policy == FailurePolicy.SKIP:
            self.logger.warning(
                "Step %s failed with policy 'SKIP'. Skipping to next step: %s",
                step_id,
                error,
            )
            return FailureDecision(
                action=FailureAction.SKIP,
                policy=policy,
                message=f"Skipped step {step_id}: {error}",
            )

        if policy == FailurePolicy.CONTINUE:
            self.logger.warning(
                "Step %s failed with policy 'CONTINUE'. Continuing execution: %s",
                step_id,
                error,
            )
            return FailureDecision(
                action=FailureAction.CONTINUE,
                policy=policy,
                message=f"Continued past failed step {step_id}: {error}",
            )

        if policy == FailurePolicy.RESTART_WORKFLOW:
            current_restarts = int(context.get_variable("_restart_count", 0)) + 1
            max_restarts = max(1, failure_config.max_restarts)

            if current_restarts > max_restarts:
                msg = (
                    f"Workflow restart limit exceeded ({max_restarts} maximum restarts allowed). "
                    f"Aborting on step {step_id} failure: {error}"
                )
                self.logger.error(msg)
                raise WorkflowExecutionError(msg) from error

            context.set_variable("_restart_count", current_restarts)
            self.logger.warning(
                "Step %s failed with policy 'RESTART_WORKFLOW'. "
                "Restarting workflow from step 1 (restart %d/%d): %s",
                step_id,
                current_restarts,
                max_restarts,
                error,
            )
            return FailureDecision(
                action=FailureAction.RESTART,
                policy=policy,
                message=f"Restarting workflow ({current_restarts}/{max_restarts}): {error}",
                restart_count=current_restarts,
            )

        if policy == FailurePolicy.RETRY:
            # RetryExecutor has already made every configured attempt before the
            # failure reaches this handler. Abort without another retry loop.
            self.logger.error(
                "Step %s failed after its retry attempts were used: %s",
                step_id,
                error,
            )
            return FailureDecision(
                action=FailureAction.ABORT,
                policy=policy,
                message=str(error),
            )

        # Fallback to STOP for unknown policies
        self.logger.warning(
            "Unknown failure policy '%s'. Defaulting to STOP. Error: %s",
            policy,
            error,
        )
        return FailureDecision(
            action=FailureAction.ABORT,
            policy=policy,
            message=str(error),
        )
