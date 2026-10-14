"""Settings Tab interface.

Enables SSD folder selection, drive auto-detection, multi-server configuration,
retention policy management, and safety validations.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.models.server_config import ServerConfig
from src.security.credentials import delete_credential
from src.services.cleanup_service import CleanupService
from src.services.disk_service import DiskService, DriveInfo
from src.storage.database import Database, get_db
from src.storage.settings_store import AppSettings, SettingsStore
from src.ui.server_dialog import ServerDialog
from src.utils.formatters import format_bytes
from src.workers.test_connection_worker import TestConnectionWorker


class SettingsTab(QWidget):
    """Tab widget for managing storage destination, servers, and retention."""

    def __init__(
        self,
        parent=None,
        db: Optional[Database] = None,
        on_settings_changed: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent)
        self.db = db or get_db()
        self.settings_store = SettingsStore(self.db)
        self.on_settings_changed = on_settings_changed

        self.servers: List[ServerConfig] = []
        self.drives: List[DriveInfo] = []
        self.test_worker: Optional[TestConnectionWorker] = None

        self._build_ui()
        self.load_settings()

    def _build_ui(self) -> None:
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(16)
        main_layout.setContentsMargins(18, 18, 18, 18)

        # -------------------------------------------------------------
        # Section A: SSD / Destination Folder Selection
        # -------------------------------------------------------------
        ssd_group = QGroupBox("A. External SSD & Backup Destination")
        ssd_layout = QVBoxLayout(ssd_group)
        ssd_layout.setSpacing(10)

        # Path input row
        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("SSD Folder:"))
        self.ssd_path_edit = QLineEdit()
        self.ssd_path_edit.setPlaceholderText("Select destination folder on your external backup drive...")
        self.ssd_path_edit.textChanged.connect(self._on_destination_text_changed)
        path_row.addWidget(self.ssd_path_edit)

        self.browse_ssd_btn = QPushButton("Browse Folder...")
        self.browse_ssd_btn.clicked.connect(self._browse_destination_folder)
        path_row.addWidget(self.browse_ssd_btn)
        ssd_layout.addLayout(path_row)

        # Space & Health Indicator Label
        self.space_status_label = QLabel("Checking drive status...")
        self.space_status_label.setStyleSheet("font-weight: 500; color: #475569;")
        ssd_layout.addWidget(self.space_status_label)

        # Detected Drives Table
        drives_label = QLabel("Available Local Disks & Removable Volumes Detected on Windows:")
        drives_label.setStyleSheet("font-weight: 600; color: #334155;")
        ssd_layout.addWidget(drives_label)

        self.drives_table = QTableWidget()
        self.drives_table.setColumnCount(6)
        self.drives_table.setHorizontalHeaderLabels(
            ["Mount / Drive", "Volume Label", "Total Space", "Free Space", "File System", "Type"]
        )
        self.drives_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.drives_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.drives_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.drives_table.setMaximumHeight(140)
        self.drives_table.itemSelectionChanged.connect(self._on_drive_row_selected)
        ssd_layout.addWidget(self.drives_table)

        drives_btn_row = QHBoxLayout()
        self.refresh_drives_btn = QPushButton("Refresh Drives List")
        self.refresh_drives_btn.clicked.connect(self.refresh_drives)
        drives_btn_row.addWidget(self.refresh_drives_btn)

        self.use_drive_btn = QPushButton("Use Selected Drive as Backup Root")
        self.use_drive_btn.clicked.connect(self._use_selected_drive)
        drives_btn_row.addWidget(self.use_drive_btn)

        drives_btn_row.addStretch()
        ssd_layout.addLayout(drives_btn_row)

        main_layout.addWidget(ssd_group)

        # -------------------------------------------------------------
        # Section B: Remote Frappe Server Configurations
        # -------------------------------------------------------------
        server_group = QGroupBox("B. Frappe / ERPNext Remote Servers & Sites")
        server_layout = QVBoxLayout(server_group)
        server_layout.setSpacing(10)

        self.server_table = QTableWidget()
        self.server_table.setColumnCount(7)
        self.server_table.setHorizontalHeaderLabels(
            ["Server Host", "SSH User", "Auth", "Password / Key", "Frappe Site", "Enabled", "Bench Directory"]
        )
        self.server_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.server_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.server_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.server_table.doubleClicked.connect(self._edit_selected_server)
        server_layout.addWidget(self.server_table)

        # Server action buttons
        server_btn_row = QHBoxLayout()

        self.add_server_btn = QPushButton("Add Server...")
        self.add_server_btn.setObjectName("primaryButton")
        self.add_server_btn.clicked.connect(self._add_server)
        server_btn_row.addWidget(self.add_server_btn)

        self.edit_server_btn = QPushButton("Edit Server...")
        self.edit_server_btn.clicked.connect(self._edit_selected_server)
        server_btn_row.addWidget(self.edit_server_btn)

        self.test_server_btn = QPushButton("Test Connection")
        self.test_server_btn.clicked.connect(self._test_selected_server)
        server_btn_row.addWidget(self.test_server_btn)

        self.delete_server_btn = QPushButton("Delete Server")
        self.delete_server_btn.setObjectName("dangerButton")
        self.delete_server_btn.clicked.connect(self._delete_selected_server)
        server_btn_row.addWidget(self.delete_server_btn)

        server_btn_row.addStretch()
        server_layout.addLayout(server_btn_row)

        main_layout.addWidget(server_group)

        # -------------------------------------------------------------
        # Section C: Cleanup & Retention Policy
        # -------------------------------------------------------------
        retention_group = QGroupBox("C. Retention & Disk Cleanup Settings")
        ret_layout = QVBoxLayout(retention_group)
        ret_layout.setSpacing(8)

        self.retention_enable_chk = QCheckBox("Enable automatic SSD retention cleanup (Deletes older backups)")
        self.retention_enable_chk.setChecked(False)
        self.retention_enable_chk.toggled.connect(self._on_retention_toggle)
        ret_layout.addWidget(self.retention_enable_chk)

        ret_settings_row = QHBoxLayout()
        ret_settings_row.addWidget(QLabel("Cleanup Policy Mode:"))

        self.ret_mode_combo = QComboBox()
        self.ret_mode_combo.addItems(["Keep backups by Days", "Keep latest N backup Sets"])
        ret_settings_row.addWidget(self.ret_mode_combo)

        ret_settings_row.addWidget(QLabel("Retention Limit:"))
        self.ret_value_spin = QSpinBox()
        self.ret_value_spin.setRange(1, 3650)
        self.ret_value_spin.setValue(30)
        ret_settings_row.addWidget(self.ret_value_spin)

        self.ret_unit_label = QLabel("days")
        ret_settings_row.addWidget(self.ret_unit_label)

        ret_settings_row.addStretch()

        self.preview_cleanup_btn = QPushButton("Preview Cleanup (Dry Run)...")
        self.preview_cleanup_btn.clicked.connect(self._preview_cleanup)
        ret_settings_row.addWidget(self.preview_cleanup_btn)

        ret_layout.addLayout(ret_settings_row)
        main_layout.addWidget(retention_group)

        # Bottom Action Bar
        bottom_row = QHBoxLayout()
        bottom_row.addStretch()

        self.save_all_btn = QPushButton("Save Settings")
        self.save_all_btn.setObjectName("primaryButton")
        self.save_all_btn.clicked.connect(self.save_settings)
        bottom_row.addWidget(self.save_all_btn)

        main_layout.addLayout(bottom_row)

    def load_settings(self) -> None:
        """Load persistent settings from SQLite store."""
        settings = self.settings_store.get_settings()
        self.ssd_path_edit.setText(settings.ssd_folder)
        self.retention_enable_chk.setChecked(settings.retention_enabled)
        self.ret_mode_combo.setCurrentIndex(0 if settings.retention_mode == "days" else 1)
        self.ret_value_spin.setValue(
            settings.retention_days if settings.retention_mode == "days" else settings.retention_max_sets
        )
        self._on_retention_toggle(settings.retention_enabled)

        self.refresh_drives()
        self.load_servers()
        self._validate_destination()

    def load_servers(self) -> None:
        """Populate servers table from database."""
        self.servers = self.db.get_servers()
        self.server_table.setRowCount(len(self.servers))

        for row, s in enumerate(self.servers):
            self.server_table.setItem(row, 0, QTableWidgetItem(s.host))
            self.server_table.setItem(row, 1, QTableWidgetItem(s.username))
            self.server_table.setItem(row, 2, QTableWidgetItem("SSH Key" if s.auth_type == "key" else "Password"))

            cred_disp = "Key Configured" if s.auth_type == "key" else "Stored (Secure)"
            self.server_table.setItem(row, 3, QTableWidgetItem(cred_disp))
            self.server_table.setItem(row, 4, QTableWidgetItem(s.site_name))

            enabled_item = QTableWidgetItem("Yes" if s.enabled else "No")
            enabled_item.setTextAlignment(Qt.AlignCenter)
            self.server_table.setItem(row, 5, enabled_item)

            self.server_table.setItem(row, 6, QTableWidgetItem(s.bench_path))

    def refresh_drives(self) -> None:
        """Detect and refresh all local disk options."""
        self.drives = DiskService.list_available_drives()
        self.drives_table.setRowCount(len(self.drives))

        for row, d in enumerate(self.drives):
            self.drives_table.setItem(row, 0, QTableWidgetItem(d.mountpoint))
            self.drives_table.setItem(row, 1, QTableWidgetItem(d.volume_label or "(No Label)"))
            self.drives_table.setItem(row, 2, QTableWidgetItem(d.total_space_formatted))
            self.drives_table.setItem(row, 3, QTableWidgetItem(d.free_space_formatted))
            self.drives_table.setItem(row, 4, QTableWidgetItem(d.fstype or "Unknown"))

            badge = "System Drive" if d.is_system_drive else ("Removable SSD/Drive" if d.is_removable else "Fixed Drive")
            self.drives_table.setItem(row, 5, QTableWidgetItem(badge))

    def _on_drive_row_selected(self) -> None:
        selected_rows = self.drives_table.selectionModel().selectedRows()
        self.use_drive_btn.setEnabled(bool(selected_rows))

    def _use_selected_drive(self) -> None:
        selected_rows = self.drives_table.selectionModel().selectedRows()
        if not selected_rows:
            return
        row = selected_rows[0].row()
        drive = self.drives[row]

        # Require explicit confirmation if system drive is selected
        if drive.is_system_drive:
            confirm = QMessageBox.warning(
                self,
                "Confirm System Drive Selection",
                f"Drive '{drive.mountpoint}' appears to be your Windows System Drive (Operating System).\n\n"
                "It is strongly recommended to store backups on a dedicated external SSD.\n\n"
                "Are you sure you want to store backups on the system drive?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if confirm != QMessageBox.Yes:
                return

        # Default subfolder on selected drive
        backup_folder = os.path.join(drive.mountpoint, "FrappeBackups")
        self.ssd_path_edit.setText(backup_folder)
        self.save_settings()

    def _browse_destination_folder(self) -> None:
        current = self.ssd_path_edit.text() or ""
        folder = QFileDialog.getExistingDirectory(self, "Select Backup Destination Folder on SSD", current)
        if folder:
            # Check if chosen folder is on system drive
            if DiskService.is_system_drive(folder):
                confirm = QMessageBox.warning(
                    self,
                    "Warning: System Drive Selected",
                    f"The selected folder:\n{folder}\n\nis located on the primary system drive.\n"
                    "For reliability and disaster recovery, backups should reside on an external SSD.\n\n"
                    "Proceed with this destination?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if confirm != QMessageBox.Yes:
                    return

            self.ssd_path_edit.setText(folder)
            self.save_settings()

    def _on_destination_text_changed(self) -> None:
        self._validate_destination()

    def _validate_destination(self) -> None:
        path = self.ssd_path_edit.text().strip()
        if not path:
            self.space_status_label.setText("⚠ Please select a backup folder on your SSD.")
            self.space_status_label.setStyleSheet("color: #92400e; font-weight: bold;")
            return

        accessible, msg = DiskService.check_destination_accessibility(path)
        if not accessible:
            self.space_status_label.setText(f"✗ Destination Inaccessible: {msg}")
            self.space_status_label.setStyleSheet("color: #991b1b; font-weight: bold;")
        else:
            free_bytes = DiskService.get_free_space(path)
            free_str = format_bytes(free_bytes)
            is_sys = DiskService.is_system_drive(path)
            sys_warning = " ⚠ (Warning: On System Drive)" if is_sys else ""
            self.space_status_label.setText(f"✓ Ready: Destination accessible. Free Space: {free_str}{sys_warning}")
            self.space_status_label.setStyleSheet("color: #166534; font-weight: bold;")

    def _on_retention_toggle(self, enabled: bool) -> None:
        self.ret_mode_combo.setEnabled(enabled)
        self.ret_value_spin.setEnabled(enabled)
        self.preview_cleanup_btn.setEnabled(enabled)

    def _preview_cleanup(self) -> None:
        dest = self.ssd_path_edit.text().strip()
        if not dest or not os.path.exists(dest):
            QMessageBox.warning(self, "Invalid Destination", "Destination folder must exist to preview cleanup.")
            return

        mode = "days" if self.ret_mode_combo.currentIndex() == 0 else "sets"
        val = self.ret_value_spin.value()

        to_delete, total_bytes = CleanupService.preview_cleanup(
            backup_root=Path(dest),
            mode=mode,
            retention_value=val,
        )

        if not to_delete:
            QMessageBox.information(
                self,
                "Retention Preview",
                f"No backup folders are currently eligible for deletion under policy: {val} {mode}.",
            )
            return

        folder_names = "\n".join(f"• {f.name}" for f in to_delete[:20])
        if len(to_delete) > 20:
            folder_names += f"\n... and {len(to_delete) - 20} more"

        QMessageBox.information(
            self,
            "Proposed Deletions (Dry Run)",
            f"The following {len(to_delete)} folder(s) will be deleted when cleanup runs:\n\n"
            f"{folder_names}\n\n"
            f"Total space to be reclaimed: {format_bytes(total_bytes)}\n\n"
            "Note: Deletion will only execute after a successful backup run.",
        )

    def _add_server(self) -> None:
        dialog = ServerDialog(self)
        if dialog.exec():
            self.db.save_server(dialog.config)
            self.load_servers()
            if self.on_settings_changed:
                self.on_settings_changed()

    def _edit_selected_server(self) -> None:
        selected_rows = self.server_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.information(self, "Selection Required", "Please select a server row to edit.")
            return
        row = selected_rows[0].row()
        server = self.servers[row]

        dialog = ServerDialog(self, config=server)
        if dialog.exec():
            self.db.save_server(dialog.config)
            self.load_servers()
            if self.on_settings_changed:
                self.on_settings_changed()

    def _test_selected_server(self) -> None:
        selected_rows = self.server_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.information(self, "Selection Required", "Please select a server row to test.")
            return
        row = selected_rows[0].row()
        server = self.servers[row]

        self.test_server_btn.setEnabled(False)
        self.test_server_btn.setText("Testing...")

        self.test_worker = TestConnectionWorker(server, self)
        self.test_worker.test_finished.connect(
            lambda success, msg: self._on_server_test_done(server.host, success, msg)
        )
        self.test_worker.start()

    def _on_server_test_done(self, host: str, success: bool, message: str) -> None:
        self.test_server_btn.setEnabled(True)
        self.test_server_btn.setText("Test Connection")

        if success:
            QMessageBox.information(self, "Connection Test Succeeded", f"Server {host}:\n\n{message}")
        else:
            QMessageBox.critical(self, "Connection Test Failed", f"Server {host}:\n\n{message}")

    def _delete_selected_server(self) -> None:
        selected_rows = self.server_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.information(self, "Selection Required", "Please select a server row to delete.")
            return
        row = selected_rows[0].row()
        server = self.servers[row]

        confirm = QMessageBox.question(
            self,
            "Confirm Server Deletion",
            f"Are you sure you want to remove configuration for site '{server.site_name}' ({server.host})?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm == QMessageBox.Yes:
            # Delete credential reference
            if server.credential_id:
                delete_credential(server.credential_id)
            self.db.delete_server(server.id)
            self.load_servers()
            if self.on_settings_changed:
                self.on_settings_changed()

    def save_settings(self) -> None:
        """Persist current settings to SQLite."""
        settings = AppSettings(
            ssd_folder=self.ssd_path_edit.text().strip(),
            retention_enabled=self.retention_enable_chk.isChecked(),
            retention_days=self.ret_value_spin.value() if self.ret_mode_combo.currentIndex() == 0 else 30,
            retention_max_sets=self.ret_value_spin.value() if self.ret_mode_combo.currentIndex() == 1 else 10,
            retention_mode="days" if self.ret_mode_combo.currentIndex() == 0 else "sets",
        )
        self.settings_store.save_settings(settings)
        self._validate_destination()
        if self.on_settings_changed:
            self.on_settings_changed()
        QMessageBox.information(self, "Settings Saved", "Your configuration has been saved successfully.")
