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

        # Check if any expected artifact was missing
        missing = [a for a in artifacts if a.status == "failed"]
        if missing:
            missing_names = ", ".join(a.filename for a in missing)
            msg = f"Expected backup artifact(s) reported by bench were missing from remote server: {missing_names}"
            logger.error(msg)
            return False, msg, artifacts

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
        stdout_filenames: set[str] = set()
        stdout_label_map: dict[str, str] = {}
        stdout_path_map: dict[str, str] = {}

        # Remote backup directory in Frappe
        remote_backups_dir = f"{bench_dir}/sites/{site_name}/private/backups"

        # 1. Parse Frappe backup command output
        # Strip ANSI escape codes if present
        clean_stdout = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", stdout or "")

        # Regex to match label : path lines from various Frappe versions:
        # Config   : ./sites/site1.local/private/backups/...
        # Database : ./sites/site1.local/private/backups/...
        # Public   : ./sites/site1.local/private/backups/...
        # Private  : ./sites/site1.local/private/backups/...
        # Backup Config : ...
        # Site Config   : ...
        # Public Files  : ...
        # Private Files : ...
        # Files         : ...
        label_re = re.compile(
            r"^\s*(?:\*\*\*\s*)?(Database|DB|Public(?:\s+Files)?|Private(?:\s+Files)?|Files|(?:Site\s+|Backup\s+)?Config(?:\s+Backup)?)\s*:\s*(\S+)",
            re.IGNORECASE,
        )
        path_re = re.compile(
            r"(?:^|\s)(?:(?:\.?/)?(?:sites/[^/\s]+/)?private/backups/(\S+))",
            re.IGNORECASE,
        )

        for line in clean_stdout.splitlines():
            line_str = line.strip()
            if not line_str:
                continue

            match = label_re.search(line_str)
            if match:
                label = match.group(1).strip()
                raw_path = match.group(2).strip()
                fname = PurePosixPath(raw_path.strip().rstrip("/")).name
                clean_path = self._normalize_remote_path(raw_path, bench_dir, site_name, fname)

                stdout_filenames.add(fname)
                stdout_label_map[fname] = label
                stdout_path_map[fname] = clean_path
            else:
                pmatch = path_re.search(line_str)
                if pmatch:
                    raw_candidate = pmatch.group(1).strip()
                    fname = PurePosixPath(raw_candidate).name
                    if fname.endswith((".sql.gz", ".sql", ".tar", ".tgz", ".tar.gz", ".json")):
                        stdout_filenames.add(fname)
                        if fname not in stdout_path_map:
                            stdout_path_map[fname] = self._normalize_remote_path(
                                raw_candidate, bench_dir, site_name, fname
                            )

        # Extract backup run timestamp prefix (e.g. 20261014_181143)
        run_timestamp_prefix: Optional[str] = None
        for fn in stdout_filenames:
            m = re.match(r"^(\d{8}_\d{6})", fn)
            if m:
                run_timestamp_prefix = m.group(1)
                break

        # 2. Inspect remote backup directory via SFTP
        try:
            sftp = self.ssh.open_sftp()
            try:
                try:
                    sftp_cwd = sftp.normalize(".")
                    logger.info("[%s] SFTP session ready. SFTP working directory: %s", site_name, sftp_cwd)
                    if log_cb:
                        log_cb(f"[{site_name}] SFTP working directory: {sftp_cwd}")
                except Exception:
                    sftp_cwd = "unknown"

                logger.info("[%s] Inspecting remote backups directory: %s", site_name, remote_backups_dir)
                if log_cb:
                    log_cb(f"[{site_name}] Inspecting remote backup directory: {remote_backups_dir}")

                file_attrs = sftp.listdir_attr(remote_backups_dir)
                # Sort newest first by mtime
                file_attrs.sort(key=lambda a: a.st_mtime or 0, reverse=True)
                attr_by_name = {a.filename: a for a in file_attrs}

                # If timestamp prefix was not discovered from stdout, inspect newest database in directory
                latest_db_mtime: Optional[float] = None
                if not run_timestamp_prefix and file_attrs:
                    latest_db_attr = next(
                        (
                            a
                            for a in file_attrs
                            if "database" in a.filename.lower()
                            or a.filename.lower().endswith((".sql.gz", ".sql"))
                        ),
                        None,
                    )
                    if latest_db_attr:
                        pm = re.match(r"^(\d{8}_\d{6})", latest_db_attr.filename)
                        if pm:
                            run_timestamp_prefix = pm.group(1)
                        else:
                            latest_db_mtime = float(latest_db_attr.st_mtime or 0)

                # Determine candidate files belonging to this backup run
                candidate_names: set[str] = set()

                # Add all files matching the run timestamp prefix (e.g. 20261014_181143-*)
                if run_timestamp_prefix:
                    for attr in file_attrs:
                        if attr.filename.startswith(run_timestamp_prefix):
                            candidate_names.add(attr.filename)
                elif latest_db_mtime is not None:
                    for attr in file_attrs:
                        if abs((attr.st_mtime or 0) - latest_db_mtime) <= 120:
                            candidate_names.add(attr.filename)

                # Add all files explicitly reported in bench stdout
                for fname in stdout_filenames:
                    candidate_names.add(fname)

                # Build artifact objects
                for fname in candidate_names:
                    if fname in attr_by_name:
                        attr = attr_by_name[fname]
                        # Prefer the verified canonical path in remote_backups_dir
                        full_remote = f"{remote_backups_dir}/{attr.filename}"
                        art = self._classify_artifact(
                            filename=attr.filename,
                            remote_path=full_remote,
                            size=attr.st_size or 0,
                            label=stdout_label_map.get(attr.filename),
                        )
                    else:
                        full_remote = stdout_path_map.get(
                            fname,
                            self._normalize_remote_path("", bench_dir, site_name, fname),
                        )
                        logger.error(
                            "Expected backup artifact %s reported by bench was not found in %s",
                            fname,
                            remote_backups_dir,
                        )
                        art = self._classify_artifact(
                            filename=fname,
                            remote_path=full_remote,
                            size=0,
                            label=stdout_label_map.get(fname),
                        )
                        art.status = "failed"
                        art.error_message = (
                            f"Remote backup artifact '{fname}' reported by bench was not found on server at '{full_remote}'."
                        )

                    logger.info(
                        "[%s] Discovered artifact: %s (type: %s, size: %d bytes, remote_path: %s)",
                        site_name,
                        art.filename,
                        art.artifact_type,
                        art.size_bytes,
                        art.remote_path,
                    )
                    if log_cb:
                        log_cb(f"[{site_name}] Discovered artifact: {art.filename} ({art.size_bytes} bytes) -> {art.remote_path}")

                    artifacts.append(art)

            finally:
                sftp.close()
        except Exception as exc:
            logger.warning(
                "Error inspecting remote backup directory %s via SFTP: %s",
                remote_backups_dir,
                exc,
            )

        # Sort canonically: config -> database -> files -> private_files -> other
        type_priority = {
            "config": 0,
            "database": 1,
            "files": 2,
            "private_files": 3,
            "other": 4,
        }
        artifacts.sort(key=lambda a: (type_priority.get(a.artifact_type, 99), a.filename))
        return artifacts

    @staticmethod
    def _classify_artifact(
        filename: str,
        remote_path: str,
        size: int,
        label: Optional[str] = None,
    ) -> BackupArtifact:
        """Determine artifact type from bench output label or filename patterns."""
        lbl_lower = (label or "").strip().lower()
        fn_lower = filename.lower()

        if lbl_lower in ("database", "db"):
            art_type = "database"
        elif lbl_lower in ("public", "public files", "files"):
            art_type = "files"
        elif lbl_lower in ("private", "private files"):
            art_type = "private_files"
        elif "config" in lbl_lower:
            art_type = "config"
        elif "database" in fn_lower or fn_lower.endswith((".sql.gz", ".sql", ".sql.bz2", ".sql.xz")):
            art_type = "database"
        elif "private-files" in fn_lower or "private_files" in fn_lower or (
            "private" in fn_lower and ("file" in fn_lower or fn_lower.endswith((".tar", ".tgz", ".tar.gz")))
        ):
            art_type = "private_files"
        elif "files" in fn_lower or "public" in fn_lower or fn_lower.endswith((".tar", ".tgz", ".tar.gz")):
            art_type = "files"
        elif "site_config" in fn_lower or "config" in fn_lower or fn_lower.endswith(".json"):
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

    @staticmethod
    def _normalize_remote_path(
        raw_path: str,
        bench_dir: str,
        site_name: str,
        filename: str,
    ) -> str:
        """Resolve a raw path from bench stdout to an absolute remote path.

        Handles variants:
        - ./site/private/backups/file -> <bench_dir>/sites/site/private/backups/file
        - ./sites/site/private/backups/file -> <bench_dir>/sites/site/private/backups/file
        - /absolute/path/to/sites/site/private/backups/file -> preserved as-is
        - private/backups/file -> <bench_dir>/sites/site/private/backups/file
        - file -> <bench_dir>/sites/site/private/backups/file
        """
        bench_clean = bench_dir.rstrip("/")
        site_clean = site_name.strip()
        default_canonical = f"{bench_clean}/sites/{site_clean}/private/backups/{filename}"

        if not raw_path:
            return default_canonical

        stripped = raw_path.strip()
        while stripped.startswith("./"):
            stripped = stripped[2:]

        if stripped.startswith("/"):
            # Check if it's missing /sites/ e.g. /root/frappe-bench/riya-associate.com/...
            bench_site_prefix = f"{bench_clean}/{site_clean}/"
            if stripped.startswith(bench_site_prefix) and f"{bench_clean}/sites/" not in stripped:
                return f"{bench_clean}/sites/{stripped[len(bench_clean) + 1:]}"
            return stripped

        if stripped.startswith("sites/"):
            return f"{bench_clean}/{stripped}"

        if stripped.startswith(f"{site_clean}/"):
            return f"{bench_clean}/sites/{stripped}"

        if stripped.startswith("private/backups/"):
            return f"{bench_clean}/sites/{site_clean}/{stripped}"

        if stripped == filename:
            return default_canonical

        return f"{bench_clean}/sites/{site_clean}/private/backups/{filename}"
