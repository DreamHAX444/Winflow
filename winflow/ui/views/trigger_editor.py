import json
from copy import deepcopy
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from winflow.config.trigger_form import (
    MATCH_MODES,
    NOTIFICATION_TYPE,
    NotificationFormState,
    ScheduleFormState,
    TriggerFormError,
    merge_notification_trigger,
    merge_schedule_trigger,
    notification_form_from_trigger,
    schedule_form_from_trigger,
)
from winflow.core.errors import TriggerConfigurationError
from winflow.triggers.matcher import validate_notification_trigger_config
from winflow.ui.design_tokens import SPACING

SCHEDULE_TYPES = ("one_time", "daily", "weekly", "startup")
_NOTIFICATION_INDEX = 1
_SCHEDULE_INDEX = 2
_RAW_INDEX = 3


class TriggerEditor(QWidget):
    """Editor for a workflow trigger.

    The editor keeps the trigger it was loaded with. ``get_config`` returns a new
    dictionary built from that original, so keys the form does not show (unknown
    fields, the enabled flag when untouched, and similar) are preserved. It raises
    ``TriggerFormError`` instead of returning a partial trigger.
    """

    def __init__(self, config: dict[str, Any] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._original: dict[str, Any] | None = None
        self._setup_ui()
        self.set_config(config)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING["md"])

        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("Trigger Type:"))
        self.type_combo = QComboBox()
        self.type_combo.addItems(["(None)", "Windows Notification", "Schedule", "Raw JSON / Other"])
        self.type_combo.currentIndexChanged.connect(self._on_type_changed)
        type_layout.addWidget(self.type_combo)
        type_layout.addStretch()
        layout.addLayout(type_layout)

        self.enabled_check = QCheckBox("Trigger enabled")
        self.enabled_check.setObjectName("triggerEnabledCheck")
        self.enabled_check.setToolTip(
            "When unchecked, the trigger stays in the file but does not listen for events."
        )
        layout.addWidget(self.enabled_check)

        self.warning_label = QLabel("")
        self.warning_label.setObjectName("triggerWarning")
        self.warning_label.setWordWrap(True)
        self.warning_label.hide()
        layout.addWidget(self.warning_label)

        self.stack = QStackedWidget()
        layout.addWidget(self.stack)

        self.none_widget = QWidget()
        self.stack.addWidget(self.none_widget)

        self.notification_widget = self._create_notification_ui()
        self.stack.addWidget(self.notification_widget)

        self.schedule_widget = self._create_schedule_ui()
        self.stack.addWidget(self.schedule_widget)

        self.raw_widget = self._create_raw_ui()
        self.stack.addWidget(self.raw_widget)

    def _create_notification_ui(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        match_group = QGroupBox("Match Criteria")
        match_layout = QFormLayout(match_group)

        self.match_all = QCheckBox("Match all notifications")
        self.match_all.setObjectName("matchAllCheck")
        self.match_all.setToolTip(
            "Triggers on every Windows notification. Leave all criteria empty to use this."
        )
        self.match_all.toggled.connect(self._update_match_state)
        match_layout.addRow("", self.match_all)
        self.match_all_warning = QLabel(
            "This workflow will run for EVERY Windows notification. Use it only if that is intended."
        )
        self.match_all_warning.setObjectName("triggerWarning")
        self.match_all_warning.setWordWrap(True)
        self.match_all_warning.hide()
        match_layout.addRow("", self.match_all_warning)

        self._criteria_modes: dict[str, QComboBox] = {}
        self._criteria_values: dict[str, QLineEdit] = {}
        placeholders = {
            "application": "Application name to match",
            "title": "Notification title to match",
            "body": "Notification body text to match",
        }
        labels = {"application": "Application:", "title": "Title:", "body": "Body:"}
        for name in ("application", "title", "body"):
            mode = QComboBox()
            mode.addItems(list(MATCH_MODES))
            mode.setAccessibleName(f"{name} match mode")
            value = QLineEdit()
            value.setPlaceholderText(placeholders[name])
            value.setAccessibleName(f"{name} criterion")
            row = QHBoxLayout()
            row.addWidget(mode)
            row.addWidget(value)
            match_layout.addRow(labels[name], row)
            self._criteria_modes[name] = mode
            self._criteria_values[name] = value

        self.case_sensitive = QCheckBox("Case Sensitive")
        match_layout.addRow("", self.case_sensitive)

        self.criteria_hint = QLabel(
            "Add at least one criterion, or check 'Match all notifications'."
        )
        self.criteria_hint.setObjectName("helperText")
        self.criteria_hint.setWordWrap(True)
        match_layout.addRow("", self.criteria_hint)

        layout.addWidget(match_group)

        adv_group = QGroupBox("Advanced Options")
        adv_layout = QFormLayout(adv_group)

        self.dedupe_enabled = QCheckBox("Enable Deduplication")
        self.dedupe_window = QDoubleSpinBox()
        self.dedupe_window.setRange(0, 3600)
        self.dedupe_window.setSuffix(" sec")
        dedupe_row = QHBoxLayout()
        dedupe_row.addWidget(self.dedupe_enabled)
        dedupe_row.addWidget(self.dedupe_window)
        adv_layout.addRow("Deduplication:", dedupe_row)

        self.cooldown = QDoubleSpinBox()
        self.cooldown.setRange(0, 3600)
        self.cooldown.setSuffix(" sec")
        adv_layout.addRow("Cooldown:", self.cooldown)

        self.while_running = QComboBox()
        self.while_running.addItems(["ignore", "queue", "restart"])
        adv_layout.addRow("While Running:", self.while_running)

        layout.addWidget(adv_group)
        layout.addStretch()
        return widget

    def _create_schedule_ui(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        form = QFormLayout()
        self.sched_type = QComboBox()
        self.sched_type.addItems(list(SCHEDULE_TYPES))
        form.addRow("Schedule Type:", self.sched_type)

        self.sched_time = QLineEdit()
        self.sched_time.setPlaceholderText("HH:MM (e.g. 14:30)")
        form.addRow("Time:", self.sched_time)

        layout.addLayout(form)
        layout.addStretch()
        return widget

    def _create_raw_ui(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("Raw JSON Configuration (saved exactly as written):"))
        self.raw_edit = QPlainTextEdit()
        self.raw_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.raw_edit)
        return widget

    def _on_type_changed(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        self._update_enabled_control()
        self._update_match_state()

    def _update_enabled_control(self) -> None:
        index = self.type_combo.currentIndex()
        self.enabled_check.setVisible(index in (_NOTIFICATION_INDEX, _SCHEDULE_INDEX, _RAW_INDEX))
        self.enabled_check.setEnabled(index in (_NOTIFICATION_INDEX, _SCHEDULE_INDEX))
        if index == _RAW_INDEX:
            self.enabled_check.setToolTip('Edit "enabled" in the raw JSON; this box is not used here.')
        else:
            self.enabled_check.setToolTip(
                "When unchecked, the trigger stays in the file but does not listen for events."
            )

    def _update_match_state(self, *_: Any) -> None:
        match_all = self.match_all.isChecked()
        self.match_all_warning.setVisible(match_all)
        for name in self._criteria_values:
            self._criteria_modes[name].setEnabled(not match_all)
            self._criteria_values[name].setEnabled(not match_all)
        self.case_sensitive.setEnabled(not match_all)
        self.criteria_hint.setVisible(not match_all)

    def set_warning(self, text: str) -> None:
        self.warning_label.setText(text)
        self.warning_label.setVisible(bool(text))

    def set_config(self, config: dict[str, Any] | None) -> None:
        """Load ``config`` (a single trigger dictionary, or None for no trigger)."""
        self._original = deepcopy(config) if config else None
        self.set_warning("")
        if not config:
            self.type_combo.setCurrentIndex(0)
            self.enabled_check.setChecked(True)
            self._update_enabled_control()
            return

        self.enabled_check.setChecked(bool(config.get("enabled", True)))
        t_type = config.get("type", "")
        if t_type == NOTIFICATION_TYPE:
            self.type_combo.setCurrentIndex(_NOTIFICATION_INDEX)
            self._load_notification_config(config)
        elif t_type in SCHEDULE_TYPES:
            self.type_combo.setCurrentIndex(_SCHEDULE_INDEX)
            self._load_schedule_config(config)
        else:
            self.type_combo.setCurrentIndex(_RAW_INDEX)
            self.raw_edit.setPlainText(json.dumps(config, indent=2, ensure_ascii=False))
        self._update_enabled_control()
        self._update_match_state()

    def _load_notification_config(self, config: dict[str, Any]) -> None:
        state = notification_form_from_trigger(config)
        for name in ("application", "title", "body"):
            criterion = state.criteria.get(name)
            if criterion is None:
                self._criteria_modes[name].setCurrentText("contains")
                self._criteria_values[name].setText("")
            else:
                self._criteria_modes[name].setCurrentText(criterion["mode"])
                self._criteria_values[name].setText(criterion["value"])
        self.match_all.setChecked(state.match_all)
        self.case_sensitive.setChecked(state.case_sensitive)
        self.dedupe_enabled.setChecked(state.dedupe_enabled)
        self.dedupe_window.setValue(state.dedupe_window)
        self.cooldown.setValue(state.cooldown)
        idx = self.while_running.findText(state.while_running)
        self.while_running.setCurrentIndex(idx if idx >= 0 else 0)
        try:
            validate_notification_trigger_config(config)
        except TriggerConfigurationError as exc:
            self.set_warning(
                f"This notification trigger has settings the engine rejects: {exc} "
                "Correct them in Raw JSON / Other, or the save will be refused."
            )

    def _load_schedule_config(self, config: dict[str, Any]) -> None:
        state = schedule_form_from_trigger(config)
        idx = self.sched_type.findText(state.schedule_type)
        if idx >= 0:
            self.sched_type.setCurrentIndex(idx)
        self.sched_time.setText(state.time_text)

    def get_config(self) -> dict[str, Any] | None:
        """Return the trigger to store, or None to remove it.

        Raises:
            TriggerFormError: When the form values cannot be saved. Nothing is changed.
        """
        index = self.type_combo.currentIndex()
        enabled = self.enabled_check.isChecked()
        if index == 0:
            return None
        if index == _NOTIFICATION_INDEX:
            return merge_notification_trigger(self._original, self._notification_form(), enabled)
        if index == _SCHEDULE_INDEX:
            form = ScheduleFormState(
                schedule_type=self.sched_type.currentText(),
                time_text=self.sched_time.text(),
            )
            if form.schedule_type != "startup" and not form.time_text.strip():
                raise TriggerFormError("Enter a time in HH:MM format for this schedule.")
            return merge_schedule_trigger(self._original, form, enabled)
        text = self.raw_edit.toPlainText().strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise TriggerFormError(f"Raw trigger JSON is invalid: {exc}") from exc
        if not isinstance(parsed, dict):
            raise TriggerFormError("Raw trigger JSON must be an object.")
        return parsed

    def _notification_form(self) -> NotificationFormState:
        criteria: dict[str, dict[str, str]] = {}
        for name in ("application", "title", "body"):
            value = self._criteria_values[name].text().strip()
            if value:
                criteria[name] = {"mode": self._criteria_modes[name].currentText(), "value": value}
        return NotificationFormState(
            criteria=criteria,
            match_all=self.match_all.isChecked(),
            case_sensitive=self.case_sensitive.isChecked(),
            dedupe_enabled=self.dedupe_enabled.isChecked(),
            dedupe_window=float(self.dedupe_window.value()),
            cooldown=float(self.cooldown.value()),
            while_running=self.while_running.currentText(),
        )
