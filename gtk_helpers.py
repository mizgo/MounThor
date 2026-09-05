#!/usr/bin/env python3

"""GTK helper functions for MounThor UI."""

from gi.repository import Adw, Gdk, Gtk

from .constants import (
    LIST_PADDING_BOTTOM,
    LIST_PADDING_LEFT,
    LIST_PADDING_RIGHT,
    LIST_PADDING_TOP,
    MOUNT_CARD_COLOR,
    MOUNT_CARD_HOVER,
    MOUNT_PADDING_BOTTOM,
    MOUNT_PADDING_LEFT,
    MOUNT_PADDING_RIGHT,
    MOUNT_PADDING_TOP,
)


# ============================================================================
# Widget helpers
# ============================================================================

def get_text(widget) -> str:
    """Get text from a widget that supports it."""
    return widget.get_text()


def install_enter_action(dialog, accept_button: Gtk.Button):
    """Make Enter activate the dialog's primary action.

    Uses Gtk.EventControllerKey instead of GTK3-era default-widget
    APIs, keeping compatibility with GTK 4.23.x.
    """
    controller = Gtk.EventControllerKey()
    controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

    def on_key_pressed(_controller, keyval, _keycode, _state):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if accept_button.get_sensitive():
                accept_button.emit("clicked")
                return True
        return False

    controller.connect("key-pressed", on_key_pressed)
    dialog.add_controller(controller)


def make_action_bar(cancel_label: str, accept_label: str, accept_css_class: str = "suggested-action"):
    """Create an action bar with cancel and accept buttons."""
    action_bar = Gtk.ActionBar()

    cancel_button = Gtk.Button.new_with_label(cancel_label)
    accept_button = Gtk.Button.new_with_label(accept_label)

    if accept_css_class:
        accept_button.add_css_class(accept_css_class)

    action_bar.pack_start(cancel_button)
    action_bar.pack_end(accept_button)

    return action_bar, cancel_button, accept_button


def make_dialog_view(content, action_bar):
    """Create a toolbar view with content and an action bar at the bottom."""
    toolbar_view = Adw.ToolbarView()
    toolbar_view.set_content(content)
    toolbar_view.add_bottom_bar(action_bar)
    return toolbar_view


def make_entry_listbox():
    """Create a listbox with boxed-list CSS class."""
    listbox = Gtk.ListBox()
    listbox.set_selection_mode(Gtk.SelectionMode.NONE)
    listbox.add_css_class("boxed-list")
    return listbox


def make_mount_listbox():
    """Create a mount listbox with explicit selection management."""
    listbox = Gtk.ListBox()
    # Selection is managed explicitly by MountRow instead of Gtk.ListBox.
    # This keeps clicks on child controls (buttons/switch) out of the
    # selection mechanism entirely.
    listbox.set_selection_mode(Gtk.SelectionMode.NONE)
    return listbox


# ============================================================================
# CSS styling
# ============================================================================

def install_mount_list_css():
    """Install custom CSS for the mount list styling."""
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
            display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
