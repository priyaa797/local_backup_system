"""SFTP file transfer management with progress streaming, speed estimation, and atomic partial writes.

Downloads backup artifacts using temporary '.part' files to guarantee that
interrupted or corrupted transfers never appear as valid backups.
"""

from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Callable, List, Optional, Tuple

import paramiko

from src.models.backup_artifact import BackupArtifact
from src.services.ssh_service import SSHService
from src.utils.formatters import sanitize_filename
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

    def validate_remote_artifacts(
        self,
        artifacts: List[BackupArtifact],
        log_cb: Optional[Callable[[str], None]] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Verify that all artifacts exist on the remote server via SFTP stat before downloading.

        Returns (True, None) if all artifacts exist, or (False, error_details) on failure.
        """
        if not artifacts:
            return False, "No backup artifacts to download."

        sftp = self.ssh.open_sftp()
        try:
            try:
                sftp_cwd = sftp.normalize(".")
            except Exception:
                sftp_cwd = "unknown"

            for art in artifacts:
                try:
                    st = sftp.stat(art.remote_path)
                    actual_size = st.st_size if st.st_size is not None else art.size_bytes
                    art.size_bytes = actual_size
                    logger.info(
                        "Pre-transfer check OK for %s (%d bytes) at '%s'",
                        art.filename,
                        actual_size,
                        art.remote_path,
                    )
                    if log_cb:
                        log_cb(f"Pre-transfer verified: {art.filename} ({actual_size} bytes)")
                except Exception as exc:
                    err_str = str(exc)
                    errno_val = getattr(exc, "errno", None)
                    if isinstance(exc, FileNotFoundError) or errno_val == 2 or "No such file" in err_str:
                        reason = f"File missing at '{art.remote_path}' ([Errno 2] No such file)"
                    elif errno_val == 13 or "Permission denied" in err_str:
                        reason = f"Permission denied accessing '{art.remote_path}' ([Errno 13] Permission denied)"
                    else:
                        reason = f"SFTP stat error for '{art.remote_path}': {type(exc).__name__}: {exc}"

                    diag = (
                        f"Pre-transfer validation failed for {art.filename}: {reason} "
                        f"(SFTP cwd: '{sftp_cwd}'). Aborting before download to prevent partial transfers."
                    )
                    logger.error(diag)
                    art.status = "failed"
                    art.error_message = diag
                    return False, diag

            return True, None
        finally:
            sftp.close()

    def download_artifact(
        self,
        artifact: BackupArtifact,
        destination_dir: Path,
        progress_cb: Optional[ProgressCallback] = None,
        chunk_size: int = 262144,  # 256 KB
    ) -> bool:
        """Download remote artifact to local destination_dir.

        Downloads first to <filename>.part, then verifies size before renaming.
        """
        self._cancel_requested = False
        destination_dir.mkdir(parents=True, exist_ok=True)

        safe_filename = sanitize_filename(artifact.filename)
        final_local_path = destination_dir / safe_filename
        part_local_path = destination_dir / f"{safe_filename}.part"

        # Remove previous leftover .part file if any
        if part_local_path.exists():
            try:
                part_local_path.unlink()
            except OSError:
                pass

        logger.info(
            "Starting download of %s (%d bytes) from remote path '%s' to '%s'",
            artifact.filename,
            artifact.size_bytes,
            artifact.remote_path,
            part_local_path,
        )

        sftp = self.ssh.open_sftp()
        try:
            try:
                sftp_cwd = sftp.normalize(".")
            except Exception:
                sftp_cwd = "unknown"

            # Check remote file attributes immediately before download
            try:
                remote_stat = sftp.stat(artifact.remote_path)
                total_bytes = remote_stat.st_size if remote_stat.st_size is not None else artifact.size_bytes
                artifact.size_bytes = total_bytes
                logger.info(
                    "SFTP stat verified for %s (%d bytes) at '%s' (SFTP cwd: '%s')",
                    artifact.filename,
                    total_bytes,
                    artifact.remote_path,
                    sftp_cwd,
                )
            except Exception as exc:
                err_str = str(exc)
                errno_val = getattr(exc, "errno", None)
                if isinstance(exc, FileNotFoundError) or errno_val == 2 or "No such file" in err_str:
                    err = (
                        f"Remote file missing: SFTP stat failed for '{artifact.remote_path}' "
                        f"(SFTP cwd: '{sftp_cwd}'): [Errno 2] No such file."
                    )
                elif errno_val == 13 or "Permission denied" in err_str:
                    err = (
                        f"Permission denied: SFTP stat failed for '{artifact.remote_path}' "
                        f"(SFTP cwd: '{sftp_cwd}'): [Errno 13] Permission denied."
                    )
                else:
                    err = (
                        f"SFTP stat error for '{artifact.remote_path}' "
                        f"(SFTP cwd: '{sftp_cwd}'): {type(exc).__name__}: {exc}"
                    )

                logger.error("Could not stat remote file %s: %s", artifact.remote_path, err)
                artifact.status = "failed"
                artifact.error_message = err
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
