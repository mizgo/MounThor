#!/usr/bin/env python3

"""MountRow widget for MounThor."""

from gi.repository import Adw, Gdk, Gtk


class MountRow(Adw.ActionRow):
    """A row representing a single SMB mount in the list."""

    def __init__(self, app, entry: dict):
        super().__init__()

        self.app = app
        self.entry = entry

        self._updating = False
        self._busy = False
        self._selected = False

        self.set_activatable(False)
        self.set_selectable(True)

        # Click controller for row selection
        self._click_controller = Gtk.GestureClick()
        self._click_controller.set_button(Gdk.BUTTON_PRIMARY)
        self._click_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self._click_controller.connect("pressed", self._on_primary_pressed)
        self.add_controller(self._click_controller)

        # Title and subtitle
        self.set_title(entry.get("name", "Unnamed share"))
        self.set_subtitle(
            f"//{entry.get('host', '')}/{entry.get('share', '')}"
            f"  →  {entry.get('path', '')}"
        )

        # Prefix: icon + spinner
        self.icon = Gtk.Image.new_from_icon_name("drive-harddisk-symbolic")
        self.spinner = Gtk.Spinner()
        self.spinner.set_visible(False)

        prefix = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        prefix.append(self.icon)
        prefix.append(self.spinner)
        self.add_prefix(prefix)

        # --------------------------------------------------------------------
        # Action buttons (suffix)
        # --------------------------------------------------------------------

        self.duplicate_button = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
        self.duplicate_button.set_tooltip_text("Duplicate share")

        self.edit_button = Gtk.Button.new_from_icon_name("document-edit-symbolic")
        self.edit_button.set_tooltip_text("Edit share")

        self.delete_button = Gtk.Button.new_from_icon_name("user-trash-symbolic")
        self.delete_button.set_tooltip_text("Remove share")

        self.switch = Gtk.Switch()
        self.switch.set_valign(Gtk.Align.CENTER)
        self.switch.set_tooltip_text("Mount or unmount share")

        suffix = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            halign=Gtk.Align.END,
            valign=Gtk.Align.CENTER,
        )
        suffix.append(self.duplicate_button)
        suffix.append(self.edit_button)
        suffix.append(self.delete_button)
        suffix.append(self.switch)
        self.add_suffix(suffix)

        # Connect signals
        self.switch.connect("notify::active", self._switch_changed)
        self.duplicate_button.connect("clicked", self._on_duplicate_clicked)
        self.edit_button.connect("clicked", self._on_edit_clicked)
        self.delete_button.connect("clicked", self._on_delete_clicked)

    # ------------------------------------------------------------------------
    # Selection handling
    # ------------------------------------------------------------------------

    def _on_primary_pressed(self, _gesture, _n_press, x, y):
        """Handle primary button press for row selection."""
        picked = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        widget = picked

        while widget is not None:
            if isinstance(widget, (Gtk.Button, Gtk.Switch)):
                _gesture.set_state(Gtk.EventSequenceState.DENIED)
                return
            if widget is self:
                break
            widget = widget.get_parent()

        if self._busy:
            return

        self.app.select_mount_row(self)
        _gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def set_selected(self, selected: bool):
        """Set the selection state of this row."""
        self._selected = bool(selected)
        if self._selected:
            self.add_css_class("smb-selected")
        else:
            self.remove_css_class("smb-selected")

    def is_selected(self) -> bool:
        return self._selected

    # ------------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------------

    def _on_duplicate_clicked(self, _button):
        self.app.duplicate_row(self)

    def _on_edit_clicked(self, _button):
        self.app.edit_row(self)

    def _on_delete_clicked(self, _button):
        self.app.delete_row(self)

    # ------------------------------------------------------------------------
    # Switch handler
    # ------------------------------------------------------------------------

    def _switch_changed(self, *_args):
        if self._updating or self._busy:
            return

        if self.is_selected():
            if self.switch.get_active():
                self.app.connect_selected()
            else:
                self.app.disconnect_selected()
            return

        self.app.toggle_mount(self, self.switch.get_active())

    # ------------------------------------------------------------------------
    # State updates
    # ------------------------------------------------------------------------

    def set_mounted(self, mounted: bool):
        """Update the switch state and icon based on mount status."""
        self._updating = True
        try:
            self.switch.set_active(mounted)
        finally:
            self._updating = False

        self.icon.set_from_icon_name(
            "network-workgroup-symbolic" if mounted else "drive-harddisk-symbolic"
        )

    def set_busy(self, busy: bool):
        """Show/hide the loading spinner and disable controls."""
        self._busy = busy
        self.spinner.set_visible(busy)
        self.spinner.set_spinning(busy)
        self.switch.set_sensitive(not busy)
        self.duplicate_button.set_sensitive(not busy)
        self.edit_button.set_sensitive(not busy)
        self.delete_button.set_sensitive(not busy)
