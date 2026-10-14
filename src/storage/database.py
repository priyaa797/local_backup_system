"""SQLite persistent database storage for servers, jobs, artifacts, and settings.

Stores only non-sensitive configuration, historical execution records, and state.
Never stores plain-text passwords or secret keys.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Generator, List, Optional
import uuid

from src.models.backup_artifact import BackupArtifact
from src.models.backup_job import BackupJob, BackupStage, JobStatus, SiteJobRecord
from src.models.server_config import ServerConfig
from src.storage.app_paths import get_db_path
from src.utils.logger import get_logger

logger = get_logger()


class Database:
    """SQLite Database manager with thread-safe connections and migrations."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or get_db_path()
        self.init_db()

    @contextlib.contextmanager
    def get_connection(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        """Create tables if they do not exist and run migrations."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Server and Site Configurations
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS servers (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL DEFAULT 22,
                    username TEXT NOT NULL,
                    auth_type TEXT NOT NULL DEFAULT 'password',
                    credential_id TEXT NOT NULL,
                    key_path TEXT,
                    site_name TEXT NOT NULL,
                    bench_path TEXT NOT NULL DEFAULT '/home/frappe/frappe-bench',
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            # Backup Jobs
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    destination_path TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    total_sites INTEGER NOT NULL DEFAULT 0,
                    success_sites INTEGER NOT NULL DEFAULT 0,
                    failed_sites INTEGER NOT NULL DEFAULT 0,
                    total_bytes INTEGER NOT NULL DEFAULT 0,
                    total_files INTEGER NOT NULL DEFAULT 0,
                    error_summary TEXT
                )
                """
            )

            # Job Site Records
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS job_sites (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL,
                    server_id TEXT NOT NULL,
                    host TEXT NOT NULL,
                    site_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    backup_stage TEXT NOT NULL,
                    download_dir TEXT,
                    started_at TEXT,
                    completed_at TEXT,
                    error_message TEXT,
                    diagnostics TEXT,
                    suggested_action TEXT,
                    FOREIGN KEY (job_id) REFERENCES jobs (id) ON DELETE CASCADE
                )
                """
            )

            # Backup Artifacts
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS job_artifacts (
                    id TEXT PRIMARY KEY,
                    site_record_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    remote_path TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL DEFAULT 0,
                    sha256_hash TEXT,
                    verified INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    error_message TEXT,
                    FOREIGN KEY (site_record_id) REFERENCES job_sites (id) ON DELETE CASCADE
                )
                """
            )

            # Non-sensitive Application Settings
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )

            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_started_at ON jobs (started_at DESC)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_job_sites_job_id ON job_sites (job_id)"
            )

        self.recover_interrupted_jobs()

    def recover_interrupted_jobs(self) -> None:
        """Mark any jobs and site records left in 'running' or 'pending' state as 'interrupted'."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE jobs
                SET status = ?, error_summary = ?
                WHERE status IN (?, ?)
                """,
                (
                    JobStatus.INTERRUPTED,
                    "Application terminated unexpectedly while backup was active.",
                    JobStatus.RUNNING,
                    JobStatus.PENDING,
                ),
            )
            jobs_recovered = cursor.rowcount

            cursor.execute(
                """
                UPDATE job_sites
                SET status = ?, backup_stage = ?, error_message = ?
                WHERE status IN (?, ?)
                """,
                (
                    JobStatus.INTERRUPTED,
                    BackupStage.FAILED,
                    "Application terminated unexpectedly while site backup was active.",
                    JobStatus.RUNNING,
                    JobStatus.PENDING,
                ),
            )
            sites_recovered = cursor.rowcount

            if jobs_recovered > 0 or sites_recovered > 0:
                logger.warning(
                    "Recovered %d interrupted job(s) and %d site record(s) from previous abnormal termination.",
                    jobs_recovered,
                    sites_recovered,
                )

    # --- Server Config CRUD ---

    def save_server(self, server: ServerConfig) -> None:
        """Insert or update a server configuration."""
        server.updated_at = datetime.now(timezone.utc).isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO servers (
                    id, name, host, port, username, auth_type, credential_id,
                    key_path, site_name, bench_path, enabled, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    host = excluded.host,
                    port = excluded.port,
                    username = excluded.username,
                    auth_type = excluded.auth_type,
                    credential_id = excluded.credential_id,
                    key_path = excluded.key_path,
                    site_name = excluded.site_name,
                    bench_path = excluded.bench_path,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    server.id,
                    server.name,
                    server.host,
                    server.port,
                    server.username,
                    server.auth_type,
                    server.credential_id,
                    server.key_path,
                    server.site_name,
                    server.bench_path,
                    1 if server.enabled else 0,
                    server.created_at,
                    server.updated_at,
                ),
            )

    def get_servers(self, enabled_only: bool = False) -> List[ServerConfig]:
        """Fetch all configured servers."""
        query = "SELECT * FROM servers"
        params: tuple[Any, ...] = ()
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY host, site_name"

        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [
                ServerConfig(
                    id=row["id"],
                    name=row["name"],
                    host=row["host"],
                    port=row["port"],
                    username=row["username"],
                    auth_type=row["auth_type"],
                    credential_id=row["credential_id"],
                    key_path=row["key_path"],
                    site_name=row["site_name"],
                    bench_path=row["bench_path"],
                    enabled=bool(row["enabled"]),
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                )
                for row in rows
            ]

    def get_server_by_id(self, server_id: str) -> Optional[ServerConfig]:
        """Fetch single server by ID."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM servers WHERE id = ?", (server_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return ServerConfig(
                id=row["id"],
                name=row["name"],
                host=row["host"],
                port=row["port"],
                username=row["username"],
                auth_type=row["auth_type"],
                credential_id=row["credential_id"],
                key_path=row["key_path"],
                site_name=row["site_name"],
                bench_path=row["bench_path"],
                enabled=bool(row["enabled"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )

    def delete_server(self, server_id: str) -> bool:
        """Delete a server configuration."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM servers WHERE id = ?", (server_id,))
            return cursor.rowcount > 0

    # --- Backup Job CRUD ---

    def save_job(self, job: BackupJob) -> None:
        """Save or update entire BackupJob including sites and artifacts."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO jobs (
                    id, status, destination_path, started_at, completed_at,
                    total_sites, success_sites, failed_sites, total_bytes,
                    total_files, error_summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    destination_path = excluded.destination_path,
                    completed_at = excluded.completed_at,
                    total_sites = excluded.total_sites,
                    success_sites = excluded.success_sites,
                    failed_sites = excluded.failed_sites,
                    total_bytes = excluded.total_bytes,
                    total_files = excluded.total_files,
                    error_summary = excluded.error_summary
                """,
                (
                    job.id,
                    job.status,
                    job.destination_path,
                    job.started_at,
                    job.completed_at,
                    job.total_sites,
                    job.success_sites,
                    job.failed_sites,
                    job.total_bytes,
                    job.total_files,
                    job.error_summary,
                ),
            )

            for site in job.site_records:
                cursor.execute(
                    """
                    INSERT INTO job_sites (
                        id, job_id, server_id, host, site_name, status,
                        backup_stage, download_dir, started_at, completed_at,
                        error_message, diagnostics, suggested_action
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        status = excluded.status,
                        backup_stage = excluded.backup_stage,
                        download_dir = excluded.download_dir,
                        completed_at = excluded.completed_at,
                        error_message = excluded.error_message,
                        diagnostics = excluded.diagnostics,
                        suggested_action = excluded.suggested_action
                    """,
                    (
                        site.id,
                        job.id,
                        site.server_id,
                        site.host,
                        site.site_name,
                        site.status,
                        site.backup_stage,
                        site.download_dir,
                        site.started_at,
                        site.completed_at,
                        site.error_message,
                        site.diagnostics,
                        site.suggested_action,
                    ),
                )

                for art in site.artifacts:
                    art_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{site.id}_{art.filename}"))
                    cursor.execute(
                        """
                        INSERT INTO job_artifacts (
                            id, site_record_id, filename, artifact_type,
                            remote_path, local_path, size_bytes, sha256_hash,
                            verified, status, error_message
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(id) DO UPDATE SET
                            local_path = excluded.local_path,
                            size_bytes = excluded.size_bytes,
                            sha256_hash = excluded.sha256_hash,
                            verified = excluded.verified,
                            status = excluded.status,
                            error_message = excluded.error_message
                        """,
                        (
                            art_id,
                            site.id,
                            art.filename,
                            art.artifact_type,
                            art.remote_path,
                            art.local_path,
                            art.size_bytes,
                            art.sha256_hash,
                            1 if art.verified else 0,
                            art.status,
                            art.error_message,
                        ),
                    )

    def get_job_by_id(self, job_id: str) -> Optional[BackupJob]:
        """Fetch complete BackupJob with site records and artifacts."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
            job_row = cursor.fetchone()
            if not job_row:
                return None

            job = BackupJob(
                id=job_row["id"],
                status=job_row["status"],
                destination_path=job_row["destination_path"],
                started_at=job_row["started_at"],
                completed_at=job_row["completed_at"],
                total_sites=job_row["total_sites"],
                success_sites=job_row["success_sites"],
                failed_sites=job_row["failed_sites"],
                total_bytes=job_row["total_bytes"],
                total_files=job_row["total_files"],
                error_summary=job_row["error_summary"],
            )

            cursor.execute("SELECT * FROM job_sites WHERE job_id = ?", (job_id,))
            site_rows = cursor.fetchall()
            for s_row in site_rows:
                site = SiteJobRecord(
                    id=s_row["id"],
                    job_id=s_row["job_id"],
                    server_id=s_row["server_id"],
                    host=s_row["host"],
                    site_name=s_row["site_name"],
                    status=s_row["status"],
                    backup_stage=s_row["backup_stage"],
                    download_dir=s_row["download_dir"],
                    started_at=s_row["started_at"],
                    completed_at=s_row["completed_at"],
                    error_message=s_row["error_message"],
                    diagnostics=s_row["diagnostics"],
                    suggested_action=s_row["suggested_action"],
                )

                cursor.execute(
                    "SELECT * FROM job_artifacts WHERE site_record_id = ?",
                    (site.id,),
                )
                art_rows = cursor.fetchall()
                for a_row in art_rows:
                    site.artifacts.append(
                        BackupArtifact(
                            filename=a_row["filename"],
                            artifact_type=a_row["artifact_type"],
                            remote_path=a_row["remote_path"],
                            local_path=a_row["local_path"],
                            size_bytes=a_row["size_bytes"],
                            sha256_hash=a_row["sha256_hash"],
                            verified=bool(a_row["verified"]),
                            status=a_row["status"],
                            error_message=a_row["error_message"],
                        )
                    )
                job.site_records.append(site)

            return job

    def get_recent_jobs(self, limit: int = 20) -> List[BackupJob]:
        """Fetch recent backup jobs."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id FROM jobs ORDER BY started_at DESC LIMIT ?", (limit,)
            )
            rows = cursor.fetchall()

        jobs = []
        for row in rows:
            job = self.get_job_by_id(row["id"])
            if job:
                jobs.append(job)
        return jobs

    def get_last_job(self) -> Optional[BackupJob]:
        """Fetch the most recent backup execution."""
        recent = self.get_recent_jobs(limit=1)
        return recent[0] if recent else None

    def get_last_successful_job(self) -> Optional[BackupJob]:
        """Fetch the most recent successful backup job."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id FROM jobs
                WHERE status IN (?, ?)
                ORDER BY started_at DESC LIMIT 1
                """,
                (JobStatus.SUCCESS, JobStatus.PARTIAL_SUCCESS),
            )
            row = cursor.fetchone()
            if not row:
                return None
            return self.get_job_by_id(row["id"])

    # --- Settings Store ---

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Retrieve setting value."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            if row:
                return row["value"]
            return default

    def set_setting(self, key: str, value: str) -> None:
        """Store setting value."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, str(value)),
            )


# Global singleton instance
_db_instance: Optional[Database] = None


def get_db() -> Database:
    global _db_instance
    if _db_instance is None:
        _db_instance = Database()
    return _db_instance
