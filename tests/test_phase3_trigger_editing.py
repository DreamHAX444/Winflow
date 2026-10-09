"""Phase 3 (GAP-007, GAP-008): trigger placement, preservation, and form merging.

These tests exercise the pure rules the trigger editor uses. They do not need Qt.
"""

import copy
import unittest

from winflow.config.trigger_form import (
    NotificationFormState,
    ScheduleFormState,
    TriggerFormError,
    merge_notification_trigger,
    merge_schedule_trigger,
    notification_form_from_trigger,
    schedule_form_from_trigger,
)
from winflow.config.trigger_location import (
    BOTH,
    CONFLICT,
    NESTED,
    NONE,
    ROOT,
    resolve_trigger_location,
    write_trigger,
)
from winflow.config.validator import validate_config
from winflow.core.errors import ConfigurationError
from winflow.triggers.matcher import build_notification_match_config


def _notification(**extra):
    trigger = {
        "type": "windows_notification",
        "match": {"title": {"mode": "contains", "value": "Done"}},
    }
    trigger.update(extra)
    return trigger


def _config(**extra):
    config = {"schema_version": "1.0", "workflow": {"id": "t", "name": "T"}, "steps": []}
    config.update(extra)
    return config


class TriggerPlacementTests(unittest.TestCase):
    def test_root_only(self):
        location = resolve_trigger_location(_config(trigger={"type": "manual"}))
        self.assertEqual(location.kind, ROOT)

    def test_nested_only_is_loaded(self):
        config = _config()
        config["workflow"]["trigger"] = _notification()
        location = resolve_trigger_location(config)
        self.assertEqual(location.kind, NESTED)
        self.assertEqual(location.trigger, _notification())

    def test_empty_root_counts_as_absent_like_engine(self):
        config = _config(trigger={})
        config["workflow"]["trigger"] = _notification()
        self.assertEqual(resolve_trigger_location(config).kind, NESTED)

    def test_identical_copies_are_both(self):
        config = _config(trigger=_notification())
        config["workflow"]["trigger"] = _notification()
        self.assertEqual(resolve_trigger_location(config).kind, BOTH)

    def test_different_copies_are_conflict_and_not_rewritten(self):
        config = _config(trigger={"type": "daily", "config": {"time": "08:00"}})
        config["workflow"]["trigger"] = _notification()
        before = copy.deepcopy(config)
        location = resolve_trigger_location(config)
        self.assertEqual(location.kind, CONFLICT)
        self.assertIsNone(location.trigger)
        self.assertIn("two different triggers", location.message)
        with self.assertRaises(ValueError):
            write_trigger(config, {"type": "manual"})
        self.assertEqual(config, before)

    def test_no_trigger(self):
        self.assertEqual(resolve_trigger_location(_config()).kind, NONE)


class WriteTriggerTests(unittest.TestCase):
    def test_nested_only_writes_nested_and_does_not_create_root(self):
        config = _config()
        config["workflow"]["trigger"] = _notification()
        write_trigger(config, _notification(cooldown_seconds=5))
        self.assertNotIn("trigger", config)
        self.assertEqual(config["workflow"]["trigger"]["cooldown_seconds"], 5)

    def test_new_trigger_goes_to_root(self):
        config = _config()
        write_trigger(config, {"type": "manual"})
        self.assertEqual(config["trigger"], {"type": "manual"})

    def test_both_identical_stay_identical(self):
        config = _config(trigger=_notification())
        config["workflow"]["trigger"] = _notification()
        write_trigger(config, _notification(cooldown_seconds=3))
        self.assertEqual(config["trigger"], config["workflow"]["trigger"])
        self.assertEqual(config["trigger"]["cooldown_seconds"], 3)

    def test_removal_clears_existing_location(self):
        config = _config(trigger={"type": "manual"})
        write_trigger(config, None)
        self.assertNotIn("trigger", config)


class NotificationMergeTests(unittest.TestCase):
    def _form(self, **kwargs):
        base = NotificationFormState(
            criteria={"title": {"mode": "contains", "value": "Done"}},
        )
        for key, value in kwargs.items():
            setattr(base, key, value)
        return base

    def test_unknown_fields_regex_dedupe_cooldown_and_while_running_preserved(self):
        original = {
            "type": "windows_notification",
            "match": {
                "application": {"mode": "regex", "value": "^Slack\\d+$"},
                "case_sensitive": True,
            },
            "deduplication": {"enabled": True, "window_seconds": 30, "scope": "global"},
            "cooldown_seconds": 12,
            "while_running": "queue",
            "x_note": "keep me",
        }
        form = NotificationFormState(
            criteria={"application": {"mode": "regex", "value": "^Slack\\d+$"}},
            case_sensitive=True,
            dedupe_enabled=True,
            dedupe_window=30.0,
            cooldown=12.0,
            while_running="queue",
        )
        merged = merge_notification_trigger(original, form, enabled=True)
        self.assertEqual(merged["match"]["application"], {"mode": "regex", "value": "^Slack\\d+$"})
        self.assertIs(merged["match"]["case_sensitive"], True)
        self.assertEqual(merged["deduplication"], {"enabled": True, "window_seconds": 30.0, "scope": "global"})
        self.assertEqual(merged["cooldown_seconds"], 12.0)
        self.assertEqual(merged["while_running"], "queue")
        self.assertEqual(merged["x_note"], "keep me")
        self.assertNotIn("enabled", merged)
        # The merged result must still be accepted by the backend.
        build_notification_match_config(merged)

    def test_enabled_false_preserved_and_never_silently_enabled(self):
        original = _notification(enabled=False)
        kept = merge_notification_trigger(original, self._form(), enabled=False)
        self.assertIs(kept["enabled"], False)
        turned_on = merge_notification_trigger(original, self._form(), enabled=True)
        self.assertIs(turned_on["enabled"], True)

    def test_untouched_enabled_absent_stays_absent(self):
        merged = merge_notification_trigger(_notification(), self._form(), enabled=True)
        self.assertNotIn("enabled", merged)

    def test_empty_matcher_is_refused(self):
        with self.assertRaises(TriggerFormError) as ctx:
            merge_notification_trigger(None, NotificationFormState(), enabled=True)
        self.assertIn("at least one notification criterion", str(ctx.exception))

    def test_match_all_is_explicit_and_writes_match_any(self):
        merged = merge_notification_trigger(
            None, NotificationFormState(match_all=True), enabled=True
        )
        self.assertIs(merged["match_any"], True)
        build_notification_match_config(merged)

    def test_match_all_and_criteria_are_mutually_exclusive(self):
        with self.assertRaises(TriggerFormError):
            merge_notification_trigger(None, self._form(match_all=True), enabled=True)

    def test_match_all_unchecked_clears_match_any(self):
        original = {"type": "windows_notification", "match_any": True}
        merged = merge_notification_trigger(original, self._form(), enabled=True)
        self.assertNotIn("match_any", merged)
        build_notification_match_config(merged)

    def test_invalid_regex_is_refused(self):
        form = NotificationFormState(criteria={"title": {"mode": "regex", "value": "("}})
        with self.assertRaises(TriggerFormError):
            merge_notification_trigger(None, form, enabled=True)

    def test_criteria_are_never_invented(self):
        merged = merge_notification_trigger(None, self._form(), enabled=True)
        self.assertEqual(set(merged["match"]), {"title"})

    def test_cleared_criterion_is_removed_from_match(self):
        original = {
            "type": "windows_notification",
            "match": {"title": {"mode": "contains", "value": "Old"}, "body": {"mode": "contains", "value": "x"}},
        }
        merged = merge_notification_trigger(original, self._form(), enabled=True)
        self.assertEqual(set(merged["match"]), {"title"})

    def test_legacy_top_level_criteria_are_replaced_by_match(self):
        original = {"type": "windows_notification", "title": {"mode": "contains", "value": "Old"}}
        merged = merge_notification_trigger(original, self._form(), enabled=True)
        self.assertNotIn("title", merged)
        self.assertIn("title", merged["match"])
        build_notification_match_config(merged)

    def test_deduplication_bool_kept_when_unchanged(self):
        original = {"type": "windows_notification", "deduplication": False}
        form = self._form(dedupe_enabled=False)
        merged = merge_notification_trigger(original, form, enabled=True)
        self.assertIs(merged["deduplication"], False)

    def test_default_deduplication_stays_absent(self):
        merged = merge_notification_trigger(None, self._form(), enabled=True)
        self.assertNotIn("deduplication", merged)
        self.assertNotIn("cooldown_seconds", merged)
        self.assertNotIn("while_running", merged)

    def test_loaded_form_reflects_match_all_and_policy(self):
        state = notification_form_from_trigger({"type": "windows_notification", "match_any": True})
        self.assertTrue(state.match_all)
        state = notification_form_from_trigger(
            {"type": "windows_notification", "match": {"title": "Done"}, "while_running_policy": "restart"}
        )
        self.assertEqual(state.criteria["title"], {"mode": "contains", "value": "Done"})
        self.assertEqual(state.while_running, "restart")


class ScheduleMergeTests(unittest.TestCase):
    def test_time_is_written_where_it_already_is(self):
        original = {"type": "daily", "time": "08:00", "enabled": False}
        merged = merge_schedule_trigger(original, ScheduleFormState("daily", "09:30"), enabled=False)
        self.assertEqual(merged["time"], "09:30")
        self.assertNotIn("config", merged)
        self.assertIs(merged["enabled"], False)

    def test_nested_time_is_updated_in_place(self):
        original = {"type": "daily", "config": {"time": "08:00", "label": "keep"}}
        merged = merge_schedule_trigger(original, ScheduleFormState("daily", "10:15"), enabled=True)
        self.assertEqual(merged["config"], {"time": "10:15", "label": "keep"})
        self.assertNotIn("time", merged)

    def test_new_schedule_keeps_the_editor_shape(self):
        merged = merge_schedule_trigger(None, ScheduleFormState("daily", "07:45"), enabled=True)
        self.assertEqual(merged, {"type": "daily", "config": {"time": "07:45"}})

    def test_existing_date_and_days_are_preserved(self):
        original = {"type": "one_time", "date": "2030-01-02", "time": "06:00"}
        merged = merge_schedule_trigger(original, ScheduleFormState("one_time", "06:30"), enabled=True)
        self.assertEqual(merged["date"], "2030-01-02")
        self.assertEqual(merged["time"], "06:30")

    def test_loaded_nested_time_is_read(self):
        state = schedule_form_from_trigger({"type": "daily", "config": {"time": "11:00"}})
        self.assertEqual(state.time_text, "11:00")


class MergedTriggersPassValidationTests(unittest.TestCase):
    def test_merged_notification_trigger_validates_in_a_workflow(self):
        form = NotificationFormState(criteria={"title": {"mode": "contains", "value": "Done"}}, cooldown=2.0)
        trigger = merge_notification_trigger(None, form, enabled=True)
        config = _config(trigger=trigger, steps=[{"action": "wait", "params": {"seconds": 0.1}}])
        validate_config(config)

    def test_match_all_validates_in_a_workflow(self):
        trigger = merge_notification_trigger(None, NotificationFormState(match_all=True), enabled=True)
        validate_config(_config(trigger=trigger, steps=[{"action": "wait", "params": {"seconds": 0.1}}]))

    def test_match_all_with_criteria_is_rejected_by_backend(self):
        trigger = {
            "type": "windows_notification",
            "match_any": True,
            "match": {"title": {"mode": "contains", "value": "x"}},
        }
        with self.assertRaises(ConfigurationError):
            validate_config(_config(trigger=trigger, steps=[{"action": "wait", "params": {"seconds": 0.1}}]))


if __name__ == "__main__":
    unittest.main()
