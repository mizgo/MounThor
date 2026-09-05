#!/usr/bin/env python3

"""Mount/unmount operations and batch helpers for MounThor."""

import json
import logging
import os
import secrets
import subprocess
import sys
from pathlib import Path

from ..constants import HELPER_BIN, USE_SUDO

LOGGER = logging.getLogger("mounthor")


# ============================================================================
# Mount state checking
# ============================================================================

def _unescape_mount_field(value: str) -> str:
    """Unescape special characters from /proc/mounts fields."""
    return (
        value
        .replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\134", "\\")
    )


def is_mounted(path: str, host: str | None = None, share: str | None = None) -> bool:
    """Check whether a path is mounted (optionally matching host/share)."""
    if not path:
        return False

    target = os.path.realpath(os.path.expanduser(path))

    # Normalize host/share for exact matching
    norm_host, norm_share = "", ""
    if host is not None and share is not None:
        norm_host = host.strip().lower()
        norm_share = share.strip().lstrip("/").lower()
        if not norm_host or not norm_share:
            return False

    try:
        with open("/proc/self/mounts", "r", encoding="utf-8") as mounts:
            for line in mounts:
                parts = line.split(None, 3)
                if len(parts) < 2:
                    continue

                device = parts[0].strip()
                mounted_path = _unescape_mount_field(parts[1])
                filesystem_type = parts[2] if len(parts) >= 3 else ""

                if host is not None and share is not None:
                    expected_device = f"//{norm_host}/{norm_share}"
                    normalized_device = device.lower()

                    if (
                        filesystem_type.lower() == "cifs"
                        and normalized_device == expected_device
                        and os.path.realpath(mounted_path) == target
                    ):
                        return True
                    continue

                # Fallback: match by mount point only
                if os.path.realpath(mounted_path) == target:
                    return True

    except OSError:
        pass

    return False


def get_mount_source(path: str) -> str | None:
    """Return the SMB source (e.g. '//host/share') mounted at *path*, or None."""
    return _get_topmost_mount_source(path)


def _get_topmost_mount_source(path: str) -> str | None:
    """Find the topmost mount source for a given path using /proc/self/mountinfo."""
    if not path:
        return None

    target = os.path.realpath(os.path.expanduser(path))
    mounts = []

    try:
        with open("/proc/self/mountinfo", "r", encoding="utf-8") as mountinfo:
            for line in mountinfo:
                fields = line.split(" - ", 1)
                if len(fields) != 2:
                    continue

                pre_separator = fields[0].split()
                post_separator = fields[1].split()

                if len(pre_separator) < 5 or len(post_separator) < 2:
                    continue

                mount_id = int(pre_separator[0])
                parent_id = int(pre_separator[1])
                mounted_path = os.path.realpath(_unescape_mount_field(pre_separator[4]))

                if mounted_path != target:
                    continue

                filesystem_type = post_separator[0]
                source = _unescape_mount_field(post_separator[1])

                mounts.append({
                    "id": mount_id,
                    "parent_id": parent_id,
                    "filesystem_type": filesystem_type,
                    "source": source,
                })

    except (OSError, ValueError):
        return None

    if not mounts:
        return None

    mount_ids = {mount["id"] for mount in mounts}
    child_mount_ids = {
        mount["parent_id"] for mount in mounts
        if mount["parent_id"] in mount_ids
    }

    topmost = [mount for mount in mounts if mount["id"] not in child_mount_ids]

    if len(topmost) != 1:
        return None

    return topmost[0]["source"]


# ============================================================================
# Privilege and credential helpers
# ============================================================================

def _auth(command: list[str]) -> list[str]:
    """Prepend pkexec or sudo to a command based on USE_SUDO setting."""
    if USE_SUDO:
        return ["sudo", "-n", *command]
    return ["pkexec", *command]


def _runtime_directory() -> str:
    """Get the XDG_RUNTIME_DIR, falling back to /tmp."""
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if runtime_dir and os.path.isdir(runtime_dir):
        return runtime_dir
    return "/tmp"


def cleanup_stale_credentials() -> int:
    """Remove stale temporary CIFS credential files from the runtime directory. Returns count removed."""
    runtime_dir = _runtime_directory()
    removed = 0

    try:
        runtime_path = Path(runtime_dir)
        for cred_file in runtime_path.glob("cifs-creds-*"):
            if not cred_file.is_file():
                continue
            try:
                cred_file.unlink()
                removed += 1
                LOGGER.info("Removed stale temporary credential file.")
            except OSError as exc:
                LOGGER.warning("Could not remove stale temporary credential file: %s", exc)
    except OSError as exc:
        LOGGER.warning("Could not inspect runtime directory for stale credential files: %s", exc)

    return removed


def _clean_cifs_options(value: str) -> list[str]:
    """Parse and clean CIFS mount options from a string."""
    value = (value or "").strip()
    if not value:
        return []

    if value.startswith("-o"):
        value = value[2:].lstrip()
        if value.startswith("="):
            value = value[1:].lstrip()

    result = []
    for item in value.split(","):
        item = item.strip()
        if item:
            result.append(item)
    return result


# ============================================================================
# Mount / Unmount operations
# ============================================================================

def do_mount(entry: dict, password: str, authenticate: bool = True, uid: int | None = None, gid: int | None = None):
    """Mount a single CIFS share. Returns (success: bool, message: str)."""
    host = entry.get("host") or ""
    share = entry.get("share") or ""
    path = entry.get("path") or ""

    if not host.strip():
        return False, "host is required"
    if not share.strip().lstrip("/"):
        return False, "share name is required"
    if not path.strip():
        return False, "mount path is required"

    mountpoint = os.path.expanduser(path)

    try:
        os.makedirs(mountpoint, exist_ok=True)
    except OSError as exc:
        return False, f"cannot create mount point: {exc}"

    cred_dir = _runtime_directory()
    cred_file = os.path.join(cred_dir, f"cifs-creds-{secrets.token_hex(16)}")
    username = entry.get("username") or os.environ.get("USER") or "guest"

    if uid is None:
        uid = os.getuid()
    if gid is None:
        gid = os.getgid()

    try:
        fd = os.open(cred_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as credentials:
            credentials.write(f"username={username}\n")
            credentials.write(f"password={password}\n")

        options = [
            f"credentials={cred_file}",
            f"uid={uid}",
            f"gid={gid}",
            "file_mode=0644",
            "dir_mode=0755",
        ]
        options.extend(_clean_cifs_options(entry.get("options", "")))

        command = [
            "/usr/bin/mount", "-t", "cifs",
            f"//{host.strip()}/{share.strip().lstrip('/')}",
            mountpoint,
            "-o", ",".join(options),
        ]

        if authenticate:
            command = _auth(command)

        result = subprocess.run(command, capture_output=True, text=True, timeout=90)

        if result.returncode != 0:
            message = (result.stderr or result.stdout or "mount failed").strip()
            return False, message

        return True, "mounted"

    except FileExistsError:
        return False, "temporary credentials file already exists"
    except PermissionError as exc:
        return False, f"cannot create credentials file: {exc}"
    except subprocess.TimeoutExpired:
        return False, "mount timed out"
    except OSError as exc:
        return False, f"mount error: {exc}"
    finally:
        try:
            os.unlink(cred_file)
        except OSError:
            pass


def do_unmount(entry: dict, authenticate: bool = True):
    """Unmount a single CIFS share. Returns (success: bool, message: str)."""
    path = entry.get("path") or ""
    if not path.strip():
        return False, "mount path is empty"

    mountpoint = os.path.expanduser(path)
    expected_source = f"//{entry.get('host', '').strip()}/{entry.get('share', '').strip().lstrip('/')}"
    topmost_source = _get_topmost_mount_source(mountpoint)

    if topmost_source is not None and topmost_source.lower() != expected_source.lower():
        return False, "another mount is covering this share's mount point"

    command = ["/usr/bin/umount", mountpoint]

    if authenticate:
        command = _auth(command)

    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            message = (result.stderr or result.stdout or "umount failed").strip()
            return False, message

        return True, "unmounted"

    except subprocess.TimeoutExpired:
        return False, "unmount timed out"
    except OSError as exc:
        return False, f"unmount error: {exc}"


def mount_entry_privileged(entry: dict, password: str) -> tuple[bool, str]:
    """Mount via the privileged helper (no sudo/pkexec wrapper)."""
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

    LOGGER.info(
        f"Invoking mount helper for '{entry.get('name')}' "
        f"(//{entry.get('host')}/{entry.get('share')} -> {entry.get('path')})"
    )

    try:
        result = subprocess.run(
            _auth([str(HELPER_BIN)]),
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        LOGGER.error(f"Mount helper invocation failed for '{entry.get('name')}': {exc}")
        return False, f"Helper invocation failed: {exc}"

    try:
        data = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        message = result.stderr.strip() or f"Helper exited with code {result.returncode}."
        LOGGER.error(f"Mount helper returned invalid output for '{entry.get('name')}': {message}")
        return False, message

    if data.get("ok"):
        return True, data.get("message", "Mounted.")
    return False, data.get("message", "Mount failed.")


# ============================================================================
# Batch operations
# ============================================================================

def _run_privileged_batch(mode: str, items: list[dict]):
    """Run a privileged batch mount/unmount operation. Returns list of results."""
    payload = {"mode": mode, "items": items}
    script = str(Path(__file__).resolve().parent / "mounthor.py")

    command = _auth([sys.executable, script, f"--{mode}"])
    timeout = max(120, (90 * len(items)) + 30)

    try:
        result = subprocess.run(
            command,
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return [{"id": item.get("id"), "ok": False, "message": "batch operation timed out"} for item in items]
    except OSError as exc:
        return [{"id": item.get("id"), "ok": False, "message": str(exc)} for item in items]

    if result.returncode != 0:
        message = (result.stderr or result.stdout or "privileged operation failed").strip()
        return [{"id": item.get("id"), "ok": False, "message": message} for item in items]

    try:
        response = json.loads(result.stdout)
        if isinstance(response, list):
            return response
    except json.JSONDecodeError:
        pass

    message = (result.stderr or result.stdout or "invalid batch response").strip()
    return [{"id": item.get("id"), "ok": False, "message": message} for item in items]


def _privileged_batch_main(mode: str) -> int:
    """Entry point for privileged batch operations (run as root)."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        print(json.dumps([], ensure_ascii=False))
        return 1

    if not isinstance(payload, dict):
        print(json.dumps([], ensure_ascii=False))
        return 1

    items = payload.get("items", [])
    if not isinstance(items, list):
        items = []

    results = []

    for item in items:
        if not isinstance(item, dict):
            continue

        entry = item.get("entry", {})
        if not isinstance(entry, dict):
            entry = {}

        item_id = item.get("id")

        if mode == "batch-mount":
            password = item.get("password", "") or ""
            uid = item.get("uid")
            gid = item.get("gid")

            try:
                uid = int(uid)
            except (TypeError, ValueError):
                uid = 0
            try:
                gid = int(gid)
            except (TypeError, ValueError):
                gid = 0

            ok, message = do_mount(entry, password, authenticate=False, uid=uid, gid=gid)

        elif mode == "batch-unmount":
            ok, message = do_unmount(entry, authenticate=False)

        else:
            ok = False
            message = "unknown batch operation"

        results.append({"id": item_id, "ok": ok, "message": message})

    print(json.dumps(results, ensure_ascii=False))
    return 0
