"""SFTP file transfer management with progress streaming, speed estimation, and atomic partial writes.

Downloads backup artifacts using temporary '.part' files to guarantee that
interrupted or corrupted transfers never appear as valid backups.
"""

from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Callable, Optional

import paramiko

from src.models.backup_artifact import BackupArtifact
from src.services.ssh_service import SSHService
from src.utils.logger import get_logger

logger = get_logger()

# Callback signature: (filename, transferred_bytes, total_bytes, current_speed, avg_speed, eta_seconds)
ProgressCallback = Callable[[str, int, int, float, float, float], None]


class FileTransferService:
    """Handles streaming SFTP downloads with real-time speed and ETA calculation."""

    def __init__(self, ssh_service: SSHService) -> None:
        self.ssh = ssh_service
        self._cancel_requested = False

    def cancel(self) -> None:
        """Signal ongoing transfer to abort immediately."""
        self._cancel_requested = True

    def download_artifact(
        self,
        artifact: BackupArtifact,
        destination_dir: Path,
        progress_cb: Optional[ProgressCallback] = None,
        chunk_size: int = 65536,  # 64 KB
    ) -> bool:
        """Download remote artifact to local destination_dir.

        Downloads first to <filename>.part, then verifies size before renaming.
        """
        self._cancel_requested = False
        destination_dir.mkdir(parents=True, exist_ok=True)

        final_local_path = destination_dir / artifact.filename
        part_local_path = destination_dir / f"{artifact.filename}.part"

        # Remove previous leftover .part file if any
        if part_local_path.exists():
            try:
                part_local_path.unlink()
            except OSError:
                pass

        logger.info(
            "Starting download of %s (%d bytes) to %s",
            artifact.filename,
            artifact.size_bytes,
            part_local_path,
        )

        sftp = self.ssh.open_sftp()
        try:
            # Check remote file attributes
            try:
                remote_stat = sftp.stat(artifact.remote_path)
                total_bytes = remote_stat.st_size or artifact.size_bytes
                artifact.size_bytes = total_bytes
            except Exception as exc:
                logger.error("Could not stat remote file %s: %s", artifact.remote_path, exc)
                artifact.status = "failed"
                artifact.error_message = f"Remote file missing or unreadable: {exc}"
                return False

            artifact.status = "downloading"
            start_time = time.perf_counter()
            last_time = start_time
            transferred_bytes = 0
            last_bytes = 0

            with sftp.open(artifact.remote_path, "rb") as remote_file:
                with open(part_local_path, "wb") as local_file:
                    while True:
                        if self._cancel_requested:
                            logger.warning("Transfer of %s cancelled by user", artifact.filename)
                            artifact.status = "cancelled"
                            artifact.error_message = "Transfer cancelled by user."
                            part_local_path.unlink(missing_ok=True)
                            return False

                        chunk = remote_file.read(chunk_size)
                        if not chunk:
                            break

                        local_file.write(chunk)
                        transferred_bytes += len(chunk)

                        now = time.perf_counter()
                        delta_t = now - last_time

                        # Update metrics roughly every 0.15 - 0.25 seconds
                        if delta_t >= 0.2 or transferred_bytes == total_bytes:
                            elapsed = now - start_time
                            avg_speed = (transferred_bytes / elapsed) if elapsed > 0 else 0.0
                            current_speed = ((transferred_bytes - last_bytes) / delta_t) if delta_t > 0 else 0.0

                            remaining_bytes = max(0, total_bytes - transferred_bytes)
                            eta = (remaining_bytes / avg_speed) if avg_speed > 0 else 0.0

                            if progress_cb:
                                progress_cb(
                                    artifact.filename,
                                    transferred_bytes,
                                    total_bytes,
                                    current_speed,
                                    avg_speed,
                                    eta,
                                )

                            last_time = now
                            last_bytes = transferred_bytes

            # Check downloaded size against expected size
            downloaded_size = part_local_path.stat().st_size
            if total_bytes > 0 and downloaded_size != total_bytes:
                err = f"Size mismatch for {artifact.filename}: expected {total_bytes} bytes, received {downloaded_size} bytes."
                logger.error(err)
                artifact.status = "failed"
                artifact.error_message = err
                part_local_path.unlink(missing_ok=True)
                return False

            # Success: rename .part to final file
            if final_local_path.exists():
                final_local_path.unlink()
            part_local_path.rename(final_local_path)
            artifact.local_path = str(final_local_path)
            artifact.size_bytes = downloaded_size
            artifact.status = "downloaded"
            logger.info("Successfully downloaded %s (%d bytes)", artifact.filename, downloaded_size)
            return True

        except Exception as exc:
            msg = f"Transfer failed for {artifact.filename}: {exc}"
            logger.error(msg)
            artifact.status = "failed"
            artifact.error_message = msg
            part_local_path.unlink(missing_ok=True)
            return False
        finally:
            try:
                sftp.close()
            except Exception:
                pass
