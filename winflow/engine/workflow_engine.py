"""Workflow engine subsystem for WinFlow.

Provides the central orchestration engine coordinating configuration loading,
registries, emergency-stop handling, execution history, desktop locking,
event triggers, and workflow runs.
"""

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from winflow.config.loader import load_and_validate_config
from winflow.core.emergency_stop import EmergencyStop, get_emergency_stop
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
        self._lock = threading.Lock()

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
    ) -> ExecutionContext:
        """Execute a workflow through the shared runner path used by triggers and manual runs."""
        workflow = workflow_config.get("workflow", {})
        workflow_id = workflow.get("id", "unknown_workflow")
        runner = self.create_runner(workflow_config)
        context = ExecutionContext(
            workflow_id=workflow_id,
            emergency_stop=self.emergency_stop,
        )
        if isinstance(event_data, dict):
            context.trigger_event = dict(event_data)
        context.variables["manual_run"] = bool(manual)

        self.status_manager.register_execution(context)
        try:
            finished_ctx = runner.run(context=context)
            if on_completed is not None:
                on_completed(finished_ctx)
            return finished_ctx
        except Exception:
            raise
        finally:
            self.status_manager.unregister_execution(context.execution_id)

    def start_workflow_listener(
        self,
        workflow_config: dict[str, Any],
        on_completed: Callable[[ExecutionContext], None] | None = None,
    ) -> BaseTrigger | None:
        """Start listening for events configured in the workflow definition.

        Args:
            workflow_config: Validated workflow configuration dictionary.
            on_completed: Optional callback invoked when triggered workflow finishes.

        Returns:
            The running BaseTrigger instance, or None if no trigger is defined.
        """
        workflow = workflow_config.get("workflow", {})
        workflow_id = workflow.get("id", "unknown_workflow")
        trigger_cfg = (
            workflow_config.get("trigger")
            or workflow.get("trigger")
        )

        if not trigger_cfg or not isinstance(trigger_cfg, dict):
            self.logger.info("No trigger configuration defined for workflow '%s'.", workflow_id)
            return None

        trigger_cfg = dict(trigger_cfg)
        trigger_cfg["workflow_id"] = workflow_id
        trigger_cfg["workflow_name"] = workflow.get("name") or workflow_id
        trigger_cfg["workflow_enabled"] = bool(workflow.get("enabled", True))

        trigger_type = str(trigger_cfg.get("type", "")).strip()
        if not trigger_type or trigger_type.lower() == "manual":
            self.logger.info(
                "Workflow '%s' has manual trigger type. Listener not started.",
                workflow_id,
            )
            return None

        if not bool(trigger_cfg.get("enabled", True)) or not bool(trigger_cfg.get("workflow_enabled", True)):
            self.logger.info(
                "Workflow '%s' or trigger '%s' is disabled. Listener not started.",
                workflow_id,
                trigger_type,
            )
            return None

        normalized_type = trigger_type.lower()
        trigger_cls = None
        for candidate_name in self.trigger_registry.list_triggers():
            if candidate_name.lower() == normalized_type:
                trigger_cls = self.trigger_registry.get(candidate_name)
                break
        if trigger_cls is None:
            self.logger.warning("Unsupported trigger type '%s' for workflow '%s'.", trigger_type, workflow_id)
            return None

        trigger_name = f"{workflow_id}_{normalized_type}"
        with self._lock:
            existing = self.active_triggers.get(trigger_name)
            if existing is not None:
                try:
                    existing.stop()
                except Exception:
                    pass
                self.active_triggers.pop(trigger_name, None)

        trigger = trigger_cls(
            name=trigger_name,
            config=trigger_cfg,
        )

        def _on_trigger_event(event_data: dict[str, Any]) -> None:
            """Handler invoked when trigger detects matching event."""
            self.logger.info(
                "Trigger '%s' activated. Initiating workflow '%s'...",
                trigger_name,
                workflow_id,
            )

            def _run_worker() -> None:
                if hasattr(trigger, "set_workflow_executing"):
                    trigger.set_workflow_executing(True)
                try:
                    self.run_workflow(
                        workflow_config,
                        event_data=event_data,
                        on_completed=on_completed,
                    )
                except Exception as exc:
                    self.logger.error(
                        "Triggered workflow '%s' failed: %s",
                        workflow_id,
                        exc,
                    )
                finally:
                    if hasattr(trigger, "set_workflow_executing"):
                        trigger.set_workflow_executing(False)

            worker_thread = threading.Thread(
                target=_run_worker,
                name=f"WinFlowTriggerWorker_{workflow_id}",
                daemon=True,
            )
            worker_thread.start()

        trigger.start(callback=_on_trigger_event)
        with self._lock:
            self.active_triggers[trigger_name] = trigger

        self.logger.info(
            "Workflow listener started for '%s' using trigger '%s'.",
            workflow_id,
            trigger_type,
        )
        return trigger

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
        
        self.status_manager.register_execution(context)
        try:
            return runner.run(context=context)
        finally:
            self.status_manager.unregister_execution(context.execution_id)
            
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
        """Stop a specific active workflow listener by name.

        Args:
            trigger_name: Identifier of the trigger to stop.
        """
        with self._lock:
            trigger = self.active_triggers.pop(trigger_name, None)

        if trigger:
            trigger.stop()
            self.logger.info("Stopped workflow listener '%s'.", trigger_name)

    def stop_all_listeners(self) -> None:
        """Stop all active workflow trigger listeners."""
        with self._lock:
            triggers_to_stop = list(self.active_triggers.values())
            self.active_triggers.clear()

        for trig in triggers_to_stop:
            try:
                trig.stop()
            except Exception as exc:
                self.logger.warning("Error stopping trigger '%s': %s", trig.name, exc)

        self.dispatcher.stop()
        self.logger.info("All workflow trigger listeners stopped.")

    def shutdown(self) -> None:
        """Gracefully shut down the engine and clean up resources."""
        self.logger.info("Shutting down WinFlow WorkflowEngine...")
        self.stop_all_listeners()
        self._is_initialized = False
