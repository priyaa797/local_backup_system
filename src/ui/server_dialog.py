"""Server and Site configuration dialog.

Provides input fields for server connection details, secure credential entry,
bench directory path, Frappe site name, and interactive connection testing.
"""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from src.models.server_config import ServerConfig
from src.security.credentials import get_credential, save_credential
from src.workers.test_connection_worker import TestConnectionWorker


class ServerDialog(QDialog):
    """Dialog for creating or editing a Frappe server and site configuration."""

    def __init__(self, parent=None, config: Optional[ServerConfig] = None) -> None:
        super().__init__(parent)
        self.config = config or ServerConfig()
        self.is_edit = config is not None
        self.test_worker: Optional[TestConnectionWorker] = None

        self.setWindowTitle("Edit Server & Site" if self.is_edit else "Add Frappe Server & Site")
        self.resize(520, 500)
        self.setModal(True)

        self._build_ui()
        self._load_data()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(14)

        form = QFormLayout()
        form.setSpacing(12)

        # Host
        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("e.g. 192.168.1.100 or erp.company.com")
        form.addRow("Server IP / Host *:", self.host_edit)

        # Port
        self.port_edit = QLineEdit("22")
        form.addRow("SSH Port *:", self.port_edit)

        # Username
        self.user_edit = QLineEdit("frappe")
        self.user_edit.setPlaceholderText("e.g. frappe, erpnext, root")
        form.addRow("SSH Username *:", self.user_edit)

        # Auth Type
        self.auth_combo = QComboBox()
        self.auth_combo.addItems(["Password", "SSH Private Key"])
        self.auth_combo.currentIndexChanged.connect(self._on_auth_type_changed)
        form.addRow("Authentication Method:", self.auth_combo)

        # Password
        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setPlaceholderText("Enter SSH password")
        self.show_pass_btn = QPushButton("Show")
        self.show_pass_btn.setFixedWidth(60)
        self.show_pass_btn.clicked.connect(self._toggle_password_visibility)

        pass_layout = QHBoxLayout()
        pass_layout.addWidget(self.password_edit)
        pass_layout.addWidget(self.show_pass_btn)
        self.pass_label = QLabel("Password *:")
        form.addRow(self.pass_label, pass_layout)

        # SSH Key Path
        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText("Path to private key (id_rsa, id_ed25519, etc.)")
        self.browse_key_btn = QPushButton("Browse...")
        self.browse_key_btn.setFixedWidth(80)
        self.browse_key_btn.clicked.connect(self._browse_key)

        key_layout = QHBoxLayout()
        key_layout.addWidget(self.key_edit)
        key_layout.addWidget(self.browse_key_btn)
        self.key_label = QLabel("Private Key File *:")
        form.addRow(self.key_label, key_layout)

        # Key Passphrase (optional)
        self.passphrase_edit = QLineEdit()
        self.passphrase_edit.setEchoMode(QLineEdit.Password)
        self.passphrase_edit.setPlaceholderText("Optional passphrase if private key is encrypted")
        self.passphrase_label = QLabel("Key Passphrase:")
        form.addRow(self.passphrase_label, self.passphrase_edit)

        # Frappe Site Name
        self.site_edit = QLineEdit()
        self.site_edit.setPlaceholderText("e.g. riya-associate.com or site1.local")
        form.addRow("Frappe Site Name *:", self.site_edit)

        # Bench Directory Path
        self.bench_edit = QLineEdit("/home/frappe/frappe-bench")
        self.bench_edit.setPlaceholderText("Full path to bench folder on server")
        form.addRow("Bench Directory *:", self.bench_edit)

        # Enabled Checkbox
        self.enabled_chk = QCheckBox("Enable this server for scheduled backups")
        self.enabled_chk.setChecked(True)
        form.addRow("", self.enabled_chk)

        layout.addLayout(form)

        # Connection Test Feedback Label
        self.test_status_label = QLabel("")
        self.test_status_label.setWordWrap(True)
        self.test_status_label.setStyleSheet("padding: 4px; font-weight: 500;")
        layout.addWidget(self.test_status_label)

        # Test Connection Button
        test_layout = QHBoxLayout()
        self.test_btn = QPushButton("Test Connection")
        self.test_btn.clicked.connect(self._run_connection_test)
        test_layout.addWidget(self.test_btn)
        test_layout.addStretch()
        layout.addLayout(test_layout)

        # Dialog Buttons (Cancel, Save)
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        self.save_btn = QPushButton("Save Server")
        self.save_btn.setObjectName("primaryButton")
        self.save_btn.clicked.connect(self._on_save)
        btn_layout.addWidget(self.save_btn)

        layout.addLayout(btn_layout)

    def _toggle_password_visibility(self) -> None:
        if self.password_edit.echoMode() == QLineEdit.Password:
            self.password_edit.setEchoMode(QLineEdit.Normal)
            self.show_pass_btn.setText("Hide")
        else:
            self.password_edit.setEchoMode(QLineEdit.Password)
            self.show_pass_btn.setText("Show")

    def _browse_key(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select SSH Private Key",
            str(os.path.expanduser("~/.ssh")),
            "SSH Keys (*.pem *.key id_* *);;All Files (*)",
        )
        if path:
            self.key_edit.setText(path)

    def _on_auth_type_changed(self, index: int) -> None:
        is_password = index == 0
        self.pass_label.setVisible(is_password)
        self.password_edit.setVisible(is_password)
        self.show_pass_btn.setVisible(is_password)

        self.key_label.setVisible(not is_password)
        self.key_edit.setVisible(not is_password)
        self.browse_key_btn.setVisible(not is_password)
        self.passphrase_label.setVisible(not is_password)
        self.passphrase_edit.setVisible(not is_password)

    def _load_data(self) -> None:
        self.host_edit.setText(self.config.host)
        self.port_edit.setText(str(self.config.port))
        self.user_edit.setText(self.config.username)
        self.site_edit.setText(self.config.site_name)
        self.bench_edit.setText(self.config.bench_path or f"/home/{self.config.username}/frappe-bench")
        self.enabled_chk.setChecked(self.config.enabled)

        if self.config.auth_type == "key":
            self.auth_combo.setCurrentIndex(1)
            self.key_edit.setText(self.config.key_path or "")
        else:
            self.auth_combo.setCurrentIndex(0)

        # Retrieve secret from credential manager
        if self.config.credential_id:
            secret = get_credential(self.config.credential_id) or ""
            if self.config.auth_type == "key":
                self.passphrase_edit.setText(secret)
            else:
                self.password_edit.setText(secret)

        self._on_auth_type_changed(self.auth_combo.currentIndex())

    def _get_temp_config(self) -> tuple[Optional[ServerConfig], Optional[str]]:
        """Extract config from form and validate."""
        host = self.host_edit.text().strip()
        try:
            port = int(self.port_edit.text().strip())
        except ValueError:
            port = 22
        user = self.user_edit.text().strip()
        auth_type = "password" if self.auth_combo.currentIndex() == 0 else "key"
        key_path = self.key_edit.text().strip() if auth_type == "key" else None
        site = self.site_edit.text().strip()
        bench = self.bench_edit.text().strip()
        enabled = self.enabled_chk.isChecked()

        secret = self.password_edit.text() if auth_type == "password" else self.passphrase_edit.text()

        temp_cfg = ServerConfig(
            id=self.config.id,
            host=host,
            port=port,
            username=user,
            auth_type=auth_type,
            credential_id=self.config.credential_id,
            key_path=key_path,
            site_name=site,
            bench_path=bench,
            enabled=enabled,
        )

        errors = temp_cfg.validate()
        if auth_type == "password" and not secret:
            errors.append("Password is required for password authentication.")

        if errors:
            QMessageBox.warning(self, "Validation Error", "\n".join(errors))
            return None, None

        return temp_cfg, secret

    def _run_connection_test(self) -> None:
        temp_cfg, secret = self._get_temp_config()
        if not temp_cfg:
            return

        # Temporarily save secret so test worker can use it
        if secret:
            save_credential(temp_cfg.credential_id, secret)

        self.test_btn.setEnabled(False)
        self.test_status_label.setText("Testing connection and bench environment... please wait.")
        self.test_status_label.setStyleSheet("color: #2563eb;")

        self.test_worker = TestConnectionWorker(temp_cfg, self)
        self.test_worker.test_finished.connect(self._on_test_finished)
        self.test_worker.start()

    def _on_test_finished(self, success: bool, message: str) -> None:
        self.test_btn.setEnabled(True)
        if success:
            self.test_status_label.setText(f"✓ {message}")
            self.test_status_label.setStyleSheet("color: #166534; font-weight: bold;")
        else:
            self.test_status_label.setText(f"✗ {message}")
            self.test_status_label.setStyleSheet("color: #991b1b; font-weight: bold;")

    def _on_save(self) -> None:
        temp_cfg, secret = self._get_temp_config()
        if not temp_cfg:
            return

        # Save secret securely in OS credential store
        if secret:
            save_credential(temp_cfg.credential_id, secret)

        self.config = temp_cfg
        self.accept()
