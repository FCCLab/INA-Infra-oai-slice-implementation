# 3GPP 5G Network Slicing Live Test Suite Specification (PR #451)

This specification defines the complete 12-test suite covering **both Idle and Full traffic** for all symmetric and asymmetric configurations directly taken from **duranta-project/openairinterface5g Pull Request #451** (*Add frequency-domain network slicing SCHE_NS*).

---

## 1. Testbed Baseline & Environment

| Parameter | Value |
| :--- | :--- |
| **Carrier Bandwidth** | 133 PRBs (Band n78, 3.325 GHz, Subcarrier Spacing 30 kHz, Numerology 1) |
| **Scheduler Type** | `NSBOTH` (`dl_scheduler_type: 1`, `ul_scheduler_type: 1`) |
| **Core Network** | Open5GS (`nws-5gc`), UPF tun IP `10.45.0.1` |
| **S-NSSAI Slice Mapping** | Slice 1: `SST=1, SD=0x000001` (UE 1: `10.45.0.31`)<br>Slice 2: `SST=1, SD=0x000002` (UE 2: `10.45.0.32`)<br>Slice 3: `SST=1, SD=0x000003` (UE 3: `10.45.0.33`)<br>Slice 4: `SST=1, SD=0x000004` (UE 4: `10.45.0.34`)<br>Slice 5: `SST=1, SD=0x000005` (UE 5: `10.45.0.35`) |
| **Traffic Generator** | `iperf3` bursts over PDU session (`-t 30 -P 5`, `-b 10M` for UL, `-b 20M` for DL) |

---

## 2. Complete Test Matrix: Idle & Full Traffic

```
Pass 1: Dedicated PRBs (Non-shareable reservation, held even when idle)
Pass 2: Min PRBs       (Prioritized shareable pool, surrendered when idle)
Pass 3: Max PRBs       (Competitive pool up to ceiling cap)
Pass 4: Shared Pool    (Remaining PRBs distributed proportionally)
```

---

### Group 000: No Slice PF Policy (Pure Proportional Fair, sch=PF)

#### TC-000: No Slice PF Policy - 30s 5-UE Ping (`pf_only` / `000`)
* **RAN Configuration:** `sch = PF` (`dl_scheduler_type: 0`, `ul_scheduler_type: 0`). Slicing algorithm completely bypassed.
* **Test Input:** 30-second continuous ICMP ping from all 5 UEs (`nws-oai-nr-ue1..5`) to UPF core gateway (`10.45.0.1`).
* **Pass Criteria:**
  - Packet Loss $\le 5.0\%$ across all 5 UEs.
  - Round-Trip Time (RTT) Average $< 50\text{ ms}$.

#### TC-001: No Slice PF Policy - DL UDP Full Traffic (`pf_dl_udp` / `001`)
* **RAN Configuration:** `sch = PF` (`dl_scheduler_type: 0`, `ul_scheduler_type: 0`).
* **Traffic:** Downlink UDP traffic (`iperf3 -u -b 20M -P 5 -R`) across all 5 UEs simultaneously.
* **Pass Criteria:**
  - Proportional fair scheduler balances bandwidth evenly across all 5 active UEs (~12.0% – 28.0% share each).

#### TC-002: No Slice PF Policy - DL TCP Full Traffic (`pf_dl_tcp` / `002`)
* **RAN Configuration:** `sch = PF` (`dl_scheduler_type: 0`, `ul_scheduler_type: 0`).
* **Traffic:** Downlink TCP traffic (`iperf3 -P 5 -R`) across all 5 UEs simultaneously.
* **Pass Criteria:**
  - Multi-stream TCP congestion control achieves high throughput with balanced ~12.0% – 32.0% share each.

#### TC-003: No Slice PF Policy - UL UDP Full Traffic (`pf_ul_udp` / `003`)
* **RAN Configuration:** `sch = PF` (`dl_scheduler_type: 0`, `ul_scheduler_type: 0`).
* **Traffic:** Uplink UDP traffic (`iperf3 -u -b 10M -P 5`) across all 5 UEs simultaneously.
* **Pass Criteria:**
  - Equal ~12.0% – 28.0% share each (~45+ Mbps total radio capacity).

#### TC-004: No Slice PF Policy - UL TCP Full Traffic (`pf_ul_tcp` / `004`)
* **RAN Configuration:** `sch = PF` (`dl_scheduler_type: 0`, `ul_scheduler_type: 0`).
* **Traffic:** Uplink TCP traffic (`iperf3 -P 5`) across all 5 UEs simultaneously.
* **Pass Criteria:**
  - Equal ~12.0% – 32.0% share each with robust TCP flow control (~90+ Mbps total).

---

### Group 100: As-No-Slice Policy (0/0/100% under NSBOTH)

#### TC-100: As-No-Slice Policy - 30s 5-UE Ping (`as_no_slice` / `100`)
* **Slice Configuration:** `min = 0%`, `max = 100%`, `dedicated = 0%` across all Slices 1–5 (`0 / 0 / 100%`). Behaves equivalent to no-slice baseline under NSBOTH with full dynamic PRB sharing.
* **Test Input:** 30-second continuous ICMP ping from all 5 UEs (`nws-oai-nr-ue1..5`) to UPF core gateway (`10.45.0.1`).
* **Pass Criteria:**
  - Packet Loss $\le 5.0\%$ across all 5 UEs.
  - Round-Trip Time (RTT) Average $< 50\text{ ms}$.

#### TC-101: As-No-Slice Policy - DL UDP Full Traffic (`as_no_slice_dl_udp` / `101`)
* **Slice Configuration:** `0 / 0 / 100%` across all slices.
* **Traffic:** Downlink UDP traffic (`iperf3 -u -b 20M -P 5 -R`) across all 5 UEs simultaneously.
* **Pass Criteria:**
  - Intra-slice proportional fair balances bandwidth evenly across all 5 active UEs (~12.0% – 28.0% share each).

#### TC-102: As-No-Slice Policy - DL TCP Full Traffic (`as_no_slice_dl_tcp` / `102`)
* **Slice Configuration:** `0 / 0 / 100%` across all slices.
* **Traffic:** Downlink TCP traffic (`iperf3 -P 5 -R`) across all 5 UEs simultaneously.
* **Pass Criteria:**
  - Multi-stream TCP congestion control achieves high throughput with balanced ~12.0% – 32.0% share each.

#### TC-103: As-No-Slice Policy - UL UDP Full Traffic (`as_no_slice_ul_udp` / `103`)
* **Slice Configuration:** `0 / 0 / 100%` across all slices.
* **Traffic:** Uplink UDP traffic (`iperf3 -u -b 10M -P 5`) across all 5 UEs simultaneously.
* **Pass Criteria:**
  - Equal ~12.0% – 28.0% share each (~45+ Mbps total radio capacity).

#### TC-104: As-No-Slice Policy - UL TCP Full Traffic (`as_no_slice_ul_tcp` / `104`)
* **Slice Configuration:** `0 / 0 / 100%` across all slices.
* **Traffic:** Uplink TCP traffic (`iperf3 -P 5`) across all 5 UEs simultaneously.
* **Pass Criteria:**
  - Equal ~12.0% – 32.0% share each with robust TCP flow control (~90+ Mbps total)

### Group 200: Dedicated Policy (Pass 1 - Fixed Non-Shareable Reservation, min=ded, max=100%)

#### Symmetric Idle (`15/15/15/15/15% dedicated, min=15%, max=100%` | UE1 active, UEs 2-5 idle)
- **201)** DL UDP | **202)** DL TCP | **203)** UL UDP | **204)** UL TCP
- **Behavior:** Slices 2–5 hold 60% dedicated PRBs even when idle; UE1 is restricted to its own 15% slice + unreserved pool (up to 100% max).

#### Symmetric Full (`15/15/15/15/15% dedicated, min=15%, max=100%` | UEs 1-5 active)
- **205)** DL UDP | **206)** DL TCP | **207)** UL UDP | **208)** UL TCP
- **Behavior:** All 5 UEs compete equally and receive balanced ~15.0% – 25.0% share each.

#### Asymmetric Idle (`15/15/15/15/7% dedicated, min=ded, max=100%` | UE1 active, UEs 2-5 idle)
- **211)** DL UDP | **212)** DL TCP | **213)** UL UDP | **214)** UL TCP
- **Behavior:** Slices 2–5 hold 52% dedicated PRBs without leaking to UE1.

#### Asymmetric Full (`15/15/15/15/7% dedicated, min=ded, max=100%` | UEs 1-5 active)
- **215)** DL UDP | **216)** DL TCP | **217)** UL UDP | **218)** UL TCP
- **Behavior:** Slices 1–4 each receive 18.0% – 26.0% share; Slice 5 receives 7.0% – 14.0% share.

---

### Group 300: Min Policy (Pass 2 - Prioritized Shareable Guarantee)

#### Symmetric Idle (`20/20/20/20/20%` | UE1 active, UEs 2-5 idle)
- **301)** DL UDP | **302)** DL TCP | **303)** UL UDP | **304)** UL TCP
- **Behavior:** Unused min PRBs from idle slices are released $\rightarrow$ active UE1 bursts to 100% capacity!

#### Symmetric Full (`20/20/20/20/20%` | UEs 1-5 active)
- **305)** DL UDP | **306)** DL TCP | **307)** UL UDP | **308)** UL TCP
- **Behavior:** All 5 UEs receive balanced ~15.0% – 25.0% minimum guaranteed share each.

#### Asymmetric Idle (`20/20/20/20/10%` | UE1 active, UEs 2-5 idle)
- **311)** DL UDP | **312)** DL TCP | **313)** UL UDP | **314)** UL TCP
- **Behavior:** Unused min PRBs are released $\rightarrow$ active UE1 bursts to 100% capacity.

#### Asymmetric Full (`20/20/20/20/10%` | UEs 1-5 active)
- **315)** DL UDP | **316)** DL TCP | **317)** UL UDP | **318)** UL TCP
- **Behavior:** Slices 1–4 receive 18.0% – 26.0% share; Slice 5 receives 9.0% – 15.0% share (~half of Slices 1–4).

---

### Group 400: Max Policy (Pass 3 - Hard Ceiling Caps)

#### Symmetric Idle (`100/100/100/100/100%` | UE1 active, UEs 2-5 idle)
- **401)** DL UDP | **402)** DL TCP | **403)** UL UDP | **404)** UL TCP
- **Behavior:** No ceiling cap; active UE1 bursts freely across 100% cell bandwidth.

#### Symmetric Full (`100/100/100/100/100%` | UEs 1-5 active)
- **405)** DL UDP | **406)** DL TCP | **407)** UL UDP | **408)** UL TCP
- **Behavior:** All 5 slices compete uncapped; proportional fair scheduler balances equal ~15.0% – 25.0% share.

#### Asymmetric Idle (`100/100/100/50/50%` | UE5 active, UEs 1-4 idle)
- **411)** DL UDP | **412)** DL TCP | **413)** UL UDP | **414)** UL TCP
- **Behavior:** UE5 is strictly throttled by its 50% ceiling cap despite completely idle channel.

#### Asymmetric Full (`100/100/100/50/50%` | UEs 1-5 active)
- **415)** DL UDP | **416)** DL TCP | **417)** UL UDP | **418)** UL TCP
- **Behavior:** Slices 1–3 receive 22.0% – 32.0% share; Slices 4–5 capped at 10.0% – 17.0% share.
