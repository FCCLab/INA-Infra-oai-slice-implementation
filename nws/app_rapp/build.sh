#!/usr/bin/env bash
# Build the nws rApp container image from this directory.
#
# Usage:
#   ./build.sh
#   ./build.sh --tag 0.0.1
#   ./build.sh --repo 10.1.110.84:5000 --name nws-rapp --tag 0.0.1 --push
#   ./build.sh no-push
#   ./build.sh 10.1.110.84:5000 --tag 0.0.1
#
# If dockerd is not running, falls back to a Kaniko Job on the local Kubernetes
# node and (for no-push) imports the tarball into containerd.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

IMAGE_NAME="${IMAGE_NAME:-nws-rapp}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
REPO="${REPO:-}"
PUSH=0
NO_CACHE=0
KANIKO_IMAGE="${KANIKO_IMAGE:-gcr.io/kaniko-project/executor:v1.23.2}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [no-push|<registry>] [options]

  no-push | --no-push     Build locally, do not push (default)
  <registry>              Registry prefix, e.g. 10.1.110.84:5000 or localhost:5000
  --repo URL              Same as <registry>
  --name NAME             Image name (default: ${IMAGE_NAME})
  --tag TAG               Image tag (default: ${IMAGE_TAG})
  --push                  Push after build (implied if a registry is given)
  --no-cache              Disable build cache
  -h, --help              Show this help

Env: IMAGE_NAME, IMAGE_TAG, REPO, KANIKO_IMAGE
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help) usage; exit 0 ;;
    no-push|--no-push) PUSH=0; shift ;;
    --push) PUSH=1; shift ;;
    --repo) REPO="$2"; PUSH=1; shift 2 ;;
    --name) IMAGE_NAME="$2"; shift 2 ;;
    --tag) IMAGE_TAG="$2"; shift 2 ;;
    --no-cache) NO_CACHE=1; shift ;;
    --*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      if [[ -z "${REPO}" ]]; then
        REPO="$1"
        PUSH=1
      else
        echo "Unknown argument: $1" >&2
        usage >&2
        exit 2
      fi
      shift
      ;;
  esac
done

LOCAL_IMAGE="${IMAGE_NAME}:${IMAGE_TAG}"
if [[ -n "${REPO}" ]]; then
  DEST_IMAGE="${REPO}/${IMAGE_NAME}:${IMAGE_TAG}"
else
  DEST_IMAGE="${LOCAL_IMAGE}"
fi

docker_ready() {
  docker info >/dev/null 2>&1
}

insecure_flags_kaniko() {
  # HTTP registries (no TLS) used in this lab.
  if [[ "${DEST_IMAGE}" == 10.*:*/* ]] || [[ "${DEST_IMAGE}" == localhost:*/* ]] || [[ "${DEST_IMAGE}" == 127.0.0.1:*/* ]]; then
    echo "--insecure --insecure-pull --skip-tls-verify"
  fi
}

build_with_docker() {
  echo "==> docker build ${LOCAL_IMAGE}"
  local args=(-t "${LOCAL_IMAGE}" -f Dockerfile .)
  if [[ "${NO_CACHE}" == "1" ]]; then
    args=(--no-cache "${args[@]}")
  fi
  docker build "${args[@]}"
  echo "BUILD OK: ${LOCAL_IMAGE}"

  if [[ -n "${REPO}" ]]; then
    echo "==> docker tag ${LOCAL_IMAGE} -> ${DEST_IMAGE}"
    docker tag "${LOCAL_IMAGE}" "${DEST_IMAGE}"
  fi
  if [[ "${PUSH}" == "1" ]]; then
    echo "==> docker push ${DEST_IMAGE}"
    docker push "${DEST_IMAGE}"
    echo "PUSH OK: ${DEST_IMAGE}"
  fi
}

build_with_kaniko() {
  local ns="${KANIKO_NAMESPACE:-default}"
  local job="nws-rapp-build-$$"
  local tar_host="${SCRIPT_DIR}/out/nws-rapp-${IMAGE_TAG}.tar"
  mkdir -p "${SCRIPT_DIR}/out"

  echo "==> docker unavailable; building with Kaniko Job ${ns}/${job}"
  echo "    destination: ${DEST_IMAGE}"

  local dest_arg
  if [[ "${PUSH}" == "1" ]]; then
    dest_arg="${DEST_IMAGE}"
  else
    dest_arg="${LOCAL_IMAGE}"
  fi

  local extra_args=""
  extra_args+=" $(insecure_flags_kaniko)"
  if [[ "${NO_CACHE}" == "1" ]]; then
    extra_args+=" --no-cache"
  fi
  if [[ "${PUSH}" != "1" ]]; then
    extra_args+=" --no-push --tarPath=/out/nws-rapp.tar"
  fi

  kubectl delete job "${job}" -n "${ns}" --ignore-not-found >/dev/null 2>&1 || true

  kubectl apply -n "${ns}" -f - <<EOF
apiVersion: batch/v1
kind: Job
metadata:
  name: ${job}
spec:
  backoffLimit: 1
  ttlSecondsAfterFinished: 300
  template:
    spec:
      restartPolicy: Never
      containers:
      - name: kaniko
        image: ${KANIKO_IMAGE}
        imagePullPolicy: IfNotPresent
        args:
        - --dockerfile=/workspace/Dockerfile
        - --context=/workspace
        - --destination=${dest_arg}
        - --snapshot-mode=redo
$(for a in ${extra_args}; do echo "        - ${a}"; done)
        volumeMounts:
        - name: workspace
          mountPath: /workspace
        - name: out
          mountPath: /out
      volumes:
      - name: workspace
        hostPath:
          path: ${SCRIPT_DIR}
          type: Directory
      - name: out
        hostPath:
          path: ${SCRIPT_DIR}/out
          type: Directory
EOF

  echo "==> waiting for Kaniko Job"
  if ! kubectl wait -n "${ns}" --for=condition=complete "job/${job}" --timeout=600s; then
    echo "BUILD FAILED" >&2
    kubectl logs -n "${ns}" "job/${job}" --tail=80 >&2 || true
    kubectl delete job "${job}" -n "${ns}" --ignore-not-found >/dev/null 2>&1 || true
    exit 1
  fi
  kubectl logs -n "${ns}" "job/${job}" --tail=40 || true
  kubectl delete job "${job}" -n "${ns}" --ignore-not-found >/dev/null 2>&1 || true

  if [[ "${PUSH}" != "1" ]]; then
    if [[ ! -s "${tar_host}" ]]; then
      # kaniko wrote /out/nws-rapp.tar
      if [[ -s "${SCRIPT_DIR}/out/nws-rapp.tar" ]]; then
        mv -f "${SCRIPT_DIR}/out/nws-rapp.tar" "${tar_host}"
      fi
    fi
    if [[ ! -s "${tar_host}" ]]; then
      echo "BUILD FAILED: Kaniko tar not found at ${tar_host}" >&2
      exit 1
    fi
    echo "==> importing ${tar_host} into containerd (k8s.io)"
    if sudo -n ctr -n k8s.io images import "${tar_host}" >/dev/null; then
      echo "IMPORT OK: ${LOCAL_IMAGE}"
    else
      echo "Tar saved at ${tar_host} (ctr import needs sudo). Load with:"
      echo "  sudo ctr -n k8s.io images import ${tar_host}"
    fi
  fi
  echo "BUILD OK: ${DEST_IMAGE}"
}

if docker_ready; then
  build_with_docker
else
  echo "note: dockerd is not running; using Kaniko on Kubernetes"
  build_with_kaniko
fi

echo "IMAGE OK: ${DEST_IMAGE}"
echo "DONE"
