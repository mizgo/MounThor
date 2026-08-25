#!/usr/bin/env bash

set -euo pipefail

# ============================================================================
# Paths
# ============================================================================

XDG_DATA_HOME="${XDG_DATA_HOME:-${HOME}/.local/share}"

APP_DIR="${XDG_DATA_HOME}/mounthor"
BIN_DIR="${HOME}/.local/bin"
APPLICATIONS_DIR="${XDG_DATA_HOME}/applications"

APP_TARGET="${APP_DIR}/mounthor.py"
BIN_TARGET="${BIN_DIR}/mounthor"
DESKTOP_TARGET="${APPLICATIONS_DIR}/io.github.mizgo.MounThor.desktop"

CONFIG_DIR="${XDG_CONFIG_HOME:-${HOME}/.config}/mounthor"
STATE_DIR="${XDG_STATE_HOME:-${HOME}/.local/state}/mounthor"

# ============================================================================
# Confirmation
# ============================================================================

echo
echo "This will remove the MounThor application."
echo
echo "The following application files will be removed:"
echo "  ${APP_TARGET}"
echo "  ${BIN_TARGET}"
echo "  ${DESKTOP_TARGET}"
echo
echo "Your saved shares, credentials, and logs will NOT be removed."
echo

read -r -p "Continue with uninstall? [y/N] " answer

case "${answer}" in

    y|Y|yes|YES)
        ;;

    *)
        echo
        echo "Uninstallation cancelled."
        exit 0
        ;;

esac

# ============================================================================
# Remove application files
# ============================================================================

rm -f \
    "${APP_TARGET}" \
    "${BIN_TARGET}" \
    "${DESKTOP_TARGET}"

# Remove the application directory if it is now empty.
rmdir \
    "${APP_DIR}" \
    2>/dev/null || true

# ============================================================================
# Refresh desktop database
# ============================================================================

if command -v update-desktop-database >/dev/null 2>&1; then

    update-desktop-database \
        "${APPLICATIONS_DIR}" \
        >/dev/null 2>&1 || true

fi

# ============================================================================
# Remove user data
# ============================================================================

echo
echo "MounThor application files have been removed."
echo

if [[ -d "${CONFIG_DIR}" ]]; then

    echo "Configuration directory exists:"
    echo "  ${CONFIG_DIR}"

fi

if [[ -d "${STATE_DIR}" ]]; then

    echo "State and log directory exists:"
    echo "  ${STATE_DIR}"

fi

if [[ -d "${CONFIG_DIR}" || -d "${STATE_DIR}" ]]; then

    echo
    echo "Your MounThor configuration and logs contain user data."
    echo

    read -r -p \
        "Remove configuration and logs as well? [y/N] " \
        remove_data

    case "${remove_data}" in

        y|Y|yes|YES)

            rm -rf \
                "${CONFIG_DIR}" \
                "${STATE_DIR}"

            echo
            echo "MounThor configuration and logs have been removed."

            ;;

        *)

            echo
            echo "MounThor configuration and logs were kept."

            ;;

    esac

else

    echo "No MounThor configuration or logs were found."

fi

echo
echo "MounThor has been uninstalled."