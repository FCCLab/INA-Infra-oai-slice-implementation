#!/bin/bash
# Incremental FlexRIC build: compile in cached builder container, package oai-flexric image.
# Rebuilds only changed translation units (ninja). One-time oai-flexric-builder image for toolchain.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE_DIR="$(cd "${SCRIPT_DIR}/../../" && pwd)"
OAI_DIR="${WORKSPACE_DIR}/openairinterface5g"
FLEXRIC_DIR="${OAI_DIR}/openair2/E2AP/flexric"
BUILD_DIR="${FLEXRIC_DIR}/build"
STAGING_DIR="${SCRIPT_DIR}/staging/flexric"
DOCKERFILE="${SCRIPT_DIR}/Dockerfile.flexric.quick.ubuntu"
BUILDER_IMAGE="oai-flexric-builder:latest"
BUILDER_DOCKERFILE="${FLEXRIC_DIR}/docker/Dockerfile.flexric.ubuntu"

E2AP_VERSION="${E2AP_VERSION:-E2AP_V3}"
KPM_VERSION="${KPM_VERSION:-KPM_V3_00}"

NO_CACHE=0
BUILD_LOCAL=1
FORCE_BUILDER=0

# SM plugins + nearRT-RIC (+ xApp SDK when multilanguage is enabled at configure time).
FLEXRIC_NINJA_TARGETS=(
    slice_sm kpm_sm rc_sm mac_sm rlc_sm pdcp_sm gtp_sm tc_sm
    nearRT-RIC xapp_sdk
)

usage() {
    echo "Usage: $0 [--no-cache] [--no-local-build] [--force-builder]"
    echo "  --no-cache         docker build --no-cache for oai-flexric packaging layer"
    echo "  --no-local-build   skip ninja (artifacts must already exist in ${BUILD_DIR})"
    echo "  --force-builder    rebuild oai-flexric-builder toolchain image"
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
        --force-builder)
            FORCE_BUILDER=1
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

ensure_builder_image() {
    if [[ "${FORCE_BUILDER}" -eq 1 ]] || ! docker image inspect "${BUILDER_IMAGE}" &>/dev/null; then
        echo "Building ${BUILDER_IMAGE} (toolchain; one-time unless --force-builder)..."
        docker build \
            --target oai-flexric-builder \
            --tag "${BUILDER_IMAGE}" \
            --build-arg BASE_IMAGE=ubuntu:noble \
            --build-arg E2AP_VERSION="${E2AP_VERSION}" \
            --build-arg KPM_VERSION="${KPM_VERSION}" \
            --file "${BUILDER_DOCKERFILE}" \
            "${FLEXRIC_DIR}"
    fi
}

# Ubuntu noble/multiarch: FindCURL needs pkg-config (or explicit -DCURL_*) when cmake
# regenerates during ninja. Older builder images may lack these packages.
ensure_builder_curl_deps() {
    if docker run --rm "${BUILDER_IMAGE}" bash -lc \
        'command -v pkg-config >/dev/null \
         && pkg-config --exists libcurl \
         && (test -f /usr/include/curl/curl.h || ls /usr/include/*/curl/curl.h >/dev/null 2>&1)'; then
        return 0
    fi
    echo "Patching ${BUILDER_IMAGE} with pkg-config + libcurl4-gnutls-dev (FindCURL)..."
    local tmpdir
    tmpdir="$(mktemp -d)"
    cat >"${tmpdir}/Dockerfile" <<'EOF'
ARG BASE
FROM ${BASE}
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends \
        pkg-config \
        libcurl4-gnutls-dev && \
    apt-get clean && rm -rf /var/lib/apt/lists/*
EOF
    docker build --build-arg "BASE=${BUILDER_IMAGE}" -t "${BUILDER_IMAGE}" "${tmpdir}"
    rm -rf "${tmpdir}"
}

# Resolve CURL paths inside the container so cmake regenerate cannot fail FindCURL.
flexric_cmake_configure() {
    mkdir -p "${BUILD_DIR}"
    docker run --rm \
        -v "${FLEXRIC_DIR}:/flexric" \
        -w /flexric/build \
        "${BUILDER_IMAGE}" \
        bash -lc "
set -euo pipefail
# Prefer exact multiarch paths; avoid 'ls a b' (missing path => rc=2 + pipefail silent exit).
CURL_H=
for cand in /usr/include/curl/curl.h /usr/include/*/curl/curl.h; do
  if [[ -f \"\${cand}\" ]]; then CURL_H=\"\${cand}\"; break; fi
done
if [[ -z \"\${CURL_H}\" ]]; then
  echo 'error: curl.h not found in ${BUILDER_IMAGE} (install libcurl4-gnutls-dev)' >&2
  exit 1
fi
# curl.h lives at .../curl/curl.h — FindCURL wants the parent of the curl/ dir.
CURL_INCLUDE_DIR=\$(dirname \"\$(dirname \"\${CURL_H}\")\")
CURL_LIBRARY=
for cand in /usr/lib/*/libcurl.so /usr/lib/libcurl.so; do
  if [[ -e \"\${cand}\" ]]; then CURL_LIBRARY=\"\${cand}\"; break; fi
done
if [[ -z \"\${CURL_LIBRARY}\" ]]; then
  echo 'error: libcurl.so not found in ${BUILDER_IMAGE}' >&2
  exit 1
fi
echo \"FlexRIC cmake: CURL_INCLUDE_DIR=\${CURL_INCLUDE_DIR} CURL_LIBRARY=\${CURL_LIBRARY}\"
cmake -GNinja -DCMAKE_BUILD_TYPE=Release \
  -DE2AP_VERSION=${E2AP_VERSION} \
  -DKPM_VERSION=${KPM_VERSION} \
  -DXAPP_MULTILANGUAGE=ON \
  -DCMAKE_C_COMPILER=gcc-12 \
  -DCMAKE_CXX_COMPILER=g++-12 \
  -DCURL_INCLUDE_DIR=\"\${CURL_INCLUDE_DIR}\" \
  -DCURL_LIBRARY=\"\${CURL_LIBRARY}\" \
  ..
"
}

configure_flexric_if_needed() {
    local need_cfg=0
    if [[ ! -f "${BUILD_DIR}/CMakeCache.txt" ]]; then
        need_cfg=1
    elif ! grep -qE '^CURL_(LIBRARY|LIBRARY_RELEASE):FILEPATH=.+/libcurl\.so' \
        "${BUILD_DIR}/CMakeCache.txt" 2>/dev/null; then
        echo "FlexRIC CMakeCache missing CURL — reconfiguring..."
        need_cfg=1
    elif grep -qE '^CURL_(LIBRARY|LIBRARY_RELEASE|INCLUDE_DIR):.*=.*NOTFOUND' \
        "${BUILD_DIR}/CMakeCache.txt" 2>/dev/null; then
        echo "FlexRIC CMakeCache has CURL NOTFOUND — reconfiguring..."
        need_cfg=1
    fi
    if [[ "${need_cfg}" -eq 1 ]]; then
        echo "Configuring FlexRIC cmake in ${BUILD_DIR}..."
        flexric_cmake_configure
    fi
}

run_flexric_ninja() {
    echo "Incremental FlexRIC build: ${FLEXRIC_NINJA_TARGETS[*]}..."
    local ninja_log
    ninja_log="$(mktemp)"
    set +e
    docker run --rm \
        -v "${FLEXRIC_DIR}:/flexric" \
        -w /flexric/build \
        "${BUILDER_IMAGE}" \
        bash -lc "ninja ${FLEXRIC_NINJA_TARGETS[*]} -j\$(nproc)" 2>&1 | tee "${ninja_log}"
    local rc=${PIPESTATUS[0]}
    set -e
    if [[ "${rc}" -ne 0 ]]; then
        if grep -qE 'Could NOT find CURL|FindCURL|CURL_LIBRARY|CURL_INCLUDE_DIR' "${ninja_log}"; then
            echo "ninja cmake regenerate failed FindCURL — reconfiguring with explicit CURL paths and retrying..."
            flexric_cmake_configure
            rm -f "${ninja_log}"
            docker run --rm \
                -v "${FLEXRIC_DIR}:/flexric" \
                -w /flexric/build \
                "${BUILDER_IMAGE}" \
                bash -lc "ninja ${FLEXRIC_NINJA_TARGETS[*]} -j\$(nproc)"
        else
            rm -f "${ninja_log}"
            exit "${rc}"
        fi
    fi
    rm -f "${ninja_log}"
}

stage_flexric_artifacts() {
    local near_ric="${BUILD_DIR}/examples/ric/nearRT-RIC"
    local xapp_so="${BUILD_DIR}/src/xApp/libe42_xapp_shared.so"
    local kpm_meas="${BUILD_DIR}/28_552_kpm_meas.txt"

    if [[ ! -f "${near_ric}" ]]; then
        echo "error: ${near_ric} missing — run without --no-local-build" >&2
        exit 1
    fi
    if [[ ! -f "${xapp_so}" ]]; then
        echo "error: ${xapp_so} missing — run without --no-local-build" >&2
        exit 1
    fi

    rm -rf "${STAGING_DIR}"
    mkdir -p \
        "${STAGING_DIR}/bin" \
        "${STAGING_DIR}/lib/flexric" \
        "${STAGING_DIR}/xApp/python3" \
        "${STAGING_DIR}/xApp/c" \
        "${STAGING_DIR}/emulator/agent" \
        "${STAGING_DIR}/etc/flexric"

    cp -f "${near_ric}" "${STAGING_DIR}/bin/nearRT-RIC"
    cp -f "${xapp_so}" "${STAGING_DIR}/lib/libe42_xapp_shared.so"

    while IFS= read -r -d '' so; do
        cp -f "${so}" "${STAGING_DIR}/lib/flexric/"
    done < <(find "${BUILD_DIR}" -path '*/src/sm/*' -name 'lib*_sm.so' -print0)

    if [[ ! -f "${STAGING_DIR}/lib/flexric/libslice_sm.so" ]]; then
        echo "error: staged libslice_sm.so missing under ${STAGING_DIR}/lib/flexric" >&2
        exit 1
    fi

    if [[ -f "${BUILD_DIR}/examples/xApp/python3/xapp_sdk.py" ]]; then
        cp -f "${BUILD_DIR}/examples/xApp/python3/xapp_sdk.py" "${STAGING_DIR}/xApp/python3/"
    fi
    shopt -s nullglob
    for f in "${BUILD_DIR}/examples/xApp/python3"/_xapp_sdk*.so; do
        cp -f "${f}" "${STAGING_DIR}/xApp/python3/"
    done
    shopt -u nullglob

    if [[ -d "${BUILD_DIR}/examples/xApp/c" ]]; then
        cp -a "${BUILD_DIR}/examples/xApp/c/." "${STAGING_DIR}/xApp/c/" 2>/dev/null || true
    fi
    if [[ -d "${BUILD_DIR}/examples/emulator/agent" ]]; then
        find "${BUILD_DIR}/examples/emulator/agent" -maxdepth 1 -type f -executable \
            -exec cp -f {} "${STAGING_DIR}/emulator/agent/" \;
    fi
    if [[ -f "${kpm_meas}" ]]; then
        cp -f "${kpm_meas}" "${STAGING_DIR}/28_552_kpm_meas.txt"
    fi
    if [[ -f "${FLEXRIC_DIR}/flexric.conf" ]]; then
        cp -f "${FLEXRIC_DIR}/flexric.conf" "${STAGING_DIR}/etc/flexric/flexric.conf"
    fi
    if [[ -f "${WORKSPACE_DIR}/nws/configs/flexric/flexric.conf" ]]; then
        cp -f "${WORKSPACE_DIR}/nws/configs/flexric/flexric.conf" "${STAGING_DIR}/etc/flexric/flexric.conf"
    fi

    echo "Staged FlexRIC artifacts:"
    ls -la "${STAGING_DIR}/lib/flexric/"
}

ensure_builder_image
ensure_builder_curl_deps

if [[ "${BUILD_LOCAL}" -eq 1 ]]; then
    configure_flexric_if_needed
    run_flexric_ninja
fi

stage_flexric_artifacts

echo "Quick packaging oai-flexric:latest (incremental ninja + staged artifacts)..."
cd "${WORKSPACE_DIR}"

BUILD_ARGS=(
    --target oai-flexric
    --tag oai-flexric:latest
    --file "${DOCKERFILE}"
)
if [[ "${NO_CACHE}" -eq 1 ]]; then
    BUILD_ARGS+=(--no-cache)
fi

docker build "${BUILD_ARGS[@]}" .

docker tag oai-flexric:latest "oai-flexric:latest-${ARCH_TAG}"
echo "Successfully quick-built oai-flexric:latest (${ARCH_TAG})"
