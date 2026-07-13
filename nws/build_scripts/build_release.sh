#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

# Save current directory and define paths
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "${SCRIPT_DIR}/../../" && pwd)"
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

# Run modular build scripts sequentially
echo ""
echo "-------------------------------------------------------------------------"
echo " Step 1/6: Building ran-base..."
echo "-------------------------------------------------------------------------"
"${SCRIPT_DIR}/build_ran_base.sh"

echo ""
echo "-------------------------------------------------------------------------"
echo " Step 2/6: Building ran-build..."
echo "-------------------------------------------------------------------------"
"${SCRIPT_DIR}/build_ran_build.sh"

echo ""
echo "-------------------------------------------------------------------------"
echo " Step 3/6: Building oai-gnb (cucp, du)..."
echo "-------------------------------------------------------------------------"
"${SCRIPT_DIR}/build_oai_gnb.sh"

echo ""
echo "-------------------------------------------------------------------------"
echo " Step 4/6: Building oai-nr-cuup..."
echo "-------------------------------------------------------------------------"
"${SCRIPT_DIR}/build_oai_nr_cuup.sh"

echo ""
echo "-------------------------------------------------------------------------"
echo " Step 5/6: Building oai-nr-ue..."
echo "-------------------------------------------------------------------------"
"${SCRIPT_DIR}/build_oai_nr_ue.sh"

echo ""
echo "-------------------------------------------------------------------------"
echo " Step 6/6: Building oai-flexric..."
echo "-------------------------------------------------------------------------"
"${SCRIPT_DIR}/build_oai_flexric.sh"

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
