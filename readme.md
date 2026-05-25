# Network Slicing Workspace

This repository is a workspace for 5G network-slicing experiments and demos. It brings together the main RAN codebase, a containerized 5G core, slice orchestration scripts, visualization tools, and a few supporting utilities in one place.

The root README is meant to help you navigate the workspace quickly. Each major component also has its own README with setup and usage details.

## Main components

- `openairinterface5g/`
  OpenAirInterface 5G RAN sources and documentation. This is the main RAN codebase used for gNB, UE, E2, and slice-related work.

- `nws/`
  Network-slicing orchestration assets, including Docker Compose stacks, automation scripts, and an Open5GS-based 5G core setup.

- `resource-grid-visualizer/`
  A visualization stack with a Vite frontend, FastAPI backend, MediaMTX streaming, and optional CUDA-based resource-grid rendering.

## Recommended entry points

- RAN build and run docs:
  `openairinterface5g/README.md`

- Open5GS 5G core:
  `nws/5gc/open5gs/README.md`

- End-to-end slicing flow:
  `nws/scripts/README-e2e-slice.md`

- Resource-grid visualizer:
  `resource-grid-visualizer/readme.md`

- Pegatron dongle utility:
  `nws/pegatron-5g-dongle/README.md`

## Common workflows

### 1. Bring up the 5G core

From `nws/5gc/open5gs/`:

```bash
docker compose up -d
```

Use the component README for ports, subscriber configuration, and validation steps.

### 2. Work with the OAI RAN

Start with `openairinterface5g/README.md`.

That documentation links to the OAI build, runtime, and feature-set guides.

### 3. Run the end-to-end slicing demo

From `nws/`:

```bash
python3 scripts/e2e_nw_slice_docker.py
```

This workflow expects the Open5GS core network to be available first and uses the compose assets under `nws/`.

### 4. Launch the resource-grid visualizer

From `resource-grid-visualizer/`:

```bash
docker compose -f compose/docker-compose.yml watch
```

Open `http://127.0.0.1:5173` after the stack starts.

## Suggested prerequisites

Depending on which part of the workspace you use, you may need:

- Docker and Docker Compose
- Python 3
- `iperf3`
- NVIDIA Container Toolkit for GPU-backed visualization flows
- A Linux host suitable for containerized 5G networking workloads

## Notes

- This workspace contains multiple related projects rather than a single application.
- Slice-specific RAN work is primarily under `openairinterface5g/` and `nws/`.
- For deeper details, prefer the README inside the component you are actively working on.
