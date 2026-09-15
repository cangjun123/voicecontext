from __future__ import annotations

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication


# Modern color palette
LIGHT_COLORS = {
    "bg_primary": "#ffffff",
    "bg_secondary": "#f5f7fa",
    "bg_tertiary": "#eef1f5",
    "fg_primary": "#1a1d21",
    "fg_secondary": "#4a5568",
    "fg_muted": "#718096",
    "accent": "#0d9488",
    "accent_hover": "#0f766e",
    "accent_light": "#ccfbf1",
    "border": "#e2e8f0",
    "border_focus": "#0d9488",
    "danger": "#dc2626",
    "danger_light": "#fef2f2",
    "success": "#16a34a",
    "warning": "#d97706",
    "shadow": "rgba(0, 0, 0, 0.05)",
}

DARK_COLORS = {
    "bg_primary": "#1a1d23",
    "bg_secondary": "#23272f",
    "bg_tertiary": "#2d333b",
    "fg_primary": "#f3f4f6",
    "fg_secondary": "#d1d5db",
    "fg_muted": "#9ca3af",
    "accent": "#14b8a6",
    "accent_hover": "#2dd4bf",
    "accent_light": "#134e4a",
    "border": "#374151",
    "border_focus": "#14b8a6",
    "danger": "#ef4444",
    "danger_light": "#7f1d1d",
    "success": "#22c55e",
    "warning": "#f59e0b",
    "shadow": "rgba(0, 0, 0, 0.3)",
}


def _get_style(colors: dict[str, str]) -> str:
    return f"""
/* Base */
QWidget {{
    font-family: "Segoe UI Variable", "Segoe UI", "Microsoft YaHei UI", sans-serif;
    font-size: 13px;
    color: {colors['fg_primary']};
}}

QMainWindow, QDialog, QWidget#root {{
    background: {colors['bg_secondary']};
}}

/* Sidebar */
QFrame#sidebar {{
    background: {colors['bg_primary']};
    border-right: 1px solid {colors['border']};
}}

QLabel#appTitle {{
    font-size: 20px;
    font-weight: 700;
    color: {colors['fg_primary']};
    letter-spacing: -0.5px;
}}

QLabel#sectionTitle {{
    font-size: 13px;
    font-weight: 600;
    color: {colors['fg_secondary']};
    text-transform: uppercase;
    letter-spacing: 0.5px;
}}

QLabel#muted {{
    color: {colors['fg_muted']};
    font-size: 12px;
}}

QLabel#statusBadge {{
    background: {colors['accent_light']};
    color: {colors['accent']};
    border-radius: 12px;
    padding: 4px 12px;
    font-weight: 600;
    font-size: 12px;
}}

/* Buttons */
QPushButton {{
    background: {colors['bg_primary']};
    border: 1px solid {colors['border']};
    border-radius: 6px;
    padding: 8px 16px;
    min-height: 20px;
    font-weight: 500;
}}

QPushButton:hover {{
    background: {colors['bg_tertiary']};
    border-color: {colors['fg_muted']};
}}

QPushButton:pressed {{
    background: {colors['bg_secondary']};
}}

QPushButton:disabled {{
    color: {colors['fg_muted']};
    background: {colors['bg_secondary']};
    border-color: {colors['border']};
}}

QPushButton#primary {{
    background: {colors['accent']};
    color: white;
    border-color: {colors['accent']};
    font-weight: 600;
}}

QPushButton#primary:hover {{
    background: {colors['accent_hover']};
    border-color: {colors['accent_hover']};
}}

QPushButton#danger {{
    color: {colors['danger']};
    border-color: {colors['danger']};
}}

QPushButton#danger:hover {{
    background: {colors['danger_light']};
}}

QPushButton#nav {{
    text-align: left;
    border: none;
    background: transparent;
    padding: 10px 14px;
    border-radius: 6px;
    font-weight: 500;
    color: {colors['fg_secondary']};
}}

QPushButton#nav:hover {{
    background: {colors['bg_tertiary']};
    color: {colors['fg_primary']};
}}

QPushButton#nav:checked {{
    background: {colors['accent_light']};
    color: {colors['accent']};
    font-weight: 600;
}}

/* Inputs */
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {colors['bg_primary']};
    border: 1px solid {colors['border']};
    border-radius: 6px;
    padding: 8px 12px;
    selection-background-color: {colors['accent']};
    selection-color: white;
}}

QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus {{
    border-color: {colors['border_focus']};
    outline: none;
}}

QLineEdit::placeholder, QTextEdit::placeholder, QPlainTextEdit::placeholder {{
    color: {colors['fg_muted']};
}}

/* Lists and Tables */
QListWidget, QTableWidget {{
    background: {colors['bg_primary']};
    border: 1px solid {colors['border']};
    border-radius: 8px;
    gridline-color: {colors['border']};
}}

QListWidget::item, QTableWidget::item {{
    padding: 8px;
    border-bottom: 1px solid {colors['border']};
}}

QListWidget::item:selected, QTableWidget::item:selected {{
    background: {colors['accent_light']};
    color: {colors['accent']};
}}

/* Settings sidebar navigation */
QListWidget#settingsNav {{
    background: {colors['bg_secondary']};
    border: none;
    border-right: 1px solid {colors['border']};
    border-radius: 0;
    outline: none;
}}

QListWidget#settingsNav::item {{
    padding: 12px 16px;
    border: none;
    border-radius: 8px;
    margin: 2px 8px;
    color: {colors['fg_secondary']};
    font-weight: 500;
}}

QListWidget#settingsNav::item:hover {{
    background: {colors['bg_tertiary']};
    color: {colors['fg_primary']};
}}

QListWidget#settingsNav::item:selected {{
    background: {colors['accent_light']};
    color: {colors['accent']};
    font-weight: 600;
}}

QHeaderView::section {{
    background: {colors['bg_secondary']};
    border: none;
    border-bottom: 2px solid {colors['border']};
    padding: 10px 8px;
    font-weight: 600;
    font-size: 12px;
    color: {colors['fg_secondary']};
}}

/* Scrollbars */
QScrollBar:vertical {{
    width: 12px;
    background: transparent;
    margin: 4px;
}}

QScrollBar::handle:vertical {{
    background: {colors['border']};
    min-height: 32px;
    border-radius: 6px;
}}

QScrollBar::handle:vertical:hover {{
    background: {colors['fg_muted']};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}

/* Panels and Cards */
QFrame#panel {{
    background: {colors['bg_primary']};
    border: 1px solid {colors['border']};
    border-radius: 12px;
}}

QFrame#referenceChip {{
    background: {colors['bg_secondary']};
    border: 1px solid {colors['border']};
    border-radius: 8px;
}}

QFrame#referenceChip:hover {{
    border-color: {colors['accent']};
}}

/* Menus */
QMenu {{
    background: {colors['bg_primary']};
    border: 1px solid {colors['border']};
    border-radius: 8px;
    padding: 6px;
}}

QMenu::item {{
    padding: 8px 32px 8px 12px;
    border-radius: 6px;
}}

QMenu::item:selected {{
    background: {colors['accent_light']};
    color: {colors['accent']};
}}

/* Tabs */
QTabBar::tab {{
    padding: 10px 16px;
    border-radius: 6px 6px 0 0;
    margin-right: 4px;
}}

QTabBar::tab:selected {{
    background: {colors['bg_primary']};
    border: 1px solid {colors['border']};
    border-bottom: none;
}}

/* Progress */
QProgressBar {{
    border: none;
    background: {colors['bg_tertiary']};
    height: 4px;
    border-radius: 2px;
}}

QProgressBar::chunk {{
    background: {colors['accent']};
    border-radius: 2px;
}}

/* Tooltips */
QToolTip {{
    background: {colors['bg_primary']};
    color: {colors['fg_primary']};
    border: 1px solid {colors['border']};
    border-radius: 6px;
    padding: 8px 12px;
    font-size: 12px;
}}

/* Splitter */
QSplitter::handle {{
    background: {colors['border']};
}}

QSplitter::handle:horizontal {{
    width: 2px;
}}

QSplitter::handle:vertical {{
    height: 2px;
}}
"""


LIGHT_STYLE = _get_style(LIGHT_COLORS)
DARK_STYLE = _get_style(DARK_COLORS)


def apply_theme(app: QApplication, theme: str) -> str:
    if theme == "system":
        color = app.palette().color(QPalette.ColorRole.Window)
        theme = "dark" if color.lightness() < 128 else "light"
    app.setStyleSheet(DARK_STYLE if theme == "dark" else LIGHT_STYLE)
    return theme
