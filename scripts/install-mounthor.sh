#!/usr/bin/env bash

set -euo pipefail

# ============================================================================
# Paths
# ============================================================================

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

XDG_DATA_HOME="${XDG_DATA_HOME:-${HOME}/.local/share}"

APP_DIR="${XDG_DATA_HOME}/mounthor"
BIN_DIR="${HOME}/.local/bin"
APPLICATIONS_DIR="${XDG_DATA_HOME}/applications"

APP_SOURCE="${PROJECT_DIR}/mounthor.py"
LAUNCHER_SOURCE="${PROJECT_DIR}/scripts/mounthor"
HELPER_SOURCE="${PROJECT_DIR}/scripts/mounthor-mount-helper"
DESKTOP_SOURCE="${PROJECT_DIR}/data/io.github.mizgo.MounThor.desktop.in"

APP_TARGET="${APP_DIR}/mounthor.py"
BIN_TARGET="${BIN_DIR}/mounthor"
HELPER_TARGET="${APP_DIR}/scripts/mounthor-mount-helper"
DESKTOP_TARGET="${APPLICATIONS_DIR}/io.github.mizgo.MounThor.desktop"

# ============================================================================
# Validation
# ============================================================================

if [[ ! -f "${APP_SOURCE}" ]]; then

    echo "Error: mounthor.py not found."

    exit 1

fi

if [[ ! -f "${LAUNCHER_SOURCE}" ]]; then

    echo "Error: launcher not found."

    exit 1

fi

if [[ ! -f "${HELPER_SOURCE}" ]]; then

    echo "Error: mount helper not found."

    exit 1

fi

if [[ ! -f "${DESKTOP_SOURCE}" ]]; then

    echo "Error: desktop entry template not found."

    exit 1

fi

if [[ -f "${APP_TARGET}" ]]; then

    IS_UPDATE=true

else

    IS_UPDATE=false

fi

# ============================================================================
# Directories
# ============================================================================

mkdir -p \
    "${APP_DIR}/scripts" \
    "${BIN_DIR}" \
    "${APPLICATIONS_DIR}"

# ============================================================================
# Application
# ============================================================================

install \
    -m 0644 \
    "${APP_SOURCE}" \
    "${APP_TARGET}"

# ============================================================================
# Launcher
# ============================================================================

install \
    -m 0755 \
    "${LAUNCHER_SOURCE}" \
    "${BIN_TARGET}"

# ============================================================================
# Mount helper
# ============================================================================

install \
    -m 0755 \
    "${HELPER_SOURCE}" \
    "${HELPER_TARGET}"

# ============================================================================
# Desktop entry
# ============================================================================

desktop_tmp="$(mktemp --suffix=.desktop)"

trap 'rm -f "${desktop_tmp}"' EXIT

sed \
    "s|@MOUNTHOR_EXEC@|${BIN_TARGET}|g" \
    "${DESKTOP_SOURCE}" \
    > "${desktop_tmp}"

if command -v desktop-file-validate >/dev/null 2>&1; then

    desktop-file-validate \
        "${desktop_tmp}"

fi

install \
    -m 0644 \
    "${desktop_tmp}" \
    "${DESKTOP_TARGET}"

# ============================================================================
# Refresh desktop database
# ============================================================================

if command -v update-desktop-database >/dev/null 2>&1; then

    update-desktop-database \
        "${APPLICATIONS_DIR}" \
        >/dev/null 2>&1 || true

fi

# ============================================================================
# Result
# ============================================================================

echo

if [[ "${IS_UPDATE}" == true ]]; then

    echo "MounThor has been updated successfully."

else

    echo "MounThor has been installed successfully."

fi

echo
echo "Application:"
echo "  ${APP_TARGET}"
echo
echo "Launcher:"
echo "  ${BIN_TARGET}"
echo
echo "Mount helper:"
echo "  ${HELPER_TARGET}"
echo
echo "Desktop entry:"
echo "  ${DESKTOP_TARGET}"
echo
echo "The application should now be available in your application menu."