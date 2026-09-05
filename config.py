#!/usr/bin/env python3

"""Configuration management for MounThor."""

import json
import logging
import os
import secrets
from pathlib import Path

from .constants import CONFIG_DIR, CONFIG_FILE, STATE_DIR, LOG_FILE

LOGGER = logging.getLogger("mounthor")


# ============================================================================
# Logging setup
# ============================================================================

def configure_logging() -> None:
    """Configure file-based application logging."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o700)

    if not LOG_FILE.exists():
        fd = os.open(LOG_FILE, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        os.close(fd)
    else:
        os.chmod(LOG_FILE, 0o600)

    logging.basicConfig(
        filename=str(LOG_FILE),
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


# ============================================================================
# Configuration I/O
# ============================================================================

def load_config() -> dict:
    """Load the mounts configuration from disk."""
    try:
        text = CONFIG_FILE.read_text(encoding="utf-8")
        cfg = json.loads(text)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {"mounts": []}

    if not isinstance(cfg, dict):
        return {"mounts": []}

    if not isinstance(cfg.get("mounts"), list):
        cfg["mounts"] = []

    # Migrate legacy entries without credential_storage field
    for mount in cfg["mounts"]:
        if not isinstance(mount, dict):
            continue
        if "credential_storage" not in mount:
            password = mount.get("password") or ""
            mount["credential_storage"] = "plaintext" if password else "none"

    return cfg


def save_config(cfg: dict) -> None:
    """Atomically save the mounts configuration to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    tmp_file = CONFIG_FILE.with_name("mounts.json.tmp")
    data = json.dumps(cfg, indent=2, ensure_ascii=False)
    tmp_file.write_text(data + "\n", encoding="utf-8")
    os.chmod(tmp_file, 0o600)
    os.replace(tmp_file, CONFIG_FILE)


# ============================================================================
# Entry normalization
# ============================================================================

def entry_from_data(data: dict) -> dict:
    """Normalize a mount entry dictionary with defaults."""
    host = (data.get("host") or "").strip()
    share = (data.get("share") or "").strip().lstrip("/")
    name = (data.get("name") or "").strip()

    if not name:
        name = f"{host}/{share}"

    password = data.get("password") or ""
    credential_storage = (
        data.get("credential_storage")
        or ("plaintext" if password else "none")
    )

    return {
        "id": secrets.token_hex(8),
        "name": name,
        "host": host,
        "share": share,
        "path": (data.get("path") or "").strip(),
        "username": (data.get("username") or "").strip(),
        "password": password,
        "credential_storage": credential_storage,
        "options": (data.get("options") or "").strip(),
        "automount": bool(data.get("automount", False)),
        "system_automount": bool(data.get("system_automount", False)),
    }


# ============================================================================
# Login helpers
# ============================================================================

def get_effective_username(entry: dict) -> str:
    """Return the effective username for SMB authentication."""
    return entry.get("username") or os.environ.get("USER") or "guest"
