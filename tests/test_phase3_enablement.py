"""Phase 3 (GAP-001): workflow enablement is read from and saved to the file.

Covers the shared effective rule, the status wording, and the persisted toggle path.
No listener, engine, or desktop automation is started.
"""

import json
import tempfile
import unittest
from pathlib import Path

from winflow.config.enablement import describe_enablement, is_workflow_enabled
from winflow.config.loader import load_and_validate_config
from winflow.config.writer import save_config, set_workflow_enabled
from winflow.core.errors import ConfigurationError


def _base_config(**overrides):
    config = {
        "schema_version": "1.0",
        "workflow": {"id": "phase3-enable", "name": "Enable Me"},
        "steps": [{"action": "wait", "params": {"seconds": 0.1}}],
    }
    config.update(overrides)
    return config


class EnablementRuleTests(unittest.TestCase):
    def test_defaults_are_enabled(self):
        self.assertTrue(is_workflow_enabled(_base_config()))
        status = describe_enablement(_base_config())
        self.assertTrue(status.configured)
        self.assertTrue(status.eligible)

    def test_workflow_flag_off_blocks(self):
        config = _base_config()
        config["workflow"]["enabled"] = False
        status = describe_enablement(config)
        self.assertFalse(status.configured)
        self.assertFalse(status.eligible)
        self.assertIn("workflow.enabled is false", status.reason)

    def test_settings_flag_off_blocks_even_when_workflow_on(self):
        config = _base_config(settings={"enabled": False})
        status = describe_enablement(config)
        self.assertTrue(status.configured)
        self.assertFalse(status.eligible)
        self.assertIn("settings.enabled is false", status.reason)
        self.assertIn("will not run", status.reason)

    def test_status_never_claims_running(self):
        for config in (
            _base_config(),
            _base_config(settings={"enabled": False}),
        ):
            reason = describe_enablement(config).reason.lower()
            self.assertNotIn("running now", reason)
            self.assertNotIn("is running", reason)

    def test_non_boolean_flag_raises(self):
        config = _base_config()
        config["workflow"]["enabled"] = "yes"
        with self.assertRaises(ConfigurationError):
            is_workflow_enabled(config)


class SetWorkflowEnabledTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name, config):
        path = self.dir / name
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        return path

    def test_toggle_persists_and_survives_reload(self):
        original = _base_config(
            trigger={"type": "windows_notification", "match": {"title": {"mode": "contains", "value": "Done"}}},
            variables={"counter": 2},
        )
        original["workflow"]["version"] = "7"
        original["x_custom"] = {"keep": True}
        path = self._write("flow.json", original)

        set_workflow_enabled(path, False)

        reloaded = load_and_validate_config(path)
        self.assertIs(reloaded["workflow"]["enabled"], False)
        self.assertEqual(reloaded["workflow"]["version"], "7")
        self.assertEqual(reloaded["workflow"]["name"], "Enable Me")
        self.assertEqual(reloaded["variables"], {"counter": 2})
        self.assertEqual(reloaded["x_custom"], {"keep": True})
        self.assertEqual(reloaded["trigger"], original["trigger"])
        self.assertEqual(reloaded["steps"], original["steps"])
        self.assertFalse(describe_enablement(reloaded).eligible)

        set_workflow_enabled(path, True)
        self.assertIs(load_and_validate_config(path)["workflow"]["enabled"], True)

    def test_settings_flag_is_not_changed_by_toggle(self):
        path = self._write("flow.json", _base_config(settings={"enabled": False}))
        set_workflow_enabled(path, True)
        reloaded = load_and_validate_config(path)
        self.assertIs(reloaded["settings"]["enabled"], False)
        self.assertIs(reloaded["workflow"]["enabled"], True)
        self.assertFalse(is_workflow_enabled(reloaded))

    def test_invalid_file_is_refused_and_untouched(self):
        broken = {"workflow": {"id": "bad"}, "steps": [{"action": "not_a_real_action"}]}
        path = self._write("broken.json", broken)
        before = path.read_bytes()
        with self.assertRaises(ConfigurationError):
            set_workflow_enabled(path, False)
        self.assertEqual(path.read_bytes(), before)

    def test_failed_validation_on_save_keeps_existing_file(self):
        path = self._write("keep.json", _base_config())
        before = path.read_bytes()
        invalid = _base_config()
        invalid["workflow"]["enabled"] = "nope"
        with self.assertRaises(ConfigurationError):
            save_config(invalid, path)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual([p.name for p in self.dir.iterdir()], ["keep.json"])

    def test_no_temp_files_left_after_success(self):
        path = self._write("clean.json", _base_config())
        set_workflow_enabled(path, False)
        self.assertEqual([p.name for p in self.dir.iterdir()], ["clean.json"])


if __name__ == "__main__":
    unittest.main()
