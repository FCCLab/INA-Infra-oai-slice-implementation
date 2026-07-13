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

echo "Building ran-base:latest..."
cd "${OAI_DIR}"
docker build \
    --target ran-base \
    --tag ran-base:latest \
    --file docker/Dockerfile.base.ubuntu .

docker tag ran-base:latest ran-base:latest-${ARCH_TAG}
echo "Successfully built and tagged ran-base:latest (${ARCH_TAG})"
