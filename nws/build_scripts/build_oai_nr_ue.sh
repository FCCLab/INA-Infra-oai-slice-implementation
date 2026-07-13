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

echo "Building oai-nr-ue:latest..."
cd "${OAI_DIR}"
docker build \
    --target oai-nr-ue \
    --tag oai-nr-ue:latest \
    --file docker/Dockerfile.nrUE.ubuntu .

docker tag oai-nr-ue:latest oai-nr-ue:latest-${ARCH_TAG}
echo "Successfully built and tagged oai-nr-ue:latest (${ARCH_TAG})"
