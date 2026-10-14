"""Orchestrates end-to-end backup jobs across multiple Frappe servers and sites.

Implements the multi-stage pipeline:
1. Validation -> 2. Connection -> 3. Remote Backup -> 4. Artifact Discovery
-> 5. File Download -> 6. Integrity Verification -> 7. Finalization & Reporting.
"""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import time
from typing import Callable, List, Optional

from src.models.backup_artifact import BackupArtifact
from src.models.backup_job import BackupJob, BackupStage, JobStatus, SiteJobRecord
from src.models.server_config import ServerConfig
from src.services.cleanup_service import CleanupService
from src.services.disk_service import DiskService
from src.services.frappe_backup_service import FrappeBackupService
from src.services.ssh_service import SSHService
from src.services.transfer_service import FileTransferService, ProgressCallback
from src.services.verification_service import VerificationService
from src.storage.database import Database, get_db
from src.storage.settings_store import SettingsStore
from src.utils.formatters import sanitize_filename
from src.utils.logger import get_logger

logger = get_logger()


class BackupCoordinator:
    """Manages full execution of a multi-server backup run."""

    def __init__(
        self,
        db: Optional[Database] = None,
        log_callback: Optional[Callable[[str], None]] = None,
        stage_callback: Optional[Callable[[str, str], None]] = None,  # (site_name, stage)
        progress_callback: Optional[ProgressCallback] = None,
        job_progress_callback: Optional[Callable[[int, int, float], None]] = None,  # (completed_sites, total_sites, pct)
    ) -> None:
        self.db = db or get_db()
        self.settings_store = SettingsStore(self.db)
        self.log_cb = log_callback
        self.stage_cb = stage_callback
        self.progress_cb = progress_callback
        self.job_progress_cb = job_progress_callback
        self._cancel_requested = False
        self._active_transfer_service: Optional[FileTransferService] = None

    def cancel(self) -> None:
        """Signal ongoing job to abort safely."""
        self._cancel_requested = True
        if self._active_transfer_service:
            self._active_transfer_service.cancel()

    def log(self, message: str) -> None:
        logger.info(message)
        if self.log_cb:
            self.log_cb(message)

    def set_stage(self, record: SiteJobRecord, stage: str) -> None:
        record.backup_stage = stage
        if self.stage_cb:
            self.stage_cb(record.site_name, stage)

    def run_backup_job(
        self,
        destination_folder: str,
        servers_to_backup: Optional[List[ServerConfig]] = None,
    ) -> BackupJob:
        """Execute complete backup job sequentially across all enabled servers."""
        self._cancel_requested = False
        job = BackupJob(
            destination_path=destination_folder,
            status=JobStatus.RUNNING,
            started_at=datetime.now(timezone.utc).isoformat(),
        )

        # Snapshot servers
        if servers_to_backup is None:
            servers = self.db.get_servers(enabled_only=True)
        else:
            servers = servers_to_backup

        job.total_sites = len(servers)

        # Initialize site records
        for s in servers:
            record = SiteJobRecord(
                job_id=job.id,
                server_id=s.id,
                host=s.host,
                site_name=s.site_name,
                status=JobStatus.PENDING,
            )
            job.site_records.append(record)

        self.db.save_job(job)
        self.log(f"Started Backup Job {job.id} for {len(servers)} site(s).")

        # Step 1: Validate Destination
        self.log(f"Validating backup destination: {destination_folder}")
        accessible, access_msg = DiskService.check_destination_accessibility(destination_folder)
        if not accessible:
            self.log(f"Destination error: {access_msg}")
            job.status = JobStatus.FAILED
            job.error_summary = access_msg
            job.completed_at = datetime.now(timezone.utc).isoformat()
            self.db.save_job(job)
            return job

        free_bytes = DiskService.get_free_space(destination_folder)
        # Warn if less than 1 GB free
        if free_bytes < 1024 * 1024 * 1024:
            self.log(f"Warning: Low disk space on destination: {free_bytes // (1024*1024)} MB available.")

        dest_root = Path(destination_folder).resolve()
        now_dt = datetime.now()
        date_str = now_dt.strftime("%Y-%m-%d")
        date_folder = dest_root / date_str
        date_folder.mkdir(parents=True, exist_ok=True)

        # Process each server/site sequentially
        for idx, (server_config, site_record) in enumerate(zip(servers, job.site_records)):
            if self._cancel_requested:
                site_record.status = JobStatus.CANCELLED
                site_record.backup_stage = BackupStage.CANCELLED
                site_record.error_message = "Backup cancelled by user."
                continue

            self._process_site(
                server_config=server_config,
                record=site_record,
                job_id=job.id,
                date_folder=date_folder,
            )

            # Update job progress
            completed_count = idx + 1
            pct = (completed_count / len(servers)) * 100.0
            if self.job_progress_cb:
                self.job_progress_cb(completed_count, len(servers), pct)

            self.db.save_job(job)

        # Finalize Job
        job.completed_at = datetime.now(timezone.utc).isoformat()
        job.compute_summary()
        if self._cancel_requested:
            job.status = JobStatus.CANCELLED

        self.db.save_job(job)
        self.log(
            f"Backup Job {job.id} finished with status: {job.status.upper()} "
            f"({job.success_sites} succeeded, {job.failed_sites} failed)."
        )

        # Retention Cleanup (if explicitly enabled)
        settings = self.settings_store.get_settings()
        if settings.retention_enabled and job.status in (JobStatus.SUCCESS, JobStatus.PARTIAL_SUCCESS):
            try:
                self.log("Running scheduled retention cleanup...")
                to_delete, bytes_reclaimed = CleanupService.preview_cleanup(
                    backup_root=dest_root,
                    mode=settings.retention_mode,
                    retention_value=settings.retention_days if settings.retention_mode == "days" else settings.retention_max_sets,
                    exclude_paths=[date_folder],  # strictly preserve today's folder
                )
                if to_delete:
                    CleanupService.execute_cleanup(to_delete, dest_root)
            except Exception as exc:
                logger.error("Error executing retention cleanup: %s", exc)

        return job

    def _process_site(
        self,
        server_config: ServerConfig,
        record: SiteJobRecord,
        job_id: str,
        date_folder: Path,
    ) -> None:
        """Execute backup pipeline for a single site."""
        site_name = server_config.site_name
        record.started_at = datetime.now(timezone.utc).isoformat()
        record.status = JobStatus.RUNNING
        self.log(f"--- Processing site: {site_name} on {server_config.host} ---")

        # Step 2: Connect
        self.set_stage(record, BackupStage.CONNECTING)
        ssh = SSHService(server_config, auto_accept_new_host_keys=True)
        connected, msg = ssh.connect(timeout=20)
        if not connected:
            self._fail_site(
                record,
                stage=BackupStage.CONNECTING,
                error_msg=f"Could not connect to {server_config.host}: {msg}",
                action="Verify host IP, SSH port, username, credentials, and network firewall.",
            )
            return

        try:
            # Step 3: Run Remote Backup
            self.set_stage(record, BackupStage.CREATING_BACKUP)
            backup_svc = FrappeBackupService(ssh, server_config)
            success, backup_msg, artifacts = backup_svc.run_backup(
                log_cb=self.log,
                timeout_seconds=600,
            )
            if not success:
                self._fail_site(
                    record,
                    stage=BackupStage.CREATING_BACKUP,
                    error_msg=backup_msg,
                    action="Ensure bench directory path is valid and bench user has permissions to run backup.",
                )
                return

            record.artifacts = artifacts

            # Prepare local site folder
            clean_site_name = sanitize_filename(site_name)
            site_dest = date_folder / clean_site_name

            # Avoid overwriting earlier runs from the same day
            if site_dest.exists() and any(site_dest.iterdir()):
                time_suffix = datetime.now().strftime("%H%M%S")
                site_dest = date_folder / f"{clean_site_name}_{time_suffix}"

            site_dest.mkdir(parents=True, exist_ok=True)
            record.download_dir = str(site_dest)

            # Step 4: Download Files
            self.set_stage(record, BackupStage.DOWNLOADING_FILES)
            transfer_svc = FileTransferService(ssh)
            self._active_transfer_service = transfer_svc

            for artifact in record.artifacts:
                if self._cancel_requested:
                    self._fail_site(record, BackupStage.DOWNLOADING_FILES, "Download cancelled.", "")
                    return

                self.log(f"[{site_name}] Downloading {artifact.filename} ({artifact.size_bytes} bytes)...")
                ok = transfer_svc.download_artifact(
                    artifact=artifact,
                    destination_dir=site_dest,
                    progress_cb=self.progress_cb,
                )
                if not ok:
                    err = artifact.error_message or "Unknown transfer error"
                    if artifact.filename not in err:
                        err = f"Download failed for {artifact.filename}: {err}"
                    self._fail_site(
                        record,
                        stage=BackupStage.DOWNLOADING_FILES,
                        error_msg=err,
                        action="Check network stability and destination SSD write permissions.",
                    )
                    return

            # Step 5: Verification & Integrity Check
            self.set_stage(record, BackupStage.VERIFYING_INTEGRITY)
            verifier = VerificationService(ssh)
            for artifact in record.artifacts:
                self.log(f"[{site_name}] Verifying SHA-256 integrity for {artifact.filename}...")
                verified = verifier.verify_artifact(artifact)
                if not verified:
                    err = artifact.error_message or "Unknown verification error"
                    if artifact.filename not in err:
                        err = f"Verification failed for {artifact.filename}: {err}"
                    self._fail_site(
                        record,
                        stage=BackupStage.VERIFYING_INTEGRITY,
                        error_msg=err,
                        action="File integrity check failed. The downloaded file may be corrupted.",
                    )
                    return

            # Step 6: Finalize Manifest
            self.set_stage(record, BackupStage.FINALIZING_BACKUP)
            VerificationService.write_manifest(
                site_folder=site_dest,
                job_id=job_id,
                site_name=site_name,
                server_host=server_config.host,
                artifacts=record.artifacts,
            )

            has_db = any(a.artifact_type == "database" and a.verified for a in record.artifacts)
            has_files = any(a.artifact_type == "files" and a.verified for a in record.artifacts)
            has_priv = any(a.artifact_type == "private_files" and a.verified for a in record.artifacts)
            has_cfg = any(a.artifact_type == "config" and a.verified for a in record.artifacts)

            if has_db and has_files and has_priv and has_cfg:
                record.diagnostics = "Full backup complete: Database, Config, Public Files, and Private Files verified."
            elif has_db and not (has_files or has_priv):
                record.diagnostics = "Database backup verified. No public or private files archives were produced by the server."
            else:
                record.diagnostics = f"Backup completed with {len(record.artifacts)} verified artifact(s)."

            record.status = JobStatus.SUCCESS
            record.backup_stage = BackupStage.COMPLETED
            record.completed_at = datetime.now(timezone.utc).isoformat()
            self.log(f"[{site_name}] Backup completed successfully! Saved to {site_dest}")

        except Exception as exc:
            logger.exception("Unexpected error processing site %s", site_name)
            self._fail_site(
                record,
                stage=record.backup_stage,
                error_msg=f"Unexpected error: {exc}",
                action="Inspect application logs for traceback.",
            )
        finally:
            self._active_transfer_service = None
            ssh.close()

    def _fail_site(
        self,
        record: SiteJobRecord,
        stage: str,
        error_msg: str,
        action: str,
    ) -> None:
        """Mark a site as failed with diagnostic guidance."""
        record.status = JobStatus.FAILED
        record.backup_stage = stage
        record.error_message = error_msg
        record.suggested_action = action
        record.completed_at = datetime.now(timezone.utc).isoformat()
        self.log(f"[{record.site_name}] FAILED at stage '{stage}': {error_msg}")
