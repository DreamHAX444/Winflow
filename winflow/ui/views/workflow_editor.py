"""Workflow step editor for registered desktop actions."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from winflow.config.step_coordinates import (
    COORDINATE_ACTIONS,
    CURSOR_ACTIONS,
    CURSOR_HELP_TEXT,
    CoordinateError,
    apply_coordinate_change,
    check_coordinate_presence,
    coordinate_change_for,
)
from winflow.config.trigger_form import TriggerFormError
from winflow.config.trigger_location import CONFLICT, NESTED, resolve_trigger_location, write_trigger
from winflow.config.writer import save_config
from winflow.core.errors import ConfigurationError
from winflow.engine.action_registry import get_action_registry
from winflow.ui.design_tokens import CONTROL_WIDTH, SPACING, style_combo_box_popup
from winflow.ui.location_picker import LocationPicker
from winflow.ui.window_picker import WindowPicker
from winflow.ui.views.trigger_editor import TriggerEditor


_ACTION_DEFAULTS: dict[str, dict[str, Any]] = {
    # Coordinates are intentionally absent: an omitted x/y means "current cursor" for
    # click actions, and Move Mouse needs a point chosen with Pick Location or typed in.
    "move": {"duration_ms": 0},
    "click": {"button": "left", "clicks": 1, "interval_ms": 50},
    "double_click": {"button": "left"},
    "right_click": {},
    "scroll": {"amount": -1},
    "key": {"key": ""},
    "hotkey": {"keys": ["CTRL", "C"]},
    "type": {"text": "", "interval_ms": 0},
    "clipboard_read": {"variable_name": ""},
    "clipboard_write": {"text": ""},
    "clipboard_clear": {},
    "wait": {"seconds": 1.0},
    "focus_window": {"target": {}},
    "get_active_window": {"variable_name": ""},
    "launch_application": {"path": "", "arguments": []},
    "set_variable": {"name": "", "value": ""},
}

_PARAMETER_LABELS = {
    "x": "X",
    "y": "Y",
    "button": "Button",
    "clicks": "Click count",
    "interval_ms": "Click interval (ms)",
    "duration_ms": "Duration (ms)",
    "amount": "Amount",
    "key": "Key",
    "keys": "Keys (JSON list)",
    "text": "Text",
    "seconds": "Seconds",
    "milliseconds": "Milliseconds",
    "target": "Target",
    "variable_name": "Variable name",
    "path": "Application path",
    "arguments": "Arguments (JSON list)",
    "name": "Variable name",
    "value": "Value",
}

_FAILURE_POLICIES = {
    "stop": "Stop",
    "retry": "Retry",
    "skip": "Skip",
    "continue": "Continue",
    "restart_workflow": "Restart workflow",
}

_VERIFICATION_TYPES = {
    "window_exists": "Window exists",
    "window_active": "Window is active",
    "process_running": "Process is running",
}

_TARGET_FIELDS = {
    "title_contains": "Window title contains",
    "title_exact": "Window title is",
    "process_name": "Application",
    "executable_name": "Application file",
    "hwnd": "Window handle (HWND)",
}

_MOUSE_LOCATION_ACTIONS = {
    "click": "Mouse Click",
    "double_click": "Double Click",
    "right_click": "Right Click",
    "move": "Move Mouse",
}

_WINDOW_ACTION_LABELS = {
    "focus_window": "Focus Window",
    "get_active_window": "Get Active Window",
}


class _NoWheelSpinBox(QSpinBox):
    def wheelEvent(self, event: Any) -> None:
        event.ignore()


class _NoWheelDoubleSpinBox(QDoubleSpinBox):
    def wheelEvent(self, event: Any) -> None:
        event.ignore()


class _NoWheelComboBox(QComboBox):
    def wheelEvent(self, event: Any) -> None:
        event.ignore()


class WorkflowSettingsDialog(QDialog):
    def __init__(self, config: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Workflow Settings")
        self.setObjectName("workflowSettingsDialog")
        self.resize(600, 500)
        self.config = config
        self._location = resolve_trigger_location(config)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        if self._location.kind == CONFLICT:
            note_text = self._location.message
        elif self._location.kind == NESTED:
            note_text = "This trigger is stored under 'workflow.trigger'. Saving keeps it there."
        else:
            note_text = ""
        self.trigger_note = QLabel(note_text)
        self.trigger_note.setObjectName("triggerWarning")
        self.trigger_note.setWordWrap(True)
        self.trigger_note.setVisible(bool(note_text))
        layout.addWidget(self.trigger_note)

        self.trigger_editor = TriggerEditor(self._location.trigger)
        if self._location.kind == CONFLICT:
            self.trigger_editor.setEnabled(False)
        layout.addWidget(self.trigger_editor, 1)

        vars_group = QGroupBox("Variables (JSON)")
        vars_layout = QVBoxLayout(vars_group)
        vars_layout.setContentsMargins(SPACING["sm"], SPACING["xl"], SPACING["sm"], SPACING["sm"])
        self.variables_edit = QPlainTextEdit()
        self.variables_edit.setPlainText(
            json.dumps(self.config.get("variables", {}), indent=2, ensure_ascii=False)
        )
        self.variables_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.variables_edit.setMaximumHeight(150)
        vars_layout.addWidget(self.variables_edit)
        layout.addWidget(vars_group)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn:
            ok_btn.setObjectName("primaryButton")
        cancel_btn = buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn:
            cancel_btn.setObjectName("secondaryButton")

        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:
        # Validate everything first; only then change the configuration.
        new_trigger: dict[str, Any] | None = self._location.trigger
        if self._location.kind != CONFLICT:
            try:
                new_trigger = self.trigger_editor.get_config()
            except TriggerFormError as exc:
                QMessageBox.warning(self, "Trigger not saved", str(exc))
                return

        try:
            vars_text = self.variables_edit.toPlainText().strip()
            variables_data = json.loads(vars_text) if vars_text else {}
        except json.JSONDecodeError as exc:
            QMessageBox.warning(self, "Invalid Variables JSON", str(exc))
            return

        if not isinstance(variables_data, dict):
            QMessageBox.warning(self, "Invalid Variables JSON", "Variables must be a JSON object.")
            return

        if self._location.kind != CONFLICT and new_trigger != self._location.trigger:
            try:
                write_trigger(self.config, new_trigger)
            except ValueError as exc:
                QMessageBox.warning(self, "Trigger not saved", str(exc))
                return

        if variables_data:
            self.config["variables"] = variables_data
        else:
            self.config.pop("variables", None)

        super().accept()


class WorkflowEditor(QWidget):
    back_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.config: dict[str, Any] | None = None
        self.workflow_path: Path | None = None
        self._steps: list[dict[str, Any]] = []
        self._selected_step: int | None = None
        self._building_fields = False
        self._splitter_initialized = False
        self._parameter_widgets: dict[str, QWidget] = {}
        self._parameter_types: dict[str, type] = {}
        self._advanced_widgets: dict[str, QWidget] = {}
        self._target_fields: dict[str, dict[str, QLineEdit]] = {}
        self._target_field_labels: dict[str, dict[str, QLabel]] = {}
        self._target_summaries: dict[str, QLabel] = {}
        self._target_process_labels: dict[str, QLabel] = {}
        self._target_details: dict[str, dict[str, Any]] = {}
        self._target_inspector_labels: dict[
            str, dict[str, tuple[QLabel, QLabel]]
        ] = {}
        self._target_modes: dict[str, tuple[QRadioButton, QRadioButton]] = {}
        self._target_present: dict[str, bool] = {}
        self._setting_picked_window = False
        self._current_action = ""
        self._dirty_fields: set[str] = set()
        self._coordinate_editors: tuple[QLineEdit, QLineEdit] | None = None
        self._saved_snapshot: str | None = None
        self._last_error = ""
        self._location_picker = LocationPicker(self)
        self._location_picker.location_picked.connect(self._set_picked_location)
        self._window_picker = WindowPicker(self)
        self._window_picker.window_picked.connect(self._set_picked_window)
        self._setup_ui()

    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(SPACING["md"])

        header = QHBoxLayout()
        back = QPushButton("Back to workflows")
        back.setObjectName("secondaryButton")
        back.clicked.connect(self.back_requested.emit)
        header.addWidget(back)
        header.addStretch()
        self.workflow_name = QLabel("Workflow")
        self.workflow_name.setObjectName("pageTitle")
        header.addWidget(self.workflow_name)
        header.addStretch()
        self.settings_button = QPushButton("Settings (Trigger && Vars)")
        self.settings_button.setObjectName("secondaryButton")
        self.settings_button.clicked.connect(self.edit_settings)
        header.addWidget(self.settings_button)

        self.save_button = QPushButton("Save")
        self.save_button.setObjectName("primaryButton")
        self.save_button.clicked.connect(self.save_workflow)
        header.addWidget(self.save_button)
        root.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("editorSplitter")
        self.editor_splitter = splitter

        step_panel = QWidget()
        step_layout = QVBoxLayout(step_panel)
        step_layout.setContentsMargins(0, 0, SPACING["md"], 0)
        step_heading = QLabel("Steps")
        step_heading.setObjectName("sectionLabel")
        step_layout.addWidget(step_heading)
        self.step_list = QListWidget()
        self.step_list.setObjectName("editorStepList")
        self.step_list.setAccessibleName("Workflow steps")
        self.step_list.currentRowChanged.connect(self._select_step)
        step_layout.addWidget(self.step_list, 1)

        step_controls = QHBoxLayout()
        self.add_step_button = QPushButton("Add step")
        self.add_step_button.setObjectName("secondaryButton")
        self.add_step_button.clicked.connect(self.add_step)
        step_controls.addWidget(self.add_step_button)
        self.remove_step_button = QPushButton("Remove")
        self.remove_step_button.setObjectName("secondaryButton")
        self.remove_step_button.clicked.connect(self.remove_step)
        step_controls.addWidget(self.remove_step_button)
        step_layout.addLayout(step_controls)

        ordering = QHBoxLayout()
        self.move_up_button = QPushButton("Move up")
        self.move_up_button.setObjectName("secondaryButton")
        self.move_up_button.clicked.connect(lambda: self.move_step(-1))
        self.move_down_button = QPushButton("Move down")
        self.move_down_button.setObjectName("secondaryButton")
        self.move_down_button.clicked.connect(lambda: self.move_step(1))
        ordering.addWidget(self.move_up_button)
        ordering.addWidget(self.move_down_button)
        step_layout.addLayout(ordering)
        splitter.addWidget(step_panel)

        properties_scroll = QScrollArea()
        properties_scroll.setObjectName("propertiesScroll")
        properties_scroll.setWidgetResizable(True)
        properties_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        properties = QWidget()
        properties.setObjectName("propertiesContent")
        self.properties_layout = QVBoxLayout(properties)
        self.properties_layout.setContentsMargins(SPACING["md"], 0, 0, 0)
        self.properties_layout.setSpacing(SPACING["md"])
        properties_scroll.setWidget(properties)
        splitter.addWidget(properties_scroll)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

        self.save_state = QLabel("")
        self.save_state.setObjectName("saveState")
        root.addWidget(self.save_state)
        self._update_step_controls()

    def showEvent(self, event: Any) -> None:
        super().showEvent(event)
        if not self._splitter_initialized:
            QTimer.singleShot(0, self._initialize_splitter)

    def _initialize_splitter(self) -> None:
        if not self.isVisible() or self._splitter_initialized:
            return
        width = self.editor_splitter.width()
        if width <= 0:
            return
        first_pane = width // 3
        self.editor_splitter.setSizes([first_pane, width - first_pane])
        self._splitter_initialized = True

    def edit_settings(self) -> None:
        if self.config is None:
            return
        dialog = WorkflowSettingsDialog(self.config, self)
        dialog.exec()

    def set_workflow(
        self,
        config: dict[str, Any],
        path: Path | None = None,
    ) -> None:
        self.config = deepcopy(config)
        self.workflow_path = path
        workflow = self.config.get("workflow", {})
        self.workflow_name.setText(
            str(workflow.get("name") or workflow.get("id") or "Workflow")
        )
        self._steps = self._get_steps(self.config)
        self._selected_step = None
        self._refresh_step_list()
        self._saved_snapshot = self._snapshot(self.config)

    @staticmethod
    def _snapshot(config: dict[str, Any] | None) -> str:
        return json.dumps(config, sort_keys=True, ensure_ascii=False, default=str)

    def has_unsaved_changes(self) -> bool:
        """True when the editor differs from the last loaded or saved state.

        Field edits are copied into the configuration first, the same way Save does.
        If a field is invalid, the state is reported as unsaved so Back cannot discard it silently.
        """
        if self.config is None:
            return False
        if not self._store_visible_fields():
            return True
        return self._snapshot(self.config) != self._saved_snapshot

    @staticmethod
    def _get_steps(config: dict[str, Any]) -> list[dict[str, Any]]:
        """Return the live steps list, attached to the configuration it came from.

        Steps are read from ``workflow.steps`` when present, otherwise from the root
        ``steps``. A new list is attached to the root only when neither exists, so the
        editor never duplicates steps into a second location.
        """
        workflow = config.get("workflow")
        if isinstance(workflow, dict) and isinstance(workflow.get("steps"), list):
            return workflow["steps"]
        if isinstance(config.get("steps"), list):
            return config["steps"]
        steps: list[dict[str, Any]] = []
        config["steps"] = steps
        return steps

    def _refresh_step_list(self, selected: int = 0) -> None:
        self.step_list.blockSignals(True)
        self.step_list.clear()
        for index, step in enumerate(self._steps):
            action = str(step.get("action", "unknown"))
            label = str(step.get("name") or action.replace("_", " ").title())
            item = QListWidgetItem(f"{index + 1}.  {label}")
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.step_list.addItem(item)
        self.step_list.blockSignals(False)
        self._selected_step = None
        if self._steps:
            selected = max(0, min(selected, len(self._steps) - 1))
            self.step_list.setCurrentRow(selected)
        else:
            self._clear_properties()
            self._update_step_controls()

    def _select_step(self, row: int) -> None:
        if row < 0 or row >= len(self._steps):
            self._selected_step = None
            self._clear_properties()
            self._update_step_controls()
            return
        if self._selected_step is not None and not self._store_visible_fields():
            self.step_list.blockSignals(True)
            self.step_list.setCurrentRow(self._selected_step)
            self.step_list.blockSignals(False)
            QMessageBox.warning(
                self,
                "Invalid value",
                self._last_error or "A step property contains invalid JSON or a value of the wrong type.",
            )
            return
        self._selected_step = row
        self._show_step_properties(self._steps[row])
        self._update_step_controls()

    def _clear_properties(self) -> None:
        self._building_fields = True
        self._clear_layout(self.properties_layout)
        self._parameter_widgets.clear()
        self._parameter_types.clear()
        self._advanced_widgets.clear()
        self._target_fields.clear()
        self._target_field_labels.clear()
        self._target_summaries.clear()
        self._target_process_labels.clear()
        self._target_details.clear()
        self._target_inspector_labels.clear()
        self._target_modes.clear()
        self._target_present.clear()
        self._dirty_fields.clear()
        self.properties_layout.addWidget(QLabel("Select a step to edit its properties."))
        self.properties_layout.addStretch()
        self._building_fields = False

    def _show_step_properties(self, step: dict[str, Any]) -> None:
        if self._location_picker.is_active:
            self._location_picker.cancel()
        if self._window_picker.is_active:
            self._window_picker.cancel()
        self._building_fields = True
        self._clear_layout(self.properties_layout)
        self._parameter_widgets.clear()
        self._parameter_types.clear()
        self._advanced_widgets.clear()
        self._target_fields.clear()
        self._target_field_labels.clear()
        self._target_summaries.clear()
        self._target_process_labels.clear()
        self._target_details.clear()
        self._target_inspector_labels.clear()
        self._target_modes.clear()
        self._target_present.clear()
        self._dirty_fields.clear()
        self._coordinate_editors = None

        params = step.get("params", {})
        if not isinstance(params, dict):
            params = {}

        action = str(step.get("action", ""))
        self._current_action = action
        heading = QLabel(
            _MOUSE_LOCATION_ACTIONS.get(
                action, _WINDOW_ACTION_LABELS.get(action, "Properties")
            )
        )
        heading.setObjectName("sectionLabel")
        self.properties_layout.addWidget(heading)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(SPACING["md"])
        form.setVerticalSpacing(SPACING["sm"])

        if action == "get_active_window":
            active_window_help = QLabel(
                "Stores the active window details in this variable when the workflow runs."
            )
            active_window_help.setObjectName("helperText")
            active_window_help.setWordWrap(True)
            form.addRow("", active_window_help)

        mouse_action = action in _MOUSE_LOCATION_ACTIONS
        if mouse_action:
            location_row = QWidget()
            location_layout = QHBoxLayout(location_row)
            location_layout.setContentsMargins(0, 0, 0, 0)
            location_layout.setSpacing(SPACING["sm"])
            coordinate_editors: list[QLineEdit] = []
            for key in ("x", "y"):
                # Blank means "not set". Coordinates are never shown as a placeholder 0.
                raw_value = params.get(key)
                editor = QLineEdit("" if raw_value is None else str(raw_value))
                editor.setPlaceholderText("Current cursor" if action in CURSOR_ACTIONS else "Required")
                editor.setFixedWidth(112)
                editor.setObjectName(f"location{key.upper()}Field")
                editor.setAccessibleName(f"Location {key.upper()}")
                label = self._field_label(f"{key.upper()}:")
                coordinate = QWidget()
                coordinate_layout = QHBoxLayout(coordinate)
                coordinate_layout.setContentsMargins(0, 0, 0, 0)
                coordinate_layout.setSpacing(SPACING["xs"])
                coordinate_layout.addWidget(label)
                coordinate_layout.addWidget(editor)
                location_layout.addWidget(coordinate)
                self._track_parameter_editor(key, editor)
                coordinate_editors.append(editor)
            self._coordinate_editors = (coordinate_editors[0], coordinate_editors[1])
            location_layout.addStretch(1)
            form.addRow(self._field_label("Location"), location_row)

            location_help = QLabel(
                CURSOR_HELP_TEXT
                if action in CURSOR_ACTIONS
                else "Move Mouse needs both X and Y. Use Pick Location to choose a point."
            )
            location_help.setObjectName("helperText")
            location_help.setWordWrap(True)
            form.addRow("", location_help)

            self.pick_location_button = QPushButton("Pick Location")
            self.pick_location_button.setObjectName("secondaryButton")
            self.pick_location_button.setAccessibleName("Pick a location on screen")
            self.pick_location_button.clicked.connect(
                self._location_picker.start
            )
            pick_row = QHBoxLayout()
            pick_row.setContentsMargins(0, 0, 0, 0)
            pick_row.addWidget(self.pick_location_button)
            pick_row.addStretch(1)
            form.addRow("", pick_row)

        advanced_parameters: list[tuple[str, QWidget]] = []
        for key, value in params.items():
            if mouse_action and key in {"x", "y"}:
                continue
            editor = self._make_value_editor(key, value)
            if mouse_action and key in {"clicks", "interval_ms", "duration_ms"}:
                editor.setFixedWidth(112)
            label = QLabel(_PARAMETER_LABELS.get(key, key.replace("_", " ").title()))
            label.setObjectName("fieldLabel")
            if mouse_action and key in {"duration_ms", "interval_ms"}:
                advanced_parameters.append((key, editor))
            else:
                form.addRow(label, editor)
            self._parameter_widgets[key] = editor
            self._parameter_types[key] = type(value)
            self._track_parameter_editor(key, editor)

        if not params and not mouse_action:
            empty = QLabel("This action has no editable parameters.")
            empty.setObjectName("helperText")
            form.addRow(empty)
        self.properties_layout.addLayout(form)

        self.advanced_toggle = QToolButton()
        self.advanced_toggle.setObjectName("advancedToggle")
        self.advanced_toggle.setText("Advanced")
        self.advanced_toggle.setCheckable(True)
        self.advanced_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.advanced_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.advanced_toggle.setAccessibleName("Advanced step properties")
        self.advanced_toggle.toggled.connect(self._toggle_advanced)
        self.properties_layout.addWidget(self.advanced_toggle)

        self.advanced_content = QWidget()
        advanced_layout = QVBoxLayout(self.advanced_content)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        advanced_layout.setSpacing(SPACING["md"])

        if advanced_parameters:
            self._add_divider(advanced_layout)
            advanced_layout.addWidget(self._section_title("Mouse behavior"))
            advanced_form = self._make_advanced_form()
            for key, editor in advanced_parameters:
                advanced_form.addRow(
                    self._field_label(_PARAMETER_LABELS.get(key, key.title())),
                    editor,
                )
            advanced_layout.addLayout(advanced_form)

        name_form = self._make_advanced_form()
        name_value = step.get("name", "")
        name_editor = QLineEdit(name_value if isinstance(name_value, str) else "")
        name_editor.setAccessibleName("Optional step name")
        name_editor.textChanged.connect(lambda: self._mark_dirty("name"))
        name_form.addRow(self._field_label("Name (optional)"), name_editor)
        name_helper = QLabel("A short label to help identify this step.")
        name_helper.setObjectName("helperText")
        name_form.addRow("", name_helper)
        advanced_layout.addLayout(name_form)
        self._advanced_widgets["name"] = name_editor

        if isinstance(step.get("failure"), dict) or "failure" not in step:
            self._add_divider(advanced_layout)
            self._build_failure_section(advanced_layout, step.get("failure", {}))
        else:
            self._add_divider(advanced_layout)
            self._add_preserved_value_notice(
                advanced_layout, "Failure settings are preserved but cannot be edited."
            )

        if isinstance(step.get("verify"), dict) or "verify" not in step:
            self._add_divider(advanced_layout)
            self._build_verification_section(advanced_layout, step.get("verify", {}))
        else:
            self._add_divider(advanced_layout)
            self._add_preserved_value_notice(
                advanced_layout, "Verification settings are preserved but cannot be edited."
            )

        if (
            "timeout_seconds" in step
            and isinstance(step["timeout_seconds"], (int, float))
            and not isinstance(step["timeout_seconds"], bool)
        ):
            self._add_divider(advanced_layout)
            timeout_form = self._make_advanced_form()
            timeout_editor = self._make_seconds_editor(step["timeout_seconds"])
            timeout_editor.valueChanged.connect(
                lambda: self._mark_dirty("timeout_seconds")
            )
            timeout_form.addRow(self._field_label("Step timeout"), timeout_editor)
            advanced_layout.addWidget(self._section_title("Timing"))
            advanced_layout.addLayout(timeout_form)
            self._advanced_widgets["timeout_seconds"] = timeout_editor

        self.advanced_content.setVisible(False)
        self.properties_layout.addWidget(self.advanced_content)
        self.properties_layout.addStretch()
        self._building_fields = False

    def _set_picked_location(self, x: int, y: int) -> None:
        if self._coordinate_editors is None:
            return
        x_editor, y_editor = self._coordinate_editors
        for key, editor, value in (("x", x_editor, x), ("y", y_editor, y)):
            editor.setText(str(int(value)))
            self._mark_dirty(f"param:{key}")

    @classmethod
    def _clear_layout(cls, layout: QLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child_layout = item.layout()
            if widget is not None:
                widget.deleteLater()
            elif child_layout is not None:
                cls._clear_layout(child_layout)
                child_layout.deleteLater()

    def _make_value_editor(self, key: str, value: Any) -> QWidget:
        if key == "button":
            combo = _NoWheelComboBox()
            combo.setObjectName("mouseButtonSelector")
            combo.setFixedWidth(CONTROL_WIDTH["mouse_button"])
            style_combo_box_popup(combo)
            for button in ("left", "right", "middle"):
                combo.addItem(button.title(), button)
            index = combo.findData(str(value).lower())
            if index < 0:
                combo.addItem(str(value), str(value))
                index = combo.count() - 1
            combo.setCurrentIndex(index)
            combo.setAccessibleName("Mouse button")
            combo.view().setMinimumWidth(CONTROL_WIDTH["mouse_button"])
            return combo
        if isinstance(value, bool):
            combo = _NoWheelComboBox()
            style_combo_box_popup(combo)
            combo.addItem("True", True)
            combo.addItem("False", False)
            combo.setCurrentIndex(0 if value else 1)
            return combo
        if isinstance(value, int):
            spin = _NoWheelSpinBox()
            spin.setRange(-2147483647, 2147483647)
            spin.setValue(value)
            return spin
        if isinstance(value, float):
            spin = _NoWheelDoubleSpinBox()
            spin.setRange(-1000000000, 1000000000)
            spin.setDecimals(4)
            spin.setValue(value)
            return spin
        if isinstance(value, (dict, list)):
            if key == "target":
                if isinstance(value, dict):
                    return self._make_target_widget(
                        value,
                        prefix="param:target",
                        fields=(
                            "title_contains",
                            "title_exact",
                            "process_name",
                            "executable_name",
                            "hwnd",
                        ),
                    )
                notice = QLabel("This target format is preserved but cannot be edited here.")
                notice.setObjectName("helperText")
                return notice
            line = QLineEdit(json.dumps(value, ensure_ascii=False))
            line.setAccessibleName(_PARAMETER_LABELS.get(key, key))
            return line
        line = QLineEdit("" if value is None else str(value))
        line.setAccessibleName(_PARAMETER_LABELS.get(key, key))
        return line

    def _track_parameter_editor(self, key: str, editor: QWidget) -> None:
        if key == "target" and f"param:target" in self._target_fields:
            return
        dirty_key = f"param:{key}"
        if isinstance(editor, QComboBox):
            editor.currentIndexChanged.connect(
                lambda: self._mark_dirty(dirty_key)
            )
        elif isinstance(editor, QSpinBox):
            editor.valueChanged.connect(lambda: self._mark_dirty(dirty_key))
        elif isinstance(editor, QDoubleSpinBox):
            editor.valueChanged.connect(lambda: self._mark_dirty(dirty_key))
        elif isinstance(editor, QLineEdit):
            editor.textChanged.connect(lambda: self._mark_dirty(dirty_key))

    def _mark_dirty(self, key: str) -> None:
        if not self._building_fields:
            self._dirty_fields.add(key)

    @staticmethod
    def _field_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    @staticmethod
    def _section_title(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("advancedSectionTitle")
        return label

    @staticmethod
    def _make_advanced_form() -> QFormLayout:
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(SPACING["md"])
        form.setVerticalSpacing(SPACING["sm"])
        return form

    @staticmethod
    def _add_divider(layout: QVBoxLayout) -> None:
        divider = QFrame()
        divider.setObjectName("advancedDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setFrameShadow(QFrame.Shadow.Plain)
        layout.addWidget(divider)

    @staticmethod
    def _add_preserved_value_notice(layout: QVBoxLayout, text: str) -> None:
        notice = QLabel(text)
        notice.setObjectName("helperText")
        layout.addWidget(notice)

    @staticmethod
    def _make_seconds_editor(value: int | float) -> QDoubleSpinBox:
        editor = _NoWheelDoubleSpinBox()
        editor.setRange(-1_000_000_000, 1_000_000_000)
        editor.setDecimals(4)
        editor.setValue(float(value) if not isinstance(value, bool) else 0.0)
        editor.setSuffix(" seconds")
        return editor

    def _build_failure_section(
        self,
        layout: QVBoxLayout,
        failure: dict[str, Any],
    ) -> None:
        layout.addWidget(self._section_title("Failure handling"))
        form = self._make_advanced_form()

        policy_value = failure.get("policy", "stop")
        policy = policy_value.lower() if isinstance(policy_value, str) else None
        policy_editor = _NoWheelComboBox()
        style_combo_box_popup(policy_editor)
        for value, label in _FAILURE_POLICIES.items():
            policy_editor.addItem(label, value)
        policy_index = policy_editor.findData(policy)
        if policy_index < 0:
            label = policy_value if isinstance(policy_value, str) else "Unsupported policy (preserved)"
            policy_editor.addItem(label, policy)
            policy_index = policy_editor.count() - 1
            policy_editor.setEnabled(False)
        policy_editor.setCurrentIndex(policy_index)
        policy_editor.currentIndexChanged.connect(
            lambda: self._mark_dirty("failure:policy")
        )
        form.addRow(self._field_label("Policy"), policy_editor)
        self._advanced_widgets["failure:policy"] = policy_editor

        attempts = self._make_integer_editor(failure.get("attempts", 1), minimum=1)
        attempts.setAccessibleName("Retry attempts")
        attempts.valueChanged.connect(lambda: self._mark_dirty("failure:attempts"))
        delay_value = failure.get("delay_seconds", 0.1)
        if not isinstance(delay_value, (int, float)) or isinstance(delay_value, bool):
            delay_value = 0.1
        delay = self._make_seconds_editor(delay_value)
        delay.setAccessibleName("Retry delay")
        delay.valueChanged.connect(lambda: self._mark_dirty("failure:delay_seconds"))
        attempts_label = self._field_label("Attempts")
        delay_label = self._field_label("Delay")
        form.addRow(attempts_label, attempts)
        form.addRow(delay_label, delay)
        self._advanced_widgets["failure:attempts"] = attempts
        self._advanced_widgets["failure:delay_seconds"] = delay

        max_restarts = self._make_integer_editor(
            failure.get("max_restarts", 3), minimum=1
        )
        max_restarts.setAccessibleName("Maximum workflow restarts")
        max_restarts.valueChanged.connect(
            lambda: self._mark_dirty("failure:max_restarts")
        )
        restart_label = self._field_label("Maximum restarts")
        form.addRow(restart_label, max_restarts)
        self._advanced_widgets["failure:max_restarts"] = max_restarts
        layout.addLayout(form)

        def update_policy_fields() -> None:
            selected_policy = policy_editor.currentData()
            show_retry = selected_policy == "retry"
            attempts_label.setVisible(show_retry)
            attempts.setVisible(show_retry)
            delay_label.setVisible(show_retry)
            delay.setVisible(show_retry)
            show_restart = selected_policy == "restart_workflow"
            restart_label.setVisible(show_restart)
            max_restarts.setVisible(show_restart)

        policy_editor.currentIndexChanged.connect(update_policy_fields)
        update_policy_fields()

    def _build_verification_section(
        self,
        layout: QVBoxLayout,
        verification: dict[str, Any],
    ) -> None:
        layout.addWidget(self._section_title("Verification"))
        form = self._make_advanced_form()

        enabled = _NoWheelComboBox()
        style_combo_box_popup(enabled)
        enabled.addItem("Disabled", False)
        enabled.addItem("Enabled", True)
        is_enabled = bool(verification)
        enabled.setCurrentIndex(1 if is_enabled else 0)
        enabled.currentIndexChanged.connect(
            lambda: self._mark_dirty("verify:enabled")
        )
        form.addRow(self._field_label("Status"), enabled)
        layout.addLayout(form)
        self._advanced_widgets["verify:enabled"] = enabled

        detail_widget = QWidget()
        detail_layout = QVBoxLayout(detail_widget)
        detail_layout.setContentsMargins(0, 0, 0, 0)
        detail_layout.setSpacing(SPACING["sm"])
        detail_form = self._make_advanced_form()

        verification_type_value = verification.get("type", "window_exists")
        verification_type = (
            verification_type_value
            if isinstance(verification_type_value, str)
            else ""
        )
        type_editor = _NoWheelComboBox()
        style_combo_box_popup(type_editor)
        for value, label in _VERIFICATION_TYPES.items():
            type_editor.addItem(label, value)
        type_index = type_editor.findData(verification_type)
        if type_index < 0:
            label = (
                verification_type
                if verification_type
                else "Unsupported type (preserved)"
            )
            type_editor.addItem(
                label,
                verification_type_value if isinstance(verification_type_value, str) else None,
            )
            type_index = type_editor.count() - 1
            type_editor.setEnabled(False)
        type_editor.setCurrentIndex(type_index)
        type_editor.currentIndexChanged.connect(
            lambda: self._mark_dirty("verify:type")
        )
        detail_form.addRow(self._field_label("Type"), type_editor)
        self._advanced_widgets["verify:type"] = type_editor

        target = self._verification_target(verification)
        target_widget = self._make_target_widget(
            target,
            prefix="verify",
            fields=("title_contains", "title_exact", "process_name", "executable_name"),
        )
        target_label = self._field_label("Target")
        detail_form.addRow(target_label, target_widget)
        timeout_value = verification.get("timeout_seconds", 2.0)
        timeout = self._make_seconds_editor(
            timeout_value
            if isinstance(timeout_value, (int, float))
            and not isinstance(timeout_value, bool)
            else 2.0
        )
        timeout.setAccessibleName("Verification timeout")
        timeout.valueChanged.connect(
            lambda: self._mark_dirty("verify:timeout_seconds")
        )
        timeout_label = self._field_label("Timeout")
        detail_form.addRow(timeout_label, timeout)
        self._advanced_widgets["verify:timeout_seconds"] = timeout
        detail_layout.addLayout(detail_form)
        layout.addWidget(detail_widget)

        def update_verification_fields() -> None:
            detail_widget.setVisible(bool(enabled.currentData()))
            target_fields = self._target_fields.get("verify", {})
            target_labels = self._target_field_labels.get("verify", {})
            selected_type = type_editor.currentData()
            supported_type = (
                isinstance(selected_type, str)
                and selected_type in _VERIFICATION_TYPES
            )
            target_widget.setVisible(supported_type)
            target_label.setVisible(supported_type)
            timeout.setVisible(supported_type)
            timeout_label.setVisible(supported_type)
            process_only = selected_type == "process_running"
            for key in ("title_contains", "title_exact"):
                if key in target_fields:
                    target_fields[key].setVisible(supported_type and not process_only)
                    target_labels[key].setVisible(supported_type and not process_only)
            if "verify" in self._target_summaries:
                visible_fields = (
                    ("process_name", "executable_name")
                    if process_only
                    else tuple(target_fields)
                )
                self._update_target_summary("verify", visible_fields)

        enabled.currentIndexChanged.connect(update_verification_fields)
        type_editor.currentIndexChanged.connect(update_verification_fields)
        update_verification_fields()

    @staticmethod
    def _verification_target(verification: dict[str, Any]) -> dict[str, Any]:
        for key in ("target", "params"):
            target = verification.get(key)
            if isinstance(target, dict):
                return target
        return {
            key: verification[key]
            for key in _TARGET_FIELDS
            if key in verification
        }

    @staticmethod
    def _make_integer_editor(value: Any, minimum: int = 0) -> QSpinBox:
        editor = _NoWheelSpinBox()
        editor.setRange(minimum, 2_147_483_647)
        try:
            if isinstance(value, bool):
                raise ValueError("Boolean is not an integer setting")
            editor.setValue(int(value))
        except (TypeError, ValueError, OverflowError):
            editor.setValue(minimum)
        return editor

    def _make_target_widget(
        self,
        target: dict[str, Any],
        prefix: str,
        fields: tuple[str, ...],
    ) -> QWidget:
        if prefix == "param:target" and self._current_action == "focus_window":
            return self._make_window_target_widget(target, prefix, fields)

        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING["xs"])

        summary = QLabel()
        summary.setObjectName("targetSummary")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        field_form = self._make_advanced_form()
        editors: dict[str, QLineEdit] = {}
        for key in fields:
            value = target.get(key, "")
            editor = QLineEdit(value if isinstance(value, str) else "")
            editor.setAccessibleName(_TARGET_FIELDS[key])
            editor.textChanged.connect(
                lambda _text, field=key: self._mark_dirty(
                    f"{prefix}:target:{field}"
                )
            )
            field_form.addRow(self._field_label(_TARGET_FIELDS[key]), editor)
            editors[key] = editor
        layout.addLayout(field_form)

        def update_summary() -> None:
            self._update_target_summary(prefix, fields)

        for editor in editors.values():
            editor.textChanged.connect(update_summary)
        update_summary()
        self._target_fields[prefix] = editors
        self._target_summaries[prefix] = summary
        self._target_field_labels[prefix] = {
            key: field_form.labelForField(editor)
            for key, editor in editors.items()
        }
        self._update_target_summary(prefix, fields)
        return widget

    def _make_window_target_widget(
        self,
        target: dict[str, Any],
        prefix: str,
        fields: tuple[str, ...],
    ) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING["sm"])

        summary = QLabel()
        summary.setObjectName("targetSummary")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        process_label = QLabel()
        process_label.setObjectName("helperText")
        layout.addWidget(process_label)

        mode_form = QFormLayout()
        mode_form.setLabelAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        mode_form.setHorizontalSpacing(SPACING["md"])
        mode_form.setVerticalSpacing(SPACING["xs"])
        mode_row = QWidget()
        mode_layout = QVBoxLayout(mode_row)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(SPACING["xs"])
        specific_radio = QRadioButton("Specific window")
        specific_radio.setAccessibleName("Target a specific window")
        application_radio = QRadioButton("Any window for this application")
        application_radio.setAccessibleName("Target any window for this application")
        specific_hint = QLabel("Pick a window to target it separately from other app windows.")
        specific_hint.setObjectName("helperText")
        specific_hint.setWordWrap(True)
        has_specific_criteria = any(
            target.get(key) for key in ("title_exact", "title_contains", "hwnd")
        )
        specific_radio.setChecked(has_specific_criteria or not target)
        application_radio.setChecked(bool(target) and not has_specific_criteria)
        mode_layout.addWidget(specific_radio)
        mode_layout.addWidget(specific_hint)
        mode_layout.addWidget(application_radio)
        mode_form.addRow(self._field_label("Target mode"), mode_row)
        layout.addLayout(mode_form)
        self._target_modes[prefix] = (specific_radio, application_radio)
        specific_radio.toggled.connect(
            lambda checked: self._mark_dirty(f"{prefix}:mode") if checked else None
        )
        application_radio.toggled.connect(
            lambda checked: self._target_application_mode_changed(prefix)
            if checked
            else None
        )

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        self.pick_window_button = QPushButton("Pick Window")
        self.pick_window_button.setObjectName("secondaryButton")
        self.pick_window_button.setAccessibleName("Pick a window on screen")
        self.pick_window_button.clicked.connect(self._window_picker.start)
        self.clear_target_button = QPushButton("Clear")
        self.clear_target_button.setObjectName("secondaryButton")
        self.clear_target_button.setAccessibleName("Clear window target")
        self.clear_target_button.clicked.connect(
            lambda: self._clear_window_target(prefix)
        )
        self.clear_target_button.setEnabled(bool(target))
        button_row.addWidget(self.pick_window_button)
        button_row.addWidget(self.clear_target_button)
        button_row.addStretch()
        layout.addLayout(button_row)

        details_toggle = QToolButton()
        details_toggle.setObjectName("advancedToggle")
        details_toggle.setText("Technical details")
        details_toggle.setCheckable(True)
        details_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        details_toggle.setArrowType(Qt.ArrowType.RightArrow)
        details_toggle.setAccessibleName("Window technical details")
        layout.addWidget(details_toggle)
        details_content = QWidget()
        details_layout = QVBoxLayout(details_content)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(SPACING["sm"])
        details_form = self._make_advanced_form()
        editors: dict[str, QLineEdit] = {}
        for key in fields:
            value = target.get(key, "")
            editor = QLineEdit("" if value is None else str(value))
            editor.setAccessibleName(_TARGET_FIELDS.get(key, key))
            editor.textChanged.connect(
                lambda _text, field=key: self._window_target_field_changed(
                    prefix, field, fields
                )
            )
            details_form.addRow(
                self._field_label(_TARGET_FIELDS.get(key, key)), editor
            )
            editors[key] = editor
        details_layout.addLayout(details_form)

        details = {
            "process_id": target.get("process_id", target.get("pid")),
            "executable": target.get("executable"),
        }
        self._target_details[prefix] = details
        self._target_present[prefix] = bool(target)
        self._target_inspector_labels[prefix] = {
            "process_id": self._add_inspector_detail(
                details_form, "Process ID", details.get("process_id")
            ),
            "executable": self._add_inspector_detail(
                details_form, "Executable path", details.get("executable")
            ),
        }

        tab_form = self._make_advanced_form()
        tab_status = QLabel(
            "Not available in this version. Target the window itself instead."
        )
        tab_status.setObjectName("helperText")
        tab_status.setWordWrap(True)
        tab_form.addRow(self._field_label("Tab targeting"), tab_status)
        details_layout.addLayout(tab_form)
        layout.addWidget(details_content)
        details_content.setVisible(False)

        def toggle_details(expanded: bool) -> None:
            details_content.setVisible(expanded)
            details_toggle.setArrowType(
                Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
            )

        details_toggle.toggled.connect(toggle_details)

        self._target_fields[prefix] = editors
        self._target_summaries[prefix] = summary
        self._target_process_labels[prefix] = process_label
        self._target_field_labels[prefix] = {
            key: details_form.labelForField(editor)
            for key, editor in editors.items()
        }
        def update_specific_hint() -> None:
            specific = specific_radio.isChecked()
            has_discriminator = any(
                editors[key].text().strip()
                for key in ("title_exact", "title_contains", "hwnd")
                if key in editors
            )
            specific_hint.setVisible(specific and not has_discriminator)

        specific_radio.toggled.connect(update_specific_hint)
        for editor in editors.values():
            editor.textChanged.connect(update_specific_hint)
        update_specific_hint()
        self._update_target_summary(prefix, fields)
        return widget

    @staticmethod
    def _add_inspector_detail(
        form: QFormLayout, label: str, value: Any
    ) -> tuple[QLabel, QLabel]:
        field_label = WorkflowEditor._field_label(label)
        value_label = QLabel(str(value))
        value_label.setObjectName("helperText")
        value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        field_label.setVisible(value is not None and value != "")
        value_label.setVisible(value is not None and value != "")
        form.addRow(field_label, value_label)
        return field_label, value_label

    def _clear_window_target(self, prefix: str) -> None:
        for key, editor in self._target_fields.get(prefix, {}).items():
            editor.clear()
            self._dirty_fields.add(f"{prefix}:target:{key}")
        self._dirty_fields.add(f"{prefix}:clear")
        self._dirty_fields.add(f"{prefix}:recaptured")
        self._target_details[prefix] = {}
        for field_label, value_label in self._target_inspector_labels.get(prefix, {}).values():
            field_label.hide()
            value_label.hide()
        self._target_present[prefix] = False
        self.clear_target_button.setEnabled(False)

    def _window_target_field_changed(
        self,
        prefix: str,
        field: str,
        fields: tuple[str, ...],
    ) -> None:
        self._mark_dirty(f"{prefix}:target:{field}")
        if not self._setting_picked_window:
            self._clear_inspector_details(prefix)
            self._dirty_fields.add(f"{prefix}:recaptured")
        self._update_target_summary(prefix, fields)

    def _target_application_mode_changed(self, prefix: str) -> None:
        self._mark_dirty(f"{prefix}:mode")
        self._clear_inspector_details(prefix)
        self._dirty_fields.add(f"{prefix}:recaptured")

    def _clear_inspector_details(self, prefix: str) -> None:
        self._target_details[prefix] = {}
        for field_label, value_label in self._target_inspector_labels.get(prefix, {}).values():
            field_label.hide()
            value_label.hide()

    def _update_target_summary(
        self,
        prefix: str,
        fields: tuple[str, ...],
    ) -> None:
        summary = self._target_summaries.get(prefix)
        editors = self._target_fields.get(prefix)
        if summary is None or editors is None:
            return
        values = {
            key: editors[key].text().strip()
            for key in fields
            if key in editors and editors[key].text().strip()
        }
        title = values.get("title_exact") or values.get("title_contains", "")
        process = values.get("process_name") or values.get("executable_name", "")
        application = Path(process).stem.replace("_", " ").title()
        if application and title:
            summary.setText(f"{application} — {title}")
        elif application:
            summary.setText(application)
        elif title:
            summary.setText(title)
        elif values.get("hwnd"):
            summary.setText("Selected window")
        else:
            summary.setText("No window selected")
        process_label = self._target_process_labels.get(prefix)
        if process_label is not None:
            process_label.setText(process)
            process_label.setVisible(bool(process))
        clear_button = getattr(self, "clear_target_button", None)
        if clear_button is not None and prefix == "param:target":
            if values:
                self._target_present[prefix] = True
            clear_button.setEnabled(bool(values) or self._target_present.get(prefix, False))

    def _set_picked_window(self, details: dict[str, Any]) -> None:
        prefix = "param:target"
        editors = self._target_fields.get(prefix)
        if editors is None:
            return
        process_name = details.get("process_name", "")
        values = {
            "title_exact": details.get("title", ""),
            "title_contains": "",
            "process_name": process_name,
            "executable_name": process_name,
            "hwnd": str(details.get("hwnd", "")),
        }
        self._setting_picked_window = True
        try:
            for key, editor in editors.items():
                value = values.get(key, "")
                editor.setText("" if value is None else str(value))
                self._dirty_fields.add(f"{prefix}:target:{key}")
        finally:
            self._setting_picked_window = False
        specific_radio, _ = self._target_modes[prefix]
        specific_radio.setChecked(True)
        self._target_details[prefix] = {
            "process_id": details.get("process_id"),
            "executable": details.get("executable"),
        }
        self._dirty_fields.add(f"{prefix}:recaptured")
        for key, labels in self._target_inspector_labels.get(prefix, {}).items():
            value = self._target_details[prefix].get(key)
            field_label, value_label = labels
            value_label.setText("" if value is None else str(value))
            field_label.setVisible(value is not None and value != "")
            value_label.setVisible(value is not None and value != "")
        self._target_present[prefix] = True
        self.clear_target_button.setEnabled(True)

    def _toggle_advanced(self, expanded: bool) -> None:
        self.advanced_content.setVisible(expanded)
        self.advanced_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )

    def _store_visible_fields(self) -> bool:
        if self._selected_step is None:
            return True
        step = self._steps[self._selected_step]
        original_params = step.get("params", {})
        self._last_error = ""
        try:
            updated_params = deepcopy(original_params) if isinstance(original_params, dict) else {}
            for key, editor in self._parameter_widgets.items():
                dirty_key = f"param:{key}"
                if self._coordinate_editors is not None and key in ("x", "y"):
                    continue  # handled together below so a partial pair is refused
                if key == "target":
                    if any(dirty.startswith("param:target:") for dirty in self._dirty_fields):
                        if "param:target:clear" in self._dirty_fields:
                            updated_params.pop("target", None)
                            continue
                        old_target = original_params.get("target", {})
                        if isinstance(old_target, dict):
                            target = self._apply_target_edits(
                                old_target, "param:target"
                            )
                            target_mode = self._target_modes.get("param:target")
                            if target_mode is not None and target_mode[1].isChecked():
                                for field in (
                                    "title_contains",
                                    "title_exact",
                                    "hwnd",
                                    "process_id",
                                    "pid",
                                    "executable",
                                ):
                                    target.pop(field, None)
                            if "param:target:recaptured" in self._dirty_fields:
                                for field in ("process_id", "pid", "executable"):
                                    target.pop(field, None)
                            if target:
                                updated_params["target"] = target
                            else:
                                updated_params.pop("target", None)
                    continue
                if dirty_key not in self._dirty_fields:
                    continue
                value = self._read_editor(editor, self._parameter_types[key])
                if isinstance(original_params, dict) and value == original_params.get(key):
                    continue
                updated_params[key] = value
            if self._coordinate_editors is not None:
                action_name = str(step.get("action", ""))
                if "param:x" in self._dirty_fields or "param:y" in self._dirty_fields:
                    x_editor, y_editor = self._coordinate_editors
                    change = coordinate_change_for(action_name, x_editor.text(), y_editor.text())
                    apply_coordinate_change(updated_params, change)
                else:
                    check_coordinate_presence(action_name, updated_params)

            if updated_params != original_params and (
                isinstance(original_params, dict) or updated_params
            ):
                step["params"] = updated_params

            if "name" in self._dirty_fields:
                name_editor = self._advanced_widgets.get("name")
                if isinstance(name_editor, QLineEdit):
                    name = name_editor.text()
                    if name:
                        step["name"] = name
                    else:
                        step.pop("name", None)

            self._store_failure_settings(step)
            self._store_verification_settings(step)

            timeout_editor = self._advanced_widgets.get("timeout_seconds")
            if (
                "timeout_seconds" in self._dirty_fields
                and isinstance(timeout_editor, QDoubleSpinBox)
                and timeout_editor.value() != step.get("timeout_seconds")
            ):
                step["timeout_seconds"] = timeout_editor.value()
        except json.JSONDecodeError:
            self._last_error = "A step property contains invalid JSON."
            return False
        except (ValueError, CoordinateError) as exc:
            self._last_error = str(exc) or "A step property has the wrong type."
            return False
        self._refresh_selected_label()
        return True

    def _store_failure_settings(self, step: dict[str, Any]) -> None:
        dirty = {
            field for field in self._dirty_fields if field.startswith("failure:")
        }
        if not dirty:
            return
        current = step.get("failure")
        if "failure" in step and not isinstance(current, dict):
            return
        failure = deepcopy(current) if isinstance(current, dict) else {}

        policy_editor = self._advanced_widgets.get("failure:policy")
        if "failure:policy" in dirty and isinstance(policy_editor, QComboBox):
            policy = str(policy_editor.currentData())
            old_policy = str(failure.get("policy", "stop")).lower()
            if policy != old_policy:
                failure["policy"] = policy

        for field, key in (
            ("failure:attempts", "attempts"),
            ("failure:delay_seconds", "delay_seconds"),
            ("failure:max_restarts", "max_restarts"),
        ):
            editor = self._advanced_widgets.get(field)
            if field not in dirty or editor is None:
                continue
            value = editor.value()
            if value != failure.get(key, {"attempts": 1, "delay_seconds": 0.1, "max_restarts": 3}[key]):
                failure[key] = value

        if failure != current:
            step["failure"] = failure

    def _store_verification_settings(self, step: dict[str, Any]) -> None:
        dirty = {field for field in self._dirty_fields if field.startswith("verify:")}
        if not dirty:
            return
        current = step.get("verify")
        if "verify" in step and not isinstance(current, dict):
            return
        enabled_editor = self._advanced_widgets.get("verify:enabled")
        enabled = bool(enabled_editor.currentData()) if isinstance(enabled_editor, QComboBox) else False
        if "verify:enabled" in dirty and not enabled:
            step.pop("verify", None)
            return
        if not enabled:
            return

        verification = deepcopy(current) if isinstance(current, dict) else {}
        type_editor = self._advanced_widgets.get("verify:type")
        if "verify:enabled" in dirty and isinstance(type_editor, QComboBox):
            if not current:
                verification["type"] = type_editor.currentData()
                timeout_editor = self._advanced_widgets.get("verify:timeout_seconds")
                if isinstance(timeout_editor, QDoubleSpinBox):
                    verification["timeout_seconds"] = timeout_editor.value()
        if "verify:type" in dirty and isinstance(type_editor, QComboBox):
            verification_type = str(type_editor.currentData())
            if verification_type != verification.get("type", "window_exists"):
                verification["type"] = verification_type

        timeout_editor = self._advanced_widgets.get("verify:timeout_seconds")
        if (
            "verify:timeout_seconds" in dirty
            and isinstance(timeout_editor, QDoubleSpinBox)
            and timeout_editor.value() != verification.get("timeout_seconds", 2.0)
        ):
            verification["timeout_seconds"] = timeout_editor.value()

        if any(field.startswith("verify:target:") for field in dirty):
            target_key = next(
                (
                    key
                    for key in ("target", "params")
                    if isinstance(verification.get(key), dict)
                ),
                None,
            )
            if target_key:
                verification[target_key] = self._apply_target_edits(
                    verification[target_key], "verify"
                )
            elif any(key in verification for key in _TARGET_FIELDS):
                for key, value in self._apply_target_edits(verification, "verify").items():
                    if key in _TARGET_FIELDS:
                        if value == "":
                            verification.pop(key, None)
                        else:
                            verification[key] = value
            else:
                target = self._apply_target_edits({}, "verify")
                if target:
                    verification["target"] = target

        if verification != current:
            step["verify"] = verification

    def _apply_target_edits(
        self,
        original: dict[str, Any],
        prefix: str,
    ) -> dict[str, Any]:
        target = deepcopy(original)
        for key, editor in self._target_fields.get(prefix, {}).items():
            if f"{prefix}:target:{key}" not in self._dirty_fields:
                continue
            value = editor.text()
            if value.strip() == "":
                target.pop(key, None)
            elif key == "hwnd":
                try:
                    parsed_value = int(value, 16) if value.lower().startswith("0x") else int(value)
                except ValueError as exc:
                    raise ValueError("Window handle must be an integer") from exc
                if parsed_value <= 0:
                    raise ValueError("Window handle must be a positive integer")
                if parsed_value != target.get(key):
                    target[key] = parsed_value
            elif value != str(target.get(key, "")):
                target[key] = value
        return target

    @staticmethod
    def _read_editor(editor: QWidget, value_type: type) -> Any:
        if isinstance(editor, QComboBox):
            return editor.currentData()
        if isinstance(editor, QSpinBox):
            return editor.value()
        if isinstance(editor, QDoubleSpinBox):
            return editor.value()
        if isinstance(editor, QLineEdit):
            text = editor.text()
            if value_type is str:
                return text
            return WorkflowEditor._coerce_value(text, value_type)
        return None

    @staticmethod
    def _coerce_value(text: str, value_type: type) -> Any:
        value = json.loads(text)
        if value_type is int and isinstance(value, bool):
            raise ValueError("Boolean is not an integer parameter")
        if value_type is float:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("Expected a number")
            return float(value)
        if value_type in (int, float, bool, list, dict) and not isinstance(value, value_type):
            raise ValueError(f"Expected {value_type.__name__}")
        return value

    def _refresh_selected_label(self) -> None:
        if self._selected_step is None:
            return
        step = self._steps[self._selected_step]
        item = self.step_list.item(self._selected_step)
        label = str(step.get("name") or str(step.get("action", "")).replace("_", " ").title())
        item.setText(f"{self._selected_step + 1}.  {label}")

    def _update_step_controls(self) -> None:
        index = self._selected_step
        count = len(self._steps)
        self.remove_step_button.setEnabled(index is not None)
        self.move_up_button.setEnabled(index is not None and index > 0)
        self.move_down_button.setEnabled(index is not None and index < count - 1)

    def add_step(self) -> None:
        action_names = get_action_registry().list_actions()
        if not action_names:
            QMessageBox.warning(self, "No actions available", "The action registry is empty.")
            return
        action, accepted = QInputDialog.getItem(
            self, "Add step", "Action", action_names, 0, False
        )
        if not accepted:
            return
        if self._selected_step is not None and not self._store_visible_fields():
            QMessageBox.warning(
                self, "Invalid value", self._last_error or "Fix the selected step values before adding another step."
            )
            return
        step = {
            "action": action,
            "params": deepcopy(_ACTION_DEFAULTS.get(action, {})),
        }
        self._steps.append(step)
        self._refresh_step_list(len(self._steps) - 1)

    def remove_step(self) -> None:
        index = self._selected_step
        if index is None:
            return
        if not self._store_visible_fields():
            QMessageBox.warning(
                self, "Invalid value", self._last_error or "Fix the selected step values before removing it."
            )
            return
        del self._steps[index]
        self._refresh_step_list(min(index, len(self._steps) - 1))

    def move_step(self, direction: int) -> None:
        index = self._selected_step
        target = index + direction if index is not None else -1
        if index is None or not 0 <= target < len(self._steps):
            return
        if not self._store_visible_fields():
            QMessageBox.warning(
                self, "Invalid value", self._last_error or "Fix the selected step values before moving it."
            )
            return
        self._steps[index], self._steps[target] = self._steps[target], self._steps[index]
        self._refresh_step_list(target)

    def save_workflow(self) -> bool:
        """Validate and write the workflow.

        Returns True only when the file was written. On any failure the editor stays
        open with its edits intact, and the status line is not changed.
        """
        if self.config is None:
            return False
        if not self._store_visible_fields():
            QMessageBox.warning(
                self,
                "Workflow not saved",
                self._last_error or "Check numeric and JSON fields before saving.",
            )
            return False

        path = self.workflow_path
        if path is None:
            selected, _ = QFileDialog.getSaveFileName(
                self,
                "Save workflow",
                str(Path.home() / "workflow.json"),
                "Workflow files (*.json *.yaml *.yml)",
            )
            if not selected:
                return False
            path = Path(selected)
        try:
            save_config(self.config, path)
        except ConfigurationError as exc:
            QMessageBox.critical(
                self,
                "Workflow not saved",
                f"{exc}\n\nThe file was not changed. The editor is still open so you can fix the problem.",
            )
            return False
        self.workflow_path = path
        self._saved_snapshot = self._snapshot(self.config)
        self.save_state.setText(f"Saved to {path}")
        return True
