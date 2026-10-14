"""SSH Host Key verification and management.

Strictly verifies remote host keys and manages an isolated known_hosts file.
Host key checking is never bypassed or set to silent AutoAdd without tracking.
"""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from typing import Callable, Optional

import paramiko
from paramiko.client import MissingHostKeyPolicy

from src.storage.app_paths import get_known_hosts_path
from src.utils.logger import get_logger

logger = get_logger()


def get_key_fingerprint(key: paramiko.PKey) -> str:
    """Calculate the SHA256 fingerprint of a public key in OpenSSH format."""
    raw = key.asbytes()
    digest = hashlib.sha256(raw).digest()
    b64 = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"SHA256:{b64}"


class VerifyingHostKeyPolicy(MissingHostKeyPolicy):
    """Custom Paramiko policy that prompts or verifies unknown host keys.

    Never quietly ignores host keys. Mismatched keys always raise BadHostKeyException.
    """

    def __init__(
        self,
        approval_callback: Optional[Callable[[str, str, str], bool]] = None,
        auto_accept_new: bool = False,
    ) -> None:
        """Initialize policy.

        :param approval_callback: Optional callback(hostname, key_type, fingerprint) -> bool
        :param auto_accept_new: If True and no callback, new host keys are accepted and saved.
        """
        self.approval_callback = approval_callback
        self.auto_accept_new = auto_accept_new

    def missing_host_key(
        self, client: paramiko.SSHClient, hostname: str, key: paramiko.PKey
    ) -> None:
        key_type = key.get_name()
        fingerprint = get_key_fingerprint(key)
        known_hosts_path = get_known_hosts_path()

        logger.info(
            "Verifying host key for %s (Type: %s, Fingerprint: %s)",
            hostname,
            key_type,
            fingerprint,
        )

        approved = False
        if self.approval_callback is not None:
            approved = self.approval_callback(hostname, key_type, fingerprint)
        elif self.auto_accept_new:
            logger.info("Auto-accepting new host key for %s into known_hosts", hostname)
            approved = True

        if not approved:
            err_msg = (
                f"SSH Host key for {hostname} ({key_type} {fingerprint}) is not trusted "
                "or was rejected by user."
            )
            logger.error(err_msg)
            raise paramiko.SSHException(err_msg)

        # Save approved host key into application's known_hosts
        try:
            known_hosts_path.parent.mkdir(parents=True, exist_ok=True)
            # Add to client's host keys and persist to file
            client._host_keys.add(hostname, key_type, key)
            client.save_host_keys(str(known_hosts_path))
            logger.info("Saved host key for %s to %s", hostname, known_hosts_path)
        except Exception as exc:
            logger.warning("Failed to persist host key to %s: %s", known_hosts_path, exc)


def load_app_host_keys(client: paramiko.SSHClient) -> None:
    """Load known hosts from the application's isolated known_hosts file."""
    known_hosts_path = get_known_hosts_path()
    if known_hosts_path.exists():
        try:
            client.load_host_keys(str(known_hosts_path))
        except Exception as exc:
            logger.warning("Error loading known_hosts from %s: %s", known_hosts_path, exc)
    else:
        # Create empty file
        known_hosts_path.touch(mode=0o600, exist_ok=True)


def check_host_key_status(hostname: str, port: int = 22) -> dict[str, str]:
    """Inspect known_hosts to see if host is already recorded."""
    known_hosts_path = get_known_hosts_path()
    result = {"known": "no", "fingerprint": "", "key_type": ""}
    if not known_hosts_path.exists():
        return result

    try:
        host_keys = paramiko.HostKeys(str(known_hosts_path))
        entry_name = f"[{hostname}]:{port}" if port != 22 else hostname
        if entry_name in host_keys:
            key_dict = host_keys[entry_name]
            for k_type, pkey in key_dict.items():
                result["known"] = "yes"
                result["key_type"] = k_type
                result["fingerprint"] = get_key_fingerprint(pkey)
                break
    except Exception as exc:
        logger.debug("Failed inspecting known_hosts for %s: %s", hostname, exc)

    return result
