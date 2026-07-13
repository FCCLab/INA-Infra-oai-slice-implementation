# DCI Search Spaces in This OAI Setup

## SearchSpace
```
SearchSpace ::=                         SEQUENCE {
    searchSpaceId                           SearchSpaceId,
    controlResourceSetId                    ControlResourceSetId                                        OPTIONAL,   -- Cond SetupOnly
    monitoringSlotPeriodicityAndOffset      CHOICE {
        sl1                                     NULL,
        sl2                                     INTEGER (0..1),
        sl4                                     INTEGER (0..3),
        sl5                                     INTEGER (0..4),
        sl8                                     INTEGER (0..7),
        sl10                                    INTEGER (0..9),
        sl16                                    INTEGER (0..15),
        sl20                                    INTEGER (0..19),
        sl40                                    INTEGER (0..39),
        sl80                                    INTEGER (0..79),
        sl160                                   INTEGER (0..159),
        sl320                                   INTEGER (0..319),
        sl640                                   INTEGER (0..639),
        sl1280                                  INTEGER (0..1279),
        sl2560                                  INTEGER (0..2559)
    }                                                                                                   OPTIONAL,   -- Cond Setup4
    duration                                INTEGER (2..2559)                                           OPTIONAL,   -- Need S
    monitoringSymbolsWithinSlot             BIT STRING (SIZE (14))                                      OPTIONAL,   -- Cond Setup
    nrofCandidates                          SEQUENCE {
        aggregationLevel1                       ENUMERATED {n0, n1, n2, n3, n4, n5, n6, n8},
        aggregationLevel2                       ENUMERATED {n0, n1, n2, n3, n4, n5, n6, n8},
        aggregationLevel4                       ENUMERATED {n0, n1, n2, n3, n4, n5, n6, n8},
        aggregationLevel8                       ENUMERATED {n0, n1, n2, n3, n4, n5, n6, n8},
        aggregationLevel16                      ENUMERATED {n0, n1, n2, n3, n4, n5, n6, n8}
    }                                                                                                   OPTIONAL,   -- Cond Setup
    searchSpaceType                         CHOICE {
        common                                  SEQUENCE {
            dci-Format0-0-AndFormat1-0              SEQUENCE {
                ...
            }                                                                                           OPTIONAL,   -- Need R
            dci-Format2-0                           SEQUENCE {
                nrofCandidates-SFI                      SEQUENCE {
                    aggregationLevel1                       ENUMERATED {n1, n2}                         OPTIONAL,   -- Need R
                    aggregationLevel2                       ENUMERATED {n1, n2}                         OPTIONAL,   -- Need R
                    aggregationLevel4                       ENUMERATED {n1, n2}                         OPTIONAL,   -- Need R
                    aggregationLevel8                       ENUMERATED {n1, n2}                         OPTIONAL,   -- Need R
                    aggregationLevel16                      ENUMERATED {n1, n2}                         OPTIONAL    -- Need R
                },
                ...
            }                                                                                           OPTIONAL,   -- Need R
            dci-Format2-1                           SEQUENCE {
                ...
            }                                                                                           OPTIONAL,   -- Need R
            dci-Format2-2                           SEQUENCE {
                ...
            }                                                                                           OPTIONAL,   -- Need R
            dci-Format2-3                           SEQUENCE {
                dummy1                                  ENUMERATED {sl1, sl2, sl4, sl5, sl8, sl10, sl16, sl20}  OPTIONAL,   -- Cond Setup
                dummy2                                  ENUMERATED {n1, n2},
                ...
            }                                                                                           OPTIONAL    -- Need R
        },
        ue-Specific                                 SEQUENCE {
            dci-Formats                                 ENUMERATED {formats0-0-And-1-0, formats0-1-And-1-1},
            ...,
            [[
            dci-Formats-MT-r16                   ENUMERATED {formats2-5}                                OPTIONAL,    -- Need R
            dci-FormatsSL-r16                    ENUMERATED {formats0-0-And-1-0, formats0-1-And-1-1, formats3-0, formats3-1,
                                                             formats3-0-And-3-1}                        OPTIONAL,    -- Need R
            dci-FormatsExt-r16                   ENUMERATED {formats0-2-And-1-2, formats0-1-And-1-1And-0-2-And-1-2}
                                                                                                        OPTIONAL     -- Need R
            ]]
        }
    }                                                                                                   OPTIONAL    -- Cond Setup2
}
```

### What The Spec Means

- `monitoringSlotPeriodicityAndOffset`: how often the search space repeats, together with its slot offset inside that period
- `duration`: how many consecutive slots the search space remains active each time it appears
- `monitoringSymbolsWithinSlot`: which OFDM symbols inside those active slots are monitored for PDCCH
- `controlResourceSetId`: which `CORESET` carries the PDCCH in frequency/time resources

Practical meaning:

- `sl1` means the search space appears every slot
- `sl10 = 3` means the search space repeats every 10 slots with offset 3
- `duration = 2` means each appearance lasts 2 consecutive slots
- if `duration` is absent, the effective duration is 1 slot

So if a search space has `sl10 = 3` and `duration = 2`, each recurrence spans 2 consecutive slots in a 10-slot periodic pattern.

## CORESET

```
ControlResourceSet ::=              SEQUENCE {
    controlResourceSetId                ControlResourceSetId,
    frequencyDomainResources            BIT STRING (SIZE (45)),
    duration                            INTEGER (1..maxCoReSetDuration),
    cce-REG-MappingType                 CHOICE {
        interleaved                         SEQUENCE {
            reg-BundleSize                      ENUMERATED {n2, n3, n6},
            interleaverSize                     ENUMERATED {n2, n3, n6},
            shiftIndex                          INTEGER(0..maxNrofPhysicalResourceBlocks-1) OPTIONAL
        },
        nonInterleaved                      NULL
    },
    precoderGranularity                 ENUMERATED {sameAsREG-bundle, allContiguousRBs},
    ...
}
```

### What The Spec Means

- `controlResourceSetId`: the CORESET identifier referenced by a `SearchSpace`
- `frequencyDomainResources`: the RB groups used by the CORESET in frequency domain
- `duration`: how many OFDM symbols the CORESET spans in time domain
- `cce-REG-MappingType`: how CCEs are mapped to REGs, either `interleaved` or `nonInterleaved`
- `precoderGranularity`: whether precoding follows `sameAsREG-bundle` or `allContiguousRBs`

Practical meaning:

- `frequencyDomainResources` determines where PDCCH can be placed in frequency
- `duration` determines how many symbols the CORESET occupies
- `cce-REG-MappingType` affects how CCE candidates are laid out across REGs
- a `SearchSpace` points to a CORESET through `controlResourceSetId`

## OAI-Specific Config

The gNB container in `nws/docker-compose/docker-compose.open5gs.5slices.nsul.yaml` mounts:

- `nws/configs/gnb/gnb.sa.band78.106prb.rfsim.open5gs.5slices.nsul.yaml` as `/workspace/gnb.yaml`

The relevant Type-0 PDCCH config in that file is:

```yaml
initialDLBWPlocationAndBandwidth: 28875
initialDLBWPsubcarrierSpacing: 1
initialDLBWPcontrolResourceSetZero: 11
initialDLBWPsearchSpaceZero: 0
```

- `initialDLBWPcontrolResourceSetZero: 11`
- `initialDLBWPsearchSpaceZero: 0`

These two values are not direct timing fields. Instead, OAI uses the Type-0 PDCCH rules to derive:

- search-space periodicity
- slot offset
- duration
- first monitored symbol
- `CORESET0` RB location

### How OAI Derives CORESET0

For this setup, OAI uses the FR1 Type-0 PDCCH path with SSB SCS = 30 kHz and PDCCH SCS = 30 kHz.

- `controlResourceSetZero = 11` selects 38.213 Table 13-4 entry 11:
  - `num_rbs = 48`
  - `num_symbols = 1`
  - `rb_offset = 14`
- OAI then builds `CORESET0` from those values:
  - `controlResourceSetId = 0`
  - `duration = num_symbols = 1`
  - `frequencyDomainResources` corresponds to 48 RB
  - `cce-REG-MappingType = interleaved`
  - `reg-BundleSize = n6`
  - `interleaverSize = n2`
  - `precoderGranularity = sameAsREG-bundle`
- OAI computes the CORESET start RB as:

```text
cset_start_rb = ssb_offset_point_a - rb_offset
              = 43 - 14
              = 29
```

This matches the runtime log for `BCH_SIB1`, which shows `BWP_RB_off=29`.

### OAI CORESETs In This Setup

CORESET | ID | How It Is Created | RB Range | RB Width | Duration | Mapping | REG Bundle | Interleaver | Precoder | Linked Search Spaces | Seen In Current Log | Notes
--- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | ---
`CORESET0` | `0` | Derived from Type-0 PDCCH using `initialDLBWPcontrolResourceSetZero: 11` | RB 29-76 | 48 RB | 1 symbol | `interleaved` | `n6` | `n2` | `sameAsREG-bundle` | `SS0`, `SS1`, `SS4` in the current log | Yes | Used for `SIB1`, `RA-Msg2`, and some common DL/UL DCI in this setup
`CORESET1` | `1` | Built by OAI `get_coreset_config(bwp_id=0, curr_bwp=106, ...)` | First 96 RB of the BWP | 96 RB | 1 symbol | `nonInterleaved` | N/A | N/A | `sameAsREG-bundle` | `SS5` in the current log; OAI also configures additional search spaces tied to this CORESET in code paths | Yes | Used for UE scheduling in this run; the log shows many `CORESET1 SS5` UL/DL DCI allocations

#### How OAI Derives CORESET1 Capacity

For `CORESET1`, OAI uses the first 96 RB of the BWP and `1` symbol.

OAI computes the number of CCEs as:

```text
total_resource_element_groups = num_rbs * duration
                              = 96 * 1
                              = 96

reg_per_cce = 6

total_cces = total_resource_element_groups / reg_per_cce
           = 96 / 6
           = 16
```

If UESS uses aggregation level `L=2`, then each DCI consumes 2 CCE:

```text
max_dci_per_slot = total_cces / 2
                 = 16 / 2
                 = 8
```

So for this setup:

- `CORESET1` capacity = `16 CCE`
- maximum DCI at `L=2` = `8` per slot

In the current log, `CORESET0` and `CORESET1` are both present. No `CORESET2+` entries were found.

### How OAI Derives Periodicity, Offset, and Duration

For this setup, OAI uses the FR1 Type-0 PDCCH path with SSB SCS = 30 kHz and PDCCH SCS = 30 kHz.

- `controlResourceSetZero = 11` selects 38.213 Table 13-4 entry 11:
  - `num_rbs = 48`
  - `num_symbols = 1`
  - `rb_offset = 14`
- `searchSpaceZero = 0` selects 38.213 Table 13-11 entry 0:
  - `O = 0`
  - `M = 1`
  - `first_symbol_index = 0`
- `slots_per_frame = 20` for 30 kHz SCS
- OAI uses `ssb_index = 0` in this common setup

OAI computes:

```text
temp = O * (1 << scs_pdcch) + ssb_index * M
     = 0 * (1 << 1) + 0 * 1
     = 0

n_c  = temp / slots_per_frame = 0 / 20 = 0
sfn_c = n_c % 2 = 0
n_0  = temp % slots_per_frame = 0 % 20 = 0
```

For FR1, Type-0 PDCCH mux pattern 1, OAI then derives:

- `Periodicity = slots_per_frame << 1 = 40 slots`
- `Offset = n_0 + slots_per_frame * sfn_c = 0 + 20 * 0 = 0`
- `Duration = 2 slots`

So the `SS0` row in the table is not guessed by hand; it comes from the Type-0 table lookup plus these OAI calculations.

### OAI Search Spaces In This Setup

SS | Spec | Purpose | Periodicity | Offset | Duration | Symbols | CORESET | Aggregation Candidates (L1/L2/L4/L8/L16) | Observed Aggregation | Behavior In This Setup
--- | --- | --- | --- | --- | --- | --- | --- | --- | --- | ---
0 | 38.331 `searchSpaceZero` | Type-0 common search space on `CORESET0`, used for `SIB1` | Derived from Type-0 PDCCH tables: 40 slots (2 frames) | Derived from Type-0 PDCCH tables: 0 | Derived from Type-0 PDCCH tables: 2 slots | Derived from Type-0 PDCCH tables: Symbol 0; 1 CORESET symbol | `CORESET0` (derived Type-0 values: 48 RB at RB 29) | Derived Type-0 values: `0/0/4/2/1` | `L=4` for `BCH_SIB1` | Used for `SIB1`; derived from `initialDLBWPcontrolResourceSetZero: 11` and `initialDLBWPsearchSpaceZero: 0`; runtime log shows `BWP_RB_off=29`, `sym s0 n1`
1 | 38.331 `ra-SearchSpace` | Random access control, e.g. `RA-Msg2` DCI | Every slot | 0 | 1 slot | Symbol 0 | `CORESET0` | OAI common CSS defaults: `0/0/2/0/0` | `L=4` for `RA_Msg2_DL_DCI` | Random access search space, used for `RA-Msg2`
2 | 38.331 `pagingSearchSpace` | Paging | Every slot | 0 | 1 slot | Symbol 0 | `CORESET0` | OAI common CSS defaults: `0/0/2/0/0` | Not observed in current log | Paging
3 | 38.331 `searchSpaceOtherSystemInformation` | Other system information | Every slot | 0 | 1 slot | Symbol 0 | `CORESET0` | OAI common CSS defaults: `0/0/2/0/0` | Not observed in current log | Other system information
4 | OAI common search space | Extra common search space used by OAI for common DL/UL DCI handling | Every slot | 0 | 1 slot | Symbol 0 | `CORESET0` | OAI common CSS defaults: `0/0/1/0/0` | `L=4` in current log | Extra OAI common search space used in runtime DCI handling
5 | OAI UE-specific search space | UE-specific scheduling search space | Every slot | 0 | 1 slot | Symbol 0 | OAI UE-specific CORESET/search-space configuration | OAI UESS defaults: `0/2/0/0/0` | `L=2` in current log | UE-specific scheduling search space

OAI Notes

- `SS1`, `SS2`, `SS3`, `SS4`, and `SS5` are created by OAI with `sl1`, no explicit duration, and `monitoringSymbolsWithinSlot` set to symbol 0 only.
- `SS0` is special: OAI derives it from the Type-0 PDCCH configuration rather than hardcoding the slot pattern.
- Even if a search space is "every slot", a DCI can only be scheduled where the slot is actually usable for DL control under the configured TDD pattern.