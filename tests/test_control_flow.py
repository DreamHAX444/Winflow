import unittest
from typing import Any
from unittest.mock import patch

from winflow.actions.base import BaseAction
from winflow.actions.wait import WaitAction
from winflow.core.emergency_stop import EmergencyStop
from winflow.core.errors import (
    ActionExecutionError,
    EmergencyStopTriggered,
    WorkflowExecutionError,
)
from winflow.engine.action_registry import ActionRegistry
from winflow.engine.execution_context import ExecutionContext
from winflow.engine.workflow_runner import WorkflowRunner


class _RecordAction(BaseAction):
    events: list[tuple[str, Any]] = []

    def execute(self, context: ExecutionContext, **kwargs: Any) -> dict[str, Any]:
        context.check_cancellation()
        self.events.append((self.name, dict(self.params)))
        return {}


class _IncrementAction(BaseAction):
    def execute(self, context: ExecutionContext, **kwargs: Any) -> dict[str, Any]:
        name = self.params.get("name", "count")
        context.set_variable(name, context.get_variable(name, 0) + 1)
        return {}


class _CaptureLoopAction(BaseAction):
    seen: list[tuple[Any, Any, int]] = []

    def execute(self, context: ExecutionContext, **kwargs: Any) -> dict[str, Any]:
        self.seen.append(
            (
                context.get_variable("_loop_index"),
                context.get_variable("_loop_item"),
                context.loop_iteration,
            )
        )
        return {}


class _FailAction(BaseAction):
    def execute(self, context: ExecutionContext, **kwargs: Any) -> None:
        raise ActionExecutionError("intentional step failure")


class _StopAction(BaseAction):
    events: list[str] = []

    def execute(self, context: ExecutionContext, **kwargs: Any) -> None:
        self.events.append("stopped")
        context.emergency_stop.request_stop("test stop")


class _RegisterCleanupAction(BaseAction):
    calls: list[str] = []

    def execute(self, context: ExecutionContext, **kwargs: Any) -> None:
        context.register_cleanup(lambda: self.calls.append("cleaned"))


class _TimelineAction(BaseAction):
    events: list[tuple[str, float | None]] = []

    def execute(self, context: ExecutionContext, **kwargs: Any) -> None:
        self.events.append(("action", None))


class ControlFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.emergency_stop = EmergencyStop()
        _RecordAction.events = []
        _CaptureLoopAction.seen = []
        _StopAction.events = []
        _RegisterCleanupAction.calls = []
        _TimelineAction.events = []
        self.registry = ActionRegistry()
        self.registry.register("record", _RecordAction)
        self.registry.register("increment", _IncrementAction)
        self.registry.register("capture_loop", _CaptureLoopAction)
        self.registry.register("fail", _FailAction)
        self.registry.register("stop", _StopAction)
        self.registry.register("register_cleanup", _RegisterCleanupAction)
        self.registry.register("wait", WaitAction)
        self.registry.register("timeline", _TimelineAction)

    def _run(
        self,
        steps: list[dict[str, Any]],
        variables: dict[str, Any] | None = None,
        loop_config: dict[str, Any] | None = None,
        nested_loop: bool = False,
    ):
        workflow = {"id": "control-flow-tests", "steps": steps}
        config: dict[str, Any] = {
            "workflow": workflow,
            "settings": {"use_desktop_lock": False},
        }
        if loop_config is not None:
            if nested_loop:
                workflow["loop"] = loop_config
            else:
                config["loop"] = loop_config
        runner = WorkflowRunner(
            config,
            action_registry=self.registry,
            emergency_stop=self.emergency_stop,
        )
        context = ExecutionContext(
            workflow_id="control-flow-tests",
            emergency_stop=self.emergency_stop,
            variables=variables,
        )
        return runner.run(context)

    def test_steps_run_sequentially_and_keep_monotonic_step_numbers(self) -> None:
        context = self._run(
            [
                {"action": "record", "value": "first"},
                {
                    "action": "if",
                    "condition": {
                        "left": "{{ready}}",
                        "operator": "equals",
                        "right": True,
                    },
                    "then": [{"action": "record", "value": "branch"}],
                    "else": [{"action": "record", "value": "wrong"}],
                },
                {"action": "record", "value": "last"},
            ],
            {"ready": True},
        )
        self.assertEqual(
            [params["value"] for _, params in _RecordAction.events],
            ["first", "branch", "last"],
        )
        self.assertEqual(context.current_step, 4)

    def test_false_step_condition_skips_only_that_step(self) -> None:
        self._run(
            [
                {
                    "action": "record",
                    "condition": {
                        "left": "{{enabled}}",
                        "operator": "equals",
                        "right": True,
                    },
                    "value": "skipped",
                },
                {"action": "record", "value": "still runs"},
            ],
            {"enabled": False},
        )
        self.assertEqual(
            [params["value"] for _, params in _RecordAction.events],
            ["still runs"],
        )

    def test_while_loop_runs_until_condition_becomes_false(self) -> None:
        context = self._run(
            [
                {
                    "action": "while",
                    "condition": {
                        "left": "{{count}}",
                        "operator": "less_than",
                        "right": 3,
                    },
                    "max_iterations": 3,
                    "do": [{"action": "increment", "name": "count"}],
                }
            ],
            {"count": 0},
        )
        self.assertEqual(context.get_variable("count"), 3)
        self.assertEqual(context.loop_iteration, 0)
        self.assertEqual(context.current_step, 4)

    def test_for_each_exposes_items_and_restores_preexisting_loop_state(self) -> None:
        context = self._run(
            [
                {
                    "action": "for_each",
                    "items": [10, 20],
                    "do": [{"action": "capture_loop"}],
                }
            ],
            {"_loop_index": 8, "_loop_item": "outer"},
        )
        self.assertEqual(_CaptureLoopAction.seen, [(0, 10, 0), (1, 20, 1)])
        self.assertEqual(context.get_variable("_loop_index"), 8)
        self.assertEqual(context.get_variable("_loop_item"), "outer")

    def test_finite_workflow_loop_supports_root_and_nested_schema_locations(self) -> None:
        for loop_config, nested in (
            ({"count": 2}, False),
            ({"count": 3, "delay_between_seconds": 0}, True),
        ):
            _RecordAction.events = []
            self._run(
                [{"action": "record", "value": "repeat"}],
                loop_config=loop_config,
                nested_loop=nested,
            )
            self.assertEqual(len(_RecordAction.events), loop_config["count"])

    def test_workflow_loop_delay_runs_between_repetitions(self) -> None:
        events = _TimelineAction.events
        clock = [0.0]

        def time_now() -> float:
            return clock[0]

        def sleep(seconds: float) -> None:
            events.append(("wait", seconds))
            clock[0] += seconds

        with patch("winflow.actions.wait.time.time", side_effect=time_now), patch(
            "winflow.actions.wait.time.sleep", side_effect=sleep
        ):
            self._run(
                [{"action": "timeline"}],
                loop_config={"count": 2, "delay_between_seconds": 0.05},
            )
        self.assertEqual(events[-1], ("action", None))
        self.assertEqual(
            [event for event, _ in events].count("action"),
            2,
        )
        self.assertAlmostEqual(
            sum(duration for event, duration in events if event == "wait"),
            0.05,
        )

    def test_infinite_workflow_loop_is_rejected(self) -> None:
        with self.assertRaisesRegex(WorkflowExecutionError, "Infinite workflow loops"):
            self._run(
                [{"action": "record"}],
                loop_config={"count": "infinite"},
            )
        self.assertEqual(_RecordAction.events, [])

    def test_loop_state_is_restored_when_body_fails(self) -> None:
        with self.assertRaisesRegex(ActionExecutionError, "intentional step failure"):
            self._run(
                [
                    {
                        "action": "for_each",
                        "items": [10],
                        "do": [{"action": "fail"}],
                    }
                ],
                {"_loop_index": 8, "_loop_item": "outer"},
            )

    def test_invalid_repeat_count_or_items_fails_clearly(self) -> None:
        with self.assertRaisesRegex(WorkflowExecutionError, "max_iterations"):
            self._run(
                [
                    {
                        "action": "while",
                        "condition": {},
                        "max_iterations": 0,
                        "do": [],
                    }
                ]
            )
        self.emergency_stop.reset()
        with self.assertRaisesRegex(WorkflowExecutionError, "must resolve to a list"):
            self._run([{"action": "for_each", "items": "not a list", "do": []}])

    def test_while_loop_cannot_exceed_its_iteration_bound(self) -> None:
        with self.assertRaisesRegex(WorkflowExecutionError, "exceeded max_iterations"):
            self._run(
                [
                    {
                        "action": "while",
                        "condition": {},
                        "max_iterations": 2,
                        "do": [],
                    }
                ]
            )

    def test_wait_respects_duration_and_completes_before_next_action(self) -> None:
        events: list[tuple[str, float | None]] = _TimelineAction.events
        clock = [0.0]

        def time_now() -> float:
            return clock[0]

        def sleep(seconds: float) -> None:
            events.append(("wait", seconds))
            clock[0] += seconds

        with patch("winflow.actions.wait.time.time", side_effect=time_now), patch(
            "winflow.actions.wait.time.sleep", side_effect=sleep
        ):
            self._run(
                [
                    {"action": "wait", "seconds": 0.1},
                    {"action": "timeline"},
                ]
            )
        wait_events = [duration for event, duration in events if event == "wait"]
        self.assertGreaterEqual(sum(wait_events), 0.1)
        self.assertEqual(events[-1], ("action", None))

    def test_step_failure_propagates_and_cleanup_still_runs(self) -> None:
        with self.assertRaisesRegex(ActionExecutionError, "intentional step failure"):
            self._run(
                [
                    {"action": "register_cleanup"},
                    {"action": "fail"},
                    {"action": "record", "value": "must not run"},
                ]
            )
        self.assertEqual(_RegisterCleanupAction.calls, ["cleaned"])
        self.assertEqual(_RecordAction.events, [])

    def test_emergency_stop_prevents_remaining_steps_and_runs_cleanup(self) -> None:
        with self.assertRaises(EmergencyStopTriggered):
            self._run(
                [
                    {"action": "register_cleanup"},
                    {"action": "stop"},
                    {"action": "record", "value": "must not run"},
                ]
            )
        self.assertEqual(_StopAction.events, ["stopped"])
        self.assertEqual(_RegisterCleanupAction.calls, ["cleaned"])
        self.assertEqual(_RecordAction.events, [])

    def test_invalid_condition_is_reported_as_workflow_error(self) -> None:
        with self.assertRaisesRegex(WorkflowExecutionError, "Invalid condition"):
            self._run(
                [
                    {
                        "action": "if",
                        "condition": {"left": "x"},
                        "then": [],
                        "else": [],
                    }
                ]
            )


if __name__ == "__main__":
    unittest.main()
