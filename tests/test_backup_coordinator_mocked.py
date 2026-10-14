"""End-to-end mocked test of BackupCoordinator workflow across multiple servers."""

from __future__ import annotations

import io
from pathlib import Path
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from src.models.backup_artifact import BackupArtifact
from src.models.backup_job import JobStatus
from src.models.server_config import ServerConfig
from src.services.backup_coordinator import BackupCoordinator
from src.storage.database import Database


class MockFile:
    def __init__(self, data: bytes):
        self.stream = io.BytesIO(data)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, n):
        return self.stream.read(n)


@pytest.fixture
def mock_env():
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test.db"
        dest_dir = Path(tmp_dir) / "ssd_backups"
        dest_dir.mkdir()
        db = Database(db_path=db_path)
        yield db, dest_dir


def test_coordinator_full_success_run(mock_env):
    db, dest_dir = mock_env

    s1 = ServerConfig(
        host="server1.erp.com",
        username="frappe",
        site_name="riya-associate.com",
        bench_path="/home/frappe/frappe-bench",
    )
    s2 = ServerConfig(
        host="server2.erp.com",
        username="frappe",
        site_name="second-site.org",
        bench_path="/home/frappe/frappe-bench",
    )
    db.save_server(s1)
    db.save_server(s2)

    coordinator = BackupCoordinator(db=db)

    # Mock SSH Service
    with patch("src.services.backup_coordinator.SSHService") as MockSSH:
        mock_ssh_inst = MagicMock()
        MockSSH.return_value = mock_ssh_inst
        mock_ssh_inst.connect.return_value = (True, "Connected")

        # Mock Frappe backup service discovery
        with patch("src.services.backup_coordinator.FrappeBackupService") as MockFrappe:
            mock_frappe_inst = MagicMock()
            MockFrappe.return_value = mock_frappe_inst

            def fake_run_backup(log_cb=None, timeout_seconds=600):
                site = mock_frappe_inst.config.site_name
                arts = [
                    BackupArtifact(
                        filename=f"20261014_114000-{site}-database.sql.gz",
                        artifact_type="database",
                        remote_path=f"/bench/sites/{site}/private/backups/20261014_114000-{site}-database.sql.gz",
                        size_bytes=1024,
                    )
                ]
                return True, "Backup OK", arts

            mock_frappe_inst.run_backup.side_effect = fake_run_backup

            # Mock File Transfer Service
            with patch("src.services.backup_coordinator.FileTransferService") as MockTransfer:
                mock_transfer_inst = MagicMock()
                MockTransfer.return_value = mock_transfer_inst

                def fake_download(artifact, destination_dir, progress_cb=None):
                    local_f = destination_dir / artifact.filename
                    local_f.write_bytes(b"dummy sql backup payload" * 50)
                    artifact.local_path = str(local_f)
                    artifact.size_bytes = local_f.stat().st_size
                    artifact.status = "downloaded"
                    return True

                mock_transfer_inst.download_artifact.side_effect = fake_download

                # Execute Job
                job = coordinator.run_backup_job(
                    destination_folder=str(dest_dir),
                    servers_to_backup=[s1, s2],
                )

                assert job.status == JobStatus.SUCCESS
                assert job.total_sites == 2
                assert job.success_sites == 2
                assert job.failed_sites == 0

                # Check database record
                db_job = db.get_job_by_id(job.id)
                assert db_job is not None
                assert db_job.status == JobStatus.SUCCESS
                assert len(db_job.site_records) == 2


def test_coordinator_partial_success_run(mock_env):
    db, dest_dir = mock_env

    s1 = ServerConfig(host="ok-server.com", site_name="site1.com")
    s2 = ServerConfig(host="broken-server.com", site_name="site2.com")

    coordinator = BackupCoordinator(db=db)

    with patch("src.services.backup_coordinator.SSHService") as MockSSH:
        def ssh_side_effect(cfg, auto_accept_new_host_keys=True):
            mock_inst = MagicMock()
            if cfg.host == "broken-server.com":
                mock_inst.connect.return_value = (False, "Network connection timed out.")
            else:
                mock_inst.connect.return_value = (True, "Connected")
            return mock_inst

        MockSSH.side_effect = ssh_side_effect

        with patch("src.services.backup_coordinator.FrappeBackupService") as MockFrappe:
            mock_frappe_inst = MagicMock()
            MockFrappe.return_value = mock_frappe_inst
            mock_frappe_inst.run_backup.return_value = (
                True,
                "OK",
                [
                    BackupArtifact(
                        filename="20261014_114000-site1.com-database.sql.gz",
                        artifact_type="database",
                        remote_path="/path/db.sql.gz",
                        size_bytes=500,
                    )
                ],
            )

            with patch("src.services.backup_coordinator.FileTransferService") as MockTransfer:
                mock_transfer_inst = MagicMock()
                MockTransfer.return_value = mock_transfer_inst

                def fake_dl(artifact, destination_dir, progress_cb=None):
                    lf = destination_dir / artifact.filename
                    lf.write_bytes(b"data" * 125)  # 500 bytes
                    artifact.local_path = str(lf)
                    artifact.size_bytes = 500
                    artifact.status = "downloaded"
                    return True

                mock_transfer_inst.download_artifact.side_effect = fake_dl

                job = coordinator.run_backup_job(
                    destination_folder=str(dest_dir),
                    servers_to_backup=[s1, s2],
                )

                assert job.status == JobStatus.PARTIAL_SUCCESS
                assert job.total_sites == 2
                assert job.success_sites == 1
                assert job.failed_sites == 1

                failed_rec = next(s for s in job.site_records if s.site_name == "site2.com")
                assert failed_rec.status == JobStatus.FAILED
                assert "timed out" in failed_rec.error_message
                assert failed_rec.suggested_action is not None
