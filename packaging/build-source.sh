#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(sed -n 's/^APP_VERSION = "\([^"]*\)"/\1/p' "${ROOT}/mounthor.py" | head -n1)"
[[ -n "${VERSION}" ]] || { echo "Could not read APP_VERSION from mounthor.py" >&2; exit 1; }

OUTPUT_DIR="${OUTPUT_DIR:-${ROOT}/dist}"
mkdir -p "${OUTPUT_DIR}"
ARCHIVE="${OUTPUT_DIR}/MounThor-${VERSION}-source.tar.gz"
tar -C "${ROOT}" \
    --exclude='./dist' --exclude='./__pycache__' --exclude='./.git' \
    -czf "${ARCHIVE}" \
    --transform="s,^,MounThor-${VERSION}/," \
    mounthor.py LICENSE README.md scripts data packaging debian tools
printf 'Source archive: %s\n' "${ARCHIVE}"
