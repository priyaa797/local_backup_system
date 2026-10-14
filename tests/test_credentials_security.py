"""Tests for secure credential handling, OS store integration, and secret redaction."""

from __future__ import annotations

import logging
import os
import uuid

import pytest

from src.security.credentials import (
    delete_credential,
    get_credential,
    has_credential,
    save_credential,
)
from src.utils.logger import SecretRedactionFilter


def test_credential_store_crud():
    cred_id = f"test-cred-{uuid.uuid4()}"
    secret_value = "SuperSecretP@ssw0rd!123"

    # Save
    saved = save_credential(cred_id, secret_value)
    assert saved is True
    assert has_credential(cred_id) is True

    # Retrieve
    retrieved = get_credential(cred_id)
    assert retrieved == secret_value

    # Delete
    deleted = delete_credential(cred_id)
    assert deleted is True
    assert get_credential(cred_id) is None
    assert has_credential(cred_id) is False


def test_secret_redaction_in_logs():
    redactor = SecretRedactionFilter()

    # Password patterns
    test_cases = [
        ("Connecting with password=mySecretPassword to server", "Connecting with password=[REDACTED] to server"),
        ("Error: secret: 'hidden_pass_123' failed", "Error: secret:[REDACTED] failed"),
        ("bench backup --password=topsecret123 --with-files", "bench backup --password=[REDACTED] --with-files"),
    ]

    for raw, expected in test_cases:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg=raw,
            args=(),
            exc_info=None,
        )
        redactor.filter(record)
        assert "[REDACTED]" in record.msg
        assert "mySecretPassword" not in record.msg
        assert "hidden_pass_123" not in record.msg
        assert "topsecret123" not in record.msg


def test_private_key_redaction_in_logs():
    redactor = SecretRedactionFilter()
    raw_key = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEA0wJd8v3q...\n"
        "-----END RSA PRIVATE KEY-----"
    )
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg=f"Loaded key:\n{raw_key}",
        args=(),
        exc_info=None,
    )
    redactor.filter(record)
    assert "MIIEowIBAAKCAQEA0wJd8v3q" not in record.msg
    assert "[REDACTED_PRIVATE_KEY]" in record.msg
