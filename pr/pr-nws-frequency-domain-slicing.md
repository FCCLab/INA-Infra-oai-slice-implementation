<!--
PR metadata (not shown on GitHub)
  Title:  Add frequency-domain network slicing (SCHE_NS)
  Repo:    duranta-project/openairinterface5g
  Base:    develop
  Head:    FCCLab:nws-3gpp-impl-no-flexric
  Label:   5G-NR, gNB (see pr/label.list)
  Compare: https://github.com/duranta-project/openairinterface5g/compare/develop...FCCLab:openairinterface5g-duranta:nws-3gpp-impl-no-flexric

  gh pr create \
    --repo duranta-project/openairinterface5g \
    --base develop \
    --head FCCLab:nws-3gpp-impl-no-flexric \
    --title "Add frequency-domain network slicing (SCHE_NS)" \
    --label "5G-NR" \
    --label "gNB" \
    --body-file pr/pr-nws-frequency-domain-slicing.md
-->

## Summary

This PR adds **frequency-domain network slicing** to the NR gNB MAC scheduler,
aligned with 3GPP S-NSSAI concepts and the existing modular proportional-fair
scheduler pipeline.

### What it does

- Introduces scheduler type `SCHE_NS` (independent DL/UL configuration via
  `dl_scheduler_type` / `ul_scheduler_type` in gNB YAML)
- Allocates per-slice PRB windows using a four-pass **dedicated / min / max**
  ratio policy (`slice_prb_allocator/`)
- Maps UEs to slices via S-NSSAI (SST + SD) from DRB/RRC configuration
- Reuses the modular PF pipeline **inside each slice** for intra-slice UE
  scheduling
- Extends the E2 Slice RAN function for runtime policy read/write
- Adds telnet slice statistics and a 5-slice rfsim CI config
- Documents the feature in `doc/network_slice_3gpp_impl.md`

### Architecture

Two-layer design:

1. **Slice layer** — PRB allocator assigns a contiguous frequency window per
   slice each slot (`nr_*_schedule_ns`)
2. **UE layer** — standard modular PF scheduler runs once per slice inside that
   window (`nr_*_schedule` with slice RB bounds)

```
Slot
 └─ nr_*sch_preprocessor
     ├─ SCHE_PF → nr_*_schedule()           (unchanged default path)
     └─ SCHE_NS → nr_*_schedule_ns()
                    ├─ slice_prb_allocator  (Pass 1–4: ded/min/max)
                    └─ per-slice nr_*_schedule(slice_prb, remainUEs)
```

### Scheduler modes

| Mode | DL | UL | Notes |
|------|----|----|-------|
| `NSUL` | PF | NS | Common NWS lab default |
| `NSDL` | NS | PF | DL NS shares `remainUEs` across slices |
| `NSBOTH` | NS | NS | Both directions sliced |
| `PF` | PF | PF | Default; slice YAML ignored for scheduler |

### Scope

| In scope | Out of scope |
|----------|--------------|
| Per-slice PRB range allocation (DL/UL) | Per-slice CORESET / PDCCH search spaces |
| S-NSSAI UE-to-slice mapping | CN slice selection (NSSF) |
| `dedicated` / `min` / `max` PRB ratios | L1 beam / cell-level slicing |
| E2 Slice SM policy read/write | End-to-end orchestration (separate repo) |

### Example YAML

```yaml
MACRLCs:
  - dl_scheduler_type: 0   # 0 = SCHE_PF, 1 = SCHE_NS
    ul_scheduler_type: 1

Slices:
  - slice_id: 1
    sst: 1
    sd: 0x000001
    dedicated_prb_ratio: 0.15
    min_prb_ratio: 0.15
    max_prb_ratio: 0.30
```

## Demo (5 UEs, 5 slices, rfsim)

### Lab setup

| Parameter | Value |
|-----------|-------|
| gNB | OAI rfsim, 133 PRB, band 78 |
| Scheduler | `NSBOTH` (`dl_scheduler_type: 1`, `ul_scheduler_type: 1`) |
| UEs | 5 UEs, one per slice (SST=1, SD=0x01 … 0x05) |
| Core | Open5GS with matching S-NSSAI per subscriber |
| Traffic | Full-traffic runs: simultaneous `iperf3` DL on all 5 UEs |

Each screenshot shows gNB MAC scheduler output with:

1. **NS UL/DL PRB allocation** — per-slice PRB window (`latest` / `avg`), PRB range
   `[start,end]`, utilization %, and configured `dedicated/min/max` ratios
2. **Per-UE stats** — RNTI, S-NSSAI, DL/UL MCS, BLER, and **goodput** (Mbps)

The five ratios in each heading are the per-slice policy values for slices
SD=0x01 … 0x05 (left to right). For example, `15 / 15 / 15 / 15 / 7 %` means
slice 1–4 each get 15% and slice 5 gets 7% of the configured ratio type.

### Dedicated policy

`dedicated_prb_ratio` is set per slice; `min` and `max` equal `dedicated` (no
shareable pool). PRBs are **always reserved** in Pass 1, even when a slice has
no traffic.

**What to look for**

- **Idle:** each active slice shows a fixed PRB window and non-zero `avg` allocation
  despite ~0% utilization — bandwidth is held, not released to other slices.
- **Full traffic:** each UE's goodput stays within its slice window; slices do not
  borrow from neighbours because dedicated resources are non-shareable.

#### 15 / 15 / 15 / 15 / 15 %

Equal 15% dedicated per slice (≈20 PRBs each on 133 PRB). Under load all five
UEs receive similar DL goodput (~20–24 Mbps).

| Idle (no traffic) | Full traffic (iperf) |
|---|---|
| ![dedicated 15-15 idle](https://raw.githubusercontent.com/FCCLab/INA-Infra-oai-slice-implementation/main/imgs/nws-5ues-dedicated-15-15-15-15-15-no-traffic.png) | ![dedicated 15-15 full](https://raw.githubusercontent.com/FCCLab/INA-Infra-oai-slice-implementation/main/imgs/nws-5ues-dedicated-15-15-15-15-15-full-traffic.png) |

#### 15 / 15 / 15 / 15 / 7 %

Asymmetric dedicated split: slice 5 gets a smaller guaranteed window (7% ≈ 9 PRBs).
Under load, UE on slice 5 achieves lower goodput than the other four.

| Idle (no traffic) | Full traffic (iperf) |
|---|---|
| ![dedicated 15-7 idle](https://raw.githubusercontent.com/FCCLab/INA-Infra-oai-slice-implementation/main/imgs/nws-5ues-dedicated-15-15-15-15-7-no-traffic.png) | ![dedicated 15-7 full](https://raw.githubusercontent.com/FCCLab/INA-Infra-oai-slice-implementation/main/imgs/nws-5ues-dedicated-15-15-15-15-7-full-traffic.png) |

### Min policy

`min_prb_ratio` is set per slice; `dedicated = 0`, `max = 100%`. The shareable
part above dedicated is allocated in Pass 2 only when the slice needs PRBs.

**What to look for**

- **Idle:** reserved min windows are visible but utilization stays near 0%; unused
  shareable PRBs can be used by other slices.
- **Full traffic:** each active slice receives at least its min share; slices with
  higher min ratios get proportionally more PRBs when all UEs are backlogged.

#### 20 / 20 / 20 / 20 / 20 %

Equal 20% minimum per slice (≈27 PRBs each). Under full load all five UEs achieve
similar goodput (~26–29 Mbps DL).

| Idle (no traffic) | Full traffic (iperf) |
|---|---|
| ![min 20-20 idle](https://raw.githubusercontent.com/FCCLab/INA-Infra-oai-slice-implementation/main/imgs/nws-5ues-min-20-20-20-20-20-no-traffic.png) | ![min 20-20 full](https://raw.githubusercontent.com/FCCLab/INA-Infra-oai-slice-implementation/main/imgs/nws-5ues-min-20-20-20-20-20-full-traffic.png) |

#### 20 / 20 / 20 / 20 / 10 %

Slice 5 has a lower min (10% ≈ 13 PRBs). Under load, UE on slice 5 gets roughly
half the goodput of the other four (~13 vs ~27 Mbps DL).

| Idle (no traffic) | Full traffic (iperf) |
|---|---|
| ![min 20-10 idle](https://raw.githubusercontent.com/FCCLab/INA-Infra-oai-slice-implementation/main/imgs/nws-5ues-min-20-20-20-20-10-no-traffic.png) | ![min 20-10 full](https://raw.githubusercontent.com/FCCLab/INA-Infra-oai-slice-implementation/main/imgs/nws-5ues-min-20-20-20-20-10-full-traffic.png) |

### Max policy

`max_prb_ratio` is set per slice; `dedicated = 0`, `min = 0`. Slices compete for
the full PRB pool in Pass 3, but no slice can exceed its max cap.

**What to look for**

- **Idle:** no PRBs are reserved; all slices show 0% utilization.
- **Full traffic:** slices grow up to their max cap; when caps differ, lower-cap
  slices are throttled while higher-cap slices consume more of the cell bandwidth.

#### 100 / 100 / 100 / 100 / 100 %

No cap — all slices can use the full 133 PRBs. Under contention each UE gets
similar goodput (~27–29 Mbps DL) as the PF scheduler shares the cell fairly.

| Idle (no traffic) | Full traffic (iperf) |
|---|---|
| ![max 100-100 idle](https://raw.githubusercontent.com/FCCLab/INA-Infra-oai-slice-implementation/main/imgs/nws-5ues-max-100-100-100-100-100-no-traffic.png) | ![max 100-100 full](https://raw.githubusercontent.com/FCCLab/INA-Infra-oai-slice-implementation/main/imgs/nws-5ues-max-100-100-100-100-100-full-traffic.png) |

#### 100 / 100 / 100 / 50 / 50 %

Slices 1–3 uncapped; slices 4–5 capped at 50%. Under load, UEs on slices 4–5
achieve ~13 Mbps DL while slices 1–3 reach ~27–29 Mbps — the max cap is enforced.

| Idle (no traffic) | Full traffic (iperf) |
|---|---|
| ![max 50-50 idle](https://raw.githubusercontent.com/FCCLab/INA-Infra-oai-slice-implementation/main/imgs/nws-5ues-max-100-100-100-50-50-no-traffic.png) | ![max 50-50 full](https://raw.githubusercontent.com/FCCLab/INA-Infra-oai-slice-implementation/main/imgs/nws-5ues-max-100-100-100-50-50-full-traffic.png) |

## Changes (37 files, +8855 / −108)

| Area | Key files |
|------|-----------|
| PRB allocator (new) | `openair2/LAYER2/NR_MAC_gNB/slice_prb_allocator/` |
| MAC scheduler | `gNB_scheduler_dlsch.c`, `gNB_scheduler_ulsch.c`, `main.c` |
| gNB config | `gnb_config.c`, `gnb_paramdef.h`, `MACRLC_nr_paramdef.h` |
| E2 control | `ran_func_slice.c`, `oai_ns_slice_ie.h` |
| RLC NSSAI API | `nr_rlc_oai_api.c`, `nr_rlc_oai_api.h` |
| Observability | `telnetsrv_proccmd.c` |
| CI | `ci-scripts/yaml_files/5g_sa_nws/gnb.sa.band78.133prb.rfsim5slices.nsboth.yaml` |
| Docs | `doc/network_slice_3gpp_impl.md`, `slice_prb_allocator/README.md` |

## Test plan

- [ ] Unit tests:
      `cd openair2/LAYER2/NR_MAC_gNB/slice_prb_allocator && make test`
- [ ] Build gNB with default config (`SCHE_PF`) — no regression
- [ ] rfsim 5-slice config:
      `ci-scripts/yaml_files/5g_sa_nws/gnb.sa.band78.133prb.rfsim5slices.nsboth.yaml`
- [ ] E2 Slice SM read/write via `ran_func_slice.c`
- [ ] Confirm `dedicated ≤ min ≤ max` and sum of dedicated ratios ≤ 1.0 enforced at config time

## Documentation

- Feature overview: `doc/network_slice_3gpp_impl.md`
- Allocator algorithm: `openair2/LAYER2/NR_MAC_gNB/slice_prb_allocator/README.md`
- Scheduler pipeline: `doc/MAC/scheduler-architecture.md` (updated)

## Notes for reviewers

- End-to-end lab assets (Open5GS compose, xApp, e2e scripts) live in a separate
  INA-Infra workspace and are **not** part of this PR.
- CORESET / PDCCH remain cell-wide; multi-slice experiments require adequate
  `coreset_duration` and `uess_agg_levels` (documented in feature doc).
- Default scheduler path (`SCHE_PF`) is unchanged when `dl/ul_scheduler_type: 0`.
