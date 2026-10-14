"""Data models for Server and Site configuration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import uuid


@dataclass
class ServerConfig:
    """Configuration for a remote Frappe server and site instance."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    host: str = ""
    port: int = 22
    username: str = "frappe"
    auth_type: str = "password"  # 'password' or 'key'
    credential_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    key_path: Optional[str] = None
    site_name: str = ""
    bench_path: str = "/home/frappe/frappe-bench"
    enabled: bool = True
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    updated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def __post_init__(self) -> None:
        if not self.name and self.site_name:
            self.name = f"{self.site_name} ({self.host})"
        if not self.credential_id:
            self.credential_id = str(uuid.uuid4())

    def validate(self) -> list[str]:
        """Validate configuration fields, returning a list of validation errors."""
        errors: list[str] = []
        if not self.host.strip():
            errors.append("Host / Server IP is required.")
        if not (1 <= self.port <= 65535):
            errors.append("Port must be between 1 and 65535.")
        if not self.username.strip():
            errors.append("SSH Username is required.")
        if not self.site_name.strip():
            errors.append("Frappe Site Name is required.")
        elif any(c in self.site_name for c in [";", "&", "|", "`", "$", " ", "\\", "\"", "'"]):
            errors.append("Frappe Site Name contains invalid shell characters.")
        if self.auth_type not in ("password", "key"):
            errors.append("Authentication type must be 'password' or 'key'.")
        if self.auth_type == "key" and not self.key_path:
            errors.append("Private key path is required when using key authentication.")
        if not self.bench_path.strip():
            errors.append("Bench directory path is required.")
        return errors

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary suitable for SQLite or non-sensitive export.

        Never contains plain-text passwords.
        """
        data = asdict(self)
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ServerConfig:
        """Construct a ServerConfig from a dictionary."""
        # Filter keys to match dataclass fields
        allowed = {
            "id", "name", "host", "port", "username", "auth_type",
            "credential_id", "key_path", "site_name", "bench_path",
            "enabled", "created_at", "updated_at"
        }
        filtered = {k: v for k, v in data.items() if k in allowed}
        return cls(**filtered)
