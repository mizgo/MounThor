#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(sed -n 's/^APP_VERSION = "\([^"]*\)"/\1/p' "${ROOT}/mounthor.py" | head -n1)"
DEBIAN_VERSION="$(sed -n '1s/^mounthor (\([^)-]*\)-[^)]*) .*/\1/p' "${ROOT}/debian/changelog")"
[[ -n "${VERSION}" && "${VERSION}" == "${DEBIAN_VERSION}" ]] || {
    echo "Debian changelog version (${DEBIAN_VERSION}) does not match app version (${VERSION})." >&2
    exit 1
}
command -v dpkg-buildpackage >/dev/null || {
    echo "dpkg-buildpackage is required (install dpkg-dev and debhelper)." >&2
    exit 1
}

BUILD_ROOT="${BUILD_ROOT:-${ROOT}/dist}"
mkdir -p "${BUILD_ROOT}"
BUILD_DIR="$(mktemp -d "${BUILD_ROOT}/.mounthor-deb-build.XXXXXX")"
trap 'rm -rf -- "${BUILD_DIR}"' EXIT
cp -a "${ROOT}/mounthor.py" "${ROOT}/LICENSE" "${ROOT}/README.md" \
    "${ROOT}/scripts" "${ROOT}/data" "${ROOT}/packaging" "${ROOT}/debian" \
    "${BUILD_DIR}/"
chmod 0755 "${BUILD_DIR}/packaging/linux/mounthor" "${BUILD_DIR}/debian/rules"
cd "${BUILD_DIR}"
dpkg-buildpackage --build=binary --no-sign
printf 'DEB output: %s\n' "${BUILD_ROOT}"
