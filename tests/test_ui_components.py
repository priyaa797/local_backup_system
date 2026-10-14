"""Automated UI tests verifying that all dialogs and tabs instantiate and render correctly."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
import pytest

from src.models.backup_artifact import BackupArtifact
from src.models.backup_job import BackupJob, JobStatus, SiteJobRecord
from src.models.server_config import ServerConfig
from src.storage.database import Database
from src.ui.history_dialog import HistoryDialog
from src.ui.home_tab import HomeTab
from src.ui.log_viewer_dialog import LogViewerDialog
from src.ui.main_window import MainWindow
from src.ui.server_dialog import ServerDialog
from src.ui.settings_tab import SettingsTab
from src.ui.summary_dialog import SummaryDialog


@pytest.fixture(scope="session")
def qapp():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


@pytest.fixture
def test_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    db = Database(db_path=db_path)
    yield db
    if db_path.exists():
        db_path.unlink()


def test_main_window_init(qapp, test_db: Database):
    window = MainWindow(db=test_db)
    assert window.windowTitle() == "Local Backup System"
    assert window.tab_widget.count() == 2
    assert window.tab_widget.tabText(0) == "Home"
    assert window.tab_widget.tabText(1) == "Settings"
    window.close()


def test_server_dialog_init(qapp):
    dialog = ServerDialog()
    assert "Add Frappe Server" in dialog.windowTitle()
    assert dialog.host_edit.text() == ""

    # Test with existing config
    cfg = ServerConfig(host="1.2.3.4", site_name="test.site", username="admin")
    edit_dialog = ServerDialog(config=cfg)
    assert "Edit Server" in edit_dialog.windowTitle()
    assert edit_dialog.host_edit.text() == "1.2.3.4"
    assert edit_dialog.site_edit.text() == "test.site"
    assert edit_dialog.user_edit.text() == "admin"


def test_summary_dialog_render(qapp):
    job = BackupJob(
        status=JobStatus.SUCCESS,
        destination_path="/backups",
        started_at="2026-10-14T10:00:00",
        completed_at="2026-10-14T10:05:00",
    )
    s = SiteJobRecord(
        site_name="riya-associate.com",
        host="192.168.1.50",
        status=JobStatus.SUCCESS,
    )
    s.artifacts.append(
        BackupArtifact(
            filename="db.sql.gz",
            artifact_type="database",
            remote_path="/remote",
            local_path="/local",
            size_bytes=1024,
            verified=True,
        )
    )
    job.site_records.append(s)
    job.compute_summary()

    dialog = SummaryDialog(job=job)
    assert dialog.table.rowCount() == 1
    assert dialog.table.item(0, 1).text() == "riya-associate.com"


def test_history_and_log_dialogs(qapp, test_db: Database):
    hist_dlg = HistoryDialog(db=test_db)
    assert "History" in hist_dlg.windowTitle()

    log_dlg = LogViewerDialog()
    assert "Logs" in log_dlg.windowTitle()
