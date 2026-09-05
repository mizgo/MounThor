#!/usr/bin/env python3

"""
MounThor
============

Simple GTK4/libadwaita CIFS mount manager.

Configuration:
    ~/.config/mounthor/mounts.json
"""

import json
import logging
import os
import secrets
import secretstorage
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

LOGGER = logging.getLogger(
    "mounthor"
)


# ============================================================================
# Application constants and paths
# ============================================================================

APP_ID = "io.github.mizgo.MounThor"

APP_NAME = "MounThor"
APP_VERSION = "0.9.0"
APP_RELEASE_DATE = "28 August 2026"
APP_AUTHOR = "mizgo"

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


# False = pkexec
# True  = sudo -n
USE_SUDO = False


# ============================================================================
# System automount (login-time mounting) paths
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
# Logging
# ============================================================================

def _configure_logging() -> None:

    STATE_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    os.chmod(
        STATE_DIR,
        0o700,
    )

    if not LOG_FILE.exists():

        fd = os.open(
            LOG_FILE,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_APPEND,
            0o600,
        )

        os.close(
            fd
        )

    else:

        os.chmod(
            LOG_FILE,
            0o600,
        )

    logging.basicConfig(
        filename=str(
            LOG_FILE
        ),
        level=logging.INFO,
        format=(
            "%(asctime)s "
            "%(levelname)s "
            "%(message)s"
        ),
    )


# ============================================================================
# Credential storage
# ============================================================================

CREDENTIAL_SERVICE = "MounThor"


def _secret_service_connection():
    return secretstorage.dbus_init()


def _secret_service_collection(
    connection,
):

    collection = (
        secretstorage.get_default_collection(
            connection
        )
    )

    if collection.is_locked():

        collection.unlock()

    return collection


def _secret_service_attributes(
    host: str,
    share: str,
    username: str,
) -> dict:

    return {
        "service": CREDENTIAL_SERVICE,
        "host": host,
        "share": share,
        "username": username,
    }


def _secure_storage_available() -> bool:

    try:

        connection = _secret_service_connection()

        _secret_service_collection(
            connection
        )

        return True

    except Exception:

        return False

def _secure_store_password(
    host: str,
    share: str,
    username: str,
    password: str,
) -> None:

    connection = _secret_service_connection()

    collection = _secret_service_collection(
        connection
    )

    attributes = _secret_service_attributes(
        host,
        share,
        username,
    )

    label = (
        f"MounThor SMB password for "
        f"//{host}/{share}"
    )

    collection.create_item(
        label,
        attributes,
        password.encode(
            "utf-8"
        ),
        replace=True,
    )

def _secure_load_password(
    host: str,
    share: str,
    username: str,
) -> str | None:

    connection = _secret_service_connection()

    collection = _secret_service_collection(
        connection
    )

    attributes = _secret_service_attributes(
        host,
        share,
        username,
    )

    items = collection.search_items(
        attributes
    )

    for item in items:

        if item.is_locked():

            item.unlock()

        return item.get_secret().decode(
            "utf-8"
        )

    return None

def _secure_delete_password(
    host: str,
    share: str,
    username: str,
) -> None:

    connection = _secret_service_connection()

    collection = _secret_service_collection(
        connection
    )

    attributes = _secret_service_attributes(
        host,
        share,
        username,
    )

    items = collection.search_items(
        attributes
    )

    for item in items:

        item.delete()


# ============================================================================
# Configuration management
# ============================================================================

def load_config() -> dict:

    try:

        text = CONFIG_FILE.read_text(
            encoding="utf-8"
        )

        cfg = json.loads(
            text
        )

    except (
        FileNotFoundError,
        json.JSONDecodeError,
        OSError,
    ):

        return {
            "mounts": []
        }

    if not isinstance(
        cfg,
        dict,
    ):

        return {
            "mounts": []
        }

    if not isinstance(
        cfg.get("mounts"),
        list,
    ):

        cfg["mounts"] = []

    for mount in cfg["mounts"]:

        if not isinstance(
            mount,
            dict,
        ):

            continue

        if "credential_storage" not in mount:

            password = (
                mount.get(
                    "password"
                )
                or ""
            )

            mount["credential_storage"] = (
                "plaintext"
                if password
                else "none"
            )

    return cfg


def save_config(
    cfg: dict,
) -> None:

    CONFIG_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    tmp_file = CONFIG_FILE.with_name(
        "mounts.json.tmp"
    )

    data = json.dumps(
        cfg,
        indent=2,
        ensure_ascii=False,
    )

    tmp_file.write_text(
        data + "\n",
        encoding="utf-8",
    )

    os.chmod(
        tmp_file,
        0o600,
    )

    os.replace(
        tmp_file,
        CONFIG_FILE,
    )


def entry_from_data(
    data: dict,
) -> dict:

    host = (
        data.get("host")
        or ""
    ).strip()

    share = (
        data.get("share")
        or ""
    ).strip().lstrip("/")

    name = (
        data.get("name")
        or ""
    ).strip()

    if not name:

        name = f"{host}/{share}"

    password = (
        data.get(
            "password"
        )
        or ""
    )

    credential_storage = (
        data.get(
            "credential_storage"
        )
        or (
            "plaintext"
            if password
            else "none"
        )
    )

    return {
        "id": secrets.token_hex(8),
        "name": name,
        "host": host,
        "share": share,
        "path": (
            data.get("path")
            or ""
        ).strip(),
        "username": (
            data.get("username")
            or ""
        ).strip(),
        "password": password,
        "credential_storage": credential_storage,
        "options": (
            data.get("options")
            or ""
        ).strip(),
        "automount": bool(
            data.get(
                "automount",
                False,
            )
        ),
        "system_automount": bool(
            data.get(
                "system_automount",
                False,
            )
        ),
    }


# ============================================================================
# Login helpers
# ============================================================================

def _get_effective_username(
    entry,
) -> str:

    return (
        entry.get("username")
        or os.environ.get("USER")
        or "guest"
    )


# ============================================================================
# Mount helpers
# ============================================================================

def _unescape_mount_field(
    value: str,
) -> str:

    return (
        value
        .replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\134", "\\")
    )


def is_mounted(
    path: str,
    host: str | None = None,
    share: str | None = None,
) -> bool:

    if not path:

        return False

    target = os.path.realpath(
        os.path.expanduser(
            path
        )
    )

    # Normalize host/share for exact matching (lowercase, strip leading slashes)
    norm_host = ""
    norm_share = ""

    if host is not None and share is not None:

        norm_host = (
            host.strip().lower()
        )

        norm_share = (
            share.strip().lstrip("/").lower()
        )

        if not norm_host or not norm_share:

            return False

    try:

        with open(
            "/proc/self/mounts",
            "r",
            encoding="utf-8",
        ) as mounts:

            for line in mounts:

                parts = line.split(
                    None,
                    3,
                )

                if len(parts) < 2:

                    continue

                device = parts[0].strip()
                mounted_path = (
                    _unescape_mount_field(
                        parts[1]
                    )
                )

                #Check Filesystem type
                filesystem_type = (
                    parts[2]
                    if len(parts) >= 3
                    else ""
                )

                if host is not None and share is not None:

                    expected_device = (
                        f"//{norm_host}/{norm_share}"
                    )

                    normalized_device = (
                        device.lower()
                    )

                    if (
                        filesystem_type.lower()
                        == "cifs"
                        and normalized_device
                        == expected_device
                        and os.path.realpath(
                            mounted_path
                        )
                        == target
                    ):

                        return True

                    continue

                # Fallback / original behavior: match by mount point only.
                mounted_path = os.path.realpath(
                    mounted_path
                )

                if mounted_path == target:

                    return True

    except OSError:

        pass

    return False

def _get_topmost_mount_source(
    path: str,
) -> str | None:

    if not path:

        return None

    target = os.path.realpath(
        os.path.expanduser(
            path
        )
    )

    mounts = []

    try:

        with open(
            "/proc/self/mountinfo",
            "r",
            encoding="utf-8",
        ) as mountinfo:

            for line in mountinfo:

                fields = line.split(
                    " - ",
                    1,
                )

                if len(fields) != 2:

                    continue

                pre_separator = fields[0].split()
                post_separator = fields[1].split()

                if len(pre_separator) < 5:

                    continue

                if len(post_separator) < 2:

                    continue

                mount_id = int(
                    pre_separator[0]
                )

                parent_id = int(
                    pre_separator[1]
                )

                mounted_path = (
                    _unescape_mount_field(
                        pre_separator[4]
                    )
                )

                mounted_path = os.path.realpath(
                    mounted_path
                )

                if mounted_path != target:

                    continue

                filesystem_type = (
                    post_separator[0]
                )

                source = (
                    _unescape_mount_field(
                        post_separator[1]
                    )
                )

                mounts.append(
                    {
                        "id": mount_id,
                        "parent_id": parent_id,
                        "filesystem_type": filesystem_type,
                        "source": source,
                    }
                )

    except (
        OSError,
        ValueError,
    ):

        return None

    if not mounts:

        return None

    mount_ids = {
        mount["id"]
        for mount in mounts
    }

    child_mount_ids = {
        mount["parent_id"]
        for mount in mounts
        if mount["parent_id"] in mount_ids
    }

    topmost = [
        mount
        for mount in mounts
        if mount["id"] not in child_mount_ids
    ]

    if len(topmost) != 1:

        return None

    return topmost[0]["source"]


def get_mount_source(
    path: str,
) -> str | None:

    """Return the SMB source (e.g. '//host/share') mounted at *path*, or None."""

    return _get_topmost_mount_source(
        path
    )


def _auth(
    command: list[str],
) -> list[str]:

    if USE_SUDO:

        return [
            "sudo",
            "-n",
            *command,
        ]

    return [
        "pkexec",
        *command,
    ]


def _runtime_directory() -> str:

    runtime_dir = os.environ.get(
        "XDG_RUNTIME_DIR"
    )

    if (
        runtime_dir
        and os.path.isdir(
            runtime_dir
        )
    ):

        return runtime_dir

    return "/tmp"


def _cleanup_stale_credentials() -> int:

    runtime_dir = _runtime_directory()

    removed = 0

    try:

        runtime_path = Path(
            runtime_dir
        )

        for cred_file in runtime_path.glob(
            "cifs-creds-*"
        ):

            if not cred_file.is_file():

                continue

            try:

                cred_file.unlink()

                removed += 1

                LOGGER.info(
                    "Removed stale temporary credential file."
                )

            except OSError as exc:

                LOGGER.warning(
                    "Could not remove stale temporary "
                    "credential file: %s",
                    exc,
                )

    except OSError as exc:

        LOGGER.warning(
            "Could not inspect runtime directory "
            "for stale credential files: %s",
            exc,
        )

    return removed


def _clean_cifs_options(
    value: str,
) -> list[str]:

    value = (
        value
        or ""
    ).strip()

    if not value:

        return []

    if value.startswith(
        "-o"
    ):

        value = value[2:].lstrip()

        if value.startswith(
            "="
        ):

            value = value[1:].lstrip()

    result = []

    for item in value.split(
        ","
    ):

        item = item.strip()

        if item:

            result.append(
                item
            )

    return result


# ============================================================================
# System automount support (login-time mounting)
# ============================================================================

def _install_helper() -> bool:

    """Copy the mount helper to ~/.local/bin and make it executable."""

    source = (
        Path(__file__).resolve().parent / "scripts" / HELPER_NAME
    )

    if not source.is_file():

        LOGGER.error(
            f"Mount helper not found at {source}"
        )

        return False

    try:

        HELPER_BIN.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        shutil.copyfile(
            source,
            HELPER_BIN
        )

        os.chmod(
            HELPER_BIN,
            0o755,
        )

    except OSError as exc:

        LOGGER.error(
            f"Failed to install mount helper: {exc}"
        )

        return False

    LOGGER.info(
        f"Mount helper installed at {HELPER_BIN}"
    )

    return True


def _polkit_rule_content() -> str:

    username = os.environ.get(
        "USER",
        "unknown",
    )

    program = str(
        HELPER_BIN
    )

    return (
        "// MounThor system automount rule.\n"
        f"// Generated by MounThor for user '{username}'.\n"
        "polkit.addRule(function(action, subject) {\n"
        "    if (\n"
        '        action.id == "org.freedesktop.policykit.exec"\n'
        f'        && action.lookup("program") == "{program}"\n'
        f'        && subject.user == "{username}"\n'
        "    ) {\n"
        "        return polkit.Result.YES;\n"
        "    }\n"
        "});\n"
    )


def _ensure_polkit_rule() -> bool:

    """Install the per-user polkit rule (one-time pkexec prompt)."""

    if POLKIT_RULE_FILE.is_file():

        return True

    runtime_dir = _runtime_directory()

    tmp_file = Path(
        runtime_dir
    ) / f".{HELPER_NAME}-rule.tmp"

    try:

        tmp_file.write_text(
            _polkit_rule_content(),
            encoding="utf-8",
        )

        os.chmod(
            tmp_file,
            0o600,
        )

    except OSError as exc:

        LOGGER.error(
            f"Failed to prepare polkit rule file: {exc}"
        )

        return False

    try:

        command = _auth([
            "install",
            "-m",
            "0644",
            str(tmp_file),
            str(POLKIT_RULE_FILE),
        ])

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
        )

    except (subprocess.SubprocessError, OSError) as exc:

        LOGGER.error(
            f"Polkit rule installation failed: {exc}"
        )

        return False

    finally:

        try:

            tmp_file.unlink()

        except OSError:

            pass

    if result.returncode != 0:

        LOGGER.error(
            f"Polkit rule installation exited "
            f"{result.returncode}: {result.stderr.strip()}"
        )

        return False

    LOGGER.info(
        f"Polkit rule installed at {POLKIT_RULE_FILE}"
    )

    return True


def _automount_service_content() -> str:

    return (
        "[Unit]\n"
        "Description=MounThor - automount CIFS shares at login\n"
        "After=default.target\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"ExecStart={MOUNTHOR_BIN} --autostart\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def _ensure_automount_service() -> bool:

    """Create and enable the user systemd service."""

    try:

        USER_SYSTEMD_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        AUTOMOUNT_SERVICE_FILE.write_text(
            _automount_service_content(),
            encoding="utf-8",
        )

    except OSError as exc:

        LOGGER.error(
            f"Failed to write systemd unit: {exc}"
        )

        return False

    try:

        subprocess.run(
            [
                "systemctl",
                "--user",
                "daemon-reload",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )

        subprocess.run(
            [
                "systemctl",
                "--user",
                "enable",
                AUTOMOUNT_SERVICE_NAME,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        )

    except (subprocess.SubprocessError, OSError) as exc:

        LOGGER.error(
            f"Failed to enable systemd unit: {exc}"
        )

        return False

    LOGGER.info(
        f"Systemd user service {AUTOMOUNT_SERVICE_NAME} enabled"
    )

    return True


def _disable_automount_service() -> None:

    try:

        subprocess.run(
            [
                "systemctl",
                "--user",
                "disable",
                AUTOMOUNT_SERVICE_NAME,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

    except (subprocess.SubprocessError, OSError) as exc:

        LOGGER.warning(
            f"Failed to disable systemd unit: {exc}"
        )


def _ensure_system_automount_ready() -> tuple[bool, str]:

    """Set up helper + polkit rule + service. Returns (ok, message)."""

    if not _install_helper():

        return False, "Failed to install the mount helper."

    if not _ensure_polkit_rule():

        return False, "Polkit authorization was not granted."

    if not _ensure_automount_service():

        _disable_automount_service()

        return (
            False,
            "Failed to enable the systemd service.",
        )

    return (
        True,
        "System automount is ready. Shares will be mounted at login.",
    )


def mount_entry_privileged(
    entry: dict,
    password: str,
) -> tuple[bool, str]:

    """Mount *entry* via the privileged helper (no sudo/pkexec wrapper)."""

    payload = {
        "op": "mount",
        "entry": {
            "host": entry.get("host"),
            "share": entry.get("share"),
            "path": entry.get("path"),
            "username": entry.get("username"),
            "options": entry.get("options") or [],
        },
        "password": password,
        "uid": os.getuid(),
        "gid": os.getgid(),
    }

    try:

        result = subprocess.run(
            _auth([str(HELPER_BIN)]),
            input=json.dumps(
                payload
            ),
            capture_output=True,
            text=True,
            timeout=120,
        )

    except (subprocess.SubprocessError, OSError) as exc:

        return False, f"Helper invocation failed: {exc}"

    try:

        data = json.loads(
            result.stdout
        )

    except (json.JSONDecodeError, ValueError):

        message = (
            result.stderr.strip()
            or f"Helper exited with code {result.returncode}."
        )

        return False, message

    if data.get("ok"):

        return True, data.get(
            "message",
            "Mounted.",
        )

    return False, data.get(
        "message",
        "Mount failed.",
    )


# ============================================================================
# Mount operation
# ============================================================================

def do_mount(
    entry: dict,
    password: str,
    authenticate: bool = True,
    uid: int | None = None,
    gid: int | None = None,
):

    host = (
        entry.get("host")
        or ""
    ).strip()

    share = (
        entry.get("share")
        or ""
    ).strip().lstrip("/")

    path = (
        entry.get("path")
        or ""
    ).strip()

    if not host:

        return False, "host is required"

    if not share:

        return False, "share name is required"

    if not path:

        return False, "mount path is required"

    mountpoint = os.path.expanduser(
        path
    )

    try:

        os.makedirs(
            mountpoint,
            exist_ok=True,
        )

    except OSError as exc:

        return (
            False,
            f"cannot create mount point: {exc}",
        )

    cred_dir = _runtime_directory()

    cred_file = os.path.join(
        cred_dir,
        f"cifs-creds-{secrets.token_hex(16)}",
    )

    username = _get_effective_username(
        entry
    )

    if uid is None:

        uid = os.getuid()

    if gid is None:

        gid = os.getgid()

    try:

        fd = os.open(
            cred_file,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL,
            0o600,
        )

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as credentials:

            credentials.write(
                f"username={username}\n"
            )

            credentials.write(
                f"password={password}\n"
            )

        options = [
            f"credentials={cred_file}",
            f"uid={uid}",
            f"gid={gid}",
            "file_mode=0644",
            "dir_mode=0755",
        ]

        options.extend(
            _clean_cifs_options(
                entry.get(
                    "options",
                    "",
                )
            )
        )

        command = [
            "/usr/bin/mount",
            "-t",
            "cifs",
            f"//{host}/{share}",
            mountpoint,
            "-o",
            ",".join(
                options
            ),
        ]

        if authenticate:

            command = _auth(
                command
            )

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=90,
        )

        if result.returncode != 0:

            message = (
                result.stderr
                or result.stdout
                or "mount failed"
            ).strip()

            return False, message

        return True, "mounted"

    except FileExistsError:

        return (
            False,
            "temporary credentials file already exists",
        )

    except PermissionError as exc:

        return (
            False,
            f"cannot create credentials file: {exc}",
        )

    except subprocess.TimeoutExpired:

        return (
            False,
            "mount timed out",
        )

    except OSError as exc:

        return (
            False,
            f"mount error: {exc}",
        )

    finally:

        try:

            os.unlink(
                cred_file
            )

        except OSError:

            pass


def do_unmount(
    entry: dict,
    authenticate: bool = True,
):

    path = (
        entry.get("path")
        or ""
    ).strip()

    if not path:

        return False, "mount path is empty"

    mountpoint = os.path.expanduser(
        path
    )

    expected_source = (
        f"//{entry.get('host', '').strip()}/"
        f"{entry.get('share', '').strip().lstrip('/')}"
    )

    topmost_source = _get_topmost_mount_source(
        mountpoint
    )

    if (
        topmost_source is not None
        and topmost_source.lower()
        != expected_source.lower()
    ):

        return (
            False,
            (
                "another mount is covering "
                "this share's mount point"
            ),
        )

    command = [
        "/usr/bin/umount",
        mountpoint,
    ]

    if authenticate:

        command = _auth(
            command
        )

    try:

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
        )

        if result.returncode != 0:

            message = (
                result.stderr
                or result.stdout
                or "umount failed"
            ).strip()

            return False, message

        return True, "unmounted"

    except subprocess.TimeoutExpired:

        return False, "unmount timed out"

    except OSError as exc:

        return (
            False,
            f"unmount error: {exc}",
        )


# ============================================================================
# Privileged batch helpers
# ============================================================================

def _run_privileged_batch(
    mode: str,
    items: list[dict],
):

    payload = {
        "mode": mode,
        "items": items,
    }

    script = str(
        Path(__file__).resolve()
    )

    command = _auth(
        [
            sys.executable,
            script,
            f"--{mode}",
        ]
    )

    timeout = max(
        120,
        (90 * len(items)) + 30,
    )

    try:

        result = subprocess.run(
            command,
            input=json.dumps(
                payload,
                ensure_ascii=False,
            ),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    except subprocess.TimeoutExpired:

        return [
            {
                "id": item.get("id"),
                "ok": False,
                "message": "batch operation timed out",
            }
            for item in items
        ]

    except OSError as exc:

        return [
            {
                "id": item.get("id"),
                "ok": False,
                "message": str(exc),
            }
            for item in items
        ]

    if result.returncode != 0:

        message = (
            result.stderr
            or result.stdout
            or "privileged operation failed"
        ).strip()

        return [
            {
                "id": item.get("id"),
                "ok": False,
                "message": message,
            }
            for item in items
        ]

    try:

        response = json.loads(
            result.stdout
        )

        if isinstance(
            response,
            list,
        ):

            return response

    except json.JSONDecodeError:

        pass

    message = (
        result.stderr
        or result.stdout
        or "invalid batch response"
    ).strip()

    return [
        {
            "id": item.get("id"),
            "ok": False,
            "message": message,
        }
        for item in items
    ]


def _privileged_batch_main(
    mode: str,
) -> int:

    try:

        payload = json.load(
            sys.stdin
        )

    except (
        json.JSONDecodeError,
        OSError,
    ):

        print(
            json.dumps(
                [],
                ensure_ascii=False,
            )
        )

        return 1

    if not isinstance(
        payload,
        dict,
    ):

        print(
            json.dumps(
                [],
                ensure_ascii=False,
            )
        )

        return 1

    items = payload.get(
        "items",
        [],
    )

    if not isinstance(
        items,
        list,
    ):

        items = []

    results = []

    for item in items:

        if not isinstance(
            item,
            dict,
        ):

            continue

        entry = item.get(
            "entry",
            {},
        )

        if not isinstance(
            entry,
            dict,
        ):

            entry = {}

        item_id = item.get(
            "id"
        )

        if mode == "batch-mount":

            password = (
                item.get(
                    "password",
                    "",
                )
                or ""
            )

            uid = item.get(
                "uid"
            )

            gid = item.get(
                "gid"
            )

            try:

                uid = int(uid)

            except (
                TypeError,
                ValueError,
            ):

                uid = 0

            try:

                gid = int(gid)

            except (
                TypeError,
                ValueError,
            ):

                gid = 0

            ok, message = do_mount(
                entry,
                password,
                authenticate=False,
                uid=uid,
                gid=gid,
            )

        elif mode == "batch-unmount":

            ok, message = do_unmount(
                entry,
                authenticate=False,
            )

        else:

            ok = False
            message = "unknown batch operation"

        results.append(
            {
                "id": item_id,
                "ok": ok,
                "message": message,
            }
        )

    print(
        json.dumps(
            results,
            ensure_ascii=False,
        )
    )

    return 0


# ============================================================================
# GTK helpers
# ============================================================================

def get_text(
    widget,
) -> str:

    return widget.get_text()


def install_enter_action(
    dialog: Adw.Dialog,
    accept_button: Gtk.Button,
):
    """
    Make Enter activate the dialog's primary action.

    Uses Gtk.EventControllerKey instead of GTK3-era default-widget
    APIs, keeping compatibility with GTK 4.23.x.
    """

    controller = Gtk.EventControllerKey()

    controller.set_propagation_phase(
        Gtk.PropagationPhase.CAPTURE
    )

    def on_key_pressed(
        _controller,
        keyval,
        _keycode,
        _state,
    ):

        if keyval in (
            Gdk.KEY_Return,
            Gdk.KEY_KP_Enter,
        ):

            if accept_button.get_sensitive():

                accept_button.emit(
                    "clicked"
                )

                return True

        return False

    controller.connect(
        "key-pressed",
        on_key_pressed,
    )

    dialog.add_controller(
        controller
    )


def make_action_bar(
    cancel_label: str,
    accept_label: str,
    accept_css_class: str = "suggested-action",
):

    action_bar = Gtk.ActionBar()

    cancel_button = (
        Gtk.Button.new_with_label(
            cancel_label
        )
    )

    accept_button = (
        Gtk.Button.new_with_label(
            accept_label
        )
    )

    if accept_css_class:

        accept_button.add_css_class(
            accept_css_class
        )

    action_bar.pack_start(
        cancel_button
    )

    action_bar.pack_end(
        accept_button
    )

    return (
        action_bar,
        cancel_button,
        accept_button,
    )


def make_dialog_view(
    content,
    action_bar,
):

    toolbar_view = Adw.ToolbarView()

    toolbar_view.set_content(
        content
    )

    toolbar_view.add_bottom_bar(
        action_bar
    )

    return toolbar_view


def make_entry_listbox():

    listbox = Gtk.ListBox()

    listbox.set_selection_mode(
        Gtk.SelectionMode.NONE
    )

    listbox.add_css_class(
        "boxed-list"
    )

    return listbox


def make_mount_listbox():

    listbox = Gtk.ListBox()

    # Selection is managed explicitly by MountRow instead of Gtk.ListBox.
    # This keeps clicks on child controls (buttons/switch) out of the
    # selection mechanism entirely.
    listbox.set_selection_mode(
        Gtk.SelectionMode.NONE
    )

    return listbox


def install_mount_list_css():

    provider = Gtk.CssProvider()

    CSS = f"""
        .smb-mount-list {{
            background: transparent;
        }}

        .smb-mount-list > row {{
            background: transparent;
            padding-top: {LIST_PADDING_TOP}px;
            padding-bottom: {LIST_PADDING_BOTTOM}px;
            padding-left: {LIST_PADDING_LEFT}px;
            padding-right: {LIST_PADDING_RIGHT}px;
        }}

        .smb-mount-list > row > * {{
            background: alpha(@card_bg_color, {MOUNT_CARD_COLOR});
            border-radius: 12px;

            padding-top: {MOUNT_PADDING_TOP}px;
            padding-bottom: {MOUNT_PADDING_BOTTOM}px;
            padding-left: {MOUNT_PADDING_LEFT}px;
            padding-right: {MOUNT_PADDING_RIGHT}px;
        }}

        .smb-mount-list > row:hover > * {{
            background: alpha(@card_bg_color, {MOUNT_CARD_HOVER});
        }}

        .smb-mount-list > row.smb-selected > * {{
            background: alpha(@accent_bg_color, 0.32);
            box-shadow: none;
            outline: none;
        }}

        .smb-mount-list > row.smb-selected:hover > * {{
            background: alpha(@accent_bg_color, 0.40);
            box-shadow: none;
            outline: none;
        }}
        """.encode()

    provider.load_from_data(CSS)

    display = Gdk.Display.get_default()

    if display is not None:

        Gtk.StyleContext.add_provider_for_display(
            display,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )


# ============================================================================
# Mount row
# ============================================================================

class MountRow(
    Adw.ActionRow,
):

    def __init__(
        self,
        app,
        entry: dict,
    ):

        super().__init__()

        self.app = app
        self.entry = entry

        self._updating = False
        self._busy = False
        self._selected = False

        self.set_activatable(False)
        self.set_selectable(True)

        self._click_controller = Gtk.GestureClick()
        self._click_controller.set_button(Gdk.BUTTON_PRIMARY)
        self._click_controller.set_propagation_phase(
            Gtk.PropagationPhase.CAPTURE
        )
        self._click_controller.connect(
            "pressed",
            self._on_primary_pressed,
        )
        self.add_controller(
            self._click_controller
        )

        self.set_title(
            entry.get(
                "name",
                "Unnamed share",
            )
        )

        self.set_subtitle(
            f"//{entry.get('host', '')}/"
            f"{entry.get('share', '')}"
            f"  →  "
            f"{entry.get('path', '')}"
        )

        self.icon = (
            Gtk.Image.new_from_icon_name(
                "drive-harddisk-symbolic"
            )
        )

        self.spinner = Gtk.Spinner()

        self.spinner.set_visible(
            False
        )

        prefix = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
        )

        prefix.append(
            self.icon
        )

        prefix.append(
            self.spinner
        )

        self.add_prefix(
            prefix
        )

        # --------------------------------------------------------------------
        # Duplicate
        # --------------------------------------------------------------------

        self.duplicate_button = (
            Gtk.Button.new_from_icon_name(
                "edit-copy-symbolic"
            )
        )

        self.duplicate_button.set_tooltip_text(
            "Duplicate share"
        )

        # --------------------------------------------------------------------
        # Edit
        # --------------------------------------------------------------------

        self.edit_button = (
            Gtk.Button.new_from_icon_name(
                "document-edit-symbolic"
            )
        )

        self.edit_button.set_tooltip_text(
            "Edit share"
        )

        # --------------------------------------------------------------------
        # Delete
        # --------------------------------------------------------------------

        self.delete_button = (
            Gtk.Button.new_from_icon_name(
                "user-trash-symbolic"
            )
        )

        self.delete_button.set_tooltip_text(
            "Remove share"
        )

        # --------------------------------------------------------------------
        # Switch
        # --------------------------------------------------------------------

        self.switch = Gtk.Switch()

        self.switch.set_valign(
            Gtk.Align.CENTER
        )

        self.switch.set_tooltip_text(
            "Mount or unmount share"
        )

        suffix = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            halign=Gtk.Align.END,
            valign=Gtk.Align.CENTER,
        )

        suffix.append(
            self.duplicate_button
        )

        suffix.append(
            self.edit_button
        )

        suffix.append(
            self.delete_button
        )

        suffix.append(
            self.switch
        )

        self.add_suffix(
            suffix
        )

        self.switch.connect(
            "notify::active",
            self._switch_changed,
        )

        self.duplicate_button.connect(
            "clicked",
            self._on_duplicate_clicked,
        )

        self.edit_button.connect(
            "clicked",
            self._on_edit_clicked,
        )

        self.delete_button.connect(
            "clicked",
            self._on_delete_clicked,
        )

    def _on_primary_pressed(
        self,
        _gesture,
        _n_press,
        x,
        y,
    ):

        # The selection gesture lives on the whole ActionRow, so explicitly
        # ignore presses that originated on one of the row's interactive
        # controls (or on one of their internal child widgets).  Those
        # controls must keep their normal GTK event handling.
        picked = self.pick(
            x,
            y,
            Gtk.PickFlags.DEFAULT,
        )

        widget = picked

        while widget is not None:

            # Never let the row-selection gesture consume events destined
            # for an interactive child.  This covers the actual controls
            # as well as their internal child widgets (icons, labels, etc.).
            if isinstance(widget, (Gtk.Button, Gtk.Switch)):

                _gesture.set_state(
                    Gtk.EventSequenceState.DENIED
                )

                return

            if widget is self:

                break

            widget = widget.get_parent()

        if self._busy:

            return

        self.app.select_mount_row(
            self
        )

        _gesture.set_state(
            Gtk.EventSequenceState.CLAIMED
        )

    def set_selected(
        self,
        selected: bool,
    ):

        self._selected = bool(selected)

        if self._selected:
            self.add_css_class("smb-selected")
        else:
            self.remove_css_class("smb-selected")

    def is_selected(
        self,
    ) -> bool:

        return self._selected

    def _on_duplicate_clicked(
        self,
        _button,
    ):

        self.app.duplicate_row(
            self
        )

    def _on_edit_clicked(
        self,
        _button,
    ):

        self.app.edit_row(
            self
        )

    def _on_delete_clicked(
        self,
        _button,
    ):

        self.app.delete_row(
            self
        )

    def _switch_changed(
        self,
        *_args,
    ):

        if self._updating:

            return

        if self._busy:

            return

        if self.is_selected():

            if self.switch.get_active():

                self.app.connect_selected()

            else:

                self.app.disconnect_selected()

            return

        self.app.toggle_mount(
            self,
            self.switch.get_active(),
        )

    def set_mounted(
        self,
        mounted: bool,
    ):

        self._updating = True

        try:

            self.switch.set_active(
                mounted
            )

        finally:

            self._updating = False

        self.icon.set_from_icon_name(
            "network-workgroup-symbolic"
            if mounted
            else "drive-harddisk-symbolic"
        )

    def set_busy(
        self,
        busy: bool,
    ):

        self._busy = busy

        self.spinner.set_visible(
            busy
        )

        self.spinner.set_spinning(
            busy
        )

        self.switch.set_sensitive(
            not busy
        )

        self.duplicate_button.set_sensitive(
            not busy
        )

        self.edit_button.set_sensitive(
            not busy
        )

        self.delete_button.set_sensitive(
            not busy
        )


# ============================================================================
# Main application
# ============================================================================

class MounThorApp(
    Adw.Application,
):

    def __init__(self):

        super().__init__(
            application_id=APP_ID
        )

        self.win = None
        self.overlay = None
        self.rows_list = None
        self.rows = {}
        self._selection_anchor_id = None

        self._batch_active = False

    def do_shutdown(
        self,
    ):

        LOGGER.info(
            "Application exiting."
        )

        Gio.Application.do_shutdown(
            self
        )


    # ------------------------------------------------------------------------
    # Activation
    # ------------------------------------------------------------------------

    def do_activate(
        self,
    ):

        cleaned_credentials = (
            _cleanup_stale_credentials()
        )

        if cleaned_credentials:

            LOGGER.info(
                "Cleaned up %d stale temporary "
                "credential file(s) at startup.",
                cleaned_credentials,
            )

        LOGGER.info(
            "Application started."
        )

        if self.win is not None:

            self.win.present()

            return

        self.win = Adw.ApplicationWindow(
            application=self
        )

        self.win.set_default_size(
            540,
            680,
        )

        self.win.set_title(
            APP_NAME
        )

        root = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL
        )

        self.win.set_content(
            root
        )

        # --------------------------------------------------------------------
        # Header
        # --------------------------------------------------------------------

        header = Adw.HeaderBar()

        # Main menu on the LEFT.
        menu = Gio.Menu()

        menu.append(
            "Connect All",
            "app.connect-all",
        )

        menu.append(
            "Disconnect All",
            "app.disconnect-all",
        )

        menu.append(
            "Clear Selection",
            "app.deselect-all",
        )

        menu.append(
            "About",
            "app.about",
        )

        menu_button = Gtk.MenuButton()

        menu_button.set_icon_name(
            "open-menu-symbolic"
        )

        menu_button.set_tooltip_text(
            "Main menu"
        )

        menu_button.set_menu_model(
            menu
        )

        header.pack_start(
            menu_button
        )

        title = Adw.WindowTitle(
            title=APP_NAME,
            subtitle="CIFS mount manager",
        )

        header.set_title_widget(
            title
        )

        # Add button
        add_button = (
            Gtk.Button.new_from_icon_name(
                "list-add-symbolic"
            )
        )

        add_button.set_tooltip_text(
            "Add SMB share"
        )

        add_button.connect(
            "clicked",
            self._on_add_clicked,
        )

        header.pack_end(
            add_button
        )

        root.append(
            header
        )

        # --------------------------------------------------------------------
        # Actions
        # --------------------------------------------------------------------

        connect_action = Gio.SimpleAction.new(
            "connect-all",
            None,
        )

        connect_action.connect(
            "activate",
            self._on_connect_all_action,
        )

        self.add_action(
            connect_action
        )

        disconnect_action = Gio.SimpleAction.new(
            "disconnect-all",
            None,
        )

        disconnect_action.connect(
            "activate",
            self._on_disconnect_all_action,
        )

        self.add_action(
            disconnect_action
        )

        deselect_all_action = Gio.SimpleAction.new(
            "deselect-all",
            None,
        )

        deselect_all_action.connect(
            "activate",
            self._on_deselect_all_action,
        )

        self.add_action(
            deselect_all_action
        )

        about_action = Gio.SimpleAction.new(
            "about",
            None,
        )

        about_action.connect(
            "activate",
            self._on_about_action,
        )

        self.add_action(
            about_action
        )

        # --------------------------------------------------------------------
        # Toast overlay
        # --------------------------------------------------------------------

        self.overlay = Adw.ToastOverlay()

        root.append(
            self.overlay
        )

        scroll = Gtk.ScrolledWindow(
            vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
        )

        self.rows_list = make_mount_listbox()

        self.rows_list.add_css_class(
            "smb-mount-list"
        )

        install_mount_list_css()

        scroll.set_child(
            self.rows_list
        )

        self.overlay.set_child(
            scroll
        )

        self.rebuild_rows()

        self.win.present()

        GLib.idle_add(
            self._automount_on_startup
        )

    def _on_add_clicked(
        self,
        *_args,
    ):

        self.entry_dialog(
            None
        )

    # =========================================================================
    # Toast
    # =========================================================================

    def toast(
        self,
        message: str,
        error: bool = False,
    ):

        if self.overlay is None:

            return

        toast = Adw.Toast()

        toast.set_title(
            message
        )

        toast.set_timeout(
            5
        )

        if error:

            toast.set_priority(
                Adw.ToastPriority.HIGH
            )

        self.overlay.add_toast(
            toast
        )

    # =========================================================================
    # Rows
    # =========================================================================

    def rebuild_rows(
        self,
    ):

        if self.rows_list is None:

            return

        child = (
            self.rows_list.get_first_child()
        )

        while child is not None:

            next_child = (
                child.get_next_sibling()
            )

            self.rows_list.remove(
                child
            )

            child = next_child

        selected_ids = {
            entry_id
            for entry_id, row in self.rows.items()
            if row.is_selected()
        }

        self.rows = {}

        cfg = load_config()

        mounts = cfg.get(
            "mounts",
            [],
        )

        valid_mounts = []

        for entry in mounts:

            if not isinstance(
                entry,
                dict,
            ):

                continue

            if not entry.get(
                "id"
            ):

                entry["id"] = secrets.token_hex(
                    8
                )

            valid_mounts.append(
                entry
            )

        if not valid_mounts:

            empty = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=12,
                halign=Gtk.Align.CENTER,
                valign=Gtk.Align.CENTER,
                margin_top=48,
                margin_bottom=48,
                margin_start=24,
                margin_end=24,
            )

            icon = (
                Gtk.Image.new_from_icon_name(
                    "folder-remote-symbolic"
                )
            )

            icon.set_pixel_size(
                48
            )

            label = Gtk.Label(
                label=(
                    "No SMB shares yet.\n"
                    "Click + to add one."
                ),
                justify=Gtk.Justification.CENTER,
                wrap=True,
            )

            empty.append(
                icon
            )

            empty.append(
                label
            )

            self.rows_list.append(
                empty
            )

            return

        for entry in valid_mounts:

            row = MountRow(
                self,
                entry,
            )

            self.rows[
                entry["id"]
            ] = row

            self.rows_list.append(
                row
            )

            if entry["id"] in selected_ids:

                row.set_selected(True)

            row.set_mounted(
                is_mounted(
                    entry.get(
                        "path",
                        "",
                    ),
                    entry.get("host"),
                    entry.get("share"),
                )
            )

    def select_mount_row(
        self,
        row: MountRow,
    ):

        state = row._click_controller.get_current_event_state()

        has_shift = bool(
            state & Gdk.ModifierType.SHIFT_MASK
        )

        row_id = row.entry.get(
            "id"
        )

        ordered_rows = list(
            self.rows.values()
        )

        if has_shift and self._selection_anchor_id:

            anchor_index = next(
                (
                    index
                    for index, candidate in enumerate(ordered_rows)
                    if candidate.entry.get("id")
                    == self._selection_anchor_id
                ),
                None,
            )

            if anchor_index is not None:

                row_index = ordered_rows.index(
                    row
                )

                start = min(
                    anchor_index,
                    row_index,
                )

                end = max(
                    anchor_index,
                    row_index,
                )

                for candidate in ordered_rows[start:end + 1]:

                    candidate.set_selected(True)

                return

        row.set_selected(
            not row.is_selected()
        )

        self._selection_anchor_id = row_id

    def selected_mount_rows(
        self,
    ) -> list[MountRow]:

        return [
            row
            for row in self.rows.values()
            if row.is_selected()
        ]

    def deselect_all(
        self,
    ):

        for row in self.rows.values():

            row.set_selected(False)

        self._selection_anchor_id = None

    def save_current_rows(
        self,
    ):

        cfg = {
            "mounts": [
                row.entry
                for row in self.rows.values()
            ]
        }

        save_config(
            cfg
        )

    def refresh_mount_states(
        self,
    ):

        for row in self.rows.values():

            row.set_mounted(
                is_mounted(
                    row.entry.get(
                        "path",
                        "",
                    ),
                    row.entry.get("host"),
                    row.entry.get("share"),
                )
            )

    # =========================================================================
    # Automount on application startup
    # =========================================================================

    def _automount_on_startup(
        self,
    ):

        if self._batch_active:

            return False

        rows = [
            row
            for row in self.rows.values()
            if (
                bool(
                    row.entry.get(
                        "automount",
                        False,
                    )
                )
                and not is_mounted(
                    row.entry.get(
                        "path",
                        "",
                    ),
                    row.entry.get("host"),
                    row.entry.get("share"),
                )
            )
        ]

        if not rows:

            return False

        self._batch_active = True

        for row in rows:

            row.set_busy(
                True
            )

        self._collect_batch_passwords(
            rows,
            0,
            {},
        )

        return False

    # =========================================================================
    # Mount / unmount
    # =========================================================================

    def toggle_mount(
        self,
        row: MountRow,
        mount: bool,
    ):

        if self._batch_active:

            row.set_mounted(
                is_mounted(
                    row.entry.get(
                        "path",
                        "",
                    ),
                    row.entry.get("host"),
                    row.entry.get("share"),
                )
            )

            return

        if mount:

            path = row.entry.get("path", "")
            host = row.entry.get("host", "")
            share = row.entry.get("share", "")

            # Check for conflict: path is mounted by a *different* share
            if (
                is_mounted(path)
                and not is_mounted(
                    path, host, share
                )
            ):

                existing_source = get_mount_source(
                    path
                )

                self._show_replace_dialog(
                    row,
                    existing_source or "",
                )

                return

            self._ask_password(
                row
            )

        else:

            self._unmount(
                row
            )

    def _show_replace_dialog(
        self,
        row: MountRow,
        existing_source: str,
        on_after_close=None,
    ):

        host = row.entry.get("host", "")
        share = row.entry.get("share", "")
        path = row.entry.get("path", "")

        dialog = Adw.AlertDialog()
        dialog.set_heading(
            "Mount path already in use"
        )
        dialog.set_body(
            f"The path ``{path}``\n"
            "is currently mounted as\n"
            f"{existing_source}.\n\n"
            "Replace it with:\n"
            f"//{host}/{share}?"
        )

        dialog.add_response(
            "keep", "Keep Original"
        )
        dialog.set_close_response(
            "keep"
        )

        dialog.add_response(
            "replace", "Replace"
        )
        dialog.set_default_response(
            "replace"
        )
        dialog.set_response_appearance(
            "replace", Adw.ResponseAppearance.DESTRUCTIVE
        )

        def on_response(
            _alert,
            response,
        ):

            if response == "replace":

                self._replace_mount(
                    row,
                    on_after_close=on_after_close,
                )

            else:

                # "keep" → do nothing, existing mount stays
                row.set_mounted(
                    is_mounted(
                        row.entry.get(
                            "path",
                            "",
                        ),
                        row.entry.get("host"),
                        row.entry.get("share"),
                    )
                )
                if on_after_close:

                    GLib.idle_add(
                        on_after_close
                    )

        dialog.connect(
            "response",
            on_response,
        )

        dialog.present(
            self.win
        )

    def _replace_mount(
        self,
        row: MountRow,
        on_after_close=None,
    ):

        path = row.entry.get("path", "")
        host = row.entry.get("host", "")
        share = row.entry.get("share", "")

        # Get the existing mount source to unmount it first
        existing_source = get_mount_source(
            path
        )

        if not existing_source:

            self.toast(
                "Could not identify the existing mount.",
                error=True,
            )

            return

        without_scheme = (
            existing_source[2:]
            if existing_source.startswith("//")
            else existing_source
        )

        parts = without_scheme.split("/", 1)

        if len(parts) != 2 or not parts[0] or not parts[1]:

            self.toast(
                "Could not parse existing mount source.",
                error=True,
            )

            return

        old_host = parts[0]
        old_share = parts[1].lstrip("/")

        temp_entry = {
            "path": path,
            "host": old_host,
            "share": old_share,
        }

        def worker():

            ok, message = do_unmount(
                temp_entry
            )

            GLib.idle_add(
                self._replace_mount_done,
                row,
                ok,
                message,
                on_after_close,
            )

        threading.Thread(
            target=worker,
            name="smb-replace",
            daemon=True,
        ).start()

    def _replace_mount_done(
        self,
        row: MountRow,
        ok: bool,
        message: str,
        on_after_close=None,
    ):

        if not ok:

            LOGGER.error(
                "Unmount failed during replace for %s/%s: %s",
                row.entry.get("host"),
                row.entry.get("share"),
                message,
            )

            self.toast(
                f"Failed to unmount existing share: {message}",
                error=True,
            )

            if on_after_close:

                GLib.idle_add(
                    on_after_close
                )

            return

        # Unmount succeeded — now proceed with normal mount flow
        LOGGER.info(
            "Unmounted existing share, proceeding to mount //%s/%s",
            row.entry.get("host"),
            row.entry.get("share"),
        )

        # Refresh toggles of other rows sharing this mount path so the
        # previously-mounted share no longer shows as connected.
        target = os.path.realpath(
            os.path.expanduser(
                row.entry.get("path", "")
            )
        )

        for other in self.rows.values():

            if other is row:

                continue

            o_path = other.entry.get("path", "")

            if not o_path:

                continue

            if os.path.realpath(
                os.path.expanduser(
                    o_path
                )
            ) != target:

                continue

            other.set_mounted(
                is_mounted(
                    o_path,
                    other.entry.get("host"),
                    other.entry.get("share"),
                )
            )

        self._ask_password(
            row
        )

    def _mount(
        self,
        row: MountRow,
        password: str,
        credential_storage=None,
    ):

        row.set_busy(
            True
        )

        def worker():

            ok, message = do_mount(
                row.entry,
                password,
            )

            GLib.idle_add(
                self._mount_done,
                row,
                ok,
                message,
                password,
                credential_storage,
            )

        threading.Thread(
            target=worker,
            name="smb-mount",
            daemon=True,
        ).start()

    def _mount_done(
        self,
        row,
        ok,
        message,
        password,
        credential_storage,
    ):

        row.set_busy(
            False
        )

        if ok:

            LOGGER.info(
                "Mounted share: //%s/%s",
                row.entry.get("host", ""),
                row.entry.get("share", ""),
            )

            if credential_storage is not None:

                try:

                    if credential_storage == "secret-service":

                        _secure_store_password(
                            row.entry["host"],
                            row.entry["share"],
                            _get_effective_username(
                                row.entry
                            ),
                            password,
                        )

                        row.entry["password"] = ""

                    elif credential_storage == "plaintext":

                        row.entry["password"] = password

                    else:

                        _secure_delete_password(
                            row.entry["host"],
                            row.entry["share"],
                            _get_effective_username(
                                row.entry
                            ),
                        )

                        row.entry["password"] = ""

                    row.entry[
                        "credential_storage"
                    ] = credential_storage

                    self.save_current_rows()

                except Exception as exc:

                    LOGGER.error(
                        "Could not save SMB credential: %s",
                        exc,
                    )

                    self.toast(
                        f"Mounted, but could not save password: {exc}",
                        error=True,
                    )

            row.set_mounted(
                True
            )

            self.toast(
                f"Mounted {row.entry['name']}"
            )

        else:

            LOGGER.error(
                "Mount failed for //%s/%s: %s",
                row.entry.get("host", ""),
                row.entry.get("share", ""),
                message,
            )

            row.set_mounted(
                False
            )

            self.toast(
                f"Mount failed: {message}",
                error=True,
            )

        return False

    def _unmount(
        self,
        row,
    ):

        row.set_busy(
            True
        )

        def worker():

            ok, message = do_unmount(
                row.entry
            )

            GLib.idle_add(
                self._unmount_done,
                row,
                ok,
                message,
            )

        threading.Thread(
            target=worker,
            name="smb-umount",
            daemon=True,
        ).start()

    def _unmount_done(
        self,
        row,
        ok,
        message,
    ):

        row.set_busy(
            False
        )

        if ok:

            row.set_mounted(
                False
            )

            self.toast(
                f"Unmounted {row.entry['name']}"
            )

        else:

            row.set_mounted(
                is_mounted(
                    row.entry.get(
                        "path",
                        "",
                    ),
                    row.entry.get("host"),
                    row.entry.get("share"),
                )
            )

            self.toast(
                f"Unmount failed: {message}",
                error=True,
            )

        return False

    # =========================================================================
    # Password dialog
    # =========================================================================

    def _ask_password(
        self,
        row,
    ):

        entry = row.entry

        def migrate_plaintext_password(
            password,
        ):

            try:

                if not _secure_storage_available():

                    return False

                _secure_store_password(
                    entry["host"],
                    entry["share"],
                    _get_effective_username(
                        entry
                    ),
                    password,
                )

                entry["password"] = ""

                entry[
                    "credential_storage"
                ] = "secret-service"

                self.save_current_rows()

                return True

            except Exception as exc:

                LOGGER.warning(
                    "Could not migrate SMB password "
                    "to Secret Service: %s",
                    exc,
                )

                return False
        
        def ask_migrate_plaintext_password(
            password,
        ):

            alert = Adw.AlertDialog(
                heading=(
                    "Password stored without encryption"
                ),
                body=(
                    "This SMB password is currently "
                    "stored unencrypted in MounThor's "
                    "configuration. "
                    "Secure Credential Storage is "
                    "available and can be used instead."
                ),
            )

            alert.add_response(
                "keep",
                "Keep as is",
            )

            alert.add_response(
                "migrate",
                "Move to Secure Storage",
            )

            alert.set_default_response(
                "migrate"
            )

            def on_response(
                _alert,
                response,
            ):

                if response == "migrate":

                    self._mount(
                        row,
                        password,
                        "secret-service",
                    )

                elif response == "keep":

                    self._mount(
                        row,
                        password,
                        "plaintext",
                    )

            alert.connect(
                "response",
                on_response,
            )

            alert.present(
                self.win
            )

        if (
            entry.get(
                "credential_storage"
            )
            == "secret-service"
        ):

            try:

                password = _secure_load_password(
                    entry["host"],
                    entry["share"],
                    _get_effective_username(
                        entry
                    ),
                )

            except Exception as exc:

                LOGGER.warning(
                    "Could not load SMB password "
                    "from Secret Service: %s",
                    exc,
                )

                password = None

            if password:

                self._mount(
                    row,
                    password,
                )

                return

        if (
            entry.get(
                "credential_storage"
            )
            == "plaintext"
        ):

            password = (
                entry.get(
                    "password"
                )
                or ""
            )

            if password:

                if _secure_storage_available():

                    ask_migrate_plaintext_password(
                        password
                    )

                else:

                    self._mount(
                        row,
                        password,
                        None,
                    )

                return

        dialog = Adw.Dialog()

        dialog.set_title(
            "SMB password"
        )

        dialog.set_content_width(
            440
        )

        dialog.set_content_height(
            320
        )

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=18,
            margin_bottom=18,
            margin_start=18,
            margin_end=18,
        )

        info = Gtk.Label(
            label=(
                f"Enter the password for\n"
                f"//{entry['host']}/{entry['share']}"
            ),
            wrap=True,
            halign=Gtk.Align.START,
        )

        content.append(
            info
        )

        fields = make_entry_listbox()

        password_row = (
            Adw.PasswordEntryRow(
                title="Password"
            )
        )

        fields.append(
            password_row
        )

        content.append(
            fields
        )

        remember_list = make_entry_listbox()

        remember_row = Adw.SwitchRow(
            title="Remember password",
            subtitle=(
                "Store the password for future mounts."
            ),
        )
        remember_list.append(
            remember_row
        )

        content.append(
            remember_list
        )

        (
            action_bar,
            cancel_button,
            connect_button,
        ) = make_action_bar(
            "Cancel",
            "Connect",
        )

        def on_cancel(
            _button,
        ):

            row.set_mounted(
                False
            )

            dialog.close()

        def complete_connect(
            password,
            credential_storage,
        ):

            dialog.close()

            self._mount(
                row,
                password,
                credential_storage,
            )

        def ask_insecure_storage(
            password,
        ):

            alert = Adw.AlertDialog(
                heading=(
                    "Secure Credential Storage unavailable"
                ),
                body=(
                    "Secure Credential Storage is not "
                    "available on this system. "
                    "You can continue without saving "
                    "the password, or save it unencrypted "
                    "in MounThor's configuration."
                ),
            )

            alert.add_response(
                "cancel",
                "Cancel",
            )

            alert.add_response(
                "none",
                "Don't remember",
            )

            alert.add_response(
                "plaintext",
                "Save without encryption",
            )

            alert.set_default_response(
                "none"
            )

            alert.set_close_response(
                "cancel"
            )

            alert.set_response_appearance(
                "plaintext",
                Adw.ResponseAppearance.DESTRUCTIVE,
            )

            def on_response(
                _alert,
                response,
            ):

                if response == "none":

                    complete_connect(
                        password,
                        "none",
                    )

                elif response == "plaintext":

                    complete_connect(
                        password,
                        "plaintext",
                    )

            alert.connect(
                "response",
                on_response,
            )

            alert.present(
                self.win
            )

        def on_connect(
            _button,
        ):

            password = get_text(
                password_row
            )

            if not password:

                self.toast(
                    "Password cannot be empty.",
                    error=True,
                )

                return

            if not remember_row.get_active():

                complete_connect(
                    password,
                    "none",
                )

                return

            try:

                if _secure_storage_available():

                    complete_connect(
                        password,
                        "secret-service",
                    )

                else:

                    ask_insecure_storage(
                        password
                    )

            except Exception:

                ask_insecure_storage(
                    password
                )

        cancel_button.connect(
            "clicked",
            on_cancel,
        )

        connect_button.connect(
            "clicked",
            on_connect,
        )

        view = make_dialog_view(
            content,
            action_bar,
        )

        dialog.set_child(
            view
        )

        install_enter_action(
            dialog,
            connect_button,
        )

        dialog.present(
            self.win
        )

    # =========================================================================
    # Connect / Disconnect Selected
    # =========================================================================

    def connect_selected(
        self,
    ):

        if self._batch_active:

            return

        selected_rows = self.selected_mount_rows()

        if not selected_rows:

            self.toast(
                "No SMB shares are selected."
            )

            return

        # Separate rows into clean and conflict groups
        clean_rows = []
        conflict_rows = []

        for row in selected_rows:

            path = row.entry.get("path", "")
            host = row.entry.get("host", "")
            share = row.entry.get("share", "")

            if is_mounted(
                path, host, share
            ):

                # Already mounted with same share — skip entirely
                continue

            elif is_mounted(path):

                # Conflict: path mounted by different share
                conflict_rows.append(row)

            else:

                clean_rows.append(row)

        if not clean_rows and not conflict_rows:

            self.toast(
                "All selected SMB shares are already connected."
            )

            return

        # Block the operation when multiple shares target the same path
        duplicate_groups = self._find_duplicate_paths(
            clean_rows + conflict_rows
        )

        if duplicate_groups:

            # The user's toggle click already flipped the switch on —
            # reset every selected row to its real mount state.
            for row in selected_rows:

                row.set_mounted(
                    is_mounted(
                        row.entry.get("path", ""),
                        row.entry.get("host"),
                        row.entry.get("share"),
                    )
                )

            self._show_duplicate_path_dialog(
                duplicate_groups
            )

            return

        # Process clean rows through normal batch flow
        if clean_rows:

            self._batch_active = True

            for row in clean_rows:

                row.set_busy(
                    True
                )

            self._collect_selected_batch_password(
                clean_rows
            )

        # Show dialogs for conflict rows (one at a time)
        if conflict_rows:

            self._show_conflict_dialogs(
                conflict_rows,
                0,
            )

    def _start_connect_all_clean(
        self,
        clean_rows,
    ):

        if not clean_rows:

            return

        self._batch_active = True

        for row in clean_rows:

            row.set_busy(
                True
            )

        self._collect_batch_passwords(
            clean_rows,
            0,
            {},
        )

    def _show_conflict_dialogs(
        self,
        conflict_rows: list[MountRow],
        index: int,
        on_after_close=None,
    ):

        if index >= len(conflict_rows):

            if on_after_close:

                GLib.idle_add(
                    on_after_close
                )

            return

        row = conflict_rows[index]
        path = row.entry.get("path", "")
        existing_source = get_mount_source(path) or ""

        def on_next():

            GLib.idle_add(
                self._show_conflict_dialogs,
                conflict_rows,
                index + 1,
                on_after_close,
            )

        self._show_replace_dialog(
            row,
            existing_source,
            on_after_close=on_next,
        )

    def _find_duplicate_paths(
        self,
        rows: list[MountRow],
    ):

        groups = {}

        for row in rows:

            path = row.entry.get("path", "")

            if not path:

                continue

            key = os.path.realpath(
                os.path.expanduser(
                    path
                )
            )

            groups.setdefault(
                key,
                [],
            ).append(
                row
            )

        return {
            key: group
            for key, group in groups.items()
            if len(group) > 1
        }

    def _show_duplicate_path_dialog(
        self,
        groups: dict,
    ):

        lines = [
            "Multiple SMB shares"
        ]

        for key in sorted(groups):

            names = " & ".join(
                f"{row.entry.get('name', 'Unnamed')}"
                for row in groups[key]
            )

            lines.append(f"{names}\n")
            lines.append(f"are trying to connect to the same mount path:")
            lines.append(f"``{key}``")

        lines.append("")
        lines.append(
            "This operation cannot be performed."
        )

        dialog = Adw.AlertDialog()
        dialog.set_heading(
            "Duplicate mount paths"
        )
        dialog.set_body(
            "\n".join(lines)
        )
        dialog.add_response(
            "ok",
            "OK",
        )
        dialog.set_close_response(
            "ok"
        )

        dialog.present(
            self.win
        )

    def disconnect_selected(
        self,
    ):

        if self._batch_active:

            return

        selected_rows = self.selected_mount_rows()

        if not selected_rows:

            self.toast(
                "No SMB shares are selected."
            )

            return

        rows = [
            row
            for row in selected_rows
            if is_mounted(
                row.entry.get(
                    "path",
                    "",
                ),
                row.entry.get("host"),
                row.entry.get("share"),
            )
        ]

        if not rows:

            self.toast(
                "No selected SMB shares are connected."
            )

            return

        self._batch_active = True

        for row in rows:

            row.set_busy(
                True
            )

        self._start_disconnect_batch(
            rows,
            "Disconnect Selected",
        )
    
    def _collect_selected_batch_password(
        self,
        rows: list[MountRow],
    ):

        passwords = {
            row.entry["id"]: (
                row.entry.get(
                    "password",
                    "",
                )
                or ""
            )
            for row in rows
        }

        def collect_next(
            index: int,
        ):

            if index >= len(rows):

                self._start_connect_all(
                    rows,
                    passwords,
                    "Connect Selected",
                )

                return

            row = rows[index]

            entry = row.entry

            saved_password = (
                entry.get(
                    "password",
                    "",
                )
                or ""
            )

            if saved_password:

                passwords[
                    entry["id"]
                ] = saved_password

                collect_next(
                    index + 1
                )

                return

            def accepted(
                password,
            ):

                passwords[
                    entry["id"]
                ] = password

                collect_next(
                    index + 1
                )

            def cancelled():

                for candidate in rows:

                    candidate.set_busy(
                        False
                    )

                self._batch_active = False

                self.refresh_mount_states()

                self.toast(
                    "Connect Selected cancelled.",
                    error=True,
                )

            self._ask_batch_password(
                entry,
                accepted,
                cancelled,
                index + 1,
                len(rows),
            )

        collect_next(
            0
        )

    def _ask_selected_batch_password(
        self,
        entry,
        on_accept,
        on_cancel,
        missing_count,
    ):

        dialog = Adw.Dialog()

        dialog.set_title(
            "Password required"
        )

        dialog.set_content_width(
            440
        )

        dialog.set_content_height(
            320
        )

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=18,
            margin_bottom=18,
            margin_start=18,
            margin_end=18,
        )

        info = Gtk.Label(
            label=(
                "Enter one password for the selected batch.\n\n"
                f"It will be used for {missing_count} selected share(s) "
                "without a saved password.\n\n"
                f"First share: “{entry.get('name', 'Unnamed share')}”"
            ),
            wrap=True,
            halign=Gtk.Align.START,
        )

        content.append(
            info
        )

        fields = make_entry_listbox()

        password_row = Adw.PasswordEntryRow(
            title="Password"
        )

        fields.append(
            password_row
        )

        content.append(
            fields
        )

        (
            action_bar,
            cancel_button,
            connect_button,
        ) = make_action_bar(
            "Cancel",
            "Continue",
        )

        def on_cancel_clicked(
            _button,
        ):

            dialog.close()

            on_cancel()

        def on_connect_clicked(
            _button,
        ):

            password = get_text(
                password_row
            )

            if not password:

                self.toast(
                    "Password cannot be empty.",
                    error=True,
                )

                return

            dialog.close()

            on_accept(
                password
            )

        cancel_button.connect(
            "clicked",
            on_cancel_clicked,
        )

        connect_button.connect(
            "clicked",
            on_connect_clicked,
        )

        dialog.set_child(
            make_dialog_view(
                content,
                action_bar,
            )
        )

        install_enter_action(
            dialog,
            connect_button,
        )

        dialog.present(
            self.win
        )

    # =========================================================================
    # Connect All
    # =========================================================================

    def _on_connect_all_action(
        self,
        _action,
        _parameter,
    ):

        if self._batch_active:

            return

        clean_rows = []
        conflict_rows = []

        for row in self.rows.values():

            path = row.entry.get("path", "")
            host = row.entry.get("host", "")
            share = row.entry.get("share", "")

            if is_mounted(
                path,
                host,
                share,
            ):

                continue

            if is_mounted(path):

                conflict_rows.append(
                    row
                )

            else:

                clean_rows.append(
                    row
                )

        rows = clean_rows + conflict_rows

        if not rows:

            self.toast(
                "All SMB shares are already connected."
            )

            return

        # Block the operation when multiple shares target the same path
        duplicate_groups = self._find_duplicate_paths(
            rows
        )

        if duplicate_groups:

            self._show_duplicate_path_dialog(
                duplicate_groups
            )

            return

        if conflict_rows:

            self._show_conflict_dialogs(
                conflict_rows,
                0,
                on_after_close=lambda: self._start_connect_all_clean(
                    clean_rows
                ),
            )

            return

        self._start_connect_all_clean(
            clean_rows
        )

    def _collect_batch_passwords(
        self,
        rows: list[MountRow],
        index: int,
        passwords: dict,
    ):

        if index >= len(rows):

            self._start_connect_all(
                rows,
                passwords,
            )

            return

        row = rows[index]

        entry = row.entry

        saved_password = (
            entry.get(
                "password",
                "",
            )
            or ""
        )

        if saved_password:

            passwords[
                entry["id"]
            ] = saved_password

            self._collect_batch_passwords(
                rows,
                index + 1,
                passwords,
            )

            return

        def accepted(
            password,
        ):

            passwords[
                entry["id"]
            ] = password

            self._collect_batch_passwords(
                rows,
                index + 1,
                passwords,
            )

        def cancelled():

            for candidate in rows:

                candidate.set_busy(
                    False
                )

            self._batch_active = False

            self.refresh_mount_states()

            self.toast(
                "Connect All cancelled.",
                error=True,
            )

        self._ask_batch_password(
            entry,
            accepted,
            cancelled,
            index + 1,
            len(rows),
        )

    def _ask_batch_password(
        self,
        entry,
        on_accept,
        on_cancel,
        number,
        total,
    ):

        dialog = Adw.Dialog()

        dialog.set_title(
            "Password required"
        )

        dialog.set_content_width(
            440
        )

        dialog.set_content_height(
            320
        )

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=18,
            margin_bottom=18,
            margin_start=18,
            margin_end=18,
        )

        info = Gtk.Label(
            label=(
                f"Share {number} of {total}\n\n"
                f"Enter the password for\n"
                f"“{entry.get('name', 'Unnamed share')}”\n\n"
                f"//{entry.get('host', '')}/"
                f"{entry.get('share', '')}"
            ),
            wrap=True,
            halign=Gtk.Align.START,
        )

        content.append(
            info
        )

        fields = make_entry_listbox()

        password_row = Adw.PasswordEntryRow(
            title="Password"
        )

        fields.append(
            password_row
        )

        content.append(
            fields
        )

        remember_list = make_entry_listbox()

        remember_row = Adw.SwitchRow(
            title="Remember password",
            subtitle=(
                "Store the password for future mounts."
            ),
        )

        remember_list.append(
            remember_row
        )

        content.append(
            remember_list
        )

        (
            action_bar,
            cancel_button,
            connect_button,
        ) = make_action_bar(
            "Cancel",
            "Continue",
        )

        def on_cancel_clicked(
            _button,
        ):

            dialog.close()

            on_cancel()

        def on_connect_clicked(
            _button,
        ):

            password = get_text(
                password_row
            )

            if not password:

                self.toast(
                    "Password cannot be empty.",
                    error=True,
                )

                return

            if remember_row.get_active():

                entry["password"] = password

                try:

                    self.save_current_rows()

                except OSError as exc:

                    self.toast(
                        f"Could not save password: {exc}",
                        error=True,
                    )

                    return

            dialog.close()

            on_accept(
                password
            )

        cancel_button.connect(
            "clicked",
            on_cancel_clicked,
        )

        connect_button.connect(
            "clicked",
            on_connect_clicked,
        )

        dialog.set_child(
            make_dialog_view(
                content,
                action_bar,
            )
        )

        install_enter_action(
            dialog,
            connect_button,
        )

        dialog.present(
            self.win
        )

    def _start_connect_all(
        self,
        rows,
        passwords,
        operation_name="Connect All",
    ):

        items = []

        for row in rows:

            entry = dict(
                row.entry
            )

            if not (
                entry.get("username")
                or ""
            ).strip():

                entry["username"] = (
                    os.environ.get("USER")
                    or ""
                )

            items.append(
                {
                    "id": row.entry["id"],
                    "entry": entry,
                    "password": passwords.get(
                        row.entry["id"],
                        "",
                    ),
                    "uid": os.getuid(),
                    "gid": os.getgid(),
                }
            )

        self.toast(
            f"{operation_name}: connecting {len(rows)} share(s)…"
        )

        def worker():

            results = _run_privileged_batch(
                "batch-mount",
                items,
            )

            GLib.idle_add(
                self._connect_all_done,
                rows,
                results,
                operation_name,
            )

        threading.Thread(
            target=worker,
            name="smb-connect-all",
            daemon=True,
        ).start()

    def _connect_all_done(
        self,
        rows,
        results,
        operation_name="Connect All",
    ):

        result_map = {
            result.get("id"): result
            for result in results
            if isinstance(
                result,
                dict,
            )
        }

        succeeded = 0
        failed = 0

        for row in rows:

            result = result_map.get(
                row.entry["id"]
            )

            if result is None:

                ok = False

                message = (
                    "no result returned"
                )

            else:

                ok = bool(
                    result.get(
                        "ok",
                        False,
                    )
                )

                message = (
                    result.get(
                        "message",
                        "mount failed",
                    )
                    or "mount failed"
                )

            row.set_busy(
                False
            )

            row.set_mounted(
                ok
            )

            if ok:

                succeeded += 1

            else:

                failed += 1

        LOGGER.info(
            "%s finished: %d succeeded, %d failed.",
            operation_name,
            succeeded,
            failed,
        )

        self._batch_active = False

        if operation_name == "Connect Selected":

            for row in self.selected_mount_rows():

                row.set_selected(False)

        if failed:

            self.toast(
                f"{operation_name} finished: "
                f"{succeeded} connected, "
                f"{failed} failed.",
                error=True,
            )

        else:

            self.toast(
                f"Connected {succeeded} share(s)."
            )

        return False

    # =========================================================================
    # Disconnect All
    # =========================================================================

    def _start_disconnect_batch(
        self,
        rows,
        operation_name="Disconnect All",
    ):

        self.toast(
            f"{operation_name}: disconnecting {len(rows)} share(s)…"
        )

        items = [
            {
                "id": row.entry["id"],
                "entry": dict(
                    row.entry
                ),
            }
            for row in rows
        ]

        def worker():

            results = _run_privileged_batch(
                "batch-unmount",
                items,
            )

            GLib.idle_add(
                self._disconnect_all_done,
                rows,
                results,
                operation_name,
            )

        threading.Thread(
            target=worker,
            name="smb-disconnect-batch",
            daemon=True,
        ).start()

    def _on_disconnect_all_action(
        self,
        _action,
        _parameter,
    ):

        if self._batch_active:

            return

        rows = [
            row
            for row in self.rows.values()
            if is_mounted(
                row.entry.get(
                    "path",
                    "",
                ),
                row.entry.get("host"),
                row.entry.get("share"),
            )
        ]

        if not rows:

            self.toast(
                "No SMB shares are connected."
            )

            return

        self._batch_active = True

        for row in rows:

            row.set_busy(
                True
            )

        self.toast(
            f"Disconnecting {len(rows)} share(s)…"
        )

        items = [
            {
                "id": row.entry["id"],
                "entry": dict(
                    row.entry
                ),
            }
            for row in rows
        ]

        def worker():

            results = _run_privileged_batch(
                "batch-unmount",
                items,
            )

            GLib.idle_add(
                self._disconnect_all_done,
                rows,
                results,
            )

        threading.Thread(
            target=worker,
            name="smb-disconnect-all",
            daemon=True,
        ).start()

    def _disconnect_all_done(
        self,
        rows,
        results,
        operation_name="Disconnect All",
    ):

        result_map = {
            result.get("id"): result
            for result in results
            if isinstance(
                result,
                dict,
            )
        }

        succeeded = 0
        failed = 0

        for row in rows:

            result = result_map.get(
                row.entry["id"]
            )

            if result is None:

                ok = False

            else:

                ok = bool(
                    result.get(
                        "ok",
                        False,
                    )
                )

            row.set_busy(
                False
            )

            if ok:

                row.set_mounted(
                    False
                )

                succeeded += 1

            else:

                row.set_mounted(
                    is_mounted(
                        row.entry.get(
                            "path",
                            "",
                        ),
                        row.entry.get("host"),
                        row.entry.get("share"),
                    )
                )

                failed += 1

        LOGGER.info(
            "%s finished: %d succeeded, %d failed.",
            operation_name,
            succeeded,
            failed,
        )

        self._batch_active = False

        if operation_name == "Disconnect Selected":

            for row in self.selected_mount_rows():

                row.set_selected(False)

        if failed:

            self.toast(
                f"{operation_name} finished: "
                f"{succeeded} disconnected, "
                f"{failed} failed.",
                error=True,
            )

        else:

            self.toast(
                f"Disconnected {succeeded} share(s)."
            )

        return False

    # =========================================================================
    # Add / edit / duplicate
    # =========================================================================

    def entry_dialog(
        self,
        row=None,
        duplicate_entry=None,
    ):

        if duplicate_entry is not None:

            entry = duplicate_entry
            is_duplicate = True

        else:

            entry = (
                row.entry
                if row is not None
                else None
            )

            is_duplicate = False

        dialog = Adw.Dialog()

        if is_duplicate:

            dialog.set_title(
                "Duplicate SMB share"
            )

        elif entry:

            dialog.set_title(
                "Edit share"
            )

        else:

            dialog.set_title(
                "Add SMB share"
            )

        dialog.set_content_width(
            500
        )

        dialog.set_content_height(
            620
        )

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=18,
            margin_bottom=18,
            margin_start=18,
            margin_end=18,
        )

        description = Gtk.Label(
            label=(
                "Configure the SMB share and "
                "local mount point."
            ),
            wrap=True,
            halign=Gtk.Align.START,
        )

        content.append(
            description
        )

        fields = make_entry_listbox()

        name_row = Adw.EntryRow(
            title="Name"
        )

        initial_name = ""

        if entry:

            initial_name = entry.get(
                "name",
                "",
            )

        if is_duplicate:

            if initial_name:

                initial_name = (
                    f"{initial_name} Copy"
                )

            else:

                initial_name = "Copy"

        name_row.set_text(
            initial_name
        )

        name_row.set_tooltip_text(
            "Friendly name displayed in the mount list of this Tool."
        )

        fields.append(
            name_row
        )

        host_row = Adw.EntryRow(
            title="Host"
        )

        host_row.set_text(
            entry.get(
                "host",
                "",
            )
            if entry
            else ""
        )

        host_row.set_tooltip_text(
            "IP address or hostname, for example: 192.168.1.12."
        )

        fields.append(
            host_row
        )

        share_row = Adw.EntryRow(
            title="Share"
        )

        share_row.set_text(
            entry.get(
                "share",
                "",
            )
            if entry
            else ""
        )

        share_row.set_tooltip_text(
            "SMB share name, for example: movies"
        )

        fields.append(
            share_row
        )

        path_row = Adw.EntryRow(
            title="Mount path"
        )

        path_row.set_text(
            entry.get(
                "path",
                "",
            )
            if entry
            else ""
        )

        path_row.set_tooltip_text(
            "Local mount path, for example: ~/mnt/movies"
        )

        fields.append(
            path_row
        )

        username_row = Adw.EntryRow(
            title="Username"
        )

        username_row.set_text(
            entry.get(
                "username",
                "",
            )
            if entry
            else ""
        )

        username_row.set_tooltip_text(
            "Optional SMB username. Empty uses the current Linux user."
        )

        fields.append(
            username_row
        )

        options_row = Adw.EntryRow(
            title="CIFS options"
        )

        options_row.set_text(
            entry.get(
                "options",
                "",
            )
            if entry
            else ""
        )

        options_row.set_tooltip_text(
            "Optional comma-separated options, e.g. vers=3.1.1"
        )

        fields.append(
            options_row
        )

        automount_list = make_entry_listbox()

        automount_row = Adw.SwitchRow(
            title="Automount with application startup",
            subtitle=(
                "Automatically mount this share "
                "when MounThor starts."
            ),
        )

        automount_row.set_active(
            bool(
                entry.get(
                    "automount",
                    False,
                )
            )
            if entry
            else False
        )

        automount_list.append(
            automount_row
        )

        system_automount_row = Adw.SwitchRow(
            title="Automount with system startup",
            subtitle=(
                "Automatically mount this share "
                "at login, even when MounThor is not running."
            ),
        )

        system_automount_row.set_active(
            bool(
                entry.get(
                    "system_automount",
                    False,
                )
            )
            if entry
            else False
        )

        automount_list.append(
            system_automount_row
        )

        content.append(
            fields
        )

        content.append(
            automount_list
        )

        password_info = Gtk.Label(
            label=(
                "Password is not configured here. It is set when mounting."
            ),
            wrap=True,
            halign=Gtk.Align.START,
        )

        password_info.add_css_class(
            "dim-label"
        )

        content.append(
            password_info
        )

        (
            action_bar,
            cancel_button,
            save_button,
        ) = make_action_bar(
            "Cancel",
            "Save",
        )

        def on_cancel(
            _button,
        ):

            dialog.close()

        def on_save(
            _button,
        ):

            host = get_text(
                host_row
            ).strip()

            share = (
                get_text(
                    share_row
                )
                .strip()
                .lstrip("/")
            )

            path = get_text(
                path_row
            ).strip()

            username = get_text(
                username_row
            ).strip()

            options = get_text(
                options_row
            ).strip()

            name = get_text(
                name_row
            ).strip()

            if not host:

                self.toast(
                    "Host is required.",
                    error=True,
                )

                return

            if not share:

                self.toast(
                    "Share name is required.",
                    error=True,
                )

                return

            if not path:

                self.toast(
                    "Mount path is required.",
                    error=True,
                )

                return

            if not name:

                name = f"{host}/{share}"

            normalized_options = ",".join(
                _clean_cifs_options(
                    options
                )
            )

            data = {
                "name": name,
                "host": host,
                "share": share,
                "path": path,
                "username": username,
                "options": normalized_options,
                "automount": automount_row.get_active(),
                "system_automount": (
                    system_automount_row.get_active()
                ),
            }

            cfg = load_config()

            old_credential_storage = "none"
            old_effective_username = ""
            credential_identity_changed = False

            old_entry = None

            if (
                row is not None
                and not is_duplicate
            ):

                old_entry = next(
                    (
                        mount
                        for mount in cfg["mounts"]
                        if (
                            isinstance(
                                mount,
                                dict,
                            )
                            and mount.get("id")
                            == row.entry["id"]
                        )
                    ),
                    None,
                )

            if (
                row is not None
                and not is_duplicate
            ):

                found = False

                for index, mount in enumerate(
                    cfg["mounts"]
                ):

                    if (
                        isinstance(
                            mount,
                            dict,
                        )
                        and mount.get("id")
                        == row.entry["id"]
                    ):

                        existing_password = (
                            mount.get(
                                "password",
                                "",
                            )
                            or ""
                        )

                        old_effective_username = (
                            _get_effective_username(
                                old_entry
                            )
                            if old_entry
                            else ""
                        )

                        new_entry_for_identity = {
                            **data,
                        }

                        new_effective_username = (
                            _get_effective_username(
                                new_entry_for_identity
                            )
                        )

                        credential_identity_changed = (
                            old_entry is not None
                            and (
                                old_entry.get(
                                    "host",
                                    "",
                                )
                                != data["host"]
                                or old_entry.get(
                                    "share",
                                    "",
                                )
                                != data["share"]
                                or old_effective_username
                                != new_effective_username
                            )
                        )

                        existing_credential_storage = (
                            mount.get(
                                "credential_storage"
                            )
                            or (
                                "plaintext"
                                if existing_password
                                else "none"
                            )
                        )

                        old_credential_storage = (
                            existing_credential_storage
                        )

                        if credential_identity_changed:

                            new_password = ""
                            new_credential_storage = "none"

                        else:

                            new_password = (
                                existing_password
                            )

                            new_credential_storage = (
                                existing_credential_storage
                            )

                        cfg["mounts"][index] = {
                            **mount,
                            **data,
                            "id": row.entry["id"],
                            "password": new_password,
                            "credential_storage": (
                                new_credential_storage
                            ),
                        }

                        found = True

                        break

                if not found:

                    cfg["mounts"].append(
                        {
                            **data,
                            "id": row.entry["id"],
                            "password": "",
                            "credential_storage": "none",
                        }
                    )

            else:

                cfg["mounts"].append(
                    entry_from_data(
                        {
                            **data,
                            "password": "",
                        }
                    )
                )

            try:

                save_config(
                    cfg
                )

            except OSError as exc:

                self.toast(
                    f"Could not save configuration: {exc}",
                    error=True,
                )

                return

            if (
                credential_identity_changed
                and old_credential_storage
                == "secret-service"
            ):

                try:

                    _secure_delete_password(
                        old_entry["host"],
                        old_entry["share"],
                        old_effective_username,
                    )

                except Exception as exc:

                    LOGGER.warning(
                        "Could not remove old SMB "
                        "credential from Secret Service: %s",
                        exc,
                    )

            if (
                data["system_automount"]
                and not POLKIT_RULE_FILE.is_file()
            ):

                def _revert_system_automount():

                    cfg_now = load_config()

                    for mount in cfg_now["mounts"]:

                        if (
                            isinstance(
                                mount,
                                dict,
                            )
                            and mount.get("host")
                            == data["host"]
                            and mount.get("share")
                            == data["share"]
                            and mount.get("path")
                            == data["path"]
                        ):

                            mount[
                                "system_automount"
                            ] = False

                    save_config(
                        cfg_now
                    )

                def _on_setup_result(
                    ok,
                    message,
                ):

                    if not ok:

                        _revert_system_automount()

                        self.rebuild_rows()

                    self.toast(
                        message,
                        error=not ok,
                    )

                def on_setup_choice(
                    _alert,
                    response,
                ):

                    if response != "setup":

                        _revert_system_automount()

                        self.rebuild_rows()

                        return

                    def _worker():

                        ok, message = (
                            _ensure_system_automount_ready()
                        )

                        GLib.idle_add(
                            _on_setup_result,
                            ok,
                            message,
                        )

                    threading.Thread(
                        target=_worker,
                        daemon=True,
                    ).start()

                alert = Adw.AlertDialog(
                    heading=(
                        "One-time authorization required"
                    ),
                    body=(
                        "Automounting this share at login "
                        "requires installing a small helper "
                        "and enabling a systemd service. "
                        "You will be asked for your password "
                        "once to authorize this."
                    ),
                )

                alert.add_response(
                    "cancel",
                    "Cancel"
                )

                alert.add_response(
                    "setup",
                    "Set up now"
                )

                alert.set_default_response(
                    "setup"
                )

                alert.connect(
                    "response",
                    on_setup_choice,
                )

                alert.present(
                    self.win
                )

            dialog.close()

            self.rebuild_rows()

            self.toast(
                "Share saved."
            )

        cancel_button.connect(
            "clicked",
            on_cancel,
        )

        save_button.connect(
            "clicked",
            on_save,
        )

        view = make_dialog_view(
            content,
            action_bar,
        )

        dialog.set_child(
            view
        )

        install_enter_action(
            dialog,
            save_button,
        )

        dialog.present(
            self.win
        )

    def edit_row(
        self,
        row,
    ):

        self.entry_dialog(
            row
        )

    def duplicate_row(
        self,
        row,
    ):

        duplicate_data = dict(
            row.entry
        )

        duplicate_data.pop(
            "id",
            None,
        )

        duplicate_data["password"] = ""

        self.entry_dialog(
            duplicate_entry=duplicate_data
        )

    # =========================================================================
    # Delete
    # =========================================================================

    def delete_row(
        self,
        row,
    ):

        entry = row.entry

        mounted = is_mounted(
            entry.get(
                "path",
                "",
            ),
            entry.get("host"),
            entry.get("share"),
        )

        dialog = Adw.Dialog()

        dialog.set_title(
            f"Remove “{entry.get('name', 'share')}”?"
        )

        dialog.set_content_width(
            440
        )

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_top=18,
            margin_bottom=18,
            margin_start=18,
            margin_end=18,
        )

        message = (
            "This share is currently mounted.\n\n"
            "It will be unmounted first and then "
            "removed from this application."
            if mounted
            else
            "This removes the share from this application.\n\n"
            "The actual SMB share on the NAS will not "
            "be affected."
        )

        label = Gtk.Label(
            label=message,
            wrap=True,
            halign=Gtk.Align.START,
        )

        content.append(
            label
        )

        (
            action_bar,
            cancel_button,
            remove_button,
        ) = make_action_bar(
            "Cancel",
            "Remove",
            accept_css_class="destructive-action",
        )

        cancel_button.connect(
            "clicked",
            lambda _button: dialog.close(),
        )

        def on_remove(
            _button,
        ):

            dialog.close()

            self._remove_entry(
                row
            )

        remove_button.connect(
            "clicked",
            on_remove,
        )

        view = make_dialog_view(
            content,
            action_bar,
        )

        dialog.set_child(
            view
        )

        install_enter_action(
            dialog,
            remove_button,
        )

        dialog.present(
            self.win
        )

    def _remove_entry(
        self,
        row,
    ):

        entry = dict(
            row.entry
        )

        row.set_busy(
            True
        )

        def worker():

            if is_mounted(
                entry.get(
                    "path",
                    "",
                ),
                entry.get("host"),
                entry.get("share"),
            ):

                ok, message = do_unmount(
                    entry
                )

                if not ok:

                    GLib.idle_add(
                        self._delete_failed,
                        row,
                        message,
                    )

                    return

            if (
                entry.get(
                    "credential_storage"
                )
                == "secret-service"
            ):

                try:

                    _secure_delete_password(
                        entry["host"],
                        entry["share"],
                        _get_effective_username(
                            entry
                        ),
                    )

                except Exception as exc:

                    GLib.idle_add(
                        self._delete_failed,
                        row,
                        (
                            "cannot remove stored "
                            f"credential: {exc}"
                        ),
                    )

                    return

            cfg = load_config()

            cfg["mounts"] = [
                mount
                for mount in cfg["mounts"]
                if (
                    not isinstance(
                        mount,
                        dict,
                    )
                    or mount.get("id")
                    != entry.get("id")
                )
            ]

            try:

                save_config(
                    cfg
                )

            except OSError as exc:

                GLib.idle_add(
                    self._delete_failed,
                    row,
                    f"cannot save configuration: {exc}",
                )

                return

            GLib.idle_add(
                self._delete_done,
                entry,
            )

        threading.Thread(
            target=worker,
            name="smb-remove",
            daemon=True,
        ).start()

    def _delete_failed(
        self,
        row,
        message,
    ):

        row.set_busy(
            False
        )

        row.set_mounted(
            is_mounted(
                row.entry.get(
                    "path",
                    "",
                ),
                row.entry.get("host"),
                row.entry.get("share"),
            )
        )

        self.toast(
            f"Remove failed: {message}",
            error=True,
        )

        return False

    def _delete_done(
        self,
        entry,
    ):

        self.rebuild_rows()

        self.toast(
            f"Removed “{entry.get('name', 'share')}”"
        )

        return False

    # =========================================================================
    # About
    # =========================================================================

    def _on_deselect_all_action(
        self,
        _action,
        _parameter,
    ):

        self.deselect_all()
    
    def _on_about_action(
        self,
        _action,
        _parameter,
    ):

        self.show_about()

    def show_about(
        self,
    ):

        about = Adw.AboutDialog()

        about.set_application_name(
            APP_NAME
        )

        about.set_application_icon(
            "folder-remote-symbolic"
        )

        about.set_version(
            APP_VERSION
        )

        about.set_developer_name(
            APP_AUTHOR
        )

        about.set_release_notes(
            "<p>New in this version:</p>"
            "<p>Layered mounting of multiple shares to the same mount path is now detected and prevented.</p>" 
            "<p>MounThor now detects attempts to mount a share to a path that is already occupied. Depending on the situation, it can offer to replace the existing share mount with the requested one. For batch operations containing multiple mount requests targeting the same path, MounThor instead alerts the user to the conflict and does not perform the operation.</p>"
            "<ul>"
                "<li>Improved handling of multiple SMB shares configured with the same mount path.</li>"
                "<li>Fixed incorrect mounted-state detection when different shares use the same local path.</li>"
                "<li>Added protection against unintentionally stacking SMB mounts on an already used mount path.</li>"
                "<li>Added an option to replace an existing mount with another SMB share.</li>"
                "<li>Fixed Connect Selected and Connect All handling for mount path conflicts.</li>"
            "</ul>"
            "<p>New in 0.8.0 release:</p>"
            "<ul>"
                "<li>Added secure password storage using the Freedesktop Secret Service API.</li>"
                "<li>Save credentials only after a successful mount.</li>"
                "<li>Added migration of plaintext passwords to secure storage.</li>"
            "</ul>"
            "<p>New in 0.7.0 release:</p>"
            "<ul>"
                "<li>Defined app name and license.</li>"
                "<li>Added cleanup of temporary CIFS credential files after an unexpected application exit.</li>"
                "<li>Added basic logging.</li>"
                "<li>Renamed Deselect All button to Clear Selection</li>"
                "<li>Updated the Details section.</li>"
            "</ul>"
            "<p>New in 0.6.3 pre-release:</p>"
            "<ul>"
                "<li>Added a Deselect All button to the main menu.</li>"
            "</ul>"
            "<p>New in 0.6.2 pre-release:</p>"
            "<ul>"
                "<li>Removed Connect Selected and Disconnect Selected from the main menu.</li>"
            "</ul>"
            "<p>New in 0.6.1 pre-release:</p>"
            "<ul>"
                "<li>Automatically deselect shares after selected actions.</li>"
                "<li>Connect Selected and Disconnect Selected can now be triggered from the share toggle when multiple shares are selected.</li>"
            "</ul>"
            "<p>New in 0.6.0 pre-release:</p>"
            "<ul>"
                "<li>Added share selection.</li>"
                "<li>Added Connect Selected.</li>"
                "<li>Added Disconnect Selected.</li>"
            "</ul>"
            "<p>New in 0.5.0 pre-release:</p>"
            "<ul>"
                "<li>Added automatic mounting at application startup.</li>"
                "<li>Added What's New and more details to the About dialog.</li>"
                "<li>Polished names and tooltips.</li>"
            "</ul>"
            "<p>New in 0.4.1 pre-release</p>"
            "<ul>"
                "<li>Fixed naming inconsistencies.</li>"
            "</ul>"
            "<p>New in 0.4.0 pre-pre-release</p>"
            "<ul>"
                "<li>Added <em>Connect All</em> action.</li>"
                "<li>Batch actions require only one superuser confirmation.</li>"
                "<li>Added support for confirming with the Enter key.</li>"
            "</ul>"
            "<p>New in 0.3.0 pre-release</p>"
            "<ul>"
                "<li>Added a main menu.</li>"
                "<li>Added <em>Disconnect All</em> action.</li>"
                "<li>Reordered buttons.</li>"
            "</ul>"
            "<p>New in 0.2.0 pre-release</p>"
            "<ul>"
                "<li>Added the ability to duplicate shares.</li>"
            "</ul>"
            "<p>New in 0.1.1 pre-release</p>"
            "<ul>"
                "<li>UI tweaks.</li>"
            "</ul>"
            "<p>Initial 0.1.0 pre-release</p>"
            "<ul>"
                "<li>Base UI</li>"
                "<li>Add, edit, and remove shares.</li>"
                "<li>Mount and unmount shares.</li>"
                "<li>JSON configuration.</li>"
                "<li>Support for automatically using the current host username.</li>"
            "</ul>"
        )

        about.set_release_notes_version(
            APP_VERSION
        )

        about.set_website(
            "https://github.com/mizgo/MounThor"
        )

        about.set_comments(
            f"<b>{APP_NAME} {APP_VERSION}</b>\n"
            f"Release date: {APP_RELEASE_DATE}\n\n"
            "A simple Linux desktop application for fast and convenient SMB network share mounting.\n\n"
            "Save frequently used shares along with their mount settings and connect them with just a few clicks. For credentials, you can either save them in the application's configuration file or enter the password each time a share is mounted. You can also use your current Linux account name as the SMB username without storing it in the configuration.\n\n"
            "The app provides batch actions for connecting and disconnecting all shares or selected shares, allowing you to authenticate with your superuser password once and apply the action to multiple shares. Individual shares can also be configured to automatically mount when the application starts.\n"
            "The interface follows the system's GTK light and dark themes and respects the configured accent color.\n\n"
            "The goal is to provide a simple and elegant GUI built with GTK4 and libadwaita for managing CIFS/SMB mounts without repeatedly entering long mount commands and credentials or writing custom scripts for shares that do not need to be persistent."
        )

        about.set_license_type(
            Gtk.License.GPL_3_0
        )

        about.present(
            self.win
        )


# ============================================================================
# System automount entry point (--autostart)
# ============================================================================

def _run_autostart() -> int:

    """Mount all entries with system_automount enabled. No GUI."""

    _configure_logging()

    try:

        cfg = load_config()

    except Exception as exc:

        LOGGER.error(
            f"Failed to load config: {exc}"
        )

        print(json.dumps({
            "ok": False,
            "message": f"Failed to load config: {exc}",
        }))

        return 1

    summary = {
        "mounted": [],
        "skipped": [],
        "failed": [],
    }

    for entry in cfg.get(
        "mounts",
        []
    ):

        if not entry.get(
            "system_automount",
            False
        ):

            continue

        host = entry.get("host") or ""
        share = entry.get("share") or ""
        path = entry.get("path") or ""
        username = entry.get("username") or ""

        if is_mounted(
            path,
            host,
            share
        ):

            summary["skipped"].append(
                f"{entry.get('name')}: already mounted"
            )

            continue

        password = _secure_load_password(
            host,
            share,
            username
        )

        if not password:

            password = entry.get("password") or ""

        if not password:

            LOGGER.warning(
                f"Skipping '{entry.get('name')}': "
                "no stored password available."
            )

            summary["skipped"].append(
                f"{entry.get('name')}: no password"
            )

            continue

        ok, message = mount_entry_privileged(
            entry,
            password
        )

        if ok:

            LOGGER.info(
                f"Auto-mounted '{entry.get('name')}': {message}"
            )

            summary["mounted"].append(
                entry.get("name")
            )

        else:

            LOGGER.error(
                f"Failed to auto-mount "
                f"'{entry.get('name')}': {message}"
            )

            summary["failed"].append(
                f"{entry.get('name')}: {message}"
            )

    print(json.dumps(summary))

    return 0


# ============================================================================
# Main / privileged helper entry point
# ============================================================================

def main():

    if "--batch-mount" in sys.argv:

        return _privileged_batch_main(
            "batch-mount"
        )

    if "--batch-unmount" in sys.argv:

        return _privileged_batch_main(
            "batch-unmount"
        )

    if "--autostart" in sys.argv:

        return _run_autostart()

    _configure_logging()

    app = MounThorApp()

    return app.run(
        None
    )

if __name__ == "__main__":

    sys.exit(
        main()
    )