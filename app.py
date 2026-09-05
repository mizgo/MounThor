#!/usr/bin/env python3

"""Main application class for MounThor."""

import json
import logging
import os
import secrets
import sys
import threading

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .constants import APP_ID, APP_NAME, APP_RELEASE_DATE, APP_VERSION, APP_AUTHOR, POLKIT_RULE_FILE
from .config import configure_logging, load_config, save_config, get_effective_username
from .mounts import (
    cleanup_stale_credentials,
    do_mount,
    do_unmount,
    get_mount_source,
    is_mounted,
)
from .credentials import secure_storage_available, secure_load_password, secure_store_password, secure_delete_password
from .gtk_helpers import (
    get_text,
    install_enter_action,
    make_action_bar,
    make_dialog_view,
    make_entry_listbox,
    make_mount_listbox,
    install_mount_list_css,
)
from .rows import MountRow

LOGGER = logging.getLogger("mounthor")


class MounThorApp(Adw.Application):

    def __init__(self):
        super().__init__(application_id=APP_ID)
        self.win = None
        self.overlay = None
        self.rows_list = None
        self.rows = {}
        self._selection_anchor_id = None
        self._batch_active = False

    def do_shutdown(self):
        LOGGER.info("Application exiting.")
        Gio.Application.do_shutdown(self)

    # ------------------------------------------------------------------------
    # Activation
    # ------------------------------------------------------------------------

    def do_activate(self):
        cleaned_credentials = cleanup_stale_credentials()
        if cleaned_credentials:
            LOGGER.info(
                "Cleaned up %d stale temporary credential file(s) at startup.",
                cleaned_credentials,
            )

        LOGGER.info("Application started.")

        if self.win is not None:
            self.win.present()
            return

        self.win = Adw.ApplicationWindow(application=self)
        self.win.set_default_size(540, 680)
        self.win.set_title(APP_NAME)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.win.set_content(root)

        # --------------------------------------------------------------------
        # Header
        # --------------------------------------------------------------------

        header = Adw.HeaderBar()

        menu = Gio.Menu()
        menu.append("Connect All", "app.connect-all")
        menu.append("Disconnect All", "app.disconnect-all")
        menu.append("Clear Selection", "app.deselect-all")
        menu.append("About", "app.about")

        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.set_tooltip_text("Main menu")
        menu_button.set_menu_model(menu)
        header.pack_start(menu_button)

        title = Adw.WindowTitle(title=APP_NAME, subtitle="CIFS mount manager")
        header.set_title_widget(title)

        add_button = Gtk.Button.new_from_icon_name("list-add-symbolic")
        add_button.set_tooltip_text("Add SMB share")
        add_button.connect("clicked", self._on_add_clicked)
        header.pack_end(add_button)

        root.append(header)

        # --------------------------------------------------------------------
        # Actions
        # --------------------------------------------------------------------

        connect_action = Gio.SimpleAction.new("connect-all", None)
        connect_action.connect("activate", self._on_connect_all_action)
        self.add_action(connect_action)

        disconnect_action = Gio.SimpleAction.new("disconnect-all", None)
        disconnect_action.connect("activate", self._on_disconnect_all_action)
        self.add_action(disconnect_action)

        deselect_all_action = Gio.SimpleAction.new("deselect-all", None)
        deselect_all_action.connect("activate", self._on_deselect_all_action)
        self.add_action(deselect_all_action)

        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self._on_about_action)
        self.add_action(about_action)

        # --------------------------------------------------------------------
        # Toast overlay
        # --------------------------------------------------------------------

        self.overlay = Adw.ToastOverlay()
        root.append(self.overlay)

        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.rows_list = make_mount_listbox()
        self.rows_list.add_css_class("smb-mount-list")
        install_mount_list_css()
        scroll.set_child(self.rows_list)
        self.overlay.set_child(scroll)

        self.rebuild_rows()
        self.win.present()

        GLib.idle_add(self._automount_on_startup)

    def _on_add_clicked(self, *_args):
        self.entry_dialog(None)

    # =========================================================================
    # Toast
    # =========================================================================

    def toast(self, message: str, error: bool = False):
        if self.overlay is None:
            return

        toast = Adw.Toast()
        toast.set_title(message)
        toast.set_timeout(5)
        if error:
            toast.set_priority(Adw.ToastPriority.HIGH)
        self.overlay.add_toast(toast)

    # =========================================================================
    # Rows management
    # =========================================================================

    def rebuild_rows(self):
        if self.rows_list is None:
            return

        child = self.rows_list.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self.rows_list.remove(child)
            child = next_child

        selected_ids = {entry_id for entry_id, row in self.rows.items() if row.is_selected()}
        self.rows = {}

        cfg = load_config()
        mounts = cfg.get("mounts", [])
        valid_mounts = []

        for entry in mounts:
            if not isinstance(entry, dict):
                continue
            if not entry.get("id"):
                entry["id"] = secrets.token_hex(8)
            valid_mounts.append(entry)

        if not valid_mounts:
            empty = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL, spacing=12,
                halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                margin_top=48, margin_bottom=48, margin_start=24, margin_end=24,
            )
            icon = Gtk.Image.new_from_icon_name("folder-remote-symbolic")
            icon.set_pixel_size(48)
            label = Gtk.Label(
                label="No SMB shares yet.\nClick + to add one.",
                justify=Gtk.Justification.CENTER, wrap=True,
            )
            empty.append(icon)
            empty.append(label)
            self.rows_list.append(empty)
            return

        for entry in valid_mounts:
            row = MountRow(self, entry)
            self.rows[entry["id"]] = row
            self.rows_list.append(row)

            if entry["id"] in selected_ids:
                row.set_selected(True)

            row.set_mounted(is_mounted(
                entry.get("path", ""),
                entry.get("host"),
                entry.get("share"),
            ))

    def select_mount_row(self, row: MountRow):
        state = row._click_controller.get_current_event_state()
        has_shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        row_id = row.entry.get("id")
        ordered_rows = list(self.rows.values())

        if has_shift and self._selection_anchor_id:
            anchor_index = next(
                (index for index, candidate in enumerate(ordered_rows)
                 if candidate.entry.get("id") == self._selection_anchor_id),
                None,
            )
            if anchor_index is not None:
                row_index = ordered_rows.index(row)
                start = min(anchor_index, row_index)
                end = max(anchor_index, row_index)
                for candidate in ordered_rows[start:end + 1]:
                    candidate.set_selected(True)
                return

        row.set_selected(not row.is_selected())
        self._selection_anchor_id = row_id

    def selected_mount_rows(self) -> list[MountRow]:
        return [row for row in self.rows.values() if row.is_selected()]

    def deselect_all(self):
        for row in self.rows.values():
            row.set_selected(False)
        self._selection_anchor_id = None

    def save_current_rows(self):
        cfg = {"mounts": [row.entry for row in self.rows.values()]}
        save_config(cfg)

    def refresh_mount_states(self):
        for row in self.rows.values():
            row.set_mounted(is_mounted(
                row.entry.get("path", ""),
                row.entry.get("host"),
                row.entry.get("share"),
            ))

    # =========================================================================
    # Automount on application startup
    # =========================================================================

    def _automount_on_startup(self):
        if self._batch_active:
            return False

        rows = [
            row for row in self.rows.values()
            if (
                bool(row.entry.get("automount", False))
                and not is_mounted(
                    row.entry.get("path", ""),
                    row.entry.get("host"),
                    row.entry.get("share"),
                )
            )
        ]

        if not rows:
            return False

        self._batch_active = True
        for row in rows:
            row.set_busy(True)

        self._collect_batch_passwords(rows, 0, {})
        return False

    # =========================================================================
    # Mount / unmount
    # =========================================================================

    def toggle_mount(self, row: MountRow, mount: bool):
        if self._batch_active:
            row.set_mounted(is_mounted(
                row.entry.get("path", ""),
                row.entry.get("host"),
                row.entry.get("share"),
            ))
            return

        if mount:
            path = row.entry.get("path", "")
            host = row.entry.get("host", "")
            share = row.entry.get("share", "")

            if is_mounted(path) and not is_mounted(path, host, share):
                existing_source = get_mount_source(path)
                self._show_replace_dialog(row, existing_source or "")
                return

            self._ask_password(row)
        else:
            self._unmount(row)

    def _show_replace_dialog(self, row: MountRow, existing_source: str, on_after_close=None):
        host = row.entry.get("host", "")
        share = row.entry.get("share", "")
        path = row.entry.get("path", "")

        dialog = Adw.AlertDialog()
        dialog.set_heading("Mount path already in use")
        dialog.set_body(
            f"The path ``{path}``\n"
            "is currently mounted as\n"
            f"{existing_source}.\n\n"
            "Replace it with:\n"
            f"//{host}/{share}?"
        )

        dialog.add_response("keep", "Keep Original")
        dialog.set_close_response("keep")
        dialog.add_response("replace", "Replace")
        dialog.set_default_response("replace")
        dialog.set_response_appearance("replace", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_alert, response):
            if response == "replace":
                self._replace_mount(row, on_after_close=on_after_close)
            else:
                row.set_mounted(is_mounted(
                    row.entry.get("path", ""),
                    row.entry.get("host"),
                    row.entry.get("share"),
                ))
                if on_after_close:
                    GLib.idle_add(on_after_close)

        dialog.connect("response", on_response)
        dialog.present(self.win)

    def _replace_mount(self, row: MountRow, on_after_close=None):
        path = row.entry.get("path", "")
        existing_source = get_mount_source(path)

        if not existing_source:
            self.toast("Could not identify the existing mount.", error=True)
            return

        without_scheme = existing_source[2:] if existing_source.startswith("//") else existing_source
        parts = without_scheme.split("/", 1)

        if len(parts) != 2 or not parts[0] or not parts[1]:
            self.toast("Could not parse existing mount source.", error=True)
            return

        old_host, old_share = parts[0], parts[1].lstrip("/")
        temp_entry = {"path": path, "host": old_host, "share": old_share}

        def worker():
            ok, message = do_unmount(temp_entry)
            GLib.idle_add(self._replace_mount_done, row, ok, message, on_after_close)

        threading.Thread(target=worker, name="smb-replace", daemon=True).start()

    def _replace_mount_done(self, row: MountRow, ok: bool, message: str, on_after_close=None):
        if not ok:
            LOGGER.error(
                "Unmount failed during replace for %s/%s: %s",
                row.entry.get("host"), row.entry.get("share"), message,
            )
            self.toast(f"Failed to unmount existing share: {message}", error=True)
            if on_after_close:
                GLib.idle_add(on_after_close)
            return

        LOGGER.info(
            "Unmounted existing share, proceeding to mount //%s/%s",
            row.entry.get("host"), row.entry.get("share"),
        )

        target = os.path.realpath(os.path.expanduser(row.entry.get("path", "")))
        for other in self.rows.values():
            if other is row:
                continue
            o_path = other.entry.get("path", "")
            if not o_path:
                continue
            if os.path.realpath(os.path.expanduser(o_path)) != target:
                continue
            other.set_mounted(is_mounted(
                o_path, other.entry.get("host"), other.entry.get("share")
            ))

        self._ask_password(row)

    def _mount(self, row: MountRow, password: str, credential_storage=None):
        row.set_busy(True)

        def worker():
            ok, message = do_mount(row.entry, password)
            GLib.idle_add(
                self._mount_done, row, ok, message, password, credential_storage
            )

        threading.Thread(target=worker, name="smb-mount", daemon=True).start()

    def _mount_done(self, row, ok, message, password, credential_storage):
        row.set_busy(False)

        if ok:
            LOGGER.info(
                "Mounted share: //%s/%s",
                row.entry.get("host", ""), row.entry.get("share", ""),
            )

            if credential_storage is not None:
                try:
                    if credential_storage == "secret-service":
                        secure_store_password(
                            row.entry["host"],
                            row.entry["share"],
                            get_effective_username(row.entry),
                            password,
                        )
                        row.entry["password"] = ""
                    elif credential_storage == "plaintext":
                        row.entry["password"] = password
                    else:
                        secure_delete_password(
                            row.entry["host"],
                            row.entry["share"],
                            get_effective_username(row.entry),
                        )
                        row.entry["password"] = ""

                    row.entry["credential_storage"] = credential_storage
                    self.save_current_rows()

                except Exception as exc:
                    LOGGER.error("Could not save SMB credential: %s", exc)
                    self.toast(f"Mounted, but could not save password: {exc}", error=True)

            row.set_mounted(True)
            self.toast(f"Mounted {row.entry['name']}")
        else:
            LOGGER.error(
                "Mount failed for //%s/%s: %s",
                row.entry.get("host", ""), row.entry.get("share", ""), message,
            )
            row.set_mounted(False)
            self.toast(f"Mount failed: {message}", error=True)

        return False

    def _unmount(self, row):
        row.set_busy(True)

        def worker():
            ok, message = do_unmount(row.entry)
            GLib.idle_add(self._unmount_done, row, ok, message)

        threading.Thread(target=worker, name="smb-umount", daemon=True).start()

    def _unmount_done(self, row, ok, message):
        row.set_busy(False)

        if ok:
            row.set_mounted(False)
            self.toast(f"Unmounted {row.entry['name']}")
        else:
            row.set_mounted(is_mounted(
                row.entry.get("path", ""),
                row.entry.get("host"),
                row.entry.get("share"),
            ))
            self.toast(f"Unmount failed: {message}", error=True)

        return False

    # =========================================================================
    # Password dialog
    # =========================================================================

    def _ask_password(self, row):
        entry = row.entry

        def migrate_plaintext_password(password):
            try:
                if not secure_storage_available():
                    return False
                secure_store_password(
                    entry["host"], entry["share"],
                    get_effective_username(entry), password,
                )
                entry["password"] = ""
                entry["credential_storage"] = "secret-service"
                self.save_current_rows()
                return True
            except Exception as exc:
                LOGGER.warning(
                    "Could not migrate SMB password to Secret Service: %s", exc,
                )
                return False

        def ask_migrate_plaintext_password(password):
            alert = Adw.AlertDialog(
                heading="Password stored without encryption",
                body=(
                    "This SMB password is currently "
                    "stored unencrypted in MounThor's configuration. "
                    "Secure Credential Storage is available and can be used instead."
                ),
            )
            alert.add_response("keep", "Keep as is")
            alert.add_response("migrate", "Move to Secure Storage")
            alert.set_default_response("migrate")

            def on_response(_alert, response):
                if response == "migrate":
                    self._mount(row, password, "secret-service")
                elif response == "keep":
                    self._mount(row, password, None)

            alert.connect("response", on_response)
            alert.present(self.win)

        # Try to load from secret service
        if entry.get("credential_storage") == "secret-service":
            try:
                password = secure_load_password(
                    entry["host"], entry["share"],
                    get_effective_username(entry),
                )
            except Exception as exc:
                LOGGER.warning("Could not load SMB password from Secret Service: %s", exc)
                password = None

            if password:
                self._mount(row, password)
                return

        # Try plaintext stored password
        if entry.get("credential_storage") == "plaintext":
            password = entry.get("password") or ""
            if password:
                if secure_storage_available():
                    ask_migrate_plaintext_password(password)
                else:
                    self._mount(row, password, None)
                return

        # Show password dialog
        dialog = Adw.Dialog()
        dialog.set_title("SMB password")
        dialog.set_content_width(440)
        dialog.set_content_height(320)

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12,
            margin_top=18, margin_bottom=18, margin_start=18, margin_end=18,
        )

        info = Gtk.Label(
            label=f"Enter the password for\n//{entry['host']}/{entry['share']}",
            wrap=True, halign=Gtk.Align.START,
        )
        content.append(info)

        fields = make_entry_listbox()
        password_row = Adw.PasswordEntryRow(title="Password")
        fields.append(password_row)
        content.append(fields)

        remember_list = make_entry_listbox()
        remember_row = Adw.SwitchRow(
            title="Remember password",
            subtitle="Store the password for future mounts.",
        )
        remember_list.append(remember_row)
        content.append(remember_list)

        action_bar, cancel_button, connect_button = make_action_bar("Cancel", "Connect")

        def on_cancel(_button):
            row.set_mounted(False)
            dialog.close()

        def complete_connect(password, credential_storage):
            dialog.close()
            self._mount(row, password, credential_storage)

        def ask_insecure_storage(password):
            alert = Adw.AlertDialog(
                heading="Secure Credential Storage unavailable",
                body=(
                    "Secure Credential Storage is not available on this system. "
                    "You can continue without saving the password, or save it unencrypted "
                    "in MounThor's configuration."
                ),
            )
            alert.add_response("cancel", "Cancel")
            alert.add_response("none", "Don't remember")
            alert.add_response("plaintext", "Save without encryption")
            alert.set_default_response("none")
            alert.set_close_response("cancel")
            alert.set_response_appearance("plaintext", Adw.ResponseAppearance.DESTRUCTIVE)

            def on_response(_alert, response):
                if response == "none":
                    complete_connect(password, "none")
                elif response == "plaintext":
                    complete_connect(password, "plaintext")

            alert.connect("response", on_response)
            alert.present(self.win)

        def on_connect(_button):
            password = get_text(password_row)
            if not password:
                self.toast("Password cannot be empty.", error=True)
                return

            if not remember_row.get_active():
                complete_connect(password, "none")
                return

            try:
                if secure_storage_available():
                    complete_connect(password, "secret-service")
                else:
                    ask_insecure_storage(password)
            except Exception:
                ask_insecure_storage(password)

        cancel_button.connect("clicked", on_cancel)
        connect_button.connect("clicked", on_connect)

        view = make_dialog_view(content, action_bar)
        dialog.set_child(view)
        install_enter_action(dialog, connect_button)
        dialog.present(self.win)

    # =========================================================================
    # Connect / Disconnect Selected
    # =========================================================================

    def connect_selected(self):
        if self._batch_active:
            return

        selected_rows = self.selected_mount_rows()
        if not selected_rows:
            self.toast("No SMB shares are selected.")
            return

        clean_rows, conflict_rows = [], []
        for row in selected_rows:
            path = row.entry.get("path", "")
            host = row.entry.get("host", "")
            share = row.entry.get("share", "")

            if is_mounted(path, host, share):
                continue
            elif is_mounted(path):
                conflict_rows.append(row)
            else:
                clean_rows.append(row)

        if not clean_rows and not conflict_rows:
            self.toast("All selected SMB shares are already connected.")
            return

        duplicate_groups = self._find_duplicate_paths(clean_rows + conflict_rows)
        if duplicate_groups:
            for row in selected_rows:
                row.set_mounted(is_mounted(
                    row.entry.get("path", ""),
                    row.entry.get("host"),
                    row.entry.get("share"),
                ))
            self._show_duplicate_path_dialog(duplicate_groups)
            return

        if clean_rows:
            self._batch_active = True
            for row in clean_rows:
                row.set_busy(True)
            self._collect_selected_batch_password(clean_rows)

        if conflict_rows:
            self._show_conflict_dialogs(conflict_rows, 0)

    def _start_connect_all_clean(self, clean_rows):
        if not clean_rows:
            return

        self._batch_active = True
        for row in clean_rows:
            row.set_busy(True)
        self._collect_batch_passwords(clean_rows, 0, {})

    def _show_conflict_dialogs(self, conflict_rows: list[MountRow], index: int, on_after_close=None):
        if index >= len(conflict_rows):
            if on_after_close:
                GLib.idle_add(on_after_close)
            return

        row = conflict_rows[index]
        path = row.entry.get("path", "")
        existing_source = get_mount_source(path) or ""

        def on_next():
            GLib.idle_add(self._show_conflict_dialogs, conflict_rows, index + 1, on_after_close)

        self._show_replace_dialog(row, existing_source, on_after_close=on_next)

    def _find_duplicate_paths(self, rows: list[MountRow]):
        groups = {}
        for row in rows:
            path = row.entry.get("path", "")
            if not path:
                continue
            key = os.path.realpath(os.path.expanduser(path))
            groups.setdefault(key, []).append(row)
        return {key: group for key, group in groups.items() if len(group) > 1}

    def _show_duplicate_path_dialog(self, groups: dict):
        lines = ["Multiple SMB shares"]
        for key in sorted(groups):
            names = " & ".join(
                f"{row.entry.get('name', 'Unnamed')}" for row in groups[key]
            )
            lines.append(f"{names}\n")
            lines.append("are trying to connect to the same mount path:")
            lines.append(f"``{key}``")

        lines.extend(["", "This operation cannot be performed."])

        dialog = Adw.AlertDialog()
        dialog.set_heading("Duplicate mount paths")
        dialog.set_body("\n".join(lines))
        dialog.add_response("ok", "OK")
        dialog.set_close_response("ok")
        dialog.present(self.win)

    def disconnect_selected(self):
        if self._batch_active:
            return

        selected_rows = self.selected_mount_rows()
        if not selected_rows:
            self.toast("No SMB shares are selected.")
            return

        rows = [
            row for row in selected_rows
            if is_mounted(
                row.entry.get("path", ""),
                row.entry.get("host"),
                row.entry.get("share"),
            )
        ]

        if not rows:
            self.toast("No selected SMB shares are connected.")
            return

        self._batch_active = True
        for row in rows:
            row.set_busy(True)

        self._start_disconnect_batch(rows, "Disconnect Selected")

    def _collect_selected_batch_password(self, rows: list[MountRow]):
        passwords = {
            row.entry["id"]: (row.entry.get("password", "") or "")
            for row in rows
        }

        def collect_next(index: int):
            if index >= len(rows):
                self._start_connect_all(rows, passwords, "Connect Selected")
                return

            row = rows[index]
            entry = row.entry
            saved_password = entry.get("password", "") or ""

            if saved_password:
                passwords[entry["id"]] = saved_password
                collect_next(index + 1)
                return

            def accepted(password):
                passwords[entry["id"]] = password
                collect_next(index + 1)

            def cancelled():
                for candidate in rows:
                    candidate.set_busy(False)
                self._batch_active = False
                self.refresh_mount_states()
                self.toast("Connect Selected cancelled.", error=True)

            self._ask_batch_password(
                entry, accepted, cancelled, index + 1, len(rows),
            )

        collect_next(0)

    def _ask_selected_batch_password(self, entry, on_accept, on_cancel, missing_count):
        dialog = Adw.Dialog()
        dialog.set_title("Password required")
        dialog.set_content_width(440)
        dialog.set_content_height(320)

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12,
            margin_top=18, margin_bottom=18, margin_start=18, margin_end=18,
        )

        info = Gtk.Label(
            label=(
                "Enter one password for the selected batch.\n\n"
                f"It will be used for {missing_count} selected share(s) "
                "without a saved password.\n\n"
                f'First share: "{entry.get("name", "Unnamed share")}"'
            ),
            wrap=True, halign=Gtk.Align.START,
        )
        content.append(info)

        fields = make_entry_listbox()
        password_row = Adw.PasswordEntryRow(title="Password")
        fields.append(password_row)
        content.append(fields)

        action_bar, cancel_button, connect_button = make_action_bar("Cancel", "Continue")

        def on_cancel_clicked(_button):
            dialog.close()
            on_cancel()

        def on_connect_clicked(_button):
            password = get_text(password_row)
            if not password:
                self.toast("Password cannot be empty.", error=True)
                return
            dialog.close()
            on_accept(password)

        cancel_button.connect("clicked", on_cancel_clicked)
        connect_button.connect("clicked", on_connect_clicked)

        dialog.set_child(make_dialog_view(content, action_bar))
        install_enter_action(dialog, connect_button)
        dialog.present(self.win)

    # =========================================================================
    # Connect All
    # =========================================================================

    def _on_connect_all_action(self, _action, _parameter):
        if self._batch_active:
            return

        clean_rows, conflict_rows = [], []
        for row in self.rows.values():
            path = row.entry.get("path", "")
            host = row.entry.get("host", "")
            share = row.entry.get("share", "")

            if is_mounted(path, host, share):
                continue
            if is_mounted(path):
                conflict_rows.append(row)
            else:
                clean_rows.append(row)

        rows = clean_rows + conflict_rows
        if not rows:
            self.toast("All SMB shares are already connected.")
            return

        duplicate_groups = self._find_duplicate_paths(rows)
        if duplicate_groups:
            self._show_duplicate_path_dialog(duplicate_groups)
            return

        if conflict_rows:
            self._show_conflict_dialogs(
                conflict_rows, 0,
                on_after_close=lambda: self._start_connect_all_clean(clean_rows),
            )
            return

        self._start_connect_all_clean(clean_rows)

    def _collect_batch_passwords(self, rows: list[MountRow], index: int, passwords: dict):
        if index >= len(rows):
            self._start_connect_all(rows, passwords)
            return

        row = rows[index]
        entry = row.entry
        saved_password = entry.get("password", "") or ""

        if saved_password:
            passwords[entry["id"]] = saved_password
            self._collect_batch_passwords(rows, index + 1, passwords)
            return

        def accepted(password):
            passwords[entry["id"]] = password
            self._collect_batch_passwords(rows, index + 1, passwords)

        def cancelled():
            for candidate in rows:
                candidate.set_busy(False)
            self._batch_active = False
            self.refresh_mount_states()
            self.toast("Connect All cancelled.", error=True)

        self._ask_batch_password(
            entry, accepted, cancelled, index + 1, len(rows),
        )

    def _ask_batch_password(self, entry, on_accept, on_cancel, number, total):
        dialog = Adw.Dialog()
        dialog.set_title("Password required")
        dialog.set_content_width(440)
        dialog.set_content_height(320)

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12,
            margin_top=18, margin_bottom=18, margin_start=18, margin_end=18,
        )

        info = Gtk.Label(
            label=(
                f"Share {number} of {total}\n\n"
                f'Enter the password for\n"{entry.get("name", "Unnamed share")}"\n\n'
                f"//{entry.get('host', '')}/{entry.get('share', '')}"
            ),
            wrap=True, halign=Gtk.Align.START,
        )
        content.append(info)

        fields = make_entry_listbox()
        password_row = Adw.PasswordEntryRow(title="Password")
        fields.append(password_row)
        content.append(fields)

        remember_list = make_entry_listbox()
        remember_row = Adw.SwitchRow(
            title="Remember password",
            subtitle="Store the password for future mounts.",
        )
        remember_list.append(remember_row)
        content.append(remember_list)

        action_bar, cancel_button, connect_button = make_action_bar("Cancel", "Continue")

        def on_cancel_clicked(_button):
            dialog.close()
            on_cancel()

        def on_connect_clicked(_button):
            password = get_text(password_row)
            if not password:
                self.toast("Password cannot be empty.", error=True)
                return

            if remember_row.get_active():
                entry["password"] = password
                try:
                    self.save_current_rows()
                except OSError as exc:
                    self.toast(f"Could not save password: {exc}", error=True)
                    return

            dialog.close()
            on_accept(password)

        cancel_button.connect("clicked", on_cancel_clicked)
        connect_button.connect("clicked", on_connect_clicked)

        dialog.set_child(make_dialog_view(content, action_bar))
        install_enter_action(dialog, connect_button)
        dialog.present(self.win)

    def _start_connect_all(self, rows, passwords, operation_name="Connect All"):
        items = []
        for row in rows:
            entry = dict(row.entry)
            if not (entry.get("username") or "").strip():
                entry["username"] = os.environ.get("USER") or ""
            items.append({
                "id": row.entry["id"],
                "entry": entry,
                "password": passwords.get(row.entry["id"], ""),
                "uid": os.getuid(),
                "gid": os.getgid(),
            })

        self.toast(f"{operation_name}: connecting {len(rows)} share(s)…")

        def worker():
            from .mounts import _run_privileged_batch  # noqa: F811 - local import to avoid circular
            results = _run_privileged_batch("batch-mount", items)
            GLib.idle_add(self._connect_all_done, rows, results, operation_name)

        threading.Thread(target=worker, name="smb-connect-all", daemon=True).start()

    def _connect_all_done(self, rows, results, operation_name="Connect All"):
        result_map = {
            result.get("id"): result
            for result in results if isinstance(result, dict)
        }

        succeeded = failed = 0
        for row in rows:
            result = result_map.get(row.entry["id"])
            if result is None:
                ok, message = False, "no result returned"
            else:
                ok = bool(result.get("ok", False))
                message = (result.get("message", "mount failed") or "mount failed")

            row.set_busy(False)
            row.set_mounted(ok)

            if ok:
                succeeded += 1
            else:
                failed += 1

        LOGGER.info(
            "%s finished: %d succeeded, %d failed.",
            operation_name, succeeded, failed,
        )

        self._batch_active = False

        if operation_name == "Connect Selected":
            for row in self.selected_mount_rows():
                row.set_selected(False)

        if failed:
            self.toast(
                f"{operation_name} finished: {succeeded} connected, {failed} failed.",
                error=True,
            )
        else:
            self.toast(f"Connected {succeeded} share(s).")

        return False

    # =========================================================================
    # Disconnect All
    # =========================================================================

    def _start_disconnect_batch(self, rows, operation_name="Disconnect All"):
        self.toast(f"{operation_name}: disconnecting {len(rows)} share(s)…")

        items = [
            {"id": row.entry["id"], "entry": dict(row.entry)}
            for row in rows
        ]

        def worker():
            from .mounts import _run_privileged_batch  # noqa: F811 - local import to avoid circular
            results = _run_privileged_batch("batch-unmount", items)
            GLib.idle_add(self._disconnect_all_done, rows, results, operation_name)

        threading.Thread(target=worker, name="smb-disconnect-batch", daemon=True).start()

    def _on_disconnect_all_action(self, _action, _parameter):
        if self._batch_active:
            return

        rows = [
            row for row in self.rows.values()
            if is_mounted(
                row.entry.get("path", ""),
                row.entry.get("host"),
                row.entry.get("share"),
            )
        ]

        if not rows:
            self.toast("No SMB shares are connected.")
            return

        self._batch_active = True
        for row in rows:
            row.set_busy(True)

        self.toast(f"Disconnecting {len(rows)} share(s)…")

        items = [
            {"id": row.entry["id"], "entry": dict(row.entry)}
            for row in rows
        ]

        def worker():
            from .mounts import _run_privileged_batch  # noqa: F811 - local import to avoid circular
            results = _run_privileged_batch("batch-unmount", items)
            GLib.idle_add(self._disconnect_all_done, rows, results)

        threading.Thread(target=worker, name="smb-disconnect-all", daemon=True).start()

    def _disconnect_all_done(self, rows, results, operation_name="Disconnect All"):
        result_map = {
            result.get("id"): result
            for result in results if isinstance(result, dict)
        }

        succeeded = failed = 0
        for row in rows:
            result = result_map.get(row.entry["id"])
            if result is None:
                ok = False
            else:
                ok = bool(result.get("ok", False))

            row.set_busy(False)

            if ok:
                row.set_mounted(False)
                succeeded += 1
            else:
                row.set_mounted(is_mounted(
                    row.entry.get("path", ""),
                    row.entry.get("host"),
                    row.entry.get("share"),
                ))
                failed += 1

        LOGGER.info(
            "%s finished: %d succeeded, %d failed.",
            operation_name, succeeded, failed,
        )

        self._batch_active = False

        if operation_name == "Disconnect Selected":
            for row in self.selected_mount_rows():
                row.set_selected(False)

        if failed:
            self.toast(
                f"{operation_name} finished: {succeeded} disconnected, {failed} failed.",
                error=True,
            )
        else:
            self.toast(f"Disconnected {succeeded} share(s).")

        return False

    # =========================================================================
    # Add / edit / duplicate
    # =========================================================================

    def entry_dialog(self, row=None, duplicate_entry=None):
        if duplicate_entry is not None:
            entry = duplicate_entry
            is_duplicate = True
        else:
            entry = row.entry if row is not None else None
            is_duplicate = False

        dialog = Adw.Dialog()

        if is_duplicate:
            dialog.set_title("Duplicate SMB share")
        elif entry:
            dialog.set_title("Edit share")
        else:
            dialog.set_title("Add SMB share")

        dialog.set_content_width(500)
        dialog.set_content_height(620)

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12,
            margin_top=18, margin_bottom=18, margin_start=18, margin_end=18,
        )

        description = Gtk.Label(
            label="Configure the SMB share and local mount point.",
            wrap=True, halign=Gtk.Align.START,
        )
        content.append(description)

        fields = make_entry_listbox()

        # Name
        name_row = Adw.EntryRow(title="Name")
        initial_name = ""
        if entry:
            initial_name = entry.get("name", "")
        if is_duplicate and initial_name:
            initial_name = f"{initial_name} Copy"
        elif is_duplicate:
            initial_name = "Copy"
        name_row.set_text(initial_name)
        name_row.set_tooltip_text(
            "Friendly name displayed in the mount list of this Tool."
        )
        fields.append(name_row)

        # Host
        host_row = Adw.EntryRow(title="Host")
        host_row.set_text(entry.get("host", "") if entry else "")
        host_row.set_tooltip_text(
            "IP address or hostname, for example: 192.168.1.12."
        )
        fields.append(host_row)

        # Share
        share_row = Adw.EntryRow(title="Share")
        share_row.set_text(entry.get("share", "") if entry else "")
        share_row.set_tooltip_text(
            "SMB share name, for example: movies"
        )
        fields.append(share_row)

        # Path
        path_row = Adw.EntryRow(title="Mount path")
        path_row.set_text(entry.get("path", "") if entry else "")
        path_row.set_tooltip_text(
            "Local mount path, for example: ~/mnt/movies"
        )
        fields.append(path_row)

        # Username
        username_row = Adw.EntryRow(title="Username")
        username_row.set_text(entry.get("username", "") if entry else "")
        username_row.set_tooltip_text(
            "Optional SMB username. Empty uses the current Linux user."
        )
        fields.append(username_row)

        # Options
        options_row = Adw.EntryRow(title="CIFS options")
        options_row.set_text(entry.get("options", "") if entry else "")
        options_row.set_tooltip_text(
            "Optional comma-separated options, e.g. vers=3.1.1"
        )
        fields.append(options_row)

        # Automount switches
        automount_list = make_entry_listbox()
        automount_row = Adw.SwitchRow(
            title="Automount with application startup",
            subtitle="Automatically mount this share when MounThor starts.",
        )
        automount_row.set_active(bool(entry.get("automount", False)) if entry else False)
        automount_list.append(automount_row)

        system_automount_row = Adw.SwitchRow(
            title="Automount with system startup",
            subtitle=(
                "Automatically mount this share at login, even when MounThor is not running."
            ),
        )
        system_automount_row.set_active(bool(entry.get("system_automount", False)) if entry else False)
        automount_list.append(system_automount_row)

        content.append(fields)
        content.append(automount_list)

        password_info = Gtk.Label(
            label="Password is not configured here. It is set when mounting.",
            wrap=True, halign=Gtk.Align.START,
        )
        password_info.add_css_class("dim-label")
        content.append(password_info)

        action_bar, cancel_button, save_button = make_action_bar("Cancel", "Save")

        def on_cancel(_button):
            dialog.close()

        def on_save(_button):
            host = get_text(host_row).strip()
            share = get_text(share_row).strip().lstrip("/")
            path = get_text(path_row).strip()
            username = get_text(username_row).strip()
            options = get_text(options_row).strip()
            name = get_text(name_row).strip()

            if not host:
                self.toast("Host is required.", error=True)
                return
            if not share:
                self.toast("Share name is required.", error=True)
                return
            if not path:
                self.toast("Mount path is required.", error=True)
                return

            if not name:
                name = f"{host}/{share}"

            from .mounts import _clean_cifs_options  # noqa: F811 - local import
            normalized_options = ",".join(_clean_cifs_options(options))

            data = {
                "name": name, "host": host, "share": share, "path": path,
                "username": username, "options": normalized_options,
                "automount": automount_row.get_active(),
                "system_automount": system_automount_row.get_active(),
            }

            cfg = load_config()
            old_credential_storage = "none"
            old_effective_username = ""
            credential_identity_changed = False
            old_entry = None

            if row is not None and not is_duplicate:
                old_entry = next(
                    (mount for mount in cfg["mounts"]
                     if isinstance(mount, dict) and mount.get("id") == row.entry["id"]),
                    None,
                )

            if row is not None and not is_duplicate:
                found = False
                for index, mount in enumerate(cfg["mounts"]):
                    if isinstance(mount, dict) and mount.get("id") == row.entry["id"]:
                        existing_password = mount.get("password", "") or ""
                        old_effective_username = (
                            get_effective_username(old_entry) if old_entry else ""
                        )

                        new_entry_for_identity = {**data}
                        new_effective_username = get_effective_username(new_entry_for_identity)

                        credential_identity_changed = (
                            old_entry is not None and (
                                old_entry.get("host", "") != data["host"]
                                or old_entry.get("share", "") != data["share"]
                                or old_effective_username != new_effective_username
                            )
                        )

                        existing_credential_storage = (
                            mount.get("credential_storage")
                            or ("plaintext" if existing_password else "none")
                        )
                        old_credential_storage = existing_credential_storage

                        if credential_identity_changed:
                            new_password, new_credential_storage = "", "none"
                        else:
                            new_password = existing_password
                            new_credential_storage = existing_credential_storage

                        cfg["mounts"][index] = {
                            **mount, **data, "id": row.entry["id"],
                            "password": new_password,
                            "credential_storage": new_credential_storage,
                        }
                        found = True
                        break

                if not found:
                    cfg["mounts"].append({
                        **data, "id": row.entry["id"],
                        "password": "", "credential_storage": "none",
                    })
            else:
                from .config import entry_from_data  # noqa: F811 - local import
                cfg["mounts"].append(entry_from_data({**data, "password": ""}))

            try:
                save_config(cfg)
            except OSError as exc:
                LOGGER.error(f"Could not save configuration for share '{name}': {exc}")
                self.toast(f"Could not save configuration: {exc}", error=True)
                return

            if row is not None and not is_duplicate:
                LOGGER.info(
                    f"Updated share '{name}' (//{host}/{share} -> {path})."
                )
            elif is_duplicate:
                LOGGER.info(
                    f"Duplicated share as '{name}' (//{host}/{share} -> {path})."
                )
            else:
                LOGGER.info(
                    f"Added share '{name}' (//{host}/{share} -> {path})."
                )

            if credential_identity_changed and old_credential_storage == "secret-service":
                try:
                    secure_delete_password(
                        old_entry["host"], old_entry["share"], old_effective_username,
                    )
                except Exception as exc:
                    LOGGER.warning(
                        "Could not remove old SMB credential from Secret Service: %s", exc,
                    )

            if data["system_automount"] and not POLKIT_RULE_FILE.is_file():
                def _revert_system_automount():
                    cfg_now = load_config()
                    for mount in cfg_now["mounts"]:
                        if (isinstance(mount, dict)
                                and mount.get("host") == data["host"]
                                and mount.get("share") == data["share"]
                                and mount.get("path") == data["path"]):
                            mount["system_automount"] = False
                    save_config(cfg_now)

                def _on_setup_result(ok, message):
                    if not ok:
                        _revert_system_automount()
                        self.rebuild_rows()
                    self.toast(message, error=not ok)

                def on_setup_choice(_alert, response):
                    if response != "setup":
                        LOGGER.info(
                            f"User cancelled system automount setup for '{data['name']}'.",
                        )
                        _revert_system_automount()
                        self.rebuild_rows()
                        return

                    def _worker():
                        from .automount import ensure_system_automount_ready  # noqa: F811
                        ok, message = ensure_system_automount_ready()
                        GLib.idle_add(_on_setup_result, ok, message)

                    threading.Thread(target=_worker, daemon=True).start()

                alert = Adw.AlertDialog(
                    heading="One-time authorization required",
                    body=(
                        "Automounting this share at login requires installing a small helper "
                        "and enabling a systemd service. You will be asked for your password "
                        "once to authorize this."
                    ),
                )
                alert.add_response("cancel", "Cancel")
                alert.add_response("setup", "Set up now")
                alert.set_default_response("setup")
                alert.connect("response", on_setup_choice)
                alert.present(self.win)

            dialog.close()
            self.rebuild_rows()
            self.toast("Share saved.")

        cancel_button.connect("clicked", on_cancel)
        save_button.connect("clicked", on_save)

        view = make_dialog_view(content, action_bar)
        dialog.set_child(view)
        install_enter_action(dialog, save_button)
        dialog.present(self.win)

    def edit_row(self, row):
        self.entry_dialog(row)

    def duplicate_row(self, row):
        duplicate_data = dict(row.entry)
        duplicate_data.pop("id", None)
        duplicate_data["password"] = ""
        self.entry_dialog(duplicate_entry=duplicate_data)

    # =========================================================================
    # Delete
    # =========================================================================

    def delete_row(self, row):
        entry = row.entry
        mounted = is_mounted(
            entry.get("path", ""),
            entry.get("host"),
            entry.get("share"),
        )

        dialog = Adw.Dialog()
        dialog.set_title(f'Remove "{entry.get("name", "share")}"?')
        dialog.set_content_width(440)

        content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12,
            margin_top=18, margin_bottom=18, margin_start=18, margin_end=18,
        )

        message = (
            "This share is currently mounted.\n\n"
            "It will be unmounted first and then removed from this application."
            if mounted
            else (
                "This removes the share from this application.\n\n"
                "The actual SMB share on the NAS will not be affected."
            )
        )

        label = Gtk.Label(label=message, wrap=True, halign=Gtk.Align.START)
        content.append(label)

        action_bar, cancel_button, remove_button = make_action_bar(
            "Cancel", "Remove", accept_css_class="destructive-action",
        )

        cancel_button.connect("clicked", lambda _button: dialog.close())

        def on_remove(_button):
            dialog.close()
            self._remove_entry(row)

        remove_button.connect("clicked", on_remove)

        view = make_dialog_view(content, action_bar)
        dialog.set_child(view)
        install_enter_action(dialog, remove_button)
        dialog.present(self.win)

    def _remove_entry(self, row):
        entry = dict(row.entry)
        LOGGER.info(
            f"Removing share '{entry.get('name')}' "
            f"(//{entry.get('host')}/{entry.get('share')} -> {entry.get('path')})."
        )

        row.set_busy(True)

        def worker():
            if is_mounted(
                entry.get("path", ""),
                entry.get("host"),
                entry.get("share"),
            ):
                ok, message = do_unmount(entry)
                if not ok:
                    GLib.idle_add(self._delete_failed, row, message)
                    return

            if entry.get("credential_storage") == "secret-service":
                try:
                    secure_delete_password(
                        entry["host"], entry["share"],
                        get_effective_username(entry),
                    )
                except Exception as exc:
                    GLib.idle_add(
                        self._delete_failed, row,
                        f"cannot remove stored credential: {exc}",
                    )
                    return

            cfg = load_config()
            cfg["mounts"] = [
                mount for mount in cfg["mounts"]
                if not isinstance(mount, dict) or mount.get("id") != entry.get("id")
            ]

            try:
                save_config(cfg)
            except OSError as exc:
                GLib.idle_add(
                    self._delete_failed, row, f"cannot save configuration: {exc}",
                )
                return

            GLib.idle_add(self._delete_done, entry)

        threading.Thread(target=worker, name="smb-remove", daemon=True).start()

    def _delete_failed(self, row, message):
        LOGGER.error(
            f"Remove failed for '{row.entry.get('name')}': {message}"
        )
        row.set_busy(False)
        row.set_mounted(is_mounted(
            row.entry.get("path", ""),
            row.entry.get("host"),
            row.entry.get("share"),
        ))
        self.toast(f"Remove failed: {message}", error=True)
        return False

    def _delete_done(self, entry):
        self.rebuild_rows()
        self.toast(f'Removed "{entry.get("name", "share")}"')
        return False

    # =========================================================================
    # Actions
    # =========================================================================

    def _on_deselect_all_action(self, _action, _parameter):
        self.deselect_all()

    def _on_about_action(self, _action, _parameter):
        self.show_about()

    def show_about(self):
        about = Adw.AboutDialog()
        about.set_application_name(APP_NAME)
        about.set_application_icon("folder-remote-symbolic")
        about.set_version(APP_VERSION)
        about.set_developer_name(APP_AUTHOR)

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
            "<ul><li>Added a Deselect All button to the main menu.</li></ul>"
            "<p>New in 0.6.2 pre-release:</p>"
            "<ul><li>Removed Connect Selected and Disconnect Selected from the main menu.</li></ul>"
            "<p>New in 0.6.1 pre-release:</p>"
            "<ul><li>Automatically deselect shares after selected actions.</li>"
                "<li>Connect Selected and Disconnect Selected can now be triggered from the share toggle when multiple shares are selected.</li></ul>"
            "<p>New in 0.6.0 pre-release:</p>"
            "<ul><li>Added share selection.</li><li>Added Connect Selected.</li><li>Added Disconnect Selected.</li></ul>"
            "<p>New in 0.5.0 pre-release:</p>"
            "<ul><li>Added automatic mounting at application startup.</li>"
                "<li>Added What's New and more details to the About dialog.</li>"
                "<li>Polished names and tooltips.</li></ul>"
            "<p>New in 0.4.1 pre-release</p><ul><li>Fixed naming inconsistencies.</li></ul>"
            "<p>New in 0.4.0 pre-pre-release</p>"
            "<ul><li>Added <em>Connect All</em> action.</li>"
                "<li>Batch actions require only one superuser confirmation.</li>"
                "<li>Added support for confirming with the Enter key.</li></ul>"
            "<p>New in 0.3.0 pre-release</p>"
            "<ul><li>Added a main menu.</li><li>Added <em>Disconnect All</em> action.</li><li>Reordered buttons.</li></ul>"
            "<p>New in 0.2.0 pre-release</p><ul><li>Added the ability to duplicate shares.</li></ul>"
            "<p>New in 0.1.1 pre-release</p><ul><li>UI tweaks.</li></ul>"
            "<p>Initial 0.1.0 pre-release</p>"
            "<ul><li>Base UI</li><li>Add, edit, and remove shares.</li>"
                "<li>Mount and unmount shares.</li><li>JSON configuration.</li>"
                "<li>Support for automatically using the current host username.</li></ul>"
        )

        about.set_release_notes_version(APP_VERSION)
        about.set_website("https://github.com/mizgo/MounThor")

        about.set_comments(
            f"<b>{APP_NAME} {APP_VERSION}</b>\n"
            f"Release date: {APP_RELEASE_DATE}\n\n"
            "A simple Linux desktop application for fast and convenient SMB network share mounting.\n\n"
            "Save frequently used shares along with their mount settings and connect them with just a few clicks. For credentials, you can either save them in the application's configuration file or enter the password each time a share is mounted. You can also use your current Linux account name as the SMB username without storing it in the configuration.\n\n"
            "The app provides batch actions for connecting and disconnecting all shares or selected shares, allowing you to authenticate with your superuser password once and apply the action to multiple shares. Individual shares can also be configured to automatically mount when the application starts.\n"
            "The interface follows the system's GTK light and dark themes and respects the configured accent color.\n\n"
            "The goal is to provide a simple and elegant GUI built with GTK4 and libadwaita for managing CIFS/SMB mounts without repeatedly entering long mount commands and credentials or writing custom scripts for shares that do not need to be persistent."
        )

        about.set_license_type(Gtk.License.GPL_3_0)
        about.present(self.win)


# ============================================================================
# System automount entry point (--autostart)
# ============================================================================

def run_autostart() -> int:
    """Mount all entries with system_automount enabled. No GUI."""
    from .mounts import mount_entry_privileged  # noqa: F811 - local import to avoid circular

    try:
        cfg = load_config()
    except Exception as exc:
        LOGGER.error(f"Failed to load config: {exc}")
        print(json.dumps({"ok": False, "message": f"Failed to load config: {exc}"}))
        return 1

    summary = {"mounted": [], "skipped": [], "failed": []}

    for entry in cfg.get("mounts", []):
        if not entry.get("system_automount", False):
            continue

        host = entry.get("host") or ""
        share = entry.get("share") or ""
        path = entry.get("path") or ""
        username = entry.get("username") or ""

        if is_mounted(path, host, share):
            summary["skipped"].append(f"{entry.get('name')}: already mounted")
            continue

        password = secure_load_password(host, share, username)
        if not password:
            password = entry.get("password") or ""

        if not password:
            LOGGER.warning(f"Skipping '{entry.get('name')}': no stored password available.")
            summary["skipped"].append(f"{entry.get('name')}: no password")
            continue

        ok, message = mount_entry_privileged(entry, password)

        if ok:
            LOGGER.info(f"Auto-mounted '{entry.get('name')}': {message}")
            summary["mounted"].append(entry.get("name"))
        else:
            LOGGER.error(f"Failed to auto-mount '{entry.get('name')}': {message}")
            summary["failed"].append(f"{entry.get('name')}: {message}")

    print(json.dumps(summary))
    return 0


# ============================================================================
# Main / privileged helper entry point
# ============================================================================

def main():
    from .mounts import _privileged_batch_main  # noqa: F811 - local import to avoid circular

    if "--batch-mount" in sys.argv:
        return _privileged_batch_main("batch-mount")

    if "--batch-unmount" in sys.argv:
        return _privileged_batch_main("batch-unmount")

    if "--autostart" in sys.argv:
        return run_autostart()

    configure_logging()
    app = MounThorApp()
    return app.run(None)


if __name__ == "__main__":
    import sys  # noqa: E402 - top-level import needed for main guard
    sys.exit(main())
