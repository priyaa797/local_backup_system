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

    # 1. Mock SSH Service per server
    with patch("src.services.backup_coordinator.SSHService") as MockSSH:
        def ssh_side_effect(cfg, auto_accept_new_host_keys=True):
            mock_inst = MagicMock()
            mock_inst.config = cfg
            mock_inst.connect.return_value = (True, "Connected")
            mock_inst.execute_command.return_value = (1, "", "")
            return mock_inst

        MockSSH.side_effect = ssh_side_effect

        # 2. Mock Frappe backup service discovery per server
        with patch("src.services.backup_coordinator.FrappeBackupService") as MockFrappe:
            def frappe_side_effect(ssh, server_config):
                mock_inst = MagicMock()
                mock_inst.ssh = ssh
                mock_inst.config = server_config
                site = server_config.site_name
                payload_len = 1200

                def fake_run_backup(log_cb=None, timeout_seconds=600):
                    arts = [
                        BackupArtifact(
                            filename=f"20261014_114000-{site}-database.sql.gz",
                            artifact_type="database",
                            remote_path=f"/bench/sites/{site}/private/backups/20261014_114000-{site}-database.sql.gz",
                            size_bytes=payload_len,
                        )
                    ]
                    return True, "Backup OK", arts

                mock_inst.run_backup.side_effect = fake_run_backup
                return mock_inst

            MockFrappe.side_effect = frappe_side_effect

            # 3. Mock File Transfer Service
            with patch("src.services.backup_coordinator.FileTransferService") as MockTransfer:
                def transfer_side_effect(ssh):
                    mock_inst = MagicMock()
                    mock_inst.ssh = ssh

                    def fake_download(artifact, destination_dir, progress_cb=None):
                        local_f = destination_dir / artifact.filename
                        payload = b"dummy sql backup payload" * 50  # 24 * 50 = 1200 bytes
                        local_f.write_bytes(payload)
                        artifact.local_path = str(local_f)
                        artifact.size_bytes = len(payload)
                        artifact.status = "downloaded"
                        return True

                    mock_inst.download_artifact.side_effect = fake_download
                    return mock_inst

                MockTransfer.side_effect = transfer_side_effect

                # Execute Job
                job = coordinator.run_backup_job(
                    destination_folder=str(dest_dir),
                    servers_to_backup=[s1, s2],
                )

                assert job.status == JobStatus.SUCCESS
                assert job.total_sites == 2
                assert job.success_sites == 2
                assert job.failed_sites == 0

                # Validate site 1 records and manifest
                s1_rec = next(s for s in job.site_records if s.site_name == "riya-associate.com")
                assert s1_rec.status == JobStatus.SUCCESS
                assert len(s1_rec.artifacts) == 1
                assert "riya-associate.com" in s1_rec.artifacts[0].filename
                assert Path(s1_rec.artifacts[0].local_path).exists()
                assert (Path(s1_rec.download_dir) / "backup_manifest.json").exists()

                # Validate site 2 records and manifest
                s2_rec = next(s for s in job.site_records if s.site_name == "second-site.org")
                assert s2_rec.status == JobStatus.SUCCESS
                assert len(s2_rec.artifacts) == 1
                assert "second-site.org" in s2_rec.artifacts[0].filename
                assert Path(s2_rec.artifacts[0].local_path).exists()
                assert (Path(s2_rec.download_dir) / "backup_manifest.json").exists()

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
            mock_inst.config = cfg
            if cfg.host == "broken-server.com":
                mock_inst.connect.return_value = (False, "Network connection timed out.")
            else:
                mock_inst.connect.return_value = (True, "Connected")
                mock_inst.execute_command.return_value = (1, "", "")
            return mock_inst

        MockSSH.side_effect = ssh_side_effect

        with patch("src.services.backup_coordinator.FrappeBackupService") as MockFrappe:
            def frappe_side_effect(ssh, server_config):
                mock_inst = MagicMock()
                mock_inst.ssh = ssh
                mock_inst.config = server_config
                site = server_config.site_name
                mock_inst.run_backup.return_value = (
                    True,
                    "OK",
                    [
                        BackupArtifact(
                            filename=f"20261014_114000-{site}-database.sql.gz",
                            artifact_type="database",
                            remote_path=f"/path/{site}-db.sql.gz",
                            size_bytes=500,
                        )
                    ],
                )
                return mock_inst

            MockFrappe.side_effect = frappe_side_effect

            with patch("src.services.backup_coordinator.FileTransferService") as MockTransfer:
                def transfer_side_effect(ssh):
                    mock_inst = MagicMock()
                    mock_inst.ssh = ssh

                    def fake_dl(artifact, destination_dir, progress_cb=None):
                        lf = destination_dir / artifact.filename
                        lf.write_bytes(b"data" * 125)  # 500 bytes
                        artifact.local_path = str(lf)
                        artifact.size_bytes = 500
                        artifact.status = "downloaded"
                        return True

                    mock_inst.download_artifact.side_effect = fake_dl
                    return mock_inst

                MockTransfer.side_effect = transfer_side_effect

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


def test_coordinator_full_four_artifacts_workflow(mock_env):
    """End-to-end verification of 4 artifacts (database, config, public files, private files)."""
    import json
    db, dest_dir = mock_env

    s = ServerConfig(
        host="server1.erp.com",
        username="frappe",
        site_name="riya-associate.com",
        bench_path="/home/frappe/frappe-bench",
    )
    db.save_server(s)

    coordinator = BackupCoordinator(db=db)

    with patch("src.services.backup_coordinator.SSHService") as MockSSH:
        mock_ssh = MagicMock()
        mock_ssh.config = s
        mock_ssh.connect.return_value = (True, "Connected")
        mock_ssh.execute_command.return_value = (1, "", "")
        MockSSH.return_value = mock_ssh

        with patch("src.services.backup_coordinator.FrappeBackupService") as MockFrappe:
            mock_frappe = MagicMock()
            mock_frappe.ssh = mock_ssh
            mock_frappe.config = s

            # Simulate the 4 production artifacts
            arts = [
                BackupArtifact(
                    filename="20261014_181143-riya-associate_com-site_config_backup.json",
                    artifact_type="config",
                    remote_path="/bench/sites/riya-associate.com/private/backups/20261014_181143-riya-associate_com-site_config_backup.json",
                    size_bytes=518,
                ),
                BackupArtifact(
                    filename="20261014_181143-riya-associate_com-database.sql.gz",
                    artifact_type="database",
                    remote_path="/bench/sites/riya-associate.com/private/backups/20261014_181143-riya-associate_com-database.sql.gz",
                    size_bytes=34063064,
                ),
                BackupArtifact(
                    filename="20261014_181143-riya-associate_com-files.tar",
                    artifact_type="files",
                    remote_path="/bench/sites/riya-associate.com/private/backups/20261014_181143-riya-associate_com-files.tar",
                    size_bytes=1932735283,
                ),
                BackupArtifact(
                    filename="20261014_181143-riya-associate_com-private-files.tar",
                    artifact_type="private_files",
                    remote_path="/bench/sites/riya-associate.com/private/backups/20261014_181143-riya-associate_com-private-files.tar",
                    size_bytes=575248793,
                ),
            ]
            mock_frappe.run_backup.return_value = (True, "Backup OK", arts)
            MockFrappe.return_value = mock_frappe

            with patch("src.services.backup_coordinator.FileTransferService") as MockTransfer:
                mock_transfer = MagicMock()
                mock_transfer.ssh = mock_ssh

                def fake_download(artifact, destination_dir, progress_cb=None):
                    lf = destination_dir / artifact.filename
                    # Write small representative payload without allocating gigabytes in test
                    lf.write_bytes(f"sample payload for {artifact.filename}".encode("utf-8"))
                    artifact.local_path = str(lf)
                    # Keep size_bytes reflecting the actual file for verification
                    artifact.size_bytes = lf.stat().st_size
                    artifact.status = "downloaded"
                    return True

                mock_transfer.download_artifact.side_effect = fake_download
                MockTransfer.return_value = mock_transfer

                job = coordinator.run_backup_job(
                    destination_folder=str(dest_dir),
                    servers_to_backup=[s],
                )

                assert job.status == JobStatus.SUCCESS
                assert job.total_sites == 1
                assert job.success_sites == 1
                assert job.failed_sites == 0

                rec = job.site_records[0]
                assert rec.status == JobStatus.SUCCESS
                assert len(rec.artifacts) == 4
                assert "Full backup complete" in (rec.diagnostics or "")

                # Verify manifest on disk
                manifest_file = Path(rec.download_dir) / "backup_manifest.json"
                assert manifest_file.exists()
                manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))

                assert manifest_data["all_verified"] is True
                assert manifest_data["total_artifacts"] == 4
                assert manifest_data["has_database"] is True
                assert manifest_data["has_files"] is True
                assert manifest_data["has_private_files"] is True
                assert manifest_data["has_config"] is True
                assert manifest_data["is_complete_with_files"] is True
                assert manifest_data["backup_type"] == "full"
                assert len(manifest_data["artifacts"]) == 4


def test_coordinator_fails_when_public_files_download_fails(mock_env):
    """Job must FAIL if the public files archive fails to download."""
    db, dest_dir = mock_env

    s = ServerConfig(host="server1.erp.com", site_name="riya-associate.com")
    db.save_server(s)

    coordinator = BackupCoordinator(db=db)

    with patch("src.services.backup_coordinator.SSHService") as MockSSH:
        mock_ssh = MagicMock()
        mock_ssh.config = s
        mock_ssh.connect.return_value = (True, "Connected")
        MockSSH.return_value = mock_ssh

        with patch("src.services.backup_coordinator.FrappeBackupService") as MockFrappe:
            mock_frappe = MagicMock()
            arts = [
                BackupArtifact(
                    filename="20261014_181143-riya-associate_com-database.sql.gz",
                    artifact_type="database",
                    remote_path="/bench/sites/riya-associate.com/private/backups/db.sql.gz",
                    size_bytes=34000000,
                ),
                BackupArtifact(
                    filename="20261014_181143-riya-associate_com-files.tar",
                    artifact_type="files",
                    remote_path="/bench/sites/riya-associate.com/private/backups/files.tar",
                    size_bytes=1900000000,
                ),
            ]
            mock_frappe.run_backup.return_value = (True, "Backup OK", arts)
            MockFrappe.return_value = mock_frappe

            with patch("src.services.backup_coordinator.FileTransferService") as MockTransfer:
                mock_transfer = MagicMock()

                def fake_dl(artifact, destination_dir, progress_cb=None):
                    if artifact.artifact_type == "files":
                        artifact.status = "failed"
                        artifact.error_message = "Connection reset by peer during 1.8 GiB download"
                        return False
                    lf = destination_dir / artifact.filename
                    lf.write_bytes(b"dummy db data")
                    artifact.local_path = str(lf)
                    artifact.size_bytes = len(b"dummy db data")
                    artifact.status = "downloaded"
                    return True

                mock_transfer.download_artifact.side_effect = fake_dl
                MockTransfer.return_value = mock_transfer

                job = coordinator.run_backup_job(
                    destination_folder=str(dest_dir),
                    servers_to_backup=[s],
                )

                # MUST be FAILED, NOT SUCCESS!
                assert job.status == JobStatus.FAILED
                rec = job.site_records[0]
                assert rec.status == JobStatus.FAILED
                assert "Connection reset by peer" in rec.error_message
                assert "files.tar" in rec.error_message


def test_coordinator_fails_when_private_files_verification_fails(mock_env):
    """Job must FAIL if private files archive fails verification."""
    db, dest_dir = mock_env

    s = ServerConfig(host="server1.erp.com", site_name="riya-associate.com")
    db.save_server(s)

    coordinator = BackupCoordinator(db=db)

    with patch("src.services.backup_coordinator.SSHService") as MockSSH:
        mock_ssh = MagicMock()
        mock_ssh.config = s
        mock_ssh.connect.return_value = (True, "Connected")
        MockSSH.return_value = mock_ssh

        with patch("src.services.backup_coordinator.FrappeBackupService") as MockFrappe:
            mock_frappe = MagicMock()
            arts = [
                BackupArtifact(
                    filename="20261014_181143-riya-associate_com-database.sql.gz",
                    artifact_type="database",
                    remote_path="/bench/sites/riya-associate.com/private/backups/db.sql.gz",
                    size_bytes=1000,
                ),
                BackupArtifact(
                    filename="20261014_181143-riya-associate_com-private-files.tar",
                    artifact_type="private_files",
                    remote_path="/bench/sites/riya-associate.com/private/backups/private-files.tar",
                    size_bytes=2000,
                ),
            ]
            mock_frappe.run_backup.return_value = (True, "Backup OK", arts)
            MockFrappe.return_value = mock_frappe

            with patch("src.services.backup_coordinator.FileTransferService") as MockTransfer:
                mock_transfer = MagicMock()

                def fake_dl(artifact, destination_dir, progress_cb=None):
                    lf = destination_dir / artifact.filename
                    lf.write_bytes(b"x" * artifact.size_bytes)
                    artifact.local_path = str(lf)
                    artifact.status = "downloaded"
                    return True

                mock_transfer.download_artifact.side_effect = fake_dl
                MockTransfer.return_value = mock_transfer

                with patch("src.services.backup_coordinator.VerificationService") as MockVerifier:
                    mock_verifier_inst = MagicMock()

                    def fake_verify(art):
                        if art.artifact_type == "private_files":
                            art.verified = False
                            art.error_message = "SHA-256 hash mismatch! Local: aaa vs Remote: bbb"
                            return False
                        art.verified = True
                        art.sha256_hash = "abc123hash"
                        return True

                    mock_verifier_inst.verify_artifact.side_effect = fake_verify
                    MockVerifier.return_value = mock_verifier_inst

                    job = coordinator.run_backup_job(
                        destination_folder=str(dest_dir),
                        servers_to_backup=[s],
                    )

                    # MUST be FAILED, NOT SUCCESS!
                    assert job.status == JobStatus.FAILED
                    rec = job.site_records[0]
                    assert rec.status == JobStatus.FAILED
                    assert "hash mismatch" in rec.error_message
                    assert "private-files.tar" in rec.error_message


def test_crash_recovery_marks_interrupted_jobs_and_sites(mock_env):
    """Ensure abnormal termination leaves jobs and site records marked as INTERRUPTED."""
    from src.models.backup_job import BackupJob, SiteJobRecord
    db, dest_dir = mock_env

    job = BackupJob(
        destination_path=str(dest_dir),
        status=JobStatus.RUNNING,
    )
    rec = SiteJobRecord(
        job_id=job.id,
        site_name="riya-associate.com",
        status=JobStatus.RUNNING,
    )
    job.site_records.append(rec)
    db.save_job(job)

    # Trigger recovery
    db.recover_interrupted_jobs()

    recovered_job = db.get_job_by_id(job.id)
    assert recovered_job is not None
    assert recovered_job.status == JobStatus.INTERRUPTED
    assert "terminated unexpectedly" in (recovered_job.error_summary or "")

    recovered_site = recovered_job.site_records[0]
    assert recovered_site.status == JobStatus.INTERRUPTED
