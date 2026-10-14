"""Application logging with automatic secret redaction and log rotation.

Ensures that passwords, private keys, authentication tokens, and sensitive
credentials never appear in console output or disk log files.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import sys
from typing import Optional

from src.storage.app_paths import get_logs_dir

LOGGER_NAME = "LocalBackupSystem"
_logger_initialized = False

# Regex patterns to redact sensitive credentials
SENSITIVE_PATTERNS = [
    re.compile(r"(password['\":\s=]+)([^\s,;'\"]+)", re.IGNORECASE),
    re.compile(r"(secret['\":\s=]+)([^\s,;'\"]+)", re.IGNORECASE),
    re.compile(r"(token['\":\s=]+)([^\s,;'\"]+)", re.IGNORECASE),
    re.compile(r"(-----BEGIN [A-Z ]+ PRIVATE KEY-----)[\s\S]*?(-----END [A-Z ]+ PRIVATE KEY-----)", re.MULTILINE),
    re.compile(r"(--password[\s=]+)([^\s]+)", re.IGNORECASE),
]


class SecretRedactionFilter(logging.Filter):
    """Logging filter that scrubs sensitive patterns from all log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self.redact(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {
                    k: (self.redact(v) if isinstance(v, str) else v)
                    for k, v in record.args.items()
                }
            elif isinstance(record.args, (list, tuple)):
                record.args = tuple(
                    (self.redact(arg) if isinstance(arg, str) else arg)
                    for arg in record.args
                )
        return True

    @staticmethod
    def redact(text: str) -> str:
        for pattern in SENSITIVE_PATTERNS:
            if "PRIVATE KEY" in pattern.pattern:
                text = pattern.sub(r"\1 [REDACTED_PRIVATE_KEY] \2", text)
            else:
                text = pattern.sub(r"\1[REDACTED]", text)
        return text


def get_current_log_file() -> Path:
    """Return the active log file path."""
    return get_logs_dir() / "local_backup_system.log"


def setup_logger(
    level: int = logging.INFO,
    log_file: Optional[Path] = None,
    console_output: bool = True,
) -> logging.Logger:
    """Initialize and configure the central application logger."""
    global _logger_initialized
    logger = logging.getLogger(LOGGER_NAME)

    if _logger_initialized:
        return logger

    logger.setLevel(level)
    logger.propagate = False

    # Redaction filter
    redactor = SecretRedactionFilter()

    # Formatter
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s:%(threadName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Rotating file handler (10 MB max, up to 5 backups = 50 MB max)
    if log_file is None:
        log_file = get_current_log_file()

    file_handler = RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redactor)
    logger.addHandler(file_handler)

    # Console handler (for terminal/debugger)
    if console_output:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        console_handler.addFilter(redactor)
        logger.addHandler(console_handler)

    _logger_initialized = True
    return logger


def get_logger() -> logging.Logger:
    """Return the application logger, initializing if necessary."""
    global _logger_initialized
    if not _logger_initialized:
        return setup_logger()
    return logging.getLogger(LOGGER_NAME)
