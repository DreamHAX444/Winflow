"""Minimal WinFlow desktop application shell."""

import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
)

from winflow.ui.design_tokens import SPACING, create_stylesheet
from winflow.ui.views.workflow_editor import WorkflowEditor
from winflow.ui.views.workflows_page import WorkflowsPage


class EmptyState(QWidget):
    def __init__(self, title: str, description: str) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(SPACING["sm"])

        title_label = QLabel(title)
        title_label.setObjectName("emptyStateTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        description_label = QLabel(description)
        description_label.setObjectName("emptyStateDescription")
        description_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(title_label)
        layout.addWidget(description_label)


class MainWindow(QMainWindow):
    workflow_open_requested = Signal(dict, object)

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("WinFlow")
        self.setMinimumSize(760, 520)
        self.resize(1120, 760)

        root = QWidget()
        root.setObjectName("appRoot")
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        root_layout.addWidget(self._create_top_bar())

        body = QWidget()
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)
        self.navigation = self._create_navigation()
        body_layout.addWidget(self.navigation)
        body_layout.addWidget(self._create_main_content(), 1)
        root_layout.addWidget(body, 1)

        self.setCentralWidget(root)

    def _create_top_bar(self) -> QWidget:
        top_bar = QWidget()
        top_bar.setObjectName("topBar")
        layout = QHBoxLayout(top_bar)
        layout.setContentsMargins(
            SPACING["xl"],
            SPACING["md"],
            SPACING["xl"],
            SPACING["md"],
        )

        brand = QLabel("WinFlow")
        brand.setObjectName("brand")
        status = QLabel("Ready")
        status.setObjectName("readyStatus")

        layout.addWidget(brand)
        layout.addStretch()
        layout.addWidget(status)
        return top_bar

    def _create_navigation(self) -> QWidget:
        navigation = QWidget()
        navigation.setObjectName("navigation")
        navigation.setFixedWidth(184)
        layout = QVBoxLayout(navigation)
        layout.setContentsMargins(
            SPACING["md"],
            SPACING["lg"],
            SPACING["md"],
            SPACING["lg"],
        )
        layout.setSpacing(SPACING["xs"])

        self.navigation_buttons: list[QPushButton] = []
        for index, label in enumerate(("Workflows", "Settings")):
            button = QPushButton(label)
            button.setObjectName("navigationItem")
            button.setCheckable(True)
            button.setAccessibleName(label)
            button.clicked.connect(lambda checked=False, page=index: self._show_page(page))
            self.navigation_buttons.append(button)
            layout.addWidget(button)

        layout.addStretch()
        self.navigation_buttons[0].setChecked(True)
        return navigation

    def _create_main_content(self) -> QWidget:
        content = QWidget()
        content.setObjectName("mainContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(
            SPACING["xxl"],
            SPACING["xxl"],
            SPACING["xxl"],
            SPACING["xxl"],
        )

        self.pages = QStackedWidget()
        self.workflows_page = WorkflowsPage()
        self.workflows_page.workflow_open_requested.connect(
            self._open_workflow_editor
        )
        self.workflows_page.workflow_open_requested.connect(
            self.workflow_open_requested.emit
        )
        self.pages.addWidget(self.workflows_page)
        self.pages.addWidget(
            EmptyState(
                "Settings",
                "Settings will appear here in a future step.",
            )
        )
        self.workflow_editor = WorkflowEditor()
        self.workflow_editor.back_requested.connect(self._return_to_workflows)
        self.pages.addWidget(self.workflow_editor)
        layout.addWidget(self.pages)
        return content

    def _show_page(self, index: int) -> None:
        self.navigation.show()
        self.pages.setCurrentIndex(index)
        for button_index, button in enumerate(self.navigation_buttons):
            button.setChecked(button_index == index)

    def _open_workflow_editor(
        self,
        config: dict[str, Any],
        path: Path | None,
    ) -> None:
        self.workflow_editor.set_workflow(config, path)
        self.navigation.hide()
        self.pages.setCurrentWidget(self.workflow_editor)

    def _return_to_workflows(self) -> None:
        row = self.workflows_page.workflow_list.currentRow()
        if self.workflow_editor.config is not None:
            self.workflows_page.update_workflow_entry(
                row,
                self.workflow_editor.config,
                self.workflow_editor.workflow_path,
            )
        self._show_page(0)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("WinFlow")
    app.setStyle("Fusion")
    app.setStyleSheet(create_stylesheet())

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
