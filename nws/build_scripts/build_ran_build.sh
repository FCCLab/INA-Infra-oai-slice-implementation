#!/bin/bash
set -e

# Detect paths relative to the script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "${SCRIPT_DIR}/../../" && pwd)"
OAI_DIR="${WORKSPACE_DIR}/openairinterface5g"

NO_CACHE=0

usage() {
    echo "Usage: $0 [--no-cache]"
    echo "  --no-cache  Pass docker build --no-cache (full recompile of ran-build)"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-cache)
            NO_CACHE=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

ARCH=$(uname -m)
if [[ "$ARCH" == "x86_64" ]]; then
    ARCH_TAG="amd64"
elif [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]]; then
    ARCH_TAG="arm64"
else
    ARCH_TAG="$ARCH"
fi

echo "Building ran-build:latest (no-cache=${NO_CACHE})..."
cd "${OAI_DIR}"

BUILD_ARGS=(
    --target ran-build
    --tag ran-build:latest
    --file docker/Dockerfile.build.ubuntu
)
if [[ "${NO_CACHE}" -eq 1 ]]; then
    BUILD_ARGS+=(--no-cache)
fi

docker build "${BUILD_ARGS[@]}" .

docker tag ran-build:latest ran-build:latest-${ARCH_TAG}
echo "Successfully built and tagged ran-build:latest (${ARCH_TAG})"
