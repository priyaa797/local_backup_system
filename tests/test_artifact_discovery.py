"""Tests for Frappe backup command execution and artifact discovery."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.models.server_config import ServerConfig
from src.services.frappe_backup_service import FrappeBackupService


def test_site_name_security_validation():
    cfg = ServerConfig(site_name="riya-associate.com")
    svc = FrappeBackupService(MagicMock(), cfg)
    assert svc.validate_site_name() is True

    cfg.site_name = "site1.local"
    assert svc.validate_site_name() is True

    # Dangerous inputs
    bad_sites = [
        "site; rm -rf /",
        "site && whoami",
        "site `reboot`",
        "site$(cat /etc/passwd)",
        "site|nc -e /bin/bash",
    ]
    for bad in bad_sites:
        cfg.site_name = bad
        assert svc.validate_site_name() is False


def test_artifact_discovery_from_bench_output():
    cfg = ServerConfig(
        site_name="riya-associate.com",
        bench_path="/home/frappe/frappe-bench",
    )
    mock_ssh = MagicMock()

    # Mock SFTP attributes
    mock_sftp = MagicMock()
    mock_ssh.open_sftp.return_value = mock_sftp

    def make_attr(filename, size):
        a = MagicMock()
        a.filename = filename
        a.st_size = size
        a.st_mtime = 1700000000
        return a

    mock_sftp.listdir_attr.return_value = [
        make_attr("20261014_114000-riya_associate_com-database.sql.gz", 25000000),
        make_attr("20261014_114000-riya_associate_com-files.tar", 12000000),
        make_attr("20261014_114000-riya_associate_com-private-files.tar", 8000000),
        make_attr("20261014_114000-riya_associate_com-site_config_backup.json", 1500),
        make_attr("older_backup_database.sql.gz", 10000000),
    ]

    stdout = """
Backup for site riya-associate.com has been successfully completed
Database    : ./sites/riya-associate.com/private/backups/20261014_114000-riya_associate_com-database.sql.gz
Public Files: ./sites/riya-associate.com/private/backups/20261014_114000-riya_associate_com-files.tar
Private Files: ./sites/riya-associate.com/private/backups/20261014_114000-riya_associate_com-private-files.tar
Backup Config: ./sites/riya-associate.com/private/backups/20261014_114000-riya_associate_com-site_config_backup.json
*** Backup completed successfully ***
"""

    mock_ssh.execute_command.return_value = (0, stdout, "")

    svc = FrappeBackupService(mock_ssh, cfg)
    success, msg, artifacts = svc.run_backup()

    assert success is True
    assert len(artifacts) == 4

    db_art = next((a for a in artifacts if a.artifact_type == "database"), None)
    assert db_art is not None
    assert db_art.size_bytes == 25000000
    assert "database.sql.gz" in db_art.filename

    pub_art = next((a for a in artifacts if a.artifact_type == "files"), None)
    assert pub_art is not None
    assert pub_art.size_bytes == 12000000


def test_missing_database_backup_fails():
    cfg = ServerConfig(site_name="site1.local", bench_path="/bench")
    mock_ssh = MagicMock()
    mock_sftp = MagicMock()
    mock_ssh.open_sftp.return_value = mock_sftp
    mock_sftp.listdir_attr.return_value = []

    # Command ran with exit 0, but no database was output
    mock_ssh.execute_command.return_value = (0, "Backup completed without database", "")

    svc = FrappeBackupService(mock_ssh, cfg)
    success, msg, artifacts = svc.run_backup()

    assert success is False
    assert "No database backup file was identified" in msg
    assert artifacts == []
