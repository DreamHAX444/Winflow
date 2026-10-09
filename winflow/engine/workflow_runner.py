"""Workflow runner component for WinFlow.

Executes steps of a validated workflow configuration using registered actions
and verifications, supporting timeouts, retries, failure policies, and restarts.
"""

import logging
import math
import sys
from typing import Any

from winflow.core.condition import ConditionEvaluator
from winflow.core.emergency_stop import EmergencyStop, get_emergency_stop
from winflow.core.errors import (
    ActionExecutionError,
    ActionNotFoundError,
    ConfigurationError,
    EmergencyStopTriggered,
    VerificationFailedError,
    VerificationNotFoundError,
    WorkflowExecutionError,
    WorkflowRestartRequested,
)
from winflow.core.execution_lock import DesktopExecutionLock, get_desktop_lock
from winflow.core.failure_policy import (
    FailureAction,
    FailureConfig,
    FailureHandler,
    FailurePolicy,
)
from winflow.core.logger import get_logger, log_step_event
from winflow.core.retry import RetryConfig, RetryExecutor
from winflow.core.timeout import execute_with_timeout
from winflow.core.variables import VariableResolver
from winflow.actions.wait import WaitAction
from winflow.engine.action_registry import ActionRegistry, get_action_registry
from winflow.config.initial_variables import config_variables
from winflow.config.step_aliases import action_params_from_step, canonical_step
from winflow.engine.execution_context import ExecutionContext
from winflow.engine.verification_registry import (
    VerificationRegistry,
    get_verification_registry,
)
from winflow.storage.execution_history import BaseExecutionHistory


class WorkflowRunner:
    """Orchestrates step-by-step execution of a workflow specification."""

    def __init__(
        self,
        config: dict[str, Any],
        action_registry: ActionRegistry | None = None,
        verification_registry: VerificationRegistry | None = None,
        desktop_lock: DesktopExecutionLock | None = None,
        emergency_stop: EmergencyStop | None = None,
        history_storage: BaseExecutionHistory | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        """Initialize WorkflowRunner.

        Args:
            config: Validated workflow configuration dictionary.
            action_registry: Registry containing action implementations.
            verification_registry: Registry containing verification implementations.
            desktop_lock: Global desktop execution lock guarding physical input.
            emergency_stop: EmergencyStop cancellation mechanism.
            history_storage: Execution history persistence adapter.
            logger: Central logger.
        """
        self.config: dict[str, Any] = config
        self.action_registry: ActionRegistry = (
            action_registry if action_registry is not None else get_action_registry()
        )
        self.verification_registry: VerificationRegistry = (
            verification_registry
            if verification_registry is not None
            else get_verification_registry()
        )
        self.desktop_lock: DesktopExecutionLock = (
            desktop_lock if desktop_lock is not None else get_desktop_lock()
        )
        self.emergency_stop: EmergencyStop = (
            emergency_stop if emergency_stop is not None else get_emergency_stop()
        )

        self.history_storage: BaseExecutionHistory | None = history_storage
        self.logger: logging.Logger = logger or get_logger()
        self.failure_handler: FailureHandler = FailureHandler(logger=self.logger)

        if self.history_storage:
            try:
                from winflow.core.execution_trace import get_event_bus
                get_event_bus().subscribe(self.history_storage.record_trace_event)
            except Exception:
                pass

    def _resolve_failure_and_retry_config(
        self,
        step_def: dict[str, Any],
    ) -> tuple[FailureConfig, RetryConfig]:
        """Extract and harmonize failure policy and retry parameters."""
        settings = self.config.get("settings", {})
        failure_raw = step_def.get("failure") or settings.get("failure") or {}
        retry_raw = step_def.get("retry") or settings.get("retry") or {}

        # settings.retry_attempts is the workflow-level fallback for retries: the
        # number of retries after the first attempt. It is used only when the step
        # (or settings.retry/failure) does not declare its own attempts.
        workflow_retries = settings.get("retry_attempts")
        if workflow_retries is not None:
            if isinstance(workflow_retries, bool) or not isinstance(workflow_retries, int) or workflow_retries < 0:
                raise WorkflowExecutionError(
                    "'settings.retry_attempts' must be a non-negative integer."
                )

        policy = str(failure_raw.get("policy", "")).lower()
        if not policy and (retry_raw or workflow_retries):
            policy = FailurePolicy.RETRY

        if not policy:
            policy = FailurePolicy.STOP

        if "attempts" in retry_raw:
            attempts = int(retry_raw["attempts"])
        elif "attempts" in failure_raw:
            attempts = int(failure_raw["attempts"])
        elif workflow_retries is not None:
            attempts = workflow_retries + 1
        else:
            attempts = 1
        delay_seconds = float(
            retry_raw.get(
                "delay_seconds",
                failure_raw.get("delay_seconds", settings.get("retry_delay_seconds", 0.0)),
            )
        )
        max_restarts = int(
            failure_raw.get("max_restarts", settings.get("max_restarts", 3))
        )

        failure_cfg = FailureConfig(
            policy=policy,
            attempts=attempts,
            delay_seconds=delay_seconds,
            max_restarts=max_restarts,
        )
        retry_cfg = RetryConfig(
            attempts=attempts if policy == FailurePolicy.RETRY else 1,
            delay_seconds=delay_seconds,
        )
        return failure_cfg, retry_cfg

    def run(self, context: ExecutionContext | None = None) -> ExecutionContext:
        """Execute the workflow sequence."""
        workflow_id = self.config.get("workflow", {}).get("id", "unknown_workflow")
        config_defaults = config_variables(self.config)
        if context is None:
            ctx = ExecutionContext(
                workflow_id=workflow_id,
                emergency_stop=self.emergency_stop,
                variables=config_defaults,
            )
        else:
            ctx = context
            # Caller-supplied values take precedence; config fills only missing names.
            for name, value in config_defaults.items():
                ctx.variables.setdefault(name, value)

        wf = self.config.get("workflow", {})
        settings = self.config.get("settings", {})
        if not isinstance(wf, dict) or not isinstance(settings, dict):
            raise WorkflowExecutionError("'workflow' and 'settings' must be objects.")
        for path, block in (("workflow.enabled", wf), ("settings.enabled", settings)):
            if "enabled" in block and not isinstance(block["enabled"], bool):
                raise WorkflowExecutionError(f"'{path}' must be a boolean.")
        if not wf.get("enabled", True) or not settings.get("enabled", True):
            raise WorkflowExecutionError(f"Workflow '{workflow_id}' is disabled.")

        steps = wf.get("steps", self.config.get("steps", []))
        repeat_count, repeat_delay, has_repeat_config = self._workflow_repeat_config(
            self.config, wf
        )
        use_desktop_lock = bool(self.config.get("settings", {}).get("use_desktop_lock", True))
        lock_timeout = float(self.config.get("settings", {}).get("desktop_lock_timeout", 10.0))
        lock_acquired = False
        if use_desktop_lock:
            self.desktop_lock.acquire(owner=ctx.execution_id, timeout=lock_timeout)
            lock_acquired = True

        try:
            # Include history/log initialization in the protected region so a
            # startup failure cannot strand the desktop lock.
            if self.history_storage:
                self.history_storage.record_start(
                    execution_id=ctx.execution_id,
                    workflow_id=workflow_id,
                    start_time=ctx.start_time,
                )

            log_step_event(
                self.logger, execution_id=ctx.execution_id,
                workflow_id=workflow_id, step=0,
                action="workflow_start", status="STARTED",
            )

            previous_iteration = ctx.loop_iteration
            try:
                for iteration in range(repeat_count):
                    ctx.check_cancellation()
                    if has_repeat_config:
                        ctx.loop_iteration = iteration
                    while True:
                        ctx.check_cancellation()
                        try:
                            self._execute_steps(steps, ctx, workflow_id, depth=0)
                            break
                        except WorkflowRestartRequested:
                            cleanup_errors = ctx.run_cleanups()
                            if cleanup_errors:
                                raise ActionExecutionError(
                                    "Keyboard cleanup failed: " + "; ".join(cleanup_errors)
                                )
                            self.logger.info("Restarting workflow execution from beginning...")
                    if repeat_delay and iteration + 1 < repeat_count:
                        WaitAction(params={"seconds": repeat_delay}).execute(ctx)
            finally:
                if has_repeat_config:
                    ctx.loop_iteration = previous_iteration

            cleanup_errors = ctx.run_cleanups()
            if cleanup_errors:
                raise ActionExecutionError(
                    "Keyboard cleanup failed: " + "; ".join(cleanup_errors)
                )

            if self.history_storage:
                self.history_storage.record_complete(
                    execution_id=ctx.execution_id,
                    status="COMPLETED",
                )

            log_step_event(
                self.logger, execution_id=ctx.execution_id,
                workflow_id=workflow_id, step=ctx.current_step,
                action="workflow_finish", status="COMPLETED",
            )
            return ctx

        except EmergencyStopTriggered as exc:
            if self.history_storage:
                self.history_storage.record_complete(execution_id=ctx.execution_id, status="CANCELLED", error=str(exc))
            log_step_event(
                self.logger, execution_id=ctx.execution_id, workflow_id=workflow_id,
                step=ctx.current_step, action="workflow_emergency_stop",
                status="CANCELLED", error=str(exc),
            )
            raise

        except Exception as exc:
            error_msg = str(exc)
            if self.history_storage:
                self.history_storage.record_complete(execution_id=ctx.execution_id, status="FAILED", error=error_msg)
            log_step_event(
                self.logger, execution_id=ctx.execution_id, workflow_id=workflow_id,
                step=ctx.current_step, action="workflow_error",
                status="FAILED", error=error_msg,
            )
            if isinstance(exc, (ActionNotFoundError, ActionExecutionError, VerificationNotFoundError, VerificationFailedError)):
                raise
            raise WorkflowExecutionError(f"Workflow execution failed: {exc}") from exc

        finally:
            execution_failed = sys.exc_info()[0] is not None
            cleanup_errors = ctx.run_cleanups()
            if lock_acquired and self.desktop_lock.current_owner == ctx.execution_id:
                self.desktop_lock.release(owner=ctx.execution_id)
            if cleanup_errors:
                message = "Keyboard cleanup failed: " + "; ".join(cleanup_errors)
                if execution_failed:
                    self.logger.error(message)
                else:
                    raise ActionExecutionError(message)

    def _execute_steps(self, steps: list, ctx: ExecutionContext, workflow_id: str, depth: int = 0) -> None:
        """Recursively execute a list of steps, handling control flow."""
        max_depth = int(self.config.get("settings", {}).get("max_control_depth", 10))
        if depth > max_depth:
            raise WorkflowExecutionError(f"Exceeded max control depth of {max_depth}")
        if not isinstance(steps, list):
            raise WorkflowExecutionError("Control-flow steps must be a list.")

        for step_def in steps:
            ctx.check_cancellation()
            ctx.update_step(ctx.current_step + 1)
            step_index = ctx.current_step
            if not isinstance(step_def, dict):
                raise WorkflowExecutionError(
                    f"Step {step_index} must be an object, got {type(step_def).__name__}."
                )
            action_name = step_def.get("action", "")
            if not isinstance(action_name, str) or not action_name.strip():
                raise WorkflowExecutionError(
                    f"Step {step_index} must have a non-empty action name."
                )
            try:
                step_def = canonical_step(step_def, path=f"Step {step_index}")
            except ConfigurationError as exc:
                raise WorkflowExecutionError(str(exc)) from exc
            ctx.current_action = action_name

            # Control structures
            if action_name == "if":
                condition = step_def.get("condition", {})
                then_steps = step_def.get("then", [])
                else_steps = step_def.get("else", [])
                if not isinstance(then_steps, list) or not isinstance(else_steps, list):
                    raise WorkflowExecutionError(
                        f"Conditional step {step_index} branches must be lists."
                    )
                if self._evaluate_condition(condition, ctx, step_index):
                    self._execute_steps(then_steps, ctx, workflow_id, depth + 1)
                else:
                    self._execute_steps(else_steps, ctx, workflow_id, depth + 1)
                continue

            elif action_name == "while":
                condition = step_def.get("condition", {})
                max_iter = self._positive_integer(
                    step_def.get("max_iterations", 1000),
                    field=f"while step {step_index} max_iterations",
                )
                body = step_def.get("do", step_def.get("steps", []))
                if not isinstance(body, list):
                    raise WorkflowExecutionError(
                        f"While step {step_index} body must be a list."
                    )
                previous_iteration = ctx.loop_iteration
                try:
                    iter_count = 0
                    while self._evaluate_condition(condition, ctx, step_index):
                        ctx.check_cancellation()
                        if iter_count >= max_iter:
                            raise WorkflowExecutionError(
                                f"While loop at step {step_index} exceeded max_iterations ({max_iter})."
                            )
                        ctx.loop_iteration = iter_count
                        self._execute_steps(body, ctx, workflow_id, depth + 1)
                        iter_count += 1
                finally:
                    ctx.loop_iteration = previous_iteration
                continue

            elif action_name == "for_each":
                condition = step_def.get("condition")
                if condition is not None and not self._evaluate_condition(
                    condition, ctx, step_index
                ):
                    continue
                items_raw = step_def.get("items", [])
                items = VariableResolver.resolve(items_raw, ctx)
                if not isinstance(items, list):
                    raise WorkflowExecutionError(
                        f"for_each items at step {step_index} must resolve to a list, "
                        f"got {type(items).__name__}."
                    )

                max_items = self._positive_integer(
                    step_def.get("max_items", 10000),
                    field=f"for_each step {step_index} max_items",
                )
                if len(items) > max_items:
                    raise WorkflowExecutionError(
                        f"for_each items at step {step_index} exceed max_items ({max_items})."
                    )
                body = step_def.get("do", step_def.get("steps", []))
                if not isinstance(body, list):
                    raise WorkflowExecutionError(
                        f"for_each step {step_index} body must be a list."
                    )

                had_index = "_loop_index" in ctx.variables
                prev_index = ctx.variables.get("_loop_index")
                had_item = "_loop_item" in ctx.variables
                prev_item = ctx.variables.get("_loop_item")
                prev_iter = ctx.loop_iteration

                try:
                    for idx, item in enumerate(items):
                        ctx.check_cancellation()
                        ctx.loop_iteration = idx
                        ctx.set_variable("_loop_index", idx)
                        ctx.set_variable("_loop_item", item)
                        self._execute_steps(body, ctx, workflow_id, depth + 1)
                finally:
                    ctx.loop_iteration = prev_iter
                    if had_index:
                        ctx.set_variable("_loop_index", prev_index)
                    else:
                        ctx.variables.pop("_loop_index", None)
                    if had_item:
                        ctx.set_variable("_loop_item", prev_item)
                    else:
                        ctx.variables.pop("_loop_item", None)
                continue

            condition = step_def.get("condition")
            if condition is not None and not self._evaluate_condition(
                condition, ctx, step_index
            ):
                continue

            # Standard actions
            # Explicit params win; other top-level keys are legacy params unless
            # they are structural or metadata keys (name, step_id, label, ...).
            raw_params = action_params_from_step(step_def)

            # Variable interpolation
            params = VariableResolver.resolve(raw_params, ctx)
            verify_def_raw = step_def.get("verify")
            verify_def = VariableResolver.resolve(verify_def_raw, ctx) if verify_def_raw else None
            
            settings = self.config.get("settings", {})
            default_timeout = settings.get(
                "timeout_seconds",
                settings.get("global_timeout_seconds", 0.0),
            )
            try:
                timeout_seconds = float(step_def.get("timeout_seconds", default_timeout))
            except (TypeError, ValueError, OverflowError) as exc:
                raise WorkflowExecutionError(
                    f"Timeout for action '{action_name}' must be a finite non-negative number."
                ) from exc
            if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
                raise WorkflowExecutionError(
                    f"Timeout for action '{action_name}' must be a finite non-negative number."
                )

            failure_cfg, retry_cfg = self._resolve_failure_and_retry_config(step_def)

            if self.history_storage:
                self.history_storage.record_update(
                    execution_id=ctx.execution_id,
                    current_step=step_index,
                    status="RUNNING",
                )

            log_step_event(
                self.logger,
                execution_id=ctx.execution_id,
                workflow_id=workflow_id,
                step=step_index,
                action=action_name,
                status="RUNNING",
            )

            action_cls = self.action_registry.get(action_name)
            action_instance = action_cls(name=action_name, params=params)

            ver_instance = None
            ver_type = None
            ver_timeout = 0.0
            ver_poll = 0.2
            ver_params: dict[str, Any] = {}

            if verify_def and isinstance(verify_def, dict):
                ver_type = verify_def.get("type", "")
                ver_cls = self.verification_registry.get(ver_type)
                ver_params = verify_def.get("target") or verify_def.get("params") or {}
                for k in ("title_contains", "title_exact", "process_name", "executable_name", "hwnd"):
                    if k in verify_def and k not in ver_params:
                        ver_params[k] = verify_def[k]

                ver_timeout = float(verify_def.get("timeout_seconds", 0.0))
                ver_poll = float(verify_def.get("poll_interval_seconds", 0.2))
                ver_instance = ver_cls(name=ver_type, params=ver_params)

            def execute_logical_unit(attempt: int) -> None:
                ctx.check_cancellation()
                
                is_dry_run = getattr(self, "dry_run", False) or bool(self.config.get("settings", {}).get("dry_run", False))
                if is_dry_run:
                    self.logger.info(f"DRY RUN: Would execute {action_name} with params {params}")
                    import time
                    start_t = time.monotonic()
                    # Preserve control-flow accuracy without touching the desktop.
                    if action_name in {"set_variable", "wait"}:
                        action_instance.execute(ctx, **params)
                    else:
                        time.sleep(0.01)
                    elapsed = (time.monotonic() - start_t) * 1000
                    log_step_event(
                        self.logger, execution_id=ctx.execution_id, workflow_id=workflow_id,
                        step=step_index, action=action_name, status="SIMULATED", extra={"duration_ms": elapsed}
                    )
                else:
                    import time
                    start_t = time.monotonic()
                    execute_with_timeout(
                        operation=lambda: action_instance.execute(ctx, **params),
                        timeout_seconds=timeout_seconds,
                        context=ctx,
                        operation_name=action_name,
                    )
                    elapsed = (time.monotonic() - start_t) * 1000
                    log_step_event(
                        self.logger, execution_id=ctx.execution_id, workflow_id=workflow_id,
                        step=step_index, action=action_name, status="ACTION_COMPLETED", extra={"duration_ms": elapsed}
                    )
                
                ctx.check_cancellation()

                if ver_instance is not None:
                    self.logger.info(
                        "Running verification '%s' for step %d (attempt %d)...",
                        ver_type,
                        step_index,
                        attempt,
                    )
                    import time
                    start_ver_t = time.monotonic()
                    if is_dry_run:
                        self.logger.info(f"DRY RUN: Would verify {ver_type} with params {ver_params}")
                        ver_res = type('MockVerRes', (), {'success': True, 'error': None})()
                    else:
                        ver_res = ver_instance.poll_verify(
                            context=ctx,
                            timeout_seconds=ver_timeout,
                            poll_interval_seconds=ver_poll,
                            **ver_params,
                        )
                    elapsed_ver = (time.monotonic() - start_ver_t) * 1000
                    log_step_event(
                        self.logger, execution_id=ctx.execution_id, workflow_id=workflow_id,
                        step=step_index, action=f"verify_{ver_type}", status="VERIFICATION_COMPLETED", extra={"duration_ms": elapsed_ver}
                    )
                    if not ver_res.success:
                        err_msg = ver_res.error or f"Verification '{ver_type}' failed."
                        raise VerificationFailedError(err_msg)
                    self.logger.info("Verification '%s' passed for step %d.", ver_type, step_index)

            try:
                if failure_cfg.policy == FailurePolicy.RETRY and retry_cfg.attempts > 1:
                    retry_executor = RetryExecutor(
                        config=retry_cfg,
                        context=ctx,
                        logger=self.logger,
                    )
                    retry_executor.execute(
                        operation=execute_logical_unit,
                        operation_name=f"{action_name} [step {step_index}]",
                    )
                else:
                    execute_logical_unit(1)

                ctx.check_cancellation()
                log_step_event(
                    self.logger,
                    execution_id=ctx.execution_id,
                    workflow_id=workflow_id,
                    step=step_index,
                    action=action_name,
                    status="SUCCESS",
                )

            except Exception as step_exc:
                ctx.check_cancellation()
                
                try:
                    from winflow.core.diagnostics import get_diagnostics
                    get_diagnostics().generate_snapshot(
                        context=ctx,
                        error=step_exc,
                        step=step_index,
                        action=action_name,
                        verification=ver_type if ver_instance else None,
                        capture_screenshot=bool(self.config.get("settings", {}).get("screenshot_on_failure", False))
                    )
                except Exception:
                    pass

                decision = self.failure_handler.handle_failure(
                    error=step_exc,
                    failure_config=failure_cfg,
                    context=ctx,
                    step_id=step_index,
                )

                if decision.action == FailureAction.SKIP:
                    log_step_event(
                        self.logger, execution_id=ctx.execution_id,
                        workflow_id=workflow_id, step=step_index,
                        action=action_name, status="SKIPPED", error=decision.message,
                    )
                    continue

                if decision.action == FailureAction.CONTINUE:
                    log_step_event(
                        self.logger, execution_id=ctx.execution_id,
                        workflow_id=workflow_id, step=step_index,
                        action=action_name, status="CONTINUED", error=decision.message,
                    )
                    continue

                if decision.action == FailureAction.RESTART:
                    log_step_event(
                        self.logger, execution_id=ctx.execution_id,
                        workflow_id=workflow_id, step=step_index,
                        action=action_name, status="RESTARTING", error=decision.message,
                    )
                    raise WorkflowRestartRequested(decision.message)

                raise step_exc

    @staticmethod
    def _positive_integer(value: Any, *, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise WorkflowExecutionError(f"{field} must be a positive integer.")
        try:
            parsed = int(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise WorkflowExecutionError(
                f"{field} must be a positive integer."
            ) from exc
        if parsed < 1:
            raise WorkflowExecutionError(f"{field} must be a positive integer.")
        return parsed

    @classmethod
    def _workflow_repeat_config(
        cls,
        config: dict[str, Any],
        workflow: dict[str, Any],
    ) -> tuple[int, float, bool]:
        if "loop" in config:
            loop = config["loop"]
        elif "loop" in workflow:
            loop = workflow["loop"]
        else:
            return 1, 0.0, False
        if not isinstance(loop, dict):
            raise WorkflowExecutionError("Workflow loop configuration must be an object.")

        raw_count = loop.get("count", 1)
        if isinstance(raw_count, str) and raw_count.strip().lower() == "infinite":
            raise WorkflowExecutionError(
                "Infinite workflow loops are not supported; configure a finite loop.count."
            )
        count = cls._positive_integer(raw_count, field="workflow loop.count")

        raw_delay = loop.get("delay_between_seconds", 0.0)
        if isinstance(raw_delay, bool) or not isinstance(raw_delay, (int, float)):
            raise WorkflowExecutionError(
                "workflow loop.delay_between_seconds must be a finite non-negative number."
            )
        try:
            delay = float(raw_delay)
        except (OverflowError, ValueError) as exc:
            raise WorkflowExecutionError(
                "workflow loop.delay_between_seconds must be a finite non-negative number."
            ) from exc
        if not math.isfinite(delay) or delay < 0:
            raise WorkflowExecutionError(
                "workflow loop.delay_between_seconds must be a finite non-negative number."
            )
        return count, delay, True

    @staticmethod
    def _evaluate_condition(
        condition: Any,
        context: ExecutionContext,
        step_index: int,
    ) -> bool:
        if condition is None:
            condition = {}
        if not isinstance(condition, dict):
            raise WorkflowExecutionError(
                f"Condition at step {step_index} must be an object."
            )
        try:
            return ConditionEvaluator.evaluate(condition, context)
        except (AttributeError, TypeError, ValueError, KeyError, OverflowError) as exc:
            raise WorkflowExecutionError(
                f"Invalid condition at step {step_index}: {exc}"
            ) from exc
