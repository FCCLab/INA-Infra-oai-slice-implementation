#!/bin/bash
# Build OAI SMF from oai-cn5g-fed/component/oai-smf (includes local source patches).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "${SCRIPT_DIR}/../../" && pwd)"
SMF_DIR="${WORKSPACE_DIR}/oai-cn5g-fed/component/oai-smf"

NO_CACHE=0
# Bump suffix (-2, -3, ...) when rebuilding after SMF source changes.
IMAGE_TAG="${IMAGE_TAG:-v2.2.1-dnn-fix-3}"
SKIP_SUBMODULES=0

usage() {
    cat <<EOF
Usage: $0 [OPTIONS]

Build oai-smf Docker image from INA-Infra-oai-slice-implementation/oai-cn5g-fed/component/oai-smf.

Options:
  --no-cache          Pass docker build --no-cache
  --tag TAG           Image tag (default: v2.2.1-dnn-fix-3; bump -4, -5, ...)
  --skip-submodules   Do not run git submodule update --init
  -h, --help          Show this help

Environment:
  IMAGE_TAG           Same as --tag (default: v2.2.1-dnn-fix-3)

Output tags:
  oai-smf:\$TAG
  oai-smf:\$TAG-\$ARCH
  oaisoftwarealliance/oai-smf:\$TAG   (for nws/5gc/oai docker-compose)
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-cache)
            NO_CACHE=1
            shift
            ;;
        --tag)
            IMAGE_TAG="${2:?--tag requires a value}"
            shift 2
            ;;
        --skip-submodules)
            SKIP_SUBMODULES=1
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
case "$ARCH" in
    x86_64) ARCH_TAG="amd64" ;;
    aarch64|arm64) ARCH_TAG="arm64" ;;
    *) ARCH_TAG="$ARCH" ;;
esac

if ! command -v docker &>/dev/null; then
    echo "Error: docker not found." >&2
    exit 1
fi

if ! docker info &>/dev/null; then
    echo "Error: Docker daemon not running or no permission." >&2
    exit 1
fi

if [[ ! -d "${SMF_DIR}" ]]; then
    echo "Error: SMF source not found: ${SMF_DIR}" >&2
    exit 1
fi

if [[ ! -f "${SMF_DIR}/docker/Dockerfile.smf.ubuntu" ]]; then
    echo "Error: Dockerfile not found: ${SMF_DIR}/docker/Dockerfile.smf.ubuntu" >&2
    exit 1
fi

echo "========================================================================="
echo " OAI SMF Image Builder"
echo " SMF source: ${SMF_DIR}"
echo " Tag:        oai-smf:${IMAGE_TAG} (${ARCH_TAG})"
echo " no-cache:   ${NO_CACHE}"
echo "========================================================================="

if [[ "${SKIP_SUBMODULES}" -eq 0 ]]; then
    echo "Initializing SMF git submodules (common-src, common-build, common-ci)..."
    (
        cd "${SMF_DIR}"
        git submodule update --init --depth 1 \
            src/oai-cn5g-common-src \
            build/common-build \
            ci-scripts/common
    )
fi

cd "${SMF_DIR}"

BUILD_ARGS=(
    --target oai-smf
    --tag "oai-smf:${IMAGE_TAG}"
    --file docker/Dockerfile.smf.ubuntu
)
if [[ "${NO_CACHE}" -eq 1 ]]; then
    BUILD_ARGS+=(--no-cache)
fi

echo "Building oai-smf:${IMAGE_TAG}..."
docker build "${BUILD_ARGS[@]}" .

docker tag "oai-smf:${IMAGE_TAG}" "oai-smf:${IMAGE_TAG}-${ARCH_TAG}"
docker tag "oai-smf:${IMAGE_TAG}" "oaisoftwarealliance/oai-smf:${IMAGE_TAG}"

echo "Successfully built and tagged:"
echo "  oai-smf:${IMAGE_TAG}"
echo "  oai-smf:${IMAGE_TAG}-${ARCH_TAG}"
echo "  oaisoftwarealliance/oai-smf:${IMAGE_TAG}"
echo ""
echo "Use with nws OAI core: OAI_IMAGE_TAG=${IMAGE_TAG} docker compose up -d nws-oai-smf"
