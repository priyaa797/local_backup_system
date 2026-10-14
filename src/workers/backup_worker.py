"""Background QThread worker for executing backup jobs without blocking the GUI."""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QThread, Signal

from src.models.backup_job import BackupJob
from src.models.server_config import ServerConfig
from src.services.backup_coordinator import BackupCoordinator
from src.storage.database import get_db
from src.utils.logger import get_logger

logger = get_logger()


class BackupWorker(QThread):
    """Background worker executing backup jobs and emitting thread-safe progress updates."""

    log_emitted = Signal(str)
    stage_changed = Signal(str, str)  # site_name, stage_text
    file_progress_updated = Signal(str, int, int, float, float, float)  # filename, done, total, speed, avg_speed, eta
    job_progress_updated = Signal(int, int, float)  # done_sites, total_sites, pct
    job_finished = Signal(object)  # BackupJob

    def __init__(
        self,
        destination_folder: str,
        servers_to_backup: Optional[List[ServerConfig]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.destination_folder = destination_folder
        self.servers_to_backup = servers_to_backup
        self.coordinator: Optional[BackupCoordinator] = None

    def cancel(self) -> None:
        """Request immediate job cancellation."""
        if self.coordinator:
            self.coordinator.cancel()

    def run(self) -> None:
        """Thread entry point."""
        logger.info("BackupWorker thread started.")
        self.coordinator = BackupCoordinator(
            db=get_db(),
            log_callback=lambda msg: self.log_emitted.emit(msg),
            stage_callback=lambda site, stage: self.stage_changed.emit(site, stage),
            progress_callback=lambda fn, done, tot, spd, avg, eta: self.file_progress_updated.emit(
                fn, done, tot, spd, avg, eta
            ),
            job_progress_callback=lambda done_s, tot_s, pct: self.job_progress_updated.emit(
                done_s, tot_s, pct
            ),
        )

        try:
            job = self.coordinator.run_backup_job(
                destination_folder=self.destination_folder,
                servers_to_backup=self.servers_to_backup,
            )
            self.job_finished.emit(job)
        except Exception as exc:
            logger.exception("Fatal unhandled error inside BackupWorker: %s", exc)
            err_job = BackupJob(
                destination_path=self.destination_folder,
                status="failed",
                error_summary=f"Worker crashed: {exc}",
            )
            self.job_finished.emit(err_job)
