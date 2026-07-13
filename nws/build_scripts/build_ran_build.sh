#!/bin/bash
set -e

# Detect paths relative to the script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "${SCRIPT_DIR}/../../" && pwd)"
OAI_DIR="${WORKSPACE_DIR}/openairinterface5g"

ARCH=$(uname -m)
if [[ "$ARCH" == "x86_64" ]]; then
    ARCH_TAG="amd64"
elif [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]]; then
    ARCH_TAG="arm64"
else
    ARCH_TAG="$ARCH"
fi

echo "Building ran-build:latest..."
cd "${OAI_DIR}"
docker build \
    --target ran-build \
    --tag ran-build:latest \
    --file docker/Dockerfile.build.ubuntu .

docker tag ran-build:latest ran-build:latest-${ARCH_TAG}
echo "Successfully built and tagged ran-build:latest (${ARCH_TAG})"
