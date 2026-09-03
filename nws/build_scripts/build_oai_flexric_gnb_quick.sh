#!/bin/bash
# Quick oai-gnb: host cmake/ninja (incremental) -> stage binaries -> docker package.
# Ninja tracks source changes; docker COPY invalidates image layers when staged files change.
# Also runs incremental FlexRIC quick build (slice_sm etc.) unless --no-flexric.
# Requires: ran-base:latest; host cmake+ninja; staging/flexric from build_oai_flexric_quick.sh.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "${SCRIPT_DIR}/../../" && pwd)"
OAI_DIR="${WORKSPACE_DIR}/openairinterface5g"
LOCAL_BUILD="${OAI_DIR}/cmake_targets/ran_build/build"
LOCAL_BIN="${LOCAL_BUILD}/nr-softmodem"
STAGING_DIR="${SCRIPT_DIR}/staging"
STAGED_BIN="${STAGING_DIR}/nr-softmodem"
STAGED_UE_BIN="${STAGING_DIR}/nr-uesoftmodem"
STAGED_LIBS="${STAGING_DIR}/libs"
STAGED_FLEXRIC="${STAGING_DIR}/flexric/lib/flexric"
DOCKERFILE="${SCRIPT_DIR}/Dockerfile.gNB.quick.ubuntu"
UE_QUICK_SH="${SCRIPT_DIR}/build_oai_nr_ue_quick.sh"
FLEXRIC_QUICK_SH="${SCRIPT_DIR}/build_oai_flexric_quick.sh"
NO_CACHE=0
BUILD_LOCAL=1
BUILD_FLEXRIC=1
LOCK_FILE="${STAGING_DIR}/.build_oai_gnb_quick.lock"

# gNB + UE must share librfsimulator (RFsim wire protocol).
QUICK_CMAKE_TARGETS=(nr-softmodem nr-uesoftmodem rfsimulator)

usage() {
    echo "Usage: $0 [--no-cache] [--no-local-build] [--no-flexric] [--flexric-only]"
    echo "  Host cmake/ninja builds nr-softmodem, then docker packages staged binaries."
    echo "  --no-cache         docker build --no-cache for oai-gnb packaging layer"
    echo "  --no-local-build   skip host cmake build (nr-softmodem + runtime .so must already exist)"
    echo "  --no-flexric       skip incremental FlexRIC build (use existing staging/flexric)"
    echo "  --flexric-only     only run build_oai_flexric_quick.sh"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-cache)
            NO_CACHE=1
            shift
            ;;
        --no-local-build)
            BUILD_LOCAL=0
            shift
            ;;
        --no-flexric)
            BUILD_FLEXRIC=0
            shift
            ;;
        --flexric-only)
            BUILD_FLEXRIC=1
            BUILD_LOCAL=0
            shift
            if [[ $# -gt 0 ]]; then
                echo "Unknown option after --flexric-only: $1" >&2
                usage >&2
                exit 1
            fi
            bash "${FLEXRIC_QUICK_SH}" $([[ "${NO_CACHE}" -eq 1 ]] && echo --no-cache)
            exit 0
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

if ! docker image inspect ran-base:latest &>/dev/null; then
    echo "error: ran-base:latest missing — run build_ran_base.sh once" >&2
    exit 1
fi

if [[ "${BUILD_FLEXRIC}" -eq 1 ]]; then
  FLEXRIC_ARGS=()
  [[ "${NO_CACHE}" -eq 1 ]] && FLEXRIC_ARGS+=(--no-cache)
  [[ "${BUILD_LOCAL}" -eq 0 ]] && FLEXRIC_ARGS+=(--no-local-build)
  echo "Running incremental FlexRIC quick build..."
  bash "${FLEXRIC_QUICK_SH}" "${FLEXRIC_ARGS[@]}"
fi

if [[ ! -f "${STAGED_FLEXRIC}/libslice_sm.so" ]]; then
    echo "error: ${STAGED_FLEXRIC}/libslice_sm.so missing — run build_oai_flexric_quick.sh" >&2
    exit 1
fi

if [[ ! -d "${LOCAL_BUILD}" ]]; then
    echo "error: ${LOCAL_BUILD} missing — run a cmake configure first (build_ran_build.sh or build_oai)" >&2
    exit 1
fi

mkdir -p "${STAGING_DIR}"
exec 9>"${LOCK_FILE}"
if ! flock -n 9; then
    echo "error: another build_oai_gnb_quick.sh is running (lock: ${LOCK_FILE})" >&2
    exit 1
fi

cmake_build_requires_ccache() {
    local cache="${LOCAL_BUILD}/CMakeCache.txt"
    [[ -f "${cache}" ]] || return 1
    # Only ACTIVE=ON means ninja will invoke ccache. CCACHE_FOUND alone is leftover cache.
    grep -qE '^CCACHE_ACTIVE:BOOL=ON' "${cache}"
}

host_ccache_path() {
    local cache="${LOCAL_BUILD}/CMakeCache.txt"
    local path=""
    if [[ -f "${cache}" ]]; then
        path="$(grep -E '^CCACHE_FOUND:FILEPATH=' "${cache}" | cut -d= -f2-)"
    fi
    if [[ -z "${path}" ]]; then
        command -v ccache 2>/dev/null || echo "/usr/bin/ccache"
    else
        echo "${path}"
    fi
}

host_has_working_ccache() {
    local ccache_path
    ccache_path="$(host_ccache_path)"
    [[ -n "${ccache_path}" && -x "${ccache_path}" ]] && "${ccache_path}" --version >/dev/null 2>&1
}

ensure_build_dir_writable() {
    local probe="${LOCAL_BUILD}/.quick_build_write_probe"
    if touch "${probe}" 2>/dev/null; then
        rm -f "${probe}"
        return 0
    fi
    local bad_file="${LOCAL_BUILD}/.ninja_deps"
    [[ -e "${bad_file}" ]] || bad_file="${LOCAL_BUILD}"
    echo "error: cannot write to ${LOCAL_BUILD} ($(stat -c '%U:%G %a' "${bad_file}" 2>/dev/null || echo 'permission denied'))" >&2
    echo "This usually means a prior docker compile created root-owned files in the cmake build tree." >&2
    echo "Fix: sudo chown -R $(id -un):$(id -gn) ${LOCAL_BUILD}" >&2
    exit 1
}

ensure_host_cmake_ready() {
    ensure_build_dir_writable
    if ! command -v cmake >/dev/null 2>&1 || ! command -v ninja >/dev/null 2>&1; then
        echo "error: host cmake and ninja are required for quick build" >&2
        exit 1
    fi
    if cmake_build_requires_ccache && ! host_has_working_ccache; then
        echo "Host ccache unavailable — reconfiguring ${LOCAL_BUILD} with CCACHE_ACTIVE=OFF..."
        cmake "${LOCAL_BUILD}" -DCCACHE_ACTIVE=OFF
    fi
}

run_host_cmake_build() {
    ensure_host_cmake_ready
    cmake --build "${LOCAL_BUILD}" --target "${QUICK_CMAKE_TARGETS[@]}" -j"$(nproc)"
}

if [[ "${BUILD_LOCAL}" -eq 1 ]]; then
    echo "Building on host: ${QUICK_CMAKE_TARGETS[*]}..."
    run_host_cmake_build
fi

if [[ ! -f "${LOCAL_BIN}" ]]; then
    echo "error: ${LOCAL_BIN} not found" >&2
    exit 1
fi

mkdir -p "${STAGED_LIBS}"
rm -f "${STAGED_LIBS}"/*.so
# Atomic replace so Docker BuildKit always sees a new file content/mtime for COPY.
cp -f "${LOCAL_BIN}" "${STAGED_BIN}.new"
mv -f "${STAGED_BIN}.new" "${STAGED_BIN}"
if [[ -f "${LOCAL_BUILD}/nr-uesoftmodem" ]]; then
    cp -f "${LOCAL_BUILD}/nr-uesoftmodem" "${STAGED_UE_BIN}.new"
    mv -f "${STAGED_UE_BIN}.new" "${STAGED_UE_BIN}"
fi
cp -f "${LOCAL_BUILD}"/*.so "${STAGED_LIBS}/"

if [[ ! -f "${STAGED_LIBS}/librfsimulator.so" ]] || [[ ! -f "${STAGED_LIBS}/libparams_yaml.so" ]]; then
    echo "error: staged libs missing (need librfsimulator.so + libparams_yaml.so)" >&2
    exit 1
fi

OAI_BIN_SHA="$(sha256sum "${STAGED_BIN}" | awk '{print $1}')"
echo "Staged nr-softmodem sha256=${OAI_BIN_SHA}"
if grep -aFq "feedback overdue" "${STAGED_BIN}"; then
    echo "Staged nr-softmodem: UCI fix marker present"
else
    echo "warning: staged nr-softmodem missing 'feedback overdue' string (UCI fix may not be compiled in)" >&2
fi

echo "Quick packaging oai-gnb:latest (staged nr-softmodem + FlexRIC SM plugins)..."
cd "${WORKSPACE_DIR}"

BUILD_ARGS=(
    --target oai-gnb
    --tag oai-gnb:latest
    --file "${DOCKERFILE}"
    --build-arg "OAI_BIN_SHA=${OAI_BIN_SHA}"
)
if [[ "${NO_CACHE}" -eq 1 ]]; then
    BUILD_ARGS+=(--no-cache)
fi

docker build "${BUILD_ARGS[@]}" .

docker tag oai-gnb:latest oai-cucp:latest
docker tag oai-gnb:latest oai-du:latest
docker tag oai-gnb:latest oai-gnb:latest-"${ARCH_TAG}"
docker tag oai-cucp:latest oai-cucp:latest-"${ARCH_TAG}"
docker tag oai-du:latest oai-du:latest-"${ARCH_TAG}"

echo "Successfully quick-built oai-gnb from ${LOCAL_BIN} (${ARCH_TAG})"

UE_BUILT=0
if [[ -f "${STAGED_UE_BIN}" ]] && [[ -f "${UE_QUICK_SH}" ]]; then
    bash "${UE_QUICK_SH}" $([[ "${NO_CACHE}" -eq 1 ]] && echo --no-cache)
    UE_BUILT=1
else
    echo "warning: nr-uesoftmodem not staged — UE image not updated (RFsim may mismatch gNB)" >&2
fi

echo
echo "======== quick-build summary ========"
echo "FlexRIC : $([[ "${BUILD_FLEXRIC}" -eq 1 ]] && echo built || echo skipped)"
echo "Host    : $([[ "${BUILD_LOCAL}" -eq 1 ]] && echo "built ${QUICK_CMAKE_TARGETS[*]}" || echo skipped)"
echo "UE img  : $([[ "${UE_BUILT}" -eq 1 ]] && echo updated || echo skipped)"
echo
echo "Built Docker images:"
printf '  %-28s  %-14s  %-10s  %s\n' "IMAGE" "ID" "SIZE" "CREATED"
img_lines="$(docker images --format '{{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}\t{{.CreatedSince}}' \
    | grep -E '^(oai-gnb|oai-cucp|oai-du|oai-nr-ue|oai-flexric):' || true)"
if [[ -z "${img_lines}" ]]; then
    echo "  (none found)"
else
    while IFS=$'\t' read -r img id size created; do
        printf '  %-28s  %-14s  %-10s  %s\n' "${img}" "${id}" "${size}" "${created}"
    done <<<"${img_lines}"
fi
echo
echo "Next: cd nws/scripts && ./bringup.py --no-build"
echo "===================================="