"""Regression tests for notification trigger matching safety.

Guarantees covered:
* An empty or effectively empty matcher is rejected unless match_any is explicit.
* Legacy/unsupported matching keys are rejected, never silently ignored.
* Root-level and workflow-level trigger blocks receive identical validation.
* Supported matching modes, case sensitivity, deduplication, and cooldown still work.
* Shipped sample workflows match only what they are documented to match.
"""

import unittest
from pathlib import Path

from winflow.config.loader import load_and_validate_config
from winflow.config.validator import validate_config
from winflow.core.emergency_stop import EmergencyStop
from winflow.core.errors import SchemaValidationError, TriggerConfigurationError
from winflow.triggers.event import NotificationEvent
from winflow.triggers.matcher import NotificationMatcher
from winflow.triggers.sources.mock import MockNotificationSource
from winflow.triggers.windows_notification import WindowsNotificationTrigger

WORKFLOWS_DIR = Path(__file__).resolve().parents[1] / "winflow" / "workflows"


def _workflow(trigger=None, root_trigger=None):
    config = {
        "schema_version": "1.0",
        "workflow": {"id": "notify-test", "steps": [{"action": "wait", "seconds": 0}]},
    }
    if trigger is not None:
        config["workflow"]["trigger"] = trigger
    if root_trigger is not None:
        config["trigger"] = root_trigger
    return config


def _event(app="Example.App", title="Build completed", body="Finished 3 tasks", event_id="e1"):
    return NotificationEvent(event_id=event_id, application_id=app, title=title, body=body)


def _matcher_for(trigger_cfg):
    """Build a trigger the same way the engine does and return its matcher."""
    trigger = WindowsNotificationTrigger(
        config=trigger_cfg,
        source=MockNotificationSource(),
        emergency_stop=EmergencyStop(),
    )
    return trigger.matcher


class EmptyCriteriaRejectionTests(unittest.TestCase):
    def test_trigger_without_any_criteria_is_rejected_by_default(self) -> None:
        for trigger in (
            {"type": "windows_notification"},
            {"type": "windows_notification", "match": {}},
            {"type": "windows_notification", "match": {"case_sensitive": True}},
            {"type": "windows_notification", "cooldown_seconds": 3.0},
        ):
            with self.subTest(trigger=trigger):
                with self.assertRaisesRegex(SchemaValidationError, "no matching criteria"):
                    validate_config(_workflow(root_trigger=trigger))
                with self.assertRaisesRegex(TriggerConfigurationError, "no matching criteria"):
                    _matcher_for(trigger)

    def test_empty_values_are_rejected_even_though_they_would_match_everything(self) -> None:
        for trigger in (
            {"type": "windows_notification", "application": {"contains": ""}},
            {"type": "windows_notification", "match": {"title": {"mode": "contains", "value": ""}}},
            {"type": "windows_notification", "body": "   "},
            {"type": "windows_notification", "match": {"application": {"mode": "regex", "value": ""}}},
        ):
            with self.subTest(trigger=trigger), self.assertRaises(SchemaValidationError):
                validate_config(_workflow(root_trigger=trigger))
            with self.subTest(trigger=trigger, runtime=True), self.assertRaises(TriggerConfigurationError):
                _matcher_for(trigger)

    def test_null_criteria_do_not_count_as_criteria(self) -> None:
        trigger = {"type": "windows_notification", "application": None, "title": None}
        with self.assertRaisesRegex(SchemaValidationError, "no matching criteria"):
            validate_config(_workflow(root_trigger=trigger))

    def test_matcher_constructor_rejects_empty_config_by_default(self) -> None:
        with self.assertRaises(TriggerConfigurationError):
            NotificationMatcher(config={})
        with self.assertRaises(TriggerConfigurationError):
            NotificationMatcher(config=None)


class ExplicitMatchAnyTests(unittest.TestCase):
    def test_match_any_true_is_accepted_at_root_and_workflow_level(self) -> None:
        trigger = {"type": "windows_notification", "match_any": True}
        self.assertTrue(validate_config(_workflow(root_trigger=trigger)))
        self.assertTrue(validate_config(_workflow(trigger=trigger)))

    def test_match_any_true_in_match_block_is_accepted(self) -> None:
        trigger = {"type": "windows_notification", "match": {"match_any": True}}
        self.assertTrue(validate_config(_workflow(root_trigger=trigger)))

    def test_match_any_true_intentionally_matches_every_notification(self) -> None:
        matcher = _matcher_for({"type": "windows_notification", "match_any": True})
        self.assertTrue(matcher.matches(_event()))
        self.assertTrue(matcher.matches(_event(app="Unrelated", title="Lunch", body="")))

    def test_match_any_false_does_not_permit_match_all(self) -> None:
        with self.assertRaisesRegex(SchemaValidationError, "no matching criteria"):
            validate_config(_workflow(root_trigger={"type": "windows_notification", "match_any": False}))

    def test_match_any_must_be_boolean(self) -> None:
        with self.assertRaisesRegex(SchemaValidationError, "match_any"):
            validate_config(_workflow(root_trigger={"type": "windows_notification", "match_any": "yes"}))

    def test_match_any_with_criteria_is_contradictory_and_rejected(self) -> None:
        trigger = {"type": "windows_notification", "match_any": True, "application": "Example"}
        with self.assertRaisesRegex(SchemaValidationError, "cannot be combined"):
            validate_config(_workflow(root_trigger=trigger))

    def test_conflicting_match_any_values_are_rejected(self) -> None:
        trigger = {
            "type": "windows_notification",
            "match_any": True,
            "match": {"match_any": False},
        }
        with self.assertRaisesRegex(SchemaValidationError, "conflicting values"):
            validate_config(_workflow(root_trigger=trigger))


class LegacyKeyRejectionTests(unittest.TestCase):
    def test_legacy_flat_keys_are_rejected_not_ignored(self) -> None:
        for key in ("app_name", "title_contains", "body_contains", "app_id", "application_name",
                    "app_contains", "title_regex", "application_exact"):
            trigger = {"type": "windows_notification", key: "DemoApp", "cooldown_seconds": 1}
            with self.subTest(key=key), self.assertRaisesRegex(
                SchemaValidationError, f"Unsupported notification matching key '{key}'"
            ):
                validate_config(_workflow(root_trigger=trigger))

    def test_legacy_key_alone_cannot_become_match_all(self) -> None:
        trigger = {"type": "windows_notification", "app_name": "DemoApp"}
        with self.assertRaises(SchemaValidationError):
            validate_config(_workflow(root_trigger=trigger))
        with self.assertRaises(TriggerConfigurationError):
            _matcher_for(trigger)

    def test_unknown_keys_inside_match_are_rejected(self) -> None:
        trigger = {"type": "windows_notification", "match": {"app_name": "DemoApp", "title": "Hello"}}
        with self.assertRaisesRegex(SchemaValidationError, "Unsupported notification match key|has unsupported key"):
            validate_config(_workflow(root_trigger=trigger))
        with self.assertRaisesRegex(TriggerConfigurationError, "app_name"):
            NotificationMatcher(config={"app_name": "DemoApp", "title": "Hello"})

    def test_unknown_keys_inside_field_dictionary_are_rejected(self) -> None:
        trigger = {"type": "windows_notification", "match": {"title": {"mode": "contains", "value": "x", "exact": True}}}
        with self.assertRaisesRegex(SchemaValidationError, "Unsupported notification match key|has unsupported key"):
            validate_config(_workflow(root_trigger=trigger))

    def test_criteria_in_nested_config_are_rejected_not_silently_dropped(self) -> None:
        trigger = {
            "type": "windows_notification",
            "config": {"match": {"application": "Example"}},
        }
        with self.assertRaisesRegex(SchemaValidationError, "inside 'config' is not read"):
            validate_config(_workflow(root_trigger=trigger))

    def test_match_and_top_level_criteria_together_are_rejected(self) -> None:
        trigger = {
            "type": "windows_notification",
            "match": {"application": "Example"},
            "title": "completed",
        }
        with self.assertRaisesRegex(SchemaValidationError, "both in 'match' and at the trigger"):
            validate_config(_workflow(root_trigger=trigger))


class NestedAndRootEquivalenceTests(unittest.TestCase):
    def test_root_and_workflow_trigger_receive_the_same_outcome(self) -> None:
        valid = {"type": "windows_notification", "match": {"application": "Example"}}
        invalid = {"type": "windows_notification"}
        self.assertTrue(validate_config(_workflow(root_trigger=valid)))
        self.assertTrue(validate_config(_workflow(trigger=valid)))
        for bad in (invalid, {"type": "windows_notification", "app_name": "x"}):
            with self.subTest(trigger=bad):
                with self.assertRaises(SchemaValidationError):
                    validate_config(_workflow(root_trigger=bad))
                with self.assertRaises(SchemaValidationError):
                    validate_config(_workflow(trigger=bad))

    def test_root_and_workflow_match_any_are_equivalent(self) -> None:
        trigger = {"type": "windows_notification", "match_any": True}
        self.assertTrue(validate_config(_workflow(root_trigger=trigger)))
        self.assertTrue(validate_config(_workflow(trigger=trigger)))


class SupportedMatchingModeTests(unittest.TestCase):
    def test_contains_and_exact_modes_match_expected_notifications(self) -> None:
        matcher = _matcher_for({
            "type": "windows_notification",
            "match": {
                "application": {"mode": "contains", "value": "Example"},
                "title": {"mode": "exact", "value": "Build completed"},
            },
        })
        self.assertTrue(matcher.matches(_event()))
        self.assertFalse(matcher.matches(_event(title="Build completed soon")))
        self.assertFalse(matcher.matches(_event(app="Other.App")))

    def test_top_level_shorthand_and_v1_contains_forms_still_work(self) -> None:
        shorthand = _matcher_for({"type": "windows_notification", "application": "Antigravity"})
        self.assertTrue(shorthand.matches(_event(app="Antigravity IDE")))
        v1 = _matcher_for({"type": "windows_notification", "title": {"contains": "done"}})
        self.assertTrue(v1.matches(_event(title="All done")))
        self.assertFalse(v1.matches(_event(title="Still working")))

    def test_regex_mode_still_supported(self) -> None:
        matcher = _matcher_for({
            "type": "windows_notification",
            "match": {"body": {"mode": "regex", "value": r"Finished \d+ tasks"}},
        })
        self.assertTrue(matcher.matches(_event(body="Finished 3 tasks")))
        self.assertFalse(matcher.matches(_event(body="Finished many tasks")))

    def test_case_sensitivity_is_unchanged(self) -> None:
        insensitive = _matcher_for({"type": "windows_notification", "title": "COMPLETED"})
        self.assertTrue(insensitive.matches(_event(title="build completed")))
        sensitive = _matcher_for({
            "type": "windows_notification",
            "match": {"title": "COMPLETED", "case_sensitive": True},
        })
        self.assertFalse(sensitive.matches(_event(title="build completed")))
        self.assertTrue(sensitive.matches(_event(title="BUILD COMPLETED")))

    def test_invalid_regex_and_mode_are_rejected(self) -> None:
        with self.assertRaises(SchemaValidationError):
            validate_config(_workflow(root_trigger={
                "type": "windows_notification",
                "match": {"title": {"mode": "regex", "value": "("}},
            }))
        with self.assertRaises(SchemaValidationError):
            validate_config(_workflow(root_trigger={
                "type": "windows_notification",
                "match": {"title": {"mode": "fuzzy", "value": "x"}},
            }))


class DeduplicationAndCooldownTests(unittest.TestCase):
    def test_duplicate_events_are_suppressed_and_cooldown_is_applied(self) -> None:
        source = MockNotificationSource()
        trigger = WindowsNotificationTrigger(
            config={
                "type": "windows_notification",
                "match": {"application": "Example"},
                "deduplication": {"enabled": True, "window_seconds": 60},
                "cooldown_seconds": 3600,
            },
            source=source,
            emergency_stop=EmergencyStop(),
        )
        received = []
        trigger.start(received.append)
        trigger._handle_notification_event(_event(event_id="a"))
        trigger._handle_notification_event(_event(event_id="a"))          # duplicate
        trigger._handle_notification_event(_event(title="Different", event_id="b"))  # cooldown
        trigger.stop()
        self.assertEqual([item["event_id"] for item in received], ["a"])

    def test_non_matching_event_never_reaches_callback(self) -> None:
        source = MockNotificationSource()
        trigger = WindowsNotificationTrigger(
            config={"type": "windows_notification", "application": "Example"},
            source=source,
            emergency_stop=EmergencyStop(),
        )
        received = []
        trigger.start(received.append)
        trigger._handle_notification_event(_event(app="Unrelated.App", event_id="x"))
        trigger.stop()
        self.assertEqual(received, [])


class SampleWorkflowMatchingTests(unittest.TestCase):
    def _trigger_for(self, filename: str) -> dict:
        config = load_and_validate_config(WORKFLOWS_DIR / filename)
        # Engine precedence: root "trigger" first, then "workflow.trigger".
        return config.get("trigger") or config["workflow"]["trigger"]

    def test_all_sample_workflows_validate(self) -> None:
        for path in sorted(WORKFLOWS_DIR.glob("*.json")):
            with self.subTest(sample=path.name):
                load_and_validate_config(path)

    def test_winflow_demo_matches_only_its_documented_notification(self) -> None:
        matcher = _matcher_for(self._trigger_for("winflow_demo.json"))
        self.assertTrue(matcher.matches(_event(app="DemoApp", title="Start Demo now")))
        # The legacy keys previously ignored made this trigger match "Lunch" notifications.
        self.assertFalse(matcher.matches(_event(app="Teams", title="Lunch", body="Lunch at noon")))
        self.assertFalse(matcher.matches(_event(app="DemoApp", title="Demo finished")))

    def test_wa_sample_still_matches_its_application(self) -> None:
        matcher = _matcher_for(self._trigger_for("wa.json"))
        self.assertTrue(matcher.matches(_event(app="Antigravity IDE")))
        self.assertFalse(matcher.matches(_event(app="Notepad")))

    def test_example_sample_still_requires_all_documented_fields(self) -> None:
        matcher = _matcher_for(self._trigger_for("example.json"))
        self.assertTrue(matcher.matches(_event(app="Example App", title="Job completed", body="All finished")))
        self.assertFalse(matcher.matches(_event(app="Example App", title="Job completed", body="Still running")))
        self.assertFalse(matcher.matches(_event(app="Other", title="Job completed", body="All finished")))

    def test_asd_sample_is_disabled_placeholder_and_documents_required_criteria(self) -> None:
        config = load_and_validate_config(WORKFLOWS_DIR / "asd.json")
        trigger = config["workflow"]["trigger"]
        self.assertIs(trigger.get("enabled"), False)
        self.assertIn("DISABLED", config["workflow"]["description"])
        self.assertIn("match", config["workflow"]["description"])
        # Enabling without replacing match_any would run on every notification.
        self.assertIs(trigger.get("match_any"), True)
        self.assertNotIn("application", trigger)
        self.assertNotIn("title", trigger)
        self.assertNotIn("body", trigger)

if __name__ == "__main__":
    unittest.main()
