"""Local disk and SSD detection, space monitoring, and destination validation.

Detects drives across Windows, Linux, and macOS, identifies volume labels,
calculates free space, and warns when system drives or disconnected media are detected.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
from pathlib import Path
import platform
import shutil
import sys
from typing import List, Optional

import psutil

from src.utils.formatters import format_bytes
from src.utils.logger import get_logger

logger = get_logger()


@dataclass
class DriveInfo:
    """Detailed information for a detected storage volume."""

    device: str  # e.g. "E:\\" or "/dev/sdb1"
    mountpoint: str  # e.g. "E:\\" or "/media/backup"
    fstype: str  # e.g. "NTFS", "exFAT", "ext4"
    volume_label: str  # e.g. "SAMSUNG_T7" or "BackupSSD"
    total_bytes: int
    used_bytes: int
    free_bytes: int
    is_removable: bool
    is_system_drive: bool

    @property
    def free_space_formatted(self) -> str:
        return format_bytes(self.free_bytes)

    @property
    def total_space_formatted(self) -> str:
        return format_bytes(self.total_bytes)

    @property
    def percent_used(self) -> float:
        if self.total_bytes <= 0:
            return 0.0
        return round((self.used_bytes / self.total_bytes) * 100.0, 1)

    @property
    def display_name(self) -> str:
        label_part = f" [{self.volume_label}]" if self.volume_label else ""
        system_badge = " (System Drive)" if self.is_system_drive else ""
        return (
            f"{self.mountpoint}{label_part} - Free: {self.free_space_formatted} "
            f"of {self.total_space_formatted} ({self.fstype}){system_badge}"
        )


def _get_windows_volume_label(root_path: str) -> str:
    """Retrieve volume label on Windows via kernel32 GetVolumeInformationW."""
    if sys.platform != "win32":
        return ""
    try:
        volume_name_buffer = ctypes.create_unicode_buffer(1024)
        file_system_name_buffer = ctypes.create_unicode_buffer(1024)
        serial_number = ctypes.c_ulong()
        max_component_length = ctypes.c_ulong()
        file_system_flags = ctypes.c_ulong()

        clean_path = root_path if root_path.endswith("\\") else root_path + "\\"
        success = ctypes.windll.kernel32.GetVolumeInformationW(
            ctypes.c_wchar_p(clean_path),
            volume_name_buffer,
            ctypes.sizeof(volume_name_buffer),
            ctypes.byref(serial_number),
            ctypes.byref(max_component_length),
            ctypes.byref(file_system_flags),
            file_system_name_buffer,
            ctypes.sizeof(file_system_name_buffer),
        )
        if success:
            return volume_name_buffer.value
    except Exception as exc:
        logger.debug("Failed to retrieve Windows volume label for %s: %s", root_path, exc)
    return ""


class DiskService:
    """Manages drive detection, volume health, accessibility, and space calculations."""

    @staticmethod
    def get_system_drive_prefix() -> str:
        """Return prefix of system drive (e.g. 'C:' on Windows, '/' on Unix)."""
        if sys.platform == "win32":
            return os.environ.get("SystemDrive", "C:").upper()
        return "/"

    @classmethod
    def is_system_drive(cls, path_str: str) -> bool:
        """Determine if a given path is located on the operating system drive."""
        if not path_str:
            return False
        try:
            resolved = Path(path_str).resolve()
            if sys.platform == "win32":
                sys_drive = cls.get_system_drive_prefix()
                drive = os.path.splitdrive(str(resolved))[0].upper()
                return drive.startswith(sys_drive)
            # Linux / macOS: check if mount point is root '/'
            mount = cls.get_mount_point(str(resolved))
            return mount == "/" or mount.startswith("/home")
        except Exception:
            return False

    @staticmethod
    def get_mount_point(path_str: str) -> str:
        """Find the mount point for a specified path."""
        path = Path(path_str).resolve()
        while not path.is_mount() and path.parent != path:
            path = path.parent
        return str(path)

    @classmethod
    def list_available_drives(cls) -> List[DriveInfo]:
        """Enumerate all available physical and mounted storage volumes."""
        drives: List[DriveInfo] = []
        try:
            partitions = psutil.disk_partitions(all=False)
        except Exception as exc:
            logger.warning("Error enumerating partitions with psutil: %s", exc)
            return drives

        sys_prefix = cls.get_system_drive_prefix()

        for p in partitions:
            try:
                # Skip cdrom or virtual loop filesystems on linux
                if "cdrom" in p.opts.lower() or p.fstype.lower() in ("squashfs", "iso9660"):
                    continue

                usage = shutil.disk_usage(p.mountpoint)
                volume_label = ""
                is_system = False

                if sys.platform == "win32":
                    volume_label = _get_windows_volume_label(p.mountpoint)
                    drive_letter = os.path.splitdrive(p.mountpoint)[0].upper()
                    is_system = drive_letter.startswith(sys_prefix)
                    is_removable = "removable" in p.opts.lower()
                else:
                    is_system = p.mountpoint == "/"
                    is_removable = (
                        "/media" in p.mountpoint
                        or "/run/media" in p.mountpoint
                        or "/mnt" in p.mountpoint
                        or "/Volumes" in p.mountpoint
                    )

                drive_info = DriveInfo(
                    device=p.device,
                    mountpoint=p.mountpoint,
                    fstype=p.fstype,
                    volume_label=volume_label,
                    total_bytes=usage.total,
                    used_bytes=usage.used,
                    free_bytes=usage.free,
                    is_removable=is_removable,
                    is_system_drive=is_system,
                )
                drives.append(drive_info)
            except (PermissionError, FileNotFoundError, OSError) as exc:
                logger.debug("Skipping inaccessible mount %s: %s", p.mountpoint, exc)
                continue

        return drives

    @classmethod
    def check_destination_accessibility(cls, destination_folder: str) -> tuple[bool, str]:
        """Validate if target folder is writable and drive is present."""
        if not destination_folder or not destination_folder.strip():
            return False, "Destination folder path is empty. Please select a backup folder on your SSD."

        dest_path = Path(destination_folder).resolve()

        # Check if parent mount point exists
        mount = cls.get_mount_point(str(dest_path))
        if not os.path.exists(mount):
            return False, f"The backup drive '{mount}' is disconnected or not recognized by Windows."

        # Ensure directory exists or can be created
        try:
            dest_path.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            return False, f"Permission denied creating folder: {dest_path}. Check access rights."
        except OSError as exc:
            return False, f"Cannot access backup path {dest_path}: {exc}"

        # Test write permission by creating and removing a temporary canary file
        canary = dest_path / ".lbs_write_test.tmp"
        try:
            canary.write_text("ok", encoding="utf-8")
            canary.unlink(missing_ok=True)
        except PermissionError:
            return False, f"Backup folder {dest_path} is read-only or permission is denied."
        except OSError as exc:
            return False, f"Write check failed on {dest_path}: {exc}"

        return True, "Destination is accessible and writable."

    @classmethod
    def get_free_space(cls, path_str: str) -> int:
        """Return free space in bytes for given path, or 0 if unreachable."""
        try:
            if not os.path.exists(path_str):
                # Check parent mount
                mount = cls.get_mount_point(path_str)
                return shutil.disk_usage(mount).free
            return shutil.disk_usage(path_str).free
        except Exception:
            return 0
