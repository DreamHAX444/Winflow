"""Central design tokens and stylesheet for the WinFlow application shell."""

from pathlib import Path

from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QComboBox, QStyle, QStyleOptionViewItem, QStyledItemDelegate
COLORS = {
    "background": "#F5F6F8",
    "surface": "#FFFFFF",
    "surface_elevated": "#FFFFFF",
    "text_primary": "#202630",
    "text_secondary": "#505B6A",
    "text_muted": "#626D7A",
    "border": "#E1E5EA",
    "accent": "#365F8C",
    "destructive": "#B54747",
    "success": "#347454",
    "warning": "#95651F",
    "nav_active": "#EDF2F7",
    "focus": "#527AA5",
}

SPACING = {
    "xs": 4,
    "sm": 8,
    "md": 12,
    "lg": 20,
    "xl": 28,
    "xxl": 40,
}

RADIUS = {
    "sm": 4,
    "md": 6,
}

CONTROL_HEIGHT = {
    "navigation": 36,
    "button": 36,
}

CONTROL_PADDING = {
    "horizontal": 10,
    "combo_horizontal": 8,
    "combo_arrow": 18,
    "spin_button": 16,
    "arrow_glyph": 8,
}

CONTROL_WIDTH = {
    "mouse_button": 128,
}

TYPOGRAPHY = {
    "family": '"Segoe UI", "Arial", sans-serif',
    "brand": 16,
    "body": 14,
    "navigation": 13,
    "empty_state": 22,
    "field_label": 13,
}


def create_stylesheet() -> str:
    """Build the shared application stylesheet from the tokens above."""
    assets = Path(__file__).parent / "assets"
    return f"""
        * {{
            font-family: {TYPOGRAPHY["family"]};
        }}

        QLabel {{
            color: {COLORS["text_primary"]};
        }}

        QMainWindow,
        QDialog,
        QWidget#appRoot {{
            background-color: {COLORS["background"]};
            color: {COLORS["text_primary"]};
            font-size: {TYPOGRAPHY["body"]}px;
        }}

        QWidget#topBar,
        QWidget#navigation {{
            background-color: {COLORS["surface"]};
        }}

        QWidget#topBar {{
            border-bottom: 1px solid {COLORS["border"]};
        }}

        QWidget#navigation {{
            border-right: 1px solid {COLORS["border"]};
        }}

        QWidget#mainContent {{
            background-color: {COLORS["background"]};
        }}

        QLabel#brand {{
            color: {COLORS["text_primary"]};
            font-size: {TYPOGRAPHY["brand"]}px;
            font-weight: 600;
        }}

        QLabel#readyStatus {{
            color: {COLORS["success"]};
            font-size: {TYPOGRAPHY["body"]}px;
        }}

        QPushButton#navigationItem {{
            min-height: {CONTROL_HEIGHT["navigation"]}px;
            padding: 0 {SPACING["md"]}px;
            border: 1px solid transparent;
            border-radius: {RADIUS["sm"]}px;
            background-color: transparent;
            color: {COLORS["text_secondary"]};
            text-align: left;
            font-size: {TYPOGRAPHY["navigation"]}px;
        }}

        QPushButton#navigationItem:hover {{
            background-color: {COLORS["background"]};
            color: {COLORS["text_primary"]};
        }}

        QPushButton#navigationItem:checked {{
            background-color: {COLORS["nav_active"]};
            color: {COLORS["accent"]};
            font-weight: 600;
        }}

        QPushButton#navigationItem:focus {{
            border-color: {COLORS["focus"]};
        }}

        QLabel#emptyStateTitle {{
            color: {COLORS["text_primary"]};
            font-size: {TYPOGRAPHY["empty_state"]}px;
            font-weight: 500;
        }}

        QLabel#emptyStateDescription {{
            color: {COLORS["text_muted"]};
            font-size: {TYPOGRAPHY["body"]}px;
        }}

        QLabel#pageTitle {{
            color: {COLORS["text_primary"]};
            font-size: {TYPOGRAPHY["empty_state"]}px;
            font-weight: 500;
        }}

        QLabel#sectionLabel {{
            color: {COLORS["text_secondary"]};
            font-size: {TYPOGRAPHY["navigation"]}px;
            font-weight: 600;
        }}

        QPushButton#primaryButton,
        QPushButton#secondaryButton {{
            min-height: {CONTROL_HEIGHT["button"]}px;
            padding: 0 {SPACING["md"]}px;
            border-radius: {RADIUS["sm"]}px;
            font-size: {TYPOGRAPHY["navigation"]}px;
        }}

        QPushButton#primaryButton {{
            border: 1px solid {COLORS["accent"]};
            background-color: {COLORS["accent"]};
            color: {COLORS["surface"]};
            font-weight: 600;
        }}

        QPushButton#primaryButton:hover {{
            background-color: {COLORS["text_primary"]};
            border-color: {COLORS["text_primary"]};
        }}

        QPushButton#secondaryButton {{
            border: 1px solid {COLORS["border"]};
            background-color: {COLORS["surface"]};
            color: {COLORS["text_secondary"]};
        }}

        QPushButton#secondaryButton:hover {{
            border-color: {COLORS["focus"]};
            color: {COLORS["text_primary"]};
        }}

        QPushButton#secondaryButton:disabled {{
            color: {COLORS["text_muted"]};
            background-color: {COLORS["background"]};
        }}

        QPushButton#primaryButton:focus,
        QPushButton#secondaryButton:focus {{
            border-color: {COLORS["focus"]};
        }}

        QPushButton#primaryButton:disabled {{
            border-color: {COLORS["border"]};
            background-color: {COLORS["border"]};
            color: {COLORS["text_muted"]};
        }}

        QDialog#locationPicker,
        QDialog#windowPicker {{
            border: 1px solid {COLORS["border"]};
            background-color: {COLORS["surface"]};
            color: {COLORS["text_primary"]};
        }}

        QLabel#locationPickerTitle {{
            color: {COLORS["text_primary"]};
            font-size: {TYPOGRAPHY["body"]}px;
            font-weight: 600;
        }}

        QLabel#locationPickerStatus {{
            color: {COLORS["warning"]};
            font-size: {TYPOGRAPHY["navigation"]}px;
        }}

        QListWidget#workflowList {{
            background: transparent;
            border: 1px solid transparent;
            outline: none;
        }}

        QListWidget#workflowList:focus {{
            border-color: transparent;
        }}

        QListWidget#workflowList::item {{
            min-height: 0;
            padding: 0;
            border: none;
            background: transparent;
        }}

        QWidget#workflowRow {{
            min-height: 52px;
            border: 1px solid transparent;
            border-radius: {RADIUS["sm"]}px;
            background-color: {COLORS["surface"]};
        }}

        QWidget#workflowRow:hover {{
            background-color: {COLORS["surface"]};
            border-color: {COLORS["border"]};
        }}

        QWidget#workflowRow[selected="true"] {{
            background-color: {COLORS["nav_active"]};
            border-color: {COLORS["border"]};
            border-left: 2px solid {COLORS["accent"]};
        }}

        QWidget#workflowRow[selected="true"] QLabel#workflowRowName {{
            font-weight: 600;
        }}

        QWidget#workflowsContent {{
            background: transparent;
        }}

        QLabel#workflowSelectionLabel {{
            color: {COLORS["text_secondary"]};
            font-size: {TYPOGRAPHY["navigation"]}px;
        }}

        QLineEdit,
        QComboBox,
        QSpinBox,
        QDoubleSpinBox {{
            height: {CONTROL_HEIGHT["button"]}px;
            min-height: {CONTROL_HEIGHT["button"]}px;
            max-height: {CONTROL_HEIGHT["button"]}px;
            padding: 0 {CONTROL_PADDING["horizontal"]}px;
            border: 1px solid {COLORS["border"]};
            border-radius: {RADIUS["sm"]}px;
            background-color: {COLORS["surface"]};
            color: {COLORS["text_primary"]};
            selection-background-color: {COLORS["accent"]};
            selection-color: {COLORS["surface"]};
        }}

        QPlainTextEdit {{
            min-height: {CONTROL_HEIGHT["button"]}px;
            padding: 0 {CONTROL_PADDING["horizontal"]}px;
            border: 1px solid {COLORS["border"]};
            border-radius: {RADIUS["sm"]}px;
            background-color: {COLORS["surface"]};
            color: {COLORS["text_primary"]};
            selection-background-color: {COLORS["accent"]};
            selection-color: {COLORS["surface"]};
        }}

        QLineEdit:focus,
        QComboBox:focus,
        QPlainTextEdit:focus,
        QSpinBox:focus,
        QDoubleSpinBox:focus {{
            border-color: {COLORS["focus"]};
        }}

        QLineEdit:hover,
        QComboBox:hover,
        QPlainTextEdit:hover,
        QSpinBox:hover,
        QDoubleSpinBox:hover {{
            border-color: {COLORS["focus"]};
        }}

        QComboBox {{
            padding-left: {CONTROL_PADDING["combo_horizontal"]}px;
            padding-right: {CONTROL_PADDING["combo_arrow"]}px;
            padding-top: 0;
            padding-bottom: 0;
        }}

        QComboBox::drop-down {{
            width: {CONTROL_PADDING["combo_arrow"]}px;
            border: none;
            border-left: 1px solid {COLORS["border"]};
            border-top-right-radius: {RADIUS["sm"]}px;
            border-bottom-right-radius: {RADIUS["sm"]}px;
            background-color: {COLORS["surface"]};
        }}

        QComboBox::drop-down:hover,
        QComboBox:on {{
            background-color: {COLORS["nav_active"]};
            border-color: {COLORS["focus"]};
        }}

        QComboBox::down-arrow {{
            image: url("{(assets / "chevron-down.svg").as_posix()}");
            width: {CONTROL_PADDING["arrow_glyph"]}px;
            height: {CONTROL_PADDING["arrow_glyph"]}px;
        }}

        QComboBox QAbstractItemView {{
            min-width: 0;
            padding: 2px;
            border: 1px solid {COLORS["border"]};
            background-color: {COLORS["surface"]};
            color: {COLORS["text_primary"]};
            outline: none;
            selection-background-color: {COLORS["nav_active"]};
            selection-color: {COLORS["text_primary"]};
        }}

        QComboBox QAbstractItemView::item {{
            min-height: 24px;
            padding: 0 {CONTROL_PADDING["combo_horizontal"]}px;
            color: {COLORS["text_primary"]};
        }}

        QComboBox QAbstractItemView::item:hover {{
            background-color: {COLORS["background"]};
            color: {COLORS["text_primary"]};
        }}

        QComboBox QAbstractItemView::item:selected {{
            background-color: {COLORS["nav_active"]};
            color: {COLORS["text_primary"]};
            font-weight: 600;
        }}

        QLineEdit:disabled,
        QComboBox:disabled,
        QPlainTextEdit:disabled,
        QSpinBox:disabled,
        QDoubleSpinBox:disabled {{
            background-color: {COLORS["background"]};
            color: {COLORS["text_muted"]};
            border-color: {COLORS["border"]};
        }}

        QComboBox::drop-down:disabled {{
            background-color: {COLORS["background"]};
            border-left-color: {COLORS["border"]};
        }}

        QSpinBox::up-button,
        QSpinBox::down-button,
        QDoubleSpinBox::up-button,
        QDoubleSpinBox::down-button {{
            width: {CONTROL_PADDING["spin_button"]}px;
            padding: 0;
            margin: 0;
            border-left: 1px solid {COLORS["border"]};
            background-color: {COLORS["surface"]};
        }}

        QSpinBox::up-button:hover,
        QSpinBox::down-button:hover,
        QDoubleSpinBox::up-button:hover,
        QDoubleSpinBox::down-button:hover {{
            background-color: {COLORS["nav_active"]};
        }}

        QSpinBox::up-arrow,
        QDoubleSpinBox::up-arrow {{
            image: url("{(assets / "chevron-up.svg").as_posix()}");
            width: {CONTROL_PADDING["arrow_glyph"]}px;
            height: {CONTROL_PADDING["arrow_glyph"]}px;
        }}

        QSpinBox::down-arrow,
        QDoubleSpinBox::down-arrow {{
            image: url("{(assets / "chevron-down.svg").as_posix()}");
            width: {CONTROL_PADDING["arrow_glyph"]}px;
            height: {CONTROL_PADDING["arrow_glyph"]}px;
        }}

        QLabel#fieldLabel {{
            color: {COLORS["text_secondary"]};
            font-size: {TYPOGRAPHY["field_label"]}px;
            font-weight: 500;
        }}

        QLabel#helperText,
        QLabel#locationHint {{
            color: {COLORS["text_muted"]};
            font-size: {TYPOGRAPHY["navigation"]}px;
        }}

        QLabel#advancedSectionTitle {{
            color: {COLORS["text_secondary"]};
            font-size: {TYPOGRAPHY["field_label"]}px;
            font-weight: 600;
        }}

        QLabel#targetSummary {{
            color: {COLORS["text_primary"]};
            font-size: {TYPOGRAPHY["navigation"]}px;
            font-weight: 600;
        }}

        QGroupBox {{
            border: 1px solid {COLORS["border"]};
            border-radius: {RADIUS["md"]}px;
            margin-top: {SPACING["lg"]}px;
            padding-top: {SPACING["xl"]}px;
        }}

        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            padding: 0 {SPACING["xs"]}px;
            color: {COLORS["text_secondary"]};
            font-size: {TYPOGRAPHY["field_label"]}px;
            font-weight: 600;
        }}

        QCheckBox {{
            color: {COLORS["text_secondary"]};
            spacing: {SPACING["sm"]}px;
        }}

        QCheckBox::indicator {{
            width: 14px;
            height: 14px;
            border: 1px solid {COLORS["border"]};
            border-radius: {RADIUS["sm"]}px;
            background-color: {COLORS["surface"]};
        }}

        QCheckBox::indicator:checked {{
            border-color: {COLORS["accent"]};
            background-color: {COLORS["accent"]};
        }}

        QCheckBox:focus {{
            color: {COLORS["text_primary"]};
        }}

        QToolTip {{
            background-color: {COLORS["text_primary"]};
            color: {COLORS["surface"]};
            border: 1px solid {COLORS["text_primary"]};
            padding: {SPACING["xs"]}px {SPACING["sm"]}px;
            border-radius: {RADIUS["sm"]}px;
        }}

        QRadioButton {{
            color: {COLORS["text_secondary"]};
            spacing: {SPACING["sm"]}px;
        }}

        QRadioButton::indicator {{
            width: 14px;
            height: 14px;
            border: 1px solid {COLORS["border"]};
            border-radius: 7px;
            background-color: {COLORS["surface"]};
        }}

        QRadioButton::indicator:checked {{
            border-color: {COLORS["accent"]};
            background-color: {COLORS["accent"]};
        }}

        QRadioButton:focus {{
            color: {COLORS["text_primary"]};
        }}

        QFrame#advancedDivider {{
            max-height: 1px;
            border: none;
            background-color: {COLORS["border"]};
        }}

        QLineEdit#workflowNameEdit {{
            min-height: 32px;
            padding: 0;
            border-color: transparent;
            background: transparent;
            font-size: {TYPOGRAPHY["brand"]}px;
            font-weight: 600;
        }}

        QLineEdit#workflowNameEdit:focus {{
            border-color: {COLORS["focus"]};
        }}

        QLabel#workflowPath {{
            color: {COLORS["text_muted"]};
            font-size: {TYPOGRAPHY["navigation"]}px;
        }}

        QLabel#saveState {{
            color: {COLORS["text_muted"]};
            font-size: {TYPOGRAPHY["navigation"]}px;
        }}

        QListWidget#editorStepList {{
            background: transparent;
            border: 1px solid transparent;
            outline: none;
        }}

        QListWidget#editorStepList:focus {{
            border-color: {COLORS["focus"]};
        }}

        QListWidget#editorStepList::item {{
            min-height: 36px;
            margin-bottom: {SPACING["xs"]}px;
            padding: {SPACING["sm"]}px;
            border-left: 2px solid transparent;
            background-color: {COLORS["surface"]};
            color: {COLORS["text_primary"]};
        }}

        QListWidget#editorStepList::item:selected {{
            border-left: 2px solid {COLORS["accent"]};
            background-color: {COLORS["nav_active"]};
        }}

        QSplitter#editorSplitter::handle {{
            background-color: {COLORS["border"]};
        }}

        QScrollArea#propertiesScroll {{
            border: none;
            background: transparent;
        }}

        QScrollBar:vertical {{
            width: {SPACING["sm"]}px;
            margin: 0;
            background: transparent;
        }}

        QScrollBar::handle:vertical {{
            min-height: {SPACING["xl"]}px;
            border-radius: {RADIUS["sm"]}px;
            background-color: {COLORS["border"]};
        }}

        QScrollBar::add-line:vertical,
        QScrollBar::sub-line:vertical {{
            height: 0;
            background: transparent;
        }}

        QScrollBar::add-page:vertical,
        QScrollBar::sub-page:vertical {{
            background: transparent;
        }}

        QWidget#propertiesContent {{
            background: transparent;
        }}

        QToolButton#advancedToggle {{
            min-height: {CONTROL_HEIGHT["navigation"]}px;
            border: none;
            color: {COLORS["text_secondary"]};
            text-align: left;
            font-weight: 600;
        }}

        QToolButton#advancedToggle:focus {{
            border: 1px solid {COLORS["focus"]};
            border-radius: {RADIUS["sm"]}px;
        }}

        QLabel#workflowRowName {{
            color: {COLORS["text_primary"]};
            font-size: {TYPOGRAPHY["body"]}px;
            font-weight: 500;
        }}

        QLabel#workflowRowDetail {{
            color: {COLORS["text_muted"]};
            font-size: {TYPOGRAPHY["navigation"]}px;
        }}

        QCheckBox#workflowEnabledToggle {{
            min-width: 52px;
            min-height: {CONTROL_HEIGHT["navigation"]}px;
            spacing: {SPACING["xs"]}px;
            color: {COLORS["text_muted"]};
            font-size: {TYPOGRAPHY["navigation"]}px;
            font-weight: 600;
        }}

        QCheckBox#workflowEnabledToggle::indicator {{
            width: 32px;
            height: 18px;
            image: url("{(assets / "toggle-off.svg").as_posix()}");
        }}

        QCheckBox#workflowEnabledToggle::indicator:checked {{
            image: url("{(assets / "toggle-on.svg").as_posix()}");
        }}

        QCheckBox#workflowEnabledToggle:hover {{
            color: {COLORS["text_primary"]};
        }}

        QCheckBox#workflowEnabledToggle:focus {{
            border: 1px solid {COLORS["focus"]};
            border-radius: {RADIUS["sm"]}px;
        }}
    """


class _ComboBoxPopupDelegate(QStyledItemDelegate):
    """Draw combo popup rows with tokenized selection and hover states."""

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
        if selected:
            background = COLORS["nav_active"]
        elif hovered:
            background = COLORS["background"]
        else:
            background = COLORS["surface"]

        painter.save()
        painter.fillRect(option.rect, QColor(background))
        painter.setPen(
            QColor(COLORS["text_primary"] if enabled else COLORS["text_muted"])
        )
        font = QFont(option.font)
        if selected:
            font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.drawText(
            option.rect.adjusted(SPACING["sm"], 0, -SPACING["sm"], 0),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            str(index.data(Qt.ItemDataRole.DisplayRole) or ""),
        )
        painter.restore()


def style_combo_box_popup(combo: QComboBox) -> None:
    """Apply the shared token-driven delegate to a combo box popup."""
    view = combo.view()
    view.setItemDelegate(_ComboBoxPopupDelegate(view))
    view.setMouseTracking(True)
    view.viewport().setMouseTracking(True)
