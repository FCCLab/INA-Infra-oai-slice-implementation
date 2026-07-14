#!/bin/bash
set -e

# Detect paths relative to the script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "${SCRIPT_DIR}/../../" && pwd)"
OAI_DIR="${WORKSPACE_DIR}/openairinterface5g"

NO_CACHE=0

usage() {
    echo "Usage: $0 [--no-cache]"
    echo "  --no-cache  Pass docker build --no-cache when packaging oai-gnb"
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

echo "Building oai-gnb:latest (includes cucp, du; no-cache=${NO_CACHE})..."
cd "${OAI_DIR}"

BUILD_ARGS=(
    --target oai-gnb
    --tag oai-gnb:latest
    --file docker/Dockerfile.gNB.ubuntu
)
if [[ "${NO_CACHE}" -eq 1 ]]; then
    BUILD_ARGS+=(--no-cache)
fi

docker build "${BUILD_ARGS[@]}" .

# Create primary aliases
docker tag oai-gnb:latest oai-cucp:latest
docker tag oai-gnb:latest oai-du:latest

# Create architecture-specific tags
docker tag oai-gnb:latest oai-gnb:latest-${ARCH_TAG}
docker tag oai-cucp:latest oai-cucp:latest-${ARCH_TAG}
docker tag oai-du:latest oai-du:latest-${ARCH_TAG}

echo "Successfully built and tagged oai-gnb, oai-cucp, and oai-du:latest (${ARCH_TAG})"
