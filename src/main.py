"""Application entry point for Local Backup System."""

from __future__ import annotations

import os
import sys
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from src.storage.database import get_db
from src.ui.main_window import MainWindow
from src.utils.logger import get_logger, setup_logger

logger = setup_logger()


def exception_hook(exc_type, exc_value, exc_traceback):
    """Global exception handler to log errors cleanly instead of crashing silently."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return

    logger.critical("Uncaught application exception", exc_info=(exc_type, exc_value, exc_traceback))

    # Show friendly message if Qt event loop is active
    app = QApplication.instance()
    if app:
        QMessageBox.critical(
            None,
            "Application Error",
            f"An unexpected error occurred:\n\n{exc_value}\n\n"
            "Diagnostic details have been recorded in the application log file.",
        )


def main() -> int:
    """Initialize and run Local Backup System desktop application."""
    # Set Windows AppUserModelID for taskbar icon grouping
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "LocalBackupSystem.App.1.0"
            )
        except Exception as exc:
            logger.debug("Failed setting AppUserModelID: %s", exc)

    sys.excepthook = exception_hook

    app = QApplication(sys.argv)
    app.setApplicationName("Local Backup System")
    app.setApplicationDisplayName("Local Backup System")
    app.setOrganizationName("LocalBackupSystem")

    # Load application icon if available
    icon_path = os.path.join(os.path.dirname(__file__), "..", "assets", "icon.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    # Initialize SQLite database and crash recovery
    try:
        db = get_db()
    except Exception as exc:
        logger.exception("Failed initializing database: %s", exc)
        QMessageBox.critical(
            None,
            "Database Initialization Error",
            f"Failed to initialize local application storage:\n{exc}",
        )
        return 1

    window = MainWindow(db=db)
    window.show()

    logger.info("Local Backup System started successfully.")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
