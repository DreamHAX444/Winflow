import json
from typing import Any

from PySide6.QtCore import Qt
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

from winflow.ui.design_tokens import SPACING


class TriggerEditor(QWidget):
    """A user-friendly editor for workflow trigger configurations."""

    def __init__(self, config: dict[str, Any] | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config or {}
        self._setup_ui()
        self.set_config(self.config)

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

        self.app_mode = QComboBox()
        self.app_mode.addItems(["contains", "exact"])
        self.app_value = QLineEdit()
        self.app_value.setPlaceholderText("e.g. Chrome, Slack")
        app_row = QHBoxLayout()
        app_row.addWidget(self.app_mode)
        app_row.addWidget(self.app_value)
        match_layout.addRow("Application:", app_row)

        self.title_mode = QComboBox()
        self.title_mode.addItems(["contains", "exact"])
        self.title_value = QLineEdit()
        self.title_value.setPlaceholderText("e.g. New Message")
        title_row = QHBoxLayout()
        title_row.addWidget(self.title_mode)
        title_row.addWidget(self.title_value)
        match_layout.addRow("Title:", title_row)

        self.body_mode = QComboBox()
        self.body_mode.addItems(["contains", "exact"])
        self.body_value = QLineEdit()
        self.body_value.setPlaceholderText("e.g. John Doe")
        body_row = QHBoxLayout()
        body_row.addWidget(self.body_mode)
        body_row.addWidget(self.body_value)
        match_layout.addRow("Body:", body_row)

        self.case_sensitive = QCheckBox("Case Sensitive")
        match_layout.addRow("", self.case_sensitive)

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
        self.sched_type.addItems(["one_time", "daily", "weekly", "startup"])
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
        layout.addWidget(QLabel("Raw JSON Configuration:"))
        self.raw_edit = QPlainTextEdit()
        self.raw_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.raw_edit)
        return widget

    def _on_type_changed(self, index: int) -> None:
        self.stack.setCurrentIndex(index)

    def set_config(self, config: dict[str, Any]) -> None:
        if not config:
            self.type_combo.setCurrentIndex(0)
            return

        t_type = config.get("type", "")
        if t_type == "windows_notification":
            self.type_combo.setCurrentIndex(1)
            self._load_notification_config(config)
        elif t_type in ("one_time", "daily", "weekly", "startup"):
            self.type_combo.setCurrentIndex(2)
            self._load_schedule_config(config)
        else:
            self.type_combo.setCurrentIndex(3)
            self.raw_edit.setPlainText(json.dumps(config, indent=2, ensure_ascii=False))

    def _load_notification_config(self, config: dict[str, Any]) -> None:
        match = config.get("match", {})
        
        def load_field(name: str, mode_cb: QComboBox, val_le: QLineEdit) -> None:
            val = match.get(name)
            if not val:
                val = config.get(name) # legacy
            
            if not val:
                mode_cb.setCurrentText("contains")
                val_le.setText("")
            elif isinstance(val, dict):
                mode_cb.setCurrentText(val.get("mode", "contains"))
                val_le.setText(val.get("value", ""))
            else:
                mode_cb.setCurrentText("contains")
                val_le.setText(str(val))

        load_field("application", self.app_mode, self.app_value)
        load_field("title", self.title_mode, self.title_value)
        load_field("body", self.body_mode, self.body_value)
        
        self.case_sensitive.setChecked(bool(match.get("case_sensitive", False)))
        
        dedupe = config.get("deduplication", {})
        if isinstance(dedupe, bool):
            self.dedupe_enabled.setChecked(dedupe)
            self.dedupe_window.setValue(10.0)
        else:
            self.dedupe_enabled.setChecked(bool(dedupe.get("enabled", True)))
            self.dedupe_window.setValue(float(dedupe.get("window_seconds", 10.0)))
            
        self.cooldown.setValue(float(config.get("cooldown_seconds", 0.0)))
        
        while_run = str(config.get("while_running", "ignore")).lower().strip()
        idx = self.while_running.findText(while_run)
        if idx >= 0:
            self.while_running.setCurrentIndex(idx)

    def _load_schedule_config(self, config: dict[str, Any]) -> None:
        t_type = config.get("type", "daily")
        idx = self.sched_type.findText(t_type)
        if idx >= 0:
            self.sched_type.setCurrentIndex(idx)
        
        # very basic time extraction
        cfg = config.get("config", {})
        self.sched_time.setText(str(cfg.get("time", "")))

    def get_config(self) -> dict[str, Any] | None:
        idx = self.type_combo.currentIndex()
        if idx == 0:
            return None
        elif idx == 1:
            return self._get_notification_config()
        elif idx == 2:
            return self._get_schedule_config()
        else:
            text = self.raw_edit.toPlainText().strip()
            if not text:
                return None
            return json.loads(text)

    def _get_notification_config(self) -> dict[str, Any]:
        match = {"case_sensitive": self.case_sensitive.isChecked()}
        
        def save_field(name: str, mode_cb: QComboBox, val_le: QLineEdit) -> None:
            text = val_le.text().strip()
            if text:
                match[name] = {"mode": mode_cb.currentText(), "value": text}
                
        save_field("application", self.app_mode, self.app_value)
        save_field("title", self.title_mode, self.title_value)
        save_field("body", self.body_mode, self.body_value)
        
        return {
            "type": "windows_notification",
            "match": match,
            "deduplication": {
                "enabled": self.dedupe_enabled.isChecked(),
                "window_seconds": self.dedupe_window.value(),
            },
            "cooldown_seconds": self.cooldown.value(),
            "while_running": self.while_running.currentText(),
        }

    def _get_schedule_config(self) -> dict[str, Any]:
        return {
            "type": self.sched_type.currentText(),
            "config": {
                "time": self.sched_time.text().strip()
            }
        }
