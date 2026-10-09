"""Workflow engine subsystem for WinFlow.

Provides the central orchestration engine coordinating configuration loading,
registries, emergency-stop handling, execution history, desktop locking,
event triggers, and workflow runs.
"""

import logging
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from winflow.config.loader import load_and_validate_config
from winflow.core.emergency_hotkey import EmergencyStopHotkey, parse_emergency_hotkey
from winflow.core.emergency_stop import EmergencyStop, get_emergency_stop
from winflow.core.errors import ConfigurationError, WorkflowExecutionError
from winflow.core.execution_lock import DesktopExecutionLock, get_desktop_lock
from winflow.core.logger import get_logger, setup_logger
from winflow.engine.action_registry import ActionRegistry, get_action_registry
from winflow.engine.execution_context import ExecutionContext
from winflow.engine.trigger_registry import TriggerRegistry, get_trigger_registry
from winflow.engine.verification_registry import (
    VerificationRegistry,
    get_verification_registry,
)
from winflow.engine.workflow_runner import WorkflowRunner
from winflow.storage.execution_history import (
    BaseExecutionHistory,
    SQLiteExecutionHistory,
)
from winflow.triggers.base import BaseTrigger
from winflow.triggers.dispatcher import EventDispatcher


@dataclass
class _TriggerExecutionState:
    """Engine-owned atomic execution gate and pending events for one listener."""

    name: str
    trigger: BaseTrigger
    workflow_config: dict[str, Any]
    on_completed: Callable[[ExecutionContext], None] | None
    policy: str
    running: bool = False
    context: ExecutionContext | None = None
    worker: threading.Thread | None = None
    queued_events: deque[dict[str, Any]] = field(default_factory=deque)
    restart_event: dict[str, Any] | None = None
    stopping: bool = False


class WorkflowEngine:
    """Core orchestration engine for WinFlow."""

    def __init__(
        self,
        action_registry: ActionRegistry | None = None,
        trigger_registry: TriggerRegistry | None = None,
        verification_registry: VerificationRegistry | None = None,
        desktop_lock: DesktopExecutionLock | None = None,
        emergency_stop: EmergencyStop | None = None,
        history_storage: BaseExecutionHistory | None = None,
        dispatcher: EventDispatcher | None = None,
        logger: logging.Logger | None = None,
        emergency_hotkey_factory: Callable[[str, EmergencyStop, logging.Logger], Any] | None = None,
    ) -> None:
        """Initialize the workflow engine.

        Args:
            action_registry: Custom ActionRegistry or defaults to global registry.
            trigger_registry: Custom TriggerRegistry or defaults to global registry.
            verification_registry: Custom VerificationRegistry or defaults to global registry.
            desktop_lock: Custom DesktopExecutionLock or defaults to global singleton.
            emergency_stop: Custom EmergencyStop or defaults to global singleton.
            history_storage: Persistence history backend (defaults to SQLite).
            dispatcher: Custom EventDispatcher or creates a new instance.
            logger: Custom logger or defaults to central winflow logger.
            emergency_hotkey_factory: Optional factory for a runtime emergency-stop
                hotkey listener. Intended for platform-independent tests.
        """
        self.action_registry: ActionRegistry = (
            action_registry if action_registry is not None else get_action_registry()
        )
        self.trigger_registry: TriggerRegistry = (
            trigger_registry if trigger_registry is not None else get_trigger_registry()
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
        self.history_storage: BaseExecutionHistory = (
            history_storage if history_storage is not None else SQLiteExecutionHistory("winflow_history.sqlite")
        )
        self.logger: logging.Logger = logger or get_logger()
        self.dispatcher: EventDispatcher = (
            dispatcher if dispatcher is not None else EventDispatcher(logger=self.logger)
        )
        self.active_triggers: dict[str, BaseTrigger] = {}

        from winflow.engine.status import EngineStatusManager
        self.status_manager = EngineStatusManager(self.history_storage)

        self._is_initialized: bool = False
        self._lock = threading.RLock()
        self._worker_condition = threading.Condition(self._lock)
        self._active_workers: set[threading.Thread] = set()
        self._active_contexts: dict[str, tuple[ExecutionContext, threading.Thread]] = {}
        self._trigger_executions: dict[str, _TriggerExecutionState] = {}
        self._is_shutting_down = False
        self._hotkey_lock = threading.Lock()
        self._emergency_hotkey_factory = emergency_hotkey_factory or (
            lambda hotkey, stop, logger: EmergencyStopHotkey(hotkey, stop, logger)
        )
        self._emergency_hotkey_listener: Any | None = None
        self._emergency_hotkey_spec: str | None = None

    def get_status(self) -> dict[str, Any]:
        status = self.status_manager.get_status()
        status["desktop_locked"] = self.desktop_lock.is_locked
        status["emergency_stop_active"] = self.emergency_stop.is_triggered()
        return status
        
    def get_current_execution(self, execution_id: str) -> dict[str, Any] | None:
        return self.status_manager.get_current_execution(execution_id)
        
    def get_execution_history(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.status_manager.get_execution_history(limit)
        
    def get_recent_events(self, execution_id: str) -> list[dict[str, Any]]:
        return self.status_manager.get_recent_events(execution_id)
        
    def get_diagnostics(self, execution_id: str) -> dict[str, Any] | None:
        return self.status_manager.get_diagnostics(execution_id)

    def initialize(
        self,
        log_level: str = "INFO",
        log_file: str | Path | None = None,
    ) -> None:
        """Initialize the engine subsystems and logging.

        Args:
            log_level: Logging severity level.
            log_file: Optional file destination for logs.
        """
        self.logger = setup_logger(level=log_level, log_file=log_file)
        self.logger.info("Initializing WinFlow WorkflowEngine...")
        self._is_initialized = True

    def prepare_environment(self) -> None:
        """Prepare and verify the execution environment.

        Ensures registries are accessible and populated, emergency stop is reset,
        and storage is operational.
        """
        if not self._is_initialized:
            self.initialize()

        self.logger.info("Preparing execution environment...")
        self.emergency_stop.reset()

        from winflow.actions import register_desktop_actions
        register_desktop_actions(self.action_registry)

        from winflow.verification import register_desktop_verifications
        register_desktop_verifications(self.verification_registry)

        from winflow.triggers import register_desktop_triggers
        register_desktop_triggers(self.trigger_registry)

        self.logger.info(
            "Environment prepared. Registered actions: %d, triggers: %d, verifications: %d.",
            len(self.action_registry.list_actions()),
            len(self.trigger_registry.list_triggers()),
            len(self.verification_registry.list_verifications()),
        )

    def load_workflow(self, config_path: str | Path) -> dict[str, Any]:
        """Load and validate a workflow configuration from disk.

        Args:
            config_path: Path to workflow file.

        Returns:
            Validated configuration dictionary.
        """
        self.logger.info("Loading workflow configuration from: %s", config_path)
        config = load_and_validate_config(config_path)
        self.logger.info(
            "Workflow '%s' loaded and validated successfully.",
            config.get("workflow", {}).get("id"),
        )
        return config

    def create_runner(self, workflow_config: dict[str, Any]) -> WorkflowRunner:
        """Construct a WorkflowRunner for a given workflow configuration.

        Args:
            workflow_config: Validated workflow configuration.

        Returns:
            Configured WorkflowRunner instance.
        """
        return WorkflowRunner(
            config=workflow_config,
            action_registry=self.action_registry,
            verification_registry=self.verification_registry,
            desktop_lock=self.desktop_lock,
            emergency_stop=self.emergency_stop,
            history_storage=self.history_storage,
            logger=self.logger,
        )

    def run_workflow(
        self,
        workflow_config: dict[str, Any],
        event_data: dict[str, Any] | None = None,
        on_completed: Callable[[ExecutionContext], None] | None = None,
        manual: bool = False,
        context: ExecutionContext | None = None,
    ) -> ExecutionContext:
        """Execute a workflow through the shared runner path.

        ``context`` is optional and primarily lets the trigger worker reserve and
        expose its cancellation handle before its thread starts.
        """
        workflow = workflow_config.get("workflow", {})
        workflow_id = workflow.get("id", "unknown_workflow")
        settings = workflow_config.get("settings", {})
        if not self._workflow_is_enabled(workflow_config):
            raise ConfigurationError(f"Workflow '{workflow_id}' is disabled.")
        with self._worker_condition:
            if self._is_shutting_down:
                raise WorkflowExecutionError("Workflow engine is shutting down; new runs are rejected.")
        self._ensure_emergency_hotkey(settings)

        runner = self.create_runner(workflow_config)
        ctx = context or ExecutionContext(
            workflow_id=workflow_id,
            emergency_stop=self.emergency_stop,
        )
        if isinstance(event_data, dict):
            ctx.trigger_event = dict(event_data)
        ctx.variables["manual_run"] = bool(manual)
        current_thread = threading.current_thread()

        with self._worker_condition:
            if self._is_shutting_down:
                raise WorkflowExecutionError("Workflow engine is shutting down; new runs are rejected.")
            self._active_contexts[ctx.execution_id] = (ctx, current_thread)

        try:
            self.status_manager.register_execution(ctx)
            finished_ctx = runner.run(context=ctx)
            if on_completed is not None:
                on_completed(finished_ctx)
            return finished_ctx
        finally:
            try:
                self.status_manager.unregister_execution(ctx.execution_id)
            finally:
                with self._worker_condition:
                    self._active_contexts.pop(ctx.execution_id, None)
                    self._worker_condition.notify_all()

    @staticmethod
    def _workflow_is_enabled(workflow_config: dict[str, Any]) -> bool:
        """Honor both the legacy settings flag and the workflow-level flag."""
        workflow = workflow_config.get("workflow", {})
        settings = workflow_config.get("settings", {})
        if not isinstance(workflow, dict) or not isinstance(settings, dict):
            raise ConfigurationError("'workflow' and 'settings' must be objects.")
        for path, block, key in (
            ("workflow.enabled", workflow, "enabled"),
            ("settings.enabled", settings, "enabled"),
        ):
            if key in block and not isinstance(block[key], bool):
                raise ConfigurationError(f"'{path}' must be a boolean.")
        return workflow.get("enabled", True) and settings.get("enabled", True)

    def _ensure_emergency_hotkey(self, settings: Any) -> None:
        """Start the configured application-wide emergency-stop hotkey once."""
        with self._worker_condition:
            if self._is_shutting_down:
                raise WorkflowExecutionError("Workflow engine is shutting down; hotkey start rejected.")
        if not isinstance(settings, dict):
            raise ConfigurationError("'settings' must be an object.")
        raw_hotkey = settings.get("emergency_stop_hotkey")
        if raw_hotkey is None:
            return
        try:
            parsed = parse_emergency_hotkey(raw_hotkey)
        except ValueError as exc:
            raise ConfigurationError(f"Invalid emergency-stop hotkey: {exc}") from exc

        with self._hotkey_lock:
            if self._emergency_hotkey_listener is not None:
                if parsed.canonical != self._emergency_hotkey_spec:
                    raise ConfigurationError(
                        "One engine cannot use multiple emergency-stop hotkeys; "
                        f"already using '{self._emergency_hotkey_spec}', received '{parsed.canonical}'."
                    )
                return

            listener = self._emergency_hotkey_factory(
                raw_hotkey,
                self.emergency_stop,
                self.logger,
            )
            listener.start()
            self._emergency_hotkey_listener = listener
            self._emergency_hotkey_spec = parsed.canonical

    def start_workflow_listener(
        self,
        workflow_config: dict[str, Any],
        on_completed: Callable[[ExecutionContext], None] | None = None,
    ) -> BaseTrigger | None:
        """Start listening for events configured in the workflow definition."""
        workflow = workflow_config.get("workflow", {})
        workflow_id = workflow.get("id", "unknown_workflow")
        settings = workflow_config.get("settings", {})
        if not self._workflow_is_enabled(workflow_config):
            self.logger.info("Workflow '%s' is disabled; listener not started.", workflow_id)
            return None

        trigger_cfg = workflow_config.get("trigger") or workflow.get("trigger")
        if not trigger_cfg or not isinstance(trigger_cfg, dict):
            self.logger.info("No trigger configuration defined for workflow '%s'.", workflow_id)
            return None

        trigger_cfg = dict(trigger_cfg)
        trigger_cfg["workflow_id"] = workflow_id
        trigger_cfg["workflow_name"] = workflow.get("name") or workflow_id
        trigger_cfg["workflow_enabled"] = self._workflow_is_enabled(workflow_config)

        trigger_type = str(trigger_cfg.get("type", "")).strip()
        if not trigger_type or trigger_type.lower() == "manual":
            self.logger.info(
                "Workflow '%s' has manual trigger type. Listener not started.",
                workflow_id,
            )
            return None
        if not isinstance(trigger_cfg.get("enabled", True), bool):
            raise ConfigurationError("Trigger 'enabled' must be a boolean.")
        if not trigger_cfg.get("enabled", True):
            self.logger.info(
                "Trigger '%s' for workflow '%s' is disabled; listener not started.",
                trigger_type,
                workflow_id,
            )
            return None

        self._ensure_emergency_hotkey(settings)
        normalized_type = trigger_type.lower()
        trigger_cls = next(
            (
                self.trigger_registry.get(candidate_name)
                for candidate_name in self.trigger_registry.list_triggers()
                if candidate_name.lower() == normalized_type
            ),
            None,
        )
        if trigger_cls is None:
            self.logger.warning(
                "Unsupported trigger type '%s' for workflow '%s'.",
                trigger_type,
                workflow_id,
            )
            return None

        trigger_name = f"{workflow_id}_{normalized_type}"
        policy = str(
            trigger_cfg.get("while_running", trigger_cfg.get("while_running_policy", "ignore"))
        ).strip().lower()
        policy = {
            "terminate_and_restart": "queue",
            "run_concurrently": "queue",
        }.get(policy, policy)
        if policy not in {"ignore", "queue", "restart"}:
            raise ConfigurationError(
                "Trigger while_running must be 'ignore', 'queue', or 'restart'."
            )

        trigger = trigger_cls(name=trigger_name, config=trigger_cfg)
        state = _TriggerExecutionState(
            name=trigger_name,
            trigger=trigger,
            workflow_config=workflow_config,
            on_completed=on_completed,
            policy=policy,
        )
        with self._worker_condition:
            if self._is_shutting_down:
                raise WorkflowExecutionError("Workflow engine is shutting down; listener start rejected.")
            existing = self.active_triggers.get(trigger_name)
            if existing is not None:
                self.logger.warning(
                    "Listener '%s' is already active; keeping the existing listener.", trigger_name
                )
                return existing
            # Publish the execution gate before starting the source. Startup
            # triggers may emit synchronously from trigger.start().
            self.active_triggers[trigger_name] = trigger
            self._trigger_executions[trigger_name] = state

        def _on_trigger_event(event_data: dict[str, Any]) -> None:
            self._accept_trigger_event(trigger_name, event_data)

        try:
            trigger.start(callback=_on_trigger_event)
        except Exception:
            with self._worker_condition:
                self.active_triggers.pop(trigger_name, None)
                self._trigger_executions.pop(trigger_name, None)
                self._worker_condition.notify_all()
            raise

        self.logger.info(
            "Workflow listener started for '%s' using trigger '%s'.",
            workflow_id,
            trigger_type,
        )
        return trigger

    def _accept_trigger_event(
        self,
        trigger_name: str,
        event_data: dict[str, Any],
    ) -> None:
        """Atomically apply ignore/queue/restart and reserve a worker before start."""
        if not isinstance(event_data, dict):
            self.logger.warning("Ignoring non-object trigger event for '%s'.", trigger_name)
            return
        payload = dict(event_data)
        with self._worker_condition:
            if self._is_shutting_down:
                return
            state = self._trigger_executions.get(trigger_name)
            if state is None or state.stopping:
                return
            if state.running:
                if state.policy == "ignore":
                    self.logger.info(
                        "Trigger '%s' ignored a duplicate event while its workflow is running.",
                        trigger_name,
                    )
                    return
                if state.policy == "queue":
                    state.queued_events.append(payload)
                    self.logger.info("Queued trigger event for workflow listener '%s'.", trigger_name)
                    return
                # Restart is safe only after the current worker observes
                # cancellation and runs its cleanup callbacks. Latest event wins.
                state.restart_event = payload
                if state.context is not None:
                    state.context.cancel("Workflow restart requested by a new trigger event")
                self.logger.info(
                    "Requested safe restart of workflow listener '%s' after cleanup.",
                    trigger_name,
                )
                return

            self._start_trigger_worker_locked(state, payload)

    def _start_trigger_worker_locked(
        self,
        state: _TriggerExecutionState,
        event_data: dict[str, Any],
    ) -> None:
        """Reserve state and register the worker before it can process events."""
        if self._is_shutting_down or state.stopping:
            return
        workflow = state.workflow_config.get("workflow", {})
        context = ExecutionContext(
            workflow_id=workflow.get("id", "unknown_workflow"),
            emergency_stop=self.emergency_stop,
        )
        context.trigger_event = dict(event_data)
        context.variables["manual_run"] = False
        worker = threading.Thread(
            target=self._run_trigger_worker,
            args=(state, context, dict(event_data)),
            name=f"WinFlowTriggerWorker_{workflow.get('id', 'workflow')}",
            daemon=False,
        )
        state.running = True
        state.context = context
        state.worker = worker
        self._active_workers.add(worker)
        setter = getattr(state.trigger, "set_workflow_executing", None)
        if callable(setter):
            setter(True)
        try:
            worker.start()
        except Exception:
            self._active_workers.discard(worker)
            state.running = False
            state.context = None
            state.worker = None
            if callable(setter):
                setter(False)
            raise

    def _run_trigger_worker(
        self,
        state: _TriggerExecutionState,
        context: ExecutionContext,
        event_data: dict[str, Any],
    ) -> None:
        try:
            self.run_workflow(
                state.workflow_config,
                event_data=event_data,
                on_completed=state.on_completed,
                context=context,
            )
        except Exception as exc:
            self.logger.error(
                "Triggered workflow '%s' failed: %s",
                context.workflow_id,
                exc,
            )
        finally:
            self._complete_trigger_worker(state)

    def _complete_trigger_worker(self, state: _TriggerExecutionState) -> None:
        """Drain queued/restart events without dropping the atomic reservation."""
        current_thread = threading.current_thread()
        with self._worker_condition:
            self._active_workers.discard(current_thread)
            if self._is_shutting_down or state.stopping:
                state.queued_events.clear()
                state.restart_event = None
                state.running = False
                state.context = None
                state.worker = None
                setter = getattr(state.trigger, "set_workflow_executing", None)
                if callable(setter):
                    setter(False)
                if state.stopping:
                    self._trigger_executions.pop(state.name, None)
                self._worker_condition.notify_all()
                return

            next_event = state.restart_event
            if next_event is not None:
                state.restart_event = None
            elif state.queued_events:
                next_event = state.queued_events.popleft()

            if next_event is None:
                state.running = False
                state.context = None
                state.worker = None
                setter = getattr(state.trigger, "set_workflow_executing", None)
                if callable(setter):
                    setter(False)
                if state.stopping:
                    self._trigger_executions.pop(state.name, None)
                self._worker_condition.notify_all()
                return

            # Keep state.running true while replacing the worker, so a rapid
            # event cannot slip through between consecutive queued executions.
            state.running = False
            state.context = None
            state.worker = None
            self._start_trigger_worker_locked(state, next_event)
            self._worker_condition.notify_all()

    def test_single_step(self, step_config: dict[str, Any], variables: dict[str, Any] | None = None, dry_run: bool = False) -> ExecutionContext:
        """Create a temporary runner and execute just this single step safely."""
        workflow_id = "single_step_test"
        mock_wf = {
            "schema_version": "1.0",
            "workflow": {"id": workflow_id, "name": "Step Test", "version": "1.0"},
            "steps": [step_config]
        }
        
        runner = self.create_runner(mock_wf)
        if dry_run:
            runner.dry_run = True
            
        context = ExecutionContext(
            workflow_id=workflow_id,
            emergency_stop=self.emergency_stop,
            variables=variables or {}
        )
        
        current_thread = threading.current_thread()
        with self._worker_condition:
            if self._is_shutting_down:
                raise WorkflowExecutionError("Workflow engine is shutting down; step test rejected.")
            self._active_contexts[context.execution_id] = (context, current_thread)
        try:
            self.status_manager.register_execution(context)
            return runner.run(context=context)
        finally:
            try:
                self.status_manager.unregister_execution(context.execution_id)
            finally:
                with self._worker_condition:
                    self._active_contexts.pop(context.execution_id, None)
                    self._worker_condition.notify_all()
            
    def simulate_trigger_event(self, trigger_name: str, event_data: dict[str, Any]) -> bool:
        """Simulate a notification event flowing into a running trigger."""
        with self._lock:
            trigger = self.active_triggers.get(trigger_name)
            
        if not trigger:
            self.logger.warning("Cannot simulate trigger: '%s' is not active.", trigger_name)
            return False
            
        if not hasattr(trigger, "_handle_notification_event"):
            self.logger.warning("Trigger '%s' does not support simulated notification events.", trigger_name)
            return False
            
        import time
        import uuid

        from winflow.triggers.event import NotificationEvent
        
        event = NotificationEvent(
            event_id=f"sim_{uuid.uuid4().hex[:8]}",
            timestamp=time.time(),
            application_id=event_data.get("application", "SimulatedApp"),
            title=event_data.get("title", "Simulated Title"),
            body=event_data.get("body", "Simulated Body"),
            raw_metadata=event_data,
        )
        self.logger.info("Injecting simulated event into trigger '%s'", trigger_name)
        trigger._handle_notification_event(event)
        return True

    def stop_workflow_listener(self, trigger_name: str) -> None:
        """Stop a specific listener and discard its not-yet-started events."""
        with self._worker_condition:
            trigger = self.active_triggers.pop(trigger_name, None)
            state = self._trigger_executions.get(trigger_name)
            if state is not None:
                state.stopping = True
                state.queued_events.clear()
                state.restart_event = None
                if not state.running:
                    self._trigger_executions.pop(trigger_name, None)

        if trigger is not None:
            trigger.stop()
            self.logger.info("Stopped workflow listener '%s'.", trigger_name)

    def stop_all_listeners(self) -> None:
        """Stop all active workflow trigger listeners without cancelling current runs."""
        with self._worker_condition:
            triggers_to_stop = list(self.active_triggers.values())
            self.active_triggers.clear()
            for state in list(self._trigger_executions.values()):
                state.stopping = True
                state.queued_events.clear()
                state.restart_event = None
                if not state.running:
                    self._trigger_executions.pop(state.name, None)

        for trigger in triggers_to_stop:
            try:
                trigger.stop()
            except Exception as exc:
                self.logger.warning("Error stopping trigger '%s': %s", trigger.name, exc)

        self.dispatcher.stop()
        self.logger.info("All workflow trigger listeners stopped.")

    def shutdown(self) -> None:
        """Cancel active executions, stop listeners, and wait for safe cleanup."""
        current_thread = threading.current_thread()
        with self._worker_condition:
            if self._is_shutting_down:
                return
            self._is_shutting_down = True
            contexts = [context for context, _owner in self._active_contexts.values()]
            workers = list(self._active_workers)
            for state in self._trigger_executions.values():
                state.stopping = True
                state.queued_events.clear()
                state.restart_event = None
                if state.context is not None and state.context not in contexts:
                    contexts.append(state.context)

        self.logger.info("Shutting down WinFlow WorkflowEngine...")
        self.emergency_stop.request_stop("Workflow engine shutdown")
        for context in contexts:
            context.cancel("Workflow engine shutdown")

        self.stop_all_listeners()

        with self._hotkey_lock:
            hotkey_listener = self._emergency_hotkey_listener
            self._emergency_hotkey_listener = None
            self._emergency_hotkey_spec = None
        if hotkey_listener is not None:
            try:
                hotkey_listener.stop()
            except Exception as exc:
                self.logger.warning("Error stopping emergency-stop hotkey: %s", exc)

        # Trigger workers are non-daemon and remain in this set until their
        # runner cleanup has completed. Manual runs are additionally covered by
        # the active-context condition below.
        for worker in workers:
            if worker is not current_thread and worker.ident is not None:
                worker.join()

        with self._worker_condition:
            while any(
                owner is not current_thread
                for _context, owner in self._active_contexts.values()
            ):
                self._worker_condition.wait()
            for name, state in list(self._trigger_executions.items()):
                if not state.running:
                    self._trigger_executions.pop(name, None)
            self._is_initialized = False

        self.logger.info("WinFlow WorkflowEngine shut down cleanly.")
