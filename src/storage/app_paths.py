"""Application path resolution and management.

Provides consistent, cross-platform paths for storing application data,
SQLite database, rotating logs, and SSH known_hosts without requiring
administrative privileges or polluting the source tree.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "LocalBackupSystem"


def get_app_data_dir() -> Path:
    """Return the base application data directory.

    Honors LOCAL_BACKUP_SYSTEM_DATA_DIR environment variable if provided.
    On Windows: %LOCALAPPDATA%/LocalBackupSystem
    On Linux/macOS: ~/.local/share/LocalBackupSystem or ~/.LocalBackupSystem
    """
    override = os.environ.get("LOCAL_BACKUP_SYSTEM_DATA_DIR")
    if override:
        path = Path(override).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            path = Path(local_app_data) / APP_NAME
        else:
            app_data = os.environ.get("APPDATA")
            if app_data:
                path = Path(app_data) / APP_NAME
            else:
                path = Path.home() / f".{APP_NAME.lower()}"
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        # Linux / Unix
        xdg_data_home = os.environ.get("XDG_DATA_HOME")
        if xdg_data_home:
            path = Path(xdg_data_home) / APP_NAME
        else:
            path = Path.home() / ".local" / "share" / APP_NAME

    path.mkdir(parents=True, exist_ok=True)
    return path


def get_logs_dir() -> Path:
    """Return the directory for rotating application logs."""
    logs_dir = get_app_data_dir() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def get_db_path() -> Path:
    """Return the path to the SQLite application database."""
    return get_app_data_dir() / "backup_system.db"


def get_known_hosts_path() -> Path:
    """Return the path to the application's isolated known_hosts file."""
    return get_app_data_dir() / "known_hosts"
