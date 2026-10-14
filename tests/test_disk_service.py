"""Tests for disk detection, accessibility validation, and path helpers."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile

import pytest

from src.services.disk_service import DiskService
from src.utils.formatters import (
    format_bytes,
    format_duration,
    format_speed,
    sanitize_filename,
)


def test_format_helpers():
    assert format_bytes(0) == "0 B"
    assert format_bytes(512) == "512 B"
    assert format_bytes(1024) == "1.00 KB"
    assert format_bytes(1048576) == "1.00 MB"
    assert format_bytes(1073741824) == "1.00 GB"

    assert format_speed(1048576) == "1.00 MB/s"
    assert format_speed(0) == "0 B/s"

    assert format_duration(30) == "30s"
    assert format_duration(125) == "02m 05s"
    assert format_duration(3665) == "01h 01m 05s"


def test_sanitize_filename():
    assert sanitize_filename("riya-associate.com") == "riya-associate.com"
    assert sanitize_filename("site:with*invalid?chars") == "site_with_invalid_chars"
    assert sanitize_filename("  trailing spaces. ") == "trailing spaces"
    assert sanitize_filename("") == "unnamed"


def test_destination_accessibility():
    with tempfile.TemporaryDirectory() as tmp_dir:
        dest = Path(tmp_dir) / "backups_folder"
        accessible, msg = DiskService.check_destination_accessibility(str(dest))
        assert accessible is True
        assert dest.exists()

    # Empty path
    accessible, msg = DiskService.check_destination_accessibility("")
    assert accessible is False
    assert "empty" in msg.lower()


def test_system_drive_identification():
    # Root '/' or 'C:\' should be identified as system drive
    sys_prefix = DiskService.get_system_drive_prefix()
    assert sys_prefix in ("/", "C:", "C")
