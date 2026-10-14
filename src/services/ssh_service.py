"""SSH connection management, authentication, command execution, and latency testing.

Uses Paramiko with strict host key verification and credential separation.
All command executions capture stdout and stderr safely without shell concatenation.
"""

from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Callable, Optional, Tuple

import paramiko

from src.models.server_config import ServerConfig
from src.security.credentials import get_credential
from src.security.host_key_verifier import (
    VerifyingHostKeyPolicy,
    load_app_host_keys,
)
from src.utils.logger import get_logger

logger = get_logger()


class SSHService:
    """Manages secure Paramiko SSH sessions for Frappe server communication."""

    def __init__(
        self,
        config: ServerConfig,
        host_key_approval_cb: Optional[Callable[[str, str, str], bool]] = None,
        auto_accept_new_host_keys: bool = False,
    ) -> None:
        self.config = config
        self.client: Optional[paramiko.SSHClient] = None
        self.host_key_approval_cb = host_key_approval_cb
        self.auto_accept_new_host_keys = auto_accept_new_host_keys
        self.latency_ms: float = 0.0

    def connect(self, timeout: int = 20) -> Tuple[bool, str]:
        """Establish authenticated SSH connection to configured server."""
        self.close()

        client = paramiko.SSHClient()
        load_app_host_keys(client)

        policy = VerifyingHostKeyPolicy(
            approval_callback=self.host_key_approval_cb,
            auto_accept_new=self.auto_accept_new_host_keys,
        )
        client.set_missing_host_key_policy(policy)

        # Retrieve secret from secure credential store
        secret = get_credential(self.config.credential_id)

        start_time = time.perf_counter()
        try:
            connect_kwargs = {
                "hostname": self.config.host,
                "port": self.config.port,
                "username": self.config.username,
                "timeout": timeout,
                "banner_timeout": timeout,
                "auth_timeout": timeout,
                "allow_agent": False,
                "look_for_keys": False,
            }

            if self.config.auth_type == "password":
                if not secret:
                    return False, f"Password for {self.config.username}@{self.config.host} not found in secure storage."
                connect_kwargs["password"] = secret
            elif self.config.auth_type == "key":
                if not self.config.key_path or not os.path.exists(self.config.key_path):
                    return False, f"Private key file not found: {self.config.key_path}"
                connect_kwargs["key_filename"] = self.config.key_path
                if secret:
                    # Passphrase for private key
                    connect_kwargs["passphrase"] = secret
            else:
                return False, f"Unsupported authentication type: {self.config.auth_type}"

            logger.info(
                "Connecting to %s@%s:%d using %s authentication...",
                self.config.username,
                self.config.host,
                self.config.port,
                self.config.auth_type,
            )

            client.connect(**connect_kwargs)
            self.latency_ms = round((time.perf_counter() - start_time) * 1000.0, 1)
            self.client = client
            logger.info("Connected successfully to %s (latency: %.1f ms)", self.config.host, self.latency_ms)
            return True, f"Connected successfully (latency: {self.latency_ms} ms)."

        except paramiko.AuthenticationException as exc:
            msg = f"Authentication failed for {self.config.username}@{self.config.host}. Check credentials."
            logger.error(msg)
            return False, msg
        except paramiko.BadHostKeyException as exc:
            msg = f"Host key mismatch for {self.config.host}! Possible security risk or server reinstall."
            logger.error(msg)
            return False, msg
        except paramiko.SSHException as exc:
            msg = f"SSH error connecting to {self.config.host}: {exc}"
            logger.error(msg)
            return False, msg
        except (TimeoutError, OSError) as exc:
            msg = f"Network timeout or unreachable host {self.config.host}:{self.config.port} ({exc})"
            logger.error(msg)
            return False, msg
        except Exception as exc:
            msg = f"Unexpected connection error to {self.config.host}: {exc}"
            logger.exception("Unexpected error connecting to %s", self.config.host)
            return False, msg

    def execute_command(
        self,
        command: str,
        cwd: Optional[str] = None,
        stdout_cb: Optional[Callable[[str], None]] = None,
        stderr_cb: Optional[Callable[[str], None]] = None,
        timeout: Optional[int] = 300,
    ) -> Tuple[int, str, str]:
        """Execute a remote command via SSH session.

        Streams output lines in real time and returns (exit_code, full_stdout, full_stderr).
        """
        if self.client is None:
            raise RuntimeError("SSH Client is not connected. Call connect() first.")

        if cwd:
            # Change directory safely
            full_command = f"cd {cwd} && {command}"
        else:
            full_command = command

        logger.info("Executing remote command: %s", command)
        transport = self.client.get_transport()
        if transport is None or not transport.is_active():
            raise RuntimeError("SSH transport is not active.")

        channel = transport.open_session()
        channel.settimeout(timeout)
        channel.exec_command(full_command)

        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        stdout_file = channel.makefile("r", -1)
        stderr_file = channel.makefile_stderr("r", -1)

        # Stream lines while command executes
        while not channel.exit_status_ready():
            # Read stdout
            line = stdout_file.readline()
            while line:
                stdout_chunks.append(line)
                if stdout_cb:
                    stdout_cb(line.rstrip("\r\n"))
                line = stdout_file.readline()

            # Read stderr
            err_line = stderr_file.readline()
            while err_line:
                stderr_chunks.append(err_line)
                if stderr_cb:
                    stderr_cb(err_line.rstrip("\r\n"))
                err_line = stderr_file.readline()

            time.sleep(0.05)

        # Flush remaining output
        for line in stdout_file:
            stdout_chunks.append(line)
            if stdout_cb:
                stdout_cb(line.rstrip("\r\n"))

        for err_line in stderr_file:
            stderr_chunks.append(err_line)
            if stderr_cb:
                stderr_cb(err_line.rstrip("\r\n"))

        exit_code = channel.recv_exit_status()
        full_stdout = "".join(stdout_chunks)
        full_stderr = "".join(stderr_chunks)

        logger.info("Command completed with exit code %d", exit_code)
        return exit_code, full_stdout, full_stderr

    def open_sftp(self) -> paramiko.SFTPClient:
        """Open SFTP client on active SSH connection."""
        if self.client is None:
            raise RuntimeError("Cannot open SFTP: SSH Client is not connected.")
        return self.client.open_sftp()

    def test_connection_and_environment(self) -> Tuple[bool, str]:
        """Perform comprehensive connection test: SSH login, Bench directory, Bench binary."""
        connected, msg = self.connect(timeout=15)
        if not connected:
            return False, msg

        try:
            # Check bench directory
            bench_path = self.config.bench_path.strip()
            check_cmd = f"test -d {bench_path} && echo 'BENCH_DIR_OK' || echo 'BENCH_DIR_MISSING'"
            exit_code, stdout, stderr = self.execute_command(check_cmd, timeout=15)
            if "BENCH_DIR_OK" not in stdout:
                return (
                    False,
                    f"Connected to {self.config.host}, but bench directory '{bench_path}' does not exist on server.",
                )

            # Check bench command presence
            check_bench = f"cd {bench_path} && (which bench || test -f env/bin/bench) && echo 'BENCH_CMD_OK'"
            exit_code, stdout, stderr = self.execute_command(check_bench, timeout=15)
            if "BENCH_CMD_OK" not in stdout:
                logger.warning("Bench command not found in standard PATH, will test direct execution")

            # Check site directory
            site_check = (
                f"cd {bench_path} && test -d sites/{self.config.site_name} && echo 'SITE_DIR_OK' || echo 'SITE_DIR_MISSING'"
            )
            exit_code, stdout, stderr = self.execute_command(site_check, timeout=15)
            if "SITE_DIR_OK" not in stdout:
                return (
                    False,
                    f"Bench directory found, but site 'sites/{self.config.site_name}' does not exist in {bench_path}.",
                )

            return (
                True,
                f"Success! Connected to {self.config.host} (Latency: {self.latency_ms} ms). Bench and site '{self.config.site_name}' verified.",
            )

        except Exception as exc:
            return False, f"Error validating remote bench environment: {exc}"
        finally:
            self.close()

    def close(self) -> None:
        """Safely close active SSH session."""
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass
            self.client = None
