"""Application settings store wrapping database persistence."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import Optional

from src.storage.database import Database, get_db


@dataclass
class AppSettings:
    """Non-sensitive application preferences."""

    ssd_folder: str = ""
    retention_enabled: bool = False  # Destructive cleanup disabled by default!
    retention_days: int = 30
    retention_max_sets: int = 10
    retention_mode: str = "days"  # 'days' or 'sets'
    warn_system_drive: bool = True
    window_geometry: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class SettingsStore:
    """Facade for managing application preferences."""

    def __init__(self, db: Optional[Database] = None) -> None:
        self.db = db or get_db()

    def get_settings(self) -> AppSettings:
        raw_json = self.db.get_setting("app_settings")
        if not raw_json:
            return AppSettings()
        try:
            data = json.loads(raw_json)
            return AppSettings(**data)
        except Exception:
            return AppSettings()

    def save_settings(self, settings: AppSettings) -> None:
        raw_json = json.dumps(settings.to_dict())
        self.db.set_setting("app_settings", raw_json)

    def get_ssd_folder(self) -> str:
        return self.get_settings().ssd_folder

    def set_ssd_folder(self, folder: str) -> None:
        settings = self.get_settings()
        settings.ssd_folder = folder
        self.save_settings(settings)
