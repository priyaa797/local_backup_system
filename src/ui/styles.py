"""Application design system, CSS styling tokens, and Qt stylesheets.

Features clean typography, high contrast, sleek modern layout,
and distinctive visual states (success, warning, error, in-progress).
"""

from __future__ import annotations

APP_STYLESHEET = """
/* Base Window and Widget Styles */
QWidget {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    font-size: 13px;
    color: #1e293b;
    background-color: #f8fafc;
}

QMainWindow {
    background-color: #f8fafc;
}

/* Primary Tab Widget */
QTabWidget::pane {
    border: 1px solid #e2e8f0;
    background: #ffffff;
    border-radius: 8px;
    top: -1px;
}

QTabBar::tab {
    background: #f1f5f9;
    color: #475569;
    border: 1px solid #cbd5e1;
    border-bottom: none;
    padding: 10px 24px;
    font-weight: 600;
    font-size: 14px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 4px;
}

QTabBar::tab:selected {
    background: #ffffff;
    color: #0f172a;
    border-color: #cbd5e1;
    border-bottom: 2px solid #2563eb;
}

QTabBar::tab:hover:!selected {
    background: #e2e8f0;
    color: #1e293b;
}

/* Cards & Group Boxes */
QGroupBox {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    margin-top: 18px;
    padding: 18px 14px 14px 14px;
    font-weight: 600;
    font-size: 13px;
    color: #334155;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 0 6px;
    background-color: #ffffff;
    color: #1e293b;
}

/* Push Buttons */
QPushButton {
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: 600;
    color: #334155;
    min-height: 20px;
}

QPushButton:hover {
    background-color: #f1f5f9;
    border-color: #94a3b8;
    color: #0f172a;
}

QPushButton:pressed {
    background-color: #e2e8f0;
}

QPushButton:disabled {
    background-color: #f8fafc;
    border-color: #e2e8f0;
    color: #94a3b8;
}

/* Primary Action Button (Start Backup) */
QPushButton#primaryButton {
    background-color: #2563eb;
    color: #ffffff;
    border: none;
    font-size: 15px;
    font-weight: 700;
    padding: 12px 28px;
    border-radius: 8px;
}

QPushButton#primaryButton:hover {
    background-color: #1d4ed8;
}

QPushButton#primaryButton:pressed {
    background-color: #1e40af;
}

QPushButton#primaryButton:disabled {
    background-color: #93c5fd;
    color: #ffffff;
}

/* Destructive / Cancel Button */
QPushButton#dangerButton {
    background-color: #ef4444;
    color: #ffffff;
    border: none;
    font-weight: 700;
    padding: 10px 20px;
    border-radius: 6px;
}

QPushButton#dangerButton:hover {
    background-color: #dc2626;
}

/* Success Button */
QPushButton#successButton {
    background-color: #16a34a;
    color: #ffffff;
    border: none;
    font-weight: 700;
    padding: 8px 18px;
    border-radius: 6px;
}

QPushButton#successButton:hover {
    background-color: #15803d;
}

/* Line Edits & Spin Boxes */
QLineEdit, QSpinBox, QComboBox {
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 7px 10px;
    color: #1e293b;
    selection-background-color: #2563eb;
    selection-color: #ffffff;
}

QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    border: 1px solid #2563eb;
    outline: none;
}

QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {
    background-color: #f1f5f9;
    color: #64748b;
}

/* Tables */
QTableWidget, QTableView {
    background-color: #ffffff;
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    gridline-color: #f1f5f9;
    selection-background-color: #eff6ff;
    selection-color: #1e40af;
    color: #1e293b;
}

QHeaderView::section {
    background-color: #f8fafc;
    color: #475569;
    font-weight: 600;
    padding: 8px 10px;
    border: none;
    border-bottom: 2px solid #e2e8f0;
}

/* Progress Bars */
QProgressBar {
    border: 1px solid #e2e8f0;
    border-radius: 6px;
    background-color: #f1f5f9;
    text-align: center;
    color: #1e293b;
    font-weight: 600;
    height: 20px;
}

QProgressBar::chunk {
    background-color: #2563eb;
    border-radius: 5px;
}

/* Text Edit / Logs */
QTextEdit, QPlainTextEdit {
    background-color: #0f172a;
    color: #f8fafc;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 12px;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 8px;
}

/* Scroll Bars */
QScrollBar:vertical {
    border: none;
    background: #f1f5f9;
    width: 8px;
    border-radius: 4px;
}

QScrollBar::handle:vertical {
    background: #cbd5e1;
    min-height: 20px;
    border-radius: 4px;
}

QScrollBar::handle:vertical:hover {
    background: #94a3b8;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
"""


def get_status_badge(status: str) -> tuple[str, str]:
    """Return background and text color for a status badge."""
    st = status.lower()
    if st in ("success", "completed", "ok"):
        return "#dcfce7", "#166534"  # Green
    elif st in ("failed", "error"):
        return "#fee2e2", "#991b1b"  # Red
    elif st in ("partial_success", "warning"):
        return "#fef3c7", "#92400e"  # Amber
    elif st in ("running", "downloading", "in_progress"):
        return "#dbeafe", "#1e40af"  # Blue
    elif st in ("cancelled", "interrupted"):
        return "#f1f5f9", "#475569"  # Gray
    return "#f1f5f9", "#334155"
