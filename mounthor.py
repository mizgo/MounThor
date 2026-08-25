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
APP_VERSION = "0.7.0"
APP_RELEASE_DATE = "24 August 2026"
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
        "password": (
            data.get("password")
            or ""
        ),
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
    }


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
) -> bool:

    if not path:

        return False

    target = os.path.realpath(
        os.path.expanduser(
            path
        )
    )

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

                mounted_path = (
                    _unescape_mount_field(
                        parts[1]
                    )
                )

                mounted_path = os.path.realpath(
                    mounted_path
                )

                if mounted_path == target:

                    return True

    except OSError:

        pass

    return False


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

    username = (
        entry.get("username")
        or os.environ.get("USER")
        or "guest"
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
                    )
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
                    )
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
                    )
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
                    )
                )
            )

            return

        if mount:

            saved_password = (
                row.entry.get(
                    "password",
                    "",
                )
                or ""
            )

            if not saved_password:

                self._ask_password(
                    row
                )

            else:

                self._mount(
                    row,
                    saved_password,
                )

        else:

            self._unmount(
                row
            )

    def _mount(
        self,
        row: MountRow,
        password: str,
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
                    )
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
                "Store the password in mounts.json "
                "for future mounts."
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

            if remember_row.get_active():

                row.entry["password"] = password

                try:

                    self.save_current_rows()

                except OSError as exc:

                    self.toast(
                        f"Could not save password: {exc}",
                        error=True,
                    )

                    return

            dialog.close()

            self._mount(
                row,
                password,
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

        rows = [
            row
            for row in selected_rows
            if not is_mounted(
                row.entry.get(
                    "path",
                    "",
                )
            )
        ]

        if not rows:

            self.toast(
                "All selected SMB shares are already connected."
            )

            return

        self._batch_active = True

        for row in rows:

            row.set_busy(
                True
            )

        self._collect_selected_batch_password(
            rows
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
                )
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

        rows = [
            row
            for row in self.rows.values()
            if not is_mounted(
                row.entry.get(
                    "path",
                    "",
                )
            )
        ]

        if not rows:

            self.toast(
                "All SMB shares are already connected."
            )

            return

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
                "Store the password in mounts.json "
                "for future mounts."
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
                )
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
                        )
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
            }

            cfg = load_config()

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

                        cfg["mounts"][index] = {
                            **mount,
                            **data,
                            "id": row.entry["id"],
                            "password": existing_password,
                        }

                        found = True

                        break

                if not found:

                    cfg["mounts"].append(
                        {
                            **data,
                            "id": row.entry["id"],
                            "password": "",
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
            )
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
                )
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
                )
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
            "<p>Preparing the project for GitHub publication.</p>"
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

        about.set_comments(
            f"<b>{APP_NAME} {APP_VERSION}</b>\n"
            f"Release date: {APP_RELEASE_DATE}\n\n"
            "A simple GTK4/libadwaita GUI for mounting SMB shares on Linux using the kernel's CIFS/SMB filesystem client.\n\n"
            "Save frequently used shares along with their mount settings and connect them with just a few clicks. For credentials, you can either save them in the application's configuration file or enter the password each time a share is mounted. You can also use your current Linux account name as the SMB username without storing it in the configuration.\n\n"
            "The app provides batch actions for connecting and disconnecting all shares or selected shares, allowing you to authenticate with your superuser password once and apply the action to multiple shares. Individual shares can also be configured to automatically mount when the application starts.\n\n"
            "The interface follows the system's GTK light and dark themes and respects the configured accent color."
            "The goal is to provide a simple and elegant GUI for managing CIFS/SMB mounts without maintaining persistent mounts through /etc/fstab or systemd units."
        )

        about.set_license_type(
            Gtk.License.GPL_3_0
        )

        about.present(
            self.win
        )


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

    _configure_logging()

    app = MounThorApp()

    return app.run(
        None
    )

if __name__ == "__main__":

    sys.exit(
        main()
    )