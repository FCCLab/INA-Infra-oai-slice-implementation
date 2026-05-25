#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

bash "${SCRIPT_DIR}/test_signal.sh"
bash "${SCRIPT_DIR}/test_network.sh"
bash "${SCRIPT_DIR}/test_connection.sh"
bash "${SCRIPT_DIR}/test_cell.sh"
bash "${SCRIPT_DIR}/test_device.sh"
bash "${SCRIPT_DIR}/test_sim.sh"
bash "${SCRIPT_DIR}/test_wan.sh"
bash "${SCRIPT_DIR}/test_ca.sh"
bash "${SCRIPT_DIR}/test_airplane.sh"
bash "${SCRIPT_DIR}/test_apn.sh"
bash "${SCRIPT_DIR}/test_discover.sh"
