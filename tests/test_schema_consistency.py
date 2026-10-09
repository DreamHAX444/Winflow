"""Consistency between the JSON schema, the runtime validator, and the trigger normalizers.

Rules checked:
* Root-level and workflow-level trigger schemas are the same definition (always runs).
* The JSON schema must never reject a trigger that the runtime accepts (it may be
  stricter only where the runtime is also strict). Checked when jsonschema is installed.
* Trigger shapes the runtime rejects must also be rejected by the JSON schema.

The jsonschema checks are skipped, with a visible reason, when the package is not
installed. A skip is never reported as a pass.
"""

import unittest
from datetime import datetime

from winflow.config import validator as validator_module
from winflow.config.schema import WINFLOW_CONFIG_SCHEMA
from winflow.config.validator import validate_config
from winflow.core.errors import SchemaValidationError

NOW = datetime(2026, 10, 9, 10, 0)

try:  # pragma: no cover - availability depends on the environment
    import jsonschema
    HAS_JSONSCHEMA = True
except ImportError:  # pragma: no cover
    jsonschema = None
    HAS_JSONSCHEMA = False

JSONSCHEMA_SKIP_REASON = "jsonschema is not installed; JSON Schema checks were NOT executed"


def _workflow(trigger, root=True):
    config = {
        "schema_version": "1.0",
        "workflow": {"id": "schema-test", "steps": [{"action": "wait", "seconds": 0}]},
    }
    if root:
        config["trigger"] = trigger
    else:
        config["workflow"]["trigger"] = trigger
    return config


def _schema_errors(config):
    validator = jsonschema.Draft202012Validator(WINFLOW_CONFIG_SCHEMA)
    return list(validator.iter_errors(config))


class SharedTriggerSchemaTests(unittest.TestCase):
    def test_root_and_workflow_trigger_schemas_are_the_same_definition(self) -> None:
        root = WINFLOW_CONFIG_SCHEMA["properties"]["trigger"]
        nested = WINFLOW_CONFIG_SCHEMA["properties"]["workflow"]["properties"]["trigger"]
        self.assertIs(root, nested)

    def test_schema_declares_match_any_and_schedule_fields(self) -> None:
        props = WINFLOW_CONFIG_SCHEMA["properties"]["trigger"]["properties"]
        for key in ("match_any", "time", "at", "date", "datetime", "days", "weekdays", "config", "enabled"):
            with self.subTest(key=key):
                self.assertIn(key, props)

    def test_runtime_accepts_both_locations_identically_for_schedules(self) -> None:
        trigger = {"type": "daily", "config": {"time": 14}}
        self.assertTrue(validate_config(_workflow(trigger, root=True), now=NOW))
        self.assertTrue(validate_config(_workflow(trigger, root=False), now=NOW))


@unittest.skipUnless(HAS_JSONSCHEMA, JSONSCHEMA_SKIP_REASON)
class JsonSchemaExecutionTests(unittest.TestCase):
    def test_jsonschema_path_is_active_in_this_run(self) -> None:
        self.assertTrue(validator_module.HAS_JSONSCHEMA)

    def test_schema_document_is_itself_valid_draft_2020_12(self) -> None:
        jsonschema.Draft202012Validator.check_schema(WINFLOW_CONFIG_SCHEMA)

    def test_runtime_valid_triggers_are_not_rejected_by_the_schema(self) -> None:
        valid_triggers = [
            {"type": "daily", "time": "14:30"},
            {"type": "daily", "time": " 14:30 "},
            {"type": "daily", "time": 0},
            {"type": "daily", "time": 23},
            {"type": "daily", "time": "", "config": {"time": "14:30"}},      # blank counts as absent
            {"type": "daily", "config": {"time": 14}},
            {"type": "weekly", "time": "10:00", "days": ["mon", "fri"]},
            {"type": "weekly", "time": "10:00", "days": "mon,fri"},
            {"type": "weekly", "time": "10:00", "weekdays": [0, "friday"]},
            {"type": "weekly", "time": "10:00", "days": 4},
            {"type": "one_time", "datetime": "2999-01-01T09:00", "enabled": True},
            {"type": "one_time", "date": "2999-01-01", "time": "09:00"},
            {"type": "one_time", "datetime": "2026-10-08T09:00", "enabled": False},
            {"type": "startup"},
            {"type": "windows_notification", "match": {"application": "Example"}},
            {"type": "windows_notification", "match_any": True},
            {"type": "windows_notification", "match": {"title": {"mode": "regex", "value": "x"}, "case_sensitive": True}},
        ]
        for trigger in valid_triggers:
            for root in (True, False):
                with self.subTest(trigger=trigger, root=root):
                    config = _workflow(trigger, root=root)
                    self.assertEqual(_schema_errors(config), [])
                    self.assertTrue(validate_config(config, now=NOW))

    def test_shape_invalid_triggers_are_rejected_by_both_layers(self) -> None:
        invalid_triggers = [
            {"type": "daily", "time": 14.5},
            {"type": "daily", "time": True},
            {"type": "daily", "time": 24},
            {"type": "daily", "time": "9am"},
            {"type": "daily", "config": "14:30"},
            {"type": "weekly", "time": "10:00", "days": [9]},
            {"type": "weekly", "time": "10:00", "days": [True]},
            {"type": "windows_notification", "case_sensitive": True, "title": "x"},
            {"type": "windows_notification", "match_any": "yes"},
            {"type": "daily", "time": "09:00", "enabled": "false"},
        ]
        for trigger in invalid_triggers:
            for root in (True, False):
                with self.subTest(trigger=trigger, root=root):
                    config = _workflow(trigger, root=root)
                    self.assertTrue(_schema_errors(config), "schema should reject this shape")
                    with self.assertRaises(SchemaValidationError):
                        validate_config(config, now=NOW)

    def test_semantic_rules_are_enforced_by_runtime_even_when_schema_is_satisfied(self) -> None:
        # Shape is valid for the schema, but the runtime rules still reject these.
        semantic_invalid = [
            {"type": "daily"},
            {"type": "weekly", "time": "10:00"},
            {"type": "windows_notification"},
            {"type": "windows_notification", "match": {}},
            {"type": "one_time", "date": "2026-10-08", "time": "09:00", "enabled": True},
        ]
        for trigger in semantic_invalid:
            with self.subTest(trigger=trigger):
                self.assertEqual(_schema_errors(_workflow(trigger)), [])
                with self.assertRaises(SchemaValidationError):
                    validate_config(_workflow(trigger), now=NOW)

    def test_shipped_samples_pass_both_layers(self) -> None:
        import json
        from pathlib import Path

        for path in sorted((Path(__file__).resolve().parents[1] / "winflow" / "workflows").glob("*.json")):
            with self.subTest(sample=path.name):
                config = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(_schema_errors(config), [])
                self.assertTrue(validate_config(config, now=NOW))


if __name__ == "__main__":
    unittest.main()
