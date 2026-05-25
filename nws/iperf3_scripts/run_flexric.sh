#!/bin/bash

set -euo pipefail

CONFIGFILE=/workspace/flexric.conf
BUILD_DIR=/workspace/openairinterface5g/openair2/E2AP/flexric/build

cd $BUILD_DIR/examples/ric

# Set LD_LIBRARY_PATH for service model libraries
export LD_LIBRARY_PATH="${BUILD_DIR}/src/lib:${BUILD_DIR}/src/sm:/usr/local/lib/flexric:/usr/local/lib:${LD_LIBRARY_PATH:-}"

# Run nearRT-RIC with config file
exec ./nearRT-RIC -c "$CONFIGFILE"
