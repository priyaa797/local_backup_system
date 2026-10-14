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


def test_exact_production_four_artifacts_discovery():
    """Reproduce exact production bench backup --with-files scenario for riya-associate.com."""
    cfg = ServerConfig(
        site_name="riya-associate.com",
        bench_path="/home/frappe/frappe-bench",
    )
    mock_ssh = MagicMock()
    mock_sftp = MagicMock()
    mock_ssh.open_sftp.return_value = mock_sftp

    def make_attr(filename, size, mtime=1700000000):
        a = MagicMock()
        a.filename = filename
        a.st_size = size
        a.st_mtime = mtime
        return a

    # Exact 4 artifacts reported by server plus older backup files
    config_size = 518
    db_size = 34063064          # ~32.5 MiB
    files_size = 1932735283      # ~1.8 GiB
    private_files_size = 575248793  # ~548.6 MiB
    total_expected_bytes = config_size + db_size + files_size + private_files_size

    mock_sftp.listdir_attr.return_value = [
        make_attr("20261014_181143-riya-associate_com-site_config_backup.json", config_size, 1728929503),
        make_attr("20261014_181143-riya-associate_com-database.sql.gz", db_size, 1728929505),
        make_attr("20261014_181143-riya-associate_com-files.tar", files_size, 1728929550),
        make_attr("20261014_181143-riya-associate_com-private-files.tar", private_files_size, 1728929570),
        # Older backup from previous run
        make_attr("20261010_100000-riya-associate_com-database.sql.gz", 30000000, 1728500000),
        make_attr("20261010_100000-riya-associate_com-files.tar", 1000000000, 1728500010),
    ]

    # Exact stdout format produced by Frappe server
    stdout = """Backup Summary for site riya-associate.com at 2026-10-14 18:11:43
Config   : ./sites/riya-associate.com/private/backups/20261014_181143-riya-associate_com-site_config_backup.json
Database : ./sites/riya-associate.com/private/backups/20261014_181143-riya-associate_com-database.sql.gz
Public   : ./sites/riya-associate.com/private/backups/20261014_181143-riya-associate_com-files.tar
Private  : ./sites/riya-associate.com/private/backups/20261014_181143-riya-associate_com-private-files.tar
"""

    mock_ssh.execute_command.return_value = (0, stdout, "")

    svc = FrappeBackupService(mock_ssh, cfg)
    success, msg, artifacts = svc.run_backup()

    assert success is True
    # MUST discover all 4 artifacts, NOT just 1!
    assert len(artifacts) == 4
    total_size = sum(a.size_bytes for a in artifacts)
    assert total_size == total_expected_bytes
    assert total_size > 2_000_000_000  # Total ~2.37 GiB

    # Validate types
    cfg_art = next((a for a in artifacts if a.artifact_type == "config"), None)
    assert cfg_art is not None
    assert cfg_art.size_bytes == config_size
    assert "site_config_backup.json" in cfg_art.filename

    db_art = next((a for a in artifacts if a.artifact_type == "database"), None)
    assert db_art is not None
    assert db_art.size_bytes == db_size
    assert "database.sql.gz" in db_art.filename

    pub_art = next((a for a in artifacts if a.artifact_type == "files"), None)
    assert pub_art is not None
    assert pub_art.size_bytes == files_size
    assert "files.tar" in pub_art.filename

    priv_art = next((a for a in artifacts if a.artifact_type == "private_files"), None)
    assert priv_art is not None
    assert priv_art.size_bytes == private_files_size
    assert "private-files.tar" in priv_art.filename


def test_directory_fallback_discovers_all_four_when_stdout_has_only_database():
    """If bench stdout only printed Database, all 4 companion files must still be discovered via SFTP."""
    cfg = ServerConfig(
        site_name="riya-associate.com",
        bench_path="/home/frappe/frappe-bench",
    )
    mock_ssh = MagicMock()
    mock_sftp = MagicMock()
    mock_ssh.open_sftp.return_value = mock_sftp

    def make_attr(filename, size, mtime=1700000000):
        a = MagicMock()
        a.filename = filename
        a.st_size = size
        a.st_mtime = mtime
        return a

    mock_sftp.listdir_attr.return_value = [
        make_attr("20261014_181143-riya-associate_com-site_config_backup.json", 518),
        make_attr("20261014_181143-riya-associate_com-database.sql.gz", 34063064),
        make_attr("20261014_181143-riya-associate_com-files.tar", 1932735283),
        make_attr("20261014_181143-riya-associate_com-private-files.tar", 575248793),
        make_attr("20261010_100000-riya-associate_com-database.sql.gz", 30000000),
    ]

    # Stdout only has database line!
    stdout = """Database : ./sites/riya-associate.com/private/backups/20261014_181143-riya-associate_com-database.sql.gz\n"""
    mock_ssh.execute_command.return_value = (0, stdout, "")

    svc = FrappeBackupService(mock_ssh, cfg)
    success, msg, artifacts = svc.run_backup()

    assert success is True
    # All 4 matching the timestamp prefix 20261014_181143 must be discovered!
    assert len(artifacts) == 4
    types = {a.artifact_type for a in artifacts}
    assert types == {"config", "database", "files", "private_files"}


def test_missing_expected_public_files_fails():
    """If bench stdout reports public files but the file does not exist on disk, backup must fail."""
    cfg = ServerConfig(
        site_name="riya-associate.com",
        bench_path="/home/frappe/frappe-bench",
    )
    mock_ssh = MagicMock()
    mock_sftp = MagicMock()
    mock_ssh.open_sftp.return_value = mock_sftp

    def make_attr(filename, size):
        a = MagicMock()
        a.filename = filename
        a.st_size = size
        a.st_mtime = 1700000000
        return a

    # Server listdir is missing 20261014_181143-riya-associate_com-files.tar
    mock_sftp.listdir_attr.return_value = [
        make_attr("20261014_181143-riya-associate_com-site_config_backup.json", 518),
        make_attr("20261014_181143-riya-associate_com-database.sql.gz", 34063064),
        make_attr("20261014_181143-riya-associate_com-private-files.tar", 575248793),
    ]

    stdout = """Config   : ./sites/riya-associate.com/private/backups/20261014_181143-riya-associate_com-site_config_backup.json
Database : ./sites/riya-associate.com/private/backups/20261014_181143-riya-associate_com-database.sql.gz
Public   : ./sites/riya-associate.com/private/backups/20261014_181143-riya-associate_com-files.tar
Private  : ./sites/riya-associate.com/private/backups/20261014_181143-riya-associate_com-private-files.tar
"""
    mock_ssh.execute_command.return_value = (0, stdout, "")

    svc = FrappeBackupService(mock_ssh, cfg)
    success, msg, artifacts = svc.run_backup()

    assert success is False
    assert "Expected backup artifact(s) reported by bench were missing" in msg
    assert "files.tar" in msg


def test_missing_expected_private_files_fails():
    """If bench stdout reports private files but the file does not exist on disk, backup must fail."""
    cfg = ServerConfig(
        site_name="riya-associate.com",
        bench_path="/home/frappe/frappe-bench",
    )
    mock_ssh = MagicMock()
    mock_sftp = MagicMock()
    mock_ssh.open_sftp.return_value = mock_sftp

    def make_attr(filename, size):
        a = MagicMock()
        a.filename = filename
        a.st_size = size
        a.st_mtime = 1700000000
        return a

    mock_sftp.listdir_attr.return_value = [
        make_attr("20261014_181143-riya-associate_com-site_config_backup.json", 518),
        make_attr("20261014_181143-riya-associate_com-database.sql.gz", 34063064),
        make_attr("20261014_181143-riya-associate_com-files.tar", 1932735283),
    ]

    stdout = """Config   : ./sites/riya-associate.com/private/backups/20261014_181143-riya-associate_com-site_config_backup.json
Database : ./sites/riya-associate.com/private/backups/20261014_181143-riya-associate_com-database.sql.gz
Public   : ./sites/riya-associate.com/private/backups/20261014_181143-riya-associate_com-files.tar
Private  : ./sites/riya-associate.com/private/backups/20261014_181143-riya-associate_com-private-files.tar
"""
    mock_ssh.execute_command.return_value = (0, stdout, "")

    svc = FrappeBackupService(mock_ssh, cfg)
    success, msg, artifacts = svc.run_backup()

    assert success is False
    assert "Expected backup artifact(s) reported by bench were missing" in msg
    assert "private-files.tar" in msg


def test_ansi_colored_stdout_parsing():
    """Ensure ANSI colored terminal codes in bench output do not prevent discovery."""
    cfg = ServerConfig(
        site_name="testsite.local",
        bench_path="/home/frappe/frappe-bench",
    )
    mock_ssh = MagicMock()
    mock_sftp = MagicMock()
    mock_ssh.open_sftp.return_value = mock_sftp

    def make_attr(filename, size):
        a = MagicMock()
        a.filename = filename
        a.st_size = size
        a.st_mtime = 1700000000
        return a

    mock_sftp.listdir_attr.return_value = [
        make_attr("20261014_120000-testsite_local-site_config_backup.json", 500),
        make_attr("20261014_120000-testsite_local-database.sql.gz", 20000000),
        make_attr("20261014_120000-testsite_local-files.tar", 50000000),
    ]

    stdout = """\x1b[32mConfig\x1b[0m   : ./sites/testsite.local/private/backups/20261014_120000-testsite_local-site_config_backup.json
\x1b[32mDatabase\x1b[0m : ./sites/testsite.local/private/backups/20261014_120000-testsite_local-database.sql.gz
\x1b[32mPublic\x1b[0m   : ./sites/testsite.local/private/backups/20261014_120000-testsite_local-files.tar
"""
    mock_ssh.execute_command.return_value = (0, stdout, "")

    svc = FrappeBackupService(mock_ssh, cfg)
    success, msg, artifacts = svc.run_backup()

    assert success is True
    assert len(artifacts) == 3
