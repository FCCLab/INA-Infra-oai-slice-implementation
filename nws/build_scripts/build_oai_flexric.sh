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

sudo rm -rf ${OAI_DIR}/openair2/E2AP/flexric/build

DOCKERFILE="${SCRIPT_DIR}/Dockerfile.flexric.ubuntu"
FLEXRIC_DIR="${OAI_DIR}/openair2/E2AP/flexric"

echo "Building oai-flexric:latest (Dockerfile ${DOCKERFILE})..."
docker build \
    --target oai-flexric \
    --tag oai-flexric:latest \
    --build-arg BASE_IMAGE=ubuntu:noble \
    --file "${DOCKERFILE}" \
    "${FLEXRIC_DIR}"

docker tag oai-flexric:latest oai-flexric:latest-${ARCH_TAG}
echo "Successfully built and tagged oai-flexric:latest (${ARCH_TAG})"
