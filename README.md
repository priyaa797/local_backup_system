# Local Backup System

A production-quality desktop application that automates downloading backups from multiple remote **Frappe / ERPNext** servers directly to a locally connected external SSD.

Designed for non-technical office assistants with a modern graphical interface—no terminal access, command line interaction, or manual Linux server operations required.

---

## 🌟 Key Features

- **One-Click Backup Workflow**: Prominent "Start Backup Now" button with automated multi-server orchestration.
- **Modern Graphical Interface**: Built with PySide6 (Qt) featuring clear labels, readable typography, and responsive progress feedback.
- **Live Progress Dashboard**:
  - Overall job progress across all servers and sites.
  - Multi-stage pipeline tracking (Validating → Connecting → Backing up → Discovering → Downloading → Verifying → Finalizing).
  - Per-file transfer progress with instantaneous speed, average speed, and estimated time remaining (ETA).
  - Live streaming event log.
- **External SSD Detection & Space Monitoring**:
  - Automatically enumerates local disk drives, volume labels, total capacity, and free space.
  - Detects disconnected external SSDs before and during operations.
  - Warns if the user accidentally selects the primary operating system drive (`C:\`).
  - Pre-flight checks ensure adequate storage before downloading.
- **Multi-Server & Multi-Site Support**:
  - Configure multiple Frappe servers and multiple sites per server.
  - Custom Bench directory per server with default `/home/{user}/frappe-bench`.
  - Built-in "Test Connection" tool to verify SSH and Frappe Bench accessibility.
- **Enterprise-Grade Credential Security**:
  - Passwords and SSH private key passphrases are stored in **Windows Credential Manager** (or OS secure keyring).
  - Credentials never appear in SQLite, JSON files, application logs, console output, or Git commits.
  - Supports both SSH key authentication and password authentication.
  - Automatic log redaction scrubs secrets, tokens, and private keys.
  - Strict SSH Host Key verification protects against Man-In-The-Middle attacks.
- **Atomic Transfers & Verification**:
  - Incomplete downloads use temporary `.part` files and are renamed only after completion.
  - SHA-256 cryptographic checksums and size verification.
  - Produces an immutable `backup_manifest.json` in each site backup directory.
- **Safe Retention & Cleanup (Disabled by Default)**:
  - Destructive cleanup is strictly opt-in and disabled by default.
  - Configurable retention by number of days or number of backup sets.
  - "Preview Cleanup (Dry Run)" button allows users to see proposed deletions before enabling.
  - Safety constraints prevent deleting non-date folders or active backup sets.
- **Crash Recovery & History**:
  - Persistent SQLite history database tracks past backup runs and artifact metrics.
  - Gracefully recovers from unexpected power loss or crashes, marking incomplete jobs as `interrupted`.
  - Rotating diagnostic logs stored in per-user application data directory.

---

## 🗂️ Backup Folder Structure

Downloaded backups are organized deterministically on the destination drive:

```
[SSD_FOLDER]\
  └── YYYY-MM-DD\
      └── SITE_NAME\
          ├── 20261014_114000-site_name-database.sql.gz
          ├── 20261014_114000-site_name-files.tar
          ├── 20261014_114000-site_name-private-files.tar
          ├── 20261014_114000-site_name-site_config_backup.json
          └── backup_manifest.json
```

If multiple backup runs take place on the same day, earlier backups are preserved without overwrite by creating timestamped run folders (e.g. `SITE_NAME_143000`).

---

## 🚀 Quickstart for Office Staff (Running the Application)

1. Connect your external backup SSD to the Windows computer.
2. Double-click **`LocalBackupSystem.exe`**.
3. **First-Time Setup (Settings Tab)**:
   - Click the **Settings** tab.
   - In **Section A**, select your external SSD from the detected drive table or click **Browse Folder...** (e.g. `E:\FrappeBackups`).
   - In **Section B**, click **Add Server...** and enter your Frappe server connection details:
     - **Server IP / Host**: e.g., `192.168.1.100` or `erp.mycompany.com`
     - **SSH Username**: e.g., `frappe`
     - **Password / Key**: Enter SSH credentials (stored securely in Windows Credential Manager).
     - **Frappe Site Name**: e.g., `riya-associate.com`
     - **Bench Directory**: e.g., `/home/frappe/frappe-bench`
   - Click **Test Connection** to ensure the server is reachable and the site exists.
   - Click **Save Server**, then click **Save Settings**.
4. **Running Backups (Home Tab)**:
   - Switch to the **Home** tab.
   - Verify that your SSD shows as **Online** with adequate free space.
   - Click the prominent **▶ Start Backup Now** button.
   - Monitor real-time progress and speeds.
   - Upon completion, an execution summary panel will display total files downloaded, data transferred, and verification results.

---

## 🛠️ Building from Source

### Prerequisites

- Python 3.10 or newer (Python 3.12 recommended)
- Git

### Setup Instructions

1. **Clone the repository:**
   ```bash
   git clone git@github.com:priyaa797/local_backup_system.git
   cd local_backup_system
   ```

2. **Create and activate a virtual environment:**
   ```bash
   # On Windows:
   python -m venv .venv
   .venv\Scripts\activate

   # On Linux / macOS:
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install dependencies:**
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

4. **Run automated test suite:**
   ```bash
   pytest
   ```

5. **Run the application locally:**
   ```bash
   python -m src.main
   ```

---

## 📦 Packaging the Windows Executable (`.exe`)

A dedicated build script `build_windows.bat` is included for repeatable Windows builds:

```cmd
build_windows.bat
```

The script will:
1. Verify the Python environment.
2. Install packaging dependencies.
3. Run the automated test suite to verify code integrity.
4. Execute PyInstaller using `local_backup_system.spec`.
5. Output the standalone binary to `dist\LocalBackupSystem.exe`.

---

## 🔒 Security Architecture

1. **No Plain-Text Secrets**: Passwords and private key passphrases are never saved to plain-text files or SQLite. They are stored directly in the operating system's credential store (Windows Credential Manager via `keyring`).
2. **Automatic Secret Redaction**: All console and file logging passes through `SecretRedactionFilter`, which sanitizes passwords, private keys, authorization headers, and tokens.
3. **SSH Host Key Policy**: Host keys are stored and verified against an isolated `known_hosts` file inside the user application directory (`%LOCALAPPDATA%\LocalBackupSystem\known_hosts`). Unknown keys prompt verification rather than blindly accepting connections.
4. **Shell Injection Prevention**: Frappe site names and remote paths are strictly validated using character whitelists before constructing SSH execution commands.
5. **No Administrative Privileges Needed**: The application operates entirely in user-space, storing application state in `%LOCALAPPDATA%\LocalBackupSystem` without requiring elevation.

---

## 🧪 Testing and Verification

Run the test suite with:

```bash
pytest -v
```

The automated test suite includes:
- **`test_settings_and_models.py`**: Model validation, SQLite CRUD, and crash recovery.
- **`test_credentials_security.py`**: Windows Credential Manager integration and secret redaction.
- **`test_disk_service.py`**: Drive detection, space calculation, and destination accessibility canary checks.
- **`test_artifact_discovery.py`**: Parsing Bench backup output and identifying database and file archives.
- **`test_transfer_and_integrity.py`**: SFTP partial download handling, size mismatch detection, and SHA-256 manifest verification.
- **`test_cleanup_service.py`**: Retention policy calculations, safety exclusions, and root folder containment checks.
- **`test_backup_coordinator_mocked.py`**: End-to-end multi-site backup pipeline simulation.
- **`test_ui_components.py`**: Headless UI initialization and rendering of all dialogs and tabs.

---

## 📋 Non-Production End-to-End Test Procedure

Before deploying to office computers:
1. Set up a test server running Frappe / Bench (or a staging site like `test.local`).
2. In **Settings**, add the staging server and test connection.
3. Set destination folder to a USB test thumb drive or test folder.
4. Click **Start Backup Now**.
5. Verify in the test destination folder:
   - Database archive (`*.sql.gz`) is present and non-zero size.
   - Public and private file archives are present (if applicable).
   - `backup_manifest.json` contains valid SHA-256 hashes for all files.
6. Verify in the application summary that all stages show as **✓ Done** and status is **SUCCESS**.

---

## 📄 License

This software is developed for internal backup automation and distributed under the MIT License.
