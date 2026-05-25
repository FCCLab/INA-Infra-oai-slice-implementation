#!/usr/bin/env bash

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
DONGLE_SCRIPT="${PROJECT_ROOT}/dongle.py"
UE_IDS=(UE1 UE2 UE3 UE4 UE5)
DEFAULT_ENDPOINT="${DEFAULT_ENDPOINT:-/fibo/webapi}"
UE1_DEFAULT_ENDPOINT="${UE1_DEFAULT_ENDPOINT:-/thru/webapi}"
UE2_DEFAULT_ENDPOINT="${UE2_DEFAULT_ENDPOINT:-/fibo/webapi}"
UE3_DEFAULT_ENDPOINT="${UE3_DEFAULT_ENDPOINT:-/thru/webapi}"
UE4_DEFAULT_ENDPOINT="${UE4_DEFAULT_ENDPOINT:-/fibo/webapi}"
UE5_DEFAULT_ENDPOINT="${UE5_DEFAULT_ENDPOINT:-/thru/webapi}"

default_ip_for_ue() {
  case "$1" in
    UE1) echo "192.168.101.1" ;;
    UE2) echo "192.168.102.1" ;;
    UE3) echo "192.168.103.1" ;;
    UE4) echo "192.168.104.1" ;;
    UE5) echo "192.168.105.1" ;;
    *) echo "" ;;
  esac
}

default_endpoint_for_ue() {
  case "$1" in
    UE1) echo "${UE1_DEFAULT_ENDPOINT}" ;;
    UE2) echo "${UE2_DEFAULT_ENDPOINT}" ;;
    UE3) echo "${UE3_DEFAULT_ENDPOINT}" ;;
    UE4) echo "${UE4_DEFAULT_ENDPOINT}" ;;
    UE5) echo "${UE5_DEFAULT_ENDPOINT}" ;;
    *) echo "${DEFAULT_ENDPOINT}" ;;
  esac
}

run_for_all_ues() {
  local feature_name="$1"
  shift
  local args=("$@")

  for ue in "${UE_IDS[@]}"; do
    local ip_var="${ue}_IP"
    local endpoint_var="${ue}_ENDPOINT"
    local ip="${!ip_var:-$(default_ip_for_ue "${ue}")}"
    local endpoint="${!endpoint_var:-${ENDPOINT:-$(default_endpoint_for_ue "${ue}")}}"

    echo "=============================================="
    echo "Feature: ${feature_name} | UE: ${ue}"

    if [[ -z "${ip}" ]]; then
      echo "SKIP: no IP configured for ${ue}"
      continue
    fi

    echo "Command: ${PYTHON_BIN} ${DONGLE_SCRIPT} --ip ${ip} --endpoint ${endpoint} ${args[*]}"
    "${PYTHON_BIN}" "${DONGLE_SCRIPT}" --ip "${ip}" --endpoint "${endpoint}" "${args[@]}"
    echo
  done
}

run_for_ue() {
  local ue="$1"
  local feature_name="$2"
  shift 2
  local args=("$@")

  local ip_var="${ue}_IP"
  local endpoint_var="${ue}_ENDPOINT"
  local ip="${!ip_var:-$(default_ip_for_ue "${ue}")}"
  local endpoint="${!endpoint_var:-${ENDPOINT:-$(default_endpoint_for_ue "${ue}")}}"

  echo "=============================================="
  echo "Feature: ${feature_name} | UE: ${ue}"

  if [[ -z "${ip}" ]]; then
    echo "SKIP: no IP configured for ${ue}"
    return 0
  fi

  echo "Command: ${PYTHON_BIN} ${DONGLE_SCRIPT} --ip ${ip} --endpoint ${endpoint} ${args[*]}"
  "${PYTHON_BIN}" "${DONGLE_SCRIPT}" --ip "${ip}" --endpoint "${endpoint}" "${args[@]}"
}
