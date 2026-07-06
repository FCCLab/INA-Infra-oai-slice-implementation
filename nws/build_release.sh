#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# Save current directory and define paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "${SCRIPT_DIR}/../" && pwd)"
OAI_DIR="${WORKSPACE_DIR}/openairinterface5g"

# Detect architecture
ARCH=$(uname -m)
if [[ "$ARCH" == "x86_64" ]]; then
    ARCH_TAG="amd64"
elif [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]]; then
    ARCH_TAG="arm64"
else
    ARCH_TAG="$ARCH"
fi

echo "========================================================================="
echo " OAI Release Image Builder (gNB, CUCP, CUUP, DU, UE, FlexRIC)"
echo " Workspace: ${WORKSPACE_DIR}"
echo " OAI Directory: ${OAI_DIR}"
echo " Architecture: ${ARCH} (Tag: ${ARCH_TAG})"
echo "========================================================================="

# Check if Docker is installed and running
if ! command -v docker &> /dev/null; then
    echo "Error: docker command not found. Please install docker first."
    exit 1
fi

if ! docker info &> /dev/null; then
    echo "Error: Docker daemon is not running or current user has no permission."
    exit 1
fi

# Go to the OAI directory
cd "${OAI_DIR}"

# 1. Build ran-base image
echo ""
echo "-------------------------------------------------------------------------"
echo " Step 1/6: Building ran-base:latest..."
echo "-------------------------------------------------------------------------"
docker build \
    --target ran-base \
    --tag ran-base:latest \
    --file docker/Dockerfile.base.ubuntu .

# 2. Build ran-build image
echo ""
echo "-------------------------------------------------------------------------"
echo " Step 2/6: Building ran-build:latest..."
echo "-------------------------------------------------------------------------"
docker build \
    --target ran-build \
    --tag ran-build:latest \
    --file docker/Dockerfile.build.ubuntu .

# 3. Build oai-gnb image (cucp, du, gnb)
echo ""
echo "-------------------------------------------------------------------------"
echo " Step 3/6: Building oai-gnb:latest..."
echo "-------------------------------------------------------------------------"
docker build \
    --target oai-gnb \
    --tag oai-gnb:latest \
    --file docker/Dockerfile.gNB.ubuntu .

# 4. Build oai-nr-cuup image (cuup)
echo ""
echo "-------------------------------------------------------------------------"
echo " Step 4/6: Building oai-nr-cuup:latest..."
echo "-------------------------------------------------------------------------"
docker build \
    --target oai-nr-cuup \
    --tag oai-nr-cuup:latest \
    --file docker/Dockerfile.nr-cuup.ubuntu .

# 5. Build oai-nr-ue image (ue)
echo ""
echo "-------------------------------------------------------------------------"
echo " Step 5/6: Building oai-nr-ue:latest..."
echo "-------------------------------------------------------------------------"
docker build \
    --target oai-nr-ue \
    --tag oai-nr-ue:latest \
    --file docker/Dockerfile.nrUE.ubuntu .

# 6. Build oai-flexric image
echo ""
echo "-------------------------------------------------------------------------"
echo " Step 6/6: Building oai-flexric:latest..."
echo "-------------------------------------------------------------------------"
docker build \
    --target oai-flexric \
    --tag oai-flexric:latest \
    --build-arg BASE_IMAGE=ubuntu:noble \
    --file openair2/E2AP/flexric/docker/Dockerfile.flexric.ubuntu \
    openair2/E2AP/flexric

# 7. Tag and alias the release images
echo ""
echo "-------------------------------------------------------------------------"
echo " Step 7/7: Creating release tags for oai gnb, cucp, du, nr-cuup, nr-ue..."
echo "-------------------------------------------------------------------------"
# Create primary aliases
docker tag oai-gnb:latest oai-cucp:latest
docker tag oai-gnb:latest oai-du:latest

# Create architecture-specific tags
docker tag ran-base:latest ran-base:latest-${ARCH_TAG}
docker tag ran-build:latest ran-build:latest-${ARCH_TAG}
docker tag oai-gnb:latest oai-gnb:latest-${ARCH_TAG}
docker tag oai-cucp:latest oai-cucp:latest-${ARCH_TAG}
docker tag oai-nr-cuup:latest oai-nr-cuup:latest-${ARCH_TAG}
docker tag oai-du:latest oai-du:latest-${ARCH_TAG}
docker tag oai-nr-ue:latest oai-nr-ue:latest-${ARCH_TAG}
docker tag oai-flexric:latest oai-flexric:latest-${ARCH_TAG}

echo ""
echo "Successfully created tags:"
echo "----------------------------------------------------------------------------------------"
printf " %-22s | %-24s | %-32s \n" "Image Name" "Alias / Target" "Architecture Tag"
echo "----------------------------------------------------------------------------------------"
printf " %-22s | %-24s | %-32s \n" "oai-gnb:latest" "-" "oai-gnb:latest-${ARCH_TAG}"
printf " %-22s | %-24s | %-32s \n" "oai-cucp:latest" "oai-gnb:latest" "oai-cucp:latest-${ARCH_TAG}"
printf " %-22s | %-24s | %-32s \n" "oai-nr-cuup:latest" "-" "oai-nr-cuup:latest-${ARCH_TAG}"
printf " %-22s | %-24s | %-32s \n" "oai-du:latest" "oai-gnb:latest" "oai-du:latest-${ARCH_TAG}"
printf " %-22s | %-24s | %-32s \n" "oai-nr-ue:latest" "-" "oai-nr-ue:latest-${ARCH_TAG}"
printf " %-22s | %-24s | %-32s \n" "oai-flexric:latest" "-" "oai-flexric:latest-${ARCH_TAG}"
printf " %-22s | %-24s | %-32s \n" "ran-base:latest" "-" "ran-base:latest-${ARCH_TAG}"
printf " %-22s | %-24s | %-32s \n" "ran-build:latest" "-" "ran-build:latest-${ARCH_TAG}"
echo "----------------------------------------------------------------------------------------"
echo ""
echo " Done!"


