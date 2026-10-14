"""Tests for file transfers, .part atomic writes, progress streaming, and integrity verification."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import tempfile
from unittest.mock import MagicMock

import pytest

from src.models.backup_artifact import BackupArtifact
from src.services.transfer_service import FileTransferService
from src.services.verification_service import VerificationService


def test_download_artifact_success():
    payload = b"Hello, this is a database backup content for test!" * 50
    payload_len = len(payload)
    mock_ssh = MagicMock()
    mock_sftp = MagicMock()
    mock_ssh.open_sftp.return_value = mock_sftp

    # Stat
    mock_stat = MagicMock()
    mock_stat.st_size = payload_len
    mock_sftp.stat.return_value = mock_stat

    # Open file simulator
    class MockRemoteFile:
        def __init__(self, data):
            self.stream = io.BytesIO(data)

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            pass

        def read(self, chunk_size):
            return self.stream.read(chunk_size)

    mock_sftp.open.return_value = MockRemoteFile(payload)

    with tempfile.TemporaryDirectory() as tmp_dir:
        dest_dir = Path(tmp_dir)
        art = BackupArtifact(
            filename="db_test.sql.gz",
            artifact_type="database",
            remote_path="/remote/db_test.sql.gz",
            size_bytes=payload_len,
        )

        progress_records = []

        def on_progress(fn, done, total, spd, avg, eta):
            progress_records.append((done, total))

        transfer_svc = FileTransferService(mock_ssh)
        ok = transfer_svc.download_artifact(
            artifact=art,
            destination_dir=dest_dir,
            progress_cb=on_progress,
            chunk_size=128,
        )

        assert ok is True
        assert art.status == "downloaded"
        assert (dest_dir / "db_test.sql.gz").exists()
        assert not (dest_dir / "db_test.sql.gz.part").exists()
        assert (dest_dir / "db_test.sql.gz").read_bytes() == payload
        assert len(progress_records) > 0


def test_download_size_mismatch_fails_and_cleans_up():
    payload = b"Short payload"
    mock_ssh = MagicMock()
    mock_sftp = MagicMock()
    mock_ssh.open_sftp.return_value = mock_sftp

    mock_stat = MagicMock()
    mock_stat.st_size = 5000  # expects 5000 bytes!
    mock_sftp.stat.return_value = mock_stat

    class MockRemoteFile:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, n):
            if hasattr(self, "_read"):
                return b""
            self._read = True
            return payload

    mock_sftp.open.return_value = MockRemoteFile()

    with tempfile.TemporaryDirectory() as tmp_dir:
        dest_dir = Path(tmp_dir)
        art = BackupArtifact(
            filename="mismatch.sql.gz",
            artifact_type="database",
            remote_path="/remote/mismatch.sql.gz",
            size_bytes=5000,
        )

        transfer_svc = FileTransferService(mock_ssh)
        ok = transfer_svc.download_artifact(art, dest_dir)

        assert ok is False
        assert art.status == "failed"
        assert "Size mismatch" in art.error_message
        assert not (dest_dir / "mismatch.sql.gz").exists()
        assert not (dest_dir / "mismatch.sql.gz.part").exists()


def test_verification_sha256_and_manifest():
    with tempfile.TemporaryDirectory() as tmp_dir:
        dest_dir = Path(tmp_dir)
        test_file = dest_dir / "test_db.sql.gz"
        content = b"ERPNext database test dump data"
        test_file.write_bytes(content)

        expected_hash = hashlib.sha256(content).hexdigest()

        art = BackupArtifact(
            filename="test_db.sql.gz",
            artifact_type="database",
            remote_path="/remote/test_db.sql.gz",
            local_path=str(test_file),
            size_bytes=len(content),
        )

        # Mock SSH with matching remote sha256sum
        mock_ssh = MagicMock()
        mock_ssh.execute_command.return_value = (0, f"{expected_hash}  /remote/test_db.sql.gz\n", "")

        verifier = VerificationService(mock_ssh)
        verified = verifier.verify_artifact(art)

        assert verified is True
        assert art.verified is True
        assert art.sha256_hash == expected_hash

        # Write manifest
        manifest_path = VerificationService.write_manifest(
            site_folder=dest_dir,
            job_id="job-12345",
            site_name="riya-associate.com",
            server_host="192.168.1.50",
            artifacts=[art],
        )

        assert manifest_path.exists()
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert data["job_id"] == "job-12345"
        assert data["site_name"] == "riya-associate.com"
        assert data["all_verified"] is True
        assert len(data["artifacts"]) == 1
        assert data["artifacts"][0]["sha256"] == expected_hash
