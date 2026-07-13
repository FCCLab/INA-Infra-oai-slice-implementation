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

echo "Building oai-gnb:latest (includes cucp, du)..."
cd "${OAI_DIR}"
docker build \
    --target oai-gnb \
    --tag oai-gnb:latest \
    --file docker/Dockerfile.gNB.ubuntu .

# Create primary aliases
docker tag oai-gnb:latest oai-cucp:latest
docker tag oai-gnb:latest oai-du:latest

# Create architecture-specific tags
docker tag oai-gnb:latest oai-gnb:latest-${ARCH_TAG}
docker tag oai-cucp:latest oai-cucp:latest-${ARCH_TAG}
docker tag oai-du:latest oai-du:latest-${ARCH_TAG}

echo "Successfully built and tagged oai-gnb, oai-cucp, and oai-du:latest (${ARCH_TAG})"
