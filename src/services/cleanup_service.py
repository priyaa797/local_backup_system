"""Safe local backup retention and cleanup service.

Ensures that destructive cleanup is only performed when explicitly enabled,
never deletes current backups, and only operates on verified date folders
matching the expected Local Backup System directory structure.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import re
import shutil
from typing import List, Tuple

from src.utils.formatters import format_bytes
from src.utils.logger import get_logger

logger = get_logger()

# Matches YYYY-MM-DD folder format
DATE_FOLDER_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


class CleanupService:
    """Safely calculates and executes retention cleanup on local SSD backups."""

    @classmethod
    def preview_cleanup(
        cls,
        backup_root: Path,
        mode: str = "days",  # 'days' or 'sets'
        retention_value: int = 30,
        exclude_paths: List[Path] | None = None,
    ) -> Tuple[List[Path], int]:
        """Scan backup_root and return list of folders eligible for deletion and total bytes.

        :param backup_root: Root backup folder on SSD.
        :param mode: 'days' (delete older than N days) or 'sets' (keep newest N date folders).
        :param retention_value: Number of days or sets.
        :param exclude_paths: Paths to strictly exclude (e.g., currently active backup run).
        :return: (list_of_folder_paths_to_delete, total_bytes_to_reclaim)
        """
        exclude_set = {p.resolve() for p in (exclude_paths or [])}
        eligible_folders: List[Tuple[datetime, Path]] = []

        if not backup_root.exists() or not backup_root.is_dir():
            return [], 0

        # Enumerate date folders (YYYY-MM-DD)
        for child in backup_root.iterdir():
            if not child.is_dir():
                continue
            match = DATE_FOLDER_RE.match(child.name)
            if not match:
                # Never touch folders that do not match date pattern
                continue

            try:
                folder_date = datetime.strptime(child.name, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                eligible_folders.append((folder_date, child))
            except ValueError:
                continue

        # Sort newest date first
        eligible_folders.sort(key=lambda x: x[0], reverse=True)

        to_delete: List[Path] = []
        now = datetime.now(timezone.utc)

        if mode == "days":
            cutoff_date = now - timedelta(days=retention_value)
            for folder_date, folder_path in eligible_folders:
                if folder_date < cutoff_date and folder_path.resolve() not in exclude_set:
                    to_delete.append(folder_path)
        elif mode == "sets":
            # Keep newest retention_value sets
            if len(eligible_folders) > retention_value:
                for _, folder_path in eligible_folders[retention_value:]:
                    if folder_path.resolve() not in exclude_set:
                        to_delete.append(folder_path)

        # Calculate space to reclaim
        total_bytes = 0
        for folder in to_delete:
            total_bytes += cls._calculate_folder_size(folder)

        logger.info(
            "Cleanup preview in %s: %d folder(s) eligible, %s reclaimable.",
            backup_root,
            len(to_delete),
            format_bytes(total_bytes),
        )
        return to_delete, total_bytes

    @classmethod
    def execute_cleanup(
        cls,
        folders_to_delete: List[Path],
        backup_root: Path,
    ) -> int:
        """Safely delete confirmed eligible backup folders.

        Performs strict parent containment validation to guarantee safety.
        """
        root_resolved = backup_root.resolve()
        deleted_count = 0

        for folder in folders_to_delete:
            resolved_folder = folder.resolve()

            # Safety check: must be a direct child of backup_root
            if resolved_folder.parent != root_resolved:
                logger.error(
                    "Safety violation: %s is not a direct child of %s! Aborting deletion.",
                    resolved_folder,
                    root_resolved,
                )
                continue

            # Safety check: folder name must match YYYY-MM-DD
            if not DATE_FOLDER_RE.match(resolved_folder.name):
                logger.error(
                    "Safety violation: %s does not match date format YYYY-MM-DD! Skipping.",
                    resolved_folder.name,
                )
                continue

            try:
                logger.info("Retention cleanup: Removing old backup folder %s", resolved_folder)
                shutil.rmtree(resolved_folder)
                deleted_count += 1
            except Exception as exc:
                logger.error("Failed to delete %s during cleanup: %s", resolved_folder, exc)

        logger.info("Retention cleanup finished. Removed %d folder(s).", deleted_count)
        return deleted_count

    @staticmethod
    def _calculate_folder_size(folder: Path) -> int:
        total = 0
        try:
            for root, _, files in os.walk(folder):
                for f in files:
                    fp = os.path.join(root, f)
                    try:
                        total += os.path.getsize(fp)
                    except OSError:
                        pass
        except Exception:
            pass
        return total
