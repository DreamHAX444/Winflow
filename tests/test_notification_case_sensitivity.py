"""Regression tests for notification case-sensitivity configuration.

Canonical shape: ``match.case_sensitive`` (boolean), optionally overridden per field
with ``match.<field>.case_sensitive``. Top-level ``case_sensitive`` was never read by
the runtime, so it is rejected instead of being silently accepted.
"""

import unittest

from winflow.config.validator import validate_config
from winflow.core.errors import SchemaValidationError, TriggerConfigurationError
from winflow.triggers.event import NotificationEvent
from winflow.triggers.matcher import NotificationMatcher, build_notification_match_config


def _workflow(root_trigger):
    return {
        "schema_version": "1.0",
        "workflow": {"id": "case-test", "steps": [{"action": "wait", "seconds": 0}]},
        "trigger": root_trigger,
    }


def _event(title):
    return NotificationEvent(event_id="e", application_id="App", title=title)


class CanonicalCaseSensitivityTests(unittest.TestCase):
    def test_default_is_case_insensitive(self) -> None:
        matcher = NotificationMatcher({"title": {"mode": "contains", "value": "DONE"}})
        self.assertTrue(matcher.matches(_event("job done")))
        self.assertTrue(matcher.matches(_event("JOB DONE")))

    def test_match_case_sensitive_true_is_case_sensitive(self) -> None:
        matcher = NotificationMatcher({"title": {"mode": "contains", "value": "DONE"}, "case_sensitive": True})
        self.assertFalse(matcher.matches(_event("job done")))
        self.assertTrue(matcher.matches(_event("JOB DONE")))

    def test_match_case_sensitive_false_is_explicitly_insensitive(self) -> None:
        matcher = NotificationMatcher({"title": "DONE", "case_sensitive": False})
        self.assertTrue(matcher.matches(_event("job done")))

    def test_case_sensitivity_applies_to_exact_mode(self) -> None:
        sensitive = NotificationMatcher({"title": {"mode": "exact", "value": "Build OK"}, "case_sensitive": True})
        self.assertFalse(sensitive.matches(_event("build ok")))
        self.assertTrue(sensitive.matches(_event("Build OK")))

    def test_field_level_override_takes_precedence(self) -> None:
        matcher = NotificationMatcher({
            "title": {"mode": "contains", "value": "Done", "case_sensitive": True},
            "case_sensitive": False,
        })
        self.assertFalse(matcher.matches(_event("done")))
        self.assertTrue(matcher.matches(_event("Done")))

    def test_canonical_shape_is_accepted_at_root_and_workflow_levels(self) -> None:
        trigger = {"type": "windows_notification", "match": {"title": "Done", "case_sensitive": True}}
        self.assertTrue(validate_config(_workflow(trigger)))
        nested = {"type": "windows_notification", "match": {"title": "Done", "case_sensitive": True}}
        self.assertTrue(validate_config({
            "schema_version": "1.0",
            "workflow": {"id": "case-test", "trigger": nested, "steps": [{"action": "wait", "seconds": 0}]},
        }))

    def test_non_boolean_case_sensitive_is_rejected(self) -> None:
        for bad in ("false", "true", 0, 1, None):
            with self.subTest(value=bad):
                with self.assertRaises((TriggerConfigurationError, SchemaValidationError)):
                    NotificationMatcher({"title": "Done", "case_sensitive": bad})
        with self.assertRaisesRegex(SchemaValidationError, "case_sensitive must be true or false"):
            validate_config(_workflow({
                "type": "windows_notification",
                "match": {"title": {"mode": "contains", "value": "Done", "case_sensitive": "false"}},
            }))


class TopLevelCaseSensitivityRejectionTests(unittest.TestCase):
    def test_top_level_case_sensitive_is_rejected_with_guidance(self) -> None:
        trigger = {"type": "windows_notification", "title": "Done", "case_sensitive": True}
        with self.assertRaisesRegex(SchemaValidationError, "Top-level 'case_sensitive' is not a supported"):
            validate_config(_workflow(trigger))
        with self.assertRaisesRegex(TriggerConfigurationError, "match"):
            build_notification_match_config(trigger)

    def test_top_level_case_sensitive_false_is_also_rejected(self) -> None:
        trigger = {"type": "windows_notification", "title": "Done", "case_sensitive": False}
        with self.assertRaises(SchemaValidationError):
            validate_config(_workflow(trigger))

    def test_top_level_and_nested_values_together_are_rejected(self) -> None:
        trigger = {
            "type": "windows_notification",
            "match": {"title": "Done", "case_sensitive": False},
            "case_sensitive": True,
        }
        with self.assertRaisesRegex(SchemaValidationError, "Top-level 'case_sensitive'"):
            validate_config(_workflow(trigger))

    def test_case_sensitive_inside_nested_config_is_rejected_not_dropped(self) -> None:
        trigger = {
            "type": "windows_notification",
            "title": "Done",
            "config": {"case_sensitive": True},
        }
        with self.assertRaisesRegex(SchemaValidationError, "inside 'config' is not read"):
            validate_config(_workflow(trigger))

    def test_rejected_top_level_key_cannot_change_matching(self) -> None:
        # Previously this silently matched case-insensitively; it must now fail loudly.
        with self.assertRaises(SchemaValidationError):
            validate_config(_workflow({"type": "windows_notification", "title": "DONE", "case_sensitive": True}))


if __name__ == "__main__":
    unittest.main()
