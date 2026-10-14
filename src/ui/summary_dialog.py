"""Completion Summary and Diagnostic Reporting Dialog.

Displays comprehensive job metrics, per-site success/failure breakdown,
human-readable error diagnostic guidance, and actions (Open Folder, View Logs, Retry).
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
)

from src.models.backup_job import BackupJob, JobStatus
from src.ui.styles import get_status_badge
from src.utils.formatters import (
    format_bytes,
    format_duration,
    format_timestamp,
)


class SummaryDialog(QDialog):
    """Dialog displayed after backup run completes or on request from history."""

    def __init__(
        self,
        job: BackupJob,
        parent=None,
        on_retry_failed_cb: Optional[Callable[[list[str]], None]] = None,
        on_view_logs_cb: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent)
        self.job = job
        self.on_retry_failed_cb = on_retry_failed_cb
        self.on_view_logs_cb = on_view_logs_cb

        self.setWindowTitle("Backup Execution Summary - Local Backup System")
        self.resize(780, 620)
        self.setModal(True)

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        # Header Status Banner
        bg_col, text_col = get_status_badge(self.job.status)
        status_label = QLabel(f"Job Status: {self.job.status.replace('_', ' ').upper()}")
        status_label.setAlignment(Qt.AlignCenter)
        status_label.setStyleSheet(
            f"background-color: {bg_col}; color: {text_col}; font-size: 16px; font-weight: bold; "
            f"padding: 10px; border-radius: 6px;"
        )
        layout.addWidget(status_label)

        # Key Metrics Row
        metrics_box = QGroupBox("Execution Metrics")
        metrics_layout = QHBoxLayout(metrics_box)

        # Calculate duration
        start_time_str = format_timestamp(self.job.started_at)
        comp_time_str = format_timestamp(self.job.completed_at)

        dur_text = "--"
        if self.job.started_at and self.job.completed_at:
            try:
                from datetime import datetime
                t0 = datetime.fromisoformat(self.job.started_at)
                t1 = datetime.fromisoformat(self.job.completed_at)
                dur_text = format_duration((t1 - t0).total_seconds())
            except Exception:
                pass

        col1 = QVBoxLayout()
        col1.addWidget(QLabel(f"<b>Sites Total:</b> {self.job.total_sites}"))
        col1.addWidget(QLabel(f"<b>Sites Succeeded:</b> {self.job.success_sites}"))
        col1.addWidget(QLabel(f"<b>Sites Failed:</b> {self.job.failed_sites}"))
        metrics_layout.addLayout(col1)

        col2 = QVBoxLayout()
        col2.addWidget(QLabel(f"<b>Files Downloaded:</b> {self.job.total_files}"))
        col2.addWidget(QLabel(f"<b>Total Data:</b> {format_bytes(self.job.total_bytes)}"))
        col2.addWidget(QLabel(f"<b>Duration:</b> {dur_text}"))
        metrics_layout.addLayout(col2)

        col3 = QVBoxLayout()
        col3.addWidget(QLabel(f"<b>Started:</b> {start_time_str}"))
        col3.addWidget(QLabel(f"<b>Completed:</b> {comp_time_str}"))
        col3.addWidget(QLabel(f"<b>Destination:</b> {Path(self.job.destination_path).name or self.job.destination_path}"))
        metrics_layout.addLayout(col3)

        layout.addWidget(metrics_box)

        # Sites Results Table
        table_box = QGroupBox("Per-Site Status & Verification")
        table_layout = QVBoxLayout(table_box)

        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["Server Host", "Frappe Site", "Backup", "Download", "Verification", "Overall Result"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setRowCount(len(self.job.site_records))

        failed_site_ids: list[str] = []

        for row, site in enumerate(self.job.site_records):
            self.table.setItem(row, 0, QTableWidgetItem(site.host))
            self.table.setItem(row, 1, QTableWidgetItem(site.site_name))

            # Backup creation stage status
            backup_ok = "✓ Done" if site.status == JobStatus.SUCCESS or site.artifacts else "✗ Failed"
            self.table.setItem(row, 2, QTableWidgetItem(backup_ok))

            # Download status
            download_ok = "✓ Done" if any(a.local_path for a in site.artifacts) else ("Pending" if site.status == "pending" else "✗ Failed")
            self.table.setItem(row, 3, QTableWidgetItem(download_ok))

            # Verification status
            verified_count = site.verified_files_count
            tot_count = site.total_files
            ver_text = f"{verified_count}/{tot_count} Verified" if tot_count > 0 else "--"
            self.table.setItem(row, 4, QTableWidgetItem(ver_text))

            # Result item
            res_item = QTableWidgetItem(site.status.upper())
            bg, fg = get_status_badge(site.status)
            res_item.setBackground(Qt.GlobalColor.white)
            self.table.setItem(row, 5, res_item)

            if site.status == JobStatus.FAILED:
                failed_site_ids.append(site.server_id)

        table_layout.addWidget(self.table)
        layout.addWidget(table_box)

        # Failure Guidance Section (if any failures)
        failed_sites = [s for s in self.job.site_records if s.status == JobStatus.FAILED]
        if failed_sites:
            err_box = QGroupBox("Failure Explanations & Suggested Actions")
            err_layout = QVBoxLayout(err_box)

            err_text = ""
            for s in failed_sites:
                err_text += f"• [{s.site_name} on {s.host}]\n"
                err_text += f"  Failed Stage: {s.backup_stage}\n"
                err_text += f"  Explanation: {s.error_message or 'Unknown error'}\n"
                if s.suggested_action:
                    err_text += f"  Recommended Action: {s.suggested_action}\n"
                err_text += "\n"

            err_view = QTextEdit()
            err_view.setReadOnly(True)
            err_view.setPlainText(err_text)
            err_view.setMaximumHeight(130)
            err_view.setStyleSheet("background-color: #fef2f2; color: #991b1b; font-family: sans-serif;")
            err_layout.addWidget(err_view)
            layout.addWidget(err_box)

        # Action Buttons
        btn_layout = QHBoxLayout()

        self.open_folder_btn = QPushButton("Open Backup Folder")
        self.open_folder_btn.clicked.connect(self._open_destination_folder)
        btn_layout.addWidget(self.open_folder_btn)

        self.view_logs_btn = QPushButton("View Full Log")
        self.view_logs_btn.clicked.connect(self._on_view_logs)
        btn_layout.addWidget(self.view_logs_btn)

        if failed_site_ids and self.on_retry_failed_cb:
            self.retry_btn = QPushButton(f"Retry Failed Sites ({len(failed_site_ids)})")
            self.retry_btn.setObjectName("dangerButton")
            self.retry_btn.clicked.connect(lambda: self._on_retry(failed_site_ids))
            btn_layout.addWidget(self.retry_btn)

        btn_layout.addStretch()

        self.close_btn = QPushButton("Close Summary")
        self.close_btn.setObjectName("primaryButton")
        self.close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(self.close_btn)

        layout.addLayout(btn_layout)

    def _open_destination_folder(self) -> None:
        path = self.job.destination_path
        if not path or not os.path.exists(path):
            QMessageBox.warning(self, "Folder Not Found", f"Destination folder does not exist:\n{path}")
            return

        try:
            if sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:
            QMessageBox.warning(self, "Open Error", f"Could not open directory: {exc}")

    def _on_view_logs(self) -> None:
        if self.on_view_logs_cb:
            self.on_view_logs_cb()

    def _on_retry(self, site_ids: list[str]) -> None:
        self.accept()
        if self.on_retry_failed_cb:
            self.on_retry_failed_cb(site_ids)
