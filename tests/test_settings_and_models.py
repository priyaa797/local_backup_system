"""Tests for models, validation, database operations, and crash recovery."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import uuid

import pytest

from src.models.backup_artifact import BackupArtifact
from src.models.backup_job import BackupJob, JobStatus, SiteJobRecord
from src.models.server_config import ServerConfig
from src.storage.database import Database


@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    db = Database(db_path=db_path)
    yield db
    if db_path.exists():
        db_path.unlink()


def test_server_config_validation():
    # Valid config
    cfg = ServerConfig(
        host="192.168.1.100",
        port=22,
        username="frappe",
        site_name="riya-associate.com",
        bench_path="/home/frappe/frappe-bench",
        auth_type="password",
    )
    assert cfg.validate() == []

    # Missing host
    cfg.host = ""
    assert "Host / Server IP is required." in cfg.validate()

    # Invalid port
    cfg.host = "192.168.1.100"
    cfg.port = 99999
    assert "Port must be between 1 and 65535." in cfg.validate()

    # Shell injection characters in site name
    cfg.port = 22
    cfg.site_name = "site1.local; rm -rf /"
    assert any("invalid shell characters" in err for err in cfg.validate())

    # Key auth without key path
    cfg.site_name = "site1.local"
    cfg.auth_type = "key"
    cfg.key_path = None
    assert any("Private key path is required" in err for err in cfg.validate())


def test_server_config_to_dict_never_contains_password():
    cfg = ServerConfig(
        host="erp.test.com",
        username="frappe",
        site_name="site1.local",
    )
    d = cfg.to_dict()
    assert "password" not in d
    assert "secret" not in d
    assert d["host"] == "erp.test.com"
    assert "credential_id" in d


def test_backup_job_summary_computation():
    job = BackupJob()
    assert job.status == JobStatus.PENDING

    # Add 2 sites: 1 success, 1 failed
    s1 = SiteJobRecord(site_name="site1.local", status=JobStatus.SUCCESS)
    s1.artifacts.append(
        BackupArtifact(filename="db1.sql.gz", artifact_type="database", remote_path="/tmp", size_bytes=1000, verified=True)
    )

    s2 = SiteJobRecord(site_name="site2.local", status=JobStatus.FAILED)

    job.site_records = [s1, s2]
    job.compute_summary()

    assert job.total_sites == 2
    assert job.success_sites == 1
    assert job.failed_sites == 1
    assert job.total_bytes == 1000
    assert job.status == JobStatus.PARTIAL_SUCCESS

    # If s2 also succeeds
    s2.status = JobStatus.SUCCESS
    job.compute_summary()
    assert job.status == JobStatus.SUCCESS


def test_database_server_crud(temp_db: Database):
    cfg = ServerConfig(
        host="10.0.0.5",
        username="erpnext",
        site_name="demo.erpnext.com",
        bench_path="/opt/bench",
    )
    temp_db.save_server(cfg)

    servers = temp_db.get_servers()
    assert len(servers) == 1
    loaded = servers[0]
    assert loaded.host == "10.0.0.5"
    assert loaded.site_name == "demo.erpnext.com"

    # Update server
    loaded.port = 2222
    temp_db.save_server(loaded)

    updated = temp_db.get_server_by_id(loaded.id)
    assert updated is not None
    assert updated.port == 2222

    # Delete
    deleted = temp_db.delete_server(loaded.id)
    assert deleted is True
    assert len(temp_db.get_servers()) == 0


def test_database_crash_recovery(temp_db: Database):
    # Simulate a job left in running state when app crashed
    job = BackupJob(
        destination_path="/backups",
        status=JobStatus.RUNNING,
    )
    temp_db.save_job(job)

    # Re-instantiate database or call recover_interrupted_jobs
    temp_db.recover_interrupted_jobs()

    recovered = temp_db.get_job_by_id(job.id)
    assert recovered is not None
    assert recovered.status == JobStatus.INTERRUPTED
    assert "abnormal" in recovered.error_summary or "unexpectedly" in recovered.error_summary
