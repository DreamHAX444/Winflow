import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from winflow.config.loader import load_and_validate_config
from winflow.config.validator import validate_config
from winflow.core.errors import SchemaValidationError


class ConfigurationCompatibilityTests(unittest.TestCase):
    def _config(self, **overrides):
        config = {
            "schema_version": "1.0",
            "workflow": {
                "id": "config-test",
                "steps": [{"action": "wait", "seconds": 0}],
            },
        }
        config.update(overrides)
        return config

    def test_runtime_rejects_infinite_loops_at_root_and_workflow_scope(self) -> None:
        for config in (
            self._config(loop={"count": "infinite"}),
            self._config(workflow={
                "id": "config-test",
                "loop": {"count": "infinite"},
                "steps": [{"action": "wait", "seconds": 0}],
            }),
        ):
            with self.subTest(config=config), self.assertRaisesRegex(
                SchemaValidationError, "not supported"
            ):
                validate_config(config)

    def test_present_null_loop_blocks_are_rejected_without_jsonschema(self) -> None:
        for config in (
            self._config(loop=None),
            self._config(
                workflow={
                    "id": "config-test",
                    "loop": None,
                    "steps": [{"action": "wait", "seconds": 0}],
                }
            ),
        ):
            with self.subTest(config=config), self.assertRaisesRegex(
                SchemaValidationError, "loop.*dictionary"
            ):
                validate_config(config)

    def test_legacy_trigger_policy_aliases_are_validated_and_supported(self) -> None:
        self.assertTrue(
            validate_config(
                self._config(
                    trigger={"type": "manual", "while_running_policy": "run_concurrently"}
                )
            )
        )
        with self.assertRaisesRegex(SchemaValidationError, "while_running_policy"):
            validate_config(
                self._config(
                    trigger={"type": "manual", "while_running_policy": "parallel"}
                )
            )

    def test_validate_only_returns_failure_for_infinite_loop_config(self) -> None:
        config = self._config(loop={"count": "infinite"})
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "infinite.json"
            path.write_text(json.dumps(config), encoding="utf-8")
            repo_root = Path(__file__).resolve().parents[1]
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "winflow.main",
                    "--config",
                    str(path),
                    "--validate-only",
                ],
                cwd=repo_root,
                capture_output=True,
                text=True,
                timeout=20,
            )
        self.assertEqual(result.returncode, 1)
        self.assertIn("not supported", result.stdout + result.stderr)
        self.assertNotIn("Configuration validated. Exiting", result.stdout)

    def test_hotkey_and_safety_settings_are_schema_checked(self) -> None:
        self.assertTrue(
            validate_config(
                self._config(
                    settings={
                        "enabled": False,
                        "global_timeout_seconds": 4,
                        "emergency_stop_hotkey": "CTRL+F8",
                    }
                )
            )
        )
        with self.assertRaisesRegex(SchemaValidationError, "emergency_stop_hotkey"):
            validate_config(
                self._config(
                    settings={"emergency_stop_hotkey": "CTRL+ALT"}
                )
            )
        with self.assertRaisesRegex(SchemaValidationError, "settings.enabled"):
            validate_config(self._config(settings={"enabled": "false"}))

    def test_sample_workflows_still_validate_after_compatibility_changes(self) -> None:
        workflows_dir = Path(__file__).resolve().parents[1] / "winflow" / "workflows"
        for path in sorted(workflows_dir.glob("*.json")):
            with self.subTest(path=path.name):
                load_and_validate_config(path)


if __name__ == "__main__":
    unittest.main()
