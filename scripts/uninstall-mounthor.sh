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
HELPER_SOURCE_TARGET="${APP_DIR}/scripts/mounthor-mount-helper"
HELPER_BIN_TARGET="${BIN_DIR}/mounthor-mount-helper"
DESKTOP_TARGET="${APPLICATIONS_DIR}/io.github.mizgo.MounThor.desktop"

SERVICE_FILE="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user/mounthor-automount.service"
POLKIT_RULE="/etc/polkit-1/rules.d/60-mounthor.rules"

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
echo "  ${HELPER_SOURCE_TARGET}"
echo "  ${HELPER_BIN_TARGET}"
echo "  ${DESKTOP_TARGET}"

if [[ -f "${SERVICE_FILE}" ]]; then

    echo
    echo "The system automount service will be disabled and removed:"
    echo "  ${SERVICE_FILE}"

fi

if [[ -f "${POLKIT_RULE}" ]]; then

    echo
    echo "The polkit rule will also be removed (password required):"
    echo "  ${POLKIT_RULE}"

fi

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
# Disable system automount service
# ============================================================================

if [[ -f "${SERVICE_FILE}" ]]; then

    systemctl --user disable mounthor-automount.service \
        >/dev/null 2>&1 || true

    rm -f \
        "${SERVICE_FILE}"

    systemctl --user daemon-reload \
        >/dev/null 2>&1 || true

fi

# ============================================================================
# Remove polkit rule
# ============================================================================

if [[ -f "${POLKIT_RULE}" ]]; then

    echo
    echo "Removing the polkit rule requires administrator privileges."

    if command -v pkexec >/dev/null 2>&1; then

        read -r -p \
            "Remove ${POLKIT_RULE} now? [y/N] " \
            remove_rule

        case "${remove_rule}" in

            y|Y|yes|YES)

                pkexec rm -f "${POLKIT_RULE}" || true

                systemctl restart polkit \
                    >/dev/null 2>&1 || true

                ;;

            *)

                echo "Polkit rule was kept. You can remove it later with:"
                echo "  sudo rm ${POLKIT_RULE} && sudo systemctl restart polkit"

                ;;

        esac

    else

        echo "pkexec is not available. You can remove the rule later with:"
        echo "  sudo rm ${POLKIT_RULE} && sudo systemctl restart polkit"

    fi

fi

# ============================================================================
# Remove application files
# ============================================================================

rm -f \
    "${APP_TARGET}" \
    "${BIN_TARGET}" \
    "${HELPER_SOURCE_TARGET}" \
    "${HELPER_BIN_TARGET}" \
    "${DESKTOP_TARGET}"

rmdir \
    "${APP_DIR}/scripts" \
    2>/dev/null || true

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