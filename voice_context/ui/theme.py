from __future__ import annotations

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication


LIGHT_STYLE = """
QWidget { font-family: "Segoe UI"; font-size: 13px; color: #202124; }
QMainWindow, QDialog, QWidget#root { background: #f7f8fa; }
QFrame#sidebar { background: #eef0f2; border-right: 1px solid #d9dde2; }
QLabel#appTitle { font-size: 18px; font-weight: 650; color: #17191c; }
QLabel#sectionTitle { font-size: 14px; font-weight: 650; color: #25282d; }
QLabel#muted { color: #5a626a; }
QLabel#statusBadge { background: #e7ece9; color: #355b46; border-radius: 5px; padding: 4px 9px; font-weight: 600; }
QPushButton { background: #ffffff; border: 1px solid #cfd4da; border-radius: 5px; padding: 7px 12px; min-height: 18px; }
QPushButton:hover { background: #f2f4f6; border-color: #aeb5bd; }
QPushButton:pressed { background: #e5e8eb; }
QPushButton:disabled { color: #999fa6; background: #f1f2f3; border-color: #e0e2e4; }
QPushButton#primary { background: #176b52; color: white; border-color: #176b52; font-weight: 600; }
QPushButton#primary:hover { background: #115b45; }
QPushButton#danger { color: #9a3030; }
QPushButton#nav { text-align: left; border: none; background: transparent; padding: 9px 12px; }
QPushButton#nav:hover { background: #e1e4e7; }
QPushButton#nav:checked { background: #d7e5df; color: #164f3e; font-weight: 600; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: #ffffff; border: 1px solid #ccd1d7; border-radius: 5px; padding: 6px; selection-background-color: #27775f; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border-color: #35836b; }
QListWidget, QTableWidget { background: #ffffff; border: 1px solid #d9dde2; border-radius: 5px; gridline-color: #e6e8eb; }
QHeaderView::section { background: #f0f2f4; border: none; border-bottom: 1px solid #d5d9de; padding: 7px; font-weight: 600; }
QScrollBar:vertical { width: 10px; background: transparent; }
QScrollBar::handle:vertical { background: #c5c9ce; min-height: 28px; border-radius: 4px; }
QFrame#panel { background: #ffffff; border: 1px solid #dde0e4; border-radius: 6px; }
QFrame#referenceChip { background: #f4f6f7; border: 1px solid #d7dce0; border-radius: 5px; }
QMenu { background: #ffffff; border: 1px solid #ccd1d7; padding: 4px; }
QMenu::item { padding: 7px 28px 7px 10px; border-radius: 4px; }
QMenu::item:selected { background: #dcebe5; }
QTabBar::tab { padding: 8px 12px; }
QProgressBar { border: none; background: #dfe4e2; height: 3px; }
QProgressBar::chunk { background: #238163; }
QToolTip { background: #282c31; color: white; border: 0; padding: 5px; }
"""


DARK_STYLE = """
QWidget { font-family: "Segoe UI"; font-size: 13px; color: #e8eaed; }
QMainWindow, QDialog, QWidget#root { background: #202225; }
QFrame#sidebar { background: #181a1d; border-right: 1px solid #34383d; }
QLabel#appTitle { font-size: 18px; font-weight: 650; color: #ffffff; }
QLabel#sectionTitle { font-size: 14px; font-weight: 650; color: #f0f1f2; }
QLabel#muted { color: #b8bec5; }
QLabel#statusBadge { background: #29483d; color: #bce6d2; border-radius: 5px; padding: 4px 9px; font-weight: 600; }
QPushButton { background: #2d3035; border: 1px solid #484d54; border-radius: 5px; padding: 7px 12px; min-height: 18px; }
QPushButton:hover { background: #383c42; border-color: #656c75; }
QPushButton:pressed { background: #222529; }
QPushButton:disabled { color: #70757c; background: #282a2e; border-color: #34373b; }
QPushButton#primary { background: #258263; color: white; border-color: #258263; font-weight: 600; }
QPushButton#primary:hover { background: #2b9470; }
QPushButton#danger { color: #f0a0a0; }
QPushButton#nav { text-align: left; border: none; background: transparent; padding: 9px 12px; }
QPushButton#nav:hover { background: #282b30; }
QPushButton#nav:checked { background: #29483d; color: #c8ebdc; font-weight: 600; }
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox { background: #292c31; border: 1px solid #484d54; border-radius: 5px; padding: 6px; selection-background-color: #27775f; }
QListWidget, QTableWidget { background: #25282c; border: 1px solid #3c4045; border-radius: 5px; gridline-color: #383c41; }
QHeaderView::section { background: #2c3034; border: none; border-bottom: 1px solid #41464c; padding: 7px; font-weight: 600; }
QScrollBar:vertical { width: 10px; background: transparent; }
QScrollBar::handle:vertical { background: #555b62; min-height: 28px; border-radius: 4px; }
QFrame#panel { background: #25282c; border: 1px solid #3b3f44; border-radius: 6px; }
QFrame#referenceChip { background: #2c3034; border: 1px solid #454a50; border-radius: 5px; }
QMenu { background: #272a2e; border: 1px solid #484d54; padding: 4px; }
QMenu::item { padding: 7px 28px 7px 10px; border-radius: 4px; }
QMenu::item:selected { background: #315345; }
QProgressBar { border: none; background: #42474c; height: 3px; }
QProgressBar::chunk { background: #38a47d; }
QToolTip { background: #f1f3f4; color: #202124; border: 0; padding: 5px; }
"""


def apply_theme(app: QApplication, theme: str) -> str:
    if theme == "system":
        color = app.palette().color(QPalette.ColorRole.Window)
        theme = "dark" if color.lightness() < 128 else "light"
    app.setStyleSheet(DARK_STYLE if theme == "dark" else LIGHT_STYLE)
    return theme
