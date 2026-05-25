#!/bin/bash

# Auto-detect system architecture and set appropriate Docker image tags
# This script wraps docker-compose commands with architecture detection

# Detect architecture
ARCH=$(uname -m)

# Set image tag based on architecture
if [[ "$ARCH" == "aarch64" ]] || [[ "$ARCH" == "arm64" ]]; then
    export OAI_IMAGE_TAG="develop-arm-v2"
    echo "Detected ARM architecture (${ARCH}), using ARM images ${OAI_IMAGE_TAG}"
else
    export OAI_IMAGE_TAG="develop"
    echo "Detected non-ARM architecture (${ARCH}), using standard images ${OAI_IMAGE_TAG}"
fi

# Pass all arguments to docker-compose
exec docker compose "$@"
