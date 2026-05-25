# Open5GS Configuration Files

This directory contains the configuration files and scripts for the Open5GS 5G Core Network container.

## 📁 File Structure

| File | Description |
|------|-------------|
| `Dockerfile` | Container build configuration |
| `open5gs_entrypoint.sh` | Container startup script with cross-platform support |
| `open5gs-5gc.yml` | Main 5G Core configuration file |
| `open5gs.env` | Environment variables and parameters |
| `subscriber_db.csv` | UE subscriber database |
| `add_users.py` | Python script for subscriber management |
| `setup_tun.py` | TUN interface setup script |

## 🚀 Additional Services

### iperf3 Network Testing Servers

The container automatically starts multiple iperf3 servers for network performance testing:

| Server | Port | Interface | Purpose |
|--------|------|-----------|---------|
| **Internal** | 5201 | All interfaces | Internal network testing |
| **N6 Interface** | 5202 | N6 interface (10.45.0.1) | External network testing |
| **Additional 1** | 5000 | All interfaces | Additional testing port |
| **Additional 2** | 5001 | All interfaces | Additional testing port |
| **Additional 3** | 5002 | All interfaces | Additional testing port |
| **Additional 4** | 5003 | All interfaces | Additional testing port |

#### Usage Examples

```bash
# Test internal network performance (from within container)
docker exec open5gs_5gc iperf3 -c localhost -p 5201 -t 10
docker exec open5gs_5gc iperf3 -c localhost -p 5202 -t 10

# Test additional iperf3 servers
docker exec open5gs_5gc iperf3 -c localhost -p 5000 -t 10
docker exec open5gs_5gc iperf3 -c localhost -p 5001 -t 10
docker exec open5gs_5gc iperf3 -c localhost -p 5002 -t 10
docker exec open5gs_5gc iperf3 -c localhost -p 5003 -t 10

# Check all iperf3 servers status
docker exec open5gs_5gc ps aux | grep iperf3

# Verify all ports are listening
docker exec open5gs_5gc ss -tlnp | grep -E "(5000|5001|5002|5003|5201|5202)"
```

## 🔧 Configuration Files

### `open5gs-5gc.yml`
Main configuration file containing:
- Network Function (NF) configurations
- IP addresses and ports
- freeDiameter settings
- Session management parameters

### `open5gs.env`
Environment variables:
- `OPEN5GS_IP`: Core network IP (default: 10.53.1.2)
- `UE_IP_BASE`: UE IP range (default: 10.45.0)
- `DEBUG`: Debug mode (default: false)
- `SUBSCRIBER_DB`: Subscriber database file

### `subscriber_db.csv`
UE subscriber information in CSV format:
```csv
Name,IMSI,Key,OP_Type,OP/OPc,AMF,QCI,IP_alloc,sst,sd,dnn,session_type
```

## 🚀 Scripts

### `open5gs_entrypoint.sh`
Container startup script that:
- Detects system architecture (ARM64/x86_64)
- Creates cross-platform symbolic links
- Sets up networking and routing
- Starts all 5G Core components

### `add_users.py`
Python script for managing subscribers:
- Adds subscribers to MongoDB
- Supports CSV file input
- Handles authentication parameters

### `setup_tun.py`
Network setup script:
- Creates TUN interface (ogstun)
- Configures IP routing
- Sets up NAT for internet access

## 🔄 Cross-Platform Support

The entrypoint script automatically handles architecture differences:

```bash
# Detects architecture
ARCH=$(uname -m)

# Creates appropriate symbolic links
if [ "$ARCH" = "aarch64" ]; then
    # ARM64 setup
elif [ "$ARCH" = "x86_64" ]; then
    # x86_64 setup
fi
```

## 📝 Customization

### Adding Subscribers
Edit `subscriber_db.csv` or use the web UI at http://localhost:9999

### Modifying Configuration
Edit `open5gs-5gc.yml` for advanced configuration changes

### Environment Variables
Modify `open5gs.env` for basic parameter changes

## 🔍 Troubleshooting

### Check Architecture Detection
```bash
docker logs open5gs_5gc 2>&1 | grep "Detected architecture"
```

### Verify Configuration
```bash
docker exec open5gs_5gc cat /open5gs/open5gs-5gc.yml
```

### Check Subscriber Database
```bash
docker exec open5gs_5gc cat /open5gs/subscriber_db.csv
```
