"""Main Application Window for Local Backup System."""

from __future__ import annotations

import base64
from typing import Optional

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QCloseEvent, QIcon
from PySide6.QtWidgets import (
    QMainWindow,
    QMessageBox,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.storage.database import Database, get_db
from src.storage.settings_store import SettingsStore
from src.ui.home_tab import HomeTab
from src.ui.settings_tab import SettingsTab
from src.ui.styles import APP_STYLESHEET
from src.utils.logger import get_logger

logger = get_logger()


class MainWindow(QMainWindow):
    """Main window hosting Home and Settings tabs."""

    def __init__(self, parent=None, db: Optional[Database] = None) -> None:
        super().__init__(parent)
        self.db = db or get_db()
        self.settings_store = SettingsStore(self.db)

        self.setWindowTitle("Local Backup System")
        self.resize(960, 680)
        self.setStyleSheet(APP_STYLESHEET)

        self._build_ui()
        self._restore_geometry()

    def _build_ui(self) -> None:
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)

        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(12, 12, 12, 12)

        # Tab Widget
        self.tab_widget = QTabWidget()

        # Settings Tab (instantiated first so Home tab can reference its updates)
        self.settings_tab = SettingsTab(
            self,
            db=self.db,
            on_settings_changed=self._on_settings_updated,
        )

        # Home Tab
        self.home_tab = HomeTab(
            self,
            db=self.db,
            switch_to_settings_cb=lambda: self.tab_widget.setCurrentIndex(1),
        )

        self.tab_widget.addTab(self.home_tab, "Home")
        self.tab_widget.addTab(self.settings_tab, "Settings")

        layout.addWidget(self.tab_widget)

        # Status Bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self._update_status_bar()

    def _on_settings_updated(self) -> None:
        self.home_tab.refresh_dashboard()
        self._update_status_bar()

    def _update_status_bar(self) -> None:
        dest = self.settings_store.get_ssd_folder()
        dest_display = dest if dest else "No SSD destination configured"
        self.status_bar.showMessage(f"Destination: {dest_display} | Local Backup System v1.0.2")

    def _restore_geometry(self) -> None:
        geom_b64 = self.settings_store.get_settings().window_geometry
        if geom_b64:
            try:
                geom_bytes = base64.b64decode(geom_b64.encode("ascii"))
                self.restoreGeometry(QByteArray(geom_bytes))
            except Exception as exc:
                logger.debug("Could not restore window geometry: %s", exc)

    def _save_geometry(self) -> None:
        try:
            geom = self.saveGeometry()
            geom_b64 = base64.b64encode(bytes(geom)).decode("ascii")
            settings = self.settings_store.get_settings()
            settings.window_geometry = geom_b64
            self.settings_store.save_settings(settings)
        except Exception as exc:
            logger.debug("Could not save window geometry: %s", exc)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Prevent accidental closure when a backup operation is active."""
        if self.home_tab.backup_worker and self.home_tab.backup_worker.isRunning():
            confirm = QMessageBox.question(
                self,
                "Backup in Progress",
                "A backup job is currently running!\n\n"
                "Closing the application will abort the transfer and could leave an incomplete backup.\n\n"
                "Are you sure you want to exit and cancel the backup?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if confirm == QMessageBox.Yes:
                self.home_tab.backup_worker.cancel()
                self.home_tab.backup_worker.wait(3000)
                self._save_geometry()
                event.accept()
            else:
                event.ignore()
                return

        self._save_geometry()
        event.accept()
