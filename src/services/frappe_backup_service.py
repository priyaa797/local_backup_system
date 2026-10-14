"""Frappe / ERPNext remote backup command execution and artifact discovery.

Safely invokes 'bench --site <site> backup --with-files' inside the configured
bench directory, streams output, and reliably identifies generated artifacts.
"""

from __future__ import annotations

import os
from pathlib import PurePosixPath
import re
from typing import Callable, List, Optional, Tuple

import paramiko

from src.models.backup_artifact import BackupArtifact
from src.models.server_config import ServerConfig
from src.services.ssh_service import SSHService
from src.utils.logger import get_logger

logger = get_logger()

# Safe site name pattern: alphanumeric, dots, dashes, underscores
SAFE_SITE_NAME_RE = re.compile(r"^[a-zA-Z0-9.\-_]+$")


class FrappeBackupService:
    """Manages executing bench backup and discovering generated database and file archives."""

    def __init__(self, ssh_service: SSHService, config: ServerConfig) -> None:
        self.ssh = ssh_service
        self.config = config

    def validate_site_name(self) -> bool:
        """Ensure site name contains no dangerous shell injection characters."""
        site = self.config.site_name.strip()
        return bool(SAFE_SITE_NAME_RE.match(site))

    def run_backup(
        self,
        log_cb: Optional[Callable[[str], None]] = None,
        timeout_seconds: int = 600,
    ) -> Tuple[bool, str, List[BackupArtifact]]:
        """Execute remote backup command and discover generated artifacts.

        Returns (success, message, list_of_artifacts).
        """
        if not self.validate_site_name():
            return False, f"Invalid site name '{self.config.site_name}'.", []

        bench_dir = self.config.bench_path.rstrip("/")
        site_name = self.config.site_name.strip()

        # Command construction
        # Check if virtualenv bench or system bench is used
        # We invoke: bench --site <site> backup --with-files
        cmd = f"bench --site {site_name} backup --with-files"

        if log_cb:
            log_cb(f"[{site_name}] Running: {cmd} in {bench_dir}")

        try:
            exit_code, stdout, stderr = self.ssh.execute_command(
                command=cmd,
                cwd=bench_dir,
                stdout_cb=log_cb,
                stderr_cb=log_cb,
                timeout=timeout_seconds,
            )
        except Exception as exc:
            msg = f"SSH execution error during bench backup on {self.config.host}: {exc}"
            logger.error(msg)
            return False, msg, []

        if exit_code != 0:
            err_details = stderr.strip() or stdout.strip() or f"Exit code {exit_code}"
            msg = f"Bench backup command failed with exit code {exit_code}: {err_details}"
            logger.error(msg)
            return False, msg, []

        if log_cb:
            log_cb(f"[{site_name}] Remote backup command finished successfully. Discovering backup files...")

        # Discover artifacts from stdout and backup directory inspection
        artifacts = self.discover_artifacts(stdout, bench_dir, site_name, log_cb)

        # Validate that database backup exists
        db_artifact = next((a for a in artifacts if a.artifact_type == "database"), None)
        if not db_artifact:
            msg = f"No database backup file was identified for site '{site_name}'. Backup cannot be completed."
            logger.error(msg)
            return False, msg, []

        if log_cb:
            log_cb(f"[{site_name}] Discovered {len(artifacts)} backup artifact(s) totaling {sum(a.size_bytes for a in artifacts)} bytes.")

        return True, "Remote backup generated successfully.", artifacts

    def discover_artifacts(
        self,
        stdout: str,
        bench_dir: str,
        site_name: str,
        log_cb: Optional[Callable[[str], None]] = None,
    ) -> List[BackupArtifact]:
        """Identify backup artifacts from bench output and remote backups directory."""
        artifacts: List[BackupArtifact] = []
        found_filenames: set[str] = set()

        # Remote backup directory in Frappe
        remote_backups_dir = f"{bench_dir}/sites/{site_name}/private/backups"

        # 1. Parse standard Frappe output patterns:
        # e.g.:
        # Database : ./sites/site1.local/private/backups/20261014_114000-site1_local-database.sql.gz
        # Public Files : ./sites/site1.local/private/backups/20261014_114000-site1_local-files.tar
        # Private Files : ./sites/site1.local/private/backups/20261014_114000-site1_local-private-files.tar
        lines = stdout.splitlines()
        for line in lines:
            line_stripped = line.strip()
            # Match Database / Files / Config paths
            match = re.search(r"(?:Database|Public Files|Private Files|Backup Config)\s*:\s*(\S+)", line_stripped, re.IGNORECASE)
            if match:
                raw_path = match.group(1).strip()
                # Clean up relative prefix ./ if present
                clean_path = raw_path.lstrip("./")
                if not clean_path.startswith("/"):
                    clean_path = f"{bench_dir}/{clean_path}"

                fname = PurePosixPath(clean_path).name
                found_filenames.add(fname)

        # 2. Inspect the remote directory using SFTP to get exact file sizes and discover any companion files (e.g. site_config)
        try:
            sftp = self.ssh.open_sftp()
            try:
                file_attrs = sftp.listdir_attr(remote_backups_dir)
                # Sort newest first by mtime
                file_attrs.sort(key=lambda a: a.st_mtime or 0, reverse=True)

                if found_filenames:
                    # Filter matching discovered filenames
                    for attr in file_attrs:
                        if attr.filename in found_filenames:
                            full_remote = f"{remote_backups_dir}/{attr.filename}"
                            art = self._classify_artifact(attr.filename, full_remote, attr.st_size or 0)
                            artifacts.append(art)
                else:
                    # Fallback if bench stdout didn't print standard paths (e.g. customized bench output)
                    # Pick the latest database file and files from the last 15 minutes
                    logger.info("Parsing stdout yielded no paths; inspecting %s directly", remote_backups_dir)
                    if file_attrs:
                        # Find the newest database file
                        latest_db_attr = next(
                            (a for a in file_attrs if "database" in a.filename and (a.filename.endswith(".sql.gz") or a.filename.endswith(".sql"))),
                            None,
                        )
                        if latest_db_attr:
                            # Extract timestamp prefix (e.g. 20261014_114000)
                            prefix_match = re.match(r"^(\d{8}_\d{6})", latest_db_attr.filename)
                            prefix = prefix_match.group(1) if prefix_match else ""

                            for attr in file_attrs:
                                if prefix and attr.filename.startswith(prefix):
                                    full_remote = f"{remote_backups_dir}/{attr.filename}"
                                    art = self._classify_artifact(attr.filename, full_remote, attr.st_size or 0)
                                    artifacts.append(art)
                                elif not prefix and (attr.st_mtime or 0) >= (latest_db_attr.st_mtime or 0) - 60:
                                    # Within 1 minute of DB creation
                                    full_remote = f"{remote_backups_dir}/{attr.filename}"
                                    art = self._classify_artifact(attr.filename, full_remote, attr.st_size or 0)
                                    artifacts.append(art)

            finally:
                sftp.close()
        except Exception as exc:
            logger.warning("Error inspecting remote backup directory %s via SFTP: %s", remote_backups_dir, exc)

        return artifacts

    @staticmethod
    def _classify_artifact(filename: str, remote_path: str, size: int) -> BackupArtifact:
        """Determine artifact type from filename."""
        fn_lower = filename.lower()
        if "database" in fn_lower:
            art_type = "database"
        elif "private-files" in fn_lower or "private_files" in fn_lower:
            art_type = "private_files"
        elif "files" in fn_lower:
            art_type = "files"
        elif "site_config" in fn_lower or fn_lower.endswith(".json"):
            art_type = "config"
        else:
            art_type = "other"

        return BackupArtifact(
            filename=filename,
            artifact_type=art_type,
            remote_path=remote_path,
            size_bytes=size,
            status="pending",
        )
