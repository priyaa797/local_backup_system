"""Integrity verification and backup manifest generation.

Computes SHA-256 hashes of downloaded artifacts, compares against remote checksums,
and writes an immutable backup_manifest.json in the destination folder.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import List, Optional

from src.models.backup_artifact import BackupArtifact
from src.services.ssh_service import SSHService
from src.utils.logger import get_logger

logger = get_logger()


class VerificationService:
    """Verifies file integrity and produces structured backup manifests."""

    def __init__(self, ssh_service: Optional[SSHService] = None) -> None:
        self.ssh = ssh_service

    @staticmethod
    def calculate_local_sha256(file_path: Path, chunk_size: int = 1048576) -> str:
        """Compute SHA-256 hash of a local file."""
        hasher = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(chunk_size):
                hasher.update(chunk)
        return hasher.hexdigest()

    def get_remote_sha256(self, remote_path: str) -> Optional[str]:
        """Fetch remote SHA-256 hash via ssh command 'sha256sum <path>'."""
        if self.ssh is None:
            return None
        try:
            cmd = f"sha256sum '{remote_path}'"
            exit_code, stdout, stderr = self.ssh.execute_command(cmd, timeout=300)
            if exit_code == 0 and stdout:
                parts = stdout.strip().split()
                if parts:
                    return parts[0].strip()
        except Exception as exc:
            logger.debug("Remote sha256sum not available or failed for %s: %s", remote_path, exc)
        return None

    def verify_artifact(self, artifact: BackupArtifact) -> bool:
        """Perform SHA-256 and size verification on a downloaded artifact."""
        if not artifact.local_path or not os.path.exists(artifact.local_path):
            artifact.verified = False
            artifact.error_message = "Local file not found for verification."
            return False

        local_path = Path(artifact.local_path)
        actual_size = local_path.stat().st_size

        if artifact.size_bytes > 0 and actual_size != artifact.size_bytes:
            artifact.verified = False
            artifact.error_message = (
                f"Size mismatch: recorded {artifact.size_bytes} vs actual {actual_size}"
            )
            return False

        # Calculate local SHA-256
        local_hash = self.calculate_local_sha256(local_path)
        artifact.sha256_hash = local_hash

        # Try to compare with remote hash if SSH is active
        if self.ssh is not None:
            remote_hash = self.get_remote_sha256(artifact.remote_path)
            artifact.remote_sha256 = remote_hash
            if remote_hash and remote_hash.lower() != local_hash.lower():
                artifact.verified = False
                artifact.error_message = (
                    f"SHA-256 hash mismatch! Local: {local_hash} vs Remote: {remote_hash}"
                )
                logger.error(artifact.error_message)
                return False

        artifact.verified = True
        artifact.status = "verified"
        logger.info("Artifact verified: %s (SHA256: %s)", artifact.filename, local_hash[:12])
        return True

    @staticmethod
    def write_manifest(
        site_folder: Path,
        job_id: str,
        site_name: str,
        server_host: str,
        artifacts: List[BackupArtifact],
    ) -> Path:
        """Write backup_manifest.json with all artifacts and integrity metadata."""
        manifest_path = site_folder / "backup_manifest.json"
        has_database = any(a.artifact_type == "database" and a.verified for a in artifacts)
        has_files = any(a.artifact_type == "files" and a.verified for a in artifacts)
        has_private_files = any(a.artifact_type == "private_files" and a.verified for a in artifacts)
        has_config = any(a.artifact_type == "config" and a.verified for a in artifacts)
        is_complete_with_files = has_database and has_files and has_private_files and has_config

        if is_complete_with_files:
            backup_type = "full"
        elif has_database and not (has_files or has_private_files):
            backup_type = "database_only"
        else:
            backup_type = "custom"

        manifest_data = {
            "version": "1.0",
            "job_id": job_id,
            "site_name": site_name,
            "server_host": server_host,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "total_artifacts": len(artifacts),
            "total_bytes": sum(a.size_bytes for a in artifacts),
            "all_verified": all(a.verified for a in artifacts) and len(artifacts) > 0,
            "has_database": has_database,
            "has_files": has_files,
            "has_private_files": has_private_files,
            "has_config": has_config,
            "is_complete_with_files": is_complete_with_files,
            "backup_type": backup_type,
            "artifacts": [
                {
                    "filename": a.filename,
                    "artifact_type": a.artifact_type,
                    "size_bytes": a.size_bytes,
                    "sha256": a.sha256_hash,
                    "verified": a.verified,
                }
                for a in artifacts
            ],
        }

        manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
        logger.info("Written backup manifest to %s", manifest_path)
        return manifest_path

    @staticmethod
    def read_manifest(site_folder: Path) -> Optional[dict]:
        """Read and parse backup_manifest.json from site directory."""
        manifest_path = site_folder / "backup_manifest.json"
        if not manifest_path.exists():
            return None
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Could not read manifest at %s: %s", manifest_path, exc)
            return None
