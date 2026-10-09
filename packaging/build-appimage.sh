#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(sed -n 's/^APP_VERSION = "\([^"]*\)"/\1/p' "${ROOT}/mounthor.py" | head -n1)"
[[ -n "${VERSION}" ]] || { echo "Could not read APP_VERSION from mounthor.py" >&2; exit 1; }
command -v appimagetool >/dev/null || {
    echo "appimagetool is required. AppImage uses the host Python/GTK/libadwaita runtime." >&2
    exit 1
}
command -v python3 >/dev/null || { echo "python3 is required." >&2; exit 1; }

BUILD_ROOT="${BUILD_ROOT:-${ROOT}/dist/appimage}"
APPDIR="${BUILD_ROOT}/MounThor.AppDir"
rm -rf -- "${APPDIR}"
install -d "${APPDIR}/usr/lib/mounthor/scripts" \
    "${APPDIR}/usr/share/applications" "${APPDIR}/usr/share/metainfo" \
    "${APPDIR}/usr/share/icons/hicolor/scalable/apps"
install -m 0644 "${ROOT}/mounthor.py" "${APPDIR}/usr/lib/mounthor/mounthor.py"
install -m 0755 "${ROOT}/scripts/mounthor-mount-helper" \
    "${APPDIR}/usr/lib/mounthor/scripts/mounthor-mount-helper"
install -m 0644 "${ROOT}/data/io.github.mizgo.MounThor.metainfo.xml" \
    "${APPDIR}/usr/share/metainfo/"
install -m 0644 "${ROOT}/data/icons/hicolor/scalable/apps/io.github.mizgo.MounThor.svg" \
    "${APPDIR}/usr/share/icons/hicolor/scalable/apps/"
install -m 0644 "${ROOT}/data/icons/hicolor/scalable/apps/io.github.mizgo.MounThor.svg" \
    "${APPDIR}/io.github.mizgo.MounThor.svg"
install -m 0755 "${ROOT}/packaging/appimage/AppRun" "${APPDIR}/AppRun"
sed 's|@MOUNTHOR_EXEC@|AppRun|g' \
    "${ROOT}/data/io.github.mizgo.MounThor.desktop.in" \
    > "${APPDIR}/io.github.mizgo.MounThor.desktop"
install -m 0644 "${APPDIR}/io.github.mizgo.MounThor.desktop" \
    "${APPDIR}/usr/share/applications/"
install -m 0644 "${ROOT}/LICENSE" "${APPDIR}/LICENSE"

OUTPUT="${BUILD_ROOT}/MounThor-${VERSION}-x86_64.AppImage"
ARCH=x86_64 appimagetool "${APPDIR}" "${OUTPUT}"
printf 'AppImage output: %s\n' "${OUTPUT}"
