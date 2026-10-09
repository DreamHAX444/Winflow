"""Phase 3 UI tests: enablement toggle, trigger editor, coordinate editing, Back flow.

These tests need PySide6 with a working Qt platform. They run on the offscreen
platform, never start the location or window pickers, and never send real mouse
or keyboard input. If Qt cannot initialize, the whole module is SKIPPED with the
import or platform error, and that skip must not be reported as a pass.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_QT_ERROR: str | None = None
try:
    from PySide6.QtWidgets import QApplication, QMessageBox as _RealMessageBox

    from winflow.config.loader import load_and_validate_config
    from winflow.core.errors import ConfigurationError
    from winflow.config.trigger_form import TriggerFormError
    from winflow.ui.app import MainWindow
    from winflow.ui.views import workflow_editor as editor_module
    from winflow.ui.views import workflows_page as page_module
    from winflow.ui.views.trigger_editor import TriggerEditor
    from winflow.ui.views.workflow_editor import _ACTION_DEFAULTS, WorkflowEditor, WorkflowSettingsDialog
    from winflow.ui.views.workflows_page import WorkflowsPage
except Exception as exc:  # pragma: no cover - depends on the host
    _QT_ERROR = f"{type(exc).__name__}: {exc}"

_APP = None


def setUpModule():
    global _APP
    if _QT_ERROR is None:
        _APP = QApplication.instance() or QApplication([])
        _FakePrompt.ButtonRole = _RealMessageBox.ButtonRole


def _workflow(name, **extra):
    config = {
        "schema_version": "1.0",
        "workflow": {"id": name.lower().replace(" ", "-"), "name": name},
        "steps": [{"action": "wait", "params": {"seconds": 0.1}}],
    }
    config.update(extra)
    return config


class _Recorder:
    """Replaces QMessageBox.warning/critical so tests do not block on dialogs."""

    def __init__(self):
        self.messages = []

    def warning(self, _parent, title, text, *_args, **_kwargs):
        self.messages.append((title, text))
        return 0

    critical = warning


class _FakePrompt:
    """Stands in for QMessageBox in the Back prompt, returning a scripted choice."""

    ButtonRole = None  # set in setUpModule once Qt has loaded
    choice = "Cancel"
    instances: list = []

    def __init__(self, _parent=None):
        self._buttons = {}
        self._clicked = None
        self.default = None
        _FakePrompt.instances.append(self)

    def setWindowTitle(self, *_):
        pass

    def setText(self, *_):
        pass

    def setInformativeText(self, *_):
        pass

    def addButton(self, text, _role):
        button = object()
        self._buttons[text] = button
        return button

    def setDefaultButton(self, *_):
        pass

    def exec(self):
        self._clicked = self._buttons[_FakePrompt.choice]
        return 0

    def clickedButton(self):
        return self._clicked


@unittest.skipIf(_QT_ERROR is not None, f"Qt could not initialize: {_QT_ERROR}")
class WorkflowsPageEnablementTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.recorder = _Recorder()
        self._patch = mock.patch.object(page_module, "QMessageBox", self.recorder)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def _write(self, name, config):
        path = self.dir / name
        path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        return path

    def _page(self):
        page = WorkflowsPage(self.dir)
        page.show()
        return page

    def _toggle(self, page, row):
        item = page.workflow_list.item(row)
        return page.workflow_list.itemWidget(item).findChild(page_module.QCheckBox, "workflowEnabledToggle")

    def test_toggle_reflects_file_not_memory(self):
        config = _workflow("Alpha")
        config["workflow"]["enabled"] = False
        self._write("a.json", config)
        page = self._page()
        toggle = self._toggle(page, 0)
        self.assertFalse(toggle.isChecked())
        self.assertEqual(toggle.text(), "OFF")

    def test_toggle_saves_and_survives_restart(self):
        path = self._write("b.json", _workflow("Bravo", variables={"n": 1}))
        page = self._page()
        self._toggle(page, 0).toggle()
        self.assertIs(load_and_validate_config(path)["workflow"]["enabled"], False)
        self.assertEqual(load_and_validate_config(path)["variables"], {"n": 1})

        restarted = self._page()
        restarted_toggle = self._toggle(restarted, 0)
        self.assertFalse(restarted_toggle.isChecked())
        self.assertEqual(restarted_toggle.text(), "OFF")

    def test_settings_flag_shows_reason_and_keeps_configured_state(self):
        config = _workflow("Charlie", settings={"enabled": False})
        self._write("c.json", config)
        page = self._page()
        entry = page.entries[0]
        self.assertTrue(entry.enabled)
        self.assertFalse(entry.eligible)
        self.assertIn("settings.enabled is false", entry.status)
        self.assertIn("will not run", entry.status)

    def test_failed_save_keeps_prior_state_and_file(self):
        path = self._write("d.json", _workflow("Delta"))
        before = path.read_bytes()
        page = self._page()
        toggle = self._toggle(page, 0)
        with mock.patch.object(
            page_module, "set_workflow_enabled", side_effect=ConfigurationError("disk full")
        ):
            toggle.toggle()
        self.assertTrue(toggle.isChecked())
        self.assertEqual(toggle.text(), "ON")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(self.recorder.messages), 1)
        self.assertIn("disk full", self.recorder.messages[0][1])

    def test_invalid_file_has_disabled_toggle(self):
        self._write("e.json", {"workflow": {"id": "x"}, "steps": [{"action": "bogus_action"}]})
        page = self._page()
        self.assertFalse(self._toggle(page, 0).isEnabled())
        self.assertIn("Cannot read", page.entries[0].status)

    def test_update_entry_matches_by_path_not_row(self):
        self._write("first.json", _workflow("First"))
        second = self._write("second.json", _workflow("Second"))
        page = self._page()
        self.assertEqual([entry.name for entry in page.entries], ["First", "Second"])
        page.workflow_list.setCurrentRow(0)
        renamed = _workflow("Second Renamed")
        page.update_workflow_entry(renamed, second.resolve())
        self.assertEqual(page.entries[0].name, "First")
        self.assertEqual(page.entries[1].name, "Second Renamed")


@unittest.skipIf(_QT_ERROR is not None, f"Qt could not initialize: {_QT_ERROR}")
class TriggerEditorTests(unittest.TestCase):
    def test_disabled_trigger_loads_unchecked_and_stays_disabled(self):
        editor = TriggerEditor({"type": "windows_notification", "enabled": False, "match": {"title": "Done"}})
        self.assertFalse(editor.enabled_check.isChecked())
        self.assertIs(editor.get_config()["enabled"], False)

    def test_unknown_fields_and_regex_survive_a_save(self):
        original = {
            "type": "windows_notification",
            "match": {"title": {"mode": "regex", "value": "^Done\\d$"}},
            "x_extra": ["keep"],
            "cooldown_seconds": 4,
        }
        editor = TriggerEditor(original)
        self.assertEqual(editor._criteria_modes["title"].currentText(), "regex")
        saved = editor.get_config()
        self.assertEqual(saved["x_extra"], ["keep"])
        self.assertEqual(saved["match"]["title"], {"mode": "regex", "value": "^Done\\d$"})
        self.assertEqual(saved["cooldown_seconds"], 4.0)

    def test_empty_matcher_is_blocked(self):
        editor = TriggerEditor({"type": "windows_notification", "match": {"title": "Done"}})
        editor._criteria_values["title"].setText("")
        with self.assertRaises(TriggerFormError):
            editor.get_config()

    def test_match_all_writes_match_any_and_clears_criteria(self):
        editor = TriggerEditor(None)
        editor.type_combo.setCurrentIndex(1)
        editor.match_all.setChecked(True)
        self.assertFalse(editor.match_all_warning.isHidden())
        self.assertFalse(editor._criteria_values["title"].isEnabled())
        self.assertIs(editor.get_config()["match_any"], True)

    def test_match_all_with_criteria_is_refused(self):
        editor = TriggerEditor(None)
        editor.type_combo.setCurrentIndex(1)
        editor._criteria_values["title"].setText("Done")
        editor.match_all.setChecked(True)
        with self.assertRaises(TriggerFormError):
            editor.get_config()

    def test_regex_mode_is_offered(self):
        editor = TriggerEditor(None)
        modes = [editor._criteria_modes["application"].itemText(i) for i in range(editor._criteria_modes["application"].count())]
        self.assertIn("regex", modes)

    def test_schedule_time_is_saved_in_nested_location(self):
        editor = TriggerEditor({"type": "daily", "config": {"time": "08:00"}, "enabled": False})
        editor.sched_time.setText("09:45")
        saved = editor.get_config()
        self.assertEqual(saved["config"]["time"], "09:45")
        self.assertIs(saved["enabled"], False)

    def test_none_removes_trigger(self):
        editor = TriggerEditor({"type": "daily", "config": {"time": "08:00"}})
        editor.type_combo.setCurrentIndex(0)
        self.assertIsNone(editor.get_config())


@unittest.skipIf(_QT_ERROR is not None, f"Qt could not initialize: {_QT_ERROR}")
class SettingsDialogTests(unittest.TestCase):
    def setUp(self):
        self.recorder = _Recorder()
        self._patch = mock.patch.object(editor_module, "QMessageBox", self.recorder)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()

    def test_conflict_is_reported_and_not_rewritten(self):
        config = _workflow("Conflict", trigger={"type": "daily", "config": {"time": "08:00"}})
        config["workflow"]["trigger"] = {"type": "windows_notification", "match": {"title": "x"}}
        before = json.dumps(config, sort_keys=True)
        dialog = WorkflowSettingsDialog(config)
        self.assertFalse(dialog.trigger_editor.isEnabled())
        self.assertFalse(dialog.trigger_note.isHidden())
        dialog.accept()
        self.assertEqual(json.dumps(config, sort_keys=True), before)

    def test_nested_trigger_edit_stays_nested(self):
        config = _workflow("Nested")
        config["workflow"]["trigger"] = {"type": "daily", "config": {"time": "08:00"}}
        dialog = WorkflowSettingsDialog(config)
        dialog.trigger_editor.sched_time.setText("10:30")
        dialog.accept()
        self.assertNotIn("trigger", config)
        self.assertEqual(config["workflow"]["trigger"]["config"]["time"], "10:30")

    def test_invalid_matcher_keeps_dialog_and_config(self):
        config = _workflow("Blocked", trigger={"type": "windows_notification", "match": {"title": "x"}})
        before = json.dumps(config, sort_keys=True)
        dialog = WorkflowSettingsDialog(config)
        dialog.trigger_editor._criteria_values["title"].setText("")
        dialog.accept()
        self.assertEqual(json.dumps(config, sort_keys=True), before)
        self.assertEqual(self.recorder.messages[-1][0], "Trigger not saved")


@unittest.skipIf(_QT_ERROR is not None, f"Qt could not initialize: {_QT_ERROR}")
class WorkflowEditorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.recorder = _Recorder()
        self._patch = mock.patch.object(editor_module, "QMessageBox", self.recorder)
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        self._tmp.cleanup()

    def _editor(self, steps, path=None, extra=None):
        config = _workflow("Editor", steps=steps)
        if extra:
            config.update(extra)
        path = path or (self.dir / "editor.json")
        if not path.exists():
            path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        editor = WorkflowEditor()
        editor.set_workflow(load_and_validate_config(path), path)
        editor.step_list.setCurrentRow(0)
        return editor, path

    def test_click_defaults_have_no_coordinates(self):
        for action in ("click", "double_click", "right_click", "move"):
            with self.subTest(action=action):
                self.assertNotIn("x", _ACTION_DEFAULTS[action])
                self.assertNotIn("y", _ACTION_DEFAULTS[action])
        self.assertEqual(_ACTION_DEFAULTS["click"]["button"], "left")

    def test_new_click_step_shows_blank_coordinates_and_is_clean(self):
        editor, _ = self._editor([{"action": "click", "params": {}}])
        self.assertEqual(editor._coordinate_editors[0].text(), "")
        self.assertEqual(editor._coordinate_editors[1].text(), "")
        self.assertFalse(editor.has_unsaved_changes())

    def test_partial_pair_blocks_save_and_leaves_file(self):
        editor, path = self._editor([{"action": "click", "params": {}}])
        before = path.read_bytes()
        editor._coordinate_editors[0].setText("25")
        self.assertFalse(editor.save_workflow())
        self.assertEqual(path.read_bytes(), before)
        self.assertIn("Enter both X and Y", self.recorder.messages[-1][1])

    def test_explicit_zero_is_saved_and_blank_clears_keys(self):
        editor, path = self._editor([{"action": "click", "params": {"x": 5, "y": 6, "button": "left"}}])
        editor._coordinate_editors[0].setText("0")
        editor._coordinate_editors[1].setText("0")
        self.assertTrue(editor.save_workflow())
        self.assertEqual(load_and_validate_config(path)["steps"][0]["params"], {"x": 0, "y": 0, "button": "left"})

        editor._coordinate_editors[0].setText("")
        editor._coordinate_editors[1].setText("")
        self.assertTrue(editor.save_workflow())
        self.assertEqual(load_and_validate_config(path)["steps"][0]["params"], {"button": "left"})

    def test_pick_location_fills_text_fields_without_starting_picker(self):
        editor, _ = self._editor([{"action": "click", "params": {}}])
        editor._set_picked_location(640, 360)
        self.assertEqual(editor._coordinate_editors[0].text(), "640")
        self.assertEqual(editor._coordinate_editors[1].text(), "360")
        self.assertTrue(editor.has_unsaved_changes())

    def test_move_requires_both_coordinates(self):
        editor, _ = self._editor([{"action": "move", "params": {"duration_ms": 0}}])
        editor._coordinate_editors[0].setText("")
        editor._coordinate_editors[1].setText("")
        self.assertFalse(editor.save_workflow())
        self.assertIn("Move Mouse needs both X and Y", self.recorder.messages[-1][1])

    def test_steps_are_not_duplicated_when_workflow_steps_exist(self):
        path = self.dir / "nested.json"
        config = {
            "schema_version": "1.0",
            "workflow": {"id": "nested", "name": "Nested", "steps": [{"action": "wait", "params": {"seconds": 0.1}}]},
        }
        path.write_text(json.dumps(config), encoding="utf-8")
        editor = WorkflowEditor()
        editor.set_workflow(load_and_validate_config(path), path)
        self.assertNotIn("steps", editor.config)
        self.assertEqual(len(editor._steps), 1)
        self.assertTrue(editor.save_workflow())
        saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("steps", saved)
        self.assertEqual(len(saved["workflow"]["steps"]), 1)

    def test_successful_save_returns_true_and_clears_unsaved_state(self):
        editor, path = self._editor([{"action": "wait", "params": {"seconds": 0.1}}])
        editor._parameter_widgets["seconds"].setValue(0.7)
        self.assertTrue(editor.has_unsaved_changes())
        self.assertTrue(editor.save_workflow())
        self.assertFalse(editor.has_unsaved_changes())
        self.assertIn("Saved to", editor.save_state.text())

    def test_failed_save_does_not_claim_success(self):
        editor, path = self._editor([{"action": "wait", "params": {"seconds": 0.1}}])
        before = path.read_bytes()
        editor.config["workflow"]["enabled"] = "maybe"
        self.assertFalse(editor.save_workflow())
        self.assertEqual(path.read_bytes(), before)
        self.assertNotIn("Saved to", editor.save_state.text())
        self.assertTrue(editor.has_unsaved_changes())


@unittest.skipIf(_QT_ERROR is not None, f"Qt could not initialize: {_QT_ERROR}")
class MainWindowFlowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.recorder = _Recorder()
        self._patches = [
            mock.patch.object(editor_module, "QMessageBox", self.recorder),
            mock.patch("winflow.ui.app.QMessageBox", _FakePrompt),
            mock.patch.object(page_module, "QMessageBox", self.recorder),
        ]
        for patch in self._patches:
            patch.start()
        _FakePrompt.instances = []
        self.window = MainWindow()
        self.window.workflows_page.workflow_directory = self.dir
        self.window.workflows_page.refresh_workflows()

    def tearDown(self):
        for patch in self._patches:
            patch.stop()
        self._tmp.cleanup()

    def _make(self, name, config=None):
        path = self.dir / name
        path.write_text(json.dumps(config or _workflow(name.split(".")[0]), indent=2), encoding="utf-8")
        return path

    def _open(self, path):
        self.window._open_workflow_editor(load_and_validate_config(path), path)

    def test_settings_is_not_offered(self):
        labels = [button.text() for button in self.window.navigation_buttons]
        self.assertEqual(labels, ["Workflows"])

    def test_back_clean_returns_and_refreshes_by_path(self):
        first = self._make("one.json")
        second = self._make("two.json")
        self.window.workflows_page.refresh_workflows()
        self._open(second)
        self.window._request_back()
        self.assertIs(self.window.pages.currentWidget(), self.window.workflows_page)
        self.assertEqual([entry.path.name for entry in self.window.workflows_page.entries], ["one.json", "two.json"])
        self.assertFalse(_FakePrompt.instances)

    def test_back_with_changes_cancel_stays_in_editor(self):
        path = self._make("cancel.json")
        self._open(path)
        self.window.workflow_editor._mark_dirty("param:seconds")
        self.window.workflow_editor.config["workflow"]["name"] = "Changed"
        _FakePrompt.choice = "Cancel"
        self.window._request_back()
        self.assertIs(self.window.pages.currentWidget(), self.window.workflow_editor)

    def test_back_with_changes_discard_leaves_file_and_list_unchanged(self):
        path = self._make("discard.json")
        self.window.workflows_page.refresh_workflows()
        self._open(path)
        self.window.workflow_editor.config["workflow"]["name"] = "Not Saved"
        _FakePrompt.choice = "Discard"
        self.window._request_back()
        self.assertIs(self.window.pages.currentWidget(), self.window.workflows_page)
        self.assertEqual(self.window.workflows_page.entries[0].name, "discard")
        self.assertEqual(load_and_validate_config(path)["workflow"]["name"], "discard")

    def test_back_with_changes_save_updates_entry_by_path(self):
        first = self._make("alpha.json")
        second = self._make("beta.json")
        self.window.workflows_page.refresh_workflows()
        self._open(second)
        self.window.workflow_editor.config["workflow"]["name"] = "Beta Renamed"
        _FakePrompt.choice = "Save"
        self.window._request_back()
        self.assertIs(self.window.pages.currentWidget(), self.window.workflows_page)
        self.assertEqual(load_and_validate_config(second)["workflow"]["name"], "Beta Renamed")
        names = {entry.path.name: entry.name for entry in self.window.workflows_page.entries}
        self.assertEqual(names["alpha.json"], "alpha")
        self.assertEqual(names["beta.json"], "Beta Renamed")

    def test_back_save_failure_keeps_editor_open(self):
        path = self._make("broken-save.json")
        self._open(path)
        self.window.workflow_editor.config["workflow"]["enabled"] = "maybe"
        _FakePrompt.choice = "Save"
        self.window._request_back()
        self.assertIs(self.window.pages.currentWidget(), self.window.workflow_editor)
        self.assertEqual(load_and_validate_config(path)["workflow"]["name"], "broken-save")


if __name__ == "__main__":
    unittest.main()
