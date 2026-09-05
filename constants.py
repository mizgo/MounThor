#!/usr/bin/env python3

"""Application constants and paths for MounThor."""

import os
from pathlib import Path

# ============================================================================
# Application identity
# ============================================================================

APP_ID = "io.github.mizgo.MounThor"
APP_NAME = "MounThor"
APP_VERSION = "0.9.0"
APP_RELEASE_DATE = "28 August 2026"
APP_AUTHOR = "mizgo"

# ============================================================================
# Configuration and state paths
# ============================================================================

CONFIG_DIR = (
    Path(
        os.environ.get(
            "XDG_CONFIG_HOME",
            str(Path.home() / ".config"),
        )
    )
    / "mounthor"
)

CONFIG_FILE = CONFIG_DIR / "mounts.json"

STATE_DIR = (
    Path(
        os.environ.get(
            "XDG_STATE_HOME",
            str(Path.home() / ".local" / "state"),
        )
    )
    / "mounthor"
)

LOG_FILE = STATE_DIR / "mounthor.log"

# ============================================================================
# Privilege escalation
# ============================================================================

# False = pkexec
# True  = sudo -n
USE_SUDO = False

# ============================================================================
# System automount paths
# ============================================================================

HELPER_NAME = "mounthor-mount-helper"
HELPER_BIN = Path.home() / ".local" / "bin" / HELPER_NAME

POLKIT_RULES_DIR = Path("/etc/polkit-1/rules.d")
POLKIT_RULE_FILE = POLKIT_RULES_DIR / "60-mounthor.rules"

USER_SYSTEMD_DIR = (
    Path(
        os.environ.get(
            "XDG_CONFIG_HOME",
            str(Path.home() / ".config"),
        )
    )
    / "systemd"
    / "user"
)

AUTOMOUNT_SERVICE_NAME = "mounthor-automount.service"
AUTOMOUNT_SERVICE_FILE = USER_SYSTEMD_DIR / AUTOMOUNT_SERVICE_NAME

MOUNTHOR_BIN = Path.home() / ".local" / "bin" / "mounthor"

# ============================================================================
# GTK styling constants
# ============================================================================

LIST_PADDING_TOP = 8
LIST_PADDING_BOTTOM = 0
LIST_PADDING_LEFT = 32
LIST_PADDING_RIGHT = 32

MOUNT_PADDING_TOP = 8
MOUNT_PADDING_BOTTOM = 8
MOUNT_PADDING_LEFT = 16
MOUNT_PADDING_RIGHT = 16

MOUNT_CARD_COLOR = 0.70
MOUNT_CARD_HOVER = 0.85

# ============================================================================
# Credential storage
# ============================================================================

CREDENTIAL_SERVICE = "MounThor"
