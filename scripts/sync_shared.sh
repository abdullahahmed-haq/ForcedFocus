#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_DIR="${SCRIPT_DIR}/../shared"
TARGETS=("${SCRIPT_DIR}/../web/shared" "${SCRIPT_DIR}/../chrome-extension/shared")

usage() {
    echo "Usage: bash scripts/sync_shared.sh --check|--write" >&2
}

case "${1:-}" in
    --check)
        for target in "${TARGETS[@]}"; do
            if ! diff -qr "$SOURCE_DIR" "$target"; then
                echo "Shared files are out of sync: $target" >&2
                exit 1
            fi
        done
        echo "Shared files are in sync."
        ;;
    --write)
        for target in "${TARGETS[@]}"; do
            rm -rf "$target"
            cp -R "$SOURCE_DIR" "$target"
        done
        echo "Shared files synchronized."
        ;;
    *)
        usage
        exit 2
        ;;
esac
