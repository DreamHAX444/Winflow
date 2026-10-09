"""Regression tests for Phase 2: backend configuration contract integrity.

Covers initial variables, settings.retry_attempts, while_running policy support,
step alias normalization, sample repairs, and retry diagnostics.
"""

import copy
import json
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import Mock

from winflow.actions.base import BaseAction
from winflow.actions.variables import SetVariableAction
from winflow.config.initial_variables import config_variables, initial_variables
from winflow.config.loader import load_and_validate_config
from winflow.config.step_aliases import action_params_from_step, canonical_step
from winflow.config.validator import validate_config
from winflow.core.emergency_stop import EmergencyStop
from winflow.core.errors import (
    ConfigurationError,
    VerificationFailedError,
    SchemaValidationError,
    TriggerConfigurationError,
    WorkflowExecutionError,
)
from winflow.core.failure_policy import FailureAction, FailureConfig, FailureHandler
from winflow.engine.action_registry import ActionRegistry
from winflow.engine.execution_context import ExecutionContext
from winflow.engine.trigger_registry import TriggerRegistry
from winflow.engine.verification_registry import VerificationRegistry
from winflow.engine.workflow_engine import WorkflowEngine
from winflow.engine.workflow_runner import WorkflowRunner

# Imported after the engine: winflow.core.condition has a pre-existing import cycle
# that only resolves when the engine package is loaded first.
from winflow.core.condition import ConditionEvaluator  # noqa: E402
from winflow.storage.execution_history import SQLiteExecutionHistory
from winflow.triggers.base import BaseTrigger
from winflow.triggers.sources.mock import MockNotificationSource
from winflow.triggers.windows_notification import WindowsNotificationTrigger
from winflow.verification.base import BaseVerification

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLES = REPO_ROOT / "winflow" / "workflows"


class _Recorder(BaseAction):
    """Records every invocation: (params received by the action)."""

    calls: list[dict[str, Any]] = []
    behavior: Any = None  # optional callable(context, params) run before recording

    def execute(self, context: ExecutionContext, **kwargs: Any) -> dict[str, Any]:
        context.check_cancellation()
        received = {**self.params, **kwargs}
        type(self).calls.append(received)
        if type(self).behavior is not None:
            type(self).behavior(context, received)
        return {}


class _AlwaysFails(BaseAction):
    count = 0

    def execute(self, context: ExecutionContext, **kwargs: Any) -> dict[str, Any]:
        context.check_cancellation()
        type(self).count += 1
        raise RuntimeError("boom")


class _SleepsUntilCancelled(BaseAction):
    started = threading.Event()

    def execute(self, context: ExecutionContext, **kwargs: Any) -> dict[str, Any]:
        type(self).started.set()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            context.check_cancellation()
            time.sleep(0.005)
        raise AssertionError("timeout did not cancel the action")


class _ProbeVerification(BaseVerification):
    calls: list[dict[str, Any]] = []
    result = True

    def __init__(self, name: str = "", params: dict[str, Any] | None = None, backend: Any = None) -> None:
        # Avoid BaseVerification's backend lookup, which requires Windows.
        self.name = name
        self.params = dict(params or {})
        self.backend = None

    def verify(self, context: ExecutionContext, **kwargs: Any) -> bool:
        type(self).calls.append(dict(kwargs))
        return type(self).result


class _FakeTrigger(BaseTrigger):
    def start(self, callback: Any) -> None:
        self._callback = callback
        self._is_running = True

    def stop(self) -> None:
        self._is_running = False

    def fire(self, event_id: str) -> None:
        self.emit_event({"event_id": event_id})


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        _Recorder.calls = []
        _Recorder.behavior = None
        _AlwaysFails.count = 0
        _ProbeVerification.calls = []
        _ProbeVerification.result = True
        self.emergency_stop = EmergencyStop()
        self.actions = ActionRegistry()
        self.actions.register("record", _Recorder)
        self.actions.register("fails", _AlwaysFails)
        self.actions.register("sleeps", _SleepsUntilCancelled)
        self.verifications = VerificationRegistry()
        self.verifications.register("probe", _ProbeVerification)

    def _runner(self, config: dict[str, Any]) -> WorkflowRunner:
        config.setdefault("settings", {}).setdefault("use_desktop_lock", False)
        return WorkflowRunner(
            config,
            action_registry=self.actions,
            verification_registry=self.verifications,
            emergency_stop=self.emergency_stop,
        )

    def _run(
        self,
        config: dict[str, Any],
        variables: dict[str, Any] | None = None,
        trigger_event: dict[str, Any] | None = None,
    ) -> ExecutionContext:
        context = ExecutionContext(
            workflow_id="phase2",
            emergency_stop=self.emergency_stop,
            variables=variables,
        )
        if trigger_event is not None:
            context.trigger_event = dict(trigger_event)
        return self._runner(config).run(context)

    @staticmethod
    def _config(steps: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
        config: dict[str, Any] = {
            "schema_version": "1.0",
            "workflow": {"id": "phase2", "steps": steps},
            "settings": {"use_desktop_lock": False, "retry_delay_seconds": 0.0},
        }
        config.update(extra)
        return config


# ---------------------------------------------------------------------------
# 1. Initial workflow variables
# ---------------------------------------------------------------------------


class InitialVariablesTests(_Base):
    def test_config_variables_are_available_to_interpolation_and_conditions(self) -> None:
        config = self._config(
            [
                {
                    "action": "record",
                    "params": {"label": "{{ variables.greeting }}", "count": "{{ max_items }}"},
                },
                {
                    "action": "if",
                    "condition": {
                        "type": "ALL",
                        "conditions": [
                            {"operator": "equals", "left": "{{ variables.max_items }}", "right": 3}
                        ],
                    },
                    "then": [{"action": "record", "params": {"branch": "then"}}],
                    "else": [{"action": "record", "params": {"branch": "else"}}],
                },
            ],
            variables={"greeting": "hello", "max_items": 3},
        )
        self._run(config)
        self.assertEqual(_Recorder.calls[0]["label"], "hello")
        self.assertEqual(_Recorder.calls[0]["count"], 3)
        self.assertEqual(_Recorder.calls[1], {"branch": "then"})

    def test_manual_path_seeds_config_variables_into_context(self) -> None:
        engine = WorkflowEngine(
            action_registry=self.actions,
            emergency_stop=self.emergency_stop,
        )
        try:
            config = self._config(
                [{"action": "record", "params": {"seen": "{{ variables.threshold }}"}}],
                variables={"threshold": 7},
            )
            engine.run_workflow(config, manual=True)
            self.assertEqual(_Recorder.calls, [{"seen": 7}])
        finally:
            engine.shutdown()

    def test_trigger_path_seeds_config_variables_and_keeps_event_separate(self) -> None:
        triggers = TriggerRegistry()
        triggers.register("fake", _FakeTrigger)
        engine = WorkflowEngine(
            action_registry=self.actions,
            trigger_registry=triggers,
            emergency_stop=self.emergency_stop,
            history_storage=SQLiteExecutionHistory(":memory:"),
        )
        seen: list[dict[str, Any]] = []

        def behavior(context: ExecutionContext, _params: dict[str, Any]) -> None:
            seen.append(
                {
                    "threshold": context.get_variable("threshold"),
                    "event_in_variables": "event_id" in context.variables,
                    "trigger_event": dict(context.trigger_event or {}),
                    "manual_run": context.get_variable("manual_run"),
                }
            )

        _Recorder.behavior = staticmethod(behavior)
        config = self._config(
            [{"action": "record"}],
            variables={"threshold": 9},
            trigger={"type": "fake", "while_running": "ignore"},
        )
        trigger = engine.start_workflow_listener(config)
        assert isinstance(trigger, _FakeTrigger)
        try:
            trigger.fire("evt-1")
            deadline = time.monotonic() + 2.0
            while not seen and time.monotonic() < deadline:
                time.sleep(0.005)
            while time.monotonic() < deadline:
                state = engine._trigger_executions.get(trigger.name)
                if state is None or not state.running:
                    break
                time.sleep(0.005)
        finally:
            engine.shutdown()
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["threshold"], 9)
        self.assertFalse(seen[0]["event_in_variables"])
        self.assertEqual(seen[0]["trigger_event"], {"event_id": "evt-1"})
        self.assertIs(seen[0]["manual_run"], False)

    def test_explicit_runtime_override_wins_over_config(self) -> None:
        config = self._config(
            [{"action": "record", "params": {"value": "{{ variables.limit }}"}}],
            variables={"limit": 1},
        )
        merged = initial_variables(config, overrides={"limit": 5})
        self.assertEqual(merged, {"limit": 5})
        self._run(config, variables=merged)
        self.assertEqual(_Recorder.calls[0]["value"], 5)

    def test_runs_are_isolated_and_do_not_mutate_config(self) -> None:
        config = self._config(
            [
                {
                    "action": "record",
                    "params": {"items": "{{ variables.items }}"},
                }
            ],
            variables={"items": ["a"], "meta": {"n": 1}},
        )
        snapshot = copy.deepcopy(config)

        def mutate(context: ExecutionContext, _params: dict[str, Any]) -> None:
            items = context.get_variable("items")
            items.append("mutated")
            context.get_variable("meta")["n"] = 99
            context.set_variable("extra", True)

        _Recorder.behavior = staticmethod(mutate)
        self._run(config, variables=initial_variables(config))
        self._run(config, variables=initial_variables(config))

        self.assertEqual(config, snapshot)
        self.assertEqual(config_variables(config), {"items": ["a"], "meta": {"n": 1}})
        second_run = initial_variables(config)
        self.assertEqual(second_run, {"items": ["a"], "meta": {"n": 1}})

    def test_loop_variables_remain_runtime_metadata(self) -> None:
        config = self._config(
            [{"action": "record", "params": {"index": "{{ loop.iteration }}"}}],
            variables={"base": 1},
            loop={"count": 2},
        )
        self._run(config)
        self.assertEqual([call["index"] for call in _Recorder.calls], [0, 1])

    def test_validator_rejects_invalid_and_reserved_variable_keys(self) -> None:
        base = self._config([{"action": "record"}])
        for bad in ({"my-key": 1}, {"9lives": 1}, {"manual_run": True}, {"_private": 1}):
            with self.subTest(variables=bad):
                config = {**base, "variables": bad}
                with self.assertRaises(SchemaValidationError):
                    validate_config(config)

    def test_validator_rejects_conflicting_legacy_and_canonical_variables(self) -> None:
        config = self._config([{"action": "record"}])
        config["variables"] = {"x": 1}
        config["workflow"]["variables"] = {"x": 2}
        with self.assertRaisesRegex(SchemaValidationError, "both"):
            validate_config(config)

    def test_legacy_workflow_variables_are_accepted_when_consistent(self) -> None:
        config = self._config([{"action": "record"}])
        config["workflow"]["variables"] = {"x": 2}
        self.assertEqual(config_variables(config), {"x": 2})
        self.assertTrue(validate_config(config))


# ---------------------------------------------------------------------------
# 2. settings.retry_attempts
# ---------------------------------------------------------------------------


class RetryAttemptsTests(_Base):
    def _count(self, config: dict[str, Any]) -> int:
        _AlwaysFails.count = 0
        with self.assertRaises(WorkflowExecutionError):
            self._run(config)
        return _AlwaysFails.count

    def test_workflow_fallback_means_retries_after_first_attempt(self) -> None:
        config = self._config([{"action": "fails"}])
        config["settings"]["retry_attempts"] = 2
        self.assertEqual(self._count(config), 3)

    def test_zero_retries_means_single_invocation(self) -> None:
        config = self._config([{"action": "fails"}])
        config["settings"]["retry_attempts"] = 0
        self.assertEqual(self._count(config), 1)

    def test_step_retry_attempts_override_workflow_fallback(self) -> None:
        config = self._config([{"action": "fails", "retry": {"attempts": 1}}])
        config["settings"]["retry_attempts"] = 5
        self.assertEqual(self._count(config), 1)

    def test_step_failure_stop_policy_disables_workflow_fallback(self) -> None:
        config = self._config([{"action": "fails", "failure": {"policy": "stop"}}])
        config["settings"]["retry_attempts"] = 5
        self.assertEqual(self._count(config), 1)

    def test_step_retry_policy_without_attempts_uses_workflow_fallback(self) -> None:
        config = self._config([{"action": "fails", "failure": {"policy": "retry"}}])
        config["settings"]["retry_attempts"] = 1
        self.assertEqual(self._count(config), 2)

    def test_step_explicit_attempts_win_over_workflow_fallback(self) -> None:
        config = self._config(
            [{"action": "fails", "failure": {"policy": "retry", "attempts": 2}}]
        )
        config["settings"]["retry_attempts"] = 7
        self.assertEqual(self._count(config), 2)

    def test_no_duplicate_retry_loops_for_step_retry(self) -> None:
        config = self._config([{"action": "fails", "retry": {"attempts": 3}}])
        self.assertEqual(self._count(config), 3)

    def test_validator_rejects_invalid_retry_attempts(self) -> None:
        for bad in (-1, 1.5, "2", True):
            with self.subTest(value=bad):
                config = self._config([{"action": "record"}])
                config["settings"]["retry_attempts"] = bad
                with self.assertRaisesRegex(SchemaValidationError, "retry_attempts"):
                    validate_config(config)

    def test_runner_rejects_invalid_retry_attempts_without_validator(self) -> None:
        config = self._config([{"action": "fails"}])
        config["settings"]["retry_attempts"] = -2
        with self.assertRaisesRegex(WorkflowExecutionError, "retry_attempts"):
            self._run(config)
        self.assertEqual(_AlwaysFails.count, 0)

    def test_successful_step_is_not_retried_by_fallback(self) -> None:
        config = self._config([{"action": "record"}])
        config["settings"]["retry_attempts"] = 4
        self._run(config)
        self.assertEqual(len(_Recorder.calls), 1)


# ---------------------------------------------------------------------------
# 3. while_running policies
# ---------------------------------------------------------------------------


class WhileRunningPolicyTests(_Base):
    def test_validator_rejects_unsupported_policies_for_both_keys(self) -> None:
        for key in ("while_running", "while_running_policy"):
            for policy in ("run_concurrently", "terminate_and_restart"):
                with self.subTest(key=key, policy=policy):
                    config = self._config(
                        [{"action": "record"}],
                        trigger={"type": "manual", key: policy},
                    )
                    with self.assertRaisesRegex(SchemaValidationError, "not supported"):
                        validate_config(config)

    def test_validator_accepts_supported_policies(self) -> None:
        for policy in ("ignore", "queue", "restart"):
            with self.subTest(policy=policy):
                config = self._config(
                    [{"action": "record"}],
                    trigger={"type": "manual", "while_running": policy},
                )
                self.assertTrue(validate_config(config))

    def test_engine_listener_rejects_unsupported_policy_without_mapping_to_queue(self) -> None:
        triggers = TriggerRegistry()
        triggers.register("fake", _FakeTrigger)
        engine = WorkflowEngine(
            action_registry=self.actions,
            trigger_registry=triggers,
            emergency_stop=self.emergency_stop,
        )
        try:
            for policy in ("run_concurrently", "terminate_and_restart"):
                with self.subTest(policy=policy):
                    config = self._config(
                        [{"action": "record"}],
                        trigger={"type": "fake", "while_running": policy},
                    )
                    with self.assertRaisesRegex(ConfigurationError, "not supported"):
                        engine.start_workflow_listener(config)
        finally:
            engine.shutdown()

    def test_notification_trigger_rejects_unsupported_policy(self) -> None:
        for policy in ("run_concurrently", "terminate_and_restart"):
            with self.subTest(policy=policy):
                with self.assertRaisesRegex(TriggerConfigurationError, "not supported"):
                    WindowsNotificationTrigger(
                        name="t",
                        config={
                            "type": "windows_notification",
                            "match_any": True,
                            "while_running": policy,
                        },
                        source=MockNotificationSource(),
                    )

    def test_notification_trigger_keeps_supported_policies(self) -> None:
        for policy in ("ignore", "queue", "restart"):
            with self.subTest(policy=policy):
                trigger = WindowsNotificationTrigger(
                    name="t",
                    config={
                        "type": "windows_notification",
                        "match_any": True,
                        "while_running": policy,
                    },
                    source=MockNotificationSource(),
                )
                self.assertEqual(trigger.while_running_policy, policy)


# ---------------------------------------------------------------------------
# 4. Step aliases
# ---------------------------------------------------------------------------


class StepAliasTests(_Base):
    def test_legacy_timeout_is_normalized_and_enforced(self) -> None:
        _SleepsUntilCancelled.started = threading.Event()
        config = self._config([{"action": "sleeps", "timeout": 0.05}])
        started = time.monotonic()
        with self.assertRaises(Exception):
            self._run(config)
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertTrue(_SleepsUntilCancelled.started.is_set())

    def test_canonical_timeout_is_enforced(self) -> None:
        _SleepsUntilCancelled.started = threading.Event()
        config = self._config([{"action": "sleeps", "timeout_seconds": 0.05}])
        started = time.monotonic()
        with self.assertRaises(Exception):
            self._run(config)
        self.assertLess(time.monotonic() - started, 1.5)

    def test_timeout_conflict_is_rejected_by_validator_and_runner(self) -> None:
        step = {"action": "record", "timeout": 1, "timeout_seconds": 2}
        with self.assertRaisesRegex(SchemaValidationError, "timeout"):
            validate_config(self._config([step]))
        with self.assertRaisesRegex(WorkflowExecutionError, "timeout"):
            self._run(self._config([step]))
        self.assertEqual(_Recorder.calls, [])

    def test_matching_timeout_aliases_are_accepted(self) -> None:
        step = {"action": "record", "timeout": 3, "timeout_seconds": 3}
        self.assertTrue(validate_config(self._config([step])))
        self.assertEqual(canonical_step(step), {"action": "record", "timeout_seconds": 3})

    def test_legacy_verification_is_executed(self) -> None:
        config = self._config(
            [{"action": "record", "verification": {"type": "probe", "target": {"title_contains": "X"}}}]
        )
        self._run(config)
        self.assertEqual(len(_ProbeVerification.calls), 1)
        self.assertEqual(_ProbeVerification.calls[0].get("title_contains"), "X")

    def test_canonical_verify_failure_fails_the_step(self) -> None:
        _ProbeVerification.result = False
        config = self._config(
            [{"action": "record", "verify": {"type": "probe", "target": {"title_contains": "Y"}}}]
        )
        with self.assertRaises(VerificationFailedError):
            self._run(config)
        self.assertEqual(len(_ProbeVerification.calls), 1)

    def test_verification_conflict_is_rejected(self) -> None:
        step = {
            "action": "record",
            "verify": {"type": "probe", "target": {"title_contains": "A"}},
            "verification": {"type": "probe", "target": {"title_contains": "B"}},
        }
        with self.assertRaisesRegex(SchemaValidationError, "verify"):
            validate_config(self._config([step]))

    def test_metadata_keys_never_become_action_params(self) -> None:
        config = self._config(
            [
                {
                    "action": "record",
                    "name": "Open",
                    "step_id": 7,
                    "label": "Step 1",
                    "description": "human text",
                    "timeout_seconds": 5,
                    "timeout": 5,
                    "value": "kept",
                }
            ]
        )
        self._run(config)
        self.assertEqual(_Recorder.calls, [{"value": "kept"}])

    def test_legitimate_top_level_legacy_params_are_preserved(self) -> None:
        step = {"action": "record", "title_contains": "Helium", "state": "maximize"}
        self._run(self._config([step]))
        self.assertEqual(_Recorder.calls, [{"title_contains": "Helium", "state": "maximize"}])

    def test_explicit_params_win_over_top_level_legacy_params(self) -> None:
        step = {"action": "record", "value": "top", "params": {"value": "explicit"}}
        self._run(self._config([step]))
        self.assertEqual(_Recorder.calls, [{"value": "explicit"}])

    def test_action_params_helper_matches_runner_contract(self) -> None:
        step = canonical_step(
            {"action": "x", "name": "n", "params": {"a": 1}, "b": 2, "timeout": 1, "verification": {}}
        )
        self.assertEqual(action_params_from_step(step), {"a": 1, "b": 2})


# ---------------------------------------------------------------------------
# 6. Failure diagnostics for retry
# ---------------------------------------------------------------------------


class RetryDiagnosticsTests(_Base):
    def test_retry_policy_is_recognized_without_unknown_warning(self) -> None:
        logger = Mock()
        handler = FailureHandler(logger=logger)
        decision = handler.handle_failure(
            RuntimeError("x"),
            FailureConfig(policy="retry", attempts=2),
            ExecutionContext(workflow_id="w", emergency_stop=self.emergency_stop),
            step_id=1,
        )
        self.assertEqual(decision.action, FailureAction.ABORT)
        self.assertEqual(decision.policy, "retry")
        for call in logger.method_calls:
            self.assertNotIn("Unknown failure policy", str(call))

    def test_retry_exhaustion_invokes_action_exactly_attempts_times(self) -> None:
        # RetryExecutor performs the 3 attempts; the failure handler must not add more.
        config = self._config([{"action": "fails", "failure": {"policy": "retry", "attempts": 3}}])
        with self.assertRaises(WorkflowExecutionError):
            self._run(config)
        self.assertEqual(_AlwaysFails.count, 3)

    def test_retry_with_single_attempt_still_aborts_without_warning(self) -> None:
        config = self._config([{"action": "fails", "failure": {"policy": "retry", "attempts": 1}}])
        with self.assertRaises(WorkflowExecutionError):
            self._run(config)
        self.assertEqual(_AlwaysFails.count, 1)


# ---------------------------------------------------------------------------
# 5. Sample repairs (mocked actions)
# ---------------------------------------------------------------------------


def _iter_steps(steps: list[dict[str, Any]]):
    for step in steps:
        yield step
        for key in ("then", "else", "steps", "do"):
            body = step.get(key)
            if isinstance(body, list):
                yield from _iter_steps(body)


class SampleRepairTests(_Base):
    SAMPLE_NAMES = ("winflow_demo", "asd", "wa", "example")

    def _load(self, name: str) -> dict[str, Any]:
        return load_and_validate_config(SAMPLES / f"{name}.json")

    def _steps(self, config: dict[str, Any]) -> list[dict[str, Any]]:
        workflow = config.get("workflow", {})
        steps = workflow.get("steps") if isinstance(workflow, dict) and "steps" in workflow else config.get("steps", [])
        return list(_iter_steps(steps))

    def test_all_samples_validate(self) -> None:
        for name in self.SAMPLE_NAMES:
            with self.subTest(sample=name):
                self.assertTrue(self._load(name))

    def test_sample_wait_steps_use_supported_fields(self) -> None:
        for name in self.SAMPLE_NAMES:
            for step in self._steps(self._load(name)):
                if step.get("action") != "wait":
                    continue
                params = {**action_params_from_step(canonical_step(step))}
                with self.subTest(sample=name, step=step):
                    self.assertTrue(
                        "seconds" in params or "milliseconds" in params or "duration_ms" in params,
                        f"wait step has no supported duration field: {params}",
                    )

    def test_sample_conditions_are_processable(self) -> None:
        for name in self.SAMPLE_NAMES:
            config = self._load(name)
            context = ExecutionContext(
                workflow_id=name,
                emergency_stop=self.emergency_stop,
                variables=initial_variables(config),
            )
            for step in self._steps(config):
                condition = step.get("condition")
                if step.get("action") not in {"if", "while"} or not condition:
                    continue
                with self.subTest(sample=name, condition=condition):
                    ConditionEvaluator.evaluate(condition, context)

    def test_sample_variable_keys_are_valid(self) -> None:
        for name in self.SAMPLE_NAMES:
            with self.subTest(sample=name):
                config = self._load(name)
                self.assertIsInstance(config_variables(config), dict)

    def test_winflow_demo_initializes_counter_and_takes_then_branch(self) -> None:
        config = self._load("winflow_demo")
        seen_branch: list[str] = []

        def record_branch(_context: ExecutionContext, params: dict[str, Any]) -> None:
            if "Condition branch" in str(params.get("text", "")):
                seen_branch.append("then")

        _Recorder.behavior = staticmethod(record_branch)
        self.actions.register("set_variable", SetVariableAction)
        for name in ("clipboard_clear", "clipboard_write", "clipboard_read", "wait"):
            self.actions.register(name, _Recorder)
        context = self._run(config)
        self.assertEqual(context.get_variable("demo_counter"), 0)
        self.assertEqual(seen_branch, ["then"])
        waits = [c for c in _Recorder.calls if "seconds" in c]
        self.assertTrue(waits)

    def test_winflow_demo_set_variable_uses_params_name(self) -> None:
        config = self._load("winflow_demo")
        first = self._steps(config)[0]
        self.assertEqual(first["action"], "set_variable")
        self.assertEqual(first["params"]["name"], "demo_counter")
        self.assertNotIn("variable", first)

    def test_example_retry_policy_and_attempt_counts(self) -> None:
        config = self._load("example")
        steps = self._steps(config)
        verify_step = next(s for s in steps if s.get("name") == "verify_active_window_exists")
        self.assertEqual(verify_step["failure"]["attempts"], 2)
        self.assertEqual(config["settings"]["retry_attempts"], 3)

    def test_disabled_asd_placeholder_stays_disabled(self) -> None:
        config = self._load("asd")
        self.assertFalse(config["workflow"]["trigger"]["enabled"])


if __name__ == "__main__":
    unittest.main()
