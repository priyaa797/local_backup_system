"""Backup Job History Dialog.

Lists all historical backup runs stored in SQLite and provides drill-down
into individual job reports and artifact details.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from src.models.backup_job import BackupJob
from src.storage.database import Database, get_db
from src.ui.styles import get_status_badge
from src.ui.summary_dialog import SummaryDialog
from src.utils.formatters import format_bytes, format_timestamp


class HistoryDialog(QDialog):
    """Dialog displaying historical backup execution records."""

    def __init__(self, parent=None, db: Optional[Database] = None) -> None:
        super().__init__(parent)
        self.db = db or get_db()
        self.jobs: list[BackupJob] = []

        self.setWindowTitle("Backup Job History - Local Backup System")
        self.resize(880, 520)
        self.setModal(True)

        self._build_ui()
        self._load_jobs()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        header = QLabel("Historical Backup Jobs")
        header.setStyleSheet("font-size: 15px; font-weight: bold; color: #1e293b;")
        layout.addWidget(header)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(
            ["Started At", "Status", "Sites Succeeded", "Sites Failed", "Files", "Data Transferred", "Destination"]
        )
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.doubleClicked.connect(self._view_selected_job)
        layout.addWidget(self.table)

        # Buttons
        bottom = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._load_jobs)
        bottom.addWidget(self.refresh_btn)

        self.view_btn = QPushButton("View Selected Summary")
        self.view_btn.setObjectName("primaryButton")
        self.view_btn.clicked.connect(self._view_selected_job)
        bottom.addWidget(self.view_btn)

        bottom.addStretch()

        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        bottom.addWidget(self.close_btn)

        layout.addLayout(bottom)

    def _load_jobs(self) -> None:
        self.jobs = self.db.get_recent_jobs(limit=100)
        self.table.setRowCount(len(self.jobs))

        for row, job in enumerate(self.jobs):
            self.table.setItem(row, 0, QTableWidgetItem(format_timestamp(job.started_at)))

            status_item = QTableWidgetItem(job.status.replace("_", " ").upper())
            bg, fg = get_status_badge(job.status)
            self.table.setItem(row, 1, status_item)

            self.table.setItem(row, 2, QTableWidgetItem(str(job.success_sites)))
            self.table.setItem(row, 3, QTableWidgetItem(str(job.failed_sites)))
            self.table.setItem(row, 4, QTableWidgetItem(str(job.total_files)))
            self.table.setItem(row, 5, QTableWidgetItem(format_bytes(job.total_bytes)))
            self.table.setItem(row, 6, QTableWidgetItem(job.destination_path))

    def _view_selected_job(self) -> None:
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.information(self, "Selection Required", "Please select a backup job from the table to view.")
            return

        row = selected_rows[0].row()
        job = self.jobs[row]
        summary = SummaryDialog(job, self)
        summary.exec()
