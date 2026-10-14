"""Data models for Backup Jobs and Site Execution Records."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from src.models.backup_artifact import BackupArtifact


class JobStatus:
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    CANCELLED = "cancelled"


class BackupStage:
    IDLE = "Idle"
    VALIDATING_DESTINATION = "Validating destination"
    CONNECTING = "Connecting to server"
    CREATING_BACKUP = "Creating remote backup"
    DISCOVERING_FILES = "Discovering backup files"
    DOWNLOADING_FILES = "Downloading files"
    VERIFYING_INTEGRITY = "Verifying integrity"
    FINALIZING_BACKUP = "Finalizing backup"
    COMPLETED = "Completed"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


@dataclass
class SiteJobRecord:
    """Execution status and artifact records for a single Frappe site."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    job_id: str = ""
    server_id: str = ""
    host: str = ""
    site_name: str = ""
    status: str = JobStatus.PENDING  # 'pending', 'running', 'success', 'failed', 'cancelled'
    backup_stage: str = BackupStage.IDLE
    download_dir: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: Optional[str] = None
    diagnostics: Optional[str] = None
    suggested_action: Optional[str] = None
    artifacts: List[BackupArtifact] = field(default_factory=list)

    @property
    def total_bytes(self) -> int:
        return sum(a.size_bytes for a in self.artifacts)

    @property
    def total_files(self) -> int:
        return len(self.artifacts)

    @property
    def verified_files_count(self) -> int:
        return sum(1 for a in self.artifacts if a.verified)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["artifacts"] = [a.to_dict() for a in self.artifacts]
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> SiteJobRecord:
        artifacts_raw = data.pop("artifacts", [])
        artifacts = [BackupArtifact.from_dict(a) for a in artifacts_raw]
        return cls(artifacts=artifacts, **data)


@dataclass
class BackupJob:
    """Overall backup execution job orchestrating multiple servers/sites."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = JobStatus.PENDING
    destination_path: str = ""
    started_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    completed_at: Optional[str] = None
    total_sites: int = 0
    success_sites: int = 0
    failed_sites: int = 0
    total_bytes: int = 0
    total_files: int = 0
    error_summary: Optional[str] = None
    site_records: List[SiteJobRecord] = field(default_factory=list)

    def compute_summary(self) -> None:
        """Recalculate site and byte totals from site_records."""
        self.total_sites = len(self.site_records)
        self.success_sites = sum(
            1 for s in self.site_records if s.status == JobStatus.SUCCESS
        )
        self.failed_sites = sum(
            1 for s in self.site_records if s.status == JobStatus.FAILED
        )
        self.total_bytes = sum(s.total_bytes for s in self.site_records)
        self.total_files = sum(s.total_files for s in self.site_records)

        if self.failed_sites == 0 and self.success_sites > 0:
            self.status = JobStatus.SUCCESS
        elif self.success_sites > 0 and self.failed_sites > 0:
            self.status = JobStatus.PARTIAL_SUCCESS
        elif self.success_sites == 0 and self.failed_sites > 0:
            self.status = JobStatus.FAILED

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["site_records"] = [s.to_dict() for s in self.site_records]
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BackupJob:
        sites_raw = data.pop("site_records", [])
        site_records = [SiteJobRecord.from_dict(s) for s in sites_raw]
        return cls(site_records=site_records, **data)
