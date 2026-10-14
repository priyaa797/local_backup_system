"""Background QThread worker for non-blocking SSH connection testing."""

from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from src.models.server_config import ServerConfig
from src.services.ssh_service import SSHService
from src.utils.logger import get_logger

logger = get_logger()


class TestConnectionWorker(QThread):
    """Executes SSH connectivity and bench verification in the background."""

    test_finished = Signal(bool, str)  # (success, message)

    def __init__(self, config: ServerConfig, parent=None) -> None:
        super().__init__(parent)
        self.config = config

    def run(self) -> None:
        logger.info("Testing connection to %s in background thread", self.config.host)
        ssh = SSHService(self.config, auto_accept_new_host_keys=True)
        try:
            success, message = ssh.test_connection_and_environment()
            self.test_finished.emit(success, message)
        except Exception as exc:
            logger.exception("Error during test connection: %s", exc)
            self.test_finished.emit(False, f"Test failed with unexpected error: {exc}")
        finally:
            ssh.close()
