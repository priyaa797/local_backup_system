"""Tests for retention cleanup calculations and safety constraints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

import pytest

from src.services.cleanup_service import CleanupService


def test_cleanup_preview_by_days():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)

        # Create older folder (60 days ago)
        old_date = (datetime.now(timezone.utc) - timedelta(days=60)).strftime("%Y-%m-%d")
        old_folder = root / old_date
        old_folder.mkdir()
        (old_folder / "test.tar").write_bytes(b"x" * 1024)

        # Create recent folder (5 days ago)
        recent_date = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")
        recent_folder = root / recent_date
        recent_folder.mkdir()
        (recent_folder / "test2.tar").write_bytes(b"x" * 2048)

        # Non-date folder (must NEVER be touched!)
        other_folder = root / "PersonalDocuments"
        other_folder.mkdir()
        (other_folder / "doc.txt").write_text("important")

        to_delete, total_bytes = CleanupService.preview_cleanup(
            backup_root=root,
            mode="days",
            retention_value=30,  # 30 days
        )

        assert len(to_delete) == 1
        assert to_delete[0].name == old_date
        assert total_bytes == 1024


def test_cleanup_preview_by_sets():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)

        # Create 4 date folders
        dates = ["2026-10-01", "2026-10-05", "2026-10-10", "2026-10-14"]
        for d in dates:
            f = root / d
            f.mkdir()
            (f / "db.sql").write_bytes(b"dummy")

        # Keep latest 2 sets
        to_delete, _ = CleanupService.preview_cleanup(
            backup_root=root,
            mode="sets",
            retention_value=2,
        )

        deleted_names = [f.name for f in to_delete]
        assert len(to_delete) == 2
        assert "2026-10-01" in deleted_names
        assert "2026-10-05" in deleted_names
        assert "2026-10-10" not in deleted_names
        assert "2026-10-14" not in deleted_names


def test_cleanup_execution_safety():
    with tempfile.TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        old_folder = root / "2025-01-01"
        old_folder.mkdir()
        (old_folder / "data.sql").write_bytes(b"old data")

        deleted_count = CleanupService.execute_cleanup([old_folder], backup_root=root)
        assert deleted_count == 1
        assert not old_folder.exists()

        # Attempt to delete path outside backup_root (safety violation test)
        with tempfile.TemporaryDirectory() as outside_dir:
            outside_folder = Path(outside_dir) / "2025-01-01"
            outside_folder.mkdir()

            deleted_outside = CleanupService.execute_cleanup([outside_folder], backup_root=root)
            assert deleted_outside == 0
            assert outside_folder.exists()  # Kept safe!
