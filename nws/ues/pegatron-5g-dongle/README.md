# Pegatron 5G Dongle Control

This repository contains a Python script to interact with the Pegatron 5G Dongle using its JSON-RPC API.

## control script: `dongle.py`

This script allows you to query various metrics from the dongle and execute raw API commands.

### Dependencies

- Python 3
- `requests` library

```bash
pip install requests
```

### Usage

```bash
python3 dongle.py [OPTIONS] COMMAND [ARGS]
```

#### Global Options

- `--ip <IP>`: Router IP address (default: `192.168.11.1`)
- `--endpoint <PATH>`: API endpoint (default: `/fibo/webapi`)
- `--password <PASSWORD>`: Login password (if required)
- `--json`: Output raw JSON response instead of formatted text
- `-v, --verbose`: Enable verbose logging

#### Commands

- `signal`: Get Signal Strength metrics (RSRP, SINR, etc.)
- `network`: Get Network Information (PLMN, Network Name)
- `connection`: Get Connection Status and Data Usage
- `cell`: Get Current Cell Information (ID, Band, PCI)
- `device`: Get Device Information (Firmware, Model)
- `sim`: Get SIM Card Status
- `wan`: Get WAN Interface Info
- `ca`: Get Carrier Aggregation Status
- `raw`: Execute a raw JSON-RPC method
- `discover`: Try to discover undocumented APIs by brute-forcing common method names
- `airplane [on|off]`: Get or Set Airplane Mode
- `apn`: Manage APN Profiles (add, list, set-default)

### Examples

**1. Get Signal Strength:**

```bash
python3 dongle.py signal
```

**2. Manage Airplane Mode:**

```bash
# Get status
python3 dongle.py airplane

# Turn ON
python3 dongle.py airplane on

# Turn OFF
python3 dongle.py airplane off
```

**3. Manage APN Profiles:**

```bash
# List all profiles
python3 dongle.py apn --list

# Add a new profile
python3 dongle.py apn --add --name "MyAPN" --apn "internet" --user "user" --password "pass"

# Set default profile (by index from list)
python3 dongle.py apn --set-default 2
```

**4. Get Connection Info (JSON output):**

```bash
python3 dongle.py --json connection
```

**3. Set a Value (using `raw` command):**

*Note: You need to know the specific API method name and parameters.*

Example (hypothetical `SetAPN` method):

```bash
python3 dongle.py raw SetAPN '{"apn": "internet"}'
```

**4. Login with password:**

```bash
python3 dongle.py --password "admin123" signal
```
