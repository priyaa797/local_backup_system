"""Log file viewer dialog with search filtering and log directory opening."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from src.storage.app_paths import get_logs_dir
from src.utils.logger import get_current_log_file


class LogViewerDialog(QDialog):
    """Dialog allowing users to inspect redacted runtime logs."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Application Diagnostic Logs - Local Backup System")
        self.resize(850, 550)
        self.setModal(True)

        self._build_ui()
        self._load_logs()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Toolbar
        bar = QHBoxLayout()
        bar.addWidget(QLabel("Search / Filter:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Type to filter logs...")
        self.search_edit.textChanged.connect(self._apply_filter)
        bar.addWidget(self.search_edit)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._load_logs)
        bar.addWidget(self.refresh_btn)

        self.open_dir_btn = QPushButton("Open Logs Folder")
        self.open_dir_btn.clicked.connect(self._open_logs_dir)
        bar.addWidget(self.open_dir_btn)

        layout.addLayout(bar)

        # Text Viewer
        self.text_edit = QPlainTextEdit()
        self.text_edit.setReadOnly(True)
        layout.addWidget(self.text_edit)

        # Bottom Bar
        bottom = QHBoxLayout()
        self.info_label = QLabel("")
        bottom.addWidget(self.info_label)
        bottom.addStretch()

        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        bottom.addWidget(self.close_btn)

        layout.addLayout(bottom)

    def _load_logs(self) -> None:
        log_file = get_current_log_file()
        if not log_file.exists():
            self.text_edit.setPlainText("No log entries recorded yet.")
            self.info_label.setText("Log file empty.")
            return

        try:
            content = log_file.read_text(encoding="utf-8", errors="replace")
            self._full_log = content
            self._apply_filter(self.search_edit.text())
            self.info_label.setText(f"Loaded: {log_file.name} ({len(content.splitlines())} lines)")
            # Scroll to end
            self.text_edit.verticalScrollBar().setValue(
                self.text_edit.verticalScrollBar().maximum()
            )
        except Exception as exc:
            self.text_edit.setPlainText(f"Error reading log file: {exc}")

    def _apply_filter(self, query: str) -> None:
        if not hasattr(self, "_full_log"):
            return
        if not query.strip():
            self.text_edit.setPlainText(self._full_log)
        else:
            q = query.lower()
            filtered_lines = [
                line for line in self._full_log.splitlines() if q in line.lower()
            ]
            self.text_edit.setPlainText("\n".join(filtered_lines))

    def _open_logs_dir(self) -> None:
        logs_dir = get_logs_dir()
        try:
            if sys.platform == "win32":
                os.startfile(logs_dir)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(logs_dir)])
            else:
                subprocess.Popen(["xdg-open", str(logs_dir)])
        except Exception as exc:
            QMessageBox.warning(self, "Error", f"Could not open logs folder: {exc}")
