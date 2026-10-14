"""Home Tab interface.

Provides backup system dashboard, live execution progress, transfer gauges,
and access to backup history and log records.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import time
from typing import Callable, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.models.backup_job import BackupJob, BackupStage, JobStatus
from src.models.server_config import ServerConfig
from src.services.disk_service import DiskService
from src.storage.database import Database, get_db
from src.storage.settings_store import SettingsStore
from src.ui.history_dialog import HistoryDialog
from src.ui.log_viewer_dialog import LogViewerDialog
from src.ui.styles import get_status_badge
from src.ui.summary_dialog import SummaryDialog
from src.utils.formatters import (
    format_bytes,
    format_duration,
    format_speed,
    format_timestamp,
)
from src.workers.backup_worker import BackupWorker


class HomeTab(QWidget):
    """Primary user interface tab for starting and monitoring backup jobs."""

    def __init__(
        self,
        parent=None,
        db: Optional[Database] = None,
        switch_to_settings_cb: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent)
        self.db = db or get_db()
        self.settings_store = SettingsStore(self.db)
        self.switch_to_settings_cb = switch_to_settings_cb

        self.backup_worker: Optional[BackupWorker] = None
        self.job_start_time: float = 0.0
        self.timer: QTimer = QTimer(self)
        self.timer.timeout.connect(self._update_elapsed_time)

        self._build_ui()
        self.refresh_dashboard()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(14)
        main_layout.setContentsMargins(18, 18, 18, 18)

        # -------------------------------------------------------------
        # Section 1: Overview & Status Cards
        # -------------------------------------------------------------
        cards_group = QGroupBox("System Health & Status")
        cards_layout = QHBoxLayout(cards_group)
        cards_layout.setSpacing(16)

        # Card 1: Servers
        self.card_servers = QLabel()
        self.card_servers.setStyleSheet("background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px;")
        cards_layout.addWidget(self.card_servers)

        # Card 2: Destination SSD
        self.card_ssd = QLabel()
        self.card_ssd.setStyleSheet("background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px;")
        cards_layout.addWidget(self.card_ssd)

        # Card 3: Last Backup
        self.card_last_run = QLabel()
        self.card_last_run.setStyleSheet("background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px;")
        cards_layout.addWidget(self.card_last_run)

        main_layout.addWidget(cards_group)

        # -------------------------------------------------------------
        # Section 2: Prominent Action Bar
        # -------------------------------------------------------------
        action_box = QHBoxLayout()

        self.start_backup_btn = QPushButton("▶  Start Backup Now")
        self.start_backup_btn.setObjectName("primaryButton")
        self.start_backup_btn.setFixedHeight(46)
        self.start_backup_btn.clicked.connect(self.start_backup)
        action_box.addWidget(self.start_backup_btn)

        self.cancel_backup_btn = QPushButton("⏹  Cancel Backup")
        self.cancel_backup_btn.setObjectName("dangerButton")
        self.cancel_backup_btn.setFixedHeight(46)
        self.cancel_backup_btn.setVisible(False)
        self.cancel_backup_btn.clicked.connect(self._cancel_backup)
        action_box.addWidget(self.cancel_backup_btn)

        action_box.addStretch()

        self.view_history_btn = QPushButton("Backup History...")
        self.view_history_btn.clicked.connect(self._open_history_dialog)
        action_box.addWidget(self.view_history_btn)

        self.view_logs_btn = QPushButton("View Logs...")
        self.view_logs_btn.clicked.connect(self._open_logs_dialog)
        action_box.addWidget(self.view_logs_btn)

        main_layout.addLayout(action_box)

        # -------------------------------------------------------------
        # Section 3: Real-Time Progress Dashboard
        # -------------------------------------------------------------
        self.progress_group = QGroupBox("Active Backup Progress")
        prog_layout = QVBoxLayout(self.progress_group)
        prog_layout.setSpacing(10)

        # Overall Job Progress
        overall_top = QHBoxLayout()
        self.overall_label = QLabel("Job Status: Idle")
        self.overall_label.setStyleSheet("font-weight: 700; color: #1e293b; font-size: 14px;")
        overall_top.addWidget(self.overall_label)

        self.elapsed_label = QLabel("Elapsed: 00:00")
        self.elapsed_label.setStyleSheet("font-weight: 600; color: #475569;")
        overall_top.addStretch()
        overall_top.addWidget(self.elapsed_label)
        prog_layout.addLayout(overall_top)

        self.overall_progress_bar = QProgressBar()
        self.overall_progress_bar.setRange(0, 100)
        self.overall_progress_bar.setValue(0)
        prog_layout.addWidget(self.overall_progress_bar)

        # Current File Transfer
        file_top = QHBoxLayout()
        self.file_label = QLabel("Current File: None")
        self.file_label.setStyleSheet("font-weight: 600; color: #334155;")
        file_top.addWidget(self.file_label)

        self.file_metrics_label = QLabel("Speed: -- | ETA: --")
        self.file_metrics_label.setStyleSheet("color: #475569;")
        file_top.addStretch()
        file_top.addWidget(self.file_metrics_label)
        prog_layout.addLayout(file_top)

        self.file_progress_bar = QProgressBar()
        self.file_progress_bar.setRange(0, 100)
        self.file_progress_bar.setValue(0)
        prog_layout.addWidget(self.file_progress_bar)

        # Live Event Log Console
        log_header_row = QHBoxLayout()
        log_title = QLabel("Live Execution Log:")
        log_title.setStyleSheet("font-weight: 600; color: #334155;")
        log_header_row.addWidget(log_title)
        log_header_row.addStretch()

        self.toggle_log_btn = QPushButton("Hide Log")
        self.toggle_log_btn.setFixedHeight(24)
        self.toggle_log_btn.clicked.connect(self._toggle_live_log)
        log_header_row.addWidget(self.toggle_log_btn)
        prog_layout.addLayout(log_header_row)

        self.live_log_text = QTextEdit()
        self.live_log_text.setReadOnly(True)
        self.live_log_text.setMaximumHeight(150)
        prog_layout.addWidget(self.live_log_text)

        main_layout.addWidget(self.progress_group)

        # -------------------------------------------------------------
        # Section 4: Recent Backup Activity
        # -------------------------------------------------------------
        history_group = QGroupBox("Recent Backup Executions")
        hist_layout = QVBoxLayout(history_group)

        self.recent_table = QTableWidget()
        self.recent_table.setColumnCount(6)
        self.recent_table.setHorizontalHeaderLabels(
            ["Started At", "Status", "Sites Succeeded", "Sites Failed", "Files", "Total Data"]
        )
        self.recent_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.recent_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.recent_table.setSelectionMode(QTableWidget.SingleSelection)
        self.recent_table.setMaximumHeight(160)
        self.recent_table.doubleClicked.connect(self._on_recent_row_double_click)
        hist_layout.addWidget(self.recent_table)

        main_layout.addWidget(history_group)

    def refresh_dashboard(self) -> None:
        """Update system cards and recent runs table."""
        # Servers count
        servers = self.db.get_servers()
        enabled_servers = [s for s in servers if s.enabled]
        self.card_servers.setText(
            f"<b>Frappe Servers & Sites:</b><br>"
            f"Configured: <b>{len(servers)}</b><br>"
            f"Enabled for Backup: <b>{len(enabled_servers)}</b>"
        )

        # SSD destination & space
        dest_folder = self.settings_store.get_ssd_folder()
        if not dest_folder:
            self.card_ssd.setText(
                "<b>Backup Destination:</b><br>"
                "<span style='color: #92400e; font-weight: bold;'>Not Configured</span><br>"
                "Click Settings to select SSD"
            )
        else:
            accessible, _ = DiskService.check_destination_accessibility(dest_folder)
            if accessible:
                free = DiskService.get_free_space(dest_folder)
                self.card_ssd.setText(
                    f"<b>Destination SSD:</b><br>"
                    f"Path: {Path(dest_folder).name or dest_folder}<br>"
                    f"Free Space: <b>{format_bytes(free)}</b> (Online)"
                )
            else:
                self.card_ssd.setText(
                    f"<b>Destination SSD:</b><br>"
                    f"<span style='color: #991b1b; font-weight: bold;'>SSD Inaccessible / Disconnected</span><br>"
                    f"{dest_folder}"
                )

        # Last job
        last_job = self.db.get_last_job()
        if not last_job:
            self.card_last_run.setText(
                "<b>Last Backup Job:</b><br>"
                "No previous runs recorded."
            )
        else:
            bg, fg = get_status_badge(last_job.status)
            self.card_last_run.setText(
                f"<b>Last Backup Job:</b><br>"
                f"Status: <span style='color: {fg}; font-weight: bold;'>{last_job.status.upper()}</span><br>"
                f"Time: {format_timestamp(last_job.started_at)}"
            )

        # Recent Jobs Table
        recent_jobs = self.db.get_recent_jobs(limit=10)
        self.recent_table.setRowCount(len(recent_jobs))
        self._recent_jobs_cache = recent_jobs

        for row, j in enumerate(recent_jobs):
            self.recent_table.setItem(row, 0, QTableWidgetItem(format_timestamp(j.started_at)))

            st_item = QTableWidgetItem(j.status.replace("_", " ").upper())
            bg, fg = get_status_badge(j.status)
            self.recent_table.setItem(row, 1, st_item)

            self.recent_table.setItem(row, 2, QTableWidgetItem(str(j.success_sites)))
            self.recent_table.setItem(row, 3, QTableWidgetItem(str(j.failed_sites)))
            self.recent_table.setItem(row, 4, QTableWidgetItem(str(j.total_files)))
            self.recent_table.setItem(row, 5, QTableWidgetItem(format_bytes(j.total_bytes)))

    def start_backup(self, specific_servers: Optional[list[ServerConfig]] = None) -> None:
        """Validate prerequisites and launch background backup job."""
        dest_folder = self.settings_store.get_ssd_folder().strip()
        if not dest_folder:
            msg = (
                "No backup destination folder has been selected.\n\n"
                "Please configure an external SSD destination in the Settings tab before starting a backup."
            )
            res = QMessageBox.warning(
                self,
                "Destination Missing",
                msg,
                QMessageBox.Ok | QMessageBox.Cancel,
                QMessageBox.Ok,
            )
            if res == QMessageBox.Ok and self.switch_to_settings_cb:
                self.switch_to_settings_cb()
            return

        # Check accessibility
        accessible, access_msg = DiskService.check_destination_accessibility(dest_folder)
        if not accessible:
            QMessageBox.critical(
                self,
                "Backup Destination Inaccessible",
                f"Cannot write to configured backup destination:\n\n{dest_folder}\n\n"
                f"Reason: {access_msg}\n\n"
                "Please ensure your external SSD is connected.",
            )
            return

        # Check servers
        servers = specific_servers or self.db.get_servers(enabled_only=True)
        if not servers:
            QMessageBox.warning(
                self,
                "No Servers Configured",
                "There are no enabled Frappe servers or sites configured to back up.\n\n"
                "Please configure at least one server in the Settings tab.",
            )
            if self.switch_to_settings_cb:
                self.switch_to_settings_cb()
            return

        # Check free disk space vs rough estimation (e.g. 500MB per site)
        free_bytes = DiskService.get_free_space(dest_folder)
        if free_bytes < len(servers) * 100 * 1024 * 1024:
            confirm = QMessageBox.warning(
                self,
                "Low Free Space Warning",
                f"The target drive has only {format_bytes(free_bytes)} free.\n"
                "You are attempting to back up multiple sites. Disk space might run out.\n\n"
                "Do you still wish to proceed?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if confirm != QMessageBox.Yes:
                return

        # Switch UI to Running State
        self.start_backup_btn.setVisible(False)
        self.cancel_backup_btn.setVisible(True)
        self.cancel_backup_btn.setEnabled(True)

        self.overall_progress_bar.setValue(0)
        self.file_progress_bar.setValue(0)
        self.live_log_text.clear()
        self.overall_label.setText(f"Starting backup for {len(servers)} site(s)...")

        self.job_start_time = time.perf_counter()
        self.timer.start(1000)

        # Launch Worker
        self.backup_worker = BackupWorker(
            destination_folder=dest_folder,
            servers_to_backup=servers,
            parent=self,
        )
        self.backup_worker.log_emitted.connect(self._on_log_emitted)
        self.backup_worker.stage_changed.connect(self._on_stage_changed)
        self.backup_worker.file_progress_updated.connect(self._on_file_progress)
        self.backup_worker.job_progress_updated.connect(self._on_job_progress)
        self.backup_worker.job_finished.connect(self._on_job_finished)
        self.backup_worker.start()

    def _cancel_backup(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Confirm Cancellation",
            "Are you sure you want to cancel the current backup operation?\n"
            "Any in-progress file transfer will be safely aborted.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm == QMessageBox.Yes and self.backup_worker:
            self.cancel_backup_btn.setEnabled(False)
            self.cancel_backup_btn.setText("Cancelling...")
            self.backup_worker.cancel()

    def _update_elapsed_time(self) -> None:
        if self.job_start_time > 0:
            elapsed = time.perf_counter() - self.job_start_time
            self.elapsed_label.setText(f"Elapsed: {format_duration(elapsed)}")

    def _on_log_emitted(self, msg: str) -> None:
        self.live_log_text.append(msg)

    def _on_stage_changed(self, site_name: str, stage_text: str) -> None:
        self.overall_label.setText(f"[{site_name}] {stage_text}...")

    def _on_file_progress(
        self,
        filename: str,
        transferred: int,
        total: int,
        speed: float,
        avg_speed: float,
        eta: float,
    ) -> None:
        pct = int(round((transferred / total) * 100.0)) if total > 0 else 0
        self.file_label.setText(f"Downloading: {filename} ({format_bytes(transferred)} / {format_bytes(total)})")
        self.file_metrics_label.setText(f"Speed: {format_speed(speed)} | ETA: ~{format_duration(eta)}")
        self.file_progress_bar.setValue(pct)

    def _on_job_progress(self, completed: int, total: int, pct: float) -> None:
        self.overall_progress_bar.setValue(int(round(pct)))
        self.overall_label.setText(f"Progress: {completed} of {total} sites completed ({int(round(pct))}%)")

    def _on_job_finished(self, job: BackupJob) -> None:
        self.timer.stop()
        self.start_backup_btn.setVisible(True)
        self.cancel_backup_btn.setVisible(False)
        self.cancel_backup_btn.setText("⏹  Cancel Backup")

        self.overall_label.setText(f"Job Completed: {job.status.replace('_', ' ').upper()}")
        self.refresh_dashboard()

        # Display Summary Dialog
        summary = SummaryDialog(
            job=job,
            parent=self,
            on_retry_failed_cb=self._retry_failed_sites,
            on_view_logs_cb=self._open_logs_dialog,
        )
        summary.exec()

    def _retry_failed_sites(self, server_ids: list[str]) -> None:
        all_servers = self.db.get_servers()
        failed_servers = [s for s in all_servers if s.id in server_ids]
        if failed_servers:
            self.start_backup(specific_servers=failed_servers)

    def _toggle_live_log(self) -> None:
        visible = not self.live_log_text.isVisible()
        self.live_log_text.setVisible(visible)
        self.toggle_log_btn.setText("Hide Log" if visible else "Show Log")

    def _on_recent_row_double_click(self) -> None:
        selected_rows = self.recent_table.selectionModel().selectedRows()
        if not selected_rows or not hasattr(self, "_recent_jobs_cache"):
            return
        row = selected_rows[0].row()
        if row < len(self._recent_jobs_cache):
            job = self._recent_jobs_cache[row]
            summary = SummaryDialog(job, self, on_view_logs_cb=self._open_logs_dialog)
            summary.exec()

    def _open_history_dialog(self) -> None:
        history = HistoryDialog(self, self.db)
        history.exec()

    def _open_logs_dialog(self) -> None:
        logs_dlg = LogViewerDialog(self)
        logs_dlg.exec()
