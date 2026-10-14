"""Regression tests for remote path resolution, SFTP diagnostics, and pre-transfer validation."""

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
from src.services.frappe_backup_service import FrappeBackupService
from src.services.transfer_service import FileTransferService
from src.storage.database import Database


class FakeSFTPClient:
    """In-memory simulated SFTP server for testing path resolution and file transfers."""

    def __init__(self, remote_files: dict[str, bytes], cwd: str = "/root"):
        self.files = remote_files
        self.cwd = cwd

    def normalize(self, path: str) -> str:
        if path == ".":
            return self.cwd
        return path

    def stat(self, path: str):
        if path in self.files:
            m = MagicMock()
            m.st_size = len(self.files[path])
            m.st_mtime = 1728929500
            return m
        raise FileNotFoundError(2, "No such file", path)

    def listdir_attr(self, path: str):
        res = []
        prefix = path.rstrip("/") + "/"
        for fpath, data in self.files.items():
            if fpath.startswith(prefix):
                fname = fpath[len(prefix):]
                if "/" not in fname:
                    m = MagicMock()
                    m.filename = fname
                    m.st_size = len(data)
                    m.st_mtime = 1728929500
                    res.append(m)
        return res

    def open(self, path: str, mode: str = "rb"):
        if path in self.files:
            return FakeRemoteFile(self.files[path])
        raise FileNotFoundError(2, "No such file", path)

    def close(self):
        pass


class FakeRemoteFile:
    def __init__(self, data: bytes):
        self.stream = io.BytesIO(data)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, n: int = -1):
        return self.stream.read(n)


def test_normalize_remote_path_variants():
    """Verify _normalize_remote_path handles relative, absolute, and missing 'sites/' prefixes."""
    bench = "/root/frappe-bench"
    site = "riya-associate.com"
    fn = "20261014_184935-riya-associate_com-site_config_backup.json"
    expected = f"{bench}/sites/{site}/private/backups/{fn}"

    # 1. Exact production output: ./<site>/private/backups/...
    p1 = f"./{site}/private/backups/{fn}"
    assert FrappeBackupService._normalize_remote_path(p1, bench, site, fn) == expected

    # 2. Output with sites/: ./sites/<site>/private/backups/...
    p2 = f"./sites/{site}/private/backups/{fn}"
    assert FrappeBackupService._normalize_remote_path(p2, bench, site, fn) == expected

    # 3. Absolute path already correct: /root/frappe-bench/sites/<site>/...
    p3 = f"/root/frappe-bench/sites/{site}/private/backups/{fn}"
    assert FrappeBackupService._normalize_remote_path(p3, bench, site, fn) == expected

    # 4. Absolute path missing sites/: /root/frappe-bench/<site>/...
    p4 = f"/root/frappe-bench/{site}/private/backups/{fn}"
    assert FrappeBackupService._normalize_remote_path(p4, bench, site, fn) == expected

    # 5. Relative subpath: private/backups/...
    p5 = f"private/backups/{fn}"
    assert FrappeBackupService._normalize_remote_path(p5, bench, site, fn) == expected

    # 6. Just filename or empty
    assert FrappeBackupService._normalize_remote_path(fn, bench, site, fn) == expected
    assert FrappeBackupService._normalize_remote_path("", bench, site, fn) == expected


def test_discover_artifacts_with_production_relative_paths():
    """Verify discovery converts ./site/... relative paths into verified /bench/sites/... paths."""
    cfg = ServerConfig(
        site_name="riya-associate.com",
        bench_path="/root/frappe-bench",
    )
    mock_ssh = MagicMock()

    fn_cfg = "20261014_184935-riya-associate_com-site_config_backup.json"
    fn_db = "20261014_184935-riya-associate_com-database.sql.gz"
    fn_files = "20261014_184935-riya-associate_com-files.tar"
    fn_priv = "20261014_184935-riya-associate_com-private-files.tar"

    remote_dir = "/root/frappe-bench/sites/riya-associate.com/private/backups"
    fake_sftp = FakeSFTPClient(
        remote_files={
            f"{remote_dir}/{fn_cfg}": b'{"some": "config"}',
            f"{remote_dir}/{fn_db}": b"database sql dump" * 100,
            f"{remote_dir}/{fn_files}": b"public files tar" * 200,
            f"{remote_dir}/{fn_priv}": b"private files tar" * 150,
        },
        cwd="/root",
    )
    mock_ssh.open_sftp.return_value = fake_sftp

    # Production stdout output: ./riya-associate.com/private/backups/...
    stdout = f"""Backup Summary for site riya-associate.com at 2026-10-14 18:49:35
Config   : ./{cfg.site_name}/private/backups/{fn_cfg}
Database : ./{cfg.site_name}/private/backups/{fn_db}
Public   : ./{cfg.site_name}/private/backups/{fn_files}
Private  : ./{cfg.site_name}/private/backups/{fn_priv}
"""
    mock_ssh.execute_command.return_value = (0, stdout, "")

    svc = FrappeBackupService(mock_ssh, cfg)
    success, msg, artifacts = svc.run_backup()

    assert success is True
    assert len(artifacts) == 4

    # Every artifact must have a verified absolute remote path containing /sites/
    for art in artifacts:
        assert art.remote_path.startswith("/root/frappe-bench/sites/riya-associate.com/private/backups/")
        assert art.filename in art.remote_path
        assert art.status != "failed"

    # Verify each specific artifact type
    cfg_art = next(a for a in artifacts if a.artifact_type == "config")
    assert cfg_art.remote_path == f"{remote_dir}/{fn_cfg}"
    assert cfg_art.size_bytes == len(b'{"some": "config"}')

    db_art = next(a for a in artifacts if a.artifact_type == "database")
    assert db_art.remote_path == f"{remote_dir}/{fn_db}"


def test_pre_transfer_validation_all_exist():
    """Verify validate_remote_artifacts returns True when all 4 artifacts exist on SFTP."""
    remote_dir = "/root/frappe-bench/sites/riya-associate.com/private/backups"
    fake_sftp = FakeSFTPClient(
        remote_files={
            f"{remote_dir}/config.json": b"conf",
            f"{remote_dir}/db.sql.gz": b"db",
            f"{remote_dir}/files.tar": b"files",
            f"{remote_dir}/private.tar": b"priv",
        },
        cwd="/root",
    )
    mock_ssh = MagicMock()
    mock_ssh.open_sftp.return_value = fake_sftp

    artifacts = [
        BackupArtifact(filename="config.json", artifact_type="config", remote_path=f"{remote_dir}/config.json", size_bytes=4),
        BackupArtifact(filename="db.sql.gz", artifact_type="database", remote_path=f"{remote_dir}/db.sql.gz", size_bytes=2),
        BackupArtifact(filename="files.tar", artifact_type="files", remote_path=f"{remote_dir}/files.tar", size_bytes=5),
        BackupArtifact(filename="private.tar", artifact_type="private_files", remote_path=f"{remote_dir}/private.tar", size_bytes=4),
    ]

    transfer_svc = FileTransferService(mock_ssh)
    valid, err = transfer_svc.validate_remote_artifacts(artifacts)

    assert valid is True
    assert err is None


def test_pre_transfer_validation_fails_on_missing_config():
    """Verify validate_remote_artifacts aborts immediately with clear diagnostic when an artifact is missing."""
    remote_dir = "/root/frappe-bench/sites/riya-associate.com/private/backups"
    fake_sftp = FakeSFTPClient(
        remote_files={
            # config is missing!
            f"{remote_dir}/db.sql.gz": b"db",
            f"{remote_dir}/files.tar": b"files",
        },
        cwd="/root",
    )
    mock_ssh = MagicMock()
    mock_ssh.open_sftp.return_value = fake_sftp

    artifacts = [
        BackupArtifact(filename="config.json", artifact_type="config", remote_path=f"{remote_dir}/config.json", size_bytes=100),
        BackupArtifact(filename="db.sql.gz", artifact_type="database", remote_path=f"{remote_dir}/db.sql.gz", size_bytes=1000),
        BackupArtifact(filename="files.tar", artifact_type="files", remote_path=f"{remote_dir}/files.tar", size_bytes=5000),
    ]

    transfer_svc = FileTransferService(mock_ssh)
    valid, err = transfer_svc.validate_remote_artifacts(artifacts)

    assert valid is False
    assert err is not None
    assert "Pre-transfer validation failed for config.json" in err
    assert "[Errno 2] No such file" in err
    assert "SFTP cwd: '/root'" in err
    assert artifacts[0].status == "failed"


def test_download_artifact_with_fake_sftp():
    """Verify download_artifact downloads from verified remote path and cleans up .part file."""
    remote_path = "/root/frappe-bench/sites/riya-associate.com/private/backups/site_config.json"
    content = b'{"site_name": "riya-associate.com", "db_name": "erp_prod"}'
    fake_sftp = FakeSFTPClient(
        remote_files={remote_path: content},
        cwd="/root",
    )
    mock_ssh = MagicMock()
    mock_ssh.open_sftp.return_value = fake_sftp

    with tempfile.TemporaryDirectory() as tmp_dir:
        dest_dir = Path(tmp_dir)
        art = BackupArtifact(
            filename="site_config.json",
            artifact_type="config",
            remote_path=remote_path,
            size_bytes=len(content),
        )

        transfer_svc = FileTransferService(mock_ssh)
        ok = transfer_svc.download_artifact(art, dest_dir)

        assert ok is True
        assert art.status == "downloaded"
        final_file = dest_dir / "site_config.json"
        assert final_file.exists()
        assert not (dest_dir / "site_config.json.part").exists()
        assert final_file.read_bytes() == content


def test_download_artifact_stat_failure_diagnostics():
    """Verify download_artifact produces clear diagnostic with attempted path and SFTP cwd on stat failure."""
    fake_sftp = FakeSFTPClient(remote_files={}, cwd="/root")
    mock_ssh = MagicMock()
    mock_ssh.open_sftp.return_value = fake_sftp

    with tempfile.TemporaryDirectory() as tmp_dir:
        dest_dir = Path(tmp_dir)
        art = BackupArtifact(
            filename="missing_config.json",
            artifact_type="config",
            remote_path="/root/frappe-bench/riya-associate.com/private/backups/missing_config.json",
            size_bytes=500,
        )

        transfer_svc = FileTransferService(mock_ssh)
        ok = transfer_svc.download_artifact(art, dest_dir)

        assert ok is False
        assert art.status == "failed"
        assert "[Errno 2] No such file" in art.error_message
        assert "SFTP cwd: '/root'" in art.error_message
        assert "/root/frappe-bench/riya-associate.com/private/backups/missing_config.json" in art.error_message


def test_coordinator_pre_transfer_validation_aborts_before_download():
    """Verify BackupCoordinator stops immediately when pre-validation fails, avoiding any large downloads."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = Path(tmp_dir) / "test.db"
        dest_dir = Path(tmp_dir) / "backups"
        dest_dir.mkdir()
        db = Database(db_path=db_path)

        s = ServerConfig(host="server1.com", site_name="riya-associate.com")
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
                        filename="config.json",
                        artifact_type="config",
                        remote_path="/nonexistent/config.json",
                        size_bytes=500,
                    ),
                    BackupArtifact(
                        filename="large_files.tar",
                        artifact_type="files",
                        remote_path="/bench/files.tar",
                        size_bytes=2_000_000_000,
                    ),
                ]
                mock_frappe.run_backup.return_value = (True, "Backup OK", arts)
                MockFrappe.return_value = mock_frappe

                with patch("src.services.backup_coordinator.FileTransferService") as MockTransfer:
                    mock_transfer = MagicMock()
                    # Pre-validation fails on config!
                    mock_transfer.validate_remote_artifacts.return_value = (
                        False,
                        "Pre-transfer validation failed for config.json: File missing at '/nonexistent/config.json' ([Errno 2] No such file)",
                    )
                    MockTransfer.return_value = mock_transfer

                    job = coordinator.run_backup_job(
                        destination_folder=str(dest_dir),
                        servers_to_backup=[s],
                    )

                    # MUST be FAILED
                    assert job.status == JobStatus.FAILED
                    rec = job.site_records[0]
                    assert rec.status == JobStatus.FAILED
                    assert "Pre-transfer validation failed for config.json" in rec.error_message

                    # Crucial: download_artifact must NEVER have been called for any file!
                    assert mock_transfer.download_artifact.call_count == 0
