"""Workflow discovery and selection page."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from winflow.config.loader import load_and_validate_config
from winflow.core.errors import ConfigurationError
from winflow.ui.design_tokens import SPACING


@dataclass
class WorkflowEntry:
    name: str
    path: Path
    enabled: bool = False


class _WorkflowRow(QWidget):
    def __init__(self, select_row: Any, activate_row: Any) -> None:
        super().__init__()
        self._select_row = select_row
        self._activate_row = activate_row

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._select_row()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: Any) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._select_row()
            self._activate_row()
        super().mouseDoubleClickEvent(event)


class _WorkflowList(QListWidget):
    continue_requested = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.continue_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class WorkflowsPage(QWidget):
    workflow_open_requested = Signal(dict, object)

    def __init__(self, workflow_directory: Path | None = None) -> None:
        super().__init__()
        self.workflow_directory = (
            workflow_directory
            if workflow_directory is not None
            else Path(__file__).resolve().parents[2] / "workflows"
        )
        self.entries: list[WorkflowEntry] = []
        self._enabled_by_path: dict[Path, bool] = {}
        self._setup_ui()
        self.refresh_workflows()

    def _setup_ui(self) -> None:
        outer_layout = QHBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addStretch(1)

        content = QWidget()
        content.setObjectName("workflowsContent")
        content.setMaximumWidth(960)
        content.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(SPACING["md"])

        heading = QLabel("Workflows")
        heading.setObjectName("pageTitle")
        heading_row = QHBoxLayout()
        heading_row.addWidget(heading)
        heading_row.addStretch()
        self.new_workflow_button = QPushButton("+ New Workflow")
        self.new_workflow_button.setObjectName("primaryButton")
        self.new_workflow_button.setAccessibleName("Create a new workflow")
        self.new_workflow_button.clicked.connect(self.new_workflow)
        heading_row.addWidget(self.new_workflow_button)
        self.open_workflow_button = QPushButton("Open Workflow")
        self.open_workflow_button.setObjectName("secondaryButton")
        self.open_workflow_button.setAccessibleName("Open a workflow file")
        self.open_workflow_button.clicked.connect(self.open_workflow)
        heading_row.addWidget(self.open_workflow_button)
        layout.addLayout(heading_row)

        self.workflow_list = _WorkflowList()
        self.workflow_list.setObjectName("workflowList")
        self.workflow_list.setAccessibleName("Available workflows")
        self.workflow_list.setAccessibleDescription(
            "Select a workflow, then choose Continue to open it."
        )
        self.workflow_list.setSelectionMode(
            QListWidget.SelectionMode.SingleSelection
        )
        self.workflow_list.currentRowChanged.connect(
            self._update_selection_actions
        )
        self.workflow_list.itemSelectionChanged.connect(
            self._update_selection_actions
        )
        self.workflow_list.continue_requested.connect(self.continue_workflow)
        self.workflow_list.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum
        )
        self.workflow_list.setMinimumHeight(0)
        layout.addWidget(self.workflow_list)

        self.empty_state = QLabel("No workflows yet. Create one or open an existing file.")
        self.empty_state.setObjectName("emptyStateDescription")
        self.empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty_state)
        self.empty_state.hide()

        selection_row = QHBoxLayout()
        selection_row.setContentsMargins(SPACING["sm"], 0, SPACING["sm"], 0)
        self.selection_label = QLabel("Select a workflow to continue.")
        self.selection_label.setObjectName("workflowSelectionLabel")
        selection_row.addWidget(self.selection_label)
        selection_row.addStretch()
        self.continue_button = QPushButton("Continue")
        self.continue_button.setObjectName("primaryButton")
        self.continue_button.setAccessibleName("Continue")
        self.continue_button.setEnabled(False)
        self.continue_button.clicked.connect(self.continue_workflow)
        selection_row.addWidget(self.continue_button)
        layout.addLayout(selection_row)
        layout.addStretch(1)
        outer_layout.addWidget(content, 4)
        outer_layout.addStretch(1)

    def _update_selection_actions(self, _row: int = -1) -> None:
        for index in range(self.workflow_list.count()):
            item = self.workflow_list.item(index)
            row = self.workflow_list.itemWidget(item)
            if row is None:
                continue
            row.setProperty("selected", item is self.workflow_list.currentItem())
            row.style().unpolish(row)
            row.style().polish(row)

        selected = self.workflow_list.currentItem()
        entry = (
            selected.data(Qt.ItemDataRole.UserRole)
            if selected is not None
            else None
        )
        self.continue_button.setEnabled(isinstance(entry, WorkflowEntry))
        self.selection_label.setText(
            f"Selected: {entry.name}"
            if isinstance(entry, WorkflowEntry)
            else "Select a workflow to continue."
        )

    def refresh_workflows(self) -> None:
        self.entries.clear()
        self.workflow_list.clear()
        paths: list[Path] = []
        if self.workflow_directory.is_dir():
            paths = sorted(
                (
                    path.resolve()
                    for path in self.workflow_directory.iterdir()
                    if path.is_file()
                    and path.suffix.lower() in {".json", ".yaml", ".yml"}
                ),
                key=lambda path: path.name.casefold(),
            )

        for path in paths:
            entry = WorkflowEntry(
                path.stem,
                path,
                self._enabled_by_path.get(path, False),
            )
            self.entries.append(entry)
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsSelectable)

            row = _WorkflowRow(
                lambda workflow_item=item: self.workflow_list.setCurrentItem(
                    workflow_item
                ),
                self.continue_workflow,
            )
            row.setObjectName("workflowRow")
            row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(
                SPACING["md"], SPACING["sm"], SPACING["md"], SPACING["sm"]
            )
            row_layout.setSpacing(SPACING["md"])

            text_column = QVBoxLayout()
            text_column.setContentsMargins(0, 0, 0, 0)
            text_column.setSpacing(SPACING["xs"])
            name = QLabel(entry.name)
            name.setObjectName("workflowRowName")
            name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            detail = QLabel(self._display_path(entry.path))
            detail.setObjectName("workflowRowDetail")
            detail.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            text_column.addWidget(name)
            text_column.addWidget(detail)
            row_layout.addLayout(text_column, 1)
            toggle = QCheckBox("ON" if entry.enabled else "OFF")
            toggle.setObjectName("workflowEnabledToggle")
            toggle.setChecked(entry.enabled)
            toggle.setAccessibleName(f"Enable workflow {entry.name}")
            toggle.toggled.connect(
                lambda enabled, workflow_entry=entry, workflow_item=item:
                    self._set_entry_enabled(workflow_entry, workflow_item, enabled)
            )
            row_layout.addWidget(toggle)
            item.setSizeHint(row.sizeHint())
            self.workflow_list.addItem(item)
            self.workflow_list.setItemWidget(item, row)

        if not self.entries:
            item = QListWidgetItem("No workflows found")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.workflow_list.addItem(item)
        self.empty_state.setVisible(not self.entries)
        self.workflow_list.setVisible(bool(self.entries))
        self.workflow_list.setMaximumHeight(
            min(len(self.entries), 5) * 64 + 2 if self.entries else 0
        )
        self._update_selection_actions()

    @staticmethod
    def _display_path(path: Path) -> str:
        return f"{path.parent.name}  ·  {path.name}"

    def _set_entry_enabled(
        self,
        entry: WorkflowEntry,
        item: QListWidgetItem,
        enabled: bool,
    ) -> None:
        entry.enabled = enabled
        self._enabled_by_path[entry.path] = enabled
        self.workflow_list.setCurrentItem(item)
        row = self.workflow_list.itemWidget(item)
        if row is not None:
            toggle = row.findChild(QCheckBox, "workflowEnabledToggle")
            if toggle is not None:
                toggle.setText("ON" if enabled else "OFF")

    def new_workflow(self) -> None:
        workflow_name = "New workflow"
        config = {
            "schema_version": "1.0",
            "workflow": {
                "id": f"workflow_{uuid4().hex[:12]}",
                "name": workflow_name,
            },
            "steps": [],
        }
        self.workflow_open_requested.emit(config, None)

    def open_workflow(self) -> None:
        name, _ = QFileDialog.getOpenFileName(
            self,
            "Open workflow",
            str(self.workflow_directory),
            "Workflow files (*.json *.yaml *.yml)",
        )
        if not name:
            return
        path = Path(name).expanduser().resolve()
        self._open_path(path)

    def continue_workflow(self) -> None:
        selected = self.workflow_list.currentItem()
        entry = (
            selected.data(Qt.ItemDataRole.UserRole)
            if selected is not None
            else None
        )
        if not isinstance(entry, WorkflowEntry):
            return

        self._open_path(entry.path)

    def _open_path(self, path: Path) -> None:
        try:
            config = load_and_validate_config(path)
        except ConfigurationError as exc:
            QMessageBox.warning(self, "Unable to open workflow", str(exc))
            return
        self.workflow_open_requested.emit(config, path)

    def update_workflow_entry(
        self,
        row: int,
        config: dict[str, Any],
        path: Path | None,
    ) -> None:
        if not 0 <= row < len(self.entries):
            return
        entry = self.entries[row]
        if path is not None:
            if entry.path in self._enabled_by_path:
                self._enabled_by_path[path] = self._enabled_by_path.pop(entry.path)
            entry.path = path
        workflow = config.get("workflow", {})
        entry.name = str(workflow.get("name") or workflow.get("id") or entry.path.stem)
        item = self.workflow_list.item(row)
        item.setData(Qt.ItemDataRole.UserRole, entry)
        row_widget = self.workflow_list.itemWidget(item)
        if row_widget is not None:
            name = row_widget.findChild(QLabel, "workflowRowName")
            detail = row_widget.findChild(QLabel, "workflowRowDetail")
            toggle = row_widget.findChild(QCheckBox, "workflowEnabledToggle")
            if name is not None:
                name.setText(entry.name)
            if detail is not None:
                detail.setText(self._display_path(entry.path))
            if toggle is not None:
                toggle.setAccessibleName(f"Enable workflow {entry.name}")
