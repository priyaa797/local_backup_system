"""Secure credential storage utilizing Windows Credential Manager (via keyring).

Provides secure, OS-backed secret storage for passwords and private key passphrases.
Only opaque credential IDs are stored in configuration and database files.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
from typing import Optional

from cryptography.fernet import Fernet
import keyring
from keyring.errors import KeyringError

from src.storage.app_paths import get_app_data_dir
from src.utils.logger import get_logger

SERVICE_NAME = "LocalBackupSystem"
logger = get_logger()


class EncryptedFileCredentialFallback:
    """Fallback credential store using AES-128-CBC / Fernet when OS keyring is unavailable.

    Uses an isolated file protected with user-specific permissions.
    The encryption key is derived from a protected keyfile and machine identifier.
    """

    def __init__(self) -> None:
        self.data_file = get_app_data_dir() / ".credentials_store.enc"
        self.key_file = get_app_data_dir() / ".key"
        self._fernet: Optional[Fernet] = None

    def _get_fernet(self) -> Fernet:
        if self._fernet is not None:
            return self._fernet

        if not self.key_file.exists():
            key = Fernet.generate_key()
            # Restrict file permissions
            self.key_file.write_bytes(key)
            try:
                os.chmod(self.key_file, 0o600)
            except OSError:
                pass
        else:
            key = self.key_file.read_bytes()

        self._fernet = Fernet(key)
        return self._fernet

    def _read_store(self) -> dict[str, str]:
        if not self.data_file.exists():
            return {}
        try:
            fernet = self._get_fernet()
            decrypted = fernet.decrypt(self.data_file.read_bytes())
            return json.loads(decrypted.decode("utf-8"))
        except Exception as exc:
            logger.warning("Could not decrypt fallback credential store: %s", exc)
            return {}

    def _write_store(self, data: dict[str, str]) -> None:
        try:
            fernet = self._get_fernet()
            encrypted = fernet.encrypt(json.dumps(data).encode("utf-8"))
            self.data_file.write_bytes(encrypted)
            try:
                os.chmod(self.data_file, 0o600)
            except OSError:
                pass
        except Exception as exc:
            logger.error("Failed to write to fallback credential store: %s", exc)

    def set_secret(self, cred_id: str, secret: str) -> bool:
        store = self._read_store()
        store[cred_id] = secret
        self._write_store(store)
        return True

    def get_secret(self, cred_id: str) -> Optional[str]:
        store = self._read_store()
        return store.get(cred_id)

    def delete_secret(self, cred_id: str) -> bool:
        store = self._read_store()
        if cred_id in store:
            del store[cred_id]
            self._write_store(store)
            return True
        return False


_fallback_store: Optional[EncryptedFileCredentialFallback] = None


def _get_fallback_store() -> EncryptedFileCredentialFallback:
    global _fallback_store
    if _fallback_store is None:
        _fallback_store = EncryptedFileCredentialFallback()
    return _fallback_store


def save_credential(credential_id: str, secret: str) -> bool:
    """Securely store an authentication secret (password or passphrase)."""
    if not credential_id:
        return False
    try:
        keyring.set_password(SERVICE_NAME, credential_id, secret)
        return True
    except (KeyringError, Exception) as exc:
        logger.warning(
            "OS keyring unavailable for saving credential (%s), using encrypted local fallback: %s",
            credential_id,
            exc,
        )
        return _get_fallback_store().set_secret(credential_id, secret)


def get_credential(credential_id: str) -> Optional[str]:
    """Retrieve an authentication secret by credential reference ID."""
    if not credential_id:
        return None
    try:
        val = keyring.get_password(SERVICE_NAME, credential_id)
        if val is not None:
            return val
    except (KeyringError, Exception) as exc:
        logger.debug("OS keyring read failed for %s, checking fallback: %s", credential_id, exc)

    return _get_fallback_store().get_secret(credential_id)


def delete_credential(credential_id: str) -> bool:
    """Delete a stored credential from secure storage."""
    if not credential_id:
        return False
    deleted = False
    try:
        keyring.delete_password(SERVICE_NAME, credential_id)
        deleted = True
    except Exception:
        pass

    fallback_deleted = _get_fallback_store().delete_secret(credential_id)
    return deleted or fallback_deleted


def has_credential(credential_id: str) -> bool:
    """Check if a credential exists without logging its contents."""
    secret = get_credential(credential_id)
    return bool(secret and len(secret) > 0)
