# OpenAirInterface (OAI) 5G Core Network

This directory contains the Docker Compose deployment stack for the OpenAirInterface (OAI) 5G Core Network (5GC).

## 🌐 Network Configuration (N2 / N3 Interfaces)

The core is deployed on a dedicated Docker bridge network named `nws-oai-cn5g` (subnet: `192.168.200.0/24`).

*   **Host Bridge Interface:** `nws-oai-cn5g`
*   **Host IP on this Bridge:** `192.168.200.1`

| Network Function | Component | Interface / Port | IP Address |
|---|---|---|---|
| **AMF** | Access & Mobility Management | **N2** (SCTP / `38412`) | `192.168.200.132` |
| **UPF** | User Plane Function | **N3** GTP-U (UDP / `2152`) | `192.168.200.134` |
| **SMF** | Session Management Function | N4 PFCP (UDP / `8805`) | `192.168.200.133` |
| **UPF** | User Plane Function | N4 PFCP (UDP / `8805`) | `192.168.200.134` |
| **IMS** | IP Multimedia Subsystem | SIP / `5060` | `192.168.200.139` |

## 🏗️ Services Overview

| Container Name | Service / NF | Description | IP Address |
|---|---|---|---|
| `nws-oai-nrf` | NRF | Network Repository Function | `192.168.200.130` |
| `nws-mysql` | MySQL DB | Database containing subscriber information | `192.168.200.131` |
| `nws-oai-amf` | AMF | Access and Mobility Management | `192.168.200.132` |
| `nws-oai-smf` | SMF | Session Management Function | `192.168.200.133` |
| `nws-oai-upf` | UPF | User Plane Function (BPF or SimpleSwitch) | `192.168.200.134` |
| `nws-oai-udr` | UDR | Unified Data Repository | `192.168.200.136` |
| `nws-oai-udm` | UDM | Unified Data Management | `192.168.200.137` |
| `nws-oai-ausf` | AUSF | Authentication Server Function | `192.168.200.138` |
| `nws-ims` | Asterisk IMS | IMS service core | `192.168.200.139` |

## 🚀 Usage

### Bring Up the 5G Core
From this directory (`nws/5gc/oai/`):
```bash
docker compose up -d
```

### Stop the Core
```bash
docker compose down
```

### Verify Running Containers
```bash
docker compose ps
```

---

## 💻 Running gNB on Host (Local Machine)
If you build and run the OAI gNB directly on the host, update the gNB's `.yaml` configuration file to bind to the host's IP on the bridge and target the AMF container:

```yaml
# amf_ip_address points to the nws-oai-amf container
amf_ip_address:
  - ipv4: 192.168.200.132

# NETWORK_INTERFACES points to the host's IP on the nws-oai-cn5g bridge
NETWORK_INTERFACES:
  GNB_IPV4_ADDRESS_FOR_NG_AMF: 192.168.200.1
  GNB_IPV4_ADDRESS_FOR_NGU: 192.168.200.1
```

---

## 🔧 Files Structure
*   [config.yaml](file:///home/fcp/nephio-network-slicing/network-slicing/nws/5gc/oai/conf/config.yaml): The unified configuration file mounted to all OAI NFs.
*   [oai_db.sql](file:///home/fcp/nephio-network-slicing/network-slicing/nws/5gc/oai/database/oai_db.sql): Database schema and pre-provisioned subscribers database initialization script.
