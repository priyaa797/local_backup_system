"""Data model for individual backup artifacts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


@dataclass
class BackupArtifact:
    """Represents a single downloaded backup file (database, public files, private files)."""

    filename: str
    artifact_type: str  # 'database', 'files', 'private_files', 'config', 'other'
    remote_path: str
    local_path: str = ""
    size_bytes: int = 0
    sha256_hash: Optional[str] = None
    remote_sha256: Optional[str] = None
    verified: bool = False
    status: str = "pending"  # 'pending', 'downloading', 'verified', 'failed'
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> BackupArtifact:
        return cls(**data)
