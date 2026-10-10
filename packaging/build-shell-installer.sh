#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(sed -n 's/^APP_VERSION = "\([^"]*\)"/\1/p' "${ROOT}/mounthor.py" | head -n1)"
[[ -n "${VERSION}" ]] || { echo "Could not read APP_VERSION from mounthor.py" >&2; exit 1; }

OUTPUT_DIR="${OUTPUT_DIR:-${ROOT}/dist}"
mkdir -p "${OUTPUT_DIR}"
ARCHIVE_ROOT="MounThor-${VERSION}-shell-installer"
ARCHIVE="${OUTPUT_DIR}/${ARCHIVE_ROOT}.tar.gz"
tar -C "${ROOT}" \
    -czf "${ARCHIVE}" \
    --transform="s,^,${ARCHIVE_ROOT}/," \
    mounthor.py LICENSE \
    scripts/install-mounthor.sh \
    scripts/uninstall-mounthor.sh \
    scripts/mounthor \
    scripts/mounthor-mount-helper \
    data/io.github.mizgo.MounThor.desktop.in \
    data/io.github.mizgo.MounThor.metainfo.xml \
    data/icons/hicolor/scalable/apps/io.github.mizgo.MounThor.svg
printf 'Shell installer archive: %s\n' "${ARCHIVE}"