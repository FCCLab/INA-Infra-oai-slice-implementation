---
name: nws-build
description: >-
  Build OAI RAN and 5GC Docker images for the INA network-slicing lab using
  nws/build_scripts. Use when the user asks to build, compile, rebuild, or
  package OAI gNB/CUCP/DU/CU-UP/UE/FlexRIC/SMF images, fix stale Docker
  layers after MAC/scheduler changes, or run bringup with fresh images.
---

# NWS Build

Docker image builders for the INA-Infra-oai-slice-implementation lab. Scripts live in `nws/build_scripts/` and build from sources under the workspace root.

## Prerequisites

- Docker installed and daemon running (`docker info` must succeed)
- Source trees present:
  - `openairinterface5g/` — RAN images
  - `oai-cn5g-fed/component/oai-smf/` — SMF image (optional)

## Quick start

```bash
# Full RAN release (all 6 steps, ~longest)
bash nws/build_scripts/build_release.sh

# Typical incremental after MAC/scheduler edits (what bringup.py uses)
bash nws/build_scripts/build_ran_build.sh
bash nws/build_scripts/build_oai_gnb.sh

# Force full recompile (no Docker layer cache)
bash nws/build_scripts/build_ran_build.sh --no-cache
bash nws/build_scripts/build_oai_gnb.sh --no-cache

# SMF (separate from build_release.sh)
bash nws/build_scripts/build_smf.sh
```

Always run scripts from any cwd — they resolve paths relative to `nws/build_scripts/`.

## Scripts

| Script | Builds | Dockerfile | Notes |
|--------|--------|------------|-------|
| `build_release.sh` | All RAN images | orchestrator | Runs steps 1–6 sequentially |
| `build_ran_base.sh` | `ran-base:latest` | `openairinterface5g/docker/Dockerfile.base.ubuntu` | Base OS/deps layer |
| `build_ran_build.sh` | `ran-build:latest` | `openairinterface5g/docker/Dockerfile.build.ubuntu` | Compiles OAI; accepts `--no-cache` |
| `build_oai_gnb.sh` | `oai-gnb`, `oai-cucp`, `oai-du` | `openairinterface5g/docker/Dockerfile.gNB.ubuntu` | Packages softmodem; accepts `--no-cache` |
| `build_oai_nr_cuup.sh` | `oai-nr-cuup:latest` | `openairinterface5g/docker/Dockerfile.nr-cuup.ubuntu` | Needed for CU/DU split |
| `build_oai_nr_ue.sh` | `oai-nr-ue:latest` | `openairinterface5g/docker/Dockerfile.nrUE.ubuntu` | UE rfsim image |
| `build_oai_flexric.sh` | `oai-flexric:latest` | `openairinterface5g/openair2/E2AP/flexric/docker/Dockerfile.flexric.ubuntu` | nearRT-RIC / xApp |
| `build_smf.sh` | `oai-smf:$TAG` | `oai-cn5g-fed/component/oai-smf/docker/Dockerfile.smf.ubuntu` | Not in `build_release.sh` |

## Build order and dependencies

```
ran-base → ran-build → oai-gnb (also tags oai-cucp, oai-du)
                    ↘ oai-nr-cuup
                    ↘ oai-nr-ue
                    ↘ oai-flexric

oai-smf  (independent; needs git submodules in SMF tree)
```

`build_release.sh` runs: `ran-base` → `ran-build` → `oai-gnb` → `oai-nr-cuup` → `oai-nr-ue` → `oai-flexric`.

## Output tags

Every image gets `latest` plus an arch suffix (`amd64` or `arm64`):

| Image | Aliases |
|-------|---------|
| `oai-gnb:latest` | — |
| `oai-cucp:latest` | same binary as `oai-gnb` |
| `oai-du:latest` | same binary as `oai-gnb` |
| `oai-nr-cuup:latest` | — |
| `oai-nr-ue:latest` | — |
| `oai-flexric:latest` | — |
| `ran-base:latest` | — |
| `ran-build:latest` | — |
| `oai-smf:$TAG` | also `oaisoftwarealliance/oai-smf:$TAG` |

## When to rebuild what

| Change | Rebuild |
|--------|---------|
| MAC scheduler / L2 sources (`openairinterface5g/openair2/`) | `build_ran_build.sh` + `build_oai_gnb.sh` |
| CU-UP only | `build_oai_nr_cuup.sh` |
| UE changes | `build_oai_nr_ue.sh` |
| FlexRIC / E2AP / slice_sm encoder | `build_oai_flexric_quick.sh` (incremental) or `build_oai_flexric.sh` (full) |
| SMF / DNN patches | `build_smf.sh` (bump `--tag` suffix) |
| Fresh machine / corrupted cache | `build_release.sh` or pass `--no-cache` |
| Docker base image / deps | start from `build_ran_base.sh` or full release |

**Stale cache warning:** `docker compose --build` only repackages `ran-build:latest`. After MAC edits, `ran-build` must be recompiled first. Use `--no-cache` when BuildKit reuses old compile layers.

## bringup.py integration

`nws/scripts/bringup.py` calls build scripts automatically unless `--no-build`:

- Default: `build_ran_build.sh` + `build_oai_gnb.sh`
- `--force-rebuild-oai`: adds `--no-cache` to both
- Auto `--no-cache` when OAI sources are newer than `ran-build:latest` image mtime
- `--split` / `--split-mult-upf-cuup`: also runs `build_oai_nr_cuup.sh`
- `--no-ric`: skips FlexRIC compose (image still buildable via `build_oai_flexric.sh`)

```bash
cd nws/scripts
./bringup.py --sch NSDL                    # rebuild if sources newer
./bringup.py --force-rebuild-oai           # always --no-cache
./bringup.py --no-build                    # use existing images only
```

## SMF build options

```bash
bash nws/build_scripts/build_smf.sh --tag v2.2.1-dnn-fix-5   # bump tag after source changes
bash nws/build_scripts/build_smf.sh --no-cache
bash nws/build_scripts/build_smf.sh --skip-submodules        # submodules already init'd

# Use with OAI core compose:
OAI_IMAGE_TAG=v2.2.1-dnn-fix-5 docker compose up -d nws-oai-smf
```

Default tag: `v2.2.1-dnn-fix-4` (override with `--tag` or `IMAGE_TAG` env).

## Agent workflow

1. Identify which component changed (gNB/MAC, CU-UP, UE, FlexRIC, SMF).
2. Run the minimal script set from the table above — do not run `build_release.sh` unless a full rebuild is needed.
3. Pass `--no-cache` after scheduler/MAC changes or when the user reports stale behavior.
4. Verify with `docker images | grep -E 'oai-|ran-'`.
5. For end-to-end validation, run `nws/scripts/bringup.py` (without `--no-build`).

Build timeouts: `ran-build` can take up to ~2 h; `oai-gnb` packaging ~30 min. Run long builds in the background and monitor output.
