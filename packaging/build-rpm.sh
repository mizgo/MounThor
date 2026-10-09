#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(sed -n 's/^APP_VERSION = "\([^"]*\)"/\1/p' "${ROOT}/mounthor.py" | head -n1)"
[[ -n "${VERSION}" ]] || { echo "Could not read APP_VERSION from mounthor.py" >&2; exit 1; }
SPEC_VERSION="$(sed -n 's/^Version:[[:space:]]*//p' "${ROOT}/packaging/rpm/mounthor.spec" | head -n1)"
[[ "${VERSION}" == "${SPEC_VERSION}" ]] || {
    echo "RPM spec version (${SPEC_VERSION}) does not match app version (${VERSION})." >&2
    exit 1
}
command -v rpmbuild >/dev/null || { echo "rpmbuild is required (install rpm-build)." >&2; exit 1; }

BUILD_ROOT="${BUILD_ROOT:-${ROOT}/dist/rpm-build}"
TOPDIR="${BUILD_ROOT}/rpmbuild"
SOURCE_DIR="${TOPDIR}/SOURCES"
mkdir -p "${SOURCE_DIR}" "${TOPDIR}"/{BUILD,BUILDROOT,RPMS,SPECS,SRPMS}
ARCHIVE="${SOURCE_DIR}/mounthor-${VERSION}.tar.gz"
tar -C "${ROOT}" \
    --exclude='./dist' --exclude='./__pycache__' --exclude='./.git' \
    --exclude='./debian' --exclude='./packaging/rpm' \
    -czf "${ARCHIVE}" \
    --transform="s,^,mounthor-${VERSION}/," \
    mounthor.py LICENSE scripts/mounthor-mount-helper packaging/linux/mounthor \
    data/io.github.mizgo.MounThor.desktop data/io.github.mizgo.MounThor.metainfo.xml \
    data/icons/hicolor/scalable/apps/io.github.mizgo.MounThor.svg
cp "${ROOT}/packaging/rpm/mounthor.spec" "${TOPDIR}/SPECS/"
rpmbuild -ba "${TOPDIR}/SPECS/mounthor.spec" --define "_topdir ${TOPDIR}"
printf 'RPM output: %s\n' "${TOPDIR}/RPMS"
